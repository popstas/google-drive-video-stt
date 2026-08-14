import logging

import pytest

from src import meta_entity


def test_absent_entities_yield_the_four_builtins_with_config_allow_lists():
    entities = meta_entity.parse_entities(
        None, tags_allowed=("O-1",), referrals_allowed=("telegram",)
    )
    assert [entity.name for entity in entities] == [
        "subject",
        "tags",
        "referral",
        "referral_note",
    ]
    by_name = {entity.name: entity for entity in entities}
    assert by_name["tags"].allowed == ("O-1",)
    assert by_name["tags"].multiple is True
    assert by_name["referral"].allowed == ("telegram",)
    assert by_name["referral_note"].requires == "referral"
    assert by_name["subject"].label == ""


def test_declared_entities_replace_the_builtins():
    entities = meta_entity.parse_entities(
        [{"name": "target_filing", "prompt": "На какую подачу целится клиент."}],
        tags_allowed=("O-1",),
    )
    assert [entity.name for entity in entities] == ["target_filing"]
    assert entities[0].type == "text"
    assert entities[0].multiple is False
    assert entities[0].allowed == ()


def test_label_defaults_to_the_name_and_empty_label_marks_the_heading():
    entities = meta_entity.parse_entities(
        [
            {"name": "deadlines", "prompt": "Сроки."},
            {"name": "subject", "prompt": "Тема.", "label": ""},
        ]
    )
    by_name = {entity.name: entity for entity in entities}
    assert by_name["deadlines"].planfix_label == "deadlines"
    assert by_name["deadlines"].is_heading is False
    assert by_name["subject"].planfix_label == ""
    assert by_name["subject"].is_heading is True


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (
            [{"name": "tags", "prompt": "a"}, {"name": "tags", "prompt": "b"}],
            "duplicate",
        ),
        ([{"name": "manager", "prompt": "a"}], "manager"),
        ([{"name": "two words", "prompt": "a"}], "two words"),
        ([{"name": "9lives", "prompt": "a"}], "9lives"),
        ([{"name": "tags", "prompt": "a", "type": "date"}], "date"),
        ([{"name": "subject", "prompt": "a", "allowed": ["x"]}], "allowed"),
        ([{"name": "note", "prompt": "a", "requires": "nope"}], "nope"),
        ([{"name": "subject", "prompt": ""}], "prompt"),
        ([{"name": "subject"}], "prompt"),
        (["subject"], "mapping"),
        ("subject", "list"),
    ],
)
def test_invalid_entities_are_rejected_with_a_message_naming_the_problem(raw, message):
    with pytest.raises(ValueError) as excinfo:
        meta_entity.parse_entities(raw)
    assert message in str(excinfo.value)


def test_requires_cycle_is_rejected():
    with pytest.raises(ValueError) as excinfo:
        meta_entity.parse_entities(
            [
                {"name": "a", "prompt": "a", "requires": "b"},
                {"name": "b", "prompt": "b", "requires": "a"},
            ]
        )
    assert "cycle" in str(excinfo.value)


def test_enum_without_allowed_is_warned_not_rejected(caplog):
    with caplog.at_level(logging.WARNING):
        entities = meta_entity.parse_entities(
            [{"name": "referral", "prompt": "Откуда.", "type": "enum"}]
        )
    assert entities[0].allowed == ()
    assert "referral" in caplog.text
