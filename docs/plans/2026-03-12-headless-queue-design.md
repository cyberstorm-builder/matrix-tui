# Headless / Queue-Driven Execution Mode — Design

Date: 2026-03-12
Issue: https://100.73.228.90:3000/openclaw/nisto-home/issues/282
Repository: https://github.com/cyberstorm-dev/matrix-tui

## Summary
We need a headless execution path so matrix-tui can be triggered without Matrix rooms and can run from CLI or a Redis-backed queue. The new mode must be additive (no regression to Matrix/GitHub paths) and expose a job schema + CLI/queue entrypoints for workflows. This document captures the options, chosen approach, job schema, and testing/operational notes. Implementation will follow in a separate plan.

## Current state
- Entry point (`__main__.py`) always boots Matrix (`Bot`) plus optional GitHub webhook channel; no CLI/queue modes.
- `TaskRunner` mediates between channels and `Decider`/`SandboxManager`. Channels today: Matrix (chat) and GitHub (webhook path).
- No Redis dependency, no console scripts; only `uv run python -m matrix_agent` is supported.

## Goals (from issue)
- Run matrix-tui non-interactively (no Matrix room).
- Support direct CLI invocation (e.g., `matrix-tui --headless --workflow <name> --payload <file|json>`).
- Support queued execution via Redis list/stream; publish results back to Redis.
- Preserve existing Matrix behavior; headless is additive.

## Assumptions & open questions (need confirmation on issue)
1) Workflows: initial set acceptable as `agent` (Decider-based general task) and `github_issue` (reuse existing GitHub path)?
2) Redis primitive: is a simple list (BRPOP/BLPOP) acceptable, or do we need streams with consumer groups?
3) Result shape: is `status/output/logs` sufficient, or do we need to stream progress + artifacts (files) back to Redis?
4) Auth/secrets: is Redis assumed unauthenticated on trusted network, or should we require `REDIS_URL` with credentials + TLS toggles?
5) Concurrency limits: is a single worker process sufficient, or should we allow configurable concurrency >1 on queue consumption?

## Options considered
- **A. Reuse TaskRunner with a new Headless channel + mode switch**
  - Pros: minimal duplication; keeps container lifecycle, histories, and validation consistent; additive to existing channels.
  - Cons: need CLI/Redis plumbing and a new channel implementation.
- **B. Standalone headless runner that bypasses TaskRunner and calls Decider directly**
  - Pros: lighter for single-shot runs.
  - Cons: duplicates sandbox lifecycle, skips host-controlled GitHub pipeline, harder to share validation/history.
- **C. Add an HTTP API for job submission/results instead of Redis**
  - Pros: familiar REST pattern.
  - Cons: adds new surface area and infra; doesn’t meet Redis queue ask.

**Chosen: Option A** — add a headless mode that reuses TaskRunner/SandboxManager with a new Channel adapter, plus CLI/Redis entrypoints. This keeps behavior aligned with Matrix/GitHub paths and limits new surface area to input/output plumbing.

## Proposed design

### Mode selection & CLI
- Introduce a console script `matrix-tui` (via `project.scripts`) with subcommands:
  - `serve-matrix` (default): current behavior (Matrix + GitHub webhook).
  - `run-job`: one-off headless execution; args `--workflow`, `--payload`, `--payload-file`, `--correlation-id`, `--result-path` (stdout if omitted).
  - `queue-worker`: run Redis consumer; args `--redis-url`, `--job-queue`, `--result-prefix`, `--concurrency`, `--poll-interval`, `--result-ttl`.
  - `enqueue`: helper to push a job to Redis using the schema below.
- `__main__.py` becomes an argument dispatcher instead of always booting Matrix; default subcommand remains matrix-serving to preserve existing behavior.

### Settings / configuration
Add to `Settings` (env + CLI overrides):
- `headless_enabled: bool = False` (gate Redis dependency)
- `redis_url: str = "redis://localhost:6379/0"`
- `redis_job_queue: str = "matrix-tui:jobs"`
- `redis_result_prefix: str = "matrix-tui:results:"`
- `redis_result_ttl_seconds: int = 3600`
- `headless_max_concurrency: int = 1`
- `headless_poll_interval_seconds: int = 1`
- `headless_default_workflow: str = "agent"`
CLI flags override these for `run-job`/`queue-worker`/`enqueue`.

