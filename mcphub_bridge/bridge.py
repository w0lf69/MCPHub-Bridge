#!/usr/bin/env python3
"""
MCP Hub Local Bridge

Bridges Claude Desktop (stdio) to MCP Hub (HTTPS) with proper authentication.

Supports two auth modes:
1. API Key only - simpler, set MCPHUB_API_KEY env var
2. OIDC + API Key - more secure, requires Authelia OIDC config

This is the main entry point for the bridge. It:
1. Reads JSON-RPC from stdin (from Claude Desktop)
2. Authenticates (OIDC token if enabled, API key always)
3. Forwards requests to MCP Hub with auth headers
4. Writes JSON-RPC responses to stdout (back to Claude)

Usage:
    python -m mcphub_bridge
    mcphub-bridge  # If installed via pip
"""

import sys
import json
import asyncio
import logging
from pathlib import Path
from typing import Optional

import httpx

from mcphub_bridge.config import BridgeConfig
from mcphub_bridge.auth import AuthManager


# Set up logging to file (stderr would interfere with stdio)
def setup_logging(config: BridgeConfig):
    """Configure logging based on config."""
    log_file = config.log_file
    if not log_file:
        log_file = str(Path.home() / ".mcphub" / "bridge.log")

    # Ensure directory exists
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(log_file)]
    )


logger = logging.getLogger("mcphub.bridge")


def sanitize_string(s: str) -> str:
    """Sanitize a single string by removing surrogate characters.

    Args:
        s: String that may contain UTF-16 surrogates

    Returns:
        Clean UTF-8 string with surrogates replaced by U+FFFD
    """
    return s.encode("utf-8", errors="surrogatepass").decode("utf-8", errors="replace")


