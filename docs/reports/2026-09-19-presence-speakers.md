# Naming speakers from Meet's timings: measured twice, and rejected

## Verdict

**No-go.** Task 5 of `docs/plans/2026-09-19-meet-attendance.md` is abandoned. Who is
who stays the model's job, exactly as it is today.

Three methods were measured -- presence, whole-turn overlap, and turn starts alone --
and all three fail, for the same measured reason:
**Meet's timestamps are not speech boundaries.** A turn averages 23 seconds carrying
13 characters -- 0.6 characters per second, where speech runs around fifteen. The
windows are roughly twenty times wider than the words in them, so on a two-person
call one account's turns cover 74% of the clock and the other's 79%. By Meet's
timings, both people are talking almost all of the time.

## What was proposed

If only one person was in the call between 12:04 and 12:06, whatever was diarized in
that window was said by them -- no model, no aligning two transcripts that segment
speech differently. The plan claimed this covers most of a call.

## What the measurement says

Meet's transcript entries name the speaker by account, which does not come from
speech recognition. That makes them ground truth for exactly this question: for every
turn, was only one person present, and was the speaker that person?

Across **69 real calls and 9 544 turns** from three employees over 30 days:

| | |
| --- | --- |
| turns where only one person was present | **1.3%** (123 of 9 544) |
| of those, the speaker really was that person | **55.3%** |
| calls where presence named every speaker | **0 of 69** |

A typical call: 53 minutes, 196 turns, 195 of them with both people present. Another:
137 minutes, 601 turns, 598 with more than one present.

## Why the plan was wrong

The claim came from measurements on short test calls, where one person joined 20-80
seconds into a 90-180 second call -- so a large share of those calls genuinely had one
person in them. On a real call both people join within a minute and stay to the end,
and the "alone" window shrinks to the first and last seconds. Worse, those are exactly
the seconds where the other person is often the one talking, which is why the little
presence could decide came out at barely better than a coin flip.

The sample was not small; it was unrepresentative, and the difference was invisible
until it was run against real conversations.

## The second method: aligning turns, not presence

The first measurement tested presence -- who was in the room -- and that was the wrong
target. The better idea is to align *turns*: Deepgram's diarized clusters carry
timestamps, Meet's entries carry timestamps and the account that spoke, so each
cluster should belong to whichever account its turns overlap most. It needs no
presence, no words, and no agreement between two transcripts.

It was measured properly, with a paid transcription of two real calls (about 100
minutes of audio, downloaded read-only, nothing written to anybody's Drive).

| call | Deepgram clusters | assignment | margin |
| --- | --- | --- | --- |
| 73 min, 3 accounts | 4 | two clusters chose the same person | 44-55% |
| 28 min, 2 accounts | 2 | one each | 50% and 54% |

A 50% margin is a coin flip: the runner-up account overlapped almost exactly as much
as the winner. On the three-account call diarization also produced four clusters and
two of them landed on one person.

The cause is the row above: when each account's turns cover three quarters of the
call, every cluster overlaps every account by roughly the same amount, and "whichever
it overlaps most" is noise.

This is not a tuning problem. No threshold, guard band or weighting recovers a signal
from timestamps that are twenty times coarser than the thing being timed.

## The third method: only the starts

The ends of Meet's turns are clearly inflated, so the obvious refinement is to use
only the *start* of each turn -- the moment somebody began speaking -- and ask which
diarized voice was talking then. Then the stricter version: keep only the moments
where a Meet turn start and a Deepgram utterance start fall within a second or two of
each other, on the grounds that those must be the same event.

Both were measured on the same two calls. The strict version is the one that settles
it, because it fails in a way that cannot be argued with:

| tolerance | 73-minute call, 3 accounts | 28-minute call, 2 accounts |
| --- | --- | --- |
| +-1s | 50-70%, two voices on one person | 50% and 57% |
| +-2s | 51-59%, same collision | 54% and 63% |
| +-3s | 52-58%, same collision | 56% and 59% |
| +-5s | 50-57%, same collision | 53% and 58% |

**Tightening the window does not sharpen the answer.** If turn starts carried real
timing, +-1s would be far cleaner than +-5s. It is flat, which is the signature of
coincidence rather than signal.

The count confirms it. Deepgram produces an utterance start every 4.0 seconds on that
call, so a two-second window catches an unrelated one about 50% of the time by
chance -- and the measured match rate is 50%. On the second call chance predicts 53%
and the measured rate is 45%, at or below it.

So the starts are not speech starts either. Every match is a coincidence, and a vote
over coincidences is a vote over nothing.

## What survives

- **The participants themselves.** Who was in the call, and who spoke, still reach the
  prompts through `{{participants}}` and `{{participants-speakers}}`. That is Task 3,
  and it is what actually helps the model tell a manager from a client.
- **The attendance data.** Tasks 1, 2 and 4 -- who was there, the calls nobody came
  to, and the meeting's real start time -- are unaffected; none of them depends on
  this.
- **The gate.** The first method was disproved for nothing, from data already there;
  the second cost about a hundred minutes of Deepgram on two real calls. Together they
  stopped a feature that would have relabelled real calls on a coin flip, which is
  cheaper than shipping it and finding out from a customer.

## Not worth reviving as a hint

Handing the model the presence windows, or the alignment's winner, as extra evidence
was the fallback in the plan. It is not worth building: presence has an opinion about
1.3% of turns and is wrong nearly half the time, and the alignment's opinion is a coin
flip by construction. Both would add noise to a prompt that already works.

What *would* change this is a source of speech timings that are actually speech
timings. Meet's are not, and nothing here can fix that from outside.
