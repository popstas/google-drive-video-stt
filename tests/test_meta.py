from __future__ import annotations

from src import meta_entity
from src.meta import parse_meta

ALLOWED = ("клиентская-консультация", "O-1", "EB-1")

ENTITIES = meta_entity.parse_entities(
    [
        {"name": "subject", "prompt": "Тема.", "label": ""},
        {
            "name": "tags",
            "prompt": "Теги.",
            "type": "enum",
            "multiple": True,
            "allowed": ["O-1", "EB-1A"],
        },
        {
            "name": "referral",
            "prompt": "Откуда.",
            "type": "enum",
            "allowed": ["telegram"],
        },
        {"name": "referral_note", "prompt": "Подробности.", "requires": "referral"},
        {"name": "deadlines", "prompt": "Сроки.", "multiple": True},
    ]
)


def _entities(allowed=ALLOWED):
    return meta_entity.parse_entities(
        [
            {"name": "subject", "prompt": "Тема.", "label": ""},
            {
                "name": "tags",
                "prompt": "Теги.",
                "type": "enum",
                "multiple": True,
                "allowed": list(allowed),
            },
        ]
    )


def test_parses_well_formed_frontmatter():
    text = (
        "---\n"
        "subject: Консультация по визе O-1 для research-профиля\n"
        "tags: [клиентская-консультация, O-1]\n"
        "---\n"
    )
    values = parse_meta(text, _entities())
    assert values["subject"] == "Консультация по визе O-1 для research-профиля"
    assert values["tags"] == ["клиентская-консультация", "O-1"]


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
    values = parse_meta(text, _entities())
    assert values["subject"] == "Weekly sync"
    assert values["tags"] == ["O-1", "EB-1"]


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
    values = parse_meta(text, _entities())
    assert values["subject"] == "Weekly sync"
    assert values["tags"] == ["O-1"]


def test_fenced_frontmatter_is_unwrapped():
    """Models fence a YAML block routinely despite the prompt; don't lose the fields."""
    text = "```yaml\n---\nsubject: Визовая консультация\ntags: [O-1]\n---\n```"
    values = parse_meta(text, _entities())
    assert values["subject"] == "Визовая консультация"
    assert values["tags"] == ["O-1"]


def test_bare_mapping_without_delimiters_is_parsed():
    """A reply that drops the `---` delimiters still carries subject and tags."""
    text = "subject: Визовая консультация\ntags: [O-1]\n"
    values = parse_meta(text, _entities())
    assert values["subject"] == "Визовая консультация"
    assert values["tags"] == ["O-1"]


def test_fenced_bare_mapping_is_parsed():
    text = "```yaml\nsubject: Визовая консультация\ntags: [O-1]\n```"
    values = parse_meta(text, _entities())
    assert values["subject"] == "Визовая консультация"
    assert values["tags"] == ["O-1"]


def test_unquoted_colon_in_subject_keeps_both_fields():
    """`subject` is prose in the transcript's language, so a colon in it is routine.

    YAML reads the unquoted line as a nested mapping and rejects the whole document;
    the tags must not die with it.
    """
    text = "---\nsubject: Обсуждение проекта: сроки и бюджет\ntags: [O-1]\n---\n"
    values = parse_meta(text, _entities())
    assert values["subject"] == "Обсуждение проекта: сроки и бюджет"
    assert values["tags"] == ["O-1"]


def test_unquoted_colon_subject_with_embedded_quotes_recovered():
    text = '---\nsubject: He said: "yes" to O-1\ntags: [O-1]\n---\n'
    values = parse_meta(text, _entities())
    assert values["subject"] == 'He said: "yes" to O-1'
    assert values["tags"] == ["O-1"]


def test_unquoted_colon_in_bare_mapping_recovered():
    text = "subject: Roadmap review: Q3\ntags: [O-1]\n"
    values = parse_meta(text, _entities())
    assert values["subject"] == "Roadmap review: Q3"
    assert values["tags"] == ["O-1"]


