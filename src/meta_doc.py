"""Merge the ``meta`` preset's fields with the facts the code already knows.

The model is asked for four things — subject, tags, referral, referral note. Everything
else in the document is already in hand: the folder's employee, the recording's name,
the booking, the configured models. Paying a model to restate them would be slower, more
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
from src.meta import Meta

# The document's field order, top to bottom: what the call was about, who was on it,
# when, then the technical trail.
FIELD_ORDER = (
    "subject",
    "tags",
    "referral",
    "referral_note",
    "manager",
    "manager_email",
    "client",
    "date",
    "duration",
    "language",
    "planfix_task_id",
    "planfix_task_url",
    "video_id",
    "video_url",
    "source_name",
    "stt_model",
    "llm_model",
    "processed_at",
)

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


def task_url(template: str, task_id: str) -> str:
    """Render a Planfix task's web address from the configured template.

    ``<task-id>`` is substituted where it appears; a template without it is treated as
    a base and the id is appended, so both ``.../task/<task-id>`` and ``.../task``
    behave the way an operator writing either one would expect. Without a template or
    without an id there is nothing to link to, and the field stays empty.
    """
    base = (template or "").strip()
    if not base or not task_id:
        return ""
    if TASK_ID_PLACEHOLDER in base:
        return base.replace(TASK_ID_PLACEHOLDER, task_id)
    return f"{base.rstrip('/')}/{task_id}"


def _date(file_name: str) -> str:
    start = meeting_time.parse_meeting_start(file_name)
    return start.isoformat() if start else ""


def build(
    *,
    meta: Meta,
    file_id: str,
    file_name: str,
    folder_id: str,
    config: Config,
    transcript: str,
    planfix_task_id: str,
    processed_at: datetime,
) -> dict[str, object]:
    """Assemble the full meta document for one recording."""
    employee = config.folder_by_id(folder_id)
    return {
        "subject": meta.subject,
        "tags": list(meta.tags),
        "referral": meta.referral,
        "referral_note": meta.referral_note,
        "manager": employee.name if employee else "",
        "manager_email": employee.email if employee else "",
        "client": _client(file_name),
        "date": _date(file_name),
        "duration": _duration(transcript),
        "language": config.stt_language,
        "planfix_task_id": planfix_task_id,
        "planfix_task_url": task_url(config.planfix_task_url, planfix_task_id),
        "video_id": file_id,
        "video_url": f"https://drive.google.com/file/d/{file_id}/view",
        "source_name": file_name,
        "stt_model": f"{config.stt_provider}/{config.deepgram_model}",
        "llm_model": config.openai_model,
        "processed_at": processed_at.isoformat(),
    }


def to_yaml(document: dict[str, object]) -> str:
    """Serialize the document with its declared field order and readable Cyrillic."""
    ordered = {key: document.get(key, "") for key in FIELD_ORDER}
    return yaml.safe_dump(ordered, allow_unicode=True, sort_keys=False, width=1000)
