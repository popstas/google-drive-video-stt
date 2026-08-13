from __future__ import annotations

from dataclasses import dataclass, field
import logging
import json
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
    booking_gate,
    booking_server,
    drive,
    meta as meta_module,
    notify,
    output,
    planfix,
    planfix_html,
    postprocess,
    preset_pipeline,
    speaker_roles,
    webhook,
)
from src.auth import AuthError, build_drive_service
from src.config import Config, is_run_enabled, load_config
from src.extractor import extract_m4a_copy, extract_mp3
from src.openai_pipeline import OpenAIPipeline
from src.presets import Preset
from src.stt.transcribe import transcribe_file

logger = logging.getLogger(__name__)

_TRANSIENT_HTTP_STATUS_CODES = {429, 500, 502, 503, 504}
_TRANSIENT_RETRY_ATTEMPTS = 3
_TRANSIENT_RETRY_DELAYS = (1.0, 2.0)


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


def _http_status_code(exc: Exception) -> int | None:
    if isinstance(exc, HttpError):
        return getattr(exc.resp, "status", None)
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code
    return None


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
    folder_id: str,
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
        folder_id=folder_id,
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
    folder_id: str,
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
        folder_id=folder_id,
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
    tmp_dir: Path,
    config: Config,
    *,
    speaker_names: list[str] | None,
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
            folder_id,
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