def sanitize_dict(obj):
    """Recursively sanitize all strings in a dict/list structure.

    Windows stdio can produce UTF-16 surrogate characters (like \\udc90)
    that are invalid in UTF-8. JSON-escaped surrogates like \\udc90 get
    decoded into actual surrogate codepoints by json.loads(), which then
    fail when httpx tries to re-encode for HTTP.

    This recursively sanitizes all string values so the dict can be
    safely serialized to JSON for HTTP requests.

    Args:
        obj: Dict, list, or primitive value

    Returns:
        Sanitized copy with all strings cleaned
    """
    if isinstance(obj, str):
        return sanitize_string(obj)
    elif isinstance(obj, dict):
        return {k: sanitize_dict(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_dict(item) for item in obj]
    else:
        return obj


def sanitize_stdio_input(raw: str) -> str:
    """Normalize Windows stdio encoding to clean UTF-8.

    Windows stdio can produce UTF-16 surrogate characters (like \\udc90)
    that are invalid in UTF-8. This sanitizes input ONCE at the boundary
    so all downstream JSON operations work cleanly.

    Args:
        raw: Raw string from stdio (may contain surrogates)

    Returns:
        Clean UTF-8 string with surrogates replaced
    """
    return sanitize_string(raw)


class MCPHubBridge:
    """
    Stdio to HTTPS bridge for MCP Hub.

    Reads JSON-RPC from stdin, forwards to MCP Hub with authentication,
    writes responses to stdout.
    """

    def __init__(self, config: BridgeConfig):
        """
        Initialize the bridge.

        Args:
            config: Bridge configuration
        """
        self.config = config
        self.auth = AuthManager(config)
        self.http: Optional[httpx.AsyncClient] = None

    async def start(self):
        """Initialize HTTP client and authenticate."""
        self.http = httpx.AsyncClient(
            base_url=self.config.hub_url,
            timeout=self.config.timeout,
            verify=self.config.verify_ssl
        )

        # Authenticate (acquires OIDC token if enabled)
        await self.auth.ensure_authenticated()

        logger.info(f"Bridge started, connecting to {self.config.hub_url}")

    async def close(self):
        """Close HTTP client."""
        if self.http:
            await self.http.aclose()
            logger.info("Bridge closed")

    async def forward_request(self, request: dict) -> dict:
        """
        Forward a JSON-RPC request to MCP Hub.

        Args:
            request: JSON-RPC request dict

        Returns:
            JSON-RPC response dict
        """
        # Ensure we have fresh auth
        await self.auth.ensure_authenticated()

        # Get auth headers (includes API key and Bearer token if OIDC enabled)
        headers = self.auth.get_headers()

        # Sanitize request to remove any surrogate characters that would
        # cause httpx to fail when encoding to UTF-8 JSON
        clean_request = sanitize_dict(request)

        try:
            response = await self.http.post(
                "/route",  # MCP Hub's routing endpoint
                json=clean_request,
                headers=headers
            )

            if response.status_code == 401:
                # Token might be expired, try to refresh and retry
                logger.warning("Got 401, attempting token refresh...")
                self.auth.clear_tokens()
                await self.auth.ensure_authenticated()

                # Retry with fresh headers
                headers = self.auth.get_headers()
                response = await self.http.post(
                    "/route",
                    json=request,
                    headers=headers
                )

                if response.status_code == 401:
                    logger.error("Authentication failed after refresh")
                    return {
                        "jsonrpc": "2.0",
                        "id": request.get("id"),
                        "error": {
                            "code": -32001,
                            "message": "Authentication failed - check credentials"
                        }
                    }

            if response.status_code == 403:
                logger.error("Permission denied")
                return {
                    "jsonrpc": "2.0",
                    "id": request.get("id"),
                    "error": {
                        "code": -32002,
                        "message": "Permission denied"
                    }
                }

            if response.status_code != 200:
                logger.error(f"HTTP error: {response.status_code}")
                return {
                    "jsonrpc": "2.0",
                    "id": request.get("id"),
                    "error": {
                        "code": -32603,
                        "message": f"HTTP error: {response.status_code}"
                    }
                }

            return response.json()

        except httpx.ConnectError as e:
            logger.error(f"Connection error: {e}")
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "error": {
                    "code": -32603,
                    "message": f"Cannot connect to MCP Hub: {self.config.hub_url}"
                }
            }
        except httpx.TimeoutException:
            logger.error("Request timeout")
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "error": {
                    "code": -32603,
                    "message": "Request timeout"
                }
            }
        except Exception as e:
            logger.exception(f"Error forwarding request: {e}")
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "error": {
                    "code": -32603,
                    "message": str(e)
                }
            }

    async def handle_local_method(self, request: dict) -> Optional[dict]:
        """
        Handle methods that should be processed locally.

        Args:
            request: JSON-RPC request

        Returns:
            Response dict if handled locally, None to forward to hub
        """
        method = request.get("method", "")

        # Handle initialize locally (MCP protocol requirement)
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {}
                    },
                    "serverInfo": {
                        "name": "mcphub-bridge",
                        "version": "0.1.0"
                    }
                }
            }

        # Handle notifications locally - notifications don't get responses!
        if method == "notifications/initialized":
            logger.debug("Received notifications/initialized")
            return None

        # Handle any other notification (methods starting with notifications/)
        if method.startswith("notifications/"):
            logger.debug(f"Received notification: {method}")
            return None

        return None  # Forward to hub

    async def process_line(self, line: str) -> Optional[str]:
        """
        Process a single line of JSON-RPC input.

        Args:
            line: JSON string (may contain Windows surrogate characters)

        Returns:
            JSON response string, or None for notifications
        """
        # Sanitize at the boundary - ONCE
        line = sanitize_stdio_input(line)

        try:
            data = json.loads(line)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON: {e}")
            return json.dumps({
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32700,
                    "message": f"Parse error: {e}"
                }
            })

        # Handle batch requests
        if isinstance(data, list):
            responses = []
            for item in data:
                # Try local handling first
                local_response = await self.handle_local_method(item)
                if local_response is not None:
                    responses.append(local_response)
                elif local_response is None and item.get("method", "").startswith("notifications/"):
                    # Skip notifications - don't forward or respond
                    continue
                else:
                    response = await self.forward_request(item)
                    responses.append(response)
            return json.dumps(responses) if responses else None

        # Handle single request
        # Check if it's a notification first
        if data.get("method", "").startswith("notifications/"):
            logger.debug(f"Skipping notification: {data.get('method')}")
            return None

        # Try local handling
        local_response = await self.handle_local_method(data)
        if local_response is not None:
            return json.dumps(local_response)

        # Forward to hub
        response = await self.forward_request(data)
        return json.dumps(response)

    async def run(self):
        """Main event loop - read stdin, forward, write stdout."""
        await self.start()

        logger.info("Bridge running in stdio mode")

        try:
            loop = asyncio.get_event_loop()

            def read_stdin():
                """Read lines from stdin in a blocking thread."""
                while True:
                    try:
                        line = sys.stdin.readline()
                        if not line:
                            return None
                        return line.strip()
                    except Exception:
                        return None

            while True:
                # Read line in thread to avoid blocking
                line = await loop.run_in_executor(None, read_stdin)
                if line is None:
                    logger.info("EOF on stdin, exiting")
                    break
                if not line:
                    continue

                logger.debug(f"Received: {line[:100]}...")

                response = await self.process_line(line)

                if response:
                    print(sanitize_stdio_input(response), flush=True)
                    logger.debug(f"Sent: {response[:100]}...")

        except asyncio.CancelledError:
            logger.info("Bridge cancelled")
        except Exception as e:
            logger.exception(f"Bridge error: {e}")
            raise
        finally:
            await self.close()


