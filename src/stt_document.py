"""Assemble the one file a human opens after a call.

Keypoints first, because that is what anyone reads; then the meta block, so the
call can be placed without scrolling; then the transcript, which is the evidence for
both. The document is plain text — no LLM call — built from artifacts that already
exist.
"""

from __future__ import annotations

META_HEADING = "## Мета"
TRANSCRIPT_HEADING = "## Расшифровка"


def assemble(
    *, title: str, sections: list[str], meta_yaml: str, transcript: str
) -> str:
    """Concatenate the preset sections, the meta block, and the transcript.

    An empty section is dropped rather than emitted as a bare heading; the transcript
    heading is kept even when the transcript is empty, so a reader can tell the section
    exists and came back blank.
    """
    blocks: list[str] = [f"# {title.strip()}"] if title.strip() else []
    blocks.extend(section.strip() for section in sections if section and section.strip())
    blocks.append(f"{META_HEADING}\n```yaml\n{meta_yaml.strip()}\n```")
    blocks.append(f"{TRANSCRIPT_HEADING}\n{transcript.strip()}".rstrip())
    return "\n\n".join(blocks) + "\n"
