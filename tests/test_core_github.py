"""Tests for forge pipeline routing and host push behavior."""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from matrix_agent.core import TaskRunner
from matrix_agent.sandbox import SandboxManager
from conftest import SubprocessMocker, StubChannel

GITHUB_MESSAGE = "Repository: owner/repo\n\n# Fix the bug\n\nDetails here"


class StubForge:
    def __init__(self, pr_url: str | None = "https://forge/pull/1"):
        self.get_pr_url_by_head = AsyncMock(return_value=pr_url)
        self.create_pr = AsyncMock(return_value=pr_url or "https://forge/pull/new")


def _make_runner(settings, subprocess_mocker, forge=None):
    """Create TaskRunner with SandboxManager and stub forge client."""
    sandbox = SandboxManager(settings)
    decider = MagicMock()

    async def mock_handle_message(chat_id, user_text, send_update=None, system_prompt=None):
        yield "Done", None, "completed"

    decider.handle_message = mock_handle_message
    runner = TaskRunner(decider, sandbox, forge or StubForge())
    return runner, sandbox


def _setup_default_subprocess(mocker):
    mocker.on("podman", "run", stdout=b"container-id")
    mocker.on("podman", "exec")
    mocker.on("podman", "stop")
    mocker.on("podman", "rm")


async def _run_pipeline(runner, task_id, message, channel):
    await runner.enqueue(task_id, message, channel)
    for _ in range(50):
        if channel.results or channel.errors:
            break
        await asyncio.sleep(0.02)
    await runner._cleanup(task_id)


@pytest.mark.asyncio
async def test_host_push_prefers_existing_pr(settings):
    mocker = SubprocessMocker()
    _setup_default_subprocess(mocker)
    forge = StubForge(pr_url="https://forge/pull/existing")
    runner, sandbox = _make_runner(settings, mocker, forge)
    sandbox._containers = {"gh-1": "sandbox-gh-1"}

    async def mock_exec(chat_id, cmd):
        if "git rev-parse" in cmd:
            return (0, "agent/feat\n", "")
        if "git push" in cmd:
            return (0, "", "")
        return (0, "", "")

    sandbox.exec = mock_exec

    with patch("asyncio.create_subprocess_exec", mocker):
        pr_url, error = await runner._host_push("gh-1", "/workspace/repo", "owner/repo", False)

    assert pr_url == "https://forge/pull/existing"
    forge.create_pr.assert_not_awaited()
    sandbox._containers.pop("gh-1", None)


@pytest.mark.asyncio
async def test_host_push_creates_pr_when_missing(settings):
    mocker = SubprocessMocker()
    _setup_default_subprocess(mocker)
    forge = StubForge(pr_url=None)
    runner, sandbox = _make_runner(settings, mocker, forge)
    sandbox._containers = {"gh-2": "sandbox-gh-2"}
    settings.forge_default_branch = "develop"

    async def mock_exec(chat_id, cmd):
        if "git rev-parse" in cmd:
            return (0, "agent/issue-2\n", "")
        if "git push" in cmd:
            return (0, "", "")
        return (0, "", "")

    sandbox.exec = mock_exec

    with patch("asyncio.create_subprocess_exec", mocker):
        pr_url, error = await runner._host_push("gh-2", "/workspace/repo", "owner/repo", False)

    assert pr_url == "https://forge/pull/new"
    forge.create_pr.assert_awaited_with(head="agent/issue-2", base="develop", title="Fix #2", body="Closes #2")
    sandbox._containers.pop("gh-2", None)


@pytest.mark.asyncio
async def test_process_github_uses_forge_clone_url(settings):
    mocker = SubprocessMocker()
    _setup_default_subprocess(mocker)
    forge = StubForge(pr_url="https://forge/pull/10")
    runner, sandbox = _make_runner(settings, mocker, forge)
    sandbox._containers = {"gh-10": "sandbox-gh-10"}
    settings.forge_web_url = "https://gitea.example"

    exec_cmds = []

    async def tracking_exec(chat_id, cmd):
        exec_cmds.append(cmd)
        if "git diff --name-only" in cmd:
            return (0, "fix.py\n", "")
        if "git checkout -b" in cmd or "git checkout agent/issue" in cmd:
            return (0, "", "")
        if "git clone" in cmd:
            return (0, "", "")
        if "git rev-parse" in cmd:
            return (0, "agent/issue-10\n", "")
        if "git push" in cmd:
            return (0, "", "")
        return (0, "", "")

    sandbox.exec = tracking_exec
    sandbox.run_gemini_session = AsyncMock(return_value=(0, "", ""))
    sandbox.validate_work = AsyncMock(return_value=(True, []))

    channel = StubChannel()

    with patch("asyncio.create_subprocess_exec", mocker):
        await _run_pipeline(runner, "gh-10", GITHUB_MESSAGE, channel)

    clone_calls = [c for c in exec_cmds if "git clone" in c]
    assert clone_calls and settings.forge_web_url in clone_calls[0]
