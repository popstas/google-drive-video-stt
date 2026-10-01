# Setting up a fleet deployment

How to configure the service to watch a group of employees, read each one's own
Drive, and deliver summaries -- in the order the decisions actually have to be made.
Every trap named here was hit on a live deployment in September 2026, not imagined.

The settings reference is the table in the README. This is the walkthrough, and the
list of things that reference does not tell you.

## 1. Before the config: what the Google side needs

**A service account with domain-wide delegation.** The alternative is sharing each
employee's recordings folder with a single account, and sharing a recordings root is
what makes Meet abandon it -- the arrangement that grants access is the one that
breaks the folder. See [meet-recordings-folder.md](meet-recordings-folder.md).

Authorize these scopes in Admin Console -> Security -> Access and data control ->
API controls -> Manage Domain-Wide Delegation:

- `https://www.googleapis.com/auth/drive` -- reading each Drive and writing artifacts
  beside the recordings;
- `https://www.googleapis.com/auth/meetings.space.readonly` -- only for
  `discovery: meet`.

**Enable the Meet API in the Google Cloud project.** A project that never used it
answers every call with `403 SERVICE_DISABLED`, which reads like a permissions
problem and is not one. Correct delegation does not enable the API.

Nothing is granted by adding a scope to the config: the admin's authorization list is
the only thing that decides, and an unauthorized scope fails as `unauthorized_client`
at the first token request.

## 2. The folders block

```yaml
google:
  service_account_file: service-account.json   # resolved next to the config
folders:
- name: Ivan Petrov
  email: ivan@company.example
  telegram: '-1001234567890'
```

**Do not set `folder_id`.** A pinned id is a photograph of where Meet wrote the day
the config was written. Meet abandons a shared recordings root and opens a new one
beside it, so a pinned id keeps resolving, the cycle keeps reporting success, and no
new recording is ever found again. Without the id, the live root is resolved for each
address every cycle, which turns that failure into a delay of one cycle.

Pin an id only for a folder that is not a Meet root at all.

`name` is optional -- it is read from the employee's Drive profile when absent, and it
decides which speaker the transcript calls the manager. `telegram` is the chat the
summary is posted to, and it has a second effect worth knowing: **the folder is then
recognized unconditionally**, so its recordings are transcribed even with no matching
booking.

## 3. Discovery

```yaml
run:
  discovery: meet
meet:
  wait_hours: 24
  first_look_hours: 168
  fallback: walk
  skip_empty_calls: true
```

`walk` lists every meeting folder of every employee each cycle: one request per
meeting, so the cost grows with the archive rather than with the number of new calls.
On a ten-person fleet, 171 meetings meant 211 requests a cycle against about five new
calls a day.

`meet` asks the Meet API what each employee has been in -- one request per employee
whatever the size of the archive -- and finds the Drive file each recording produced.
It also brings three things `walk` cannot: the authoritative meeting start time, who
attended, and the ability to skip a call nobody but the organiser joined.

`fallback: walk` is what happens to an employee the Meet API refuses. Read section 4
before leaving it on.

Where discovery has finished is a moment in `<data-dir>/meet_checked_at.txt`. Losing
that file costs one longer listing: the next cycle looks back `first_look_hours`, and
no further than `run.since`.

## 4. run.since is a guard, not a nicety

```yaml
run:
  since: '2026-09-20'
```

Set it before the first cycle, to the date the deployment should start caring about.

The reason is not tidiness. If abandoned roots have been consolidated
([meet-folder-consolidation.md](meet-folder-consolidation.md)), every live folder now
also holds the history that came back with it, and none of that history carries
artifacts -- so a cycle with no cutoff treats all of it as work. Measured on a
ten-person fleet: `walk` found 108 recordings pending where `meet` found 22.

It is needed **even when discovery is `meet`**:

- `meet` is bounded by its look-back, but `fallback: walk` sweeps an employee the API
  refuses, and a sweep with no cutoff takes that employee's whole archive;
- `auto` with no cursor sweeps as well;
- `run.since` also clamps the meet look-back itself, so the two agree.

What it does not touch: `gdstt process <file-id>` ignores the cutoff entirely, so
manual checks on old recordings keep working after the date is set.

## 5. Delivery

Two Telegram settings that look alike and are not:

```yaml
folders:
- email: ivan@company.example
  telegram: '-1001234567890'   # where this folder's call summaries go
  telegram_calendly:           # where its booked calls also go
  - '-1005555555555'
notifications:
  telegram:
    bot_token: <token>         # one bot for both
    chat_id: '-1009876543210'  # where errors go
```

