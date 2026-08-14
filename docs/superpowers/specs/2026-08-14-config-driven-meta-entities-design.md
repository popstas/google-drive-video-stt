# Config-driven meta entities

**Status:** approved, not yet implemented
**Date:** 2026-08-14

## Problem

The `meta` preset extracts exactly four things from a call — subject, tags,
referral, referral note — and every one of those names is hardcoded in five
places:

- `Meta` (`src/meta.py:43`), a frozen dataclass with four fields
- `_REQUOTABLE_FIELDS` (`src/meta.py:56`), naming the two prose fields by hand
- `FIELD_ORDER` (`src/meta_doc.py:25`) and the literal dict in `meta_doc.build`
- the webhook payload's fixed four keys (`src/main.py:704`)
- `_PLANFIX_META_LABELS` (`src/main.py:724`), a whitelist that silently drops
  anything not in it

Adding a fifth thing to extract therefore means editing five files, an asset
prompt, and their tests. The config already owns the *values* those fields may
take (`tags.allowed`, `referrals.allowed`) but not the fields themselves, which
is the wrong half: the value lists change rarely, the questions we ask a call
change often.

Concretely, three questions are wanted now and cannot be added without code:
how long the client has to assemble their case, what other deadlines were
named, and which filing they are aiming at.

## Design

An **entity** is one thing the `meta` preset extracts. Entities are declared in
`data/config.yml`; the code knows the shape of an entity, not the list of them.

### The entity

```yaml
meta:
  entities:
  - name: subject
    type: text
    label: ''
    prompt: Одно предложение о том, про что был звонок.
  - name: tags
    type: enum
    multiple: true
    label: Теги
    allowed: [O-1, EB-1A]
    prompt: Выбери все теги, которые действительно подходят.
  - name: referral
    type: enum
    label: Откуда узнал
    allowed: [рекомендация, instagram, telegram]
    prompt: >-
      Откуда клиент впервые узнал о компании. Заполняй, только если клиент сам
      это сказал; вопрос менеджера без ответа источником не является.
  - name: referral_note
    type: text
    label: Подробности
    requires: referral
    prompt: >-
      Одна строка словами клиента — кто порекомендовал, какой пост, какое
      мероприятие.
```

| Key | Required | Meaning |
|---|---|---|
| `name` | yes | YAML key in the artifact, the meta document, and the webhook payload |
| `prompt` | yes | the instruction handed to the model for this entity |
| `type` | no, default `text` | `text` = free string in the transcript's own words; `enum` = a value copied verbatim from `allowed` |
| `multiple` | no, default `false` | `true` yields a list instead of a scalar |
| `allowed` | `enum` only | the values the model may pick from |
| `label` | no, default `name` | the label in the Planfix comment header; `''` means "render as the bold heading, not a labelled line" |
| `requires` | no | name of another entity; this one is emptied when that one came back empty |

`requires` generalizes today's `referral_note` rule. A note that survived while
its channel was dropped for being off-list would smuggle the rejected channel
back in as prose, so the note goes with it.

An `enum` with an empty or absent `allowed` list drops every value the model
returns, exactly as an empty `tags.allowed` does today: the model was handed
nothing to choose from, so anything it returned is invented.

### The three new entities

Shipped in the generated default config, appended after the existing four:

| `name` | `label` | `type` | Question |
|---|---|---|---|
| `case_deadline` | Срок сбора кейса | text | by when the client must assemble the case documents |
| `deadlines` | Дедлайны | text, `multiple: true` | other dates named on the call — visa, job, studies, relocation |
| `target_filing` | Целевая подача | text | which filing the client is aiming at: type and/or window |

Values stay in the client's own words ("к концу лета", "до октябрьского окна").
No normalization to ISO dates: relative and vague deadlines are the common case
on these calls, and a model asked for a precise date will confidently invent
one.

### Prompt assembly

One LLM call, as today. `src/assets/prompts/meta.md` keeps the framing that is
about the *format*, not about any particular field: answer with a YAML
frontmatter block and nothing else, write in the transcript's language, never
invent facts, keep each value on one line, quote a value containing a colon.

The `{{allowed_tags}}` and `{{allowed_referrals}}` placeholders are removed. A
single `{{entities}}` placeholder replaces them and renders to two things: the
response template (one line per entity, `<name>: <…>`, list syntax for
`multiple`) and one rules block per entity carrying its `prompt`, its `allowed`
list when `enum`, a "return a list" note when `multiple`, and a "leave empty
when *X* is empty" note when `requires` is set.

Placeholder rendering already runs over every preset's resolved text
(`_render_prompt_placeholders`, `src/config.py:321`), so a prompt without
`{{entities}}` is returned unchanged. No custom `data/prompts/meta.md` exists in
the repo or on the deployment, so dropping the two old placeholders breaks no
operator prompt.

### Parsing

`parse_meta(text, entities)` returns `dict[str, str | list[str]]` — one key per
entity, always present, empty when absent or rejected. The `Meta` dataclass is
deleted along with attribute access (`.subject`, `.tags`).

