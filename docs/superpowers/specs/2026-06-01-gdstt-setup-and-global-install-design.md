# gdstt Setup Wizard and Global CLI Install Design

## Goal

Make first-time local setup understandable on Windows, macOS, and Linux.
After cloning the repository, a user should be able to install the `gdstt`
command globally and run one guided setup command instead of manually assembling
`.env`, OAuth client metadata, and browser callback steps.

The repository remains the configuration boundary: `.env` and `data/` stay next
to the checkout so local runs, Docker deployment, and VPS migration use the same
files.

## User Flow

The documented local quickstart becomes:

```bash
uv tool install --editable .
uv tool update-shell
gdstt setup
```

`uv tool update-shell` is needed only when the uv tool executable directory is
not already on `PATH`.

`gdstt setup` guides the user from an empty checkout to a Drive-ready,
Deepgram-ready configuration:

1. Create `.env` from `.env.example` when `.env` is absent.
2. Ask for the Google Drive folder id and write `FOLDER_IDS`.
3. Select `deepgram` by default, ask for `DEEPGRAM_API_KEY`, and write both
   values to the gitignored `.env` without logging the key.
4. Find `gcloud` on `PATH`.
5. If `gcloud` is present, show its active project and ask whether to keep it,
   select another existing project, or create a project.
6. Before changing gcloud configuration or enabling an API, explain the
   operation and ask for confirmation.
7. Enable only `drive.googleapis.com` during the default setup flow.
8. Locate Application Default Credentials (ADC) client metadata and create
   `data/credentials.json` without copying or printing the ADC refresh token.
9. Run OAuth in the browser and write `data/token.json`.
10. Verify the finished Drive setup with the equivalent of
    `gdstt doctor --drive`.
11. Print the safe next steps: `gdstt list`, then
    `gdstt process <file-id> --dry-run`.

Google Speech-to-Text remains opt-in. The default setup flow must not enable
`speech.googleapis.com` or `storage.googleapis.com`.

## CLI Surface

Add:

```text
gdstt setup
gdstt auth [--manual] [response_url]
```

`gdstt setup` is the recommended first-run entry point.

`gdstt auth` remains available as a smaller OAuth-only operation for refresh,
recovery, and existing deployments:

- Normal mode starts a localhost callback server and opens the browser.
- `--manual` prints the authorization URL for a headless environment.
- Passing `response_url` completes the manual exchange.
- A localhost HTTP callback is accepted by the manual exchange without asking
  the user to set oauthlib environment variables.

Existing commands remain stable:

```text
gdstt doctor [--drive]
gdstt list
gdstt process <target> [--dry-run]
gdstt run-once [--dry-run]
gdstt run
```

## Configuration Defaults

Deepgram is the operational default:

```text
STT_PROVIDER=deepgram
```

When `STT_PROVIDER` is absent, `load_config()` treats it as `deepgram`.
Transcription can be disabled explicitly:

```text
STT_PROVIDER=disabled
```

Provider validation remains command-sensitive:

- Drive-only commands (`setup`, `auth`, `doctor`, `list`, `status`,
  `speakers set`, `refresh-names`) can run before a Deepgram key exists.
- Processing commands (`run`, `run-once`, `process`, `transcribe`) require the
  selected provider configuration.
- When Deepgram is selected but its key is missing, processing commands explain
  that the user can add `DEEPGRAM_API_KEY` or run `gdstt setup`.

The Deepgram `m4a_copy` behavior stays unchanged: it does not create an MP3
artifact on Drive unless `DRIVE_MP3_ARTIFACT=true` is set explicitly.

## Architecture

Add a focused `src/setup.py` module and keep `src/cli.py` as the command
dispatcher.

`src/setup.py` owns:

- interactive prompts and defaults;
- conservative `.env` updates that preserve unknown lines and user comments;
- `gcloud` discovery and confirmed subprocess calls;
- cross-platform ADC discovery;
- generation of `data/credentials.json`;
- orchestration of OAuth and Drive verification;
- concise next-step output.

`src/auth.py` continues to own OAuth mechanics:

- client config parsing;
- localhost callback flow;
- headless/manual exchange;
- secure token writes.

`src/config.py` continues to own provider defaults and validation.

This keeps setup orchestration separate from runtime processing and avoids
duplicating Drive or OAuth business logic.

## Cross-Platform Behavior

ADC discovery checks the standard paths:

```text
Windows: %APPDATA%\gcloud\application_default_credentials.json
macOS:   ~/.config/gcloud/application_default_credentials.json
Linux:   ~/.config/gcloud/application_default_credentials.json
```

Generated JSON is UTF-8 without BOM. Existing BOM-tolerant readers remain in
place because users may still create or edit files with tools that add a BOM.

All subprocess invocation uses argument lists rather than shell-specific command
strings. User-facing documentation provides both POSIX shell and PowerShell
examples only where syntax differs.

## Fallbacks and Errors

If `gcloud` is not installed, setup prints a short Google Cloud Console fallback
for creating a Desktop OAuth client and placing it at
`data/credentials.json`.

If ADC metadata is absent, setup explains how to run:

```bash
gcloud auth application-default login \
  --scopes=https://www.googleapis.com/auth/drive,https://www.googleapis.com/auth/cloud-platform
```

and then resume `gdstt setup`.

Errors must identify the missing step and the next action. They must not print
API keys, OAuth secrets, refresh tokens, `credentials.json`, or `token.json`
contents.

Setup can be re-run safely. Existing unknown `.env` keys and comments are
preserved. Existing local credentials are replaced only after explicit
confirmation.

## Documentation

Update:

- `README.md` with the global install and `gdstt setup` quickstart;
- `.env.example` with `STT_PROVIDER=deepgram` and explicit
  `STT_PROVIDER=disabled` guidance;
- `AGENTS.md` with the new operator path;
- `skills/gdstt-cli/SKILL.md` and bundled setup/configuration references;
- generated `.agents` and `.claude` mirrors through
  `scripts/sync-agent-skills.py --write`;
- skill metadata version fields when required by the existing package policy.

The longer manual Cloud Console and provider-switching instructions remain as
fallback and reference material instead of the primary quickstart.

## Testing

Use test-driven development for implementation.

Add unit coverage for:

- default provider selection and explicit disablement;
- Drive-only config loading before a Deepgram key exists;
- missing-key processing errors with the setup hint;
- global CLI help exposing `setup`;
- normal OAuth opening a browser;
- manual OAuth accepting a localhost HTTP callback;
- ADC discovery on Windows, macOS, and Linux paths;
- generated OAuth client JSON excluding the ADC refresh token;
- `.env` updates preserving unknown keys and comments;
- confirmed and declined gcloud mutations;
- setup resume behavior when files already exist;
- documentation and generated skill mirror synchronization.

Run the existing full pytest and ruff suites after focused tests.

## Out of Scope

- Publishing the package to PyPI.
- Storing configuration in the user profile.
- Automatically installing `gcloud`, `uv`, or `ffmpeg`.
- Enabling Google Speech-to-Text or Cloud Storage APIs in the default wizard.
- Moving API keys out of the gitignored `.env`.