async def async_main():
    """Async entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="MCP Hub Bridge")
    parser.add_argument(
        "--config", "-c",
        default=str(Path.home() / ".mcphub" / "config.yaml"),
        help="Path to config file"
    )
    args = parser.parse_args()

    config = BridgeConfig.load(args.config)

    # Validate config
    errors = config.validate()
    if errors:
        for error in errors:
            print(f"Config error: {error}", file=sys.stderr)
        sys.exit(1)

    # Set up logging
    setup_logging(config)

    bridge = MCPHubBridge(config)
    await bridge.run()


def main():
    """Sync entry point."""
    # Force UTF-8 on stdin/stdout regardless of the host's locale codepage.
    #
    # On Windows, sys.stdin.encoding defaults to the active ANSI codepage
    # (cp1252 in en-US locales). Claude Desktop pipes UTF-8 JSON-RPC bytes
    # to us; without this reconfigure, Python's stdin layer would decode
    # those bytes as cp1252 — turning the UTF-8 bytes for "š" (c5 a1) into
    # the two characters "Å¡", which httpx then re-encodes as UTF-8
    # (c3 85 c2 a1) on the wire. MCPHub stores the doubly-encoded bytes
    # faithfully and every reader sees mojibake forever.
    #
    # Diagnosed 2026-04-29 from raw byte inspection of the wolf-intelligence
    # corpus: 94 facts written through this bridge carried the cp1252
    # double-encoding fingerprint. The corpus was repaired in a separate
    # sweep (wolf-intelligence/scripts/repair_mojibake.py); these two
    # lines stop the leak at the source.
    #
    # `errors='surrogateescape'` preserves any genuinely-unmappable bytes
    # through the pipeline so the existing sanitize_dict() downstream can
    # still scrub them, instead of raising at the boundary.
    sys.stdin.reconfigure(encoding="utf-8", errors="surrogateescape")  # type: ignore[attr-defined]
    sys.stdout.reconfigure(encoding="utf-8", errors="surrogateescape")  # type: ignore[attr-defined]

    asyncio.run(async_main())


if __name__ == "__main__":
    main()