"""Receive upcoming-call bookings over HTTP and append them to the journal.

A daemon thread beside the polling loop, on the standard library's server: one POST
endpoint does not justify a web framework in a service whose entire HTTP surface is
``requests``, and a second container would have to share this one's volume anyway.
TLS and the public hostname belong to the reverse proxy in front of it.
"""

from __future__ import annotations

import hmac
import json
import logging
import threading
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from src.call_booking import CallBooking, append
from src.config import Config

logger = logging.getLogger(__name__)

# A booking is three short fields. Anything larger is a mistake or an attempt to make
# the receiver allocate; refuse it before reading the body.
MAX_BODY_BYTES = 64 * 1024

_running: BookingServer | None = None
_running_lock = threading.Lock()


def is_running() -> bool:
    """Whether a receiver is listening in this process.

    The gate consults this before marking a recording as permanently unmatched: if no
    receiver ever came up, every recording *looks* unmatched, and marking them would
    silently retire the whole backlog.
    """
    with _running_lock:
        return _running is not None


def _parse_start_time(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _booking_from_payload(payload: object) -> CallBooking | None:
    """Validate the POST body into a booking, or ``None`` when it is unusable."""
    if not isinstance(payload, dict):
        return None

    task_id = payload.get("task_id")
    manager_email = payload.get("manager_email")
    if not isinstance(task_id, (str, int)):
        return None
    if not isinstance(manager_email, str) or not manager_email.strip():
        return None

    task_id_text = str(task_id).strip()
    # Planfix wants a numeric task id. Rejecting it here turns a bad id into an
    # immediate 400 for the sender instead of a dropped comment hours later, on the
    # success path of a file that already cost money to transcribe. isdecimal() alone
    # accepts non-ASCII digits (e.g. Arabic-Indic), which int() would silently
    # normalize -- the stored task_id string would then disagree with what was sent.
    if not (task_id_text.isascii() and task_id_text.isdecimal()):
        return None

    start_time = _parse_start_time(payload.get("start_time"))
    if start_time is None:
        return None

    return CallBooking(
        task_id=task_id_text,
        manager_email=manager_email.strip(),
        start_time=start_time,
    )


class _Handler(BaseHTTPRequestHandler):
    server_version = "gdstt-booking/1.0"

    # Set by ``serve``.
    token: str = ""
    journal_path: Path = Path("call_bookings.jsonl")

    def log_message(self, fmt: str, *args) -> None:
        # The default handler writes to stderr outside logging; route it through the
        # module logger at debug so request lines do not spam the service log.
        logger.debug("booking receiver: " + fmt, *args)

    def _respond(self, status: HTTPStatus, text: str = "") -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not header.startswith(prefix):
            return False
        candidate = header[len(prefix) :].strip()
        # compare_digest keeps the comparison constant-time so a wrong token cannot be
        # guessed a character at a time by timing the response. It only accepts bytes
        # or ASCII-only str; header values can carry non-ASCII characters (they arrive
        # latin-1-decoded), so compare as bytes rather than let a stray accent turn an
        # unauthenticated request into a 500.
        return hmac.compare_digest(
            candidate.encode("utf-8", "surrogateescape"), self.token.encode("utf-8")
        )

    def do_GET(self) -> None:  # noqa: N802 - stdlib hook name
        if self.path.rstrip("/") in ("/health", "health"):
            self._respond(HTTPStatus.OK, "ok")
            return
        self._respond(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:  # noqa: N802 - stdlib hook name
        try:
            if not self._authorized():
                self._respond(HTTPStatus.UNAUTHORIZED, "unauthorized")
                return

            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._respond(HTTPStatus.BAD_REQUEST, "bad content-length")
                return
            # A negative length is nonsensical, but int() parses "-1" fine and it is
            # truthy, so without this check rfile.read(length) below would read until
            # EOF -- i.e. block forever, since the client keeps the connection open
            # waiting for a response that never comes.
            if length < 0:
                self._respond(HTTPStatus.BAD_REQUEST, "bad content-length")
                return
            if length > MAX_BODY_BYTES:
                self._respond(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "body too large")
                return

            # min() is a second belt on top of the length check above: never let a
            # future change to that check turn this read into an unbounded one.
            raw = self.rfile.read(min(length, MAX_BODY_BYTES)) if length else b""
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._respond(HTTPStatus.BAD_REQUEST, "malformed json")
                return

            booking = _booking_from_payload(payload)
            if booking is None:
                self._respond(HTTPStatus.BAD_REQUEST, "invalid booking")
                return

            append(self.journal_path, booking)
            logger.info(
                "Stored booking for task %s at %s",
                booking.task_id,
                booking.start_time.isoformat(),
            )
            self._respond(HTTPStatus.NO_CONTENT)
        except Exception:
            # One bad request must not take the receiver thread down with it.
            logger.exception("Booking receiver failed to handle a request")
            try:
                self._respond(HTTPStatus.INTERNAL_SERVER_ERROR, "internal error")
            except Exception:
                # The connection may already be gone (e.g. a broken pipe) if that is
                # what caused the exception above. Re-raising here would escape
                # do_POST and let socketserver's default handler dump a raw traceback
                # to stderr, bypassing the module logger for no benefit.
                pass


class BookingServer:
    """A running receiver: the socket, its thread, and how to stop them."""

    def __init__(self, httpd: ThreadingHTTPServer, thread: threading.Thread) -> None:
        self._httpd = httpd
        self._thread = thread

    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    def shutdown(self) -> None:
        global _running
        # Clear the flag before touching the socket or thread at all. is_running()
        # gates a safety invariant elsewhere (see its docstring): a True answer must
        # never outlive the actual bound port, but a False answer while the port is
        # still momentarily closing is harmless. Clearing first, outside the
        # try/finally below, also means a raise from shutdown()/server_close() can
        # never leave the flag stuck True against a receiver that is no longer there.
        with _running_lock:
            if _running is self:
                _running = None
        try:
            self._httpd.shutdown()
            self._httpd.server_close()
        finally:
            self._thread.join(timeout=5)


def serve(*, host: str, port: int, token: str, journal_path: Path) -> BookingServer:
    """Bind and start a receiver. Raises ``OSError`` when the port is unavailable."""
    global _running

    handler = type(
        "_BoundHandler",
        (_Handler,),
        {"token": token, "journal_path": journal_path},
    )
    httpd = ThreadingHTTPServer((host, port), handler)
    thread = threading.Thread(
        target=httpd.serve_forever,
        name="gdstt-booking-receiver",
        daemon=True,
    )
    thread.start()
    instance = BookingServer(httpd, thread)
    with _running_lock:
        _running = instance
    logger.info("Booking receiver listening on %s:%d", host, instance.port)
    return instance


def start(config: Config) -> BookingServer | None:
    """Start the receiver when the config enables it, else return ``None``."""
    if not config.call_booking_enabled:
        logger.debug("call_booking.enabled is false, not starting the receiver")
        return None
    return serve(
        host=config.call_booking_listen_host,
        port=config.call_booking_listen_port,
        token=config.call_booking_token,
        journal_path=config.call_bookings_file,
    )
