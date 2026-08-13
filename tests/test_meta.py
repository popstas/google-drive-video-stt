from __future__ import annotations

from src.meta import Meta, parse_meta

ALLOWED = ("клиентская-консультация", "O-1", "EB-1")


def test_parses_well_formed_frontmatter():
    text = (
        "---\n"
        "subject: Консультация по визе O-1 для research-профиля\n"
        "tags: [клиентская-консультация, O-1]\n"
        "---\n"
    )
    meta = parse_meta(text, allowed=ALLOWED)
    assert meta.subject == "Консультация по визе O-1 для research-профиля"
    assert meta.tags == ("клиентская-консультация", "O-1")


def test_parses_block_style_tags_and_trailing_body():
    text = (
        "---\n"
        "subject: Weekly sync\n"
        "tags:\n"
        "  - O-1\n"
        "  - EB-1\n"
        "---\n"
        "\n"
        "Some stray body the model added anyway.\n"
    )
    meta = parse_meta(text, allowed=ALLOWED)
    assert meta.subject == "Weekly sync"
    assert meta.tags == ("O-1", "EB-1")


def test_preamble_before_frontmatter_is_ignored():
    """A model that introduces its answer must not cost us both fields."""
    text = (
        "Sure! Here is the metadata you asked for:\n"
        "\n"
        "---\n"
        "subject: Weekly sync\n"
        "tags: [O-1]\n"
        "---\n"
    )
    assert parse_meta(text, allowed=ALLOWED) == Meta(subject="Weekly sync", tags=("O-1",))


def test_fenced_frontmatter_is_unwrapped():
    """Models fence a YAML block routinely despite the prompt; don't lose the fields."""
    text = "```yaml\n---\nsubject: Визовая консультация\ntags: [O-1]\n---\n```"
    assert parse_meta(text, allowed=ALLOWED) == Meta(
        subject="Визовая консультация", tags=("O-1",)
    )


def test_bare_mapping_without_delimiters_is_parsed():
    """A reply that drops the `---` delimiters still carries subject and tags."""
    text = "subject: Визовая консультация\ntags: [O-1]\n"
    assert parse_meta(text, allowed=ALLOWED) == Meta(
        subject="Визовая консультация", tags=("O-1",)
    )


def test_fenced_bare_mapping_is_parsed():
    text = "```yaml\nsubject: Визовая консультация\ntags: [O-1]\n```"
    assert parse_meta(text, allowed=ALLOWED) == Meta(
        subject="Визовая консультация", tags=("O-1",)
    )


def test_unquoted_colon_in_subject_keeps_both_fields():
    """`subject` is prose in the transcript's language, so a colon in it is routine.

    YAML reads the unquoted line as a nested mapping and rejects the whole document;
    the tags must not die with it.
    """
    text = "---\nsubject: Обсуждение проекта: сроки и бюджет\ntags: [O-1]\n---\n"
    assert parse_meta(text, allowed=ALLOWED) == Meta(
        subject="Обсуждение проекта: сроки и бюджет", tags=("O-1",)
    )


def test_unquoted_colon_subject_with_embedded_quotes_recovered():
    text = '---\nsubject: He said: "yes" to O-1\ntags: [O-1]\n---\n'
    assert parse_meta(text, allowed=ALLOWED) == Meta(
        subject='He said: "yes" to O-1', tags=("O-1",)
    )


def test_unquoted_colon_in_bare_mapping_recovered():
    text = "subject: Roadmap review: Q3\ntags: [O-1]\n"
    assert parse_meta(text, allowed=ALLOWED) == Meta(subject="Roadmap review: Q3", tags=("O-1",))


def test_quoted_subject_is_not_requoted():
    """The repair must not double-quote a value the model already quoted correctly."""
    text = "---\nsubject: 'Quoted: subject'\ntags: [O-1]\n---\n"
    assert parse_meta(text, allowed=ALLOWED) == Meta(subject="Quoted: subject", tags=("O-1",))


def test_no_frontmatter_degrades_to_empty_meta():
    meta = parse_meta("Just a plain answer with no frontmatter.", allowed=ALLOWED)
    assert meta == Meta(subject="", tags=())


def test_empty_text_degrades_to_empty_meta():
    assert parse_meta("", allowed=ALLOWED) == Meta(subject="", tags=())


def test_malformed_yaml_degrades_to_empty_meta():
    text = "---\nsubject: [unclosed\ntags: {{{\n---\n"
    assert parse_meta(text, allowed=ALLOWED) == Meta(subject="", tags=())


