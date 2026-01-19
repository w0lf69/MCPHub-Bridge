"""
Authentication manager for MCP Hub bridge.

Handles:
- OIDC token acquisition via client_credentials flow
- Token refresh when expired
- API key loading from environment

The bridge can use either:
1. API keys only (simpler, works offline)
2. OIDC + API keys (more secure, requires Authelia)
"""

import os
import time
import logging
from typing import Optional
from dataclasses import dataclass

import httpx

logger = logging.getLogger("mcphub.bridge.auth")


@dataclass
class TokenInfo:
    """OIDC token information."""
    access_token: str
    refresh_token: Optional[str]
    expires_at: float  # Unix timestamp


class AuthManager:
    """
    Manages OIDC and API key authentication.

    Supports two modes:
    1. API key only - simpler, set MCPHUB_API_KEY env var
    2. OIDC + API key - more secure, requires Authelia OIDC

    Example usage:
        auth = AuthManager(config)
        await auth.ensure_authenticated()
        headers = auth.get_headers()
    """

    def __init__(self, config):
        """
        Initialize auth manager.

        Args:
            config: BridgeConfig with auth settings
        """
        self.config = config
        self.token_info: Optional[TokenInfo] = None

        # Load API key from environment
        self.api_key = os.environ.get(
            config.api_key_env,
            os.environ.get("MCPHUB_API_KEY", "")
        )

        # Load OIDC client secret from environment (optional)
        self.client_secret = os.environ.get(
            config.oidc_secret_env,
            os.environ.get("MCPHUB_OIDC_SECRET", "")
        )

        # Determine auth mode
        self.oidc_enabled = bool(
            config.oidc_token_url and
            config.oidc_client_id and
            self.client_secret
        )

        if not self.api_key:
            logger.warning("API key not found in environment!")

        if self.oidc_enabled:
            logger.info(f"OIDC enabled: {config.oidc_client_id}")
        else:
            logger.info("OIDC disabled - using API key only")

    @property
    def access_token(self) -> str:
        """Get current access token (empty if OIDC disabled)."""
        if self.token_info:
            return self.token_info.access_token
        return ""

    def get_headers(self) -> dict:
        """
        Get authentication headers for requests.

        Returns:
            Dict with appropriate auth headers
        """
        headers = {"Content-Type": "application/json"}

        # Add API key if available
        if self.api_key:
            headers["X-API-Key"] = self.api_key

        # Add Bearer token if OIDC is enabled and we have a token
        if self.oidc_enabled and self.token_info:
            headers["Authorization"] = f"Bearer {self.token_info.access_token}"

        return headers

    async def ensure_authenticated(self):
        """
        Ensure we have valid credentials.

        For OIDC: acquires or refreshes tokens as needed.
        For API key only: no-op (key is always valid until revoked).
        """
        if not self.oidc_enabled:
            # API key only mode - nothing to do
            return

        if self.token_info is None:
            await self._acquire_token()
        elif time.time() >= self.token_info.expires_at - 60:  # 1 min buffer
            await self.refresh_token()

    async def _acquire_token(self):
        """Acquire new tokens using client credentials flow."""
        if not self.oidc_enabled:
            return

        logger.info("Acquiring new OIDC token...")

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.config.oidc_token_url,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self.config.oidc_client_id,
                        "client_secret": self.client_secret,
                        "scope": "openid groups email profile"
                    }
                )

                if response.status_code != 200:
                    logger.error(f"Token acquisition failed: {response.status_code}")
                    logger.error(f"Response: {response.text}")
                    raise Exception(f"Failed to acquire token: {response.status_code}")

                data = response.json()

                self.token_info = TokenInfo(
                    access_token=data["access_token"],
                    refresh_token=data.get("refresh_token"),
                    expires_at=time.time() + data.get("expires_in", 3600)
                )

                logger.info("Token acquired successfully")

        except httpx.ConnectError as e:
            logger.error(f"Cannot connect to OIDC provider: {e}")
            raise
        except Exception as e:
            logger.exception(f"Token acquisition error: {e}")
            raise

    async def refresh_token(self):
        """Refresh the access token using refresh_token grant."""
        if not self.oidc_enabled:
            return

        # If no refresh token, acquire new
        if not self.token_info or not self.token_info.refresh_token:
            await self._acquire_token()
            return

        logger.info("Refreshing OIDC token...")

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.config.oidc_token_url,
                    data={
                        "grant_type": "refresh_token",
                        "client_id": self.config.oidc_client_id,
                        "client_secret": self.client_secret,
                        "refresh_token": self.token_info.refresh_token
                    }
                )

                if response.status_code != 200:
                    logger.warning("Token refresh failed, acquiring new token")
                    await self._acquire_token()
                    return

                data = response.json()

                self.token_info = TokenInfo(
                    access_token=data["access_token"],
                    refresh_token=data.get("refresh_token", self.token_info.refresh_token),
                    expires_at=time.time() + data.get("expires_in", 3600)
                )

                logger.info("Token refreshed successfully")

        except Exception as e:
            logger.warning(f"Token refresh error: {e}, acquiring new token")
            await self._acquire_token()

    def is_token_valid(self) -> bool:
        """Check if current token is still valid."""
        if not self.oidc_enabled:
            return True  # API key mode - always "valid"

        if not self.token_info:
            return False

        # Valid if not expired (with 60s buffer)
        return time.time() < self.token_info.expires_at - 60

    def clear_tokens(self):
        """Clear cached tokens (forces re-authentication)."""
        self.token_info = None
        logger.info("Tokens cleared")
