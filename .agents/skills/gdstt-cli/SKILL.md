---
name: gdstt-cli
description: Use when operating google-drive-video-stt through gdstt, setting up Google Drive OAuth access, inspecting Drive folder state, processing Drive MP4 files, setting speaker names, or transcribing local audio.
version: 1.3.0
last_updated: 2026-06-01
---

# gdstt CLI

Operator reference for the Google Drive video STT service. Prefer safe,
single-target commands, avoid printing secrets, and ask before any command that
changes Google Cloud, Drive, local auth files, or spends STT credits.

This skill also ships as a portable installable bundle under
`.agents/skills/gdstt-cli/`. The bundled reference copies under `references/`
must stay synchronized with the canonical repo docs under `docs/skills/`.

## Start Here

Use the smallest path that fits the task:

1. First-time Drive-only setup: `gdstt auth` -> `gdstt doctor` -> `gdstt list`
2. Safe single-file processing: `gdstt process <file-id> --dry-run` -> `gdstt process <file-id>`
3. Provider or failure detail: stay in this skill for command routing, then open
  `references/provider-notes.md` or `references/troubleshooting.md` for deeper reference

Current operational default examples assume `STT_PROVIDER=deepgram`, but the
same command flow stays stable if the provider changes later.

## Invocation

```bash
gdstt <command> [args]
uv run python -m src.cli <command> [args]
```

On Windows local checkouts, `gdstt` may not be on `PATH`; use:

```powershell
.\.venv\Scripts\gdstt.exe <command> [args]
uv run python -m src.cli <command> [args]
```

Use `PYTHONIOENCODING=utf-8` or the installed `gdstt.exe` wrapper when printing
Cyrillic names from ad-hoc Python/PowerShell scripts.

### Command Boundaries

Drive-only/read-only commands use `load_config(validate_providers=False)`:

- `auth`
- `doctor`
- `list` / `status`
- `speakers set`
- `refresh-names`

Use these before any STT provider is configured.

Processing commands validate provider config and can spend STT credits:

- `run`
- `run-once`
- `process`
- `transcribe`

Start with the Drive-only commands when checking auth, folder ids, or artifact
state.

### Provider Switching Contract

Keep these rules stable even if the STT provider changes:

- `STT_PROVIDER` selects the speech-to-text backend; the CLI flow stays the same.
- `OPENAI_POSTPROCESS=true` is independent of `STT_PROVIDER` and can refine text
  after any provider.
- `STT_CHUNK_SECONDS` matters only for chunking providers; it is ignored by
  Deepgram and Google full-file paths.
- Keep Drive setup, dry-run safety, and single-file-first habits provider-agnostic.

Current operational default examples assume `STT_PROVIDER=deepgram`, but keep
the same CLI flow if the provider changes later.

### Companion References

Keep the default workflow in this one skill. This bundle ships portable
reference copies under `references/`; if you are editing the repository itself,
the canonical versions live under `docs/skills/`.

- `references/provider-notes.md` - see `Universal switching rules`, `Deepgram`,
  `Google STT`, `OpenAI STT`, and `ASR` for provider-specific tuning and switching.
- `references/troubleshooting.md` - see `Empty transcript failures`,
  `Transient Drive retries`, `Download size mismatch`, ``Invalid `FOLDER_IDS```,
  `Reading runtime summaries`, `Google STT timeout cleanup`, `Deepgram artifact surprises`,
  and `First recovery commands` for operator recovery.
- `references/provider-extension.md` - see `Invariants to preserve`,
  `Required code changes`, and `Release checklist for a provider change`
  when replacing or adding a provider.

### Supporting Playbooks

Ordinary project use should stay in the main skill flow. Use the bundled
examples only for external setup, folder-wide safety, or recovery:

- `examples/drive-only-setup.md` - first-time Drive access without bundling STT setup.
- `examples/folder-dry-run-size-guard.md` - folder-wide previews and optional size limits.
- `examples/google-timeout-recovery.md` - recovery after Google STT timeout-retained blobs.

Do not search for a scenario file before using the normal project workflow.
Reach for these playbooks only when the task is outside the ordinary day-to-day
path.

Repo maintainer note: when editing this repository, the canonical docs live at:

