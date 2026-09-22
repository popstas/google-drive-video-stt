from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
import logging
import json
import re
import ssl
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any

from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError
import requests

from src import (
    auth,
    booking_gate,
    booking_server,
    calendar_api,
    change_cursor,
    delegation,
    drive,
    meet_api,
    meet_mark,
    meet_transcript as meet_transcript_module,
    meta as meta_module,
    meta_doc,
    meta_entity,
    notify,
    output,
    planfix,
    planfix_html,
    postprocess,
    preset_pipeline,
    presets as presets_module,
    speaker_roles,
    stt_document,
    webhook,
)
from src.auth import AuthError, build_drive_service, build_meet_service
from src.config import (
    Config,
    EmployeeFolder,
    is_run_enabled,
    load_config,
    parse_since,
)
from src.meeting_time import parse_meeting_start
from src.extractor import extract_m4a_copy, extract_mp3
from src.openai_pipeline import OpenAIPipeline
from src.presets import Preset
from src.stt.base import EmptyTranscriptError
from src.stt.transcribe import transcribe_file

logger = logging.getLogger(__name__)

_TRANSIENT_HTTP_STATUS_CODES = {429, 500, 502, 503, 504}
# Drive's answer when a saved cursor has aged out of its journal. Not a failure to
# report: it is the documented way of being told to start over.
_STALE_CURSOR_HTTP_STATUS_CODES = {404, 410}
# How long a video with no videoMediaMetadata is assumed to be still uploading rather
# than simply never getting any. Generous on purpose: the cost of waiting is one more
# cycle, the cost of giving up too early is a download of a half-written file.
_MEDIA_SETTLING_GRACE = timedelta(hours=2)
_TRANSIENT_RETRY_ATTEMPTS = 3
_TRANSIENT_RETRY_DELAYS = (1.0, 2.0)
# How many cycles in a row a source may fail transiently before anyone is told. With
# ten folders polled every ten minutes Drive drops one request now and then, and that
# heals by itself on the next cycle; an alert for each one taught people to ignore
# the channel. A source still failing after this many cycles is not a blip.
_LISTING_FAILURE_ALERT_STREAK = 3
# Consecutive failed cycles per source, keyed by the `what` of the failure. A source
# that got through a cycle drops out, so the count is a run, not a total.
_LISTING_FAILURE_STREAKS: dict[str, int] = {}
_LISTING_FAILED_THIS_CYCLE: set[str] = set()


@dataclass
class _RetryState:
    retry_count: int = 0


@dataclass
class _ProcessTelemetry:
    provider: str
    processing_mode: str
    retry_count: int
    duration_s: float
    mp3_uploaded: bool = False
    txt_uploaded: bool = False
    cost_usd: dict[str, float | None] = field(default_factory=dict)
    usage: dict[str, dict[str, int]] = field(default_factory=dict)
    transcript: str = ""
    artifacts: dict[str, str] = field(default_factory=dict)
    # The merged meta document `_write_call_documents` built this cycle (None when no
    # preset stage ran). Task 6 reads this to quote the meta fields into the Planfix
    # comment instead of re-parsing the `meta` artifact a second time.
    meta_document: dict[str, object] | None = None


def _http_status_code(exc: Exception) -> int | None:
    if isinstance(exc, HttpError):
        return getattr(exc.resp, "status", None)
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code
    return None


def _is_rejected_cursor(exc: Exception) -> bool:
    """Whether Drive refused the saved cursor itself, rather than failing the read.

    An expired cursor gets 404 or 410. A malformed one gets 400 instead, with the
    error pinned to the ``pageToken`` parameter -- found by writing a corrupt token
    into the cursor file on a live Drive. Read as an ordinary feed failure, that 400
    held the cursor, and a held cursor is the same bad token on the next cycle: the
    service failed every cycle for good while the module promised that a corrupt
    cursor costs one sweep.

    Matching the parameter rather than 400 alone keeps a genuinely broken request
    -- a bad ``fields`` after a code change, say -- surfacing as the failure it is
    instead of being swept over quietly every cycle.
    """
    status = _http_status_code(exc)
    if status in _STALE_CURSOR_HTTP_STATUS_CODES:
        return True
    if status != 400 or not isinstance(exc, HttpError):
        return False
    try:
        details = json.loads(exc.content.decode("utf-8"))["error"]["errors"]
    except (AttributeError, KeyError, TypeError, ValueError):
        return False
    return any(
        isinstance(detail, dict) and detail.get("location") == "pageToken"
        for detail in details
    )


def _is_transient_runtime_error(exc: Exception) -> bool:
    if isinstance(exc, (RefreshError, AuthError)):
        return False
    if isinstance(exc, drive.DownloadIntegrityError):
        return True
    # requests' exceptions only cover our own HTTP calls. The Google API client runs on
    # httplib2, which lets socket and TLS failures through as builtins -- a dropped
    # keep-alive connection arrives as BrokenPipeError, unrelated to requests.ConnectionError.
    # Without the builtins here those never reach the retry path and a routine reconnect
    # escalates into a failed cycle and an alert.
    if isinstance(
        exc,
        (TimeoutError, ConnectionError, ssl.SSLError, requests.ConnectionError, requests.Timeout),
    ):
        return True
    status = _http_status_code(exc)
    return status in _TRANSIENT_HTTP_STATUS_CODES


def _call_with_transient_retries(operation, *, description: str, retry_state: _RetryState | None = None):
    for attempt in range(1, _TRANSIENT_RETRY_ATTEMPTS + 1):
        try:
            return operation()
        except Exception as exc:  # noqa: BLE001
            if attempt >= _TRANSIENT_RETRY_ATTEMPTS or not _is_transient_runtime_error(exc):
                raise
            if retry_state is not None:
                retry_state.retry_count += 1
            delay = _TRANSIENT_RETRY_DELAYS[min(attempt - 1, len(_TRANSIENT_RETRY_DELAYS) - 1)]
            logger.warning(
                "Transient error during %s (attempt %d/%d): %s; retrying in %.1fs",
                description,
                attempt,
                _TRANSIENT_RETRY_ATTEMPTS,
                exc,
                delay,
            )
            time.sleep(delay)


def _save_and_upload_txt(
    service: Any,
    source_file_id: str,
    mp4_name: str,
    text: str,
    container_id: str,
    tmp_dir: Path,
    config: Config,
    *,
    txt_id: str | None = None,
) -> None:
    stem = drive.drive_stem(mp4_name)
    app_properties = {
        drive.SOURCE_VIDEO_ID_PROPERTY: source_file_id,
        drive.ARTIFACT_TYPE_PROPERTY: "txt",
    }
    output.write_artifact(
        service,
        base_name=stem,
        suffix=".txt",
        text=text,
        folder_id=container_id,
        config=config,
        tmp_dir=tmp_dir,
        existing_id=txt_id,
        app_properties=app_properties,
        mime_type=drive.TXT_MIME,
    )


def _save_and_upload_preset(
    service: Any,
    source_file_id: str,
    mp4_name: str,
    preset: Preset,
    text: str,
    container_id: str,
    tmp_dir: Path,
    config: Config,
    *,
    existing_id: str | None = None,
) -> None:
    stem = drive.drive_stem(mp4_name)
    app_properties = {
        drive.SOURCE_VIDEO_ID_PROPERTY: source_file_id,
        drive.ARTIFACT_TYPE_PROPERTY: preset.name,
    }
    output.write_artifact(
        service,
        base_name=stem,
        suffix=preset.artifact_suffix,
        text=text,
        folder_id=container_id,
        config=config,
        tmp_dir=tmp_dir,
        existing_id=existing_id,
        app_properties=app_properties,
        mime_type=drive.MD_MIME,
    )


def _run_preset_stage(
    service: Any,
    source_file_id: str,
    mp4_name: str,
    transcript: str,
    folder_id: str,
    container_id: str,
    tmp_dir: Path,
    config: Config,
    *,
    speaker_names: list[str] | None,
    participants: list[str] | None = None,
    speakers: list[str] | None = None,
    artifact_ids: dict[str, str],
    reprocess: bool,
    usage: dict[str, dict[str, int]],
    unproduced: set[str],
    local_artifact_paths: dict[str, Path] | None = None,
    only_presets: list[str] | None = None,
) -> dict[str, str]:
    """Run the enabled preset DAG over a transcript and persist each new artifact.

    Returns every enabled preset's text keyed by preset name — freshly produced ones
    plus any that completed on an earlier cycle, read back from their artifacts — so
    callers (the completion webhook) ship a file's full set of outputs even though
    only the still-missing presets were run. The earlier-cycle backfill costs a Drive
    read per artifact and only the webhook consumes it, so it is skipped entirely
    when ``webhook.url`` is unset; the return is then this cycle's presets alone.

    Only presets still missing an artifact are produced (``reprocess`` re-runs them
    all, overwriting in place). Successful, non-empty outputs are written as soon as
    the stage returns; if any preset failed, an aggregated error is raised. For
    Drive targets the file is re-selected on a later cycle (its ``.txt`` sibling is
    re-fed without re-running STT) so only the still-missing presets retry. Folder
    targets write preset artifacts to local disk, which ``list_folder_state`` does
    not track, so their preset stage runs once per transcription only.

    ``unproduced`` collects presets that ran without error but returned blank text, so
    no artifact was written. Those files come back next cycle, so the caller must not
    report this pass as the file's completion.
    """
    preset_by_name = {preset.name: preset for preset in config.presets}
    if not preset_by_name:
        return {}
    local_artifact_paths = local_artifact_paths or {}
    existing_names = set(artifact_ids) | set(local_artifact_paths)
    if only_presets is not None:
        # Force-rerun an explicit set of stages (``gdstt reprocess``); their
        # dependencies are reused from existing artifacts below rather than re-run.
        missing = [name for name in only_presets if name in preset_by_name]
    elif reprocess:
        missing = list(preset_by_name)
    else:
        missing = [name for name in preset_by_name if name not in existing_names]

    # Reuse dependency artifacts already persisted on Drive so a retry re-runs
    # only the still-missing presets (per the plan): a dependency that completed
    # on an earlier cycle is re-fed from its artifact instead of being re-run,
    # which avoids extra OpenAI spend and keeps dependent siblings consistent with
    # the dependency output that produced the earlier ones.
    def load_existing(name: str) -> str | None:
        existing_id = artifact_ids.get(name)
        if existing_id is not None:
            return _call_with_transient_retries(
                lambda: drive.download_text(service, existing_id),
                description=f"download {name} artifact for {mp4_name}",
            )
        local_path = local_artifact_paths.get(name)
        if local_path is not None:
            return local_path.read_text(encoding="utf-8")
        return None

    # Every webhook POST carries a file's full artifact set, so a preset that
    # succeeded on an earlier cycle — and is therefore not re-run here — still has
    # to reach the receiver.
    # Backfilling it costs a Drive download apiece, and the completion webhook is
    # this data's only consumer, so skip the reads outright when no receiver is
    # configured (``notify_complete`` would discard them on its blank-URL return).
    # These reads only enrich the payload, so they must never fail the file: every
    # artifact is already persisted by the time this runs, and raising here would
    # both alert on a good record and — since the next cycle sees no missing presets
    # — leave the webhook permanently undelivered. Degrade to a partial payload.
    def backfill(
        produced: dict[str, str], precomputed: dict[str, str]
    ) -> dict[str, str]:
        if not config.webhook_url.strip():
            return produced
        for name in preset_by_name:
            if name in produced:
                continue
            text = precomputed.get(name)
            if text is None:
                try:
                    text = load_existing(name)
                except Exception as exc:
                    logger.warning(
                        "Webhook backfill skipped [preset=%s, file=%s]: %s",
                        name,
                        mp4_name,
                        type(exc).__name__,
                    )
                    continue
            if text is not None and text.strip():
                produced[name] = text
        return produced

    if not missing:
        # Every preset already has an artifact, so nothing is re-run — but the file
        # can still reach the webhook (its ``.txt`` was regenerated this cycle), and
        # the receiver expects the full set, so the artifacts are read back.
        return backfill({}, {})

    precomputed: dict[str, str] = {}
    if not reprocess:
        for dep in preset_pipeline.dependency_names(config.presets, missing):
            text = load_existing(dep)
            if text is not None:
                precomputed[dep] = text

    employee = config.folder_by_id(folder_id)
    results = preset_pipeline.run_presets(
        transcript,
        mp4_name,
        config,
        config.presets,
        speaker_names=speaker_names,
        manager_name=employee.name if employee else "",
        participants=participants,
        speakers=speakers,
        only=missing,
        precomputed=precomputed,
    )
    generated_names = set(results) - set(precomputed)
    names_to_save = set(missing) | (generated_names - existing_names)
    ordered_names = [
        name
        for name in preset_pipeline.topological_order(config.presets)
        if name in names_to_save
    ]
    for name in ordered_names:
        result = results.get(name)
        if result is None or not result.ok:
            continue
        if not result.text.strip():
            # Blank output writes no artifact (by design — a blank doc is worthless),
            # so the preset stays "missing" and the file is re-selected next cycle.
            # Record it: the webhook must not treat this pass as the file's completion
            # and re-POST the transcript on every cycle from here on.
            unproduced.add(name)
            logger.warning(
                "Preset %s returned empty output for %s; no artifact written",
                name,
                mp4_name,
            )
            continue
        if result.usage:
            usage[f"openai_{name}"] = dict(result.usage)
        _save_and_upload_preset(
            service,
            source_file_id,
            mp4_name,
            preset_by_name[name],
            result.text,
            container_id,
            tmp_dir,
            config,
            existing_id=artifact_ids.get(name),
        )

    aggregated = preset_pipeline.aggregate_error(results)
    if aggregated:
        raise RuntimeError(aggregated)

    produced = {
        name: result.text
        for name, result in results.items()
        if result.ok and result.text.strip()
    }
    return backfill(produced, precomputed)


