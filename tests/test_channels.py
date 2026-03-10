import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from matrix_agent.channels import ForgeChannel
from matrix_agent.forge import IssueEvent


class StubForgeClient:
    def __init__(self, issue_event: IssueEvent | None = None):
        self.issue_event = issue_event
        self.verify_webhook = MagicMock(return_value=True)
        self.comment_issue = AsyncMock()
        self.close_issue = AsyncMock()
        self.is_issue_open_with_label = AsyncMock(return_value=True)
        self.list_open_agent_issues = AsyncMock(return_value=[])
        self.parse_issue_event = MagicMock(return_value=issue_event)


def _make_settings():
    return SimpleNamespace(
        forge_type="github",
        forge_repo="owner/repo",
        forge_webhook_secret="secret",
        agent_label="agent-task",
        forge_webhook_port=0,
    )


def _make_task_runner():
    tr = MagicMock()
    tr._processing = set()
    tr.enqueue = AsyncMock()
    return tr


async def _post(client, payload, secret="secret", event="issues"):
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", "X-GitHub-Event": event}
    return await client.post("/webhook/github", data=body, headers=headers)


@pytest.mark.asyncio
async def test_webhook_rejects_invalid_signature():
    settings = _make_settings()
    forge_client = StubForgeClient()
    forge_client.verify_webhook.return_value = False
    channel = ForgeChannel(task_runner=_make_task_runner(), settings=settings, forge_client=forge_client)
    app = channel._make_app()

    async with TestClient(TestServer(app)) as client:
        resp = await _post(client, {"action": "labeled"})

    assert resp.status == 401
    forge_client.verify_webhook.assert_called()


@pytest.mark.asyncio
async def test_webhook_labeled_enqueues_and_comments():
    settings = _make_settings()
    event = IssueEvent(
        task_id="gh-7",
        repo_full="owner/repo",
        issue_number=7,
        title="Fix login",
        body="Details",
        labels=["agent-task"],
        action="labeled",
    )
    forge_client = StubForgeClient(issue_event=event)
    channel = ForgeChannel(task_runner=_make_task_runner(), settings=settings, forge_client=forge_client)
    app = channel._make_app()

    async with TestClient(TestServer(app)) as client:
        resp = await _post(client, {"action": "labeled"})

    assert resp.status == 202
    channel.task_runner.enqueue.assert_called_once()
    args = channel.task_runner.enqueue.call_args[0]
    assert args[0] == "gh-7"
    assert "Repository: owner/repo" in args[1]
    forge_client.comment_issue.assert_awaited_with(7, "🤖 Working on this issue...")


@pytest.mark.asyncio
async def test_deliver_result_closes_on_success():
    settings = _make_settings()
    forge_client = StubForgeClient()
    channel = ForgeChannel(task_runner=_make_task_runner(), settings=settings, forge_client=forge_client)

    await channel.deliver_result("gh-10", "Done")

    forge_client.comment_issue.assert_awaited_with(10, "✅ Completed — Done")
    forge_client.close_issue.assert_awaited_with(10)


@pytest.mark.asyncio
async def test_deliver_result_max_turns_does_not_close():
    settings = _make_settings()
    forge_client = StubForgeClient()
    channel = ForgeChannel(task_runner=_make_task_runner(), settings=settings, forge_client=forge_client)

    await channel.deliver_result("gh-10", "Need input", status="max_turns")

    forge_client.comment_issue.assert_awaited_with(10, "🤖 Need input")
    forge_client.close_issue.assert_not_called()


@pytest.mark.asyncio
async def test_deliver_error_comments_and_closes():
    settings = _make_settings()
    forge_client = StubForgeClient()
    channel = ForgeChannel(task_runner=_make_task_runner(), settings=settings, forge_client=forge_client)

    await channel.deliver_error("gh-11", "boom")

    forge_client.comment_issue.assert_awaited_with(11, "❌ Failed: boom")
    forge_client.close_issue.assert_awaited_with(11)


@pytest.mark.asyncio
async def test_is_valid_uses_forge():
    settings = _make_settings()
    forge_client = StubForgeClient()
    channel = ForgeChannel(task_runner=_make_task_runner(), settings=settings, forge_client=forge_client)

    assert await channel.is_valid("gh-5") is True
    forge_client.is_issue_open_with_label.assert_awaited_with(5, settings.agent_label)


@pytest.mark.asyncio
async def test_recover_tasks_enqueues_and_comments():
    settings = _make_settings()
    forge_client = StubForgeClient()
    forge_client.list_open_agent_issues.return_value = [
        {"number": 3, "title": "One", "body": "", "labels": [settings.agent_label]},
    ]
    channel = ForgeChannel(task_runner=_make_task_runner(), settings=settings, forge_client=forge_client)

    recovered = await channel.recover_tasks()

    assert recovered == [("gh-3", "Repository: owner/repo\n\n# One\n\n")]
    forge_client.comment_issue.assert_awaited_with(3, "🤖 Bot restarted — resuming work on this issue.")
    channel.task_runner.enqueue.assert_awaited_with("gh-3", "Repository: owner/repo\n\n# One\n\n", channel)
