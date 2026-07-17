"""POST a completion payload once a file finishes processing.

Fire-and-forget, mirroring :func:`src.notify.notify_error`: a webhook failure is
logged and never re-raised, so an unreachable receiver cannot fail a file that
already transcribed and wrote its artifacts. Failures log only the exception type
— the payload carries PII (employee email plus the full transcript) and the URL
may carry a token.
"""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10


def notify_complete(
    *,
    url: str = "",
    token: str = "",
    proxy_url: str = "",
    payload: dict,
) -> None:
    target = url.strip()
    if not target:
        logger.debug("Webhook URL not set, skipping completion webhook")
        return

    bearer = token.strip()
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else None
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None

    try:
        response = requests.post(
            target,
            json=payload,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
            proxies=proxies,
        )
        response.raise_for_status()
    except Exception as exc:
        logger.warning("Failed to send completion webhook: %s", type(exc).__name__)