### Job schema (shared by CLI + Redis)
```json
{
  "workflow": "agent",              // required; workflow handler name
  "payload": {"message": "..."},   // required; schema per workflow
  "correlation_id": "abc123",       // required for queue; auto-generated for CLI if missing
  "reply_to": "matrix-tui:results", // optional override for result channel/list prefix
  "artifacts": ["logs"],            // optional: requested artifacts
  "meta": {"requested_by": "..."}  // optional metadata
}
```
- Validation layer ensures required keys and types; rejects unknown workflows.
- Job IDs for TaskRunner: `hl-<correlation_id>` to avoid collision with Matrix/GitHub prefixes.

### Workflows (initial set)
- `agent` (default): run Decider/TaskRunner once with `payload.message` as the user text; uses HeadlessChannel for output.
- `github_issue` (optional if desired): reuse `_process_github` path; payload must include `repository`, `title`, `body`, and flags for `ci_fix`.
- Extensible registry mapping workflow name → handler function that prepares the message + channel behavior.

### HeadlessChannel (new ChannelAdapter)
- `send_update`: optionally emit progress as newline-delimited JSON (NDJSON) on stdout or Redis `results:<cid>:stream` if requested.
- `deliver_result`: write final result envelope `{status, output, correlation_id, workflow, duration}` to stdout or Redis result key (string) with TTL.
- `deliver_error`: same envelope with `status=error`.
- `is_valid`: always True for CLI; for queue worker, checks Redis key still present if we add cancellation later (stretch).

### Redis queue worker
- Uses `redis.asyncio`; consumes from `redis_job_queue` via `BLPOP` (configurable timeout/poll interval).
- For each job:
  - Parse/validate schema; emit error result if invalid.
  - Register HeadlessChannel bound to correlation_id.
  - Enqueue to TaskRunner with task_id `hl-<cid>` and message constructed from payload/workflow.
  - Optionally publish a "started" result envelope.
- Concurrency: semaphore-limited per-process; each task runs through TaskRunner (containers isolated per cid).
- Results: write to `f"{redis_result_prefix}{cid}"` as JSON string; set TTL. If streaming enabled, append progress lines to a list or pubsub channel (configurable later, default disabled).

### One-off CLI (`run-job`)
- Accept `--payload` as inline JSON or `--payload-file @path`.
- Runs event loop, executes the same workflow registry using HeadlessChannel that writes to stdout (NDJSON for progress + final JSON line).
- Exit code 0 on success, non-zero on validation/processing error.

### Graceful shutdown / resiliency
- Signal handlers mirror current `__main__`: on SIGINT/SIGTERM stop queue polling, cancel in-flight tasks, call `task_runner.shutdown()`, close Redis connection, and `sandbox.save_state()`.
- Backoff: if Redis unavailable, log and retry with exponential backoff up to a cap, then exit non-zero.

### Observability & logging
- Include `correlation_id` and `workflow` in log context.
- Validate and log job parse failures with the original payload (truncated) and send error result to Redis/stdout.
- Optionally add `--quiet/--verbose` flags for CLI output shaping.

### Security considerations
- Do not log payload contents when they may contain secrets; truncate in logs.
- Require explicit `redis_url` with auth/tls if needed; note in docs how to format URLs with credentials.
- Keep Matrix/GitHub paths unchanged to avoid new regressions.

### Documentation updates
- README: add section for headless/queue usage with examples for CLI run and Redis enqueue/consume.
- New doc (this file) checked in under `docs/plans`.
- Mention new env vars and dependency (`redis` Python package) in install instructions.

### Testing strategy
- Unit: job schema validator, workflow registry dispatch, HeadlessChannel behavior (stdout/Redis stubs), CLI argument parsing.
- Queue: use `fakeredis` or injected Redis client to simulate `BLPOP`/writes; ensure TTL applied and errors reported.
- Regression: ensure existing Matrix/GitHub tests still pass; add a smoke test that `serve-matrix` path still wires Bot + GitHubChannel.
- Optional integration (later): run a tiny Redis in CI and execute `queue-worker` against it with a dummy workflow.

## Risks / open points
- Need confirmation on workflow set + Redis primitive (list vs stream).
- Progress streaming to Redis may require choosing list vs pubsub; initial cut will stick to final-result write unless streaming is requested.
- Concurrency >1 could contend on shared resources; may need limits in SandboxManager if containers are heavy.

## Next steps
- Await feedback/approval on schema + mode toggles.
- Once approved, implement according to the upcoming plan (see implementation plan to be drafted with writing-plans).
