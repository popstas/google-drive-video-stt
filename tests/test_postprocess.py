from __future__ import annotations

from src import postprocess


# --- name extraction -------------------------------------------------------

def test_extract_names_and_separator():
    names = postprocess.extract_interlocutor_names(
        "Alice and Bob - 2026/05/28 17:27 GMT+04:00.mp4"
    )
    assert names == ["Alice", "Bob"]


def test_extract_names_comma_separator():
    names = postprocess.extract_interlocutor_names("Alice, Bob - Weekly sync.mp4")
    assert names == ["Alice", "Bob"]


def test_extract_names_russian_conjunction():
    names = postprocess.extract_interlocutor_names("Иван и Пётр 2026-05-28.mp4")
    assert names == ["Иван", "Пётр"]


def test_extract_names_ampersand():
    names = postprocess.extract_interlocutor_names("Alice & Bob.mp4")
    assert names == ["Alice", "Bob"]


def test_extract_names_limit_two():
    names = postprocess.extract_interlocutor_names("Alice, Bob, Carol.mp4")
    assert names == ["Alice", "Bob"]


def test_extract_names_dedupes():
    names = postprocess.extract_interlocutor_names("Alice and Alice.mp4")
    assert names == ["Alice"]


def test_extract_names_preserves_slash_before_date_cut():
    # The "/" in the date must not be treated as a path separator.
    names = postprocess.extract_interlocutor_names(
        "Call 2026/05/28 17:27 GMT+04:00 – Recording.mp4"
    )
    assert names and names[0].startswith("Call")


# --- cleaning --------------------------------------------------------------

def test_clean_collapses_blank_lines_and_trailing_space():
    raw = "line one   \r\n\n\n\nline two\n\n"
    assert postprocess.clean_transcript(raw) == "line one\n\nline two"


def test_clean_strips_leading_blank_lines():
    assert postprocess.clean_transcript("\n\nhello") == "hello"


# --- speaker mapping -------------------------------------------------------

def test_map_speakers_assigns_names_by_first_appearance():
    text = (
        "[00:00:00] Speaker 1: hi there\n"
        "[00:00:05] Speaker 2: hello back\n"
        "[00:00:10] Speaker 1: how are you"
    )
    out = postprocess.map_speakers(text, ["Alice", "Bob"])
    lines = out.split("\n")
    assert lines[0] == "[00:00:00] Alice: hi there"
    assert lines[1] == "[00:00:05] Bob: hello back"
    assert lines[2] == "[00:00:10] Alice: how are you"


def test_map_speakers_merges_consecutive_same_speaker():
    text = "Speaker 1: one\nSpeaker 1: two\nSpeaker 2: three"
    out = postprocess.map_speakers(text, ["Alice", "Bob"])
    assert out == "Alice: one two\nBob: three"


def test_map_speakers_no_labels_returns_unchanged():
    text = "just some plain transcription without speakers"
    assert postprocess.map_speakers(text, ["Alice", "Bob"]) == text


def test_map_speakers_without_names_uses_speaker_labels():
    text = "Speaker 1: a\nSpeaker 2: b"
    out = postprocess.map_speakers(text, [])
    assert out == "Speaker 1: a\nSpeaker 2: b"


def test_single_name_does_not_collapse_speakers():
    # One extracted name must not flatten a two-party transcript into one speaker.
    text = "Speaker 1: hi\nSpeaker 2: hello\nSpeaker 1: bye"
    out = postprocess.map_speakers(text, ["Alice"])
    lines = out.split("\n")
    assert lines[0] == "Alice: hi"
    assert lines[1] == "Speaker 2: hello"
    assert lines[2] == "Alice: bye"


def test_extra_speaker_merged_into_frequent_neighbor():
    # Speaker 3 is a single stray turn nested between Speaker 2's turns -> merge into 2.
    text = (
        "Speaker 1: aaa aaa aaa aaa\n"
        "Speaker 2: bbb bbb bbb\n"
        "Speaker 3: x\n"
        "Speaker 2: bbb bbb\n"
        "Speaker 1: aaa aaa"
    )
    out = postprocess.map_speakers(text, ["Alice", "Bob"])
    assert "Speaker 3" not in out
    assert "Carol" not in out
    # The stray turn now belongs to Bob (Speaker 2), merged with surrounding turns.
    assert "Bob: bbb bbb bbb x bbb bbb" in out


def test_extra_speaker_does_not_replace_first_two_speakers_when_names_known():
    text = (
        "Speaker 1: hello\n"
        "Speaker 2: hi\n"
        "Speaker 3: this is a longer stray turn\n"
        "Speaker 2: back"
    )

    out = postprocess.map_speakers(text, ["Alice", "Bob"])

    assert out == "Alice: hello\nBob: hi this is a longer stray turn back"


def test_extra_speaker_falls_back_to_dominant_when_no_real_neighbor():
    # Speaker 3 only ever neighbors itself -> merged into the most talkative real speaker.
    text = (
        "Speaker 3: x\n"
        "Speaker 3: y\n"
        "Speaker 1: aaa aaa aaa aaa aaa\n"
        "Speaker 2: bbb"
    )
    out = postprocess.map_speakers(text, ["Alice", "Bob"])
    assert "Speaker 3" not in out
    # Alice (Speaker 1) is dominant by word count; the stray turns attach to her.
    assert out.startswith("Alice: x y")


# --- orchestration ---------------------------------------------------------

def test_postprocess_transcript_end_to_end():
    text = (
        "[00:00:00] Speaker 1: hi   \n"
        "\n\n"
        "[00:00:05] Speaker 2: yo\n"
        "[00:00:09] Speaker 3: stray\n"
        "[00:00:12] Speaker 2: bye"
    )
    out = postprocess.postprocess_transcript(text, "Alice and Bob - 2026/05/28.mp4")
    assert "Speaker 3" not in out
    assert "Alice:" in out
    assert "Bob:" in out
