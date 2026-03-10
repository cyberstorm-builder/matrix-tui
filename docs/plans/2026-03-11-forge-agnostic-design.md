# Forge-agnostic client + pipeline design (Issue #283)

**Date:** 2026-03-11
**Context:** `matrix-agent` currently hardcodes GitHub everywhere (channel webhooks, host-side PR creation, sandbox auth). `Settings` now exposes `forge_*` fields for GitHub/Gitea, but the runtime still assumes GitHub CLI flows. We need a forge abstraction, PR head lookup tests, and to refactor core/channels/sandbox to consume forge-agnostic configuration.

## Goals
- Introduce a `forge.py` abstraction that encapsulates forge-specific operations (issue lifecycle, PR lookup/creation, webhook validation) and instantiates the correct client from settings (`github` or `gitea`).
- Use the new `forge_*` configuration across channels/core/sandbox so the system no longer relies on GitHub-specific fields or URLs.
- Support retrieving an existing PR URL by head branch (for CI-fix/duplicate PR cases) and cover this with tests.
- Preserve existing Matrix + GitHub flows; add Gitea support without breaking current behavior.

## Non-goals
- Implement full Gitea webhook parity (only the issue/comment events needed for agent-task flows are required).
- Replace in-sandbox `git` operations with new tooling; we keep Podman exec-based git pushes.
- Broaden template or tool safety rules beyond what already exists.

## Clarifications & Assumptions
- No interactive approval is available; proceeding with the recommended design below. If a different forge or event shape is required, we will adjust.
- Repos are HTTPS-accessible; token-based auth is acceptable for both GitHub and Gitea.
- Gitea/Forgejo uses HMAC signatures (`X-Gitea-Signature`) compatible with the configured `forge_webhook_secret`.

## Options considered
1) **Wrap existing GitHub CLI calls behind a thin interface.** Minimal diff but still GitHub-only; does not help Gitea where `gh` is unavailable.
2) **HTTP-native forge client for both GitHub and Gitea.** Uses REST APIs for issue comments/closure/recovery and PR creation/lookup; keeps sandbox git pushes unchanged. More code, but fully forge-agnostic and testable without CLI deps.
3) **Hybrid: HTTP for Gitea, keep `gh` CLI for GitHub.** Medium effort but two codepaths to maintain and harder to test uniformly.

**Decision:** Option 2 — HTTP-native clients for both GitHub and Gitea, instantiated via `ForgeClient.from_settings()`. This keeps behavior uniform, makes PR head lookups deterministic, and removes host reliance on `gh` CLI.

## Proposed design

### Forge client (`forge.py`)
- Define `ForgeClient` ABC with methods:
  - `verify_webhook(headers, body) -> bool` (HMAC using `forge_webhook_secret`).
  - `parse_issue_event(payload, event_type, action) -> IssueEvent | None` returning task_id, repo_full, issue number/title/body, labels, action, optional CI context.
  - `comment_issue(issue_number, body)`, `close_issue(issue_number)`, `is_issue_open_with_label(issue_number, label)`, `list_open_agent_issues(label)`.
  - `get_pr_url_by_head(head_branch)` and `create_pr(head_branch, base_branch, issue_number=None, body=None)` returning PR URL.
- Implement `GitHubClient` using `forge_api_base` (default `https://api.github.com`) with token bearer auth; use GitHub REST routes for issues and pulls.
- Implement `GiteaClient` against `forge_api_base` (default derived from settings) with similar methods using `/api/v1` routes.
- Provide `from_settings(settings)` factory returning the correct client.

### Channel refactor
- Replace `GitHubChannel` with `ForgeChannel` that accepts a `ForgeClient` and settings:
  - Webhook endpoint at `/webhook/<forge_type>`; validate signatures via client.
  - On issue label/reopen events, enqueue tasks using `forge_repo` and `agent_label`; include CI failure context when present.
  - On new issue comments, enqueue content if the issue still carries the agent label.
  - `deliver_result`/`deliver_error`/`is_valid` delegate to `ForgeClient` HTTP calls (comment/close/state check).
  - `recover_tasks` uses `list_open_agent_issues` to backfill tasks and posts a recovery comment.

### Core refactor
- Replace `_process_github` with `_process_forge_task` using `forge_client`:
  - Clone URL built from `settings.forge_web_url` + `forge_repo`.
  - Branch naming logic unchanged; uses `settings.forge_default_branch` fallback where needed.
  - After sandbox validation/push, call `forge_client.get_pr_url_by_head` first; if absent, call `forge_client.create_pr(head_branch, base_branch, issue_num)`; write PR URL to IPC.
  - Continue Matrix path unchanged.

### Sandbox updates
- Use `settings.forge_*` for environment and git auth:
  - Export `FORGE_TOKEN`, `FORGE_API_BASE`, `FORGE_WEB_URL` into containers.
  - For GitHub, keep `gh auth setup-git` when token present; for other forges, write `~/.git-credentials` entry using token + `forge_web_url` to allow `git push`.
- Clone/push commands use `forge_web_url` and `forge_repo` instead of hardcoded github.com.

### Testing
- Add unit tests for `ForgeClient` GitHub + Gitea implementations (signature validation, PR head lookup, PR creation URL parsing, issue state filtering).
- Extend `test_core_github` (or new forge test) to cover PR URL head lookup before creation and forge URL usage for clone/push.
- Update channel webhook tests to use the new ForgeChannel with signature validation and recovery via the client; cover agent-label filtering and CI context reuse.

## Risks / mitigations
- **API surface differences** between GitHub and Gitea (fields, pagination). Mitigate by normalizing in client methods and focusing on required fields only.
- **Token leakage in logs** when embedding in clone URLs. Mitigate by preferring credential helper writes and avoiding logging URLs containing tokens.
- **Behavioral drift** from CLI-based flows. Mitigate via tests that assert comment/close/pr-creation requests and PR head lookup logic.
