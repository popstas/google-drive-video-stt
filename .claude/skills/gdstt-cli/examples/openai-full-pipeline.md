# Drive MP4 To Final TXT With OpenAI Post-Processing

## When to use

Use this playbook when the human wants a Drive MP4 transcribed and refined into
the final sibling TXT through OpenAI post-processing.

## Ask or confirm first

- Does Drive access already work?
- Which STT provider should create the initial transcript?
- Is `OPENAI_POSTPROCESS=true` configured with `OPENAI_API_KEY`?
- Does the human approve STT and OpenAI spend for this file?
- If a TXT exists, does the human approve `--reprocess-txt`?

## Preferred sequence

1. Inspect setup and folder state:

```bash
gdstt doctor
gdstt list
```

2. Preview one file:

```bash
gdstt process <drive-mp4-file-id> --dry-run
```

3. After approval, process it:

```bash
gdstt process <drive-mp4-file-id>
```

4. If TXT exists and regeneration is intended:

```bash
gdstt process <drive-mp4-file-id> --reprocess-txt
```

## Important distinction

- `STT_PROVIDER=openai` means OpenAI performs speech-to-text.
- `OPENAI_POSTPROCESS=true` means OpenAI refines text after any STT provider.

## Do not do automatically

- Do not run folder-wide processing before a single-file preview.
- Do not use `--reprocess-txt` without explicit approval.
- Do not treat local `gdstt transcribe` as the complete Drive MP4 pipeline.
