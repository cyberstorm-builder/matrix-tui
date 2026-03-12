from types import SimpleNamespace

import pytest

from matrix_agent.headless import run_headless_once


class StubTaskRunner:
    def __init__(self):
        self.calls = []
        self.shutdown_called = False

    async def enqueue(self, task_id, message, channel):
        self.calls.append((task_id, message, channel))
        await channel.deliver_result(task_id, "ok")

    async def shutdown(self):
        self.shutdown_called = True


class HangingTaskRunner:
    def __init__(self):
        self.shutdown_called = False
        self.calls = []

    async def enqueue(self, task_id, message, channel):
        self.calls.append((task_id, message, channel))
        # do not resolve

    async def shutdown(self):
        self.shutdown_called = True


@pytest.mark.asyncio
async def test_run_headless_once_runs_and_returns_result():
    args = SimpleNamespace(
        workflow="wf",
        payload_json='{"k":1}',
        payload_file=None,
        correlation_id="cid",
        timeout_seconds=None,
        redis_url=None,
        jobs_key=None,
        results_prefix=None,
        result_ttl_seconds=None,
        progress=False,
    )
    settings = SimpleNamespace(
        headless_timeout_seconds=5,
        headless_default_workflow=None,
        redis_url=None,
        redis_results_prefix="matrix-tui:results:",
        redis_result_ttl_seconds=86400,
        headless_progress_enabled=False,
    )
    task_runner = StubTaskRunner()

    result = await run_headless_once(args, settings, task_runner, sandbox=None, decider=None)

    assert task_runner.calls
    task_id, message, _ = task_runner.calls[0]
    assert task_id == "headless:cid"
    assert "wf" in message
    assert "k" in message
    assert result["status"] == "completed"
    assert result["output"] == "ok"
    assert result["correlation_id"] == "cid"
    assert task_runner.shutdown_called is True


@pytest.mark.asyncio
async def test_run_headless_once_times_out_and_returns_timeout_result():
    args = SimpleNamespace(
        workflow="wf",
        payload_json="{}",
        payload_file=None,
        correlation_id="cid2",
        timeout_seconds=0.01,
        redis_url=None,
        jobs_key=None,
        results_prefix=None,
        result_ttl_seconds=None,
        progress=False,
    )
    settings = SimpleNamespace(
        headless_timeout_seconds=1,
        headless_default_workflow=None,
        redis_url=None,
        redis_results_prefix="matrix-tui:results:",
        redis_result_ttl_seconds=86400,
        headless_progress_enabled=False,
    )
    task_runner = HangingTaskRunner()

    result = await run_headless_once(args, settings, task_runner, sandbox=None, decider=None)

    assert result["status"] == "timeout"
    assert "timed out" in result["error"]
    assert task_runner.shutdown_called is True
