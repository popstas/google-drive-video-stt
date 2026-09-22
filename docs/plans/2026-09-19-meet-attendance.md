# Who was in the call, and what that answers

## Overview

Meet knows things about a call that no file in Drive records: who was there, when
each of them joined and left, who spoke, and when the call actually started. This
plan turns that into four things the service does better, and one it stops doing at
all.

- **Nobody came, so nothing is transcribed.** A call where the manager sat alone is
  marked and skipped before anything is downloaded.
- **Who is who, decided by presence rather than by a model**, wherever presence is
  unambiguous -- which is most of a call.
- **Participants in prompts**, as `{{participants}}` and `{{participants-speakers}}`,
  so an operator writes them where they want them.
- **The meeting's real start time**, instead of parsing it out of the recording's
  file name.

## What this rests on

Measured on the real domain (see `docs/reports/2026-09-18-meet-api.md` for the API
itself), across nine real conferences:

- `conferenceRecords.participants.list` returns each participant with
  `earliestStartTime` and `latestEndTime`; `participantSessions` splits that further
  when somebody left and rejoined. One request per conference answers "who was here
  and when", and the sessions call is only needed for a participant with more than
  one session.
- **People are rarely in the call at the same time.** Across the nine conferences the
  two participants overlapped for 15s of 123s, 20s of 81s, 26s of 79s, 77s of 168s,
  120s of 182s. For the rest of each call exactly one person was present.
- **Latecomers are the norm, not the exception.** The second participant joined
  +14s, +22s, +27s, +56s, +79s after the conference started. On a long call this is
  minutes or hours, and the session windows describe it exactly.
- **Transcript entries name the speaker but are coarse**: turns overlap each other
  (9 of 10 on one conference), a 29-second "turn" carried 7 characters, and the
  language was detected as `en-US` on every Russian call. Meet's *words* are not
  trustworthy. Meet's *identities and times* are, because they come from the account
  and the connection rather than from speech recognition.
- The API reports a participant's **display name and an opaque user id, never an
  address.** There is no scope here that maps one to the other, so the manager is
  recognised by matching that display name against the folder owner's name, which
  delegation already reads.

## The idea that makes the speaker problem easy -- DISPROVEN

Everything in this section was measured against real calls after it was written, and
it is wrong. It is kept because the reasoning is what the measurement had to defeat.
See `docs/reports/2026-09-19-presence-speakers.md`.

Aligning Deepgram's transcript with Google's is a bad plan: the two segment speech
differently, merge turns differently, and Google mishears the language outright.

Presence needs no alignment. If only one person was in the call between 12:04 and
12:06, then whatever Deepgram diarized in that window was said by that person --
whatever either transcript thinks the words were. The measurements above say this
covers most of a call. What is left is the stretches where two people really were
present at once, and only there is a model needed.

So the model is not replaced. It is narrowed to the ambiguous part, and given a
firmer question.

## What this does not change

- `run.discovery: walk` and everything about it. All of this needs the conference
  record, which only `meet` mode has; in `walk` mode every feature below degrades to
  exactly today's behaviour rather than failing.
- The rule that "done or not" is read from what sits beside the video. The skip
  marker is one more artifact beside it, not a list kept somewhere.
- The item shape discovery produces. Attendance rides along as optional data; code
  that does not know about it behaves as before.

## Development approach

- Test-first for the two rules that decide money and correctness: the solo-call
  criterion and the presence-to-speaker mapping.
- Each task carries tests and the suite passes before the next one starts.
- With `run.discovery: walk`, not one existing test may change.
- Task 5 is gated on a measurement against calls whose speaker mapping a person has
  already checked, exactly as the Meet spike was gated.

## Implementation steps

### Task 1: attendance, as the API reports it -- DONE (`meet_api.attendance`)

Extend `src/meet_api.py`, which still knows nothing about folders or the pipeline:

- `attendance(service, conference) -> Attendance`, where `Attendance` carries a
  tuple of `Presence(display_name, user_id, windows, spoke)` and nothing else.
  `windows` is a tuple of `(joined, left)` in UTC, taken from the participant's
  sessions when there is more than one and from `earliestStartTime`/`latestEndTime`
  when there is one -- which is the normal case and costs no extra request.
