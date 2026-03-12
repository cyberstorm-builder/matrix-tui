# Headless / Queue-Driven Execution Mode — Design

## Context
- Current entrypoint (`matrix_agent.__main__`) always spins up the Matrix client and webhook-driven GitHub channel, then feeds messages into `TaskRunner` via channel adapters.
- `TaskRunner` already abstracts channels (`ChannelAdapter`) for Matrix and GitHub. It streams updates/results via the channel and owns sandbox lifecycle.
- There is no way to run workflows without Matrix rooms or GitHub issues, and no queue-driven ingestion.

## Goals
- Allow non-interactive execution without Matrix rooms.
- Two headless modes:
  - **CLI fire-and-forget**: `matrix-tui --headless --workflow <name> --payload <json|@file>` executes once and writes output to stdout/file.
  - **Queue worker**: listen on Redis (list/stream) for jobs, dispatch them, and publish results back to Redis.
- Preserve existing Matrix+GitHub behaviour (additive flag/config).

## Assumptions / Open Questions
1) Redis primitive: list (BLPOP) vs stream (XREAD/XADD)? (stream preferred for replay/history, list simpler).
2) Delivery semantics: is at-least-once acceptable with idempotent workflows, or do we need explicit ACK/dead-lettering?
3) Expected workflow names and payload schema: is `workflow` arbitrary free text passed to Decider, or should we support a fixed set (e.g., `plan`, `implement`, `review`, `custom_prompt`)?
4) How large can payloads be? (affects Redis stream vs list and chunking).
5) Should we persist job state/results locally (e.g., `state.json`) for recovery, or rely solely on Redis?
6) Do we need a shutdown signal via Redis (e.g., poison pill) in addition to SIGTERM?

## Options considered
1) **Embed headless path into existing `__main__` with flags**
   - Pros: single entrypoint, minimal new files.
   - Cons: `__main__` grows; Matrix/GitHub init still happens unless carefully gated.
2) **Separate headless runner module using existing TaskRunner** (recommended)
   - Pros: clean separation; reuse `TaskRunner`/`Decider`/`SandboxManager`; avoids Matrix startup when not needed.
   - Cons: two entrypoints to maintain; need shared settings parsing.
3) **Standalone Redis worker bypassing TaskRunner**
   - Pros: simple for queue use-case only.
   - Cons: duplicates sandbox/decider logic; diverges from existing channel abstractions.

## Proposed design (option 2)
- Add a new module `matrix_agent/headless.py` providing two commands:
  - `uv run python -m matrix_agent.headless cli --workflow <name> --payload <json|@file> [--output <path>]`
  - `uv run python -m matrix_agent.headless worker [--redis-url redis://... --job-key matrix-tui:jobs --result-key matrix-tui:results --mode list|stream]`
- **Settings additions** (via `Settings`): `headless_enabled` flag (default False), `redis_url`, `redis_job_key`, `redis_result_key`, `redis_mode` (list|stream), `redis_block_seconds`, optional `redis_result_ttl_seconds`.
- **ChannelAdapter: HeadlessChannel**
  - Implements `send_update` (append to in-memory buffer + optionally publish progress to Redis results stream under `correlation_id`), `deliver_result`/`deliver_error` (write JSON with status/output to stdout/file for CLI mode; publish to Redis for worker mode).
  - `is_valid` checks correlation id still tracked (for worker) or always True (CLI).
- **Task identity**
  - `task_id` = `headless-<correlation_id>` to keep sandbox/container mapping deterministic.
  - CLI mode: generate UUID if correlation_id not provided; destroy container on exit.
  - Worker mode: keep per-task sandbox; `TaskRunner.destroy_orphans()` cleans up on restart.
- **Job schema (Redis + CLI)**
  ```json
  {
    "correlation_id": "<string>",
    "workflow": "<name>",
    "payload": { ... } | "<string>",
    "reply_to": "<result key/stream>" (optional overrides default),
    "artifacts": ["stdout", "files"] (optional)
  }
  ```
  - For CLI mode, `payload` can be a raw string or JSON; `--payload @file` reads from file.
  - For queue mode, jobs are JSON strings pushed to Redis list (`LPUSH`/`RPUSH`) or stream (`XADD`).
- **Workflow dispatch**
  - Map workflows to Decider input strings. Example:
    - `workflow=prompt`: feed `payload` as-is to Decider (Matrix-like path).
    - `workflow=plan|implement|review`: prefix payload with `/plan`, `/implement`, `/review` cues to Decider (or extend Decider to support structured invocation).
    - Default: `payload` string sent to Decider.
  - Include `correlation_id` and workflow in the first message sent to Decider for traceability.
