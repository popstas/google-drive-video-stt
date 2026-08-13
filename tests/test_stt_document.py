from src import stt_document


def test_assemble_orders_keypoints_then_meta_then_transcript():
    text = stt_document.assemble(
        title="Созвон",
        sections=["## Задачи\n- [ ] Отправить анкету"],
        meta_yaml="subject: Обсудили кейс\n",
        transcript="[00:00:05] Angelica: Здравствуйте",
    )
    assert text.index("## Задачи") < text.index("## Мета") < text.index("## Расшифровка")
    assert text.startswith("# Созвон\n")


def test_assemble_fences_the_meta_as_yaml():
    text = stt_document.assemble(
        title="t", sections=[], meta_yaml="subject: x\n", transcript="a"
    )
    assert "## Мета\n```yaml\nsubject: x\n```" in text


def test_assemble_skips_a_missing_section_without_leaving_a_hole():
    text = stt_document.assemble(title="t", sections=["", "  "], meta_yaml="s: 1\n", transcript="a")
    assert "\n\n\n" not in text


def test_assemble_keeps_the_transcript_section_even_when_empty():
    text = stt_document.assemble(title="t", sections=[], meta_yaml="s: 1\n", transcript="")
    assert text.rstrip().endswith("## Расшифровка")
