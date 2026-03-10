# Forge-agnostic client Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a forge-agnostic pipeline (GitHub + Gitea) with a new `forge.py` abstraction, PR head lookup, and updated channels/core/sandbox that consume `forge_*` config fields.

**Architecture:** Introduce `ForgeClient` (HTTP-based) with GitHub/Gitea implementations. Channels delegate webhook + issue lifecycle to the client. Core uses the client for PR lookup/creation and forge URLs for clone/push. Sandbox seeds forge env/auth for git pushes using the new config.

**Tech Stack:** Python 3.12, aiohttp, asyncio, git, pytest.

---

### Task 1: Add ForgeClient abstractions and tests

**Files:**
- Create: `src/matrix_agent/forge.py`
- Create: `tests/test_forge.py`

**Step 1: Write failing tests for ForgeClient**
- Cover GitHub + Gitea client behaviors:
  - Signature validation (`verify_webhook` HMAC header) for both forges.
  - `get_pr_url_by_head` uses API endpoints and returns None when missing.
  - `create_pr` builds correct URL/body and returns parsed HTML URL.
  - Issue helpers (`is_issue_open_with_label`, `list_open_agent_issues`) normalize label filtering.
- Use `aiohttp` mock server or monkeypatch `aiohttp.ClientSession` to return canned JSON.

**Step 2: Run tests to verify they fail**
- Run: `uv run pytest tests/test_forge.py -v`
- Expect: FAIL (module not found / behaviors not implemented).

**Step 3: Implement ForgeClient**
- Define ABC with methods: `verify_webhook`, `parse_issue_event`, `comment_issue`, `close_issue`, `is_issue_open_with_label`, `list_open_agent_issues`, `get_pr_url_by_head`, `create_pr`.
- Implement `GitHubClient` using `forge_api_base` (default https://api.github.com), bearer token auth, GitHub REST paths, HTML URLs from `html_url`.
- Implement `GiteaClient` using `/api/v1` routes, respecting `forge_web_url` for HTML links, token auth via header.
- Provide factory `ForgeClient.from_settings(settings)`.

**Step 4: Re-run tests**
- Run: `uv run pytest tests/test_forge.py -v`
- Expect: PASS.

**Step 5: Commit**
- `git add src/matrix_agent/forge.py tests/test_forge.py`
- `git commit -m "feat: add forge client abstractions"`

### Task 2: Refactor channel to forge-agnostic

**Files:**
- Modify: `src/matrix_agent/channels.py`
- Modify: `src/matrix_agent/__main__.py`
- Modify: `tests/test_channels.py`
- Modify: `tests/test_core_github.py` (adjust channel expectations if needed)

**Step 1: Write failing channel tests**
- Update/add tests to assert:
  - Webhook uses `ForgeClient.verify_webhook` and rejects bad signatures.
  - Issue labeled/reopened events enqueue tasks using `forge_repo` + `agent_label`.
  - `deliver_result`/`deliver_error` delegate to client comment/close calls.
  - `recover_tasks` uses `list_open_agent_issues` and posts recovery comment.
- Run: `uv run pytest tests/test_channels.py -v`
- Expect: FAIL on new assertions.

**Step 2: Implement forge-aware channel**
- Replace `GitHubChannel` with `ForgeChannel` (or adapt) that accepts `forge_client` + settings.
- Webhook endpoint `/webhook/<forge_type>`; parse payload via `forge_client.parse_issue_event` and enqueue messages.
- Delegate deliver/is_valid/recover to `forge_client` methods.
- Update `__main__.py` to instantiate client from settings and start channel when `forge_token` is present.

**Step 3: Re-run channel tests**
- Run: `uv run pytest tests/test_channels.py -v`
- Expect: PASS.

**Step 4: Commit**
- `git add src/matrix_agent/channels.py src/matrix_agent/__main__.py tests/test_channels.py`
- `git commit -m "refactor: add forge channel abstraction"`

### Task 3: Core + sandbox forge config and PR head lookup

**Files:**
- Modify: `src/matrix_agent/core.py`
- Modify: `src/matrix_agent/sandbox.py`
- Modify: `tests/test_core_github.py`
- Modify: `tests/test_sandbox_utils.py` (env expectations)

**Step 1: Add failing core tests for PR head lookup and forge URLs**
- Extend `_host_push` tests to assert it:
  - Calls `get_pr_url_by_head` before `create_pr` and uses returned URL.
  - Uses clone/push URLs derived from `forge_web_url`/`forge_repo`.
- Run: `uv run pytest tests/test_core_github.py -k "host_push" -v`
- Expect: FAIL on new assertions.

**Step 2: Add failing sandbox/env tests**
- In `tests/test_sandbox_utils.py`, assert containers export `FORGE_TOKEN/FORGE_API_BASE/FORGE_WEB_URL` and write git credentials for non-GitHub forges.
- Run: `uv run pytest tests/test_sandbox_utils.py -v`
- Expect: FAIL on new expectations.

**Step 3: Implement core refactor**
- Inject `forge_client` into `TaskRunner` (init arg) and use it in `_process_forge_task` (rename from `_process_github`).
- Build clone URL from settings, not hardcoded github.com.
- After push, call `forge_client.get_pr_url_by_head` then `create_pr`; write PR URL to IPC.

**Step 4: Implement sandbox forge env/auth**
- Export forge env vars on container creation.
- If forge type != github and token present, write `~/.git-credentials` with token + `forge_web_url` for HTTPS pushes.
- Keep GitHub gh auth for github type.

**Step 5: Re-run focused tests**
- Run: `uv run pytest tests/test_core_github.py -k "host_push" -v`
- Run: `uv run pytest tests/test_sandbox_utils.py -v`
- Expect: PASS.

**Step 6: Commit**
- `git add src/matrix_agent/core.py src/matrix_agent/sandbox.py tests/test_core_github.py tests/test_sandbox_utils.py`
- `git commit -m "feat: make core and sandbox forge-aware"`

### Task 4: Full test sweep and cleanup

**Files:**
- Verify repo root (no new files).

**Step 1: Run full test suite**
- Run: `uv run pytest -v`
- Expect: PASS.

**Step 2: Final review & cleanup**
- Check `git status -sb` clean.
- Ensure docs/plans updated.

**Step 3: Commit if needed**
- If additional fixes: `git add ... && git commit -m "chore: finalize forge support"`

---

Plan complete and saved to `docs/plans/2026-03-11-forge-agnostic-plan.md`. Execution: proceed in this session using subagent-driven task-by-task with checkpoints.
