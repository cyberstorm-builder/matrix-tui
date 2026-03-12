# Headless / Queue-Driven Execution Mode Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a headless execution path (CLI + Redis queue worker) that reuses TaskRunner/Decider without Matrix login, keeping Matrix/GitHub as the default.

**Architecture:** Extend `__main__.py` with mode selection; implement Headless helpers + HeadlessChannel to publish stdout/Redis results; use Redis Lists for intake/results with per-correlation SETEX TTL; single in-flight job per worker.

**Tech Stack:** Python 3.12, asyncio, argparse, redis.asyncio (fakeredis for tests), pytest, uv.

---

### Task 1: Add Redis/headless settings and dependencies

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock` (regenerate via `uv lock`)
- Modify: `src/matrix_agent/config.py`
- Modify: `tests/test_config.py`

**Step 1: Write the failing test**
- Add tests in `tests/test_config.py` asserting defaults and overrides for: `headless_mode`, `headless_default_workflow`, `redis_url`, `redis_jobs_key`, `redis_results_prefix`, `redis_results_list`, `redis_result_ttl_seconds`, `headless_timeout_seconds` (defaults to `coding_timeout_seconds` when None), and `headless_progress_enabled`.

**Step 2: Run test to verify it fails**
- Command: `uv run pytest tests/test_config.py -k headless -v`
- Expect failure for missing attributes.

**Step 3: Write minimal implementation**
- Add `redis>=5` to dependencies; add `fakeredis` to dev extra.
- Update `Settings` with new fields and derive `headless_timeout_seconds` when unset.

**Step 4: Run test to verify it passes**
- Command: `uv run pytest tests/test_config.py -k headless -v`

**Step 5: Commit**
- `git add pyproject.toml uv.lock src/matrix_agent/config.py tests/test_config.py`
- `git commit -m "chore: add headless redis settings"`

---

### Task 2: CLI mode selection in `__main__.py`

**Files:**
- Modify: `src/matrix_agent/__main__.py`
- Add: `tests/test_cli_headless.py`

**Step 1: Write the failing test**
- In `tests/test_cli_headless.py`, add argparse tests for:
  - Default invocation (no args) selects matrix mode and calls Matrix boot stub.
  - `--mode headless --workflow wf --payload-json '{"a":1}'` parses payload and bypasses Matrix setup.
  - `--mode queue --redis-url redis://x --jobs-key jobs --results-prefix res:` routes to queue runner stub with parsed args.

**Step 2: Run test to verify it fails**
- Command: `uv run pytest tests/test_cli_headless.py -v`

**Step 3: Write minimal implementation**
- Add argparse parsing with `--mode {matrix,headless,queue}` plus shared flags for workflow, payload-json/file, correlation-id, timeout, redis config, progress flag.
- Branch to existing Matrix/GitHub path when mode=matrix; call headless/queue helpers otherwise.

**Step 4: Run test to verify it passes**
- Command: `uv run pytest tests/test_cli_headless.py -v`

**Step 5: Commit**
- `git add src/matrix_agent/__main__.py tests/test_cli_headless.py`
- `git commit -m "feat: add headless/queue cli modes"`

---

### Task 3: HeadlessChannel + headless helpers (stdout/Redis publishing)

**Files:**
- Add: `src/matrix_agent/headless.py`
- Modify: `src/matrix_agent/channels.py` (export/import as needed)
- Add: `tests/test_headless_channel.py`

**Step 1: Write the failing test**
- In `tests/test_headless_channel.py`, cover:
  - stdout mode: `deliver_result` / `deliver_error` emit JSON with status/output/error/correlation_id and timestamps to a StringIO writer.
  - redis mode: using fakeredis, `deliver_result`/`deliver_error` call `setex` on `<results_prefix><correlation_id>` with TTL and payload; optional `rpush` to results list when configured.
  - `send_update` no-op unless progress flag set (then writes a progress entry to Redis list).

**Step 2: Run test to verify it fails**
- Command: `uv run pytest tests/test_headless_channel.py -v`

**Step 3: Write minimal implementation**
- Implement `HeadlessChannel` with `send_update`, `deliver_result`, `deliver_error`, `is_valid` and helper to build result dict (started/finished timestamps, duration_ms, status/output/error/workflow/correlation_id/container).
- Support stdout writer + optional Redis client (`setex` + optional `rpush`).

**Step 4: Run test to verify it passes**
- Command: `uv run pytest tests/test_headless_channel.py -v`

**Step 5: Commit**
- `git add src/matrix_agent/headless.py src/matrix_agent/channels.py tests/test_headless_channel.py`
- `git commit -m "feat: add headless channel"`

---

### Task 4: Headless one-shot runner

**Files:**
- Modify: `src/matrix_agent/headless.py`
- Add: `tests/test_headless_run.py`

