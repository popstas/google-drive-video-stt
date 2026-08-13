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


def test_only_the_first_turns_are_sent():
    """The opening exchange is where people introduce themselves; the rest is cost."""
    long_transcript = "\n".join(
        f"[00:0{i % 10}:00] Speaker {i % 2 + 1}: реплика {i}" for i in range(200)
    )
    seen = {}

    def run(instructions, input_text):
        seen["input"] = input_text
        return '{"1": "Mels", "2": "Анжелика Мункуева"}', {}

    speaker_roles.resolve(
        long_transcript,
        candidates=["Анжелика Мункуева", "Mels"],
        manager_name="Анжелика Мункуева",
        run=run,
        turns=30,
    )

    assert seen["input"].count("Speaker ") == 30
    assert "реплика 5" in seen["input"]
    assert "реплика 150" not in seen["input"]


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
