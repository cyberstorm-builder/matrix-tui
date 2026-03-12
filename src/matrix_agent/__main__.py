"""Entry point: uv run python -m matrix_agent"""

import argparse
import asyncio
import logging
import os
import signal
from typing import Any, Awaitable, Callable

from .config import Settings
from .sandbox import SandboxManager
from .decider import Decider
from .core import TaskRunner
from .bot import Bot
from .channels import GitHubChannel

logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)


Runner = Callable[[argparse.Namespace], Awaitable[Any]]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Matrix Agent entrypoint")
    parser.add_argument(
        "--mode",
        choices=["matrix", "headless", "queue"],
        default="matrix",
        help="matrix (default) starts Matrix bot; headless runs a single workflow; queue runs Redis worker",
    )
    parser.add_argument("--workflow", help="Workflow name for headless/queue runs")
    payload_group = parser.add_mutually_exclusive_group()
    payload_group.add_argument("--payload-json", dest="payload_json", help="Inline JSON payload")
    payload_group.add_argument("--payload-file", dest="payload_file", help="Path to JSON payload file")
    parser.add_argument("--correlation-id", help="Correlation id for headless/queue runs")
    parser.add_argument("--timeout-seconds", type=int, help="Override timeout for headless runs")
    parser.add_argument("--redis-url", help="Redis URL for queue/result publishing")
    parser.add_argument("--jobs-key", help="Redis list key for jobs")
    parser.add_argument("--results-prefix", help="Redis key prefix for results")
    parser.add_argument("--result-ttl-seconds", type=int, help="TTL for Redis results")
    parser.add_argument(
        "--progress",
        action="store_true",
        help="Emit progress entries when publishing to Redis",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


async def run_matrix_mode(settings: Settings, sandbox: SandboxManager, decider: Decider, task_runner: TaskRunner) -> None:
    # Load persisted state and restore histories
    histories = await sandbox.load_state()
    decider.load_histories(histories)

    # GitHub recovery: scan for open issues before starting webhook server
    github_channel = None
    if settings.github_token:
        github_channel = GitHubChannel(task_runner=task_runner, settings=settings)
        recovered = await github_channel.recover_tasks()
        await github_channel.start()
        for task_id, msg in recovered:
            await task_runner.enqueue(task_id, msg, github_channel)

    # Matrix recovery: sync + pre_register surviving rooms
    bot = Bot(settings, sandbox, decider, task_runner)
    await bot.setup()

    # Now _processing contains all recovered tasks — safe to destroy orphans
    await task_runner.destroy_orphans()

    shutdown_event = asyncio.Event()

    def handle_sigterm() -> None:
        logging.info("Received shutdown signal")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, handle_sigterm)
    loop.add_signal_handler(signal.SIGINT, handle_sigterm)

    # Run bot until signal
    bot_task = asyncio.create_task(bot.run())

    try:
        await shutdown_event.wait()
        logging.info("Starting graceful shutdown...")
    finally:
        # Graceful teardown
        bot_task.cancel()
        try:
            await bot_task
        except asyncio.CancelledError:
            pass

        await task_runner.shutdown()
        sandbox.save_state()
        if github_channel:
            await github_channel.stop()
        logging.info("Shutdown complete")


async def dispatch(args: argparse.Namespace, runners: dict[str, Runner] | None = None) -> Any:
    runner_override = (runners or {}).get(args.mode)
    if runner_override is not None:
        return await runner_override(args)

    settings = Settings()
    sandbox = SandboxManager(settings)
    decider = Decider(settings, sandbox)
    task_runner = TaskRunner(decider, sandbox)

    if args.mode == "headless":
        from .headless import run_headless_once

        return await run_headless_once(args, settings, task_runner, sandbox, decider)
    if args.mode == "queue":
        from .headless import run_queue_worker

        stop_event = asyncio.Event()
        return await run_queue_worker(args, settings, task_runner, sandbox, decider, stop_event)

    return await run_matrix_mode(settings, sandbox, decider, task_runner)


def main(argv: list[str] | None = None) -> Any:
    args = parse_args(argv)
    return asyncio.run(dispatch(args))


if __name__ == "__main__":
    main()
