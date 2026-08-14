from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, replace
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlparse

import yaml

from src import meta_entity
from src.presets import (
    BUILTIN_PRESETS,
    PACKAGED_PROMPT_ASSETS,
    Preset,
    default_artifact_suffix,
    load_packaged_prompt,
    merge_presets,
    validate_dag,
)

logger = logging.getLogger(__name__)

CONFIG_FILE_NAME = "config.yml"
# Subdirectory (relative to a config file) where ``config init`` copies the packaged
# prompt assets and where generated configs point their ``prompt_file`` entries by
# default.
PROMPTS_DIR_NAME = "prompts"
# Persistent bootstrap knob: ``GDSTT_HOME`` names the instance directory whose
# ``config.yml`` is the active config. When unset, the default home is ``./data``.
CONFIG_HOME_ENV_VAR = "GDSTT_HOME"
DEFAULT_CONFIG_HOME = Path("data")

SUPPORTED_STT_PROVIDERS = ("", "deepgram")
OUTPUT_TARGETS = ("drive", "folder")
DEEPGRAM_DIARIZE_MODELS = ("latest", "v1")
DEEPGRAM_AUDIO_SOURCES = ("m4a_copy", "mp3_96k", "mp3_192k")
DEEPGRAM_TXT_FORMATTERS = ("word_speaker", "utterance")
DEEPGRAM_DEFAULT_KEYTERMS_FILE = Path("deepgram-keyterms-example.txt")
DEEPGRAM_KEYTERMS_ASSET = "deepgram-keyterms-example.txt"
DEEPGRAM_MAX_KEYTERMS = 100

# Placeholder a preset prompt may carry to receive the configured meta entities --
# the response template plus each field's rules -- rendered at config load time.
# The built-in ``meta`` prompt uses it; any prompt may.
ENTITIES_PLACEHOLDER = "{{entities}}"


FOLDER_IDS_MIGRATION_ERROR = (
    "folder_ids is no longer supported; use\n"
    "  folders:\n"
    "    - folder_id: <id>\n"
    "      name: <employee name>\n"
    "      email: <employee email>"
)


@dataclass(frozen=True)
class EmployeeFolder:
    """One watched Drive folder and the employee it belongs to.

    ``name``/``email`` are optional: a folder whose employee is unknown still polls,
    and downstream consumers (the completion webhook) send empty strings for it.
    """

    folder_id: str
    name: str = ""
    email: str = ""


@dataclass(frozen=True)
class Config:
    folders: tuple[EmployeeFolder, ...]
    poll_interval: int
    bitrate: str
    data_dir: Path
    proxy_url: str
    stt_provider: str
    openai_api_key: str
    deepgram_api_key: str
    stt_language: str
    stt_postprocess: bool = True
    drive_mp3_artifact: bool = True
    # Runtime control flag for the polling loop. ``gdstt stop`` sets ``run.enabled``
    # to false in the config; the loop re-reads it each cycle and exits cleanly.
    run_enabled: bool = True
    output_target: str = "drive"
    output_dir: Path | None = None
    # Publish artifacts to Drive as well while keeping the local folder authoritative.
    # Flipping ``output.target`` to "drive" instead would make every already-processed
    # recording look unprocessed (the has_txt flag would stop coming from local files),
    # and the whole backlog would be re-transcribed at real cost.
    output_also_drive: bool = False
    # Which preset artifacts open the ``.stt`` document, in this order. Presets with no
    # artifact are skipped at assembly time rather than rejected here: a preset can be
    # disabled without invalidating the config.
    stt_presets: tuple[str, ...] = ("keypoints",)
    openai_keypoints: bool = False
    openai_model: str = "gpt-5.4-mini"
    openai_batch: bool = False
    openai_batch_wait: bool = True
    openai_max_parallel: int = 4
    deepgram_model: str = "nova-3"
    deepgram_diarize_model: str = "latest"
    deepgram_audio_source: str = "m4a_copy"
    deepgram_txt_formatter: str = "word_speaker"
    deepgram_keyterms_enabled: bool = True
    deepgram_keyterms_file: Path = DEEPGRAM_DEFAULT_KEYTERMS_FILE
    deepgram_keyterms: tuple[str, ...] = ()
    # Telegram error-notification credentials are config-owned (no env reads). When
    # either is blank, ``notify_error`` skips sending. See ``notifications.telegram``
    # in the generated config.yml.
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    # DEPRECATED: the allow-lists moved into ``meta.entities``. Still read for one
    # more version so a config written before that keeps working; a config that
    # declares ``meta.entities`` ignores these and gets a startup warning.
    tags_allowed: tuple[str, ...] = ()
    referrals_allowed: tuple[str, ...] = ()
    # What the ``meta`` preset extracts, in document order. See src/meta_entity.py.
    meta_entities: tuple[meta_entity.MetaEntity, ...] = ()
    # Completion-webhook target. A blank URL disables the webhook; the token, when
    # set, is sent as ``Authorization: Bearer <token>``. See ``webhook`` in the
    # generated config.yml.
    webhook_url: str = ""
    webhook_token: str = ""
    # Inbound call-booking receiver. Disabled by default: enabling it opens a
    # listening port, which a config written before this feature never asked for.
    call_booking_enabled: bool = False
    call_booking_listen_host: str = "0.0.0.0"
    call_booking_listen_port: int = 8080
    call_booking_token: str = ""
    call_booking_threshold_minutes: int = 15
    # When true, the polling loop refuses to transcribe a recording that matched no
    # booked call and marks it on Drive. Manual commands ignore this.
    call_booking_disable_recognition: bool = False
    # Planfix comment target. A blank URL disables the comment; ``planfix_presets``
    # names the preset artifacts concatenated into the comment body, in order.
    planfix_create_comment_url: str = ""
    planfix_token: str = ""
    planfix_presets: tuple[str, ...] = ("keypoints",)
    # Which meta-document fields open the Planfix comment, in this order. The models and
    # internal ids are deliberately absent: the comment is read by managers, not
    # operators.
    planfix_meta_fields: tuple[str, ...] = (
        "subject", "tags", "referral", "referral_note",
        "case_deadline", "deadlines", "target_filing",
        "duration", "video_url",
    )
    # Where a task lives in the web UI, e.g.
    # ``https://tagilcity.planfix.com/task/<task-id>``. The account name is part of the
    # host, so this cannot be derived from the comment webhook URL. Blank leaves
    # ``planfix_task_url`` empty in the meta document rather than guessing a host.
    planfix_task_url: str = ""
    presets: tuple[Preset, ...] = ()
    # Google OAuth is config-owned and inline-first. ``google_credentials``/
    # ``google_token`` hold inline mappings (the OAuth client JSON and the saved
    # token); the ``*_file`` paths point at on-disk copies instead. When all four
    # are unset the loaders fall back to ``data_dir/credentials.json`` and
    # ``data_dir/token.json`` for back-compat. The config file path is carried so
    # that refreshing an inline token can be persisted back into the YAML.
    google_credentials: dict | None = None
    google_token: dict | None = None
    google_credentials_file: Path | None = None
    google_token_file: Path | None = None
    config_file: Path | None = None

    def folder_by_id(self, folder_id: str) -> EmployeeFolder | None:
        """Return the folder with ``folder_id``, or None when it isn't configured."""
        for folder in self.folders:
            if folder.folder_id == folder_id:
                return folder
        return None

    @property
    def call_bookings_file(self) -> Path:
        """Where the booking journal lives: alongside the active config file.

        The config file already resolves to the instance directory (``GDSTT_HOME``,
        the mounted volume under Docker), so the journal survives container restarts
        without a second path knob.
        """
        base = self.config_file.parent if self.config_file else self.data_dir
        return base / "call_bookings.jsonl"


