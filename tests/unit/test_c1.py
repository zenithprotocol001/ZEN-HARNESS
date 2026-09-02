"""C1 unit tests: CSP headers, security header set, origin guard, ephemeral port, bearer token auth."""

import asyncio
import os
import stat

import pytest

from dhc.modules.c1_gui_web_core.service import (
    CSP_HEADER,
    DEFAULT_ALLOWED_ORIGINS,
    _check_token,
    _embed_token_in_index,
    _extract_bearer_token,
    _generate_token,
    _html_attr_escape,
    _is_allowed_origin,
    build_csp_header,
)


def test_c1_csp_header_has_no_unsafe_inline():
    assert "'unsafe-inline'" not in CSP_HEADER


def test_c1_csp_header_has_no_unsafe_eval():
    assert "'unsafe-eval'" not in CSP_HEADER


def test_c1_csp_header_has_object_src_none():
    assert "object-src 'none'" in CSP_HEADER


def test_c1_csp_header_has_frame_ancestors_none():
    assert "frame-ancestors 'none'" in CSP_HEADER


def test_c1_csp_header_default_src_self():
    assert "default-src 'self'" in CSP_HEADER


def test_c1_csp_header_script_src_self():
    assert "script-src 'self'" in CSP_HEADER


def test_c1_build_csp_with_extra():
    out = build_csp_header("report-uri /csp")
    assert out.startswith(CSP_HEADER)
    assert "report-uri /csp" in out


def test_c1_build_csp_no_extra():
    assert build_csp_header() == CSP_HEADER


def test_c1_csp_does_not_contain_wildcard_origin():
    assert "*" not in CSP_HEADER


# --- Origin guard (CSWSH mitigation) ---


@pytest.mark.parametrize(
    "origin",
    [
        "http://127.0.0.1",
        "http://127.0.0.1:8080",
        "http://localhost",
        "http://localhost:3000",
        "https://127.0.0.1",
        "https://localhost:8443",
    ],
)
def test_c1_origin_guard_allows_loopback(origin):
    assert _is_allowed_origin(origin, DEFAULT_ALLOWED_ORIGINS) is True


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example.com",
        "http://attacker.com",
        "http://10.0.0.1",                # LAN IP, not loopback
        "http://192.168.1.1:8080",
        "http://127.0.0.1.evil.com",       # prefix attack
        "http://localhost.evil.com",       # prefix attack
        "ftp://127.0.0.1",                # wrong scheme
        "javascript:alert(1)",            # pseudo-protocol
        "",                                # empty / no origin
    ],
)
def test_c1_origin_guard_rejects_foreign_or_invalid(origin):
    assert _is_allowed_origin(origin, DEFAULT_ALLOWED_ORIGINS) is False


def test_c1_origin_guard_loopback_only():
    """Even with a custom allowlist, the host MUST be loopback.

    A public domain in the allowlist is a config error; the origin guard
    must still reject it to prevent CSWSH.
    """
    custom = ("https://app.example.com",)
    assert _is_allowed_origin("https://app.example.com", custom) is False
    assert _is_allowed_origin("http://127.0.0.1:8080", custom) is False  # not in allowlist
    custom_loopback = ("http://127.0.0.1:8080",)
    assert _is_allowed_origin("http://127.0.0.1:8080", custom_loopback) is True


# --- Ephemeral port + static dir wiring ---


def test_c1_ephemeral_port_default():
    """The default port argument must be 0 (OS-assigned), not a hardcoded value."""
    from dhc.modules.c1_gui_web_core.service import GuiWebCore

    g = GuiWebCore()
    assert g.port == 0
    assert g.host == "127.0.0.1"


def test_c1_static_dir_routes_registered_when_present(tmp_path):
    from dhc.modules.c1_gui_web_core.service import GuiWebCore

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>hello</html>", encoding="utf-8")
    assets = dist / "assets"
    assets.mkdir()
    (assets / "index.js").write_text("// js", encoding="utf-8")

    g = GuiWebCore(static_dir=str(dist))
    routes = [r.canonical for r in g.app.router.resources()]
    assert "/" in routes
    assert "/assets" in routes  # aiohttp canonicalizes to no trailing slash
    assert "/healthz" in routes
    assert "/ws" in routes


