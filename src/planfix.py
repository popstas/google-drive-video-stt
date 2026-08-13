"""POST a meeting summary into a Planfix task as a comment.

Mirrors :func:`src.webhook.notify_complete`'s contract — a blank URL is a no-op and a
failure never raises — with one difference: this returns whether the POST succeeded.
The caller needs that answer, because it only writes the "already sent" marker on
success, and it escalates a failure to Telegram (a comment that never reached the CRM
is otherwise invisible to a human).

``description`` is the content of a client call. It must never reach a log line.
"""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10


def send_comment(
    *,
    url: str,
    token: str = "",
    proxy_url: str = "",
    task_id: str,
    description: str,
) -> bool:
    """Create a Planfix comment on ``task_id``. Returns True only when it landed."""
    target = url.strip()
    if not target:
        logger.debug("Planfix URL not set, skipping comment")
        return False

    body = description.strip()
    if not body:
        logger.debug("Planfix comment body is empty, skipping comment")
        return False

    try:
        numeric_task_id = int(task_id)
    except (TypeError, ValueError):
        # Rejected at intake too; this is the belt to that suspenders, and it must not
        # raise on the success path of a file that already cost money to transcribe.
        logger.warning("Planfix task id is not numeric, skipping comment")
        return False

    bearer = token.strip()
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else None
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None

    try:
        response = requests.post(
            target,
            json={"taskId": numeric_task_id, "description": body},
            headers=headers,
            timeout=REQUEST_TIMEOUT,
            proxies=proxies,
        )
        response.raise_for_status()
    except Exception as exc:
        # The status separates a bad token from an unreachable CRM; the exception
        # message can echo the URL, and the body is meeting content, so neither goes in.
        status = getattr(getattr(exc, "response", None), "status_code", None)
        logger.warning(
            "Failed to create Planfix comment for task %s: %s%s",
            numeric_task_id,
            type(exc).__name__,
            f" (HTTP {status})" if status else "",
        )
        return False

    logger.info("Created Planfix comment on task %s", numeric_task_id)
    return True
