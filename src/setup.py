from __future__ import annotations

import getpass
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

from src import auth, drive
from src.config import load_config
from src.pipeline_profile import (
    CHECKOUT_ROOT,
    PipelineProfile,
    load_pipeline_profile,
    required_secret_status,
)

AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
DRIVE_API = "drive.googleapis.com"
DEFAULT_PROJECT_NAME = "google-drive-video-stt"

InputFn = Callable[[str], str]
SecretInputFn = Callable[[str], str]
PrintFn = Callable[..., None]
RunCommandFn = Callable[[list[str]], subprocess.CompletedProcess[str] | object]


def _default_adc_path(
    *,
    platform: str | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    platform = platform or sys.platform
    env = env or os.environ
    home = home or Path.home()

    if platform.startswith("win"):
        appdata = env.get("APPDATA", "").strip()
        if not appdata:
            raise ValueError("APPDATA is not set; cannot locate gcloud ADC on Windows")
        return Path(appdata) / "gcloud" / "application_default_credentials.json"

    return home / ".config" / "gcloud" / "application_default_credentials.json"


def _build_client_credentials_from_adc(adc_payload: dict) -> dict:
    client_id = adc_payload.get("client_id")
    client_secret = adc_payload.get("client_secret")
    if not isinstance(client_id, str) or not client_id.strip():
        raise ValueError("ADC metadata is missing client_id")
    if not isinstance(client_secret, str) or not client_secret.strip():
        raise ValueError("ADC metadata is missing client_secret")

    return {
        "installed": {
            "client_id": client_id.strip(),
            "client_secret": client_secret.strip(),
            "auth_uri": AUTH_URI,
            "token_uri": TOKEN_URI,
            "redirect_uris": ["http://localhost"],
        }
    }


def _read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object at {path}")
    return payload


def _split_env_value_and_comment(raw_value: str) -> tuple[str, str]:
    quote_char: str | None = None
    comment_start: int | None = None

    for index, char in enumerate(raw_value):
        if quote_char is not None:
            if char == quote_char:
                quote_char = None
            continue
        if char in {'"', "'"}:
            quote_char = char
            continue
        if char == "#":
            comment_start = index
            while comment_start > 0 and raw_value[comment_start - 1] in {" ", "\t"}:
                comment_start -= 1
            break

    if comment_start is None:
        return raw_value.rstrip(), ""

    return raw_value[:comment_start].rstrip(), raw_value[comment_start:]


def _prepare_credentials_from_adc(adc_path: Path, credentials_path: Path) -> None:
    client_config = _build_client_credentials_from_adc(_read_json(adc_path))
    credentials_path.parent.mkdir(parents=True, exist_ok=True)
    credentials_path.write_text(
        json.dumps(client_config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _parse_env_assignments(env_path: Path) -> dict[str, str]:
    if not env_path.exists():
        return {}

    assignments: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in raw_line:
            continue
        key, _, value = raw_line.partition("=")
        key = key.strip()
        value, _ = _split_env_value_and_comment(value)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        assignments[key] = value
    return assignments


def _format_env_value(value: str) -> str:
    if not value:
        return ""
    if any(ch.isspace() for ch in value) or "#" in value:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _update_env_file(env_path: Path, updates: Mapping[str, str]) -> None:
    existing_lines = []
    if env_path.exists():
        existing_lines = env_path.read_text(encoding="utf-8-sig").splitlines()

    output_lines: list[str] = []
    seen: set[str] = set()
    normalized_updates = {key: value for key, value in updates.items()}

    for line in existing_lines:
        stripped = line.lstrip()
        if stripped.startswith("#") or "=" not in line:
            output_lines.append(line)
            continue

        key, _, raw_value = line.partition("=")
        key = key.strip()
        if key in normalized_updates:
            _, comment_suffix = _split_env_value_and_comment(raw_value)
            output_lines.append(
                f"{key}={_format_env_value(normalized_updates[key])}{comment_suffix}"
            )
            seen.add(key)
        else:
            output_lines.append(line)

    for key, value in normalized_updates.items():
        if key in seen:
            continue
        if output_lines and output_lines[-1] != "":
            output_lines.append("")
        output_lines.append(f"{key}={_format_env_value(value)}")

    env_path.write_text("\n".join(output_lines).rstrip("\n") + "\n", encoding="utf-8")


def _run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _prompt_yes_no(
    prompt: str,
    *,
    default: bool,
    input_func: InputFn,
) -> bool:
    while True:
        raw = input_func(prompt).strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False


def _run_confirmed_gcloud_command(
    command: list[str],
    *,
    summary: str,
    input_func: InputFn,
    run_command: RunCommandFn,
    print_func: PrintFn,
) -> bool:
    print_func(summary)
    if not _prompt_yes_no("Proceed? [y/N]: ", default=False, input_func=input_func):
        print_func("Skipped gcloud change.")
        return False
    run_command(command)
    return True


def _discover_gcloud(*, which_func: Callable[[str], str | None] = shutil.which) -> str | None:
    return which_func("gcloud")


def _stdout_text(result: object) -> str:
    return getattr(result, "stdout", "") or ""


def _current_gcloud_project(gcloud_path: str, *, run_command: RunCommandFn) -> str | None:
    result = run_command([gcloud_path, "config", "get-value", "project", "--quiet"])
    value = _stdout_text(result).strip()
    if not value or value == "(unset)":
        return None
    return value


def _list_gcloud_projects(gcloud_path: str, *, run_command: RunCommandFn) -> list[dict[str, str]]:
    result = run_command([gcloud_path, "projects", "list", "--format=json"])
    payload = json.loads(_stdout_text(result) or "[]")
    if not isinstance(payload, list):
        return []
    projects: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        project_id = item.get("projectId")
        if not isinstance(project_id, str) or not project_id.strip():
            continue
        name = item.get("name")
        projects.append(
            {
                "projectId": project_id.strip(),
                "name": name.strip() if isinstance(name, str) else "",
            }
        )
    return projects


def _select_gcloud_project(
    gcloud_path: str,
    *,
    input_func: InputFn,
    print_func: PrintFn,
    run_command: RunCommandFn,
) -> str | None:
    active_project = _current_gcloud_project(gcloud_path, run_command=run_command)
    if active_project:
        print_func(f"gcloud active project: {active_project}")
        choice = input_func(
            "Project choice [keep/select/create] (default: keep): "
        ).strip().lower() or "keep"
    else:
        print_func("gcloud has no active project.")
        choice = input_func(
            "Project choice [select/create/skip] (default: skip): "
        ).strip().lower() or "skip"

    if choice == "keep":
        return active_project
    if choice == "skip":
        return active_project

    if choice == "select":
        projects = _list_gcloud_projects(gcloud_path, run_command=run_command)
        for project in projects:
            suffix = f" ({project['name']})" if project["name"] else ""
            print_func(f"- {project['projectId']}{suffix}")
        selected = input_func("Existing project id: ").strip()
        if not selected:
            return active_project
        if selected != active_project:
            _run_confirmed_gcloud_command(
                [gcloud_path, "config", "set", "project", selected],
                summary=f"Set active gcloud project to {selected}",
                input_func=input_func,
                run_command=run_command,
                print_func=print_func,
            )
        return selected

    if choice == "create":
        project_id = input_func("New Google Cloud project id: ").strip()
        if not project_id:
            return active_project
        project_name = (
            input_func(f"Project display name [{DEFAULT_PROJECT_NAME}]: ").strip()
            or DEFAULT_PROJECT_NAME
        )
        created = _run_confirmed_gcloud_command(
            [gcloud_path, "projects", "create", project_id, f"--name={project_name}"],
            summary=f"Create Google Cloud project {project_id}",
            input_func=input_func,
            run_command=run_command,
            print_func=print_func,
        )
        if not created:
            return active_project
        _run_confirmed_gcloud_command(
            [gcloud_path, "config", "set", "project", project_id],
            summary=f"Set active gcloud project to {project_id}",
            input_func=input_func,
            run_command=run_command,
            print_func=print_func,
        )
        return project_id

    print_func("Unrecognized project choice; keeping the current gcloud configuration.")
    return active_project


def _ensure_drive_api_enabled(
    gcloud_path: str,
    project_id: str | None,
    *,
    input_func: InputFn,
    print_func: PrintFn,
    run_command: RunCommandFn,
) -> bool:
    if not project_id:
        print_func("Skipping Drive API enablement: no active gcloud project selected.")
        return False
    return _run_confirmed_gcloud_command(
        [gcloud_path, "services", "enable", DRIVE_API, f"--project={project_id}"],
        summary=(
            f"Enable {DRIVE_API} for gcloud project {project_id}. "
            "The default setup flow does not enable Google STT or Cloud Storage APIs."
        ),
        input_func=input_func,
        run_command=run_command,
        print_func=print_func,
    )


def _ensure_env_file(env_path: Path, example_path: Path, *, print_func: PrintFn) -> None:
    if env_path.exists():
        return
    if not example_path.exists():
        raise FileNotFoundError(f"Missing template: {example_path}")
    env_path.write_text(example_path.read_text(encoding="utf-8-sig"), encoding="utf-8")
    print_func(f"Created {env_path.name} from {example_path.name}.")


def _configured_data_dir(repo_root: Path, env_path: Path) -> Path:
    data_dir_value = _parse_env_assignments(env_path).get("DATA_DIR", "data").strip() or "data"
    candidate = Path(data_dir_value)
    if candidate.is_absolute():
        return candidate
    return repo_root / candidate


def _prompt_folder_ids(existing_value: str, *, input_func: InputFn, print_func: PrintFn) -> str:
    while True:
        prompt = "Google Drive folder id"
        if existing_value:
            prompt += f" [{existing_value}]"
        folder_ids = input_func(prompt + ": ").strip() or existing_value
        if folder_ids:
            return folder_ids
        print_func("A Drive folder id is required.")


def _prompt_profile_api_keys(
    profile: PipelineProfile,
    existing_env: Mapping[str, str],
    *,
    secret_input_func: SecretInputFn,
    print_func: PrintFn,
) -> dict[str, str]:
    labels = {
        "DEEPGRAM_API_KEY": "Deepgram API key",
        "OPENAI_API_KEY": "OpenAI API key",
    }
    updates: dict[str, str] = {}
    for key in required_secret_status(profile, env=existing_env):
        existing_value = existing_env.get(key, "").strip()
        while True:
            prompt = labels.get(key, key)
            if existing_value:
                prompt += " [leave blank to keep existing]"
            value = secret_input_func(prompt + ": ").strip()
            if value:
                updates[key] = value
                break
            if existing_value:
                updates[key] = existing_value
                break
            print_func(f"{labels.get(key, key)} is required by the active pipeline profile.")
    return updates


def _prepare_runtime_env(env_updates: Mapping[str, str], *, data_dir: Path) -> None:
    for key, value in env_updates.items():
        os.environ[key] = value
    os.environ["DATA_DIR"] = str(data_dir)


def _maybe_replace_existing_file(
    path: Path,
    *,
    label: str,
    input_func: InputFn,
) -> bool:
    if not path.exists():
        return True
    return _prompt_yes_no(
        f"{label} already exists at {path}. Replace it? [y/N]: ",
        default=False,
        input_func=input_func,
    )


def _print_gcloud_fallback(*, print_func: PrintFn) -> None:
    print_func("gcloud was not found on PATH.")
    print_func(
        "Fallback: create a Desktop OAuth client in Google Cloud Console and save it as "
        "data/credentials.json."
    )


def _print_adc_resume_help(*, print_func: PrintFn) -> None:
    scopes = ",".join(auth.SCOPES)
    print_func("Application Default Credentials metadata was not found.")
    print_func("Run this command, then re-run `gdstt setup`:")
    print_func(f"gcloud auth application-default login --scopes={scopes}")


def verify_drive_setup(*, print_func: PrintFn = print) -> None:
    config = load_config(validate_providers=False)
    credentials_path = config.data_dir / "credentials.json"
    token_path = config.data_dir / "token.json"

    print_func(f"DATA_DIR: {config.data_dir}")
    print_func(f"credentials.json: {'OK' if credentials_path.exists() else 'missing'}")
    print_func(f"token.json: {'OK' if token_path.exists() else 'missing'}")
    print_func(f"FOLDER_IDS: {len(config.folder_ids)} configured")
    print_func(f"STT_PROVIDER: {config.stt_provider or 'disabled'}")

    service = auth.build_drive_service(data_dir=config.data_dir)
    print_func("Drive auth: OK")
    for folder_id in config.folder_ids:
        items = drive.list_folder_state(service, folder_id)
        print_func(f"Folder {folder_id}: OK, {len(items)} mp4 file(s)")


def run_setup(
    *,
    repo_root: Path | None = None,
    input_func: InputFn = input,
    secret_input_func: SecretInputFn = getpass.getpass,
    print_func: PrintFn = print,
    which_func: Callable[[str], str | None] = shutil.which,
    run_command: RunCommandFn = _run_command,
    profile: PipelineProfile | None = None,
) -> None:
    repo_root = repo_root or CHECKOUT_ROOT
    env_path = repo_root / ".env"
    example_path = repo_root / ".env.example"

    _ensure_env_file(env_path, example_path, print_func=print_func)
    existing_env = _parse_env_assignments(env_path)
    folder_ids = _prompt_folder_ids(
        existing_env.get("FOLDER_IDS", ""),
        input_func=input_func,
        print_func=print_func,
    )

    profile = profile or load_pipeline_profile(repo_root=repo_root)
    print_func(f"Using STT_PROVIDER={profile.stt_provider} from the active pipeline profile.")
    secret_updates = _prompt_profile_api_keys(
        profile,
        existing_env,
        secret_input_func=secret_input_func,
        print_func=print_func,
    )

    env_updates = {
        "FOLDER_IDS": folder_ids,
        "STT_PROVIDER": profile.stt_provider,
        **secret_updates,
    }
    _update_env_file(env_path, env_updates)

    data_dir = _configured_data_dir(repo_root, env_path)
    data_dir.mkdir(parents=True, exist_ok=True)
    credentials_path = data_dir / "credentials.json"
    token_path = data_dir / "token.json"

    _prepare_runtime_env(env_updates, data_dir=data_dir)

    gcloud_path = _discover_gcloud(which_func=which_func)
    if gcloud_path:
        project_id = _select_gcloud_project(
            gcloud_path,
            input_func=input_func,
            print_func=print_func,
            run_command=run_command,
        )
        _ensure_drive_api_enabled(
            gcloud_path,
            project_id,
            input_func=input_func,
            print_func=print_func,
            run_command=run_command,
        )
    else:
        _print_gcloud_fallback(print_func=print_func)

    replace_credentials = _maybe_replace_existing_file(
        credentials_path,
        label="credentials.json",
        input_func=input_func,
    )
    if replace_credentials:
        adc_path = _default_adc_path()
        if adc_path.exists():
            _prepare_credentials_from_adc(adc_path, credentials_path)
            print_func(f"Wrote {credentials_path}.")
        elif not credentials_path.exists():
            _print_adc_resume_help(print_func=print_func)
            if not gcloud_path:
                _print_gcloud_fallback(print_func=print_func)
            return
        else:
            print_func(f"Keeping existing {credentials_path}.")
    else:
        print_func(f"Keeping existing {credentials_path}.")

    if _maybe_replace_existing_file(token_path, label="token.json", input_func=input_func):
        auth.run_interactive_flow(data_dir, manual=False, response_url=None)
    else:
        print_func(f"Keeping existing {token_path}.")

    verify_drive_setup(print_func=print_func)
    print_func("Next steps:")
    print_func("1. gdstt list")
    print_func("2. gdstt process <file-id> --dry-run")
