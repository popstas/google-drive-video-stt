"""Read the participants out of the transcript Google Meet writes next to a recording.

Meet leaves a Google Doc beside every recording, and it names the people. That matters
most exactly where the recording's own file name is useless: a call started outside the
calendar is named after the meeting room (``may-doqs-end (2026-09-09 18:53 GMT+2)``),
so there is nothing to extract, and the diarized speakers stay ``Speaker 1`` /
``Speaker 2``.

The document opens with its own title, an ``Attendees`` block, and then the turns::

    exf-wxzm-uzk (2026-09-09 17:42 GMT+2) - Transcript
    Attendees
    Andrei Ermolov, Oksana Ciciarelli
    Transcript
    Andrei Ermolov: ...
    Oksana Ciciarelli: ...
    00:05:00
    ...
    Meeting ended after 00:31:02

Both sources are read, because each is wrong on its own. The attendee list is complete
but unordered and includes things that are not people -- a shared screen joins as
``Oksana Ciciarelli's Presentation``. The turns say who actually spoke, but omit anyone
who stayed silent.

The turns are also evidence of who is who. Meet's speech recognition is often useless
(it heard a Russian call as English), but each turn is tied to the account that spoke,
so who talks at length and who only says "Mhm" is reliable. ``speaker_roles`` hands
them to the model for that.
"""

from __future__ import annotations

import re

# The header is written in the document's own language. Only the two we have seen are
# listed; an unknown one falls back to reading the turns, which need no header at all.
_ATTENDEES_HEADERS = {"attendees", "участники"}
_TRANSCRIPT_HEADERS = {"transcript", "расшифровка", "стенограмма"}

# A shared screen joins the call as a participant of its own.
_PRESENTATION_RE = re.compile(r"[’']s Presentation$", re.IGNORECASE)

# ``Name: what they said``. The label is bounded because a turn's own text may contain
# a colon, and a long left side is a sentence, not a name.
_TURN_RE = re.compile(r"^(?P<name>[^:]{1,60}?):\s+\S")

# ``00:05:00`` markers and the closing line are structure, not speech.
_TIMESTAMP_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")
_CLOSING_RE = re.compile(r"^Meeting ended after\b", re.IGNORECASE)


def _is_person(name: str) -> bool:
    name = name.strip()
    if not name:
        return False
    if _PRESENTATION_RE.search(name):
        return False
    return True


def _lines(text: str) -> list[str]:
    """The document's lines, stripped, with the export's byte-order mark removed.

    Drive's ``text/plain`` export opens with a BOM. It lands on the title line, which
    nothing parses -- but only by luck of the layout, and a header carrying an
    invisible prefix would silently match nothing.
    """
    return [line.strip() for line in (text or "").lstrip("﻿").splitlines()]


def _dedupe(names: list[str]) -> list[str]:
    seen: list[str] = []
    for name in names:
        if name not in seen:
            seen.append(name)
    return seen


def attendees(text: str) -> list[str]:
    """The names listed under the ``Attendees`` header, minus shared screens."""
    lines = _lines(text)
    for index, line in enumerate(lines):
        if line.lower().rstrip(":") not in _ATTENDEES_HEADERS:
            continue
        for candidate in lines[index + 1 :]:
            if not candidate:
                continue
            if candidate.lower().rstrip(":") in _TRANSCRIPT_HEADERS:
                # An empty attendee block; nothing to read.
                return []
            return _dedupe([n.strip() for n in candidate.split(",") if _is_person(n)])
    return []


def _seconds(marker: str) -> int:
    parts = [int(part) for part in marker.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    hours, minutes, seconds = parts
    return hours * 3600 + minutes * 60 + seconds


def turns(text: str) -> list[tuple[int, str, str]]:
    """Every spoken turn as ``(offset in seconds, name, what was said)``.

    Meet does not time each turn; it drops a ``00:05:00`` marker between blocks, so a
    turn's offset is the last marker above it (0 before the first). That is as precise
    as the document gets, and enough to line it up with the diarized transcript.
    """
    found: list[tuple[int, str, str]] = []
    offset = 0
    for line in _lines(text):
        if not line or _CLOSING_RE.match(line):
            continue
        if _TIMESTAMP_RE.match(line):
            offset = _seconds(line)
            continue
        match = _TURN_RE.match(line)
        if not match:
            continue
        name = match.group("name").strip()
        if _is_person(name):
            said = line[match.end("name") :].lstrip(":").strip()
            found.append((offset, name, said))
    return found


def speakers(text: str) -> list[str]:
    """Whoever actually took a turn, in the order Meet heard them first.

    Not the order diarized labels are numbered in: Meet and diarization can disagree
    about who spoke first, so this is a list of people, never a mapping onto speakers.
    """
    return _dedupe([name for _, name, _ in turns(text)])


def participants(text: str, limit: int = 2) -> list[str]:
    """Who was on the call, speakers first.

    Speakers lead so that ``limit`` keeps the people who talked. Silent attendees follow
    rather than being dropped: they are still real people, and a caller asking for more
    names than there are speakers should get them.

    Only names the attendee list agrees with are kept when there is one. A turn label
    is whatever text sat before a colon, so without that cross-check a stray line would
    become a participant; when the header is missing or in an unexpected language the
    turns stand alone, which is still better than nothing.
    """
    listed = attendees(text)
    spoke = speakers(text)
    if listed:
        ordered = [name for name in spoke if name in listed]
        ordered += [name for name in listed if name not in ordered]
    else:
        ordered = spoke
    return ordered[:limit] if limit else ordered
