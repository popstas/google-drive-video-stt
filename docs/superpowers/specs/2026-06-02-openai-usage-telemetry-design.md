# OpenAI Usage Telemetry Design

## Goal

Report the OpenAI token usage consumed by transcript refinement without
requiring an organization admin key or estimating dollar cost.

## Data Source

The OpenAI Responses API returns a `usage` object for completed synchronous
responses. Batch output JSONL embeds the same response body, including `usage`.

Capture these fields when present:

- `input_tokens`
- `input_tokens_details.cached_tokens`
- `output_tokens`
- `output_tokens_details.reasoning_tokens`
- `total_tokens`

Missing usage or missing nested fields must not fail transcript processing.

## Runtime Contract

`OpenAIPipeline.refine()` continues to return transcript text so existing
callers remain stable. The pipeline stores the latest normalized usage in
`last_usage`.

`refine_transcript()` accepts an optional `usage` dictionary collector. After
refinement, it copies `last_usage` into that collector.

`process_item()` records the collector in process telemetry. Agent execution
results add:

```json
{
  "usage": {
    "openai": {
      "input_tokens": 100,
      "cached_input_tokens": 0,
      "output_tokens": 25,
      "reasoning_tokens": 0,
      "total_tokens": 125
    }
  }
}
```

For folder processing, the executor sums each token field across processed
files. `cost_usd.openai` remains `null`.

## Safety

- Do not add `OPENAI_ADMIN_KEY`.
- Do not call organization Usage or Costs APIs.
- Do not log prompt text, transcript text, API keys, or response bodies.
- Usage reporting is best effort and must never block TXT upload.

## Testing

Add focused tests for synchronous response usage, batch JSONL usage, absent
usage, forwarding through `refine_transcript`, and aggregation in executor
results. Run the full pytest, ruff, skill sync, skill validator, and diff check.
