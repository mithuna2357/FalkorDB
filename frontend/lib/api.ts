import askFixture from "@/fixtures/ask.json"
import orgsFixture from "@/fixtures/orgs.json"
import syncFixture from "@/fixtures/sync.json"
import type { AskResponse, OrgSummary, SyncResponse } from "@/lib/types"

// "fixtures" until BACKEND_URL is set (see next.config.ts). Requests go to same-origin /api/*,
// which Next rewrites to the FastAPI backend, so no CORS setup is needed.
export const DATA_MODE: "live" | "fixtures" =
  process.env.HOR_DATA_MODE === "live" ? "live" : "fixtures"

async function request<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json" },
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

export async function sync(org: string): Promise<SyncResponse> {
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
  return request<SyncResponse>("/api/sync", { org })
}
