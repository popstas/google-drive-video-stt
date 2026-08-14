# Config-driven meta entities Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the list of things the `meta` preset extracts out of five hardcoded
places and into `data/config.yml`, and ship three new entities (case-collection
deadline, other deadlines, target filing).

**Architecture:** A new leaf module `src/meta_entity.py` owns the `MetaEntity`
dataclass, its YAML parsing, its validation, and the prompt block it renders.
`Config` gains `meta_entities`; everything downstream — the prompt asset, the
parser, the meta document, the webhook payload, the Planfix header — stops
naming fields and starts iterating that tuple.

**Tech Stack:** Python 3.11+, `uv` for every command, `dataclasses`, `PyYAML`,
`pytest`, `ruff`.

**Spec:** `docs/superpowers/specs/2026-08-14-config-driven-meta-entities-design.md`

## Global Constraints

- Every command runs through `uv`: `uv run pytest`, `uv run ruff check`.
- **Never `git commit --amend`.** A pre-commit hook generates `CHANGELOG.md` from
  commit messages; amending corrupts it.
- **Never `--no-verify`.**
- Secrets live in `./data` (gitignored). Never commit anything from `data/`.
- Tests mock every external service. There is no network in the test run.
- **The money invariant:** in folder mode only `.txt` and the preset artifacts
  decide whether a recording still needs work. `.meta.yml` and `.stt` take no
  part in the processed/pending bookkeeping. Nothing in this plan may change
  that — a regression re-transcribes the backlog at real Deepgram cost.
- The Planfix comment body must stay **one line of HTML**: Planfix rewrites every
  `\n` as `<br>`. A blank line is an explicit `<p><br></p>`
  (`planfix_html.SECTION_BREAK`).
- `skills/gdstt-cli/SKILL.md` must stay **≤ 400 lines** (`tests/test_skill_docs.py:84`).
  It is currently exactly 400 — adding a line requires removing one.
- A malformed `meta` artifact must never raise. It degrades to empty values plus
  a `logger.warning`: the recording already transcribed and its artifacts are
  already on Drive.
- Entity `prompt` text in the shipped default config is Russian (it is read by
  operators and by the model against Russian transcripts). The framing text in
  `src/assets/prompts/meta.md` stays English, matching the other assets.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/meta_entity.py` (new) | `MetaEntity`, `CODE_FIELDS`, YAML parsing, validation, `{{entities}}` rendering. A leaf: imports nothing from `config`/`meta`/`meta_doc`. |
| `src/meta.py` | Parses the model's YAML reply into a `dict` keyed by entity name. `Meta` dataclass deleted. |
| `src/meta_doc.py` | `field_order(entities)` and `build(...)`; imports `CODE_FIELDS` from `meta_entity`. |
| `src/config.py` | `Config.meta_entities`, load-time wiring, migration, `{{entities}}` placeholder, whole-Config serializer, generated default config. |
| `src/assets/prompts/meta.md` | Format framing only; field rules come from `{{entities}}`. |
| `src/main.py` | Passes `config.meta_entities` to the parser; webhook payload and Planfix header stop naming fields. |
| `tests/test_meta_entity.py` (new) | Validation, defaults, rendering. |

Import direction is one-way: `meta_entity` ← `meta`, `meta_doc`, `config` ←
`main`. `meta_doc` already imports `config` for the `Config` type, so
`CODE_FIELDS` must live in `meta_entity` (not `meta_doc`) or `config` importing
`meta_entity` would close a cycle.

---

## Task 1: The entity model

**Files:**
- Create: `src/meta_entity.py`
- Test: `tests/test_meta_entity.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `ENTITY_TYPES: tuple[str, ...] = ("text", "enum")`
  - `CODE_FIELDS: tuple[str, ...]` — the 14 meta-document fields the code fills itself
  - `class MetaEntity` — frozen dataclass, fields `name: str`, `prompt: str`,
    `type: str = "text"`, `multiple: bool = False`, `allowed: tuple[str, ...] = ()`,
    `label: str | None = None`, `requires: str = ""`;
    properties `planfix_label: str` and `is_heading: bool`
  - `default_entities(tags_allowed: tuple[str, ...] = (), referrals_allowed: tuple[str, ...] = ()) -> tuple[MetaEntity, ...]`
  - `parse_entities(raw: object, *, tags_allowed: tuple[str, ...] = (), referrals_allowed: tuple[str, ...] = ()) -> tuple[MetaEntity, ...]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_meta_entity.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_meta_entity.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.meta_entity'`

- [ ] **Step 3: Write the module**

Create `src/meta_entity.py`:

