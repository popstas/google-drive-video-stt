# Asking Meet what was recorded, instead of searching Drive for it

## Overview

- A second way to find work: Google's Meet REST API lists the conferences an
  employee took part in and, for each, the recording it produced -- including the id
  of the file in Drive. One request per employee answers "anything new?", whatever
  the size of the archive.
- It is added as a **discovery mode**, not a replacement. `run.discovery: meet` uses
  it; everything that exists today keeps working and stays reachable, because this
  depends on a scope an admin may withdraw and on behaviour we have not yet measured.
- If the spike in Task 1 confirms what the documentation promises, this becomes the
  default for delegated deployments and the Drive walk becomes the fallback.

## Context (from discovery)

- Measured today on the real fleet: 10 employees, 171 meetings in the archive, 4.6
  new calls a day. The walk costs one request per meeting subfolder **per cycle**, so
  it is 211 requests now and about 1 890 in a year -- at which point a cycle no longer
  fits inside its own ten-minute period. The number of calls a day does not change.
- `conferenceRecords.list` accepts a filter on `start_time`, and
  `conferenceRecords.recordings.list` returns each recording with its Drive
  destination. Both accept `https://www.googleapis.com/auth/meetings.space.readonly`,
  which is the read-only member of that scope family; `meetings.space.created` is
  wider and deliberately not requested.
- The scope is **not yet authorized** for our service account's client id. Everything
  below is written so that Task 1 can invalidate it cheaply.
- Delegation is already in place (`docs/plans/2026-09-18-domain-wide-delegation.md`),
  so impersonating each employee costs nothing extra here.

## What this does not change

- The pipeline downstream of discovery. It receives the same item shape it does now:
  a Drive file plus the artifacts already beside it.
- `folder_id` as the key everything hangs on. This plan replaces how recordings are
  *found*, not how they are attributed. The live Meet root is still resolved per
  employee (two requests, cached), because that is what the employee, the Telegram
  chat, the booking gate and the webhook are keyed on.

## Development approach

- Task 1 is a spike with a written verdict; nothing else starts until it lands.
- Then test-first for the mapping rules, regular for the wiring.
- Every task carries tests; the suite must pass before the next one starts.
- With `run.discovery` left alone, not one existing test may change.

## Implementation steps

### Task 1: the spike, read-only, before any code

Against the real domain, once the admin authorizes the scope. Each question has an
answer that decides part of the design:

1. Does `conferenceRecords.list` under impersonation return an employee's conferences
   at all, and does the `start_time` filter work as documented?
2. Does it include calls the employee **attended** but did not organise? That decides
   whether the same conference arrives once or several times across the fleet.
3. Does `recordings.list` carry the Drive file id (`driveDestination.file`), and what
   states does a recording pass through before that file exists?
4. How long after a call ends does the record appear, and how long until the file is
   readable? Compare with the recording's own `createdTime` in Drive.
5. Are transcripts listed the same way, and is the transcript's Drive document the
   same one `meet_transcript` reads today?
6. What does the API do for an employee with no conferences, and what does it return
   when the scope is missing -- the message an operator will actually see?
7. What are the quotas and page sizes?

Output: `docs/reports/2026-XX-XX-meet-api.md`, and a go/no-go line at the top. A
"no" here costs one afternoon and the plan stops.

### Task 2: a client for the Meet API

`src/meet_api.py`, knowing nothing about folders or the pipeline:

- `recordings_since(credentials, since) -> list[MeetRecording]`, where
  `MeetRecording` carries the conference id, the space code, start and end time, the
  Drive file id and the state.
- Recordings whose file does not exist yet are dropped, not returned half-formed: an
  item with no file id would reach code that assumes one.
- The same failure translation delegation already has: a missing scope names the
  client id and the scope to authorize, not a raw 403.
- Tests: the filter is built from `since`; paging; a recording still being written;
  an employee with no conferences; the missing-scope message.

### Task 3: a discovery mode

- `run.discovery: meet`, alongside `auto` and `walk`. Refused without delegation,
  because the API is queried as each employee.
- `meet.lookback_hours`, default 48. The window is **stateless on purpose**: no
  cursor, no file, no "where did we stop". Whether a recording still needs work is
  already derived from what sits beside it in Drive, so re-reading the same window is
  free apart from the request itself, and a service that was down for a day catches
  up by itself.
- `_discover_by_meet(fleet, config)` returns the same `_Discovery` the walk does:
  for each employee, the recordings the API named, each mapped to its container and
  carrying the artifact flags read from that container.
- Cost per employee per cycle: one list call, plus one recordings call per conference
  in the window, plus one listing per meeting folder that actually has a new
  recording. On this fleet that is about ten requests a cycle against 211.
- Tests: a window with nothing in it costs one request per employee and returns
  nothing; a new recording becomes an item with its artifacts read; a recording whose
  file is not ready yet is left for a later cycle.

### Task 4: one conference, one piece of work

A call between two employees is listed by both. Today the walk produces it once,
because only the organiser's folder holds the file.

- Deduplicate by Drive file id inside a cycle.
- Attribute it to the employee who owns the file when one of the configured
  employees does; otherwise to the employee whose API listed it.
- Tests: the same conference from two employees yields one item, attributed to the
  owner; a conference whose file belongs to nobody configured is still processed once.

### Task 5: falling back rather than falling over

- An employee whose Meet query fails is a counted folder error, exactly as an
  unreadable folder is today, and the rest of the fleet proceeds.
- `meet.fallback: walk | none`, default `walk`: when the API fails for an employee,
  that employee is walked this cycle. The failure is still logged and counted -- a
  fallback that hides the problem is how a service ends up silently paying twice.
- Tests: one employee's API failure walks only that employee; with `fallback: none`
  it is an error and nothing else changes.

### Task 6: `doctor --drive` reports the new path

Per employee: whether the Meet API answers, how many conferences it saw in the
window, how many of those produced a recording, and how many of those are already
processed. Plus the scope failure, translated.

### Task 7: documentation and live verification

- README: the mode, the scope to authorize, the window, and the fallback.
- AGENTS: that discovery has two sources and one item shape.
- `docs/meet-recordings-folder.md`: a note that this path does not care which folder
  Meet writes into.
- Live: `doctor --drive`, then `run-once --dry-run --mode meet` against the fleet and
  a comparison with `--mode walk` on the same moment -- they must find the same
  recordings. That comparison is the acceptance test; a difference is a bug in this
  plan, not a curiosity.

## What stays as it is

- `run.discovery: walk` and the delegated Drive path remain, fully supported. They
  are what runs if the scope is withdrawn, if the API proves unreliable, or for a
  deployment that never adopts delegation.
- The Drive-side optimisation (one query per employee for recent recordings, instead
  of walking every meeting folder) is worth doing regardless: it needs no permission
  from anyone and it removes the same growth. This plan does not depend on it.

## Follow-ups, deliberately not here

- **Dropping folder resolution entirely.** If discovery never needs the Meet root,
  the only remaining use of `folder_id` is attribution, and it could become the
  employee rather than a folder. That is a change to what every downstream stage is
  keyed on, and it belongs in its own plan.
- **Transcripts from the API** instead of reading the Drive document.
- **Push notifications**, which the Meet API supports through Workspace Events: no
  polling at all, at the cost of a public HTTPS endpoint and subscription renewals.
