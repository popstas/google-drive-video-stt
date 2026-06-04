"""Preset model for the config-defined OpenAI post-processing DAG.

A preset is one LLM pass with its own instructions. It feeds on the concatenated
outputs of its dependency presets (or the raw transcript when it has none) and
writes its own sibling artifact. Code ships built-in presets (at least
``keypoints``); ``data/config.yml`` presets merge over them field-by-field and can
disable a built-in with ``enabled: false``. This module owns the model, the
built-in registry, the merge, and DAG validation; ``config.py`` wires them in and
``preset_pipeline.py`` (a later task) executes the graph.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

# LLM keypoints pipeline modeled on the `keypoints-transcription` skill: take a
# speaker-named transcript of a recorded conversation and produce a concise
# Keypoints summary (Задачи / Тезисы / Открытые вопросы) grounded strictly in the
# transcript, in plain Markdown without vault-style wikilinks. This is the single
# source of truth for the built-in `keypoints` preset; openai_pipeline imports it.
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


def default_artifact_suffix(name: str) -> str:
    """Derive the default sibling-artifact suffix for a preset (``.<name>.md``)."""
    return f".{name}.md"


@dataclass(frozen=True)
class Preset:
    """One named LLM pass in the post-processing DAG.

    ``model``/``batch`` of ``None`` fall back to the global ``openai`` defaults at
    execution time. An empty ``artifact_suffix`` is derived from ``name``.
    """

    name: str
    instructions: str
    depends_on: tuple[str, ...] = ()
    model: str | None = None
    batch: bool | None = None
    artifact_suffix: str = ""
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("preset name must be non-empty")
        if not self.artifact_suffix:
            object.__setattr__(
                self, "artifact_suffix", default_artifact_suffix(self.name)
            )


# Code-shipped presets. Config presets merge over these by name.
BUILTIN_PRESETS: tuple[Preset, ...] = (
    Preset(
        name="keypoints",
        instructions=INSTRUCTIONS,
        artifact_suffix=".keypoints.md",
    ),
)


def _as_str(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def _opt_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _opt_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    raise ValueError(f"Expected boolean value, got: {value!r}")


def _bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise ValueError(f"Expected boolean value, got: {value!r}")


def _depends_on(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise ValueError(f"depends_on must be a list of preset names, got: {value!r}")
    return tuple(str(item).strip() for item in items if str(item).strip())


def _build_preset(name: str, raw: Mapping, base: Preset | None) -> Preset:
    """Create a Preset from raw config fields, overriding ``base`` when present."""
    if base is None:
        instructions = _as_str(raw.get("instructions"))
        if not instructions.strip():
            raise ValueError(
                f"preset {name!r} is not a built-in and must define instructions"
            )
        suffix = _as_str(raw.get("artifact_suffix")).strip()
        return Preset(
            name=name,
            instructions=instructions,
            depends_on=_depends_on(raw.get("depends_on")),
            model=_opt_str(raw.get("model")),
            batch=_opt_bool(raw.get("batch")),
            artifact_suffix=suffix or default_artifact_suffix(name),
            enabled=_bool(raw.get("enabled"), default=True),
        )

    overrides: dict[str, object] = {}
    if "instructions" in raw:
        overrides["instructions"] = _as_str(raw.get("instructions"))
    if "depends_on" in raw:
        overrides["depends_on"] = _depends_on(raw.get("depends_on"))
    if "model" in raw:
        overrides["model"] = _opt_str(raw.get("model"))
    if "batch" in raw:
        overrides["batch"] = _opt_bool(raw.get("batch"))
    if "artifact_suffix" in raw:
        suffix = _as_str(raw.get("artifact_suffix")).strip()
        overrides["artifact_suffix"] = suffix or default_artifact_suffix(name)
    if "enabled" in raw:
        overrides["enabled"] = _bool(raw.get("enabled"), default=True)
    return replace(base, **overrides)


def merge_presets(
    builtins: Iterable[Preset],
    config_presets: Mapping[str, Mapping | None] | None,
) -> dict[str, Preset]:
    """Merge config preset overrides over built-ins.

    Built-ins are applied first (in declaration order); each config entry either
    overrides a built-in field-by-field or adds a new preset. Presets resolving to
    ``enabled: false`` are dropped. Returns ``{name: Preset}`` of the enabled
    presets, preserving built-in order followed by newly added config presets.
    """
    merged: dict[str, Preset] = {p.name: p for p in builtins}
    for name, raw in (config_presets or {}).items():
        name = str(name).strip()
        if not name:
            raise ValueError("preset names must be non-empty")
        raw_map: Mapping = raw or {}
        if not isinstance(raw_map, Mapping):
            raise ValueError(f"preset {name!r} must be a mapping, got: {raw!r}")
        merged[name] = _build_preset(name, raw_map, merged.get(name))
    return {name: preset for name, preset in merged.items() if preset.enabled}


def validate_dag(presets: Mapping[str, Preset] | Iterable[Preset]) -> None:
    """Validate the preset DAG: deps exist and are enabled, and there are no cycles.

    ``presets`` is expected to contain only enabled presets (as returned by
    :func:`merge_presets`), so a dependency on a disabled/dropped preset surfaces as
    an unknown dependency. Raises ``ValueError`` on any violation.
    """
    if isinstance(presets, Mapping):
        preset_map = {name: preset for name, preset in presets.items()}
    else:
        preset_map = {preset.name: preset for preset in presets}

    for preset in preset_map.values():
        for dep in preset.depends_on:
            if dep not in preset_map:
                raise ValueError(
                    f"preset {preset.name!r} depends on unknown or disabled "
                    f"preset {dep!r}"
                )

    # Iterative DFS cycle detection over the dependency edges.
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {name: WHITE for name in preset_map}

    def visit(start: str) -> None:
        stack: list[tuple[str, int]] = [(start, 0)]
        while stack:
            node, idx = stack.pop()
            deps = preset_map[node].depends_on
            if idx == 0:
                if color[node] == BLACK:
                    continue
                color[node] = GRAY
            if idx < len(deps):
                stack.append((node, idx + 1))
                dep = deps[idx]
                if color[dep] == GRAY:
                    raise ValueError(
                        f"preset dependency cycle detected involving {dep!r}"
                    )
                if color[dep] == WHITE:
                    stack.append((dep, 0))
            else:
                color[node] = BLACK

    for name in preset_map:
        if color[name] == WHITE:
            visit(name)
