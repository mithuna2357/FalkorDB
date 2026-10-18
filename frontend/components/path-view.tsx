"use client"

import dynamic from "next/dynamic"
import { Maximize2, Minimize2, Minus, Network, ScanSearch } from "lucide-react"
import { useMemo } from "react"
import { Badge } from "@/components/ui/badge"
import { SUPERSEDES_COLOR, nodeColor } from "@/lib/graph-style"
import { cn } from "@/lib/utils"
import type { GraphPath } from "@/lib/types"

const GraphCanvas = dynamic(() => import("@/components/graph-canvas"), {
  ssr: false,
  loading: () => <div className="absolute inset-0 grid place-items-center text-xs text-muted-foreground">Loading graph…</div>,
})

export type PaneMode = "split" | "min" | "max"

export function PathView({
  path,
  question,
  mode,
  orientation,
  onMinimize,
  onMaximize,
  onRestore,
}: {
  path: GraphPath | null
  question: string | null
  mode: PaneMode
  orientation: "horizontal" | "vertical"
  onMinimize: () => void
  onMaximize: () => void
  onRestore: () => void
}) {
  const labels = useMemo(() => Array.from(new Set(path?.nodes.map((n) => n.label) ?? [])), [path])
  const hasSupersedes = !!path?.edges.some((e) => e.type === "SUPERSEDES")
  const empty = !path || path.nodes.length === 0

  // Minimised: a slim strip you click to bring the graph back.
  if (mode === "min") {
    const vertical = orientation === "horizontal"
    return (
      <button
        onClick={onRestore}
        title="Restore graph path"
        className={cn(
          "group flex h-full w-full items-center justify-center gap-2 rounded-xl border bg-card/40 text-xs text-muted-foreground transition-colors hover:border-[#48d55d]/50 hover:text-foreground",
          vertical && "flex-col",
        )}
      >
        <Network className="size-4 text-[#48d55d]" />
        <span className={cn("font-medium whitespace-nowrap", vertical && "[writing-mode:vertical-rl]")}>Graph path</span>
        {!empty && <span className="font-mono">{path.nodes.length}</span>}
      </button>
    )
  }

  return (
    <section className="flex h-full min-h-0 flex-col overflow-hidden rounded-xl border bg-card/40">
      <header className="flex shrink-0 items-start justify-between gap-3 border-b px-4 py-3">
        <div className="min-w-0">
          <h2 className="flex items-center gap-2 text-sm font-semibold">
            <Network className="size-4 text-[#48d55d]" /> Graph path
          </h2>
          <p className="mt-0.5 truncate text-xs text-muted-foreground">
            {question ? `For: ${question}` : "The path through the graph that produced an answer appears here."}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {!empty && (
            <div className="mr-1 hidden gap-1.5 text-xs text-muted-foreground sm:flex">
              <Badge variant="outline">{path.nodes.length} nodes</Badge>
              <Badge variant="outline">{path.edges.length} edges</Badge>
            </div>
          )}
          <WindowButton label="Minimise graph" onClick={onMinimize}>
            <Minus />
          </WindowButton>
          {mode === "max" ? (
            <WindowButton label="Restore split view" onClick={onRestore}>
              <Minimize2 />
            </WindowButton>
          ) : (
            <WindowButton label="Maximise graph" onClick={onMaximize}>
              <Maximize2 />
            </WindowButton>
          )}
        </div>
      </header>

      <div className="relative min-h-0 flex-1 overflow-hidden">
        {empty ? (
          <div className="absolute inset-0 grid place-items-center p-6 text-center text-sm text-muted-foreground">
            <div>
              <ScanSearch className="mx-auto mb-2 size-5 opacity-60" />
              {path ? "This answer did not return a path." : "Ask a question to see its path."}
            </div>
          </div>
        ) : (
          <GraphCanvas path={path} />
        )}
      </div>

      {!empty && (
        <footer className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1.5 border-t px-4 py-2.5 text-xs">
          {labels.map((label) => (
            <span key={label} className="flex items-center gap-1.5 text-muted-foreground">
              <span className="size-2.5 rounded-full ring-1 ring-[#9be9a8]/50" style={{ background: nodeColor(label) }} />
              {label}
            </span>
          ))}
          {hasSupersedes && (
            <span className="flex items-center gap-1.5 font-medium" style={{ color: SUPERSEDES_COLOR }}>
              <span className="h-0.5 w-4 rounded" style={{ background: SUPERSEDES_COLOR }} />
              SUPERSEDES
            </span>
          )}
        </footer>
      )}
    </section>
  )
}

function WindowButton({ label, onClick, children }: { label: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      aria-label={label}
      title={label}
      className="grid size-7 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground [&_svg]:size-3.5"
    >
      {children}
    </button>
  )
}
