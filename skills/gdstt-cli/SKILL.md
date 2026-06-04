---
name: gdstt-cli
description: Use when operating google-drive-video-stt through gdstt - transcribing a Drive recording or local audio with Deepgram, processing the newest mp4 in a folder, relabeling diarized speakers, and building a speaker-named transcript plus a Keypoints document (Задачи / Тезисы / Открытые вопросы).
license: MIT
version: 2.0.0
last_updated: 2026-06-04
---

# gdstt CLI

Operator guide for the Deepgram-only Google Drive video STT service. The tool has
one job: turn a recording into a speaker-named transcript plus a Keypoints
document, written either to Google Drive or to a local folder. Prefer safe,
single-target commands, never print secrets, and ask before any action that
changes Drive, local auth files, or spends Deepgram/OpenAI credits.

Google Drive setup (OAuth, scopes, folder-id discovery) lives in the repo
[README.md](../../README.md), not here.

## Start Here

Use the smallest path that fits the task:

1. "сделай расшифровку последнего созвона" -> `gdstt latest --dry-run` ->
   `gdstt latest` -> reason speakers -> confirm mapping -> `gdstt relabel` ->
   write `<base>.keypoints.md` -> place in destination.
2. OAuth refresh or headless recovery: `gdstt auth` or `gdstt auth --manual`.
3. Single Drive file: `gdstt process <file-id> --dry-run` ->
   `gdstt process <file-id>`.
4. Local audio only (no Drive): `gdstt transcribe <audio> -o out.txt`.
5. Folder-wide work: preview first with `gdstt run-once --dry-run`.
6. Inspect state without spending credits: `gdstt list` and `gdstt doctor`.

## Invocation

```bash
gdstt <command> [args]
uv run python -m src.cli <command> [args]
```

On Windows local checkouts:

```powershell
.\.venv\Scripts\gdstt.exe <command> [args]
uv run python -m src.cli <command> [args]
```

Use `PYTHONIOENCODING=utf-8` or the installed `gdstt.exe` wrapper when printing
Cyrillic names from ad-hoc Python or PowerShell scripts.

## Command Boundaries

Drive-only and bootstrap commands use `load_config(validate_providers=False)` and
do not spend credits: `auth`, `doctor`, `list` / `status`, `speakers set`.

Processing commands validate provider config and can spend Deepgram (and, when
`OPENAI_KEYPOINTS=true`, OpenAI) credits: `run`, `run-once`, `process`,
`latest`, `transcribe`.

`relabel` is a local file transform - it touches no Drive and spends nothing.

## Commands

### `auth [--manual] [response_url]`

Create or refresh local OAuth credentials. Normal mode opens a localhost browser
flow. `--manual` prints the authorization URL; passing `response_url` completes
that manual exchange.

### `latest [--folder ID] [--dry-run]`

Process the newest mp4 in a folder (the first of `FOLDER_IDS` unless `--folder`
is given). Use `--dry-run` first to confirm which file would be processed. Logs
clearly when the folder has no mp4.

### `process <target> [--folder] [--reprocess-txt] [--dry-run] [--max-size SIZE] [--confirm-large]`

Process one Drive file or folder. Prefer a single file id and `--dry-run` first.
Ask before `--reprocess-txt` (reruns STT and overwrites the linked TXT), folder
execution, or `--confirm-large`.

### `transcribe <audio> [-o PATH]`

Transcribe a local audio file with Deepgram without touching Drive. Writes to
`PATH` with `-o`, otherwise prints to stdout.

### `relabel --in SRC --out OUT --map MAP.json [--no-header]`

Deterministically rename transcript speakers from a MAP.json (`default` label ->
name, with verbatim-text `exceptions`) and merge consecutive same-speaker turns.
Utterance text is preserved byte-for-byte. Unmapped labels are reported on
stderr - add them to `default`. `--no-header` skips the MAP.json header.

### `list` / `status`

Read sibling MP3/TXT state for the configured `FOLDER_IDS` (or `--folder`)
without processing files.

### `run`

Run the continuous polling loop. Ask before use: it can repeatedly spend credits
across every pending configured folder. It has no preview mode.

### `run-once`

Run one polling cycle across the configured folders. Prefer `--dry-run` first;
add `--max-size` only as an optional manual limit for larger folder runs.

### `speakers set <file-id> <name...>`

Store explicit speaker names on the source MP4 for future post-processing.
Reprocessing is separate and can spend credits.

### `doctor [--drive]`

Report `DATA_DIR`, credentials/token presence, `FOLDER_IDS` count, and
`STT_PROVIDER`. Add `--drive` to also authenticate and list configured folders.

## Agent Keypoints Workflow

This is the primary, model-driven path. The CLI produces a raw transcript; the
agent corrects speakers and writes the Keypoints document. The deterministic
`relabel` command guarantees utterance text stays byte-for-byte.