- **Queue worker loop**
  - Use `redis-py` asyncio client (`redis.asyncio`) dependency.
  - Connect using `redis_url`.
  - `mode=list`: `BLPOP job_key` with timeout; `mode=stream`: `XREAD BLOCK` from `job_key` stream, track `last_id` for resume; optional `XACK` if we switch to consumer groups.
  - For each job: validate JSON/schema, enqueue to TaskRunner with `HeadlessChannel` configured for Redis result publishing.
  - Publish results to `result_key` (or job.reply_to) as JSON: `{correlation_id, status:completed|failed|max_turns, output, elapsed_seconds, logs?}`; optionally `EXPIRE` when using lists/keys, or `XADD` with `maxlen`.
- **CLI mode flow**
  - Parse flags; build `HeadlessChannel` with stdout/file writer (option `--output` writes JSON to file in addition to stdout).
  - Enqueue single job to TaskRunner and await completion; exit with non-zero on failure.
- **Graceful shutdown**
  - Worker listens for SIGTERM/SIGINT; sets an event; stops consuming new jobs; waits for in-flight job to finish; calls `task_runner.shutdown()` and `sandbox.save_state()`.
  - Redis mode could also treat a `shutdown` job type as a stop signal if desired (open question).
- **Docs**
  - Update README with headless/queue usage examples and job schema.
  - Document environment vars for Redis and headless flags.

## Testing strategy
- Unit tests for `HeadlessChannel` (CLI mode: send_update/deliver_result behaviours; Redis mode: publishes expected payloads).
- Integration-ish tests using `redis.asyncio` with `fakeredis` (if available) or a local Redis service (CI addon) to exercise worker loop for list and stream modes.
- CLI entrypoint tests invoking `matrix_agent.headless.cli` with temporary output file and mocked TaskRunner (use dependency injection or patch) to assert exit codes and output format.
- Ensure existing tests still pass; guard Matrix/GitHub startup behind flags so headless tests don't require Matrix env vars.

## Risks / mitigations
- **Redis dependency**: adds new runtime dep; mitigate with optional import and clear error message when headless enabled without redis installed.
- **Job loss on crash**: list mode without ACK is at-least-once but may lose jobs if popped but not processed; stream + consumer groups can provide safer semantics — need confirmation (see questions).
- **Backwards compatibility**: gate new behaviour behind flags; default path remains Matrix+GitHub.

## Next steps
- Confirm open questions (Redis primitive, workflows, ack semantics, payload limits).
- If approved, implement headless module + settings + tests, then wire docs and CI for redis-backed tests (likely via `redis` service in GitHub Actions).

---

I'm using the writing-plans skill to create the implementation plan.

# Headless / Queue-Driven Execution Mode Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a headless execution path (CLI + Redis queue worker) that reuses TaskRunner/Decider/SandboxManager without requiring Matrix rooms.

**Architecture:** Reuse the existing channel abstraction with a new HeadlessChannel. Add a dedicated `matrix_agent.headless` module exposing CLI and Redis worker commands. Gate Matrix/GitHub startup behind flags and wire new settings for Redis endpoints and headless mode selection.

**Tech Stack:** Python 3.12, asyncio, redis.asyncio client, argparse, existing TaskRunner/ChannelAdapter abstractions, pytest/ruff.

---