```python
"""Describe the things the ``meta`` preset extracts from a call.

An *entity* is one question we ask the model about a recording. Entities are
declared in ``data/config.yml``; this module knows the shape of an entity, never
the list of them. Everything downstream -- the prompt, the parser, the meta
document, the webhook payload, the Planfix header -- iterates the tuple this
module produces instead of naming fields.

A leaf module on purpose: it imports nothing from ``config``, ``meta``, or
``meta_doc``, all three of which import it.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

ENTITY_TYPES = ("text", "enum")

# The meta-document fields the code fills in from what it already knows: the
# folder's employee, the recording's name, the booking, the configured models.
# An entity may not claim one of these names -- silently overwriting the manager
# would be worse than refusing to start.
CODE_FIELDS = (
    "manager",
    "manager_email",
    "client",
    "date",
    "duration",
    "language",
    "planfix_task_id",
    "planfix_task_url",
    "video_id",
    "video_url",
    "source_name",
    "stt_model",
    "llm_model",
    "processed_at",
)

# An entity name is a YAML key in three documents and an attribute-shaped token in
# the prompt, so it must survive a round trip through all of them unquoted.
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class MetaEntity:
    """One thing the ``meta`` preset extracts."""

    name: str
    prompt: str
    type: str = "text"
    multiple: bool = False
    allowed: tuple[str, ...] = ()
    # ``None`` means "no label was declared, use the name". An explicit empty
    # string means "render as the comment's bold heading, not a labelled line" --
    # the behaviour that used to be hardcoded for ``subject``.
    label: str | None = None
    # Name of another entity; this one is emptied when that one came back empty.
    requires: str = ""

    @property
    def planfix_label(self) -> str:
        return self.name if self.label is None else self.label

    @property
    def is_heading(self) -> bool:
        return self.label == ""


def default_entities(
    tags_allowed: tuple[str, ...] = (),
    referrals_allowed: tuple[str, ...] = (),
) -> tuple[MetaEntity, ...]:
    """The four entities a config written before ``meta.entities`` existed implies.

    Their allow-lists come from the deprecated top-level ``tags.allowed`` /
    ``referrals.allowed``, so such a config keeps working untouched.
    """
    return (
        MetaEntity(
            name="subject",
            type="text",
            label="",
            prompt=(
                "Одно предложение о том, про что был звонок. Опирайся строго на "
                "транскрипт, ничего не выдумывай."
            ),
        ),
        MetaEntity(
            name="tags",
            type="enum",
            multiple=True,
            label="Теги",
            allowed=tags_allowed,
            prompt="Выбери все теги, которые действительно подходят, и никакие другие.",
        ),
        MetaEntity(
            name="referral",
            type="enum",
            label="Откуда узнал",
            allowed=referrals_allowed,
            prompt=(
                "Откуда клиент впервые узнал о компании. Заполняй, только если "
                "клиент сам это сказал: вопрос менеджера без ответа источником не "
                "является, и твоя догадка по контексту тоже."
            ),
        ),
        MetaEntity(
            name="referral_note",
            type="text",
            label="Подробности",
            requires="referral",
            prompt=(
                "Одна строка словами клиента о том, откуда он узнал о компании: "
                "кто порекомендовал, какой пост, какое мероприятие."
            ),
        ),
    )


def _entity_from_mapping(raw: object, index: int) -> MetaEntity:
    if not isinstance(raw, dict):
        raise ValueError(
            f"meta.entities[{index}] must be a mapping, got: {type(raw).__name__}"
        )
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError(f"meta.entities[{index}] must define a name")
    prompt = str(raw.get("prompt") or "").strip()
    if not prompt:
        raise ValueError(f"meta entity {name!r} must define a prompt")
    entity_type = str(raw.get("type") or "text").strip().lower()
    allowed_raw = raw.get("allowed")
    if allowed_raw is None:
        allowed: tuple[str, ...] = ()
    elif isinstance(allowed_raw, (list, tuple)):
        allowed = tuple(
            value for value in (str(entry).strip() for entry in allowed_raw) if value
        )
    else:
        raise ValueError(
            f"meta entity {name!r} allowed must be a list, got: {allowed_raw!r}"
        )
    label = raw.get("label")
    return MetaEntity(
        name=name,
        prompt=prompt,
        type=entity_type,
        multiple=bool(raw.get("multiple", False)),
        allowed=allowed,
        label=None if label is None else str(label),
        requires=str(raw.get("requires") or "").strip(),
    )


def _validate(entities: tuple[MetaEntity, ...]) -> None:
    by_name: dict[str, MetaEntity] = {}
    for entity in entities:
        if entity.name in by_name:
            raise ValueError(f"duplicate meta entity name: {entity.name!r}")
        if entity.name in CODE_FIELDS:
            raise ValueError(
                f"meta entity {entity.name!r} collides with a meta-document field "
                "the code fills in itself; pick another name"
            )
        if not _NAME_RE.match(entity.name):
            raise ValueError(
                f"meta entity name {entity.name!r} must be a plain identifier: "
                "letters, digits and underscores, not starting with a digit"
            )
        if entity.type not in ENTITY_TYPES:
            raise ValueError(
                f"meta entity {entity.name!r} has unknown type {entity.type!r}; "
                f"expected one of {', '.join(ENTITY_TYPES)}"
            )
        if entity.type != "enum" and entity.allowed:
            raise ValueError(
                f"meta entity {entity.name!r} is type {entity.type!r} and cannot "
                "carry an allowed list; only enum entities can"
            )
        by_name[entity.name] = entity

    for entity in entities:
        if not entity.requires:
            continue
        if entity.requires not in by_name:
            raise ValueError(
                f"meta entity {entity.name!r} requires {entity.requires!r}, "
                "which is not a declared entity"
            )
        seen = {entity.name}
        cursor = by_name[entity.requires]
        while cursor.requires:
            if cursor.name in seen:
                raise ValueError(
                    f"meta entity {entity.name!r} is part of a requires cycle"
                )
            seen.add(cursor.name)
            cursor = by_name[cursor.requires]

    for entity in entities:
        if entity.type == "enum" and not entity.allowed:
            # Legal: the model is handed nothing to choose from, so every value it
            # returns is invented and gets dropped. Worth saying out loud, because
            # an operator who meant to fill the list sees an always-empty field.
            logger.warning(
                "meta entity %r is an enum with an empty allowed list; it will "
                "always come back empty",
                entity.name,
            )


def parse_entities(
    raw: object,
    *,
    tags_allowed: tuple[str, ...] = (),
    referrals_allowed: tuple[str, ...] = (),
) -> tuple[MetaEntity, ...]:
    """Read ``meta.entities`` into validated entities.

    ``None`` (the key absent) yields the built-in four, wired to the deprecated
    top-level allow-lists. Anything else replaces them entirely.

    The built-ins skip validation on purpose: they are known-good, and running the
    empty-allow-list warning over them would fire on every load of a config that
    simply has no tags configured -- which is the shipped default.
    """
    if raw is None:
        return default_entities(tags_allowed, referrals_allowed)
    if not isinstance(raw, (list, tuple)):
        raise ValueError(f"meta.entities must be a list, got: {type(raw).__name__}")
    entities = tuple(
        _entity_from_mapping(entry, index) for index, entry in enumerate(raw)
    )
    _validate(entities)
    return entities
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_meta_entity.py -v`
Expected: PASS (12 test cases, counting the parametrized ones)

- [ ] **Step 5: Lint**

Run: `uv run ruff check src/meta_entity.py tests/test_meta_entity.py`
Expected: no findings.

- [ ] **Step 6: Commit**

```bash
git add src/meta_entity.py tests/test_meta_entity.py
git commit -m "feat: describe meta entities as config-shaped data"
```

---

## Task 2: Config owns the entities

**Files:**
- Modify: `src/config.py` — `Config` dataclass (near line 121), `_from_grouped_schema` (near line 762), `_config_to_dict` (near line 1349), `_default_config_dict` (near line 1169)
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `meta_entity.MetaEntity`, `meta_entity.parse_entities`, `meta_entity.default_entities` from Task 1.
- Produces:
  - `Config.meta_entities: tuple[MetaEntity, ...] = ()`
  - `data/config.yml` key `meta.entities`
  - The generated default config ships **seven** entities: the four built-ins
    plus `case_deadline`, `deadlines`, `target_filing`.

Note the deliberate asymmetry: `default_entities()` returns **four** (what an old
config without `meta.entities` implies), while `_default_config_dict` writes
**seven**. An existing deployment does not silently start extracting new fields;
a freshly generated config does.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_config.py` (match the file's existing helpers for building a
config dict and calling the loader — reuse whatever fixture the neighbouring
tests use rather than inventing one):

```python
def test_meta_entities_default_to_the_builtins_wired_to_legacy_allow_lists(tmp_path):
    config = load_config_from_mapping(
        tmp_path,
        {
            "tags": {"allowed": ["O-1"]},
            "referrals": {"allowed": ["telegram"]},
        },
    )
    by_name = {entity.name: entity for entity in config.meta_entities}
    assert set(by_name) == {"subject", "tags", "referral", "referral_note"}
    assert by_name["tags"].allowed == ("O-1",)
    assert by_name["referral"].allowed == ("telegram",)


def test_declared_meta_entities_replace_the_builtins(tmp_path):
    config = load_config_from_mapping(
        tmp_path,
        {
            "meta": {
                "entities": [
                    {
                        "name": "target_filing",
                        "prompt": "На какую подачу целится клиент.",
                        "label": "Целевая подача",
                    }
                ]
            }
        },
    )
    assert [entity.name for entity in config.meta_entities] == ["target_filing"]


