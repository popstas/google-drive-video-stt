# Command Reference

Use this reference when detailed syntax, aliases, examples, or flag
interactions are needed. Start with the smallest safe command.

## `auth [response_url]`

Run OAuth and save `token.json` into `DATA_DIR`.

```bash
gdstt auth
gdstt auth "http://localhost/?code=4/abc123&scope=..."
```

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
