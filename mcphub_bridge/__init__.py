"""
MCP Hub Bridge - Local stdio to HTTPS bridge for Claude Desktop.

This package bridges Claude Desktop (which speaks stdio JSON-RPC) to
MCP Hub (which speaks HTTPS with authentication).

Usage:
    # As a module
    python -m mcphub_bridge

    # As a script (after pip install)
    mcphub-bridge

Configuration:
    Create ~/.mcphub/config.yaml with your MCP Hub URL and credentials.
    See config.py for configuration options.
"""

from mcphub_bridge.bridge import MCPHubBridge, main
from mcphub_bridge.auth import AuthManager
from mcphub_bridge.config import BridgeConfig

__version__ = "0.1.0"
__all__ = ["MCPHubBridge", "AuthManager", "BridgeConfig", "main"]
