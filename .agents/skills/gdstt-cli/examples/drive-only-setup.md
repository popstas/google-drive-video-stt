# Drive-Only Setup

## When to use

Use this playbook when the human needs Google Drive access, folder inspection,
or first-time operator setup, but has not asked to configure Google STT.

## Ask or confirm first

- Which Google Cloud project should be used: an existing project id or a new project name?
- Which Drive folder id should go into `FOLDER_IDS`?
- Should `DATA_DIR` stay `data`, or use another path?
- May the agent run `gcloud` commands that change config or enable Drive API?
- May the agent run `gdstt auth` and open the OAuth browser flow?

## Preferred sequence

1. Run read-only discovery first:

```powershell
Get-Command gcloud
gcloud config get-value project
gcloud auth list
```

2. Explain that Drive-only setup does not imply Google STT setup.
3. After confirmation, select the project:

```powershell
gcloud config set project <project-id>
```

4. Enable only Drive API for this flow:

```powershell
gcloud services enable drive.googleapis.com
```

5. Create `data/credentials.json` from ADC metadata only after confirmation.
6. Run OAuth:

```powershell
.\.venv\Scripts\gdstt.exe auth
```

7. Verify with a read-only command:

```powershell
.\.venv\Scripts\gdstt.exe list
```

## Do not do automatically

- Do not enable `speech.googleapis.com` or `storage.googleapis.com` unless the human separately asks for Google STT.
- Do not create Drive folders without confirmation.
- Do not print OAuth secrets, refresh tokens, or `token.json` contents.