- `spoke` comes from the transcript entries, which are read only for their
  `participant` field. The text is deliberately ignored here; it is wrong often
  enough that using it would be a trap for whoever reads this later.
- A participant who is not a signed-in user (dial-in, anonymous guest) is still a
  presence, with an empty name. They were in the room, which is the question this
  answers.
- Failure is reported, never silently an empty attendance: "nobody was here" and "I
  could not find out" lead to opposite decisions in Task 2.
- Tests: one participant; two with a latecomer; a rejoin producing two windows; a
  silent participant; an anonymous one; an unreadable conference.

### Task 2: a call nobody came to is not transcribed -- DONE (`_nobody_came`, `.skipped`)

The saving this plan pays for itself with: a call where the manager waited alone
costs a download, a Deepgram bill and three OpenAI calls today, for a recording of
silence and a "hello? …hello?".

- The rule: **two people were never in the call at the same time.** Any overlap at
  all, even a second, means they came -- because the measured real calls overlap for
  as little as 15 seconds, and any threshold above that would quietly discard real
  conversations.
- Two participants that resolve to the same signed-in user (one person on a laptop
  and a phone) are one presence, or the rule would call an empty call attended.
- **Never skip on missing data.** An attendance that could not be read is not a
  solo call; it is a call we know nothing about, and it goes through the normal path.
- The decision is made in discovery, before the video is downloaded. That is the
  whole point; deciding later would save nothing.
- The marker is a **file beside the video**, named for it, carrying a sentence that
  says why: which participants were seen, and that nothing was transcribed because
  they were never there together. A manager who opens the meeting folder gets the
  answer from the folder, which a hidden property would not give them.
- `drive.list_folder_state` reports the marker as a sibling, the way it reports
  `.txt` and `.stt`, so the next cycle sees the call as decided and does not ask
  again.
- `reprocess` overrides it: the marker is a decision, not a verdict, and forcing one
  call through is a thing an operator must be able to do.
- Tests: a solo call is marked and not processed; a call with one second of overlap
  is processed; a rejoin that never coincides is still solo; unreadable attendance is
  processed; the marker makes the next cycle skip; `reprocess` ignores the marker;
  in `walk` mode nothing changes.

### Task 3: participants where the operator wants them -- DONE (`presets.render_participants`)

- `{{participants}}` -- everyone who was in the call, including the silent.
- `{{participants-speakers}}` -- only those the transcript attributes speech to.
- Rendered **per recording, at the moment the prompt is sent**, which is a new seam:
  the existing `{{entities}}` is rendered once when the config loads, and
  participants are not config, they are this call. The two must not be confused, or
  a prompt would carry the participants of whichever call happened to load first.
