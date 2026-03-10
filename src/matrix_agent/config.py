from typing import Literal, Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "", "env_file": ".env", "extra": "ignore"}

    vps_ip: str = ""
    matrix_homeserver: str = ""
    matrix_user: str = ""
    matrix_password: str

    forge_type: Literal["github", "gitea"] = "github"
    forge_api_base: str = ""
    forge_repo: str = ""
    forge_token: str = ""
    forge_web_url: str = ""
    forge_webhook_secret: str = ""
    forge_default_branch: Optional[str] = None
    agent_label: str = "agent-task"

    # Legacy GitHub-specific fields (for backward compatibility)
    github_token: str = ""
    github_repo: str = ""
    github_webhook_port: int = 8090
    github_webhook_secret: str = ""

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
    ipc_base_dir: str = "/tmp/sandbox-ipc"

    @model_validator(mode="after")
    def derive_defaults(self) -> "Settings":
        if self.vps_ip:
            if not self.matrix_homeserver:
                self.matrix_homeserver = f"http://{self.vps_ip}:8008"
            if not self.matrix_user:
                self.matrix_user = f"@matrixbot:{self.vps_ip}"
        elif not self.matrix_homeserver:
            self.matrix_homeserver = "https://matrix.org"

        if self.forge_type == "github":
            if not self.forge_api_base:
                self.forge_api_base = "https://api.github.com"
            if not self.forge_web_url:
                self.forge_web_url = "https://github.com"
            if not self.forge_repo:
                self.forge_repo = self.github_repo
            if not self.forge_token:
                self.forge_token = self.github_token
            if not self.forge_webhook_secret:
                self.forge_webhook_secret = self.github_webhook_secret
            # Backfill legacy fields for existing code paths
            if not self.github_repo:
                self.github_repo = self.forge_repo
            if not self.github_token:
                self.github_token = self.forge_token
            if not self.github_webhook_secret:
                self.github_webhook_secret = self.forge_webhook_secret
        else:
            # Derive web URL from API base (common pattern for Gitea/Forgejo)
            if self.forge_api_base and not self.forge_web_url:
                api_base = self.forge_api_base.rstrip("/")
                if api_base.endswith("/api/v1"):
                    api_base = api_base[: -len("/api/v1")]
                self.forge_web_url = api_base

        return self
