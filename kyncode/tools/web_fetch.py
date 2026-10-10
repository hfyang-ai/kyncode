from __future__ import annotations

import httpx
from pydantic import BaseModel, Field

from kyncode.tools.base import Tool, ToolResult
from kyncode.web.html_text import html_to_text
from kyncode.web.url_guard import BlockedURLError, validate_url

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 KynCode/0.1"
)
TIMEOUT = 20.0
MAX_BYTES = 2_000_000  # 最多读 2MB，防止超大响应把内存和上下文撑爆

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
    url: str = Field(description="Absolute http(s) URL to fetch")
    max_chars: int = Field(
        default=8000,
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
    category = "read"

    async def execute(self, params: Params) -> ToolResult:
        try:
            validate_url(params.url)
        except BlockedURLError as e:
            return ToolResult(output=f"Error: {e}", is_error=True)

        max_chars = max(1, params.max_chars)

        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=TIMEOUT,
                headers={"User-Agent": USER_AGENT},
            ) as client, client.stream("GET", params.url) as resp:
                if resp.status_code >= 400:
                    return ToolResult(
                        output=f"Error: HTTP {resp.status_code} for {params.url}",
                        is_error=True,
                    )

                content_type = resp.headers.get("content-type", "")
                if not _is_textual(content_type):
                    return ToolResult(
                        output=(
                            f"Error: unsupported content-type '{content_type or 'unknown'}' "
                            f"for {params.url}; only text content can be fetched"
                        ),
                        is_error=True,
                    )

                body = await _read_capped(resp)
        except httpx.HTTPError as e:
            return ToolResult(output=f"Error fetching {params.url}: {e}", is_error=True)

        text = body.decode(resp.encoding or "utf-8", errors="replace")
        if "html" in content_type.lower() or _looks_like_html(text):
            text = html_to_text(text)

        truncated = len(text) > max_chars
        text = text[:max_chars]
        if truncated:
            text += f"\n\n... (truncated at {max_chars} chars)"

        header = f"# {resp.url!s} (HTTP {resp.status_code})"
        return ToolResult(output=f"{header}\n\n{text}" if text else header)


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
