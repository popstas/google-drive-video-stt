# Planfix call-booking integration — design

Date: 2026-08-11
Status: approved, ready for an implementation plan

## Problem

Recordings land in a manager's Drive folder with no link to the CRM task the call
belongs to. Someone has to open Planfix and paste the meeting summary by hand, and
folders also collect recordings that are not client calls at all — those spend
Deepgram and OpenAI credits for nothing.

An external system already knows about every upcoming call: when it starts, which
manager runs it, and which Planfix task it belongs to. Feeding that into gdstt lets
it (a) attach the generated keypoints to the right task automatically and (b) refuse
to transcribe recordings that match no booked call.

## Scope

1. An inbound HTTP receiver that accepts upcoming-call bookings and persists them.
2. Matching a new recording to a booking by manager email and meeting start time.
3. An opt-in gate that stops the polling loop from transcribing unmatched recordings.
4. Posting the generated preset text to the matched Planfix task as a comment.

Out of scope: reading anything back from Planfix, editing existing comments,
retrying a failed comment automatically, and any Planfix API beyond the single
`planfix_create_comment` webhook already in use.

## Decisions

These were settled during brainstorming and drive the rest of the design.

- **The meeting start time comes from the Drive file name, not `createdTime`.**
  Google Meet names carry the real start time; `createdTime` is when the finished
  recording landed on Drive — start plus the call's own length plus processing, so a
  ±15-minute window would never match it.
- **The receiver is a thread inside `gdstt run`**, on the standard library's
  `ThreadingHTTPServer`. One POST endpoint does not justify FastAPI/uvicorn in a
  project whose entire HTTP surface is `requests`, and a second container would have
  to share the same volume anyway. TLS and the public hostname belong to the nginx in
  front of it.
- **Only keypoints-style preset text goes to Planfix, once, at the end.** The match
  itself sends nothing; it only remembers the `task_id`.
- **Bookings live in an append-only JSONL journal**, deduplicated by `task_id` on
  read so a rescheduled call is just a newer line. It greps by eye when someone asks
  why a recording did not match.
- **An unmatched recording is marked on Drive and never retried** by the polling
  loop. The escape hatch is the manual path, which ignores the mark.
- **The gate applies to `gdstt run` only.** Matching still runs on the manual
  commands — the `task_id` is needed there too — but neither the block nor the mark
  applies, so `gdstt process <id>` always transcribes.
- **The POST body is the payload.** `{"start_time", "task_id", "manager_email"}`
  arrives as the JSON body; the bearer token arrives as an HTTP header. There is no
  envelope to unwrap.
- **Journal retention is a 30-day constant, not a setting.** Entries older than that
  are dropped on read.

## Architecture

Five new modules. Each one owns a single concern, and only the last one knows about
more than its own data.

### `src/meeting_time.py`

`parse_meeting_start(drive_name: str) -> datetime | None` — timezone-aware, normalized
to UTC. Pure string work: no Drive, no config, no clock.

The recording names in production take these shapes:

```
cnv-bezm-efi (2026-07-19 10:25 GMT+2)
meeting ... - 2026/07/02 21:56 CEST - Recording
... - 2026/08/06 16:29 GMT+04:00 – Recording
... - 2026/08/08 09:00 GMT+04:00 – Recording
... - 2026_07_04 08_59 GMT+04_00 – Recording      (local, sanitized copy)
```

So the parser tolerates:

- date separators `/`, `-`, `_`;
- time separators `:`, `_`;
- a zone written either as `GMT±H` / `GMT±HH:MM` / `GMT±HH_MM`, or as an
  abbreviation resolved through a module-level table (`UTC`, `GMT`, `MSK`, `CET`,
  `CEST`, `EET`, `EEST`, `BST`, `IST`, and the US `EST`/`EDT`/`CST`/`CDT`/`MST`/
  `MDT`/`PST`/`PDT`);
- the surrounding punctuation being parentheses, a hyphen, or an en dash.

An unknown abbreviation or a name with no time block returns `None`. The table is a
code constant rather than config: a wrong offset silently mis-times every match, so
it should change under review, not in a YAML file.

### `src/call_booking.py`

```python
@dataclass(frozen=True)
class CallBooking:
    task_id: str
    manager_email: str
    start_time: datetime   # aware, UTC
```

- `append(path, booking)` — one JSON line, appended under an `fcntl` lock so a burst
  of concurrent webhook requests cannot interleave a torn write.
- `load(path)` — reads every line, skips unparseable ones with a warning, drops
  entries whose `start_time` is more than 30 days old, and deduplicates by `task_id`
  keeping the last occurrence. A missing file is an empty journal, not an error; an
  I/O error propagates.
- `match(bookings, *, email, video_start, threshold_minutes) -> CallBooking | None` —
  keeps bookings whose `manager_email` equals `email` case-insensitively and whose
  `start_time` is within `threshold_minutes` of `video_start`, then returns the
  closest one in time. Two back-to-back calls by the same manager therefore resolve
  to the nearer booking instead of the first one seen.

