"""Unit tests for sandbox utility functions."""

import pytest

from matrix_agent.sandbox import SandboxManager, _container_name


def test_container_name_alphanumeric():
    """Test with simple alphanumeric input and allowed characters."""
    assert _container_name("room123") == "sandbox-room123"
    assert _container_name("Room_456") == "sandbox-Room_456"
    assert _container_name("room.name") == "sandbox-room.name"
    assert _container_name("room-name") == "sandbox-room-name"
    assert _container_name("123.456-789_abc") == "sandbox-123.456-789_abc"


def test_container_name_special_chars():
    """Test with special characters that should be replaced by dashes."""
    # Matrix room IDs usually look like !hash:server.tld
    assert _container_name("!room:example.com") == "sandbox-room-example.com"
    # Multiple special chars in a row should be collapsed if they are adjacent? 
    # Actually re.sub(r"[^a-zA-Z0-9_.-]", "-", chat_id) replaces each char with a dash.
    assert _container_name("abc#$%123") == "sandbox-abc---123"


def test_container_name_stripping():
    """Test that leading/trailing dashes are stripped from the slug."""
    assert _container_name("!!!room!!!") == "sandbox-room"
    assert _container_name("###") == "sandbox-"


def test_container_name_empty():
    """Test with empty string."""
    assert _container_name("") == "sandbox-"


def test_container_name_long():
    """Test with long input."""
    long_id = "a" * 100
    assert _container_name(long_id) == f"sandbox-{long_id}"


@pytest.mark.asyncio
async def test_create_exports_forge_env(settings):
    settings.forge_token = "tok"
    settings.forge_api_base = "https://forge/api/v1"
    settings.forge_web_url = "https://forge"
    sm = SandboxManager(settings)
    calls = []

    async def fake_run(*args, **kwargs):
        calls.append(args)
        return (0, "", "")

    sm._run = fake_run
    await sm.create("room1")

    run_args = " ".join(" ".join(map(str, c)) for c in calls if c)
    assert "FORGE_TOKEN=tok" in run_args
    assert "FORGE_API_BASE=https://forge/api/v1" in run_args
    assert "FORGE_WEB_URL=https://forge" in run_args


@pytest.mark.asyncio
async def test_non_github_writes_git_credentials(settings):
    settings.forge_type = "gitea"
    settings.forge_token = "secrettok"
    settings.forge_web_url = "https://forge.example"
    sm = SandboxManager(settings)
    calls = []

    async def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return (0, "", "")

    sm._run = fake_run
    await sm.create("room2")

    scripts = [kw.get("stdin_data", b"").decode() for _, kw in calls if "stdin_data" in kw]
    assert any("git-credentials" in s and "forge.example" in s for s in scripts)
