# Changelog


## Unreleased

### Features

- Fix ExpertizeMe filename parsing in extract_interlocutor_names

### Documentation

- Add plan for employee folders, meta preset, and webhooks

### Build

- Point pre-commit changelog hook at .venv/bin/git-cliff

## v0.2.1 - 2026-07-09

### Build

- Add git-cliff changelog generation

### Miscellaneous

- Add tests workflow and bump-my-version release tooling

## v0.2.0 - 2026-07-09

### Features

- Config-owned prompts & auth, Docker deploy, reprocess + run/stop (#11)
- Verify openai preset DAG acceptance criteria
- CLI --config flag, doctor DAG view, docs, and e2e test
- Main.py wiring and multi-artifact idempotency
- DAG executor over the OpenAI pipeline
- Add preset model, built-ins, merge, and DAG validation
- Config.yml load, auto-migration, and config migrate command

### Bug Fixes

- Address codex review findings
- Gate provider validation on enabled presets and fix data_dir round-trip
- review: Reprocess missing presets and report all preset usage

### Documentation

- Add OpenAI preset DAG design spec

## v0.1.0 - 2026-06-04

### Features

- Verify acceptance criteria for CLI/postprocess/OpenAI plan
- Add OpenAI Responses transcript post-processing pipeline
- Add transcript post-processing and speaker mapping
- Fix dropped characters in output filenames for slash-containing Drive names
- Add project skill documenting all CLI capabilities
- Add operator CLI wrapping all STT service operations
- Add Deepgram Nova-3 STT provider (#3)
- stt: Add transcription pipeline with ASR and Google STT providers (#2)
- config: Add PROXY_URL for Telegram
- Update README with setup, usage, and deployment guide
- Verify acceptance criteria for Task 9
- Add Docker setup
- Add main polling loop module
- Add Telegram notification module
- Add ffmpeg extractor module
- Add Google Drive API module
- Add OAuth authentication module
- Add configuration module with env var loading
- Scaffold project with dependencies and env template

### Bug Fixes

- Address codex review findings
- review: Surface failed OpenAI batch lines and fix stale docs
- Prevent single extracted name from collapsing all speakers
- review: Document CLI/post-processing and cover batch polling
- Address codex review findings
- Exit on RefreshError to allow Docker restart
- Chmod token.json to 0o600 to protect refresh token
- Address code review findings (pass 2)
- review: Address security and correctness issues from code review

### Documentation

- Add CLAUDE.md for Claude Code
- Add init plan.md