def test_declared_entities_make_the_legacy_allow_lists_a_logged_deprecation(
    tmp_path, caplog
):
    with caplog.at_level(logging.WARNING):
        config = load_config_from_mapping(
            tmp_path,
            {
                "tags": {"allowed": ["O-1"]},
                "meta": {"entities": [{"name": "subject", "prompt": "Тема."}]},
            },
        )
    assert [entity.name for entity in config.meta_entities] == ["subject"]
    assert "tags.allowed" in caplog.text


def test_invalid_meta_entities_fail_the_load_with_the_entity_named(tmp_path):
    with pytest.raises(ValueError) as excinfo:
        load_config_from_mapping(
            tmp_path,
            {"meta": {"entities": [{"name": "manager", "prompt": "Кто."}]}},
        )
    assert "manager" in str(excinfo.value)


def test_whole_config_rewrite_round_trips_entities_with_their_prompts(tmp_path):
    original = load_config_from_mapping(
        tmp_path,
        {
            "meta": {
                "entities": [
                    {
                        "name": "deadlines",
                        "prompt": "Сроки, названные на звонке.",
                        "label": "Дедлайны",
                        "multiple": True,
                    },
                    {
                        "name": "referral",
                        "prompt": "Откуда узнал.",
                        "type": "enum",
                        "allowed": ["telegram"],
                    },
                ]
            }
        },
    )
    rewritten = load_config_from_mapping(tmp_path, config_to_dict(original))
    assert rewritten.meta_entities == original.meta_entities