def test_quoted_subject_is_not_requoted():
    """The repair must not double-quote a value the model already quoted correctly."""
    text = "---\nsubject: 'Quoted: subject'\ntags: [O-1]\n---\n"
    values = parse_meta(text, _entities())
    assert values["subject"] == "Quoted: subject"
    assert values["tags"] == ["O-1"]


def test_no_frontmatter_degrades_to_empty_meta():
    values = parse_meta("Just a plain answer with no frontmatter.", _entities())
    assert values["subject"] == ""
    assert values["tags"] == []


def test_empty_text_degrades_to_empty_meta():
    values = parse_meta("", _entities())
    assert values["subject"] == ""
    assert values["tags"] == []


def test_malformed_yaml_degrades_to_empty_meta():
    text = "---\nsubject: [unclosed\ntags: {{{\n---\n"
    values = parse_meta(text, _entities())
    assert values["subject"] == ""
    assert values["tags"] == []


def test_non_mapping_frontmatter_degrades_to_empty_meta():
    text = "---\n- just\n- a\n- list\n---\n"
    values = parse_meta(text, _entities())
    assert values["subject"] == ""
    assert values["tags"] == []


def test_unknown_tag_is_dropped():
    text = "---\nsubject: T\ntags: [O-1, hallucinated-tag]\n---\n"
    values = parse_meta(text, _entities())
    assert values["tags"] == ["O-1"]


def test_empty_tags_list():
    text = "---\nsubject: T\ntags: []\n---\n"
    values = parse_meta(text, _entities())
    assert values["subject"] == "T"
    assert values["tags"] == []


def test_missing_tags_key_yields_empty_tags():
    values = parse_meta("---\nsubject: T\n---\n", _entities())
    assert values["subject"] == "T"
    assert values["tags"] == []


def test_missing_subject_key_yields_empty_subject():
    values = parse_meta("---\ntags: [O-1]\n---\n", _entities())
    assert values["subject"] == ""
    assert values["tags"] == ["O-1"]


def test_tags_deduplicated_preserving_order():
    text = "---\nsubject: T\ntags: [O-1, EB-1, O-1]\n---\n"
    assert parse_meta(text, _entities())["tags"] == ["O-1", "EB-1"]


def test_scalar_tags_value_accepted():
    # A model that answers `tags: O-1` instead of a list must not lose the tag.
    assert parse_meta("---\nsubject: T\ntags: O-1\n---\n", _entities())["tags"] == ["O-1"]


def test_empty_allow_list_drops_every_tag():
    text = "---\nsubject: T\ntags: [O-1]\n---\n"
    values = parse_meta(text, _entities(allowed=()))
    assert values["subject"] == "T"
    assert values["tags"] == []


def test_tag_matching_the_allow_list_survives():
    text = "---\nsubject: T\ntags: [O-1, invented]\n---\n"
    assert parse_meta(text, _entities())["tags"] == ["O-1"]


def test_leading_whitespace_and_bom_tolerated():
    text = "﻿\n---\nsubject: T\ntags: [O-1]\n---\n"
    values = parse_meta(text, _entities())
    assert values["subject"] == "T"
    assert values["tags"] == ["O-1"]


def test_non_string_subject_coerced_to_text():
    assert parse_meta("---\nsubject: 42\n---\n", _entities())["subject"] == "42"


def test_tags_whitespace_stripped_before_matching():
    text = "---\nsubject: T\ntags: ['  O-1  ']\n---\n"
    assert parse_meta(text, _entities())["tags"] == ["O-1"]


def test_parse_meta_reads_subject_and_referral():
    text = (
        "---\n"
        "subject: Обсудили состав кейса\n"
        "tags: [O-1]\n"
        "referral: рекомендация\n"
        "referral_note: Посоветовала знакомая из Нью-Йорка\n"
        "---\n"
    )
    entities = meta_entity.default_entities(("O-1",), ("рекомендация", "instagram"))
    parsed = parse_meta(text, entities)
    assert parsed["subject"] == "Обсудили состав кейса"
    assert parsed["tags"] == ["O-1"]
    assert parsed["referral"] == "рекомендация"
    assert parsed["referral_note"] == "Посоветовала знакомая из Нью-Йорка"