- `docs/skills/provider-notes.md`
- `docs/skills/troubleshooting.md`
- `docs/skills/provider-extension.md`

## Commands

### `auth [response_url]`

Run the app OAuth flow and save `token.json` into `DATA_DIR`.

```bash
gdstt auth
gdstt auth "http://localhost/?code=4/abc123&scope=..."
```

Requires `credentials.json` in `DATA_DIR`.

### `doctor [--drive]`

Check local Drive/OAuth setup without changing anything. It does not validate STT
provider secrets, so use it during first-time Drive setup before Deepgram/OpenAI
is fully configured.

```bash
gdstt doctor
gdstt doctor --drive
```

`--drive` authenticates and lists configured folders. Ask before using `--drive`
if OAuth/browser prompts may appear.

### `list` / `status`

Read-only folder state.

```bash
gdstt list
gdstt status --folder <folder-id>
```

Output format: `[mp3] [txt] <filename>`.

### `run`

Run the polling loop continuously. Ask before using it because it can process
all pending configured folders and spend STT credits.

```bash
gdstt run
```

### `run-once`

Run one polling cycle over all configured folders. Ask before using it because
it can process every pending file in `FOLDER_IDS`.

```bash
gdstt run-once
gdstt run-once --dry-run
gdstt run-once --max-size 50MB
gdstt run-once --max-size 50MB --confirm-large
```

`--dry-run` previews work without downloading, uploading, or calling STT.
`--max-size` skips larger videos unless `--confirm-large` is passed.

### `process <target> [--folder] [--reprocess-txt] [--dry-run] [--max-size SIZE] [--confirm-large]`

Process one Drive file or folder. Prefer a single file id. `--reprocess-txt`
runs STT again and can spend provider credits.

```bash
gdstt process <file-id>
gdstt process <file-id> --reprocess-txt
gdstt process <folder-id> --folder
gdstt process <folder-id> --folder --dry-run
gdstt process <folder-id> --folder --max-size 50MB
```

For folder processing, use `--dry-run` first. Use `--max-size` when a folder may
contain large videos; only pass `--confirm-large` after the human explicitly
confirms the cost/risk.

`--dry-run` here only previews pending work; it does not download files, run
ffmpeg, call STT, or upload artifacts.

### `speakers set <file-id> <name...>`

Store explicit speaker names on a Drive MP4. Reprocess the file to apply them to
an existing transcript.

```bash
gdstt speakers set <file-id> "Name 1" "Name 2"
gdstt process <file-id> --reprocess-txt
```

### `refresh-names <file-id>`

Rename linked generated MP3/TXT artifacts to match the current MP4 name without
running STT.

```bash
gdstt refresh-names <file-id>
```

### `transcribe <audio> [-o PATH]`

STT-only on a local audio file. Does not touch Drive and does not run the
OpenAI post-processing pipeline.

```bash
gdstt transcribe ./meeting.mp3 -o ./meeting.txt
```

## Google Drive Auth Setup

Use this when an agent needs Drive access. This is Drive-first setup; do not
enable or configure Google STT unless the human explicitly asks for Google STT.

### Human-Facing Drive Setup Wizard

When Drive access is missing, guide the human with this flow:

1. Say plainly:

   ```text
   I can set up Google Drive access for this agent. Please sign in to Google if
   needed, then tell me which Google Cloud project to use. You can give me an
   existing project id, or a new project name you want created.
   ```

2. Ask for exactly these choices before changing anything:

   - Google account/project: existing project id, or new project name.
   - Drive folder: existing folder id, or permission to create/select one later.
   - Local app data: keep `DATA_DIR=data`, or use another path.
   - Permission to run Drive-only setup commands.

3. Explain the boundary:

   ```text
   I will configure Google Drive access only. Google Speech-to-Text is a separate setup step.
   I will not enable Speech/Storage APIs or set STT_PROVIDER=google unless you ask for that separately.
   ```

4. Run read-only discovery first, then ask for confirmation before each mutating
   group: selecting/creating a project, enabling Drive API, writing
   `credentials.json`, running OAuth, and setting the folder id.

   Before `gcloud config set project`, explain that it changes the active
   project in the user's gcloud configuration, not only this repository.

   Before creating `data/credentials.json` from ADC metadata, explain that only
   OAuth client id/secret are copied and the ADC refresh token is not copied.