def test_non_mapping_frontmatter_degrades_to_empty_meta():
    text = "---\n- just\n- a\n- list\n---\n"
    assert parse_meta(text, allowed=ALLOWED) == Meta(subject="", tags=())


def test_unknown_tag_is_dropped():
    text = "---\nsubject: T\ntags: [O-1, hallucinated-tag]\n---\n"
    meta = parse_meta(text, allowed=ALLOWED)
    assert meta.tags == ("O-1",)


def test_empty_tags_list():
    text = "---\nsubject: T\ntags: []\n---\n"
    meta = parse_meta(text, allowed=ALLOWED)
    assert meta.subject == "T"
    assert meta.tags == ()


def test_missing_tags_key_yields_empty_tags():
    meta = parse_meta("---\nsubject: T\n---\n", allowed=ALLOWED)
    assert meta.subject == "T"
    assert meta.tags == ()


def test_missing_subject_key_yields_empty_subject():
    meta = parse_meta("---\ntags: [O-1]\n---\n", allowed=ALLOWED)
    assert meta.subject == ""
    assert meta.tags == ("O-1",)


def test_tags_deduplicated_preserving_order():
    text = "---\nsubject: T\ntags: [O-1, EB-1, O-1]\n---\n"
    assert parse_meta(text, allowed=ALLOWED).tags == ("O-1", "EB-1")


def test_scalar_tags_value_accepted():
    # A model that answers `tags: O-1` instead of a list must not lose the tag.
    assert parse_meta("---\nsubject: T\ntags: O-1\n---\n", allowed=ALLOWED).tags == ("O-1",)


def test_empty_allow_list_drops_every_tag():
    text = "---\nsubject: T\ntags: [O-1]\n---\n"
    meta = parse_meta(text, allowed=())
    assert meta.subject == "T"
    assert meta.tags == ()


def test_tag_matching_the_allow_list_survives():
    text = "---\nsubject: T\ntags: [O-1, invented]\n---\n"
    assert parse_meta(text, allowed=ALLOWED).tags == ("O-1",)


def test_leading_whitespace_and_bom_tolerated():
    text = "﻿\n---\nsubject: T\ntags: [O-1]\n---\n"
    meta = parse_meta(text, allowed=ALLOWED)
    assert meta.subject == "T"
    assert meta.tags == ("O-1",)


def test_non_string_subject_coerced_to_text():
    assert parse_meta("---\nsubject: 42\n---\n", allowed=ALLOWED).subject == "42"


def test_tags_whitespace_stripped_before_matching():
    text = "---\nsubject: T\ntags: ['  O-1  ']\n---\n"
    assert parse_meta(text, allowed=ALLOWED).tags == ("O-1",)


def test_parse_meta_reads_subject_and_referral():
    text = (
        "---\n"
        "subject: Обсудили состав кейса\n"
        "tags: [O-1]\n"
        "referral: рекомендация\n"
        "referral_note: Посоветовала знакомая из Нью-Йорка\n"
        "---\n"
    )
    parsed = parse_meta(text, ["O-1"], ["рекомендация", "instagram"])
    assert parsed.subject == "Обсудили состав кейса"
    assert parsed.tags == ("O-1",)
    assert parsed.referral == "рекомендация"
    assert parsed.referral_note == "Посоветовала знакомая из Нью-Йорка"


def test_parse_meta_drops_referral_outside_the_allow_list():
    text = "---\nsubject: x\ntags: []\nreferral: телепатия\nreferral_note: сон\n---\n"
    parsed = parse_meta(text, [], ["рекомендация"])
    assert parsed.referral == ""
    # The note describes a channel that was rejected, so it cannot stand alone.
    assert parsed.referral_note == ""


def test_parse_meta_keeps_empty_referral_when_the_call_never_covered_it():
    text = "---\nsubject: x\ntags: []\nreferral: ''\nreferral_note: ''\n---\n"
    parsed = parse_meta(text, [], ["рекомендация"])
    assert parsed.referral == ""
    assert parsed.referral_note == ""


def test_parse_meta_repairs_an_unquoted_colon_in_referral_note():
    text = (
        "---\n"
        "subject: Разговор\n"
        "tags: []\n"
        "referral: instagram\n"
        "referral_note: Написала после рилса: про визу талантов\n"
        "---\n"
    )
    parsed = parse_meta(text, [], ["instagram"])
    assert parsed.referral == "instagram"
    assert parsed.referral_note.startswith("Написала после рилса")
