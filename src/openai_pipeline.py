from __future__ import annotations

import io
import json
import logging
import time

from src.config import Config
from src.postprocess import extract_interlocutor_names
from src.stt.base import STTError

logger = logging.getLogger(__name__)

# LLM keypoints pipeline modeled on the `keypoints-transcription` skill: take a
# speaker-named transcript of a recorded conversation and produce a concise
# Keypoints summary (Задачи / Тезисы / Открытые вопросы) grounded strictly in the
# transcript, in plain Markdown without vault-style wikilinks.
INSTRUCTIONS = (
    "You are a meeting analyst. You receive a speaker-named transcript of a "
    "recorded conversation and produce a concise Keypoints summary in Markdown, "
    "written in the transcript's own language.\n"
    "Return ONLY the Keypoints document with exactly these three sections, in this "
    "order and with these exact headings:\n"
    "## Задачи\n"
    "Group action items under a `### <Ответственный>` subheading per assignee, "
    "using the speaker's real name from the transcript; use `### Без "
    "ответственного` when the owner is unclear. List each task as `- [ ] <task>` "
    "and do not repeat the assignee name inside the task line.\n"
    "## Тезисы\n"
    "Key points and decisions, each as a `- ` bullet.\n"
    "## Открытые вопросы\n"
    "Unresolved questions, each as a `- ` bullet.\n"
    "Rules: base every item strictly on the transcript - never invent facts, "
    "tasks, or decisions. Omit a section's bullets only when the transcript truly "
    "has none, but always keep the three headings. Plain text only: no wikilinks "
    "(`[[...]]`), no em dashes (use `-`), no guillemets (use straight quotes). No "
    "preamble, no explanation, no marketing."
)

DEFAULT_MODEL = "gpt-5.4-mini"
RESPONSES_ENDPOINT = "/v1/responses"
_USAGE_FIELDS = {
    "input_tokens": ("input_tokens",),
    "cached_input_tokens": ("input_tokens_details", "cached_tokens"),
    "output_tokens": ("output_tokens",),
    "reasoning_tokens": ("output_tokens_details", "reasoning_tokens"),
    "total_tokens": ("total_tokens",),
}


def build_prompt(
    transcript: str,
    file_name: str,
    *,
    speaker_names: list[str] | None = None,
) -> str:
    """Compose the user prompt: participant hints + the speaker-named transcript."""
    names = speaker_names if speaker_names is not None else extract_interlocutor_names(file_name)
    if names:
        hint = (
            "Known participants (in no particular order): "
            + ", ".join(names)
            + "."
        )
    else:
        hint = "Use the participant names exactly as they appear in the transcript."
    return f"{hint}\n\nTranscript:\n{transcript}"


