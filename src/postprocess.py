from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)

# A diarized transcript line, optionally prefixed by a "[HH:MM:SS]" offset:
#   [00:01:23] Speaker 2: some text
_SPEAKER_LINE_RE = re.compile(
    r"^(?P<prefix>\[[^\]]*\]\s*)?Speaker\s+(?P<num>\d+)\s*:\s*(?P<text>.*)$"
)

# A date embedded in a meeting / recording file name (2026/05/28, 2026-05-28, 28.05.2026,
# 2026_05_28 — Google Meet writes the underscore form).
_DATE_RE = re.compile(r"\d{4}[-/._]\d{1,2}[-/._]\d{1,2}|\d{1,2}[-/._]\d{1,2}[-/._]\d{2,4}")

# Separators that split a meeting title ("Alice and Bob - Weekly sync").
_TITLE_SEP_RE = re.compile(r"\s+[-–—|]\s+")

# Conjunctions that join participant names ("Alice and Bob", "Alice, Bob", "Alice и Bob",
# "Ольга х ExpertizeMe").
_NAME_SEP_RE = re.compile(
    r"\s*,\s*|\s+&\s+|\s+and\s+|\s+и\s+|\s+х\s+|\s+x\s+", re.IGNORECASE
)

# A calendar-generated duration prefix ("30-минутная онлайн-встреча Alice ...",
# "30-minute online meeting Alice ...").
_DURATION_PREFIX_RE = re.compile(
    r"^\s*\d+\s*[-–—]?\s*"
    r"(?:минутн\w*\s+(?:онлайн-)?(?:встреча|созвон|звонок)\w*"
    r"|minute\s+(?:online\s+)?(?:meeting|call))"
    r"\s+",
    re.IGNORECASE,
)

# A parenthetical qualifier attached to a name ("Alice Smith(ExpertizeMe)").
_PARENTHETICAL_RE = re.compile(r"\([^()]*\)")

# Organization names that are never a person.
_ORG_TOKENS = {"expertizeme"}

# A Google Meet room code ("zkn-jdcd-cxc") — lowercase, hyphenated, never a person.
_MEET_CODE_RE = re.compile(r"^[a-z]{2,5}(?:-[a-z]{2,5}){2}$")


def extract_interlocutor_names(file_name: str, limit: int = 2) -> list[str]:
    """Best-effort extraction of interlocutor names from a recording file name.

    Recording names commonly look like ``Alice and Bob - 2026/05/28 ...``; the
    participant list precedes any date or topic separator. This drops any calendar
    duration prefix and parenthetical qualifiers, trims the date, keeps the first
    title segment, then splits it on common name conjunctions. Organization tokens
    and Meet room codes are discarded. Returns at most ``limit`` distinct names
    (possibly fewer, or empty).
    """
    stem = os.path.splitext(file_name)[0]
    stem = _PARENTHETICAL_RE.sub(" ", stem)
    stem = _DURATION_PREFIX_RE.sub("", stem)

    date_match = _DATE_RE.search(stem)
    if date_match:
        stem = stem[: date_match.start()]

    head = _TITLE_SEP_RE.split(stem)[0]
    names: list[str] = []
    for part in _NAME_SEP_RE.split(head):
        part = part.strip()
        if not part or part.lower() in _ORG_TOKENS or _MEET_CODE_RE.match(part):
            continue
        if part not in names:
            names.append(part)
        if len(names) >= limit:
            break
    return names


