# Configuration Reference

Use this reference when configuring `.env` or switching provider settings.
Never print secret values.

## Drive-only setup

- `FOLDER_IDS` - comma-separated Drive folder IDs to monitor.
- `DATA_DIR` - holds `credentials.json` and `token.json`.
- `PROXY_URL` - optional proxy for outbound provider traffic.
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` - optional runtime error notifications.

## Common runtime behavior

- `POLL_INTERVAL` - loop interval in seconds; must stay positive.
- `BITRATE` - MP3 bitrate when MP3 output is needed.
- `STT_PROVIDER` - `""`, `openai`, `google`, `asr`, or `deepgram`.
- `STT_LANGUAGE` - provider language hint. Required for Google STT.
- `STT_CHUNK_SECONDS` - chunk size for chunking providers; ignored by Deepgram
  and Google full-file paths.
- `STT_POSTPROCESS` - local transcript cleanup and speaker mapping.

## Deepgram

- `DEEPGRAM_API_KEY`, `DEEPGRAM_API_KEY_FILE` - credentials.
- `DEEPGRAM_MODEL` - default `nova-3`.
- `DEEPGRAM_DIARIZE_MODEL` - `latest` or `v1`.
- `DEEPGRAM_AUDIO_SOURCE` - `m4a_copy`, `mp3_96k`, or `mp3_192k`.
- `DEEPGRAM_TXT_FORMATTER` - `word_speaker` or `utterance`.
- `DEEPGRAM_KEYTERMS_ENABLED`, `DEEPGRAM_KEYTERMS_FILE` - optional hints.
- `DRIVE_MP3_ARTIFACT` - upload a Drive MP3 artifact. Defaults false for
  Deepgram `m4a_copy`, true otherwise.

## OpenAI

- `OPENAI_API_KEY` - required for `STT_PROVIDER=openai` and OpenAI
  post-processing.
- `OPENAI_POSTPROCESS` - refine text after any STT provider.
- `OPENAI_MODEL` - post-processing model, default `gpt-5.4-mini`.
- `OPENAI_BATCH` - use Batch API for lower cost and higher latency.

## Google STT

- `GOOGLE_CLOUD_PROJECT`, `GOOGLE_STT_GCS_BUCKET` - required only for
  `STT_PROVIDER=google`.
- `STT_LANGUAGE` - required BCP-47 language.

Keep Google STT setup separate from Drive-only auth setup.

## ASR

- `ASR_URL` - required for `STT_PROVIDER=asr`.
