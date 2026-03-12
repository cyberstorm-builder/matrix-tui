# Headless / Queue-Driven Mode Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a headless execution path (CLI + Redis queue) that reuses TaskRunner/Decider, provides a job schema/workflow registry, and keeps Matrix/GitHub behavior unchanged.

**Architecture:** Add a new CLI entrypoint with subcommands for matrix serve, one-off headless run, queue worker, and enqueue helper. Implement a headless workflow registry and Channel adapter that routes jobs through TaskRunner/SandboxManager. Add Redis-backed consumer for queued jobs and document configuration/env vars.

**Tech Stack:** Python 3.12, asyncio, aiohttp (existing), litellm (existing), redis.asyncio (new dep), argparse/typer (CLI), pytest/pytest-asyncio, fakeredis (tests).

---

### Task 1: Add dependencies, settings, and CLI scaffold

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/matrix_agent/config.py`
- Modify: `src/matrix_agent/__main__.py`
- Create: `src/matrix_agent/cli.py`
- Test: `tests/test_cli.py`

**Step 1: Write the failing test**

```python
# tests/test_cli.py
import json
from matrix_agent.cli import parse_args

def test_cli_run_job_defaults():
    args = parse_args([
        "run-job", "--workflow", "agent", "--payload", '{"message":"hi"}'
    ])
    assert args.command == "run-job"
    assert args.workflow == "agent"
    assert args.payload == {"message": "hi"}
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_cli_run_job_defaults -q`
Expected: FAIL (module/parse_args missing).

**Step 3: Write minimal implementation**

- Add `redis` (and `fakeredis` to dev extras) to `pyproject.toml`.
- Extend `Settings` with headless/Redis fields (url, queues, ttl, concurrency, poll interval, default workflow, headless_enabled flag).
- Add `cli.py` with `parse_args()` returning a namespace for subcommands (`serve-matrix`, `run-job`, `queue-worker`, `enqueue`).
- Update `__main__.py` to dispatch to CLI instead of always running Matrix.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add pyproject.toml src/matrix_agent/config.py src/matrix_agent/cli.py src/matrix_agent/__main__.py tests/test_cli.py
git commit -m "feat: scaffold headless CLI and settings"
```

---

### Task 2: Workflow registry + HeadlessChannel

**Files:**
- Create: `src/matrix_agent/headless.py`
- Modify: `src/matrix_agent/core.py` (if needed for task_id prefix helpers)
- Test: `tests/test_headless_channel.py`

**Step 1: Write the failing test**

```python
# tests/test_headless_channel.py
import asyncio
from matrix_agent.headless import HeadlessChannel, ResultSink

async def test_headless_channel_collects_results():
    sink = ResultSink()
    channel = HeadlessChannel(result_sink=sink, correlation_id="abc", workflow="agent")
    await channel.send_update("task1", "progress")
    await channel.deliver_result("task1", "done", status="completed")
    assert sink.progress == ["progress"]
    assert sink.final["status"] == "completed"
    assert sink.final["output"] == "done"

# mark as pytest + asyncio
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_headless_channel.py::test_headless_channel_collects_results -q`
Expected: FAIL (module missing).

**Step 3: Write minimal implementation**

- Create `HeadlessChannel` implementing `ChannelAdapter` with stdout/collector sink (no Redis yet).
- Add `ResultSink` helper to accumulate progress and final envelope (status/output/correlation_id/workflow/duration).
- Ensure `is_valid` returns True for now; allow optional cancellation hook later.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_headless_channel.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/matrix_agent/headless.py tests/test_headless_channel.py
git commit -m "feat: add headless channel and result sink"
```

---

### Task 3: Workflow handlers + one-off runner

**Files:**
- Modify: `src/matrix_agent/headless.py`
- Modify: `src/matrix_agent/core.py` (helper to build task_id prefix `hl-<cid>`)
- Modify: `src/matrix_agent/cli.py`
- Test: `tests/test_headless_runner.py`

**Step 1: Write the failing test**

```python
# tests/test_headless_runner.py
import asyncio
from matrix_agent.headless import HeadlessRunner
from matrix_agent.config import Settings
from matrix_agent.sandbox import SandboxManager
from matrix_agent.decider import Decider
from matrix_agent.core import TaskRunner

class DummyChannel:
    def __init__(self):
        self.messages = []
    async def send_update(self, task_id, text):
        self.messages.append(text)
    async def deliver_result(self, task_id, text, status="completed"):
        self.final = (status, text)
    async def deliver_error(self, task_id, error):
        self.error = error
    async def is_valid(self, task_id):
        return True
    async def start(self):
        return None
    async def stop(self):
        return None

