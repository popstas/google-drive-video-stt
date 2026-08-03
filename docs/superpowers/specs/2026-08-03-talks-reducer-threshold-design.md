# talks-reducer threshold benchmark — design

Date: 2026-08-03
Status: executed — results in `data/results/threshold-tests/report.md` (gitignored)

**Outcome:** recommended level is `0.03`. The decision rule below did **not**
survive contact with the data — no level scored zero `major`, because feeding the
LLM the 30 *largest* divergences marks nearly everything major at every level.
The experiment switched to a random sample plus deterministic volume metrics; the
report explains the substitution. The rest of this document is the design as
approved beforehand, kept unedited as the record of what was pre-registered.

## Problem

`talks-reducer --silent-threshold` trims silence before transcription. Higher
values shrink the audio (and the Deepgram bill) but clip word edges. On listening,
`0.05` clips some word starts/ends without losing meaning; `0.1` clips noticeably
more and is suspected unusable.

Find the highest threshold that still produces a transcript equivalent in meaning
to the untrimmed audio, and record how recognition time scales with the level.

Hypothesis under test: transcription error grows with threshold.

## Scope

One-off experiment. `src/` is not modified and talks-reducer is not integrated
into the gdstt pipeline — the deliverable is a number plus a written rationale.
A follow-up integration task may be filed after the result is known.

## Input

Already prepared in `data/talks-reducer-threshold-tests/` — no talks-reducer rerun:

| file | duration | vs orig |
| --- | --- | --- |
| `meeting - orig.mp3` | 6167.3 s (102.8 min) | 100.0% |
| `meeting_preset-mp3_threshold-0.01.mp3` | 5240.4 s (87.3 min) | 85.0% |
| `meeting_preset-mp3_threshold-0.02.mp3` | 5099.1 s (85.0 min) | 82.7% |
| `meeting_preset-mp3_threshold-0.03.mp3` | 5013.1 s (83.6 min) | 81.3% |
| `meeting_preset-mp3_threshold-0.04.mp3` | 4940.1 s (82.3 min) | 80.1% |
| `meeting_preset-mp3_threshold-0.05.mp3` | 4866.0 s (81.1 min) | 78.9% |
| `meeting_preset-mp3_threshold-0.10.mp3` | 4422.6 s (73.7 min) | 71.7% |

Total ≈ 596 audio-minutes of Deepgram (≈ $5–9). All seven run in full; no
sub-sampling.

## Output layout

Everything lands in `data/results/threshold-tests/` (gitignored — the experiment
leaves no trace in the repo except the TODO checkbox):

```
data/results/threshold-tests/
  run_transcribe.py          # component 1
  analyze.py                 # components 2 + 3
  deepgram_outputs/
    orig.txt  orig.json
    threshold-0.01.txt  threshold-0.01.json   ... 0.10
    runs.json                # per-run timing + request ids
  diffs/
    threshold-0.01.md        ... 0.10
  summary.json  summary.csv  summary.md
  report.md                  # the deliverable
```

## Component 1 — transcription harness (`run_transcribe.py`)

Reuses `src.config.load_config()` and `src.stt.get_provider()` so Deepgram
parameters are exactly the production ones (`nova-3`, `ru-RU`,
`diarize_model: latest`, keyterms on, `word_speaker` formatter). Two deliberate
deviations from the pipeline:

- **No ffmpeg re-encode.** The inputs are already mp3, so they go straight into
  `transcribe_full()`. Routing them through `audio_source: m4a_copy` would add a
  second variable and the levels would differ by more than trimming.
- **No LLM post-processing.** Neither `src/postprocess.py` nor any preset runs.
  Raw diarized text only, as the task requires.

Per file it records wall-clock around `transcribe_full()`,
`provider.last_duration_seconds` (Deepgram-reported audio duration) and
`provider.last_request_id` into `runs.json`.

**Raw payload capture.** `transcribe_full()` returns formatted text and discards
the JSON. Since the run costs real money, the script captures the payload by
wrapping `src.stt.deepgram_provider._format_diarized` before the call, so the
analysis can be redone later (including with word-level timings) without paying
again. This touches a private name; it is acceptable in a throwaway local script
and must not leak into `src/`. If the wrap fails to bind, the run still proceeds
and only `.txt` is saved — the JSON is a bonus, not a dependency.

**Resumable.** A level whose `.txt` already exists is skipped. A crash on the
seventh file must not re-bill the first six. Per-file failures are logged and the
run continues to the next level.

## Component 2 — analysis (`analyze.py`, local, no network)

Normalization to a comparable word stream: drop `Speaker N` labels, lowercase,
strip punctuation, collapse whitespace. Speaker labels are dropped deliberately —
silence trimming shifts utterance boundaries so diarization will diverge, and
that is not what is being measured.

Metric: WER and CER of each level against the **orig** transcript, computed with
`difflib.SequenceMatcher` from the standard library. `jiwer` would give WER in one
line, but pulling a dependency for a throwaway script is not worth it, and
`SequenceMatcher` also yields the opcodes needed to extract the divergent blocks
for component 3.

Per level: word count, WER, CER, substitution / deletion / insertion counts, audio
duration, STT wall-clock, seconds of STT per audio-minute. Written to
`summary.{json,csv,md}`.

The 30 largest divergent blocks per level, with surrounding context, go to
`diffs/threshold-<level>.md`.

## Component 3 — LLM review of the sample

Those 30 blocks per level (orig fragment vs level fragment) are sent to
`gpt-5.4-mini` (key from the same config) asking whether meaning is lost:
`none` / `minor` / `major`, with a one-line justification. Counts are aggregated
per level and folded into `summary.md`.

This converts "WER = 3.2%" into "2 of the 30 worst spots actually lost meaning",
which is what the decision needs.

## Decision rule

The recommended threshold is the highest level with **zero `major`** meaning
losses in its sample. That is the hard gate. WER is reported alongside as the
supporting trend — if it jumps sharply at a level that still passed the gate, the
report says so and recommends the level below instead, showing both numbers so the
call is visible rather than buried in a formula. If `0.05` clears the gate it is
the answer and the "as close to 0.05 as possible" goal is met; otherwise the
answer is the highest level that does.

## Caveats to state in the report

- The reference is Deepgram on untrimmed audio, which has its own errors. WER
  therefore measures **divergence from the untrimmed run**, not absolute accuracy.
- One meeting, one language (ru), one set of speakers. The result is indicative,
  not proof.
- The LLM review looks only at the 30 largest divergences per level, so it bounds
  the worst case rather than surveying every difference.
- Timing is a single measurement per level over the network, so it carries
  Deepgram-side variance; it indicates the trend, not a precise cost model.

## Verification

No unit tests — this is a throwaway script, not shipped code. Verification is the
run itself plus sanity checks before trusting the numbers: every level produced a
non-empty transcript, the orig word count is in a plausible range for 102.8
minutes of speech, and `provider.last_duration_seconds` matches the ffprobe
durations in the table above.
