"""Decide which diarized speaker is which person, from everything the call left behind.

``postprocess.map_speakers`` binds names to speakers positionally: the first name goes
to whoever talks first. No source of names knows that order. A file name lists the
organizer first, so any call the client opens comes out swapped. Meet's transcript
lists people in the order Meet heard them, which need not be the order diarization
did -- on a real call the two disagreed and the labels came out swapped.

Nothing in the audio says who is who, but the call does, and this module hands the
model all of it at once:

- the diarized transcript for its first minutes after anyone speaks -- long enough to
  reach the company's introduction, which the opening hellos and mic checks are not;
- Meet's own transcript of the same minutes, when there is one. Its words are often
  wrong, but each turn is tied to the account that spoke;
- who the manager should be: the owner of the folder the recording came from, and the
  name the calendar title marked with the company, when it has one.

The reply is validated against the candidate list, so a hallucinated name cannot
relabel a transcript, and any failure -- or an honest "cannot tell" -- returns ``None``:
the caller then decides what an unconfirmed mapping is worth.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable

from src import meet_transcript
from src.postprocess import real_speaker_order

logger = logging.getLogger(__name__)

# Minutes of transcript after the first speech. The opening minute or two is hellos
# and "can you hear me"; the company's introduction usually follows within ten.
WINDOW_SECONDS = 600

# Caps that keep the request small whatever the call looks like: ~9000 characters of
# Russian is about 3k tokens. A single turn is cut short so that one long monologue
# cannot crowd the other speaker out of the sample.
MAX_TRANSCRIPT_CHARS = 9000
MAX_MEET_CHARS = 3000
MAX_TURN_CHARS = 1200
MAX_MEET_TURN_CHARS = 300

_SPEAKER_LINE_RE = re.compile(
    r"^\s*(?:\[(?P<time>[0-9:.,\s]+)\]\s*)?Speaker\s+(?P<num>\d+)\s*:\s*(?P<text>.*)$"
)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

INSTRUCTIONS = """
You decide which diarized speaker is which participant of a recorded call.

You receive:

- the participant names;
- who the manager should be: the employee whose Drive folder holds the recording, and
  the name the calendar title marked as the company's, when there is one. Either may
  be spelled differently from the participant list (another alphabet, a short form):
  match them by person, not by spelling. The folder owner may not be on the call;
- the transcript from its first speech, with speakers labelled `Speaker N`;
- when available, Google Meet's own transcript of the same minutes. Meet ties every
  turn to the account that spoke, so who spoke, how often and at what length is
  reliable. Its speech recognition often failed (Russian heard as English), so do not
  read meaning into its words. Meet only marks time every few minutes: a turn's time is
  the last mark before it.

Exactly one named participant is the manager: an employee of ExpertizeMe who runs the
call. The others are clients.

Return ONLY a JSON object mapping each speaker number, exactly as it appears in the
transcript, to one participant name:

{"1": "<name>", "2": "<name>"}

Rules:

- Use the names exactly as given in the participant list. Never invent, translate, or
  reformat a name.
- Use each name at most once.
- Decide from the evidence: who introduces the company and explains its service, who
  describes their own situation and asks about it, and which speaker's long and short
  turns line up with which participant's turns in Meet's transcript.
