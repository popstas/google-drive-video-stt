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

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
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
    """
    parts: list[str] = []
    in_list = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            parts.append("</ul>")
            in_list = False

    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
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
    return "".join(parts)
