"""Deciding which diarized speaker is which person.

The bug this guards against: names came from the recording's file name and were bound
to speakers by whoever talked first. When the client opened the call, the manager's
turns were labelled with the client's name and every summary downstream inherited the
swap -- the model then called the manager the client, because that is what it was shown.
"""

from __future__ import annotations

from src import speaker_roles

TRANSCRIPT = "\n".join(
    [
        "[00:00:01] Speaker 2: Здравствуйте, я по поводу заявки.",
        "[00:00:05] Speaker 1: Добрый день, меня зовут Анжелика, я из ExpertizeMe.",
        "[00:00:09] Speaker 2: Хочу узнать про публикацию.",
        "[00:00:12] Speaker 1: Расскажу подробно.",
    ]
)


def test_resolve_returns_names_in_first_appearance_order():
    """map_speakers assigns positionally, so the returned order IS the binding."""
    calls = []

    def run(instructions, input_text):
        calls.append((instructions, input_text))
        return '{"1": "Анжелика Мункуева", "2": "Mels"}', {}

    names = speaker_roles.resolve(
        TRANSCRIPT,
        candidates=["Анжелика Мункуева", "Mels"],
        manager_name="Анжелика Мункуева",
        run=run,
    )

    # Speaker 2 opens the call, so the client's name comes first.
    assert names == ["Mels", "Анжелика Мункуева"]
    assert len(calls) == 1


def test_resolve_keeps_the_manager_on_the_speaker_the_model_picked():
    def run(instructions, input_text):
        return '{"2": "Анжелика Мункуева", "1": "Mels"}', {}

    names = speaker_roles.resolve(
        TRANSCRIPT,
        candidates=["Анжелика Мункуева", "Mels"],
        manager_name="Анжелика Мункуева",
        run=run,
    )

    assert names == ["Анжелика Мункуева", "Mels"]


def test_the_prompt_carries_the_candidates_and_who_the_manager_is():
    seen = {}

    def run(instructions, input_text):
        seen["instructions"] = instructions
        seen["input"] = input_text
        return '{"1": "Mels", "2": "Анжелика Мункуева"}', {}

    speaker_roles.resolve(
        TRANSCRIPT,
        candidates=["Анжелика Мункуева", "Mels"],
        manager_name="Анжелика Мункуева",
        run=run,
    )

    assert "Анжелика Мункуева" in seen["input"]
    assert "Mels" in seen["input"]
    assert "Speaker 1" in seen["input"]
    assert "Speaker 2" in seen["input"]


def _stamp(seconds):
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _sent_input(transcript, **kwargs):
    seen = {}

    def run(instructions, input_text):
        seen["input"] = input_text
        return '{"1": "Mels", "2": "Анжелика Мункуева"}', {}

    speaker_roles.resolve(
        transcript,
        candidates=["Анжелика Мункуева", "Mels"],
        manager_name=kwargs.pop("manager_name", "Анжелика Мункуева"),
        run=run,
        **kwargs,
    )
    return seen["input"]


def test_the_window_starts_at_the_first_speech_not_at_zero():
    """A recording can run silent for minutes before anyone joins; counting ten minutes
    from zero would leave four of them, most of it hellos."""
    transcript = "\n".join(
        f"[{_stamp(370 + 30 * i)}] Speaker {i % 2 + 1}: реплика {i}" for i in range(40)
    )

    sent = _sent_input(transcript)

    # 370 s + 600 s = 970 s: turn 19 starts at 940 s, turn 20 at 970 s.
    assert "реплика 0" in sent
    assert "реплика 19" in sent
    assert "реплика 20" not in sent


def test_the_opening_minutes_are_sent_whole_not_only_the_first_lines():
    """Thirty diarized lines were a minute of "can you hear me"; the introduction that
    tells the manager apart came after them."""
    transcript = "\n".join(
        f"[{_stamp(5 * i)}] Speaker {i % 2 + 1}: реплика {i}" for i in range(100)
    )

    sent = _sent_input(transcript)

    assert "реплика 30" in sent
    assert "реплика 99" in sent


def test_one_speakers_consecutive_lines_are_sent_as_one_turn():
    """Diarization starts a line on every voice change, and merged turns are what show
    who talks at length."""
    transcript = "\n".join(
        [
            "[00:00:01] Speaker 1: Добрый день.",
            "[00:00:02] Speaker 1: Расскажу о компании.",
            "[00:00:09] Speaker 2: Угу.",
        ]
    )

    sent = _sent_input(transcript)

    assert "[00:00:01] Speaker 1: Добрый день. Расскажу о компании." in sent
    assert sent.count("Speaker 1:") == 1


def test_the_sample_is_capped_and_a_monologue_cannot_crowd_out_the_other_speaker():
    monologue = "слово " * 2000
    transcript = "\n".join(
        [
            f"[00:00:01] Speaker 1: {monologue}",
            "[00:01:00] Speaker 2: А сколько это стоит?",
        ]
        + [f"[{_stamp(70 + i)}] Speaker {i % 2 + 1}: {'текст ' * 60}" for i in range(400)]
    )

    sample, _, _ = speaker_roles._opening_turns(transcript)

    assert len(sample) <= speaker_roles.MAX_TRANSCRIPT_CHARS
    assert "А сколько это стоит?" in sample


