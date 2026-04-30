# Google Drive Video STT — Full Implementation

## Overview
- Build a service that monitors Google Drive folders for new MP4 files, extracts audio to MP3 via ffmpeg, and uploads the MP3 alongside the original
- Solves: NotebookLM rejects files >200 MB, Cloud STT doesn't accept MP4 directly
- Runs as a Docker container with polling loop, zero user interaction after deploy
- Error notifications via Telegram bot, success is silent (logs only)

## Context (from discovery)
- Files/components involved: fresh repo, all code to be created from scratch
- Project structure defined in `docs/plan.md` — `src/` with 6 modules + tests
- Dependencies: `google-api-python-client`, `google-auth-oauthlib`, `requests`, `ffmpeg` (system)
- Package management: `uv` with `pyproject.toml`
- All mutable data in `./data/` (gitignored)

## Development Approach
- **Testing approach**: Regular (code first, then tests)
- Complete each task fully before moving to the next
- Make small, focused changes
- **CRITICAL: every task MUST include new/updated tests** for code changes in that task
- **CRITICAL: all tests must pass before starting next task**
- **CRITICAL: update this plan file when scope changes during implementation**
- Run tests after each change
- Maintain backward compatibility

## Testing Strategy
- **Unit tests**: required for every task
- Mock external services (Google Drive API, Telegram API) in tests
- Test ffmpeg wrapper with a small real audio file if possible, or mock subprocess
- No e2e tests (headless service, not a UI)

## Progress Tracking
- Mark completed items with `[x]` immediately when done
- Add newly discovered tasks with + prefix
- Document issues/blockers with warning prefix
- Update plan if implementation deviates from original scope

## Implementation Steps

### Task 1: Project scaffolding and dependencies
- [x] Create `pyproject.toml` with project metadata, Python 3.11+ requirement, and dependencies: `google-api-python-client`, `google-auth-oauthlib`, `google-auth-httplib2`, `requests`
- [x] Add dev dependencies: `pytest`, `pytest-mock`
- [x] Create `src/__init__.py` (empty)
- [x] Create `.env.example` with all env vars: `FOLDER_IDS`, `POLL_INTERVAL`, `BITRATE`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- [x] Run `uv sync` to verify dependencies install correctly
- [x] Verify project structure is correct

