# Command Reference

Use this reference when detailed syntax, aliases, examples, or flag
interactions are needed. Start with the smallest safe command.

## `setup`

Run the first-time local setup wizard. It creates `.env` from `.env.example`
when needed, writes `FOLDER_IDS`, defaults `STT_PROVIDER=deepgram`, prompts for
the API keys required by the active pipeline profile, discovers gcloud and ADC
client metadata when available, runs OAuth, verifies Drive access, and prints
the safe next steps.

```bash
gdstt setup
```

## `auth [--manual] [response_url]`

Run OAuth and save `token.json` into `DATA_DIR`.

```bash
gdstt auth
gdstt auth --manual
gdstt auth "http://localhost/?code=4/abc123&scope=..."
```

Normal mode opens a localhost browser flow. `--manual` prints the URL instead;
passing `response_url` completes that manual exchange.

## `doctor [--drive]`

Check local setup without changing anything. `--drive` authenticates and lists
configured folders.

```bash
gdstt doctor
gdstt doctor --drive
```

## `list [--folder ID]` / `status [--folder ID]`

Read-only folder state. Output format: `[mp3] [txt] <filename>`.

```bash
gdstt list
gdstt status --folder <folder-id>
```

## `plan --json '<intent>'`

Expand a compact agent intent into a deterministic processing plan. Planning
reports required secret readiness as booleans and does not mutate Drive.

```bash
gdstt plan --json '{"action":"process","targets":["<file-id>"]}'
```

On PowerShell, write the intent to a JSON file and use
`gdstt plan --json-file .\intent.json` to avoid native process quoting issues.

## `execute --json '<intent>' [--confirm]`

Execute the same intent after policy checks. Folder-wide processing and
`reprocess_txt` require `--confirm` unless the caller already supplied it after
reviewing the plan. Completed JSON includes best-effort OpenAI refinement token
counters under `usage.openai`; OpenAI dollar cost may remain `null`.
`txt_uploaded` and `mp3_uploaded` describe uploads that happened during that
execution. Inline JSON speaker overrides are accepted only for Drive MP4 file
targets.

```bash
gdstt execute --json '{"action":"process","targets":["<file-id>"]}'
gdstt execute --json '{"action":"process","targets":["<folder-id>"],"target_type":"folder"}' --confirm
```

`execute` also accepts `--json-file <path>`.

## `run`

Run the polling loop continuously. Ask before use because it can process all
pending configured folders repeatedly.

```bash
gdstt run
```

## `run-once [--dry-run] [--max-size SIZE] [--confirm-large]`

Run one polling cycle over configured folders.

```bash
gdstt run-once --dry-run
gdstt run-once --max-size 50MB
gdstt run-once --max-size 50MB --confirm-large
```

## `process <target> [--folder] [--reprocess-txt] [--dry-run] [--max-size SIZE] [--confirm-large]`

Process one Drive file or folder. Prefer a file id.

```bash
gdstt process <file-id> --dry-run
gdstt process <file-id>
gdstt process <file-id> --reprocess-txt
gdstt process <folder-id> --folder --dry-run
gdstt process <folder-id> --folder --max-size 50MB
```

`--dry-run` does not download files, run ffmpeg, call STT, or upload artifacts.
`--reprocess-txt` reruns STT and overwrites linked TXT. `--max-size` is optional;
use `--confirm-large` only after explicit human approval.

## `speakers set <file-id> <name...>`

Store explicit speaker names on a Drive MP4.

```bash
gdstt speakers set <file-id> "Name 1" "Name 2"
gdstt process <file-id> --reprocess-txt
```

## `refresh-names <file-id>`

Rename linked MP3/TXT artifacts after a source MP4 rename without STT.

```bash
gdstt refresh-names <file-id>
```

## `transcribe <audio> [-o PATH]`

Transcribe a local audio file. It does not touch Drive or run the OpenAI
post-processing pipeline.

```bash
gdstt transcribe ./meeting.mp3 -o ./meeting.txt
```
