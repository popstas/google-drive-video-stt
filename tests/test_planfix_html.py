"""Planfix stores comment bodies as HTML, so the Markdown presets emit must be converted.

The receiving tool replaces every newline with ``<br>``, so the converter emits a single
line -- a pretty-printed document would arrive padded with blank lines.
"""

from __future__ import annotations

from src.planfix_html import markdown_to_html


def test_headings_become_bold_paragraphs():
    assert markdown_to_html("## Задачи") == "<p><b>Задачи</b></p>"
    assert markdown_to_html("### Анжелика") == "<p><b>Анжелика</b></p>"


def test_bullets_become_one_list():
    html = markdown_to_html("- раз\n- два")

    assert html == "<ul><li>раз</li><li>два</li></ul>"


def test_consecutive_bullets_share_a_list_but_a_heading_closes_it():
    html = markdown_to_html("- раз\n\n## Тезисы\n\n- два")

    assert html == "<ul><li>раз</li></ul><p><b>Тезисы</b></p><ul><li>два</li></ul>"


def test_task_checkboxes_lose_the_marker():
    """Planfix has no Markdown checkbox; the text is what matters."""
    assert markdown_to_html("- [ ] позвонить") == "<ul><li>позвонить</li></ul>"
    assert markdown_to_html("- [x] позвонить") == "<ul><li>позвонить</li></ul>"


def test_inline_emphasis_and_links():
    html = markdown_to_html("- **счёт** на *50 000* [договор](https://e.com)")

    assert html == (
        "<ul><li><b>счёт</b> на <i>50 000</i> "
        '<a href="https://e.com">договор</a></li></ul>'
    )


def test_plain_paragraph_survives():
    assert markdown_to_html("просто текст") == "<p>просто текст</p>"


def test_html_in_the_source_is_escaped():
    """Transcript content reaches this converter; it must not inject markup."""
    html = markdown_to_html("- <script>alert(1)</script> & Co")

    assert html == "<ul><li>&lt;script&gt;alert(1)&lt;/script&gt; &amp; Co</li></ul>"


def test_link_url_is_attribute_escaped():
    html = markdown_to_html('[x](https://e.com/?a="b")')

    assert html == '<p><a href="https://e.com/?a=&quot;b&quot;">x</a></p>'


def test_output_never_contains_a_newline():
    """Every newline would become a <br> on the Planfix side."""
    source = "## Задачи\n\n- раз\n- два\n\n## Тезисы\n\n- три\n"

    assert "\n" not in markdown_to_html(source)


def test_blank_input_stays_blank():
    assert markdown_to_html("") == ""
    assert markdown_to_html("   \n\n  ") == ""
