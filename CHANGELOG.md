# Changelog


## Unreleased

### Features

- Add reasoning_effort config, globally and per preset

## v0.7.0 - 2026-08-14

### Features

- Add job-board and cold-outreach referral channels to the default list
- Label the Planfix header from the configured entities
- Parse meta artifacts into one value per configured entity
- Build the meta prompt from the configured entities
- Read meta entities from config.yml
- Describe meta entities as config-shaped data

### Bug Fixes

- Tell enum entities to stay empty rather than pick the nearest listed value
- Widen the target_filing prompt to accept a bare visa type
- Strip the hardcoded field name from the meta prompt's empty-value example
- Name the offending entity in a broken requires chain and quiet the empty-allowed enum warning to info
- Empty an entire requires chain, not just its nearest link
- Pin the empty-scalar literal in the meta prompt's general rules

### Documentation

- Warn that gdstt stop rewrites config.yml and strips comments
- Replace the deprecated tags/referrals sample in README with meta.entities
- Correct the stale entity-serializer comment in config.py
- Fix false claim that gdstt stop migrates meta.entities
- Describe config-driven meta entities
- Note the make_config keyword the webhook test needs
- Implementation plan for config-driven meta entities
- Spec for config-driven meta entities

### Testing

- Assert a configured entity reaches meta.yml, webhook, and planfix
- Assert the webhook payload carries one key per entity
- Keep the suite off the operator's live config

## v0.6.0 - 2026-08-13

### Features

- Show the newest sent comments first, capped by --limit
- Print both links from gdstt planfix sent
- Add gdstt planfix sent and a planfix_task_url meta field
- Separate the Planfix comment sections with blank lines
- Drop the preset name from the Planfix comment and mark the keypoints sections
- Enable the meta preset by default and retire action-items
- Open the Planfix comment with the call's subject, tags, and referral
- Publish only the .stt document to Drive
- Write a .stt document and a meta.yml for every processed recording
- Assemble keypoints, meta, and transcript into one .stt document
- Assemble a meta document describing each call
- Teach the meta preset to record the client's referral source
- Add output.also_drive to publish artifacts to Drive from folder mode
- Send the Planfix comment as HTML instead of Markdown

### Bug Fixes

- Stop a meta-header-only Planfix comment from posting and marking the task
- Correct the false "meta.yml never reaches Drive" claim in the docs
- Use source_name as the Planfix video_url anchor, normalise multi-line meta values, and cover the meta_document wiring
- Keep the .stt out of the transcript lookup and stop it duplicating on reprocess
- Leave meta client empty when the recording name has no manager marker
- Decide which speaker is which person instead of guessing from turn order

### Documentation

- Add the implementation plan for the .stt artifact and meta document
- Add spec for the .stt artifact and the meta document

## v0.5.1 - 2026-08-13

### Bug Fixes

- Make scripts/release.sh survive its own changelog hook (#17)

## v0.5.0 - 2026-08-13

### Bug Fixes

- Stop appProperty writes from moving the Drive modifiedTime, and repair the 1274 already moved (#16)

## v0.4.0 - 2026-08-13

### Features

- Add gdstt bookings list and rematch
- Start the booking receiver alongside the polling loop
- Post meeting keypoints into the matched Planfix task
- Gate the polling loop on booked calls
- Resolve booking matches and mark unmatched recordings
- Add the inbound call-booking receiver
- Add the Planfix comment client
- Add call_booking and planfix configuration
- Add the call-booking journal and matching
- Parse meeting start time from recording names

### Bug Fixes

- Close call-booking journal write/lock and listening-check gaps
- Harden the call-booking receiver against bad input and teardown races
- Mask call_booking.authorization_token in config get
- Retry socket-level Drive failures instead of failing the cycle

### Documentation

- Note the receiver-listening precondition for booking_match=none
- Document the call-booking receiver and Planfix comments
- Design restoring Drive modifiedTime after appProperty writes
- Plan the Planfix call-booking implementation
- Design the Planfix call-booking integration

### Testing

- Cover the fix-wave findings in call booking and the manual-command gate

## v0.3.0 - 2026-08-11

### Features

- Update documentation for folders, meta, and webhooks
- Verify acceptance criteria and fix docker smoke fallout
- Collapse the three deepgram-keyterms.txt copies into one example
- Add the completion webhook
- Return preset outputs from _run_preset_stage to process_item
- Add the meta preset (topic + tags) with allow-list injection
- Read tags.allowed into Config and stop dropping it
- Wire folders through the runtime and CLI
- Replace folder_ids with folders in Config
- Fix ExpertizeMe filename parsing in extract_interlocutor_names

### Bug Fixes

- Harden webhooks, config validation, and the keyterms example
- review: Complete webhook payloads and harden name parsing

### Documentation

- Publish the talks-reducer threshold report
- Record the talks-reducer threshold result
- Design the talks-reducer threshold benchmark
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

