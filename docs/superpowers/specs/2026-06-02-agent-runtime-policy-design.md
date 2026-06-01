# Agent Runtime Policy Design

## Goal

Add a stable JSON contract for agent-driven processing without forcing an
agent to rebuild the media pipeline from low-level CLI commands on every
request.

The common user request stays simple:

```text
Process this Drive video.
```

The default runtime expands that intent into:

```text
Drive MP4
-> provider-specific temporary audio
-> Deepgram transcript
-> OpenAI refinement
-> final TXT uploaded to Drive
-> structured result JSON
-> human-facing agent reply
```

The runtime remains flexible: provider changes, MP3 requirements, explicit
speaker names, and reprocessing are profile or intent overrides rather than new
hardcoded scenarios.

## Design Choice

Use a hybrid architecture:

- the agent interprets the human request and emits a small JSON intent;
- a deterministic planner resolves profiles, validates secrets, and decides
  whether confirmation is needed;
- an executor applies the plan through the existing Drive processing runtime;
- a structured result lets the agent answer quickly and accurately.

This avoids two bad extremes:

- a rigid one-command workflow that accumulates special-case flags;
- an unconstrained agent that rebuilds the pipeline from low-level operations
  each time.

## Profile Files

Add a versioned default profile:

```text
config/pipelines/default.json
```

Support an optional local override:

```text
config/pipelines/local.json
```

`config/pipelines/local.json` is gitignored.

The merge order is:

```text
default.json
-> local.json
-> intent overrides derived from the user's explicit request
```

The default profile is:

```json
{
  "version": 1,
  "stt": {
    "provider": "deepgram",
    "audio_source": "m4a_copy"
  },
  "refine": {
    "enabled": true,
    "provider": "openai"
  },
  "artifacts": {
    "drive_mp3": false,
    "drive_txt": true
  },
  "speakers": {
    "mode": "filename_or_metadata"
  }
}
```

Raw STT output is intermediate data. The default pipeline uploads only the
final refined TXT. Provider-specific audio is temporary unless the profile or
explicit user request enables a Drive MP3 artifact.

## Intent Contract

The smallest valid intent is:

```json
{
  "action": "process",
  "targets": ["drive-file-id"]
}
```

Supported overrides:

```json
{
  "action": "process",
  "targets": ["drive-file-id"],
  "target_type": "file",
  "overrides": {
    "speakers": ["Name One", "Name Two"],
    "stt_provider": "google",
    "refine": false,
    "drive_mp3_artifact": true,
    "reprocess_txt": true
  }
}
```

`target_type` defaults to `auto`. It may be `auto`, `file`, or `folder`.

Unknown intent fields and invalid override types fail before any Drive or
provider mutation.

## Plan Contract

Planning is read-only and returns structured JSON:

```json
{
  "status": "ready",
  "action": "process",
  "targets": ["drive-file-id"],
  "target_type": "file",
  "steps": [
    "download_mp4",
    "extract_m4a_copy",
    "deepgram_transcribe",
    "openai_refine",
    "upload_txt"
  ],
  "confirmation_required": false,
  "confirmation_reasons": [],
  "secrets": {
    "DEEPGRAM_API_KEY": {"configured": true},
    "OPENAI_API_KEY": {"configured": true}
  }
}
```

Secret values never appear in plan JSON.

When required configuration is missing, planning stops before execution:

```json
{
  "status": "configuration_required",
  "missing": ["OPENAI_API_KEY"],
  "next_action": "Run `gdstt setup` or add the missing key to .env."
}
```

## Execution Contract

Execution accepts the same intent JSON. It plans first and does no processing
when the plan is not ready or requires confirmation that was not supplied.

The result is structured JSON:

```json
{
  "status": "completed",
  "files": [
    {
      "id": "drive-file-id",
      "txt_uploaded": true,
      "mp3_uploaded": false,
      "speakers": ["Name One", "Name Two"],
      "cost_usd": {
        "deepgram": 0.01889,
        "openai": 0.0012
      }
    }
  ]
}
```

Cost reporting is best effort. Cost does not block execution and must not add a
confirmation prompt. When a provider does not expose a cost immediately, the
result may report `null` or omit that provider value.

## Confirmation Policy

No extra confirmation is required for:

- processing an explicitly named file with the active profile;
- provider-specific temporary audio;
- a Drive MP3 artifact enabled by the active profile;
- a Drive MP3 artifact explicitly requested by the user;
- speaker metadata defined by the active profile;
- speaker names explicitly supplied by the user;
- using already configured provider keys.

Confirmation is required for:

- folder-wide processing;
- `reprocess_txt`;
- a provider override that was not explicitly requested by the user.

Intent overrides represent explicit user requests. A normal agent should not
invent provider overrides when the active profile already covers the request.

## CLI and Function Surface

Add CLI commands:

```text
gdstt plan --json '<intent-json>'
gdstt execute --json '<intent-json>' [--confirm]
```

Expose Python functions suitable for a future MCP or function-calling adapter:

```text
load_pipeline_profile
plan_process
execute_process
```

The MCP adapter itself is out of scope for this iteration. The JSON and Python
contracts are the shared substrate for CLI use, skills, and future tool
registration.

## Existing Runtime Integration

The executor reuses `src.main.process_target()` and provider dispatch. It does
not duplicate media processing.

The active profile is applied to a copied `Config` value before processing:

- `stt.provider` selects `Config.stt_provider`;
- `stt.audio_source` selects Deepgram audio behavior when relevant;
- `refine.enabled` plus `refine.provider=openai` selects
  `Config.openai_postprocess`;
- `artifacts.drive_mp3` selects `Config.drive_mp3_artifact`;
- explicit speakers are stored through the existing Drive metadata function
  before processing.

Provider adapters continue to own their audio mechanics. The agent and planner
deal in desired outcomes, not ffmpeg details.

## Setup and Doctor Integration

`gdstt setup` reads the active profile before prompting for secrets:

- ask for `DEEPGRAM_API_KEY` only when the active profile uses Deepgram;
- ask for `OPENAI_API_KEY` only when OpenAI refinement is enabled;
- use hidden input;
- write values only to the gitignored `.env`;
- never print key values.

`gdstt doctor` reports profile readiness with boolean secret status only.

## Safety and Secret Handling

- `.env` remains gitignored.
- Secret prompts use `getpass`.
- Secret values never appear in logs, plan JSON, result JSON, exceptions, or
  docs examples.
- Planner validation runs before Drive download, STT calls, or uploads.
- JSON parsing is strict enough to reject misspelled fields instead of silently
  doing the wrong thing.

## Testing

Use TDD. Add focused tests for:

- default profile loading;
- recursive local profile merge;
- invalid profile rejection;
- minimal intent parsing;
- unknown intent field rejection;
- default plan step expansion;
- secret status containing booleans only;
- OpenAI key preflight stopping execution;
- confirmation policy;
- profile application to copied `Config`;
- speaker metadata override routing;
- CLI JSON plan and execute dispatch;
- setup prompting for OpenAI only when required by the active profile;
- doctor readiness output;
- skill and documentation parity.

Then run the full pytest, ruff, skill sync, and skill validator checks.

## Out of Scope

- Implementing an MCP server.
- Letting an LLM invent arbitrary executable pipeline steps.
- Uploading raw intermediate TXT by default.
- Cost-based confirmation prompts.
- Storing keys outside the gitignored `.env`.
