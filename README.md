# google-drive-video-stt

Monitors Google Drive folders for new MP4 files, optionally extracts MP3 audio
with ffmpeg, transcribes them with Deepgram Nova-3 (speaker diarization), and
writes a speaker-named transcript — plus an optional Keypoints document — back to
Google Drive or to a local folder. Designed as a headless preprocessing step for
speech-to-text pipelines.

## Features

- Polls one or more Google Drive folders on a configurable interval, each mapped to
  an employee (`folders: [{folder_id, name, email}]`) so every file knows whose
  folder it came from
- Idempotent: skips already-created artifacts, linked by source file id metadata
  when available and by sibling name as a legacy fallback
- Audio extraction via ffmpeg (`libmp3lame`, configurable bitrate)
- Deepgram Nova-3 transcription with speaker diarization, full-file (no chunking)
- Optional Telegram error notifications (success is silent)
- Operator CLI (`gdstt`) wrapping auth, the polling loop, on-demand processing,
  newest-file processing, local-file transcription, deterministic speaker
  relabeling, and folder-state inspection
- Local post-processing that maps diarized `Speaker N` labels to the interlocutor
  names parsed from the file name
- Config-defined DAG of OpenAI presets (each writes its own sibling artifact, e.g.
  the built-in Keypoints pass: `## Задачи` / `## Тезисы` / `## Открытые вопросы`),
  with independent presets run in parallel via the OpenAI Responses API
- Built-in `meta` preset extracting whatever `meta.entities` declares in
  `config.yml` — seven entities by default: subject, tags, referral +
  referral note, and case/other-deadlines + target-filing fields
- One combined `.stt` document per call (keypoints + meta + transcript) and a
  `<stem>.meta.yml` alongside every other artifact
- Optional fire-and-forget completion webhook posting `{file, employee, transcript,
  artifacts}` once per processed file
- Output to Google Drive siblings or to a local folder (`output.target`)
- Sibling `.mp3`/`.txt` names preserve the full Drive file name, including `/`
  characters
- Explicit speaker names can be stored on the Drive MP4 when the filename is not
  enough for reliable speaker mapping
- Docker-first deployment, all mutable state in `./data`

## Requirements

