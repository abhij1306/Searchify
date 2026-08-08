"""Pure policy helpers for Site Health's server-owned acquisition ladder.

The fetcher owns URL validation, redirects, bounded streaming, and the actual
network clients. This module owns only deterministic rung selection so no
transport can silently broaden the crawler's security policy.
"""

from __future__ import annotations

import re
import sys

from app.connectors.web_evidence.contracts import FetchResult
from app.core.config.site_health import (
    ACQUISITION_TRIGGER_BLOCK_STATUS,
    ACQUISITION_TRIGGER_CHALLENGE,
    ACQUISITION_TRIGGER_JS_SHELL,
    ACQUISITION_TRIGGER_LOW_CONTENT,
)

# Subtrees whose contents are never readable page text. Removed whole (open tag
# through close tag) before the remaining markup is stripped, so a 200 KB
# inline bundle cannot read as 200 KB of content.
#
# The close pattern is ``</name`` followed by anything up to ``>`` rather than
# ``\s*>``: HTML ends the element at ``</script bar>`` and ``</script/>`` just
# as it does at ``</script>``. Accepting only the tidy form is what lets a
# hostile page hide an unclosed-looking bundle from the strip below. ``\b``
# still keeps ``</scripting>`` from closing a ``script``.
_NON_TEXT_SUBTREES = re.compile(
    r"<(script|style|template|noscript)\b[^>]*>.*?</\1\b[^>]*>",
    re.IGNORECASE | re.DOTALL,
)
_UNCLOSED_NON_TEXT = re.compile(
    r"<(script|style|template|noscript)\b[^>]*>.*", re.IGNORECASE | re.DOTALL
)
_COMMENT_OPEN = "<!--"
_COMMENT_CLOSE = "-->"
_TAGS = re.compile(r"<[^>]*>", re.DOTALL)
_SCRIPT_SRC = re.compile(r"<script\b[^>]*\bsrc\s*=", re.IGNORECASE)
_INLINE_SCRIPT = re.compile(
    r"<script\b(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script\b[^>]*>",
    re.IGNORECASE | re.DOTALL,
)


def curl_cffi_pinned_resolution_supported() -> bool:
    """Whether curl-cffi can meet this process's validated-IP contract.

    The current Windows worker platform cannot reliably bind curl-cffi's DNS
    override, TLS SNI, and peer verification to the IP selected by
    ``resolve_target``. Other platforms are supported only when the installed
    binding accepts libcurl's ``RESOLVE`` option. The probe performs no network
    I/O and fails closed on an absent or incompatible binding.
    """

    if sys.platform.startswith("win"):
        return False
    try:
        from curl_cffi import Curl, CurlOpt
    # An incompatible optional binding must fail closed regardless of error type.
    except Exception:  # noqa: BLE001
        return False
    try:
        curl = Curl()
        try:
            curl.setopt(CurlOpt.RESOLVE, ["citeladder.invalid:443:127.0.0.1"])
        finally:
            curl.close()
    # Capability probing is intentionally isolated from application work.
    except Exception:  # noqa: BLE001
        return False
    return True


def readable_text_length(body: bytes, *, scan_bytes: int) -> int:
    """Non-whitespace characters of readable text in a bounded body prefix.

    PURE and deliberately regex-based rather than a real parse: this answers
    only "did the server send readable content", runs on every 2xx HTML
    response, and must not pull an HTML parser into the acquisition ladder. It
    strips script/style/template/noscript subtrees first (so an inline bundle
    never counts as prose), then comments, then the remaining tags.

    A prefix cut can leave an unterminated ``<script>`` open; the second pattern
    drops that tail rather than letting the raw JavaScript inside it count as
    text — the failure that would otherwise make every shell look content-rich.
    """

    if not body:
        return 0
    text = body[: max(0, scan_bytes)].decode("utf-8", errors="replace")
    # Comments go FIRST, the same order ``loads_script`` uses. A commented-out
    # ``<script src=...>`` is inert markup, but the unterminated-script sweep
    # below cannot tell that: it saw the opening tag, dropped the whole rest of
    # the document, and reported a content-rich page as having zero readable
    # text — which then escalated it to a browser render as a "JS shell".
    text = strip_comments(text)
    text = _NON_TEXT_SUBTREES.sub(" ", text)
    text = _UNCLOSED_NON_TEXT.sub(" ", text)
    text = _TAGS.sub(" ", text)
    return len("".join(text.split()))


def strip_comments(text: str) -> str:
    """Remove HTML comments in ONE linear pass.

    A backtracking regex over a 256 KB window of hostile markup is a denial-of-
    service surface for a check that runs on every 2xx response; scanning for
    the delimiters directly is linear whatever the input looks like. An
    unterminated comment inside the truncated window discards the remainder,
    matching how a browser treats it.
    """
    if _COMMENT_OPEN not in text:
        return text
    out: list[str] = []
    index = 0
    while True:
        start = text.find(_COMMENT_OPEN, index)
        if start < 0:
            out.append(text[index:])
            return "".join(out)
        out.append(text[index:start])
        end = text.find(_COMMENT_CLOSE, start + len(_COMMENT_OPEN))
        if end < 0:
            return "".join(out)
        out.append(" ")
        index = end + len(_COMMENT_CLOSE)


def loads_script(body: bytes, *, scan_bytes: int, min_inline_chars: int) -> bool:
    """Whether the document plausibly builds its content client-side.

    True for an external ``<script src>`` or an inline script large enough to
    be application code. Requiring this alongside a low text count is what keeps
    a genuinely short server-rendered page (a two-line contact page) from paying
    for a browser render it would gain nothing from.
    """

    if not body:
        return False
    # Comments are stripped first: a commented-out ``<script src=...>`` is
    # inert markup, and treating it as evidence that the page builds itself
    # client-side would escalate a plain static page to a browser render.
    text = strip_comments(body[: max(0, scan_bytes)].decode("utf-8", errors="replace"))
    if _SCRIPT_SRC.search(text):
        return True
    return any(
        len(match.group(1).strip()) >= min_inline_chars
        for match in _INLINE_SCRIPT.finditer(text)
    )


def curl_trigger_for_result(
    result: FetchResult,
    *,
    has_challenge_marker: bool,
    trigger_statuses: tuple[int, ...],
    low_content_bytes: int,
    js_shell_min_text_chars: int = 0,
    js_shell_min_inline_script_chars: int = 0,
    js_shell_scan_bytes: int = 0,
) -> str | None:
    """Return the sole configured reason that permits a later rung.

    Priority is deterministic and evidence-based. A regular timeout, policy
    rejection, redirect issue, or oversized response does not get retried by a
    different transport.

    ``js_shell`` is checked last and only for an ample-sized 2xx response, so a
    tiny body still reports the more specific ``low_content``.
    """

    if has_challenge_marker:
        return ACQUISITION_TRIGGER_CHALLENGE
    if result.status_code in trigger_statuses:
        return ACQUISITION_TRIGGER_BLOCK_STATUS
    if not 200 <= result.status_code < 300:
        return None
    if low_content_bytes and result.decoded_bytes < low_content_bytes:
        return ACQUISITION_TRIGGER_LOW_CONTENT
    if not js_shell_min_text_chars or not js_shell_scan_bytes:
        return None
    if readable_text_length(result.body, scan_bytes=js_shell_scan_bytes) >= (
        js_shell_min_text_chars
    ):
        return None
    if not loads_script(
        result.body,
        scan_bytes=js_shell_scan_bytes,
        min_inline_chars=js_shell_min_inline_script_chars,
    ):
        return None
    return ACQUISITION_TRIGGER_JS_SHELL