5. Finish with:

   ```text
   Drive access is ready. Google STT is still not configured.
   ```

Do not bundle Deepgram/OpenAI/Google STT setup into this wizard. If the human asks
for STT later, handle it as a separate command/task.

### Required Questions Before Mutating Anything

Ask and wait for answers before running setup commands:

- Which Google Cloud project id should be used?
- Use an existing project, or create a new one?
- Is billing setup needed? Only ask if the user wants Google STT/GCS or project creation requires it.
- Which Drive folder id should go into `FOLDER_IDS`?
- Should `DATA_DIR` stay `data`, or use another path?
- May I run `gcloud` commands that change config, enable APIs, or write `data/credentials.json`?
- May I run `gdstt auth` and open the OAuth browser flow?

The app currently requests both OAuth scopes from `src/auth.py`:

```text
https://www.googleapis.com/auth/drive
https://www.googleapis.com/auth/cloud-platform
```

Explain this before auth and ask for confirmation. The `cloud-platform` OAuth
scope is not the same as enabling Google STT APIs or setting `STT_PROVIDER=google`.
Do not enable Speech/Storage APIs or configure Google STT without a separate
explicit confirmation.

### Drive-Only Setup Flow

Run read-only discovery first:

```powershell
Get-Command gcloud
gcloud config get-value project
gcloud auth list
```

After confirmation, set/select the project:

```powershell
gcloud config set project <project-id>
```

Warn that this changes the active project in the user's gcloud configuration,
not just this checkout.

For Drive-only access, enable only Drive API:

```powershell
gcloud services enable drive.googleapis.com
```

Do not enable these unless the human separately confirms Google STT:

```text
speech.googleapis.com
storage.googleapis.com
```

Create `data/credentials.json` from local gcloud ADC client metadata only after
confirmation. This writes OAuth client id/secret only; never copy the ADC
`refresh_token` or print it:

```powershell
gcloud auth application-default login `
  --scopes=https://www.googleapis.com/auth/drive,https://www.googleapis.com/auth/cloud-platform

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

Then, after confirmation, run:

```powershell
.\.venv\Scripts\gdstt.exe auth
```

Verify without printing secrets:

```powershell
.\.venv\Scripts\gdstt.exe list
```

If `FOLDER_IDS` is not set, ask for a Drive folder id or permission to create
one. Do not create folders without confirmation.

## Operator Playbooks

### Check What Is Ready

Run read-only status first:

```bash
gdstt list
gdstt status --folder <folder-id>
```

Explain `[mp3] [txt]` plainly:

- `[txt]` means a transcript is linked or matched.
- `[mp3]` means a Drive MP3 artifact is linked or matched.
- `[---]` means missing or only present as an unlinked legacy file.

If a large file is `[---] [---]`, do not run `run-once` or folder `process`
without warning that STT may spend credits. Prefer:

```bash
gdstt run-once --dry-run
gdstt process <folder-id> --folder --dry-run
gdstt process <folder-id> --folder --max-size 50MB
```

Only use `--confirm-large` after the human explicitly confirms processing files
larger than the `--max-size` threshold.

`--max-size` is optional and disabled by default. Do not invent a global default;
use it only when the human asks for a size guard or when you are about to run a
folder-wide command and want to offer a manual safety limit.

### Fix Speaker Names

```bash
gdstt speakers set <file-id> "Name 1" "Name 2"
gdstt process <file-id> --reprocess-txt
```

Warn that `--reprocess-txt` runs STT again. Prefer a single file id.

### Source MP4 Was Renamed

```bash
gdstt refresh-names <file-id>
```

Works for artifacts linked by `appProperties.source_video_id`. Legacy artifacts
without metadata may need regeneration or manual cleanup.

### Deepgram TXT Exists But MP3 Does Not

Current operational default examples assume `STT_PROVIDER=deepgram`, but keep
the same CLI flow if the provider changes later.

Deepgram tuning knobs:

- `DEEPGRAM_MODEL` - default `nova-3`.
- `DEEPGRAM_DIARIZE_MODEL` - `latest` or `v1`.
- `DEEPGRAM_AUDIO_SOURCE` - `m4a_copy` (default, no re-encode), `mp3_96k`, or
  `mp3_192k`.
- `DEEPGRAM_TXT_FORMATTER` - `word_speaker` (more speaker switches) or
  `utterance` (cleaner blocks).
- `DEEPGRAM_KEYTERMS_ENABLED` / `DEEPGRAM_KEYTERMS_FILE` - optional domain hints;
  validate on a sample first because they can increase spend.

Deepgram default uses temporary `m4a_copy` and does not upload a Drive MP3 unless
requested:

```env
DRIVE_MP3_ARTIFACT=true
```

To create only MP3 for a file that already has TXT, set that env var for one
command and do not pass `--reprocess-txt`.

If Deepgram quality or latency is wrong, change one knob at a time and rerun a
single file with `gdstt process <file-id> --reprocess-txt` before folder-wide
commands.

For longer Deepgram and provider-comparison notes, see
`references/provider-notes.md`.

### Switch Providers Without Rewriting the Workflow

Keep the operator flow stable:

1. `gdstt doctor`
2. `gdstt list`
3. `gdstt process <file-id> --dry-run`
4. `gdstt process <file-id>` or `gdstt process <file-id> --reprocess-txt`
5. Only then `gdstt run-once` or folder `process`

Then swap only provider-specific settings:

- Deepgram: `DEEPGRAM_*`
- Google STT: `GOOGLE_CLOUD_PROJECT`, `GOOGLE_STT_GCS_BUCKET`, and explicit `STT_LANGUAGE`
- OpenAI STT: `OPENAI_API_KEY`
- ASR: `ASR_URL`

Do not rewrite the safety flow just because the provider changes.

### Full Drive MP4 To Final TXT With OpenAI Post-Processing

Use this when the human asks for the whole pipeline in one request: take a Drive
MP4, run STT, refine the transcript with OpenAI, and upload/overwrite the linked
TXT next to the source video.

Requirements:

- Drive access works (`gdstt doctor --drive` or `gdstt list` succeeds).
- An STT provider is configured, usually `STT_PROVIDER=deepgram`.
- `OPENAI_POSTPROCESS=true`.
- `OPENAI_API_KEY` is set.
- Optional: `OPENAI_MODEL` (defaults to `gpt-5.4-mini`).
- Optional: `OPENAI_BATCH=true` for lower cost and higher latency.
- Optional: `PROXY_URL` if OpenAI traffic must use a proxy.

Safe operator flow:

```bash
gdstt doctor
gdstt list
gdstt process <drive-mp4-file-id> --dry-run
gdstt process <drive-mp4-file-id>
```

If the TXT already exists and the human wants to regenerate it with OpenAI
post-processing, ask before spending STT/OpenAI credits, then run:

```bash
gdstt process <drive-mp4-file-id> --reprocess-txt
```

For folder-wide processing, offer `--dry-run` first and optionally a manual
`--max-size SIZE` guard. Do not invent a default size limit.

The OpenAI post-processing pipeline is not the same as `STT_PROVIDER=openai`:

- `STT_PROVIDER=openai` means OpenAI does speech-to-text.
- `OPENAI_POSTPROCESS=true` means OpenAI refines the text after any STT provider.

### Local MP3/MP4 Limits

`gdstt transcribe <audio>` can transcribe a local audio file and write local TXT,
but it currently does not run `OPENAI_POSTPROCESS` and does not upload anything
to Drive. There is no single local-MP4 CLI command; Drive MP4 processing is the
complete MP4-to-final-Drive-TXT pipeline.

### Inspect Config Without Running Work

Do not print API keys, OAuth tokens, or `data/token.json`.

```bash
python - <<'PY'
from src.config import load_config
cfg = load_config(validate_providers=False)
print(cfg.folder_ids)
print(cfg.stt_provider)
print(cfg.deepgram_audio_source)
print(cfg.drive_mp3_artifact)
print(cfg.stt_postprocess)
print(cfg.openai_postprocess)
print(cfg.openai_batch)
PY
```

## Environment Variables

### Drive-only setup