def test_generated_default_config_ships_the_seven_entities(tmp_path):
    generated = default_config_dict(tmp_path)
    names = [entity["name"] for entity in generated["meta"]["entities"]]
    assert names == [
        "subject",
        "tags",
        "referral",
        "referral_note",
        "case_deadline",
        "deadlines",
        "target_filing",
    ]
    # The allow-lists moved inside the entities; the old top-level homes are gone.
    assert "tags" not in generated
    assert "referrals" not in generated
    referral = next(e for e in generated["meta"]["entities"] if e["name"] == "referral")
    assert "рекомендация" in referral["allowed"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_config.py -k "meta_entit or generated_default_config_ships" -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'meta_entities'`

- [ ] **Step 3: Add the field to `Config`**

In `src/config.py`, replace the `tags_allowed`/`referrals_allowed` comment block
(around line 119-124) with:

```python
    # DEPRECATED: the allow-lists moved into ``meta.entities``. Still read for one
    # more version so a config written before that keeps working; a config that
    # declares ``meta.entities`` ignores these and gets a startup warning.
    tags_allowed: tuple[str, ...] = ()
    referrals_allowed: tuple[str, ...] = ()
    # What the ``meta`` preset extracts, in document order. See src/meta_entity.py.
    meta_entities: tuple[meta_entity.MetaEntity, ...] = ()
```

Add the import at the top of `src/config.py`, beside the other `src` imports:

```python
from src import meta_entity
```

- [ ] **Step 4: Wire the loader**

In `_from_grouped_schema`, after the line
`referrals = _as_mapping(raw.get("referrals"), "referrals")` (around line 763),
add:

```python
    meta = _as_mapping(raw.get("meta"), "meta")
```

After `referrals_allowed = _parse_referrals_allowed(referrals.get("allowed"))`
(around line 775), add:

```python
    raw_entities = meta.get("entities")
    if raw_entities is not None and (tags_allowed or referrals_allowed):
        logger.warning(
            "meta.entities is declared, so the deprecated top-level tags.allowed / "
            "referrals.allowed are ignored; move those values into the entities' "
            "allowed lists and delete the old keys"
        )
    meta_entities = meta_entity.parse_entities(
        raw_entities, tags_allowed=tags_allowed, referrals_allowed=referrals_allowed
    )
```

Add `meta_entities=meta_entities,` to the `Config(...)` construction beside
`referrals_allowed=referrals_allowed,` (around line 980).

- [ ] **Step 5: Teach the serializer to carry entities**

`_config_to_dict` rewrites the whole file on any programmatic config change
(`gdstt stop` is one) and drops anything it does not list. Replace the `tags` /
`referrals` block (around lines 1347-1350) with:

```python
        # Entity definitions are operator-owned data with prompts in them, and this
        # serializer is a whole-file rewrite: omitting them would erase every entity
        # the first time the worker is stopped. The deprecated top-level
        # tags/referrals keys are deliberately not written back -- their values now
        # live inside the entities, so the first rewrite migrates the file.
        "meta": {
            "entities": [_entity_to_dict(entity) for entity in config.meta_entities]
        },
```

Add this helper next to `_config_to_dict`:

```python
def _entity_to_dict(entity: meta_entity.MetaEntity) -> dict[str, object]:
    """Serialize one entity, omitting keys that carry their default."""
    data: dict[str, object] = {"name": entity.name, "type": entity.type}
    if entity.multiple:
        data["multiple"] = True
    if entity.type == "enum":
        data["allowed"] = list(entity.allowed)
    if entity.label is not None:
        data["label"] = entity.label
    if entity.requires:
        data["requires"] = entity.requires
    data["prompt"] = entity.prompt
    return data
```

- [ ] **Step 6: Ship the seven entities in the generated config**

In `_default_config_dict`, delete the `"tags": {"allowed": []}` entry (line 1169)
and the `referrals` entry beside it, and add:

```python
        "meta": {
            "entities": [
                {
                    "name": "subject",
                    "type": "text",
                    "label": "",
                    "prompt": (
                        "Одно предложение о том, про что был звонок. Опирайся "
                        "строго на транскрипт, ничего не выдумывай."
                    ),
                },
                {
                    "name": "tags",
                    "type": "enum",
                    "multiple": True,
                    "label": "Теги",
                    "allowed": [],
                    "prompt": (
                        "Выбери все теги, которые действительно подходят, и "
                        "никакие другие."
                    ),
                },
                {
                    "name": "referral",
                    "type": "enum",
                    "label": "Откуда узнал",
                    "allowed": [
                        "рекомендация",
                        "instagram",
                        "telegram",
                        "youtube",
                        "linkedin",
                        "поиск-google",
                        "реклама",
                        "сми-публикация",
                        "вебинар-мероприятие",
                    ],
                    "prompt": (
                        "Откуда клиент впервые узнал о компании. Заполняй, только "
                        "если клиент сам это сказал: вопрос менеджера без ответа "
                        "источником не является, и твоя догадка по контексту тоже."
                    ),
                },
                {
                    "name": "referral_note",
                    "type": "text",
                    "label": "Подробности",
                    "requires": "referral",
                    "prompt": (
                        "Одна строка словами клиента о том, откуда он узнал о "
                        "компании: кто порекомендовал, какой пост, какое "
                        "мероприятие."
                    ),
                },
                {
                    "name": "case_deadline",
                    "type": "text",
                    "label": "Срок сбора кейса",
                    "prompt": (
                        "К какому сроку клиенту нужно собрать документы кейса. "
                        "Оставь словами клиента, как он сказал на звонке, не "
                        "переводи в дату. Пусто, если о сроке сбора не говорили."
                    ),
                },
                {
                    "name": "deadlines",
                    "type": "text",
                    "multiple": True,
                    "label": "Дедлайны",
                    "prompt": (
                        "Прочие сроки, названные на звонке: виза, работа, учёба, "
                        "переезд. Одна строка на срок, словами клиента, вместе с "
                        "тем, к чему срок относится. Пустой список, если сроков "
                        "не называли."
                    ),
                },
                {
                    "name": "target_filing",
                    "type": "text",
                    "label": "Целевая подача",
                    "prompt": (
                        "На какую подачу целится клиент: тип и/или окно, например "
                        "«O-1 осенью». Пусто, если о цели подачи не говорили."
                    ),
                },
            ]
        },
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS. Existing `tags_allowed`/`referrals_allowed` assertions still pass —
those attributes are untouched.

- [ ] **Step 8: Lint and commit**

```bash
uv run ruff check src/config.py tests/test_config.py
git add src/config.py tests/test_config.py
git commit -m "feat: read meta entities from config.yml"
```

---

## Task 3: The prompt is assembled from the entities

**Files:**
- Modify: `src/meta_entity.py` (add the renderer)
- Modify: `src/config.py` — placeholder constants (lines 45-50), `_render_prompt_placeholders` (line 321), `_resolve_prompt_text` (line 341), `_resolve_presets` (line 393), the `_resolve_presets` call site (line 857)
- Modify: `src/assets/prompts/meta.md` (rewrite)
- Test: `tests/test_meta_entity.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: `MetaEntity` from Task 1, `Config.meta_entities` from Task 2.
- Produces:
  - `meta_entity.render_entities_block(entities: tuple[MetaEntity, ...]) -> str`
  - `config.ENTITIES_PLACEHOLDER = "{{entities}}"`
  - `config._render_prompt_placeholders(text: str, entities: tuple[MetaEntity, ...]) -> str`
  - `config._resolve_prompt_text(preset, config_file, entities)` and
    `config._resolve_presets(config_presets, config_file, entities)`
  - `ALLOWED_TAGS_PLACEHOLDER` and `ALLOWED_REFERRALS_PLACEHOLDER` are **deleted**.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_meta_entity.py`:

```python
def test_rendered_block_carries_the_response_template_and_per_entity_rules():
    entities = meta_entity.parse_entities(
        [
            {"name": "subject", "prompt": "Тема звонка.", "label": ""},
            {
                "name": "tags",
                "prompt": "Подходящие теги.",
                "type": "enum",
                "multiple": True,
                "allowed": ["O-1", "EB-1A"],
            },
            {
                "name": "referral_note",
                "prompt": "Словами клиента.",
                "requires": "referral",
            },
            {"name": "referral", "prompt": "Откуда узнал.", "type": "enum"},
        ]
    )
    block = meta_entity.render_entities_block(entities)

    assert "subject: <value>" in block
    assert "tags: [<value>, <value>]" in block
    assert "Тема звонка." in block
    assert "- O-1" in block
    assert "- EB-1A" in block
    assert "Return a list" in block
    assert "`referral`" in block  # referral_note's requires note
    assert "no values are configured" in block.lower()  # referral's empty allow-list


def test_rendered_block_is_stable_for_a_single_text_entity():
    entities = meta_entity.parse_entities(
        [{"name": "target_filing", "prompt": "Целевая подача."}]
    )
    block = meta_entity.render_entities_block(entities)
    assert "target_filing: <value>" in block
    assert "[<value>" not in block
```

Add to `tests/test_config.py`:

```python
def test_meta_prompt_is_rendered_with_the_configured_entities(tmp_path):
    config = load_config_from_mapping(
        tmp_path,
        {
            "meta": {
                "entities": [
                    {
                        "name": "target_filing",
                        "prompt": "На какую подачу целится клиент.",
                    }
                ]
            },
            "presets": {"meta": {"enabled": True}},
        },
    )
    meta_preset = next(p for p in config.presets if p.name == "meta")
    assert "{{entities}}" not in meta_preset.instructions
    assert "target_filing: <value>" in meta_preset.instructions
    assert "На какую подачу целится клиент." in meta_preset.instructions
    # The entity list replaced the old fixed fields entirely.
    assert "referral_note" not in meta_preset.instructions
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_meta_entity.py::test_rendered_block_carries_the_response_template_and_per_entity_rules tests/test_config.py::test_meta_prompt_is_rendered_with_the_configured_entities -v`
Expected: FAIL — `AttributeError: module 'src.meta_entity' has no attribute 'render_entities_block'`

- [ ] **Step 3: Add the renderer to `src/meta_entity.py`**

```python
def _template_line(entity: MetaEntity) -> str:
    return (
        f"{entity.name}: [<value>, <value>]"
        if entity.multiple
        else f"{entity.name}: <value>"
    )


def _entity_rules(entity: MetaEntity) -> str:
    lines = [f"## {entity.name}", "", entity.prompt, ""]
    if entity.multiple:
        lines.append("- Return a list. Return an empty list (`[]`) when nothing fits.")
    else:
        lines.append("- Return a single value, on one line.")
    if entity.type == "enum":
        if entity.allowed:
            lines.append("- Use ONLY a value from this list, copied verbatim:")
            lines.extend(f"  - {value}" for value in entity.allowed)
        else:
            lines.append(
                "- No values are configured for this field — return it empty."
            )
    if entity.requires:
        lines.append(f"- Leave this empty whenever `{entity.requires}` is empty.")
    return "\n".join(lines)


def render_entities_block(entities: tuple[MetaEntity, ...]) -> str:
    """Render the response template and per-field rules for the ``meta`` prompt.

    The asset holds what is true of every field -- YAML only, transcript's
    language, invent nothing. This holds what is true of each one.
    """
    template = "\n".join(_template_line(entity) for entity in entities)
    rules = "\n\n".join(_entity_rules(entity) for entity in entities)
    return f"---\n{template}\n---\n\nField rules:\n\n{rules}"
```

- [ ] **Step 4: Rewrite `src/assets/prompts/meta.md`**

Replace the whole file with:

```markdown
You are a meeting analyst. You receive a speaker-named transcript of a recorded
conversation and describe it with the fields listed below.

Return ONLY a YAML frontmatter block in exactly this shape and nothing else — no
preamble, no explanation, no Markdown body after it:

{{entities}}

General rules:

- Base every value strictly on the transcript. Never invent a fact, and never
  infer a value the participants did not actually say.
- Write values in the transcript's own language.
- Keep every value on a single line, and quote it if it contains a colon.
- Leave a field empty when the call does not cover it. An empty field is a
  correct answer; a guess is not.
```

- [ ] **Step 5: Swap the placeholder in `src/config.py`**

Replace `ALLOWED_TAGS_PLACEHOLDER` and `ALLOWED_REFERRALS_PLACEHOLDER`
(lines 45-50) with:

```python
# Placeholder a preset prompt may carry to receive the configured meta entities --
# the response template plus each field's rules -- rendered at config load time.
# The built-in ``meta`` prompt uses it; any prompt may.
ENTITIES_PLACEHOLDER = "{{entities}}"
```

Delete `_render_allowed_tags` (line 294) and `_render_allowed_referrals`
(line 315). Keep `_parse_tags_allowed` and `_parse_referrals_allowed` — the
deprecated keys still feed `default_entities`.

Replace `_render_prompt_placeholders` (line 321) with:

```python
def _render_prompt_placeholders(
    text: str, entities: tuple[meta_entity.MetaEntity, ...] = ()
) -> str:
    """Substitute the supported ``{{...}}`` placeholders in a resolved prompt.

    Today that is ``{{entities}}`` (the ``meta`` preset's). A prompt without it is
    returned unchanged, so this is safe to run over every preset's text.
    """
    if ENTITIES_PLACEHOLDER in text:
        text = text.replace(
            ENTITIES_PLACEHOLDER, meta_entity.render_entities_block(entities)
        )
    return text
```

In `_resolve_prompt_text` and `_resolve_presets`, replace the
`tags_allowed: tuple[str, ...] = (), referrals_allowed: tuple[str, ...] = ()`
parameter pairs with `entities: tuple[meta_entity.MetaEntity, ...] = ()`, and
each of the four `_render_prompt_placeholders(..., tags_allowed, referrals_allowed)`
calls with `_render_prompt_placeholders(..., entities)`.

At the call site (line 857) replace:

```python
    presets = _resolve_presets(config_presets, config_file, tags_allowed, referrals_allowed)
```

with:

```python
    presets = _resolve_presets(config_presets, config_file, meta_entities)
```

`meta_entities` is already computed above it (Task 2, Step 4). Confirm the
ordering: the `meta_entities` assignment must precede this line.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_meta_entity.py tests/test_config.py tests/test_preset_dag_e2e.py -v`
Expected: PASS. Any existing test asserting on `{{allowed_tags}}` rendering must be
rewritten to assert on `{{entities}}` — the placeholder is gone, not renamed.

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check src/ tests/
git add src/meta_entity.py src/config.py src/assets/prompts/meta.md tests/
git commit -m "feat: build the meta prompt from the configured entities"
```

---

## Task 4: The parser and the meta document speak dicts

**Files:**
- Modify: `src/meta.py` — delete `Meta` (line 43) and `_REQUOTABLE_FIELDS` (line 56), rewrite `parse_meta` (line 165)
- Modify: `src/meta_doc.py` — `FIELD_ORDER` (line 25) → `field_order(entities)`, `build` (line 106), `to_yaml` (line 141)
- Modify: `src/main.py` — `_write_call_documents` (near line 424)
- Test: `tests/test_meta.py`, `tests/test_meta_doc.py`

**Interfaces:**
- Consumes: `MetaEntity`, `CODE_FIELDS` from Task 1; `Config.meta_entities` from Task 2.
- Produces:
  - `meta.parse_meta(text: str, entities: tuple[MetaEntity, ...]) -> dict[str, str | list[str]]`
  - `meta_doc.field_order(entities: tuple[MetaEntity, ...]) -> tuple[str, ...]`
  - `meta_doc.build(*, values: dict[str, object], file_id, file_name, folder_id, config, transcript, planfix_task_id, processed_at) -> dict[str, object]`
    (the `meta: Meta` keyword becomes `values: dict[str, object]`)
  - `meta_doc.to_yaml(document: dict[str, object], entities: tuple[MetaEntity, ...]) -> str`
  - `src.meta.Meta` no longer exists.

- [ ] **Step 1: Write the failing tests**

Rewrite `tests/test_meta.py`'s `Meta(...)` assertions against dicts. Add these
cases:

```python
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


def test_every_entity_is_present_even_when_the_artifact_is_garbage():
    values = meta.parse_meta("это не yaml, а проза", ENTITIES)
    assert values == {
        "subject": "",
        "tags": [],
        "referral": "",
        "referral_note": "",
        "deadlines": [],
    }


def test_multiple_text_entity_parses_a_list():
    values = meta.parse_meta(
        "---\ndeadlines:\n  - виза до октября\n  - оффер к сентябрю\n---\n", ENTITIES
    )
    assert values["deadlines"] == ["виза до октября", "оффер к сентябрю"]


def test_multiple_entity_accepts_a_bare_scalar_as_one_element():
    values = meta.parse_meta("---\ndeadlines: виза до октября\n---\n", ENTITIES)
    assert values["deadlines"] == ["виза до октября"]


def test_enum_values_outside_the_allow_list_are_dropped():
    values = meta.parse_meta(
        "---\ntags: [O-1, ВЫДУМАННЫЙ]\nreferral: карты-таро\n---\n", ENTITIES
    )
    assert values["tags"] == ["O-1"]
    assert values["referral"] == ""


def test_requires_empties_a_dependent_when_its_target_was_dropped():
    values = meta.parse_meta(
        "---\nreferral: карты-таро\nreferral_note: гадалка посоветовала\n---\n",
        ENTITIES,
    )
    assert values["referral"] == ""
    assert values["referral_note"] == ""


def test_a_colon_in_prose_does_not_take_the_document_down():
    values = meta.parse_meta(
        "---\nsubject: Обсудили визу: сроки и бюджет\ntags: [O-1]\n---\n", ENTITIES
    )
    assert values["subject"] == "Обсудили визу: сроки и бюджет"
    assert values["tags"] == ["O-1"]
```

In `tests/test_meta_doc.py`, replace the two `Meta(...)` constructions with plain
dicts and add:

```python
def test_field_order_puts_entities_first_then_the_code_fields():
    entities = meta_entity.parse_entities(
        [{"name": "target_filing", "prompt": "Подача."}]
    )
    order = meta_doc.field_order(entities)
    assert order[0] == "target_filing"
    assert order[1:] == meta_entity.CODE_FIELDS


def test_to_yaml_keeps_every_entity_present_even_when_empty():
    entities = meta_entity.parse_entities(
        [
            {"name": "subject", "prompt": "Тема.", "label": ""},
            {"name": "deadlines", "prompt": "Сроки.", "multiple": True},
        ]
    )
    text = meta_doc.to_yaml({"subject": "", "deadlines": []}, entities)
    assert "subject: ''" in text
    assert "deadlines: []" in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_meta.py tests/test_meta_doc.py -v`
Expected: FAIL — `TypeError: parse_meta() takes 2 positional arguments but 3 were given`
and `AttributeError: module 'src.meta_doc' has no attribute 'field_order'`

- [ ] **Step 3: Rewrite `parse_meta` in `src/meta.py`**

Delete the `Meta` dataclass, `_REQUOTABLE_FIELDS`, `_parse_tags`, and
`_parse_referral`. Change `_requote_prose` to take the requotable names, and
replace the tail of the module with:

```python
def _requote_prose(body: str, fields: tuple[str, ...]) -> str | None:
    """Apply ``_requote_field`` to every repairable field; None when nothing changed."""
    repaired = body
    changed = False
    for field in fields:
        candidate = _requote_field(repaired, field)
        if candidate is not None:
            repaired = candidate
            changed = True
    return repaired if changed else None


def _parse_value(raw: object, entity: MetaEntity) -> str | list[str]:
    """Normalize one field's value and drop anything outside an enum's allow-list."""
    if raw is None:
        return [] if entity.multiple else ""

    if entity.multiple:
        candidates = list(raw) if isinstance(raw, (list, tuple)) else [raw]
    else:
        candidates = [raw]

    allow_set = {value.strip() for value in entity.allowed}
    values: list[str] = []
    for candidate in candidates:
        value = str(candidate).strip()
        if not value or value in values:
            continue
        if entity.type == "enum" and value not in allow_set:
            logger.debug(
                "dropping meta %s outside the allow-list: %r", entity.name, value
            )
            continue
        values.append(value)

    if entity.multiple:
        return values
    return values[0] if values else ""


def parse_meta(
    text: str, entities: tuple[MetaEntity, ...]
) -> dict[str, str | list[str]]:
    """Read the ``meta`` artifact's YAML frontmatter into one value per entity.

    Every entity is present in the result, empty when the model omitted it or its
    value was rejected. A missing or malformed block yields all-empty values rather
    than raising: the recording already transcribed and its artifacts are already
    written.
    """
    values: dict[str, str | list[str]] = {
        entity.name: ([] if entity.multiple else "") for entity in entities
    }
    requotable = tuple(entity.name for entity in entities if not entity.multiple)
    parsed = _parse_frontmatter(text, requotable)
    if parsed is None:
        return values

    for entity in entities:
        values[entity.name] = _parse_value(parsed.get(entity.name), entity)

    # A dependent that survived while its target was dropped would smuggle the
    # rejected value back in as prose.
    for entity in entities:
        if entity.requires and not values.get(entity.requires):
            values[entity.name] = [] if entity.multiple else ""
    return values
```

Change `_parse_frontmatter(text)` to `_parse_frontmatter(text, requotable)` and
its two `_requote_prose(body)` calls to `_requote_prose(body, requotable)`.

Note the deliberate widening: `requotable` is every **non-multiple** entity, not
only the `text` ones. Re-quoting exists to stop one field's stray colon from
taking the whole document down; an `enum` value with a colon gets dropped by the
allow-list a moment later either way, but the *other* fields survive the parse.

Add the import: `from src.meta_entity import MetaEntity`.

- [ ] **Step 4: Rewrite `src/meta_doc.py`**

Replace `FIELD_ORDER` (lines 23-44) with:

```python
def field_order(entities: tuple[MetaEntity, ...]) -> tuple[str, ...]:
    """The document's field order: what the model extracted, then what code knows.

    Entities come first and in config order -- what the call was about and who was
    on it -- followed by the technical trail.
    """
    return tuple(entity.name for entity in entities) + CODE_FIELDS
```

with `from src.meta_entity import CODE_FIELDS, MetaEntity` at the top.

In `build`, replace the `meta: Meta` keyword with `values: dict[str, object]` and
the returned literal's first four entries with a spread:

```python
    employee = config.folder_by_id(folder_id)
    return {
        **values,
        "manager": employee.name if employee else "",
        "manager_email": employee.email if employee else "",
        "client": _client(file_name),
        "date": _date(file_name),
        "duration": _duration(transcript),
        "language": config.stt_language,
        "planfix_task_id": planfix_task_id,
        "planfix_task_url": task_url(config.planfix_task_url, planfix_task_id),
        "video_id": file_id,
        "video_url": video_url(file_id),
        "source_name": file_name,
        "stt_model": f"{config.stt_provider}/{config.deepgram_model}",
        "llm_model": config.openai_model,
        "processed_at": processed_at.isoformat(),
    }
```

The code fields come after the spread deliberately: a name collision is rejected
at config load, and if one ever slipped through, the code's own value wins.

Replace `to_yaml`:

```python
def to_yaml(document: dict[str, object], entities: tuple[MetaEntity, ...]) -> str:
    """Serialize the document with its declared field order and readable Cyrillic."""
    ordered = {key: document.get(key, "") for key in field_order(entities)}
    return yaml.safe_dump(ordered, allow_unicode=True, sort_keys=False, width=1000)
```

- [ ] **Step 5: Update the call site in `src/main.py`**

In `_write_call_documents` (around line 424):

```python
    values = meta_module.parse_meta(
        _artifact_text("meta", artifacts, config, file_name),
        config.meta_entities,
    )
    task_id = booking_decision.task_id or str(item.get("planfix_comment_task_id") or "")
    document = meta_doc.build(
        values=values,
        file_id=file_id,
        file_name=file_name,
        folder_id=folder_id,
        config=config,
        transcript=transcript,
        planfix_task_id=task_id,
        processed_at=datetime.now(timezone.utc),
    )
    meta_yaml = meta_doc.to_yaml(document, config.meta_entities)
```

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -v`
Expected: PASS. `tests/test_main.py` will need its `Meta(...)` usages and its
`meta_doc.to_yaml(document)` calls updated to the new signatures — do that as
part of this task; it is the same change, not a separate one.

- [ ] **Step 7: Verify the money invariant is untouched**

Run: `uv run pytest tests/test_main.py -k "pending or processed or bookkeeping or missing_preset" -v`
Expected: PASS with no test modified in this task. `.meta.yml` and `.stt` must
still take no part in deciding whether a recording needs work.

- [ ] **Step 8: Lint and commit**

```bash
uv run ruff check src/ tests/
git add src/meta.py src/meta_doc.py src/main.py tests/
git commit -m "feat: parse meta artifacts into one value per configured entity"
```

---

## Task 5: The webhook payload follows the entities

**Files:**
- Modify: `src/main.py` — `_webhook_payload` (line 681)
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `meta.parse_meta(text, entities) -> dict` from Task 4.
- Produces: the payload's `artifacts.meta` object carries one key per entity.
  `_webhook_payload`'s signature is unchanged —
  `(file_id, file_name, folder_id, config, transcript, artifacts)` — it already
  receives `config` and reads `config.meta_entities` off it.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_main.py`, building the config the way the neighbouring
webhook tests already do:

```python
def test_webhook_meta_payload_carries_one_key_per_configured_entity():
    entities = meta_entity.parse_entities(
        [
            {"name": "subject", "prompt": "Тема.", "label": ""},
            {"name": "target_filing", "prompt": "Подача."},
        ]
    )
    config = make_config(meta_entities=entities)
    payload = main._webhook_payload(
        "f1",
        "запись.mp4",
        "folder1",
        config,
        "[00:00:01] Менеджер: привет",
        {"meta": "---\nsubject: Обсудили визу\ntarget_filing: O-1 осенью\n---\n"},
    )
    assert payload["artifacts"]["meta"] == {
        "subject": "Обсудили визу",
        "target_filing": "O-1 осенью",
    }
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_main.py::test_webhook_meta_payload_carries_one_key_per_configured_entity -v`
Expected: FAIL — the payload still carries the fixed four keys, so `target_filing`
is absent and `referral`/`referral_note` are present.

The failure is an assertion mismatch, not a `TypeError`: the signature does not
change in this task.

- [ ] **Step 3: Replace the fixed four keys**

In `src/main.py` (around line 700):

```python
    meta_text = artifacts.get("meta")
    if meta_text is not None:
        payload_artifacts["meta"] = meta_module.parse_meta(
            meta_text, config.meta_entities
        )
```

Update the function's docstring: `meta` is now parsed into one key per configured
entity, with enum values filtered to their allow-lists, instead of the fixed
`{subject, tags, referral, referral_note}`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_main.py -v`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/main.py tests/test_main.py
git add src/main.py tests/test_main.py
git commit -m "feat: key the webhook meta payload by configured entity"
```

---

## Task 6: The Planfix header follows the entities

**Files:**
- Modify: `src/main.py` — `_PLANFIX_META_LABELS` (line 724), `_planfix_meta_lines` (line 770), `_planfix_description` (line 811)
- Modify: `src/config.py` — the `planfix_meta_fields` default, which appears in **three** places: the `Config` dataclass (line 149), `_from_grouped_schema` (line 675), and `_default_config_dict` (line 1203)
- Test: `tests/test_main.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: `MetaEntity.planfix_label`, `MetaEntity.is_heading` from Task 1.
- Produces:
  - `main._PLANFIX_CODE_LABELS: dict[str, str]` — the five code-known fields
  - `main._planfix_meta_lines(document, fields, entities) -> list[str]`
  - `planfix_meta_fields` default:
    `("subject", "tags", "referral", "referral_note", "case_deadline", "deadlines", "target_filing", "duration", "video_url")`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_main.py`:

```python
def test_planfix_header_labels_come_from_the_entities():
    entities = meta_entity.parse_entities(
        [
            {"name": "subject", "prompt": "Тема.", "label": ""},
            {
                "name": "deadlines",
                "prompt": "Сроки.",
                "multiple": True,
                "label": "Дедлайны",
            },
            {"name": "target_filing", "prompt": "Подача."},
        ]
    )
    document = {
        "subject": "Обсудили визу",
        "deadlines": ["виза до октября", "оффер к сентябрю"],
        "target_filing": "O-1 осенью",
        "duration": "00:31:02",
    }
    lines = main._planfix_meta_lines(
        document,
        ("subject", "deadlines", "target_filing", "duration"),
        entities,
    )
    assert lines[0] == "**Обсудили визу**"
    assert "**Дедлайны:** виза до октября, оффер к сентябрю" in lines
    # No label declared, so the name is the label.
    assert "**target_filing:** O-1 осенью" in lines
    # Code-known fields keep their built-in labels.
    assert "**Длительность:** 00:31:02" in lines


def test_planfix_header_skips_an_entity_with_no_value():
    entities = meta_entity.parse_entities(
        [
            {"name": "subject", "prompt": "Тема.", "label": ""},
            {"name": "case_deadline", "prompt": "Срок.", "label": "Срок сбора кейса"},
        ]
    )
    lines = main._planfix_meta_lines(
        {"subject": "Обсудили визу", "case_deadline": ""},
        ("subject", "case_deadline"),
        entities,
    )
    assert lines == ["**Обсудили визу**"]


def test_planfix_header_uses_the_first_empty_label_field_as_the_heading():
    entities = meta_entity.parse_entities(
        [
            {"name": "alt", "prompt": "Другое.", "label": ""},
            {"name": "subject", "prompt": "Тема.", "label": ""},
        ]
    )
    lines = main._planfix_meta_lines(
        {"subject": "Обсудили визу", "alt": "Второй заголовок"},
        ("subject", "alt"),
        entities,
    )
    assert lines[0] == "**Обсудили визу**"
    assert "**Второй заголовок**" in lines[1:]
```

Add to `tests/test_config.py`:

```python
def test_planfix_meta_fields_default_includes_the_new_entities(tmp_path):
    config = load_config_from_mapping(tmp_path, {})
    assert config.planfix_meta_fields == (
        "subject",
        "tags",
        "referral",
        "referral_note",
        "case_deadline",
        "deadlines",
        "target_filing",
        "duration",
        "video_url",
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_main.py -k planfix_header tests/test_config.py::test_planfix_meta_fields_default_includes_the_new_entities -v`
Expected: FAIL — `_planfix_meta_lines() takes 2 positional arguments but 3 were given`

- [ ] **Step 3: Shrink the label map and take entities**

In `src/main.py`, replace `_PLANFIX_META_LABELS` (lines 724-734) with:

```python
# Labels for the meta-document fields the code fills in itself. Entity labels come
# from the entities -- see MetaEntity.planfix_label.
_PLANFIX_CODE_LABELS = {
    "manager": "Менеджер",
    "client": "Клиент",
    "date": "Дата",
    "duration": "Длительность",
    "video_url": "Запись",
}


def _planfix_labels(
    entities: tuple[meta_entity.MetaEntity, ...],
) -> dict[str, str]:
    """Every field that may appear in the comment header, mapped to its label."""
    labels = dict(_PLANFIX_CODE_LABELS)
    for entity in entities:
        labels[entity.name] = entity.planfix_label
    return labels
```

Add `from src import meta_entity` to the imports if it is not already there.

Rewrite `_planfix_meta_lines`:

```python
def _planfix_meta_lines(
    document: dict[str, object] | None,
    fields: tuple[str, ...],
    entities: tuple[meta_entity.MetaEntity, ...],
) -> list[str]:
    """Render the selected meta fields as Markdown lines for the comment header.

    A field that is empty or has no label is skipped silently, so shortening the
    configured list or a call with no referral never leaves a dangling label. A
    field whose label is empty is rendered bold and without a label; the first such
    field in ``fields`` is hoisted to the top as the comment's heading, and any
    further ones follow in place as bold lines.
    """
    if not document:
        return []
    labels = _planfix_labels(entities)
    lines: list[str] = []
    heading_taken = False
    for field_name in fields:
        if field_name not in labels:
            continue
        value = document.get(field_name)
        if isinstance(value, list):
            value = ", ".join(str(entry) for entry in value)
        # Collapse embedded newlines (free LLM text can carry them): markdown_to_html
        # splits on "\n", so an unnormalised value would fracture the header into
        # extra, label-less paragraphs -- or a stray bullet/heading if the
        # continuation happened to start with "- " or "#".
        text = " ".join(str(value or "").split())
        if not text:
            continue
        label = labels[field_name]
        if not label:
            if heading_taken:
                lines.append(f"**{text}**")
            else:
                lines.insert(0, f"**{text}**")
                heading_taken = True
        elif field_name == "video_url":
            # source_name is excluded from the default field list *because* it becomes
            # this anchor's text instead of a line of its own; fall back to the fixed
            # label when the document carries no source_name (or an empty one).
            anchor = str(document.get("source_name") or "").strip()
            anchor = " ".join(anchor.split()) or label
            lines.append(f"[{anchor}]({text})")
        else:
            lines.append(f"**{label}:** {text}")
    return lines
```

`_planfix_description` (line 813) takes
`(artifacts, preset_names, meta_document=None, meta_fields=())` and has no
`config`, so give it a fourth keyword and thread the entities in:

```python
def _planfix_description(
    artifacts: dict[str, str],
    preset_names: tuple[str, ...],
    meta_document: dict[str, object] | None = None,
    meta_fields: tuple[str, ...] = (),
    meta_entities: tuple[meta_entity.MetaEntity, ...] = (),
) -> str:
```

and inside it:

```python
    header = "\n".join(
        _planfix_meta_lines(meta_document, meta_fields, meta_entities)
    )
```

Update the one caller at line 889 to pass `meta_entities=config.meta_entities`.
The default of `()` keeps the three existing tests at `tests/test_main.py:3205`,
`:3216`, and `:3225` calling it unchanged — they pass no meta document, so they
render no header either way.

Keep the `if header and sections:` guard exactly as it is: a header-only comment
gets posted and marked, permanently blocking the real keypoints comment.

- [ ] **Step 4: Update the three `planfix_meta_fields` defaults**

Set all three (`src/config.py:149`, `:675`, `:1203`) to:

```python
    "subject", "tags", "referral", "referral_note",
    "case_deadline", "deadlines", "target_filing",
    "duration", "video_url",
```

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -v`
Expected: PASS

- [ ] **Step 6: Verify the comment body is still one line of HTML**

Run: `uv run pytest tests/test_main.py -k "planfix" -v`
Expected: PASS, including whatever test asserts the rendered body contains no raw
`\n`. If no such test exists, add one:

```python
def test_planfix_comment_body_is_a_single_html_line():
    body = main._planfix_description(...)  # use the file's existing fixture
    assert "\n" not in body
```

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check src/ tests/
git add src/main.py src/config.py tests/
git commit -m "feat: label the Planfix header from the configured entities"
```

---

## Task 7: Documentation

**Files:**
- Modify: `README.md` — lines 28-29, 286, 334-338, 469-470, 517-534, 578, 588
- Modify: `AGENTS.md` — lines 146-147, 180, 262-267
- Modify: `skills/gdstt-cli/SKILL.md` — lines 277, 390 (**≤ 400 lines total**)
- Test: `tests/test_skill_docs.py`

**Interfaces:**
- Consumes: everything from Tasks 1-6. Documents no new code.

- [ ] **Step 1: Run the doc test to see the current state**

Run: `uv run pytest tests/test_skill_docs.py -v && wc -l skills/gdstt-cli/SKILL.md`
Expected: PASS, 400 lines. Every line added below must be paid for by a line
removed.

- [ ] **Step 2: Update `README.md`**

Replace the meta section (around lines 517-534) so it describes entities rather
than four fields:

- The `meta` preset extracts whatever `meta.entities` in `config.yml` declares.
- The entity key table: `name`, `prompt`, `type` (`text`/`enum`), `multiple`,
  `allowed`, `label`, `requires` — copy the table from the spec.
- The shipped default is seven entities: `subject`, `tags`, `referral`,
  `referral_note`, `case_deadline`, `deadlines`, `target_filing`.
- Replace the `{{allowed_tags}}` / `{{allowed_referrals}}` paragraph (lines
  469-470, 522-524) with `{{entities}}`: one placeholder, rendered at config load
  time into the response template plus each field's rules.
- Update the `tags.allowed` / `referrals.allowed` config-table rows (lines
  334-335) to say the keys are deprecated, still read when `meta.entities` is
  absent, and that their values belong in the entities' `allowed` lists.
- Update `planfix.meta_fields` (lines 286, 338) to the new nine-field default.
- Update the webhook payload example (lines 578, 588): `meta` carries one key per
  configured entity.

Add a short migration note under the config table:

> `tags.allowed` and `referrals.allowed` are read for one more version when
> `meta.entities` is absent. Once `meta.entities` is declared, the old keys are
> ignored and logged at startup. The first whole-config rewrite (`gdstt stop` is
> one) migrates the file to the new form.

- [ ] **Step 3: Update `AGENTS.md`**

- Lines 146-147: the `meta` preset answers with a frontmatter block whose fields
  come from `meta.entities`, parsed back by `src/meta.py` into a dict.
- Line 180: the webhook's `meta` object carries one key per configured entity.
- Lines 262-267: replace the `tags.allowed`/`referrals.allowed` paragraph — an
  `enum` entity's `allowed` list is the only vocabulary the model may use for
  that field, it reaches the prompt through `{{entities}}`, `src/meta.py` drops
  off-list values, and `requires` empties a dependent whenever its target was
  dropped.

Add one line naming the new module:

> `src/meta_entity.py` — what the `meta` preset extracts, as config-shaped data.
> A leaf module: `config`, `meta`, and `meta_doc` all import it.

- [ ] **Step 4: Update `skills/gdstt-cli/SKILL.md` within the line cap**

- Line 277: `meta` пишет `<base>.meta.md` из YAML-frontmatter, поля которого
  задаёт `meta.entities` в конфиге.
- Line 390: `meta` разобран в словарь «одна сущность — один ключ».

To stay at ≤ 400 lines, compress the surrounding prose rather than deleting a
documented command. Verify with `wc -l` before committing.

- [ ] **Step 5: Verify**

Run: `uv run pytest tests/test_skill_docs.py -v && wc -l skills/gdstt-cli/SKILL.md`
Expected: PASS, ≤ 400 lines.

- [ ] **Step 6: Full suite and lint**

```bash
uv run pytest
uv run ruff check src/ tests/
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add README.md AGENTS.md skills/gdstt-cli/SKILL.md
git commit -m "docs: describe config-driven meta entities"
```

---

## Manual verification

Run after Task 7, before opening the PR. Nothing here spends Deepgram money: the
recording below is already transcribed and its `.txt` is on disk.

- [ ] **Migrate the local config.** Add a `meta.entities` block to
  `data/config.yml` carrying the seven entities, moving the nine values from
  `referrals.allowed` into the `referral` entity's `allowed` list, then delete the
  top-level `tags:` and `referrals:` sections. Edit by hand with line-anchored
  changes — `data/config.yml` carries eight explanatory comments that a YAML
  round-trip would strip. `run.enabled` must stay `false` locally.

- [ ] **Confirm the config loads and the prompt renders.**

```bash
uv run gdstt config show 2>&1 | head -40
```

Expected: no deprecation warning (the old keys are gone), no validation error.

- [ ] **Re-run the meta preset on the one fully processed recording** — the
  13.08 Angelica × Mels call, file `1tgpgW-MNkVHqJ4WHlB3x1tECFxArGUcy`. Use
  `mp3=skip, txt=skip, presets=meta` so nothing is re-transcribed. Confirm the
  regenerated `.meta.yml` carries all seven entity fields, that `case_deadline`,
  `deadlines`, and `target_filing` are present (empty is a valid answer if the
  call never covered them), and that `referral: рекомендация` and its note
  survived — that call contains "Вы к нам по рекомендации от друга, да?" / "Да,
  да, друга."

- [ ] **Send the comment to the test task, not the real one.** Post the
  regenerated Planfix comment to task **861300** by calling
  `planfix.send_comment` directly, bypassing `_send_planfix_comment`, so the real
  `planfix_comment_task_id: 918659` marker stays untouched. Confirm in the Planfix
  UI that the new labelled lines render as separate lines and empty entities left
  no dangling labels.

- [ ] **Prove the whole-config rewrite is safe.** Copy `data/config.yml` aside,
  run a command that triggers a whole-Config rewrite, and diff: every entity and
  every prompt must survive.

- [ ] **Confirm the money invariant.** `uv run gdstt` one polling cycle against
  the real folders in dry-run and read the cycle summary: `pending=0,
  processed=0, failed=0, skipped_unmatched=0`. Any non-zero `pending` means a
  recording was pushed back into the transcription queue — stop and investigate
  before merging.

- [ ] **Production config.** Only after the PR is merged and deployed: apply the
  same `meta.entities` migration to `data/config.yml` on
  `us1.dev.expertizeme.org`, keeping `run.enabled: true`, then confirm the next
  cycle reports `pending=0`.
