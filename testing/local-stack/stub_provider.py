#!/usr/bin/env python3
"""Standalone stub OAuth + provider-API server for Searchify integration testing.

Serves, on 127.0.0.1:9876 (override with STUB_PORT):
  - OAuth provider:  GET /authorize (302 auto-consent; ?mode=deny -> error redirect)
                     POST /token (code exchange + refresh; mode token_fail -> 400)
                     POST /revoke (RFC 7009; mode revoke_fail -> 400)
  - GSC:             GET  /webmasters/v3/sites (grant probe; mode probe_fail -> 401)
                     POST /webmasters/v3/sites/<ref>/searchAnalytics/query
  - GA4:             POST /v1beta/properties/<ref>:runReport
  - Bing Webmaster:  GET  /webmaster/api.svc/json/{GetSites,GetPageStats,GetQueryStats}
  - Test harness:    GET /__health, GET /__log, POST /__reset, POST /__mode

All provider data rows are generated for dates >= MIN_SYNC_DATE ONLY so a
stub-driven sync never overlaps the ORM-seeded history (2026-07-08..2026-07-21);
synced data is purely additive ("data appears" story stays crisp).

All tokens are literal dummies ("stub-*"); there are no real secrets here.
Run:  python3 testing/local-stack/stub_provider.py
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

HOST = "127.0.0.1"
PORT = int(os.environ.get("STUB_PORT", "9876"))
MIN_SYNC_DATE = date(2026, 7, 22)  # seeded history ends 2026-07-21

STATE = {
    "deny": False,
    "token_fail": False,
    "revoke_fail": False,
    "probe_fail": False,
}
LOG: list[dict] = []
LOCK = threading.Lock()

GSC_PAGES = [
    "https://acme-running.example.com/",
    "https://acme-running.example.com/pricing",
    "https://acme-running.example.com/products/trail-racer",
    "https://acme-running.example.com/products/road-glide",
    "https://acme-running.example.com/blog/best-running-shoes-2026",
    "https://blog.acme-running.example.com/cushioning-guide",
]
GSC_QUERIES = [
    "best running shoes",
    "acme trail racer review",
    "lightweight road running shoes",
    "running shoes for flat feet",
    "acme vs velocity sports",
    "marathon training shoes",
]
# (landingPage, sessionSource, sessionMedium)
GA4_LANDING = [
    ("/", "google", "organic"),
    ("/pricing", "google", "organic"),
    ("/products/trail-racer", "chatgpt.com", "referral"),
    ("/blog/best-running-shoes-2026", "perplexity.ai", "referral"),
]
GA4_SOURCE_MEDIUM = [
    ("google", "organic"),
    ("bing", "organic"),
    ("chatgpt.com", "referral"),
    ("perplexity.ai", "referral"),
]
GA4_CHANNELS = ["Organic Search", "Referral", "Paid Search"]
GA4_REFERRERS = [
    "https://chatgpt.com/",
    "https://perplexity.ai/",
    "https://gemini.google.com/app",
    "https://news.ycombinator.com/",
]
BING_PAGES = GSC_PAGES[:4]
BING_QUERIES = GSC_QUERIES[:4]


def _days(start: date, end: date) -> list[date]:
    """Requested range clamped to >= MIN_SYNC_DATE (never overlaps seed history)."""
    start = max(start, MIN_SYNC_DATE)
    out: list[date] = []
    cur = start
    while cur <= end:
        out.append(cur)
        cur += timedelta(days=1)
    return out


def _iso(d: date) -> str:
    return d.isoformat()


def _ga4_date(d: date) -> str:
    return d.strftime("%Y%m%d")


def _bing_date(d: date) -> str:
    ms = int(datetime(d.year, d.month, d.day, 7, 0, 0).timestamp() * 1000)
    return f"/Date({ms}-0700)/"


def _vol(seed: int, day_index: int, base: int, step: int) -> int:
    return base + step * ((seed * 7 + day_index * 3) % 5)


def gsc_rows(dimensions: list[str], start: date, end: date) -> list[dict]:
    values = GSC_PAGES if dimensions and dimensions[0] == "page" else GSC_QUERIES
    rows = []
    for di, d in enumerate(_days(start, end)):
        for i, value in enumerate(values):
            impressions = 60 + 10 * i + 4 * di
            clicks = (2 * i + di) % 8 + 1
            rows.append(
                {
                    "keys": [value, _iso(d)] if len(dimensions) > 1 else [value],
                    "clicks": clicks,
                    "impressions": impressions,
                    "ctr": round(clicks / impressions, 4),
                    "position": round(3.0 + 0.5 * i + 0.1 * di, 1),
                }
            )
    return rows


def ga4_rows(dimensions: list[str], start: date, end: date) -> list[dict]:
    names = [d for d in dimensions]
    rows = []
    for di, d in enumerate(_days(start, end)):
        combos: list[tuple[str, ...]]
        if names[:1] == ["sessionDefaultChannelGroup"]:
            combos = [(c,) for c in GA4_CHANNELS]
        elif names[:2] == ["sessionSource", "sessionMedium"]:
            combos = GA4_SOURCE_MEDIUM
        elif names[:1] == ["fullReferrer"]:
            combos = [(r,) for r in GA4_REFERRERS]
        else:  # landingPage, sessionSource, sessionMedium
            combos = GA4_LANDING
        for i, combo in enumerate(combos):
            sessions = _vol(i, di, 8, 6)
            dim_values = [{"value": v} for v in (*combo, _ga4_date(d))]
            rows.append(
                {
                    "dimensionValues": dim_values,
                    "metricValues": [
                        {"value": str(sessions)},
                        {"value": str(max(sessions - 3, 0))},
                        {"value": str(sessions % 4)},
                    ],
                }
            )
    return rows


def bing_rows(kind: str) -> list[dict]:
    values = BING_PAGES if kind == "GetPageStats" else BING_QUERIES
    out = []
    for di in range(3):
        d = MIN_SYNC_DATE + timedelta(days=di)
        for i, value in enumerate(values):
            impressions = 25 + 5 * i + 3 * di
            clicks = (i + di) % 5 + 1
            out.append(
                {
                    "__type": "QueryStats:#Microsoft.Bing.Webmaster.Api",
                    "AvgClickPosition": round(2.0 + 0.3 * i, 1),
                    "AvgImpressionPosition": round(3.0 + 0.4 * i, 1),
                    "Clicks": clicks,
                    "Date": _bing_date(d),
                    "Impressions": impressions,
                    "Query": value,
                }
            )
    return out


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:  # silence stderr noise
        pass

    # -- helpers -------------------------------------------------------------
    def _record(self) -> None:
        with LOCK:
            LOG.append(
                {
                    "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                    "method": self.command,
                    "path": self.path,
                }
            )

    def _send_json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # -- routing ---------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        self._record()
        split = urlsplit(self.path)
        path, query = split.path, parse_qs(split.query)
        if path == "/__health":
            return self._send_json({"ok": True})
        if path == "/__log":
            with LOCK:
                return self._send_json({"requests": list(LOG)})
        if path == "/authorize":
            return self._authorize(query)
        if path == "/webmasters/v3/sites":
            if STATE["probe_fail"]:
                return self._send_json({"error": "unauthorized"}, status=401)
            return self._send_json(
                {
                    "siteEntry": [
                        {"siteUrl": "sc-domain:acme-running.example.com",
                         "permissionLevel": "siteOwner"},
                        {"siteUrl": "https://acme-running.example.com/",
                         "permissionLevel": "siteFullUser"},
                    ]
                }
            )
        if path.startswith("/webmaster/api.svc/json/"):
            if STATE["probe_fail"]:
                return self._send_json({"error": "unauthorized"}, status=401)
            method = path.rsplit("/", 1)[-1]
            if method == "GetSites":
                return self._send_json(
                    {"d": [{"__type": "Site:#Microsoft.Bing.Webmaster.Api",
                            "Url": "https://acme-running.example.com"}]}
                )
            if method in ("GetPageStats", "GetQueryStats"):
                return self._send_json({"d": bing_rows(method)})
            return self._send_json({"error": f"unknown bing method {method}"}, status=404)
        return self._send_json({"error": f"unknown path {path}"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        self._record()
        split = urlsplit(self.path)
        path = split.path
        raw = self._read_body()
        if path == "/__reset":
            with LOCK:
                LOG.clear()
            return self._send_json({"ok": True})
        if path == "/__mode":
            try:
                updates = json.loads(raw or b"{}")
            except ValueError:
                updates = {}
            with LOCK:
                for key in STATE:
                    if key in updates:
                        STATE[key] = bool(updates[key])
            return self._send_json({"ok": True, "state": dict(STATE)})
        if path == "/token":
            if STATE["token_fail"]:
                return self._send_json(
                    {"error": "invalid_grant", "error_description": "stub token failure"},
                    status=400,
                )
            form = parse_qs(raw.decode())
            scope = (form.get("scope") or [""])[0]
            return self._send_json(
                {
                    "access_token": "stub-access-token",
                    "refresh_token": "stub-refresh-token",
                    "expires_in": 3600,
                    "scope": scope,
                    "token_type": "Bearer",
                }
            )
        if path == "/revoke":
            if STATE["revoke_fail"]:
                return self._send_json({"error": "temporarily_unavailable"}, status=400)
            return self._send_json({})
        if path.endswith("/searchAnalytics/query"):
            if STATE["probe_fail"]:
                return self._send_json({"error": "unauthorized"}, status=401)
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                body = {}
            start = date.fromisoformat(body.get("startDate", _iso(MIN_SYNC_DATE)))
            end = date.fromisoformat(body.get("endDate", _iso(MIN_SYNC_DATE)))
            dimensions = list(body.get("dimensions") or ["page"])
            rows = gsc_rows(dimensions, start, end)
            limit = int(body.get("rowLimit") or 25000)
            offset = int(body.get("startRow") or 0)
            page = rows[offset : offset + limit]
            return self._send_json(
                {"rows": page, "responseAggregationType": "byProperty"}
            )
        if path.endswith(":runReport"):
            if STATE["probe_fail"]:
                return self._send_json({"error": "unauthorized"}, status=401)
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                body = {}
            dr = (body.get("dateRanges") or [{}])[0]
            start = date.fromisoformat(dr.get("startDate", _iso(MIN_SYNC_DATE)))
            end = date.fromisoformat(dr.get("endDate", _iso(MIN_SYNC_DATE)))
            dims = [d.get("name", "") for d in (body.get("dimensions") or [])]
            rows = ga4_rows(dims, start, end)
            limit = int(body.get("limit") or 25000)
            offset = int(body.get("offset") or 0)
            page = rows[offset : offset + limit]
            dim_headers = [{"name": n} for n in [*dims]]
            metric_headers = [
                {"name": m, "type": "TYPE_INTEGER"}
                for m in ("sessions", "engagedSessions", "conversions")
            ]
            return self._send_json(
                {
                    "dimensionHeaders": dim_headers,
                    "metricHeaders": metric_headers,
                    "rows": page,
                    "rowCount": len(rows),
                }
            )
        return self._send_json({"error": f"unknown path {path}"}, status=404)

    # -- OAuth authorize --------------------------------------------------------
    def _authorize(self, query: dict[str, list[str]]) -> None:
        redirect_uri = (query.get("redirect_uri") or [""])[0]
        state = (query.get("state") or [""])[0]
        if not redirect_uri:
            return self._send_json({"error": "missing redirect_uri"}, status=400)
        sep = "&" if "?" in redirect_uri else "?"
        if STATE["deny"] or (query.get("mode") == ["deny"]):
            return self._redirect(
                f"{redirect_uri}{sep}error=access_denied&state={state}"
            )
        return self._redirect(f"{redirect_uri}{sep}code=stub-auth-code&state={state}")


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"stub provider listening on http://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
