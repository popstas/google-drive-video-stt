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


# --- name extraction: real ExpertizeMe recording names ---------------------

def test_extract_names_strips_duration_prefix_and_parenthetical():
    names = postprocess.extract_interlocutor_names(
        "30-минутная онлайн-встреча Viktoria Tolstikova(ExpertizeMe) и Oleg"
        " - 2026_03_13 19_30 GMT+05_00 – Recording.mp4"
    )
    assert names == ["Viktoria Tolstikova", "Oleg"]


def test_extract_names_strips_duration_prefix_with_double_spaces():
    names = postprocess.extract_interlocutor_names(
        "30-минутная онлайн-встреча Angelica Munkueva(ExpertizeMe) и Mariia "
        " - 2026_07_08 18_59 CEST - Recording.mp4"
    )
    assert names == ["Angelica Munkueva", "Mariia"]


def test_extract_names_english_duration_prefix():
    names = postprocess.extract_interlocutor_names(
        "30-minute online meeting Alice Smith(ExpertizeMe) and Bob - 2026_07_08.mp4"
    )
    assert names == ["Alice Smith", "Bob"]


def test_extract_names_cyrillic_ha_separator_drops_org_token():
    names = postprocess.extract_interlocutor_names(
        "Ольга х ExpertizeMe - 2026_07_08 12_00 GMT+04_00 – Recording.mp4"
    )
    assert names == ["Ольга"]


def test_extract_names_latin_x_separator_drops_org_token():
    names = postprocess.extract_interlocutor_names("Olga x ExpertizeMe - 2026_07_08.mp4")
    assert names == ["Olga"]


def test_extract_names_uppercase_x_is_a_middle_initial_not_a_separator():
    """Splitting on an uppercase "X" truncates the name and strands the real
    conjunction, yielding the bogus interlocutor "and Alice"."""
    names = postprocess.extract_interlocutor_names("Malcolm X and Alice - 2026-01-01.mp4")
    assert names == ["Malcolm X", "Alice"]


def test_extract_names_and_separator_regression_guard():
    names = postprocess.extract_interlocutor_names(
        "Aleksandr Tikhonov and Oksana Ciciarelli - 2026_05_20 16_55 CEST - Recording.mp4"
    )
    assert names == ["Aleksandr Tikhonov", "Oksana Ciciarelli"]


def test_extract_names_rejects_meet_code_stem():
    names = postprocess.extract_interlocutor_names("zkn-jdcd-cxc (2026-06-16 22_09 GMT+4).mp4")
    assert names == []


def test_extract_names_underscore_date_is_trimmed():
    names = postprocess.extract_interlocutor_names("Иван и Пётр 2026_05_28.mp4")
    assert names == ["Иван", "Пётр"]


def test_extract_names_org_only_stem_yields_nothing():
    assert postprocess.extract_interlocutor_names("ExpertizeMe - 2026_07_08.mp4") == []


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


def test_dominant_speaker_outside_canonical_range_maps_correctly():
    # Diarization emits Speaker 1 and Speaker 3 (no Speaker 2) for a 2-party
    # call, and Speaker 3 is the dominant talker. The old canonical branch
    # forced labels 1..expected as real, which would have treated the main
    # speaker (3) as a stray and merged their turns into the wrong name.
    text = (
        "Speaker 1: hi\n"
        "Speaker 3: lots and lots to say here\n"
        "Speaker 1: ok\n"
        "Speaker 3: still going on at length\n"
        "Speaker 1: sure\n"
        "Speaker 3: and even more"
    )
    out = postprocess.map_speakers(text, ["Alice", "Bob"])
    lines = out.split("\n")
    # First appearance order: Speaker 1 -> Alice, Speaker 3 -> Bob.
    assert lines[0] == "Alice: hi"
    assert lines[1] == "Bob: lots and lots to say here"
    assert "Speaker 1" not in out
    assert "Speaker 3" not in out
    # The dominant speaker's turns stay together under one name, not merged away.
    assert "Bob: and even more" in out


def test_dominant_out_of_range_speaker_not_treated_as_stray():
    # Even when canonical labels 1..expected are all present, the most prominent
    # speaker (by turn count) outside that range must remain a real speaker.
    text = (
        "Speaker 1: hello\n"
        "Speaker 2: y\n"
        "Speaker 3: a\n"
        "Speaker 3: b\n"
        "Speaker 3: c\n"
        "Speaker 3: d"
    )
    out = postprocess.map_speakers(text, ["Alice", "Bob"], expected=2)
    # Speaker 3 takes the most turns -> it is a real speaker, not merged.
    assert "Speaker 3" not in out
    # Speaker 1 (first appearance) and Speaker 3 are the two real speakers;
    # Speaker 2 (a single short stray turn) merges into a neighbor.
    assert "Speaker 2" not in out
    assert "Alice: hello" in out
    # Speaker 2's stray "y" merges into its neighbor Speaker 3 (-> Bob).
    assert "Bob: y a b c d" in out


def test_single_name_with_dominant_out_of_range_speaker():
    # A single filename name must not collapse speakers (expected floored at 2),
    # and the dominant out-of-range speaker still maps correctly.
    text = (
        "Speaker 1: hi\n"
        "Speaker 3: a long dominant turn\n"
        "Speaker 1: ok\n"
        "Speaker 3: another dominant turn"
    )
    out = postprocess.map_speakers(text, ["Alice"])
    lines = out.split("\n")
    assert lines[0] == "Alice: hi"
    # Second real speaker keeps a generic label rather than collapsing into Alice.
    assert lines[1] == "Speaker 2: a long dominant turn"
    assert "Speaker 3" not in out


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
