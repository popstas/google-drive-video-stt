"""Merge the ``meta`` preset's fields with the facts the code already knows.

The model is asked about the configured entities -- subject, tags, referral, and
whatever else the operator's ``meta.entities`` declares. Everything else in the
document is already in hand: the folder's employee, the recording's name, the
booking, the configured models. Paying a model to restate them would be slower, more
expensive, and less reliable than reading them.

Every field stays present even when empty. An operator reading the file must be able to
tell "nobody asked about the referral" from "this build does not produce that field".
"""

from __future__ import annotations

import re
from datetime import datetime

import yaml

from src import meeting_time, postprocess
from src.config import Config
from src.meta_entity import CODE_FIELDS, MetaEntity


def field_order(entities: tuple[MetaEntity, ...]) -> tuple[str, ...]:
    """The document's field order: what the model extracted, then what code knows.

    Entities come first and in config order -- what the call was about and who was
    on it -- followed by the technical trail.
    """
    return tuple(entity.name for entity in entities) + CODE_FIELDS


_TIMESTAMP_RE = re.compile(r"\[(\d{2}:\d{2}:\d{2})\]")


def _duration(transcript: str) -> str:
    """The last timestamp in the transcript, as the call's length.

    Not the media's true duration: trailing silence after the last word is dropped. That
    is cheaper than probing the file and close enough for a summary line.
    """
    stamps = _TIMESTAMP_RE.findall(transcript or "")
    return stamps[-1] if stamps else ""


def _client(file_name: str) -> str:
    """The first client name, or empty when no manager marker was found.

    A marker-less name (``split_participants`` returns every name as a "client")
    is not proof one of them is actually a client -- it may just be an
    organizer-less recording. Guessing would present Meet's own filename
    fragments as a client's name, so the field stays empty instead.
    """
    manager, clients = postprocess.split_participants(file_name)
    if not manager:
        return ""
    return clients[0] if clients else ""


TASK_ID_PLACEHOLDER = "<task-id>"
CALENDLY_UUID_PLACEHOLDER = "<uuid>"


def video_url(file_id: str) -> str:
    """Where a Drive recording opens in a browser.

    Shared with the CLI so the link an operator is shown and the link written into the
    meta document can never drift apart.
    """
    return f"https://drive.google.com/file/d/{file_id}/view" if file_id else ""


def folder_url(container_id: str, folder_id: str) -> str:
    """Where a recording's meeting folder opens, or "" when it has none of its own.

    Meet files each call into its own subfolder, which holds the video beside every
    artifact, so a reader who asks for access to that folder gets the whole call. A
    recording lying directly in the configured folder shares it with every other
    call; that folder is not "the call", and the link stays on the video.
    """
    if not container_id or container_id == folder_id:
        return ""
    return f"https://drive.google.com/drive/folders/{container_id}"


def _fill(template: str, placeholder: str, value: str) -> str:
    """Put ``value`` into a link template at ``placeholder``, or append it.

    A template without the placeholder is treated as a base and the value is
    appended, so both ``.../task/<task-id>`` and ``.../task`` behave the way an
    operator writing either one would expect. Without a template or without a value
    there is nothing to link to, and the field stays empty.
    """
    base = (template or "").strip()
    if not base or not value:
        return ""
    if placeholder in base:
        return base.replace(placeholder, value)
    return f"{base.rstrip('/')}/{value}"


def task_url(template: str, task_id: str) -> str:
    """Render a Planfix task's web address from the configured template."""
    return _fill(template, TASK_ID_PLACEHOLDER, task_id)


def calendly_url(template: str, event_uuid: str) -> str:
    """Render a Calendly event's web address from the configured template."""
    return _fill(template, CALENDLY_UUID_PLACEHOLDER, event_uuid)


def _date(file_name: str) -> str:
    start = meeting_time.parse_meeting_start(file_name)
    return start.isoformat() if start else ""


def build(
    *,
    values: dict[str, object],
    file_id: str,
    file_name: str,
    folder_id: str,
    config: Config,
    transcript: str,
    planfix_task_id: str,
    processed_at: datetime,
    container_id: str = "",
    calendly_event_uuid: str = "",
) -> dict[str, object]:
    """Assemble the full meta document for one recording.

    ``folder_id`` is the configured folder, which says whose call this is;
    ``container_id`` is the folder the video actually lies in.
    """
    employee = config.folder_by_id(folder_id)
    return {
        **values,
        "manager": employee.name if employee else "",
        "manager_email": employee.email if employee else "",
        "client": _client(file_name),
        "date": _date(file_name),
        "duration": _duration(transcript),
        "language": config.stt_language,
        "planfix_task_id": planfix_task_id,
        "planfix_task_url": task_url(config.planfix_task_url, planfix_task_id),
        "calendly_url": calendly_url(config.call_booking_calendly_url, calendly_event_uuid),
        "video_id": file_id,
        "video_url": video_url(file_id),
        "folder_url": folder_url(container_id, folder_id),
        "source_name": file_name,
        "stt_model": f"{config.stt_provider}/{config.deepgram_model}",
        "llm_model": config.openai_model,
        "processed_at": processed_at.isoformat(),
    }


def to_yaml(document: dict[str, object], entities: tuple[MetaEntity, ...]) -> str:
    """Serialize the document with its declared field order and readable Cyrillic."""
    ordered = {key: document.get(key, "") for key in field_order(entities)}
    return yaml.safe_dump(ordered, allow_unicode=True, sort_keys=False, width=1000)
