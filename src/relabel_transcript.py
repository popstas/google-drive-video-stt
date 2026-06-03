#!/usr/bin/env python3
"""Детерминированное переименование спикеров в транскрипте для keypoints-флоу.

Берёт исходный транскрипт, по переданному маппингу заменяет метки спикеров
(`Speaker 1`, `Andrey`, ...) на реальные имена/wikilink и объединяет подряд идущие
реплики одного говорящего в блок. Текст реплик сохраняется ДОСЛОВНО - скрипт не
переписывает и не сокращает содержимое, только меняет метку и склеивает блоки.

Поддерживаемые форматы строк:
  - диаризация:  [HH:MM:SS] Speaker 1: текст        (одна реплика = одна строка)
  - krisp-стиль: **Andrey | 01:23**                 (метка+таймкод, текст на следующих строках)
                 текст реплики...

Использование (standalone):
  python3 -m src.relabel_transcript --in SRC.md --out OUT.md --map MAP.json [--no-header]

В процессе (in-process):
  from src.relabel_transcript import relabel
  text = relabel(src_text, map_cfg)

Формат MAP.json:
{
  "header": "# Заголовок\\n\\n> пояснение атрибуции...",   // markdown-шапка (можно пусто)
  "default": { "Speaker 1": "[[Андрей Смирнов]]",
               "Speaker 2": "[[Андрей Смирнов]]",
               "Speaker 3": "Яна Банова" },
  "exceptions": [ { "text": "Алло, Андрей, привет.", "name": "Яна Банова" } ]
}

`default` - метка из транскрипта (как есть) -> итоговое имя.
`exceptions` - точечные правки по дословному тексту реплики (текст -> имя),
перекрывают default. Нужны, когда лишняя метка диаризации в одной-двух репликах
по контексту принадлежит другому человеку.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys

logger = logging.getLogger(__name__)

# [HH:MM:SS] Label: text   (Label - всё до первого двоеточия после таймкода)
BRACKET = re.compile(r"^\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]\s*(.+?):\s?(.*)$")
# **Label | MM:SS**  или  **Label | HH:MM:SS**
KRISP_HEAD = re.compile(r"^\*\*(.+?)\s*\|\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\*\*$")


def mmss(h, m, s):
    """Привести таймкод к MM:SS (часы сворачиваются в минуты)."""
    if s is None:
        # было MM:SS -> h=минуты, m=секунды
        return f"{int(h):02d}:{int(m):02d}"
    total_min = int(h) * 60 + int(m)
    return f"{total_min:02d}:{int(s):02d}"


def parse(text):
    """Вернуть список реплик (timecode, label, utterance_text)."""
    lines = text.splitlines()
    has_krisp = any(KRISP_HEAD.match(ln.strip()) for ln in lines)
    turns = []
    if has_krisp:
        cur = None  # (ts, label, [text_parts])
        for ln in lines:
            m = KRISP_HEAD.match(ln.strip())
            if m:
                if cur:
                    turns.append((cur[0], cur[1], " ".join(cur[2]).strip()))
                label, h, mm, ss = m.group(1).strip(), m.group(2), m.group(3), m.group(4)
                cur = (mmss(h, mm, ss), label, [])
            elif cur is not None:
                stripped = ln.strip()
                # пропускаем заголовки секций и одинокие номера блоков
                if stripped and not re.match(r"^#{1,6}\s", stripped) and not re.match(r"^\d+$", stripped):
                    cur[2].append(stripped)
        if cur:
            turns.append((cur[0], cur[1], " ".join(cur[2]).strip()))
    else:
        for ln in lines:
            m = BRACKET.match(ln)
            if not m:
                continue
            h, mm, ss, label, txt = m.groups()
            turns.append((mmss(h, mm, ss), label.strip(), txt.strip()))
    return turns


def resolve(turns, map_cfg):
    """Вернуть (blocks, unknown_labels) по разобранным репликам и маппингу.

    blocks - список [ts, name, [text_parts]] со склейкой подряд идущих реплик
    одного имени. unknown_labels - метки, для которых не нашлось имени.
    """
    default = map_cfg.get("default", {})
    exceptions = {e["text"].strip(): e["name"] for e in map_cfg.get("exceptions", [])}

    unknown = set()
    resolved = []  # (ts, name, text)
    for ts, label, txt in turns:
        name = exceptions.get(txt.strip()) or default.get(label)
        if name is None:
            unknown.add(label)
            name = label
        resolved.append((ts, name, txt))

    # склейка подряд идущих реплик одного имени
    blocks = []
    for ts, name, txt in resolved:
        if blocks and blocks[-1][1] == name:
            blocks[-1][2].append(txt)
        else:
            blocks.append([ts, name, [txt]])
    return blocks, unknown


def render(blocks, header="", *, include_header=True):
    """Собрать итоговый markdown из блоков (+ опциональная шапка)."""
    out = []
    if header and include_header:
        out.append(header.rstrip() + "\n\n")
    for ts, name, parts in blocks:
        joined = " ".join(p for p in parts if p)
        out.append(f"**{name}** | {ts}\n{joined}\n\n")
    return "".join(out)


def relabel(src_text, map_cfg, *, include_header=True):
    """Переименовать спикеров и склеить блоки, вернуть итоговый markdown.

    Текст реплик сохраняется дословно; меняется только метка и группировка.
    Неотображённые метки логируются предупреждением и остаются как есть.
    """
    turns = parse(src_text)
    if not turns:
        raise ValueError(
            "relabel_transcript: не распознано ни одной реплики - проверь формат входа"
        )
    blocks, unknown = resolve(turns, map_cfg)
    if unknown:
        logger.warning("unmapped labels: %s", ", ".join(sorted(unknown)))
    return render(blocks, map_cfg.get("header", ""), include_header=include_header)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", dest="out", required=True)
    ap.add_argument("--map", dest="mapfile", required=True)
    ap.add_argument("--no-header", action="store_true")
    args = ap.parse_args(argv)

    with open(args.mapfile, encoding="utf-8") as f:
        cfg = json.load(f)

    with open(args.src, encoding="utf-8") as f:
        src_text = f.read()

    turns = parse(src_text)
    if not turns:
        sys.exit("relabel_transcript: не распознано ни одной реплики - проверь формат входа")

    blocks, unknown = resolve(turns, cfg)
    result = render(blocks, cfg.get("header", ""), include_header=not args.no_header)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(result)

    print(f"turns: {len(turns)}, blocks: {len(blocks)}")
    if unknown:
        sys.stderr.write("unmapped labels: " + ", ".join(sorted(unknown)) + "\n")


if __name__ == "__main__":
    main()
