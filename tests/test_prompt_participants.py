"""Putting the people who were in a call into the prompt that asked for them.

Unlike ``{{entities}}``, which is configuration and is rendered once when the config
loads, these are *this call*. Every test here exists because getting that wrong is
silent: a prompt would carry one call's participants into every later call.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src import preset_pipeline, presets

PROMPT = "Summarise the call.\nPeople: {{participants}}\nWho spoke: {{participants-speakers}}\nBe brief."


def test_both_placeholders_are_filled():
    rendered = presets.render_participants(PROMPT, ["Ann", "Bob"], ["Ann"])

    assert "People: Ann, Bob" in rendered
    assert "Who spoke: Ann" in rendered
    assert "{{" not in rendered


def test_nothing_to_say_takes_the_whole_line_with_it():
    """"People:" followed by emptiness tells the model there were none."""
    rendered = presets.render_participants(PROMPT, [], None)

    assert "People:" not in rendered
    assert "Who spoke:" not in rendered
    assert "Summarise the call." in rendered
    assert "Be brief." in rendered


def test_unknown_speakers_do_not_take_the_participants_with_them():
    rendered = presets.render_participants(PROMPT, ["Ann"], None)

    assert "People: Ann" in rendered
    assert "Who spoke" not in rendered


def test_a_nameless_participant_is_not_rendered_as_a_gap():
    rendered = presets.render_participants(PROMPT, ["Ann", ""], ["Ann"])

    assert "People: Ann\n" in rendered


def test_a_prompt_with_no_placeholder_is_untouched():
    text = "Summarise the call.\nBe brief."

    assert presets.render_participants(text, ["Ann"], ["Ann"]) == text


def test_a_prompt_says_whether_it_wants_people_at_all():
    assert presets.wants_participants(PROMPT) is True
    assert presets.wants_speakers(PROMPT) is True
    assert presets.wants_participants("plain") is False
    assert presets.wants_speakers("People: {{participants}}") is False


def test_the_pipeline_renders_the_prompt_for_this_call(mocker):
    """The rendering must happen per call, which is what this asserts."""
    pipeline = MagicMock()
    pipeline.run.return_value = ("out", {})
    mocker.patch("src.preset_pipeline.OpenAIPipeline", return_value=pipeline)
    preset = MagicMock(
        depends_on=(),
        instructions=PROMPT,
        batch=False,
        batch_wait=None,
        model="",
        reasoning_effort="",
        name="keypoints",
    )
    config = MagicMock(openai_batch=False, openai_batch_wait=True, openai_api_key="k")

    preset_pipeline._run_one(
        preset,
        transcript="t",
        file_name="call.mp4",
        config=config,
        speaker_names=None,
        manager_name="",
        dep_results={},
        participants=["Ann", "Bob"],
        speakers=["Ann"],
    )

    sent = pipeline.run.call_args.args[0]
    assert "People: Ann, Bob" in sent
    assert "Who spoke: Ann" in sent


def test_without_people_the_pipeline_sends_a_prompt_that_does_not_pretend(mocker):
    pipeline = MagicMock()
    pipeline.run.return_value = ("out", {})
    mocker.patch("src.preset_pipeline.OpenAIPipeline", return_value=pipeline)
    preset = MagicMock(
        depends_on=(),
        instructions=PROMPT,
        batch=False,
        batch_wait=None,
        model="",
        reasoning_effort="",
        name="keypoints",
    )
    config = MagicMock(openai_batch=False, openai_batch_wait=True, openai_api_key="k")

    preset_pipeline._run_one(
        preset,
        transcript="t",
        file_name="call.mp4",
        config=config,
        speaker_names=None,
        manager_name="",
        dep_results={},
    )

    sent = pipeline.run.call_args.args[0]
    assert "People:" not in sent
    assert "{{" not in sent
