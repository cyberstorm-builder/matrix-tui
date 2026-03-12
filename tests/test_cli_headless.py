import asyncio

from matrix_agent.__main__ import dispatch, parse_args


def run_dispatch(args, runners):
    return asyncio.run(dispatch(args, runners=runners))


def test_default_matrix_mode_dispatches_to_matrix():
    calls = {}

    async def fake_matrix(args):
        calls["mode"] = args.mode
        return "ok"

    args = parse_args([])
    result = run_dispatch(args, runners={"matrix": fake_matrix})

    assert calls == {"mode": "matrix"}
    assert result == "ok"


def test_headless_mode_parses_payload_and_routes():
    calls = {}

    async def fake_headless(args):
        calls["args"] = args
        return "headless"

    args = parse_args(
        [
            "--mode",
            "headless",
            "--workflow",
            "wf",
            "--payload-json",
            "{\"a\":1}",
            "--correlation-id",
            "cid",
            "--timeout-seconds",
            "10",
            "--redis-url",
            "redis://x",
            "--jobs-key",
            "jobs",
            "--results-prefix",
            "res:",
            "--result-ttl-seconds",
            "50",
            "--progress",
        ]
    )

    result = run_dispatch(args, runners={"headless": fake_headless})

    assert calls["args"].mode == "headless"
    assert calls["args"].workflow == "wf"
    assert calls["args"].payload_json == "{\"a\":1}"
    assert calls["args"].payload_file is None
    assert calls["args"].correlation_id == "cid"
    assert calls["args"].timeout_seconds == 10
    assert calls["args"].redis_url == "redis://x"
    assert calls["args"].jobs_key == "jobs"
    assert calls["args"].results_prefix == "res:"
    assert calls["args"].result_ttl_seconds == 50
    assert calls["args"].progress is True
    assert result == "headless"


def test_queue_mode_routes_with_redis_settings():
    calls = {}

    async def fake_queue(args):
        calls["mode"] = args.mode
        calls["redis_url"] = args.redis_url
        calls["jobs_key"] = args.jobs_key
        calls["results_prefix"] = args.results_prefix
        return "queue"

    args = parse_args(
        [
            "--mode",
            "queue",
            "--redis-url",
            "redis://y",
            "--jobs-key",
            "queue-jobs",
            "--results-prefix",
            "results:",
        ]
    )

    result = run_dispatch(args, runners={"queue": fake_queue})

    assert calls == {
        "mode": "queue",
        "redis_url": "redis://y",
        "jobs_key": "queue-jobs",
        "results_prefix": "results:",
    }
    assert result == "queue"
