"""POST a completion payload once a file finishes processing.

Fire-and-forget, mirroring :func:`src.notify.notify_error`: a webhook failure is
logged and never re-raised, so an unreachable receiver cannot fail a file that
already transcribed and wrote its artifacts. Failures log only the exception type
— the payload carries PII (employee email plus the full transcript) and the URL
may carry a token.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


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
    if (urlparse(target).hostname or "") in LOOPBACK_HOSTS:
        # A loopback receiver must never go through a proxy: the proxy could not reach
        # it anyway, and routing it there would send the token and the transcript
        # off-host, contradicting the loopback exemption in _warn_on_plaintext_webhook.
        # None values also drop any proxy requests would otherwise take from the env.
        proxies = {"http": None, "https": None}
    elif proxy_url:
        proxies = {"http": proxy_url, "https": proxy_url}
    else:
        proxies = None

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
        # The status code is not PII and is what separates a bad token from an
        # unreachable receiver; the exception message can echo the URL, so it stays out.
        status = getattr(getattr(exc, "response", None), "status_code", None)
        logger.warning(
            "Failed to send completion webhook: %s%s",
            type(exc).__name__,
            f" (HTTP {status})" if status else "",
        )
