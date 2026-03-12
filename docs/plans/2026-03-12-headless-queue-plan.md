# Headless / Queue-Driven Execution Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a headless mode that can run workflows via CLI or Redis queue without Matrix room presence, reusing the existing TaskRunner/Decider pipeline and publishing results back to Redis.

**Architecture:** Introduce job schema + headless/Redis channels that plug into TaskRunner. Provide Typer-based CLI for one-shot runs, enqueueing jobs, and running a Redis worker. Wire __main__ to optionally skip Matrix and start the headless worker.

**Tech Stack:** Python 3.12, Typer/Click, redis.asyncio (or fakeredis for tests), pytest/pytest-asyncio.

---

### Task 1: Dependencies and settings scaffold

**Files:**
- Modify: `pyproject.toml`, `uv.lock`
- Modify: `src/matrix_agent/config.py`
- Modify/Create: `tests/test_config.py` additions

**Step 1: Write failing test**
- Add config tests asserting new settings defaults and env overrides for `headless_enabled`, Redis connection fields, and headless concurrency.

**Step 2: Run failing test**
- Run: `./.venv/bin/python -m pytest tests/test_config.py -k headless -v`
- Expect: failures for missing attributes/defaults.

**Step 3: Implement minimal code**
- Add redis dependency (`redis[asyncio]`) and fakeredis (dev) to pyproject/uv.lock.
- Extend `Settings` with headless + Redis fields and sensible defaults.

**Step 4: Run tests**
- Run: `./.venv/bin/python -m pytest tests/test_config.py -k headless -v`
- Expect: pass.

**Step 5: Commit**
- `git add pyproject.toml uv.lock src/matrix_agent/config.py tests/test_config.py`
- `git commit -m "feat: add headless and redis settings"`

---

### Task 2: Job schema and channels

**Files:**
- Create: `src/matrix_agent/headless.py`
- Modify: `src/matrix_agent/channels.py` (if reusing base types)
- Create: `tests/test_headless.py`

**Step 1: Write failing tests**
- Define tests for `HeadlessJob` parsing (defaults for correlation_id/task_id), `HeadlessChannel` behavior (captures updates/final status), and `RedisChannel` writing results dict with TTL using fakeredis.

**Step 2: Run failing tests**
- Run: `./.venv/bin/python -m pytest tests/test_headless.py -v`
- Expect: failures for missing implementations.

**Step 3: Implement minimal code**
- Add dataclass for job schema + validation helpers.
- Implement `HeadlessChannel` (stdout/stderr) and `RedisChannel` (writes to redis hash/stream; handles send_update/deliver_result/deliver_error/is_valid stubs).

**Step 4: Run tests**
- Run: `./.venv/bin/python -m pytest tests/test_headless.py -v`
- Expect: pass.

**Step 5: Commit**
- `git add src/matrix_agent/headless.py src/matrix_agent/channels.py tests/test_headless.py`
- `git commit -m "feat: add headless job schema and channels"`

---

### Task 3: Redis worker backend

**Files:**
- Create: `src/matrix_agent/queue_worker.py`
- Create: `tests/test_queue_worker.py`

**Step 1: Write failing tests**
- Use fakeredis to assert: worker pulls from list/stream, dispatches to injected TaskRunner stub, writes status transitions (`queued` → `in_progress` → `succeeded|failed`) to Redis, applies TTL, and retries/acks per config.

**Step 2: Run failing tests**
- Run: `./.venv/bin/python -m pytest tests/test_queue_worker.py -v`
- Expect: failures for missing worker.

**Step 3: Implement minimal code**
- Implement async worker with pluggable backend (list vs stream), retry/backoff, and pending-claim logic for streams. Accept TaskRunner + Settings + redis client + factory for channels.

**Step 4: Run tests**
- Run: `./.venv/bin/python -m pytest tests/test_queue_worker.py -v`
- Expect: pass.

**Step 5: Commit**
- `git add src/matrix_agent/queue_worker.py tests/test_queue_worker.py`
- `git commit -m "feat: add redis queue worker"`

---

### Task 4: CLI surface and entrypoint wiring

**Files:**
- Create: `src/matrix_agent/cli.py`
- Modify: `src/matrix_agent/__main__.py`
- Create/Modify: `tests/test_cli.py`

**Step 1: Write failing tests**
- CLI tests using Typer's CliRunner: headless run prints results, enqueue pushes JSON into Redis, worker flag starts worker loop (stubbed TaskRunner) without booting Matrix when `--headless` or env set.

**Step 2: Run failing tests**
- Run: `./.venv/bin/python -m pytest tests/test_cli.py -v`
- Expect: failures for missing commands.

**Step 3: Implement minimal code**
- Build Typer app with subcommands `headless run|enqueue|worker`.
- Update `__main__.py` to dispatch to CLI when headless flag/env present; preserve Matrix/GitHub path as default.

**Step 4: Run tests**
- Run: `./.venv/bin/python -m pytest tests/test_cli.py -v`
- Expect: pass.

**Step 5: Commit**
- `git add src/matrix_agent/cli.py src/matrix_agent/__main__.py tests/test_cli.py`
- `git commit -m "feat: add headless CLI entrypoints"`

---

### Task 5: Docs and examples

**Files:**
- Modify: `README.md`
- Modify/Create: `docs/plans/2026-03-12-headless-queue-design.md` (link plan)

**Step 1: Write failing test**
- (Documentation step; no tests.) Prepare examples for CLI and Redis job schema.

**Step 2: Run lint/tests**
- Run full suite: `./.venv/bin/python -m pytest`
- Expect: all tests passing.

**Step 3: Update docs**
- Add headless/queue usage examples, env vars, and job/result JSON snippets. Link to design + plan docs.

**Step 4: Run tests**
- Run: `./.venv/bin/python -m pytest`
- Expect: pass.

**Step 5: Commit**
- `git add README.md docs/plans/2026-03-12-headless-queue-design.md`
- `git commit -m "docs: add headless/queue usage"`

---

### Finalization
- Push branch: `git push origin headless-queue-mode`
- Open PR against `main` with summary + link to Gitea issue #282.
- Request reviewer `reviewer` and note open questions for approval before implementation proceeds (if needed).
