from __future__ import annotations

import logging

import pytest

from src import relabel_transcript


def test_default_map_applied():
    src = "[00:00:01] Speaker 1: Привет всем.\n[00:00:05] Speaker 2: Здравствуйте.\n"
    cfg = {"default": {"Speaker 1": "Андрей", "Speaker 2": "Яна"}}

    out = relabel_transcript.relabel(src, cfg)

    assert "**Андрей** | 00:01" in out
    assert "**Яна** | 00:05" in out
    assert "Привет всем." in out
    assert "Здравствуйте." in out


def test_exceptions_override_by_verbatim_text():
    src = (
        "[00:00:01] Speaker 1: Алло, Андрей, привет.\n"
        "[00:00:05] Speaker 1: Как дела?\n"
    )
    cfg = {
        "default": {"Speaker 1": "Андрей"},
        "exceptions": [{"text": "Алло, Андрей, привет.", "name": "Яна"}],
    }

    out = relabel_transcript.relabel(src, cfg)

    # The exception line is attributed to Яна; the other stays Андрей.
    assert "**Яна** | 00:01\nАлло, Андрей, привет." in out
    assert "**Андрей** | 00:05\nКак дела?" in out


def test_consecutive_same_name_blocks_merged():
    src = (
        "[00:00:01] Speaker 1: Первая.\n"
        "[00:00:03] Speaker 1: Вторая.\n"
        "[00:00:06] Speaker 2: Третья.\n"
    )
    cfg = {"default": {"Speaker 1": "Андрей", "Speaker 2": "Яна"}}

    out = relabel_transcript.relabel(src, cfg)

    # Two consecutive Speaker 1 turns merge into one block keeping the first ts.
    assert "**Андрей** | 00:01\nПервая. Вторая.\n" in out
    assert out.count("**Андрей**") == 1
    assert "**Яна** | 00:06\nТретья." in out


def test_unmapped_labels_reported(caplog):
    src = "[00:00:01] Speaker 9: Кто это?\n"
    cfg = {"default": {"Speaker 1": "Андрей"}}

    with caplog.at_level(logging.WARNING):
        out = relabel_transcript.relabel(src, cfg)

    assert "unmapped labels: Speaker 9" in caplog.text
    # Unmapped label is preserved verbatim as the speaker name.
    assert "**Speaker 9** | 00:01\nКто это?" in out


def test_timestamped_speaker_parsing_preserves_word_content():
    utterance = "Текст с  двойными пробелами, запятой и - тире."
    src = f"[01:02:03] Speaker 1: {utterance}\n"
    cfg = {"default": {"Speaker 1": "Андрей"}}

    out = relabel_transcript.relabel(src, cfg)

    # Hours are preserved as HH:MM:SS (no lossy collapse into minutes).
    assert "**Андрей** | 01:02:03" in out
    # A single-line utterance keeps its words verbatim (whitespace untouched here).
    assert utterance in out


def test_long_timecode_over_one_hour_renders_hh_mm_ss():
    src = "[01:30:00] Speaker 1: Прошёл час с половиной.\n"
    cfg = {"default": {"Speaker 1": "Андрей"}}

    out = relabel_transcript.relabel(src, cfg)

    # [01:30:00] must NOT become the ambiguous "90:00".
    assert "**Андрей** | 01:30:00" in out
    assert "90:00" not in out


def test_zero_hour_timecode_renders_mm_ss():
    src = "[00:05:07] Speaker 1: Без часов.\n"
    cfg = {"default": {"Speaker 1": "Андрей"}}

    out = relabel_transcript.relabel(src, cfg)

    # No hour component -> plain MM:SS.
    assert "**Андрей** | 05:07" in out


def test_lone_number_utterance_line_is_preserved():
    # A speaker literally says "42" on its own line inside a krisp-style block.
    src = "**Andrey | 01:23**\nОтвет на всё:\n42\nИ точка.\n"
    cfg = {"default": {"Andrey": "Андрей"}}

    out = relabel_transcript.relabel(src, cfg)

    assert "**Андрей** | 01:23\nОтвет на всё: 42 И точка." in out


def test_hash_prefixed_utterance_line_is_preserved():
    # A line starting with '#' is genuine speech, not a section header to drop.
    src = "**Andrey | 02:00**\nТеги такие:\n# важно\nвсё.\n"
    cfg = {"default": {"Andrey": "Андрей"}}

    out = relabel_transcript.relabel(src, cfg)

    assert "# важно" in out
    assert "Теги такие: # важно всё." in out


def test_word_content_round_trips_through_relabel():
    words = ["Привет", "это", "проверка", "содержимого", "слов"]
    src = "[00:00:01] Speaker 1: " + " ".join(words) + "\n"
    cfg = {"default": {"Speaker 1": "Андрей"}}

    out = relabel_transcript.relabel(src, cfg)

    # Every spoken word survives in order in the rendered body.
    body = out.split("\n", 1)[1] if "\n" in out else out
    for word in words:
        assert word in out
    # Words appear in the original relative order.
    positions = [body.index(w) for w in words]
    assert positions == sorted(positions)


def test_header_included_and_skipped():
    src = "[00:00:01] Speaker 1: Привет.\n"
    cfg = {"header": "# Заголовок\n\n> примечание", "default": {"Speaker 1": "Андрей"}}

    with_header = relabel_transcript.relabel(src, cfg)
    without_header = relabel_transcript.relabel(src, cfg, include_header=False)

    assert with_header.startswith("# Заголовок\n\n> примечание\n\n")
    assert "# Заголовок" not in without_header


def test_krisp_style_parsing_merges_text_lines():
    src = "**Andrey | 01:23**\nПервая строка.\nВторая строка.\n"
    cfg = {"default": {"Andrey": "Андрей"}}

    out = relabel_transcript.relabel(src, cfg)

    assert "**Андрей** | 01:23\nПервая строка. Вторая строка." in out


def test_empty_input_raises():
    cfg = {"default": {"Speaker 1": "Андрей"}}
    with pytest.raises(ValueError):
        relabel_transcript.relabel("no recognizable turns here", cfg)


def test_cli_main_writes_output(tmp_path, capsys):
    src_path = tmp_path / "src.md"
    src_path.write_text("[00:00:01] Speaker 1: Привет.\n", encoding="utf-8")
    map_path = tmp_path / "map.json"
    map_path.write_text(
        '{"default": {"Speaker 1": "Андрей"}}', encoding="utf-8"
    )
    out_path = tmp_path / "out.md"

    relabel_transcript.main(
        ["--in", str(src_path), "--out", str(out_path), "--map", str(map_path)]
    )

    out = out_path.read_text(encoding="utf-8")
    assert "**Андрей** | 00:01\nПривет." in out
    captured = capsys.readouterr()
    assert "turns: 1, blocks: 1" in captured.out
