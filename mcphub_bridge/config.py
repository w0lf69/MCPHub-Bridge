"""
Configuration loader for MCP Hub Bridge.

Loads configuration from:
1. ~/.mcphub/config.yaml (primary)
2. Environment variables (override)
"""

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

import yaml


@dataclass
class BridgeConfig:
    """Configuration for MCP Hub Bridge."""

    # MCP Hub connection
    hub_url: str = "https://mcphub.localhost"
    timeout: int = 300  # 5 minutes default

    # API Key authentication (simplest method)
    api_key: str = ""

    # OIDC authentication (optional - for token-based auth)
    oidc_token_url: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""

    # Environment variable names for secrets
    api_key_env: str = "MCPHUB_API_KEY"
    oidc_secret_env: str = "MCPHUB_OIDC_SECRET"

    # Logging
    log_file: Optional[str] = None
    log_level: str = "INFO"

    # SSL verification (set to False for self-signed certs in dev)
    verify_ssl: bool = True

    @classmethod
    def load(cls, config_path: str = None) -> "BridgeConfig":
        """
        Load configuration from file and environment.

        Args:
            config_path: Path to config file. Defaults to ~/.mcphub/config.yaml

        Returns:
            BridgeConfig instance
        """
        config = cls()

        # Determine config path
        if config_path is None:
            config_path = Path.home() / ".mcphub" / "config.yaml"
        else:
            config_path = Path(config_path)

        # Load from file if exists
        if config_path.exists():
            with open(config_path) as f:
                data = yaml.safe_load(f) or {}

            # Hub settings
            hub = data.get("hub", {})
            if "url" in hub:
                config.hub_url = hub["url"]
            if "timeout" in hub:
                config.timeout = hub["timeout"]
            if "verify_ssl" in hub:
                config.verify_ssl = hub["verify_ssl"]

            # OIDC settings
            oidc = data.get("oidc", {})
            if "token_url" in oidc:
                config.oidc_token_url = oidc["token_url"]
            if "client_id" in oidc:
                config.oidc_client_id = oidc["client_id"]

            # Environment variable names
            if "api_key_env" in data:
                config.api_key_env = data["api_key_env"]
            if "oidc_secret_env" in data:
                config.oidc_secret_env = data["oidc_secret_env"]

            # Logging
            logging = data.get("logging", {})
            if "file" in logging:
                config.log_file = logging["file"]
            if "level" in logging:
                config.log_level = logging["level"]

        # Load secrets from environment (override file values)
        config.api_key = os.environ.get(config.api_key_env, config.api_key)
        config.oidc_client_secret = os.environ.get(
            config.oidc_secret_env, config.oidc_client_secret
        )

        # Also check direct environment variables
        if not config.api_key:
            config.api_key = os.environ.get("MCPHUB_API_KEY", "")
        if not config.hub_url or config.hub_url == "https://mcphub.localhost":
            config.hub_url = os.environ.get("MCPHUB_URL", config.hub_url)

        return config

    def validate(self) -> list[str]:
        """
        Validate configuration and return list of errors.

        Returns:
            List of error messages (empty if valid)
        """
        errors = []

        if not self.hub_url:
            errors.append("hub_url is required")

        if not self.api_key and not self.oidc_client_id:
            errors.append("Either api_key or oidc credentials are required")

        return errors
