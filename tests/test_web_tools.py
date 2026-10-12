from __future__ import annotations

import httpx
import pytest

from kyncode.tools.web_fetch import WebFetch
from kyncode.tools.web_search import WebSearch
from kyncode.web.html_text import html_to_text
from kyncode.web.url_guard import BlockedURLError, validate_url

# ── url_guard ──


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost/admin",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://2130706433/",  # 127.0.0.1 的十进制写法
        "file:///etc/passwd",
        "ftp://example.com/",
    ],
)
def test_validate_url_blocks_ssrf(url: str) -> None:
    with pytest.raises(BlockedURLError):
        validate_url(url)


def test_validate_url_allows_public_literal_ip() -> None:
    assert validate_url("https://1.1.1.1/") == "https://1.1.1.1/"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/?token=abc",
        "https://example.com/?api_key=sk-123",
        "https://example.com/path?client_secret=x",
        "https://example.com/path?access_token=yyy",
        "https://example.com/path#private_key=zzz",
    ],
)
def test_validate_url_blocks_sensitive_params(url: str) -> None:
    with pytest.raises(BlockedURLError):
        validate_url(url)


def test_validate_url_allows_ordinary_params() -> None:
    # 普通参数（page/sort）不误伤；用公网字面量 IP 避免触发真实 DNS
    assert validate_url("https://1.1.1.1/?page=2&sort=asc") == (
        "https://1.1.1.1/?page=2&sort=asc"
    )


# ── html_to_text ──


def test_html_to_text_strips_script_and_style() -> None:
    html = """
    <html><head><title>t</title>
    <style>.a{color:red}</style></head>
    <body><h1>Hello</h1><script>alert(1)</script>
    <p>World &amp; friends</p></body></html>
    """
    text = html_to_text(html)
    assert "Hello" in text
    assert "World & friends" in text
    assert "alert" not in text
    assert "color:red" not in text
    assert "title" not in text


def test_html_to_text_separates_blocks() -> None:
    text = html_to_text("<p>one</p><p>two</p>")
    assert [line for line in text.splitlines() if line] == ["one", "two"]


# ── WebFetch.execute ──


def _patch_client(monkeypatch, handler) -> None:
    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        # 尊重调用方传的 follow_redirects（web_fetch 手动跟随重定向时传 False）
        return real(transport=transport, **kwargs)

    monkeypatch.setattr("kyncode.tools.web_fetch.httpx.AsyncClient", factory)


async def test_web_fetch_returns_text(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="<html><body><p>hello world</p></body></html>",
        )

    _patch_client(monkeypatch, handler)
    # validate_url 会真的做 DNS，换成放行以免测试依赖网络
    monkeypatch.setattr(
        "kyncode.tools.web_fetch.validate_url", lambda url: url
    )

    result = await WebFetch().execute(
        WebFetch.params_model(url="https://example.com/")
    )
    assert not result.is_error
    assert "hello world" in result.output
    assert "HTTP 200" in result.output


async def test_web_fetch_rejects_binary_content_type(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=b"\x89PNG",
        )

    _patch_client(monkeypatch, handler)
    monkeypatch.setattr("kyncode.tools.web_fetch.validate_url", lambda url: url)

    result = await WebFetch().execute(
        WebFetch.params_model(url="https://example.com/x.png")
    )
    assert result.is_error
    assert "content-type" in result.output


async def test_web_fetch_truncates(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            text="a" * 500,
        )

    _patch_client(monkeypatch, handler)
    monkeypatch.setattr("kyncode.tools.web_fetch.validate_url", lambda url: url)

    result = await WebFetch().execute(
        WebFetch.params_model(url="https://example.com/", max_chars=100)
    )
    assert not result.is_error
    assert "truncated" in result.output


async def test_web_fetch_blocks_private_url() -> None:
    result = await WebFetch().execute(
        WebFetch.params_model(url="http://127.0.0.1:8080/")
    )
    assert result.is_error
    assert "blocked" in result.output.lower()


async def test_web_fetch_follows_redirect(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://example.com/final"})
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<p>final content</p>",
        )

    _patch_client(monkeypatch, handler)
    monkeypatch.setattr("kyncode.tools.web_fetch.validate_url", lambda url: url)

    result = await WebFetch().execute(
        WebFetch.params_model(url="https://example.com/start")
    )
    assert not result.is_error
    assert "final content" in result.output


async def test_web_fetch_blocks_redirect_to_private(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302, headers={"location": "http://169.254.169.254/latest/meta-data/"}
        )

    _patch_client(monkeypatch, handler)

    # 初始 URL 放行，重定向目标（内网）被校验拦截
    def fake_validate(url: str) -> str:
        if "169.254.169.254" in url:
            raise BlockedURLError("blocked address: 169.254.169.254")
        return url

    monkeypatch.setattr("kyncode.tools.web_fetch.validate_url", fake_validate)

    result = await WebFetch().execute(
        WebFetch.params_model(url="https://example.com/start")
    )
    assert result.is_error
    assert "redirect blocked" in result.output


async def test_web_fetch_blocks_connected_private_ip(monkeypatch) -> None:
    class _FakeStream:
        def get_extra_info(self, name: str):
            if name == "peername":
                return ("127.0.0.1", 12345)
            return None

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            text="should not be read",
            extensions={"network_stream": _FakeStream()},
        )

    _patch_client(monkeypatch, handler)
    monkeypatch.setattr("kyncode.tools.web_fetch.validate_url", lambda url: url)

    result = await WebFetch().execute(
        WebFetch.params_model(url="https://example.com/")
    )
    assert result.is_error
    assert "connected to blocked address" in result.output


# ── WebSearch ──


def test_web_search_parse_extracts_results() -> None:
    html = """
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fp">
      Example Title</a>
    <a class="result__snippet">A short snippet.</a>
    <a class="result__a" href="https://other.com/q">Other</a>
    """
    results = WebSearch._parse(html)
    assert results[0]["title"] == "Example Title"
    assert results[0]["url"] == "https://example.com/p"
    assert results[0]["snippet"] == "A short snippet."
    assert results[1]["url"] == "https://other.com/q"


async def test_web_search_formats_output(monkeypatch) -> None:
    html = (
        '<a class="result__a" href="https://example.com/">'
        "Example</a>"
        '<a class="result__snippet">desc</a>'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(
        "kyncode.tools.web_search.httpx.AsyncClient",
        lambda *a, **k: real(transport=transport),
    )

    result = await WebSearch().execute(
        WebSearch.params_model(query="kyncode")
    )
    assert not result.is_error
    assert "https://example.com/" in result.output
    assert "Example" in result.output


async def test_web_search_empty_query() -> None:
    result = await WebSearch().execute(WebSearch.params_model(query="   "))
    assert result.is_error


async def test_web_search_no_results(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<html></html>")

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(
        "kyncode.tools.web_search.httpx.AsyncClient",
        lambda *a, **k: real(transport=transport),
    )
    result = await WebSearch().execute(WebSearch.params_model(query="nothing"))
    assert result.is_error


# ── registry gating ──


def test_registry_includes_web_tools_by_default() -> None:
    from kyncode.tools import create_default_registry

    registry = create_default_registry()
    assert registry.get("WebSearch") is not None
    assert registry.get("WebFetch") is not None


def test_registry_omits_web_tools_when_disabled() -> None:
    from kyncode.tools import create_default_registry

    registry = create_default_registry(web_access=False)
    assert registry.get("WebSearch") is None
    assert registry.get("WebFetch") is None

