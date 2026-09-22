# Changelog


## Unreleased

### Features

- Put the client's email from the calendar into meta and telegram
- Add the calendar.client_emails switch
- Read a call's outside invitees from its calendar event
- Link the planfix task and calendly booking in telegram summaries
- Accept several summary chats as one comma-separated string
- Show summary chats in doctor and add telegram sent
- Send booked calls to their own chats and link the meeting folder
- Build speaker candidates from the document and Meet together
- Give speaker naming the people who actually spoke
- Report who came, and document what Meet knows about a call
- Take the meeting time from Meet, not from the file name
- Let a prompt say where the people in the call belong
- Mark a call nobody came to instead of transcribing silence
- Ask Meet who was in a call, and when they were there
- Let doctor say whether Meet answers, and what it answered
- Make one conference one piece of work, whoever listed it
- Find work by asking Meet, with a mark that never steps over unfinished work
- Ask Meet for the conferences an employee has been in
- Teach every command to act as the employee who owns the file
- Resolve the fleet once, then run the cycle that already worked
- Act as the employee, and find the folder Meet writes into now
- Let a watched folder be an employee's address
- Say how many attended calls a folder will never process
- Let a shared folder's backlog stay out of scope
- Let the service run without the changes feed
- Take the speakers' names from Meet's own transcript
- Give the operator both ways of finding work, and a diagnosis worth reading
- Ask Drive what changed instead of re-reading every folder
- Leave a video alone while Drive is still processing it
- Tell apart the folder a file lives in from the folder it belongs to
- Let `latest` see into meeting subfolders
- Translate a file's own folder back to the configured one
- Walk one level of subfolders and remember where each file lives
- Post call summaries to a folder's Telegram chat

### Bug Fixes

- Summaries of a partial rerun keep their keypoints
- Read a journal's calendly uuid through the same check the receiver uses
- Give the calendar match its own half-hour window
- A bad calendly uuid drops the link, not the booking
- Match only a calendar event that starts near the call
- Alert on a failed listing only when it persists
- Say in the log when a summary reaches Telegram
- An attended call's recording is not ours to open, or to lose
- Leave outsiders' recordings alone, and bound every hold on the mark
- Stop retrying a recording nobody spoke in every cycle
- Stop three booking tests from expiring on the calendar
- Drop task checkboxes from the Telegram summary
- Leave a call the model could not place numbered, not named by the file's order
- Keep telling the presets who was on a call nobody could place
- Decide who is who from the whole call, and never bind Meet's names by order
- Stop a damaged cursor file from failing every cycle for good
- Stop `list` from contradicting what a cycle will do
- Find a newly watched folder's backlog instead of trusting the cursor
- Let the operator reports see into meeting subfolders too
- Put the mp3 beside its video, and actually take a changes cursor
- Stop three booking tests from expiring on the calendar
- Never step the cursor past work the cycle did not finish

### Documentation

- The calendar scope and the new summary lines in the fleet guide
- Plan client emails from the calendar and the us1 rerun
- Design client emails from the calendar event
- How to fill in the summary chats of a folder
- A setup walkthrough for a fleet, with the traps the reference omits
- How to rejoin a history Meet split, and what it costs
- Turn starts match by chance, which closes the question
- Say what the second measurement actually cost
- Aligning turns by time fails too, and the timings are why
- Presence cannot name speakers on real calls, and the numbers why
- Correct what an attendee can open, measured on the real fleet
- Record what landed, and what the last task still needs
- Presence names every speaker or it names none
- Plan using who was in the call, and who was not
- Answer the last open question with a call between two accounts
- Document the Meet discovery mode, and what running it proved
- Fold what the spike measured back into the Meet plan
- What the Meet API answered, and the one question it did not
- Hold the Meet mark at the work, not at the clock
- Plan asking Meet what was recorded, as a second way to find work
- Say what delegation changes, and stop doctor from misreading it
- Plan reading each employee's own Drive through delegation
- Write down why a shared Meet folder stops receiving recordings
- Say what a shortcut's target depends on, not that following it is futile
- Document the behaviour an operator will actually meet
- Write down the two rules that failed silently

### Performance

- Ask a folder for all four mime types in one query

### Testing

- A booking in the journal is what reaches the calendly chat
- Make the Drive fake answer by parent, not by guessing at the query

## v0.9.0 - 2026-08-14

### Features

- Force processing and task routing by file-name rules

## v0.8.0 - 2026-08-14

### Features

- Default to gpt-5.6-luna at low reasoning effort
- Add reasoning_effort config, globally and per preset

### Bug Fixes

- Quote list items in the meta response template

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

