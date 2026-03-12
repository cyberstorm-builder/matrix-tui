# Design: Headless / Queue-Driven Execution Mode

Issue: https://github.com/cyberstorm-dev/matrix-tui/issues/282 (claimed via queue)
Date: 2026-03-12
Owner: builder

## Context & Goals
- Allow matrix-tui to run jobs without joining/sending to a Matrix room.
- Support two entry paths: direct CLI (`--headless --workflow <name> --payload <json|path>`) and Redis-backed queue consumer (`matrix-tui:jobs`).
- Preserve existing Matrix + GitHub behaviors; headless is additive.
- Return results programmatically (stdout for CLI; Redis results channel for queue) with correlation IDs and optional artifacts.

## Assumptions & Open Questions
1) Redis flavor: okay to depend on `redis[asyncio]` (pure Python) and assume a network-accessible Redis URL? Any constraints on using streams vs lists?
2) Workflows: initial set assumed as `decider` (existing LiteLLM loop with sandbox) and `github_issue` (reuse GitHub pipeline). Are additional deterministic workflows expected (e.g., run predefined commands)?
3) Sandbox policy: should headless mode still spin up per-job Podman containers (default yes) or allow an "in-process"/no-sandbox mode for trusted jobs?
4) Result retention: how long should Redis results live (TTL)? Is at-least-once delivery acceptable for queue jobs, or do we need de-duplication/ack tracking?
5) Authentication: for Redis, will AUTH/SSL be required? For GitHub workflows, will PATs still be supplied via env vars as today?

## Current Architecture (abridged)
- `TaskRunner` routes messages to either Matrix (`_process_matrix` via `Decider`) or GitHub (`_process_github` Gemini CI loop). Channels abstract IO (MatrixChannel, GitHubChannel).
- `SandboxManager` creates per-task Podman containers and persists state.
- `__main__.py` wires Settings → Sandbox → Decider → TaskRunner → GitHubChannel + Bot (Matrix), then runs Matrix sync loop.

## Options Considered
1) **Channel-first extension (reuse TaskRunner)** — add a `HeadlessChannel` plus a small dispatcher that converts CLI/Redis jobs into `TaskRunner.enqueue` calls. Pros: minimal duplication, preserves sandbox + timeout behaviors. Cons: need CLI/Redis glue and result publishing layer.
2) **Standalone worker (bypass TaskRunner)** — build a separate headless runner that calls Decider/GitHub pipelines directly. Pros: fewer dependencies on Matrix abstractions. Cons: code drift and duplicated lifecycle logic; risks diverging sandbox behavior.
3) **External job runner (separate process/service)** — leave matrix-tui unchanged and build a new service that shells into matrix-tui containers. Pros: isolates concerns. Cons: more moving parts; harder to keep in sync with repo.

**Choice:** Option 1 — extend the existing channel/TaskRunner architecture with a headless channel and consumer. Keeps one execution path and consistent sandbox handling.

## Proposed Design
### Workflows & Job Schema
- Define a `HeadlessJob` dataclass loaded from CLI/Redis payloads:
  - `workflow`: `"decider"` | `"github_issue"` | future values
  - `payload`: raw dict; validated per workflow
  - `correlation_id`: string (required)
  - `artifacts`: optional list (e.g., `["logs", "ipc"]`); v1: unused placeholder
  - `reply`: optional dict describing where to post results (`{"mode": "stdout"|"redis", "key": "matrix-tui:results:<cid>"}`)
- Workflow handlers:
  - `decider`: use existing `_process_matrix` path (LLM loop) with `payload["message"]` as the message. Task IDs prefixed `cli-<cid>` / `redis-<cid>`.
  - `github_issue`: reuse `_process_github`; expect `payload` containing `repository`, `title`, `body`, optional `ci_context`. Build the message string identical to GitHubChannel (supports CI_FIX when `ci_context` present).

### CLI Headless Mode
- New entrypoint `python -m matrix_agent --headless ...` (argparse, no new deps).
- Flags:
  - `--workflow <name>` (required)
  - `--payload <json|string|@path>` (required)
  - `--correlation-id <id>` (default: uuid4)
  - `--redis-results <key>` and `--redis-url <url>` (optional; if provided, results published to Redis in addition to stdout)
  - `--timeout <seconds>` override (optional)
- Behavior:
  - Build `Settings`, `SandboxManager`, `Decider`, `TaskRunner`.
  - Construct `HeadlessChannel` with stdout + optional Redis publisher.
  - Call `task_runner.enqueue(task_id, message, channel)` and await completion by awaiting the worker queue `.join()` or a completion future exposed by `HeadlessChannel`.
  - Emit final JSON to stdout: `{correlation_id, status, result|error}`.

### Redis Queue Consumer
- Add optional dependency `redis[asyncio]` and a new module `headless_queue.py`.
- Settings additions:
  - `redis_url` (default `redis://localhost:6379/0`)
  - `redis_jobs_key` (default `matrix-tui:jobs`)
  - `redis_results_prefix` (default `matrix-tui:results:`)
  - `redis_mode` (`list` or `stream`, default `list`)
- Consumer loop (list mode): `BLPOP redis_jobs_key` → parse job JSON → validate → dispatch to TaskRunner with `HeadlessChannel` configured to publish results to `results_prefix + correlation_id`.
- Consumer loop (stream mode): `XREADGROUP GROUP matrix-tui workers` with manual `XACK` after completion; if parsing fails, push error to results and ack to avoid poison-pill loops.
- Results payload: `{correlation_id, workflow, status: "completed"|"error"|"timeout", result?, error?, started_at, finished_at}` pushed via `RPUSH` (list) or `XADD` (stream) on `results_key`.
- Graceful shutdown: catch SIGTERM/SIGINT, stop consuming, wait for in-flight TaskRunner workers, then close Redis connection.

### HeadlessChannel
- Implements `ChannelAdapter`:
  - `send_update`: buffer/log intermediate updates; optionally publish incremental chunks to Redis (future); v1 log only.
  - `deliver_result`: write to stdout (CLI) and/or Redis result channel.
  - `deliver_error`: same path with `status="error"`.
  - `is_valid`: always `True` for CLI; for queue, keep an in-memory active set and drop if TTL/timeout exceeded.
- Expose a `completion` future so CLI/consumer can await task completion.

### Error Handling & Timeouts
- Reuse existing `TaskRunner` timeout (coding_timeout_seconds + buffer). Allow CLI flag override.
- Validation errors (bad workflow/payload) are returned immediately as `status="error"` and, for queue mode, sent to results channel to avoid requeue loops.
- Redis connectivity issues should surface and exit non-zero rather than silent failure; document requirement.

### Testing Strategy
- Unit tests for `HeadlessJob` parsing/validation and CLI payload loader.
- Tests for `HeadlessChannel` deliver_result/error writing to a fake Redis (mock) and capturing stdout.
- Queue consumer test using `fakeredis` or mocking redis client to ensure BLPOP/XREAD parsing + error paths.
- Integration-lite test: run headless CLI with `workflow=decider`, `message="echo hi"` stubbed by mocking `TaskRunner._process_matrix` to assert dispatch and result formatting.

### Rollout
- Add optional `redis` extra to dependencies to avoid pulling Redis for Matrix-only installs.
- Document usage in README (headless CLI example + Redis job schema) and mention that existing Matrix flow is unchanged.
- After approval: implement, add tests, wire CI to install redis extra for queue tests if needed.
