from __future__ import annotations

import argparse
import importlib.util
import re
from pathlib import Path

from src import cli

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_ID = "gdstt-cli"
AGENTS_PATH = REPO_ROOT / "AGENTS.md"
CLAUDE_PATH = REPO_ROOT / "CLAUDE.md"
CANONICAL_SKILL_ROOT = REPO_ROOT / "skills" / SKILL_ID
AGENT_SKILL_CHECK_SCRIPT = REPO_ROOT / "scripts" / "check-agent-skill.py"


def _registered_commands() -> set[str]:
    """All subcommand names (including aliases) registered on the CLI parser."""
    parser = cli.build_parser()
    subparsers_action = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    return set(subparsers_action.choices.keys())


def _documented_command_entries(text: str) -> list[tuple[str, int]]:
    r"""Command names documented in ``###`` headers, with line numbers."""
    registered = _registered_commands()
    entries: list[tuple[str, int]] = []

    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.startswith("### "):
            continue
        for span in re.findall(r"`([^`]+)`", line):
            matches = [
                command
                for command in registered
                if span == command or span.startswith(command + " ")
            ]
            assert len(matches) <= 1, f"ambiguous documented command span {span!r} on line {line_number}"
            if matches:
                entries.append((matches[0], line_number))

    return entries


def _skill_frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"^---\n(.*?)\n---\n", text, flags=re.DOTALL)
    assert match, "skill must start with YAML frontmatter"

    data: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        assert sep, f"invalid frontmatter line: {line!r}"
        data[key.strip()] = value.strip().strip('"')
    return data


def _skill_path() -> Path:
    return CANONICAL_SKILL_ROOT / "SKILL.md"


def _portable_reference_path(relative_path: str) -> Path:
    return _skill_path().parent / "references" / Path(relative_path).name


def _portable_example_path(file_name: str) -> Path:
    return _skill_path().parent / "examples" / file_name


def _markdown_headings(text: str) -> set[str]:
    return {
        match.group(1).strip()
        for match in re.finditer(r"^#{2,3}\s+(.*)$", text, flags=re.MULTILINE)
    }


