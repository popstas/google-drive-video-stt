"""Parse the ``meta`` preset's artifact into a topic plus allow-listed tags.

The ``meta`` preset answers with a YAML frontmatter block (``topic:`` and
``tags:``) so the completion webhook can forward structured fields instead of raw
prose. The reply comes from an LLM, so every failure mode here degrades to
``Meta(topic="", tags=())`` rather than raising: a garbled block must never fail a
file that already transcribed and wrote its artifacts.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass

import yaml

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


# The `topic:` line, when the model gave it a plain unquoted value.
_TOPIC_LINE_RE = re.compile(
    r"^(?P<indent>[ \t]*)topic:[ \t]*(?P<value>\S.*?)[ \t]*$",
    re.MULTILINE,
)

# Value openers we must not second-guess: a quoted scalar is already well-formed,
# and `|`/`>`/`&`/`*`/`!` are YAML indicators whose meaning quoting would destroy.
_YAML_VALUE_INDICATORS = "\"'|>&*!"


@dataclass(frozen=True)
class Meta:
    """The ``meta`` preset's structured output."""

    topic: str = ""
    tags: tuple[str, ...] = ()


def _requote_topic(body: str) -> str | None:
    """Re-quote an unquoted ``topic:`` value so a colon inside it can't break the doc.

    ``topic`` is one sentence of prose in the transcript's own language, so it carries
    a colon routinely — and the prompt's "quote it if it contains a colon" rule is a
    request a model is free to ignore. YAML then reads the line as a nested mapping and
    rejects the whole document, taking ``tags`` down with it even though ``tags`` parsed
    fine on its own line. Returns ``None`` when there is nothing safe to repair.
    """
    match = _TOPIC_LINE_RE.search(body)
    if match is None:
        return None
    value = match.group("value")
    if value[0] in _YAML_VALUE_INDICATORS or ":" not in value:
        return None
    # A JSON string is also a valid YAML double-quoted scalar, escaping included.
    quoted = json.dumps(value, ensure_ascii=False)
    repaired = f"{match.group('indent')}topic: {quoted}"
    return body[: match.start()] + repaired + body[match.end() :]


def _parse_frontmatter(text: str) -> dict | None:
    fence = _FENCE_RE.match(text or "")
    unfenced = fence.group("inner") if fence else (text or "")
    match = _FRONTMATTER_RE.search(unfenced)
    # A model that answered with the bare `topic:`/`tags:` mapping and no `---`
    # delimiters still carries both fields, so load the whole document rather than
    # discard them. Prose parses to a non-mapping and is rejected just below.
    body = match.group("body") if match else unfenced
    try:
        parsed = yaml.safe_load(body)
    except yaml.YAMLError as exc:
        # Only a document that already failed gets repaired, so a well-formed reply
        # keeps parsing exactly as written.
        repaired = _requote_topic(body)
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


def _parse_tags(raw: object, allowed: Iterable[str]) -> tuple[str, ...]:
    """Normalize the ``tags:`` value and drop anything outside the allow-list.

    An empty ``allowed`` drops every tag: a config with no ``tags.allowed`` gave the
    model nothing to pick from, so any tag it returned is invented.
    """
    if raw is None:
        return ()
    if isinstance(raw, (list, tuple)):
        candidates = raw
    else:
        # A model answering `tags: O-1` instead of a list shouldn't lose the tag.
        candidates = [raw]

    allow_set = {str(tag).strip() for tag in allowed}
    tags: list[str] = []
    for candidate in candidates:
        tag = str(candidate).strip()
        if not tag or tag in tags:
            continue
        if tag not in allow_set:
            logger.debug("dropping meta tag outside the allow-list: %r", tag)
            continue
        tags.append(tag)
    return tuple(tags)


def parse_meta(text: str, allowed: Iterable[str]) -> Meta:
    """Read ``topic``/``tags`` from a ``meta`` artifact's YAML frontmatter.

    ``allowed`` is the configured tag allow-list; tags outside it are dropped, and an
    empty allow-list drops all of them. A missing or malformed block yields
    ``Meta(topic="", tags=())``.
    """
    parsed = _parse_frontmatter(text)
    if parsed is None:
        return Meta()

    topic_raw = parsed.get("topic")
    topic = "" if topic_raw is None else str(topic_raw).strip()
    return Meta(topic=topic, tags=_parse_tags(parsed.get("tags"), allowed))
