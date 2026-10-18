// Shapes of the HandsOnRepo backend API contract (GET /api/orgs, POST /api/ask, POST /api/sync).

export type OrgSummary = {
  org: string
  nodes: number
  edges: number
  lastSync: string
}

export type Source = {
  type: string // "PR" | "Issue" | "Commit" | "Person" | ...
  id: string
  title: string
  url: string
}

export type PathNode = {
  id: string
  label: string // Person, Repo, Issue, PR, Commit, File, Release, Doc, Decision, Episode
  title: string
}

export type PathEdge = {
  from: string
  to: string
  type: string // AUTHORED, REVIEWED, TOUCHED, CLOSES, REVERTS, MENTIONS, ... SUPERSEDES
}

export type GraphPath = {
  nodes: PathNode[]
  edges: PathEdge[]
}

export type ToolCall = {
  tool: string
  args: Record<string, unknown>
  ms: number
}

export type AskResponse = {
  answer: string
  sources: Source[]
  path: GraphPath
  cypher: string[]
  toolCalls: ToolCall[]
}

export type SyncResponse = {
  added: number
  updated: number
  superseded: number
}
