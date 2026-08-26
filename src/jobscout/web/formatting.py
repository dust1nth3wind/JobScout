"""Safe presentation helpers for externally supplied job content."""

from __future__ import annotations

from html import escape
from html.parser import HTMLParser


_ALLOWED_TAGS = {
    "b",
    "blockquote",
    "br",
    "em",
    "h3",
    "h4",
    "i",
    "li",
    "ol",
    "p",
    "strong",
    "ul",
}
_VOID_TAGS = {"br"}
_IGNORED_CONTENT_TAGS = {"embed", "iframe", "object", "script", "style", "svg"}


class _SafeDescriptionParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored_depth = 0
        self.saw_tag = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        self.saw_tag = True
        if tag in _IGNORED_CONTENT_TAGS:
            self.ignored_depth += 1
        elif not self.ignored_depth and tag in _ALLOWED_TAGS:
            self.parts.append(f"<{tag}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        self.saw_tag = True
        if not self.ignored_depth and tag in _VOID_TAGS:
            self.parts.append(f"<{tag}>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _IGNORED_CONTENT_TAGS and self.ignored_depth:
            self.ignored_depth -= 1
        elif not self.ignored_depth and tag in _ALLOWED_TAGS and tag not in _VOID_TAGS:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth:
            self.parts.append(escape(data))


def safe_description_html(value: str | None) -> str:
    """Keep useful formatting while removing executable or layout-breaking markup."""
    if not value:
        return ""

    parser = _SafeDescriptionParser()
    parser.feed(value)
    parser.close()
    rendered = "".join(parser.parts).strip()
    if not parser.saw_tag:
        rendered = rendered.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>\n")
    return rendered