def _extract_output_text(response: object) -> str:
    """Pull the assistant text out of a Responses API result object."""
    text = getattr(response, "output_text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()

    parts: list[str] = []
    for item in getattr(response, "output", None) or []:
        for chunk in getattr(item, "content", None) or []:
            chunk_text = getattr(chunk, "text", None)
            if isinstance(chunk_text, str):
                parts.append(chunk_text)
    if parts:
        return "".join(parts).strip()
    raise STTError(f"OpenAI returned unexpected response: {response!r}")


def _content_to_text(content: object) -> str:
    """Normalize the various shapes `files.content` can return into a string."""
    if isinstance(content, (bytes, bytearray)):
        return bytes(content).decode("utf-8")
    if isinstance(content, str):
        return content
    text = getattr(content, "text", None)
    if isinstance(text, str):
        return text
    read = getattr(content, "read", None)
    if callable(read):
        data = read()
        if isinstance(data, (bytes, bytearray)):
            return bytes(data).decode("utf-8")
        return str(data)
    return str(content)


def _nested_value(payload: object, path: tuple[str, ...]) -> object:
    value = payload
    for key in path:
        if isinstance(value, dict):
            value = value.get(key)
        else:
            value = getattr(value, key, None)
    return value


def _normalize_usage(payload: object) -> dict[str, int]:
    usage: dict[str, int] = {}
    for result_key, path in _USAGE_FIELDS.items():
        value = _nested_value(payload, path)
        if isinstance(value, int) and not isinstance(value, bool):
            usage[result_key] = value
    return usage


def _output_text_from_body(body: dict) -> str:
    """Extract assistant text from a JSON Responses body (batch output line)."""
    if isinstance(body.get("output_text"), str):
        return body["output_text"].strip()
    parts: list[str] = []
    for item in body.get("output") or []:
        for chunk in item.get("content") or []:
            if chunk.get("type") == "output_text" and isinstance(chunk.get("text"), str):
                parts.append(chunk["text"])
    return "".join(parts).strip()


def _extract_batch_text(
    content: object,
    *,
    usage: dict[str, int] | None = None,
) -> str:
    raw = _content_to_text(content)
    last = ""
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise STTError(f"OpenAI batch output was not valid JSON: {exc}") from exc
        # Surface a failed request rather than silently writing an empty/wrong
        # keypoints document: a per-line error or non-2xx status means this request
        # did not produce a usable result.
        if record.get("error"):
            raise STTError(f"OpenAI batch request failed: {record['error']}")
        response = record.get("response") or {}
        status_code = response.get("status_code")
        if status_code is not None and status_code != 200:
            raise STTError(
                f"OpenAI batch request returned HTTP {status_code}: {response.get('body')}"
            )
        body = response.get("body") or {}
        text = _output_text_from_body(body)
        if text:
            last = text
            if usage is not None:
                usage.clear()
                usage.update(_normalize_usage(body.get("usage")))
    if not last:
        raise STTError("OpenAI batch output contained no keypoints text")
    return last


class OpenAIPipeline:
    """Keypoints generation over a transcript via the OpenAI Responses API.

    Supports a synchronous path (`responses.create`) and an optional batch path
    (`files.create` + `batches.create`) that trades latency for ~50% lower cost.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        proxy_url: str = "",
        use_batch: bool = False,
        poll_interval: float = 5.0,
        batch_timeout: float = 86400.0,
    ) -> None:
        if not api_key:
            raise STTError(
                "OPENAI_API_KEY is required for the OpenAI keypoints pipeline"
            )
        self._api_key = api_key
        self._model = model or DEFAULT_MODEL
        self._proxy_url = proxy_url
        self._use_batch = use_batch
        self._poll_interval = poll_interval
        self._batch_timeout = batch_timeout
        self._client = None
        self.last_usage: dict[str, int] = {}

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise STTError(
                "openai package not installed; install with `uv add openai`"
            ) from exc

        kwargs: dict = {"api_key": self._api_key}
        if self._proxy_url:
            try:
                import httpx
            except ImportError as exc:
                raise STTError(
                    "httpx required for proxy support; install with `uv add httpx`"
                ) from exc
            kwargs["http_client"] = httpx.Client(proxy=self._proxy_url)
        self._client = OpenAI(**kwargs)
        return self._client

    def generate_keypoints(
        self,
        transcript: str,
        file_name: str,
        *,
        speaker_names: list[str] | None = None,
    ) -> str:
        self.last_usage = {}
        transcript = transcript.strip()
        if not transcript:
            return transcript
        prompt = build_prompt(transcript, file_name, speaker_names=speaker_names)
        if self._use_batch:
            return self._generate_batch(prompt)
        return self._generate_sync(prompt)

    def _generate_sync(self, prompt: str) -> str:
        client = self._get_client()
        try:
            response = client.responses.create(
                model=self._model,
                instructions=INSTRUCTIONS,
                input=prompt,
            )
        except Exception as exc:
            raise STTError(f"OpenAI keypoints generation failed: {exc}") from exc
        self.last_usage = _normalize_usage(getattr(response, "usage", None))
        return _extract_output_text(response)

    def _generate_batch(self, prompt: str) -> str:
        client = self._get_client()
        request = {
            "custom_id": "keypoints-0",
            "method": "POST",
            "url": RESPONSES_ENDPOINT,
            "body": {
                "model": self._model,
                "instructions": INSTRUCTIONS,
                "input": prompt,
            },
        }
        payload = (json.dumps(request) + "\n").encode("utf-8")
        try:
            upload = client.files.create(
                file=("batch-requests.jsonl", io.BytesIO(payload)),
                purpose="batch",
            )
            batch = client.batches.create(
                input_file_id=upload.id,
                endpoint=RESPONSES_ENDPOINT,
                completion_window="24h",
            )
            batch = self._await_batch(client, batch.id)
            output_file_id = getattr(batch, "output_file_id", None)
            if not output_file_id:
                raise STTError(f"OpenAI batch {batch.id} produced no output file")
            content = client.files.content(output_file_id)
        except STTError:
            raise
        except Exception as exc:
            raise STTError(f"OpenAI batch keypoints generation failed: {exc}") from exc
        return _extract_batch_text(content, usage=self.last_usage)

    def _await_batch(self, client, batch_id: str):
        elapsed = 0.0
        while True:
            batch = client.batches.retrieve(batch_id)
            status = getattr(batch, "status", None)
            if status == "completed":
                return batch
            if status in {"failed", "expired", "cancelled", "cancelling"}:
                raise STTError(
                    f"OpenAI batch {batch_id} ended with status {status!r}"
                )
            if elapsed >= self._batch_timeout:
                raise STTError(
                    f"OpenAI batch {batch_id} timed out after {self._batch_timeout}s"
                )
            logger.info("Waiting on OpenAI batch %s (status=%s)", batch_id, status)
            time.sleep(self._poll_interval)
            elapsed += self._poll_interval


def get_pipeline(config: Config) -> OpenAIPipeline:
    return OpenAIPipeline(
        api_key=config.openai_api_key,
        model=config.openai_model,
        proxy_url=config.proxy_url,
        use_batch=config.openai_batch,
    )


def generate_keypoints(
    text: str,
    file_name: str,
    config: Config,
    *,
    speaker_names: list[str] | None = None,
    usage: dict[str, int] | None = None,
) -> str:
    """Generate a Keypoints summary from a transcript via the configured pipeline."""
    pipeline = get_pipeline(config)
    result = pipeline.generate_keypoints(text, file_name, speaker_names=speaker_names)
    if usage is not None:
        usage.clear()
        usage.update(pipeline.last_usage)
    return result