class UniqueKeyLoader(yaml.SafeLoader):
    """A ``SafeLoader`` that rejects duplicate keys in any YAML mapping.

    PyYAML's default loader silently keeps the last value when a mapping repeats a
    key, which would let ``config.yml`` hide a second preset under the same name (or
    a second top-level key) without warning. This loader raises a ``ValueError`` the
    moment a duplicate key is constructed, covering nested maps such as two presets
    sharing a name under ``presets:``.
    """

    def construct_mapping(self, node, deep=False):  # type: ignore[override]
        mapping: dict = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in mapping:
                raise ValueError(
                    f"duplicate key {key!r} in {CONFIG_FILE_NAME} mapping "
                    f"(line {key_node.start_mark.line + 1})"
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def _parse_config_yaml(text: str) -> object:
    """Parse config YAML text, rejecting duplicate mapping keys at parse time."""
    return yaml.load(text, Loader=UniqueKeyLoader)


def _parse_folders(raw: object) -> tuple[EmployeeFolder, ...]:
    """Parse the ``folders:`` block into ``EmployeeFolder`` entries.

    Each entry must be a mapping carrying a non-empty, unique ``folder_id``; ``name``
    and ``email`` are optional and default to empty strings.
    """
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise ValueError(
            "folders must be a list of {folder_id, name, email} mappings, "
            f"got: {raw!r}"
        )
    folders: list[EmployeeFolder] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(
                f"folders[{index}] must be a mapping with folder_id/name/email, "
                f"got: {entry!r}"
            )
        folder_id = _yaml_str(entry.get("folder_id"))
        if not folder_id:
            raise ValueError(f"folders[{index}] must define a non-empty folder_id")
        if any(folder.folder_id == folder_id for folder in folders):
            # Otherwise the folder is polled once per entry and folder_by_id silently
            # attributes every file in it to whichever employee was listed first.
            raise ValueError(
                f"folders[{index}] repeats folder_id {folder_id!r}; "
                "each folder must be listed once"
            )
        folders.append(
            EmployeeFolder(
                folder_id=folder_id,
                name=_yaml_str(entry.get("name")),
                email=_yaml_str(entry.get("email")),
            )
        )
    return tuple(folders)


def _validate_call_booking(
    *,
    enabled: bool,
    token: str,
    disable_recognition: bool,
    folders: tuple[EmployeeFolder, ...],
) -> None:
    """Reject call-booking settings that would fail silently at runtime.

    Both cases are quiet in production and expensive to diagnose: an open endpoint
    that accepts anyone's bookings, and a folder that can never match a booking and so
    would never be transcribed again.
    """
    if enabled and not token.strip():
        raise ValueError(
            "call_booking.enabled is true but call_booking.authorization_token is "
            "empty; the receiver would accept unauthenticated bookings"
        )
    if not disable_recognition:
        return
    emailless = [f.folder_id for f in folders if not f.email.strip()]
    if emailless:
        raise ValueError(
            "call_booking.disable_recognition is true, so every folder must have an "
            "email to match bookings against; these do not: "
            + ", ".join(emailless)
        )


def _parse_tags_allowed(raw: object) -> tuple[str, ...]:
    """Parse the ``tags.allowed`` list into a tuple of non-empty tag names."""
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise ValueError(f"tags.allowed must be a list of tags, got: {raw!r}")
    return tuple(tag for tag in (_yaml_str(entry) for entry in raw) if tag)


def _parse_referrals_allowed(raw: object) -> tuple[str, ...]:
    """Parse the ``referrals.allowed`` list into a tuple of non-empty channel names."""
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise ValueError(f"referrals.allowed must be a list of channels, got: {raw!r}")
    return tuple(name for name in (_yaml_str(entry) for entry in raw) if name)


def _render_prompt_placeholders(
    text: str, entities: tuple[meta_entity.MetaEntity, ...] = ()
) -> str:
    """Substitute the supported ``{{...}}`` placeholders in a resolved prompt.

    Today that is ``{{entities}}`` (the ``meta`` preset's). A prompt without it is
    returned unchanged, so this is safe to run over every preset's text.
    """
    if ENTITIES_PLACEHOLDER in text:
        text = text.replace(
            ENTITIES_PLACEHOLDER, meta_entity.render_entities_block(entities)
        )
    return text


def _resolve_prompt_text(
    preset: Preset,
    config_file: Path | None,
    entities: tuple[meta_entity.MetaEntity, ...] = (),
) -> str:
    """Resolve a preset's final prompt text from instructions or prompt_file.

    Resolution priority: inline ``instructions`` win; otherwise ``prompt_file`` is
    resolved to text in this order — the path as written if readable on this OS,
    then ``<config_dir>/<prompt_file>`` relative to the config file's parent (when a
    config file exists), then the packaged asset by base name. A ``prompt_file``
    that resolves but is missing/unreadable/empty raises ``ValueError``; a preset
    with neither instructions nor prompt_file also raises.

    The resolved text has its ``{{...}}`` placeholders rendered from ``entities``
    before it is returned, so the pipeline never sees an unrendered prompt.
    """
    if preset.instructions.strip():
        return _render_prompt_placeholders(preset.instructions, entities)
    if not preset.prompt_file:
        raise ValueError(
            f"preset {preset.name!r} must define instructions or prompt_file"
        )

    candidates: list[Path] = [Path(preset.prompt_file)]
    if config_file is not None:
        candidates.append(config_file.parent / preset.prompt_file)
    for candidate in candidates:
        if candidate.is_file():
            text = candidate.read_text(encoding="utf-8-sig")
            if not text.strip():
                raise ValueError(
                    f"preset {preset.name!r} prompt_file {preset.prompt_file!r} "
                    f"is empty: {candidate}"
                )
            return _render_prompt_placeholders(text, entities)

    try:
        text = load_packaged_prompt(os.path.basename(preset.prompt_file))
    except ValueError as exc:
        raise ValueError(
            f"preset {preset.name!r} prompt_file {preset.prompt_file!r} "
            f"could not be resolved: {exc}"
        ) from exc
    return _render_prompt_placeholders(text, entities)


def _resolve_presets(
    config_presets: dict | None,
    config_file: Path | None = None,
    entities: tuple[meta_entity.MetaEntity, ...] = (),
) -> tuple[Preset, ...]:
    """Merge config presets over built-ins, resolve prompts, validate, and freeze."""
    merged = merge_presets(BUILTIN_PRESETS, config_presets)
    resolved = {
        name: replace(
            preset,
            instructions=_resolve_prompt_text(preset, config_file, entities),
        )
        for name, preset in merged.items()
    }
    validate_dag(resolved)
    _validate_unique_artifact_suffixes(resolved.values())
    return tuple(resolved.values())


def _validate_unique_artifact_suffixes(presets: object) -> None:
    """Reject two enabled presets that would write the same sibling artifact.

    Presets may share a ``prompt_file`` but must differ in ``name`` (already enforced
    by the mapping) and in ``artifact_suffix`` so their outputs don't collide on
    disk. ``merge_presets`` returns only enabled presets, so every preset passed here
    is enabled.
    """
    seen: dict[str, str] = {}
    for preset in presets:
        owner = seen.get(preset.artifact_suffix)
        if owner is not None:
            raise ValueError(
                f"presets {owner!r} and {preset.name!r} both use artifact_suffix "
                f"{preset.artifact_suffix!r}; enabled presets must use distinct suffixes"
            )
        seen[preset.artifact_suffix] = preset.name


def _load_deepgram_api_key(api_key: str, api_key_file: str) -> str:
    api_key = api_key.strip()
    if api_key:
        return api_key

    api_key_file = api_key_file.strip()
    if not api_key_file:
        return ""

    path = Path(api_key_file)
    try:
        raw = path.read_text(encoding="utf-8-sig").strip()
    except OSError as exc:
        raise ValueError(f"DEEPGRAM_API_KEY_FILE could not be read: {path}") from exc
    if not raw:
        return ""

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw

    if isinstance(parsed, str):
        return parsed.strip()
    if isinstance(parsed, dict):
        for key in ("api_key", "deepgram_api_key", "DEEPGRAM_API_KEY"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""
    return ""


def _parse_max_parallel(raw: object, *, default: int) -> int:
    """Parse a positive ``openai.max_parallel`` worker cap (env string or YAML int)."""
    if raw is None:
        return default
    # bool is an int subclass: int(True) == 1 would silently accept a YAML bool.
    if isinstance(raw, bool):
        raise ValueError(f"openai.max_parallel must be an integer, got: {raw!r}")
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return default
        raw = text
    if isinstance(raw, float) and not raw.is_integer():
        raise ValueError(f"openai.max_parallel must be an integer, got: {raw!r}")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"openai.max_parallel must be an integer, got: {raw!r}"
        ) from exc
    if value <= 0:
        raise ValueError(f"openai.max_parallel must be positive, got: {value}")
    return value


def _parse_bool(raw: str, *, default: bool) -> bool:
    value = raw.strip().lower()
    if not value:
        return default
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Expected boolean value, got: {raw!r}")


def _load_deepgram_keyterms(
    enabled: bool, keyterms_file: Path, *, explicit: bool = True
) -> tuple[str, ...]:
    if not enabled:
        return ()

    try:
        raw_lines = keyterms_file.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        # Keyterms default to on, but the default file only exists because
        # `config init` copies the example beside the config. A config that predates
        # that (or one whose operator deleted the sample, as the README suggests)
        # must still start: keyterm prompting is an optimisation, not a requirement.
        # An explicitly configured path that cannot be read stays a hard error.
        if not explicit:
            logger.warning(
                "stt.deepgram.keyterms_file %s not found; continuing without keyterm "
                "prompting. Run `gdstt config init` or set stt.deepgram.keyterms_file.",
                keyterms_file,
            )
            return ()
        raise ValueError(
            f"stt.deepgram.keyterms_file could not be read: {keyterms_file}"
        ) from exc

    keyterms = tuple(
        line.strip()
        for line in raw_lines
        if line.strip() and not line.lstrip().startswith("#")
    )
    if len(keyterms) > DEEPGRAM_MAX_KEYTERMS:
        raise ValueError(
            f"stt.deepgram.keyterms_file may contain at most {DEEPGRAM_MAX_KEYTERMS} "
            f"keyterms, got: {len(keyterms)}"
        )
    return keyterms



def _resolve_relative_to(raw: str, base: Path) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path
    return base / path


def _resolve_google_auth(
    google: dict,
    base: Path | None,
) -> tuple[dict | None, dict | None, Path | None, Path | None]:
    """Resolve the ``google:`` block into (credentials, token, creds_file, token_file).

    Inline ``credentials``/``token`` mappings win; otherwise ``credentials_file``/
    ``token_file`` paths are returned (resolved relative to ``base`` — the config
    file's parent — unless absolute). Supplying BOTH an inline mapping and a file
    pointer for the same object is rejected so generated configs and ``config set``
    writes never carry an ambiguous source.
    """
    credentials = google.get("credentials")
    if credentials is not None and not isinstance(credentials, dict):
        raise ValueError("google.credentials must be a mapping in " + CONFIG_FILE_NAME)
    token = google.get("token")
    if token is not None and not isinstance(token, dict):
        raise ValueError("google.token must be a mapping in " + CONFIG_FILE_NAME)

    credentials_file_raw = _yaml_str(google.get("credentials_file"))
    token_file_raw = _yaml_str(google.get("token_file"))

    if credentials is not None and credentials_file_raw:
        raise ValueError(
            "google.credentials and google.credentials_file are both set; "
            "use inline credentials or a file, not both."
        )
    if token is not None and token_file_raw:
        raise ValueError(
            "google.token and google.token_file are both set; "
            "use an inline token or a file, not both."
        )

    def _resolve(raw: str) -> Path:
        return _resolve_relative_to(raw, base) if base is not None else Path(raw)

    credentials_file = _resolve(credentials_file_raw) if credentials_file_raw else None
    token_file = _resolve(token_file_raw) if token_file_raw else None
    return credentials, token, credentials_file, token_file


def _expand_config_home(raw: str) -> Path:
    expanded = os.path.expanduser(os.path.expandvars(raw.strip()))
    return Path(expanded)


def _resolve_config_file_path(config_path: str | Path | None = None) -> Path:
    """Resolve the active ``config.yml`` path from the directory-home model.

    Priority: an explicit ``--config`` file override wins; otherwise the active
    config is ``<GDSTT_HOME>/config.yml`` when ``GDSTT_HOME`` is set (with ``~`` and
    ``$VAR`` expansion), falling back to ``./data/config.yml`` when it is unset. No
    ``.env`` is read and no OS-default user path is consulted.
    """
    if config_path:
        return Path(config_path)
    home_raw = os.environ.get(CONFIG_HOME_ENV_VAR, "").strip()
    home = _expand_config_home(home_raw) if home_raw else DEFAULT_CONFIG_HOME
    return home / CONFIG_FILE_NAME


def resolve_config_file_path(config_path: str | Path | None = None) -> Path:
    """Public resolver for the active ``config.yml`` path (``--config`` or home)."""
    return _resolve_config_file_path(config_path)


def _as_mapping(value: object, label: str) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    raise ValueError(f"{label} must be a mapping in {CONFIG_FILE_NAME}")


def _yaml_str(value: object, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _yaml_bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return _parse_bool(value, default=default)
    raise ValueError(f"Expected boolean value, got: {value!r}")


def _parse_positive_int(value: object, *, default: int, name: str) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer, got: {value!r}") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be positive, got: {parsed}")
    return parsed


def _parse_planfix_presets(value: object) -> tuple[str, ...]:
    """Read ``planfix.presets``, defaulting to the single ``keypoints`` preset."""
    if value is None:
        return ("keypoints",)
    if not isinstance(value, list):
        raise ValueError(f"planfix.presets must be a list, got: {value!r}")
    names = []
    for entry in value:
        name = _yaml_str(entry)
        if not name:
            raise ValueError(f"planfix.presets entries must be names, got: {entry!r}")
        names.append(name)
    return tuple(names)


def _parse_planfix_meta_fields(value: object) -> tuple[str, ...]:
    """Read ``planfix.meta_fields``, defaulting to the built-in field selection.

    Unlike ``_parse_planfix_presets``, an explicit empty list is honored as "no
    header" rather than falling back to the default: only an absent key (``None``)
    means the operator hasn't set an opinion.
    """
    if value is None:
        return (
            "subject", "tags", "referral", "referral_note",
            "case_deadline", "deadlines", "target_filing",
            "duration", "video_url",
        )
    if not isinstance(value, list):
        raise ValueError(f"planfix.meta_fields must be a list, got: {value!r}")
    names = []
    for entry in value:
        name = _yaml_str(entry)
        if not name:
            raise ValueError(f"planfix.meta_fields entries must be names, got: {entry!r}")
        names.append(name)
    return tuple(names)


def _parse_stt_presets(value: object) -> tuple[str, ...]:
    """Read ``output.stt_presets``, defaulting to the single ``keypoints`` preset."""
    if value is None:
        return ("keypoints",)
    if not isinstance(value, list):
        raise ValueError(f"output.stt_presets must be a list, got: {value!r}")
    names = []
    for entry in value:
        name = _yaml_str(entry)
        if not name:
            raise ValueError(f"output.stt_presets entries must be names, got: {entry!r}")
        names.append(name)
    return tuple(names)


def _validate_webhook_url(url: str) -> None:
    """Reject a webhook URL that could never be delivered.

    Delivery is fire-and-forget with no retry and ``notify_complete`` swallows every
    exception, so a typo like a missing scheme would otherwise load clean and then
    drop every notification with the only trace in a per-file warning. Unlike the
    plaintext case below, this is a config error rather than an operator choice.
    """
    target = url.strip()
    if not target:
        return
    parsed = urlparse(target)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(
            "webhook.url must be an absolute http:// or https:// URL, got: "
            f"{url!r}"
        )


def _warn_on_plaintext_webhook(url: str) -> None:
    """Warn once at load when the completion webhook targets a plaintext receiver.

    The payload carries PII (employee email plus the full transcript) and the request
    carries the bearer token, so an ``http://`` receiver exposes all of it to any
    on-path observer. Loopback is exempt — it never leaves the host — and this warns
    rather than raises because internal plaintext receivers are a legitimate (if
    unwise) operator choice, not a config error we should refuse to start on.
    """
    target = url.strip()
    if not target:
        return
    parsed = urlparse(target)
    if parsed.scheme != "http":
        return
    if (parsed.hostname or "") in {"localhost", "127.0.0.1", "::1"}:
        return
    logger.warning(
        "webhook.url uses http://; the bearer token and the transcript payload "
        "(including the employee email) will cross the network in clear text. "
        "Use https:// unless the receiver is loopback."
    )


def _config_from_yaml(
    raw: dict,
    config_file: Path,
    *,
    validate_providers: bool = True,
) -> Config:
    """Build a Config from the grouped `data/config.yml` schema."""
    base = config_file.parent
    output = _as_mapping(raw.get("output"), "output")
    stt = _as_mapping(raw.get("stt"), "stt")
    deepgram = _as_mapping(stt.get("deepgram"), "stt.deepgram")
    openai = _as_mapping(raw.get("openai"), "openai")
    google = _as_mapping(raw.get("google"), "google")
    run = _as_mapping(raw.get("run"), "run")
    notifications = _as_mapping(raw.get("notifications"), "notifications")
    telegram = _as_mapping(notifications.get("telegram"), "notifications.telegram")
    tags = _as_mapping(raw.get("tags"), "tags")
    referrals = _as_mapping(raw.get("referrals"), "referrals")
    meta = _as_mapping(raw.get("meta"), "meta")
    webhook = _as_mapping(raw.get("webhook"), "webhook")
    call_booking = _as_mapping(raw.get("call_booking"), "call_booking")
    planfix = _as_mapping(raw.get("planfix"), "planfix")
    config_presets = _as_mapping(raw.get("presets"), "presets")

    run_enabled = _yaml_bool(run.get("enabled"), default=True)

    telegram_bot_token = _yaml_str(telegram.get("bot_token"))
    telegram_chat_id = _yaml_str(telegram.get("chat_id"))

    tags_allowed = _parse_tags_allowed(tags.get("allowed"))
    referrals_allowed = _parse_referrals_allowed(referrals.get("allowed"))

    raw_entities = meta.get("entities")
    if raw_entities is not None and (tags_allowed or referrals_allowed):
        logger.warning(
            "meta.entities is declared, so the deprecated top-level tags.allowed / "
            "referrals.allowed are ignored; move those values into the entities' "
            "allowed lists and delete the old keys"
        )
    meta_entities = meta_entity.parse_entities(
        raw_entities, tags_allowed=tags_allowed, referrals_allowed=referrals_allowed
    )

    webhook_url = _yaml_str(webhook.get("url"))
    webhook_token = _yaml_str(webhook.get("token"))
    _validate_webhook_url(webhook_url)
    _warn_on_plaintext_webhook(webhook_url)

    call_booking_enabled = _yaml_bool(call_booking.get("enabled"), default=False)
    call_booking_listen_host = (
        _yaml_str(call_booking.get("listen_host"), "0.0.0.0") or "0.0.0.0"
    )
    call_booking_listen_port = _parse_positive_int(
        call_booking.get("listen_port"), default=8080, name="call_booking.listen_port"
    )
    call_booking_token = _yaml_str(call_booking.get("authorization_token"))
    call_booking_threshold_minutes = _parse_positive_int(
        call_booking.get("threshold_minutes"),
        default=15,
        name="call_booking.threshold_minutes",
    )
    call_booking_disable_recognition = _yaml_bool(
        call_booking.get("disable_recognition"), default=False
    )

    planfix_create_comment_url = _yaml_str(planfix.get("create_comment_url"))
    planfix_token = _yaml_str(planfix.get("token"))
    planfix_presets = _parse_planfix_presets(planfix.get("presets"))
    planfix_meta_fields = _parse_planfix_meta_fields(planfix.get("meta_fields"))
    planfix_task_url = _yaml_str(planfix.get("task_url"))

    (
        google_credentials,
        google_token,
        google_credentials_file,
        google_token_file,
    ) = _resolve_google_auth(google, base)

    # Clean break: a config still on the old flat list must be rewritten by hand so
    # each folder gains its employee, rather than silently polling nameless folders.
    if "folder_ids" in raw:
        raise ValueError(FOLDER_IDS_MIGRATION_ERROR)
    folders = _parse_folders(raw.get("folders"))
    _validate_call_booking(
        enabled=call_booking_enabled,
        token=call_booking_token,
        disable_recognition=call_booking_disable_recognition,
        folders=folders,
    )

    poll_raw = raw.get("poll_interval", 600)
    try:
        poll_interval = int(poll_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"poll_interval must be an integer, got: {poll_raw!r}") from exc
    if poll_interval <= 0:
        raise ValueError(f"poll_interval must be positive, got: {poll_interval}")

    bitrate = _yaml_str(raw.get("bitrate"), "96k") or "96k"
    data_dir = _resolve_relative_to(
        _yaml_str(raw.get("data_dir"), ".") or ".", base
    )
    proxy_url = _yaml_str(raw.get("proxy_url"))

    stt_provider = _yaml_str(stt.get("provider"), "deepgram").lower()
    if stt_provider == "disabled":
        stt_provider = ""
    if stt_provider not in SUPPORTED_STT_PROVIDERS:
        raise ValueError(
            f"stt.provider must be one of {SUPPORTED_STT_PROVIDERS!r}, got: {stt_provider!r}"
        )

    stt_language = _yaml_str(stt.get("language"))
    if stt_provider == "deepgram" and not stt_language:
        stt_language = "ru"
    stt_postprocess = _yaml_bool(stt.get("postprocess"), default=True)

    openai_api_key = _yaml_str(openai.get("api_key"))
    openai_model = _yaml_str(openai.get("model"), "gpt-5.4-mini") or "gpt-5.4-mini"
    openai_keypoints = _yaml_bool(openai.get("keypoints"), default=False)
    openai_batch = _yaml_bool(openai.get("batch"), default=False)
    openai_batch_wait = _yaml_bool(openai.get("batch_wait"), default=True)
    openai_max_parallel = _parse_max_parallel(openai.get("max_parallel"), default=4)
    presets = _resolve_presets(config_presets, config_file, meta_entities)

    deepgram_api_key = ""
    deepgram_model = _yaml_str(deepgram.get("model"), "nova-3") or "nova-3"
    deepgram_diarize_model = (
        _yaml_str(deepgram.get("diarize_model"), "latest").lower() or "latest"
    )
    deepgram_audio_source = (
        _yaml_str(deepgram.get("audio_source"), "m4a_copy").lower() or "m4a_copy"
    )
    deepgram_txt_formatter = (
        _yaml_str(deepgram.get("txt_formatter"), "word_speaker").lower() or "word_speaker"
    )
    deepgram_keyterms_enabled = _yaml_bool(
        deepgram.get("keyterms_enabled"), default=True
    )
    deepgram_keyterms_raw = _yaml_str(deepgram.get("keyterms_file"))
    # `config init` writes the default example path verbatim, so a present value that
    # merely repeats the default is not an operator choice: it gets the same
    # missing-file-warns-and-continues path as an omitted value.
    deepgram_keyterms_explicit = (
        bool(deepgram_keyterms_raw.strip())
        and deepgram_keyterms_raw.strip() != DEEPGRAM_DEFAULT_KEYTERMS_FILE.as_posix()
    )
    deepgram_keyterms_file = _resolve_relative_to(
        deepgram_keyterms_raw or str(DEEPGRAM_DEFAULT_KEYTERMS_FILE),
        base,
    )
    deepgram_keyterms: tuple[str, ...] = ()

    drive_mp3_artifact_default = not (
        stt_provider == "deepgram" and deepgram_audio_source == "m4a_copy"
    )
    drive_mp3_artifact = _yaml_bool(
        stt.get("drive_mp3_artifact"), default=drive_mp3_artifact_default
    )

    output_target = _yaml_str(output.get("target"), "drive").lower() or "drive"
    if output_target not in OUTPUT_TARGETS:
        raise ValueError(
            f"output.target must be one of {OUTPUT_TARGETS!r}, got: {output_target!r}"
        )
    output_dir_raw = _yaml_str(output.get("dir"))
    output_dir = _resolve_relative_to(output_dir_raw, base) if output_dir_raw else None
    if output_target == "folder" and output_dir is None:
        raise ValueError("output.dir is required when output.target=folder")
    output_also_drive = _yaml_bool(output.get("also_drive"), default=False)
    if output_also_drive and output_target != "folder":
        raise ValueError(
            "output.also_drive only applies when output.target=folder; "
            "target=drive already writes to Drive"
        )
    stt_presets = _parse_stt_presets(output.get("stt_presets"))

    if validate_providers:
        if presets and not openai_api_key:
            raise ValueError(
                "openai.api_key is required when any OpenAI preset is enabled"
            )
        if stt_provider == "deepgram":
            api_key_file = _yaml_str(deepgram.get("api_key_file"))
            deepgram_api_key = _load_deepgram_api_key(
                _yaml_str(deepgram.get("api_key")),
                str(_resolve_relative_to(api_key_file, base)) if api_key_file else "",
            )
            if not deepgram_api_key:
                raise ValueError(
                    "deepgram.api_key is required when stt.provider=deepgram. "
                    "Add stt.deepgram.api_key to your config.yml."
                )
            if deepgram_diarize_model not in DEEPGRAM_DIARIZE_MODELS:
                raise ValueError(
                    f"stt.deepgram.diarize_model must be one of "
                    f"{DEEPGRAM_DIARIZE_MODELS!r}, got: {deepgram_diarize_model!r}"
                )
            if deepgram_audio_source not in DEEPGRAM_AUDIO_SOURCES:
                raise ValueError(
                    f"stt.deepgram.audio_source must be one of "
                    f"{DEEPGRAM_AUDIO_SOURCES!r}, got: {deepgram_audio_source!r}"
                )
            if deepgram_txt_formatter not in DEEPGRAM_TXT_FORMATTERS:
                raise ValueError(
                    f"stt.deepgram.txt_formatter must be one of "
                    f"{DEEPGRAM_TXT_FORMATTERS!r}, got: {deepgram_txt_formatter!r}"
                )
            deepgram_keyterms = _load_deepgram_keyterms(
                deepgram_keyterms_enabled,
                deepgram_keyterms_file,
                explicit=deepgram_keyterms_explicit,
            )

    return Config(
        folders=folders,
        poll_interval=poll_interval,
        bitrate=bitrate,
        data_dir=data_dir,
        proxy_url=proxy_url,
        stt_provider=stt_provider,
        openai_api_key=openai_api_key,
        deepgram_api_key=deepgram_api_key,
        stt_language=stt_language,
        stt_postprocess=stt_postprocess,
        drive_mp3_artifact=drive_mp3_artifact,
        run_enabled=run_enabled,
        output_target=output_target,
        output_dir=output_dir,
        output_also_drive=output_also_drive,
        stt_presets=stt_presets,
        openai_keypoints=openai_keypoints,
        openai_model=openai_model,
        openai_batch=openai_batch,
        openai_batch_wait=openai_batch_wait,
        openai_max_parallel=openai_max_parallel,
        deepgram_model=deepgram_model,
        deepgram_diarize_model=deepgram_diarize_model,
        deepgram_audio_source=deepgram_audio_source,
        deepgram_txt_formatter=deepgram_txt_formatter,
        deepgram_keyterms_enabled=deepgram_keyterms_enabled,
        deepgram_keyterms_file=deepgram_keyterms_file,
        deepgram_keyterms=deepgram_keyterms,
        telegram_bot_token=telegram_bot_token,
        telegram_chat_id=telegram_chat_id,
        tags_allowed=tags_allowed,
        referrals_allowed=referrals_allowed,
        meta_entities=meta_entities,
        webhook_url=webhook_url,
        webhook_token=webhook_token,
        call_booking_enabled=call_booking_enabled,
        call_booking_listen_host=call_booking_listen_host,
        call_booking_listen_port=call_booking_listen_port,
        call_booking_token=call_booking_token,
        call_booking_threshold_minutes=call_booking_threshold_minutes,
        call_booking_disable_recognition=call_booking_disable_recognition,
        planfix_create_comment_url=planfix_create_comment_url,
        planfix_token=planfix_token,
        planfix_presets=planfix_presets,
        planfix_meta_fields=planfix_meta_fields,
        planfix_task_url=planfix_task_url,
        presets=presets,
        google_credentials=google_credentials,
        google_token=google_token,
        google_credentials_file=google_credentials_file,
        google_token_file=google_token_file,
        config_file=config_file,
    )


def _relpath_for_config(path: Path | None, config_file: Path | None) -> str | None:
    """Serialize a filesystem path so it round-trips through ``config.yml``.

    ``_config_from_yaml`` resolves relative paths against the config file's parent
    directory, so any path written to the YAML must be expressed relative to that
    directory. Writing the bare ``str(path)`` instead made paths like
    ``output.dir`` and ``deepgram.keyterms_file`` re-resolve under ``<data_dir>``
    on the next load (e.g. ``out`` -> ``data/out``), breaking the second run.
    """
    if path is None:
        return None
    if config_file is None:
        return Path(path).as_posix()
    try:
        return Path(os.path.relpath(path, config_file.parent)).as_posix()
    except ValueError:
        # Windows raises when path and base sit on different drives; fall back to the
        # absolute path (still POSIX-normalized for portable YAML).
        return Path(path).as_posix()


def _default_prompt_file(name: str) -> str:
    """Return the default ``prompt_file`` for a preset (``prompts/<name>.md``).

    Always uses ``/`` separators so generated YAML stays portable across OSes; the
    loader resolves it relative to the config file's parent directory.
    """
    return f"{PROMPTS_DIR_NAME}/{name}.md"


def _preset_to_yaml_entry(preset: Preset) -> dict:
    """Serialize one resolved preset into a ``presets:`` entry.

    The inline ``instructions`` carried by a resolved preset are deliberately not
    written back; the prompt text is owned by the ``prompt_file`` (.md asset) so the
    YAML stays a thin pointer. Non-default ``depends_on``/``model``/``batch``/
    ``artifact_suffix`` are emitted only when they diverge from the built-in defaults.
    """
    entry: dict[str, object] = {"enabled": preset.enabled}
    # The prompt text is owned by an .md asset copied beside the config under
    # ``prompts/``. A bare basename (the built-in default, e.g. ``keypoints.md``) is
    # rewritten to ``prompts/<name>.md`` so it points at the copied asset; an explicit
    # path with directories is preserved as-is (already POSIX-normalized below).
    prompt_file = preset.prompt_file or _default_prompt_file(preset.name)
    if "/" not in prompt_file and "\\" not in prompt_file:
        prompt_file = f"{PROMPTS_DIR_NAME}/{prompt_file}"
    entry["prompt_file"] = prompt_file
    if preset.depends_on:
        entry["depends_on"] = list(preset.depends_on)
    if preset.model is not None:
        entry["model"] = preset.model
    if preset.batch is not None:
        entry["batch"] = preset.batch
    # batch_wait is omitted unless explicitly set so the generated YAML stays a thin
    # pointer that inherits the global openai.batch_wait default.
    if preset.batch_wait is not None:
        entry["batch_wait"] = preset.batch_wait
    if preset.artifact_suffix != default_artifact_suffix(preset.name):
        entry["artifact_suffix"] = preset.artifact_suffix
    return entry


def _presets_to_yaml_dict(config: Config) -> dict:
    """Build the ``presets:`` block for a serialized Config.

    ``config.presets`` only holds enabled presets, so the built-in ``keypoints``
    pass is emitted explicitly (with ``enabled: false`` but a real ``prompt_file``)
    whenever it is disabled, keeping the default chain one edit away from re-enabled.
    """
    presets: dict[str, dict] = {}
    for preset in config.presets:
        presets[preset.name] = _preset_to_yaml_entry(preset)
    for builtin in BUILTIN_PRESETS:
        if builtin.name not in presets:
            presets[builtin.name] = {
                "enabled": False,
                "prompt_file": _default_prompt_file(builtin.name),
            }
    return presets


def _default_config_dict(
    *,
    data_dir: str | None = None,
    output_target: str | None = None,
    output_dir: str | None = None,
    prompt_dir: str | None = None,
) -> dict:
    """Build a full default ``config.yml`` mapping for ``config init``/``link``.

    The default preset chain is
    ``transcript-cleanup -> keypoints + meta`` with all three presets
    enabled; every prompt_file uses ``/``-style relative
    paths so the generated YAML is portable. ``prompt_dir`` (when given) is a
    ``/``-joined path the prompts were copied to and that the prompt_file entries
    point at; otherwise the default ``prompts/<name>.md`` layout is used.
    ``data_dir``/``output_*`` override the matching fields. ``action-items`` is
    retired: its output duplicates ``keypoints``' ``## Задачи`` section, so it is
    left out of the generated chain. Its prompt asset stays packaged; re-enabling
    it is a config edit (add an ``action-items`` entry under ``presets``), not a
    code change.
    """
    def prompt_path(name: str) -> str:
        if prompt_dir:
            return f"{prompt_dir.rstrip('/')}/{name}.md"
        return _default_prompt_file(name)

    # Default chain (all enabled out of the box): transcript-cleanup runs first and
    # keypoints and meta both depend on it. Order matters in the generated YAML, so
    # transcript-cleanup is written above its dependents.
    presets: dict[str, dict] = {
        "transcript-cleanup": {
            "enabled": True,
            "prompt_file": prompt_path("transcript-cleanup"),
        },
        "keypoints": {
            "enabled": True,
            "depends_on": ["transcript-cleanup"],
            "prompt_file": prompt_path("keypoints"),
        },
        "meta": {
            "enabled": True,
            "depends_on": ["transcript-cleanup"],
            "prompt_file": prompt_path("meta"),
        },
    }

    config: dict[str, object] = {
        "folders": [],
        "poll_interval": 600,
        "bitrate": "96k",
        "data_dir": data_dir or ".",
        "proxy_url": "",
        "output": {
            "target": (output_target or "drive"),
            "dir": output_dir,
            "stt_presets": ["keypoints"],
        },
        "stt": {
            "provider": "deepgram",
            "language": "ru",
            "postprocess": True,
            "deepgram": {
                "api_key": "",
                "model": "nova-3",
                "diarize_model": "latest",
                "audio_source": "m4a_copy",
                "txt_formatter": "word_speaker",
                "keyterms_enabled": True,
                "keyterms_file": DEEPGRAM_DEFAULT_KEYTERMS_FILE.as_posix(),
            },
        },
        "openai": {
            "api_key": "",
            "model": "gpt-5.4-mini",
            "batch": True,
            "max_parallel": 4,
        },
        "run": {"enabled": True},
        "notifications": {
            "telegram": {
                "bot_token": "",
                "chat_id": "",
            },
        },
        # The seven entities a freshly generated config ships extracting. An existing
        # deployment upgrading in place does not get these silently: `parse_entities`
        # falls back to the four-entity `default_entities()` when `meta.entities` is
        # absent, wired to whatever the old top-level tags.allowed/referrals.allowed
        # held. Only a config generated from scratch gets all seven.
        "meta": {
            "entities": [
                {
                    "name": "subject",
                    "type": "text",
                    "label": "",
                    "prompt": (
                        "Одно предложение о том, про что был звонок. Опирайся "
                        "строго на транскрипт, ничего не выдумывай."
                    ),
                },
                {
                    "name": "tags",
                    "type": "enum",
                    "multiple": True,
                    "label": "Теги",
                    "allowed": [],
                    "prompt": (
                        "Выбери все теги, которые действительно подходят, и "
                        "никакие другие."
                    ),
                },
                {
                    "name": "referral",
                    "type": "enum",
                    "label": "Откуда узнал",
                    "allowed": [
                        "рекомендация",
                        "instagram",
                        "telegram",
                        "youtube",
                        "linkedin",
                        "поиск-google",
                        "реклама",
                        "сми-публикация",
                        "вебинар-мероприятие",
                        # Job boards and cold outreach were missing entirely, and an
                        # enum with no fitting value does not come back empty -- the
                        # model substitutes the nearest listed one. A client who said
                        # "с Indeed я вас взяла" was recorded as `telegram`, then as
                        # `поиск-google` once the prompt forbade substituting. Adding
                        # the real channel is what fixed it.
                        "indeed",
                        "hh",
                        "facebook",
                        "холодная-рассылка",
                    ],
                    "prompt": (
                        "Откуда клиент впервые узнал о компании. Заполняй, только "
                        "если клиент сам это сказал: вопрос менеджера без ответа "
                        "источником не является, и твоя догадка по контексту тоже."
                    ),
                },
                {
                    "name": "referral_note",
                    "type": "text",
                    "label": "Подробности",
                    "requires": "referral",
                    "prompt": (
                        "Одна строка словами клиента о том, откуда он узнал о "
                        "компании: кто порекомендовал, какой пост, какое "
                        "мероприятие."
                    ),
                },
                {
                    "name": "case_deadline",
                    "type": "text",
                    "label": "Срок сбора кейса",
                    "prompt": (
                        "К какому сроку клиенту нужно собрать документы кейса. "
                        "Оставь словами клиента, как он сказал на звонке, не "
                        "переводи в дату. Пусто, если о сроке сбора не говорили."
                    ),
                },
                {
                    "name": "deadlines",
                    "type": "text",
                    "multiple": True,
                    "label": "Дедлайны",
                    "prompt": (
                        "Прочие сроки, названные на звонке: виза, работа, учёба, "
                        "переезд. Одна строка на срок, словами клиента, вместе с "
                        "тем, к чему срок относится. Пустой список, если сроков "
                        "не называли."
                    ),
                },
                {
                    "name": "target_filing",
                    "type": "text",
                    "label": "Целевая подача",
                    # Both loosenings below come from a live miss: on a call where the
                    # manager said "у вас основная цель - визы талантов" the field came
                    # back empty, because the old wording's single example paired a type
                    # with a window and "целится клиент" read as requiring the client's
                    # own words.
                    "prompt": (
                        "На какую подачу целится клиент: тип визы и/или срок подачи. "
                        "Годится и один тип без срока («виза талантов», «EB-1A»), и "
                        "связка («O-1 осенью»), и один срок без типа. Цель "
                        "засчитывается и тогда, когда её проговаривает менеджер со "
                        "слов клиента («у вас основная цель — ...»). Пусто, только "
                        "если о цели подачи вообще не говорили."
                    ),
                },
            ]
        },
        # Seeded empty: a blank url disables the completion webhook.
        "webhook": {"url": "", "token": ""},
        # Seeded disabled: enabling this opens a listening port, so it must be an
        # explicit choice rather than something a `config init` turns on.
        "call_booking": {
            "enabled": False,
            "listen_host": "0.0.0.0",
            "listen_port": 8080,
            "authorization_token": "",
            "threshold_minutes": 15,
            "disable_recognition": False,
        },
        # Seeded empty: a blank url disables the Planfix comment.
        "planfix": {
            "create_comment_url": "",
            "token": "",
            "presets": ["keypoints"],
            "meta_fields": [
                "subject", "tags", "referral", "referral_note",
                "case_deadline", "deadlines", "target_filing",
                "duration", "video_url",
            ],
            # e.g. https://<account>.planfix.com/task/<task-id>
            "task_url": "",
        },
        # Google auth is inline-first and config-owned. The generated config ships an
        # empty block (no *_file pointers) so the data_dir fallback applies until the
        # operator runs `gdstt auth import-credentials` / `auth use-files`.
        "google": {},
        "presets": presets,
    }
    return config


def copy_prompt_assets(target_dir: Path, *, overwrite: bool = False) -> list[Path]:
    """Copy the packaged prompt assets into ``target_dir``.

    Each asset in :data:`PACKAGED_PROMPT_ASSETS` is written under ``target_dir`` by
    file name. Existing files are left untouched unless ``overwrite`` is set. The
    directory is created if needed. Returns the paths actually written.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name in PACKAGED_PROMPT_ASSETS:
        dest = target_dir / name
        if dest.exists() and not overwrite:
            continue
        dest.write_text(load_packaged_prompt(name), encoding="utf-8")
        written.append(dest)
    return written


def load_packaged_keyterms() -> str:
    """Read the packaged default Deepgram keyterms file."""
    try:
        text = (
            files("src.assets")
            .joinpath(DEEPGRAM_KEYTERMS_ASSET)
            .read_text(encoding="utf-8-sig")
        )
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        raise ValueError(
            f"packaged Deepgram keyterms asset {DEEPGRAM_KEYTERMS_ASSET!r} is missing"
        ) from exc
    if not text.strip():
        raise ValueError(
            f"packaged Deepgram keyterms asset {DEEPGRAM_KEYTERMS_ASSET!r} is empty"
        )
    return text


def copy_deepgram_keyterms_asset(config_dir: Path, *, overwrite: bool = False) -> Path:
    """Copy the packaged Deepgram keyterms example beside a generated config."""
    dest = config_dir / DEEPGRAM_DEFAULT_KEYTERMS_FILE
    if dest.exists() and not overwrite:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(load_packaged_keyterms(), encoding="utf-8")
    return dest


def _google_to_yaml_dict(config: Config, config_file: Path | None) -> dict:
    """Serialize the Google auth block, inline-first.

    Inline ``credentials``/``token`` mappings are written verbatim (masking happens
    at display time, not on disk). The ``*_file`` pointers are only emitted in file
    mode (i.e. when no inline mapping is present and a file path is set); they are
    written relative to the config file's parent so they round-trip. When nothing is
    configured an empty mapping is returned so the loader's data_dir fallback applies.
    """
    block: dict[str, object] = {}
    if config.google_credentials is not None:
        block["credentials"] = config.google_credentials
    elif config.google_credentials_file is not None:
        block["credentials_file"] = _relpath_for_config(
            config.google_credentials_file, config_file
        )
    if config.google_token is not None:
        block["token"] = config.google_token
    elif config.google_token_file is not None:
        block["token_file"] = _relpath_for_config(config.google_token_file, config_file)
    return block


def _entity_to_dict(entity: meta_entity.MetaEntity) -> dict[str, object]:
    """Serialize one entity, omitting keys that carry their default."""
    data: dict[str, object] = {"name": entity.name, "type": entity.type}
    if entity.multiple:
        data["multiple"] = True
    if entity.type == "enum":
        data["allowed"] = list(entity.allowed)
    if entity.label is not None:
        data["label"] = entity.label
    if entity.requires:
        data["requires"] = entity.requires
    data["prompt"] = entity.prompt
    return data


def _config_to_yaml_dict(config: Config, config_file: Path | None = None) -> dict:
    """Serialize a Config into the grouped `config.yml` schema.

    Filesystem paths (``data_dir``, ``output.dir``, ``deepgram.keyterms_file``)
    are written relative to the config file's parent directory so that re-reading
    the YAML (which resolves relative paths against that parent) yields the same
    locations. See :func:`_relpath_for_config`.
    """
    return {
        "folders": [
            {"folder_id": folder.folder_id, "name": folder.name, "email": folder.email}
            for folder in config.folders
        ],
        "poll_interval": config.poll_interval,
        "bitrate": config.bitrate,
        "data_dir": _relpath_for_config(config.data_dir, config_file),
        "proxy_url": config.proxy_url,
        "output": {
            "target": config.output_target,
            "dir": _relpath_for_config(config.output_dir, config_file),
            "stt_presets": list(config.stt_presets),
        },
        "stt": {
            "provider": config.stt_provider,
            "language": config.stt_language,
            "postprocess": config.stt_postprocess,
            "drive_mp3_artifact": config.drive_mp3_artifact,
            "deepgram": {
                "api_key": config.deepgram_api_key,
                "model": config.deepgram_model,
                "diarize_model": config.deepgram_diarize_model,
                "audio_source": config.deepgram_audio_source,
                "txt_formatter": config.deepgram_txt_formatter,
                "keyterms_enabled": config.deepgram_keyterms_enabled,
                "keyterms_file": _relpath_for_config(
                    config.deepgram_keyterms_file, config_file
                ),
            },
        },
        "openai": {
            "api_key": config.openai_api_key,
            "model": config.openai_model,
            "batch": config.openai_batch,
            # batch_wait defaults to true and is omitted unless explicitly disabled,
            # keeping the generated YAML free of the (unsupported) async path.
            **(
                {}
                if config.openai_batch_wait
                else {"batch_wait": config.openai_batch_wait}
            ),
            "max_parallel": config.openai_max_parallel,
            "keypoints": config.openai_keypoints,
        },
        "run": {"enabled": config.run_enabled},
        "notifications": {
            "telegram": {
                "bot_token": config.telegram_bot_token,
                "chat_id": config.telegram_chat_id,
            },
        },
        # This serializer (`_config_to_yaml_dict`) has no production caller: `gdstt
        # stop`/`start` go through `set_run_enabled` -> `config_set`, which patches
        # one dotted key in the raw YAML via `_set_nested` and writes that back, not
        # this function. Only tests exercise this path today. It is also not
        # exhaustive -- it drops the whole top-level `call_booking:` block -- so
        # wiring it up as a real rewrite path would mean making it exhaustive first;
        # being a whole-file dump, it would also strip every operator comment from
        # the file. The deprecated top-level tags/referrals keys are deliberately
        # not written back here -- their values now live inside the entities.
        "meta": {
            "entities": [_entity_to_dict(entity) for entity in config.meta_entities]
        },
        "webhook": {"url": config.webhook_url, "token": config.webhook_token},
        # planfix.presets/create_comment_url/token were previously absent from this
        # serializer too (a whole-Config rewrite silently dropped them); meta_fields is
        # added alongside them here rather than as an isolated partial block.
        "planfix": {
            "create_comment_url": config.planfix_create_comment_url,
            "token": config.planfix_token,
            "presets": list(config.planfix_presets),
            "meta_fields": list(config.planfix_meta_fields),
            "task_url": config.planfix_task_url,
        },
        "google": _google_to_yaml_dict(config, config_file),
        # Serialize the resolved preset DAG. Each entry carries a ``prompt_file`` so
        # the prompt text stays owned by the .md assets; disabled built-ins (e.g.
        # keypoints disabled through the preset config are still written with their
        # prompt_file so the default preset remains one edit away from re-enabled.
        "presets": _presets_to_yaml_dict(config),
    }


def _dump_yaml(data: dict) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def _missing_config_error(path: Path, *, empty: bool = False) -> ValueError:
    state = "empty" if empty else "missing"
    return ValueError(
        f"{path} is {state}; run `gdstt config init` to create a config.yml."
    )


def _write_config_text(path: Path, text: str) -> None:
    """Write the effective config and restrict it to owner-only (0600).

    The config file is the primary store for inline secrets (``openai.api_key``,
    ``stt.deepgram.api_key``, ``google.credentials.*.client_secret``,
    ``google.token.*``), so it is created/rewritten with the same owner-only
    permissions as ``token.json`` rather than the process umask, which on a shared
    host would otherwise leave secrets group/world-readable. ``chmod`` is best-effort
    (a no-op on platforms that do not support POSIX modes).
    """
    # Open with owner-only perms so a freshly created config never has a
    # world-readable window before chmod. O_CREAT's mode applies only on creation
    # (subject to umask); the chmod afterwards tightens an already-existing file.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _read_config_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError:
        return ""


def load_config(
    *,
    validate_providers: bool = True,
    config_path: str | Path | None = None,
) -> Config:
    """Load configuration from the active ``config.yml``.

    The active file is ``<GDSTT_HOME>/config.yml`` (or ``./data/config.yml`` when
    ``GDSTT_HOME`` is unset), unless ``config_path`` overrides it. There is no
    ``.env`` reading and no implicit config generation: a missing or empty file is a
    clear setup error pointing the operator at ``gdstt config init``.
    """
    resolved = _resolve_config_file_path(config_path)
    if not resolved.exists():
        raise _missing_config_error(resolved)
    text = _read_config_text(resolved)
    if not text.strip():
        raise _missing_config_error(resolved, empty=True)
    raw = _parse_config_yaml(text)
    if not isinstance(raw, dict):
        raise ValueError(
            f"{resolved} must contain a YAML mapping, got: {type(raw).__name__}"
        )
    return _config_from_yaml(raw, resolved, validate_providers=validate_providers)


def _rel_posix(path: Path, base: Path) -> str:
    """Express ``path`` relative to ``base`` using ``/`` separators (portable YAML)."""
    try:
        return Path(os.path.relpath(path, base)).as_posix()
    except ValueError:
        # Different Windows drives: no relative path exists, keep the absolute one.
        return Path(path).as_posix()


def init_config(
    *,
    config_path: str | Path | None = None,
    data_dir: str | Path | None = None,
    output_dir: str | Path | None = None,
    prompt_dir: str | Path | None = None,
    force: bool = False,
) -> Path:
    """Create a fresh full ``config.yml`` from the packaged defaults.

    Target selection uses the same resolver as the runtime so init writes where
    the runtime reads: an explicit ``config_path`` wins; otherwise the active
    config is ``<GDSTT_HOME>/config.yml`` when ``GDSTT_HOME`` is set, falling back
    to ``./data/config.yml`` when it is unset. The default preset chain is
    ``transcript-cleanup -> keypoints + meta`` with all three presets
    enabled. Prompt assets are always copied beside the config: into
    ``prompt_dir`` when given (and the ``prompt_file`` entries point there), else
    into ``<config_dir>/prompts/``.
    Refuses to overwrite a non-empty existing config unless ``force``.
    """
    if config_path is not None:
        target = Path(config_path)
    else:
        target = _resolve_config_file_path()

    if target.exists() and _read_config_text(target).strip() and not force:
        raise ValueError(
            f"{target} already exists; pass --force to overwrite it."
        )

    config_dir = target.parent
    config_dir.mkdir(parents=True, exist_ok=True)

    if prompt_dir is not None:
        prompts_target = Path(prompt_dir)
        if not prompts_target.is_absolute():
            prompts_target = config_dir / prompts_target
        prompt_rel = _rel_posix(prompts_target, config_dir)
    else:
        prompts_target = config_dir / PROMPTS_DIR_NAME
        prompt_rel = None

    copy_prompt_assets(prompts_target)
    copy_deepgram_keyterms_asset(config_dir)

    data_dir_value = (
        _rel_posix(Path(data_dir), config_dir) if data_dir is not None else None
    )
    output_target = None
    output_dir_value = None
    if output_dir is not None:
        output_target = "folder"
        output_dir_value = _rel_posix(Path(output_dir), config_dir)

    data = _default_config_dict(
        data_dir=data_dir_value,
        output_target=output_target,
        output_dir=output_dir_value,
        prompt_dir=prompt_rel,
    )
    _write_config_text(target, _dump_yaml(data))
    return target


# --- config get / set / unset -----------------------------------------------

# Secret-bearing keys are masked when the whole config is dumped via ``config get``
# (with no KEY). Each entry is a tuple of nested mapping keys leading to the value.
MASKED_KEY_PATHS: tuple[tuple[str, ...], ...] = (
    ("openai", "api_key"),
    ("stt", "deepgram", "api_key"),
    ("google", "client_secret"),
    ("google", "refresh_token"),
    ("notifications", "telegram", "bot_token"),
)
# Leaf key names whose values are masked wherever they appear (covers nested
# credentials/token blocks copied verbatim into the YAML).
MASKED_LEAF_KEYS: frozenset[str] = frozenset(
    {
        "client_secret",
        "refresh_token",
        "token",
        "access_token",
        "api_key",
        "bot_token",
        "authorization_token",
    }
)
MASK = "***"
# URL-valued keys whose credentials (userinfo), path and query string are redacted
# unless --show-secrets is passed: a webhook receiver commonly authenticates via a
# token, and it may sit in any of the three — Slack/Discord/Teams put it in the path,
# others in the query — so the raw URL is as sensitive as `webhook.token`. Only the
# scheme and host stay visible, which is enough to confirm the target.
REDACTED_URL_KEY_PATHS: tuple[tuple[str, ...], ...] = (("webhook", "url"),)


def _redact_url(value: object) -> object:
    """Strip userinfo, path and query from a URL string, leaving scheme/host visible."""
    if not isinstance(value, str) or not value.strip():
        return value
    try:
        parsed = urlparse(value)
    except ValueError:
        return MASK
    netloc = parsed.netloc
    if "@" in netloc:
        netloc = f"{MASK}@{netloc.rsplit('@', 1)[1]}"
    path = parsed.path
    if path.strip("/"):
        path = f"/{MASK}"
    redacted = parsed._replace(
        netloc=netloc,
        path=path,
        query=MASK if parsed.query else "",
        fragment=MASK if parsed.fragment else "",
    )
    return redacted.geturl()


def _redact_url_paths(data: dict, paths: tuple[tuple[str, ...], ...]) -> None:
    """Redact each existing URL path in ``data`` in place."""
    for path in paths:
        node: object = data
        for key in path[:-1]:
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(key)
        if isinstance(node, dict) and path[-1] in node:
            node[path[-1]] = _redact_url(node[path[-1]])


def _load_effective_yaml_dict(config_path: str | Path | None = None) -> tuple[Path, dict]:
    """Return the (effective_path, parsed-mapping) for the active config file.

    Config mutation requires an existing, non-empty config: a missing or empty file
    raises the same setup error as :func:`load_config`, pointing the operator at
    ``gdstt config init`` rather than silently materializing an empty mapping.
    """
    effective = _resolve_config_file_path(config_path)
    if not effective.exists():
        raise _missing_config_error(effective)
    text = _read_config_text(effective)
    if not text.strip():
        raise _missing_config_error(effective, empty=True)
    raw = _parse_config_yaml(text)
    if not isinstance(raw, dict):
        raise ValueError(
            f"{effective} must contain a YAML mapping, got: {type(raw).__name__}"
        )
    return effective, raw


def _mask_value(value: object) -> object:
    if isinstance(value, dict):
        return {k: _mask_value(MASK if k in MASKED_LEAF_KEYS else v) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_value(item) for item in value]
    return value


def _mask_config_dict(data: dict) -> dict:
    """Return a deep copy of ``data`` with secret values replaced by ``MASK``."""
    masked = _mask_value(data)
    assert isinstance(masked, dict)  # noqa: S101 - top-level is always a mapping
    for path in MASKED_KEY_PATHS:
        node: object = masked
        for key in path[:-1]:
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(key)
        if isinstance(node, dict) and path[-1] in node and node[path[-1]] not in (None, ""):
            node[path[-1]] = MASK
    _redact_url_paths(masked, REDACTED_URL_KEY_PATHS)
    return masked


def _split_key(key: str) -> list[str]:
    parts = [part for part in key.split(".") if part]
    if not parts:
        raise ValueError("config key must be a non-empty dotted path")
    return parts


def _get_nested(data: dict, parts: list[str]) -> object:
    node: object = data
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            raise KeyError(".".join(parts))
        node = node[part]
    return node


def _set_nested(data: dict, parts: list[str], value: object) -> None:
    node = data
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def _unset_nested(data: dict, parts: list[str]) -> bool:
    node: object = data
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    if not isinstance(node, dict) or parts[-1] not in node:
        return False
    del node[parts[-1]]
    return True


def _format_get_value(value: object) -> str:
    if isinstance(value, (dict, list)):
        return _dump_yaml(value).rstrip("\n")
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def _mask_get_value(parts: list[str], value: object) -> object:
    """Mask a single ``config get KEY`` value so secrets do not leak to stdout/logs.

    A secret scalar leaf (api keys, tokens, client_secret, refresh_token) is replaced
    wholesale; a mapping/list value (e.g. ``google.credentials`` / ``google.token``)
    is deep-masked so nested secret leaves are hidden while structure stays visible.
    URL leaves that may carry a token (``webhook.url``) keep their target visible but
    lose credentials and query string.
    """
    if isinstance(value, (dict, list)):
        masked = _mask_value(value)
        if isinstance(masked, dict):
            suffixes = tuple(
                path[len(parts) :]
                for path in REDACTED_URL_KEY_PATHS
                if tuple(parts) == path[: len(parts)] and len(path) > len(parts)
            )
            _redact_url_paths(masked, suffixes)
        return masked
    if parts[-1] in MASKED_LEAF_KEYS and value not in (None, ""):
        return MASK
    if tuple(parts) in REDACTED_URL_KEY_PATHS:
        return _redact_url(value)
    return value


def config_get(
    key: str | None = None,
    *,
    config_path: str | Path | None = None,
    show_secrets: bool = False,
) -> str:
    """Return a printable view of the effective config (whole) or one value.

    Secrets (api keys, tokens, client_secret, refresh_token) are masked by default
    for both the whole-config dump and a single-key lookup; pass ``show_secrets`` to
    reveal them. This keeps ``config get google.credentials`` / ``google.token`` from
    leaking the OAuth secret or refresh token to terminal scrollback or CI logs.
    """
    _, data = _load_effective_yaml_dict(config_path)
    if not key:
        return _dump_yaml(data if show_secrets else _mask_config_dict(data)).rstrip("\n")
    parts = _split_key(key)
    try:
        value = _get_nested(data, parts)
    except KeyError as exc:
        raise ValueError(f"config key {key!r} is not set") from exc
    if not show_secrets:
        value = _mask_get_value(parts, value)
    return _format_get_value(value)


def _parse_set_value(parts: list[str], raw: str) -> object:
    """Coerce a raw CLI string into the value type implied by its dotted key."""
    leaf = parts[-1]
    # depends_on accepts a JSON list or a comma/space-separated list of names.
    if leaf == "depends_on":
        text = raw.strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"depends_on must be a JSON list, got: {raw!r}") from exc
            if not isinstance(parsed, list):
                raise ValueError(f"depends_on must be a JSON list, got: {raw!r}")
            return [str(item).strip() for item in parsed if str(item).strip()]
        items = [item for item in re.split(r"[,\s]+", text) if item]
        return items
    # Booleans for known boolean leaves.
    if leaf in {"enabled", "batch", "batch_wait", "drive", "postprocess", "keyterms_enabled"}:
        return _parse_bool(raw, default=False)
    # Integers for known numeric leaves.
    if leaf in {"poll_interval", "max_parallel"}:
        try:
            return int(raw.strip())
        except ValueError as exc:
            raise ValueError(f"{leaf} must be an integer, got: {raw!r}") from exc
    return raw


def _atomic_write_validated(
    effective: Path, data: dict, *, config_path: str | Path | None
) -> None:
    """Write ``data`` to ``effective``, validating; leave the file unchanged on failure.

    The original text is snapshotted first; the new YAML is written, then
    ``load_config(validate_providers=False)`` is run against the effective path. Any
    validation error restores the original bytes (or removes a freshly created file)
    and re-raises, so a bad ``set``/``unset`` never corrupts the on-disk config.
    """
    existed = effective.exists()
    original = effective.read_text(encoding="utf-8-sig") if existed else None
    effective.parent.mkdir(parents=True, exist_ok=True)
    _write_config_text(effective, _dump_yaml(data))
    try:
        load_config(validate_providers=False, config_path=config_path)
    except Exception:
        if original is not None:
            _write_config_text(effective, original)
        else:
            effective.unlink(missing_ok=True)
        raise


def config_set(key: str, value: str, *, config_path: str | Path | None = None) -> Path:
    """Set a dotted ``key`` to ``value`` in the effective config and validate.

    Booleans/ints/lists are parsed from the raw string per key. ``output.dir PATH``
    also sets ``output.target: folder``; ``output.drive true`` sets
    ``output.target: drive``; ``output.drive false`` requires an existing
    ``output.dir`` (else an error tells the operator to set it first). The resulting
    full config is validated and the file is left unchanged if validation fails.
    """
    effective, data = _load_effective_yaml_dict(config_path)
    parts = _split_key(key)

    if parts == ["output", "drive"]:
        as_drive = _parse_bool(value, default=False)
        output = data.setdefault("output", {})
        if not isinstance(output, dict):
            output = {}
            data["output"] = output
        if as_drive:
            output["target"] = "drive"
        else:
            if not _yaml_str(output.get("dir")):
                raise ValueError(
                    "output.drive false needs a local folder; run "
                    "`config set output.dir PATH` first."
                )
            output["target"] = "folder"
    else:
        parsed = _parse_set_value(parts, value)
        _set_nested(data, parts, parsed)
        if parts == ["output", "dir"]:
            output = data.setdefault("output", {})
            if isinstance(output, dict):
                output["target"] = "folder"

    _atomic_write_validated(effective, data, config_path=config_path)
    return effective


def config_unset(key: str, *, config_path: str | Path | None = None) -> Path:
    """Remove a dotted ``key`` from the effective config and validate the result."""
    effective, data = _load_effective_yaml_dict(config_path)
    parts = _split_key(key)
    if not _unset_nested(data, parts):
        raise ValueError(f"config key {key!r} is not set")
    _atomic_write_validated(effective, data, config_path=config_path)
    return effective


def is_run_enabled(config_path: str | Path | None = None) -> bool:
    """Read ``run.enabled`` from the effective config (default true).

    Used by the polling loop to detect a ``gdstt stop`` between cycles. The read is
    deliberately light (no full validation) and defensive: any read/parse error
    returns ``True`` so a transient hiccup never stops a healthy loop.
    """
    try:
        _, data = _load_effective_yaml_dict(config_path)
    except (OSError, ValueError):
        return True
    run = data.get("run")
    if not isinstance(run, dict) or "enabled" not in run:
        return True
    try:
        return _yaml_bool(run.get("enabled"), default=True)
    except ValueError:
        return True


def set_run_enabled(enabled: bool, *, config_path: str | Path | None = None) -> Path:
    """Set ``run.enabled`` in the effective config (used by ``gdstt run``/``stop``)."""
    return config_set("run.enabled", "true" if enabled else "false", config_path=config_path)


# --- google auth config helpers ---------------------------------------------


def import_google_credentials(
    credentials_path: str | Path, *, config_path: str | Path | None = None
) -> Path:
    """Read an OAuth client JSON and store it inline under ``google.credentials``.

    The file's parsed structure (the Desktop-app client config, e.g. the
    ``{"installed": {...}}`` mapping) is written verbatim into the effective config
    so it is config-owned. Any pre-existing ``google.credentials_file`` pointer is
    cleared so the two sources never both apply. Validation rejects a malformed JSON
    file and any inline/file conflict before the write lands.
    """
    src = Path(credentials_path)
    try:
        parsed = json.loads(src.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ValueError(
            f"OAuth client credentials at {src} could not be read: {exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise ValueError(
            f"OAuth client credentials at {src} must be a JSON object."
        )

    effective, data = _load_effective_yaml_dict(config_path)
    google = data.setdefault("google", {})
    if not isinstance(google, dict):
        google = {}
        data["google"] = google
    google["credentials"] = parsed
    google.pop("credentials_file", None)
    _atomic_write_validated(effective, data, config_path=config_path)
    return effective


def use_google_files(
    credentials_file: str | Path,
    *,
    token_file: str | Path | None = None,
    config_path: str | Path | None = None,
) -> Path:
    """Switch Google auth to file mode and clear any inline credentials/token.

    Writes ``google.credentials_file`` (and ``google.token_file``) and removes the
    inline ``google.credentials``/``google.token`` mappings so the file pointers are
    the single source. ``token_file`` defaults to ``<credentials parent>/token.json``.
    """
    effective, data = _load_effective_yaml_dict(config_path)
    creds_path = Path(credentials_file)
    if not creds_path.is_absolute():
        creds_path = Path.cwd() / creds_path
    if token_file is None:
        token_path = creds_path.parent / "token.json"
    else:
        token_path = Path(token_file)
        if not token_path.is_absolute():
            token_path = Path.cwd() / token_path
    google = data.setdefault("google", {})
    if not isinstance(google, dict):
        google = {}
        data["google"] = google
    google.pop("credentials", None)
    google.pop("token", None)
    google["credentials_file"] = _relpath_for_config(creds_path, effective)
    google["token_file"] = _relpath_for_config(token_path, effective)
    _atomic_write_validated(effective, data, config_path=config_path)
    return effective