Knows nothing about Drive or HTTP.

### `src/booking_server.py`

A `ThreadingHTTPServer` in a daemon thread.

- `POST /` — compares the `Authorization: Bearer …` value against the configured
  token with `hmac.compare_digest`, validates the three fields, parses `start_time`
  as ISO-8601 (a trailing `Z` means UTC), appends to the journal, answers `204`.
- `GET /health` — `200`, for container and reverse-proxy probes.
- Status codes: `401` for a missing or wrong token, `413` for a body over 64 KiB,
  `400` for malformed JSON, a missing field, an unparseable `start_time`, or a
  `task_id` that is not a decimal integer, `500` for anything unexpected. No response
  body ever echoes the request.

`task_id` is kept as a string everywhere (it is an identifier, not a quantity) and
converted with `int()` only when building the Planfix body. Rejecting a non-numeric
`task_id` at intake is what makes that conversion safe: the alternative is a
`ValueError` hours later, on the success path of a file that already cost money to
transcribe.
- `start(config) -> BookingServer | None` and `is_running()`. Started from `main()`
  only when `call_booking.enabled` is set. A handler exception is logged by type and
  never kills the thread.

### `src/planfix.py`

`send_comment(*, url, token, proxy_url, task_id, description)` mirrors
`webhook.notify_complete`'s contract exactly: a blank URL is a `logger.debug` no-op,
a 10-second timeout, and failures log only the exception type plus the HTTP status.
The body is `{"taskId": <int>, "description": <text>}` — the shape in
`data/send-to-planfix-example.sh`.

`description` is meeting content, so it never reaches a log line.

### `src/booking_gate.py`

The only module that sees both Drive and the journal.

```python
Decision = disabled | matched(task_id, booking) | unmatched(reason)
```

- `resolve(file_info, folder_id, config) -> Decision`, evaluated in order:
  feature disabled → `disabled`; the folder's employee has no `email` →
  `unmatched("no-folder-email")`; `meeting_time.parse_meeting_start` returns `None` →
  `unmatched("no-meeting-time")`; `call_booking.match` returns `None` →
  `unmatched("no-booking")`; otherwise `matched`.
- `mark_unmatched(service, file_id)` — sets `appProperties.booking_match="none"`.

### Changes to existing modules

Deliberately small, so the new behavior stays testable on its own:

- `src/drive.py` — a metadata-only `update_app_properties(service, file_id, props)`,
  plus `booking_match` and `planfix_comment_task_id` surfaced onto each item in
  `list_folder_state`. The listing already requests `appProperties`, so this reads
  two more keys off data it is fetching anyway — no extra API call.
- `src/config.py` — the `call_booking` and `planfix` sections plus two validations.
- `src/main.py` — roughly six lines of gate at the top of `process_item`, the Planfix
  send next to the existing `notify_complete` block, the marked-file filter in
  `run_once`, a `skipped_unmatched` counter in the cycle summary, and the server
  start in `main()`.
- `src/cli.py` — `gdstt bookings list` prints the journal after dedupe and pruning
  (task id, manager email, start time), which is the first thing to look at when a
  recording did not match. `gdstt bookings rematch <file-id>` clears the
  `booking_match` property on that mp4 so the polling loop reconsiders it on the next
  cycle; it neither transcribes nor spends anything itself.

## Data flow

### Booking intake

```
POST /  +  Authorization: Bearer <token>
{"start_time":"2026-08-11T07:00:00.000000Z","task_id":"851030","manager_email":"manager@example.com"}
  → token compared with compare_digest
  → start_time parsed to aware UTC
  → call_booking.append → <GDSTT_HOME>/call_bookings.jsonl
  → 204
```

### Automatic cycle (`gdstt run` → `run_once` → `process_item`, `enforce_booking_gate=True`)

```
mp4 from list_folder_state
  ├─ appProperties.booking_match == "none" → filtered out in run_once, never reaches process_item
  ↓
booking_gate.resolve(...)
  ├─ disabled            → process exactly as today
  ├─ unmatched(reason)   → if disable_recognition and booking_server.is_running():
  │                            drive.update_app_properties(booking_match="none")
  │                            log + return None, counted as skipped_unmatched
  │                        if disable_recognition and the server is NOT running:
  │                            skip without marking, retry next cycle
  │                        if not disable_recognition:
  │                            process normally, send nothing to Planfix
  └─ matched(task_id)    → process normally, remember task_id
  ↓
mp3 → STT → presets → artifacts written
```

The marked-file filter lives in `run_once`, not in `_pending_items`: that helper is
shared with `process_target`'s folder branch, so filtering there would also disable
manual folder processing, which contradicts the gate-applies-to-`run`-only decision.

### Manual paths (`process`, `latest`, `transcribe`, `reprocess`)

