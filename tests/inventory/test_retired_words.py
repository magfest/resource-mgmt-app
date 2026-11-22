"""Fail on any retired inventory word in text a person can read.

Scans inventory templates, scripts and Python strings. Comments and
docstrings are not scanned. Keys, classes and URLs are skipped: a string with
no space and no capital letter, or one made only of capitals, digits and
underscores (a code such as UNIT).
"""
import ast
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

RETIRED = re.compile(
    r"\bunits?\b(?! of measure)|\bgear\b|inventory item group|\bitem type\b"
    r"|\bsupply item|magfest tag|festops label|tag or label"
    r"|\bidentifiers?\b|white asset tag",
    re.IGNORECASE,
)

# (path relative to the repository root, exact text). Add a comment with the
# reason on every entry.
ALLOWED: set[tuple[str, str]] = set()

VISIBLE_ATTRS = ("placeholder", "title", "aria-label", "alt",
                 "data-confirm", "data-retire-confirm", "data-label")


def _sources():
    found = []
    found += sorted((ROOT / "app/templates/inventory").rglob("*.html"))
    found += sorted((ROOT / "app/static/inventory").glob("*.js"))
    found += sorted((ROOT / "app/inventory").glob("*.py"))
    found += sorted((ROOT / "app/routes/inventory").glob("*.py"))
    return [p.relative_to(ROOT).as_posix() for p in found]


def _skip(text):
    text = text.strip()
    if not text:
        return True
    if re.fullmatch(r"[A-Z0-9_]+", text):
        return True
    return " " not in text and not re.search(r"[A-Z]", text)


def _js_strings(src):
    """Yield string literals, ignoring comments and regex literals. Template `${...}` is removed."""
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/":
            while i < n and src[i] != "\n":
                i += 1
        elif ch == "/" and nxt == "*":
            end = src.find("*/", i + 2)
            i = n if end < 0 else end + 2
        elif ch == "/" and re.search(r"(^|[(,=:\[!&|?{};+])\s*$", src[max(0, i - 40):i]):
            i += 1
            in_class = False
            while i < n and (in_class or src[i] != "/"):
                if src[i] == "\\":
                    i += 1
                elif src[i] == "[":
                    in_class = True
                elif src[i] == "]":
                    in_class = False
                i += 1
            i += 1
        elif ch in "'\"`":
            quote, buf, i = ch, [], i + 1
            depth = 0
            while i < n:
                c = src[i]
                if c == "\\":
                    buf.append(src[i:i + 2] if depth == 0 else "")
                    i += 2
                    continue
                if quote == "`" and c == "$" and src[i + 1:i + 2] == "{" and depth == 0:
                    depth = 1
                    i += 2
                    buf.append(" ")
                    continue
                if depth:
                    if c == "{":
                        depth += 1
                    elif c == "}":
                        depth -= 1
                    i += 1
                    continue
                if c == quote:
                    break
                buf.append(c)
                i += 1
            i += 1
            yield "".join(buf)
        else:
            i += 1


class _Html(HTMLParser):
    def __init__(self):
        super().__init__()
        self.texts, self.scripts = [], []
        self._in = None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._in = tag
        for name, value in attrs:
            if name in VISIBLE_ATTRS and value:
                self.texts.append(value)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._in = None

    def handle_data(self, data):
        if self._in == "script":
            self.scripts.append(data)
        elif self._in is None:
            self.texts.append(data)


def _template_strings(src):
    src = re.sub(r"\{#.*?#\}|\{%.*?%\}|\{\{.*?\}\}", " ", src, flags=re.DOTALL)
    parser = _Html()
    parser.feed(src)
    parser.close()
    out = list(parser.texts)
    for body in parser.scripts:
        out += _js_strings(body)
    return out


def _python_strings(src):
    tree = ast.parse(src)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                docstrings.add(id(first.value))
    return [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
    ]


def _strings_for(path):
    src = (ROOT / path).read_text(encoding="utf-8")
    if path.endswith(".html"):
        return _template_strings(src)
    if path.endswith(".js"):
        return list(_js_strings(src))
    return _python_strings(src)


def _offenders(path, strings):
    bad = []
    for text in strings:
        if _skip(text) or (path, text.strip()) in ALLOWED or (path, text) in ALLOWED:
            continue
        if RETIRED.search(text):
            bad.append(" ".join(text.split()))
    return bad


@pytest.mark.parametrize("path", _sources())
def test_no_retired_words(path):
    bad = _offenders(path, _strings_for(path))
    assert not bad, f"{path} shows retired words:\n" + "\n".join(f"  {t!r}" for t in bad)


def test_station_names_have_no_retired_words():
    from app.models.constants import INV_STATION_NAMES

    bad = _offenders("app/models/constants.py", list(INV_STATION_NAMES.values()))
    assert not bad, "INV_STATION_NAMES shows retired words:\n" + "\n".join(bad)
