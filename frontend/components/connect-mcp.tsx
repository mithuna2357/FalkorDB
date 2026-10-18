"use client"

import { Check, Copy, ExternalLink, X } from "lucide-react"
import { AnimatePresence, motion } from "motion/react"
import { useEffect, useState } from "react"
import { createPortal } from "react-dom"
import { McpMark } from "@/components/mcp-mark"
import { Button } from "@/components/ui/button"
import { REPO_URL } from "@/lib/site"
import { cn } from "@/lib/utils"

const EASE = [0.22, 1, 0.36, 1] as const

// One stdio command for every client. Paths are the local checkout of the repo.
const DEFAULT_PATH = { win: "C:\\Users\\you\\HandsOnRepo", nix: "/Users/you/HandsOnRepo" }

type Cmd = { command: string; args: string[] }
// Build the one stdio command every client uses from where the repo is cloned.
function cmdFor(os: "win" | "nix", repoPath: string): Cmd {
  const base = repoPath.trim().replace(/[\\/]+$/, "") || DEFAULT_PATH[os]
  return os === "win"
    ? { command: `${base}\\.venv\\Scripts\\python.exe`, args: [`${base}\\mcp\\server.py`] }
    : { command: `${base}/.venv/bin/python`, args: [`${base}/mcp/server.py`] }
}

// JSON.stringify escapes each backslash once, which is exactly what a JSON config file needs (D:\\path).
const std = (c: Cmd) =>
  JSON.stringify({ mcpServers: { "handsonrepo": { command: c.command, args: c.args, env: { BRAIN_DEFAULT_ORG: "FalkorDB" } } } }, null, 2)
const vscode = (c: Cmd) =>
  JSON.stringify({ servers: { "handsonrepo": { type: "stdio", command: c.command, args: c.args, env: { BRAIN_DEFAULT_ORG: "FalkorDB" } } } }, null, 2)
// TOML literal strings (single quotes) take Windows backslashes as-is.
const codex = (c: Cmd) =>
  `[mcp_servers.handsonrepo]\ncommand = '${c.command}'\nargs = ['${c.args[0]}']\nstartup_timeout_sec = 30\n\n[mcp_servers.handsonrepo.env]\nBRAIN_DEFAULT_ORG = "FalkorDB"`

type Client = { id: string; name: string; where: string; steps: string[]; snippet: (c: Cmd) => string; lang: string }

const CLIENTS: Client[] = [
  { id: "claude", name: "Claude Desktop", where: "%APPDATA%\\Claude\\claude_desktop_config.json (Windows) · ~/Library/Application Support/Claude/claude_desktop_config.json (macOS)", lang: "json",
    steps: ["Merge the JSON below into the config file.", "Restart Claude Desktop.", "Ask: “Who should review a change to the vector index code in FalkorDB?”"], snippet: std },
  { id: "claude-code", name: "Claude Code", where: "Project scope is automatic: .mcp.json at the repo root. User scope:", lang: "bash",
    steps: ["Run the command below once.", "Type /mcp in Claude Code to confirm handsonrepo is listed."],
    snippet: (c) => `claude mcp add handsonrepo -s user -e BRAIN_DEFAULT_ORG=FalkorDB -- ${c.command} ${c.args[0]}` },
  { id: "cursor", name: "Cursor", where: "~/.cursor/mcp.json (global) or .cursor/mcp.json in the repo (already there)", lang: "json",
    steps: ["Cursor Settings → MCP → Add new global MCP server, or paste the JSON into the file.", "Toggle handsonrepo on in the MCP list."], snippet: std },
  { id: "windsurf", name: "Windsurf", where: "~/.codeium/windsurf/mcp_config.json", lang: "json",
    steps: ["Settings → Cascade → MCP Servers → View raw config.", "Paste the JSON and click Refresh."], snippet: std },
  { id: "antigravity", name: "Google Antigravity", where: "~/.gemini/config/mcp_config.json (global) or .agents/mcp_config.json in the repo (already there)", lang: "json",
    steps: ["Agent side panel → MCP Servers → Manage MCP Servers → View raw config.", "Paste the JSON. In the Antigravity CLI type /mcp to confirm."], snippet: std },
  { id: "codex", name: "OpenAI Codex", where: "~/.codex/config.toml (user) or .codex/config.toml in the repo (already there)", lang: "toml",
    steps: ["Add the TOML below, or run: codex mcp add handsonrepo -- <command> <args>.", "Codex asks you to trust the project the first time."], snippet: codex },
  { id: "vscode", name: "VS Code Copilot", where: ".vscode/mcp.json in the repo (already there) or “MCP: Open User Configuration”", lang: "json",
    steps: ["VS Code shows a Start button above the server entry.", "Use agent mode and ask about the org."], snippet: vscode },
  { id: "gemini", name: "Gemini CLI", where: "~/.gemini/settings.json (user) or .gemini/settings.json (project)", lang: "json",
    steps: ["Add the JSON under mcpServers.", "Run gemini and type /mcp to confirm."], snippet: std },
  { id: "cline", name: "Cline / Roo Code", where: "cline_mcp_settings.json (MCP Servers icon → Configure)", lang: "json",
    steps: ["Paste the JSON.", "Same shape works for Roo Code."], snippet: std },
]

