from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "", "env_file": ".env", "extra": "ignore"}

    vps_ip: str = ""
    matrix_homeserver: str = ""
    matrix_user: str = ""
    matrix_password: str

    @model_validator(mode="after")
    def derive_from_vps_ip(self) -> "Settings":
        if self.vps_ip:
            if not self.matrix_homeserver:
                self.matrix_homeserver = f"http://{self.vps_ip}:8008"
            if not self.matrix_user:
                self.matrix_user = f"@matrixbot:{self.vps_ip}"
        elif not self.matrix_homeserver:
            self.matrix_homeserver = "https://matrix.org"

        if self.headless_timeout_seconds is None:
            self.headless_timeout_seconds = self.coding_timeout_seconds
        return self
    llm_api_key: str
    llm_api_base: str = ""
    llm_model: str = "openrouter/anthropic/claude-haiku-4-5"
    podman_path: str = "podman"
    sandbox_image: str = "matrix-agent-sandbox:latest"
    command_timeout_seconds: int = 120
    coding_timeout_seconds: int = 2400
    max_agent_turns: int = 30
    screenshot_script: str = "/opt/playwright/screenshot.js"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3-flash-preview"
    dashscope_api_key: str = ""
    github_token: str = ""
    github_repo: str = ""
    github_webhook_port: int = 8090
    github_webhook_secret: str = ""
    ipc_base_dir: str = "/tmp/sandbox-ipc"
    # Headless / Redis
    headless_mode: str = "matrix"
    headless_default_workflow: str | None = None
    redis_url: str = "redis://localhost:6379/0"
    redis_jobs_key: str = "matrix-tui:jobs"
    redis_results_prefix: str = "matrix-tui:results:"
    redis_results_list: str = "matrix-tui:results:list"
    redis_result_ttl_seconds: int = 86400
    headless_timeout_seconds: int | None = None
    headless_progress_enabled: bool = False
