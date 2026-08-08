"""Capability and trigger policy for the Site Health acquisition ladder."""

from __future__ import annotations

import pytest

from app.connectors.web_evidence import acquisition
from app.connectors.web_evidence.contracts import FetchResult


def test_curl_pinned_resolution_probe_is_fail_closed_on_windows(monkeypatch) -> None:
    monkeypatch.setattr(acquisition.sys, "platform", "win32")
    assert acquisition.curl_cffi_pinned_resolution_supported() is False


def test_curl_pinned_resolution_probe_checks_installed_binding(monkeypatch) -> None:
    monkeypatch.setattr(acquisition.sys, "platform", "linux")
    assert acquisition.curl_cffi_pinned_resolution_supported() is True


def test_curl_pinned_resolution_probe_fails_closed_on_binding_error(
    monkeypatch,
) -> None:
    import curl_cffi

    class IncompatibleCurl:
        def setopt(self, *_args) -> None:
            raise AttributeError("RESOLVE is unavailable")

        def close(self) -> None:
            return None

    monkeypatch.setattr(acquisition.sys, "platform", "linux")
    monkeypatch.setattr(curl_cffi, "Curl", IncompatibleCurl)

    assert acquisition.curl_cffi_pinned_resolution_supported() is False


def _result(body: bytes, *, status_code: int = 200) -> FetchResult:
    """A minimal 2xx HTML result carrying ``body``."""
    return FetchResult(
        requested_url="https://example.test/",
        final_url="https://example.test/",
        status_code=status_code,
        redacted_headers={},
        content_type="text/html",
        http_version="1.1",
        body=body,
        wire_bytes=len(body),
        decoded_bytes=len(body),
        ttfb_ms=None,
        latency_ms=None,
    )


def _trigger(body: bytes, *, status_code: int = 200) -> str | None:
    return acquisition.curl_trigger_for_result(
        _result(body, status_code=status_code),
        has_challenge_marker=False,
        trigger_statuses=(403, 429, 503),
        low_content_bytes=512,
        js_shell_min_text_chars=600,
        js_shell_min_inline_script_chars=1024,
        js_shell_scan_bytes=262_144,
    )


# The shape the browser rung exists for: ample bytes, a real bundle reference,
# and an empty mount point. Measured live, this is what never escalated.
_JS_SHELL = (
    b"<!doctype html><html><head><title>App</title>"
    + b"<meta name='description' content='x'>" * 40
    + b"</head><body><div id='root'></div>"
    + b"<script src='/static/bundle.js'></script></body></html>"
)


def test_js_shell_escalates_despite_ample_bytes() -> None:
    assert len(_JS_SHELL) > 512
    assert _trigger(_JS_SHELL) == "js_shell"


def test_server_rendered_page_does_not_escalate() -> None:
    body = (
        b"<!doctype html><html><body><h1>Admissions</h1>"
        + b"<p>Applications open in January and close in March. </p>" * 20
        + b"<script src='/analytics.js'></script></body></html>"
    )
    assert _trigger(body) is None


def test_inline_bundle_is_not_counted_as_readable_text() -> None:
    """A page whose only bulk is JavaScript is a shell, not a content page."""
    body = (
        b"<!doctype html><html><body><div id='app'></div><script>"
        + b"var a=1;function f(){return a;}" * 200
        + b"</script></body></html>"
    )
    assert _trigger(body) == "js_shell"


def test_short_server_rendered_page_without_script_is_not_a_shell() -> None:
    """Thin but honestly server-rendered: rendering it would add nothing."""
    # Padded with TEXT-FREE markup so the response is comfortably above the
    # 512-byte low-content floor while its readable text stays under the shell
    # floor — otherwise this would silently be testing ``low_content`` instead
    # of the signal it names.
    body = b"<!doctype html><html><head><title>Contact us today</title></head>" + (
        b'<body class="' + b"page-contact-layout-wide " * 40 + b'">'
        b"<h1>Contact</h1>"
        + b"<p>Reception is open weekdays.</p>" * 8
        + b"</body></html>"
    )
    assert len(body) > 512 * 2
    assert acquisition.readable_text_length(body, scan_bytes=262_144) < 600
    assert _trigger(body) is None


def test_tiny_response_reports_low_content_not_js_shell() -> None:
    """The two states stay distinct: nothing sent vs content not in the page."""
    assert _trigger(b"<html><body><div id=root></div></body></html>") == "low_content"


def test_non_2xx_never_reports_a_content_trigger() -> None:
    assert _trigger(_JS_SHELL, status_code=404) is None


def test_block_status_outranks_the_shell_signal() -> None:
    assert _trigger(_JS_SHELL, status_code=503) == "block_status"


def test_shell_signal_is_disabled_by_zero_thresholds() -> None:
    assert (
        acquisition.curl_trigger_for_result(
            _result(_JS_SHELL),
            has_challenge_marker=False,
            trigger_statuses=(403, 429, 503),
            low_content_bytes=512,
            js_shell_min_text_chars=0,
            js_shell_min_inline_script_chars=1024,
            js_shell_scan_bytes=262_144,
        )
        is None
    )


def test_unterminated_script_at_the_scan_boundary_is_not_read_as_text() -> None:
    """A prefix cut mid-``<script>`` must not turn JavaScript into prose."""
    body = b"<html><body><div id=root></div><script>" + b"x=1;" * 500
    assert acquisition.readable_text_length(body, scan_bytes=200) == 0


@pytest.mark.parametrize(
    "close",
    [b"</script>", b"</script >", b"</script\t\n bar>", b"</script/>", b"</SCRIPT>"],
)
def test_every_html_script_end_tag_form_closes_the_subtree(close: bytes) -> None:
    """HTML ends a script at ``</script`` plus whitespace, ``/``, or ``>``.

    Accepting only ``</script\\s*>`` let a page write ``</script bar>``, leave
    the subtree looking unterminated, and have the tail-drop discard the real
    prose after it — reporting a content-rich page as a near-empty shell.
    """
    prose = "Readable prose that must still be counted."
    body = (
        b"<html><body><script>var x=1;"
        + close
        + b"<p>"
        + prose.encode()
        + b"</p></body></html>"
    )
    assert acquisition.readable_text_length(body, scan_bytes=262_144) == len(
        "".join(prose.split())
    )


def test_a_longer_tag_name_does_not_close_a_script() -> None:
    """``</scripting>`` is not a ``script`` end tag; the word boundary holds."""
    body = (
        b"<html><body><script>var x=1;</scripting>still script</script>ok</body></html>"
    )
    assert acquisition.readable_text_length(body, scan_bytes=262_144) == 2


def test_commented_out_script_does_not_erase_the_page_text() -> None:
    """A disabled ``<script>`` is inert markup, not the start of a bundle.

    The unterminated-script sweep saw the opening tag inside the comment and
    dropped the whole rest of the document, so a content-rich page reported
    zero readable text and escalated to a browser render as a "JS shell".
    """
    prose = "Real prose that should count. "
    body = (
        b"<html><body><!-- <script src=x.js> --><p>"
        + (prose * 20).encode()
        + b"</p></body></html>"
    )
    assert acquisition.readable_text_length(body, scan_bytes=262_144) == len(
        "".join((prose * 20).split())
    )


def test_commented_out_script_is_not_evidence_of_client_rendering() -> None:
    """Inert markup must not escalate a static page to a browser render."""
    body = (
        b"<!doctype html><html><body><div id='root'></div>"
        + b"<!-- <script src='/bundle.js'></script> -->"
        + b"</body></html>"
    )
    assert not acquisition.loads_script(body, scan_bytes=262_144, min_inline_chars=1024)
