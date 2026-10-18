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
  label: string // Person, Repo, Issue, PR, Commit, File, Release, Doc, Comment, Action, Fact, Episode
  title: string
  url?: string // GitHub link when the node has one
  key?: string
  weight?: number // overview graphs: commits for people, used for node size
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

export type MemoryItem = {
  episode_id?: string
  question: string
  answer_excerpt?: string
  at?: string
  agent?: string // chat | triage | mcp
  score?: number
}

export type Usage = {
  prompt_tokens?: number
  completion_tokens?: number
  model?: string
}

export type AskResponse = {
  answer: string
  sources: Source[]
  path: GraphPath
  cypher: string[]
  toolCalls: ToolCall[]
  memory?: MemoryItem[]
  usage?: Usage
}

export type SyncResponse = {
  added: number
  updated: number
  superseded: number
}
