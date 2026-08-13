"""Parse the ``meta`` preset's artifact into a subject plus allow-listed fields.

The ``meta`` preset answers with a YAML frontmatter block (``subject:``, ``tags:``,
``referral:``, ``referral_note:``) so the completion webhook can forward structured
fields instead of raw prose. The reply comes from an LLM, so every failure mode here
degrades to an empty ``Meta()`` rather than raising: a garbled block must never fail a
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

# Value openers we must not second-guess: a quoted scalar is already well-formed,
# and `|`/`>`/`&`/`*`/`!` are YAML indicators whose meaning quoting would destroy.
_YAML_VALUE_INDICATORS = "\"'|>&*!"


@dataclass(frozen=True)
class Meta:
    """The ``meta`` preset's structured output."""

    subject: str = ""
    tags: tuple[str, ...] = ()
    referral: str = ""
    referral_note: str = ""


# The prose fields a model routinely writes unquoted, and routinely writes a colon
# into. YAML then reads the line as a nested mapping and rejects the whole document,
# taking the fields that parsed fine down with it.
_REQUOTABLE_FIELDS = ("subject", "referral_note")


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


def _requote_prose(body: str) -> str | None:
    """Apply ``_requote_field`` to every repairable field; None when nothing changed."""
    repaired = body
    changed = False
    for field in _REQUOTABLE_FIELDS:
        candidate = _requote_field(repaired, field)
        if candidate is not None:
            repaired = candidate
            changed = True
    return repaired if changed else None


def _parse_frontmatter(text: str) -> dict | None:
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
        repaired = _requote_prose(body)
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


def _parse_referral(raw: object, allowed: Iterable[str]) -> str:
    """Return the referral channel when it is on the allow-list, else an empty string.

    An empty ``allowed`` rejects everything: a config with no ``referrals.allowed``
    gave the model nothing to pick from, so any channel it returned is invented.
    """
    if raw is None:
        return ""
    channel = str(raw).strip()
    if not channel:
        return ""
    if channel not in {str(entry).strip() for entry in allowed}:
        logger.debug("dropping meta referral outside the allow-list: %r", channel)
        return ""
    return channel


def parse_meta(
    text: str, allowed: Iterable[str], referrals_allowed: Iterable[str] = ()
) -> Meta:
    """Read the ``meta`` artifact's YAML frontmatter into structured fields.

    ``allowed``/``referrals_allowed`` are the configured allow-lists; values outside
    them are dropped, and an empty allow-list drops all of them. A missing or malformed
    block yields an empty ``Meta``.
    """
    parsed = _parse_frontmatter(text)
    if parsed is None:
        return Meta()

    subject_raw = parsed.get("subject")
    subject = "" if subject_raw is None else str(subject_raw).strip()
    referral = _parse_referral(parsed.get("referral"), referrals_allowed)
    note_raw = parsed.get("referral_note")
    note = "" if note_raw is None else str(note_raw).strip()
    return Meta(
        subject=subject,
        tags=_parse_tags(parsed.get("tags"), allowed),
        referral=referral,
        # A note without a surviving channel describes a source we rejected; keeping
        # it would smuggle an off-list channel back in as prose.
        referral_note=note if referral else "",
    )
