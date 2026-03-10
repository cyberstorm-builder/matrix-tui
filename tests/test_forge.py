import asyncio
import hashlib
import hmac
from types import SimpleNamespace

import pytest

from matrix_agent.forge import GitHubClient, GiteaClient


@pytest.mark.asyncio
async def test_github_verify_webhook_signature_valid():
    body = b"{\"action\":\"opened\"}"
    secret = "topsecret"
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    client = GitHubClient(SimpleNamespace(forge_webhook_secret=secret, forge_repo="o/r", forge_token="t"))

    assert client.verify_webhook({"X-Hub-Signature-256": signature}, body) is True
    assert client.verify_webhook({"X-Hub-Signature-256": "bad"}, body) is False


@pytest.mark.asyncio
async def test_gitea_verify_webhook_signature_valid():
    body = b"payload"
    secret = "s3cret"
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    client = GiteaClient(SimpleNamespace(forge_webhook_secret=secret, forge_repo="o/r", forge_token="t", forge_api_base="https://forge/api/v1", forge_web_url="https://forge"))

    assert client.verify_webhook({"X-Gitea-Signature": signature}, body) is True
    assert client.verify_webhook({"X-Gitea-Signature": "nope"}, body) is False


@pytest.mark.asyncio
async def test_github_get_pr_url_by_head_queries_head_branch():
    calls = []

    async def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return [
            {
                "html_url": "https://github.com/owner/repo/pull/7",
                "head": {"ref": "feature", "repo": {"full_name": "owner/repo"}},
                "state": "open",
            }
        ]

    settings = SimpleNamespace(
        forge_repo="owner/repo",
        forge_api_base="https://api.github.com",
        forge_token="t",
        agent_label="agent-task",
    )
    client = GitHubClient(settings, http=fake_request)

    url = await client.get_pr_url_by_head("feature")

    assert url == "https://github.com/owner/repo/pull/7"
    assert calls and "head=owner:feature" in calls[0][1]


@pytest.mark.asyncio
async def test_gitea_get_pr_url_by_head_filters_locally():
    calls = []

    async def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return [
            {"html_url": "https://forge/owner/repo/pulls/2", "head": {"ref": "other"}, "state": "open"},
            {"html_url": "https://forge/owner/repo/pulls/3", "head": {"ref": "feat"}, "state": "open"},
        ]

    settings = SimpleNamespace(
        forge_repo="owner/repo",
        forge_api_base="https://forge/api/v1",
        forge_web_url="https://forge",
        forge_token="token",
        agent_label="agent-task",
    )
    client = GiteaClient(settings, http=fake_request)

    url = await client.get_pr_url_by_head("feat")

    assert url == "https://forge/owner/repo/pulls/3"
    assert calls and calls[0][1].endswith("/repos/owner/repo/pulls?state=open")


@pytest.mark.asyncio
async def test_create_pr_github_uses_api_and_returns_html_url():
    calls = []

    async def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return {"html_url": "https://github.com/owner/repo/pull/9"}

    client = GitHubClient(
        SimpleNamespace(
            forge_repo="owner/repo",
            forge_api_base="https://api.github.com",
            forge_token="t",
            forge_default_branch=None,
            agent_label="agent-task",
        ),
        http=fake_request,
    )

    url = await client.create_pr(head="agent/issue-9", base="main", title="Fix #9", body="body")

    assert url == "https://github.com/owner/repo/pull/9"
    method, url_called, kwargs = calls[0]
    assert method == "POST"
    assert url_called.endswith("/repos/owner/repo/pulls")
    assert kwargs["json"]["head"] == "agent/issue-9"
    assert kwargs["json"]["base"] == "main"


@pytest.mark.asyncio
async def test_create_pr_gitea_uses_api_v1():
    calls = []

    async def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return {"html_url": "https://forge/owner/repo/pulls/4"}

    client = GiteaClient(
        SimpleNamespace(
            forge_repo="owner/repo",
            forge_api_base="https://forge/api/v1",
            forge_web_url="https://forge",
            forge_token="tok",
            forge_default_branch="main",
            agent_label="agent-task",
        ),
        http=fake_request,
    )

    url = await client.create_pr(head="feature", base=None, title="Feat", body=None)

    assert url == "https://forge/owner/repo/pulls/4"
    method, url_called, kwargs = calls[0]
    assert method == "POST"
    assert url_called.endswith("/repos/owner/repo/pulls")
    assert kwargs["json"]["base"] == "main"  # default branch fallback


@pytest.mark.asyncio
async def test_is_issue_open_with_label_filters_by_state_and_label():
    calls = []

    async def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return {
            "state": "open",
            "labels": [{"name": "bug"}, {"name": "agent-task"}],
        }

    client = GitHubClient(
        SimpleNamespace(
            forge_repo="owner/repo",
            forge_api_base="https://api.github.com",
            forge_token="tok",
            agent_label="agent-task",
        ),
        http=fake_request,
    )

    assert await client.is_issue_open_with_label(5, "agent-task") is True
    assert calls[0][1].endswith("/repos/owner/repo/issues/5")


@pytest.mark.asyncio
async def test_list_open_agent_issues_filters_and_normalizes():
    calls = []

    async def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return [
            {"number": 1, "title": "One", "body": "", "labels": [{"name": "agent-task"}]},
            {"number": 2, "title": "Two", "body": "", "labels": []},
        ]

    client = GitHubClient(
        SimpleNamespace(
            forge_repo="owner/repo",
            forge_api_base="https://api.github.com",
            forge_token="tok",
            agent_label="agent-task",
        ),
        http=fake_request,
    )

    issues = await client.list_open_agent_issues()

    assert [i["number"] for i in issues] == [1]
    assert calls and "state=open" in calls[0][1]
