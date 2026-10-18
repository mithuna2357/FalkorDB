# HandsOnRepo MCP server

The same FalkorDB graph tools the web agent uses, exposed over the Model Context Protocol (MCP), so any MCP-capable
assistant can query an organisation's brain directly: Claude Desktop, Claude Code, Cursor, Windsurf, Google Antigravity,
OpenAI Codex, VS Code Copilot, Gemini CLI, Cline, JetBrains, and anything else that can launch a stdio server.

Transport: **stdio** (the client launches `server.py` as a subprocess). One server, one command, every client.

## What you need running

1. FalkorDB on `localhost:6379` (Docker: `docker start falkordb`) or FalkorDB Cloud credentials in `backend/.env`.
2. At least one org ingested through the backend (`POST /api/ingest` or `python -m app.ingest <org>`).

The server reads `backend/.env` for the FalkorDB connection. No LLM key is needed: the client's own model does the reasoning.

## The one command every client uses

Every client launches the same stdio server: the repo's Python interpreter running `mcp/server.py`. Print the config
for your checkout with the real absolute paths filled in, then paste it:

```bash
.venv/Scripts/python mcp/print_config.py              # JSON (Claude Desktop, Cursor, Windsurf, Antigravity, Gemini CLI, Cline)
.venv/Scripts/python mcp/print_config.py vscode       # VS Code "servers" shape
.venv/Scripts/python mcp/print_config.py codex        # OpenAI Codex TOML
.venv/Scripts/python mcp/print_config.py claude-code  # the claude mcp add command
```

On macOS and Linux use `.venv/bin/python`. Optional env `BRAIN_DEFAULT_ORG=FalkorDB` lets tool calls omit `org`
(`--org Acme` changes it).

The files in [`configs/`](configs/) and the project-level files at the repo root (`.mcp.json`, `.cursor/mcp.json`,
`.vscode/mcp.json`, `.agents/mcp_config.json`, `.codex/config.toml`) are the same configs generated for the authors'
checkout; regenerate them with the command above if your clone lives somewhere else.

## Tools

| Tool | What it answers |
|---|---|
| `list_orgs` | which organisations have a brain loaded |
| `repo_overview` | repos in the graph with counts |
| `who_knows` | who owns / knows / should review a topic, path or area (commits + reviews + PageRank) |
| `top_reviewers` | most trusted human reviewers and who they review for |
| `reverts` | every revert with who, when, why, and linked issues |
| `recent` | latest PRs, issues, commits or releases, filtered by repo, state or author |
| `node_context` | everything around a PR, issue, commit, person or file |
| `search` | full-text over PRs, issues, docs, commits, files |
| `find_path` | the chain of relationships between two things |
| `bus_factor` | knowledge risk: people bridges, sole-owned files, critical files |
| `clusters` | module clusters from files that change together |
| `owner_history` | current and past owners of a file (SUPERSEDES history) |
| `recall` / `remember` | the org's episodic memory |
| `run_cypher` | read-only Cypher (writes rejected) |

Resource: `brain://orgs`. Every tool returns `{result, sources, cypher, ms}` so the assistant can cite evidence.

## Verify it works

```bash
D:\Falkordb\.venv\Scripts\python.exe D:\Falkordb\mcp\test_client.py
```

Expected: `15 tools: ...`, real people for `who_knows`, `write guard: rejected`, `MCP OK`.

---

## Claude Desktop

