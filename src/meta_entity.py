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

    # Existence pass first, over every entity's own `requires` target: a chain
    # like a->b->nope must name `nope` (b's stale link) rather than raising
    # KeyError from inside the cycle walk below, which only ever dereferences
    # links it has already confirmed exist.
    for entity in entities:
        if entity.requires and entity.requires not in by_name:
            raise ValueError(
                f"meta entity {entity.name!r} requires {entity.requires!r}, "
                "which is not a declared entity"
            )

    for entity in entities:
        if not entity.requires:
            continue
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
            # Deliberately `info`, not `warning`: the shipped default config
            # declares `tags` as an enum with an empty `allowed` (the vocabulary
            # is the operator's to fill in), so this fires on every load of a
            # fresh install. A valid, working default is not worth a WARNING --
            # that just trains operators to ignore warnings.
            logger.info(
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


def _template_line(entity: MetaEntity) -> str:
    # List items are quoted in the template because the model copies its shape, and
    # a real answer routinely contains a comma ("не раньше, чем 25-е число — ..."):
    # unquoted, YAML reads that one deadline as two entries and the CRM comment gets
    # sentence fragments. Quoting costs nothing and cannot be recovered downstream,
    # because by parse time the phrase is already split.
    return (
        f'{entity.name}: ["<value>", "<value>"]'
        if entity.multiple
        else f"{entity.name}: <value>"
    )


def _entity_rules(entity: MetaEntity) -> str:
    lines = [f"## {entity.name}", "", entity.prompt, ""]
    if entity.multiple:
        lines.append(
            "- Return a list, wrapping every item in double quotes so a comma "
            "inside an item cannot split it. Return an empty list (`[]`) when "
            "nothing fits."
        )
    else:
        lines.append("- Return a single value, on one line.")
    if entity.type == "enum":
        if entity.allowed:
            lines.append("- Use ONLY a value from this list, copied verbatim:")
            lines.extend(f"  - {value}" for value in entity.allowed)
            # Without this the model answers the question rather than the list: asked
            # for a referral channel by a client who said "Indeed" -- absent from the
            # list -- it returned `telegram`, the nearest listed value. The parser
            # drops off-list values, so the only thing that clause buys is silence
            # instead of a confident wrong answer reaching the CRM.
            lines.append(
                "- If what was actually said is not on the list, leave this field "
                "empty. Do not substitute the closest listed value."
            )
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
