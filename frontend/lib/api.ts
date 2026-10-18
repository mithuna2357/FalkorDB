import askFixture from "@/fixtures/ask.json"
import orgsFixture from "@/fixtures/orgs.json"
import syncFixture from "@/fixtures/sync.json"
import { authHeaders } from "@/lib/auth"
import type { AskResponse, GraphPath, OrgSummary, SyncResponse } from "@/lib/types"

// "fixtures" until BACKEND_URL is set (see next.config.ts). Requests go to same-origin /api/*,
// which Next rewrites to the FastAPI backend, so no CORS setup is needed.
export const DATA_MODE: "live" | "fixtures" =
  process.env.HOR_DATA_MODE === "live" ? "live" : "fixtures"

async function request<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  })
  if (!res.ok) {
    let detail = ""
    try {
      detail = (await res.text()).slice(0, 200)
    } catch {}
    throw new Error(`${res.status} ${res.statusText}${detail ? ` — ${detail}` : ""}`)
  }
  return res.json() as Promise<T>
}

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms))

// Live ingestion log for the "Build brain" progress card (fed by the status polling in sync()).
type BuildLogListener = () => void
let buildLog: string[] = []
const buildLogListeners = new Set<BuildLogListener>()
function setBuildLog(lines: string[]) {
  buildLog = lines
  buildLogListeners.forEach((l) => l())
}
export function subscribeBuildLog(l: BuildLogListener) {
  buildLogListeners.add(l)
  return () => {
    buildLogListeners.delete(l)
  }
}
export const getBuildLog = () => buildLog

// In-memory org list so "Build brain" and "Sync now" behave plausibly in fixture mode.
let fixtureOrgs: OrgSummary[] = orgsFixture.map((o) => ({ ...o }))

export async function getOrgs(): Promise<OrgSummary[]> {
  if (DATA_MODE === "fixtures") {
    await wait(250)
    return fixtureOrgs.map((o) => ({ ...o }))
  }
  return request<OrgSummary[]>("/api/orgs")
}

export async function ask(org: string, question: string): Promise<AskResponse> {
  if (DATA_MODE === "fixtures") {
    await wait(900)
    return structuredClone(askFixture) as AskResponse
  }
  return request<AskResponse>("/api/ask", { org, question })
}

export async function sync(org: string, repos?: string[]): Promise<SyncResponse> {
  if (DATA_MODE === "fixtures") {
    await wait(1500)
    const s = syncFixture as SyncResponse
    const now = new Date().toISOString()
    const existing = fixtureOrgs.find((o) => o.org.toLowerCase() === org.toLowerCase())
    if (existing) {
      fixtureOrgs = fixtureOrgs.map((o) =>
        o === existing ? { ...o, nodes: o.nodes + s.added, edges: o.edges + s.added + s.superseded, lastSync: now } : o,
      )
    } else {
      fixtureOrgs = [...fixtureOrgs, { org, nodes: s.added, edges: s.added + s.updated, lastSync: now }]
    }
    return { ...s }
  }
  const first = await request<SyncResponse & { status?: string }>("/api/sync", repos?.length ? { org, repos } : { org })
  if (first.status !== "building") return first
  // New org: the backend started a full ingestion. Poll its status until the brain is ready (a few minutes).
  setBuildLog([`starting ingestion for ${repos?.length ? repos.join(", ") : org}`])
  try {
    for (;;) {
      await wait(2500)
      const st = await request<{ state: string; error?: string; log?: string[]; summary?: { nodes?: number; edges?: number } | null }>(
        `/api/ingest/${encodeURIComponent(org)}/status`,
      )
      if (st.log?.length) setBuildLog(st.log.slice(-6))
      if (st.state === "done") {
        return { added: st.summary?.nodes ?? 0, updated: 0, superseded: 0 }
      }
      if (st.state === "error") throw new Error(st.error ?? "ingestion failed")
    }
  } finally {
    setBuildLog([])
  }
}


/** Overview graph for an org (repos, most active people, who contributes where). Shown before any question is asked. */
export async function getOrgMap(org: string): Promise<GraphPath> {
  if (DATA_MODE === "fixtures") {
    await wait(300)
    return structuredClone((askFixture as AskResponse).path)
  }
  const res = await request<{ path: GraphPath }>(`/api/graph/${encodeURIComponent(org)}/map`)
  return res.path
}


/** Example questions generated from this org's own graph (busiest area, latest revert, top contributor...). */
export async function getSuggestions(org: string): Promise<string[]> {
  if (DATA_MODE === "fixtures") {
    await wait(200)
    return []
  }
  const res = await request<{ questions: string[] }>(`/api/graph/${encodeURIComponent(org)}/suggest`)
  return res.questions ?? []
}