const TOOLS = ["list_orgs", "repo_overview", "who_knows", "top_reviewers", "decision_trail", "reverts", "recent", "impact_of_leaving", "teams", "bus_factor", "clusters", "owner_history", "node_context", "search", "find_path", "recall", "remember", "run_cypher"]

export function ConnectMcpButton() {
  const [open, setOpen] = useState(false)
  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)} title="Use this brain from Claude, Codex, Cursor, Antigravity and more">
        <McpMark /> Connect MCP
      </Button>
      <ConnectMcpDialog open={open} onClose={() => setOpen(false)} />
    </>
  )
}

export function ConnectMcpDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [client, setClient] = useState<Client>(CLIENTS[0])
  const [os, setOs] = useState<"win" | "nix">("win")
  const [repoPath, setRepoPath] = useState("")
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose()
    window.addEventListener("keydown", onKey)
    const prev = document.body.style.overflow
    document.body.style.overflow = "hidden"
    return () => {
      window.removeEventListener("keydown", onKey)
      document.body.style.overflow = prev
    }
  }, [open, onClose])

  const cmd = cmdFor(os, repoPath)
  const snippet = client.snippet(cmd)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(snippet)
      setCopied(true)
      setTimeout(() => setCopied(false), 1200)
    } catch {}
  }

  const [mounted, setMounted] = useState(false)
  useEffect(() => setMounted(true), [])
  if (!mounted) return null

  return createPortal(
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4 backdrop-blur-sm"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={onClose}
          role="dialog"
          aria-modal
          aria-label="Connect MCP"
        >
          <motion.div
            className="flex h-[min(90dvh,860px)] w-full max-w-4xl flex-col overflow-hidden rounded-2xl border bg-background shadow-2xl"
            initial={{ opacity: 0, y: 16, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 10, scale: 0.98 }}
            transition={{ duration: 0.3, ease: EASE }}
            onClick={(e) => e.stopPropagation()}
          >
            <header className="flex items-start justify-between gap-4 border-b px-5 py-4">
              <div>
                <h2 className="flex items-center gap-2 text-base font-semibold">
                  <McpMark className="size-5 text-[#48d55d]" /> Connect this brain to your AI tools
                </h2>
                <p className="mt-1 text-sm text-muted-foreground">
                  HandsOnRepo ships an MCP server with the same {TOOLS.length} graph tools the chat uses. Any MCP client can ask the org brain
                  directly: Claude, Codex, Cursor, Antigravity, Windsurf, VS Code, Gemini CLI, Cline. One command, every client.
                </p>
              </div>
              <button onClick={onClose} className="rounded-md p-1 text-muted-foreground hover:bg-secondary hover:text-foreground" aria-label="Close">
                <X className="size-4" />
              </button>
            </header>

            <div className="grid min-h-0 flex-1 grid-cols-1 grid-rows-[auto_minmax(0,1fr)] overflow-hidden md:grid-cols-[220px_1fr] md:grid-rows-[minmax(0,1fr)]">
              <nav data-lenis-prevent className="flex min-h-0 gap-1 overflow-x-auto border-b p-2 md:flex-col md:overflow-y-auto md:border-r md:border-b-0" aria-label="Clients">
                {CLIENTS.map((c) => (
                  <button
                    key={c.id}
                    onClick={() => setClient(c)}
                    className={cn(
                      "shrink-0 rounded-lg px-3 py-2 text-left text-sm transition-colors hover:bg-secondary",
                      client.id === c.id && "bg-secondary font-medium text-[#48d55d]",
                    )}
                  >
                    {c.name}
                  </button>
                ))}
              </nav>

              <div data-lenis-prevent className="min-h-0 space-y-4 overflow-y-auto overscroll-contain p-5">
                <section className="rounded-xl border bg-card/60 p-4 text-sm">
                  <h3 className="mb-1 font-medium">Before you start (once)</h3>
                  <ol className="list-decimal space-y-1 pl-5 text-muted-foreground">
                    <li>
                      Clone the repo and install:{" "}
                      <code className="rounded bg-black/40 px-1 py-0.5 font-mono text-xs text-[#9be9a8]">python -m venv .venv && .venv/bin/pip install -r backend/requirements.txt mcp</code>
                    </li>
                    <li>Run FalkorDB (Docker on 6379 or FalkorDB Cloud credentials in backend/.env) and build at least one brain from this app.</li>
                    <li>Pick your tool on the left and follow its steps. Set BRAIN_DEFAULT_ORG so tool calls can omit the org.</li>
                  </ol>
                </section>

                <section className="rounded-xl border bg-card/60 p-4 text-sm">
                  <label htmlFor="mcp-repo-path" className="mb-1 block font-medium">
                    Where did you clone HandsOnRepo?
                  </label>
                  <p className="mb-2 text-xs text-muted-foreground">
                    The snippets below are generated from this path. Example: <span className="font-mono">{DEFAULT_PATH[os]}</span>
                  </p>
                  <input
                    id="mcp-repo-path"
                    value={repoPath}
                    onChange={(e) => setRepoPath(e.target.value)}
                    placeholder={DEFAULT_PATH[os]}
                    spellCheck={false}
                    className="w-full rounded-lg border bg-background px-3 py-2 font-mono text-xs outline-none focus:border-[#48d55d]/60"
                  />
                </section>

                <section>
                  <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                    <h3 className="font-medium">{client.name}</h3>
                    <div className="flex items-center gap-1 rounded-lg border p-0.5 text-xs">
                      {(["win", "nix"] as const).map((o) => (
                        <button key={o} onClick={() => setOs(o)} className={cn("rounded-md px-2 py-1", os === o ? "bg-secondary text-foreground" : "text-muted-foreground")}>
                          {o === "win" ? "Windows" : "macOS / Linux"}
                        </button>
                      ))}
                    </div>
                  </div>
                  <p className="mb-2 font-mono text-xs text-muted-foreground">{client.where}</p>
                  <div className="group relative">
                    <pre className="overflow-x-auto rounded-lg border bg-black/40 p-3 pr-10 font-mono text-xs leading-relaxed text-[#9be9a8]">
                      <code>{snippet}</code>
                    </pre>
                    <button onClick={copy} className="absolute top-2 right-2 rounded p-1 text-muted-foreground hover:bg-secondary hover:text-foreground" aria-label="Copy">
                      {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
                    </button>
                  </div>
                  <ol className="mt-3 list-decimal space-y-1 pl-5 text-sm text-muted-foreground">
                    {client.steps.map((s) => (
                      <li key={s}>{s}</li>
                    ))}
                  </ol>
                </section>

                <section className="text-sm">
                  <h3 className="mb-1 font-medium">What you get</h3>
                  <div className="flex flex-wrap gap-1.5">
                    {TOOLS.map((t) => (
                      <span key={t} className="rounded-full border px-2 py-0.5 font-mono text-[11px] text-muted-foreground">
                        {t}
                      </span>
                    ))}
                  </div>
                  <p className="mt-2 text-muted-foreground">
                    Every tool returns the result plus its sources and the Cypher that ran. <code className="font-mono text-xs">remember</code> writes
                    into the same memory the web agent reads, so a decision recorded in Cursor shows up here as “Remembered from earlier”.
                  </p>
                </section>

                <footer className="flex flex-wrap items-center gap-3 border-t pt-3 text-sm">
                  <a href={`${REPO_URL}/blob/main/mcp/README.md`} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 text-[#48d55d] hover:underline">
                    Full MCP guide <ExternalLink className="size-3.5" />
                  </a>
                  <a href={REPO_URL} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 text-muted-foreground hover:text-foreground hover:underline">
                    Source on GitHub <ExternalLink className="size-3.5" />
                  </a>
                  <span className="ml-auto text-xs text-muted-foreground">Open source · FalkorDB inside</span>
                </footer>
              </div>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  )
}