def test_a_transcript_without_times_is_still_sampled_up_to_the_cap():
    transcript = "\n".join(f"Speaker {i % 2 + 1}: реплика {i}" for i in range(3000))

    sample, _, _ = speaker_roles._opening_turns(transcript)

    assert sample.startswith("Speaker 1: реплика 0")
    assert len(sample) <= speaker_roles.MAX_TRANSCRIPT_CHARS


_MEET_DOC = """call - Transcript
Attendees
Анжелика Мункуева, Анжелика Мункуева's Presentation, Mels
Transcript
Mels: hello early
00:05:00
Анжелика Мункуева: company expertise
Mels: Mhm.
00:10:00
Mels: in the window
00:15:00
Mels: still overlapping
00:20:00
Анжелика Мункуева: too late
Meeting ended after 00:21:00
"""


def test_meets_turns_for_the_same_minutes_are_sent_with_the_transcript():
    """Meet's words are often wrong, but who spoke is tied to the account: the one
    source that knows which person each voice belongs to."""
    transcript = "\n".join(
        f"[{_stamp(370 + 30 * i)}] Speaker {i % 2 + 1}: реплика {i}" for i in range(40)
    )

    sent = _sent_input(transcript, meet_text=_MEET_DOC)

    # The window is 370-970 s: the blocks starting at 300, 600 and 900 s overlap it.
    assert "[00:05:00] Анжелика Мункуева: company expertise" in sent
    assert "[00:10:00] Mels: in the window" in sent
    assert "[00:15:00] Mels: still overlapping" in sent
    assert "hello early" not in sent
    assert "too late" not in sent
    assert "Presentation" not in sent


def test_without_meets_transcript_no_meet_section_is_sent():
    assert "Google Meet" not in _sent_input(TRANSCRIPT)


def test_the_calendar_titles_marked_manager_is_sent():
    sent = _sent_input(TRANSCRIPT, calendar_manager="Angelica Munkueva")

    assert "Angelica Munkueva" in sent


def test_an_unknown_folder_owner_is_said_so():
    sent = _sent_input(TRANSCRIPT, manager_name="")

    assert "folder owner is not known" in sent


def test_an_honest_cannot_tell_is_none_and_the_reply_is_logged(caplog):
    """Guessing is what swapped the labels; the log line is what tells "could not
    tell" apart from "answered in a shape we reject"."""

    def run(instructions, input_text):
        return "{}", {}

    with caplog.at_level("WARNING"):
        names = speaker_roles.resolve(
            TRANSCRIPT,
            candidates=["Анжелика Мункуева", "Mels"],
            manager_name="Анжелика Мункуева",
            run=run,
        )

    assert names is None
    assert "'{}'" in caplog.text


def test_a_reply_naming_someone_who_was_not_a_candidate_is_rejected():
    """An invented name would silently relabel the whole transcript."""

    def run(instructions, input_text):
        return '{"1": "Иван Иванов", "2": "Mels"}', {}

    assert (
        speaker_roles.resolve(
            TRANSCRIPT,
            candidates=["Анжелика Мункуева", "Mels"],
            manager_name="Анжелика Мункуева",
            run=run,
        )
        is None
    )


def test_a_reply_using_one_name_twice_is_rejected():
    def run(instructions, input_text):
        return '{"1": "Mels", "2": "Mels"}', {}

    assert (
        speaker_roles.resolve(
            TRANSCRIPT,
            candidates=["Анжелика Мункуева", "Mels"],
            manager_name="Анжелика Мункуева",
            run=run,
        )
        is None
    )


def test_unparseable_reply_falls_back_to_none():
    """None means "keep today's positional behaviour"; it must never raise."""

    def run(instructions, input_text):
        return "не знаю", {}

    assert (
        speaker_roles.resolve(
            TRANSCRIPT,
            candidates=["Анжелика Мункуева", "Mels"],
            manager_name="Анжелика Мункуева",
            run=run,
        )
        is None
    )


def test_a_failing_call_never_raises():
    """A recording that already cost money to transcribe must not die on this step."""

    def run(instructions, input_text):
        raise RuntimeError("openai down")

    assert (
        speaker_roles.resolve(
            TRANSCRIPT,
            candidates=["Анжелика Мункуева", "Mels"],
            manager_name="Анжелика Мункуева",
            run=run,
        )
        is None
    )


def test_json_wrapped_in_prose_or_a_fence_is_still_read():
    def run(instructions, input_text):
        return 'Вот ответ:\n```json\n{"1": "Mels", "2": "Анжелика Мункуева"}\n```', {}

    names = speaker_roles.resolve(
        TRANSCRIPT,
        candidates=["Анжелика Мункуева", "Mels"],
        manager_name="Анжелика Мункуева",
        run=run,
    )

    assert names == ["Анжелика Мункуева", "Mels"]


def test_fewer_than_two_candidates_skips_the_call():
    """With nothing to disambiguate there is no mapping to get wrong."""
    calls = []

    def run(instructions, input_text):
        calls.append(1)
        return "{}", {}

    assert (
        speaker_roles.resolve(
            TRANSCRIPT,
            candidates=["Анжелика Мункуева"],
            manager_name="Анжелика Мункуева",
            run=run,
        )
        is None
    )
    assert calls == []


def test_a_transcript_without_speaker_labels_skips_the_call():
    calls = []

    def run(instructions, input_text):
        calls.append(1)
        return "{}", {}

    assert (
        speaker_roles.resolve(
            "просто текст без меток",
            candidates=["Анжелика Мункуева", "Mels"],
            manager_name="Анжелика Мункуева",
            run=run,
        )
        is None
    )
    assert calls == []
