from __future__ import annotations

import io
import json
import logging
import time

from src.config import Config
from src.postprocess import extract_interlocutor_names
from src.stt.base import STTError

logger = logging.getLogger(__name__)

# LLM post-processing pipeline modeled on the `keypoints-transcription` skill: take a
# raw (usually diarized) STT transcript and return a corrected transcript with
# `Speaker N` labels mapped to the real interlocutor names and spurious extra speakers
# merged into the real ones, keeping every utterance verbatim.
INSTRUCTIONS = (
    "You are a transcript editor. You receive a raw speech-to-text transcript of a "
    "recorded conversation, usually diarized with `Speaker N:` labels.\n"
    "Your job:\n"
    "1. Map each `Speaker N` label to the real interlocutor name when the name is "
    "known (provided below) or unambiguous from context.\n"
    "2. Diarization often emits more labels than there are people. Merge a spurious "
    "extra speaker into the real speaker whose turns it continues.\n"
    "3. Keep the wording of every utterance verbatim - do not paraphrase, summarize, "
    "translate, or drop content. Only relabel speakers and tidy whitespace.\n"
    "4. Preserve any `[HH:MM:SS]` timestamps that prefix the lines.\n"
    "Return only the corrected transcript, with no preamble or explanation."
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
    """Compose the user prompt: name hints from the file name + the raw transcript."""
    names = speaker_names if speaker_names is not None else extract_interlocutor_names(file_name)
    if names:
        hint = (
            "Known interlocutor names (in no particular order): "
            + ", ".join(names)
            + "."
        )
    else:
        hint = (
            "The interlocutor names are unknown; keep the `Speaker N` labels if you "
            "cannot confidently infer them."
        )
    return f"{hint}\n\nRaw transcript:\n{transcript}"


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
        # Surface a failed request rather than silently uploading an empty/wrong
        # transcript over the sibling .txt: a per-line error or non-2xx status means
        # this request did not produce a usable result.
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
        raise STTError("OpenAI batch output contained no transcript text")
    return last


class OpenAIPipeline:
    """LLM post-processing over a transcript via the OpenAI Responses API.

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
                "OPENAI_API_KEY is required for the OpenAI post-processing pipeline"
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

    def refine(
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
            return self._refine_batch(prompt)
        return self._refine_sync(prompt)

    def _refine_sync(self, prompt: str) -> str:
        client = self._get_client()
        try:
            response = client.responses.create(
                model=self._model,
                instructions=INSTRUCTIONS,
                input=prompt,
            )
        except Exception as exc:
            raise STTError(f"OpenAI post-processing failed: {exc}") from exc
        self.last_usage = _normalize_usage(getattr(response, "usage", None))
        return _extract_output_text(response)

    def _refine_batch(self, prompt: str) -> str:
        client = self._get_client()
        request = {
            "custom_id": "transcript-0",
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
            raise STTError(f"OpenAI batch post-processing failed: {exc}") from exc
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


def refine_transcript(
    text: str,
    file_name: str,
    config: Config,
    *,
    speaker_names: list[str] | None = None,
    usage: dict[str, int] | None = None,
) -> str:
    """Run the configured OpenAI pipeline over a raw transcript."""
    pipeline = get_pipeline(config)
    result = pipeline.refine(text, file_name, speaker_names=speaker_names)
    if usage is not None:
        usage.clear()
        usage.update(pipeline.last_usage)
    return result
