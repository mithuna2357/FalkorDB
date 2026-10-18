"""Print a ready-to-paste MCP config for this checkout, with the real absolute paths filled in.

    python mcp/print_config.py              # JSON for Claude Desktop, Cursor, Windsurf, Antigravity, Gemini CLI, Cline
    python mcp/print_config.py vscode       # VS Code's "servers" shape
    python mcp/print_config.py codex        # TOML for OpenAI Codex
    python mcp/print_config.py claude-code  # the `claude mcp add` command
    python mcp/print_config.py --org Acme   # default org other than FalkorDB
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "mcp" / "server.py"
PYTHON = ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("flavor", nargs="?", default="json", choices=["json", "vscode", "codex", "claude-code"])
    ap.add_argument("--org", default="FalkorDB", help="BRAIN_DEFAULT_ORG")
    a = ap.parse_args()
    python = str(PYTHON if PYTHON.exists() else Path(sys.executable))
    server = str(SERVER)
    env = {"BRAIN_DEFAULT_ORG": a.org}

    if a.flavor == "json":
        print(json.dumps({"mcpServers": {"handsonrepo": {"command": python, "args": [server], "env": env}}}, indent=2))
    elif a.flavor == "vscode":
        print(json.dumps({"servers": {"handsonrepo": {"type": "stdio", "command": python, "args": [server], "env": env}}}, indent=2))
    elif a.flavor == "codex":
        print("[mcp_servers.handsonrepo]")
        print(f"command = '{python}'")
        print(f"args = ['{server}']")
        print("startup_timeout_sec = 30")
        print()
        print("[mcp_servers.handsonrepo.env]")
        print(f'BRAIN_DEFAULT_ORG = "{a.org}"')
    else:
        print(f"claude mcp add handsonrepo -s user -e BRAIN_DEFAULT_ORG={a.org} -- {python} {server}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
