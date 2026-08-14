"""Parse the ``meta`` preset's artifact into one value per configured entity.

The ``meta`` preset answers with a YAML frontmatter block shaped by the operator's
configured entities (``data/config.yml``'s ``meta.entities``) so the completion
webhook can forward structured fields instead of raw prose. The reply comes from an
LLM, so every failure mode here degrades to an all-empty dict rather than raising: a
garbled block must never fail a file that already transcribed and wrote its
artifacts.
"""

from __future__ import annotations

import json
import logging
import re

import yaml

from src.meta_entity import MetaEntity

logger = logging.getLogger(__name__)

# The first YAML frontmatter block: ``---`` at the start of a line, the body, then a
# closing ``---``. Prose on either side of the block (a model that introduced or
# appended to its answer despite the prompt) is ignored rather than allowed to take
# both fields down with it.
_FRONTMATTER_RE = re.compile(
    r"^﻿?[ \t]*---[ \t]*\r?\n(?P<body>.*?)\r?\n---[ \t]*(?:\r?\n|\Z)",
    re.DOTALL | re.MULTILINE,
)

# A Markdown code fence the model wrapped its whole answer in (```yaml … ```)
# despite the prompt. Common enough that unwrapping it beats losing the fields.
_FENCE_RE = re.compile(
    r"\A\s*```[^\n]*\r?\n(?P<inner>.*?)\r?\n?```\s*\Z",
    re.DOTALL,
)

# Value openers we must not second-guess: a quoted scalar is already well-formed,
# and `|`/`>`/`&`/`*`/`!` are YAML indicators whose meaning quoting would destroy.
_YAML_VALUE_INDICATORS = "\"'|>&*!"


def _field_line_re(field: str) -> re.Pattern[str]:
    return re.compile(
        rf"^(?P<indent>[ \t]*){field}:[ \t]*(?P<value>\S.*?)[ \t]*$",
        re.MULTILINE,
    )


def _requote_field(body: str, field: str) -> str | None:
    """Re-quote one unquoted scalar so a colon inside it can't break the document."""
    match = _field_line_re(field).search(body)
    if match is None:
        return None
    value = match.group("value")
    if value[0] in _YAML_VALUE_INDICATORS or ":" not in value:
        return None
    quoted = json.dumps(value, ensure_ascii=False)
    repaired = f"{match.group('indent')}{field}: {quoted}"
    return body[: match.start()] + repaired + body[match.end() :]


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


def _parse_frontmatter(text: str, requotable: tuple[str, ...]) -> dict | None:
    fence = _FENCE_RE.match(text or "")
    unfenced = fence.group("inner") if fence else (text or "")
    match = _FRONTMATTER_RE.search(unfenced)
    # A model that answered with the bare `subject:`/`tags:` mapping and no `---`
    # delimiters still carries the fields, so load the whole document rather than
    # discard them. Prose parses to a non-mapping and is rejected just below.
    body = match.group("body") if match else unfenced
    try:
        parsed = yaml.safe_load(body)
    except yaml.YAMLError as exc:
        # Only a document that already failed gets repaired, so a well-formed reply
        # keeps parsing exactly as written.
        repaired = _requote_prose(body, requotable)
        if repaired is None:
            logger.warning("meta artifact is not valid YAML: %s", type(exc).__name__)
            return None
        try:
            parsed = yaml.safe_load(repaired)
        except yaml.YAMLError:
            logger.warning("meta artifact is not valid YAML: %s", type(exc).__name__)
            return None
    if not isinstance(parsed, dict):
        # An unparseable meta artifact is operator-actionable: the file transcribed
        # fine and its artifact is on Drive, but the webhook ships empty fields.
        logger.warning("meta artifact is not a YAML mapping: %s", type(parsed).__name__)
        return None
    return parsed


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
    # rejected value back in as prose. A chain (A requires B, B requires C) needs
    # more than one pass: B is only emptied partway through a single pass, so a
    # single pass can leave A holding B's stale, pre-drop value. Repeat until a
    # pass changes nothing. `_validate` guarantees `requires` chains are acyclic,
    # so this always converges; the range bound is kept anyway so a future
    # validation regression degrades to a wrong-but-bounded result instead of
    # hanging the parser.
    for _ in range(len(entities)):
        changed = False
        for entity in entities:
            if not entity.requires or values.get(entity.requires):
                continue
            empty = [] if entity.multiple else ""
            if values[entity.name] != empty:
                values[entity.name] = empty
                changed = True
        if not changed:
            break
    return values
