from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent


def test_compose_is_config_only_and_does_not_require_env_file():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    service = compose["services"]["google-drive-video-stt"]

    assert "env_file" not in service
    assert service["environment"]["GDSTT_HOME"] == "/app/data"
    assert "DATA_DIR" not in service["environment"]
    assert service["volumes"] == ["./data:/app/data"]


def test_docker_smoke_is_config_only_and_initializes_clean_volume():
    smoke = (ROOT / "scripts" / "docker-smoke.sh").read_text(encoding="utf-8")

    assert "--env-file" not in smoke
    assert "gdstt config init" in smoke
    assert "from src.config import load_config" in smoke


def test_compose_documents_but_does_not_publish_the_booking_receiver_port():
    # call_booking.enabled defaults to false, so nothing inside the container
    # listens on 8080 out of the box. Publishing the port unconditionally would
    # break `docker compose up` for any existing deployment where host port 8080
    # happens to be taken by something unrelated -- a backward-compatibility
    # regression for every current user upgrading without touching config.yml.
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    compose = yaml.safe_load(text)
    service = compose["services"]["google-drive-video-stt"]

    assert "ports" not in service

    # The guidance to enable it must survive as a comment, so an operator turning
    # the feature on can find the publish line and the reverse-proxy/TLS warning.
    assert "call_booking.enabled" in text
    assert "reverse proxy" in text
    assert "TLS" in text
    assert '# ports:' in text
    assert '#   - "8080:8080"' in text