- `FOLDER_IDS` - comma-separated Drive folder IDs to monitor.
- `DATA_DIR` - holds `credentials.json` / `token.json`.
- `PROXY_URL` - optional proxy for outbound provider traffic.
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` - optional notifications for runtime failures.

Use this group first when the human only needs Drive auth, folder inspection,
or read-only setup checks.

### Common runtime behavior

- `POLL_INTERVAL` - loop interval in seconds; must stay positive.
- `BITRATE` - MP3 bitrate for ffmpeg extraction when MP3 output is needed.
- `STT_PROVIDER` - `""`, `openai`, `google`, `asr`, or `deepgram`.
- `STT_LANGUAGE` - provider language hint. It defaults to `ru` for Deepgram,
  is required for Google STT, and is optional for OpenAI STT / ASR.
- `STT_CHUNK_SECONDS` - chunk size for providers that use chunking. It is
  ignored by Deepgram and Google full-file paths.
- `STT_POSTPROCESS` - run the local transcript post-processing pipeline.

### Deepgram default setup

- `DEEPGRAM_API_KEY`, `DEEPGRAM_API_KEY_FILE` - Deepgram credentials. The file
  path can contain a raw token or JSON with an API key field; never print secret
  values.
- `DEEPGRAM_MODEL` - default `nova-3`.
- `DEEPGRAM_DIARIZE_MODEL` - `latest` or `v1`.
- `DEEPGRAM_AUDIO_SOURCE` - `m4a_copy`, `mp3_96k`, or `mp3_192k`.
- `DEEPGRAM_TXT_FORMATTER` - `word_speaker` or `utterance`.
- `DEEPGRAM_KEYTERMS_ENABLED` - enable optional keyterm hints.
- `DEEPGRAM_KEYTERMS_FILE` - defaults to `config/deepgram-keyterms.txt`; keep it
  at or below the configured max and validate on a sample before large runs.
- `DRIVE_MP3_ARTIFACT` - upload a Drive MP3 artifact. Defaults false for
  Deepgram `m4a_copy`, true otherwise.

Use this as the default operator path unless the human explicitly switches
provider.

### OpenAI setup and post-processing

- `OPENAI_API_KEY` - required for `STT_PROVIDER=openai` and for OpenAI post-processing.
- `OPENAI_POSTPROCESS` - run OpenAI transcript refinement after STT.
- `OPENAI_MODEL` - OpenAI post-processing model, default `gpt-5.4-mini`.
- `OPENAI_BATCH` - submit OpenAI post-processing through Batch API.

### Google STT setup

- `GOOGLE_CLOUD_PROJECT`, `GOOGLE_STT_GCS_BUCKET` - only required for
  `STT_PROVIDER=google`; do not add or require these for Drive-only access.

Keep Google STT setup separate from Drive-only auth setup.

### ASR and other provider-specific settings

- `ASR_URL` - required for `STT_PROVIDER=asr`.

## Notes

- `auth` errors usually mean missing/expired token or missing scope; re-run
  `gdstt auth` after confirming with the human.
- Empty transcripts now fail intentionally; do not treat a blank `.txt` as a
  successful STT outcome.
- Transient Drive metadata, folder-state, and download errors are retried
  automatically before the cycle gives up.
- Downloaded files with a final size mismatch now fail intentionally and remove
  the partial temp file before retry/recovery.
- `FOLDER_IDS` that contain only commas or whitespace now fail fast during
  config loading instead of degrading into a misleading no-op run.
- Idempotency uses `appProperties.source_video_id` for new artifacts and
  filename-stem matching as a legacy fallback.
- `run-once` processes all pending configured folders; use single-file `process`
  when testing or controlling spend.
- `process_item` now logs one process summary per worked file with provider,
  `processing_mode`, outcome, retry count, and duration.
- `run-once` now logs folder summaries and one cycle summary so operators can
  see provider, overall outcome, pending/processed/failed counts, `retry_total`,
  `gcs_blob_orphans`, duration, and folder-level issues without scanning every line.
- Tests for this command surface live in `tests/test_cli.py` and
  `tests/test_skill_docs.py`.

For deeper failure handling and recovery notes, see
`references/troubleshooting.md`.
