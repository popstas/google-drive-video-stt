"""Render the presets' Markdown as the HTML Planfix stores comments in.

Planfix keeps comment bodies as HTML and runs them through its own sanitizer, so the
Markdown the presets emit arrives as literal ``##`` and ``-`` characters unless it is
converted first. Only the small subset the prompts actually produce is handled --
headings, bullets, task checkboxes and inline emphasis -- because anything richer would
be guesswork about what the sanitizer keeps.

The result is a single line on purpose: the receiving tool rewrites every newline as
``<br>``, so a pretty-printed document would arrive padded with blank lines.
"""

from __future__ import annotations

import re
from html import escape

# A Markdown thematic break is how a caller asks for vertical space. A blank source
# line cannot carry the request: this converter drops those on purpose, because the
# document it receives is pretty-printed and every blank line would otherwise become a
# `<br>` in Planfix.
SECTION_BREAK = "---"

# Planfix rewrites a newline as `<br>`, so that is also how a blank line is spelled.
_BLANK_LINE = "<p><br></p>"

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_BREAK_RE = re.compile(r"^-{3,}$")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.*)$")
_TASK_MARKER_RE = re.compile(r"^\[[ xX]\]\s*")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")


def _inline(text: str) -> str:
    """Escape the text, then re-introduce the inline markup we support.

    Escaping first is what keeps transcript content from injecting markup: by the time
    the emphasis patterns run, every ``<`` in the source is already ``&lt;``.
    """
    out = escape(text, quote=False)
    out = _LINK_RE.sub(
        lambda m: f'<a href="{escape(m.group(2), quote=True)}">{m.group(1)}</a>', out
    )
    out = _BOLD_RE.sub(r"<b>\1</b>", out)
    out = _ITALIC_RE.sub(r"<i>\1</i>", out)
    return out


def markdown_to_html(text: str) -> str:
    """Convert a preset artifact to single-line Planfix HTML.

    Headings become bold paragraphs rather than ``<h*>`` tags: the sanitizer's exact
    allow-list is undocumented, and ``<b>``/``<p>`` are safe everywhere.

    A ``---`` line asks for a blank line. Consecutive requests collapse into one, and a
    leading or trailing one is dropped, so the body never opens or closes on empty space.
    """
    parts: list[str] = []
    in_list = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            parts.append("</ul>")
            in_list = False

    def add_break() -> None:
        """Emit one blank line, never two in a row and never a leading one.

        Callers ask for space around a section independently -- the header before it,
        the heading itself on both sides -- so the same gap gets requested twice where
        two of them meet. Collapsing here means no caller has to know what its
        neighbour already did.
        """
        close_list()
        if parts and parts[-1] != _BLANK_LINE:
            parts.append(_BLANK_LINE)

    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue

        if _BREAK_RE.match(line):
            add_break()
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            close_list()
            parts.append(f"<p><b>{_inline(heading.group(2).strip())}</b></p>")
            continue

        bullet = _BULLET_RE.match(line)
        if bullet:
            if not in_list:
                parts.append("<ul>")
                in_list = True
            # Planfix has no Markdown checkbox, so `- [ ] call back` keeps only its text.
            item = _TASK_MARKER_RE.sub("", bullet.group(1).strip())
            parts.append(f"<li>{_inline(item)}</li>")
            continue

        close_list()
        parts.append(f"<p>{_inline(line)}</p>")

    close_list()
    while parts and parts[-1] == _BLANK_LINE:
        parts.pop()
    return "".join(parts)
