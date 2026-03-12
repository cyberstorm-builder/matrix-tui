"""Headless and queue helpers."""

from __future__ import annotations

import asyncio
import json
import signal
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import redis.asyncio as redis_asyncio

from .channels import ChannelAdapter


@dataclass
class HeadlessResult:
    status: str
    workflow: str
    correlation_id: str
    output: str | None = None
    error: str | None = None
    container: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None

    def to_json(self) -> str:
        def _dt(dt: datetime | None) -> str | None:
            return dt.isoformat() if dt else None

        return json.dumps(
            {
                "status": self.status,
                "workflow": self.workflow,
                "correlation_id": self.correlation_id,
                "output": self.output,
                "error": self.error,
                "container": self.container,
                "started_at": _dt(self.started_at),
                "finished_at": _dt(self.finished_at),
                "duration_ms": self.duration_ms,
            }
        )


class HeadlessChannel(ChannelAdapter):
    system_prompt = ""

    def __init__(
        self,
        *,
        workflow: str,
        correlation_id: str,
        redis_client: Any | None = None,
        results_prefix: str = "matrix-tui:results:",
        results_list: str | None = None,
        result_ttl_seconds: int | None = None,
        progress_enabled: bool = False,
        writer=None,
        completion_future: asyncio.Future | None = None,
    ) -> None:
        self.workflow = workflow
        self.correlation_id = correlation_id
        self.redis = redis_client
        self.results_prefix = results_prefix
        self.results_list = results_list
        self.result_ttl_seconds = result_ttl_seconds
        self.progress_enabled = progress_enabled
        self.started_at = datetime.now(timezone.utc)
        self.writer = writer or sys.stdout
        self._stopped = False
        self._completion_future = completion_future

    async def start(self) -> None:  # pragma: no cover - no-op for headless
        return None

    async def stop(self) -> None:  # pragma: no cover - no-op for headless
        self._stopped = True

    async def send_update(self, task_id: str, chunk: str) -> None:
        if not (self.redis and self.results_list and self.progress_enabled):
            return
        payload = self._build_result("progress", output=chunk, container=task_id)
        await self.redis.rpush(self.results_list, payload.to_json())
        if self.result_ttl_seconds:
            await self.redis.expire(self.results_list, self.result_ttl_seconds)

    async def deliver_result(self, task_id: str, text: str, *, status: str = "completed") -> None:
        result = self._build_result(status, output=text, container=task_id)
        await self._publish(result)
        self._stopped = True

    async def deliver_error(self, task_id: str, error: str) -> None:
        result = self._build_result("failed", error=error, container=task_id)
        await self._publish(result)
        self._stopped = True

    async def is_valid(self, task_id: str) -> bool:
        return not self._stopped

    def _build_result(
        self,
        status: str,
        *,
        output: str | None = None,
        error: str | None = None,
        container: str | None = None,
    ) -> HeadlessResult:
        finished_at = datetime.now(timezone.utc)
        duration_ms = int((finished_at - self.started_at).total_seconds() * 1000)
        return HeadlessResult(
            status=status,
            workflow=self.workflow,
            correlation_id=self.correlation_id,
            output=output,
            error=error,
            container=container,
            started_at=self.started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
        )

    async def _publish(self, result: HeadlessResult) -> None:
        text = result.to_json()
        if self.writer:
            self.writer.write(text + "\n")
            try:
                self.writer.flush()
            except Exception:
                pass

        if self._completion_future and not self._completion_future.done():
            try:
                self._completion_future.set_result(json.loads(text))
            except Exception:  # pragma: no cover - defensive
                self._completion_future.set_result({})

        if not self.redis:
            return

        key = f"{self.results_prefix}{self.correlation_id}"
        if self.result_ttl_seconds:
            await self.redis.setex(key, self.result_ttl_seconds, text)
        else:
            await self.redis.set(key, text)

        if self.results_list:
            await self.redis.rpush(self.results_list, text)
            if self.result_ttl_seconds:
                await self.redis.expire(self.results_list, self.result_ttl_seconds)


def _format_payload(payload: Any) -> str:
    if isinstance(payload, (dict, list)):
        return json.dumps(payload, indent=2, sort_keys=True)

    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
            return json.dumps(parsed, indent=2, sort_keys=True)
        except Exception:
            return payload
    return str(payload)


def _load_payload_from_args(args: Any) -> str:
    if getattr(args, "payload_json", None):
        return args.payload_json
    if getattr(args, "payload_file", None):
        return Path(args.payload_file).read_text().strip()
    return ""


def _build_message(workflow: str, correlation_id: str, payload_text: Any) -> str:
    payload_formatted = _format_payload(payload_text) if payload_text else ""
    header = f"WORKFLOW {workflow}\nCORRELATION {correlation_id}"
    if payload_formatted:
        return f"{header}\n{payload_formatted}"
    return header


def _default_timeout(args: Any, settings: Any, job: dict[str, Any] | None = None) -> float:
    return (
        (job or {}).get("timeout_seconds")
        or getattr(args, "timeout_seconds", None)
        or getattr(settings, "headless_timeout_seconds", None)
        or getattr(settings, "coding_timeout_seconds", None)
        or 0
    )


