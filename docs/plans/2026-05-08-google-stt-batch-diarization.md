# Google Cloud STT v2 batched provider with diarization

## Context

The current `google` STT provider uses Speech-to-Text v2 sync `Recognize` with a service-account JSON (`GOOGLE_APPLICATION_CREDENTIALS`) and returns plain text without speaker information. We want to replace it with a richer pipeline: reuse the same Google OAuth user credentials already used for Drive (no service account), submit the full MP3 as a single batched job (`BatchRecognize`), enable speaker diarization, and emit timestamped, speaker-prefixed transcripts. This gives consistent speaker IDs across the whole recording (vs. resetting per chunk) and removes the second auth path.

## Decisions (from planning)

- **Provider slot**: replace existing `google`. `STT_PROVIDER=google` now means OAuth + batch + diarization. `GOOGLE_APPLICATION_CREDENTIALS` env is removed.
- **Auth**: extend OAuth scopes to include `cloud-platform`. User re-runs `python -m src.auth` once. Drive + STT + GCS share one `token.json`.
- **GCS**: `BatchRecognize` requires gs:// URIs. User provides a bucket via new env var `GOOGLE_STT_GCS_BUCKET`. We upload the MP3 before recognize and delete the blob after (success or failure).
- **Chunking**: bypassed for the google provider — submit the full MP3 as one batch job. `STT_CHUNK_SECONDS` continues to apply to other providers.
- **Output format**: `[HH:MM:SS] Speaker N: text` lines, grouped by consecutive words from the same speaker. One line per speaker turn.
- **Testing**: regular (code first, then unit tests). Mock `SpeechClient.batch_recognize` and `storage.Client` per existing patterns.

## Context (from discovery)

- `src/stt/base.py` — `STTProvider` ABC, single method `transcribe_chunk(audio_path) -> str`.
- `src/stt/__init__.py::get_provider` — provider factory; switches on `config.stt_provider`.
- `src/stt/google_provider.py` — current sync v2 implementation to replace.
- `src/stt/transcribe.py::transcribe_file` — orchestrates chunking then per-chunk calls. Needs branch for full-file providers.
- `src/stt/chunker.py` — ffmpeg chunker; unchanged.
- `src/auth.py` — OAuth flow; `SCOPES` list and `load_credentials()` are reused. Adding `cloud-platform` invalidates existing `token.json` (refresh fails on scope change → `RefreshError` → user re-auths once).
- `src/config.py` — `Config` dataclass; STT validation block.
- Existing tests: `tests/test_stt_google.py`, `tests/test_stt_transcribe.py`, `tests/test_config.py`, `tests/test_auth.py`.
- `pyproject.toml` — already has `google-cloud-speech`. Need to add `google-cloud-storage`.
- `.env.example` — STT provider docs.

## Development Approach

- **Testing approach**: Regular (code first, then tests).
- Complete each task fully before moving to the next.
- **CRITICAL: every task MUST include new/updated tests**; tests cover success and error scenarios.
- **CRITICAL: all tests must pass before starting next task**.
- Run `uv run pytest` after each change.
- Maintain backward compatibility for non-google providers (openai, asr).

## Testing Strategy

- **Unit tests**: required per task. Mock external clients (`SpeechClient`, `storage.Client`, `Credentials`).
- **No e2e tests** in this project; manual end-to-end verification with a real GCS bucket goes in Post-Completion.

## What Goes Where

- **Implementation Steps** — code, tests, .env.example, README, dep updates.
- **Post-Completion** — re-running OAuth flow on the actual machine, creating the GCS bucket, smoke-test against real audio.

## Implementation Steps

### Task 1: Extend OAuth scope to cloud-platform
- [ ] update `SCOPES` in `src/auth.py` to `["https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/cloud-platform"]`
- [ ] update `tests/test_auth.py` assertions referencing `SCOPES` (if any) to include the new scope
- [ ] add a unit test confirming `load_credentials` raises `AuthError` with re-auth message when token scope set is missing `cloud-platform` (use `Credentials.from_authorized_user_file` mock or fixture token file)
- [ ] run `uv run pytest tests/test_auth.py` — must pass before Task 2

