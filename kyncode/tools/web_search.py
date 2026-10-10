from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
from pydantic import BaseModel, Field

from kyncode.tools.base import Tool, ToolResult

SEARCH_URL = "https://html.duckduckgo.com/html/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 KynCode/0.1"
)
TIMEOUT = 15.0


class Params(BaseModel):
    query: str = Field(description="The search query")
    max_results: int = Field(default=5, description="Maximum number of results (1-10)")


class _ResultParser(HTMLParser):
    """从 DuckDuckGo html 版结果页里抽标题链接和摘要。

    结果结构不稳定，只在真正需要时按 class 匹配：标题在 a.result__a，摘要在
    a.result__snippet 或 .result__snippet。抽不到就返回空，交给上层报错。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._in_title = False
        self._in_snippet = False
        self._href = ""
        self._title_parts: list[str] = []
        self._snippet_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attr = dict(attrs)
        classes = (attr.get("class") or "").split()
        href = attr.get("href") or ""
        if "result__a" in classes:
            self._in_title = True
            self._href = href
            self._title_parts = []
        elif "result__snippet" in classes:
            self._in_snippet = True
            self._snippet_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
        elif self._in_snippet:
            self._snippet_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a":
            return
        if self._in_title:
            self._in_title = False
            self.results.append(
                {
                    "title": " ".join("".join(self._title_parts).split()),
                    "url": _unwrap_ddg_url(self._href),
                    "snippet": "",
                }
            )
        elif self._in_snippet:
            self._in_snippet = False
            if self.results and not self.results[-1]["snippet"]:
                self.results[-1]["snippet"] = " ".join(
                    "".join(self._snippet_parts).split()
                )


def _unwrap_ddg_url(href: str) -> str:
    """DDG 把外链包成 /l/?uddg=<encoded>，拆回真实 URL。"""
    if not href:
        return href
    if href.startswith("//"):
        href = "https:" + href
    split = urlsplit(href)
    if "duckduckgo.com" in split.netloc and split.path.startswith("/l/"):
        qs = parse_qs(split.query)
        if "uddg" in qs:
            return unquote(qs["uddg"][0])
    return href


class WebSearch(Tool):
    name = "WebSearch"
    description = (
        "Search the web for a query and return the top results as title, URL and snippet. Set "
        "max_results (1-10) to control how many are returned. Use WebFetch to read a result's full page."
    )
    params_model = Params
    category = "read"

    async def execute(self, params: Params) -> ToolResult:
        query = params.query.strip()
        if not query:
            return ToolResult(output="Error: empty query", is_error=True)
        limit = min(max(1, params.max_results), 10)

        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=TIMEOUT,
                headers={"User-Agent": USER_AGENT},
            ) as client:
                resp = await client.get(SEARCH_URL, params={"q": query})
        except httpx.HTTPError as e:
            return ToolResult(output=f"Error searching: {e}", is_error=True)

        if resp.status_code >= 400:
            return ToolResult(
                output=f"Error: search returned HTTP {resp.status_code}",
                is_error=True,
            )

        results = self._parse(resp.text)[:limit]
        if not results:
            return ToolResult(
                output=f"No results found for '{query}'.", is_error=True
            )
        return ToolResult(output=_format(query, results))

    @staticmethod
    def _parse(html: str) -> list[dict[str, str]]:
        parser = _ResultParser()
        try:
            parser.feed(html)
        except Exception:  # noqa: BLE001 — 畸形 HTML 不该让整次搜索失败
            return []
        seen: set[str] = set()
        deduped: list[dict[str, str]] = []
        for r in parser.results:
            if not r["url"] or r["url"] in seen:
                continue
            seen.add(r["url"])
            deduped.append(r)
        return deduped


def _format(query: str, results: list[dict[str, str]]) -> str:
    lines = [f"Search results for: {query}", ""]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}")
        lines.append(f"   {r['url']}")
        if r["snippet"]:
            lines.append(f"   {r['snippet']}")
        lines.append("")
    return "\n".join(lines).rstrip()
