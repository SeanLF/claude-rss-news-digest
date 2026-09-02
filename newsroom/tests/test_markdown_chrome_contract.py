"""The contract the derived-Markdown route depends on.

`circulation/src/markdown.rs` turns the stored HTML blob into the Markdown that
`/issues/{date}.md` and every MCP tool serve. It cannot lay a page out, so it reads two
signals from this markup: `aria-hidden="true"` means "decoration, do not read this", and the
`lbl`/`tag` label spans are bolded so they stop running into the sentence after them.

Only the first signal can lose anything. An element marked `aria-hidden` is dropped whole, for
every issue in the archive at once, with no error and no log line -- so these tests pin that
the attribute is only ever on decoration. Bolding is additive: a `lbl`/`tag` that turns out to
be content costs a pair of asterisks, which is why the Rust side bolds rather than drops.

If one of these fails, either the markup gained a case the Rust side has not seen, or the two
have drifted apart. Fix them together and say which in the commit.
"""

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_RENDER_PY = _ROOT / "newsroom" / "src" / "render.py"
_TEMPLATE = _ROOT / "newsroom" / "templates" / "digest-template.html"
_MARKDOWN_RS = _ROOT / "circulation" / "src" / "markdown.rs"

# What each aria-hidden element is allowed to contain: nothing, or a glyph that is meaningless
# without its styling. A word here would be read by a screen reader's user as silence and by a
# Markdown reader as a deletion.
_DECORATIVE_GLYPHS = {"", "/", "■", "·", "—", "•"}

# Interpolations whose expansion is decoration, each with the reason it is safe to delete.
# A placeholder NOT listed here fails, which is the point: someone must look at what it holds.
_DECORATIVE_PLACEHOLDERS = {
    # render.py: the bias bar, a row of empty coloured flex spans. Its readable equivalent
    # ("10 sources · 6 left · 2 center · 2 right") is rendered beside it and is NOT hidden.
    "{segs}",
}

_ARIA_HIDDEN = re.compile(r'<(\w+)([^>]*\baria-hidden="true"[^>]*)>(.*?)</\1>', re.S)
_TAGS = re.compile(r"<[^>]+>")


def _sources() -> dict[Path, str]:
    return {path: path.read_text(encoding="utf-8") for path in (_RENDER_PY, _TEMPLATE)}


def test_aria_hidden_marks_only_decoration():
    """Anything aria-hidden is deleted from every issue's Markdown, so it must carry no words."""
    offenders = []
    for path, text in _sources().items():
        for match in _ARIA_HIDDEN.finditer(text):
            inner = _TAGS.sub("", match.group(3)).strip()
            # A numeral is a section index, decoration by construction. An interpolation is
            # judged by name against the allowlist above, never waved through as "a variable".
            if inner in _DECORATIVE_GLYPHS or inner in _DECORATIVE_PLACEHOLDERS or inner.isdigit():
                continue
            offenders.append(f"{path.name}: <{match.group(1)}> contains {inner[:60]!r}")
    assert not offenders, (
        "aria-hidden is on something that carries words. circulation/src/markdown.rs drops "
        "those elements from /issues/*.md and from every MCP tool that serves an issue, "
        "silently and for the whole archive:\n  " + "\n  ".join(offenders)
    )


def test_label_spans_stay_short_and_inline():
    """`lbl`/`tag` are bolded, and CommonMark cannot bold across a blank line."""
    offenders = []
    for path, text in _sources().items():
        for cls in ("lbl", "tag"):
            for match in re.finditer(rf'<span class="[^"]*\b{cls}\b[^"]*">(.*?)</span>', text, re.S):
                inner = match.group(1)
                if _TAGS.search(inner) or "\n" in inner:
                    offenders.append(f"{path.name}: <span class={cls!r}> holds {inner[:60]!r}")
    assert not offenders, "a label span carries markup or spans lines; markdown.rs bolds its text:\n  " + "\n  ".join(
        offenders
    )


def test_markdown_route_reads_the_signals_this_file_pins():
    """Catches a one-sided edit: the Rust side must still key on these two signals."""
    rust = _MARKDOWN_RS.read_text(encoding="utf-8")
    assert 'a.name.local == "aria-hidden"' in rust, "markdown.rs no longer reads aria-hidden"
    handled = set(re.findall(r'has_class\(&element, "([a-z-]+)"\)', rust))
    assert handled == {"lbl", "tag"}, (
        f"markdown.rs keys on classes {sorted(handled)}; this test pins ['lbl', 'tag']. "
        "A class-keyed DROP would be a silent deletion -- update both sides together."
    )