async def run_headless_once(
    args: Any,
    settings: Any,
    task_runner: Any,
    sandbox: Any,
    decider: Any,
) -> dict[str, Any]:
    workflow = getattr(args, "workflow", None) or getattr(settings, "headless_default_workflow", None) or "default"
    correlation_id = getattr(args, "correlation_id", None) or uuid.uuid4().hex
    payload_text = _load_payload_from_args(args)
    message = _build_message(workflow, correlation_id, payload_text)

    redis_client = None
    redis_url = getattr(args, "redis_url", None) or getattr(settings, "redis_url", None)
    results_prefix = getattr(args, "results_prefix", None) or getattr(settings, "redis_results_prefix", "matrix-tui:results:")
    result_ttl_seconds = getattr(args, "result_ttl_seconds", None) or getattr(settings, "redis_result_ttl_seconds", None)
    progress_enabled = getattr(args, "progress", False) or getattr(settings, "headless_progress_enabled", False)
    results_list = f"{results_prefix}list" if redis_url else None

    if redis_url:
        redis_client = redis_asyncio.from_url(redis_url)

    loop = asyncio.get_running_loop()
    completion: asyncio.Future = loop.create_future()

    channel = HeadlessChannel(
        workflow=workflow,
        correlation_id=correlation_id,
        redis_client=redis_client,
        results_prefix=results_prefix,
        results_list=results_list,
        result_ttl_seconds=result_ttl_seconds,
        progress_enabled=progress_enabled,
        completion_future=completion,
    )

    task_id = f"headless:{correlation_id}"
    await task_runner.enqueue(task_id, message, channel)

    timeout_seconds = _default_timeout(args, settings)

    try:
        return await asyncio.wait_for(completion, timeout=timeout_seconds)
    except asyncio.TimeoutError:
        timeout_result = channel._build_result(
            "timeout", error=f"Task timed out after {timeout_seconds}s", container=task_id
        )
        await channel._publish(timeout_result)
        return json.loads(timeout_result.to_json())
    finally:
        if redis_client:
            await redis_client.aclose()
        await task_runner.shutdown()


async def run_queue_worker(
    args: Any,
    settings: Any,
    task_runner: Any,
    sandbox: Any,
    decider: Any,
    stop_event: asyncio.Event,
    redis_client: Any | None = None,
) -> None:
    redis_url = getattr(args, "redis_url", None) or getattr(settings, "redis_url", None)
    jobs_key = getattr(args, "jobs_key", None) or getattr(settings, "redis_jobs_key", "matrix-tui:jobs")
    results_prefix = getattr(args, "results_prefix", None) or getattr(settings, "redis_results_prefix", "matrix-tui:results:")
    result_ttl_seconds = getattr(args, "result_ttl_seconds", None) or getattr(settings, "redis_result_ttl_seconds", None)
    progress_enabled = getattr(args, "progress", False) or getattr(settings, "headless_progress_enabled", False)

    redis = redis_client or redis_asyncio.from_url(redis_url)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:  # pragma: no cover - not available on some platforms
            pass

    try:
        while not stop_event.is_set():
            item = await redis.blpop(jobs_key, timeout=0.1)
            if not item:
                continue

            _, raw_job = item
            try:
                job_obj = json.loads(raw_job)
            except Exception:
                correlation_id = uuid.uuid4().hex
                channel = HeadlessChannel(
                    workflow="unknown",
                    correlation_id=correlation_id,
                    redis_client=redis,
                    results_prefix=results_prefix,
                    results_list=f"{results_prefix}list",
                    result_ttl_seconds=result_ttl_seconds,
                    progress_enabled=False,
                )
                await channel.deliver_error("headless:invalid", "Invalid job payload")
                await task_runner.shutdown()
                continue

            workflow = job_obj.get("workflow") or getattr(settings, "headless_default_workflow", None) or "default"
            payload = job_obj.get("payload", "")
            correlation_id = job_obj.get("correlation_id") or uuid.uuid4().hex
            reply_to = job_obj.get("reply_to") or results_prefix
            timeout_seconds = _default_timeout(args, settings, job_obj)

            completion: asyncio.Future = loop.create_future()
            message = _build_message(workflow, correlation_id, payload)
            channel = HeadlessChannel(
                workflow=workflow,
                correlation_id=correlation_id,
                redis_client=redis,
                results_prefix=reply_to,
                results_list=f"{reply_to}list",
                result_ttl_seconds=result_ttl_seconds,
                progress_enabled=progress_enabled,
                completion_future=completion,
            )
            task_id = f"headless:{correlation_id}"
            await task_runner.enqueue(task_id, message, channel)

            try:
                await asyncio.wait_for(completion, timeout=timeout_seconds)
            except asyncio.TimeoutError:
                timeout_result = channel._build_result(
                    "timeout", error=f"Task timed out after {timeout_seconds}s", container=task_id
                )
                await channel._publish(timeout_result)

            await task_runner.shutdown()
    finally:
        if not redis_client:
            await redis.aclose()
