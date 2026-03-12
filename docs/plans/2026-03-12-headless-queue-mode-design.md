# Headless / Queue-Driven Execution Mode — Design

Issue: openclaw/nisto-home#282 (matrix-tui)

## Context
- Current entrypoint (`matrix_agent.__main__.py`) always boots the Matrix client (plus optional GitHub webhook). All work flows through Matrix rooms or GitHub issue webhooks.
- TaskRunner/Decider/Sandbox are channel-agnostic; GitHubChannel already shows a non-Matrix path.
- There is no headless execution path (CLI or queue) to drive workflows without Matrix, nor a Redis-based job/result interface.

## Goals
- Add a headless execution path that reuses the existing TaskRunner/Decider/Sandbox pipeline without requiring Matrix login.
- Provide:
  - **One-shot CLI**: run a workflow with a payload (JSON string or file) and emit structured results.
  - **Redis queue worker**: consume jobs from Redis and publish results back to Redis.
- Keep Matrix/GitHub behaviour the default when no headless/queue flags are provided.

## Constraints / Decisions (approved by @nisto)
1) Use **Redis Lists** (BLPOP intake, RPUSH results) for V1 — Streams/consumer groups deferred.
2) Result target: write JSON to key per correlation id (`<results_prefix><correlation_id>`) with **default TTL 24h (86400s)**.
3) Concurrency: **single in-flight job per worker process**; scale by running more workers, no intra-process parallelism.
4) Artifacts: **text output only** (status/output/errors). No file artifacts/log uploads to Redis.
5) Keep `workflow` and `payload` separate in the job schema; embed into a single Decider message for now.

## Architecture
### Mode selection
- Extend `__main__.py` with `--mode {matrix, headless, queue}` (default `matrix`).
  - **matrix**: existing Matrix bot + optional GitHub webhook (current behaviour).
  - **headless**: run one job from CLI flags; no Matrix login.
  - **queue**: run Redis worker; no Matrix login.

### Job schema (Redis list element / CLI input)
```json
{
  "workflow": "<name>",
  "payload": { ... } | "<string>",
  "correlation_id": "<string optional for CLI, required for queue>",
  "reply_to": "<optional results key override>",
  "timeout_seconds": <optional int>,
  "metadata": { ... }
}
```
- `correlation_id` reused as task_id suffix: `headless:<id>`; generate UUID when absent (CLI).
- Decider message text: `WORKFLOW <workflow>\n<pretty JSON payload or string>`.

### Result schema (stored at `<results_prefix><correlation_id>`, TTL 24h)
```json
{
  "correlation_id": "<id>",
  "workflow": "<name>",
  "status": "completed" | "failed" | "timeout",
  "output": "<text>",
  "error": "<message optional>",
  "started_at": "<iso8601>",
  "finished_at": "<iso8601>",
  "duration_ms": <int>,
  "container": "<task_id>",
  "attempt": 1,
  "logs": []
}
```
- When `reply_to` is present, write to that key instead of the prefix.
- Queue worker also RPUSHes the same JSON onto `<results_prefix>list` (optional) for stream-like consumption.

### Components
- **HeadlessChannel** (new): implements `ChannelAdapter` to emit progress/results to stdout (CLI) or Redis (queue). `send_update` optional; `deliver_result/error` writes structured result JSON (and sets TTL when Redis).
- **Headless helpers** (new module): job parsing, message construction, one-shot runner, and Redis worker loop.
- **Redis worker**:
  - Connect via `redis.asyncio` using `settings.redis_url`.
  - Loop: `BLPOP jobs_key` ➜ parse/validate job ➜ build task_id (`headless:<correlation_id or uuid>`) ➜ create HeadlessChannel (redis mode) ➜ enqueue message to TaskRunner ➜ await completion ➜ write result JSON via `SETEX` (TTL) and `RPUSH` to results list if configured.
  - Malformed job: publish failed result and continue.
  - Timeout: apply job timeout or `headless_timeout_seconds`; publish `timeout` status.
  - Graceful shutdown: stop popping on SIGINT/SIGTERM, wait for in-flight task, close Redis.
- **Headless one-shot**:
  - Parse payload from CLI flag or file; generate correlation_id if absent.
  - Create HeadlessChannel in stdout mode (optionally also Redis if provided).
  - Enqueue once, wait for completion with timeout fallback; print result JSON to stdout.

### Config surface (Settings)
- `headless_mode`: "matrix" | "headless" | "queue" (default "matrix").
- `headless_default_workflow`: optional fallback when workflow not supplied (CLI only).
- `redis_url`: default `redis://localhost:6379/0`.
- `redis_jobs_key`: default `matrix-tui:jobs`.
- `redis_results_prefix`: default `matrix-tui:results:`.
- `redis_results_list`: optional list key (default `matrix-tui:results:list`).
- `redis_result_ttl_seconds`: default 86400.
- `headless_timeout_seconds`: default to `coding_timeout_seconds` when unset.
- `headless_progress_enabled`: default False (progress to Redis optional).

### Error handling & shutdown
- Redis unavailable: log and backoff; exit non-zero on repeated failure in worker mode.
- Malformed job: return failed result to `reply_to`/default key and continue.
- Timeout: publish timeout status.
- Shutdown: trap SIGINT/SIGTERM; stop intake; wait for current task; close Redis; let TaskRunner shutdown cleanly.

## Testing Strategy
- **Config**: defaults/overrides for new settings.
- **CLI parsing**: mode selection and payload file/JSON handling; default path still boots Matrix.
- **HeadlessChannel**: stdout vs Redis publishing (fakeredis), status payload shape.
- **Headless one-shot**: enqueues to TaskRunner mock, prints JSON result, handles errors/timeouts.
- **Redis worker**: happy path, malformed job handling, timeout path, TTL application (fakeredis), single in-flight task, graceful shutdown.
- **Regression**: ensure Matrix/GitHub paths unchanged when mode=matrix.

## Open Points
- None — decisions above already approved by @nisto (6114).