def _prepare_deepgram_audio(mp4_path: Path, config: Config) -> Path:
    if config.deepgram_audio_source == "m4a_copy":
        return extract_m4a_copy(mp4_path)
    if config.deepgram_audio_source == "mp3_96k":
        return extract_mp3(mp4_path, bitrate="96k")
    if config.deepgram_audio_source == "mp3_192k":
        return extract_mp3(mp4_path, bitrate="192k")
    raise RuntimeError(f"Unknown Deepgram audio source: {config.deepgram_audio_source}")


def _should_make_mp3_artifact(config: Config) -> bool:
    return config.drive_mp3_artifact


def _local_artifact_path(config: Config, mp4_name: str, suffix: str) -> Path | None:
    if config.output_target != "folder" or config.output_dir is None:
        return None
    stem = drive.drive_stem(mp4_name)
    return config.output_dir / (drive.safe_local_name(stem) + suffix)


def _artifact_text(
    name: str, artifacts: dict[str, str], config: Config, mp4_name: str
) -> str:
    """This cycle's text for a preset, or the artifact an earlier cycle left on disk.

    A cycle that re-ran only the still-missing presets returns just those, so the
    document would otherwise lose the sections that completed earlier.
    """
    text = artifacts.get(name, "")
    if text.strip():
        return text
    preset = next((p for p in config.presets if p.name == name), None)
    if preset is None:
        return ""
    path = _local_artifact_path(config, mp4_name, preset.artifact_suffix)
    if path is None or not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not read %s for the .stt document: %s", path, type(exc).__name__)
        return ""


def _client_emails(item: dict, file_name: str, folder_id: str, config: Config) -> list[str]:
    """The call's invited outsiders, from the employee's calendar; ``[]`` on any doubt.

    A nicety, not a requirement: a missing scope or a calendar outage is a warning and
    an empty list, never a failed recording and never an escalation.
    """
    if not config.calendar_client_emails or not config.uses_delegation:
        return []
    folder = config.folder_by_id(folder_id)
    subject = folder.email.strip() if folder else ""
    if not subject:
        return []
    start = item.get("meeting_start") or parse_meeting_start(file_name)
    if start is None:
        return []
    own_domains = {
        entry.email.rsplit("@", 1)[1].strip().lower()
        for entry in config.folders
        if "@" in entry.email
    }
    try:
        service = auth.build_calendar_service(config=config, subject=subject)
        return calendar_api.client_emails(
            service,
            start=start,
            own_domains=own_domains,
            window_minutes=config.call_booking_threshold_minutes,
        )
    except Exception as exc:
        logger.warning(
            "Could not read the calendar invitees of %s: %s", file_name, type(exc).__name__
        )
        return []


def _write_call_documents(
    service: Any,
    file_id: str,
    file_name: str,
    folder_id: str,
    container_id: str,
    transcript: str,
    artifacts: dict[str, str],
    config: Config,
    tmp_dir: Path,
    *,
    item: dict,
    booking_decision: booking_gate.BookingDecision,
) -> dict[str, object] | None:
    """Write ``<stem>.meta.yml`` and ``<stem>.stt`` for one recording.

    Returns the meta document so the Planfix comment can quote from it. Both files are
    written from artifacts that already exist, so this costs no model call. Neither file
    takes part in the processed/pending bookkeeping: only ``.txt`` and the preset
    artifacts decide whether a recording still needs work, and a deleted ``.stt`` must
    never put a recording back into the transcription queue.
    """
    stem = drive.drive_stem(file_name)
    values = meta_module.parse_meta(
        _artifact_text("meta", artifacts, config, file_name),
        config.meta_entities,
    )
    task_id = booking_decision.task_id or str(item.get("planfix_comment_task_id") or "")
    document = meta_doc.build(
        values=values,
        file_id=file_id,
        file_name=file_name,
        folder_id=folder_id,
        config=config,
        transcript=transcript,
        planfix_task_id=task_id,
        processed_at=datetime.now(timezone.utc),
        container_id=container_id,
        calendly_event_uuid=booking_decision.calendly_event_uuid,
        client_emails=_client_emails(item, file_name, folder_id, config),
    )
    meta_yaml = meta_doc.to_yaml(document, config.meta_entities)

    body = _artifact_text("transcript-cleanup", artifacts, config, file_name) or transcript
    text = stt_document.assemble(
        title=stem,
        sections=[
            _artifact_text(name, artifacts, config, file_name) for name in config.stt_presets
        ],
        meta_yaml=meta_yaml,
        transcript=body,
    )

    output.write_artifact(
        service, base_name=stem, suffix=".meta.yml", text=meta_yaml,
        folder_id=container_id, config=config, tmp_dir=tmp_dir,
        existing_id=item.get("meta_yml_id"),
    )
    output.write_artifact(
        service, base_name=stem, suffix=".stt", text=text, folder_id=container_id,
        config=config, tmp_dir=tmp_dir,
        existing_id=item.get("stt_id"),
        # No source_video_id: the transcript (`.txt`) is looked up on Drive by that
        # same appProperty, and `.stt` also uploads as text/plain, so carrying it
        # here would risk the `.stt` winning that lookup and being fed to the preset
        # stage -- or overwritten -- as if it were the transcript. `drive.py` also
        # excludes `.stt`/`.meta.yml` from that lookup by name, belt and suspenders.
        app_properties={drive.ARTIFACT_TYPE_PROPERTY: "stt"},
        mime_type=drive.TXT_MIME,
    )
    return document


def _try_write_call_documents(
    service: Any,
    file_id: str,
    file_name: str,
    folder_id: str,
    container_id: str,
    transcript: str,
    artifacts: dict[str, str],
    config: Config,
    tmp_dir: Path,
    *,
    item: dict,
    booking_decision: booking_gate.BookingDecision,
) -> dict[str, object] | None:
    """Call ``_write_call_documents``, degrading to no document on failure.

    The ``.stt``/``.meta.yml`` write happens after the ``.txt`` and every preset
    artifact are already persisted. Letting a write failure here propagate would
    re-raise out of ``process_item`` and leave the recording looking fully
    processed on the next cycle (``has_txt`` true, no missing presets) -- so the
    webhook and the Planfix comment, which run after this returns, would never
    fire, and no later cycle would retry them. A document that "takes no part in
    the bookkeeping" by design must not be able to fail a recording that already
    transcribed successfully.
    """
    try:
        return _write_call_documents(
            service, file_id, file_name, folder_id, container_id, transcript, artifacts,
            config, tmp_dir, item=item, booking_decision=booking_decision,
        )
    except Exception as exc:
        logger.warning(
            "Could not write the .stt/.meta.yml documents for %s: %s",
            file_name, type(exc).__name__,
        )
        return None


def _local_artifact_paths(item: dict) -> dict[str, Path]:
    paths = item.get("local_artifact_paths") or {}
    return {name: Path(path) for name, path in paths.items()}


def _existing_preset_names(item: dict) -> set[str]:
    artifact_ids = item.get("artifact_ids") or {}
    return set(artifact_ids) | set(_local_artifact_paths(item))


def _missing_preset_names(item: dict, config: Config) -> list[str]:
    """Enabled presets that have no artifact yet for this item."""
    existing = _existing_preset_names(item)
    return [preset.name for preset in config.presets if preset.name not in existing]


def _has_existing_transcript(item: dict) -> bool:
    return item.get("txt_id") is not None or item.get("local_txt_path") is not None


def _needs_preset_reprocess(item: dict, config: Config, *, needs_txt: bool) -> bool:
    """Whether to re-run missing presets from an existing Drive transcript.

    Only applies when a Drive ``.txt`` sibling already exists (``txt_id``) and the
    transcript is not being regenerated this pass. Folder-mode transcripts have no
    ``txt_id`` (the ``.txt`` lives on local disk, not as a Drive sibling), so they
    are excluded — their preset artifacts are not tracked in ``artifact_ids`` and
    would otherwise reprocess on every cycle.
    """
    if needs_txt or not config.presets:
        return False
    if not _has_existing_transcript(item):
        return False
    return bool(_missing_preset_names(item, config))


def _apply_local_output_state(items: list[dict], config: Config) -> list[dict]:
    """Reflect local artifacts in sibling flags when output.target=folder.

    In folder mode the .txt is written to output.dir instead of as a Drive
    sibling, so the Drive-derived ``has_txt`` flag never flips to True. Without
    this, the daemon would re-select the same source on every poll and re-run
    Deepgram (and OpenAI keypoints) indefinitely. Mark ``has_txt`` from the
    local output file so processing stays idempotent.
    """
    if config.output_target != "folder" or config.output_dir is None:
        return items
    for item in items:
        file_name = item["file"]["name"]
        local_txt = _local_artifact_path(config, file_name, ".txt")
        if local_txt.exists():
            item["has_txt"] = True
            item["local_txt_path"] = local_txt
        local_paths = _local_artifact_paths(item)
        for preset in config.presets:
            local_artifact = _local_artifact_path(config, file_name, preset.artifact_suffix)
            if local_artifact is not None and local_artifact.exists():
                local_paths[preset.name] = local_artifact
        if local_paths:
            item["local_artifact_paths"] = local_paths
    return items


def _speaker_names_from_file_info(file_info: dict) -> list[str] | None:
    raw = file_info.get("appProperties", {}).get(drive.SPEAKER_NAMES_PROPERTY)
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Ignoring invalid speaker_names appProperty on %s", file_info.get("id"))
        return None
    if not isinstance(parsed, list):
        return None
    names = [item.strip() for item in parsed if isinstance(item, str) and item.strip()]
    return names or None


def _read_meet_transcript(
    service: Any, container_id: str, file_name: str
) -> tuple[list[str], str] | None:
    """Who Meet says was on this call and the transcript itself, or ``None``.

    Meet writes a transcript next to every recording and names the people in it. That
    closes the one gap the recording's own name cannot: a call started outside the
    calendar is named after the meeting room, so there is nothing in it to read and the
    speakers stay ``Speaker 1`` / ``Speaker 2``. Even when the name does carry names it
    carries the ones the calendar invite used, which is how "Viktoriia" arrives without
    a surname. The text travels with the names because its turns are the model's best
    evidence of who is who.

    Failure here is not failure of the recording: no transcript, no access to it, or a
    shape this cannot read all return ``None`` and leave the existing name parsing in
    charge.
    """
    try:
        doc = drive.find_meet_transcript(service, container_id, file_name)
        if doc is None:
            return None
        text = drive.export_document_text(service, doc["id"])
    except (RefreshError, AuthError):
        raise
    except Exception:
        logger.info(
            "Could not read Meet's transcript beside %s; falling back to the file name",
            file_name,
            exc_info=True,
        )
        return None

    names = meet_transcript_module.participants(text)
    if len(names) < 2:
        return None
    logger.info("Meet's transcript names %s for %s", names, file_name)
    return names, text


def _speaker_candidates(
    item: dict, from_document: list[str] | None = None
) -> list[str] | None:
    """Who could be a diarized voice on this call.

    Both sources, not one. The transcript document goes first because its names are
    the exact strings the model sees in the turns it is given as evidence, and the
    answer is validated against this list -- a candidate spelled differently from the
    evidence is a correct answer thrown away. Meet's own participants follow, and they
    are what makes the list complete: the document is read with a limit of two names,
    which on a call between four people leaves the model two candidates for four
    voices.

    Whoever spoke leads whoever merely attended: a participant who never said anything
    cannot be one of the voices diarization separated.

    ``None`` when neither source has anything, and the caller keeps its own fallback.
    """
    ordered: list[str] = []
    seen: set[str] = set()
    for source in (
        from_document or [],
        item.get("speakers") or [],
        item.get("participants") or [],
    ):
        for name in source:
            cleaned = (name or "").strip()
            key = cleaned.casefold()
            if cleaned and key not in seen:
                seen.add(key)
                ordered.append(cleaned)
    return ordered or None


def _resolve_speaker_names(
    transcript: str,
    file_name: str,
    folder_id: str,
    config: Config,
    *,
    usage: dict[str, dict[str, int]] | None = None,
    candidates: list[str] | None = None,
    meet_text: str = "",
) -> list[str] | None:
    """Ask the model which diarized speaker is which participant.

    Without this the names extracted from the file name are bound to speakers by who
    talks first, which silently swaps the pair on every call the client opens. The
    model gets the opening minutes of the transcript, Meet's own turns for the same
    minutes when there are any, the folder's owner and the name the calendar title
    marked with the company.

    Returns the names in speaker order when the model placed them, and ``[]`` -- leave
    the speakers numbered -- when it was asked and did not: binding the names by
    position instead would be right only when the manager happens to speak first, and
    wrong silently. ``None`` means the model was never asked (no key, fewer than two
    names), and the caller keeps binding the file name's names by position, as it
    always did without a model.
    """
    if not config.openai_api_key:
        return None
    if candidates is None:
        candidates = postprocess.extract_interlocutor_names(file_name)
    if len(candidates) < 2:
        return None

    employee = config.folder_by_id(folder_id)
    calendar_manager, _ = postprocess.split_participants(file_name)
    pipeline = OpenAIPipeline(
        api_key=config.openai_api_key,
        model=config.openai_model,
        proxy_url=config.proxy_url,
    )
    try:
        names = speaker_roles.resolve(
            postprocess.clean_transcript(transcript),
            candidates=candidates,
            manager_name=employee.name if employee else "",
            run=pipeline.run,
            meet_text=meet_text,
            calendar_manager=calendar_manager,
        )
    finally:
        if usage is not None and pipeline.last_usage:
            usage["openai_speaker_roles"] = dict(pipeline.last_usage)
        pipeline.close()

    if names is None:
        logger.info("Speaker roles unresolved for %s; leaving the speakers numbered", file_name)
        return []
    logger.info("Speaker roles resolved for %s", file_name)
    return names


