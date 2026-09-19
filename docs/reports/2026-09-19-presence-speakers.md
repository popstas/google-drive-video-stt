# Naming speakers from presence: measured, and rejected

## Verdict

**No-go.** Task 5 of `docs/plans/2026-09-19-meet-attendance.md` is abandoned. Who is
who stays the model's job, exactly as it is today.

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

## What survives

- **The participants themselves.** Who was in the call, and who spoke, still reach the
  prompts through `{{participants}}` and `{{participants-speakers}}`. That is Task 3,
  and it is what actually helps the model tell a manager from a client.
- **The attendance data.** Tasks 1, 2 and 4 -- who was there, the calls nobody came
  to, and the meeting's real start time -- are unaffected; none of them depends on
  this.
- **The gate.** It cost one read-only afternoon and no Deepgram bill, and it stopped a
  feature that would have relabelled real calls on a coin flip.

## Not worth reviving as a hint

Handing the model the presence windows as extra evidence was the fallback in the plan.
It is not worth building: presence has an opinion about 1.3% of turns, and that
opinion is wrong nearly half the time. Adding it to a prompt would be adding noise.