def clean_transcript(text: str) -> str:
    """Normalize whitespace: unify newlines, drop trailing spaces, collapse blank runs."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = []
    prev_blank = False
    for raw_line in normalized.split("\n"):
        line = raw_line.rstrip()
        if not line.strip():
            if out and not prev_blank:
                out.append("")
            prev_blank = True
        else:
            out.append(line)
            prev_blank = False
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out)


def _parse_entries(text: str) -> list[dict]:
    entries: list[dict] = []
    for raw in text.split("\n"):
        match = _SPEAKER_LINE_RE.match(raw)
        if match is None:
            entries.append({"num": None, "raw": raw})
            continue
        entries.append({
            "num": int(match.group("num")),
            "prefix": match.group("prefix") or "",
            "text": match.group("text").strip(),
            "raw": raw,
        })
    return entries


def _merge_targets(
    entries: list[dict],
    real: list[int],
    extras: list[int],
    word_counts: dict[int, int],
) -> dict[int, int]:
    """Decide which real speaker each extra (likely-spurious) speaker merges into.

    A diarization stray usually sits between turns of the speaker it was split from,
    so each extra speaker is merged into the real speaker that is most often its
    immediate neighbor. Falls back to the most talkative real speaker.
    """
    if not extras:
        return {}

    real_set = set(real)
    sequence = [e["num"] for e in entries if e["num"] is not None]
    dominant = max(real, key=lambda n: word_counts.get(n, 0))

    targets: dict[int, int] = {}
    for extra in extras:
        neighbor_counts: dict[int, int] = {}
        for idx, num in enumerate(sequence):
            if num != extra:
                continue
            for nbr_idx in (idx - 1, idx + 1):
                if 0 <= nbr_idx < len(sequence):
                    nbr = sequence[nbr_idx]
                    if nbr in real_set:
                        neighbor_counts[nbr] = neighbor_counts.get(nbr, 0) + 1
        if neighbor_counts:
            targets[extra] = max(
                neighbor_counts,
                key=lambda n: (neighbor_counts[n], word_counts.get(n, 0)),
            )
        else:
            targets[extra] = dominant
    return targets


def _rebuild(entries: list[dict], label_of: dict[int, str]) -> str:
    """Re-emit the transcript with final labels, merging consecutive same-speaker turns."""
    out: list[str] = []
    cur_label: str | None = None
    cur_prefix = ""
    cur_parts: list[str] = []

    def flush() -> None:
        if cur_label is None:
            return
        body = " ".join(p for p in cur_parts if p).strip()
        out.append(f"{cur_prefix}{cur_label}: {body}".rstrip())

    for entry in entries:
        if entry["num"] is None:
            flush()
            cur_label = None
            cur_parts = []
            out.append(entry["raw"])
            continue
        label = label_of[entry["num"]]
        if label == cur_label:
            if entry["text"]:
                cur_parts.append(entry["text"])
        else:
            flush()
            cur_label = label
            cur_prefix = entry["prefix"]
            cur_parts = [entry["text"]] if entry["text"] else []
    flush()
    return "\n".join(out)


def map_speakers(text: str, names: list[str], *, expected: int | None = None) -> str:
    """Map ``Speaker N`` labels to interlocutor names and merge extra speakers.

    The ``expected`` most talkative speakers are treated as the real interlocutors and
    receive ``names`` in order of first appearance; any remaining speakers are merged
    into a real speaker (see ``_merge_targets``). Text without speaker labels is
    returned unchanged.
    """
    entries = _parse_entries(text)
    sequence = [e["num"] for e in entries if e["num"] is not None]
    if not sequence:
        return text

    distinct: list[int] = []
    for num in sequence:
        if num not in distinct:
            distinct.append(num)

    if expected is None:
        # Default to the common two-party case. A file name that yields a single
        # name (e.g. "Alice - weekly.mp4" -> ["Alice"]) must not collapse a
        # multi-speaker transcript into one speaker; floor the count at 2.
        expected = max(2, len(names))
    expected = max(1, expected)

    word_counts: dict[int, int] = {}
    for entry in entries:
        if entry["num"] is not None:
            word_counts[entry["num"]] = (
                word_counts.get(entry["num"], 0) + len(entry["text"].split())
            )

    first_seen = {num: idx for idx, num in enumerate(distinct)}
    real_count = min(expected, len(distinct))
    # Pick the real interlocutors by actual prominence rather than by trusting
    # the numeric labels diarization happens to emit. Diarization may skip or
    # reorder numbers (e.g. emit Speaker 3 for the dominant talker in a
    # two-party call); keying off labels 1..expected would then treat the main
    # speaker as a stray and merge their turns into the wrong name.
    #
    # Turn count is the primary signal because a genuine interlocutor takes
    # turns throughout the conversation, whereas a diarization stray is usually
    # an isolated turn (high word count but only one turn). First appearance is
    # the final tie-breaker so the result is deterministic and stable, and so
    # the earliest-speaking participants win ties (preserving canonical
    # ordering when labels are well-behaved).
    turn_counts: dict[int, int] = {}
    for num in sequence:
        turn_counts[num] = turn_counts.get(num, 0) + 1
    real = sorted(
        distinct,
        key=lambda n: (-turn_counts.get(n, 0), first_seen[n]),
    )[:real_count]
    extras = [num for num in distinct if num not in real]

    merge_targets = _merge_targets(entries, real, extras, word_counts)

    real_in_order = sorted(real, key=lambda n: first_seen[n])
    real_label: dict[int, str] = {}
    for idx, num in enumerate(real_in_order):
        real_label[num] = names[idx] if idx < len(names) else f"Speaker {idx + 1}"

    label_of: dict[int, str] = {}
    for num in distinct:
        target = merge_targets.get(num, num)
        label_of[num] = real_label.get(target, f"Speaker {target}")

    if extras:
        logger.info(
            "Post-processing: merged extra speaker(s) %s into real speakers",
            ", ".join(str(e) for e in extras),
        )

    return _rebuild(entries, label_of)


def postprocess_transcript(
    text: str,
    file_name: str,
    *,
    speaker_names: list[str] | None = None,
) -> str:
    """Clean a raw STT transcript and map diarized speakers to interlocutor names."""
    cleaned = clean_transcript(text)
    names = speaker_names if speaker_names is not None else extract_interlocutor_names(file_name)
    return map_speakers(cleaned, names)
