from __future__ import annotations

from urllib.parse import urljoin

import httpx
from pydantic import BaseModel, Field

from kyncode.tools.base import Tool, ToolResult
from kyncode.web.html_text import html_to_text
from kyncode.web.url_guard import BlockedURLError, is_blocked_ip_string, validate_url

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 KynCode/0.1"
)
TIMEOUT = 20.0
MAX_BYTES = 2_000_000  # 最多读 2MB，防止超大响应把内存和上下文撑爆

# 重定向跟随上限 + 需要手动跟随的状态码。手动跟随（而非 httpx 的 follow_redirects）
# 是为了对每一跳的 Location 都重新过一遍 SSRF 校验——否则公网 URL 一个 302 就能
# 把请求拐到内网。
MAX_REDIRECTS = 5
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})

# 只处理文本类响应。二进制（图片、PDF、压缩包）转不成文本，直接告诉模型换 URL。
_TEXTUAL_PREFIXES = ("text/",)
_TEXTUAL_EXACT = (
    "application/json",
    "application/xml",
    "application/xhtml+xml",
    "application/javascript",
    "application/rss+xml",
    "application/atom+xml",
)


class Params(BaseModel):
    url: str = Field(
        min_length=1, max_length=2048, description="Absolute http(s) URL to fetch"
    )
    max_chars: int = Field(
        default=8000,
        ge=1,
        description="Maximum characters of extracted text to return (default 8000)",
    )


class WebFetch(Tool):
    name = "WebFetch"
    description = (
        "Fetch an http(s) URL and return its content as plain text (HTML is converted to text). "
        "Only textual content is supported. Set max_chars to cap the returned text (default 8000). "
        "Use this to read a web page when you already know its URL."
    )
    params_model = Params
    category = "network"

    async def execute(self, params: Params) -> ToolResult:
        try:
            validate_url(params.url)
        except BlockedURLError as e:
            return ToolResult(output=f"Error: {e}", is_error=True)

        max_chars = max(1, params.max_chars)
        url = params.url

        try:
            async with httpx.AsyncClient(
                follow_redirects=False,
                timeout=TIMEOUT,
                headers={"User-Agent": USER_AGENT},
            ) as client:
                for _ in range(MAX_REDIRECTS):
                    async with client.stream("GET", url) as resp:
                        if resp.status_code in _REDIRECT_CODES:
                            location = resp.headers.get("location")
                            if not location:
                                return ToolResult(
                                    output=f"Error: redirect without Location for {url}",
                                    is_error=True,
                                )
                            url = urljoin(url, location)
                            try:
                                validate_url(url)
                            except BlockedURLError as e:
                                return ToolResult(
                                    output=f"Error: redirect blocked: {e}",
                                    is_error=True,
                                )
                            continue

                        if resp.status_code >= 400:
                            return ToolResult(
                                output=f"Error: HTTP {resp.status_code} for {url}",
                                is_error=True,
                            )

                        # DNS rebinding 兜底：DNS 校验和真实连接是两次解析，
                        # 攻击者可控域名可能让两次结果不同。这里校验实际连接的 peer IP。
                        peer_ip = _extract_peer_ip(resp)
                        if peer_ip is not None and is_blocked_ip_string(peer_ip):
                            return ToolResult(
                                output=f"Error: connected to blocked address {peer_ip}",
                                is_error=True,
                            )

                        content_type = resp.headers.get("content-type", "")
                        if not _is_textual(content_type):
                            return ToolResult(
                                output=(
                                    f"Error: unsupported content-type "
                                    f"'{content_type or 'unknown'}' for {url}; "
                                    f"only text content can be fetched"
                                ),
                                is_error=True,
                            )

                        body = await _read_capped(resp)
                        status_code = resp.status_code
                        break
                else:
                    return ToolResult(
                        output=f"Error: too many redirects (> {MAX_REDIRECTS})",
                        is_error=True,
                    )
        except httpx.HTTPError as e:
            return ToolResult(output=f"Error fetching {url}: {e}", is_error=True)

        text = body.decode(resp.encoding or "utf-8", errors="replace")
        if "html" in content_type.lower() or _looks_like_html(text):
            text = html_to_text(text)

        truncated = len(text) > max_chars
        text = text[:max_chars]
        if truncated:
            text += f"\n\n... (truncated at {max_chars} chars)"

        header = f"# {url} (HTTP {status_code})"
        return ToolResult(output=f"{header}\n\n{text}" if text else header)


def _extract_peer_ip(resp: httpx.Response) -> str | None:
    """从响应的底层 socket 流里取实际连接的对端 IP，用于 DNS rebinding 兜底。

    MockTransport / 某些 transport 没有 network_stream，或拿不到 peername，此时返回
    None，调用方跳过校验（宁可放过也不误拦）。
    """
    stream = resp.extensions.get("network_stream")
    if stream is None:
        return None
    try:
        peername = stream.get_extra_info("peername")
    except Exception:
        return None
    if not peername:
        return None
    return peername[0]


def _is_textual(content_type: str) -> bool:
    if not content_type:
        # 没给 content-type 时乐观处理：很多站点故意省略。真读到二进制会在
        # decode(errors="replace") 后变成乱码，模型能看出来。
        return True
    ct = content_type.split(";", 1)[0].strip().lower()
    return ct.startswith(_TEXTUAL_PREFIXES) or ct in _TEXTUAL_EXACT


async def _read_capped(resp: httpx.Response) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in resp.aiter_bytes():
        chunks.append(chunk)
        total += len(chunk)
        if total >= MAX_BYTES:
            break
    return b"".join(chunks)


def _looks_like_html(text: str) -> bool:
    head = text.lstrip()[:200].lower()
    return head.startswith(("<!doctype html", "<html"))
