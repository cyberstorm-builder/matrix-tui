# Headless / Queue-Driven Execution Design (2026-03-12)

## Context
- Matrix Agent currently exposes two execution paths: Matrix chat via `MatrixChannel` → `TaskRunner` → `Decider`, and GitHub issues via `GitHubChannel` → `TaskRunner` → `SandboxManager`/`Decider` pipeline.
- Entry point `python -m matrix_agent` always boots the Matrix client (plus GitHub webhook if configured) and relies on room presence to receive work.
- There is no way to run a workflow without joining a Matrix room, nor to feed tasks from an external queue.

## Goals
- Add a headless mode that bypasses Matrix client setup and runs workflows directly from CLI input.
- Add a queue-driven mode that consumes tasks from Redis and publishes results back to Redis.
- Keep Matrix/GitHub behavior unchanged; headless is additive and selectable via flags/env.

## Open Questions (need confirmation before implementation)
1. **Workflow surface:** Which workflows must be first-class in headless mode? (e.g., Matrix-style freeform prompts vs. GitHub `/fix-issue` requests vs. pre-defined templates?)
2. **Result contract:** What result payload do downstream consumers expect? (Raw text, structured JSON with status/errors/artifacts? Should streaming updates be emitted?)
3. **Redis primitive:** Prefer Redis Streams (with consumer groups/acks) or Lists (simple BRPOP) for jobs? Streams enable dedupe/replay but add config overhead.
4. **Retry policy:** Should failed jobs be retried automatically (with backoff/max attempts), or left in `results` as failed for an external orchestrator to requeue?
5. **Secrets/config for headless runs:** Should headless mode read `.env` the same way as Matrix, or allow an alternative config file path?

## Approaches Considered
1. **Reuse `TaskRunner` with new channel adapters (recommended):** Implement `HeadlessChannel` (CLI) and `RedisChannel` (queue) that satisfy `ChannelAdapter`, feed jobs into existing `TaskRunner`/`Decider`/`SandboxManager`. Pros: reuses lifecycle, IPC, history, and error handling; minimal duplication. Cons: requires careful task_id generation and queue lifecycle integration.
2. **Bypass `TaskRunner` with a one-off runner:** Build a new synchronous runner that calls `Decider.handle_message` directly for CLI/queue jobs. Pros: simpler control flow per job. Cons: duplicates sandbox lifecycle, loses recovery/orphan cleanup, diverges from Matrix/GitHub behavior.
3. **External worker process:** Expose a thin CLI that enqueues jobs; require a separate long-running worker that reuses existing Matrix bot container images. Pros: clear separation. Cons: highest operational overhead; still needs adapters to talk to `TaskRunner`.

**Decision:** Proceed with approach (1) — add channel adapters plus CLI/Redis surfaces on top of `TaskRunner`.

## Proposed Design

### Modes
- **CLI headless:** `matrix-agent headless run --workflow <name> --payload <json|path> [--correlation-id <id>] [--output json|text] [--no-stream]` runs a single job, prints updates/results to stdout/stderr, exits with non-zero on failure.
- **Redis queue worker:** `matrix-agent headless worker --redis-url ... [--jobs-key matrix-tui:jobs] [--results-prefix matrix-tui:results] [--stream]` runs an event loop that pulls jobs, dispatches to `TaskRunner`, and publishes results.
- **Redis enqueue helper:** `matrix-agent headless enqueue --workflow ... --payload ... [--correlation-id ...]` pushes a job into the configured queue for external orchestrators.

### Components
- **Settings additions (env vars):**
  - `headless_enabled` flag to allow skipping Matrix client.
  - `redis_url`, `redis_jobs_key` (list) or `redis_jobs_stream`/`redis_consumer_group` (stream), `redis_results_prefix`, `redis_results_ttl_seconds`, `redis_block_ms`, `headless_max_concurrency`.
  - `headless_default_workflow` fallback (e.g., `matrix` vs `github`).
- **Job schema (JSON):**
  ```json
  {
    "workflow": "matrix" | "github",
    "message": "text or /fix-issue payload",
    "repo": "owner/repo" (optional, for github workflow),
    "correlation_id": "<string>",
    "reply_to": "matrix-tui:results" (optional override),
    "artifacts": {"save_files": false},
    "metadata": {"requested_by": "..."}
  }
  ```
  - `task_id` generated as `cli-<uuid>` or `q-<uuid>`; `correlation_id` defaults to the same if omitted.
  - Headless CLI maps flags into this schema; Redis worker expects one JSON object per job entry.
- **HeadlessChannel (CLI):** Implements `ChannelAdapter`; prints `send_update` to stderr (optional), collects final text/status, exits with proper code. `is_valid` always true for current task.
- **RedisChannel (queue):** Implements `ChannelAdapter`; writes updates/results to Redis (hash or stream entry) under `results_prefix:<correlation_id>`, sets TTL. `is_valid` can consult Redis (e.g., cancellation flag) later if needed.
- **Queue worker:**
  - Uses `redis.asyncio` client.
  - Supports Redis **Streams** (preferred for ack/replay) and **Lists** (BRPOP fallback) behind a common interface.
  - For Streams: create consumer group; claim stale pending entries; ack on success/failure; optional retry/backoff before DLQ (e.g., write to `...:dead`).
  - For Lists: BRPOP to pull; on failure re-PUSH with retry counter in payload.
  - Dispatches job to `TaskRunner.enqueue(task_id, message, channel)`; waits for completion via a `Future`/callback from channel.
- **Entry point wiring:** `__main__.py` gains CLI dispatch: if `--headless`/`headless_enabled`, skip Matrix login and either run a one-shot CLI job or start the Redis worker. Matrix+GitHub path remains default when no headless flag is given.
- **Result contract:** Results stored as JSON: `{status: queued|in_progress|succeeded|failed, result: <text>, error: <string|none>, started_at, finished_at, task_id, correlation_id}` with TTL applied. Streaming updates optionally append to a Redis Stream `results_prefix:<corr>:updates` or push incremental fields in the same hash.

### Error Handling & Shutdown
- Signal handlers propagate to worker loop; cancel blocking Redis reads; await `TaskRunner.shutdown()` and close Redis client.
- Retry policy configurable: max attempts + backoff seconds; failed jobs recorded with `status=failed` and `error` field.
- Idempotency: `task_id` unique per dequeue; when using Streams, pending entries claimed on restart to avoid duplicate work.

### Testing Strategy
- Unit tests for job schema parsing/validation and task_id/correlation_id defaults.
- Queue worker tests using `fakeredis` covering: list vs stream backends, retry/ack paths, TTL applied to results, and update publishing.
- HeadlessChannel tests: verify stdout/stderr behavior and exit codes for success/failure.
- Integration-ish test stubbing `TaskRunner._process` or using a lightweight fake channel to ensure jobs round-trip through the worker without needing Podman/Gemini.

### Documentation
- README section for headless usage with CLI examples and environment variables.
- Example Redis job JSON and result payloads; note that Matrix mode remains default and how to opt into headless/queue mode.
