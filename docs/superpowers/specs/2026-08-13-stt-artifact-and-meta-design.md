# One `.stt` artifact per call, and a meta document that describes it

**Status:** approved, not yet implemented
**Date:** 2026-08-13

## Problem

Two problems, one shape.

**Drive receives a pile of files.** Since `output.also_drive` landed, every
artifact is published next to the recording: `.txt`, `.transcript-cleanup.md`,
`.keypoints.md`, `.action-items.md`. Four files to open before anyone knows what
the call was about, and `action-items` restates the `## Задачи` section that
`keypoints` already produced.

**Nothing describes the call.** A `meta` preset producing a topic and tags was
written in July (commit 46e05d7e) and never enabled — `data/config.yml` lists
only `transcript-cleanup`, `keypoints`, and `action-items`. So no artifact
answers "what was this about", "which topics", or "where did this client come
from", and none of it can be aggregated across calls.

The referral question is already a live business process. Managers ask it
verbatim in the recordings — *"подскажите, пожалуйста, откуда о нас узнали?
Может быть, кто-то порекомендовал?"* — and one manager says out loud that she is
recording the answer: *"просто фиксируем рефералов"*. The answer is spoken on the
call and then lost.

## Solution

One human-facing file in Drive, one machine-facing file on disk.

### The `.stt` artifact

`<stem>.stt`, assembled after the preset DAG finishes, from parts that already
exist. No LLM call of its own.

~~~
# <recording name>

## Задачи
### Angelica Munkueva
- [ ] Отправить Марии анкету …
## Тезисы
- …
## Открытые вопросы
- …

## Мета
```yaml
subject: Состав кейса и коммерческое предложение для Марии
tags: [клиентская-консультация, коммерческое-предложение]
referral: рекомендация
…
```

## Расшифровка
[00:00:13] Angelica Munkueva: …
[00:00:53] Mariia: …
~~~

Keypoints, then meta, then transcript. Which presets fill the first block is
config-driven — `output.stt_presets`, defaulting to `["keypoints"]`, following
the `planfix.presets` pattern already in the codebase. The transcript is the
`transcript-cleanup` output; when that preset is disabled the raw `.txt` is used
instead, so the section is never empty.

The file is written with `mimeType: text/plain` so Drive previews it in the
browser despite the unfamiliar extension.

It is written locally on every run, whether or not it is also published to
Drive. It is not a preset artifact and takes no part in the
processed/pending bookkeeping — `has_txt` and the preset artifact set continue to
decide what still needs work, so a missing or deleted `.stt` can never put a
recording back in the transcription queue.

### The meta document

`<stem>.meta.yml`, local only. Four fields come from the model; the rest the code
already knows and must not pay a model to restate.

| Field | Source |
|---|---|
| `subject`, `tags`, `referral`, `referral_note` | the `meta` preset — one LLM call |
| `manager`, `manager_email` | the folder's entry in `folders:` |
| `client` | the non-manager participant resolved by `speaker_roles` |
| `date` | `meeting_time` parsed from the file name; Drive `createdTime` as fallback |
| `duration` | the last timestamp in the transcript |
| `language`, `stt_model`, `llm_model` | config |
| `planfix_task_id` | the `planfix_comment_task_id` appProperty |
| `video_id`, `video_url`, `source_name` | the Drive source file |
| `processed_at` | wall clock at write time |

`duration` reads the transcript's last timestamp rather than probing the media:
the transcript is already in hand, and a timestamp that trails the true end by a
few seconds of silence costs nothing here.

The existing preset returns `topic`; it becomes `subject` in the prompt, in
`parse_meta`, and in the webhook payload. No compatibility shim — the preset has
never run in production, so no artifact exists that spells it the old way.

### Referral

`referral` is a channel drawn from a config allow-list, `referrals.allowed`,
validated exactly as `tags.allowed` already is: a value outside the list is
dropped with a log line rather than written. `referral_note` is free text
carrying the client's own words.

The seed list comes from how clients actually answer in the existing
transcripts:

```yaml
referrals:
  allowed:
    - рекомендация
    - instagram
    - telegram
    - youtube
    - linkedin
    - поиск-google
    - реклама
    - сми-публикация
    - вебинар-мероприятие
```

Personal recommendation dominates the real answers and is deliberately not split
into sub-kinds (client / acquaintance / partner): callers rarely specify, so
sub-kinds would be the model guessing. Who recommended whom survives in
`referral_note`.

Two prompt rules exist to stop invention:

- Fill the fields only when **the client** states where they heard about the
  company. A manager asking the question with no answer is not a source.
- When the call never covers it, return empty strings, not a guess.