- Do not assume the manager speaks first. Clients often open the call.
- If the evidence does not settle it, return {} instead of guessing.
- Return no preamble, explanation, or Markdown fence.
""".strip()


def _offset(raw: str | None) -> int | None:
    """Seconds from a ``HH:MM:SS`` (or ``MM:SS``) stamp, or None when there is none."""
    if not raw:
        return None
    try:
        parts = [float(part.replace(",", ".")) for part in raw.strip().split(":")]
    except ValueError:
        return None
    if not parts or len(parts) > 3:
        return None
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return int(seconds)


def _stamp(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _render(lines: list[str], max_chars: int) -> str:
    """Whole lines up to ``max_chars``: a cut line would read as a finished turn."""
    kept: list[str] = []
    used = 0
    for line in lines:
        if kept and used + len(line) + 1 > max_chars:
            break
        kept.append(line)
        used += len(line) + 1
    return "\n".join(kept)


def _opening_turns(
    transcript: str,
    window_seconds: int = WINDOW_SECONDS,
    max_chars: int = MAX_TRANSCRIPT_CHARS,
) -> tuple[str, int, int]:
    """The transcript from its first speech for ``window_seconds``, as merged turns.

    Returns the sample and the window it covers in seconds, so Meet's turns can be cut
    to the same minutes. The window starts at the first speech, not at zero: a
    recording often runs silent for minutes before anyone joins.

    Diarization starts a new line whenever the voice changes, so consecutive lines of
    one speaker are joined back into the turn they were.
    """
    entries: list[tuple[int | None, int, str]] = []
    for line in transcript.replace("\r\n", "\n").split("\n"):
        match = _SPEAKER_LINE_RE.match(line)
        if match is None:
            continue
        entries.append(
            (_offset(match.group("time")), int(match.group("num")), match.group("text").strip())
        )
    if not entries:
        return "", 0, 0

    start = next((offset for offset, _, _ in entries if offset is not None), None)
    merged: list[list] = []
    for offset, number, text in entries:
        if start is not None and offset is not None and offset >= start + window_seconds:
            break
        if merged and merged[-1][1] == number:
            if text:
                merged[-1][2] = f"{merged[-1][2]} {text}".strip()
            continue
        merged.append([offset, number, text])

    lines = []
    for offset, number, text in merged:
        prefix = f"[{_stamp(offset)}] " if offset is not None else ""
        lines.append(f"{prefix}Speaker {number}: {_clip(text, MAX_TURN_CHARS)}")
    begin = start or 0
    return _render(lines, max_chars), begin, begin + window_seconds


def _meet_turns(
    meet_text: str, start: int, end: int, max_chars: int = MAX_MEET_CHARS
) -> str:
    """Meet's turns for the blocks that overlap ``[start, end)``."""
    turns = meet_transcript.turns(meet_text)
    if not turns:
        return ""
    # A turn carries the start of its block, so the block holding ``start`` begins at
    # the last mark at or before it.
    first_block = max((offset for offset, _, _ in turns if offset <= start), default=0)
    merged: list[list] = []
    for offset, name, said in turns:
        if offset < first_block:
            continue
        if offset >= end:
            break
        if merged and merged[-1][0] == offset and merged[-1][1] == name:
            merged[-1][2] = f"{merged[-1][2]} {said}".strip()
            continue
        merged.append([offset, name, said])
    lines = [
        f"[{_stamp(offset)}] {name}: {_clip(said, MAX_MEET_TURN_CHARS)}"
        for offset, name, said in merged
    ]
    return _render(lines, max_chars)


def _build_input(
    sample: str,
    candidates: list[str],
    manager_name: str,
    *,
    calendar_manager: str = "",
    meet_sample: str = "",
) -> str:
    listed = "\n".join(f"- {name}" for name in candidates)
    hints = [
        f"The recording is in the Drive folder of: {manager_name}"
        if manager_name
        else "The folder owner is not known; infer the manager from the conversation."
    ]
    if calendar_manager:
        hints.append(f"The calendar title marks as the company's side: {calendar_manager}")
    parts = [
        f"Participants:\n{listed}",
        "\n".join(hints),
        f"Transcript, from its first speech:\n{sample}",
    ]
    if meet_sample:
        parts.append(f"Google Meet's transcript of the same minutes:\n{meet_sample}")
    return "\n\n".join(parts)


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
    meet_text: str = "",
    calendar_manager: str = "",
    window_seconds: int = WINDOW_SECONDS,
) -> list[str] | None:
    """Order ``candidates`` the way ``map_speakers`` needs them, or None when unsure.

    ``run`` is the one-shot LLM primitive (``OpenAIPipeline.run``). ``meet_text`` is
    Meet's transcript as exported, or empty. The returned list is ordered by the
    speakers' first appearance, because that is the order ``map_speakers`` assigns
    names in -- the two must agree or the labels swap.
    """
    if len(candidates) < 2:
        return None

    order = real_speaker_order(transcript, expected=len(candidates))
    if len(order) < 2:
        return None

    sample, start, end = _opening_turns(transcript, window_seconds)
    if not sample:
        return None
    meet_sample = _meet_turns(meet_text, start, end) if meet_text else ""

    try:
        reply, _ = run(
            INSTRUCTIONS,
            _build_input(
                sample,
                candidates,
                manager_name,
                calendar_manager=calendar_manager,
                meet_sample=meet_sample,
            ),
        )
    except Exception as exc:
        logger.warning("Speaker role mapping failed (%s)", type(exc).__name__)
        return None

    mapping = _parse_mapping(reply, candidates)
    if mapping is None:
        # The reply is a few names at most; logging it is what separates "the model
        # could not tell" from "the model answered in a shape we reject".
        logger.warning(
            "Speaker role mapping returned no usable answer: %r", _clip(reply or "", 200)
        )
        return None

    names = [mapping.get(number) for number in order]
    if any(name is None for name in names):
        logger.warning("Speaker role mapping skipped a speaker: %r", _clip(reply, 200))
        return None
    return [name for name in names if name is not None]