File: `%APPDATA%\Claude\claude_desktop_config.json` (Windows) or `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS).
Merge [`configs/claude_desktop.json`](configs/claude_desktop.json) into it, restart Claude Desktop, then ask:
"Who should review a change to the vector index code in FalkorDB?"

## Claude Code

Project scope is already set up by `.mcp.json` at the repo root. For user scope:

```bash
.venv/Scripts/python mcp/print_config.py claude-code   # prints the exact command
```

## Cursor

Project scope: `.cursor/mcp.json` at the repo root (already there). Global: Cursor Settings → MCP → Add new global MCP server,
or edit `~/.cursor/mcp.json` with [`configs/cursor.json`](configs/cursor.json). Toggle the server on in the MCP list.

## Windsurf

Windsurf → Settings → Cascade → MCP Servers → View raw config, which opens `~/.codeium/windsurf/mcp_config.json`.
Paste [`configs/windsurf.json`](configs/windsurf.json) and click Refresh.

## Google Antigravity

Workspace scope: `.agents/mcp_config.json` at the repo root (already there) is picked up when you open the folder.
Global: Agent side panel → MCP Servers → Manage MCP Servers → View raw config, which opens `~/.gemini/config/mcp_config.json`.
Paste [`configs/antigravity.json`](configs/antigravity.json). Same `mcpServers` shape as Windsurf. In the Antigravity CLI,
type `/mcp` to open the MCP manager and confirm `handsonrepo` is listed.

## OpenAI Codex (CLI and IDE extension)

Either command:

```bash
codex mcp add handsonrepo --env BRAIN_DEFAULT_ORG=FalkorDB -- $(.venv/Scripts/python mcp/print_config.py claude-code | sed 's/.*-- //')
```

or add [`configs/codex.toml`](configs/codex.toml) to `~/.codex/config.toml`. Codex uses snake_case: `[mcp_servers.handsonrepo]`.
Project scope: `.codex/config.toml` at the repo root (already there; Codex asks you to trust the project first).

## VS Code (GitHub Copilot agent mode)

Project scope: `.vscode/mcp.json` at the repo root (already there). VS Code shows a Start button above the server entry.
User scope: Command Palette → "MCP: Open User Configuration" and paste [`configs/vscode.mcp.json`](configs/vscode.mcp.json).
Note VS Code uses `"servers"` and `"type": "stdio"`, not `mcpServers`.

## Gemini CLI

Add [`configs/gemini-cli.json`](configs/gemini-cli.json) to `~/.gemini/settings.json` (user) or `.gemini/settings.json` (project),
then run `gemini` and type `/mcp` to confirm.

## Cline / Roo Code (VS Code extensions)

Cline: MCP Servers icon → Configure → opens `cline_mcp_settings.json`. Paste [`configs/cline.json`](configs/cline.json).
Roo Code uses the same `mcpServers` shape in its MCP settings file.

## JetBrains (IntelliJ, PyCharm, WebStorm) AI Assistant

Settings → Tools → AI Assistant → Model Context Protocol (MCP) → Add → "As JSON", paste the `handsonrepo` block from
[`configs/claude_desktop.json`](configs/claude_desktop.json).

## Any other client

If the client can run a stdio MCP server, give it the command and args from the table above. If it only supports HTTP
servers, run the server behind an MCP stdio-to-HTTP bridge (for example `npx mcp-remote` in reverse, or the `mcp` package's
streamable HTTP transport by changing the last line of `server.py` to `mcp.run(transport="streamable-http")`).

## Troubleshooting

- **No tools / server fails to start**: run the verify command above; it prints the real error. Usual causes: FalkorDB not
  running, or the python path is wrong for your OS.
- **"no brain for X"**: that org is not ingested yet. `list_orgs` shows what is loaded.
- **Slow first call**: PageRank is computed on first use and cached for 10 minutes.
- **Secrets**: nothing sensitive is in these config files; the FalkorDB password stays in `backend/.env`.

## Added Oct 18

`decision_trail(ref)`: the full story of a PR in time order (motivating issue and its discussion, PR, reviews and comments,
who merged it, release, any revert). Comment threads are now in the graph as `Comment` nodes, so `search` also finds what people said.

`recent(kind="Action")` lists what the triage agent did; `recent(kind="Episode")` lists earlier Q&A from any agent. `remember` writes
into the same memory the web agent reads, so a decision you record from this client shows up in the web app's "Remembered from earlier".
