# Preserving Drive modifiedTime on appProperty writes

**Status:** approved, not yet implemented
**Date:** 2026-08-13

## Problem

Writing an appProperty to a Drive file counts as an edit. `files().update()` bumps
`modifiedTime`, sets `lastModifyingUser`, and appends "You edited an item" to the
file's activity feed — even when the request body carries nothing but
`appProperties` and the file's content is untouched.

On 2026-08-11 the polling loop ran its first cycle with
`call_booking.disable_recognition: true` and stamped `booking_match=none` on the
entire backlog. Between `22:10:30` and `22:23:13` UTC it marked **1272 files**.
Every one of them jumped to that timestamp in Drive's "Last modified" column.

A survey of all six configured folders (read-only, 2026-08-13):

```
total              1276
clean                 0     modifiedTime == createdTime on no file at all
drifted            1276
  in_backlog_window 1272    the 22:00–22:30 UTC marking run
  marked_later         2    new recordings, marked by the gate on 08-12
  other_drift          2    not ours
has_booking_mark   1274
has_planfix_mark      0
```

The two "not ours" files carry no appProperties, were last modified by
`smirnov@expertizeme.org`, and drift by 0.8 s (`15:09:02.818` → `15:09:03.633`) —
that is Drive's own upload behaviour, not an edit.

People sort these folders by "Last modified" and some count recordings by that
date. Both broke.

This is not a one-off: every new recording without a matching booking is marked
within minutes of upload, so the column re-breaks daily until the cause is fixed.

## Goal

1. An appProperty write must not change a file's `modifiedTime`.
2. The 1274 already-stamped files get their `modifiedTime` restored.

## Non-goals

**The activity feed cannot be repaired.** The "You edited an item" entries from
03:20 are permanent, and the repair pass adds one more entry per file. Drive
exposes no way to delete activity history. Sorting and date-based counting are
fully recoverable; the feed is not.

**Sub-second fidelity is unrecoverable.** The original `modifiedTime` — upload
time plus a fraction of a second — was overwritten and never recorded anywhere.
`createdTime` is the closest available value, so restored files land under a
second away from the truth. Irrelevant for sorting and counting.

## Design

### Prevention: `drive.set_file_app_properties` preserves the timestamp

The function's docstring already promises to "merge appProperties onto a Drive
file **without changing its content**". Bumping `modifiedTime` breaks that
promise. The fix makes the function honour it: read the file's current
`modifiedTime`, then send it back in the same `files().update()` call alongside
the properties. `modifiedTime` is writable in Drive API v3 (`readOnly: false` in
the discovery document), so the write lands as a no-op on the date.

**No opt-in flag.** All four call sites write bookkeeping properties, not user
edits:

| Call site | Property |
| --- | --- |
| `booking_gate.mark_unmatched` | `booking_match=none` |
| `booking_gate.clear_mark` | `booking_match` deleted |
| `main.process_item` | `planfix_comment_task_id` |
| `cli.cmd_speakers_set` | `speaker_names` |

A flag would mean a future call site forgets to pass it and the bug returns
silently. The guarantee belongs inside the one function that makes the write.

Cost: one extra `files().get(fields="modifiedTime")` per property write — a
handful of calls per day in steady state.

### Repair: `gdstt bookings restore-dates`

A subcommand of the existing `bookings` group, because its selection criterion is
literally "files carrying the `booking_match` mark". No new top-level namespace
for a single command.

Behaviour: walk the configured folders, list mp4 files with
`id, name, createdTime, modifiedTime, appProperties`, and select those where

- `appProperties` contains `booking_match`, **and**
- `modifiedTime != createdTime`

then set `modifiedTime = createdTime` on each. The `appProperties` predicate is
what keeps the two naturally-drifted files untouched; a timestamp-window filter
would have caught only 1272 of the 1274 and risked unrelated files.

`--dry-run` prints the selected files and the total without writing. It is the
default way to use the command first: the mess this repairs was itself made by a
mass write that nobody previewed.

The update body carries only `modifiedTime`, so `appProperties` survive — the
marks must stay, or the backlog would be reconsidered and re-transcribed.

## Testing

Both changes are unit-testable against a mocked Drive service; no test touches a
real Drive.

- `set_file_app_properties` sends the file's current `modifiedTime` in the update
  body, and still sends the properties unchanged
- a property write whose value is `None` (the `clear_mark` delete path) also
  preserves the timestamp
- `restore-dates` selects a file with `booking_match` and drifted times
- it skips a file with drifted times but no `booking_match` — the regression that
  would damage unrelated files
- it skips a file already at `modifiedTime == createdTime` — the pass is
  idempotent and a second run is a no-op
- `--dry-run` performs no `update()` call at all

## Rollout

1. Merge PR #14, so `BOOKING_MATCH_PROPERTY` exists on `main`.
2. One branch off `main` carrying both changes.
3. Deploy prevention first, then run `restore-dates --dry-run`, read the list,
   then run it for real. Prevention first means the repaired dates stay repaired.

## Risks

**A wrong filter damages real edit history.** 1274 production files are touched.
The `booking_match` predicate is narrow by construction, `--dry-run` shows the
list before anything is written, and the operation is idempotent — but a file a
human genuinely edited *after* it was marked would have that edit's timestamp
reset. Nothing in the survey suggests such a file exists; the command reports its
selection so the operator can see for themselves before committing.

**The repair is visible.** Each restored file gains a fresh activity entry.
Employees who noticed the 03:20 edits will see a second wave.