def _resolve_speaker_names(
    transcript: str,
    file_name: str,
    folder_id: str,
    config: Config,
    *,
    usage: dict[str, dict[str, int]] | None = None,
) -> list[str] | None:
    """Ask the model which diarized speaker is which participant.

    Without this the names extracted from the file name are bound to speakers by who
    talks first, which silently swaps the pair on every call the client opens. The
    folder's owner is the one identity we know for certain, so it is handed over as the
    manager and the model places the rest from the opening turns.

    Returns None whenever the answer cannot be trusted; the caller then keeps the
    positional order, which is what this code did before.
    """
    if not config.openai_api_key:
        return None
    candidates = postprocess.extract_interlocutor_names(file_name)
    if len(candidates) < 2:
        return None

    employee = config.folder_by_id(folder_id)
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
        )
    finally:
        if usage is not None and pipeline.last_usage:
            usage["openai_speaker_roles"] = dict(pipeline.last_usage)
        pipeline.close()

    if names is not None:
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
    into ``{topic, tags}`` (tags filtered to the configured allow-list). An unknown
    employee sends empty strings rather than omitting the key.
    """
    employee = config.folder_by_id(folder_id)
    payload_artifacts: dict[str, object] = dict(artifacts)
    meta_text = artifacts.get("meta")
    if meta_text is not None:
        parsed = meta_module.parse_meta(meta_text, config.tags_allowed)
        payload_artifacts["meta"] = {"topic": parsed.topic, "tags": list(parsed.tags)}

    return {
        "file": {"id": file_id, "name": file_name, "folder_id": folder_id},
        "employee": {
            "name": employee.name if employee else "",
            "email": employee.email if employee else "",
        },
        "transcript": transcript,
        "artifacts": payload_artifacts,
    }


def _planfix_description(
    artifacts: dict[str, str], preset_names: tuple[str, ...]
) -> str:
    """Concatenate the configured preset artifacts into one comment body.

    Presets are joined in configured order, each under its own heading, and a preset
    with no artifact is skipped rather than emitting an empty section.

    The result is HTML, not the Markdown the presets emit: Planfix stores comments as
    HTML and renders ``##`` and ``-`` as literal characters. Conversion happens once,
    on the assembled document, so headings and lists nest the same way they read.
    """
    sections = [
        f"## {name}\n{artifacts[name].strip()}"
        for name in preset_names
        if artifacts.get(name, "").strip()
    ]
    return planfix_html.markdown_to_html("\n\n".join(sections))


def _send_planfix_comment(
    service: Any,
    item: dict,
    file_id: str,
    config: Config,
    artifacts: dict[str, str],
    booking_decision: booking_gate.BookingDecision,
) -> None:
    """Post the meeting summary into the matched Planfix task, exactly once.

    `process_item` can legitimately reach its success path more than once per file — a
    later cycle that backfills a newly configured preset re-feeds the transcript — so
    the `planfix_comment_task_id` marker, written only after a successful POST, is what
    keeps a second pass from posting a duplicate comment into the task.
    """
    if not booking_decision.is_matched:
        return
    if not config.planfix_create_comment_url:
        return
    if item.get("planfix_comment_task_id"):
        logger.debug("Planfix comment already sent for %s, skipping", file_id)
        return

    description = _planfix_description(artifacts, config.planfix_presets)
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
        booking_decision = booking_gate.resolve(file_info, folder_id, config)

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
                    folder_id,
                    mime_type=drive.MP3_MIME,
                    name=mp3_drive_name,
                    app_properties={
                        drive.SOURCE_VIDEO_ID_PROPERTY: file_id,
                        drive.ARTIFACT_TYPE_PROPERTY: "mp3",
                    },
                )
                mp3_uploaded = True
                logger.info("Uploaded %s to folder %s", mp3_drive_name, folder_id)

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
                if config.stt_postprocess:
                    if speaker_names is None:
                        speaker_names = _resolve_speaker_names(
                            text, file_name, folder_id, config, usage=usage
                        )
                    text = postprocess.postprocess_transcript(
                        text,
                        file_name,
                        speaker_names=speaker_names,
                    )
                _save_and_upload_txt(
                    service, file_id, file_name, text, folder_id, tmp_dir, config,
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
                    tmp_dir,
                    config,
                    speaker_names=speaker_names,
                    artifact_ids=item.get("artifact_ids") or {},
                    reprocess=reprocess_txt,
                    usage=usage,
                    unproduced=unproduced,
                    local_artifact_paths=_local_artifact_paths(item),
                    only_presets=reprocess_presets,
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
                    tmp_dir,
                    config,
                    speaker_names=speaker_names,
                    artifact_ids=item.get("artifact_ids") or {},
                    reprocess=False,
                    usage=usage,
                    unproduced=unproduced,
                    local_artifact_paths=_local_artifact_paths(item),
                    only_presets=reprocess_presets,
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
                service, item, file_id, config, artifacts, booking_decision
            )
        except Exception as exc:
            # A file that transcribed and uploaded must count as processed even if the
            # CRM hand-off misbehaves.
            logger.warning("Planfix comment failed: %s", type(exc).__name__)

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
    )


def _pending_items(items: list[dict], config: Config) -> list[dict]:
    stt_enabled = bool(config.stt_provider)
    pending = []
    for item in items:
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
            lambda: drive.list_folder_state(service, target_id),
            description=f"list folder state for {target_id}",
        )
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
                    target_id, item, config,
                    reprocess_txt=reprocess_txt,
                    reprocess_presets=reprocess_presets,
                )
            return telemetry
        for item in pending:
            result = process_item(
                service,
                item,
                target_id,
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
    folder_id = parents[0]
    items = _call_with_transient_retries(
        lambda: drive.list_folder_state(service, folder_id),
        description=f"list folder state for {folder_id}",
    )
    _apply_local_output_state(items, config)
    match = next(
        (it for it in items if it["file"]["id"] == target_id), None
    )
    if match is None:
        raise RuntimeError(
            f"File {target_id} is not an MP4 in folder {folder_id}"
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


def run_once(
    service: Any,
    config: Config,
    *,
    dry_run: bool = False,
    max_size_bytes: int | None = None,
    confirm_large: bool = False,
) -> None:
    cycle_started_at = time.monotonic()
    cycle_pending = 0
    cycle_processed = 0
    cycle_failed = 0
    cycle_retry_total = 0
    cycle_skipped_size = 0
    cycle_skipped_unmatched = 0
    cycle_folder_errors = 0

    for folder in config.folders:
        folder_id = folder.folder_id
        listing_retry_state = _RetryState()
        try:
            items = _call_with_transient_retries(
                lambda: drive.list_folder_state(service, folder_id),
                description=f"list folder state for {folder_id}",
                retry_state=listing_retry_state,
            )
        except (RefreshError, AuthError):
            raise
        except Exception as exc:
            cycle_folder_errors += 1
            logger.exception("Failed to list folder %s", folder_id)
            notify.notify_error(
                f"Failed to list folder {folder_id}: {exc}\n{traceback.format_exc()}",
                telegram_bot_token=config.telegram_bot_token,
                telegram_chat_id=config.telegram_chat_id,
                proxy_url=config.proxy_url,
            )
            continue
        finally:
            cycle_retry_total += listing_retry_state.retry_count

        _apply_local_output_state(items, config)
        pending = _pending_items(items, config)
        # A marked recording is settled: reconsidering it every cycle would re-log and
        # re-decide forever. `gdstt bookings rematch` or any manual command revives it.
        pending = [
            item for item in pending
            if item.get("booking_match") != drive.BOOKING_MATCH_NONE
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
            "Folder %s summary [total=%d, pending=%d, skipped_size=%d, dry_run=%s]",
            folder_id,
            len(items),
            len(pending),
            skipped_size,
            dry_run,
        )
        if dry_run:
            for item in pending:
                _log_dry_run(folder_id, item, config, reprocess_txt=False)
            continue
        for item in pending:
            decision = booking_gate.resolve(item["file"], folder_id, config)
            if (
                decision.state == booking_gate.UNMATCHED
                and config.call_booking_disable_recognition
            ):
                file_name = item.get("file", {}).get("name")
                if booking_server.is_running():
                    # Permanent by design: the booking arrives before the call, so a
                    # recording with no booking is not a client call.
                    try:
                        booking_gate.mark_unmatched(service, item["file"]["id"])
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
                    service, item, folder_id, config, booking_decision=decision
                )
                cycle_processed += 1
                cycle_retry_total += _retry_count_from_process_result(telemetry)
            except (RefreshError, AuthError):
                raise
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

    logger.info(
        "Cycle summary [provider=%s, outcome=%s, folders=%d, pending=%d, processed=%d, failed=%d, "
        "retry_total=%d, skipped_size=%d, skipped_unmatched=%d, folder_errors=%d, dry_run=%s, "
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
        cycle_folder_errors,
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
            run_once(service, config)
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
