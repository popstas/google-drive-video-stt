# Google Timeout Recovery

## When to use

Use this playbook when Google STT hits a client-side timeout, a process summary
shows a Google timeout failure, or a cycle summary reports `gcs_blob_orphans`.

## Ask or confirm first

- Which file or cycle is being investigated?
- Has the original server-side Google STT job definitely finished, or is that still unknown?
- Does the human want diagnosis only, or a real retry after diagnosis?
- Does the human have permission to perform manual GCS cleanup if it turns out to be needed?

## Preferred sequence

1. Read the nearest process and cycle summaries first.
2. Explain that `gcs_blob_orphans` is a subset of failed items, not an extra failure count.
3. Treat the retained blob as an operational cleanup concern, not as a silent success.
4. Confirm whether the original server-side job may still finish before retrying.
5. If a retry is needed, prefer one single-file path instead of a folder-wide rerun:

```bash
gdstt process <file-id> --dry-run
gdstt process <file-id>
```

## Do not do automatically

- Do not assume the uploaded GCS blob was deleted after the timeout.
- Do not start a folder-wide retry just because one Google file timed out.
- Do not delete blobs blindly before checking whether the original job may still complete.