### Task 2: Add GCS bucket config; drop `GOOGLE_APPLICATION_CREDENTIALS` requirement
- [ ] add field `google_stt_gcs_bucket: str` to `Config` in `src/config.py`
- [ ] read `GOOGLE_STT_GCS_BUCKET` env var in `load_config()`
- [ ] remove `GOOGLE_APPLICATION_CREDENTIALS` requirement from the `stt_provider == "google"` validation block; keep the field on `Config` but no longer require it (it is now ignored — leave the env read for backward compat or remove entirely; see decision in this task)
- [ ] decide and implement: drop `google_application_credentials` from `Config` entirely (cleaner, matches "no service account" decision)
- [ ] require `GOOGLE_CLOUD_PROJECT` and `GOOGLE_STT_GCS_BUCKET` when `STT_PROVIDER=google`
- [ ] update `tests/test_config.py`: drop tests requiring `GOOGLE_APPLICATION_CREDENTIALS`, add tests for `GOOGLE_STT_GCS_BUCKET` validation (missing → ValueError, present → success)
- [ ] run `uv run pytest tests/test_config.py` — must pass before Task 3

### Task 3: Add full-file transcription hook to STTProvider
- [ ] in `src/stt/base.py`, add a non-abstract method `transcribe_full(self, audio_path: Path) -> str | None` returning `None` by default — providers that need full-file access override it
- [ ] in `src/stt/transcribe.py::transcribe_file`, call `provider.transcribe_full(mp3_path)` first; if it returns a non-None string, return it directly; otherwise fall through to existing chunking path
- [ ] add tests in `tests/test_stt_transcribe.py`: (a) provider returning `None` from `transcribe_full` falls back to chunking (existing path) (b) provider returning a string from `transcribe_full` skips chunking and returns that string verbatim (c) `chunk_mp3` is not invoked in case (b) — assert via mock
- [ ] run `uv run pytest tests/test_stt_transcribe.py` — must pass before Task 4

### Task 4: Implement batched + diarization GoogleProvider
- [ ] add `google-cloud-storage` to `pyproject.toml` and run `uv lock` (note: lockfile change committed)
- [ ] rewrite `src/stt/google_provider.py`:
  - constructor takes `project: str`, `bucket: str`, `language: str`, `data_dir: Path` (for credentials); store, do not initialize clients eagerly
  - `_get_credentials()`: call `src.auth.load_credentials(data_dir)`; raise `STTError` wrapping `AuthError` with hint to re-run `python -m src.auth`
  - `_get_speech_client()`: lazy `SpeechClient(credentials=creds)`
  - `_get_storage_client()`: lazy `storage.Client(project=project, credentials=creds)`
  - `transcribe_full(audio_path)`: upload mp3 to `gs://{bucket}/{stt-{uuid}-{name}.mp3}`, run `BatchRecognize` with `RecognitionConfig(model="long", language_codes=[lang], features=RecognitionFeatures(enable_word_time_offsets=True, enable_word_confidence=False, diarization_config=SpeakerDiarizationConfig(min_speaker_count=2, max_speaker_count=6)))`, `RecognitionOutputConfig(inline_response_config=InlineOutputConfig())`, await `operation.result()`, parse, then delete the GCS blob in a `finally` block. Return the formatted transcript.
  - `transcribe_chunk(audio_path)`: route to `transcribe_full` so the provider also works if some caller bypasses `transcribe_full` (defensive).
  - helper `_format_diarized(results) -> str`: walk word-level results, group consecutive words with the same `speaker_label`, emit one line per turn formatted as `[HH:MM:SS] Speaker N: <joined words>` where the timestamp is the first word's `start_offset`.
- [ ] update `src/stt/__init__.py::get_provider` to pass `bucket=config.google_stt_gcs_bucket` and `data_dir=config.data_dir` to `GoogleProvider`
- [ ] rewrite `tests/test_stt_google.py`:
  - mock `src.auth.load_credentials` to return a sentinel credential
  - mock `google.cloud.storage.Client` and its bucket/blob `upload_from_filename` / `delete`
  - mock `google.cloud.speech_v2.SpeechClient.batch_recognize` to return an operation whose `result()` yields a fake `BatchRecognizeResponse` with one file containing two diarized turns
  - assert: GCS upload called with `gs://{bucket}/...` URI; batch_recognize called with `model="long"` and diarization features; output equals `"[00:00:00] Speaker 1: hello world\n[00:00:05] Speaker 2: hi there"` (or similar from fixture)
  - assert blob is deleted on both success and exception paths (use `pytest.raises` plus mock assertion)
  - assert auth error path: when `load_credentials` raises `AuthError`, provider raises `STTError`