### Task 1: Settings and dependency scaffolding

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/matrix_agent/config.py`
- Modify: `tests/test_config.py`

**Step 1: Write the failing test**
- Add tests asserting new Settings fields (`headless_enabled`, `redis_url`, `redis_job_key`, `redis_result_key`, `redis_mode`, `redis_block_seconds`, `redis_result_ttl_seconds`, `headless_output_path`) default correctly and honor env overrides.
- Add tests to ensure existing fields still derive from `vps_ip` even when headless flags are present.

**Step 2: Run test to verify it fails**
- Run: `uv run pytest tests/test_config.py -k headless -v`
- Expected: fails because fields are missing.

**Step 3: Write minimal implementation**
- Add optional dependency `redis>=5` (for `redis.asyncio`).
- Extend `Settings` with the new fields + sensible defaults (headless off by default; redis_url empty; job/result keys defaults; mode default "list"; block seconds 5-10; TTL optional `None`; output path optional `None`).

**Step 4: Run test to verify it passes**
- Run: `uv run pytest tests/test_config.py -k headless -v`
- Expected: pass.

**Step 5: Commit**
- `git add pyproject.toml src/matrix_agent/config.py tests/test_config.py`
- `git commit -m "chore: add headless settings and redis dependency"`

### Task 2: Headless channel + CLI single-run path

**Files:**
- Create: `src/matrix_agent/headless.py`
- Modify: `src/matrix_agent/channels.py` (add HeadlessChannel)
- Modify: `tests/test_channels.py` (add HeadlessChannel unit coverage)
- Create: `tests/test_headless_cli.py`

**Step 1: Write the failing tests**
- In `tests/test_channels.py`, add tests for HeadlessChannel in CLI mode: `send_update` buffers progress; `deliver_result` returns structured payload; `deliver_error` marks failure; `is_valid` true for matching correlation id.
- In `tests/test_headless_cli.py`, mock TaskRunner/HeadlessChannel to assert `cli` command parses `--workflow`/`--payload` (string and @file), builds task_id `headless-<correlation>` (generated when missing), calls `enqueue`, waits for completion, and writes JSON to stdout/file. Use `pytest.mark.asyncio` with `AsyncMock` for TaskRunner.

**Step 2: Run tests to verify they fail**
- Run: `uv run pytest tests/test_channels.py::test_headless_channel_cli tests/test_headless_cli.py -v`
- Expected: fail (HeadlessChannel/CLI not implemented).

**Step 3: Write minimal implementation**
- Implement `HeadlessChannel` in `channels.py` (or new module) subclassing `ChannelAdapter` with modes for CLI vs Redis publishing; configurable correlation_id, optional redis client and result key, supports `send_update` (append to progress list and optionally publish progress events) and `deliver_result`/`deliver_error` returning structured JSON.
- Add `matrix_agent.headless` module using `argparse` with subcommands `cli` and placeholder `worker` (wired but may be completed in Task 3), building Settings, SandboxManager, Decider, TaskRunner, and running a single job through `_process_matrix` path. Support `--output` path to write JSON results.

**Step 4: Run tests to verify they pass**
- Run: `uv run pytest tests/test_channels.py::test_headless_channel_cli tests/test_headless_cli.py -v`
- Expected: pass.

**Step 5: Commit**
- `git add src/matrix_agent/channels.py src/matrix_agent/headless.py tests/test_channels.py tests/test_headless_cli.py`
- `git commit -m "feat: add headless channel and CLI entrypoint"`

### Task 3: Redis worker and queue ingestion

**Files:**
- Modify: `src/matrix_agent/headless.py`
- Create: `tests/test_headless_worker.py`

**Step 1: Write the failing tests**
- Add tests using `fakeredis.aioredis` (or `redis.asyncio` with monkeypatched client) to simulate list and stream modes: enqueue jobs, ensure worker pulls jobs, enqueues into TaskRunner with correct `HeadlessChannel` configured, publishes results to result key/stream with correlation_id and status, and respects `redis_block_seconds`.
- Include test ensuring invalid job payloads are skipped with logged errors, and that worker stops on cancellation event.

**Step 2: Run tests to verify they fail**
- Run: `uv run pytest tests/test_headless_worker.py -v`
- Expected: fail (worker not implemented).

**Step 3: Write minimal implementation**
- In `headless.py`, flesh out `worker` subcommand:
  - Connect to Redis using `redis.asyncio` with provided URL.
  - Support `mode=list` (BLPOP job_key) and `mode=stream` (XREAD block) with `last_id` resume; parse JSON jobs into schema; allow optional `reply_to` override.
  - Create `HeadlessChannel` configured for Redis publishing (result key, redis client, correlation_id) and enqueue to TaskRunner.
  - Await task completion; publish result JSON `{correlation_id, status, output, elapsed_seconds}`; optional TTL when using list/keys; use `XADD` with maxlen for streams.
  - Handle SIGTERM/SIGINT to stop consuming and shut down TaskRunner/SandboxManager cleanly.

**Step 4: Run tests to verify they pass**
- Run: `uv run pytest tests/test_headless_worker.py -v`
- Expected: pass.

**Step 5: Commit**
- `git add src/matrix_agent/headless.py tests/test_headless_worker.py`
- `git commit -m "feat: add redis queue worker for headless mode"`

### Task 4: Documentation and defaults

**Files:**
- Modify: `README.md`
- Create: `docs/plans/2026-03-12-headless-queue-mode-design.md` (already) — append usage examples section
- Modify: `AGENTS.md` (add headless mode gotchas/decisions)

**Step 1: Write the failing test**
- If using docs linting, add a lightweight check in `tests/test_headless_cli.py` verifying help text mentions headless flags (e.g., run `python -m matrix_agent.headless --help` contains `--workflow`).

**Step 2: Run tests to verify they fail**
- Run: `uv run pytest tests/test_headless_cli.py -k help -v`
- Expected: fail (help text not present).

**Step 3: Write minimal implementation**
- Update README with headless/queue usage examples, job schema, required env vars, Redis dependency.
- Append AGENTS.md Decisions/Gotchas for headless (e.g., queue semantics, container naming, cleanup expectations).
- Ensure `headless.py` argparse help strings cover flags.

**Step 4: Run tests to verify they pass**
- Run: `uv run pytest tests/test_headless_cli.py -k help -v`
- Expected: pass.

**Step 5: Commit**
- `git add README.md AGENTS.md docs/plans/2026-03-12-headless-queue-mode-design.md`
- `git commit -m "docs: document headless/queue execution"`

---

Plan complete and saved to `docs/plans/2026-03-12-headless-queue-mode-design.md`.

Two execution options:
1. **Subagent-Driven (this session)** — dispatch fresh subagent per task using superpowers:subagent-driven-development.
2. **Parallel Session (separate)** — open a new session with superpowers:executing-plans and run through the tasks with checkpoints.
