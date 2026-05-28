# Deepgram Summary

This folder keeps aggregate Deepgram experiment results only. Full transcript TXT
and raw JSON outputs are local temporary artifacts and are not committed.

## Current Default

Use `nova-3 + ru + latest + m4a_copy + keyterms + word_speaker`.

Rationale from local tests:

- `m4a_copy` avoided one false speaker on the long file compared with MP3 96k.
- `nova-3 + ru + latest` had fewer speaker switches than `multi`, `nova-2`, and `base`.
- `word_speaker` can split a Deepgram utterance when word-level speaker labels change.
- Keyterms target technical interview vocabulary without switching to `multi`;
  keep the term list small and validate the quality on real files.

## Previous Audio-Source Test

| video | audio | speakers | switches | cost |
| --- | ---: | ---: | ---: | ---: |
| maxim_short | mp3_96k | 2 | 9 | `$0.014440` |
| maxim_short | mp3_192k | 2 | 11 | `$0.014440` |
| maxim_short | m4a_copy | 2 | 11 | `$0.014440` |
| mikhail_long | mp3_96k | 5 | 123 | `$0.267080` |
| mikhail_long | mp3_192k | 4 | 121 | `$0.267080` |
| mikhail_long | m4a_copy | 4 | 111 | `$0.267080` |

## Previous Model Matrix

| video | variant | speakers | switches | avg confidence | cost |
| --- | ---: | ---: | ---: | ---: | ---: |
| maxim_short | nova3_ru_latest | 2 | 12 | 0.8846 | `$0.014440` |
| maxim_short | nova3_multi_latest | 2 | 13 | 0.8473 | `$0.017220` |
| maxim_short | nova3_ru_v1 | 2 | 9 | 0.8846 | `$0.014440` |
| maxim_short | nova2_ru_latest | 2 | 12 | 0.8435 | `$0.014330` |
| maxim_short | base_ru_latest | 2 | 11 | 0.7933 | `$0.041670` |
| mikhail_long | nova3_ru_latest | 4 | 111 | 0.9012 | `$0.267080` |
| mikhail_long | nova3_multi_latest | 4 | 125 | 0.9013 | `$0.318440` |
| mikhail_long | nova3_ru_v1 | 4 | 119 | 0.9010 | `$0.267080` |
| mikhail_long | nova2_ru_latest | 4 | 150 | 0.8326 | `$0.265020` |
| mikhail_long | base_ru_latest | 4 | 140 | 0.7685 | `$0.770420` |

## Final Switch Matrix

Final long-video run after implementation. Fixed settings: `nova-3 + ru + latest`.
The formatter is local, so the twelve rows below required six Deepgram API
requests: `audio_source x keyterms`.

| audio | formatter | keyterms | speakers | switches | lines | utterances | avg confidence | request cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| m4a_copy | word_speaker | true | 4 | 221 | 972 | 823 | 0.8873 | `$0.349260` |
| m4a_copy | utterance | true | 4 | 128 | 823 | 823 | 0.8873 | `$0.349260` |
| m4a_copy | word_speaker | false | 4 | 226 | 933 | 771 | 0.9004 | `$0.267080` |
| m4a_copy | utterance | false | 4 | 116 | 771 | 771 | 0.9004 | `$0.267080` |
| mp3_96k | word_speaker | true | 5 | 210 | 981 | 840 | 0.8882 | `$0.349260` |
| mp3_96k | utterance | true | 5 | 125 | 840 | 840 | 0.8882 | `$0.349260` |
| mp3_96k | word_speaker | false | 5 | 217 | 946 | 798 | 0.9038 | `$0.267080` |
| mp3_96k | utterance | false | 5 | 120 | 798 | 798 | 0.9038 | `$0.267080` |
| mp3_192k | word_speaker | true | 4 | 215 | 977 | 843 | 0.8865 | `$0.349260` |
| mp3_192k | utterance | true | 4 | 126 | 843 | 843 | 0.8865 | `$0.349260` |
| mp3_192k | word_speaker | false | 4 | 221 | 945 | 796 | 0.9038 | `$0.267080` |
| mp3_192k | utterance | false | 4 | 121 | 796 | 796 | 0.9038 | `$0.267080` |

Total API cost for the final switch matrix: `$1.849020`.

Notes:

- `m4a_copy` and `mp3_192k` kept four speakers; `mp3_96k` produced a fifth
  speaker label.
- `mp3_192k` looked closer to `m4a_copy` than `mp3_96k` on speaker count, but
  it still re-encodes the source while `m4a_copy` preserves the original audio.
- `word_speaker` creates more lines and switches because it exposes word-level
  speaker changes that the utterance formatter hides inside longer lines.
- Keyterms changed the transcript request cost on this file from `$0.267080` to
  `$0.349260`. Use qualitative term checks before expanding the keyterms list.

The same aggregate matrix is also available as
`docs/deepgram-summary/final-switch-matrix.csv`.