1. **Get the raw diarized transcript.** The relabel map keys are `Speaker N`
   labels, so the input must still carry them. `gdstt transcribe <audio>` emits
   raw `Speaker N` output (no post-processing). `gdstt latest` / `gdstt process
   <file-id>` only keep `Speaker N` when `STT_POSTPROCESS=false`; under the
   default `STT_POSTPROCESS=true` they already map labels to interlocutor names
   in `<base>.txt`, so a `Speaker N` map matches nothing. Note the `base` name
   (the source file stem) - the artifacts are `<base>.txt` and later
   `<base>.keypoints.md`.
2. **Reason about speakers.** Diarization usually emits more labels than people
   (e.g. `Speaker 1/2/3` for two participants). Map each label to a person by
   content: role and lexicon, who is addressed by name in a line (a line saying
   "Я тебе объясняю, Андрей" is *not* Andrey), who initiates the call. If labels
   outnumber people, decide which extra label merges into whom (the diarizer
   usually splits one person), and record per-line `exceptions` by verbatim text
   for the few lines that belong to someone else (greeting, a direct question).
3. **Confirm the mapping with the user** via `AskUserQuestion` before writing
   anything: `Speaker N -> Name`, which label merged into whom and why, and the
   exception list. Skip this only if the user explicitly said "не спрашивай".
   Wrong attribution corrupts both documents.
4. **Build MAP.json** from the confirmed decision:

   ```json
   {
     "header": "# Транскрипт: <Имя1> × <Имя2>\n\n> Исправленная атрибуция спикеров.",
     "default": { "Speaker 1": "Имя2", "Speaker 2": "Имя2", "Speaker 3": "Имя1" },
     "exceptions": [ { "text": "Алло, привет.", "name": "Имя1" } ]
   }
   ```

   `default` maps each transcript label to a plain name; `exceptions` override by
   verbatim line text. Use plain names - no wikilinks, no vault styling.
5. **Run relabel:** `gdstt relabel --in <base>.txt --out <base>.transcript.md
   --map MAP.json`. Check stderr for `unmapped labels` and extend `default` until
   none remain. Confirm no `Speaker N` label survives in the output body.
6. **Write `<base>.keypoints.md`** from the corrected transcript using the
   template below.
7. **Place artifacts in the destination.** When working through Drive, the TXT is
   already a sibling; upload the Keypoints file next to it. When `OUTPUT_TARGET=
   folder`, write both into `OUTPUT_DIR`.
8. **Final status:** files created, the speaker mapping (who merged into whom),
   and any unmapped labels.

### Keypoints Template

Build `<base>.keypoints.md` from the corrected transcript. Plain text, no
wikilinks, no vault style. Do not invent facts - take decisions and tasks from
the transcript only.

```md
# Keypoints: <Имя1> × <Имя2>

Источник: <base>. <1-2 строки о чём разговор>.

## Задачи
### Ответственный
- [ ] <задача>

## Тезисы
- <ключевая мысль>

## Открытые вопросы
- <что осталось без ответа>
```

- Group `## Задачи` by `### Ответственный` (plain name); do not repeat the name
  inside the item. Unknown owner -> `### Без ответственного`.
- `## Тезисы` and `## Открытые вопросы` use `-` bullets.
- No filler, no marketing.

For the unattended auto path, set `OPENAI_KEYPOINTS=true` and the service writes
`<base>.keypoints.md` via the OpenAI Responses API without agent involvement.

## Safety Rules

- Ask before commands that mutate Drive, local auth files, or spend
  Deepgram/OpenAI credits.
- Prefer one file before one folder; prefer one `--dry-run` before one real run.
- Confirm the speaker mapping with the user before writing transcript or
  Keypoints files (unless explicitly told not to ask).
- `--max-size` is optional and disabled by default. Do not invent a global
  threshold. Add `--confirm-large` only after explicit human approval.
- `--reprocess-txt` reruns STT and intentionally overwrites the linked TXT.
- `run` has no preview mode. Use it only after controlled checks match
  expectations.
- Empty transcripts fail intentionally; never accept a blank TXT as success.
- Never print API keys, OAuth tokens, `credentials.json`, or `token.json`.

## Core Notes

- Deepgram is the only STT provider; `STT_PROVIDER=""` keeps MP3-only mode.
- Idempotency uses `appProperties.source_video_id` and sibling stem matching as a
  legacy fallback; existing siblings are updated in place, not duplicated.
- Transient Drive metadata, folder-state, and download reads retry before a cycle
  gives up. Uploads are not retried automatically.
- `process_item` logs provider, outcome, retry count, and duration; `run-once`
  logs per-folder and one cycle summary (pending/processed/failed counts).
- `OUTPUT_TARGET=drive` writes artifacts as Drive siblings; `OUTPUT_TARGET=
  folder` writes them into `OUTPUT_DIR`.