- A placeholder with nothing to say leaves no trace: no empty heading, no "Participants:"
  followed by nothing. In `walk` mode, or when attendance failed, it falls back to
  the names already available (the file name, Meet's own transcript document) and
  then to nothing at all.
- `build_prompt` already injects a "Known participants" hint line. That stays for
  prompts that carry no placeholder; a prompt that carries one owns the decision, and
  the automatic line is not added on top of it.
- Tests: both placeholders render; a silent participant appears in one and not the
  other; no attendance renders nothing rather than an empty list; a prompt with no
  placeholder is unchanged; the hint line is not duplicated.

### Task 4: the time the call actually started -- DONE (`item['meeting_start']`)

- `conferenceRecords.startTime` is authoritative. Today the meeting time is parsed
  out of the recording's file name, and when that fails the gate answers
  `unmatched / no-meeting-time` and the call reaches no Planfix task.
- It rides on the discovered item as optional data. Where the pipeline asks for the
  meeting time it prefers what the conference said and falls back to
  `parse_meeting_start` -- so `walk` mode, and `gdstt process <file-id>` on a file
  with no conference, behave exactly as they do now.
- It is written into the artifacts that already carry the call's metadata, so a
  reprocess months later does not fall back to parsing a name again.
- Tests: the conference's time wins over the name; a missing conference falls back to
  the name; a file whose name carries no time is matched anyway under `meet` mode;
  the booking gate's existing behaviour is untouched in `walk` mode.

### Task 5: who is who, from presence -- ABANDONED, the gate said no

Nothing here starts until the gate below is passed.

- Convert presence windows to offsets into the recording using `recording.startTime`
  as the anchor, and **shrink each window by a few seconds at both edges**: the
  measured skew between the conference, the recording and the first turn is seconds,
  which is nothing against a window of minutes but matters exactly at a join.
- For every diarized segment, ask who was present. A segment inside a single-presence
  window is that person's, with no model involved. Votes accumulate per diarized
  speaker id.
- **Presence either names every speaker or names none.** A speaker is named when its
  votes point at one person without contradiction; if even one speaker is left
  unnamed, the whole call falls back to the path that runs today, the model reading
  both transcripts, and nothing from presence is mixed into its answer. A half-
  presence, half-model mapping would put two authorities on one call and let them
  contradict each other -- presence calling a speaker one person while the model,
  reasoning over the call as a whole, calls the same voice another.
- Two diarized speakers resolving to the same person is a real answer, not a bug: it
  means diarization split one voice, and the call had one speaker.
- **The gate:** run this against calls whose speaker mapping a person has already
  checked, and compare. It replaces the model only if it agrees; if it disagrees on
  even one call, it becomes evidence handed to the model instead, and this task stops
  there. A written verdict, as the Meet spike had.
- Tests: every speaker resolved by presence means no model call at all; one speaker
  left unresolved sends the whole call to the model, with no presence names kept; a
  latecomer's window excludes earlier segments; a boundary segment inside the guard
  is not attributed; contradictory votes count as unresolved.

### Task 6: doctor, documentation, live verification -- DONE for tasks 1-4

- `doctor --drive` per employee: how many conferences in the window had nobody but
  the organiser, and how many recordings carry a skip marker.
- README and the CLI skill: the four behaviours, the placeholders, the marker file
  and what `reprocess` does to it.
- Live: one real solo call marked and skipped end to end; one real attended call
  processed with both placeholders rendered; `--mode walk` unchanged on the same
  moment.

## What the live verification found

Run against the real domain on 2026-09-19, one delegated employee:

- `doctor --drive`: 13 conferences in the window, 10 recorded, **0 that nobody but the
  organiser came to**, 1 of 9 already processed, 1 recording this account cannot open
  (a call the employee only attended, whose file belongs to its organiser).
- `run-once --dry-run --mode meet` and `--mode walk` found **the same 8 recordings**,
  by file id, and nothing was wrongly marked.
- The attendance lookup found real join times on every call, including latecomers.

The first run of that comparison found 0 against the walk's 8: `config.presets` is a
tuple of presets, not the mapping this plan's code assumed, and an empty one of either
shape is falsy -- so every unit test passed and the first real config failed. The fix
carries a regression test with a non-empty tuple. Worth recording because it is the
second time here that a live run caught what a mocked one could not.

## What the gate decided

It was run for free, against Meet's own transcript entries as ground truth rather
than a paid re-transcription: the entries name the speaker by account, which is the
answer presence was trying to guess. Across 69 real calls and 9 544 turns, presence
could decide 1.3% of turns, was right on 55.3% of those, and named every speaker on
none of the 69 calls. `docs/reports/2026-09-19-presence-speakers.md` has the numbers
and why the plan's premise was wrong.

A second method -- aligning Deepgram's diarized clusters to Meet's turns by time,
which needs no presence at all -- was then measured with a paid transcription of two
real calls. It fails too, and the same report says why: a Meet turn averages 23
seconds carrying 13 characters, so each account's turns cover three quarters of the
call and every cluster overlaps every account about equally. The winning margins came
out at 44-55%, and on the three-account call two clusters chose the same person.

Task 5 is abandoned in both forms. The four tasks that landed do not depend on it.

## What this costs

One `participants.list` per new recording, plus one transcripts-entries read for
`spoke`, plus a sessions call only for a participant who rejoined. That is per
recording, not per cycle, so it does not grow with the archive. Against it: every
call nobody came to stops costing a Deepgram bill and three OpenAI calls.

## Follow-ups, deliberately not here

- **Matching a booking by meeting code.** `spaces.get` returns the code, but
  `CallBooking` carries no Meet link to compare it with, so this waits on the side
  that creates bookings.
- **Smart notes**, which the domain already generates, as a cross-check on keypoints.
- **Push instead of polling**, through Workspace Events.
