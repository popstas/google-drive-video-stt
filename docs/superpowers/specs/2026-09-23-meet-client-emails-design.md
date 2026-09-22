# Client emails from the calendar event

## Goal

Every processed call carries the email addresses of the people from outside the
company who were invited to it -- in practice, the client. They land in the meta
document (`.meta.yml` and the `.stt` meta block) and in the
Telegram summary header. The Planfix comment is unchanged.

## Why the calendar

The Meet API returns a display name and an opaque user id per participant, never an
address. The calendar event behind the call has the invitees' addresses, including a
Calendly invitee (Calendly writes the booking into the host's calendar). One source
covers booked and ad-hoc calls alike.

## Design

### Lookup (`src/calendar_api.py`)

- `auth.build_calendar_service(config=..., subject=...)`: delegated credentials as the
  folder's `email`, scope `https://www.googleapis.com/auth/calendar.events.readonly`
  (`auth.CALENDAR_SCOPES`). Delegation only, like `build_meet_service`.
- `client_emails(service, *, start, own_domains, window_minutes) -> list[str]`:
  - `events().list(calendarId="primary", singleEvents=True, timeMin=start-window,
    timeMax=start+window)`.
  - Candidate events: those with a Meet link (`hangoutLink`, or a `conferenceData`
    entry point of type `video`) and a `dateTime` start.
  - Keep candidates whose start is within the window (the listing returns every
    event *overlapping* it). Prefer one with outside attendees, then the nearest.
    None -> `[]`. (Amended after review: an uncapped nearest match picked a long
    workshop over an unscheduled call, and a same-slot internal sync hid a client.)
  - From its `attendees`, drop `self`, `resource`, and any address whose domain is in
    `own_domains`. Lowercase, dedupe, sort.
- Matching is by time, not by Meet code. The code would need a `spaces.get` call and
  a `space` carried through discovery, and the walk has no conference at all. The
  meeting start from Meet is exact; the nearest event with a Meet link is enough.

### Wiring (`src/main.py`)

- `_client_emails(item, file_name, folder_id, config) -> list[str]`, called from
  `_write_call_documents`:
  - Returns `[]` without a request when `config.calendar_client_emails` is off, the
    deployment does not use delegation, the folder has no `email`, or no meeting
    start is known (`item["meeting_start"]`, else parsed from the name).
  - `own_domains`: the domains of every `folders[].email`.
  - `window_minutes`: 30, its own constant. (Amended: the booking threshold of 15
    missed a real call on us1 that started 16 minutes before its slot.)
  - Any exception -> a warning naming the recording and the exception type, `[]`.
    Never `notify_error`, never a failed recording: the address is a nicety.
- `meta_doc.build(..., client_emails=...)` -> field `client_emails` (list), added to
  `meta_entity.CODE_FIELDS` after `client`.
- `_telegram_link_lines` also renders `Email клиента: a@x.com, b@y.com` (not `Клиент:` -- the
  `client` field already means the name taken from the recording) when the list is
  non-empty, before the Planfix/Calendly links. Telegram only.

### Config

`calendar.client_emails: false` (default). Opt-in because the admin must first
authorize the scope and enable the Calendar API; until then every lookup would warn.
Parsed by `load_config`, seeded by `config init`, serialized by
`_config_to_yaml_dict`.

## Error handling

| situation | result |
| --- | --- |
| flag off / no delegation / folder without email / no meeting start | `[]`, no request |
| scope not authorized, API disabled, any HTTP error | warning, `[]` |
| no event with a Meet link in the window | `[]` |
| event found, only company attendees | `[]` |

## Testing

- `tests/test_calendar_api.py`: event selection (nearest, Meet-link filter, all-day
  events skipped, empty listing) and attendee filtering (self, resource, own domain,
  case, dedupe) against a fake service; the request's time window.
- `tests/test_config.py`: flag default, parse, seeded key.
- `tests/test_main.py`: `_client_emails` short-circuits (flag, delegation, email,
  start), swallows an exception; the meta document carries the list; the Telegram
  header renders it, the Planfix comment does not.
- `tests/test_meta_doc.py`: `client_emails` present and defaulting to `[]`.

## Live verification

On us1 (Calendar API enabled in `expertizeme-docs`, scope authorized for client id
101429969751747416130): set `calendar.client_emails: true`, rerun the last two
processed calls with `gdstt reprocess <file-id> <meta stage>` after clearing their
`telegram_sent_chat_id`, and confirm `client_emails` in `.meta.yml` and the
`Email клиента:` line in the Telegram message.

## Docs

README (scope table, the `calendar.client_emails` key, the Telegram header), AGENTS.md,
`skills/gdstt-cli/SKILL.md` (within its 550-line guard).