### Task 2: Configuration module (`src/config.py`)
- [x] Implement `config.py`: load env vars with defaults — `FOLDER_IDS` (comma-separated list), `POLL_INTERVAL` (default 600), `BITRATE` (default "96k"), `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `DATA_DIR` (default "data")
- [x] Parse `FOLDER_IDS` into a list
- [x] Write tests for config loading with various env combinations
- [x] Write tests for edge cases (empty FOLDER_IDS, missing vars)
- [x] Run tests — must pass before next task

### Task 3: OAuth authentication module (`src/auth.py`)
- [x] Implement `build_drive_service()`: load token from `data/token.json`, refresh if expired, return authorized Drive service
- [x] Implement `__main__` block: run interactive OAuth flow, save token to `data/token.json` using `credentials.json`
- [x] Handle missing `credentials.json` with clear error message
- [x] Handle expired/invalid token with re-auth prompt
- [x] Write tests for token loading and refresh logic (mock google auth)
- [x] Write tests for error cases (missing files, invalid token)
- [x] Run tests — must pass before next task

### Task 4: Drive API module (`src/drive.py`)
- [x] Implement `list_unprocessed_mp4(service, folder_id)`: list MP4 files in folder, filter out those that already have sibling MP3
- [x] Implement `download(service, file_id, dest_dir) -> Path`: download file to local path
- [x] Implement `upload(service, local_path, folder_id)`: upload file to Drive folder with correct MIME type
- [x] Write tests for `list_unprocessed_mp4` — mock Drive API responses for: no files, all processed, some unprocessed
- [x] Write tests for `download` — mock file download
- [x] Write tests for `upload` — mock file upload
- [x] Run tests — must pass before next task

### Task 5: FFmpeg extractor module (`src/extractor.py`)
- [x] Implement `extract_mp3(mp4_path: Path, bitrate="96k") -> Path`: run ffmpeg subprocess to extract audio
- [x] Handle ffmpeg errors (non-zero exit, missing binary) with clear exceptions
- [x] Write tests for successful extraction (mock subprocess or use tiny test fixture)
- [x] Write tests for error cases (ffmpeg failure, missing input file)
- [x] Run tests — must pass before next task

### Task 6: Telegram notification module (`src/notify.py`)
- [x] Implement `notify_error(text: str)`: send message via Telegram bot API, truncate to 4000 chars
- [x] Gracefully skip if `TELEGRAM_BOT_TOKEN` or `TELEGRAM_CHAT_ID` not set
- [x] Never raise — catch and log exceptions from Telegram API
- [x] Write tests for successful notification (mock requests.post)
- [x] Write tests for: missing token/chat_id (no-op), API failure (logged, not raised), message truncation
- [x] Run tests — must pass before next task

### Task 7: Main loop (`src/main.py`)
- [ ] Implement `process_file(service, file_info, folder_id)`: download MP4, extract MP3, upload MP3, clean up temp dir
- [ ] Implement `run_once(service)`: iterate folders and unprocessed files, call `process_file`, catch per-file errors and notify
- [ ] Implement `main()`: build service, run loop with `sleep(POLL_INTERVAL)`, catch cycle-level errors and notify
- [ ] Add `if __name__ == "__main__"` entry point
- [ ] Write tests for `process_file` (mock drive + extractor)
- [ ] Write tests for `run_once` — verify error in one file doesn't stop others
- [ ] Write tests for error notification integration
- [ ] Run tests — must pass before next task

### Task 8: Docker setup
- [ ] Create `Dockerfile`: Python 3.11-slim base, install ffmpeg, copy project, install deps with uv
- [ ] Create `docker-compose.yml`: single service, env_file, volume mount for `./data`, tmp volume, restart policy, logging config
- [ ] Verify `docker compose build` succeeds
- [ ] Run tests — must pass before next task

### Task 9: Verify acceptance criteria
- [ ] Verify all modules import and work together (integration smoke test)
- [ ] Verify edge cases: empty folder list, all files already processed, ffmpeg failure on single file doesn't crash loop
- [ ] Run full test suite
- [ ] Run linter (`ruff check`) — all issues must be fixed
- [ ] Verify test coverage meets 80%+

### Task 10: [Final] Update documentation
- [ ] Update README.md with setup instructions, usage, deployment guide
- [ ] Create `.env.example` if not already done

## Technical Details
- **Polling**: `files.list` with query filter by folder + mimeType, every `POLL_INTERVAL` seconds
- **Idempotency**: check for `<basename>.mp3` sibling before processing
- **FFmpeg args**: `-y -i input.mp4 -vn -acodec libmp3lame -b:a 96k output.mp3`
- **OAuth scopes**: `https://www.googleapis.com/auth/drive` (full access needed for upload)
- **Temp files**: `tempfile.TemporaryDirectory()` per file, auto-cleaned
- **Token storage**: `data/token.json` with refresh_token, auto-refresh on expiry

## Post-Completion
**Manual verification:**
- Place test MP4 in monitored Drive folder, verify MP3 appears after one poll cycle
- Restart container, verify already-processed files are skipped
- Test with corrupted MP4 to verify Telegram error notification
- Test with empty `TELEGRAM_BOT_TOKEN` to verify silent mode works

**Deployment (Phase 2):**
- Copy `data/` and `.env` to VPS
- Run `docker compose up -d --build` on VPS
- Add remaining folder IDs to `FOLDER_IDS` after successful single-folder test

**Risks to monitor:**
- OAuth refresh_token expiry (7 days in Testing mode, move to Production)
- VPS disk space (500 MB per hour of video during processing)
- Drive API rate limits (12 requests per cycle, well within 1000/100s limit)