Every existing tolerance survives, because it is about YAML and not about field
names: a fenced answer is unwrapped, a block missing its `---` delimiters is
still loaded, and an unquoted prose value containing a colon is re-quoted before
a second parse attempt. `_REQUOTABLE_FIELDS` stops being a constant and becomes
"every `text` entity that is not `multiple`". A malformed artifact still
degrades to empty values and a `logger.warning`, never an exception: the file
already transcribed and its artifacts are already on Drive.

### The meta document, the webhook, and Planfix

**`FIELD_ORDER`** becomes the entity names in config order followed by the
fields the code already knows (`manager` … `processed_at`). An entity whose
`name` collides with one of those is rejected at config load with a message
naming the collision — silently overwriting the manager would be worse than
refusing to start.

**Webhook payload:** the `meta` key carries one key per entity instead of the
fixed four.

**Planfix comment:** `_PLANFIX_META_LABELS` shrinks to the code-known fields
(`manager`, `client`, `date`, `duration`, `video_url`). Entity labels come from
the entity. The heading case — today `if field_name == "subject"` — becomes "the
field whose label is empty is hoisted to the top and bolded"; if several
qualify, the first in `planfix.meta_fields` wins. The `video_url` anchor built
from `source_name` stays code-specific.

`planfix.meta_fields` keeps naming fields by name, so it now spans entities and
code-known fields uniformly. Its default gains the three new entities. A field
with no value is already skipped silently, so a call that never mentioned a
deadline adds no lines.

**`.stt`:** its meta block is `meta_doc.to_yaml(document)`, so it picks up new
fields with no change.

### Config loading and validation

`src/meta_entity.py` is a new module holding the `MetaEntity` dataclass, the
YAML parser, validation, and the `{{entities}}` renderer. `src/config.py` is
1922 lines already; it imports the module and stores the result on
`Config.meta_entities`.

Rejected at load, each with a message naming the offending entity:

- duplicate `name`
- `name` colliding with a code-known meta-document field
- `name` that is not a plain identifier (spaces, punctuation, leading digit)
- unknown `type`
- `allowed` on a `text` entity
- `requires` naming an entity that does not exist
- `requires` forming a cycle
- missing `prompt`

Warned, not rejected: an `enum` with an empty `allowed` list (legal, but the
entity will always come back empty).

The top-level `meta:` section describes *what to extract*; `presets.meta`
remains the DAG node that enables the preset and names its dependency. They are
separate because one is data and the other is wiring.

**The whole-Config serializer must carry it.** `_config_to_dict`
(`src/config.py:1349`) rewrites the entire file on any programmatic config
change — `gdstt stop` is one — and drops anything it does not list; the existing
comment there says exactly that about `tags.allowed`. `meta.entities` is
operator-owned data with prompts in it, so it goes into that serializer in the
same change. Omitting it would erase every entity definition the first time an
operator stops the worker.

### Migration

`tags.allowed` and `referrals.allowed` live at the top level of every existing
config, including the deployment on us1. They are read for one more version:

- `meta.entities` absent → the four built-in entities are used, with `tags` and
  `referral` taking their `allowed` lists from the old top-level keys. Existing
  configs keep working untouched, minus the three new entities.
- `meta.entities` present → it is the whole list. A non-empty top-level
  `tags.allowed`/`referrals.allowed` is then ignored and logged as deprecated at
  startup.

`gdstt config init` writes the new form: `meta.entities` with `allowed` inline,
and no top-level `tags`/`referrals` sections.

## Testing

`tests/test_meta.py` (12 `Meta(` constructions) and `tests/test_meta_doc.py`
(2) move to dict-shaped assertions; `tests/test_config.py`,
`tests/test_main.py`, and `tests/test_preset_dag_e2e.py` follow their
allow-list and payload assertions.

New coverage:

- each validation rejection above, asserting the message names the entity
- `{{entities}}` rendering: template lines, `allowed` list, `multiple`,
  `requires`
- a `multiple: true` `text` entity parsing a list, and parsing a bare scalar
  into a one-element list (the `tags: O-1` tolerance, generalized)
- `requires` emptying a dependent when its target was dropped off-list
- an entity added to config appearing in `.meta.yml`, in the webhook payload,
  and — once named in `planfix.meta_fields` — in the Planfix header
- migration: a config with only top-level `tags.allowed` still feeds the `tags`
  entity; a config with `meta.entities` ignores it and logs the deprecation
- a whole-Config rewrite round-trips `meta.entities` without losing an entity or
  its prompt

All external services stay mocked, as everywhere else in the suite.

## Out of scope

- A `date` type with ISO normalization. Rejected above.
- Per-entity model calls. One `meta` call keeps cost and latency flat as
  entities are added.
- Per-entity model or temperature overrides.
- Backfilling `.meta.yml` for already-processed recordings. The new fields
  appear on calls processed after the change; re-running an old recording is an
  operator decision with a Deepgram bill attached.
- Making the `keypoints` or `transcript-cleanup` prompts config-driven. They
  produce prose, not fields.
