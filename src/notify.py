from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 4000
REQUEST_TIMEOUT = 10


def notify_error(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat_id:
        logger.debug("Telegram credentials not set, skipping notification")
        return

    if len(text) > MAX_MESSAGE_LENGTH:
        text = text[-MAX_MESSAGE_LENGTH:]

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}

    try:
        response = requests.post(url, data=payload, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except Exception as exc:
        logger.warning("Failed to send Telegram notification: %s", type(exc).__name__)
