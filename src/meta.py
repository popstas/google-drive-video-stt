"""Parse the ``meta`` preset's artifact into a topic plus allow-listed tags.

The ``meta`` preset answers with a YAML frontmatter block (``topic:`` and
``tags:``) so the completion webhook can forward structured fields instead of raw
prose. The reply comes from an LLM, so every failure mode here degrades to
``Meta(topic="", tags=())`` rather than raising: a garbled block must never fail a
file that already transcribed and wrote its artifacts.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass

import yaml

logger = logging.getLogger(__name__)

# A leading YAML frontmatter block: optional BOM/blank lines, ``---``, the body,
# then a closing ``---``. Anything after the block (a model that appended prose
# despite the prompt) is ignored.
_FRONTMATTER_RE = re.compile(
    r"\A﻿?\s*---[ \t]*\r?\n(?P<body>.*?)\r?\n---[ \t]*(?:\r?\n|\Z)",
    re.DOTALL,
)


@dataclass(frozen=True)
class Meta:
    """The ``meta`` preset's structured output."""

    topic: str = ""
    tags: tuple[str, ...] = ()


def _parse_frontmatter(text: str) -> dict | None:
    match = _FRONTMATTER_RE.match(text or "")
    if match is None:
        logger.debug("meta artifact has no YAML frontmatter block")
        return None
    try:
        parsed = yaml.safe_load(match.group("body"))
    except yaml.YAMLError as exc:
        logger.warning("meta frontmatter is not valid YAML: %s", type(exc).__name__)
        return None
    if not isinstance(parsed, dict):
        logger.warning("meta frontmatter is not a mapping: %s", type(parsed).__name__)
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