The bot must be a member of the chat, an admin for a channel. A folder with a
`telegram` value and no `bot_token` is a startup error.

Planfix is an independent channel, not an alternative: with both configured, a call
reaches the task **and** the chat. `planfix.ignore_telegram_when_planfix: true` makes
the chat a fallback instead.

### Filling in the chats

`telegram` gets every call of the employee and makes the folder recognized
unconditionally. `telegram_calendly` gets only calls matched to a booking in the
journal (what `gdstt bookings list` shows; a `name_rules` match does not count), ignores
`ignore_telegram_when_planfix` and forces nothing. The message is the same in both; a
chat listed in both fields of one folder gets it once. The field belongs to the folder,
so a supervisor who reviews booked calls is repeated on every employee who takes them:

```yaml
folders:
- email: ivan@company.example
  telegram: '-1001111111111'          # one chat: a plain value
  telegram_calendly: '-1002222222222'
- email: maria@company.example
  telegram:                           # several chats: a YAML list, one per line
  - '-1001111111111'
  - '@sales_channel'
  telegram_calendly: ['-1002222222222', '-1003333333333']   # or on one line
- email: oleg@company.example
  telegram: '241225329, 241225322'    # or one string with commas
```

- **Several chats**: a YAML list, or one string with commas -- `'-1001, -1002'` is two
  chats. Spaces around the commas, blanks and repeats are ignored.
- **Quote every id.** An unquoted `-1001234567890` still loads, but an unquoted
  `@channel` breaks the whole file: `@` cannot start a plain YAML value.
- **Where an id comes from:** add the bot, post a message in the chat, open
  `https://api.telegram.org/bot<token>/getUpdates` and take `message.chat.id`. A
  personal chat works only after that person pressed Start in the chat with the bot.
- **Edit `config.yml` by hand**: `gdstt config set` cannot reach an entry inside the
  `folders` list. Then run `gdstt doctor`, which fails on a bad file and prints every
  folder's chats, and restart the service -- a running loop does not re-read the file.

Turning on `call_booking` and `planfix` means comments start being posted to real
tasks on the first cycle. Check `gdstt doctor` for `planfix: url=set, token=set`
before enabling the service, not after.

A recording is matched to a booking by manager and start time, within
`call_booking.threshold_minutes` (15 by default), nearest booking wins. Under
`discovery: meet` the start time comes from the Meet API rather than from the
recording's name; measured across 100 real calls, the two differ by a median of 0.2
minutes and never by more than 8, so switching the source changes no match.

## 6. Two settings that mislead

**`output.also_drive` publishes one document, not four.** It adds a single `.stt`
sibling next to the video, holding the keypoints, meta and transcript sections
together. Expecting a separate transcript file and a separate keypoints file beside
the recording is the common misreading. Every artifact still lands separately in
`output.dir` locally, regardless of this setting.

**`stt.deepgram.keyterms_file` fails hard when it is set and missing.** That is
deliberate -- an explicitly configured path that cannot be read is an error, while the
default path merely warns -- but it stops the service at config load with a message
that reads like a Deepgram problem. `gdstt config init` copies the example file beside
the config; copying `src/assets/deepgram-keyterms-example.txt` by hand does the same.
The example ships with no active terms, so it biases nothing until terms are added.

## 7. The order to check things in

```bash
gdstt doctor                          # config parses, delegation on, channels as set
gdstt doctor --drive                  # every address resolves to a live root
gdstt run-once --mode meet --dry-run
gdstt run-once --mode walk --dry-run
```

The two dry runs are worth running together: the gap between their `pending` counts
is the archive the cutoff is keeping out. If `meet` reports 0 and `walk` reports 100,
the cutoff is doing its job and `fallback: walk` is a loaded gun.

`doctor --drive` also warns about any recordings root carrying a permission beyond its
owner. That root is one recording away from being abandoned, so the warning is worth
acting on the day it appears.

Only then set `run.enabled: true`.

## 8. What this arrangement still does not do

- **Shortcuts are counted, not followed.** A participant who did not organise a call
  gets a shortcut rather than the recording, and neither discovery path opens one.
  Such calls are processed through the organiser's folder when the organiser is
  configured, and not at all when they are not.
- **An employee whose only recordings root is shared has nowhere to move to.** The
  next recording opens a new root; until then, the calls in the shared one are the
  ones a consolidation cannot rescue.
- **Recordings owned outside the configured fleet are left alone.** Meet names them
  for an employee who merely attended, and the alternative would be writing artifacts
  into a stranger's Drive, so they are skipped and counted instead.
