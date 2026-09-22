from __future__ import annotations

from src import meet_transcript

# The shape of a real export, with the speech replaced. Structure is what the parser
# reads; the calls themselves stay out of the repository.
CALENDAR_CALL = """30-минутная онлайн-встреча Oksana Ciciarelli(ExpertizeMe) и Viktoriia  - 2026/09/09 16:56 CEST - Transcript
Attendees
Oksana Ciciarelli, Viktoriia Piesova
Transcript
00:05:00
Viktoriia Piesova: Mhm.
Oksana Ciciarelli: one
Viktoriia Piesova: two
Oksana Ciciarelli: three
Meeting ended after 00:28:14
This editable transcript was computer generated and might contain errors.
"""

ROOM_CODE_CALL = """may-doqs-end (2026-09-09 18:53 GMT+2) - Transcript
Attendees
Oksana Ciciarelli, Oksana Ciciarelli's Presentation, Roman Starodubtsev
Transcript
Oksana Ciciarelli: one
Roman Starodubtsev: two
00:05:00
Roman Starodubtsev: three
Meeting ended after 01:00:19
This editable transcript was computer generated and might contain errors.
"""


def test_attendees_are_read_from_the_header():
    assert meet_transcript.attendees(CALENDAR_CALL) == [
        "Oksana Ciciarelli",
        "Viktoriia Piesova",
    ]


def test_a_shared_screen_is_not_a_participant():
    """A shared screen joins the call under its own name. Treating it as a person
    would spend one of the two speaker slots on it."""
    assert meet_transcript.attendees(ROOM_CODE_CALL) == [
        "Oksana Ciciarelli",
        "Roman Starodubtsev",
    ]


def test_speakers_come_back_in_the_order_meet_heard_them_first():
    """A list of people, not a mapping: Meet and diarization can disagree about who
    spoke first."""
    assert meet_transcript.speakers(CALENDAR_CALL) == [
        "Viktoriia Piesova",
        "Oksana Ciciarelli",
    ]


def test_timestamps_and_the_closing_line_are_not_speakers():
    assert "Meeting ended after 01" not in meet_transcript.speakers(ROOM_CODE_CALL)
    assert meet_transcript.speakers(ROOM_CODE_CALL) == [
        "Oksana Ciciarelli",
        "Roman Starodubtsev",
    ]


def test_participants_lead_with_whoever_spoke_first():
    assert meet_transcript.participants(CALENDAR_CALL) == [
        "Viktoriia Piesova",
        "Oksana Ciciarelli",
    ]


def test_a_silent_attendee_is_kept_but_comes_last():
    text = """Call - Transcript
Attendees
Alice, Bob, Carol
Transcript
Bob: one
Alice: two
"""
    assert meet_transcript.participants(text, limit=3) == ["Bob", "Alice", "Carol"]


def test_a_turn_label_the_attendee_list_does_not_know_is_dropped():
    """A turn label is only "whatever sat before a colon", so it is cross-checked."""
    text = """Call - Transcript
Attendees
Alice, Bob
Transcript
Note to self: remember this
Bob: one
Alice: two
"""
    assert meet_transcript.participants(text) == ["Bob", "Alice"]


def test_turns_alone_are_used_when_the_header_is_missing_or_unfamiliar():
    """Meet writes the header in the document's own language. An unknown one must
    degrade to reading the turns, not to returning nothing."""
    text = """Call - Transcript
Teilnehmer
Alice, Bob
Transcript
Bob: one
Alice: two
"""
    assert meet_transcript.participants(text) == ["Bob", "Alice"]


def test_a_russian_header_is_understood():
    text = """Звонок - Transcript
Участники
Алиса, Борис
Расшифровка
Борис: раз
Алиса: два
"""
    assert meet_transcript.participants(text) == ["Борис", "Алиса"]


def test_an_empty_attendee_block_does_not_swallow_the_transcript_header():
    text = """Call - Transcript
Attendees
Transcript
Bob: one
"""
    assert meet_transcript.attendees(text) == []
    assert meet_transcript.participants(text) == ["Bob"]


def test_the_limit_is_honoured():
    text = """Call - Transcript
Attendees
Alice, Bob, Carol
Transcript
Alice: one
Bob: two
Carol: three
"""
    assert meet_transcript.participants(text, limit=2) == ["Alice", "Bob"]


def test_empty_input_is_not_an_error():
    assert meet_transcript.participants("") == []
    assert meet_transcript.attendees("") == []
    assert meet_transcript.speakers("") == []


def test_a_repeated_speaker_is_listed_once():
    text = """Call - Transcript
Attendees
Alice, Bob
Transcript
Alice: one
Alice: two
Bob: three
"""
    assert meet_transcript.speakers(text) == ["Alice", "Bob"]


def test_turns_carry_the_start_of_their_block():
    """Meet only marks time between blocks, so that mark is each turn's time -- what
    lines its turns up with the diarized transcript."""
    assert meet_transcript.turns(ROOM_CODE_CALL) == [
        (0, "Oksana Ciciarelli", "one"),
        (0, "Roman Starodubtsev", "two"),
        (300, "Roman Starodubtsev", "three"),
    ]


def test_a_turn_keeps_a_colon_inside_what_was_said():
    text = "Transcript\n01:02:03\nAlice: the price: ten\n"

    assert meet_transcript.turns(text) == [(3723, "Alice", "the price: ten")]


def test_a_shared_screens_turns_are_not_turns():
    text = "Transcript\nAlice's Presentation: slide one\nAlice: hello\n"

    assert meet_transcript.turns(text) == [(0, "Alice", "hello")]


def test_the_export_byte_order_mark_is_ignored():
    """Drive's text/plain export opens with a BOM. It happens to land on the title
    line today, but a header carrying an invisible prefix would match nothing."""
    text = "﻿Attendees\nAlice, Bob\nTranscript\nBob: one\nAlice: two\n"

    assert meet_transcript.attendees(text) == ["Alice", "Bob"]
    assert meet_transcript.participants(text) == ["Bob", "Alice"]
