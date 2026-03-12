from matrix_agent.config import Settings


def test_derive_from_vps_ip_success():
    """When vps_ip is set and matrix_homeserver/matrix_user are empty, they should be derived from vps_ip."""
    vps_ip = "1.2.3.4"
    settings = Settings(
        vps_ip=vps_ip,
        matrix_password="pass",
        llm_api_key="key"
    )
    assert settings.matrix_homeserver == f"http://{vps_ip}:8008"
    assert settings.matrix_user == f"@matrixbot:{vps_ip}"


def test_derive_from_vps_ip_no_overwrite():
    """When matrix_homeserver and matrix_user are already set, they should NOT be overwritten."""
    vps_ip = "1.2.3.4"
    custom_hs = "https://custom.hs"
    custom_user = "@custom:user"
    settings = Settings(
        vps_ip=vps_ip,
        matrix_homeserver=custom_hs,
        matrix_user=custom_user,
        matrix_password="pass",
        llm_api_key="key"
    )
    assert settings.matrix_homeserver == custom_hs
    assert settings.matrix_user == custom_user


def test_derive_from_vps_ip_empty_vps_ip():
    """When vps_ip is empty, derived fields should remain unchanged if they have values."""
    custom_hs = "https://example.com"
    custom_user = "@user:example.com"
    settings = Settings(
        vps_ip="",
        matrix_homeserver=custom_hs,
        matrix_user=custom_user,
        matrix_password="pass",
        llm_api_key="key"
    )
    assert settings.matrix_homeserver == custom_hs
    assert settings.matrix_user == custom_user


def test_derive_from_vps_ip_empty_vps_ip_fallback():
    """When vps_ip is empty and matrix_homeserver is empty, it should default to matrix.org."""
    settings = Settings(
        vps_ip="",
        matrix_homeserver="",
        matrix_password="pass",
        llm_api_key="key"
    )
    assert settings.matrix_homeserver == "https://matrix.org"
    assert settings.matrix_user == ""


def test_derive_from_vps_ip_partial_overwrite():
    """When only one of matrix_homeserver or matrix_user is set, the other should still be derived if vps_ip is set."""
    vps_ip = "1.2.3.4"
    custom_hs = "https://custom.hs"
    settings = Settings(
        vps_ip=vps_ip,
        matrix_homeserver=custom_hs,
        matrix_password="pass",
        llm_api_key="key"
    )
    assert settings.matrix_homeserver == custom_hs
    assert settings.matrix_user == f"@matrixbot:{vps_ip}"

def test_headless_defaults():
    settings = Settings(
        matrix_password="pass",
        llm_api_key="key",
    )

    assert settings.headless_mode == "matrix"
    assert settings.headless_default_workflow is None
    assert settings.redis_url == "redis://localhost:6379/0"
    assert settings.redis_jobs_key == "matrix-tui:jobs"
    assert settings.redis_results_prefix == "matrix-tui:results:"
    assert settings.redis_results_list == "matrix-tui:results:list"
    assert settings.redis_result_ttl_seconds == 86400
    assert settings.headless_timeout_seconds == settings.coding_timeout_seconds
    assert settings.headless_progress_enabled is False


def test_headless_overrides():
    settings = Settings(
        matrix_password="pass",
        llm_api_key="key",
        headless_mode="queue",
        headless_default_workflow="wf",
        redis_url="redis://redis:6380/1",
        redis_jobs_key="jobs:list",
        redis_results_prefix="results:list:",
        redis_results_list="results:list:queue",
        redis_result_ttl_seconds=3600,
        headless_timeout_seconds=120,
        headless_progress_enabled=True,
    )

    assert settings.headless_mode == "queue"
    assert settings.headless_default_workflow == "wf"
    assert settings.redis_url == "redis://redis:6380/1"
    assert settings.redis_jobs_key == "jobs:list"
    assert settings.redis_results_prefix == "results:list:"
    assert settings.redis_results_list == "results:list:queue"
    assert settings.redis_result_ttl_seconds == 3600
    assert settings.headless_timeout_seconds == 120
    assert settings.headless_progress_enabled is True
