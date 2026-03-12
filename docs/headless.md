# Headless / Queue Mode

Matrix Agent can run without a Matrix room by using a headless CLI or a Redis-backed queue worker. Matrix/GitHub behaviour remains the default when no headless flags are provided.

## One-shot CLI

```bash
python -m matrix_agent \
  --mode headless \
  --workflow <name> \
  --payload-json '{"task": "describe"}' \
  [--correlation-id <id>] \
  [--redis-url redis://localhost:6379/0] \
  [--results-prefix matrix-tui:results:] \
  [--result-ttl-seconds 86400] \
  [--progress]
```

- Runs a single workflow and prints a JSON result to stdout.
- If `--redis-url` is provided, the result is also written to `<results_prefix><correlation_id>` (SETEX with TTL, default 24h) and appended to `<results_prefix>list`.
- Progress entries are pushed to the results list only when `--progress` is set.

## Redis queue worker

Start a worker:

```bash
python -m matrix_agent \
  --mode queue \
  --redis-url redis://localhost:6379/0 \
  --jobs-key matrix-tui:jobs \
  --results-prefix matrix-tui:results: \
  [--result-ttl-seconds 86400] \
  [--timeout-seconds 900] \
  [--progress]
```

Enqueue jobs (Redis List, default `matrix-tui:jobs`):

```json
{
  "workflow": "<name>",               // required
  "payload": { "task": "..." },     // required (object or string)
  "correlation_id": "<id>",          // optional; generated if missing
  "reply_to": "results:custom:",     // optional results prefix override
  "timeout_seconds": 900              // optional; falls back to settings
}
```

Worker behaviour:
- One in-flight job per worker process; run multiple workers to scale.
- Builds a headless message from `workflow` + payload and executes via TaskRunner/Sandbox.
- Results stored at `<results_prefix><correlation_id>` with TTL and appended to `<results_prefix>list`.
- Status values: `completed`, `failed`, `timeout` (and `progress` entries when enabled).
- Text-only results; no file artifacts are pushed to Redis.

## Result schema

Stored JSON at the result key/list entries:

```json
{
  "status": "completed|failed|timeout|progress",
  "workflow": "<name>",
  "correlation_id": "<id>",
  "output": "<text output>",
  "error": "<error message>",
  "container": "headless:<id>",
  "started_at": "<iso8601>",
  "finished_at": "<iso8601>",
  "duration_ms": 123
}
```

## Defaults

- `redis_url`: `redis://localhost:6379/0`
- `redis_jobs_key`: `matrix-tui:jobs`
- `redis_results_prefix`: `matrix-tui:results:`
- `redis_result_ttl_seconds`: `86400` (24h)
- `headless_timeout_seconds`: falls back to `coding_timeout_seconds` when unset
- `headless_progress_enabled`: `False`

Refer to `pyproject.toml`/`config.py` for the full settings surface.