async def test_agent_workflow_builds_message(monkeypatch):
    settings = Settings(matrix_password="x", llm_api_key="k", gemini_api_key="g", dashscope_api_key="d")
    sandbox = SandboxManager(settings)
    decider = Decider(settings, sandbox)
    runner = HeadlessRunner(settings, sandbox, decider)

    # stub decider.handle_message to avoid LLM calls
    async def fake_handle_message(task_id, message, send_update=None, system_prompt=None):
        yield ("ok:" + message, None, "completed")
    runner._decider.handle_message = fake_handle_message  # type: ignore

    result = await runner.run_job(workflow="agent", payload={"message": "hello"}, correlation_id="abc")
    assert result["status"] == "completed"
    assert "hello" in result["output"]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_headless_runner.py::test_agent_workflow_builds_message -q`
Expected: FAIL (HeadlessRunner missing).

**Step 3: Write minimal implementation**

- Implement `HeadlessRunner` with workflow registry (agent default) mapping workflow name → async handler.
- Agent workflow: constructs message from payload["message"], enqueues via TaskRunner using `HeadlessChannel` and returns final envelope (use ResultSink to wait for completion).
- Provide helper to run one-off job from CLI (`run_job`), generating correlation_id if absent.
- Update CLI to wire `run-job` subcommand to `HeadlessRunner.run_job` with stdout output.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_headless_runner.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/matrix_agent/headless.py src/matrix_agent/cli.py tests/test_headless_runner.py
git commit -m "feat: add headless workflow runner with agent workflow"
```

---

### Task 4: Redis queue worker + enqueue helper

**Files:**
- Modify: `src/matrix_agent/headless.py`
- Modify: `src/matrix_agent/cli.py`
- Test: `tests/test_headless_queue.py`

**Step 1: Write the failing test**

```python
# tests/test_headless_queue.py
import asyncio
import json
import fakeredis.aioredis
from matrix_agent.headless import QueueWorker

async def test_queue_worker_consumes_job(monkeypatch):
    redis = fakeredis.aioredis.FakeRedis()
    worker = QueueWorker(redis=redis, job_queue="jobs", result_prefix="results:")

    await redis.rpush("jobs", json.dumps({
        "workflow": "agent", "payload": {"message": "hi"}, "correlation_id": "cid1"
    }))

    # stub runner
    class DummyRunner:
        async def run_job(self, workflow, payload, correlation_id):
            return {"status": "completed", "output": "ok", "correlation_id": correlation_id}
    worker.runner = DummyRunner()

    await worker.process_once()
    stored = await redis.get("results:cid1")
    assert json.loads(stored)["status"] == "completed"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_headless_queue.py::test_queue_worker_consumes_job -q`
Expected: FAIL (QueueWorker missing).

**Step 3: Write minimal implementation**

- Implement `QueueWorker` using `redis.asyncio`; methods `process_once()` (single pop), `run_forever()` (loop with sleep/backoff), writes results with TTL.
- Validate job schema, return error result on parse failure, use runner.run_job to execute.
- Update CLI `queue-worker` to instantiate QueueWorker with Settings and start loop; add `enqueue` subcommand writing validated job to queue.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_headless_queue.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/matrix_agent/headless.py src/matrix_agent/cli.py tests/test_headless_queue.py
git commit -m "feat: add Redis queue worker and enqueue helper"
```

---

### Task 5: Documentation and regression checks

**Files:**
- Modify: `README.md`
- Modify: `docs/plans/2026-03-12-headless-queue-design.md` (link to plan if needed)
- Test: `uv run ruff check .` and `uv run pytest`

**Step 1: Write the failing test**

- Add a small doc lint check placeholder in `tests/test_cli.py` or create `tests/test_docs.py` asserting README mentions headless commands.

```python
# tests/test_docs.py
def test_readme_mentions_headless():
    import pathlib
    readme = pathlib.Path(__file__).parents[1] / "README.md"
    assert "headless" in readme.read_text().lower()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_docs.py::test_readme_mentions_headless -q`
Expected: FAIL (text not present).

**Step 3: Write minimal implementation**

- Document new CLI/Redis usage, env vars, and job schema in README.
- Link design + plan docs if helpful.

**Step 4: Run test to verify it passes**

Run: `uv run pytest -q`
Expected: PASS (including new doc test).

**Step 5: Commit**

```bash
git add README.md docs/plans/2026-03-12-headless-queue-design.md tests/test_docs.py
git commit -m "docs: describe headless and queue-driven modes"
```

---

### Execution handoff
Plan complete and saved to `docs/plans/2026-03-12-headless-queue-implementation-plan.md`. Two execution options:
1) Subagent-Driven (this session) — dispatch subagent per task using superpowers:subagent-driven-development.
2) Parallel Session — open new session and use superpowers:executing-plans to run tasks with checkpoints.
