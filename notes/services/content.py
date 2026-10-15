"""Keep Diary's HTML document format while allowing only safe editor markup."""
import re
import bleach
from bleach.css_sanitizer import CSSSanitizer
from bs4 import BeautifulSoup

TAGS = {"p", "br", "div", "span", "strong", "b", "em", "i", "u", "s", "strike", "blockquote", "pre", "code", "ol", "ul", "li", "a", "h1", "h2", "h3", "h4", "h5", "h6", "sub", "sup"}
CSS = CSSSanitizer(allowed_css_properties={"color", "background-color", "font-size", "font-family", "font-weight", "font-style", "text-align", "text-decoration", "vertical-align", "direction"})


def _attribute(tag, name, value):
    if name == "class":
        return all(re.fullmatch(r"ql-[a-z0-9-]+", token) for token in value.split())
    if name == "style":
        return True
    return tag == "a" and name in {"href", "title"}


def normalize_note_content(content: str, *, allow_empty=False) -> tuple[str, str]:
    if not isinstance(content, str):
        raise ValueError("Content must be HTML text")
    soup = BeautifulSoup(content, "html.parser")
    for node in soup.find_all(["script", "style", "iframe", "object", "embed", "svg", "math"]):
        node.decompose()
    html = bleach.clean(str(soup), tags=TAGS, attributes=_attribute,
                        protocols={"http", "https", "mailto", "tel"}, strip=True,
                        css_sanitizer=CSS)
    plain = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    plain = re.sub(r"[\u200b-\u200d\ufeff]", "", plain)
    plain = " ".join(plain.split())
    if not plain and not allow_empty:
        raise ValueError("Write something first")
    return html, plain


def normalize_note_fields(title: str, content: str) -> tuple[str, str]:
    """Validate the complete note, including retained fields during PATCH."""
    html, plain = normalize_note_content(content, allow_empty=True)
    visible_title = re.sub(r"[\u200b-\u200d\ufeff]", "", title).strip()
    if not visible_title and not plain:
        raise ValueError("Write a title or some content first")
    return html, plain
