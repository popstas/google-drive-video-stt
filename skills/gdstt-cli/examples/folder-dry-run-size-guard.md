# Folder Dry-Run With Size Guard

## When to use

Use this playbook when the human wants to inspect or process an entire folder,
or when a backlog may contain large videos that should be previewed first.

## Ask or confirm first

- Should the agent use configured `FOLDER_IDS`, or a specific folder id?
- Does the human want an optional size guard such as `50MB`?
- If large files exceed the guard, should they stay skipped or be explicitly confirmed later?
- Does the human really want a one-shot folder run, or only a preview of pending work?

## Preferred sequence

1. Start with a preview, never the real folder run:

```bash
gdstt run-once --dry-run
gdstt process <folder-id> --folder --dry-run
```

2. If the folder may contain expensive files, offer an optional manual limit:

```bash
gdstt process <folder-id> --folder --max-size 50MB --dry-run
```

3. Explain that `--max-size` is optional and disabled by default.
4. Only after the human explicitly approves larger files, run with `--confirm-large`.
5. Use the same folder target and same size assumptions between preview and real execution.

## Do not do automatically

- Do not invent a default `--max-size` threshold.
- Do not add `--confirm-large` without explicit human approval.
- Do not jump from preview straight to continuous `gdstt run` unless the human clearly wants ongoing polling.
