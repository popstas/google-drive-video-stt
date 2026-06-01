# Troubleshooting

Operational reference for failures and recovery. Keep the default command flow
in the main skill; use this file when the normal flow is not enough.

## Empty transcript failures

Blank transcript output is treated as a failure, not as success.

- A provider returning an empty transcript now raises an STT error.
- The service should not upload a blank `.txt` as if processing succeeded.
- Re-run on a single file first and inspect provider-specific logs before any
  folder-wide retry.

## Transient Drive retries

The runtime retries transient Drive read paths before giving up:

- metadata lookup
- folder-state listing
- downloads

This retry layer is intentionally limited to read-only paths so it does not
duplicate uploads or create artifacts twice.

## Download size mismatch

Downloads can now fail if the final local file size does not match the Drive
metadata size.

- Treat this as a transfer-integrity failure, not as a successful download.
- The partial local temp file is removed before the error is raised.
- Re-run the single file first; if it repeats, inspect the Drive item metadata
  and recent network/proxy behavior.

## Invalid `FOLDER_IDS`

If `FOLDER_IDS` contains only commas or whitespace, startup now fails instead
of silently behaving like an empty folder list.

- Fix the env value first.
- Use `gdstt doctor` or `gdstt list` after correcting it.
- Do not treat a no-op startup as healthy configuration.

## Reading runtime summaries

`run-once` now emits:

- one process summary per processed file: provider, `processing_mode`, outcome,
  retry count, and duration
- one folder summary per folder: total files, pending files, skipped-by-size count,
  and whether it was a dry run
- one cycle summary: provider, overall outcome, folder count, pending count,
  processed count, failed count, `retry_total`, GCS-blob-orphan count,
  skipped-by-size count, folder-error count, dry-run flag, and duration

`gcs_blob_orphans` is a subset of failed items, not an extra parallel failure count.

Use these summaries before reading per-file stack traces.

## Google STT timeout cleanup

If Google STT hits its client-side batch timeout, the uploaded GCS blob may be
retained for manual cleanup.

- Treat that as an operational task, not as a silent success.
- Do not assume the blob was deleted after a timeout.
- Check the next cycle summary for a non-zero `gcs_blob_orphans` count.
- Re-run only after confirming whether the original server-side job finished.

## Deepgram artifact surprises

If Deepgram produced TXT but no Drive MP3, check `DEEPGRAM_AUDIO_SOURCE` first.

- `m4a_copy` can be correct behavior, not a failure.
- Use `docs/skills/provider-notes.md` -> `Deepgram` as the source of truth for
  `DEEPGRAM_AUDIO_SOURCE` and `DRIVE_MP3_ARTIFACT` behavior.
- Change one Deepgram setting at a time and re-run a single file before any
  folder-wide retry.

## First recovery commands

Use this sequence first:

```bash
gdstt doctor
gdstt list
gdstt process <file-id> --dry-run
gdstt process <file-id>
```

If speaker labels are wrong:

```bash
gdstt speakers set <file-id> "Name 1" "Name 2"
gdstt process <file-id> --reprocess-txt
```

If source names changed:

```bash
gdstt refresh-names <file-id>
```
