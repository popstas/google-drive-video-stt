# `.stt` Artifact and Meta Document Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish one readable `.stt` file per call to Drive and write a machine-readable meta document beside it, with the model supplying subject, tags, and the client's referral source.

**Architecture:** The `meta` preset (written in July, never enabled) grows from two fields to four and is switched on. A new assembly step runs after the preset DAG: it merges the model's fields with facts the code already knows into `<stem>.meta.yml`, then concatenates keypoints, that meta block, and the cleaned transcript into `<stem>.stt`. `output.also_drive` narrows to publishing the `.stt` alone. The Planfix comment gains a header built from a configurable subset of meta fields. `action-items` is retired.

**Tech Stack:** Python 3.11/3.12, uv, pytest, ruff, PyYAML, google-api-python-client.

**Spec:** [docs/superpowers/specs/2026-08-13-stt-artifact-and-meta-design.md](../specs/2026-08-13-stt-artifact-and-meta-design.md)

## Global Constraints

- Branch off `stt-artifact-and-meta` (already created from `fix-speaker-roles-and-planfix-html`, PR #18). **Never commit to `main`.**
- **Never use `git commit --amend`** — the pre-commit hook generates `CHANGELOG.md` from commit messages, and amending rewrites a message the changelog already carries.
- **Never use `--no-verify`.** When the `generate-changelog` hook reports "files were modified by this hook", `git add CHANGELOG.md` and commit again.
- Python runs through `uv` only: `uv run pytest`, `uv run ruff check`. Never create another venv or install packages.
- `uv run ruff check` must pass (line-length 100, target py311).
- Tests mock every external service. No network calls, no real Drive/OpenAI/Deepgram/Planfix.
- Secrets live in `./data` (gitignored). Never commit `config.yml`, `credentials.json`, `token.json`.
- **Local artifact writes must not change.** In folder mode the local artifact is what marks a recording processed; `has_txt`, `_missing_preset_names`, and the preset artifact set decide what still needs work. Nothing in this plan may make an already-processed recording look pending — that would re-run Deepgram over a 1277-file backlog for real money.
- The Planfix comment body must stay **a single line of HTML**. Planfix rewrites every `\n` as `<br>`.
- The meta preset's own artifact keeps the suffix `.meta.md`. The merged document is `.meta.yml`. They are different files and both exist.

---

### Task 1: The `meta` preset returns four fields

The prompt gains `referral`/`referral_note` and a second allow-list; the parser reads and validates them.

**Files:**
- Modify: `src/assets/prompts/meta.md`
- Modify: `src/meta.py`
- Modify: `src/config.py` (`ALLOWED_REFERRALS_PLACEHOLDER`, `Config.referrals_allowed`, `_parse_referrals_allowed`, `_render_allowed_referrals`, `_render_prompt_placeholders`, `_resolve_prompt_text`, `_resolve_presets`, `to_dict`)
- Test: `tests/test_meta.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `meta.Meta(subject: str, tags: tuple[str, ...], referral: str, referral_note: str)` and `meta.parse_meta(text: str, allowed: Iterable[str], referrals_allowed: Iterable[str] = ()) -> Meta`; `Config.referrals_allowed: tuple[str, ...]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_meta.py`:

```python
def test_parse_meta_reads_subject_and_referral():
    text = (
        "---\n"
        "subject: Обсудили состав кейса\n"
        "tags: [O-1]\n"
        "referral: рекомендация\n"
        "referral_note: Посоветовала знакомая из Нью-Йорка\n"
        "---\n"
    )
    parsed = parse_meta(text, ["O-1"], ["рекомендация", "instagram"])
    assert parsed.subject == "Обсудили состав кейса"
    assert parsed.tags == ("O-1",)
    assert parsed.referral == "рекомендация"
    assert parsed.referral_note == "Посоветовала знакомая из Нью-Йорка"


def test_parse_meta_drops_referral_outside_the_allow_list():
    text = "---\nsubject: x\ntags: []\nreferral: телепатия\nreferral_note: сон\n---\n"
    parsed = parse_meta(text, [], ["рекомендация"])
    assert parsed.referral == ""
    # The note describes a channel that was rejected, so it cannot stand alone.
    assert parsed.referral_note == ""


def test_parse_meta_keeps_empty_referral_when_the_call_never_covered_it():
    text = "---\nsubject: x\ntags: []\nreferral: ''\nreferral_note: ''\n---\n"
    parsed = parse_meta(text, [], ["рекомендация"])
    assert parsed.referral == ""
    assert parsed.referral_note == ""


def test_parse_meta_repairs_an_unquoted_colon_in_referral_note():
    text = (
        "---\n"
        "subject: Разговор\n"
        "tags: []\n"
        "referral: instagram\n"
        "referral_note: Написала после рилса: про визу талантов\n"
        "---\n"
    )
    parsed = parse_meta(text, [], ["instagram"])
    assert parsed.referral == "instagram"
    assert parsed.referral_note.startswith("Написала после рилса")
```

Append to `tests/test_config.py`:

```python
def test_referrals_allowed_is_parsed(tmp_path):
    config = _load_config(tmp_path, {"referrals": {"allowed": ["рекомендация", "instagram"]}})
    assert config.referrals_allowed == ("рекомендация", "instagram")


def test_referrals_allowed_renders_into_the_meta_prompt(tmp_path):
    config = _load_config(
        tmp_path,
        {
            "openai": {"api_key": "k"},
            "referrals": {"allowed": ["instagram"]},
            "presets": {"meta": {"enabled": True}},
        },
    )
    meta_preset = next(p for p in config.presets if p.name == "meta")
    assert "{{allowed_referrals}}" not in meta_preset.instructions
    assert "- instagram" in meta_preset.instructions


def test_referrals_allowed_must_be_a_list(tmp_path):
    with pytest.raises(ValueError, match="referrals.allowed"):
        _load_config(tmp_path, {"referrals": {"allowed": "instagram"}})
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_meta.py tests/test_config.py -v`
Expected: FAIL — `parse_meta() takes 2 positional arguments`, `Meta has no attribute 'subject'`, `Config has no attribute 'referrals_allowed'`.

- [ ] **Step 3: Rename `topic` to `subject` and add the referral fields in `src/meta.py`**

Rename the dataclass field, then generalize the colon repair so it covers both prose fields:

```python
@dataclass(frozen=True)
class Meta:
    """The ``meta`` preset's structured output."""

    subject: str = ""
    tags: tuple[str, ...] = ()
    referral: str = ""
    referral_note: str = ""


# The prose fields a model routinely writes unquoted, and routinely writes a colon
# into. YAML then reads the line as a nested mapping and rejects the whole document,
# taking the fields that parsed fine down with it.
_REQUOTABLE_FIELDS = ("subject", "referral_note")


def _field_line_re(field: str) -> re.Pattern[str]:
    return re.compile(
        rf"^(?P<indent>[ \t]*){field}:[ \t]*(?P<value>\S.*?)[ \t]*$",
        re.MULTILINE,
    )


def _requote_field(body: str, field: str) -> str | None:
    """Re-quote one unquoted scalar so a colon inside it can't break the document."""
    match = _field_line_re(field).search(body)
    if match is None:
        return None
    value = match.group("value")
    if value[0] in _YAML_VALUE_INDICATORS or ":" not in value:
        return None
    quoted = json.dumps(value, ensure_ascii=False)
    repaired = f"{match.group('indent')}{field}: {quoted}"
    return body[: match.start()] + repaired + body[match.end() :]


def _requote_prose(body: str) -> str | None:
    """Apply ``_requote_field`` to every repairable field; None when nothing changed."""
    repaired = body
    changed = False
    for field in _REQUOTABLE_FIELDS:
        candidate = _requote_field(repaired, field)
        if candidate is not None:
            repaired = candidate
            changed = True
    return repaired if changed else None
```

Delete `_TOPIC_LINE_RE` and `_requote_topic`; call `_requote_prose(body)` where `_requote_topic(body)` was called in `_parse_frontmatter`.

Then the referral parsing and the new `parse_meta`:

```python
def _parse_referral(raw: object, allowed: Iterable[str]) -> str:
    """Return the referral channel when it is on the allow-list, else an empty string.

    An empty ``allowed`` rejects everything: a config with no ``referrals.allowed`` gave
    the model nothing to pick from, so any channel it returned is invented.
    """
    if raw is None:
        return ""
    channel = str(raw).strip()
    if not channel:
        return ""
    if channel not in {str(entry).strip() for entry in allowed}:
        logger.debug("dropping meta referral outside the allow-list: %r", channel)
        return ""
    return channel


def parse_meta(
    text: str, allowed: Iterable[str], referrals_allowed: Iterable[str] = ()
) -> Meta:
    """Read the ``meta`` artifact's YAML frontmatter into structured fields.

    ``allowed``/``referrals_allowed`` are the configured allow-lists; values outside
    them are dropped, and an empty allow-list drops all of them. A missing or malformed
    block yields an empty ``Meta``.
    """
    parsed = _parse_frontmatter(text)
    if parsed is None:
        return Meta()

    subject_raw = parsed.get("subject")
    subject = "" if subject_raw is None else str(subject_raw).strip()
    referral = _parse_referral(parsed.get("referral"), referrals_allowed)
    note_raw = parsed.get("referral_note")
    note = "" if note_raw is None else str(note_raw).strip()
    return Meta(
        subject=subject,
        tags=_parse_tags(parsed.get("tags"), allowed),
        referral=referral,
        # A note without a surviving channel describes a source we rejected; keeping it
        # would smuggle an off-list channel back in as prose.
        referral_note=note if referral else "",
    )
```

- [ ] **Step 4: Add `referrals.allowed` to `src/config.py`**

Next to `ALLOWED_TAGS_PLACEHOLDER` (line 47):

```python
ALLOWED_REFERRALS_PLACEHOLDER = "{{allowed_referrals}}"
```

On `Config`, next to `tags_allowed` (line 114):

```python
    # The referral channels the ``meta`` preset may pick from. Empty means the preset
    # is handed no channels and must return an empty referral.
    referrals_allowed: tuple[str, ...] = ()
```

Next to `_parse_tags_allowed`:

```python
def _parse_referrals_allowed(raw: object) -> tuple[str, ...]:
    """Parse the ``referrals.allowed`` list into a tuple of non-empty channel names."""
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise ValueError(f"referrals.allowed must be a list of channels, got: {raw!r}")
    return tuple(name for name in (_yaml_str(entry) for entry in raw) if name)


def _render_allowed_referrals(referrals_allowed: tuple[str, ...]) -> str:
    """Render ``referrals.allowed`` as the bullet list that replaces the placeholder."""
    if not referrals_allowed:
        return "(none configured — return an empty referral)"
    return "\n".join(f"- {name}" for name in referrals_allowed)
```

Widen the placeholder renderer and everything that calls it:

```python
def _render_prompt_placeholders(
    text: str,
    tags_allowed: tuple[str, ...],
    referrals_allowed: tuple[str, ...] = (),
) -> str:
    """Substitute the supported ``{{...}}`` placeholders in a resolved prompt.

    Today those are ``{{allowed_tags}}`` and ``{{allowed_referrals}}`` (both the
    ``meta`` preset's). A prompt without them is returned unchanged, so this is safe to
    run over every preset's text.
    """
    if ALLOWED_TAGS_PLACEHOLDER in text:
        text = text.replace(ALLOWED_TAGS_PLACEHOLDER, _render_allowed_tags(tags_allowed))
    if ALLOWED_REFERRALS_PLACEHOLDER in text:
        text = text.replace(
            ALLOWED_REFERRALS_PLACEHOLDER, _render_allowed_referrals(referrals_allowed)
        )
    return text
```

Thread `referrals_allowed: tuple[str, ...] = ()` through `_resolve_prompt_text` and `_resolve_presets` (each forwards it to `_render_prompt_placeholders` at all three call sites inside `_resolve_prompt_text`). In the loader body, beside `tags_allowed = _parse_tags_allowed(tags.get("allowed"))`:

```python
    referrals = data.get("referrals") or {}
    if not isinstance(referrals, dict):
        raise ValueError(f"referrals must be a mapping, got: {referrals!r}")
    referrals_allowed = _parse_referrals_allowed(referrals.get("allowed"))
```

Pass it to `_resolve_presets(config_presets, config_file, tags_allowed, referrals_allowed)` and into the `Config(...)` construction as `referrals_allowed=referrals_allowed`. In `to_dict`, beside the `"tags"` entry:

```python
        "referrals": {"allowed": list(config.referrals_allowed)},
```

- [ ] **Step 5: Rewrite `src/assets/prompts/meta.md`**

```markdown
You are a meeting analyst. You receive a speaker-named transcript of a recorded
conversation and describe it with a subject, a set of tags, and where the client
heard about the company.

Return ONLY a YAML frontmatter block and nothing else — no preamble, no
explanation, no Markdown body after it:

---
subject: <one sentence>
tags: [<tag>, <tag>]
referral: <channel>
referral_note: <the client's own words>
---

Rules:

- `subject` is exactly one sentence saying what the call was about, written in the
  transcript's own language. Base it strictly on the transcript; never invent
  facts.
- `tags` may contain ONLY tags from the allowed list below, copied verbatim.
  Never invent a tag, translate one, or alter its spelling.
- Pick every tag that genuinely fits and no others. Return an empty list
  (`tags: []`) when nothing in the allowed list fits.
- `referral` is where the client first heard about the company. Use ONLY a channel
  from the allowed referrals list below, copied verbatim.
- Fill `referral` only when the client themselves says where they heard about the
  company. A manager asking the question and getting no answer is not a source,
  and neither is your guess from context.
- `referral_note` is one line in the client's own words about it: who recommended
  them, which post, which event. Leave it empty when `referral` is empty.
- When the call never covers where the client came from, return `referral: ''` and
  `referral_note: ''` rather than a guess.
- Keep every value on a single line and quote it if it contains a colon.

Allowed tags:

{{allowed_tags}}

Allowed referrals:

{{allowed_referrals}}
```

Update the comment above `META_INSTRUCTIONS` in `src/presets.py` to say four fields and two allow-lists.

- [ ] **Step 6: Fix the fallout in existing tests**

`topic` is gone. Run `uv run pytest -k "meta or webhook or preset" -v` and update every assertion that reads `parsed.topic` to `parsed.subject`. In `src/main.py`, `_webhook_payload` currently builds `{"topic": parsed.topic, "tags": ...}`; change it to `{"subject": parsed.subject, "tags": list(parsed.tags), "referral": parsed.referral, "referral_note": parsed.referral_note}` and update the matching webhook-payload test. No compatibility alias — the preset has never run in production, so no artifact spells it the old way.

- [ ] **Step 7: Run the whole suite and lint**

Run: `uv run pytest && uv run ruff check`
Expected: PASS, no lint findings.

- [ ] **Step 8: Commit**

```bash
git add src/meta.py src/config.py src/presets.py src/main.py src/assets/prompts/meta.md tests/
git commit -m "feat: teach the meta preset to record the client's referral source"
# If the changelog hook reports modified files:
git add CHANGELOG.md && git commit -m "feat: teach the meta preset to record the client's referral source"
```

---

### Task 2: Build the meta document

Merge the model's four fields with the facts the code already knows, and serialize them.

**Files:**
- Create: `src/meta_doc.py`
- Modify: `src/postprocess.py` (add `split_participants`)
- Test: `tests/test_meta_doc.py` (create), `tests/test_postprocess.py`

**Interfaces:**
- Consumes: `meta.Meta` and `meta.parse_meta` from Task 1.
- Produces:
  - `postprocess.split_participants(file_name: str) -> tuple[str, list[str]]` — `(manager, clients)`.
  - `meta_doc.build(*, meta: Meta, file_id: str, file_name: str, folder_id: str, config: Config, transcript: str, planfix_task_id: str, processed_at: datetime) -> dict[str, object]`
  - `meta_doc.to_yaml(document: dict[str, object]) -> str`
  - `meta_doc.FIELD_ORDER: tuple[str, ...]`

- [ ] **Step 1: Write the failing tests for `split_participants`**

Append to `tests/test_postprocess.py`:

```python
def test_split_participants_reads_the_marker_attached_to_a_name():
    manager, clients = postprocess.split_participants(
        "30-минутная онлайн-встреча Angelica Munkueva(ExpertizeMe) и Mels "
        "- 2026/08/13 14:29 CEST - Recording.mp4"
    )
    assert manager == "Angelica Munkueva"
    assert clients == ["Mels"]


def test_split_participants_reads_the_marker_as_its_own_token():
    manager, clients = postprocess.split_participants("Ольга х ExpertizeMe - 2026/07/02 21:56 CEST")
    assert manager == "Ольга"
    assert clients == []


def test_split_participants_without_a_marker_names_no_manager():
    manager, clients = postprocess.split_participants("Alice and Bob - 2026/07/02 21:56 CEST")
    assert manager == ""
    assert clients == ["Alice", "Bob"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_postprocess.py -k split_participants -v`
Expected: FAIL with `AttributeError: module 'src.postprocess' has no attribute 'split_participants'`.

- [ ] **Step 3: Implement `split_participants`**

In `src/postprocess.py`, beside `extract_interlocutor_names`:

```python
_ORG_MARKER_RE = re.compile("|".join(_ORG_TOKENS), re.IGNORECASE)


def split_participants(file_name: str) -> tuple[str, list[str]]:
    """Split a recording name into ``(manager, clients)`` using the ExpertizeMe marker.

    Meet writes the organizer two ways: attached to the name
    (``Angelica Munkueva(ExpertizeMe) и Mels``) or as its own conjunction-separated
    token (``Ольга х ExpertizeMe``). Both mean the same thing, so both are read here.

    A name with no marker returns an empty manager and every name as a client: the
    caller must be able to tell "the organizer is unmarked" from "the organizer is
    this person", and guessing would put the client's name in the manager's field.
    """
    stem = os.path.splitext(file_name)[0]
    stem = _DURATION_PREFIX_RE.sub("", stem)

    date_match = _DATE_RE.search(stem)
    if date_match:
        stem = stem[: date_match.start()]

    head = _TITLE_SEP_RE.split(stem)[0]

    manager = ""
    names: list[str] = []
    for raw in _NAME_SEP_RE.split(head):
        marked = bool(_ORG_MARKER_RE.search(raw))
        part = _PARENTHETICAL_RE.sub(" ", raw).strip()
        part = _ORG_MARKER_RE.sub("", part).strip()
        if not part or _MEET_CODE_RE.match(part):
            # A bare marker token: the organizer is whoever was named just before it.
            if marked and names:
                manager = manager or names.pop()
            continue
        if marked:
            manager = manager or part
            continue
        if part not in names:
            names.append(part)
    return manager, [name for name in names if name != manager]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_postprocess.py -v`
Expected: PASS, including the pre-existing `extract_interlocutor_names` tests (untouched).

- [ ] **Step 5: Write the failing tests for the document**

Create `tests/test_meta_doc.py`:

```python
from datetime import datetime, timezone

import yaml

from src import meta_doc
from src.meta import Meta

TRANSCRIPT = "[00:00:05] Angelica Munkueva: Здравствуйте\n[00:31:42] Mels: Спасибо\n"
NAME = (
    "30-минутная онлайн-встреча Angelica Munkueva(ExpertizeMe) и Mels "
    "- 2026/08/13 14:29 CEST - Recording.mp4"
)


def _document(config, **overrides):
    kwargs = {
        "meta": Meta(subject="Обсудили кейс", tags=("O-1",), referral="рекомендация",
                     referral_note="Посоветовала знакомая"),
        "file_id": "FILE1",
        "file_name": NAME,
        "folder_id": "FOLDER1",
        "config": config,
        "transcript": TRANSCRIPT,
        "planfix_task_id": "918659",
        "processed_at": datetime(2026, 8, 13, 18, 52, 10, tzinfo=timezone.utc),
    }
    kwargs.update(overrides)
    return meta_doc.build(**kwargs)


def test_build_merges_model_fields_with_known_facts(config_with_folder):
    document = _document(config_with_folder)
    assert document["subject"] == "Обсудили кейс"
    assert document["tags"] == ["O-1"]
    assert document["referral"] == "рекомендация"
    assert document["manager"] == "Анжелика Мункуева"
    assert document["manager_email"] == "angelica@expertizeme.org"
    assert document["client"] == "Mels"
    assert document["planfix_task_id"] == "918659"
    assert document["video_url"] == "https://drive.google.com/file/d/FILE1/view"


def test_build_reads_the_date_from_the_recording_name(config_with_folder):
    assert _document(config_with_folder)["date"] == "2026-08-13T12:29:00+00:00"


def test_build_leaves_the_date_empty_when_the_name_has_no_timestamp(config_with_folder):
    assert _document(config_with_folder, file_name="Recording.mp4")["date"] == ""


def test_build_takes_the_duration_from_the_last_timestamp(config_with_folder):
    assert _document(config_with_folder)["duration"] == "00:31:42"


def test_build_leaves_the_duration_empty_without_timestamps(config_with_folder):
    assert _document(config_with_folder, transcript="Speaker 1: привет")["duration"] == ""


def test_build_keeps_every_field_present_when_the_model_returned_nothing(config_with_folder):
    document = _document(config_with_folder, meta=Meta())
    assert set(document) == set(meta_doc.FIELD_ORDER)
    assert document["subject"] == ""
    assert document["tags"] == []


def test_to_yaml_round_trips_and_keeps_the_declared_order(config_with_folder):
    text = meta_doc.to_yaml(_document(config_with_folder))
    assert list(yaml.safe_load(text)) == list(meta_doc.FIELD_ORDER)
    assert "Обсудили кейс" in text
```

with this fixture at the top of the same file (`Config` requires the first nine fields positionally-by-name; everything else has a default):

```python
import pytest

from pathlib import Path
from src.config import Config, EmployeeFolder


@pytest.fixture
def config_with_folder():
    return Config(
        folders=(EmployeeFolder("FOLDER1", "Анжелика Мункуева", "angelica@expertizeme.org"),),
        poll_interval=600,
        bitrate="96k",
        data_dir=Path("data"),
        proxy_url="",
        stt_provider="deepgram",
        openai_api_key="",
        deepgram_api_key="",
        stt_language="ru-RU",
    )
```

`openai_model` (`gpt-5.4-mini`) and `deepgram_model` (`nova-3`) come from `Config`'s own defaults, so `stt_model` is `deepgram/nova-3`.

- [ ] **Step 6: Run them to verify they fail**

Run: `uv run pytest tests/test_meta_doc.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.meta_doc'`.

- [ ] **Step 7: Implement `src/meta_doc.py`**

```python
"""Merge the ``meta`` preset's fields with the facts the code already knows.

The model is asked for four things — subject, tags, referral, referral note. Everything
else in the document is already in hand: the folder's employee, the recording's name,
the booking, the configured models. Paying a model to restate them would be slower, more
expensive, and less reliable than reading them.

Every field stays present even when empty. An operator reading the file must be able to
tell "nobody asked about the referral" from "this build does not produce that field".
"""

from __future__ import annotations

import re
from datetime import datetime

import yaml

from src import meeting_time, postprocess
from src.config import Config
from src.meta import Meta

# The document's field order, top to bottom: what the call was about, who was on it,
# when, then the technical trail.
FIELD_ORDER = (
    "subject",
    "tags",
    "referral",
    "referral_note",
    "manager",
    "manager_email",
    "client",
    "date",
    "duration",
    "language",
    "planfix_task_id",
    "video_id",
    "video_url",
    "source_name",
    "stt_model",
    "llm_model",
    "processed_at",
)

_TIMESTAMP_RE = re.compile(r"\[(\d{2}:\d{2}:\d{2})\]")


def _duration(transcript: str) -> str:
    """The last timestamp in the transcript, as the call's length.

    Not the media's true duration: trailing silence after the last word is dropped. That
    is cheaper than probing the file and close enough for a summary line.
    """
    stamps = _TIMESTAMP_RE.findall(transcript or "")
    return stamps[-1] if stamps else ""


def _client(file_name: str) -> str:
    _, clients = postprocess.split_participants(file_name)
    return clients[0] if clients else ""


def _date(file_name: str) -> str:
    start = meeting_time.parse_meeting_start(file_name)
    return start.isoformat() if start else ""


def build(
    *,
    meta: Meta,
    file_id: str,
    file_name: str,
    folder_id: str,
    config: Config,
    transcript: str,
    planfix_task_id: str,
    processed_at: datetime,
) -> dict[str, object]:
    """Assemble the full meta document for one recording."""
    employee = config.folder_by_id(folder_id)
    return {
        "subject": meta.subject,
        "tags": list(meta.tags),
        "referral": meta.referral,
        "referral_note": meta.referral_note,
        "manager": employee.name if employee else "",
        "manager_email": employee.email if employee else "",
        "client": _client(file_name),
        "date": _date(file_name),
        "duration": _duration(transcript),
        "language": config.stt_language,
        "planfix_task_id": planfix_task_id,
        "video_id": file_id,
        "video_url": f"https://drive.google.com/file/d/{file_id}/view",
        "source_name": file_name,
        "stt_model": f"{config.stt_provider}/{config.deepgram_model}",
        "llm_model": config.openai_model,
        "processed_at": processed_at.isoformat(),
    }


def to_yaml(document: dict[str, object]) -> str:
    """Serialize the document with its declared field order and readable Cyrillic."""
    ordered = {key: document.get(key, "") for key in FIELD_ORDER}
    return yaml.safe_dump(ordered, allow_unicode=True, sort_keys=False, width=1000)
```

Check the real attribute names on `Config` for the provider/model fields before writing this (`config.stt_provider`, `config.deepgram_model`, `config.openai_model`, `config.stt_language`); use whatever they are actually called and keep the composed `stt_model` string in `provider/model` shape.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest tests/test_meta_doc.py tests/test_postprocess.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/meta_doc.py src/postprocess.py tests/test_meta_doc.py tests/test_postprocess.py
git commit -m "feat: assemble a meta document describing each call"
```

---

### Task 3: Assemble the `.stt` document

**Files:**
- Create: `src/stt_document.py`
- Modify: `src/config.py` (`Config.stt_presets`, parsing under `output`)
- Test: `tests/test_stt_document.py` (create), `tests/test_config.py`

**Interfaces:**
- Consumes: `meta_doc.to_yaml` from Task 2.
- Produces: `stt_document.assemble(*, title: str, sections: list[str], meta_yaml: str, transcript: str) -> str` and `Config.stt_presets: tuple[str, ...]` (default `("keypoints",)`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_stt_document.py`:

```python
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
```

Append to `tests/test_config.py`:

```python
def test_stt_presets_defaults_to_keypoints(tmp_path):
    assert _load_config(tmp_path, {}).stt_presets == ("keypoints",)


def test_stt_presets_is_read_from_output(tmp_path):
    config = _load_config(tmp_path, {"output": {"stt_presets": ["keypoints", "action-items"]}})
    assert config.stt_presets == ("keypoints", "action-items")


def test_stt_presets_must_be_a_list(tmp_path):
    with pytest.raises(ValueError, match="output.stt_presets"):
        _load_config(tmp_path, {"output": {"stt_presets": "keypoints"}})
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_stt_document.py tests/test_config.py -v`
Expected: FAIL — no module `src.stt_document`, no attribute `stt_presets`.

- [ ] **Step 3: Implement `src/stt_document.py`**

```python
"""Assemble the one file a human opens after a call.

Keypoints first, because that is what anyone reads; then the meta block, so the
call can be placed without scrolling; then the transcript, which is the evidence for
both. The document is plain text — no LLM call — built from artifacts that already
exist.
"""

from __future__ import annotations

META_HEADING = "## Мета"
TRANSCRIPT_HEADING = "## Расшифровка"


def assemble(
    *, title: str, sections: list[str], meta_yaml: str, transcript: str
) -> str:
    """Concatenate the preset sections, the meta block, and the transcript.

    An empty section is dropped rather than emitted as a bare heading; the transcript
    heading is kept even when the transcript is empty, so a reader can tell the section
    exists and came back blank.
    """
    blocks: list[str] = [f"# {title.strip()}"] if title.strip() else []
    blocks.extend(section.strip() for section in sections if section and section.strip())
    blocks.append(f"{META_HEADING}\n```yaml\n{meta_yaml.strip()}\n```")
    blocks.append(f"{TRANSCRIPT_HEADING}\n{transcript.strip()}".rstrip())
    return "\n\n".join(blocks) + "\n"
```

- [ ] **Step 4: Add `output.stt_presets` to `src/config.py`**

On `Config`, next to `output_also_drive`:

```python
    # Which preset artifacts open the ``.stt`` document, in this order. Presets with no
    # artifact are skipped at assembly time rather than rejected here: a preset can be
    # disabled without invalidating the config.
    stt_presets: tuple[str, ...] = ("keypoints",)
```

Beside `_parse_planfix_presets`:

```python
def _parse_stt_presets(value: object) -> tuple[str, ...]:
    """Read ``output.stt_presets``, defaulting to the single ``keypoints`` preset."""
    if value is None:
        return ("keypoints",)
    if not isinstance(value, list):
        raise ValueError(f"output.stt_presets must be a list, got: {value!r}")
    names = []
    for entry in value:
        name = _yaml_str(entry)
        if not name:
            raise ValueError(f"output.stt_presets entries must be names, got: {entry!r}")
        names.append(name)
    return tuple(names)
```

Call it where `output_also_drive` is parsed (`stt_presets = _parse_stt_presets(output.get("stt_presets"))`), pass `stt_presets=stt_presets` into `Config(...)`, and emit it in `to_dict` inside the `output` mapping.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_stt_document.py tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/stt_document.py src/config.py tests/test_stt_document.py tests/test_config.py
git commit -m "feat: assemble keypoints, meta, and transcript into one .stt document"
```

---

### Task 4: Write `.meta.yml` and `.stt` for every processed recording

**Files:**
- Modify: `src/main.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `meta_doc.build`/`to_yaml` (Task 2), `stt_document.assemble` and `Config.stt_presets` (Task 3), `meta.parse_meta` (Task 1).
- Produces: `main._write_call_documents(service, file_id, file_name, folder_id, transcript, artifacts, config, tmp_dir, *, item, booking_decision) -> dict[str, object] | None` — returns the meta document (for Task 6's Planfix header) or `None` when nothing was written.

- [ ] **Step 1: Write the failing tests**

First extend `make_config` in `tests/test_main.py` with the fields this plan added, each defaulting to the `Config` default: `output_also_drive=False`, `stt_presets=("keypoints",)`, `referrals_allowed=()`, `planfix_meta_fields=<the Config default>`. Pass them through to the `Config(...)` construction.

Then append:

```python
_STT_NAME = "Angelica Munkueva(ExpertizeMe) и Mels - 2026/08/13 14:29 CEST.mp4"
_STT_STEM = "Angelica Munkueva(ExpertizeMe) и Mels - 2026_08_13 14_29 CEST"
_STT_TRANSCRIPT = "[00:00:05] Angelica Munkueva: Здравствуйте\n[00:31:42] Mels: Спасибо"
_META_ARTIFACT = "---\nsubject: Обсудили кейс\ntags: []\n---"


def _stt_config(tmp_path, **overrides):
    out = tmp_path / "results"
    out.mkdir(exist_ok=True)
    return make_config(
        folders=[{"folder_id": "folderA", "name": "Анжелика Мункуева",
                  "email": "angelica@expertizeme.org"}],
        presets=(_KEYPOINTS_BUILTIN,),
        output_target="folder",
        output_dir=out,
        **overrides,
    )


def _write_documents(cfg, tmp_path, artifacts, transcript=_STT_TRANSCRIPT):
    return main._write_call_documents(
        MagicMock(),
        "fid1",
        _STT_NAME,
        "folderA",
        transcript,
        artifacts,
        cfg,
        tmp_path,
        item={},
        booking_decision=MATCHED_DECISION,
    )


def test_write_call_documents_writes_the_stt_and_the_meta_yml(tmp_path):
    cfg = _stt_config(tmp_path)
    document = _write_documents(
        cfg, tmp_path, {"keypoints": "## Задачи\n- [ ] x", "meta": _META_ARTIFACT}
    )
    stt = (cfg.output_dir / f"{_STT_STEM}.stt").read_text(encoding="utf-8")
    assert stt.index("## Задачи") < stt.index("## Мета") < stt.index("## Расшифровка")
    assert (cfg.output_dir / f"{_STT_STEM}.meta.yml").exists()
    assert document["subject"] == "Обсудили кейс"
    assert document["planfix_task_id"] == "851030"


def test_write_call_documents_reads_a_preset_from_its_local_artifact(tmp_path):
    """A cycle that re-ran only `meta` must still get keypoints into the .stt."""
    cfg = _stt_config(tmp_path)
    (cfg.output_dir / f"{_STT_STEM}.keypoints.md").write_text(
        "## Задачи\n- [ ] x", encoding="utf-8"
    )
    _write_documents(cfg, tmp_path, {"meta": _META_ARTIFACT})
    stt = (cfg.output_dir / f"{_STT_STEM}.stt").read_text(encoding="utf-8")
    assert "## Задачи" in stt


def test_write_call_documents_falls_back_to_the_raw_transcript(tmp_path):
    """No transcript-cleanup artifact means the .stt carries the raw text."""
    cfg = _stt_config(tmp_path)
    _write_documents(cfg, tmp_path, {})
    stt = (cfg.output_dir / f"{_STT_STEM}.stt").read_text(encoding="utf-8")
    assert "[00:00:05] Angelica Munkueva: Здравствуйте" in stt


def test_write_call_documents_writes_the_meta_yml_when_the_preset_produced_nothing(tmp_path):
    cfg = _stt_config(tmp_path)
    document = _write_documents(cfg, tmp_path, {"keypoints": "## Задачи"})
    assert (cfg.output_dir / f"{_STT_STEM}.meta.yml").exists()
    assert document["subject"] == ""
    assert document["client"] == "Mels"
```

`_STT_STEM` differs from `_STT_NAME` because `drive.safe_local_name` replaces `/` and `:` — confirm the exact local filename by reading `drive.safe_local_name` before writing the assertions, and adjust the constant to what it actually returns rather than forcing the code to match the guess.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_main.py -k write_call_documents -v`
Expected: FAIL with `AttributeError: module 'src.main' has no attribute '_write_call_documents'`.

- [ ] **Step 3: Implement `_write_call_documents` in `src/main.py`**

```python
def _artifact_text(
    name: str, artifacts: dict[str, str], config: Config, mp4_name: str
) -> str:
    """This cycle's text for a preset, or the artifact an earlier cycle left on disk.

    A cycle that re-ran only the still-missing presets returns just those, so the
    document would otherwise lose the sections that completed earlier.
    """
    text = artifacts.get(name, "")
    if text.strip():
        return text
    preset = next((p for p in config.presets if p.name == name), None)
    if preset is None:
        return ""
    path = _local_artifact_path(config, mp4_name, preset.artifact_suffix)
    if path is None or not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not read %s for the .stt document: %s", path, type(exc).__name__)
        return ""


def _write_call_documents(
    service: Any,
    file_id: str,
    file_name: str,
    folder_id: str,
    transcript: str,
    artifacts: dict[str, str],
    config: Config,
    tmp_dir: Path,
    *,
    item: dict,
    booking_decision: booking_gate.BookingDecision,
) -> dict[str, object] | None:
    """Write ``<stem>.meta.yml`` and ``<stem>.stt`` for one recording.

    Returns the meta document so the Planfix comment can quote from it. Both files are
    written from artifacts that already exist, so this costs no model call. Neither file
    takes part in the processed/pending bookkeeping: only ``.txt`` and the preset
    artifacts decide whether a recording still needs work, and a deleted ``.stt`` must
    never put a recording back into the transcription queue.
    """
    stem = drive.drive_stem(file_name)
    parsed = meta_module.parse_meta(
        _artifact_text("meta", artifacts, config, file_name),
        config.tags_allowed,
        config.referrals_allowed,
    )
    task_id = booking_decision.task_id or str(item.get("planfix_comment_task_id") or "")
    document = meta_doc.build(
        meta=parsed,
        file_id=file_id,
        file_name=file_name,
        folder_id=folder_id,
        config=config,
        transcript=transcript,
        planfix_task_id=task_id,
        processed_at=datetime.now(timezone.utc),
    )
    meta_yaml = meta_doc.to_yaml(document)

    body = _artifact_text("transcript-cleanup", artifacts, config, file_name) or transcript
    text = stt_document.assemble(
        title=stem,
        sections=[
            _artifact_text(name, artifacts, config, file_name) for name in config.stt_presets
        ],
        meta_yaml=meta_yaml,
        transcript=body,
    )

    output.write_artifact(
        service, base_name=stem, suffix=".meta.yml", text=meta_yaml,
        folder_id=folder_id, config=config, tmp_dir=tmp_dir,
    )
    output.write_artifact(
        service, base_name=stem, suffix=".stt", text=text, folder_id=folder_id,
        config=config, tmp_dir=tmp_dir,
        app_properties={
            drive.SOURCE_VIDEO_ID_PROPERTY: file_id,
            drive.ARTIFACT_TYPE_PROPERTY: "stt",
        },
        mime_type=drive.TXT_MIME,
    )
    return document
```

The `.meta.yml` needs no "local only" flag: Task 5 filters Drive publishing by suffix, so only the `.stt` ever leaves folder mode. Add the imports (`meta_doc`, `stt_document`, `datetime`/`timezone`) at the top of `src/main.py`.

- [ ] **Step 4: Call it from both preset paths**

In `process_item`, immediately after each of the two `_run_preset_stage(...)` calls (the fresh-transcription branch and the `elif needs_presets` branch), inside the `with tempfile.TemporaryDirectory()` block so `tmp_dir` is still live:

```python
                meta_document = _write_call_documents(
                    service, file_id, file_name, folder_id, text, artifacts,
                    config, tmp_dir, item=item, booking_decision=booking_decision,
                )
```

Initialize `meta_document: dict[str, object] | None = None` beside the other pre-`try` locals so the success path can read it after the block exits.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_main.py -v`
Expected: PASS, including the existing `process_item` tests.

- [ ] **Step 6: Commit**

```bash
git add src/main.py tests/test_main.py
git commit -m "feat: write a .stt document and a meta.yml for every processed recording"
```

---

### Task 5: Publish only the `.stt` to Drive

**Files:**
- Modify: `src/output.py`
- Test: `tests/test_output.py`

**Interfaces:**
- Consumes: the `.stt` write from Task 4.
- Produces: the narrowed `also_drive` behaviour, keyed on `suffix == ".stt"`, and `output.DRIVE_PUBLISHED_SUFFIX`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_output.py`:

```python
def test_also_drive_publishes_the_stt(tmp_path, drive_service):
    config = _folder_config(tmp_path, also_drive=True)
    output.write_artifact(drive_service, base_name="rec", suffix=".stt", text="x",
                          folder_id="F", config=config, tmp_dir=tmp_path)
    assert drive_service.uploaded == ["rec.stt"]


def test_also_drive_does_not_publish_other_artifacts(tmp_path, drive_service):
    config = _folder_config(tmp_path, also_drive=True)
    for suffix in (".txt", ".keypoints.md", ".meta.yml"):
        output.write_artifact(drive_service, base_name="rec", suffix=suffix, text="x",
                              folder_id="F", config=config, tmp_dir=tmp_path)
    assert drive_service.uploaded == []
    assert (config.output_dir / "rec.keypoints.md").exists()


def test_drive_target_still_uploads_every_artifact(tmp_path, drive_service):
    """Only folder mode narrows; the drive target keeps its per-artifact behaviour."""
    config = _drive_config(tmp_path)
    output.write_artifact(drive_service, base_name="rec", suffix=".keypoints.md", text="x",
                          folder_id="F", config=config, tmp_dir=tmp_path)
    assert drive_service.uploaded == ["rec.keypoints.md"]
```

Reuse the fixtures the existing `also_drive` tests already use; rename or extend the helpers rather than duplicating them.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_output.py -v`
Expected: FAIL — every artifact currently uploads under `also_drive`, and `local_only` is not a parameter.

- [ ] **Step 3: Narrow `also_drive` in `src/output.py`**

Add the constant and the parameter:

```python
# The only artifact published to Drive from folder mode: one file per call carrying
# keypoints, meta, and the transcript. Publishing each artifact separately put four
# files next to every recording and made the folder unreadable.
DRIVE_PUBLISHED_SUFFIX = ".stt"
```

In `write_artifact`, replace the folder-mode branch's early return:

```python
    if config.output_target == "folder":
        _write_to_folder(base_name, suffix, text, config)
        if not config.output_also_drive or suffix != DRIVE_PUBLISHED_SUFFIX:
            return
        ...
```

Leave the `output.target=drive` path untouched — it still uploads every artifact, and it is not what production runs. Update the docstring: `also_drive` publishes the `.stt` alone, every artifact still lands locally, and the local artifact remains what marks a recording processed.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_output.py tests/test_main.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/output.py tests/test_output.py
git commit -m "feat: publish only the .stt document to Drive"
```

---

### Task 6: A meta header on the Planfix comment

**Files:**
- Modify: `src/config.py` (`Config.planfix_meta_fields`, `_parse_planfix_meta_fields`)
- Modify: `src/main.py` (`_planfix_description`, `_send_planfix_comment`)
- Test: `tests/test_main.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: the meta document returned by `_write_call_documents` (Task 4).
- Produces: `Config.planfix_meta_fields: tuple[str, ...]` (default `("subject", "tags", "referral", "referral_note", "duration", "video_url")`) and `main._planfix_description(artifacts, preset_names, meta_document, meta_fields) -> str`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_main.py`:

```python
def test_planfix_description_puts_the_subject_and_tags_on_top():
    html = main._planfix_description(
        {"keypoints": "## Задачи\n- [ ] x"},
        ("keypoints",),
        {"subject": "Обсудили кейс", "tags": ["O-1"], "referral": "рекомендация"},
        ("subject", "tags", "referral"),
    )
    assert html.index("Обсудили кейс") < html.index("Задачи")
    assert "O-1" in html and "рекомендация" in html
    assert "\n" not in html


def test_planfix_description_skips_empty_and_unknown_fields():
    html = main._planfix_description(
        {"keypoints": "## Задачи"},
        ("keypoints",),
        {"subject": "s", "referral": "", "tags": []},
        ("subject", "referral", "tags", "not_a_field"),
    )
    assert "Реферал" not in html and "Теги" not in html


def test_planfix_description_renders_the_video_url_as_a_link():
    html = main._planfix_description(
        {}, (), {"video_url": "https://drive.google.com/file/d/X/view", "source_name": "Созвон.mp4"},
        ("video_url",),
    )
    assert '<a href="https://drive.google.com/file/d/X/view">' in html


def test_planfix_description_without_a_meta_document_is_unchanged():
    html = main._planfix_description({"keypoints": "## Задачи"}, ("keypoints",), None, ())
    assert "Задачи" in html
```

Append to `tests/test_config.py`:

```python
def test_planfix_meta_fields_has_a_default(tmp_path):
    assert _load_config(tmp_path, {}).planfix_meta_fields == (
        "subject", "tags", "referral", "referral_note", "duration", "video_url",
    )


def test_planfix_meta_fields_is_read_from_config(tmp_path):
    config = _load_config(tmp_path, {"planfix": {"meta_fields": ["subject"]}})
    assert config.planfix_meta_fields == ("subject",)


def test_planfix_meta_fields_can_be_emptied(tmp_path):
    assert _load_config(tmp_path, {"planfix": {"meta_fields": []}}).planfix_meta_fields == ()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_main.py tests/test_config.py -k planfix -v`
Expected: FAIL — `_planfix_description()` takes 2 arguments; no `planfix_meta_fields`.

- [ ] **Step 3: Add `planfix.meta_fields` to `src/config.py`**

```python
    # Which meta-document fields open the Planfix comment, in this order. The models and
    # internal ids are deliberately absent: the comment is read by managers, not
    # operators.
    planfix_meta_fields: tuple[str, ...] = (
        "subject", "tags", "referral", "referral_note", "duration", "video_url",
    )
```

Parse it beside `planfix.presets` with a `_parse_planfix_meta_fields` that mirrors `_parse_planfix_presets` but returns `()` for an explicit empty list (only `None` falls back to the default), and emit it in `to_dict`.

- [ ] **Step 4: Render the header in `src/main.py`**

```python
# Labels for the meta fields the Planfix comment may carry. Fixed in code, not config:
# a field's label is part of how the comment reads, not a deployment choice.
_PLANFIX_META_LABELS = {
    "subject": "",  # rendered as the heading, not as a labelled line
    "tags": "Теги",
    "referral": "Откуда узнал",
    "referral_note": "Подробности",
    "manager": "Менеджер",
    "client": "Клиент",
    "date": "Дата",
    "duration": "Длительность",
    "video_url": "Запись",
}


def _planfix_meta_lines(
    document: dict[str, object] | None, fields: tuple[str, ...]
) -> list[str]:
    """Render the selected meta fields as Markdown lines for the comment header.

    A field that is empty, unknown, or has no label is skipped silently, so shortening
    the configured list or a call with no referral never leaves a dangling label.
    """
    if not document:
        return []
    lines: list[str] = []
    for field in fields:
        if field not in _PLANFIX_META_LABELS:
            continue
        value = document.get(field)
        if isinstance(value, list):
            value = ", ".join(str(entry) for entry in value)
        text = str(value or "").strip()
        if not text:
            continue
        if field == "subject":
            lines.insert(0, f"**{text}**")
        elif field == "video_url":
            lines.append(f"[{_PLANFIX_META_LABELS[field]}]({text})")
        else:
            lines.append(f"**{_PLANFIX_META_LABELS[field]}:** {text}")
    return lines
```

Widen `_planfix_description` to take `meta_document` and `meta_fields`, prepend `"\n".join(_planfix_meta_lines(...))` as the first block before the preset sections, and keep the single `planfix_html.markdown_to_html` call over the assembled document so the result stays one line. Pass `meta_document` and `config.planfix_meta_fields` through `_send_planfix_comment` from `process_item`.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest && uv run ruff check`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/main.py src/config.py tests/
git commit -m "feat: open the Planfix comment with the call's subject, tags, and referral"
```

---

### Task 7: Retire `action-items` from the generated config, document the change

Note: `_default_config_dict` **already** writes `meta` with `enabled: true` and `depends_on: [transcript-cleanup]`. Enabling it on the running deployment is a config edit on the host, not a code change — it belongs to the manual-verification block, not to this task.

**Files:**
- Modify: `src/config.py` (`_default_config_dict`)
- Modify: `README.md`, `AGENTS.md`, `skills/gdstt-cli/SKILL.md`
- Test: `tests/test_config.py`, `tests/test_skill_docs.py`

**Interfaces:**
- Consumes: every earlier task.
- Produces: a generated config whose chain is `transcript-cleanup -> keypoints + meta`, seeded `referrals.allowed`, and `output.stt_presets` / `planfix.meta_fields` written out.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_default_chain_keeps_meta_and_drops_action_items():
    presets = _default_config_dict()["presets"]
    assert presets["meta"]["enabled"] is True
    assert presets["meta"]["depends_on"] == ["transcript-cleanup"]
    assert "action-items" not in presets


def test_default_config_seeds_the_referral_allow_list():
    assert "рекомендация" in _default_config_dict()["referrals"]["allowed"]


def test_default_config_writes_the_new_output_and_planfix_keys():
    data = _default_config_dict()
    assert data["output"]["stt_presets"] == ["keypoints"]
    assert data["planfix"]["meta_fields"] == [
        "subject", "tags", "referral", "referral_note", "duration", "video_url",
    ]
```

`_default_config_dict` takes only keyword arguments, all optional — `tests/test_config.py` already calls it bare (see the `webhook` default test).

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_config.py -k default -v`
Expected: FAIL — `action-items` present, `meta` absent, no `referrals` key.

- [ ] **Step 3: Update `_default_config_dict`**

Remove the `action-items` entry from the generated `presets` mapping; set `meta` to `enabled: true` with `depends_on: [transcript-cleanup]`; add the seeded `referrals.allowed` list from the spec; write `output.stt_presets` and `planfix.meta_fields` with their defaults. Leave `src/assets/prompts/action-items.md` and `data/prompts/action-items.md` in place — the preset is retired, not deleted, and re-enabling it must stay a config edit.

Update the two docstrings in `config.py` that spell the chain as `transcript-cleanup -> keypoints + action-items + meta` (lines ~996 and ~1332) to the new chain.

- [ ] **Step 4: Update the docs**

- `README.md`: the artifact list — one `.stt` in Drive, every artifact locally, `.meta.yml` beside them; the new config keys (`referrals.allowed`, `output.stt_presets`, `planfix.meta_fields`); `also_drive` now means "publish the `.stt`".
- `AGENTS.md`: same in the architecture/conventions section, one or two lines.
- `skills/gdstt-cli/SKILL.md`: the operator-facing description of what lands where. **The file must stay under 400 lines** and `tests/test_skill_docs.py` must keep passing — trim rather than append if it is near the limit.

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest && uv run ruff check`
Expected: PASS — including `tests/test_skill_docs.py`.

- [ ] **Step 6: Commit**

```bash
git add src/config.py README.md AGENTS.md skills/gdstt-cli/SKILL.md tests/
git commit -m "feat: enable the meta preset by default and retire action-items"
```

---

## Manual verification (after the branch is merged and deployed)

Not part of any task — run it on the deployment, with the operator watching.

1. Deploy to us1 by rsync **excluding `data/`** (it holds the config with secrets and the booking journal).
2. Edit `data/config.yml` on the host: enable the `meta` preset with `depends_on: [transcript-cleanup]`, disable `action-items`, add `referrals.allowed`, `output.stt_presets`, `planfix.meta_fields`. Back the file up first.
3. Rebuild and recreate the container, then confirm the next cycle reports **`pending=0`**. A non-zero count means the artifact bookkeeping shifted and the backlog is about to be re-transcribed — stop and roll back the config.
4. On the first recording processed after the change, check:
   - `data/results/<stem>.stt` exists, opens with `# `, and carries `## Задачи`, `## Мета`, `## Расшифровка` in that order;
   - `data/results/<stem>.meta.yml` parses as YAML and has every field of `FIELD_ORDER`;
   - `subject` and `tags` read sensibly, and `referral` is either a channel the client actually named or empty — **this is the check that matters most: the `meta` preset has never run on production data**;
   - Drive holds the `.stt` next to the mp4 and **no other new artifact**;
   - the Planfix comment opens with the subject line and its body is one line of HTML;
   - the mp4's `modifiedTime` still equals its `createdTime`.
5. If `referral` comes back populated on a call where nobody asked, treat it as a prompt bug, not a config one: the anti-invention rules in `meta.md` are the fix.