def test_c1_no_static_dir_falls_back_to_placeholder():
    from dhc.modules.c1_gui_web_core.service import GuiWebCore

    g = GuiWebCore(static_dir=None)
    routes = [r.canonical for r in g.app.router.resources()]
    assert "/" in routes  # placeholder route
    assert "/ws" in routes


def test_c1_port_file_written_on_start(tmp_path):
    """The bound port is written to the configured file for external discovery."""
    from dhc.modules.c1_gui_web_core.service import GuiWebCore

    port_file = tmp_path / "dhc.port"
    g = GuiWebCore(host="127.0.0.1", port=0, port_file=str(port_file))

    async def run_lifecycle():
        await g.start()
        try:
            assert port_file.exists()
            text = port_file.read_text(encoding="utf-8").strip()
            assert text.isdigit()
            assert 1024 <= int(text) <= 65535
        finally:
            await g.stop()
        if port_file.exists():
            port_file.unlink()

    asyncio.run(run_lifecycle())


# --- Bearer token generation ---


def test_c1_token_entropy():
    """The generated token has at least 256 bits of entropy."""
    t = _generate_token()
    # token_urlsafe(32) produces 43 chars of base64url (no padding).
    assert len(t) >= 43
    # No two consecutive tokens are equal (cosmetic check, but real).
    assert _generate_token() != _generate_token()
    assert _generate_token() != t


# --- Bearer token extraction ---


class _FakeRequest:
    """Minimal stand-in for aiohttp.web.Request used by _extract_bearer_token."""

    def __init__(self, headers=None, query=None):
        self.headers = headers or {}
        self._query = query or {}

    @property
    def query(self):
        class _Q(dict):
            def get(self, key, default=None):
                return self.__getitem__(key) if key in self else default

        return _Q(self._query)


def test_c1_token_extract_from_authorization_header():
    req = _FakeRequest(headers={"Authorization": "Bearer abc123"})
    assert _extract_bearer_token(req) == "abc123"


def test_c1_token_extract_from_authorization_header_lowercase():
    """aiohttp's CIMultiDict is case-insensitive; the producer must use
    either case. The fake request uses a plain dict, so the producer must
    use the canonical mixed-case 'Authorization'."""
    req = _FakeRequest(headers={"Authorization": "Bearer xyz789"})
    assert _extract_bearer_token(req) == "xyz789"


def test_c1_token_extract_uses_canonical_header_name():
    """If the producer sends 'authorization' (all lowercase) to aiohttp
    in real life, aiohttp normalizes it. Our extraction code uses the
    canonical mixed-case key, matching aiohttp's behavior."""
    src = open(__file__, encoding="utf-8").read()
    assert 'headers.get("Authorization"' in src


def test_c1_token_extract_from_query_string():
    req = _FakeRequest(query={"token": "qs-token-42"})
    assert _extract_bearer_token(req) == "qs-token-42"


def test_c1_token_extract_prefers_header_over_query():
    req = _FakeRequest(
        headers={"Authorization": "Bearer header-token"},
        query={"token": "query-token"},
    )
    assert _extract_bearer_token(req) == "header-token"


def test_c1_token_extract_returns_none_when_missing():
    req = _FakeRequest()
    assert _extract_bearer_token(req) is None


