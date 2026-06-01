from __future__ import annotations

import json
from pathlib import Path

from src import setup
from src.pipeline_profile import load_pipeline_profile


def test_default_adc_path_uses_windows_appdata():
    path = setup._default_adc_path(
        platform="win32",
        env={"APPDATA": r"C:\Users\me\AppData\Roaming"},
        home=Path("/ignored"),
    )

    assert path == Path(r"C:\Users\me\AppData\Roaming") / "gcloud" / "application_default_credentials.json"


def test_default_adc_path_uses_macos_config_home():
    path = setup._default_adc_path(
        platform="darwin",
        env={},
        home=Path("/Users/me"),
    )

    assert path == Path("/Users/me/.config/gcloud/application_default_credentials.json")


def test_default_adc_path_uses_linux_config_home():
    path = setup._default_adc_path(
        platform="linux",
        env={},
        home=Path("/home/me"),
    )

    assert path == Path("/home/me/.config/gcloud/application_default_credentials.json")


def test_build_client_credentials_from_adc_excludes_refresh_token():
    client = setup._build_client_credentials_from_adc(
        {
            "client_id": "cid.apps.googleusercontent.com",
            "client_secret": "csec",
            "refresh_token": "should-not-leak",
            "type": "authorized_user",
        }
    )

    assert client == {
        "installed": {
            "client_id": "cid.apps.googleusercontent.com",
            "client_secret": "csec",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    assert "refresh_token" not in json.dumps(client)


def test_update_env_file_preserves_unknown_keys_and_comments(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# existing comment\n"
        "CUSTOM_FLAG=yes\n"
        "FOLDER_IDS=old-folder\n"
        "\n",
        encoding="utf-8",
    )

    setup._update_env_file(
        env_path,
        {
            "FOLDER_IDS": "new-folder",
            "STT_PROVIDER": "deepgram",
            "DEEPGRAM_API_KEY": "dg-secret",
        },
    )

    text = env_path.read_text(encoding="utf-8")
    assert "# existing comment" in text
    assert "CUSTOM_FLAG=yes" in text
    assert "FOLDER_IDS=new-folder" in text
    assert "STT_PROVIDER=deepgram" in text
    assert "DEEPGRAM_API_KEY=dg-secret" in text
    assert "FOLDER_IDS=old-folder" not in text


def test_update_env_file_preserves_inline_comment_on_updated_key(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "FOLDER_IDS=old-folder  # keep this note\n",
        encoding="utf-8",
    )

    setup._update_env_file(env_path, {"FOLDER_IDS": "new-folder"})

    text = env_path.read_text(encoding="utf-8")
    assert "FOLDER_IDS=new-folder  # keep this note" in text


def test_run_confirmed_gcloud_command_runs_when_confirmed():
    calls: list[list[str]] = []

    def run_command(command: list[str]):
        calls.append(command)

    result = setup._run_confirmed_gcloud_command(
        ["gcloud", "services", "enable", "drive.googleapis.com"],
        summary="Enable Drive API",
        input_func=lambda prompt: "y",
        run_command=run_command,
        print_func=lambda *args, **kwargs: None,
    )

    assert result is True
    assert calls == [["gcloud", "services", "enable", "drive.googleapis.com"]]


def test_run_confirmed_gcloud_command_skips_when_declined():
    calls: list[list[str]] = []

    def run_command(command: list[str]):
        calls.append(command)

    result = setup._run_confirmed_gcloud_command(
        ["gcloud", "services", "enable", "drive.googleapis.com"],
        summary="Enable Drive API",
        input_func=lambda prompt: "n",
        run_command=run_command,
        print_func=lambda *args, **kwargs: None,
    )

    assert result is False
    assert calls == []


def test_prompt_profile_api_keys_requests_only_required_secrets():
    prompts: list[str] = []
    answers = iter(["dg-secret", "sk-secret"])

    updates = setup._prompt_profile_api_keys(
        load_pipeline_profile(),
        {},
        secret_input_func=lambda prompt: prompts.append(prompt) or next(answers),
        print_func=lambda *args, **kwargs: None,
    )

    assert updates == {
        "DEEPGRAM_API_KEY": "dg-secret",
        "OPENAI_API_KEY": "sk-secret",
    }
    assert prompts == ["Deepgram API key: ", "OpenAI API key: "]


def test_run_setup_resume_skips_existing_credentials_and_token_when_declined(tmp_path, mocker):
    (tmp_path / ".env.example").write_text(
        "FOLDER_IDS=\nSTT_PROVIDER=\nDEEPGRAM_API_KEY=\n",
        encoding="utf-8",
    )

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    credentials_path = data_dir / "credentials.json"
    credentials_path.write_text('{"installed": {"client_id": "existing"}}', encoding="utf-8")
    token_path = data_dir / "token.json"
    token_path.write_text('{"token": "existing"}', encoding="utf-8")
    adc_path = tmp_path / "adc.json"
    adc_path.write_text(
        json.dumps(
            {
                "client_id": "cid.apps.googleusercontent.com",
                "client_secret": "csec",
                "refresh_token": "refresh",
            }
        ),
        encoding="utf-8",
    )

    prepare_mock = mocker.patch("src.setup._prepare_credentials_from_adc")
    auth_mock = mocker.patch("src.setup.auth.run_interactive_flow")
    verify_mock = mocker.patch("src.setup.verify_drive_setup")
    mocker.patch("src.setup._discover_gcloud", return_value="gcloud")
    mocker.patch("src.setup._select_gcloud_project", return_value="proj-1")
    mocker.patch("src.setup._ensure_drive_api_enabled", return_value=True)
    mocker.patch("src.setup._default_adc_path", return_value=adc_path)

    answers = iter(["folder-123", "n", "n"])
    secret_answers = iter(["dg-secret", "sk-secret"])

    setup.run_setup(
        repo_root=tmp_path,
        input_func=lambda prompt: next(answers),
        secret_input_func=lambda prompt: next(secret_answers),
        print_func=lambda *args, **kwargs: None,
        profile=load_pipeline_profile(),
    )

    prepare_mock.assert_not_called()
    auth_mock.assert_not_called()
    verify_mock.assert_called_once_with(print_func=mocker.ANY)
    assert "FOLDER_IDS=folder-123" in (tmp_path / ".env").read_text(encoding="utf-8")
    assert credentials_path.read_text(encoding="utf-8") == '{"installed": {"client_id": "existing"}}'
    assert token_path.read_text(encoding="utf-8") == '{"token": "existing"}'


def test_run_setup_does_not_resolve_adc_path_when_existing_credentials_are_kept(tmp_path, mocker):
    (tmp_path / ".env.example").write_text(
        "FOLDER_IDS=\nSTT_PROVIDER=\nDEEPGRAM_API_KEY=\n",
        encoding="utf-8",
    )

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "credentials.json").write_text('{"installed": {"client_id": "existing"}}', encoding="utf-8")
    (data_dir / "token.json").write_text('{"token": "existing"}', encoding="utf-8")

    auth_mock = mocker.patch("src.setup.auth.run_interactive_flow")
    verify_mock = mocker.patch("src.setup.verify_drive_setup")
    mocker.patch("src.setup._discover_gcloud", return_value=None)
    default_adc_mock = mocker.patch(
        "src.setup._default_adc_path",
        side_effect=ValueError("APPDATA is not set; cannot locate gcloud ADC on Windows"),
    )

    answers = iter(["folder-123", "n", "n"])
    secret_answers = iter(["dg-secret", "sk-secret"])

    setup.run_setup(
        repo_root=tmp_path,
        input_func=lambda prompt: next(answers),
        secret_input_func=lambda prompt: next(secret_answers),
        print_func=lambda *args, **kwargs: None,
        profile=load_pipeline_profile(),
    )

    default_adc_mock.assert_not_called()
    auth_mock.assert_not_called()
    verify_mock.assert_called_once_with(print_func=mocker.ANY)
