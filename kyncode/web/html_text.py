"""极简 HTML -> 纯文本转换。

用标准库 html.parser 而不是引入 beautifulsoup4/lxml：只想把 script/style/注释
剔掉、把块级标签换成换行、解码实体、压掉多余空白，这些用不到完整 DOM。正文质量
不如 readability 那类库，但对「把网页喂给模型读」这个用途够用，且零新依赖。
"""

from __future__ import annotations

from html.parser import HTMLParser

# 这些标签的内容整段丢弃（脚本、样式、元数据、隐藏结构）
_SKIP_TAGS = {
    "script",
    "style",
    "noscript",
    "template",
    "head",
    "svg",
    "canvas",
    "iframe",
}
# 块级标签：结束时要补一个换行，避免相邻段落粘成一行
_BLOCK_TAGS = {
    "p",
    "div",
    "br",
    "hr",
    "li",
    "tr",
    "section",
    "article",
    "header",
    "footer",
    "nav",
    "main",
    "aside",
    "blockquote",
    "pre",
    "table",
    "ul",
    "ol",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            if self._skip_depth > 0:
                self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 — 解析器对畸形 HTML 一般不抛，真抛了就走兜底
        # 退回粗暴剥标签的策略
        return _strip_tags_fallback(html)
    return _normalize(parser.get_text())


def _strip_tags_fallback(html: str) -> str:
    out: list[str] = []
    in_tag = False
    for ch in html:
        if ch == "<":
            in_tag = True
        elif ch == ">":
            in_tag = False
        elif not in_tag:
            out.append(ch)
    return _normalize("".join(out))


def _normalize(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.splitlines()]
    # 折叠连续空行为单个空行，并去掉首尾空行
    collapsed: list[str] = []
    for line in lines:
        if line or (collapsed and collapsed[-1]):
            collapsed.append(line)
    return "\n".join(collapsed).strip()