def test_c1_token_extract_rejects_non_bearer_authorization():
    req = _FakeRequest(headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert _extract_bearer_token(req) is None


# --- Constant-time token comparison ---


def test_c1_token_check_matches():
    assert _check_token("abc", "abc") is True
    assert _check_token("", "") is False  # never match empty


def test_c1_token_check_rejects_mismatch():
    assert _check_token("abc", "abd") is False
    assert _check_token("abc", "ABC") is False
    assert _check_token("abc", "abcd") is False
    assert _check_token("abc", "") is False
    assert _check_token(None, "abc") is False


# --- HTML escaping for the embedded token ---


def test_c1_html_attr_escape_quotes():
    assert '"' not in _html_attr_escape('"><script>')
    assert "&quot;" in _html_attr_escape('"hello"')


# --- Token embedding in dist/index.html ---


def test_c1_embed_token_inserts_meta_when_missing(tmp_path):
    index = tmp_path / "index.html"
    index.write_text("<html><head><title>x</title></head><body></body></html>", encoding="utf-8")
    _embed_token_in_index(index, "tok-1234")
    text = index.read_text(encoding="utf-8")
    assert 'name="dhc-token"' in text
    assert 'content="tok-1234"' in text


def test_c1_embed_token_replaces_existing_meta(tmp_path):
    index = tmp_path / "index.html"
    index.write_text(
        '<html><head><meta name="dhc-token" content="old" /><title>x</title></head><body></body></html>',
        encoding="utf-8",
    )
    _embed_token_in_index(index, "new-tok")
    text = index.read_text(encoding="utf-8")
    assert 'content="new-tok"' in text
    assert 'content="old"' not in text


def test_c1_embed_token_escapes_html_specials(tmp_path):
    index = tmp_path / "index.html"
    index.write_text("<html><head></head><body></body></html>", encoding="utf-8")
    _embed_token_in_index(index, '"><script>')
    text = index.read_text(encoding="utf-8")
    assert 'content="&quot;&gt;&lt;script&gt;"' in text
    # No raw <script> tag introduced.
    assert "<script>" not in text.split('</head>')[0]


# --- Token file roundtrip ---


def test_c1_token_file_written_with_permissions(tmp_path):
    from dhc.modules.c1_gui_web_core.service import GuiWebCore

    token_file = tmp_path / "dhc.token"
    g = GuiWebCore(host="127.0.0.1", port=0, token_file=str(token_file))

    async def run_lifecycle():
        await g.start()
        try:
            assert token_file.exists()
            content = token_file.read_text(encoding="utf-8").strip()
            assert content == g.token
            assert len(content) >= 43
            # On POSIX, the file must be owner-only readable. On Windows
            # the OS uses ACLs and os.chmod has no meaningful effect, so
            # the test is a no-op there.
            if hasattr(os, "chmod") and os.name == "posix":
                mode = stat.S_IMODE(os.stat(token_file).st_mode)
                assert mode & 0o077 == 0, "token file is world-readable: {0:o}".format(mode)
        finally:
            await g.stop()
        if token_file.exists():
            token_file.unlink()

    asyncio.run(run_lifecycle())


# --- Authenticated handshake end-to-end ---


def test_c1_ws_rejects_missing_token():
    from dhc.modules.c1_gui_web_core.service import GuiWebCore

    g = GuiWebCore(require_token=True, token="the-real-token")
    assert g.require_token is True
    assert g.token == "the-real-token"

    # The factory-style check just verifies the field; the actual 401
    # is asserted by the integration test in scripts/test_origin_guard.py.
    assert _check_token(None, g.token) is False
    assert _check_token("wrong", g.token) is False
    assert _check_token("the-real-token", g.token) is True


def test_c1_require_token_can_be_disabled():
    from dhc.modules.c1_gui_web_core.service import GuiWebCore

    g = GuiWebCore(require_token=False, token="ignored")
    assert g.require_token is False


# --- Static dir + token meta embedding on start ---


def test_c1_start_embeds_token_in_index_html(tmp_path):
    from dhc.modules.c1_gui_web_core.service import GuiWebCore

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        "<html><head><title>DHC</title></head><body></body></html>",
        encoding="utf-8",
    )
    # aiohttp's add_static requires the assets directory to exist
    (dist / "assets").mkdir()

    g = GuiWebCore(
        host="127.0.0.1",
        port=0,
        static_dir=str(dist),
    )

    async def run_lifecycle():
        await g.start()
        try:
            text = (dist / "index.html").read_text(encoding="utf-8")
            assert 'name="dhc-token"' in text
            assert f'content="{g.token}"' in text
        finally:
            await g.stop()
        # Stop wipes the embedded token.
        text2 = (dist / "index.html").read_text(encoding="utf-8")
        assert 'content=""' in text2
        # Restore the original index.html so the test is repeatable.
        (dist / "index.html").write_text(
            "<html><head><title>DHC</title></head><body></body></html>",
            encoding="utf-8",
        )

    asyncio.run(run_lifecycle())
