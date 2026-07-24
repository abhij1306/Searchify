#!/usr/bin/env python3
"""Group A — API verification for the integrations/traffic/llm-analytics surface.

Runs against the LIVE dev stack through the Next same-origin proxy
(http://localhost:3000 — invariant 12; never :8000 from the "client").
Cookie auth (searchify_session). State-safe ordering:

  Phase 1 (demo, reads):      A1 A2 A10 A11 A13 A14 A15 A16 A3-ok
  Phase 2 (demo, enqueue):    A12 A4 A5
  Phase 3 (demo, mappings):   A6 A3-fail
  Phase 4 (user2 throwaway):  A7, OAuth curl connect, A8, A9

Run:  cd backend && uv run python ../testing/local-stack/group_a_api_tests.py
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import uuid
from datetime import date, timedelta

import httpx

BASE = "http://localhost:3000/api/v1"
STUB = "http://127.0.0.1:9876"
RESULTS: list[dict] = []

# Seeded ids — resolved at runtime by resolve_ids() at the start of main()
# (requires seed.sh + seed_integrations.py to have run).
P = ""          # Acme Running Shoes
EMPTY_P = ""    # Empty Co (no integrations)
GSC = ""
GA4 = ""
BING = ""


def resolve_ids(client: httpx.Client) -> None:
    global P, EMPTY_P, GSC, GA4, BING
    projects = client.get("/projects").json()
    plist = projects if isinstance(projects, list) else projects.get("items", [])
    P = next(p["id"] for p in plist if p["name"] == "Acme Running Shoes")
    EMPTY_P = next(p["id"] for p in plist if p["name"] == "Empty Co (no integrations)")
    conns = client.get("/integrations").json()
    clist = conns if isinstance(conns, list) else conns.get("items", [])
    by_provider = {c["provider"]: c["id"] for c in clist}
    GSC, GA4, BING = by_provider["gsc"], by_provider["ga4"], by_provider["bing"]
W_FULL = ("2026-07-08", "2026-07-21")
W_SHORT = ("2026-07-19", "2026-07-21")

CONNECTION_KEYS = {
    "id", "workspace_id", "grant_id", "provider", "label", "account_ref",
    "grant_status", "granted_scopes", "last_synced_at", "created_at", "updated_at",
}


def check(case: str, name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append({"case": case, "name": name, "pass": bool(cond), "detail": detail})
    print(f"{'PASS' if cond else 'FAIL'}  {case}  {name}" + (f"  -- {detail}" if detail and not cond else ""))


def login(client: httpx.Client, email: str, password: str) -> httpx.Response:
    return client.post(f"{BASE}/auth/login", json={"email": email, "password": password})


def stub_mode(**flags: bool) -> None:
    httpx.post(f"{STUB}/__mode", json=flags, timeout=10)


def stub_log() -> list[dict]:
    return httpx.get(f"{STUB}/__log", timeout=10).json()["requests"]


# --- expected-value math (from the deterministic seed formulas) ----------------
DAYS = [date(2026, 7, 8) + timedelta(days=i) for i in range(14)]


def gsc_page_sums(di_range):
    imps = clicks = 0
    for di in di_range:
        for pi in range(55):
            imps += 100 + 5 * pi + 2 * di
            clicks += (pi + di) % 9 + 1
    return imps, clicks


def ga4_sessions_total(di_range):
    total = 0
    for di in di_range:
        total += 40 + 2 * di                      # Organic Search channel
        total += (6 + di) + (3 + di % 3)          # AI source/medium (chatgpt + perplexity)
    return total


def _cleanup_leftovers() -> None:
    asyncio.run(_cleanup_leftovers_async())


async def _cleanup_leftovers_async() -> None:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))
    from sqlalchemy import delete

    from app.core.database import SessionLocal, engine
    from app.models.integrations import (
        IntegrationPropertyMapping,
        IntegrationSyncRun,
    )

    async with SessionLocal() as session:
        await session.execute(
            delete(IntegrationSyncRun).where(
                IntegrationSyncRun.connection_id.in_([uuid.UUID(GSC), uuid.UUID(GA4), uuid.UUID(BING)]),
                IntegrationSyncRun.status.in_(["queued", "leased", "running", "retry_wait"]),
            )
        )
        await session.execute(
            delete(IntegrationPropertyMapping).where(
                IntegrationPropertyMapping.property_ref.in_(
                    ["https://www.acme-running.example.com/blog",
                     "sc-domain:blog.acme-running.example.com"]
                )
            )
        )
        await session.commit()
    await engine.dispose()  # asyncpg pools bind to the running loop


def main() -> int:
    demo = httpx.Client(base_url=BASE, timeout=30, follow_redirects=False)
    anon = httpx.Client(base_url=BASE, timeout=30, follow_redirects=False)

    # ---------------- A1: login + me ----------------
    r = login(demo, "demo@searchify.dev", "DemoPass123!")
    check("A1", "login 200", r.status_code == 200, f"got {r.status_code}")
    check("A1", "session cookie set", "searchify_session" in demo.cookies)
    r = demo.get("/auth/me")
    check("A1", "auth/me 200", r.status_code == 200 and r.json()["user"]["email"] == "demo@searchify.dev")
    resolve_ids(demo)
    _cleanup_leftovers()  # needs resolved connection ids; safe to run post-login

    # ---------------- A2: GET /integrations shape + token hygiene ----------------
    r = demo.get("/integrations")
    body = r.text
    items = r.json() if r.status_code == 200 else []
    check("A2", "list 200 with 3 connections", r.status_code == 200 and len(items) == 3, f"got {r.status_code} n={len(items)}")
    check("A2", "strict DTO keys", all(set(c.keys()) == CONNECTION_KEYS for c in items),
          str([sorted(c.keys()) for c in items[:1]]))
    check("A2", "providers {gsc,ga4,bing}", {c["provider"] for c in items} == {"gsc", "ga4", "bing"})
    check("A2", "grant_status connected", all(c["grant_status"] == "connected" for c in items))
    check("A2", "gsc+ga4 share one grant", len({c["grant_id"] for c in items if c["provider"] in ("gsc", "ga4")}) == 1)
    leak = re.search(r"(?i)(access_token|refresh_token|secret|\"token\")", body)
    check("A2", "no token/secret keys in payload", leak is None, leak.group(0) if leak else "")
    by_provider = {c["provider"]: c for c in items}
    check("A2", "seeded connection ids match", {by_provider["gsc"]["id"], by_provider["ga4"]["id"], by_provider["bing"]["id"]} == {GSC, GA4, BING})

    # ---------------- A10: traffic reads ----------------
    r = demo.get(f"/projects/{P}/traffic", params={"from": W_FULL[0], "to": W_FULL[1], "granularity": "day"})
    d = r.json() if r.status_code == 200 else {}
    exp_imps, exp_clicks = gsc_page_sums(range(14))
    check("A10", "W_FULL day 200 + window echo", r.status_code == 200 and d.get("window_start") == W_FULL[0] and d.get("window_end") == W_FULL[1] and d.get("granularity") == "day")
    t = d.get("totals", {})
    check("A10", "totals.impressions == seeded GSC page sum", t.get("impressions") == exp_imps, f"{t.get('impressions')} != {exp_imps}")
    check("A10", "totals.clicks == seeded sum", t.get("clicks") == exp_clicks, f"{t.get('clicks')} != {exp_clicks}")
    check("A10", "totals.ctr == clicks/impressions", t.get("ctr") is not None and abs(t["ctr"] - exp_clicks / exp_imps) < 1e-9)
    exp_sessions = ga4_sessions_total(range(14))
    check("A10", "totals.sessions == Organic+AI only (Paid excluded)", t.get("sessions") == exp_sessions, f"{t.get('sessions')} != {exp_sessions}")
    check("A10", "6 series with 14 day-points", set(d.get("series", {}).keys()) == {"impressions", "clicks", "ctr", "position", "sessions", "conversions"} and all(len(v) == 14 for v in d.get("series", {}).values()))
    r = demo.get(f"/projects/{P}/traffic", params={"from": W_FULL[0], "to": W_FULL[1], "granularity": "week"})
    d = r.json()
    check("A10", "week granularity served (persisted)", r.status_code == 200 and len(d["series"]["impressions"]) == 3, str(len(d.get("series", {}).get("impressions", []))))
    r = demo.get(f"/projects/{P}/traffic")
    check("A10", "default read = latest persisted snapshot", r.status_code == 200 and r.json()["window_start"] == W_SHORT[0], r.json().get("window_start", ""))
    r = demo.get(f"/projects/{P}/traffic", params={"from": "2026-07-01", "to": "2026-07-05"})
    d = r.json()
    check("A10", "unpersisted window -> 200 empty payload", r.status_code == 200 and d["totals"]["impressions"] == 0 and d["series"]["impressions"] == [])
    r = demo.get(f"/projects/{P}/traffic", params={"granularity": "year"})
    check("A10", "bad granularity -> 422", r.status_code == 422, str(r.status_code))
    r = demo.get(f"/projects/{P}/traffic", params={"from": "2026-07-21", "to": "2026-07-08"})
    check("A10", "inverted window -> 422", r.status_code == 422, str(r.status_code))
    r = demo.get(f"/projects/{P}/traffic", params={"from": "2025-01-01", "to": "2026-07-21"})
    check("A10", ">480d window -> 422", r.status_code == 422, str(r.status_code))
    r = demo.get(f"/projects/{uuid.uuid4()}/traffic")
    check("A10", "unknown project -> 404", r.status_code == 404, str(r.status_code))
    r = demo.get(f"/projects/{EMPTY_P}/traffic")
    d = r.json()
    check("A10", "empty project -> 200 zeroed/null payload", r.status_code == 200 and d["totals"]["impressions"] == 0 and d["totals"]["sessions"] is None, str(d.get("totals")))

    # ---------------- A11: pages/queries keyset + sort ----------------
    r = demo.get(f"/projects/{P}/traffic/pages", params={"from": W_FULL[0], "to": W_FULL[1]})
    d = r.json()
    check("A11", "pages p1: 50 items + next_cursor", r.status_code == 200 and len(d["items"]) == 50 and d["next_cursor"], f"n={len(d.get('items', []))}")
    imps = [row["impressions"] for row in d["items"]]
    check("A11", "default sort -impressions", imps == sorted(imps, reverse=True))
    c1 = d["next_cursor"]
    r2 = demo.get(f"/projects/{P}/traffic/pages", params={"from": W_FULL[0], "to": W_FULL[1], "cursor": c1})
    d2 = r2.json()
    check("A11", "pages p2: 5 items, no overlap, no further cursor", len(d2["items"]) == 5 and d2["next_cursor"] is None and not {r["canonical_url"] for r in d2["items"]} & {r["canonical_url"] for r in d["items"]})
    check("A11", "page row keys", set(d["items"][0].keys()) == {"canonical_url", "site_url_id", "impressions", "clicks", "ctr", "position", "sessions", "conversions"})
    r = demo.get(f"/projects/{P}/traffic/pages", params={"from": W_FULL[0], "to": W_FULL[1], "sort": "clicks"})
    cc = [row["clicks"] for row in r.json()["items"]]
    check("A11", "sort=clicks ascending", cc == sorted(cc))
    r = demo.get(f"/projects/{P}/traffic/pages", params={"sort": "bogus"})
    check("A11", "pages bogus sort -> 422", r.status_code == 422, str(r.status_code))
    r = demo.get(f"/projects/{P}/traffic/pages", params={"from": W_FULL[0], "to": W_FULL[1], "sort": "clicks", "cursor": c1})
    check("A11", "cursor replay with changed sort -> 400", r.status_code == 400, str(r.status_code))
    r = demo.get(f"/projects/{P}/traffic/pages", params={"from": W_FULL[0], "to": W_FULL[1], "cursor": c1[:-4] + "AAAA"})
    check("A11", "tampered cursor -> 400", r.status_code == 400, str(r.status_code))
    r = demo.get(f"/projects/{P}/traffic/queries", params={"from": W_FULL[0], "to": W_FULL[1]})
    d = r.json()
    check("A11", "queries p1: 50 items + next_cursor", r.status_code == 200 and len(d["items"]) == 50 and d["next_cursor"])
    check("A11", "query row keys (no sessions/conversions)", set(d["items"][0].keys()) == {"normalized_query", "impressions", "clicks", "ctr", "position"})
    r = demo.get(f"/projects/{P}/traffic/queries", params={"sort": "sessions"})
    check("A11", "queries sort=sessions not whitelisted -> 422", r.status_code == 422, str(r.status_code))
    r2 = demo.get(f"/projects/{P}/traffic/queries", params={"from": W_FULL[0], "to": W_FULL[1], "cursor": d["next_cursor"]})
    check("A11", "queries p2: 5 items", len(r2.json()["items"]) == 5)

    # ---------------- A13: llm-analytics ----------------
    r = demo.get(f"/projects/{P}/llm-analytics", params={"from": W_FULL[0], "to": W_FULL[1], "granularity": "day"})
    d = r.json()
    corr = d.get("correlation", {})
    check("A13", "W_FULL 200", r.status_code == 200, str(r.status_code))
    check("A13", "correlation ok with 10 samples", corr.get("state") == "ok" and corr.get("sample_size") == 10, str(corr))
    check("A13", "coefficient in (0,1] (rising axes)", corr.get("coefficient") is not None and 0 < corr["coefficient"] <= 1, str(corr.get("coefficient")))
    srcs = {s["ai_source"]: s["sessions"] for s in d.get("sources", [])}
    check("A13", "sources cover 5 AI engines, chatgpt largest", {"chatgpt", "gemini", "claude", "perplexity", "copilot"} <= set(srcs) and srcs.get("chatgpt", 0) > srcs.get("gemini", 0), str(srcs))
    engines = {e["logical_engine"]: len(e["series"]) for e in d.get("engine_visibility", [])}
    check("A13", "engine_visibility: 3 engines x 14 points", engines == {"gemini": 14, "chatgpt": 14, "claude": 14}, str(engines))
    check("A13", "volume/share series 14 points", len(d.get("referral_volume", [])) == 14 and len(d.get("referral_share", [])) == 14)
    r = demo.get(f"/projects/{P}/llm-analytics", params={"from": W_SHORT[0], "to": W_SHORT[1]})
    corr = r.json()["correlation"]
    check("A13", "W_SHORT -> insufficient_data, null coefficient", corr["state"] == "insufficient_data" and corr["coefficient"] is None and corr["sample_size"] == 0, str(corr))
    r = demo.get(f"/projects/{P}/llm-analytics", params={"granularity": "year"})
    check("A13", "bad granularity -> 422", r.status_code == 422, str(r.status_code))
    r = demo.get(f"/projects/{EMPTY_P}/llm-analytics")
    d = r.json()
    check("A13", "empty project -> empty payload + insufficient_data", r.status_code == 200 and d["correlation"]["state"] == "insufficient_data" and d["sources"] == [])

    # ---------------- A14: referrals keyset + filter ----------------
    r = demo.get(f"/projects/{P}/llm-analytics/referrals")
    d = r.json()
    check("A14", "referrals p1: 50 + cursor", r.status_code == 200 and len(d["items"]) == 50 and d["next_cursor"], f"n={len(d.get('items', []))}")
    occ = [row["occurred_at"] for row in d["items"]]
    check("A14", "newest-first ordering", occ == sorted(occ, reverse=True))
    r2 = demo.get(f"/projects/{P}/llm-analytics/referrals", params={"cursor": d["next_cursor"]})
    r3 = demo.get(f"/projects/{P}/llm-analytics/referrals", params={"cursor": r2.json()["next_cursor"]})
    total_pages = len(d["items"]) + len(r2.json()["items"]) + len(r3.json()["items"])
    check("A14", "3 pages = 140 events, cursor then None", total_pages == 140 and r3.json()["next_cursor"] is None, f"total={total_pages}")
    r = demo.get(f"/projects/{P}/llm-analytics/referrals", params={"source": "chatgpt"})
    items = r.json()["items"]
    check("A14", "source=chatgpt filter (28 rows all chatgpt)", len(items) == 28 and all(i["ai_source"] == "chatgpt" and i["is_ai_referral"] for i in items), f"n={len(items)}")
    r = demo.get(f"/projects/{P}/llm-analytics/referrals", params={"source": "other"})
    others = r.json()["items"]
    check("A14", "non-AI rows: is_ai_referral false + confidence exact + null engine", len(others) > 0 and all(i["is_ai_referral"] is False and i["ai_source"] == "other" and i["confidence"] == "exact" and i["logical_engine"] is None for i in others), str(others[:1]))
    r = demo.get(f"/projects/{P}/llm-analytics/referrals", params={"source": "bogus"})
    check("A14", "bogus source -> 422", r.status_code == 422, str(r.status_code))
    r = demo.get(f"/projects/{P}/llm-analytics/referrals", params={"source": "gemini", "cursor": d["next_cursor"]})
    check("A14", "cursor replay with changed source -> 400", r.status_code == 400, str(r.status_code))

    # ---------------- A15: themes ----------------
    r = demo.get(f"/projects/{P}/llm-analytics/themes", params={"from": W_FULL[0], "to": W_FULL[1]})
    themes = {t["theme"]: t for t in r.json()} if r.status_code == 200 else {}
    check("A15", "themes 200 with Sizing+Pricing", r.status_code == 200 and set(themes) == {"Sizing", "Pricing"}, str(r.status_code))
    check("A15", "Sizing rate 1.0 (3/3 mentioned)", themes.get("Sizing", {}).get("brand_mention_rate") == 1.0, str(themes.get("Sizing")))
    check("A15", "Pricing rate 0.6667 (2/3, builder 4dp rounding)", themes.get("Pricing", {}).get("brand_mention_rate") == 0.6667, str(themes.get("Pricing")))
    check("A15", "intents carried", themes.get("Sizing", {}).get("intent") == "discovery" and themes.get("Pricing", {}).get("intent") == "purchase")

    # ---------------- A16: unauthenticated ----------------
    for method, path in [
        ("GET", "/integrations"),
        ("POST", f"/integrations/{GSC}/test"),
        ("POST", f"/integrations/{GSC}/sync"),
        ("GET", f"/integrations/{GSC}/syncs"),
        ("GET", f"/integrations/{GSC}/mappings"),
        ("GET", f"/projects/{P}/traffic"),
        ("GET", f"/projects/{P}/traffic/pages"),
        ("POST", f"/projects/{P}/traffic/sync"),
        ("GET", f"/projects/{P}/llm-analytics"),
        ("GET", f"/projects/{P}/llm-analytics/referrals"),
        ("GET", f"/projects/{P}/llm-analytics/themes"),
    ]:
        r = anon.request(method, path)
        check("A16", f"no-cookie {method} {path} -> 401", r.status_code == 401, str(r.status_code))

    # ---------------- A3: connection test probe ----------------
    r = demo.post(f"/integrations/{GSC}/test")
    body = r.json() if r.status_code == 200 else {}
    check("A3", "probe ok: status ok, empty error_code", r.status_code == 200 and body.get("status") == "ok" and body.get("error_code") == "", str(body))
    check("A3", "probe DTO keys", set(body.keys()) == {"connection_id", "status", "error_code", "detail", "tested_at"})
    r = demo.post(f"/integrations/{uuid.uuid4()}/test")
    check("A3", "unknown connection -> 404", r.status_code == 404, str(r.status_code))
    stub_mode(probe_fail=True)
    try:
        r = demo.post(f"/integrations/{BING}/test")
        body = r.json()
        check("A3", "probe 401 -> failed + grant_auth_failed", body.get("status") == "failed" and body.get("error_code") == "grant_auth_failed", str(body))
    finally:
        stub_mode(probe_fail=False)

    # ---------------- A12: traffic/sync fan-out ----------------
    r = demo.post(f"/projects/{P}/traffic/sync")
    runs = r.json() if r.status_code == 202 else []
    check("A12", "sync 202 bare array of exactly 2 (gsc+ga4, no bing)", r.status_code == 202 and isinstance(runs, list) and len(runs) == 2 and {x["connection_id"] for x in runs} == {GSC, GA4}, f"{r.status_code} {runs}")
    check("A12", "C3 entry keys + queued status", all(set(x.keys()) == {"sync_run_id", "connection_id", "status"} and x["status"] == "queued" for x in runs), str(runs))
    r = demo.post(f"/projects/{P}/traffic/sync")
    check("A12", "immediate repeat while active -> 409", r.status_code == 409, str(r.status_code))
    r = demo.post(f"/projects/{EMPTY_P}/traffic/sync")
    check("A12", "no mapped connections -> 202 empty array", r.status_code == 202 and r.json() == [], f"{r.status_code} {r.text[:80]}")

    # ---------------- A4: integrations sync enqueue validation ----------------
    window = {"window_start": "2026-07-15", "window_end": "2026-07-17"}
    r = demo.post(f"/integrations/{GSC}/sync", json=window)
    body = r.json() if r.status_code == 202 else {}
    check("A4", "explicit window 202", r.status_code == 202 and set(body.keys()) == {"sync_run_id", "connection_id", "status"}, f"{r.status_code} {body}")
    a4_run = body.get("sync_run_id")
    r = demo.post(f"/integrations/{GSC}/sync", json=window)
    check("A4", "duplicate active window -> 409 sync_active_window_conflict", r.status_code == 409 and "sync_active_window_conflict" in r.text, str(r.status_code))
    r = demo.post(f"/integrations/{GSC}/sync", json={"window_start": "2026-07-15"})
    check("A4", "half-specified window -> 422", r.status_code == 422, str(r.status_code))
    r = demo.post(f"/integrations/{GSC}/sync", json={"window_start": "2026-07-17", "window_end": "2026-07-15"})
    check("A4", "inverted window -> 422 sync_window_invalid", r.status_code == 422 and "sync_window_invalid" in r.text, str(r.status_code))
    r = demo.post(f"/integrations/{uuid.uuid4()}/sync")
    check("A4", "unknown connection -> 404", r.status_code == 404, str(r.status_code))

    # ---------------- A5: sync history + detail ----------------
    r = demo.get(f"/integrations/{GSC}/syncs")
    runs = r.json() if r.status_code == 200 else []
    check("A5", "history lists seeded + new runs", r.status_code == 200 and len(runs) >= 3, f"n={len(runs)}")
    r = demo.get(f"/integrations/{GSC}/syncs/{a4_run}")
    d = r.json()
    # allocator groups resync_seq by (connection, sync_kind, window): this on_demand
    # window group is new -> seq 0. The post-completion bump is verified in F1.
    check("A5", "detail: queued run, window echo, resync_seq 0 (new kind/window group)", r.status_code == 200 and d["status"] == "queued" and d["window_start"] == window["window_start"] and d["resync_seq"] == 0, str(d))
    check("A5", "detail DTO keys", set(d.keys()) == {"id", "connection_id", "sync_kind", "status", "window_start", "window_end", "row_count", "resync_seq", "error_code", "error_detail", "created_at", "updated_at", "completed_at"})
    seeded = next((x for x in runs if x["status"] == "succeeded"), None)
    check("A5", "seeded terminal run succeeded with completed_at", seeded is not None and seeded["completed_at"] is not None)
    r = demo.get(f"/integrations/{GSC}/syncs/{uuid.uuid4()}")
    check("A5", "unknown sync id -> 404", r.status_code == 404, str(r.status_code))

    # ---------------- A6: property mappings ----------------
    r = demo.post(f"/integrations/{GSC}/mappings", json={"provider": "gsc", "property_ref": "sc-domain:acme-running.example.com", "project_id": P})
    check("A6", "duplicate active owner -> 409", r.status_code == 409 and "mapping_active_owner_conflict" in r.text, str(r.status_code))
    r = demo.post(f"/integrations/{GSC}/mappings", json={"provider": "ga4", "property_ref": "sc-domain:acme-running.example.com", "project_id": P})
    check("A6", "provider mismatch -> 422", r.status_code == 422 and "mapping_provider_mismatch" in r.text, str(r.status_code))
    r = demo.post(f"/integrations/{GSC}/mappings", json={"provider": "gsc", "property_ref": "sc-domain:evil.example.com", "project_id": P})
    check("A6", "unowned property -> 422", r.status_code == 422 and "mapping_property_not_owned" in r.text, str(r.status_code))
    r = demo.post(f"/integrations/{GSC}/mappings", json={"provider": "gsc", "property_ref": "https://www.acme-running.example.com/blog", "project_id": P})
    check("A6", "URL-form ref normalizes to owned -> 201", r.status_code == 201, f"{r.status_code} {r.text[:120]}")
    scratch_mapping = r.json().get("id") if r.status_code == 201 else None
    r = demo.post(f"/integrations/{GSC}/mappings", json={"provider": "gsc", "property_ref": "sc-domain:blog.acme-running.example.com", "project_id": P})
    check("A6", "owned subdomain property -> 201", r.status_code == 201, str(r.status_code))
    r = demo.get(f"/integrations/{GSC}/mappings")
    check("A6", "list shows seeded + 2 new active mappings", r.status_code == 200 and len([m for m in r.json() if m["status"] == "active"]) == 3, str(len(r.json())))
    r = demo.delete(f"/integrations/mappings/{scratch_mapping}")
    check("A6", "disable mapping -> 204", r.status_code == 204, str(r.status_code))
    r = demo.get(f"/integrations/{GSC}/mappings")
    scratch = next((m for m in r.json() if m["id"] == scratch_mapping), {})
    check("A6", "disabled is a status flip (row kept)", scratch.get("status") == "disabled", str(scratch))
    r = demo.post(f"/integrations/{GSC}/mappings", json={"provider": "gsc", "property_ref": "https://www.acme-running.example.com/blog", "project_id": P})
    check("A6", "disable frees the slot -> re-create 201", r.status_code == 201, str(r.status_code))
    r = demo.delete(f"/integrations/mappings/{uuid.uuid4()}")
    check("A6", "unknown mapping -> 404", r.status_code == 404, str(r.status_code))

    # ---------------- A7: cross-workspace isolation (user2) ----------------
    user2 = httpx.Client(base_url=BASE, timeout=30, follow_redirects=False)
    email2 = f"xa7-{uuid.uuid4().hex[:8]}@example.com"
    r = user2.post("/auth/register", json={"email": email2, "password": "TestPass123!", "name": "XA7 User"})
    check("A7", "register user2", r.status_code in (200, 201), f"{r.status_code} {r.text[:120]}")
    if "searchify_session" not in user2.cookies:
        login(user2, email2, "TestPass123!")
    r = user2.get("/integrations")
    check("A7", "user2 sees no demo connections", r.status_code == 200 and r.json() == [])
    for method, path in [
        ("GET", f"/integrations/{GSC}/syncs"),
        ("GET", f"/integrations/{GSC}/syncs/{a4_run}"),
        ("POST", f"/integrations/{GSC}/test"),
        ("POST", f"/integrations/{GSC}/sync"),
        ("DELETE", f"/integrations/{GSC}"),
        ("GET", f"/integrations/{GSC}/mappings"),
        ("POST", f"/integrations/{GSC}/mappings"),
        ("GET", f"/projects/{P}/traffic"),
        ("GET", f"/projects/{P}/traffic/pages"),
        ("GET", f"/projects/{P}/traffic/queries"),
        ("POST", f"/projects/{P}/traffic/sync"),
        ("GET", f"/projects/{P}/llm-analytics"),
        ("GET", f"/projects/{P}/llm-analytics/referrals"),
        ("GET", f"/projects/{P}/llm-analytics/themes"),
    ]:
        json_body = {"provider": "gsc", "property_ref": "sc-domain:acme-running.example.com", "project_id": P} if method == "POST" and path.endswith("/mappings") else None
        r = user2.request(method, path, json=json_body)
        check("A7", f"cross-workspace {method} {path} -> 404", r.status_code == 404, str(r.status_code))

    # ---------------- user2 OAuth connect (curl-level mechanics) ----------------
    r = user2.get("/integrations/oauth/gsc/start")
    loc = r.headers.get("location", "")
    check("B0", "user2 gsc start -> 302 stub authorize", r.status_code == 302 and loc.startswith(f"{STUB}/authorize?"), loc[:80])
    auth = httpx.get(loc, follow_redirects=False, timeout=10)
    cb = auth.headers.get("location", "")
    check("B0", "stub authorize -> 302 callback with code+state", auth.status_code == 302 and "code=stub-auth-code" in cb and "state=" in cb, cb[:120])
    r = user2.get(cb)  # absolute URL — httpx uses as-is
    loc = r.headers.get("location", "")
    check("B0", "callback -> 302 landing connected=gsc", r.status_code == 302 and loc.startswith("/settings?tab=integrations") and "connected=gsc" in loc, loc)
    r = user2.get("/integrations")
    conns2 = r.json()
    check("B0", "user2 now has gsc+ga4 on one grant", len(conns2) == 2 and {c["provider"] for c in conns2} == {"gsc", "ga4"} and len({c["grant_id"] for c in conns2}) == 1, str(conns2))
    r = user2.get("/integrations/oauth/bing/start")
    auth = httpx.get(r.headers["location"], follow_redirects=False, timeout=10)
    r = user2.get(auth.headers["location"])
    check("B0", "bing callback -> connected=bing", "connected=bing" in r.headers.get("location", ""), r.headers.get("location", ""))
    r = user2.get("/integrations")
    conns2 = r.json()
    bing2 = next((c for c in conns2 if c["provider"] == "bing"), None)
    check("B0", "user2 has 3 connections, bing on separate grant", len(conns2) == 3 and bing2 is not None and bing2["grant_id"] != conns2[0]["grant_id"])

    gsc2 = next(c for c in conns2 if c["provider"] == "gsc")
    ga42 = next(c for c in conns2 if c["provider"] == "ga4")

    # ---------------- A8: disconnect shared-grant semantics ----------------
    httpx.post(f"{STUB}/__reset")
    r = user2.delete(f"/integrations/{gsc2['id']}")
    check("A8", "delete gsc -> 204", r.status_code == 204, str(r.status_code))
    r = user2.get("/integrations")
    remaining = r.json()
    check("A8", "ga4 remains, grant still connected", len(remaining) == 2 and any(c["provider"] == "ga4" and c["grant_status"] == "connected" for c in remaining))
    check("A8", "no remote revoke for shared grant", not any(x["path"] == "/revoke" for x in stub_log()))
    r = user2.delete(f"/integrations/{bing2['id']}")
    check("A8", "delete bing (last on microsoft grant) -> 204", r.status_code == 204, str(r.status_code))
    check("A8", "microsoft disconnect is local-only (still no /revoke call)", not any(x["path"] == "/revoke" for x in stub_log()))
    r = user2.delete(f"/integrations/{ga42['id']}")
    check("A8", "delete ga4 (last on google grant) -> 204", r.status_code == 204, str(r.status_code))
    check("A8", "google remote revoke called once", sum(1 for x in stub_log() if x["path"] == "/revoke") == 1, str([x for x in stub_log() if x["path"] == "/revoke"]))
    r = user2.get("/integrations")
    check("A8", "all connections gone", r.status_code == 200 and r.json() == [])

    # ---------------- A9: revoke failure -> pending_revocation ----------------
    r = user2.get("/integrations/oauth/gsc/start")
    auth = httpx.get(r.headers["location"], follow_redirects=False, timeout=10)
    user2.get(auth.headers["location"])
    conns2 = user2.get("/integrations").json()
    gsc2 = next(c for c in conns2 if c["provider"] == "gsc")
    ga42 = next(c for c in conns2 if c["provider"] == "ga4")
    stub_mode(revoke_fail=True)
    try:
        user2.delete(f"/integrations/{gsc2['id']}")
        r = user2.delete(f"/integrations/{ga42['id']}")
        check("A9", "last-connection delete with failing revoke -> 204", r.status_code == 204, str(r.status_code))
    finally:
        stub_mode(revoke_fail=False)
    grant_state = asyncio.run(_grant_state(email2))
    check("A9", "grant pending_revocation + tokens retained", grant_state == "pending_revocation|tokens_retained=True", str(grant_state))

    # ---------------- summary ----------------
    failed = [x for x in RESULTS if not x["pass"]]
    print(f"\n===== GROUP A: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed, {len(failed)} failed =====")
    for x in failed:
        print(f"  FAILED: {x['case']} {x['name']} -- {x['detail']}")
    with open("/code/.generated_artifacts/logs/group_a_api_results.json", "w") as f:
        json.dump(RESULTS, f, indent=2)
    return 1 if failed else 0


async def _grant_state(user2_email: str) -> str:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))
    from sqlalchemy import select

    from app.core.database import SessionLocal, engine
    from app.models.integrations import IntegrationOAuthGrant
    from app.models.user import User
    from app.models.workspace import WorkspaceMember

    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.email == user2_email))
        grant = await session.scalar(
            select(IntegrationOAuthGrant)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == IntegrationOAuthGrant.workspace_id)
            .where(WorkspaceMember.user_id == user.id)
            .where(IntegrationOAuthGrant.transport == "google_oauth")
        )
        result = "no-grant" if grant is None else (
            f"{grant.status}|tokens_retained={bool(grant.access_token_encrypted)}"
        )
    await engine.dispose()  # asyncpg pools bind to the running loop
    return result


if __name__ == "__main__":
    raise SystemExit(main())
