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
  // supporting detail: muted so they do not compete with people and tickets
  Comment: "#2dd4bf",
  Action: "#f59e0b",
  Fact: "#a3e635",
}

export const FALLBACK_NODE_COLOR = "#216e39"
export const NODE_STROKE = "rgba(155, 233, 168, 0.55)"
export const SUPERSEDES_COLOR = "#ef4444"
export const EDGE_COLOR = "rgba(148, 163, 184, 0.55)"

export const nodeColor = (label: string) => NODE_COLORS[label] ?? FALLBACK_NODE_COLOR

// Relationships FalkorDB derives after ingestion (drawn dashed), as opposed to ones read straight from GitHub (solid).
export const DERIVED_EDGES = new Set(["TOUCHES", "REVIEWED_FOR", "COLLABORATES", "STRONG_COLLAB", "CO_CHANGED", "CONTRIBUTES", "HAS_FACT", "ABOUT", "CITES", "SUGGESTS"])
export const DERIVED_EDGE_COLOR = "rgba(125, 211, 252, 0.55)"
export const isDerivedEdge = (type: string) => DERIVED_EDGES.has(type)
