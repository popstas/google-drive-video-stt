# Provider Notes

Reference notes for provider tuning. The main operator workflow stays in
`.agents/skills/gdstt-cli/SKILL.md`; use this file only for deeper comparisons
or when changing STT provider behavior.

## Universal switching rules

- Keep the same CLI workflow when changing providers: `doctor` -> `list` ->
  single-file `process --dry-run` -> single-file `process` -> folder-wide runs.
- `STT_PROVIDER` selects the backend; the command surface does not change.
- `OPENAI_POSTPROCESS=true` is independent of `STT_PROVIDER` and can refine
  output after any provider.
- `STT_CHUNK_SECONDS` matters only for chunking providers.

## Deepgram

Deepgram is the current operational default.

- `DEEPGRAM_MODEL` defaults to `nova-3`.
- `DEEPGRAM_DIARIZE_MODEL` supports `latest` and `v1`.
- `DEEPGRAM_AUDIO_SOURCE` supports `m4a_copy`, `mp3_96k`, and `mp3_192k`.
- `DEEPGRAM_TXT_FORMATTER` supports `word_speaker` and `utterance`.
- `DEEPGRAM_KEYTERMS_ENABLED` and `DEEPGRAM_KEYTERMS_FILE` tune domain hints.

Operational notes:

- `m4a_copy` is the default and does not create a Drive MP3 artifact unless
  `DRIVE_MP3_ARTIFACT=true`.
- Change one tuning parameter at a time and validate on a single file with
  `gdstt process <file-id> --reprocess-txt` before folder-wide processing.
- Use the empirical notes in `docs/deepgram-summary/README.md` for deeper model,
  formatter, and audio-source comparisons.

## Google STT

- Requires `GOOGLE_CLOUD_PROJECT` and `GOOGLE_STT_GCS_BUCKET`.
- Requires explicit `STT_LANGUAGE`.
- Uses a full-file path through GCS rather than chunking.
- Client-side timeout can retain the uploaded blob for manual cleanup.

Keep Google STT setup separate from Drive-only auth setup.

## OpenAI STT

- Requires `OPENAI_API_KEY`.
- Uses chunking; `STT_CHUNK_SECONDS` applies.
- Does not provide the same diarized output contract as Deepgram or Google.

Do not confuse `STT_PROVIDER=openai` with `OPENAI_POSTPROCESS=true`.

## ASR

- Requires `ASR_URL`.
- Uses chunking; `STT_CHUNK_SECONDS` applies.
- Best suited when you need a self-hosted path.

## When changing provider defaults

- Update `AGENTS.md` if a provider-level invariant changes.
- Update `.agents/skills/gdstt-cli/SKILL.md` if the default operator flow changes.
- Update `tests/test_skill_docs.py` whenever new required env vars or operator
  behaviors become part of the contract.