"""Channel adapters — ingest tasks from external sources (forge webhooks, etc.)."""

import json
import logging
from abc import ABC, abstractmethod

from aiohttp import web

from .forge import ForgeClient, IssueEvent

log = logging.getLogger(__name__)


class ChannelAdapter(ABC):
    system_prompt: str = ""

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def send_update(self, task_id: str, text: str) -> None: ...

    @abstractmethod
    async def deliver_result(self, task_id: str, text: str, *, status: str = "completed") -> None: ...

    @abstractmethod
    async def deliver_error(self, task_id: str, error: str) -> None: ...

    @abstractmethod
    async def is_valid(self, task_id: str) -> bool: ...

    async def recover_tasks(self) -> list[tuple[str, str]]:
        """Return (task_id, message) pairs to re-enqueue after restart."""
        return []


class ForgeChannel(ChannelAdapter):
    system_prompt = ""

    def __init__(self, task_runner, settings, forge_client: ForgeClient):
        self.task_runner = task_runner
        self.settings = settings
        self.forge = forge_client
        self._runner: web.AppRunner | None = None

    # --------------------------- Lifecycle --------------------------- #
    def _make_app(self) -> web.Application:
        app = web.Application()
        path = f"/webhook/{getattr(self.settings, 'forge_type', 'github')}"
        app.router.add_post(path, self._handle_webhook)
        return app

    async def start(self) -> None:
        app = self._make_app()
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        port = getattr(self.settings, "github_webhook_port", 0)
        site = web.TCPSite(self._runner, "0.0.0.0", port)
        await site.start()
        log.info("Forge webhook listening on port %s", port)

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            self._runner = None

    # --------------------------- Webhook handling --------------------------- #
    async def _handle_webhook(self, request: web.Request) -> web.Response:
        body = await request.read()
        if not self.forge.verify_webhook(request.headers, body):
            return web.Response(status=401, text="Invalid signature")

        payload = json.loads(body or b"{}")
        event_type = request.headers.get("X-GitHub-Event") or request.headers.get("X-Gitea-Event", "")
        action = payload.get("action", "")
        issue_event = self.forge.parse_issue_event(payload, event_type, action)
        if not issue_event:
            return web.Response(text="ignored")

        task_id = issue_event.task_id

        if event_type == "issues" and action in ("labeled", "reopened"):
            if task_id in getattr(self.task_runner, "_processing", set()):
                return web.Response(text="already processing")
            await self.forge.comment_issue(issue_event.issue_number, "🤖 Working on this issue...")
            await self.task_runner.enqueue(task_id, issue_event.message, self)
            if issue_event.ci_context:
                await self.task_runner.enqueue(task_id, issue_event.ci_context, self)
        elif event_type == "issue_comment" and action == "created":
            comment_body = payload.get("comment", {}).get("body", "")
            if comment_body:
                await self.task_runner.enqueue(task_id, comment_body, self)
        else:
            return web.Response(text="ignored")

        return web.Response(status=202, text="Accepted")

    # --------------------------- Deliveries --------------------------- #
    async def send_update(self, task_id: str, text: str) -> None:
        # No-op to avoid spamming issues
        return None

    async def deliver_result(self, task_id: str, text: str, *, status: str = "completed") -> None:
        issue_number = int(task_id.split("-", 1)[1])
        if status == "max_turns":
            await self.forge.comment_issue(issue_number, f"🤖 {text}")
            return
        await self.forge.comment_issue(issue_number, f"✅ Completed — {text}")
        await self.forge.close_issue(issue_number)

    async def deliver_error(self, task_id: str, error: str) -> None:
        issue_number = int(task_id.split("-", 1)[1])
        await self.forge.comment_issue(issue_number, f"❌ Failed: {error}")
        await self.forge.close_issue(issue_number)

    async def is_valid(self, task_id: str) -> bool:
        issue_number = int(task_id.split("-", 1)[1])
        return await self.forge.is_issue_open_with_label(issue_number, getattr(self.settings, "agent_label", "agent-task"))

    async def recover_tasks(self) -> list[tuple[str, str]]:
        repo = getattr(self.settings, "forge_repo", "")
        results: list[tuple[str, str]] = []
        issues = await self.forge.list_open_agent_issues()
        for issue in issues:
            number = issue.get("number")
            if not number:
                continue
            task_id = f"gh-{number}"
            title = issue.get("title", "")
            body = issue.get("body", "")
            message = f"Repository: {repo}\n\n# {title}\n\n{body}"
            results.append((task_id, message))
            await self.task_runner.enqueue(task_id, message, self)
            await self.forge.comment_issue(number, "🤖 Bot restarted — resuming work on this issue.")
        log.info("Forge recovery: found %d open issues", len(results))
        return results


# Backward compatibility
GitHubChannel = ForgeChannel
