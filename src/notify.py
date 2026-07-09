from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 4000
REQUEST_TIMEOUT = 10


def notify_error(
    text: str,
    *,
    telegram_bot_token: str = "",
    telegram_chat_id: str = "",
    proxy_url: str = "",
) -> None:
    token = telegram_bot_token.strip()
    chat_id = telegram_chat_id.strip()

    if not token or not chat_id:
        logger.debug("Telegram credentials not set, skipping notification")
        return

    if len(text) > MAX_MESSAGE_LENGTH:
        text = text[-MAX_MESSAGE_LENGTH:]

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None

    try:
        response = requests.post(
            url, data=payload, timeout=REQUEST_TIMEOUT, proxies=proxies,
        )
        response.raise_for_status()
    except Exception as exc:
        logger.warning("Failed to send Telegram notification: %s", type(exc).__name__)