def _markdown_section(text: str, heading: str) -> str:
    match = re.search(
        rf"^#{{2,3}}\s+{re.escape(heading)}\n(.*?)(?=^#{{2,3}}\s+|\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match, f"missing section: {heading}"
    return match.group(1)


def _normalized_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n").rstrip("\n")


def _collapsed_text(path: Path) -> str:
    return " ".join(_normalized_text(path).split())


def test_skill_file_exists_with_valid_frontmatter():
    skill_path = _skill_path()
    assert skill_path.exists(), f"missing skill file: {skill_path}"

    text = skill_path.read_text(encoding="utf-8")
    frontmatter = _skill_frontmatter(text)

    assert frontmatter["name"] == SKILL_ID
    assert frontmatter["description"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", frontmatter["version"])
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", frontmatter["last_updated"])


def test_canonical_skill_package_exists_and_primary_skill_is_compact():
    skill_path = CANONICAL_SKILL_ROOT / "SKILL.md"

    assert skill_path.exists(), f"missing canonical skill: {skill_path}"
    assert len(skill_path.read_text(encoding="utf-8").splitlines()) <= 400


def test_repository_tracks_one_installable_skill_bundle():
    forbidden_paths = (
        REPO_ROOT / ".agents" / "skills",
        REPO_ROOT / ".claude" / "skills",
        REPO_ROOT / "docs" / "skills",
        REPO_ROOT / "scripts" / "sync-agent-skills.py",
    )

    assert (CANONICAL_SKILL_ROOT / "SKILL.md").exists()
    for path in forbidden_paths:
        assert not path.exists(), f"obsolete duplicate skill surface remains: {path}"


def test_skill_package_contains_one_discoverable_skill_and_routes_every_resource():
    skill_text = (CANONICAL_SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    skill_files = list(CANONICAL_SKILL_ROOT.rglob("SKILL.md"))
    resources = [
        path
        for resource_dir in ("references", "examples")
        for path in (CANONICAL_SKILL_ROOT / resource_dir).glob("*")
        if path.is_file()
    ]

    assert skill_files == [CANONICAL_SKILL_ROOT / "SKILL.md"]
    for path in resources:
        relative_path = path.relative_to(CANONICAL_SKILL_ROOT).as_posix()
        assert relative_path in skill_text, f"primary skill must route to {relative_path}"


def test_documented_commands_match_registered_subcommands():
    text = _skill_path().read_text(encoding="utf-8")
    entries = _documented_command_entries(text)
    registered = _registered_commands()
    documented = {command for command, _ in entries}

    duplicates: dict[str, list[int]] = {}
    for command, line_number in entries:
        duplicates.setdefault(command, []).append(line_number)
    duplicates = {
        command: line_numbers
        for command, line_numbers in duplicates.items()
        if len(line_numbers) > 1
    }

    missing = registered - documented
    extra = documented - registered
    assert not duplicates, f"skill documents commands multiple times: {duplicates}"
    assert not missing, f"CLI commands not documented in the skill: {sorted(missing)}"
    assert not extra, f"skill documents commands that are not registered: {sorted(extra)}"


def test_skill_documents_provider_env_vars():
    text = _portable_reference_path("configuration.md").read_text(encoding="utf-8")
    for var in (
        "STT_PROVIDER",
        "OPENAI_API_KEY",
        "OPENAI_POSTPROCESS",
        "OPENAI_BATCH",
        "DEEPGRAM_API_KEY",
        "ASR_URL",
        "FOLDER_IDS",
    ):
        assert var in text, f"skill should document {var}"


def test_skill_has_start_here_and_scenario_env_sections():
    text = _skill_path().read_text(encoding="utf-8")
    config_text = _portable_reference_path("configuration.md").read_text(encoding="utf-8")

    assert "## Start Here" in text
    assert "First-time local setup" in text
    assert "Safe low-level single-file processing" in text
    assert "## Drive-only setup" in config_text
    assert "## Common runtime behavior" in config_text
    assert "## Agent pipeline profile" in config_text
    assert "## Deepgram" in config_text
    assert "## OpenAI" in config_text
    assert "## Google STT" in config_text
    assert "## ASR" in config_text


def test_skill_start_here_routes_common_operator_intents():
    text = _skill_path().read_text(encoding="utf-8")
    start_here = _markdown_section(text, "Start Here")

    assert "Use the smallest path that fits the task" in start_here
    assert "gdstt setup" in start_here
    assert "gdstt auth" in start_here
    assert "gdstt list" in start_here
    assert "gdstt plan --json" in start_here
    assert "gdstt execute --json" in start_here
    assert "gdstt process <file-id> --dry-run" in start_here
    assert "gdstt process <file-id>" in start_here
    assert "routing table below" in start_here


def test_skill_start_here_stays_compact_and_ordered():
    text = _skill_path().read_text(encoding="utf-8")
    start_here = _markdown_section(text, "Start Here")

    numbered_routes = re.findall(r"^\d+\.\s+(.*)$", start_here, flags=re.MULTILINE)

    assert numbered_routes == [
        "First-time local setup: `gdstt setup` -> `gdstt list` -> `gdstt process <file-id> --dry-run`",
        "OAuth refresh or headless recovery: `gdstt auth` or `gdstt auth --manual`",
        "Agent JSON processing: `gdstt plan --json '<intent>'` -> `gdstt execute --json '<intent>'`",
        "Safe low-level single-file processing: `gdstt process <file-id> --dry-run` -> `gdstt process <file-id>`",
        "Folder-wide low-level work: preview first with `gdstt run-once --dry-run` or folder `process --dry-run`",
        "Provider or failure detail: read the matching resource from the routing table below",
    ]


def test_skill_keeps_env_vars_grouped_by_operator_scenario():
    text = _portable_reference_path("configuration.md").read_text(encoding="utf-8")

    drive_setup = _markdown_section(text, "Drive-only setup")
    common_runtime = _markdown_section(text, "Common runtime behavior")
    deepgram = _markdown_section(text, "Deepgram")
    openai = _markdown_section(text, "OpenAI")
    google = _markdown_section(text, "Google STT")
    asr = _markdown_section(text, "ASR")

    assert "FOLDER_IDS" in drive_setup
    assert "DATA_DIR" in drive_setup
    assert "PROXY_URL" in drive_setup
    assert "OPENAI_API_KEY" not in drive_setup

    assert "POLL_INTERVAL" in common_runtime
    assert "STT_PROVIDER" in common_runtime
    assert "STT_CHUNK_SECONDS" in common_runtime

    assert "DEEPGRAM_API_KEY" in deepgram
    assert "DEEPGRAM_AUDIO_SOURCE" in deepgram
    assert "DRIVE_MP3_ARTIFACT" in deepgram

    assert "OPENAI_API_KEY" in openai
    assert "OPENAI_POSTPROCESS" in openai
    assert "OPENAI_BATCH" in openai

    assert "GOOGLE_CLOUD_PROJECT" in google
    assert "GOOGLE_STT_GCS_BUCKET" in google
    assert "separate from the default `gdstt setup` wizard" in google

    assert "ASR_URL" in asr


def test_skill_documents_runtime_and_deepgram_env_vars():
    text = _portable_reference_path("configuration.md").read_text(encoding="utf-8")
    for var in (
        "POLL_INTERVAL",
        "PROXY_URL",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "STT_CHUNK_SECONDS",
        "STT_POSTPROCESS",
        "DRIVE_MP3_ARTIFACT",
        "DEEPGRAM_MODEL",
        "DEEPGRAM_DIARIZE_MODEL",
        "DEEPGRAM_AUDIO_SOURCE",
        "DEEPGRAM_TXT_FORMATTER",
        "DEEPGRAM_KEYTERMS_ENABLED",
        "DEEPGRAM_KEYTERMS_FILE",
    ):
        assert var in text, f"skill should document {var}"


def test_skill_documents_agent_json_pipeline():
    skill_text = _skill_path().read_text(encoding="utf-8")
    config_text = _portable_reference_path("configuration.md").read_text(encoding="utf-8")

    assert "gdstt plan --json" in skill_text
    assert "gdstt execute --json" in skill_text
    assert "config/pipelines/default.json" in config_text
    assert "config/pipelines/local.json" in config_text
    assert "`configured` or `missing`" in config_text


def test_skill_documents_command_boundaries_and_provider_switching():
    text = _skill_path().read_text(encoding="utf-8")

    assert "Bootstrap and Drive-only commands use `load_config(validate_providers=False)`" in text
    assert "Processing commands validate provider config and can spend STT credits" in text
    assert "Current operational default examples assume `STT_PROVIDER=deepgram`" in text
    assert "the same CLI flow if the provider changes later." in text
    assert "Deepgram and Google full-file paths" in _collapsed_text(_skill_path())


def test_skill_documents_default_setup_wizard():
    text = _collapsed_text(_portable_example_path("drive-only-setup.md"))

    assert "Human-Facing Setup Wizard" in text
    assert "existing project id, or a new project name" in text
    assert "Deepgram API key" in text
    assert "Google Speech-to-Text remains a separate opt-in step" in text
    assert "Drive and default Deepgram setup are ready. Google STT is still not configured." in text
    assert "Do not enable `speech.googleapis.com` or `storage.googleapis.com` in the default wizard" in text
    assert "changes the active project in the user's gcloud configuration" in text
    assert "OAuth client id/secret" in text
    assert "refresh token is not copied" in text


def test_skill_documents_max_size_is_optional():
    text = _collapsed_text(_skill_path())

    assert "`--max-size` is optional and disabled by default" in text
    assert "Do not invent a global threshold" in text


def test_skill_documents_openai_full_drive_pipeline():
    text = _portable_example_path("openai-full-pipeline.md").read_text(encoding="utf-8")

    assert "Drive MP4 To Final TXT With OpenAI Post-Processing" in text
    assert "OPENAI_POSTPROCESS=true" in text
    assert "STT_PROVIDER=openai" in text
    assert "OpenAI performs speech-to-text" in text
    assert "OpenAI refines text after any STT provider" in text
    assert "Do not treat local `gdstt transcribe` as the complete Drive MP4 pipeline" in text


def test_skill_documents_cycle_summary_interpretation():
    text = _skill_path().read_text(encoding="utf-8")

    assert "gcs_blob_orphans" in text
    assert "`run-once` logs folder summaries and one cycle summary" in text
    assert "`process_item` logs provider" in text
    assert "processing_mode" in text
    assert "provider, overall" in text
    assert "retry_total" in text
    assert "duration" in text
    assert "references/troubleshooting.md" in text


def test_skill_bundle_has_scenario_playbooks():
    text = _skill_path().read_text(encoding="utf-8")

    assert "## Resource Routing" in text
    assert "Bundled references and examples are installed resources, not nested skills" in text
    for example_name in (
        "drive-only-setup.md",
        "folder-dry-run-size-guard.md",
        "google-timeout-recovery.md",
        "openai-full-pipeline.md",
    ):
        example_path = _portable_example_path(example_name)
        assert example_path.exists(), f"missing scenario playbook: {example_path}"
        example_text = example_path.read_text(encoding="utf-8")
        assert "## When to use" in example_text
        assert "## Ask or confirm first" in example_text
        assert "## Preferred sequence" in example_text
        assert f"examples/{example_name}" in text


def test_agents_doc_exists_with_portable_contract():
    assert AGENTS_PATH.exists(), f"missing AGENTS.md: {AGENTS_PATH}"
    text = AGENTS_PATH.read_text(encoding="utf-8")

    assert text.startswith("# AGENTS.md")
    assert "Source of truth layering" in text
    assert "python scripts/check-agent-skill.py" in text
    assert "gh skill install" in text
    assert "--agent codex" in text
    assert "--agent claude-code" in text
    assert ".agents/skills/gdstt-cli/" not in text
    assert ".claude/skills/gdstt-cli/" not in text
    assert "Bootstrap and Drive-only commands use `load_config(validate_providers=False)`" in text
    assert "Processing commands validate provider configuration and can spend credits" in text
    assert "Deepgram is the current operational default" in text
    assert "Keep one primary operator skill" in text
    assert "Prefer companion reference docs over separate active subskills" in text
    assert "not as nested subskills inside the main `gdstt-cli` package" in text


def test_agent_skill_validator_script_exists():
    assert AGENT_SKILL_CHECK_SCRIPT.exists(), (
        f"missing agent skill validator script: {AGENT_SKILL_CHECK_SCRIPT}"
    )


def test_agent_skill_validator_script_passes():
    spec = importlib.util.spec_from_file_location(
        "gdstt_agent_skill_validator",
        AGENT_SKILL_CHECK_SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.main() == 0


def test_claude_doc_points_back_to_agents():
    assert CLAUDE_PATH.exists(), f"missing CLAUDE.md: {CLAUDE_PATH}"
    text = CLAUDE_PATH.read_text(encoding="utf-8")

    assert "Primary shared repo instructions live in [AGENTS.md](AGENTS.md)." in text
    assert "[AGENTS.md](AGENTS.md) is the source of truth." in text


def test_bundled_reference_docs_exist_with_required_sections():
    skill_text = _skill_path().read_text(encoding="utf-8")
    references = {
        "provider-notes.md": (
            "Universal switching rules",
            "Deepgram",
            "Google STT",
            "OpenAI STT",
            "ASR",
        ),
        "troubleshooting.md": (
            "Empty transcript failures",
            "Transient Drive retries",
            "Download size mismatch",
            "Invalid `FOLDER_IDS`",
            "Reading runtime summaries",
            "Google STT timeout cleanup",
            "Deepgram artifact surprises",
            "First recovery commands",
        ),
        "provider-extension.md": (
            "Invariants to preserve",
            "Required code changes",
            "Release checklist for a provider change",
        ),
    }

    for file_name, required_sections in references.items():
        bundled_path = _portable_reference_path(file_name)
        assert bundled_path.exists(), f"missing bundled reference: {bundled_path}"
        assert bundled_path.relative_to(_skill_path().parent).as_posix() in skill_text

        headings = _markdown_headings(bundled_path.read_text(encoding="utf-8"))
        for section in required_sections:
            assert section in headings, (
                f"{bundled_path} should include the promised section `{section}`"
            )
