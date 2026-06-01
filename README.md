# google-drive-video-stt

Monitors Google Drive folders for new MP4 files, optionally extracts MP3 audio
with ffmpeg, and uploads transcripts alongside the original. Designed as a
headless preprocessing step for speech-to-text pipelines (e.g. NotebookLM, which
rejects files over 200 MB, and Cloud STT, which doesn't accept MP4 directly).

## Features

- Polls one or more Google Drive folders on a configurable interval
- Idempotent: skips already-created Drive artifacts, linked by source file id
  metadata when available and by sibling name as a legacy fallback
- Audio extraction via ffmpeg (`libmp3lame`, configurable bitrate)
- Optional Telegram error notifications (success is silent)
- Operator CLI (`gdstt`) wrapping auth, the polling loop, on-demand processing, local-file transcription, and folder-state inspection
- Optional transcript post-processing (local or OpenAI LLM) that maps diarized speakers to the interlocutor names in the file name
- Sibling `.mp3`/`.txt` names preserve the full Drive file name, including `/` characters
- Explicit speaker names can be stored on the Drive MP4 when the filename is not
  enough for reliable speaker mapping
- Agent JSON pipeline with deterministic planning, profile defaults, and
  confirmation gates for broad or destructive processing
- Docker-first deployment, all mutable state in `./data`

## Requirements

- Python 3.11+ and [`uv`](https://github.com/astral-sh/uv) for local development
- `ffmpeg` available on `PATH` for local runs (already included in the Docker image)
- Google Cloud project with the Drive API enabled and OAuth client metadata in `credentials.json`
- Optional: a Telegram bot token + chat ID for error notifications
- Optional: an HTTP(S) or SOCKS proxy via `PROXY_URL`; SOCKS support is included
  through the `requests[socks]` dependency

## Setup

For an operator-style local install, use the global CLI first:

```bash
uv tool install --editable .
uv tool update-shell
gdstt setup
```

`gdstt setup` creates `.env` from `.env.example` when needed, writes
`FOLDER_IDS`, defaults `STT_PROVIDER` to Deepgram, prompts for
the API keys required by the active pipeline profile, prepares
`data/credentials.json` from Application Default Credentials when available,
runs OAuth, verifies Drive access, and finishes with the safe next steps
`gdstt list` then `gdstt process <file-id> --dry-run`.

For development in this checkout, install the editable environment too:

```bash
uv sync --extra dev
```

If the default wizard cannot finish a step, the manual Google Cloud fallback
below remains the reference path.

## Google Cloud and Drive setup with gcloud

Use this section when `gdstt setup` cannot finish automatically or when you need
to inspect or change the Google Cloud pieces directly.

Install and initialize the Google Cloud CLI first:

```bash
gcloud init
gcloud auth login --enable-gdrive-access
```

Use an existing project:

```bash
gcloud config set project <project-id>
```

Or create a dedicated project for this service:

```bash
gcloud projects create <project-id> \
  --name="google-drive-video-stt" \
  --set-as-default
```

If the project needs billing for Google Speech-to-Text or Cloud Storage, list
billing accounts and link one:

```bash
gcloud billing accounts list
gcloud billing projects link <project-id> --billing-account=<billing-account-id>
```

Enable the APIs used by this app:

```bash
gcloud services enable \
  drive.googleapis.com \
  speech.googleapis.com \
  storage.googleapis.com
```

`drive.googleapis.com` is needed for all runs. `speech.googleapis.com` and
`storage.googleapis.com` are needed only when `STT_PROVIDER=google`.

This app uses an installed-app OAuth client JSON at `data/credentials.json`.
If you have initialized gcloud Application Default Credentials, create that file
from the local gcloud client metadata:

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

The generated `data/credentials.json` contains only the OAuth client metadata.
Do not copy the ADC `refresh_token` into it. The app creates its own
`data/token.json` when you run `gdstt auth`.

The OAuth-only flow remains available for refresh, recovery, or headless/manual
exchange:

```bash
gdstt auth
gdstt auth --manual
gdstt auth "http://localhost/?code=4/abc123&scope=..."
```

If the ADC flow is not available in your environment, use the Google Cloud
Console fallback: APIs & Services -> Credentials -> Create Credentials ->
OAuth client ID -> Desktop app, then save the downloaded JSON as
`data/credentials.json`. If Google asks for an OAuth consent screen first,
configure it in external test mode and add your own Google account as a test
user. The app requests these scopes: `https://www.googleapis.com/auth/drive`
and `https://www.googleapis.com/auth/cloud-platform`.

To create a Drive folder from the command line, use the Drive API with the
gcloud access token:

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

Copy the returned `id` into `.env` as `FOLDER_IDS=<folder-id>`. For an existing
Drive folder, the folder id is the last path segment in the browser URL:
`https://drive.google.com/drive/folders/<folder-id>`.

## Configuration

All configuration is environment-driven. See `.env.example`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `FOLDER_IDS` | (required) | Comma-separated Google Drive folder IDs to monitor |
| `POLL_INTERVAL` | `600` | Seconds between poll cycles |
| `BITRATE` | `96k` | MP3 audio bitrate passed to ffmpeg |
| `DRIVE_MP3_ARTIFACT` | auto | Upload an MP3 artifact to Drive. Defaults to `false` for `STT_PROVIDER=deepgram` + `DEEPGRAM_AUDIO_SOURCE=m4a_copy`; defaults to `true` otherwise |
| `TELEGRAM_BOT_TOKEN` | (empty) | If set with chat ID, errors are posted to Telegram |
| `TELEGRAM_CHAT_ID` | (empty) | Telegram chat to receive error notifications |
| `DATA_DIR` | `data` | Directory holding `credentials.json` and `token.json` |
| `STT_PROVIDER` | `deepgram` | `deepgram` by default. Set `disabled` to skip transcription explicitly, or use `openai`, `google`, or `asr` |
| `STT_LANGUAGE` | (empty) | Language hint. `openai`/`asr`: optional (`en`, `ru`); empty = auto-detect. `google`: required BCP-47 (`en-US`, `ru-RU`); `deepgram`: empty defaults to `ru` |
| `STT_CHUNK_SECONDS` | `600` | Chunk length for `openai`/`asr`. Ignored when `STT_PROVIDER=google` or `deepgram` |
| `STT_POSTPROCESS` | `true` | Clean the transcript and map diarized `Speaker N` labels to the interlocutor names parsed from the file name, merging spurious extra speakers |
| `OPENAI_POSTPROCESS` | `false` | Use the OpenAI Responses LLM pipeline to refine the transcript instead of the local `STT_POSTPROCESS` cleanup. Takes precedence over `STT_POSTPROCESS`. Requires `OPENAI_API_KEY` |
| `OPENAI_MODEL` | `gpt-5.4-mini` | Model for the OpenAI post-processing pipeline |
| `OPENAI_BATCH` | `false` | Submit OpenAI post-processing via the Batch API (~50% cheaper, higher latency) |
| `OPENAI_API_KEY` | — | Required when `STT_PROVIDER=openai` or `OPENAI_POSTPROCESS=true` |
| `DEEPGRAM_API_KEY` | — | Required when `STT_PROVIDER=deepgram` unless `DEEPGRAM_API_KEY_FILE` is set |
| `DEEPGRAM_API_KEY_FILE` | — | Optional file containing a raw Deepgram token or JSON with `api_key`, `deepgram_api_key`, or `DEEPGRAM_API_KEY` |
| `DEEPGRAM_MODEL` | `nova-3` | Deepgram model name |
| `DEEPGRAM_DIARIZE_MODEL` | `latest` | Deepgram diarization model: `latest` or `v1` |
| `DEEPGRAM_AUDIO_SOURCE` | `m4a_copy` | Audio sent to Deepgram: `m4a_copy`, `mp3_96k`, or `mp3_192k` |
| `DEEPGRAM_TXT_FORMATTER` | `word_speaker` | Deepgram TXT formatter: `word_speaker` or `utterance` |
| `DEEPGRAM_KEYTERMS_ENABLED` | `true` | Enables Nova-3 keyterm prompting |
| `DEEPGRAM_KEYTERMS_FILE` | `config/deepgram-keyterms.txt` | Keyterms file, one term per line, max 100 |
| `GOOGLE_CLOUD_PROJECT` | — | Required when `STT_PROVIDER=google` |
| `GOOGLE_STT_GCS_BUCKET` | — | Required when `STT_PROVIDER=google`; bucket used to stage MP3 uploads |
| `ASR_URL` | — | Required when `STT_PROVIDER=asr`; base URL of whisper-asr-webservice |

## Speech-to-text

Setting `STT_PROVIDER` to a non-empty value transcribes each pending recording
through the selected provider and uploads a sibling `<basename>.txt` next to the
MP4 and any optional generated audio artifact.

### Agent JSON pipeline

For agent-driven requests, prefer a compact intent and deterministic expansion:

```bash
gdstt plan --json '{"action":"process","targets":["<drive-mp4-file-id>"]}'
gdstt execute --json '{"action":"process","targets":["<drive-mp4-file-id>"]}'
```

On PowerShell, prefer `gdstt plan --json-file .\intent.json` and
`gdstt execute --json-file .\intent.json` to avoid native process quoting
issues.

The versioned profile lives in `config/pipelines/default.json`; optional
machine-specific overrides belong in gitignored `config/pipelines/local.json`.
The default profile uses Deepgram `m4a_copy`, OpenAI refinement, TXT upload, no
Drive MP3 artifact, and speaker names from the file name or Drive metadata.
Folder-wide processing and transcript regeneration require `--confirm`.
Execution results include best-effort OpenAI refinement token counters under
`usage.openai`. OpenAI dollar cost remains `null` because per-response billing
is not exposed through the runtime API key.

### Transcript post-processing

By default (`STT_POSTPROCESS=true`) the transcript is post-processed before upload rather
than stored as raw STT output. The local post-processor (`src/postprocess.py`) normalizes
whitespace, parses the interlocutor names from the recording file name (e.g.
`Alice and Bob - 2026/05/28 ... .mp4` → `Alice`, `Bob`), maps them onto the diarized
`Speaker N` labels by order of appearance, and merges any extra (spurious) diarization
speakers into the real one whose turns they continue. Speaker mapping only applies to
diarized transcripts (`google` / `deepgram`); with non-diarizing providers (`openai` /
`asr`) there are no `Speaker N` labels, so post-processing only normalizes whitespace.

Set `OPENAI_POSTPROCESS=true` to instead refine the transcript with the OpenAI Responses
API (`src/openai_pipeline.py`), which performs the same speaker mapping/merging via an LLM
while keeping every utterance verbatim. It requires `OPENAI_API_KEY`, honors `PROXY_URL`,
uses `OPENAI_MODEL` (default `gpt-5.4-mini`), and can run through the OpenAI Batch API
(`OPENAI_BATCH=true`) for ~50% lower cost at the price of higher latency. When enabled it
takes precedence over the local `STT_POSTPROCESS` path.

When a sibling `.txt` already exists, normal polling skips it to avoid spending
STT credits repeatedly. Use `gdstt process <file-id> --reprocess-txt` when you
intentionally want to run STT again and overwrite the existing `.txt` in place.
New `.txt` and `.mp3` artifacts are tagged with the source MP4 id, so future
source renames do not break artifact detection.

### Deepgram Nova-3 (diarization)

The `deepgram` provider submits a full-file audio copy to Deepgram's pre-recorded
`/v1/listen` endpoint using Nova-3, Russian language, and `diarize_model=latest`
by default. It is the recommended provider for Russian speaker diarization.
It does not require the Deepgram SDK; the provider uses the existing `requests`
HTTP client dependency.

Setup:

1. Create a Deepgram API key.
2. Set `STT_PROVIDER=deepgram` and either `DEEPGRAM_API_KEY` or
   `DEEPGRAM_API_KEY_FILE` in `.env`.
3. Keep `STT_CHUNK_SECONDS` as-is; it is ignored because Deepgram receives one
   full-file request so speaker labels remain consistent across the recording.

`DEEPGRAM_API_KEY_FILE` may contain either the raw token or JSON with one of these fields:
`api_key`, `deepgram_api_key`, or `DEEPGRAM_API_KEY`. The API key is never logged.
After each successful Deepgram transcription, the service logs the request id, duration,
and best-effort request cost in USD when Deepgram's usage API has recorded it.

The production defaults are:

```
DEEPGRAM_MODEL=nova-3
STT_LANGUAGE=ru
DEEPGRAM_DIARIZE_MODEL=latest
DEEPGRAM_AUDIO_SOURCE=m4a_copy  # m4a_copy, mp3_96k, or mp3_192k
DEEPGRAM_TXT_FORMATTER=word_speaker
DEEPGRAM_KEYTERMS_ENABLED=true
DEEPGRAM_KEYTERMS_FILE=config/deepgram-keyterms.txt
```

`m4a_copy` extracts a temporary AAC/M4A audio copy from the source MP4 for
Deepgram without re-encoding. Use `mp3_96k` or `mp3_192k` to send a temporary
MP3 instead. With the Deepgram `m4a_copy` default, no extra Drive MP3 is uploaded
unless `DRIVE_MP3_ARTIFACT=true` is set. If an MP3 already exists but TXT is
missing, Deepgram downloads the MP4 again so it can use the selected high-quality
audio source.

`word_speaker` is a Deepgram-only TXT formatter. It uses `utterances` for readable
timing, but splits a line when `words[].speaker` changes inside the utterance.
Set `DEEPGRAM_TXT_FORMATTER=utterance` to use the older utterance-level formatter.

Keyterms are read from `DEEPGRAM_KEYTERMS_FILE`, one term per line. Blank lines
and lines beginning with `#` are ignored. At most 100 keyterms are allowed, and
they are sent only when `DEEPGRAM_MODEL=nova-3`.

Sample output:

```
[00:00:00] Speaker 1: Привет, коллеги.
[00:00:05] Speaker 2: Добрый день.
```

Deepgram sync pre-recorded requests have a processing-time limit: Nova/Base/Enhanced
requests that process for more than 10 minutes may return `504 Gateway Timeout`.
Callback mode would avoid that for long files, but it requires a public callback endpoint
and is intentionally not implemented in this first provider.

### Google (batched + diarization)

The `google` provider uses Speech-to-Text v2 `BatchRecognize` with speaker diarization,
authenticated by the same OAuth user credentials as Drive (no service account).

Setup:

1. Enable the Speech-to-Text and Cloud Storage APIs on your GCP project.
2. Create a GCS bucket (same region as your recognizer) — e.g. `gsutil mb -l us gs://<bucket>`.
3. Set `GOOGLE_CLOUD_PROJECT`, `GOOGLE_STT_GCS_BUCKET`, and `STT_LANGUAGE` (BCP-47, e.g. `en-US`) in `.env`.
4. Re-run the OAuth flow once so the new `cloud-platform` scope is granted:

   ```bash
   rm data/token.json && uv run python -m src.auth
   ```

Each MP3 is uploaded to `gs://<bucket>/stt-<uuid>-<name>.mp3`, transcribed as a single batch
job, then the staged blob is deleted. `STT_CHUNK_SECONDS` does not apply.

Sample output:

```
[00:00:00] Speaker 1: hello, thanks for joining today
[00:00:05] Speaker 2: hi, glad to be here
```

## Usage

Local run (after `src/auth` has produced a token):

```bash
uv run python -m src.main
```

The process loops forever, sleeping `POLL_INTERVAL` seconds between cycles.

### CLI

`uv sync` installs a `gdstt` console script that wraps every operation (equivalently
`uv run python -m src.cli`). All commands read configuration from `.env` / the
environment via `load_config()`.

Safe operator flow: `gdstt doctor` -> `gdstt list` -> `gdstt process <file-id> --dry-run`
-> `gdstt process <file-id>`. Move to `run-once` or continuous `run` only after
that single-file path looks correct.

```bash
gdstt auth [response_url]   # one-time interactive OAuth → data/token.json
gdstt doctor [--drive]      # check Drive/OAuth configuration without changing it
gdstt plan --json '<intent>'   # expand an agent JSON request without mutating Drive
gdstt execute --json '<intent>' [--confirm]   # execute after deterministic policy checks
gdstt plan --json-file <path>   # PowerShell-friendly JSON input; execute supports it too
gdstt run                   # continuous polling; can spend STT credits across all pending configured folders
gdstt run-once [--dry-run] [--max-size SIZE] [--confirm-large]   # single cycle; use --dry-run first
gdstt process <id> [--folder] [--reprocess-txt] [--dry-run] [--max-size SIZE] [--confirm-large]   # single target or folder; use --dry-run first
gdstt speakers set <file-id> "Alice" "Bob"   # store explicit speaker names on an MP4
gdstt refresh-names <file-id>   # rename linked MP3/TXT artifacts after an MP4 rename
gdstt transcribe <audio> [-o out.txt]   # STT-only on a local file; prints to stdout by default
gdstt list [--folder ID]   # show sibling mp3/txt state without doing work (alias: status)
```

`process` auto-detects whether the ID is a file or a folder; pass `--folder` to force
folder handling. `list`/`status` defaults to the configured `FOLDER_IDS` when `--folder`
is omitted. `--reprocess-txt` intentionally spends STT provider credits again and
overwrites the linked `.txt` when one exists. `speakers set` affects future local
post-processing; combine it with `process <file-id> --reprocess-txt` when an
already-uploaded transcript needs to be regenerated with corrected names.

Use `doctor` first when setting up a new agent or machine: it checks whether
`credentials.json`, `token.json`, and `FOLDER_IDS` are present without validating STT
provider secrets. Add `--drive` only when you want it to authenticate and list the
configured folders. Use `--dry-run` on `run-once` or folder `process` to preview pending
work without downloads, uploads, or STT calls. `--max-size` is off unless you pass it.
Use it as an optional manual safety limit before processing folders, for example
`--max-size 50MB`; files larger than the limit are skipped unless you also pass
`--confirm-large` after confirming that large files should be processed.

`run` has no preview mode and is intentionally the least safe operator entrypoint:
it keeps polling and can continue spending STT credits until you stop it. Use it
only after the single-file or `run-once --dry-run` path already matches expectations.

### Runtime reliability and summaries

The runtime treats incomplete output as failure instead of silently uploading it:

- Empty provider transcripts raise an STT error; a blank `.txt` is not uploaded.
- Transient Drive metadata lookups, folder-state listings, and downloads retry with
  bounded backoff. Uploads are not retried automatically.
- Downloads are checked against Drive metadata size; mismatched partial temp files
  are removed before retry or recovery.
- `FOLDER_IDS` containing only commas or whitespace fails configuration loading
  instead of producing a misleading no-op run.

`run-once` logs one process summary per worked file, one folder summary per folder,
and one cycle summary. The cycle summary includes pending, processed, failed,
`retry_total`, `gcs_blob_orphans`, skipped-by-size, folder-error, and duration fields.
`gcs_blob_orphans` is a subset of failed items: it means a Google STT timeout retained
a GCS blob for manual inspection or cleanup.

For recovery steps and provider-specific details, see
[`docs/skills/troubleshooting.md`](docs/skills/troubleshooting.md) and
[`docs/skills/provider-notes.md`](docs/skills/provider-notes.md).

### Agent-facing documentation

Shared repository instructions live in [`AGENTS.md`](AGENTS.md). The canonical
installable package lives in [`skills/gdstt-cli/`](skills/gdstt-cli/). It contains
one discoverable `SKILL.md`; references and examples are installed recursively as
resources that the main skill opens only when needed.

Prefer the `gh skill` workflow for installation:

```bash
gh skill preview wyrtensi/google-drive-video-stt gdstt-cli
gh skill install wyrtensi/google-drive-video-stt gdstt-cli --agent codex --scope user
gh skill update --all
```

For local checkout testing before publishing:

```bash
gh skill publish --dry-run
gh skill install . gdstt-cli --from-local --agent codex --scope user
```

Pin a known version when reproducibility matters:

```bash
gh skill install wyrtensi/google-drive-video-stt gdstt-cli@YOUR_TAG_OR_COMMIT --agent codex --scope user
```

`gh skill install --help` lists supported hosts and their install locations.
Current host choices include Codex, Claude Code, Cursor, Gemini CLI, GitHub
Copilot, Windsurf, and many more. VS Code agent mode can discover Copilot Agent
Skills from supported workspace layouts. Workspace loaders can also discover the generated
[`.agents/skills/gdstt-cli/`](.agents/skills/gdstt-cli/) and
[`.claude/skills/gdstt-cli/`](.claude/skills/gdstt-cli/) mirrors when supported.

For a host without `gh skill` integration, manually copy the canonical
`skills/gdstt-cli` directory into that host's documented skills directory. Check
the host documentation first instead of assuming an editor-specific path.

Gemini CLI can refresh workspace discovery without restarting by running
`/skills reload` in an interactive session.

Official references:

- [GitHub Copilot Agent Skills](https://docs.github.com/en/copilot/concepts/agents/about-agent-skills)
- [VS Code Agent Skills](https://code.visualstudio.com/docs/copilot/customization/agent-skills)
- [Claude Code skills](https://code.claude.com/docs/en/slash-commands)
- [Gemini CLI Agent Skills](https://geminicli.com/docs/cli/skills/)

After changing the canonical package or companion docs, refresh and validate the
generated mirrors with:

```bash
uv run python scripts/sync-agent-skills.py --write
uv run python scripts/sync-agent-skills.py --check
uv run python scripts/check-agent-skill.py
```

## Tests

```bash
uv run pytest
uv run ruff check
uv run python scripts/check-agent-skill.py
```

Deepgram has a gated live smoke test that can spend a small amount of credit. It is skipped
unless explicitly enabled:

```bash
RUN_DEEPGRAM_LIVE_TESTS=1 \
DEEPGRAM_API_KEY_FILE=/path/to/deepgram_api_secret.json \
DEEPGRAM_LIVE_AUDIO_PATH=/path/to/short-audio-or-video.mp4 \
uv run pytest tests/test_stt_deepgram_live.py -s
```

For MP4/MOV/M4V inputs, the live test extracts only the first 30 seconds to a temporary MP3.
It prints the transcript preview, Deepgram request id, duration, and best-effort USD cost
when Deepgram's usage API has recorded it.

## Docker deployment

Build and run with the bundled Compose file:

```bash
docker compose up -d --build
```

The container mounts `./data` for persistent token storage. Logs are JSON-file with a 10 MB / 3-file rotation. Restart policy is `unless-stopped`.

For a fresh VPS:

1. Copy the repo, `.env`, and `data/` (with `credentials.json` and `token.json`) to the host.
2. `docker compose up -d --build`
3. Tail logs with `docker compose logs -f` and verify a poll cycle completes.

## Project layout

```
src/
  auth.py        OAuth flow + Drive service builder
  config.py      Env var loading
  drive.py       List / download / upload helpers
  extractor.py   ffmpeg MP4 → MP3 wrapper
  notify.py      Telegram error notifier
  main.py        Polling loop + on-demand process_target entry points
  cli.py         gdstt operator CLI (argparse subcommands)
  postprocess.py Local transcript cleanup + speaker-name mapping
  openai_pipeline.py OpenAI Responses LLM transcript refinement (sync + batch)
  stt/
    base.py            STTProvider ABC (transcribe_chunk + transcribe_full hook)
    chunker.py         ffmpeg MP3 splitter (used by chunked providers)
    transcribe.py      Dispatch: full-file path or chunked path
    openai_provider.py OpenAI Whisper API client
    asr_provider.py    Self-hosted whisper-asr-webservice client
    google_provider.py Speech-to-Text v2 BatchRecognize + diarization
    deepgram_provider.py Deepgram Nova-3 + diarization
tests/           Unit tests (mock external services)
data/            Tokens, credentials, gitignored
```

## License

MIT — see `LICENSE`.
