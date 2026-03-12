import asyncio
import json
from types import SimpleNamespace

import fakeredis
import pytest

from matrix_agent.headless import run_queue_worker


class StubTaskRunner:
    def __init__(self, result_text="done"):
        self.calls = []
        self.shutdown_called = 0
        self.result_text = result_text

    async def enqueue(self, task_id, message, channel):
        self.calls.append((task_id, message, channel))
        await channel.deliver_result(task_id, self.result_text)

    async def shutdown(self):
        self.shutdown_called += 1


class HangingTaskRunner:
    def __init__(self):
        self.calls = []
        self.shutdown_called = 0

    async def enqueue(self, task_id, message, channel):
        self.calls.append((task_id, message, channel))
        # do not resolve

    async def shutdown(self):
        self.shutdown_called += 1


@pytest.mark.asyncio
async def test_queue_worker_processes_job_and_writes_result():
    redis = fakeredis.aioredis.FakeRedis()
    jobs_key = "matrix-tui:jobs"
    job = {"workflow": "wf", "payload": {"a": 1}, "correlation_id": "job1"}
    await redis.rpush(jobs_key, json.dumps(job))

    args = SimpleNamespace(
        mode="queue",
        redis_url=None,
        jobs_key=jobs_key,
        results_prefix="results:",
        result_ttl_seconds=5,
        timeout_seconds=5,
        progress=False,
    )
    settings = SimpleNamespace(
        redis_url=None,
        redis_jobs_key="matrix-tui:jobs",
        redis_results_prefix="results:",
        redis_result_ttl_seconds=86400,
        headless_timeout_seconds=5,
        headless_progress_enabled=False,
        headless_default_workflow=None,
        coding_timeout_seconds=30,
    )
    stop_event = asyncio.Event()

    worker_task = asyncio.create_task(
        run_queue_worker(args, settings, StubTaskRunner(), sandbox=None, decider=None, stop_event=stop_event, redis_client=redis)
    )

    await asyncio.wait_for(redis.ttl("results:job1"), timeout=1)
    await asyncio.sleep(0.01)
    stop_event.set()
    await asyncio.wait_for(worker_task, timeout=1)

    raw = await redis.get("results:job1")
    result = json.loads(raw)
    assert result["status"] == "completed"
    assert result["output"] == "done"
    assert result["correlation_id"] == "job1"

    entries = await redis.lrange("results:list", 0, -1)
    assert entries, "result list should have entries"


@pytest.mark.asyncio
async def test_queue_worker_handles_malformed_job():
    redis = fakeredis.aioredis.FakeRedis()
    jobs_key = "matrix-tui:jobs"
    await redis.rpush(jobs_key, "not-json")

    args = SimpleNamespace(
        mode="queue",
        redis_url=None,
        jobs_key=jobs_key,
        results_prefix="results:",
        result_ttl_seconds=5,
        timeout_seconds=1,
        progress=False,
    )
    settings = SimpleNamespace(
        redis_url=None,
        redis_jobs_key="matrix-tui:jobs",
        redis_results_prefix="results:",
        redis_result_ttl_seconds=86400,
        headless_timeout_seconds=5,
        headless_progress_enabled=False,
        headless_default_workflow=None,
        coding_timeout_seconds=30,
    )
    stop_event = asyncio.Event()

    worker_task = asyncio.create_task(
        run_queue_worker(args, settings, StubTaskRunner(), sandbox=None, decider=None, stop_event=stop_event, redis_client=redis)
    )

    await asyncio.sleep(0.05)
    stop_event.set()
    await asyncio.wait_for(worker_task, timeout=1)

    keys = await redis.keys("results:*")
    assert keys, "should emit a result even for malformed job"


@pytest.mark.asyncio
async def test_queue_worker_times_out_job():
    redis = fakeredis.aioredis.FakeRedis()
    jobs_key = "matrix-tui:jobs"
    job = {"workflow": "wf", "payload": {"a": 1}, "correlation_id": "job2", "timeout_seconds": 0.01}
    await redis.rpush(jobs_key, json.dumps(job))

    args = SimpleNamespace(
        mode="queue",
        redis_url=None,
        jobs_key=jobs_key,
        results_prefix="results:",
        result_ttl_seconds=5,
        timeout_seconds=1,
        progress=False,
    )
    settings = SimpleNamespace(
        redis_url=None,
        redis_jobs_key="matrix-tui:jobs",
        redis_results_prefix="results:",
        redis_result_ttl_seconds=86400,
        headless_timeout_seconds=5,
        headless_progress_enabled=False,
        headless_default_workflow=None,
        coding_timeout_seconds=30,
    )
    stop_event = asyncio.Event()

    worker_task = asyncio.create_task(
        run_queue_worker(args, settings, HangingTaskRunner(), sandbox=None, decider=None, stop_event=stop_event, redis_client=redis)
    )

    await asyncio.sleep(0.05)
    stop_event.set()
    await asyncio.wait_for(worker_task, timeout=1)

    raw = await redis.get("results:job2")
    result = json.loads(raw)
    assert result["status"] == "timeout"