- [ ] run `uv run pytest tests/test_stt_google.py` — must pass before Task 5

### Task 5: Documentation and example env
- [ ] update `.env.example`: remove `GOOGLE_APPLICATION_CREDENTIALS`; add `GOOGLE_STT_GCS_BUCKET`; clarify that `STT_PROVIDER=google` now uses OAuth (no service account) and batched diarization
- [ ] note in `.env.example` that `STT_CHUNK_SECONDS` is ignored when `STT_PROVIDER=google` (full-file batch)
- [ ] update `README.md` (if it documents STT setup): remove service-account instructions; add (a) re-auth note for new scope (b) bucket-creation note (`gsutil mb` or console) (c) sample diarized output snippet
- [ ] no test changes for this task

### Task 6: Verify acceptance criteria
- [ ] verify all decisions from the Decisions section are reflected in code
- [ ] run full test suite: `uv run pytest` — all tests must pass
- [ ] run `uv run python -c "from src.config import load_config; load_config()"` with a sample `.env` to confirm validation
- [ ] run linter if project uses one (`uv run ruff check` or equivalent) — fix any issues
- [ ] verify `transcribe_chunk` is no longer the entry point for google provider (logs in `transcribe.py` should reflect "transcribing full file" branch)

## Technical Details

### Files modified
- `src/auth.py` — `SCOPES` list.
- `src/config.py` — `Config` dataclass + `load_config` validation.
- `src/stt/base.py` — add `transcribe_full` default.
- `src/stt/transcribe.py` — branch on `transcribe_full` result.
- `src/stt/google_provider.py` — full rewrite.
- `src/stt/__init__.py` — pass new args to `GoogleProvider`.
- `pyproject.toml`, `uv.lock` — add `google-cloud-storage`.
- `.env.example`, `README.md` — docs.
- `tests/test_auth.py`, `tests/test_config.py`, `tests/test_stt_google.py`, `tests/test_stt_transcribe.py` — updated/expanded tests.

### Key API references (speech_v2)
- `BatchRecognizeRequest(recognizer, config, files=[BatchRecognizeFileMetadata(uri=...)], recognition_output_config=RecognitionOutputConfig(inline_response_config=InlineOutputConfig()), processing_strategy=DYNAMIC_BATCHING)`
- Diarization: `RecognitionFeatures(enable_word_time_offsets=True, diarization_config=SpeakerDiarizationConfig(min_speaker_count=2, max_speaker_count=6))`
- Model: `"long"` (supports diarization + word offsets; `chirp_2` does not currently support diarization).
- Result shape: `response.results[file_uri].transcript.results[i].alternatives[0].words[j]` — each word has `word`, `start_offset` (`Duration`), `end_offset`, `speaker_label`.

### Output formatting
Group consecutive words by `speaker_label`. Emit `[HH:MM:SS] Speaker N: <joined words>`. Speaker labels from the API are integers starting at 1 — preserve them as `Speaker {n}`.

### Cleanup ordering
GCS blob deletion must run in a `finally` to avoid orphaned blobs on errors. Log (warning level) but do not raise on delete failure.

## Progress Tracking

- Mark completed items with `[x]` immediately when done.
- Add newly discovered tasks with ➕ prefix.
- Document blockers with ⚠️ prefix.
- Update plan if implementation deviates from scope.

## Post-Completion

**Manual verification** (requires real GCP project + bucket):

- Create a GCS bucket in the same region as the recognizer (`gsutil mb -l us gs://<bucket>`).
- Re-run OAuth: `rm data/token.json && uv run python -m src.auth` (or paste the redirect URL on a headless box).
- Drop a short MP4 with two speakers in a watched Drive folder.
- Confirm the resulting `.txt` uploaded to Drive contains `[HH:MM:SS] Speaker N:` lines.
- Confirm the GCS blob is deleted after the run (`gsutil ls gs://<bucket>/`).

**External system updates**:
- None — this is a self-contained behavior change.