def _coerce_size_bytes(raw: Any) -> int | None:
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _processing_provider(config: Config, *, needs_txt: bool) -> str:
    if needs_txt and config.stt_provider:
        return config.stt_provider
    return "artifact-only"


def _processing_mode(*, needs_mp3: bool, needs_txt: bool) -> str:
    if needs_mp3 and needs_txt:
        return "artifact-and-txt"
    if needs_mp3:
        return "artifact-only"
    return "txt-only"


def _processing_outcome(exc: Exception | None) -> str:
    if exc is None:
        return "success"
    return "failed"


def _cycle_outcome(*, dry_run: bool, failed: int, folder_errors: int) -> str:
    if dry_run:
        return "dry_run"
    if failed or folder_errors:
        return "partial_failure"
    return "success"


def _retry_count_from_process_result(result: Any) -> int:
    retry_count = getattr(result, "retry_count", None)
    return retry_count if isinstance(retry_count, int) else 0


def _retry_count_from_exception(exc: Exception) -> int:
    retry_count = getattr(exc, "gdstt_retry_count", None)
    return retry_count if isinstance(retry_count, int) else 0


def _webhook_payload(
    file_id: str,
    file_name: str,
    folder_id: str,
    config: Config,
    transcript: str,
    artifacts: dict[str, str],
) -> dict:
    """Build the completion-webhook body.

    Non-``meta`` presets pass through as raw text keyed by preset name, so adding a
    preset to config.yml extends the payload with no code change. ``meta`` is parsed
    into one key per configured entity (``config.meta_entities``) -- the built-in
    ``{subject, tags, referral, referral_note}`` for an operator who hasn't declared
    ``meta.entities``, or whatever else that config names instead. Enum values are
    filtered to each entity's allow-list. An unknown employee sends empty strings
    rather than omitting the key.
    """
    employee = config.folder_by_id(folder_id)
    payload_artifacts: dict[str, object] = dict(artifacts)
    meta_text = artifacts.get("meta")
    if meta_text is not None:
        parsed = meta_module.parse_meta(meta_text, config.meta_entities)
        payload_artifacts["meta"] = dict(parsed)

    return {
        "file": {"id": file_id, "name": file_name, "folder_id": folder_id},
        "employee": {
            "name": employee.name if employee else "",
            "email": employee.email if employee else "",
        },
        "transcript": transcript,
        "artifacts": payload_artifacts,
    }


# Labels for the meta-document fields the code fills in itself. Entity labels come
# from the entities -- see MetaEntity.planfix_label.
_PLANFIX_CODE_LABELS = {
    "manager": "Менеджер",
    "client": "Клиент",
    "date": "Дата",
    "duration": "Длительность",
    "video_url": "Запись",
}


def _planfix_labels(
    entities: tuple[meta_entity.MetaEntity, ...],
) -> dict[str, str]:
    """Every field that may appear in the comment header, mapped to its label."""
    labels = dict(_PLANFIX_CODE_LABELS)
    for entity in entities:
        labels[entity.name] = entity.planfix_label
    return labels


# Markers prepended to the keypoints headings in the Planfix comment, so the three
# sections are tellable apart while scrolling a CRM feed. They live here and not in the
# preset prompt on purpose: the `.keypoints.md` artifact and the `.stt` document are
# read as documents and parsed by other tools, where a symbol in a heading is noise.
# A heading this map does not name is left exactly as the preset wrote it.
_PLANFIX_SECTION_MARKERS = {
    "Задачи": "☑️",
    "Тезисы": "📝",
    "Открытые вопросы": "❓",
}

_MARKDOWN_HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})[ \t]+(?P<title>.+?)[ \t]*$", re.MULTILINE)


def _mark_planfix_sections(text: str) -> str:
    """Mark the known keypoints headings and give each one air, for the CRM comment only.

    A marked heading is surrounded by blank lines: run together, the three sections read
    as one wall of text in a Planfix feed. Sub-headings the preset emits per assignee
    (``### Mels``) are names, not section titles, so they miss the map and pass through
    untouched, tight against the section they belong to.
    """
    gap = planfix_html.SECTION_BREAK

    def _mark(match: re.Match[str]) -> str:
        marker = _PLANFIX_SECTION_MARKERS.get(match.group("title"))
        if marker is None:
            return match.group(0)
        heading = f"{match.group('hashes')} {marker} {match.group('title')}"
        return f"{gap}\n\n{heading}\n\n{gap}"

    return _MARKDOWN_HEADING_RE.sub(_mark, text)


def _planfix_meta_lines(
    document: dict[str, object] | None,
    fields: tuple[str, ...],
    entities: tuple[meta_entity.MetaEntity, ...],
) -> list[str]:
    """Render the selected meta fields as Markdown lines for the comment header.

    A field that is empty, or is not in ``labels`` at all (an unknown name -- e.g.
    a stale entry left in ``planfix.meta_fields`` after an entity was removed), is
    skipped silently, so shortening the configured list or a call with no referral
    never leaves a dangling label. That is a different case from a field whose
    *label* is the empty string: such a field is rendered bold with no label
    instead of being skipped; the first one encountered in ``fields`` is hoisted to
    the top as the comment's heading, and any further ones follow in place as bold
    lines.
    """
    if not document:
        return []
    labels = _planfix_labels(entities)
    lines: list[str] = []
    heading_taken = False
    for field_name in fields:
        if field_name not in labels:
            continue
        value = document.get(field_name)
        if isinstance(value, list):
            value = ", ".join(str(entry) for entry in value)
        # Collapse embedded newlines (free LLM text can carry them): markdown_to_html
        # splits on "\n", so an unnormalised value would fracture the header into
        # extra, label-less paragraphs -- or a stray bullet/heading if the
        # continuation happened to start with "- " or "#".
        text = " ".join(str(value or "").split())
        if not text:
            continue
        label = labels[field_name]
        if not label:
            if heading_taken:
                lines.append(f"**{text}**")
            else:
                lines.insert(0, f"**{text}**")
                heading_taken = True
        elif field_name == "video_url":
            # source_name is excluded from the default field list *because* it becomes
            # this anchor's text instead of a line of its own; fall back to the fixed
            # label when the document carries no source_name (or an empty one).
            anchor = str(document.get("source_name") or "").strip()
            anchor = " ".join(anchor.split()) or label
            # The meeting folder beats the bare video: it holds the transcript too, and
            # asking for access to it is asking for the whole call. Substituted here
            # rather than listed as a field of its own so every configured
            # ``planfix.meta_fields`` gets it without an edit.
            href = " ".join(str(document.get("folder_url") or "").split()) or text
            lines.append(f"[{anchor}]({href})")
        else:
            lines.append(f"**{label}:** {text}")
    return lines


_MARKDOWN_LINK_RE = re.compile(r"\[(?P<text>[^\]]+)\]\((?P<url>[^)\s]+)\)")
_MARKDOWN_BOLD_RE = re.compile(r"\*\*(?P<text>.+?)\*\*")
_MARKDOWN_TASK_MARKER_RE = re.compile(r"^(?P<bullet>[ \t]*[-*+][ \t]+)\[[ xX]\][ \t]*", re.MULTILINE)


def _summary_sections(
    artifacts: dict[str, str], preset_names: tuple[str, ...]
) -> list[str]:
    """The preset artifacts that carry text, in configured order.

    Shared by both delivery channels so a call reads the same in Planfix and in
    Telegram; only the rendering below them differs.
    """
    return [
        artifacts[name].strip()
        for name in preset_names
        if artifacts.get(name, "").strip()
    ]


def _to_plain_text(markdown: str) -> str:
    """Flatten the Markdown a preset emits into text Telegram can show as-is.

    Telegram's parse modes are all-or-nothing: one unbalanced ``*`` or ``<`` anywhere
    in a transcript-derived document fails the whole ``sendMessage`` call, so the
    summary goes out unparsed and the markup has to come off here instead. Headings
    keep their title, links become "text: url", bold loses its asterisks; list dashes
    stay, because they read fine as plain text, but a task's `[ ]` checkbox does not
    render in Telegram and comes off.
    """
    text = _MARKDOWN_TASK_MARKER_RE.sub(lambda m: m.group("bullet"), markdown)
    text = _MARKDOWN_LINK_RE.sub(
        lambda m: f"{m.group('text')}: {m.group('url')}", text
    )
    text = _MARKDOWN_HEADING_RE.sub(lambda m: m.group("title"), text)
    return _MARKDOWN_BOLD_RE.sub(lambda m: m.group("text"), text)


# Where a Telegram reader goes next: the CRM task and the booking. Not part of
# ``planfix.meta_fields`` -- a Planfix comment linking to its own task is noise, and the
# chat wants them whatever the CRM header is configured to show.
_TELEGRAM_LINKS = (("Planfix", "planfix_task_url"), ("Calendly", "calendly_url"))


def _telegram_extra_lines(document: dict[str, object] | None) -> list[str]:
    """The client's email and links to the Planfix task and Calendly booking.

    Telegram only: the CRM already holds the client, and a comment linking to its
    own task is noise.
    """
    if not document:
        return []
    lines = []
    emails = document.get("client_emails") or []
    if isinstance(emails, list) and emails:
        lines.append("Email клиента: " + ", ".join(str(email) for email in emails))
    for label, field_name in _TELEGRAM_LINKS:
        url = " ".join(str(document.get(field_name) or "").split())
        if url:
            lines.append(f"[{label}]({url})")
    return lines


def _telegram_summary(
    artifacts: dict[str, str],
    preset_names: tuple[str, ...],
    meta_document: dict[str, object] | None = None,
    meta_fields: tuple[str, ...] = (),
    meta_entities: tuple[meta_entity.MetaEntity, ...] = (),
) -> str:
    """Render the same header + preset sections as the Planfix comment, as plain text.

    Returns an empty string when no preset produced anything, matching
    ``_planfix_description``: a header alone (a duration and a link) is not a summary
    worth posting into someone's chat.
    """
    sections = _summary_sections(artifacts, preset_names)
    if not sections:
        return ""
    lines = _planfix_meta_lines(meta_document, meta_fields, meta_entities)
    lines.extend(_telegram_extra_lines(meta_document))
    header = "\n".join(lines)
    blocks = [header] if header else []
    blocks.extend(sections)
    return _to_plain_text("\n\n".join(blocks)).strip()


def _planfix_description(
    artifacts: dict[str, str],
    preset_names: tuple[str, ...],
    meta_document: dict[str, object] | None = None,
    meta_fields: tuple[str, ...] = (),
    meta_entities: tuple[meta_entity.MetaEntity, ...] = (),
) -> str:
    """Concatenate the meta header and the configured preset artifacts into one body.

    The header (subject, tags, referral, ...) is rendered first from ``meta_document``
    and ``meta_fields``, so a manager reading the comment sees what the call was about
    before scrolling into the preset sections. Presets are joined in configured order
    and a preset with no artifact is skipped rather than emitting an empty section.
    The preset's own name is not printed: "keypoints" is how the pipeline spells a
    stage, not something a manager reading a CRM comment needs to see, and it landed
    as a stray English word between the Russian header and the Russian content. The
    header renders only alongside at least one preset section -- a header with no
    sections returns an empty string, which callers rely on to mean "nothing to
    comment".

    The result is HTML, not the Markdown the presets emit: Planfix stores comments as
    HTML and renders ``##`` and ``-`` as literal characters. Conversion happens once,
    on the assembled document, so headings and lists nest the same way they read (and
    the body stays a single line, since Planfix rewrites every newline as ``<br>``).
    """
    sections = [
        _mark_planfix_sections(section)
        for section in _summary_sections(artifacts, preset_names)
    ]
    # The header alone is never enough: a document carrying only a duration and a
    # link but no preset section is not something worth commenting, and
    # `_send_planfix_comment`'s `if not description` guard relies on an empty
    # return here to skip both the POST and the `planfix_comment_task_id` marker.
    if not sections:
        return ""

    header = "\n".join(
        _planfix_meta_lines(meta_document, meta_fields, meta_entities)
    )
    blocks: list[str] = [header] if header else []
    for section in sections:
        if blocks:
            # Where the preset's name used to sit. Doubling with the gap a marked
            # heading brings is harmless -- the converter collapses them.
            blocks.append(planfix_html.SECTION_BREAK)
        blocks.append(section)
    return planfix_html.markdown_to_html("\n\n".join(blocks))


def _send_planfix_comment(
    service: Any,
    item: dict,
    file_id: str,
    config: Config,
    artifacts: dict[str, str],
    booking_decision: booking_gate.BookingDecision,
    meta_document: dict[str, object] | None = None,
) -> None:
    """Post the meeting summary into the matched Planfix task, exactly once.

    `process_item` can legitimately reach its success path more than once per file — a
    later cycle that backfills a newly configured preset re-feeds the transcript — so
    the `planfix_comment_task_id` marker, written only after a successful POST, is what
    keeps a second pass from posting a duplicate comment into the task.

    ``meta_document`` (the merged document Task 4's ``_write_call_documents`` built
    this cycle) opens the comment with a header drawn from ``config.planfix_meta_fields``
    before the preset sections; it defaults to ``None`` so a caller with no document
    still gets the plain preset-only comment.
    """
    if not booking_decision.is_matched:
        return
    if not config.planfix_create_comment_url:
        return
    if item.get("planfix_comment_task_id"):
        logger.debug("Planfix comment already sent for %s, skipping", file_id)
        return

    description = _planfix_description(
        artifacts,
        config.planfix_presets,
        meta_document,
        config.planfix_meta_fields,
        meta_entities=config.meta_entities,
    )
    if not description:
        logger.warning(
            "No configured Planfix preset produced text for %s; nothing to comment",
            file_id,
        )
        return

    sent = planfix.send_comment(
        url=config.planfix_create_comment_url,
        token=config.planfix_token,
        proxy_url=config.proxy_url,
        task_id=booking_decision.task_id,
        description=description,
    )
    if sent:
        drive.set_file_app_properties(
            service,
            file_id,
            {drive.PLANFIX_COMMENT_TASK_ID_PROPERTY: booking_decision.task_id},
        )
        return

    # Unlike the completion webhook, a lost CRM comment is invisible to a human, so it
    # escalates. No marker is written, so `gdstt reprocess` can resend it.
    notify.notify_error(
        f"Failed to create the Planfix comment on task {booking_decision.task_id} "
        f"for {item.get('file', {}).get('name')}; rerun `gdstt reprocess {file_id}`",
        telegram_bot_token=config.telegram_bot_token,
        telegram_chat_id=config.telegram_chat_id,
        proxy_url=config.proxy_url,
    )


