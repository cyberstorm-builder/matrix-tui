import io
import json

import fakeredis
import pytest

from matrix_agent.headless import HeadlessChannel


@pytest.mark.asyncio
async def test_headless_channel_stdout_result_and_error():
    writer = io.StringIO()
    channel = HeadlessChannel(workflow="wf", correlation_id="cid", writer=writer)

    await channel.deliver_result("headless:cid", "done")
    payload = json.loads(writer.getvalue().strip())
    assert payload["status"] == "completed"
    assert payload["output"] == "done"
    assert payload["workflow"] == "wf"
    assert payload["correlation_id"] == "cid"
    assert payload["container"] == "headless:cid"
    assert payload["started_at"]
    assert payload["finished_at"]
    assert payload["duration_ms"] >= 0
    assert await channel.is_valid("headless:cid") is False

    writer = io.StringIO()
    channel = HeadlessChannel(workflow="wf", correlation_id="cid", writer=writer)
    await channel.deliver_error("task-1", "boom")
    err_payload = json.loads(writer.getvalue().strip())
    assert err_payload["status"] == "failed"
    assert err_payload["error"] == "boom"


@pytest.mark.asyncio
async def test_headless_channel_redis_progress_and_result():
    redis = fakeredis.aioredis.FakeRedis()
    channel = HeadlessChannel(
        workflow="wf",
        correlation_id="cid",
        redis_client=redis,
        results_prefix="res:",
        results_list="res:list",
        result_ttl_seconds=10,
        progress_enabled=True,
    )

    await channel.send_update("task-1", "chunk1")
    await channel.deliver_result("task-1", "ok")

    raw = await redis.get("res:cid")
    payload = json.loads(raw)
    assert payload["status"] == "completed"
    assert payload["output"] == "ok"
    ttl = await redis.ttl("res:cid")
    assert ttl > 0
    assert ttl <= 10

    entries = await redis.lrange("res:list", 0, -1)
    assert len(entries) == 2
    progress = json.loads(entries[0])
    assert progress["status"] == "progress"
    assert progress["output"] == "chunk1"
    final = json.loads(entries[1])
    assert final["status"] == "completed"
    assert final["output"] == "ok"
