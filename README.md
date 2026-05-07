# google-drive-video-stt

Monitors Google Drive folders for new MP4 files, extracts MP3 audio with ffmpeg, and uploads the MP3 alongside the original. Designed as a headless preprocessing step for speech-to-text pipelines (e.g. NotebookLM, which rejects files over 200 MB, and Cloud STT, which doesn't accept MP4 directly).

## Features

- Polls one or more Google Drive folders on a configurable interval
- Idempotent: skips MP4s that already have a sibling `<basename>.mp3`
- Audio extraction via ffmpeg (`libmp3lame`, configurable bitrate)
- Optional Telegram error notifications (success is silent)
- Docker-first deployment, all mutable state in `./data`

## Requirements

- Python 3.11+ and [`uv`](https://github.com/astral-sh/uv) for local development
- `ffmpeg` available on `PATH` for local runs (already included in the Docker image)
- Google Cloud project with the Drive API enabled and an OAuth client (Desktop app) — `credentials.json`
- Optional: a Telegram bot token + chat ID for error notifications

## Setup

1. Clone the repo and install dependencies (use `--extra dev` for tests/lint tools):

   ```bash
   uv sync --extra dev
   ```

2. Create a Google Cloud OAuth client (Desktop app), enable the Drive API, and download `credentials.json` into `./data/credentials.json`.

3. Copy `.env.example` to `.env` and fill in `FOLDER_IDS` (comma-separated Drive folder IDs) plus optional Telegram credentials:

   ```bash
   cp .env.example .env
   ```

4. Run the OAuth flow once to mint a refresh token. This opens a browser and writes `data/token.json`:

   ```bash
   uv run python -m src.auth
   ```

## Configuration

All configuration is environment-driven. See `.env.example`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `FOLDER_IDS` | (required) | Comma-separated Google Drive folder IDs to monitor |
| `POLL_INTERVAL` | `600` | Seconds between poll cycles |
| `BITRATE` | `96k` | MP3 audio bitrate passed to ffmpeg |
| `TELEGRAM_BOT_TOKEN` | (empty) | If set with chat ID, errors are posted to Telegram |
| `TELEGRAM_CHAT_ID` | (empty) | Telegram chat to receive error notifications |
| `DATA_DIR` | `data` | Directory holding `credentials.json` and `token.json` |
| `STT_PROVIDER` | (empty) | `openai`, `google`, `asr`, or empty to disable transcription |
| `STT_LANGUAGE` | (empty) | Language hint. `openai`/`asr`: optional (`en`, `ru`); empty = auto-detect. `google`: required BCP-47 (`en-US`, `ru-RU`) — `long` model has no auto-detect |
| `STT_CHUNK_SECONDS` | `600` | Chunk length for `openai`/`asr`. Ignored when `STT_PROVIDER=google` |
| `OPENAI_API_KEY` | — | Required when `STT_PROVIDER=openai` |
| `GOOGLE_CLOUD_PROJECT` | — | Required when `STT_PROVIDER=google` |
| `GOOGLE_STT_GCS_BUCKET` | — | Required when `STT_PROVIDER=google`; bucket used to stage MP3 uploads |
| `ASR_URL` | — | Required when `STT_PROVIDER=asr`; base URL of whisper-asr-webservice |

## Speech-to-text

Setting `STT_PROVIDER` to a non-empty value transcribes each MP3 and uploads a sibling
`<basename>.txt` next to the MP4/MP3.

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

## Tests

```bash
uv run pytest
uv run ruff check
```

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
  main.py        Polling loop entry point
  stt/
    base.py            STTProvider ABC (transcribe_chunk + transcribe_full hook)
    chunker.py         ffmpeg MP3 splitter (used by chunked providers)
    transcribe.py      Dispatch: full-file path or chunked path
    openai_provider.py OpenAI Whisper API client
    asr_provider.py    Self-hosted whisper-asr-webservice client
    google_provider.py Speech-to-Text v2 BatchRecognize + diarization
tests/           Unit tests (mock external services)
data/            Tokens, credentials, gitignored
```

## License

MIT — see `LICENSE`.