def folder_telegram_chats(config: Config, folder_id: str) -> tuple[str, ...]:
    """The chats every summary from a folder goes to, or () when it has none.

    Also the "recognize unconditionally" predicate: a folder with a chat is watched for
    its own sake, so ``run_once`` must not skip its recordings for want of a booking.
    ``telegram_calendly`` is deliberately not part of it -- those chats want booked
    calls only, and a booked call is recognized anyway.
    """
    folder = config.folder_by_id(folder_id)
    return folder.telegram if folder else ()


def _summary_chats(
    config: Config,
    folder_id: str,
    file_id: str,
    booking_decision: booking_gate.BookingDecision,
) -> tuple[str, ...]:
    """Every chat this recording's summary belongs in, each once.

    ``telegram`` gets every call unless ``planfix.ignore_telegram_when_planfix`` hands
    a call the CRM recorded over to the CRM. ``telegram_calendly`` gets booked calls
    and ignores that option: it is a delivery channel of its own, not a fallback.
    """
    folder = config.folder_by_id(folder_id)
    if folder is None:
        return ()
    chats = list(folder.telegram)
    if (
        chats
        and config.planfix_ignore_telegram_when_planfix
        and booking_decision.is_matched
        and config.planfix_create_comment_url
    ):
        logger.debug(
            "Planfix covers %s and ignore_telegram_when_planfix is set; "
            "leaving the folder's telegram chats out",
            file_id,
        )
        chats = []
    if booking_decision.is_booked:
        chats.extend(folder.telegram_calendly)
    return tuple(dict.fromkeys(chats))


def _sent_chats(item: dict) -> list[str]:
    """The chats a summary already reached, read off the recording's marker.

    The marker used to hold a single chat id; that value reads as a one-chat list, so
    recordings delivered before chats became lists are not delivered again.
    """
    raw = str(item.get("telegram_sent_chat_id") or "")
    return [chat for chat in raw.split(",") if chat]


def _send_telegram_summary(
    service: Any,
    item: dict,
    file_id: str,
    folder_id: str,
    config: Config,
    artifacts: dict[str, str],
    booking_decision: booking_gate.BookingDecision,
    meta_document: dict[str, object] | None = None,
) -> None:
    """Post the meeting summary into each of the folder's Telegram chats, once per chat.

    Which chats is ``_summary_chats``' call. Like the Planfix marker,
    ``telegram_sent_chat_id`` records a chat only after its send succeeded, so a later
    cycle backfilling a newly configured preset does not re-post the summary, and a
    send that failed for one chat is retried for that chat alone.
    """
    name = item.get("file", {}).get("name")
    chats = _summary_chats(config, folder_id, file_id, booking_decision)
    if not chats:
        logger.debug("Folder %s has no Telegram chat for %s; nothing to send", folder_id, name)
        return
    sent = _sent_chats(item)
    pending = [chat for chat in chats if chat not in sent]
    if not pending:
        logger.debug("Telegram summary already sent for %s, skipping", file_id)
        return

    text = _telegram_summary(
        artifacts,
        config.planfix_presets,
        meta_document,
        config.planfix_meta_fields,
        meta_entities=config.meta_entities,
    )
    if not text:
        logger.warning(
            "No configured preset produced text for %s; nothing to send to Telegram",
            file_id,
        )
        return

    for chat_id in pending:
        # Both ends of the send are logged, because only the failures used to be. A
        # summary that arrived left no trace at all, so a quiet log could not be told
        # apart from a delivery that never happened -- and the first thing anybody does
        # when a chat stays empty is read the log.
        logger.info("Sending the Telegram summary for %s to chat %s", name, chat_id)
        delivered = notify.send_message(
            text,
            bot_token=config.telegram_bot_token,
            chat_id=chat_id,
            proxy_url=config.proxy_url,
        )
        if not delivered:
            # No ``notify_error`` here: the error channel is the same Telegram API that
            # just failed, so the escalation would most likely be lost too. The chat is
            # left off the marker, so `gdstt reprocess` can resend it.
            logger.warning(
                "Failed to send the Telegram summary for %s to %s; rerun "
                "`gdstt reprocess %s`",
                name, chat_id, file_id,
            )
            continue
        # Written after each chat rather than once at the end: a later chat failing,
        # or the process dying, must not cost the record of the ones that arrived.
        sent.append(chat_id)
        drive.set_file_app_properties(
            service,
            file_id,
            {drive.TELEGRAM_SENT_CHAT_ID_PROPERTY: ",".join(sent)},
        )
        logger.info("Telegram summary for %s delivered to chat %s", name, chat_id)


