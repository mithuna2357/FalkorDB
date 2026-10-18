// One GitHub-contribution-style green per node label. Red is reserved for SUPERSEDES edges.
export const NODE_COLORS: Record<string, string> = {
  Person: "#39d353",
  Repo: "#9be9a8",
  Issue: "#26a641",
  PR: "#7ee787",
  Commit: "#006d32",
  File: "#40c463",
  Release: "#aff5b4",
  Doc: "#2ea043",
  Decision: "#56d364",
  Episode: "#0e4429",
}

export const FALLBACK_NODE_COLOR = "#216e39"
export const NODE_STROKE = "rgba(155, 233, 168, 0.55)"
export const SUPERSEDES_COLOR = "#ef4444"
export const EDGE_COLOR = "rgba(148, 163, 184, 0.55)"

export const nodeColor = (label: string) => NODE_COLORS[label] ?? FALLBACK_NODE_COLOR
