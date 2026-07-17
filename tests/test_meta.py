from __future__ import annotations

from src.meta import Meta, parse_meta

ALLOWED = ("клиентская-консультация", "O-1", "EB-1")


def test_parses_well_formed_frontmatter():
    text = (
        "---\n"
        "topic: Консультация по визе O-1 для research-профиля\n"
        "tags: [клиентская-консультация, O-1]\n"
        "---\n"
    )
    meta = parse_meta(text, allowed=ALLOWED)
    assert meta.topic == "Консультация по визе O-1 для research-профиля"
    assert meta.tags == ("клиентская-консультация", "O-1")


def test_parses_block_style_tags_and_trailing_body():
    text = (
        "---\n"
        "topic: Weekly sync\n"
        "tags:\n"
        "  - O-1\n"
        "  - EB-1\n"
        "---\n"
        "\n"
        "Some stray body the model added anyway.\n"
    )
    meta = parse_meta(text, allowed=ALLOWED)
    assert meta.topic == "Weekly sync"
    assert meta.tags == ("O-1", "EB-1")


def test_no_frontmatter_degrades_to_empty_meta():
    meta = parse_meta("Just a plain answer with no frontmatter.", allowed=ALLOWED)
    assert meta == Meta(topic="", tags=())


def test_empty_text_degrades_to_empty_meta():
    assert parse_meta("", allowed=ALLOWED) == Meta(topic="", tags=())


def test_malformed_yaml_degrades_to_empty_meta():
    text = "---\ntopic: [unclosed\ntags: {{{\n---\n"
    assert parse_meta(text, allowed=ALLOWED) == Meta(topic="", tags=())


def test_non_mapping_frontmatter_degrades_to_empty_meta():
    text = "---\n- just\n- a\n- list\n---\n"
    assert parse_meta(text, allowed=ALLOWED) == Meta(topic="", tags=())


def test_unknown_tag_is_dropped():
    text = "---\ntopic: T\ntags: [O-1, hallucinated-tag]\n---\n"
    meta = parse_meta(text, allowed=ALLOWED)
    assert meta.tags == ("O-1",)


def test_empty_tags_list():
    text = "---\ntopic: T\ntags: []\n---\n"
    meta = parse_meta(text, allowed=ALLOWED)
    assert meta.topic == "T"
    assert meta.tags == ()


def test_missing_tags_key_yields_empty_tags():
    meta = parse_meta("---\ntopic: T\n---\n", allowed=ALLOWED)
    assert meta.topic == "T"
    assert meta.tags == ()


def test_missing_topic_key_yields_empty_topic():
    meta = parse_meta("---\ntags: [O-1]\n---\n", allowed=ALLOWED)
    assert meta.topic == ""
    assert meta.tags == ("O-1",)


def test_tags_deduplicated_preserving_order():
    text = "---\ntopic: T\ntags: [O-1, EB-1, O-1]\n---\n"
    assert parse_meta(text, allowed=ALLOWED).tags == ("O-1", "EB-1")


def test_scalar_tags_value_accepted():
    # A model that answers `tags: O-1` instead of a list must not lose the tag.
    assert parse_meta("---\ntopic: T\ntags: O-1\n---\n", allowed=ALLOWED).tags == ("O-1",)


def test_empty_allow_list_drops_every_tag():
    text = "---\ntopic: T\ntags: [O-1]\n---\n"
    meta = parse_meta(text, allowed=())
    assert meta.topic == "T"
    assert meta.tags == ()


def test_allowed_none_skips_filtering():
    text = "---\ntopic: T\ntags: [anything-goes]\n---\n"
    assert parse_meta(text).tags == ("anything-goes",)


def test_leading_whitespace_and_bom_tolerated():
    text = "﻿\n---\ntopic: T\ntags: [O-1]\n---\n"
    meta = parse_meta(text, allowed=ALLOWED)
    assert meta.topic == "T"
    assert meta.tags == ("O-1",)


def test_non_string_topic_coerced_to_text():
    assert parse_meta("---\ntopic: 42\n---\n", allowed=ALLOWED).topic == "42"


def test_tags_whitespace_stripped_before_matching():
    text = "---\ntopic: T\ntags: ['  O-1  ']\n---\n"
    assert parse_meta(text, allowed=ALLOWED).tags == ("O-1",)