- Python 3.11+ and [`uv`](https://github.com/astral-sh/uv) for local development
- `ffmpeg` available on `PATH` for local runs (already included in the Docker image)
- Google Cloud project with the Drive API enabled and OAuth client metadata
  imported into `config.yml` or supplied in file mode
- A Deepgram API key for transcription (`stt.provider: deepgram`)
- Optional: an OpenAI API key when any OpenAI preset is enabled (e.g. `keypoints`)
- Optional: a Telegram bot token + chat ID for error notifications
- Optional: an HTTP(S) or SOCKS proxy via `PROXY_URL`; SOCKS support is included
  through the `requests[socks]` dependency

## Setup

For an operator-style local install, install the global CLI first:

```bash
uv tool install --editable .
uv tool update-shell
```

For development in this checkout, install the editable environment too:

```bash
uv sync --extra dev
```

Configuration lives in a single `config.yml`. For a fresh install, generate
one from the packaged defaults — the full chain `transcript-cleanup -> keypoints +
meta` is enabled out of the box with `openai.batch: true`. `action-items` ships
disabled: its output duplicates `keypoints`' `## Задачи` section, so re-enabling it
is a config edit, not a code change:

```bash
gdstt config init      # writes config.yml + prompts/ to the resolved target (see below)
gdstt config path      # print the resolved config.yml path
```

The active config is always `<GDSTT_HOME>/config.yml`. When `GDSTT_HOME` is unset
the home defaults to `./data`, so a repo or VPS checkout uses `./data/config.yml`
with no hidden OS config paths:

```text
--config PATH                 # one-shot file override (tests/diagnostics, not persisted)
GDSTT_HOME/config.yml         # persistent instance directory
./data/config.yml             # default when GDSTT_HOME is unset
```

Point at a custom instance directory by exporting `GDSTT_HOME`:

```bash
export GDSTT_HOME=/srv/gdstt
gdstt config init --force     # writes /srv/gdstt/config.yml
```

The packaged prompt assets (`keypoints.md`, `transcript-cleanup.md`,
`action-items.md`, `meta.md`) are copied into `<GDSTT_HOME>/prompts/` beside the config, and
each preset's `prompt_file` points at them. There is no hidden runtime prompt in
Python — the prompt text is owned by these `.md` files.

There is no auto-generation and no migration: a missing or empty config is a clear
setup error that points you at `gdstt config init`. Author `config.yml` by hand
(see [Configuration](#configuration)) or generate it, then fill in your keys. Pass
`gdstt --config PATH ...` for a one-shot override against a specific file.

After Google credentials are imported inline (or file mode is configured; see
below), authenticate once and verify access with the safe operator flow:

```bash
gdstt auth
gdstt doctor --drive
gdstt list
gdstt process <file-id> --dry-run
```

## Google Drive setup

This app authenticates with a single Google OAuth user credential covering Drive.

Google auth is **config-owned and inline-first**. By default the OAuth client JSON
lives inline in the config under `google.credentials` and the saved token under
`google.token`, both inside `config.yml`. Import the downloaded client with
`gdstt auth import-credentials <path>`, then run `gdstt auth` to write the token.

File mode is an explicit opt-in: `gdstt auth use-files --credentials-file PATH
[--token-file PATH]` sets `google.credentials_file`/`google.token_file` and clears
the inline copies. When neither inline mappings nor file pointers are set, the
loader falls back to `data/credentials.json` and `data/token.json` for
back-compatibility (the legacy layout used by the examples below).

Inline tokens, `client_secret`, and `refresh_token` are secrets: in a shared
(synced) config, keep them in file mode (or a local, non-synced part of the
config) rather than inline where others can read the config.

### Option A — gcloud / Application Default Credentials

Install and initialize the Google Cloud CLI first:

```bash
gcloud init
gcloud auth login --enable-gdrive-access
```

Use an existing project or create a dedicated one:

```bash
gcloud config set project <project-id>
# or
gcloud projects create <project-id> \
  --name="google-drive-video-stt" \
  --set-as-default
```

Enable the Drive API:

```bash
gcloud services enable drive.googleapis.com
```

This app uses an installed-app OAuth client JSON at `data/credentials.json`.
If you have initialized gcloud Application Default Credentials, create that file
from the local gcloud client metadata:

```powershell
gcloud auth application-default login `
  --scopes=https://www.googleapis.com/auth/drive

New-Item -ItemType Directory -Force data | Out-Null
$adcPath = Join-Path $env:APPDATA "gcloud\application_default_credentials.json"
$adc = Get-Content $adcPath | ConvertFrom-Json
$client = @{
  installed = @{
    client_id = $adc.client_id
    client_secret = $adc.client_secret
    auth_uri = "https://accounts.google.com/o/oauth2/auth"
    token_uri = "https://oauth2.googleapis.com/token"
    redirect_uris = @("http://localhost")
  }
}
$client | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 data\credentials.json
```

The generated `data/credentials.json` contains only the OAuth client metadata.
Do not copy the ADC `refresh_token` into it. The app creates its own
`data/token.json` when you run `gdstt auth`.

### Option B — Google Cloud Console OAuth client

If the ADC flow is not available in your environment, use the Console fallback:
APIs & Services -> Credentials -> Create Credentials -> OAuth client ID ->
Desktop app, then save the downloaded JSON as `data/credentials.json`. If Google
asks for an OAuth consent screen first, configure it in external test mode and add
your own Google account as a test user. The app requests the
`https://www.googleapis.com/auth/drive` scope.

### Authenticating

```bash
gdstt auth
gdstt auth --manual
gdstt auth "http://localhost/?code=4/abc123&scope=..."
```

### Finding folder ids

To create a Drive folder from the command line, use the Drive API with the gcloud
access token:

```powershell
$token = gcloud auth print-access-token
$body = @{
  name = "google-drive-video-stt"
  mimeType = "application/vnd.google-apps.folder"
} | ConvertTo-Json
Invoke-RestMethod `
  -Method Post `
  -Uri "https://www.googleapis.com/drive/v3/files" `
  -Headers @{ Authorization = "Bearer $token" } `
  -ContentType "application/json" `
  -Body $body
```

Copy the returned `id` into `config.yml` as an entry under `folders:` (see
[Configuration](#configuration)). For an existing Drive folder, the folder id is
the last path segment in the browser URL:
`https://drive.google.com/drive/folders/<folder-id>`.

## Configuration

All configuration lives in the active `config.yml` (`<GDSTT_HOME>/config.yml`, or
`./data/config.yml` when `GDSTT_HOME` is unset). It is grouped under `output`, `stt`
(with a nested `deepgram` block), `openai`, `tags`, and `webhook`, plus a top-level
`presets` map.
There is no auto-generation: create the file with `gdstt config init` (or by hand),
then edit it. Resolve a one-shot non-default file with `gdstt --config PATH ...`.

```yaml
folders:                 # one entry per employee folder
  - folder_id: abc
    name: Олег Иванов    # optional; sent in the completion webhook
    email: oleg@example.com   # optional
  - folder_id: def
poll_interval: 600
bitrate: 96k
data_dir: .
proxy_url: ""
run:
  enabled: true          # gdstt stop/start toggle this; persists across restarts
notifications:
  telegram:
    bot_token: ""        # optional; errors are posted only when both are set
    chat_id: ""
output:
  target: drive          # drive | folder
  dir: null              # required when target=folder
  also_drive: false      # folder mode only: publish the .stt document as well
  stt_presets: [keypoints]   # preset sections opened inside the .stt document
stt:
  provider: deepgram     # "" / disabled => MP3-only
  language: ru
  postprocess: true
  drive_mp3_artifact: false
  deepgram:
    api_key: "..."
    model: nova-3
    diarize_model: latest
    audio_source: m4a_copy
    txt_formatter: word_speaker
    keyterms_enabled: true
    keyterms_file: deepgram-keyterms-example.txt
openai:
  api_key: "..."
  model: gpt-5.4-mini    # global default model for presets
  batch: false           # global default batch mode for presets
  batch_wait: true       # wait synchronously for batch results (default)
  max_parallel: 4        # cap on presets run concurrently
tags:
  allowed: [клиентская-консультация, O-1, EB-1]   # the only tags `meta` may pick
referrals:
  allowed: [рекомендация, instagram, telegram, youtube, linkedin]   # the only channels `meta` may pick for `referral`
webhook:
  url: ""                # empty => no completion webhook is sent
  token: ""              # optional; sent as "Authorization: Bearer <token>"
google: {}               # inline-first auth; empty => data_dir fallback
planfix:
  meta_fields: [subject, tags, referral, referral_note, case_deadline, deadlines, target_filing, duration, video_url]   # header fields on the Planfix comment
  task_url: ""           # e.g. https://<account>.planfix.com/task/<task-id>
presets:
  transcript-cleanup:
    prompt_file: prompts/transcript-cleanup.md   # packaged asset, copied beside the config
    batch: false                                 # keep an upstream stage off batch (see below)
  keypoints:
    depends_on: [transcript-cleanup]   # overrides the built-in keypoints preset
  meta:
    depends_on: [transcript-cleanup]   # overrides the built-in meta preset
```

Presets define the OpenAI post-processing DAG (see
[Preset DAG](#preset-dag-keypoints-and-beyond)). A preset's prompt comes from
`instructions` (inline) **or** `prompt_file`; supplying neither is an error. Each
setting below maps to a `config.yml` key; no `.env` file or legacy environment
variable is read at runtime:

| Setting | Default | Purpose |
| --- | --- | --- |
| `folders` | (required) | Google Drive folders to monitor, one entry per employee: `{folder_id, name, email}`. `name`/`email` are optional and default to empty; they identify the employee in the completion webhook |
| `poll_interval` | `600` | Seconds between poll cycles |
| `bitrate` | `96k` | MP3 audio bitrate passed to ffmpeg |
| `stt.drive_mp3_artifact` | auto | Upload an MP3 artifact to Drive. Defaults to `false` for `stt.deepgram.audio_source=m4a_copy`; `true` otherwise |
| `notifications.telegram.bot_token` | (empty) | Read at runtime from `config.yml`. If set with chat ID, errors are posted to Telegram |
| `notifications.telegram.chat_id` | (empty) | Read at runtime from `config.yml`. Telegram chat to receive error notifications |
| `data_dir` | `.` | Base directory for credentials/token files and other instance state |
| `proxy_url` | (empty) | Optional `http`/`https`/`socks5` proxy for Telegram, Deepgram, and OpenAI |
| `stt.provider` | `deepgram` | `deepgram` by default. Set `disabled` (or empty) to skip transcription and only manage MP3 artifacts |
| `stt.language` | (empty) | Language hint. `deepgram`: empty defaults to `ru` |
| `stt.postprocess` | `true` | Clean the transcript and map diarized `Speaker N` labels to the interlocutor names parsed from the file name, merging spurious extra speakers |
| `output.target` | `drive` | Where artifacts are written: `drive` (sibling files) or `folder` (local `output.dir`) |
| `output.dir` | — | Required when `output.target=folder`; local directory for transcript/keypoints files |
| `output.also_drive` | `false` | Folder mode only: also publish the combined `.stt` document (keypoints + meta + transcript) as a Drive sibling. Every artifact still lands in `output.dir` regardless; this only adds the one Drive copy |
| `output.stt_presets` | `[keypoints]` | Which preset sections (in order) open the `.stt` document, between the title and the meta block |
| `openai.api_key` | — | Required when any OpenAI preset is enabled |
| `openai.model` | `gpt-5.4-mini` | Global default model for presets |
| `openai.batch` | `false` | Global default batch mode. Batch API is ~50% cheaper but slower (not higher quality); batch on an upstream preset delays its downstream presets |
| `openai.batch_wait` | `true` | Wait synchronously for batch results (the async path is unsupported) |
| `openai.max_parallel` | `4` | Max number of independent presets run concurrently |
| `stt.deepgram.api_key` | — | Required when `stt.provider=deepgram` unless `stt.deepgram.api_key_file` is set |
| `stt.deepgram.api_key_file` | — | Optional file containing a raw Deepgram token or JSON with `api_key`, `deepgram_api_key`, or `DEEPGRAM_API_KEY` |
| `stt.deepgram.model` | `nova-3` | Deepgram model name |
| `stt.deepgram.diarize_model` | `latest` | Deepgram diarization model: `latest` or `v1` |
| `stt.deepgram.audio_source` | `m4a_copy` | Audio sent to Deepgram: `m4a_copy`, `mp3_96k`, or `mp3_192k` |
| `stt.deepgram.txt_formatter` | `word_speaker` | Deepgram TXT formatter: `word_speaker` or `utterance` |
| `stt.deepgram.keyterms_enabled` | `true` | Enables Nova-3 keyterm prompting |
| `stt.deepgram.keyterms_file` | `deepgram-keyterms-example.txt` | Keyterms file, one term per line, max 100. Resolved beside `config.yml`; point it at your own list (e.g. `data/deepgram-keyterms.txt`). A missing default is a warning; a missing path you set explicitly is an error |
| `tags.allowed` | (empty) | **Deprecated.** Read only while `meta.entities` is absent, wiring the built-in `tags` entity's allow-list. Move the values into that entity's `allowed` list and delete this key |
| `referrals.allowed` | seeded list (see below) | **Deprecated.** Read only while `meta.entities` is absent, wiring the built-in `referral` entity's allow-list. Move the values into that entity's `allowed` list and delete this key |
| `meta.entities` | four built-in entities (see [Conversation meta](#conversation-meta-config-driven-entities)) | The `meta` preset's field list: one mapping per entity (`name`, `prompt`, `type`, `multiple`, `allowed`, `label`, `requires`) |
| `webhook.url` | (empty) | Completion webhook endpoint; must be an absolute `http://` or `https://` URL. Empty disables it; a failure never fails the file |
| `webhook.token` | (empty) | Optional bearer token sent as `Authorization: Bearer <token>` |
| `planfix.meta_fields` | `[subject, tags, referral, referral_note, case_deadline, deadlines, target_filing, duration, video_url]` | Which meta fields open the Planfix comment, and in what order |
| `planfix.task_url` | (empty) | Where a task lives in the web UI, e.g. `https://<account>.planfix.com/task/<task-id>`. Fills `planfix_task_url` in the meta document and the link column of `gdstt planfix sent`. Empty leaves both blank |

`tags.allowed` and `referrals.allowed` are read for one more version when
`meta.entities` is absent, so an existing config keeps working untouched — no CLI
command rewrites the whole file for you. Migrating is manual and not urgent: add a
`meta.entities` block by hand, moving each list into its entity's `allowed`, then
delete the old `tags:`/`referrals:` sections. Once `meta.entities` is declared,
the old keys are ignored and logged at startup.

## Speech-to-text

With `stt.provider=deepgram` (the default) each pending recording is transcribed
through Deepgram and a sibling `<basename>.txt` is written next to the MP4 (or into
`output.dir` when `output.target=folder`). Set `stt.provider=disabled` to skip
transcription entirely and only manage the optional MP3 artifact.

### Deepgram Nova-3 (diarization)

The `deepgram` provider submits a full-file audio copy to Deepgram's pre-recorded
`/v1/listen` endpoint using Nova-3, Russian language, and `diarize_model=latest`
by default. It is the recommended provider for Russian speaker diarization. It
does not require the Deepgram SDK; the provider uses the existing `requests` HTTP
client dependency.

Setup:

1. Create a Deepgram API key.
2. Set `stt.provider: deepgram` and either `stt.deepgram.api_key` or
   `stt.deepgram.api_key_file` in `config.yml`.

`stt.deepgram.api_key_file` may contain either the raw token or JSON with one of these
fields: `api_key`, `deepgram_api_key`, or `DEEPGRAM_API_KEY`. The API key is never
logged. After each successful Deepgram transcription, the service logs the request
id, duration, and best-effort request cost in USD when Deepgram's usage API has
recorded it.

The production defaults are:

```
stt:
  language: ru
  deepgram:
    model: nova-3
    diarize_model: latest
    audio_source: m4a_copy  # m4a_copy, mp3_96k, or mp3_192k
    txt_formatter: word_speaker
    keyterms_enabled: true
    keyterms_file: deepgram-keyterms-example.txt
```

`m4a_copy` extracts a temporary AAC/M4A audio copy from the source MP4 for
Deepgram without re-encoding. Use `mp3_96k` or `mp3_192k` to send a temporary MP3
instead. With the Deepgram `m4a_copy` default, no extra Drive MP3 is uploaded
unless `stt.drive_mp3_artifact=true` is set. If an MP3 already exists but TXT is
missing, Deepgram downloads the MP4 again so it can use the selected high-quality
audio source.

`word_speaker` is a Deepgram-only TXT formatter. It uses `utterances` for readable
timing, but splits a line when `words[].speaker` changes inside the utterance.
Set `stt.deepgram.txt_formatter=utterance` to use the older utterance-level formatter.

Keyterms are read from `stt.deepgram.keyterms_file`, one term per line. Blank lines and
lines beginning with `#` are ignored. At most 100 keyterms are allowed, and they
are sent only when `stt.deepgram.model=nova-3`.

`gdstt config init` copies a `deepgram-keyterms-example.txt` beside the config as a
starting point. Its sample terms are commented out, so keyterm prompting stays
inert until you supply your own — an uncurated sample left active would bias every
transcript toward terms you never chose. Keep your real keyterms machine-local and
out of git (the repo gitignores `data/deepgram-keyterms.txt`), then point
`stt.deepgram.keyterms_file` at it.

Sample output:

```
[00:00:00] Speaker 1: Привет, коллеги.
[00:00:05] Speaker 2: Добрый день.
```

Deepgram sync pre-recorded requests have a processing-time limit: Nova/Base/Enhanced
requests that process for more than 10 minutes may return `504 Gateway Timeout`.
Callback mode would avoid that for long files, but it requires a public callback
endpoint and is intentionally not implemented. In practice this limit has not been
hit: a 1.5-hour (~90 minute) recording has been transcribed through the sync
endpoint without triggering a `504`, so the documented caveat above is a worst-case
warning rather than a hard ceiling observed in real use.

### Transcript post-processing

By default (`STT_POSTPROCESS=true`) the transcript is post-processed before it is
written rather than stored as raw STT output. The local post-processor
(`src/postprocess.py`) normalizes whitespace, parses the interlocutor names from
the recording file name (e.g. `Alice and Bob - 2026/05/28 ... .mp4` → `Alice`,
`Bob`), maps them onto the diarized `Speaker N` labels by order of appearance, and
merges any extra (spurious) diarization speakers into the real one whose turns they
continue.

When a sibling `.txt` already exists, normal polling skips it to avoid spending STT
credits repeatedly. Use `gdstt process <file-id> --reprocess-txt` when you
intentionally want to run STT again and overwrite the existing `.txt` in place. New
`.txt` and `.mp3` artifacts are tagged with the source MP4 id, so future source
renames do not break artifact detection.

### Preset DAG (Keypoints and beyond)

After the transcript is produced, the service runs the **enabled presets** defined
in `data/config.yml` (`src/presets.py` + `src/preset_pipeline.py`, OpenAI Responses
API). Each preset is one OpenAI pass with its own `instructions`; it feeds on the
concatenated outputs of its `depends_on` presets, or the raw transcript when it has
none, and writes its own sibling artifact `<base><artifact_suffix>` (default
`.<name>.md`) tagged `artifact_type=<name>`. Independent presets run in parallel up
to `openai.max_parallel`, and each preset may set its own `model`/`batch`, falling
back to the `openai` defaults.

Two built-in presets ship with the code. `keypoints` produces a
`<base>.keypoints.md` document containing `## Задачи` (grouped by
`### Ответственный`), `## Тезисы`, and `## Открытые вопросы` in plain text.
`meta` produces a `<base>.meta.md` YAML-frontmatter artifact describing the call
(see [Conversation meta](#conversation-meta-config-driven-entities)). A
generated config enables the full chain `transcript-cleanup -> keypoints + meta`
out of the box (with `openai.batch: true`); `transcript-cleanup` is written above
its dependents. `action-items` ships disabled — its output duplicates `keypoints`'
`## Задачи` section — but its prompt asset stays packaged, so turning it back on is
a `presets:` edit, not a code change. Config presets override built-ins
field-by-field, add new presets, and disable a built-in with `enabled: false`.
Running any enabled preset requires `openai.api_key` and honors `proxy_url`.

**Prompt source priority.** Each preset's instructions are resolved as
`instructions` (inline text in the YAML) > `prompt_file` > error. A `prompt_file`
is resolved in order: the path as written, then relative to the config file's
directory, then the packaged asset by base name. A `prompt_file` that is missing,
unreadable, or empty is an error, and a preset that defines neither `instructions`
nor `prompt_file` is an error. The packaged prompt assets (`keypoints.md`,
`transcript-cleanup.md`, `action-items.md`, `meta.md`) are copied into
`<config_dir>/prompts/` beside the config — there is no hidden runtime prompt in
Python.

A prompt may contain the `{{entities}}` placeholder; it is rendered at config load
time from `meta.entities` into two things: a YAML response template (one line per
entity, list syntax for `multiple`) and a rules block per entity carrying its
`prompt`, its `allowed` list when `enum`, a "return a list" note when `multiple`,
and a "leave empty when *X* is empty" note when `requires` is set. The packaged
`meta.md` uses it; the placeholder works in inline `instructions` and in any
`prompt_file` too.

**Adding a DAG stage (no Python changes).** Add an entry under `presets:` with an
`instructions` or `prompt_file`, a `depends_on` chain, and optionally
`model`/`batch`/`artifact_suffix`. To insert a stage in the middle, point the
`depends_on` of the downstream presets at the new stage. Everything is config —
editing `config.yml` is the only step.

**Conflict rules (rejected at load time).** Duplicate YAML keys (including two
presets sharing a name) are rejected; enabled presets must use unique
`artifact_suffix` values so their sibling artifacts don't collide on disk; a
`prompt_file` must resolve to a readable, non-empty file; and `depends_on` on an
unknown/disabled preset or a cycle in the DAG is rejected.

**Batch is cheaper and slower, not higher quality.** Setting `openai.batch: true`
(globally or per preset) submits the pass through the OpenAI Batch API (~50%
cheaper at the cost of higher latency); it does not change output quality. A
recommended layout is global `openai.batch: true` with
`transcript-cleanup.batch: false`, because batch on an upstream stage delays every
downstream stage (they wait for its artifact). `openai.batch_wait: true` is the
default (the service waits synchronously for batch results; the async path is not
supported). Per-preset `model`/`batch`/`batch_wait` override the global
`openai.*` defaults.

Idempotency is per preset: `list_folder_state` reports an `artifact_ids` map keyed
by `artifact_type`, so only the presets still missing an artifact are produced on a
later cycle. Existing `.keypoints.md` files map onto the `keypoints` preset with no
migration. `gdstt doctor` prints the resolved config path and the resolved preset
DAG (names, dependencies, enabled state).

For an agent-driven path (reason about speakers, confirm the mapping, relabel
deterministically, and write the Keypoints document by hand), see
[`skills/gdstt-cli/SKILL.md`](skills/gdstt-cli/SKILL.md).

### Conversation meta (config-driven entities)

The built-in `meta` preset extracts whatever `meta.entities` declares in
`config.yml`, in one OpenAI pass, and writes a `<base>.meta.md` artifact holding
nothing but YAML frontmatter — one key per configured entity:

```markdown
---
subject: Консультация по визе O-1 для research-профиля
tags: [клиентская-консультация, O-1, рекомендательные-письма]
referral: рекомендация
referral_note: Посоветовала знакомая из Нью-Йорка
case_deadline: к концу лета
deadlines: []
target_filing: O-1 осенью
---
```

Each entity under `meta.entities` is a mapping with these keys:

| Key | Required | Meaning |
| --- | --- | --- |
| `name` | yes | YAML key in the artifact, the meta document, and the webhook payload |
| `prompt` | yes | the instruction handed to the model for this entity |
| `type` | no, default `text` | `text` = free string in the transcript's own words; `enum` = a value copied verbatim from `allowed` |
| `multiple` | no, default `false` | `true` yields a list instead of a scalar |
| `allowed` | `enum` only | the values the model may pick from |
| `label` | no, default `name` | the label in the Planfix comment header; `''` means "render as the bold heading, not a labelled line" |
| `requires` | no | name of another entity; this one is emptied when that one came back empty |

The shipped default is seven entities: `subject` (the heading, `label: ''`),
`tags` and `referral` (`enum`, values from their `allowed` lists),
`referral_note` (`requires: referral`), and three fields added on this branch —
`case_deadline`, `deadlines` (`multiple: true`), and `target_filing` — kept in
the client's own words, never normalized to an ISO date, and empty when the call
never covered them.

The model is asked to constrain itself, and `src/meta.py` enforces it
independently: `parse_meta` intersects an `enum` entity's reply with its
`allowed` list, and empties any entity whose `requires` target came back empty.
A missing or malformed frontmatter block degrades to every entity empty rather
than failing the file — a bad LLM reply must never cost you a processed
recording.

Tune an entity's vocabulary by editing its `allowed` list; add, remove, or
reword a question by editing `meta.entities`. No code change is needed either
way.

Every processed recording also gets a `<stem>.meta.yml` merging every entity
value with facts the code already knows — manager, client, date, duration, Planfix
task id, models used — and a combined `<stem>.stt` document (keypoints, then this
meta block, then the transcript). Where they land depends on `output.target`: the
default `drive` target uploads both as ordinary Drive siblings, same as every other
artifact; `folder` mode keeps every artifact local, including `.meta.yml`, and
`output.also_drive: true` additionally publishes **only** the `.stt` to Drive —
`.meta.yml` itself is never published on its own.

`config init` enables `meta` for you, with the seven entities above. Unlike
`keypoints`, it is **opt-in** for configs written before it existed — otherwise
upgrading would silently add an OpenAI pass to every deployment, including
STT-only ones that carry no `openai.api_key`. To turn it on in an existing
`config.yml`, add it under `presets:` with the dependency wired, and declare
`meta.entities` (or leave it absent, which falls back to the four built-in
entities wired to the deprecated `tags.allowed`/`referrals.allowed`):

```yaml
presets:
  meta:
    enabled: true
    depends_on: [transcript-cleanup]
    prompt_file: prompts/meta.md
```

Without `depends_on`, `meta` reads the raw diarized transcript instead of the
cleaned one.

### Completion webhook

When `webhook.url` is set, the service POSTs a JSON body per file, on the
success path only, after every artifact has been written:

```json
{
  "file": {"id": "1a2b", "name": "Ольга х ExpertizeMe - ....mp4", "folder_id": "1D0E"},
  "employee": {"name": "Олег Иванов", "email": "oleg@example.com"},
  "transcript": "Ольга: ...",
  "artifacts": {
    "meta": {
      "subject": "...",
      "tags": ["клиентская-консультация"],
      "referral": "рекомендация",
      "referral_note": "Посоветовала знакомая",
      "case_deadline": "к концу лета",
      "deadlines": [],
      "target_filing": "O-1 осенью"
    },
    "keypoints": "## Задачи\n..."
  }
}
```

The employee comes from the `folders` entry the file was found in; a folder with no
`name`/`email` sends empty strings rather than omitting the key. Every enabled
preset's output appears under `artifacts` keyed by preset name — raw text, except
`meta`, which is parsed into one key per configured entity (the built-in
`{subject, tags, referral, referral_note}` when `meta.entities` is absent, or
whatever else `meta.entities` names). Adding a preset — or an entity — to
`config.yml` therefore extends the payload with no code change, so a consumer must
tolerate new keys appearing.

A file normally notifies once, when it is transcribed. It notifies **again** if it
is later re-selected and produces preset output — after you add a preset to
`config.yml`, or run `gdstt reprocess`. The repeat POST carries the file's full
current artifact set, not just the new preset, so the latest delivery always wins.
Receivers that must not double-record should treat `file.id` as the dedupe key and
upsert on it.

Set `webhook.token` to have the request carry `Authorization: Bearer <token>`.
Delivery is **fire-and-forget**: an unreachable endpoint, a 4xx/5xx, or a timeout
(10s) is logged as a warning and the file still counts as processed. There is no
retry. Failure logs record only the exception type, so the token and transcript
never reach the log.

The payload carries PII — the employee's email and the full transcript — so point
`webhook.url` at an HTTPS endpoint and protect it with a token. A non-loopback
`http://` URL logs a warning at startup: the token and the whole payload would
cross the network in clear text. It is a warning rather than a hard error, since a
plaintext receiver on a trusted internal network is a valid choice.

### Output destination

`output.target` controls where the transcript and keypoints files land. With the
default `drive`, they are written as siblings of the source MP4 and uploaded (or
updated in place when one already exists). With `folder`, the service writes
`<output_dir>/<base_name>.txt` (and `.keypoints.md`), creating `output.dir` if it
is missing. `output.dir` is required when `output.target=folder`.

In `folder` mode, every artifact — including `<base>.meta.yml` and the combined
`<base>.stt` — always lands in `output.dir` regardless of `output.also_drive`; the
local artifact set is what marks a recording processed. Setting
`output.also_drive: true` additionally publishes **only** the `.stt` document as a
Drive sibling — one file per recording next to the source MP4, not a copy of every
artifact, and never `.meta.yml` itself.

## Usage

Local run (after `src/auth` has produced a token):

```bash
uv run python -m src.main
```

The process loops forever, sleeping `POLL_INTERVAL` seconds between cycles.

### CLI

`uv sync` installs a `gdstt` console script that wraps every operation
(equivalently `uv run python -m src.cli`). All commands read configuration from
`<GDSTT_HOME>/config.yml` (default `./data/config.yml`) via `load_config()`; the
config must already exist (`gdstt config init`). Pass `gdstt --config PATH ...` for
a one-shot override against a non-default file.

Safe operator flow: `gdstt doctor` -> `gdstt list` -> `gdstt process <file-id> --dry-run`
-> `gdstt process <file-id>`. Move to `run-once` or continuous `run` only after
that single-file path looks correct.

```bash
gdstt auth [response_url]   # one-time interactive OAuth → google.token (or token.json in file mode)
gdstt auth import-credentials <path>   # store an OAuth client JSON inline (google.credentials)
gdstt auth use-files --credentials-file PATH [--token-file PATH]   # switch to file mode
gdstt doctor [--drive]      # check Drive/OAuth configuration without changing it
gdstt latest [--folder ID] [--dry-run] [--max-size SIZE] [--confirm-large]   # process the newest mp4 in a folder
gdstt run                   # continuous polling; can spend STT credits across all pending configured folders
gdstt stop                  # pause the loop (sets run.enabled=false; stays paused across restarts, no auto-resume)
gdstt start                 # resume a paused loop (sets run.enabled=true)
gdstt run-once [--dry-run] [--max-size SIZE] [--confirm-large]   # single cycle; use --dry-run first
gdstt process <id> [--folder] [--reprocess-txt] [--dry-run] [--max-size SIZE] [--confirm-large]   # single target or folder; use --dry-run first
gdstt reprocess <id> [STAGES] [--folder] [--dry-run] [--max-size SIZE] [--confirm-large]   # force-rerun chain stages by number (0=transcript, 1..N=presets; see doctor)
gdstt speakers set <file-id> "Alice" "Bob"   # store explicit speaker names on an MP4
gdstt transcribe <audio> [-o out.txt]   # STT-only on a local file; prints to stdout by default
gdstt relabel --in SRC --out OUT --map MAP.json [--no-header]   # deterministic local speaker relabeling
gdstt list [--folder ID]   # show sibling mp3/txt state without doing work (alias: status)
gdstt config init [--data-dir DIR] [--output-dir DIR] [--prompt-dir DIR] [--force]   # fresh config.yml + prompts/ from packaged defaults
gdstt config path           # print the resolved config.yml path (no secrets required)
gdstt config get [KEY] [--show-secrets]   # print the config/KEY with secrets masked (or revealed)
gdstt config set KEY VALUE  # set a dotted KEY and validate
gdstt config unset KEY      # remove an optional dotted KEY
gdstt --config PATH <command>    # one-shot override against a non-default config.yml
```

`process` auto-detects whether the ID is a file or a folder; pass `--folder` to
force folder handling. `latest` resolves the folder from `--folder` or the first
configured `folders` entry and processes the newest (most recently created) mp4.
`list`/`status` defaults to every configured folder when `--folder` is omitted.
`--reprocess-txt` intentionally spends STT provider credits again and overwrites
the linked `.txt` when one exists. `speakers set` affects future local
post-processing; combine it with `process <file-id> --reprocess-txt` when an
already-uploaded transcript needs to be regenerated with corrected names.

**Reprocessing chain stages (`reprocess`).** Each enabled preset is a numbered
chain stage; `gdstt doctor` prints the numbering, with `0` reserved for the
transcript itself:

```text
$ gdstt doctor
...
folders: 1 configured
  abc123: Олег Иванов <oleg@example.com>
stt.provider: deepgram
Presets: 3 enabled (reprocess stages)
  0. transcript (Deepgram base)
  1. transcript-cleanup <- transcript
  2. keypoints <- transcript-cleanup
  3. meta <- transcript-cleanup
```

`gdstt reprocess <id> [STAGES]` force-reruns those stages even when the artifact
already exists. `STAGES` is a single number, a range (`lo-hi`), or a comma list over
`0..N`; omit it (or pass `all`) to rerun every preset:

```bash
gdstt reprocess <id>           # rerun every preset (1..N) from the existing transcript
gdstt reprocess <id> 2         # rerun only stage 2 (keypoints)
gdstt reprocess <id> 1-2       # rerun stages 1 and 2 (transcript-cleanup + keypoints)
gdstt reprocess <id> 2,3       # rerun stages 2 and 3 (keypoints + meta)
gdstt reprocess <id> 0         # re-transcribe (stage 0) and regenerate the whole chain
gdstt reprocess <id> 2-3 --dry-run   # preview which stages would run, spend nothing
```

Stage `0` re-runs Deepgram (spends STT credits) and, since every preset feeds on the
transcript, regenerates the entire chain. Stages `1..N` re-run only those presets
(spending OpenAI); each one's dependency outputs are reused from the existing
artifacts, so a partial reprocess never re-bills the upstream stages — and a missing
dependency artifact is regenerated as needed. Add `--folder` to reprocess every
transcribed file in a folder, and always `--dry-run` first.

`run` starts the continuous loop (and explicitly resumes by clearing any sticky
stop flag). `stop` sets `run.enabled: false` in the config; the loop re-reads it
each cycle and **pauses** — it goes idle (sleeps and re-checks) instead of exiting,
so the container stays up. The pause is sticky: `main()` never auto-enables the flag
at startup, so under Docker's `restart: unless-stopped` the stop survives a restart
and processing does **not** auto-resume. Resume explicitly with `gdstt start` (or
`gdstt run`); to halt the container entirely use `docker compose stop`.

`relabel` is a local file transform — it reads a transcript and a `MAP.json`
(`default` label → name plus verbatim-text `exceptions`), merges consecutive
same-speaker turns, preserves each utterance's words (whitespace is normalized),
and reports unmapped labels on stderr. It touches no Drive and spends nothing.

Use `doctor` first when setting up a new agent or machine: it reports the resolved
config path, credentials/token presence, each configured folder with its employee
name and email, and the STT provider without
validating provider secrets. Add `--drive` only when you want it to authenticate
and list the configured folders. Use `--dry-run` on `run-once`, `latest`, or folder
`process` to preview pending work without downloads, uploads, or STT calls.
`--max-size` is off unless you pass it. Use it as an optional manual safety limit
before processing folders, for example `--max-size 50MB`; files larger than the
limit are skipped unless you also pass `--confirm-large`.

`run` has no preview mode and is intentionally the least safe operator entrypoint:
it keeps polling and can continue spending STT credits until you stop it. Use it
only after the single-file or `run-once --dry-run` path already matches expectations.

#### Managing the config file

There is always exactly one active `config.yml`; `gdstt config path` prints where
it is. `config init` creates one from the packaged defaults and copies the prompt
assets into `<GDSTT_HOME>/prompts/`. With no `--config`, init resolves its target
the same way the runtime reads it — `<GDSTT_HOME>/config.yml`, or `./data/config.yml`
when `GDSTT_HOME` is unset — so it writes exactly where the runtime will look
(relevant under Docker, where the image bakes `GDSTT_HOME=/app/data`).
`config get`/`set`/`unset` read (with secrets masked) or edit a dotted key (e.g.
`openai.model`, `stt.deepgram.api_key`) in place, validating the result.

To run more than one instance, give each its own directory and point `GDSTT_HOME`
at it (`export GDSTT_HOME=/srv/gdstt-a`); the entire instance — `config.yml`,
`prompts/`, the keyterms file, and credentials/token files — lives
under that one home. Keep secrets (`openai.api_key`, `stt.deepgram.api_key`, inline
`google.token`/`credentials`) out of any synced or shared home directory.

Synced notes folders are just ordinary user-chosen paths: to land artifacts in
your (possibly synced) notes folder, set `output.target: folder` + `output.dir`,
and select a non-default config or prompt directory with `gdstt --config PATH` or
`config init --prompt-dir`.

### Runtime reliability and summaries

The runtime treats incomplete output as failure instead of silently uploading it:

- Empty provider transcripts raise an STT error; a blank `.txt` is not written.
- Transient Drive metadata lookups, folder-state listings, and downloads retry with
  bounded backoff. Uploads are not retried automatically.
- Downloads are checked against Drive metadata size; mismatched partial temp files
  are removed before retry or recovery.
- A `folders` entry without a `folder_id` fails configuration loading instead of
  producing a misleading no-op run. A config still carrying the removed
  `folder_ids` key fails loudly with the `folders` shape to migrate to.

`run-once` logs one process summary per worked file, one folder summary per folder,
and one cycle summary. The cycle summary includes pending, processed, failed,
`retry_total`, skipped-by-size, folder-error, and duration fields. Each process
summary also records the Deepgram request cost (USD, when the usage API has
recorded it) and the OpenAI keypoints token usage.

After `process` and `latest`, the CLI prints a short **spend summary** for the
worked files: the Deepgram cost (or `pending` when the usage API has not recorded
it yet) and, when keypoints ran, the OpenAI token counts. `transcribe` prints the
Deepgram cost after a local-file run.

### Agent-facing documentation

Shared repository instructions live in [`AGENTS.md`](AGENTS.md). The operator
skill is a single file, [`skills/gdstt-cli/SKILL.md`](skills/gdstt-cli/SKILL.md);
copy it into your agent's skills directory to use it. The skill also documents an
optional, fill-in-the-blanks Vault integration layer (wikilinks, vault output
paths, and a sensitive-fragment redaction step); the default output stays plain.

## Tests

```bash
uv run pytest
uv run ruff check
```

Deepgram has a gated live smoke test that can spend a small amount of credit. It is
skipped unless explicitly enabled:

```bash
RUN_DEEPGRAM_LIVE_TESTS=1 \
DEEPGRAM_API_KEY_FILE=/path/to/deepgram_api_secret.json \
DEEPGRAM_LIVE_AUDIO_PATH=/path/to/short-audio-or-video.mp4 \
uv run pytest tests/test_stt_deepgram_live.py -s
```

For MP4/MOV/M4V inputs, the live test extracts only the first 30 seconds to a
temporary MP3. It prints the transcript preview, Deepgram request id, duration, and
best-effort USD cost when Deepgram's usage API has recorded it.

## Docker deployment

Build and run with the bundled Compose file:

```bash
docker compose up -d --build
```

The image bakes `GDSTT_HOME=/app/data` and mounts `./data` there, so the config
resolver keeps **all mutable state inside the volume**: `config.yml`,
`prompts/`, the keyterms file, and credentials/token files are
written under `./data` and survive restarts. The Compose file also sets
`GDSTT_HOME=/app/data` explicitly for clarity, and a bare `docker run` is correct by
default thanks to the image `ENV`. Logs are JSON-file with a 10 MB / 3-file
rotation. Restart policy is
`unless-stopped`. Because `gdstt stop` is sticky (it pauses the loop without exiting
and `main()` never auto-enables on boot), the stop survives this restart policy:
`docker compose exec <svc> gdstt stop` pauses processing and `gdstt start` resumes it;
`docker compose stop` halts the container itself.

The prompt assets ship **inside the `src` package** (`src/assets/prompts/*.md`), so
the `COPY src ./src` in the `Dockerfile` carries them automatically — there is no
separate `assets/` copy and the keypoints / OpenAI preset stage works in the
container with no extra setup.

Google auth follows the config-owned model (see
[Google Drive setup](#google-drive-setup)): it is inline-first in `config.yml`
(`google.credentials` / `google.token`), with file mode
(`gdstt auth use-files --credentials-file data/credentials.json`, which points
`config.yml` at an operator-supplied `credentials.json`/`token.json` under the
volume rather than creating them) as the explicit opt-in, and a legacy fallback to
`credentials.json` / `token.json` beside the config. The generated `config.yml`
is written `0600` on POSIX systems because it can hold inline secrets.

For a fresh VPS:

1. Copy the repo and create `./data` on the host.
2. Generate the volume-owned config:
   `docker compose run --rm google-drive-video-stt gdstt config init --force`
3. Fill `./data/config.yml` (folder IDs, Deepgram/OpenAI keys, and Google auth
   inline or file mode).
4. `docker compose up -d --build`
5. Tail logs with `docker compose logs -f` and verify a poll cycle completes.

### Container smoke check

After a build, confirm the deployment-critical config-only path — volume
persistence, generated local assets, provider validation, and packaged prompts —
with the bundled script (a manual/CI check, not a pytest; it spends nothing):

```bash
scripts/docker-smoke.sh            # builds google-drive-video-stt:smoke, then runs doctor
scripts/docker-smoke.sh my-image   # build and test the custom tag name
```

Equivalently, by hand:

```bash
docker build -t google-drive-video-stt:latest .
docker run --rm -v "$PWD/data:/app/data" \
  google-drive-video-stt:latest gdstt config init --force
docker run --rm -v "$PWD/data:/app/data" \
  google-drive-video-stt:latest gdstt doctor
```

A healthy run prints a `config:` path under `/app/data/config.yml` (volume
persistence). The smoke script also verifies that a generated config can pass
provider validation without reaching external services and explicitly loads
`keypoints.md` from the `src` package to assert prompt packaging.

## Call bookings and Planfix

An external system can tell gdstt about upcoming calls so each recording is linked to
its Planfix task.

1. Enable the receiver in `config.yml`:

   ```yaml
   call_booking:
     enabled: true
     listen_host: 0.0.0.0
     listen_port: 8080
     authorization_token: <a long random string>
     threshold_minutes: 15
     disable_recognition: false
   planfix:
     create_comment_url: https://<your-host>/agent/leads/tool/planfix_create_comment
     token: <planfix webhook token>
     presets: [keypoints]
   ```

   `authorization_token` must be ASCII: header values are decoded as Latin-1, so a
   token with any non-ASCII character can never authenticate.

2. Uncomment the `ports:` block for port 8080 in `docker-compose.yml` (it ships
   commented out, since the receiver is off by default) and put a TLS-terminating
   reverse proxy in front of it. The bearer token and the booking payload must not
   cross the network in plain text.

3. Point the external system at `POST https://<your-host>/` with
   `Authorization: Bearer <authorization_token>` and this body:

   ```json
   {"start_time": "2026-08-11T07:00:00.000000Z", "task_id": "851030", "manager_email": "manager@example.com"}
   ```

   `task_id` must be numeric. `GET /health` returns 200 for probes.

4. `manager_email` is matched against the `email` of the `folders` entry the
   recording lives in, and `start_time` against the meeting time in the recording's
   name, within `threshold_minutes`.

Set `disable_recognition: true` once bookings are flowing to stop transcribing
recordings that match no booked call. Those get marked on Drive and skipped for good;
`gdstt bookings list` shows what the matcher had, and `gdstt bookings rematch <file-id>`
revives one. The mark is written only while the receiver is listening; if it failed
to bind its port, unmatched recordings are skipped and retried on the next cycle
rather than marked.

Writing the mark counts as an edit in Drive, so it moves the recording's
"Last modified" date. `gdstt bookings restore-dates --dry-run` lists the files
whose date was moved that way, and the same command without the flag puts each
one back to its creation time.

## Project layout

```
src/
  auth.py        OAuth flow + Drive service builder
  config.py      Env var loading
  drive.py       List / download / upload helpers
  extractor.py   ffmpeg MP4 → MP3/M4A wrappers
  notify.py      Telegram error notifier
  webhook.py     Fire-and-forget completion webhook
  main.py        Polling loop + on-demand process_target entry points
  cli.py         gdstt operator CLI (argparse subcommands)
  output.py      Output destination layer (Drive sibling or local folder)
  postprocess.py Local transcript cleanup + speaker-name mapping
  meta.py        Parse the meta preset's frontmatter (subject + allow-listed tags/referral)
  meta_doc.py    Merge meta fields with known facts into <stem>.meta.yml
  stt_document.py Assemble <stem>.stt from keypoints, meta, and the transcript
  openai_pipeline.py OpenAI Responses keypoints generation (sync + batch)
  relabel_transcript.py Deterministic speaker relabeling from a MAP.json
  stt/
    __init__.py        get_provider() dispatch (Deepgram-only)
    base.py            STTProvider ABC (transcribe_full hook)
    transcribe.py      Full-file transcription call + cost logging
    deepgram_provider.py Deepgram Nova-3 + diarization
    deepgram_usage.py  Best-effort Deepgram usage/cost lookup
tests/           Unit tests (mock external services)
data/            Tokens, credentials, gitignored
```

## License

MIT — see `LICENSE`.