**Step 1: Write the failing test**
- In `tests/test_headless_run.py`, using stub TaskRunner/Decider:
  - Ensure `run_headless_once(...)` builds task_id `headless:<correlation>` (UUID if none), enqueues message with workflow/payload text, and returns result JSON printed to stdout.
  - Test timeout path publishes `status="timeout"`.

**Step 2: Run test to verify it fails**
- Command: `uv run pytest tests/test_headless_run.py -v`

**Step 3: Write minimal implementation**
- Add helper to parse payload JSON or file, build message, create HeadlessChannel stdout mode, enqueue once, await completion with timeout fallback, print result JSON, optionally also write to Redis if configured.

**Step 4: Run test to verify it passes**
- Command: `uv run pytest tests/test_headless_run.py -v`

**Step 5: Commit**
- `git add src/matrix_agent/headless.py tests/test_headless_run.py`
- `git commit -m "feat: add headless one-shot runner"`

---

### Task 5: Redis queue worker (BLPOP)

**Files:**
- Modify: `src/matrix_agent/headless.py`
- Add: `tests/test_headless_worker.py`

**Step 1: Write the failing test**
- In `tests/test_headless_worker.py` (fakeredis + stub TaskRunner):
  - Push a valid job onto jobs list; worker BLPOPs, enqueues with task_id `headless:<correlation>`, and writes completed result via SETEX + RPUSH (list) with TTL.
  - Malformed job (missing workflow/payload) yields failed result and continues.
  - Timeout scenario publishes `timeout` status.
  - Worker stops cleanly when stop event is set.

**Step 2: Run test to verify it fails**
- Command: `uv run pytest tests/test_headless_worker.py -v`

**Step 3: Write minimal implementation**
- Implement `run_queue_worker(settings, task_runner, stop_event)` using redis.asyncio: BLPOP with timeout; parse JSON; select correlation_id or generate; create HeadlessChannel (redis mode) with result key/list and TTL; enqueue message; wait for completion or timeout; write result; respect stop_event/SIGINT/SIGTERM.

**Step 4: Run test to verify it passes**
- Command: `uv run pytest tests/test_headless_worker.py -v`

**Step 5: Commit**
- `git add src/matrix_agent/headless.py tests/test_headless_worker.py`
- `git commit -m "feat: add redis queue worker"`

---

### Task 6: Integrate TaskRunner interactions and defaults

**Files:**
- Modify: `src/matrix_agent/core.py` (if needed for headless prefix helpers)
- Modify: `src/matrix_agent/__main__.py` (wire helpers into main flow)
- Modify/Add tests: extend `tests/test_cli_headless.py` or add integration to confirm matrix path unchanged and headless/queue paths call helpers.

**Step 1: Write the failing test**
- Add regression ensuring default mode still starts Matrix/GitHub; headless/queue modes skip Matrix setup and call headless helpers.

**Step 2: Run test to verify it fails**
- Command: `uv run pytest tests/test_cli_headless.py -k default -v`

**Step 3: Write minimal implementation**
- Wire `main()` to branch based on parsed mode; ensure Matrix bot startup is skipped in headless/queue paths; share settings/task_runner instantiation where possible.

**Step 4: Run test to verify it passes**
- Command: `uv run pytest tests/test_cli_headless.py -v`

**Step 5: Commit**
- `git add src/matrix_agent/core.py src/matrix_agent/__main__.py tests/test_cli_headless.py`
- `git commit -m "chore: wire headless/queue into entrypoint"`

---

### Task 7: Docs + full test suite

**Files:**
- Modify: `README.md` (add headless/queue usage, job/result schema)
- Add: `docs/headless.md` (detailed examples, defaults, error handling)

**Step 1: Write the failing test**
- (Manual/doc task) Optionally snapshot help text in `tests/test_cli_headless.py` to include new modes.

**Step 2: Run test to verify it fails**
- Command: `uv run pytest tests/test_cli_headless.py -k help -v` (if added) or note manual verification.

**Step 3: Write minimal implementation**
- Document CLI flags, job schema, result schema, Redis defaults (lists, TTL 24h, single concurrency), and examples for headless run + queue worker.

**Step 4: Run test to verify it passes**
- Command: `uv run pytest -v`
- Command: `uv run ruff check src tests` (if configured)

**Step 5: Commit**
- `git add README.md docs/headless.md tests/test_cli_headless.py`
- `git commit -m "docs: add headless/queue usage"`

---

### Task 8: Final verification

**Files:**
- N/A (repo state)

**Step 1: Run full suite**
- Command: `uv run pytest -v`
- Command: `uv run ruff check src tests`

**Step 2: Commit any fixes**
- `git add ...`
- `git commit -m "chore: fix headless test regressions"`

**Step 3: Push branch + open PR**
- `git push -u origin feat/headless-queue-mode`
- Open PR against `main` with summary + `Closes #282`.
