# Provider Extension Workflow

Developer reference for adding or replacing an STT provider. Keep operator
workflow guidance in the main skill and provider-tuning notes in
`references/provider-notes.md`; use this file for implementation changes.

## Invariants to preserve

- The CLI surface should stay stable even if `STT_PROVIDER` changes.
- Drive-only commands must continue to work with `validate_providers=False`.
- `OPENAI_POSTPROCESS=true` must stay independent of the chosen STT provider.
- `STT_CHUNK_SECONDS` should matter only for providers that use chunking.
- Blank transcripts must fail instead of uploading empty `.txt` files.

## Required code changes

### 1. Config layer

Update `src/config.py`:

- add any new env vars to `Config`
- parse defaults and normalize values
- enforce validation when `validate_providers=True`
- keep Drive-only commands unblocked when `validate_providers=False`

Update `.env.example` and config tests after changing env vars.

### 2. Provider implementation

Add a provider module under `src/stt/` that subclasses `STTProvider`.

- implement `transcribe_chunk()`
- override `transcribe_full()` only if the provider has a real full-file path
- raise `STTError` on provider failures instead of leaking raw transport errors

Decide explicitly whether the provider should use chunking or full-file mode.

### 3. Factory wiring

Update `src/stt/__init__.py`:

- register the provider name in `get_provider(config)`
- pass only the config fields that belong to that provider
- keep unknown names failing through `UnknownProviderError`

### 4. Runtime behavior review

Check `src/main.py` for provider-specific behavior:

- input artifact choice for STT
- whether Drive MP3 artifacts should exist by default
- whether the provider needs MP3, M4A, or another extracted format
- whether post-processing assumptions still hold

### 5. Documentation updates

Update these docs when provider behavior changes:

- `AGENTS.md` for shared invariants
- `skills/gdstt-cli/SKILL.md` if the default operator flow changes
- `skills/gdstt-cli/references/provider-notes.md` for provider-specific env vars and tuning
- `skills/gdstt-cli/references/troubleshooting.md` for new failure or recovery paths

### 6. Test updates

At minimum, update or add:

- config tests in `tests/test_config.py`
- provider unit tests under `tests/test_stt_*.py`
- `tests/test_stt_transcribe.py` if chunking/full-file semantics change
- `tests/test_skill_docs.py` if new operator-visible env vars or behavior appear

## Release checklist for a provider change

- single-file process path works
- folder dry-run path still behaves safely
- Drive-only commands still skip provider validation
- empty transcript path fails cleanly
- provider-specific env vars are documented and tested
- companion references reflect any new tuning or recovery steps