def test_parse_meta_drops_referral_outside_the_allow_list():
    text = "---\nsubject: x\ntags: []\nreferral: телепатия\nreferral_note: сон\n---\n"
    entities = meta_entity.default_entities((), ("рекомендация",))
    parsed = parse_meta(text, entities)
    assert parsed["referral"] == ""
    # The note describes a channel that was rejected, so it cannot stand alone.
    assert parsed["referral_note"] == ""


def test_parse_meta_keeps_empty_referral_when_the_call_never_covered_it():
    text = "---\nsubject: x\ntags: []\nreferral: ''\nreferral_note: ''\n---\n"
    entities = meta_entity.default_entities((), ("рекомендация",))
    parsed = parse_meta(text, entities)
    assert parsed["referral"] == ""
    assert parsed["referral_note"] == ""


def test_parse_meta_repairs_an_unquoted_colon_in_referral_note():
    text = (
        "---\n"
        "subject: Разговор\n"
        "tags: []\n"
        "referral: instagram\n"
        "referral_note: Написала после рилса: про визу талантов\n"
        "---\n"
    )
    entities = meta_entity.default_entities((), ("instagram",))
    parsed = parse_meta(text, entities)
    assert parsed["referral"] == "instagram"
    assert parsed["referral_note"].startswith("Написала после рилса")


def test_every_entity_is_present_even_when_the_artifact_is_garbage():
    values = parse_meta("это не yaml, а проза", ENTITIES)
    assert values == {
        "subject": "",
        "tags": [],
        "referral": "",
        "referral_note": "",
        "deadlines": [],
    }


def test_multiple_text_entity_parses_a_list():
    values = parse_meta(
        "---\ndeadlines:\n  - виза до октября\n  - оффер к сентябрю\n---\n", ENTITIES
    )
    assert values["deadlines"] == ["виза до октября", "оффер к сентябрю"]


def test_multiple_entity_accepts_a_bare_scalar_as_one_element():
    values = parse_meta("---\ndeadlines: виза до октября\n---\n", ENTITIES)
    assert values["deadlines"] == ["виза до октября"]


def test_enum_values_outside_the_allow_list_are_dropped():
    values = parse_meta(
        "---\ntags: [O-1, ВЫДУМАННЫЙ]\nreferral: карты-таро\n---\n", ENTITIES
    )
    assert values["tags"] == ["O-1"]
    assert values["referral"] == ""


def test_requires_empties_a_dependent_when_its_target_was_dropped():
    values = parse_meta(
        "---\nreferral: карты-таро\nreferral_note: гадалка посоветовала\n---\n",
        ENTITIES,
    )
    assert values["referral"] == ""
    assert values["referral_note"] == ""


def test_requires_chain_empties_every_level_when_the_root_target_was_dropped():
    """A -> B -> C -> D, with D an enum whose value gets dropped by the allow-list.

    A single pass over the entities in declaration order only empties B (it sees
    C's already-dropped value); A still sees B's stale, pre-drop value that same
    pass. The whole chain must end up empty, not just the link nearest the root.
    """
    entities = meta_entity.parse_entities(
        [
            {"name": "a", "prompt": "A.", "requires": "b"},
            {"name": "b", "prompt": "B.", "requires": "c"},
            {"name": "c", "prompt": "C.", "requires": "d"},
            {"name": "d", "prompt": "D.", "type": "enum", "allowed": ["ok"]},
        ]
    )
    values = parse_meta(
        "---\na: valA\nb: valB\nc: valC\nd: not-allowed\n---\n", entities
    )
    assert values == {"a": "", "b": "", "c": "", "d": ""}


def test_a_colon_in_prose_does_not_take_the_document_down():
    values = parse_meta(
        "---\nsubject: Обсудили визу: сроки и бюджет\ntags: [O-1]\n---\n", ENTITIES
    )
    assert values["subject"] == "Обсудили визу: сроки и бюджет"
    assert values["tags"] == ["O-1"]