Empty fields stay present in the YAML. An operator reading a file must be able to
tell "nobody asked" from "this build doesn't produce the field".

### What reaches Drive

`output.also_drive` changes meaning: it publishes the `.stt` and nothing else.
Per-artifact Drive writes stop in folder mode.

Local writes are untouched — every artifact still lands in `data/results/`. This
is load-bearing: in folder mode the local artifact is what marks a recording
processed, and changing that would make the entire backlog look pending and
re-transcribe it for real money.

`output.target: drive` (not used in production) keeps its current per-artifact
behaviour.

### Planfix

The comment gains a meta header, driven by a config allow-list of field names:

```yaml
planfix:
  presets: [keypoints]
  meta_fields: [subject, tags, referral, referral_note, duration, video_url]
```

Excluded by decision: `manager`, `manager_email`, `client`, `date`, `language`,
`planfix_task_id`, `video_id`, `processed_at`, `stt_model`, `llm_model`.
`source_name` is excluded too — it becomes the label of the `video_url` link
rather than a line of its own.

Labels are a fixed mapping in code, not config. `subject` renders as a bold
heading, `tags` as one comma-joined line, `video_url` as an anchor, everything
else as `<b>Label:</b> value`. A field that is empty or unknown is skipped
silently, so a shortened list or a missing referral never leaves a dangling
label. Rendering goes through the existing `planfix_html` converter and stays on
one line, because Planfix rewrites every newline as `<br>`.

### action-items

Disabled: removed from `data/config.yml` and from the default chain in
`src/config.py`. Its output duplicates the `## Задачи` section of `keypoints`
almost verbatim, it is not sent to Planfix (`planfix.presets` names `keypoints`
only), and after this change it would not reach Drive either — so it costs one
OpenAI call per recording to produce a file nobody reads. The prompt asset stays
in the repository for anyone who wants to re-enable it.

## Components

| File | Responsibility |
|---|---|
| `src/assets/prompts/meta.md` | four-field output; `{{allowed_referrals}}` alongside `{{allowed_tags}}`; the two anti-invention rules |
| `src/meta.py` | parse `subject` / `tags` / `referral` / `referral_note`; validate referral against the allow-list |
| `src/meta_doc.py` (new) | merge model fields with deterministic ones and serialize the YAML document |
| `src/stt_document.py` (new) | assemble `.stt` from keypoints, meta, and transcript |
| `src/output.py` | `also_drive` publishes only the `.stt` |
| `src/planfix_html.py` | render the meta header from the selected fields |
| `src/config.py` | `referrals.allowed`, `output.stt_presets`, `planfix.meta_fields`; drop `action-items` from the default chain |
| `src/main.py` | wire the meta document and the `.stt` into the per-file flow |

## Error handling

Every failure degrades to a smaller artifact, never to a lost recording:

- A malformed model reply yields empty `subject` / `tags` / `referral`, as
  `parse_meta` already does. The `.stt` and the `.meta.yml` are still written.
- A referral outside the allow-list is dropped; `referral_note` is kept only when
  `referral` survived, so no orphan quote implies a channel that was rejected.
- A missing deterministic field (no booking, unparseable name) is written as an
  empty value rather than omitted.
- A Drive upload failure is logged and swallowed, as `also_drive` already does.
  The local copy is authoritative.

## Testing

Unit tests, all external services mocked, no network:

- `parse_meta` across the four fields: well-formed, fenced, missing block,
  referral outside the allow-list, note without a channel.
- Document assembly: field order, empty fields present, YAML round-trips.
- `.stt` assembly: section order (keypoints → meta → transcript), fallback to the
  raw `.txt`, a disabled keypoints preset.
- `also_drive` publishes the `.stt` and nothing else; folder writes are unchanged.
- Planfix header: field selection, unknown and empty fields skipped, output stays
  a single line.
- Config: `referrals.allowed`, `output.stt_presets`, `planfix.meta_fields`
  defaults and validation.

Manual verification after deploy: read the first two or three `subject` / `tags`
/ `referral` values on real calls. The `meta` preset has never run against
production data, so its output quality is unverified even though its parser is
tested.

## Out of scope

- **Backfill.** The ~1277 existing recordings keep what they have. `.stt` and
  meta start with recordings processed after the release.
- **Cleaning up already-published artifacts.** The repaired Angelica/Mels
  recording has three `.md` files in Drive from the `also_drive` rollout. They
  stay unless deleted by hand.

## Dependency

`output.also_drive` lives on the unmerged branch `fix-speaker-roles-and-planfix-html`
(PR #18). Production runs that code; `main` does not. This work branches off
PR #18, not off `main`.
