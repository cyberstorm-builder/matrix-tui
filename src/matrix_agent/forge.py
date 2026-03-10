"""Forge-agnostic helpers for GitHub / Gitea operations."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

import aiohttp

Json = Any
HttpFunc = Callable[..., Awaitable[Any]]


@dataclass
class IssueEvent:
    task_id: str
    repo_full: str
    issue_number: int
    title: str
    body: str
    labels: list[str]
    action: str
    ci_context: str | None = None

    @property
    def message(self) -> str:
        return f"Repository: {self.repo_full}\n\n# {self.title}\n\n{self.body or ''}"


class ForgeClient:
    def __init__(self, settings, http: HttpFunc | None = None):
        self.settings = settings
        self.repo = settings.forge_repo
        self.api_base = (getattr(settings, "forge_api_base", "") or "").rstrip("/")
        self.web_url = (getattr(settings, "forge_web_url", "") or "").rstrip("/")
        self.token = getattr(settings, "forge_token", "")
        self.agent_label = getattr(settings, "agent_label", "agent-task")
        self.default_branch = getattr(settings, "forge_default_branch", None)
        self._http = http or self._request

    # --------------------------- HTTP helper --------------------------- #
    async def _request(self, method: str, url: str, **kwargs) -> Any:
        headers = kwargs.pop("headers", {})
        if self.token:
            headers.setdefault("Authorization", f"token {self.token}")
        headers.setdefault("Accept", "application/json")
        async with aiohttp.ClientSession() as session:
            async with session.request(method, url, headers=headers, **kwargs) as resp:
                resp.raise_for_status()
                if resp.content_type == "application/json":
                    return await resp.json()
                return await resp.text()

    # --------------------------- Webhooks --------------------------- #
    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> bool:
        raise NotImplementedError

    def parse_issue_event(self, payload: dict, event_type: str, action: str) -> IssueEvent | None:
        """Normalize webhook payload into IssueEvent.

        Returns None for unhandled events (wrong label/state/etc.).
        """
        return None

    # --------------------------- Issues --------------------------- #
    async def comment_issue(self, issue_number: int, body: str) -> None:
        raise NotImplementedError

    async def close_issue(self, issue_number: int) -> None:
        raise NotImplementedError

    async def is_issue_open_with_label(self, issue_number: int, label: str) -> bool:
        raise NotImplementedError

    async def list_open_agent_issues(self) -> list[dict]:
        raise NotImplementedError

    # --------------------------- Pull Requests --------------------------- #
    async def get_pr_url_by_head(self, head: str) -> str | None:
        raise NotImplementedError

    async def create_pr(self, head: str, base: str | None, title: str, body: str | None) -> str | None:
        raise NotImplementedError

    @classmethod
    def from_settings(cls, settings, http: HttpFunc | None = None) -> "ForgeClient":
        forge_type = getattr(settings, "forge_type", "github")
        if forge_type == "gitea":
            return GiteaClient(settings, http=http)
        return GitHubClient(settings, http=http)


class GitHubClient(ForgeClient):
    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> bool:
        secret = getattr(self.settings, "forge_webhook_secret", "")
        if not secret:
            return True
        sig_header = headers.get("X-Hub-Signature-256", "")
        if not sig_header:
            return False
        expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig_header, expected)

    def parse_issue_event(self, payload: dict, event_type: str, action: str) -> IssueEvent | None:
        if event_type not in ("issues", "issue_comment"):
            return None

        issue = payload.get("issue") or {}
        labels = [lb.get("name", "") for lb in issue.get("labels", [])]
        if self.agent_label not in labels:
            return None

        repo_full = payload.get("repository", {}).get("full_name", self.repo)
        number = issue.get("number")
        if not number:
            return None
        task_id = f"gh-{number}"

        # Issue comment events forward the comment body (skip bot/self comments)
        if event_type == "issue_comment":
            comment = payload.get("comment", {})
            body = comment.get("body", "")
            sender = comment.get("user", {}).get("login", "")
            if sender.endswith("[bot]") or body.startswith(("✅", "❌", "🤖")):
                return None
            return IssueEvent(
                task_id=task_id,
                repo_full=repo_full,
                issue_number=number,
                title=issue.get("title", ""),
                body=body,
                labels=labels,
                action=action,
            )

        # Issue events (labeled/reopened)
        if action not in ("labeled", "reopened"):
            return None
        title = issue.get("title", "")
        body = issue.get("body", "")
        ci_context = None
        if action == "reopened":
            comments = payload.get("comments") or []
            if comments:
                ci_comments = [c for c in comments if isinstance(c, str) and c.strip().startswith("⚠️")]
                if ci_comments:
                    ci_context = ci_comments[-1]
        return IssueEvent(
            task_id=task_id,
            repo_full=repo_full,
            issue_number=number,
            title=title,
            body=body,
            labels=labels,
            action=action,
            ci_context=ci_context,
        )

    async def comment_issue(self, issue_number: int, body: str) -> None:
        url = f"{self.api_base}/repos/{self.repo}/issues/{issue_number}/comments"
        await self._http("POST", url, json={"body": body})

    async def close_issue(self, issue_number: int) -> None:
        url = f"{self.api_base}/repos/{self.repo}/issues/{issue_number}"
        await self._http("PATCH", url, json={"state": "closed"})

    async def is_issue_open_with_label(self, issue_number: int, label: str) -> bool:
        url = f"{self.api_base}/repos/{self.repo}/issues/{issue_number}"
        data = await self._http("GET", url)
        labels = [lb.get("name", "") for lb in data.get("labels", [])]
        state = data.get("state", "").lower()
        return state == "open" and label in labels

    async def list_open_agent_issues(self) -> list[dict]:
        url = f"{self.api_base}/repos/{self.repo}/issues?state=open"
        data = await self._http("GET", url)
        result = []
        for item in data:
            labels = [lb.get("name", "") for lb in item.get("labels", [])]
            if self.agent_label not in labels:
                continue
            result.append(
                {
                    "number": item.get("number"),
                    "title": item.get("title", ""),
                    "body": item.get("body", ""),
                    "labels": labels,
                }
            )
        return result

    async def get_pr_url_by_head(self, head: str) -> str | None:
        owner = self.repo.split("/")[0]
        url = f"{self.api_base}/repos/{self.repo}/pulls?state=open&head={owner}:{head}"
        pulls = await self._http("GET", url)
        for pr in pulls:
            pr_head = pr.get("head", {})
            if pr_head.get("ref") == head and pr.get("state") == "open":
                return pr.get("html_url") or pr.get("url")
        return None

    async def create_pr(self, head: str, base: str | None, title: str, body: str | None) -> str | None:
        payload = {
            "head": head,
            "base": base or self.default_branch or "main",
            "title": title,
        }
        if body:
            payload["body"] = body
        url = f"{self.api_base}/repos/{self.repo}/pulls"
        pr = await self._http("POST", url, json=payload)
        return pr.get("html_url") or pr.get("url")


class GiteaClient(ForgeClient):
    def __init__(self, settings, http: HttpFunc | None = None):
        super().__init__(settings, http=http)
        # Ensure api_base includes /api/v1 for Gitea
        if self.api_base.endswith("/api/v1"):
            self.api_base = self.api_base.rstrip("/")
        else:
            self.api_base = f"{self.api_base.rstrip('/')}/api/v1"

    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> bool:
        secret = getattr(self.settings, "forge_webhook_secret", "")
        if not secret:
            return True
        sig_header = headers.get("X-Gitea-Signature", "")
        if not sig_header:
            return False
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig_header, expected)

    def parse_issue_event(self, payload: dict, event_type: str, action: str) -> IssueEvent | None:
        if event_type not in ("issues", "issue_comment"):
            return None
        issue = payload.get("issue") or {}
        labels = [lb.get("name", "") for lb in issue.get("labels", [])]
        if self.agent_label not in labels:
            return None
        repo_full = payload.get("repository", {}).get("full_name", self.repo)
        number = issue.get("number")
        if not number:
            return None
        task_id = f"gh-{number}"

        if event_type == "issue_comment":
            comment = payload.get("comment", {})
            body = comment.get("body", "")
            sender = comment.get("user", {}).get("login", "")
            if sender.endswith("[bot]") or body.startswith(("✅", "❌", "🤖")):
                return None
            return IssueEvent(
                task_id=task_id,
                repo_full=repo_full,
                issue_number=number,
                title=issue.get("title", ""),
                body=body,
                labels=labels,
                action=action,
            )

        if action not in ("labeled", "reopened"):
            return None
        title = issue.get("title", "")
        body = issue.get("body", "")
        return IssueEvent(
            task_id=task_id,
            repo_full=repo_full,
            issue_number=number,
            title=title,
            body=body,
            labels=labels,
            action=action,
        )

    async def comment_issue(self, issue_number: int, body: str) -> None:
        url = f"{self.api_base}/repos/{self.repo}/issues/{issue_number}/comments"
        await self._http("POST", url, json={"body": body})

    async def close_issue(self, issue_number: int) -> None:
        url = f"{self.api_base}/repos/{self.repo}/issues/{issue_number}"
        await self._http("PATCH", url, json={"state": "closed"})

    async def is_issue_open_with_label(self, issue_number: int, label: str) -> bool:
        url = f"{self.api_base}/repos/{self.repo}/issues/{issue_number}"
        data = await self._http("GET", url)
        labels = [lb.get("name", "") for lb in data.get("labels", [])]
        state = data.get("state", "").lower()
        return state == "open" and label in labels

    async def list_open_agent_issues(self) -> list[dict]:
        url = f"{self.api_base}/repos/{self.repo}/issues?state=open"
        data = await self._http("GET", url)
        result = []
        for item in data:
            labels = [lb.get("name", "") for lb in item.get("labels", [])]
            if self.agent_label not in labels:
                continue
            result.append(
                {
                    "number": item.get("number"),
                    "title": item.get("title", ""),
                    "body": item.get("body", ""),
                    "labels": labels,
                }
            )
        return result

    async def get_pr_url_by_head(self, head: str) -> str | None:
        url = f"{self.api_base}/repos/{self.repo}/pulls?state=open"
        pulls = await self._http("GET", url)
        for pr in pulls:
            pr_head = pr.get("head", {})
            if pr_head.get("ref") == head and pr.get("state") == "open":
                return pr.get("html_url") or pr.get("url") or pr.get("web_url")
        return None

    async def create_pr(self, head: str, base: str | None, title: str, body: str | None) -> str | None:
        payload = {
            "head": head,
            "base": base or self.default_branch or "main",
            "title": title,
        }
        if body:
            payload["body"] = body
        url = f"{self.api_base}/repos/{self.repo}/pulls"
        pr = await self._http("POST", url, json=payload)
        return pr.get("html_url") or pr.get("url") or pr.get("web_url")