Identical, except `enforce_booking_gate` defaults to `False`: `resolve` still runs so
a matched call still gets its Planfix comment, but nothing is blocked and nothing is
marked. Processing a previously marked file by hand is the supported way to undo a
mark, alongside `gdstt bookings rematch`.

### Planfix comment

On the success path of `process_item`, immediately after `notify_complete`, under the
same guard and the same preconditions (no `unproduced` presets, something was
actually produced):

```
decision is matched  and  planfix.create_comment_url is set
  ├─ appProperties.planfix_comment_task_id already present → skip
  └─ description = "\n\n".join(f"## {name}\n{text}" for name in planfix.presets if produced)
     → planfix.send_comment(task_id=int(task_id), description=description)
     ├─ success → drive.update_app_properties(planfix_comment_task_id=task_id)
     └─ failure → warning + notify_error to Telegram; no mark, so `gdstt reprocess` can resend
```

The mark is what makes this idempotent. `process_item` can legitimately reach the
success path more than once per file — a later cycle that backfills a newly
configured preset re-feeds the transcript — and without the mark that second pass
would post a duplicate comment into the task.

## Configuration

```yaml
call_booking:
  enabled: false            # off by default: a pre-existing config must not start opening ports
  listen_host: 0.0.0.0
  listen_port: 8080
  authorization_token: ''
  threshold_minutes: 15
  disable_recognition: false
planfix:
  create_comment_url: ''    # blank disables the comment
  token: ''
  presets: [keypoints]
```

Two load-time validations:

- `call_booking.enabled: true` with a blank `authorization_token` is a setup error —
  an unauthenticated public endpoint must not open silently.
- `disable_recognition: true` while some `folders` entry has no `email` is a setup
  error: that folder could never match a booking, so it would be permanently and
  invisibly dead.

`docker-compose.yml` publishes `listen_port`; TLS and the hostname stay with the
reverse proxy in front of the container.

## Error handling

The dangerous failure here is not a crash, it is silent mass-marking: if the receiver
never came up while `disable_recognition` is on, every recording looks unmatched and
gets marked forever. Hence the central invariant:

> `booking_match="none"` is written only while the booking receiver is actually
> listening in this process.

A bind failure (port taken, no permission) logs an error, sends `notify_error` to
Telegram, and leaves the polling loop running with the gate degraded to
skip-without-marking. Nothing is lost and no credits are spent.

The rest follows the project's existing tiers:

- **Journal** — a torn line is skipped with a warning; a read error propagates so the
  file retries and, critically, is not marked; a missing file is simply "no bookings".
- **Name parsing** — an unknown zone or a missing time block is `None`, not an
  exception, logged with the file name so a new naming format is visible.
- **Planfix** — fire-and-forget, like the completion webhook: the transcript and
  artifacts are already uploaded and a failed POST must not undo that. Unlike the
  completion webhook it also notifies Telegram, because a comment that never reached
  the CRM is otherwise invisible to a human.
- **Secrets** — neither token is logged, and `gdstt doctor` reports only set/unset.

## Testing

Every new module is tested on plain data, with no Drive and no pipeline.

- `test_meeting_time.py` — a table of the production name shapes above, plus
  `GMT+2` vs `GMT+04:00`, hyphen vs en dash, parentheses vs ` - `, an unknown
  abbreviation, and a name with no time.
- `test_call_booking.py` — append/load round trip, dedupe by `task_id` (last wins),
  30-day pruning, a corrupt line not breaking the read; matching within and outside
  the threshold, case-insensitive email, and two candidates resolving to the nearer.
- `test_booking_server.py` — a real server bound to `127.0.0.1:0`, driven with
  `http.client`: `204`; `401` for missing and for wrong token; `400` for bad JSON, a
  missing field, and a bad `start_time`; `413`; `GET /health` → `200`. Loopback
  sockets only, no external network.
- `test_planfix.py` — mirrors `test_webhook.py`: payload shape, bearer header, blank
  URL no-op, and failure logs carrying neither body nor token.
- `test_booking_gate.py` — every `Decision` branch against a stub config and a fake
  Drive service.
- `test_main.py` additions — `run_once` blocks and marks; the manual path ignores both
  the mark and the gate; Planfix is sent on success, skipped when
  `planfix_comment_task_id` is already set, and withheld when presets went
  `unproduced`; nothing is marked while the server is not running.
- `test_config.py` additions — parsing, defaults, both validations, and the new
  sections appearing in the generated config.
- `test_docker_deploy.py` and `test_skill_docs.py` — the published port, and the
  refreshed `SKILL.md` / `README.md` / `AGENTS.md`.

## Documentation

`AGENTS.md` gains the call-booking flow and its invariants; `README.md` gains the
receiver setup (port, reverse proxy, token); `skills/gdstt-cli/SKILL.md` gains
`gdstt bookings list|rematch` and the "why was this recording skipped" answer, with
its `version` and `last_updated` refreshed.
