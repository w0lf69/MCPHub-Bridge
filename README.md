# MCP Hub Bridge

Local bridge that connects Claude Desktop to MCP Hub with authentication.

## Overview

Claude Desktop speaks stdio JSON-RPC. MCP Hub speaks HTTPS with authentication.
This bridge translates between them.

```
Claude Desktop ──stdio──▶ mcphub-bridge ──HTTPS+Auth──▶ MCP Hub
```

## Installation

```bash
# From the mcphub-bridge directory
pip install -e .

# Or install directly
pip install httpx pyyaml
```

## Configuration

Create `~/.mcphub/config.yaml`:

```yaml
# MCP Hub connection
hub:
  url: "https://mcphub.yourdomain.com"
  timeout: 300  # 5 minutes for long operations
  verify_ssl: true  # Set false for self-signed certs in dev

# Logging
logging:
  file: "~/.mcphub/bridge.log"
  level: "INFO"
```

Set your API key as an environment variable:

**Windows (PowerShell):**
```powershell
[Environment]::SetEnvironmentVariable("MCPHUB_API_KEY", "your-api-key-here", "User")
```

**Linux/Mac:**
```bash
export MCPHUB_API_KEY="your-api-key-here"
```

## Claude Desktop Configuration

Edit `%APPDATA%\Claude\claude_desktop_config.json` (Windows) or `~/.config/claude/claude_desktop_config.json` (Linux/Mac):

```json
{
  "mcpServers": {
    "mcphub": {
      "command": "python",
      "args": ["-m", "mcphub_bridge"],
      "env": {
        "MCPHUB_API_KEY": "your-api-key-here",
        "MCPHUB_URL": "https://mcphub.yourdomain.com"
      }
    }
  }
}
```

Or if you installed via pip:

```json
{
  "mcpServers": {
    "mcphub": {
      "command": "mcphub-bridge",
      "env": {
        "MCPHUB_API_KEY": "your-api-key-here",
        "MCPHUB_URL": "https://mcphub.yourdomain.com"
      }
    }
  }
}
```

## Testing

```bash
# Test the bridge manually
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | python -m mcphub_bridge
```

## Troubleshooting

Check the log file at `~/.mcphub/bridge.log` for errors.

Common issues:
- **Authentication failed**: Check your API key is correct
- **Connection refused**: MCP Hub may not be running or URL is wrong
- **SSL certificate error**: Set `verify_ssl: false` in config for self-signed certs

## Security

- API keys are stored in environment variables, not in config files
- All communication with MCP Hub is over HTTPS
- The bridge only forwards requests, it doesn't store any data
