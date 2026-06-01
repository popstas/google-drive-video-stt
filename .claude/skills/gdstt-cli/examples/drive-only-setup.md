# Local Setup Wizard

## When to use

Use this playbook when the human needs the first-run local setup flow, wants
Drive access plus the default Deepgram configuration, or needs to resume the
wizard after a missing gcloud/ADC step.

## Human-Facing Setup Wizard

Say plainly that `gdstt setup` is the default local setup path. Ask for an
existing project id, or a new project name, a Drive folder id, and the
Deepgram API key. The default agent profile also requires an OpenAI API key for
transcript refinement. Explain each mutating group before confirmation. Google
Speech-to-Text remains a separate opt-in step. Read keys through hidden input
and never print their values.

Before `gcloud config set project`, explain that it changes the active project
in the user's gcloud configuration, not only this checkout.

Before creating `data/credentials.json` from ADC metadata, explain that only the
OAuth client id/secret are copied and the ADC refresh token is not copied.

Finish with:

```text
Drive and default Deepgram setup are ready. Google STT is still not configured.
```

Do not enable `speech.googleapis.com` or `storage.googleapis.com` in the default wizard.

## Ask or confirm first

- Which Google Cloud project should be used: an existing project id or a new project name?
- Which Drive folder id should go into `FOLDER_IDS`?
- Should `.env` be created from `.env.example` if it does not exist yet?
- May the wizard write `STT_PROVIDER=deepgram`, `DEEPGRAM_API_KEY`, and
  `OPENAI_API_KEY` into the gitignored `.env`?
- May the agent run `gcloud` commands that change config or enable Drive API?
- May the agent run `gdstt auth` and open the OAuth browser flow?

## Preferred sequence

1. Run read-only discovery first:

```powershell
Get-Command gcloud
gcloud config get-value project
gcloud auth list
```

2. Create `.env` from `.env.example` when needed, write `FOLDER_IDS`, set
  `STT_PROVIDER=deepgram`, and ask for profile-required keys without printing
  them.
3. Explain that Google Speech-to-Text remains separate from the default setup.
4. After confirmation, select the project:

```powershell
gcloud config set project <project-id>
```

5. Enable only Drive API for this flow:

```powershell
gcloud services enable drive.googleapis.com
```

6. Create `data/credentials.json` from ADC metadata only after confirmation.
7. Run OAuth:

```powershell
.\.venv\Scripts\gdstt.exe auth
```

8. Verify with a read-only command:

```powershell
.\.venv\Scripts\gdstt.exe doctor --drive
.\.venv\Scripts\gdstt.exe list
```

The app requests both OAuth scopes below. Explain them before auth. The
`cloud-platform` scope is not the same as enabling Google STT APIs.

```text
https://www.googleapis.com/auth/drive
https://www.googleapis.com/auth/cloud-platform
```

When ADC client metadata is available locally, create the OAuth client file
without copying or printing the ADC refresh token:

```powershell
gcloud auth application-default login `
  --scopes=https://www.googleapis.com/auth/drive,https://www.googleapis.com/auth/cloud-platform

New-Item -ItemType Directory -Force data | Out-Null
$adcPath = Join-Path $env:APPDATA "gcloud\application_default_credentials.json"
$adc = Get-Content $adcPath | ConvertFrom-Json
$client = @{
  installed = @{
    client_id = $adc.client_id
    client_secret = $adc.client_secret
    auth_uri = "https://accounts.google.com/o/oauth2/auth"
    token_uri = "https://oauth2.googleapis.com/token"
    redirect_uris = @("http://localhost")
  }
}
$client | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 data\credentials.json
```

## Do not do automatically

- Do not enable `speech.googleapis.com` or `storage.googleapis.com` unless the human separately asks for Google STT.
- Do not create Drive folders without confirmation.
- Do not print OAuth secrets, refresh tokens, or `token.json` contents.