def process_item(
    service: Any,
    item: dict,
    folder_id: str,
    config: Config,
    *,
    reprocess_txt: bool = False,
    reprocess_presets: list[str] | None = None,
    booking_decision: booking_gate.BookingDecision | None = None,
) -> _ProcessTelemetry | None:
    file_info = item["file"]
    file_id = file_info["id"]
    file_name = file_info["name"]
    file_size = _coerce_size_bytes(file_info.get("size"))
    has_mp3 = item.get("has_mp3", False)
    has_txt = item.get("has_txt", False)
    # Artifacts belong beside the video, which with a subfolder per meeting is no
    # longer the configured folder. Falling back to ``folder_id`` keeps a caller that
    # built an item by hand working, and is exactly right for a flat folder.
    container_id = item.get("container_id") or folder_id

    stt_enabled = bool(config.stt_provider)
    preset_only_reprocess = reprocess_presets is not None and not reprocess_txt
    needs_mp3 = (
        not preset_only_reprocess
        and _should_make_mp3_artifact(config)
        and not has_mp3
    )
    needs_txt = stt_enabled and (
        reprocess_txt or (not has_txt and not preset_only_reprocess)
    )
    needs_presets = _needs_preset_reprocess(item, config, needs_txt=needs_txt)
    # `gdstt reprocess <stages>` force-reruns explicit presets from an existing
    # transcript even when their artifacts already exist.
    if reprocess_presets and not needs_txt and _has_existing_transcript(item):
        needs_presets = True

    if not needs_mp3 and not needs_txt and not needs_presets:
        return

    # `run_once` resolves this itself so it can gate and count; the manual commands do
    # not, and get a decision here purely so a matched call still reaches Planfix.
    if booking_decision is None:
        booking_decision = booking_gate.resolve(
            file_info, folder_id, config, meeting_start=item.get("meeting_start")
        )

    provider = _processing_provider(config, needs_txt=needs_txt)
    processing_mode = _processing_mode(needs_mp3=needs_mp3, needs_txt=needs_txt)
    retry_state = _RetryState()
    started_at = time.monotonic()
    error: Exception | None = None

    logger.info(
        "Processing %s (id=%s) in folder %s [mp3=%s, txt=%s]",
        file_name, file_id, folder_id, "make" if needs_mp3 else "skip",
        "make" if needs_txt else "skip",
    )

    duration_s = 0.0
    cost_usd: dict[str, float | None] = {}
    usage: dict[str, dict[str, int]] = {}
    mp3_uploaded = False
    txt_uploaded = False
    transcript = ""
    artifacts: dict[str, str] = {}
    unproduced: set[str] = set()
    meta_document: dict[str, object] | None = None

    try:
        with tempfile.TemporaryDirectory(prefix="gd-stt-") as tmp:
            tmp_dir = Path(tmp)
            mp4_path: Path | None = None
            mp3_path: Path | None = None

            if needs_mp3:
                mp4_path = _call_with_transient_retries(
                    lambda: drive.download(
                        service,
                        file_id,
                        tmp_dir,
                        file_name,
                        expected_size_bytes=file_size,
                    ),
                    description=f"download source file {file_name} ({file_id})",
                    retry_state=retry_state,
                )
                mp3_path = extract_mp3(mp4_path, bitrate=config.bitrate)
                mp3_drive_name = drive.drive_stem(file_name) + ".mp3"
                drive.upload(
                    service,
                    mp3_path,
                    container_id,
                    mime_type=drive.MP3_MIME,
                    name=mp3_drive_name,
                    app_properties={
                        drive.SOURCE_VIDEO_ID_PROPERTY: file_id,
                        drive.ARTIFACT_TYPE_PROPERTY: "mp3",
                    },
                )
                mp3_uploaded = True
                logger.info("Uploaded %s to folder %s", mp3_drive_name, container_id)

            if needs_txt:
                if mp4_path is None:
                    mp4_path = _call_with_transient_retries(
                        lambda: drive.download(
                            service,
                            file_id,
                            tmp_dir,
                            file_name,
                            expected_size_bytes=file_size,
                        ),
                        description=f"download source file {file_name} ({file_id})",
                        retry_state=retry_state,
                    )
                stt_audio_path = _prepare_deepgram_audio(mp4_path, config)
                text = transcribe_file(stt_audio_path, config, cost_usd=cost_usd)
                speaker_names = _speaker_names_from_file_info(file_info)
                # Who the presets are told was on the call. The same names as the
                # transcript's labels, except when Meet named people nobody could place
                # on a speaker: presets take them "in no particular order", so they are
                # still worth knowing.
                participant_names = speaker_names
                if config.stt_postprocess:
                    if speaker_names is None and config.openai_api_key:
                        # Meet's own transcript knows the participants even when the
                        # recording's name does not, and knows them in full when the
                        # name only has a first name from the calendar invite. Without
                        # a model nothing could use it, so it is not read.
                        meet = _read_meet_transcript(service, container_id, file_name)
                        # Who could possibly be a speaker. The API's answer wins when
                        # there is one: it comes from the accounts that joined, while
                        # the document's comes from parsing prose, and "who spoke"
                        # beats "who was there" because a silent participant cannot be
                        # a diarized voice. The document is still read -- its turns are
                        # the evidence the model weighs, whoever named the candidates.
                        known = _speaker_candidates(item, meet[0] if meet else None)
                        # An answer the model would not stand behind leaves the
                        # speakers numbered (``[]``). No name is ever bound to a
                        # speaker by order once a model could be asked: neither Meet's
                        # order nor the file name's is diarization's, and on a real call
                        # Meet's swapped the labels.
                        speaker_names = _resolve_speaker_names(
                            text, file_name, folder_id, config, usage=usage,
                            candidates=known,
                            meet_text=meet[1] if meet else "",
                        )
                        participant_names = speaker_names or known
                    text = postprocess.postprocess_transcript(
                        text,
                        file_name,
                        speaker_names=speaker_names,
                    )
                _save_and_upload_txt(
                    service, file_id, file_name, text, container_id, tmp_dir, config,
                    txt_id=item.get("txt_id"),
                )
                txt_uploaded = True
                transcript = text

                artifacts = _run_preset_stage(
                    service,
                    file_id,
                    file_name,
                    text,
                    folder_id,
                    container_id,
                    tmp_dir,
                    config,
                    speaker_names=participant_names,
                    participants=item.get("participants"),
                    speakers=item.get("speakers"),
                    artifact_ids=item.get("artifact_ids") or {},
                    reprocess=reprocess_txt,
                    usage=usage,
                    unproduced=unproduced,
                    local_artifact_paths=_local_artifact_paths(item),
                    only_presets=reprocess_presets,
                )
                meta_document = _try_write_call_documents(
                    service, file_id, file_name, folder_id, container_id, text, artifacts,
                    config, tmp_dir, item=item, booking_decision=booking_decision,
                )
            elif needs_presets:
                # The transcript already exists on Drive; re-feed it to produce the
                # still-missing presets (a failed earlier preset or a newly added
                # one) without re-running STT.
                if item.get("txt_id") is not None:
                    text = _call_with_transient_retries(
                        lambda: drive.download_text(service, item["txt_id"]),
                        description=f"download transcript for {file_name} ({file_id})",
                        retry_state=retry_state,
                    )
                else:
                    text = Path(item["local_txt_path"]).read_text(encoding="utf-8")
                transcript = text
                speaker_names = _speaker_names_from_file_info(file_info)
                artifacts = _run_preset_stage(
                    service,
                    file_id,
                    file_name,
                    text,
                    folder_id,
                    container_id,
                    tmp_dir,
                    config,
                    speaker_names=speaker_names,
                    participants=item.get("participants"),
                    speakers=item.get("speakers"),
                    artifact_ids=item.get("artifact_ids") or {},
                    reprocess=False,
                    usage=usage,
                    unproduced=unproduced,
                    local_artifact_paths=_local_artifact_paths(item),
                    only_presets=reprocess_presets,
                )
                meta_document = _try_write_call_documents(
                    service, file_id, file_name, folder_id, container_id, text, artifacts,
                    config, tmp_dir, item=item, booking_decision=booking_decision,
                )
    except Exception as exc:
        error = exc
        setattr(exc, "gdstt_retry_count", retry_state.retry_count)
        raise
    finally:
        duration_s = time.monotonic() - started_at
        logger.info(
            "Process summary [file=%s, file_id=%s, folder=%s, provider=%s, processing_mode=%s, "
            "outcome=%s, retry_count=%d, duration_s=%.3f, cost_usd=%s, usage=%s]",
            file_name,
            file_id,
            folder_id,
            provider,
            processing_mode,
            _processing_outcome(error),
            retry_state.retry_count,
            duration_s,
            cost_usd,
            usage,
        )

    # Success path only, after every artifact is written. An mp3-only pass produces
    # nothing a receiver can use, so it stays silent rather than POSTing blanks over
    # a good record. Fire-and-forget: the whole block is guarded because a file that
    # transcribed and uploaded must count as processed even if the payload or the
    # receiver misbehaves.
    #
    # A preset that returned blank wrote no artifact, so this file is still pending and
    # comes back next cycle. A file may notify more than once (a later cycle can add a
    # newly configured preset), but that re-delivery is bounded — a blank preset never
    # settles, so firing here would re-POST the transcript on *every* cycle forever.
    # Staying silent keeps re-delivery bounded; the receiver gets no retry either way.
    if unproduced:
        logger.warning(
            "Completion webhook withheld for %s: presets produced no artifact (%s)",
            file_name,
            ", ".join(sorted(unproduced)),
        )
    elif txt_uploaded or artifacts:
        try:
            webhook.notify_complete(
                url=config.webhook_url,
                token=config.webhook_token,
                proxy_url=config.proxy_url,
                payload=_webhook_payload(
                    file_id, file_name, folder_id, config, transcript, artifacts
                ),
            )
        except Exception as exc:
            logger.warning("Completion webhook failed: %s", type(exc).__name__)

        try:
            _send_planfix_comment(
                service, item, file_id, config, artifacts, booking_decision,
                meta_document=meta_document,
            )
        except Exception as exc:
            # A file that transcribed and uploaded must count as processed even if the
            # CRM hand-off misbehaves.
            logger.warning("Planfix comment failed: %s", type(exc).__name__)

        try:
            _send_telegram_summary(
                service, item, file_id, folder_id, config, artifacts,
                booking_decision, meta_document=meta_document,
            )
        except Exception as exc:
            logger.warning("Telegram summary failed: %s", type(exc).__name__)

    return _ProcessTelemetry(
        provider=provider,
        processing_mode=processing_mode,
        retry_count=retry_state.retry_count,
        duration_s=duration_s,
        mp3_uploaded=mp3_uploaded,
        txt_uploaded=txt_uploaded,
        cost_usd=cost_usd,
        usage=usage,
        transcript=transcript,
        artifacts=artifacts,
        meta_document=meta_document,
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_still_settling(item: dict, now: datetime) -> bool:
    """True while Drive looks like it has not finished processing this upload.

    Meet's recording lands in Drive well after the meeting folder does -- around an
    hour for an hour-long call -- and `videoMediaMetadata` is filled once Drive has
    processed it. Skipping a video that has no metadata yet costs one cycle;
    downloading one costs a transfer and an STT run that may have to be redone.

    The grace window is the important half. A video that never gets metadata still
    has to be transcribed, and waiting on it indefinitely would lose the recording
    quietly -- the exact failure this whole change exists to remove. So the wait is
    bounded, and anything without a readable age is processed rather than held.
    """
    if item.get("has_media_metadata", True):
        return False
    created_raw = item.get("file", {}).get("createdTime")
    if not created_raw:
        return False
    try:
        created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
    except ValueError:
        logger.info("Unreadable createdTime %r; not waiting on it", created_raw)
        return False
    return now - created < _MEDIA_SETTLING_GRACE


def _recording_datetime(item: dict) -> datetime | None:
    """When the call happened, as well as it can be known.

    The name first: Meet writes the meeting time into it, and that is what an
    operator means by "calls from the 12th". Drive's ``createdTime`` is the fallback
    rather than the source because it answers a different question -- when this file
    appeared -- and the two come apart in both small ways and large. Measured across
    eight real recordings, Meet's own lag ran 0-2 hours, enough to push a late call
    past midnight into the next day. Copying or re-uploading a recording resets
    ``createdTime`` outright: the examples this was built against were three days
    adrift for exactly that reason.

    ``None`` when neither is readable, which the caller treats as in scope. Dropping
    a recording nobody can date would be a silent loss, and silent loss is the
    failure this whole area exists to remove.
    """
    known = item.get("meeting_start")
    if known is not None:
        # Meet said so. The name is a rendering of the same moment, and a worse one:
        # a call started outside the calendar is named after the meeting room.
        return known
    file_info = item.get("file", {})
    meeting = parse_meeting_start(file_info.get("name", ""))
    if meeting is not None:
        return meeting
    created_raw = file_info.get("createdTime")
    if not created_raw:
        return None
    try:
        return datetime.fromisoformat(str(created_raw).replace("Z", "+00:00"))
    except ValueError:
        logger.info("Unreadable createdTime %r; treating it as in scope", created_raw)
        return None


def _items_in_date_scope(
    items: list[dict], cutoff: datetime | None, *, dry_run: bool
) -> tuple[list[dict], int]:
    """Split off recordings of calls older than ``cutoff``.

    Applied before everything else in the cycle, and deliberately so: an old
    recording that Drive never finished processing would otherwise be counted as
    deferred, and deferred holds the changes cursor -- the backlog an operator asked
    to ignore would freeze the feed instead.

    Counted per folder rather than logged per file: a folder with a year of history
    would print its whole backlog every ten minutes. ``--dry-run`` names them, which
    is where an operator goes to see what a cutoff will actually do.
    """
    if cutoff is None:
        return items, 0
    kept: list[dict] = []
    skipped = 0
    for item in items:
        when = _recording_datetime(item)
        if when is not None and when < cutoff:
            skipped += 1
            if dry_run:
                logger.info(
                    "DRY RUN: %s is from %s, before %s; not in scope",
                    item.get("file", {}).get("name"),
                    when.isoformat(),
                    cutoff.isoformat(),
                )
            continue
        kept.append(item)
    return kept, skipped


def _pending_items(items: list[dict], config: Config) -> list[dict]:
    stt_enabled = bool(config.stt_provider)
    now = _utcnow()
    pending = []
    for item in items:
        if _is_still_settling(item, now):
            logger.info(
                "Drive has not finished processing %s yet; leaving it for a later cycle",
                item.get("file", {}).get("name"),
            )
            continue
        needs_txt = stt_enabled and not item.get("has_txt")
        if (
            (_should_make_mp3_artifact(config) and not item.get("has_mp3"))
            or needs_txt
            or _needs_preset_reprocess(item, config, needs_txt=needs_txt)
        ):
            pending.append(item)
    return pending


def _file_size_bytes(item: dict) -> int | None:
    return _coerce_size_bytes(item.get("file", {}).get("size"))


def _format_bytes(value: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    amount = float(value)
    for unit in units:
        if amount < 1000 or unit == units[-1]:
            if unit == "B":
                return f"{value} B"
            return f"{amount:.1f} {unit}"
        amount /= 1000
    return f"{value} B"


def _items_allowed_by_size(
    items: list[dict],
    *,
    max_size_bytes: int | None,
    confirm_large: bool,
) -> list[dict]:
    if max_size_bytes is None or confirm_large:
        return items

    allowed = []
    for item in items:
        file_info = item.get("file", {})
        size = _file_size_bytes(item)
        if size is not None and size > max_size_bytes:
            logger.warning(
                "Skipping %s (id=%s): size %s exceeds --max-size %s; "
                "pass --confirm-large to process it",
                file_info.get("name"),
                file_info.get("id"),
                _format_bytes(size),
                _format_bytes(max_size_bytes),
            )
            continue
        allowed.append(item)
    return allowed


def _dry_run_preset_names(
    item: dict,
    config: Config,
    *,
    needs_txt: bool,
    reprocess_txt: bool,
    reprocess_presets: list[str] | None = None,
) -> list[str]:
    """Preset artifacts a real run would generate for this item.

    Mirrors :func:`_run_preset_stage`: ``--reprocess-txt`` regenerates every
    enabled preset, an explicit ``reprocess_presets`` set force-reruns those stages
    from an existing transcript, a fresh or reprocessable transcript generates the
    presets still missing an artifact, and otherwise no preset work happens.
    """
    if not config.presets:
        return []
    if reprocess_txt:
        return [preset.name for preset in config.presets]
    enabled = {preset.name for preset in config.presets}
    if reprocess_presets and not needs_txt and _has_existing_transcript(item):
        requested = [name for name in reprocess_presets if name in enabled]
        dependencies = preset_pipeline.dependency_names(config.presets, requested)
        existing = _existing_preset_names(item)
        names = set(requested) | (dependencies - existing)
        return [
            name
            for name in preset_pipeline.topological_order(config.presets)
            if name in names
        ]
    if needs_txt or _needs_preset_reprocess(item, config, needs_txt=needs_txt):
        return _missing_preset_names(item, config)
    return []


def _log_dry_run(
    folder_id: str,
    item: dict,
    config: Config,
    *,
    reprocess_txt: bool,
    reprocess_presets: list[str] | None = None,
) -> None:
    file_info = item["file"]
    has_mp3 = item.get("has_mp3", False)
    has_txt = item.get("has_txt", False)
    preset_only_reprocess = reprocess_presets is not None and not reprocess_txt
    needs_mp3 = (
        not preset_only_reprocess
        and _should_make_mp3_artifact(config)
        and not has_mp3
    )
    needs_txt = bool(config.stt_provider) and (
        reprocess_txt or (not has_txt and not preset_only_reprocess)
    )
    preset_names = _dry_run_preset_names(
        item, config, needs_txt=needs_txt, reprocess_txt=reprocess_txt,
        reprocess_presets=reprocess_presets,
    )
    logger.info(
        "DRY RUN: would process %s (id=%s) in folder %s [mp3=%s, txt=%s, presets=%s]",
        file_info["name"],
        file_info["id"],
        folder_id,
        "make" if needs_mp3 else "skip",
        "make" if needs_txt else "skip",
        ",".join(preset_names) if preset_names else "skip",
    )


def _configured_folder_for(service: Any, container_id: str, config: Config) -> str:
    """Which configured folder a container belongs to, falling back to itself.

    `process` and `reprocess` start from an id an operator typed, which may be a
    per-meeting subfolder the configuration has never named. Without this the
    employee, the Planfix routing and the folder's Telegram chat all resolve to
    nothing -- silently, because `folder_by_id` returns None rather than raising.

    The fallback keeps the old behaviour for an id that belongs to no configured
    folder at all: it is still processed, just without an employee, exactly as a
    hand-made folder was before subfolders existed.
    """
    configured = drive.find_configured_ancestor(
        service, container_id, {folder.folder_id for folder in config.folders}
    )
    if configured is None:
        return container_id
    if configured != container_id:
        logger.info(
            "Folder %s belongs to configured folder %s", container_id, configured
        )
    return configured


def process_target(
    service: Any,
    target_id: str,
    config: Config,
    *,
    is_folder: bool | None = None,
    reprocess_txt: bool = False,
    reprocess_presets: list[str] | None = None,
    dry_run: bool = False,
    max_size_bytes: int | None = None,
    confirm_large: bool = False,
) -> list[_ProcessTelemetry]:
    """Process a single Drive file or every pending file in a folder, on demand."""
    meta = _call_with_transient_retries(
        lambda: drive.get_file_metadata(service, target_id),
        description=f"get metadata for {target_id}",
    )
    mime = meta.get("mimeType", "")
    treat_as_folder = is_folder if is_folder is not None else mime == drive.FOLDER_MIME

    if treat_as_folder:
        telemetry: list[_ProcessTelemetry] = []
        items = _call_with_transient_retries(
            lambda: drive.list_folder_tree_state(service, target_id),
            description=f"list folder state for {target_id}",
        )
        configured_id = _configured_folder_for(service, target_id, config)
        _apply_local_output_state(items, config)
        if reprocess_txt:
            pending = items
        elif reprocess_presets:
            pending = [item for item in items if _has_existing_transcript(item)]
        else:
            pending = _pending_items(items, config)
        pending = _items_allowed_by_size(
            pending,
            max_size_bytes=max_size_bytes,
            confirm_large=confirm_large,
        )
        logger.info("Folder %s: %d pending file(s)", target_id, len(pending))
        if dry_run:
            for item in pending:
                _log_dry_run(
                    configured_id, item, config,
                    reprocess_txt=reprocess_txt,
                    reprocess_presets=reprocess_presets,
                )
            return telemetry
        for item in pending:
            result = process_item(
                service,
                item,
                configured_id,
                config,
                reprocess_txt=reprocess_txt,
                reprocess_presets=reprocess_presets,
            )
            if result is not None:
                telemetry.append(result)
        return telemetry

    parents = meta.get("parents") or []
    if not parents:
        raise RuntimeError(f"File {target_id} has no parent folder")
    container_id = parents[0]
    folder_id = _configured_folder_for(service, container_id, config)
    items = _call_with_transient_retries(
        lambda: drive.list_folder_state(service, container_id),
        description=f"list folder state for {container_id}",
    )
    _apply_local_output_state(items, config)
    match = next(
        (it for it in items if it["file"]["id"] == target_id), None
    )
    if match is None:
        raise RuntimeError(
            f"File {target_id} is not an MP4 in folder {container_id}"
        )
    allowed = _items_allowed_by_size(
        [match],
        max_size_bytes=max_size_bytes,
        confirm_large=confirm_large,
    )
    if not allowed:
        return []
    if dry_run:
        _log_dry_run(
            folder_id, match, config,
            reprocess_txt=reprocess_txt,
            reprocess_presets=reprocess_presets,
        )
        return []
    result = process_item(
        service, match, folder_id, config,
        reprocess_txt=reprocess_txt,
        reprocess_presets=reprocess_presets,
    )
    return [result] if result is not None else []


def _mark_not_worth_transcribing(
    service: Any, item: dict, reason: str, config: Config
) -> bool:
    """Leave the decision beside the recording, where the folder can explain itself.

    A hidden property would answer the pipeline but not the person who opens the
    meeting folder and wonders why this call has no analysis. The marker is that
    answer, and it is also what stops the next cycle from asking again.

    A failure to write it is a lost cycle, not a lost recording: nothing is marked,
    nothing is transcribed, and the next cycle decides again.
    """
    file_info = item.get("file", {})
    container = item.get("container_id") or ""
    try:
        drive.upload_text(
            service,
            container,
            f"{file_info.get('name', 'recording')}{drive.SKIPPED_SUFFIX}",
            reason,
            app_properties={drive.SOURCE_VIDEO_ID_PROPERTY: file_info.get("id", "")},
        )
    except Exception as exc:  # noqa: BLE001 - reported, and retried next cycle
        logger.exception("Could not mark %s as skipped", file_info.get("name"))
        notify.notify_error(
            f"Could not mark {file_info.get('name')} as skipped: {exc}",
            telegram_bot_token=config.telegram_bot_token,
            telegram_chat_id=config.telegram_chat_id,
            proxy_url=config.proxy_url,
        )
        return False
    logger.info(
        "Nobody came to %s, so it was marked and not transcribed",
        file_info.get("name"),
    )
    return True


@dataclass
class _Discovery:
    """What one cycle found, and what to remember for the next one."""

    listings: list[tuple[str, list[dict]]]
    cursor: str | None
    retries: int = 0
    folder_errors: int = 0
    # Recordings discovery decided not to transcribe, and the sentence saying why.
    # The decision is made where the conference is known; the marker is written in
    # the cycle, where writing to Drive already happens.
    skip_files: dict[str, str] = field(default_factory=dict)
    # Where Meet discovery has finished, when that is the mode. Like the cursor
    # it is only saved by a cycle that drained what it found, and for the same
    # reason: a mark that moved on "I looked" would lose the slow recordings.
    meet_mark: datetime | None = None


def _notify_listing_failure(what: str, exc: Exception, config: Config) -> None:
    """Report a source that could not be read, alerting only when it is not a blip.

    A persistent failure -- refused credentials, a missing folder, a 403 -- alerts at
    once: it will not fix itself. A transient one (a dropped connection, a 5xx that
    outlived the retries) is logged and only alerts once the same source has failed
    `_LISTING_FAILURE_ALERT_STREAK` cycles in a row.
    """
    if what not in _LISTING_FAILED_THIS_CYCLE:
        _LISTING_FAILED_THIS_CYCLE.add(what)
        _LISTING_FAILURE_STREAKS[what] = _LISTING_FAILURE_STREAKS.get(what, 0) + 1
    streak = _LISTING_FAILURE_STREAKS[what]
    if _is_transient_runtime_error(exc) and streak < _LISTING_FAILURE_ALERT_STREAK:
        logger.warning(
            "Failed to list %s (%d cycle(s) in a row, alerting at %d): %s",
            what,
            streak,
            _LISTING_FAILURE_ALERT_STREAK,
            exc,
        )
        return
    logger.exception("Failed to list %s", what)
    in_a_row = f" ({streak} cycles in a row)" if streak > 1 else ""
    notify.notify_error(
        f"Failed to list {what}{in_a_row}: {exc}\n{traceback.format_exc()}",
        telegram_bot_token=config.telegram_bot_token,
        telegram_chat_id=config.telegram_chat_id,
        proxy_url=config.proxy_url,
    )


def _begin_listing_failure_cycle() -> None:
    """Close the previous cycle's failure runs: a source it did not fail on is healed.

    Done at the start of a cycle rather than the end so that a cycle dying halfway
    still counts the failures it did record.
    """
    for what in list(_LISTING_FAILURE_STREAKS):
        if what not in _LISTING_FAILED_THIS_CYCLE:
            del _LISTING_FAILURE_STREAKS[what]
    _LISTING_FAILED_THIS_CYCLE.clear()


def _discover_by_walk(fleet: delegation.Fleet, config: Config) -> _Discovery:
    """Read every configured folder and its meeting subfolders.

    The complete answer, and the expensive one: a request per folder per cycle. It
    runs on the first cycle and whenever the cursor is gone, which is what makes
    losing the cursor a cost rather than a loss.

    The cursor is taken *before* the sweep. Anything that lands while the sweep is
    running then shows up in the next feed read; taking it afterwards would open a
    window whose files no cycle ever looks at again.

    Under delegation no cursor is taken at all: a cursor is a position in one
    account's journal, and here every folder is read as a different account. Walking
    is the whole discovery path until there is a cursor per employee.
    """
    cursor: str | None = None
    retries = 0
    if not config.uses_delegation:
        try:
            cursor = drive.get_start_page_token(fleet.fallback)
        except (RefreshError, AuthError):
            raise
        except Exception:
            # A sweep with no cursor still processes everything; it just has to sweep
            # again next time. Refusing to sweep would be the worse trade.
            logger.exception(
                "Could not take a changes cursor; this cycle will sweep again"
            )

    listings: list[tuple[str, list[dict]]] = []
    folder_errors = 0
    for folder in config.folders:
        folder_id = folder.folder_id
        listing_retry_state = _RetryState()
        folder_service = fleet.service_for(folder_id)
        try:
            items = _call_with_transient_retries(
                lambda: drive.list_folder_tree_state(folder_service, folder_id),
                description=f"list folder state for {folder_id}",
                retry_state=listing_retry_state,
            )
        except (RefreshError, AuthError) as exc:
            # One employee's credentials must not end everybody's cycle, and under
            # delegation that is exactly what re-raising would do. With one shared
            # token there is nothing left to read anyway, so it still ends the cycle.
            if not config.uses_delegation:
                raise
            folder_errors += 1
            _notify_listing_failure(f"folder {folder_id}", exc, config)
            continue
        except Exception as exc:
            folder_errors += 1
            _notify_listing_failure(f"folder {folder_id}", exc, config)
            continue
        finally:
            retries += listing_retry_state.retry_count
        listings.append((folder_id, items))
    return _Discovery(listings, cursor, retries, folder_errors)



def _moment(text: str) -> datetime | None:
    """One of Meet's timestamps as an aware UTC datetime, or ``None``."""
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Meet returned a time I cannot read: %r", text)
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _meet_floor(config: Config, now: datetime) -> datetime:
    """Where to start when there is no mark: a first run, or a wiped data dir.

    The later of the first-look window and ``run.since``, so losing the file costs one
    longer listing rather than a reading of the whole archive. What is in *scope*
    stays ``run.since``'s job alone: if the mark also decided scope, losing it would
    silently mean transcribing everything, which is a bill rather than a bug.
    """
    look_back = now - timedelta(hours=config.meet_first_look_hours)
    since = parse_since(config.run_since or None, source="run.since")
    if since is None:
        return look_back
    return max(look_back, since.astimezone(timezone.utc))


def _meet_work(
    conferences: list[meet_api.MeetConference], config: Config, now: datetime
) -> tuple[list[str], datetime | None]:
    """The Drive files to process, and the earliest moment still unfinished.

    The mark may not move past work that is not done, and Meet names a conference as
    soon as it ends while its recording appears minutes later. So a conference is
    unfinished while it is still running, while a recording of it has no file yet, and
    while it could not be read at all -- and each of those holds the mark at its own
    start time, which is exactly what brings it back next cycle.

    Every one of those holds is bounded by ``meet.wait_hours``. Anything else would
    let a single stuck conference -- a record Meet never closes, a recording whose
    file never arrives, a conference whose recordings are refused for good -- pin the
    mark for ever, and every later cycle would re-read everything since it for the
    whole fleet. That is the growth this mode exists to remove.
    """
    files: list[tuple[str, meet_api.MeetConference]] = []
    held: datetime | None = None
    wait = timedelta(hours=config.meet_wait_hours)

    def hold(conference: meet_api.MeetConference, since: datetime | None, why: str) -> None:
        """Hold the mark at this conference, unless waiting has gone on too long."""
        nonlocal held
        start = _moment(conference.start_time)
        if start is None:
            return
        if since is not None and now - since >= wait:
            logger.warning(
                "Meet has said nothing new about %s for %dh (%s); discovery will "
                "stop waiting for it",
                conference.name,
                config.meet_wait_hours,
                why,
            )
            return
        if held is None or start < held:
            held = start

    for conference in conferences:
        start = _moment(conference.start_time)
        ended = _moment(conference.end_time)
        if conference.unreadable:
            hold(conference, ended or start, "its recordings could not be listed")
            continue
        if not conference.ended:
            # No recordings *yet* -- the call is still going. Letting the mark past
            # its start time would mean never seeing this conference again.
            hold(conference, start, "it has still not ended")
            continue
        for recording in conference.recordings:
            if recording.ready:
                # The conference travels with the file: whether anybody came is asked
                # of the conference, and the file is all the rest of the service knows.
                files.append((recording.file_id, conference))
                continue
            hold(
                conference,
                ended or start,
                f"a recording is still in state {recording.state or 'unknown'}",
            )
    return files, held


def _prompts_want_people(config: Config) -> tuple[bool, bool]:
    """Whether any enabled prompt asks for the call's people, and for who spoke.

    Asked once per cycle rather than per recording, because the answer is the config
    and the cost is not: who spoke means two more requests per recording, and a
    deployment whose prompts never mention it must not pay them.
    """
    wants_people = False
    wants_speakers = False
    # `config.presets` is a tuple of presets, and elsewhere a mapping of them: both
    # shapes reach this code, and an empty one of either is falsy -- which is how a
    # version of this that only handled mappings passed every test and then failed on
    # the first real config.
    configured = config.presets or ()
    for preset in (configured.values() if hasattr(configured, "values") else configured):
        if not getattr(preset, "enabled", True):
            continue
        text = getattr(preset, "instructions", "") or ""
        wants_people = wants_people or presets_module.wants_participants(text)
        wants_speakers = wants_speakers or presets_module.wants_speakers(text)
    return wants_people, wants_speakers


def _call_people(
    service, conference: meet_api.MeetConference, *, with_speech: bool
) -> meet_api.Attendance | None:
    """Who was in this call, or ``None`` when the answer could not be had.

    ``None`` is not an empty room. Every caller below treats the two differently, and
    the one that decides whether to transcribe must never confuse them.
    """
    try:
        return meet_api.attendance(service, conference.name, include_speech=with_speech)
    except (meet_api.MeetError, HttpError) as exc:
        logger.warning(
            "Could not read who was in %s, so it is treated as unknown: %s",
            conference.name,
            exc,
        )
        return None


def _nobody_came(attendance: meet_api.Attendance | None, conference: str) -> str:
    """Why this call is not worth transcribing, or an empty string.

    A manager who waited alone for a client who never arrived produces a recording of
    silence, and today it costs a download, a Deepgram bill and three OpenAI calls.

    Two people in the call at the same time -- for any length of time at all -- means
    they came. There is no duration threshold on purpose: the measured real calls
    overlapped for as little as fifteen seconds, so a threshold would quietly discard
    real conversations to save a few cents.
    """
    if attendance is None:
        return ""
    if not attendance.people:
        # A recording exists, so somebody was in the call. An empty list is the API
        # declining to say who, not an empty room, and acting on it would skip a real
        # conversation.
        logger.warning(
            "Meet reported no participants at all for %s; processing it as usual",
            conference,
        )
        return ""
    if meet_api.ever_together(attendance):
        return ""
    who = ", ".join(
        person.display_name or f"a {person.kind} participant"
        for person in attendance.people
    )
    return (
        "Nobody but the organiser was in this call, so it was not transcribed.\n"
        f"Meet reported {len(attendance.people)} participant(s): {who or 'nobody'}.\n"
        "Delete this file and run `gdstt reprocess <file-id>` to transcribe it anyway."
    )


def _meet_placements(
    fleet: delegation.Fleet,
    config: Config,
    folder_id: str,
    file_ids: list[str],
    seen: set[str],
) -> tuple[dict[str, set[str]], int, set[str]]:
    """Which folders hold this employee's new recordings, and whose work each is.

    A call between two watched employees is listed by both of their accounts, and Meet
    gives both the same Drive file -- the organiser's. Processing it twice would
    transcribe one conversation twice and race two uploads into the same folder, so a
    file already placed this cycle is skipped, and a file owned by another watched
    employee is placed with its owner: that is the account whose Drive holds it, and
    whose artifacts should sit beside it.

    A recording owned by nobody in the fleet is left alone and counted. Meet lists the
    conferences an employee attended as well as the ones they hosted (measured), so
    these arrive routinely -- and the walk never touches one: it reaches the employee
    only as a shortcut, and no path follows shortcuts. Processing it would mean
    writing artifacts into the Drive of somebody this service was never given.
    Discovery finding more than the walk is not a feature here; it is the difference
    the acceptance test exists to catch.

    ``seen`` is read, never written: this runs inside the transient-retry wrapper, and
    a function that marked files as placed and then raised would lose them on the
    retry that follows. The files it did place come back, and the caller records them
    once the call as a whole succeeded.
    """
    owners = {
        folder.email.lower(): folder.folder_id
        for folder in config.folders
        if folder.email
    }
    service = fleet.service_for(folder_id)
    containers: dict[str, set[str]] = {}
    placed: set[str] = set()
    outside = 0
    for file_id in file_ids:
        if file_id in seen or file_id in placed:
            continue
        try:
            parents, owner = drive.file_placement(service, file_id)
        except HttpError as exc:
            if exc.resp.status not in (403, 404):
                raise
            # Refused outright: somebody else's recording of a call this employee was
            # merely in, and not shared with them. Counting it as a failure would hold
            # the mark at that conference every cycle, for work nobody here will ever
            # do. It is deliberately not recorded as placed: another employee may be
            # the one who owns it, and they can open it.
            outside += 1
            continue
        placed.add(file_id)
        target = owners.get(owner.lower())
        if target is None:
            outside += 1
            continue
        for container in parents:
            containers.setdefault(target, set()).add(container)
    return containers, outside, placed


def _meet_listing(fleet: delegation.Fleet, folder_id: str, containers: set[str]) -> list[dict]:
    """The state of every meeting folder Meet says holds a new recording.

    Meet answers with a file, and everything downstream is keyed on the folder that
    holds it -- so that folder is listed exactly as the walk lists it, and the same
    artifacts beside the video decide what still needs doing. This is the only place
    the two paths differ: which folders get listed, never what listing one means.
    """
    service = fleet.service_for(folder_id)
    items: list[dict] = []
    for container in sorted(containers):
        items.extend(drive.list_folder_state(service, container))
    return items


def _attach_call_facts(
    listings: list[tuple[str, list[dict]]],
    people_by_file: dict[str, meet_api.Attendance],
    conference_by_file: dict[str, meet_api.MeetConference],
) -> None:
    """Hand each recording what Meet knows about the call it came from.

    Optional data on an otherwise unchanged item: the walk produces the same items
    without it, and everything that does not ask for it behaves as it always did.
    ``speakers`` stays absent unless the transcript was actually read, because an
    empty list of speakers and an unread transcript would render the same and mean
    opposite things.
    """
    for _, items in listings:
        for item in items:
            file_id = item.get("file", {}).get("id", "")
            conference = conference_by_file.get(file_id)
            if conference is not None:
                started = _moment(conference.start_time)
                if started is not None:
                    item["meeting_start"] = started
            attendance = people_by_file.get(file_id)
            if attendance is None:
                continue
            item["participants"] = [
                person.display_name for person in attendance.people if person.display_name
            ]
            if attendance.speech_known:
                item["speakers"] = [
                    person.display_name
                    for person in attendance.people
                    if person.spoke and person.display_name
                ]


def _discover_by_meet(fleet: delegation.Fleet, config: Config) -> _Discovery:
    """Ask each employee's Meet what they have been in, instead of reading Drive.

    One request answers "anything new?" however large the archive, which is the whole
    point: the walk costs a request per meeting folder per cycle and grows with every
    call ever held.

    An employee whose Meet query fails is a counted folder error, and their folders
    are walked this cycle when ``meet.fallback`` says so. Either way the mark stays
    where it is, because a mark that advanced on everybody else's work would put that
    employee's conferences behind it for good.
    """
    if not config.uses_delegation:
        raise SystemExit(
            "run.discovery: meet needs a service account: the Meet API answers only "
            "for the account that asks. Use `--mode walk`."
        )
    now = datetime.now(timezone.utc)
    mark = meet_mark.read(meet_mark.path_for(config.data_dir))
    if mark is None:
        mark = _meet_floor(config, now)
        logger.info("No Meet mark saved; looking back to %s", mark.isoformat())

    listings: list[tuple[str, list[dict]]] = []
    folder_errors = 0
    retries = 0
    held: datetime | None = None
    fall_back_to_walk: list[EmployeeFolder] = []
    # Which folders each employee's work lives in, and every recording already placed
    # this cycle. Both are fleet-wide: one conference between two employees must
    # become one piece of work, not two.
    wanted: dict[str, set[str]] = {}
    seen_files: set[str] = set()
    asked: list[str] = []
    skip_files: dict[str, str] = {}
    people_by_file: dict[str, meet_api.Attendance] = {}
    conference_by_file: dict[str, meet_api.MeetConference] = {}
    wants_people, wants_speakers = _prompts_want_people(config)
    # Naming the diarized speakers is the other reason to ask who spoke, and the better
    # one: it hands the model the people who actually talked instead of every name a
    # document happened to mention.
    if config.stt_postprocess and config.openai_api_key:
        wants_people = wants_speakers = True

    def hold_at(moment: datetime | None) -> None:
        nonlocal held
        if moment is not None and (held is None or moment < held):
            held = moment

    for folder in config.folders:
        folder_id = folder.folder_id
        if not folder.email:
            # A folder pinned by id with nobody attached: there is no account to ask,
            # so it is read the way it always was.
            fall_back_to_walk.append(folder)
            continue
        try:
            service = build_meet_service(config=config, subject=folder.email)
            conferences = meet_api.conferences_since(service, mark)
        except (AuthError, meet_api.MeetError, HttpError) as exc:
            folder_errors += 1
            hold_at(mark)
            _notify_listing_failure(f"the conferences of {folder.email}", exc, config)
            if config.meet_fallback == "walk":
                fall_back_to_walk.append(folder)
            continue
        files, unfinished = _meet_work(conferences, config, now)
        hold_at(unfinished)
        for file_id, conference in files:
            conference_by_file[file_id] = conference
        if wants_people or config.meet_skip_empty_calls:
            for file_id, conference in files:
                attendance = _call_people(service, conference, with_speech=wants_speakers)
                if attendance is not None:
                    people_by_file[file_id] = attendance
                if config.meet_skip_empty_calls:
                    reason = _nobody_came(attendance, conference.name)
                    if reason:
                        skip_files[file_id] = reason
        files = [file_id for file_id, _ in files]
        placement_retry_state = _RetryState()
        try:
            placed, outside, newly_placed = _call_with_transient_retries(
                lambda: _meet_placements(fleet, config, folder_id, files, seen_files),
                description=f"find the folders Meet named for {folder_id}",
                retry_state=placement_retry_state,
            )
        except Exception as exc:  # noqa: BLE001 - counted and reported, never swallowed
            folder_errors += 1
            # The conferences were read but their files were not placed, so this
            # employee has unfinished work whatever the conferences said.
            hold_at(mark)
            _notify_listing_failure(f"the recordings of {folder.email}", exc, config)
            continue
        finally:
            retries += placement_retry_state.retry_count
        seen_files |= newly_placed
        for target, containers in placed.items():
            wanted.setdefault(target, set()).update(containers)
        logger.info(
            "Meet named %d conference(s) for %s [files=%d, folders=%d, outside=%d]",
            len(conferences),
            folder_id,
            len(files),
            sum(len(ids) for ids in placed.values()),
            outside,
        )
        if outside:
            logger.info(
                "%d recording(s) Meet named for %s belong to someone outside the "
                "configured employees; they are left to their owner, exactly as the "
                "walk leaves them",
                outside,
                folder.email,
            )
        asked.append(folder_id)

    for folder_id in asked:
        containers = wanted.pop(folder_id, set())
        listing_retry_state = _RetryState()
        try:
            items = _call_with_transient_retries(
                lambda: _meet_listing(fleet, folder_id, containers),
                description=f"list the folders Meet named for {folder_id}",
                retry_state=listing_retry_state,
            )
        except Exception as exc:  # noqa: BLE001 - counted and reported, never swallowed
            folder_errors += 1
            hold_at(mark)
            _notify_listing_failure(f"folder {folder_id}", exc, config)
            continue
        finally:
            retries += listing_retry_state.retry_count
        listings.append((folder_id, items))

    # A recording owned by somebody nobody asked for -- an employee configured but
    # not reached this cycle. Their folder is still listed, because the work is real
    # and the file is theirs. Unless they are about to be walked: the walk reads that
    # whole folder, and listing it here as well would hand the cycle the same
    # recording twice.
    walking = {folder.folder_id for folder in fall_back_to_walk}
    for folder_id, containers in wanted.items():
        if folder_id in walking:
            continue
        try:
            listings.append((folder_id, _meet_listing(fleet, folder_id, containers)))
        except Exception as exc:  # noqa: BLE001 - counted and reported, never swallowed
            folder_errors += 1
            hold_at(mark)
            _notify_listing_failure(f"folder {folder_id}", exc, config)

    if fall_back_to_walk:
        walked = _discover_by_walk(
            fleet, replace(config, folders=tuple(fall_back_to_walk))
        )
        listings.extend(walked.listings)
        folder_errors += walked.folder_errors
        retries += walked.retries

    _attach_call_facts(listings, people_by_file, conference_by_file)
    return _Discovery(
        listings,
        cursor=None,
        retries=retries,
        folder_errors=folder_errors,
        skip_files=skip_files,
        meet_mark=held or now,
    )

def _discover_by_changes(service: Any, config: Config, cursor: str) -> _Discovery | None:
    """Read Drive's own journal and look only where something happened.

    One request answers "has anything changed", however many folders are watched and
    however many meeting subfolders have piled up in them. Only the folders the
    journal names are then listed, and the listing -- not the journal -- still decides
    what needs doing, so every existing rule about siblings, markers and reprocessing
    keeps working untouched.

    Returns ``None`` when the cursor is no longer usable, which is the caller's signal
    to sweep and take a fresh one.
    """
    retry_state = _RetryState()
    try:
        entries, new_cursor = _call_with_transient_retries(
            lambda: drive.list_changes(service, cursor),
            description="read the changes feed",
            retry_state=retry_state,
        )
    except (RefreshError, AuthError):
        raise
    except Exception as exc:
        if _is_rejected_cursor(exc):
            logger.info("The changes cursor is no longer valid; sweeping instead")
            return None
        _notify_listing_failure("the changes feed", exc, config)
        return _Discovery([], cursor, retry_state.retry_count, folder_errors=1)

    configured_ids = {folder.folder_id for folder in config.folders}
    ancestors: dict[str, str | None] = {}
    containers: dict[str, str] = {}
    unresolved = 0
    for entry in entries:
        if entry.get("removed"):
            continue
        file_info = entry.get("file") or {}
        if file_info.get("trashed"):
            continue
        # Our own uploads come through here too. Judging by the entry alone is what
        # keeps the feed to a single request: no files.get to find out what something
        # is.
        if file_info.get("mimeType") != drive.MP4_MIME:
            continue
        parents = file_info.get("parents") or []
        if not parents:
            continue
        container_id = parents[0]
        if container_id in containers:
            continue
        try:
            owner = drive.find_configured_ancestor(
                service, container_id, configured_ids, cache=ancestors
            )
        except (RefreshError, AuthError):
            raise
        except Exception as exc:
            # Not knowing whose folder this is must not read as "nobody's". Counting
            # it holds the cursor, so the same change is read again next cycle.
            unresolved += 1
            _notify_listing_failure(f"the folder above {container_id}", exc, config)
            continue
        if owner is None:
            # The account can see folders nobody configured, and the feed reports
            # those too.
            continue
        containers[container_id] = owner

    by_owner: dict[str, list[dict]] = {}
    folder_errors = 0
    for container_id, owner in containers.items():
        listing_retry_state = _RetryState()
        try:
            items = _call_with_transient_retries(
                lambda: drive.list_folder_state(service, container_id),
                description=f"list folder state for {container_id}",
                retry_state=listing_retry_state,
            )
        except (RefreshError, AuthError):
            raise
        except Exception as exc:
            folder_errors += 1
            _notify_listing_failure(f"folder {container_id}", exc, config)
            continue
        finally:
            retry_state.retry_count += listing_retry_state.retry_count
        # Each item already carries its own `container_id`, so merging them under
        # the configured folder loses nothing about where the files live.
        by_owner.setdefault(owner, []).extend(items)

    listings = list(by_owner.items())
    logger.info(
        "Changes feed [entries=%d, meeting_folders=%d, folders=%d]",
        len(entries),
        len(containers),
        len(listings),
    )
    return _Discovery(
        listings, new_cursor, retry_state.retry_count, folder_errors + unresolved
    )


def _cursor_covers_config(config: Config, *, mode: str) -> bool:
    """Whether the saved cursor can vouch for the folders now being watched.

    A cursor means "nothing has happened since" only for folders that were already
    in the config when it was taken. A folder added afterwards -- which is how an
    employee gets onboarded, not some one-off migration -- brings recordings that
    were never a change after that cursor, so the feed will never name it and its
    backlog would stay invisible until someone reset the cursor by hand. One sweep
    is the whole cost of noticing.

    ``changes`` mode refuses instead of sweeping -- that is its contract, and it is
    the only safe answer here. Reading the feed anyway would let the cycle drain and
    record the new folder set as vouched for without it ever having been swept, so
    the backlog would be invisible from then on.
    """
    watched = change_cursor.fingerprint(
        folder.folder_id for folder in config.folders
    )
    vouched = change_cursor.read_folders(
        change_cursor.folders_path_for(config.data_dir)
    )
    if vouched == watched:
        return True
    if mode == "changes":
        raise SystemExit(
            "The watched folders changed since the cursor was taken; the feed cannot "
            "report recordings that were already in a folder added since. Run a "
            "normal cycle or `gdstt run-once --mode walk` first."
        )
    logger.info(
        "The watched folders changed since the cursor was taken; sweeping once so a "
        "newly added folder's existing recordings are not missed"
    )
    return False


def _discover(
    fleet: delegation.Fleet, config: Config, *, mode: str = "auto"
) -> _Discovery:
    """Take the cheap path when a cursor says where to resume, the full one otherwise.

    ``walk`` forces the sweep and leaves the cursor where it is, which is what makes
    it a safe "check everything now" for an operator: the feed picks up afterwards
    exactly where it was, and anything the sweep already handled is simply found
    done. ``changes`` refuses to fall back, so it can answer whether the feed itself
    works without waiting for a cycle.

    Delegation always walks: the journal belongs to one account, and each folder here
    is read as a different one. The config refuses ``discovery: auto`` alongside a
    service account, so this is the last line of that defence rather than a silent
    downgrade.
    """
    if mode == "meet":
        return _discover_by_meet(fleet, config)
    if config.uses_delegation:
        return replace(_discover_by_walk(fleet, config), cursor=None)
    if mode == "walk":
        found = _discover_by_walk(fleet, config)
        # Leave the saved cursor alone: this was a look, not a new starting point.
        return replace(found, cursor=None)

    saved = change_cursor.read(change_cursor.path_for(config.data_dir))
    if mode == "changes" and saved is None:
        raise SystemExit(
            "No changes cursor saved yet; run `gdstt run-once --mode walk` or a "
            "normal cycle first."
        )
    if saved is not None and not _cursor_covers_config(config, mode=mode):
        saved = None
    if saved is not None:
        found = _discover_by_changes(fleet.fallback, config, saved)
        if found is not None:
            return found
        if mode == "changes":
            raise SystemExit(
                "The saved changes cursor is no longer valid; a normal cycle would "
                "sweep and take a fresh one."
            )
    return _discover_by_walk(fleet, config)


def run_once(
    service: Any,
    config: Config,
    *,
    dry_run: bool = False,
    max_size_bytes: int | None = None,
    confirm_large: bool = False,
    mode: str = "auto",
    since: str = "",
) -> None:
    cycle_started_at = time.monotonic()
    cycle_pending = 0
    cycle_processed = 0
    cycle_failed = 0
    cycle_retry_total = 0
    cycle_skipped_size = 0
    cycle_skipped_unmatched = 0
    cycle_skipped_empty = 0
    cycle_skipped_nobody = 0
    cycle_folder_errors = 0
    cycle_deferred = 0
    cycle_skipped_old = 0
    settle_check_time = _utcnow()

    # Delegation is resolved here, once: from here on every entry carries a folder id
    # and the rest of the cycle is the cycle it always was. An employee who cannot be
    # resolved is dropped with an error counted, so the cycle cannot look drained.
    _begin_listing_failure_cycle()
    fleet = delegation.resolve(
        config,
        service,
        on_error=lambda folder, exc: _notify_listing_failure(
            f"the Meet folder of {folder.email}", exc, config
        ),
        retry=_call_with_transient_retries,
    )
    config = fleet.config
    cycle_folder_errors += fleet.errors

    discovery = _discover(fleet, config, mode=mode)
    cycle_retry_total += discovery.retries
    cycle_folder_errors += discovery.folder_errors

    for folder_id, items in discovery.listings:
        folder_service = fleet.service_for(folder_id)
        _apply_local_output_state(items, config)
        total_seen = len(items)
        items, skipped_old = _items_in_date_scope(
            items,
            parse_since(since or config.since_for(folder_id), source="since"),
            dry_run=dry_run,
        )
        cycle_skipped_old += skipped_old
        cycle_deferred += sum(
            1 for item in items if _is_still_settling(item, settle_check_time)
        )
        pending = _pending_items(items, config)
        # A marked recording is settled: reconsidering it every cycle would re-log and
        # re-decide forever. `gdstt bookings rematch` or any manual command revives it.
        pending = [
            item for item in pending
            if item.get("booking_match") != drive.BOOKING_MATCH_NONE
            and not item.get("transcript_empty")
            # A recording already marked as not worth transcribing. Unlike the two
            # above it is a decision rather than a result, so `reprocess` ignores it
            # -- that command never consults this list.
            and not item.get("skipped_id")
        ]
        # Recordings this cycle has just decided about. Splitting them out here keeps
        # the marker out of the processing loop's way: a dry run says what it would
        # write, and a real cycle writes it once.
        to_mark = [
            item for item in pending if item["file"]["id"] in discovery.skip_files
        ]
        pending = [
            item for item in pending if item["file"]["id"] not in discovery.skip_files
        ]
        pending_before_size = len(pending)
        pending = _items_allowed_by_size(
            pending,
            max_size_bytes=max_size_bytes,
            confirm_large=confirm_large,
        )
        skipped_size = pending_before_size - len(pending)
        cycle_pending += len(pending)
        cycle_skipped_size += skipped_size
        logger.info(
            "Folder %s summary [total=%d, pending=%d, skipped_size=%d, "
            "skipped_old=%d, dry_run=%s]",
            folder_id,
            total_seen,
            len(pending),
            skipped_size,
            skipped_old,
            dry_run,
        )
        if dry_run:
            for item in to_mark:
                logger.info(
                    "DRY RUN: would mark %s in folder %s as not worth transcribing",
                    item["file"].get("name"),
                    folder_id,
                )
            for item in pending:
                _log_dry_run(folder_id, item, config, reprocess_txt=False)
            continue
        for item in to_mark:
            if _mark_not_worth_transcribing(
                folder_service, item, discovery.skip_files[item["file"]["id"]], config
            ):
                cycle_skipped_nobody += 1
        for item in pending:
            decision = booking_gate.resolve(
                item["file"],
                folder_id,
                config,
                meeting_start=item.get("meeting_start"),
            )
            if (
                decision.state == booking_gate.UNMATCHED
                and config.call_booking_disable_recognition
                # A folder with a Telegram chat is recognized unconditionally: the
                # chat is the destination, so "no booking" is not a reason to skip --
                # and marking the file unmatched would park it for good.
                and not folder_telegram_chats(config, folder_id)
            ):
                file_name = item.get("file", {}).get("name")
                if booking_server.is_running():
                    # Permanent by design: the booking arrives before the call, so a
                    # recording with no booking is not a client call.
                    try:
                        booking_gate.mark_unmatched(
                            folder_service, item["file"]["id"]
                        )
                    except (RefreshError, AuthError):
                        raise
                    except Exception:
                        # A transient Drive failure here must not kill the polling
                        # loop; the file stays unmarked and is retried next cycle.
                        logger.exception(
                            "Failed to mark %s in folder %s as unmatched; will "
                            "retry next cycle",
                            file_name, folder_id,
                        )
                    else:
                        logger.info(
                            "Skipping %s in folder %s: no booked call (%s); marked "
                            "so it is not reconsidered (undo with `gdstt bookings "
                            "rematch`)",
                            file_name, folder_id, decision.reason,
                        )
                else:
                    logger.warning(
                        "Skipping %s in folder %s: no booked call (%s), but the "
                        "booking receiver is not listening, so it is not marked",
                        file_name, folder_id, decision.reason,
                    )
                cycle_skipped_unmatched += 1
                continue
            try:
                telemetry = process_item(
                    folder_service, item, folder_id, config, booking_decision=decision
                )
                cycle_processed += 1
                cycle_retry_total += _retry_count_from_process_result(telemetry)
            except (RefreshError, AuthError):
                raise
            except EmptyTranscriptError:
                # No speech is not a failure: the same audio comes back empty on every
                # retry, so park it instead of alerting and holding the cycle forever.
                cycle_skipped_empty += 1
                file_name = item.get("file", {}).get("name")
                try:
                    drive.set_file_app_properties(
                        folder_service,
                        item["file"]["id"],
                        {drive.TRANSCRIPT_EMPTY_PROPERTY: "true"},
                    )
                except (RefreshError, AuthError):
                    raise
                except Exception:
                    logger.exception(
                        "Failed to mark %s in folder %s as having no speech; will "
                        "retry next cycle",
                        file_name, folder_id,
                    )
                else:
                    logger.info(
                        "Skipping %s in folder %s: the transcript is empty (no "
                        "speech); marked so it is not reconsidered (undo with "
                        "`gdstt process <file-id>`)",
                        file_name, folder_id,
                    )
            except Exception as exc:
                cycle_failed += 1
                cycle_retry_total += _retry_count_from_exception(exc)
                file_name = item.get("file", {}).get("name")
                logger.exception(
                    "Failed to process %s in folder %s", file_name, folder_id
                )
                notify.notify_error(
                    f"Failed to process {file_name} in {folder_id}: {exc}\n"
                    f"{traceback.format_exc()}",
                    telegram_bot_token=config.telegram_bot_token,
                    telegram_chat_id=config.telegram_chat_id,
                    proxy_url=config.proxy_url,
                )

    # Only a cycle that actually drained what it found may move the cursor, and only
    # after the work. The changes feed reports a folder once, when something happens
    # in it; a recording this cycle failed on, or deliberately left for later, will
    # produce no second change of its own. Stepping over it would lose it for good --
    # the very failure this whole change exists to remove. Re-reading changes instead
    # is free, because the folder listing decides what still needs doing.
    # `cycle_skipped_old` is deliberately absent: a recording left out by `since` is
    # a permanent skip by design, like one over `--max-size`. Counting it would hold
    # the cursor on a backlog that is never going to be processed.
    cycle_drained = not (cycle_failed or cycle_folder_errors or cycle_deferred)
    if not dry_run and discovery.cursor and cycle_drained:
        change_cursor.write(change_cursor.path_for(config.data_dir), discovery.cursor)
        # Saved with the cursor, never apart from it: a cursor whose folder set is
        # missing cannot be vouched for and would sweep every cycle. The same
        # `cycle_drained` guard is what keeps a config edited before the folder was
        # actually shared from being recorded as seen -- that listing fails, which
        # counts as a folder error, which holds both files where they are.
        change_cursor.write_folders(
            change_cursor.folders_path_for(config.data_dir),
            change_cursor.fingerprint(
                folder.folder_id for folder in config.folders
            ),
        )
    elif not dry_run and discovery.cursor:
        logger.info(
            "Holding the changes cursor [failed=%d, folder_errors=%d, deferred=%d]; "
            "the next cycle reads the same changes again",
            cycle_failed, cycle_folder_errors, cycle_deferred,
        )

    # The Meet mark follows the same rule as the cursor, for the same reason: it says
    # "everything that started before this is dealt with", so a cycle that failed on
    # something, deferred it, or could not read a folder has not earned the move.
    # Discovery has already held it at the earliest conference it did not finish;
    # this guard covers what went wrong *after* discovery.
    if not dry_run and discovery.meet_mark and cycle_drained:
        meet_mark.write(meet_mark.path_for(config.data_dir), discovery.meet_mark)
    elif not dry_run and discovery.meet_mark:
        logger.info(
            "Holding the Meet mark [failed=%d, folder_errors=%d, deferred=%d]; "
            "the next cycle asks for the same conferences again",
            cycle_failed, cycle_folder_errors, cycle_deferred,
        )

    logger.info(
        "Cycle summary [provider=%s, outcome=%s, folders=%d, pending=%d, processed=%d, failed=%d, "
        "retry_total=%d, skipped_size=%d, skipped_unmatched=%d, skipped_old=%d, "
        "skipped_empty=%d, skipped_nobody=%d, folder_errors=%d, deferred=%d, "
        "cursor_moved=%s, dry_run=%s, "
        "duration_s=%.3f]",
        config.stt_provider or "artifact-only",
        _cycle_outcome(
            dry_run=dry_run,
            failed=cycle_failed,
            folder_errors=cycle_folder_errors,
        ),
        len(config.folders),
        cycle_pending,
        cycle_processed,
        cycle_failed,
        cycle_retry_total,
        cycle_skipped_size,
        cycle_skipped_unmatched,
        cycle_skipped_old,
        cycle_skipped_empty,
        cycle_skipped_nobody,
        cycle_folder_errors,
        cycle_deferred,
        bool(discovery.cursor) and cycle_drained and not dry_run,
        dry_run,
        time.monotonic() - cycle_started_at,
    )


def main(*, config_path: str | Path | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = load_config(config_path=config_path)
    if not config.folders:
        logger.error("folders is empty; configure it in config.yml to start polling")
        raise SystemExit(1)

    try:
        service = build_drive_service(config=config)
    except (RefreshError, AuthError) as exc:
        logger.exception("OAuth bootstrap failed; exiting for restart")
        notify.notify_error(
            f"OAuth bootstrap failed; container will exit so it can be restarted "
            f"after re-running `python -m src.auth`: {exc}",
            telegram_bot_token=config.telegram_bot_token,
            telegram_chat_id=config.telegram_chat_id,
            proxy_url=config.proxy_url,
        )
        raise SystemExit(1) from exc

    try:
        booking_server.start(config)
    except OSError as exc:
        # Degrade, do not exit: transcription is the primary job. With the receiver
        # down the gate refuses to mark anything (see `run_once`), so nothing is lost --
        # unmatched files simply wait.
        logger.exception("Booking receiver failed to start; continuing without it")
        notify.notify_error(
            f"Booking receiver failed to start on "
            f"{config.call_booking_listen_host}:{config.call_booking_listen_port}: "
            f"{exc}. Call bookings are not being received; recordings will not be "
            f"marked as unmatched until it is back.",
            telegram_bot_token=config.telegram_bot_token,
            telegram_chat_id=config.telegram_chat_id,
            proxy_url=config.proxy_url,
        )

    paused_logged = False
    while True:
        if not is_run_enabled(config_path=config_path):
            # `gdstt stop` sets run.enabled=false. Stay up but idle so a Docker
            # `restart: unless-stopped` policy does not crash-loop and the stop
            # survives restarts without auto-resuming. Resume with `gdstt start`.
            if not paused_logged:
                logger.info(
                    "run.enabled is false (gdstt stop); polling loop paused "
                    "(resume with `gdstt start` or `gdstt run`)"
                )
                paused_logged = True
            time.sleep(config.poll_interval)
            continue
        paused_logged = False
        try:
            run_once(service, config, mode=config.run_discovery)
        except (RefreshError, AuthError) as exc:
            logger.exception("OAuth refresh failed; exiting for restart")
            notify.notify_error(
                f"OAuth refresh failed; container will exit so it can be restarted "
                f"after re-running `python -m src.auth`: {exc}",
                telegram_bot_token=config.telegram_bot_token,
                telegram_chat_id=config.telegram_chat_id,
                proxy_url=config.proxy_url,
            )
            raise SystemExit(1) from exc
        except Exception as exc:
            logger.exception("Cycle failed")
            notify.notify_error(
                f"Cycle failed: {exc}\n{traceback.format_exc()}",
                telegram_bot_token=config.telegram_bot_token,
                telegram_chat_id=config.telegram_chat_id,
                proxy_url=config.proxy_url,
            )
        time.sleep(config.poll_interval)


if __name__ == "__main__":
    main()
