from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 4000
REQUEST_TIMEOUT = 10


def split_message(text: str, limit: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Cut ``text`` into Telegram-sized chunks, preferring line boundaries.

    Unlike ``notify_error``, which drops everything past the tail, a call summary must
    arrive whole: truncating a Keypoints document would silently lose the open
    questions at its end. Splitting on newlines keeps list items intact; a single line
    longer than the limit is hard-split, because there is nothing better to break on.
    """
    text = text.strip()
    if not text:
        return []
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current.strip():
        chunks.append(current)
    return [chunk for chunk in chunks if chunk.strip()]


def send_message(
    text: str,
    *,
    bot_token: str,
    chat_id: str,
    proxy_url: str = "",
) -> bool:
    """Send ``text`` to a chat as plain text, splitting it when it is too long.

    Plain text on purpose: the summary is assembled as Markdown for Planfix, and
    Telegram's HTML/Markdown parse modes reject most of what a transcript-derived
    document contains (unbalanced ``*``, stray ``_``, ``<`` in quoted text) by failing
    the whole request. Returns True only when every chunk was accepted, so the caller
    does not write a "delivered" marker for a half-sent summary.
    """
    token = bot_token.strip()
    chat = chat_id.strip()
    if not token or not chat:
        logger.debug("Telegram credentials not set, skipping message")
        return False

    chunks = split_message(text)
    if not chunks:
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    for chunk in chunks:
        try:
            response = requests.post(
                url,
                data={"chat_id": chat, "text": chunk},
                timeout=REQUEST_TIMEOUT,
                proxies=proxies,
            )
            response.raise_for_status()
        except Exception as exc:
            logger.warning(
                "Failed to send Telegram message to %s: %s", chat, type(exc).__name__
            )
            return False
    return True


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
