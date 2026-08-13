"""Decide which diarized speaker is which person, instead of guessing from turn order.

``postprocess.map_speakers`` binds names to speakers positionally: the first name goes
to whoever talks first. The names come from the recording's file name, where Meet lists
the organizer first -- so any call the client opens comes out with the two participants
swapped, and every summary built on it inherits the swap.

Nothing in the audio says who is who, but the opening exchange does, and we already know
one answer for certain: the folder's owner is the manager. This module hands the model
that fact plus the first turns and asks it to place the names. The reply is validated
against the candidate list, so a hallucinated name cannot relabel a transcript, and any
failure returns ``None`` -- the caller then keeps the old positional behaviour rather
than losing a recording that already cost money to transcribe.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable

from src.postprocess import real_speaker_order

logger = logging.getLogger(__name__)

DEFAULT_TURNS = 30

_SPEAKER_LINE_RE = re.compile(r"^\s*(?:\[[0-9:.,\s]+\]\s*)?Speaker\s+(\d+)\s*:\s*(.*)$")
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

INSTRUCTIONS = """
You decide which diarized speaker is which participant of a recorded call.

You receive the opening turns of a transcript, labelled `Speaker 1`, `Speaker 2`, and a
list of participant names. Exactly one named participant is the manager: an employee of
ExpertizeMe who runs the call. The others are clients.

Return ONLY a JSON object mapping each speaker number to one participant name:

{"1": "<name>", "2": "<name>"}

Rules:

- Use the names exactly as given. Never invent, translate, or reformat a name.
- Use each name at most once.
- Decide from what people say: who introduces themselves, who represents the company,
  who asks about the service and who explains it.
- Do not assume the manager speaks first. Clients often open the call.
- Return no preamble, explanation, or Markdown fence.
""".strip()


def _sample_turns(transcript: str, turns: int) -> str:
    """The first ``turns`` speaker-labelled lines, normalized to ``Speaker N: text``."""
    picked: list[str] = []
    for line in transcript.replace("\r\n", "\n").split("\n"):
        match = _SPEAKER_LINE_RE.match(line)
        if match is None:
            continue
        picked.append(f"Speaker {int(match.group(1))}: {match.group(2).strip()}")
        if len(picked) >= turns:
            break
    return "\n".join(picked)


def _build_input(sample: str, candidates: list[str], manager_name: str) -> str:
    listed = "\n".join(f"- {name}" for name in candidates)
    manager_line = (
        f"The manager is: {manager_name}"
        if manager_name
        else "The manager's name is not known; infer it from the conversation."
    )
    return f"Participants:\n{listed}\n\n{manager_line}\n\nOpening turns:\n{sample}"


def _parse_mapping(reply: str, candidates: list[str]) -> dict[int, str] | None:
    """Read ``{"1": "Name"}`` out of a reply, or None when it cannot be trusted."""
    match = _JSON_OBJECT_RE.search(reply or "")
    if match is None:
        return None
    try:
        raw = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(raw, dict):
        return None

    allowed = set(candidates)
    mapping: dict[int, str] = {}
    for key, value in raw.items():
        try:
            number = int(str(key).strip().removeprefix("Speaker").strip())
        except ValueError:
            return None
        if not isinstance(value, str) or value not in allowed:
            # An invented or reformatted name would relabel the whole transcript.
            return None
        mapping[number] = value

    if len(set(mapping.values())) != len(mapping):
        # One person cannot be both speakers; a duplicate means the model guessed.
        return None
    return mapping or None


def resolve(
    transcript: str,
    *,
    candidates: list[str],
    manager_name: str,
    run: Callable[[str, str], tuple[str, dict]],
    turns: int = DEFAULT_TURNS,
) -> list[str] | None:
    """Order ``candidates`` the way ``map_speakers`` needs them, or None to fall back.

    ``run`` is the one-shot LLM primitive (``OpenAIPipeline.run``). The returned list is
    ordered by the speakers' first appearance, because that is the order
    ``map_speakers`` assigns names in -- the two must agree or the labels swap.
    """
    if len(candidates) < 2:
        return None

    order = real_speaker_order(transcript, expected=len(candidates))
    if len(order) < 2:
        return None

    sample = _sample_turns(transcript, turns)
    if not sample:
        return None

    try:
        reply, _ = run(INSTRUCTIONS, _build_input(sample, candidates, manager_name))
    except Exception as exc:
        logger.warning("Speaker role mapping failed (%s); keeping name order", type(exc).__name__)
        return None

    mapping = _parse_mapping(reply, candidates)
    if mapping is None:
        logger.warning("Speaker role mapping returned no usable answer; keeping name order")
        return None

    names = [mapping.get(number) for number in order]
    if any(name is None for name in names):
        logger.warning("Speaker role mapping skipped a speaker; keeping name order")
        return None
    return [name for name in names if name is not None]
