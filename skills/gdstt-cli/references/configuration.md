# Configuration Reference

Use this reference when configuring `.env` or switching provider settings.
Never print secret values.

## Drive-only setup

- `FOLDER_IDS` - comma-separated Drive folder IDs to monitor.
- `DATA_DIR` - holds `credentials.json` and `token.json`.
- `PROXY_URL` - optional proxy for outbound provider traffic.
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` - optional runtime error notifications.

`gdstt setup` writes `FOLDER_IDS`, keeps `.env` comments and unknown keys, and
prepares the local auth files in `DATA_DIR`.

## Common runtime behavior

- `POLL_INTERVAL` - loop interval in seconds; must stay positive.
- `BITRATE` - MP3 bitrate when MP3 output is needed.
- `STT_PROVIDER` - defaults to `deepgram` when absent. Set `disabled` to skip
  transcription explicitly, or use `openai`, `google`, or `asr`.
- `STT_LANGUAGE` - provider language hint. Required for Google STT.
- `STT_CHUNK_SECONDS` - chunk size for chunking providers; ignored by Deepgram
  and Google full-file paths.
- `STT_POSTPROCESS` - local transcript cleanup and speaker mapping.

## Agent pipeline profile

- `config/pipelines/default.json` - versioned defaults for agent-driven
  `gdstt plan` and `gdstt execute`.
- `config/pipelines/local.json` - optional gitignored machine-specific override.
- The default profile uses Deepgram `m4a_copy`, enables OpenAI refinement,
  uploads Drive TXT, skips the Drive MP3 artifact, and resolves speaker names
  from the file name or Drive metadata.
- Profile version 1 requires Drive TXT upload and the `filename_or_metadata`
  speaker mode. Unsupported values fail before Drive mutation.
- `gdstt setup` prompts for every API key required by the active profile before
  execution. `gdstt doctor` and JSON plans report only `configured` or `missing`.
- JSON provider overrides preflight provider settings such as Google project,
  bucket, and language or `ASR_URL` before Drive processing starts.

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

Keep Google STT setup separate from the default `gdstt setup` wizard.

## ASR

- `ASR_URL` - required for `STT_PROVIDER=asr`.
