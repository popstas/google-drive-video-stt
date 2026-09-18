from __future__ import annotations

import dataclasses
import logging
from unittest.mock import MagicMock

from src import delegation, meet_root
from src.config import EmployeeFolder

from tests.test_main import make_config


def _delegating(folders, tmp_path, **extra):
    cfg = make_config(folders=["unused"], data_dir=tmp_path, stt_provider="", **extra)
    return dataclasses.replace(
        cfg,
        folders=tuple(folders),
        google_service_account={"client_email": "reader@project.iam.gserviceaccount.com"},
    )


def _root(folder_id, *, candidates=1):
    return meet_root.MeetRoot(
        folder_id=folder_id,
        name="Google Meet",
        created_time="2026-09-16T09:00:00Z",
        candidates=candidates,
    )


def test_an_address_becomes_a_folder_and_a_client_of_its_own(tmp_path, mocker):
    services = {"one@example.com": MagicMock(), "two@example.com": MagicMock()}
    build = mocker.patch(
        "src.delegation.auth.build_drive_service",
        side_effect=lambda config, subject: services[subject],
    )
    mocker.patch(
        "src.delegation.meet_root.resolve",
        side_effect=lambda service, names: _root(
            "f1" if service is services["one@example.com"] else "f2"
        ),
    )
    mocker.patch("src.delegation.meet_root.owner_name", return_value="From Drive")
    config = _delegating(
        [EmployeeFolder(folder_id="", email="one@example.com"),
         EmployeeFolder(folder_id="", email="two@example.com", name="Given")],
        tmp_path,
    )

    fleet = delegation.resolve(config, MagicMock(), build=build)

    assert [(f.folder_id, f.name) for f in fleet.config.folders] == [
        ("f1", "From Drive"),
        ("f2", "Given"),
    ]
    assert fleet.service_for("f1") is services["one@example.com"]
    assert fleet.service_for("f2") is services["two@example.com"]
    assert fleet.errors == 0


def test_without_a_service_account_nothing_is_resolved(tmp_path, mocker):
    """A deployment that has not adopted delegation must not gain a single request."""
    build = mocker.patch("src.delegation.auth.build_drive_service")
    resolve_mock = mocker.patch("src.delegation.meet_root.resolve")
    config = make_config(folders=["f1"], data_dir=tmp_path, stt_provider="")
    shared = MagicMock()

    fleet = delegation.resolve(config, shared, build=build)

    assert fleet.config is config
    assert fleet.service_for("f1") is shared
    build.assert_not_called()
    resolve_mock.assert_not_called()


def test_a_pinned_folder_is_still_read_as_its_owner(tmp_path, mocker):
    """An id in the config says which folder; delegation still says whose it is."""
    owned = MagicMock()
    build = mocker.patch(
        "src.delegation.auth.build_drive_service", return_value=owned
    )
    resolve_mock = mocker.patch("src.delegation.meet_root.resolve")
    mocker.patch("src.delegation.meet_root.owner_name", return_value="Name")
    config = _delegating(
        [EmployeeFolder(folder_id="pinned", email="one@example.com")], tmp_path
    )

    fleet = delegation.resolve(config, MagicMock(), build=build)

    assert [f.folder_id for f in fleet.config.folders] == ["pinned"]
    assert fleet.service_for("pinned") is owned
    resolve_mock.assert_not_called()


def test_a_folder_with_nobody_attached_keeps_the_shared_client(tmp_path, mocker):
    build = mocker.patch("src.delegation.auth.build_drive_service")
    config = _delegating([EmployeeFolder(folder_id="f1")], tmp_path)
    shared = MagicMock()

    fleet = delegation.resolve(config, shared, build=build)

    assert fleet.service_for("f1") is shared
    build.assert_not_called()


def test_one_employee_failing_costs_only_that_employee(tmp_path, mocker):
    """One address the domain refuses must not take the whole fleet's cycle with it."""
    good = MagicMock()

    def build(config, subject):
        if subject == "gone@example.com":
            raise RuntimeError("invalid_grant")
        return good

    mocker.patch("src.delegation.meet_root.resolve", return_value=_root("f2"))
    mocker.patch("src.delegation.meet_root.owner_name", return_value="Name")
    config = _delegating(
        [EmployeeFolder(folder_id="", email="gone@example.com"),
         EmployeeFolder(folder_id="", email="two@example.com")],
        tmp_path,
    )
    seen = []

    fleet = delegation.resolve(
        config, MagicMock(), build=build, on_error=lambda folder, exc: seen.append(folder.email)
    )

    assert [f.folder_id for f in fleet.config.folders] == ["f2"]
    assert fleet.errors == 1
    assert seen == ["gone@example.com"]


def test_an_employee_without_a_meet_folder_is_skipped_and_counted(tmp_path, mocker):
    mocker.patch("src.delegation.auth.build_drive_service", return_value=MagicMock())
    mocker.patch("src.delegation.meet_root.resolve", return_value=None)
    config = _delegating(
        [EmployeeFolder(folder_id="", email="quiet@example.com")], tmp_path
    )

    fleet = delegation.resolve(config, MagicMock())

    assert fleet.config.folders == ()
    assert fleet.errors == 1


def test_abandoned_roots_are_reported(tmp_path, mocker, caplog):
    """The calls in an abandoned root will never be processed; that must be said."""
    mocker.patch("src.delegation.auth.build_drive_service", return_value=MagicMock())
    mocker.patch(
        "src.delegation.meet_root.resolve", return_value=_root("live", candidates=3)
    )
    mocker.patch("src.delegation.meet_root.owner_name", return_value="Name")
    config = _delegating(
        [EmployeeFolder(folder_id="", email="one@example.com")], tmp_path
    )

    with caplog.at_level(logging.WARNING):
        delegation.resolve(config, MagicMock())

    assert any("3 Meet folders" in record.getMessage() for record in caplog.records)


def test_a_configured_name_is_never_overwritten(tmp_path, mocker):
    mocker.patch("src.delegation.auth.build_drive_service", return_value=MagicMock())
    mocker.patch("src.delegation.meet_root.resolve", return_value=_root("f1"))
    name_mock = mocker.patch("src.delegation.meet_root.owner_name")
    config = _delegating(
        [EmployeeFolder(folder_id="", email="one@example.com", name="Configured")],
        tmp_path,
    )

    fleet = delegation.resolve(config, MagicMock())

    assert fleet.config.folders[0].name == "Configured"
    name_mock.assert_not_called()


def test_an_unreadable_name_does_not_lose_the_folder(tmp_path, mocker):
    mocker.patch("src.delegation.auth.build_drive_service", return_value=MagicMock())
    mocker.patch("src.delegation.meet_root.resolve", return_value=_root("f1"))
    mocker.patch("src.delegation.meet_root.owner_name", side_effect=RuntimeError("no"))
    config = _delegating(
        [EmployeeFolder(folder_id="", email="one@example.com")], tmp_path
    )

    fleet = delegation.resolve(config, MagicMock())

    assert [(f.folder_id, f.name) for f in fleet.config.folders] == [("f1", "")]
    assert fleet.errors == 0
