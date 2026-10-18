"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Minus, Plus } from "lucide-react"
import ForceGraph2D, { type ForceGraphMethods, type LinkObject, type NodeObject } from "react-force-graph-2d"
import { EDGE_COLOR, NODE_STROKE, SUPERSEDES_COLOR, nodeColor } from "@/lib/graph-style"
import type { GraphPath } from "@/lib/types"

type GNode = NodeObject<{ id: string; label: string; title: string }>
type GLink = LinkObject<GNode, { type: string }>

const NODE_R = 6

export default function GraphCanvas({ path }: { path: GraphPath }) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const fgRef = useRef<ForceGraphMethods<GNode, GLink> | undefined>(undefined)
  const [size, setSize] = useState({ width: 0, height: 0 })
  const [hoverId, setHoverId] = useState<string | null>(null)

  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const ro = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect
      setSize({ width: Math.floor(width), height: Math.floor(height) })
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // force-graph mutates its input, so hand it fresh copies. Drop edges that point at unknown nodes.
  const data = useMemo(() => {
    const ids = new Set(path.nodes.map((n) => n.id))
    return {
      nodes: path.nodes.map((n) => ({ ...n })) as GNode[],
      links: path.edges
        .filter((e) => ids.has(e.from) && ids.has(e.to))
        .map((e) => ({ source: e.from, target: e.to, type: e.type })) as GLink[],
    }
  }, [path])

  // Keep the whole path in view: refit when the layout settles, on new data and on resize,
  // unless the user has panned/zoomed themselves.
  const userMoved = useRef(false)
  const fit = useCallback(
    (ms = 400) => {
      // zoomToFit before the first simulation tick sees undefined positions and leaves a broken transform.
      const ready = data.nodes.length > 0 && data.nodes.every((n) => Number.isFinite(n.x) && Number.isFinite(n.y))
      if (ready) fgRef.current?.zoomToFit(ms, 48)
    },
    [data],
  )
  useEffect(() => {
    userMoved.current = false
    const t = setTimeout(() => fit(300), 700)
    return () => clearTimeout(t)
  }, [fit])
  useEffect(() => {
    if (!userMoved.current) fit(0)
  }, [size, fit])

  const zoomBy = (factor: number) => {
    const fg = fgRef.current
    if (!fg) return
    userMoved.current = true
    fg.zoom(fg.zoom() * factor, 300)
  }

  const isSupersedes = (l: GLink) => l.type === "SUPERSEDES"

  return (
    // data-lenis-prevent + overscroll-none: the wheel only zooms the graph, it never scrolls anything.
    <div
      ref={wrapRef}
      data-lenis-prevent
      className="absolute inset-0 touch-none overscroll-none"
      onWheel={() => {
        userMoved.current = true
      }}
    >
      {size.width > 0 && size.height > 0 && (
        <ForceGraph2D<GNode, GLink>
          ref={fgRef}
          graphData={data}
          width={size.width}
          height={size.height}
          backgroundColor="rgba(0,0,0,0)"
          cooldownTicks={120}
          onEngineStop={() => {
            if (!userMoved.current) fit()
          }}
          onBackgroundClick={() => {
            userMoved.current = false
            fit()
          }}
          onNodeDragEnd={() => {
            userMoved.current = true
          }}
          nodeRelSize={NODE_R}
          nodeLabel={(n) => `${n.label}: ${n.title}`}
          onNodeHover={(n) => setHoverId(n ? String(n.id) : null)}
          nodeCanvasObject={(node, ctx, scale) => {
            const x = node.x ?? 0
            const y = node.y ?? 0
            const color = nodeColor(node.label)
            const hovered = hoverId === node.id

            ctx.beginPath()
            ctx.arc(x, y, hovered ? NODE_R + 1.5 : NODE_R, 0, 2 * Math.PI)
            ctx.fillStyle = color
            ctx.fill()
            ctx.lineWidth = 1.5 / scale
            ctx.strokeStyle = NODE_STROKE
            ctx.stroke()

            const fontSize = Math.max(10 / scale, 2.5)
            ctx.font = `${fontSize}px ui-sans-serif, system-ui, sans-serif`
            ctx.textAlign = "center"
            ctx.textBaseline = "top"
            ctx.fillStyle = "rgba(237,237,237,0.95)"
            ctx.fillText(node.title, x, y + NODE_R + 2)
            ctx.font = `${fontSize * 0.8}px ui-monospace, monospace`
            ctx.fillStyle = "rgba(155,233,168,0.85)"
            ctx.fillText(node.label, x, y + NODE_R + 2 + fontSize * 1.15)
          }}
          nodePointerAreaPaint={(node, color, ctx) => {
            ctx.fillStyle = color
            ctx.beginPath()
            ctx.arc(node.x ?? 0, node.y ?? 0, NODE_R + 3, 0, 2 * Math.PI)
            ctx.fill()
          }}
          linkColor={(l) => (isSupersedes(l) ? SUPERSEDES_COLOR : EDGE_COLOR)}
          linkWidth={(l) => (isSupersedes(l) ? 2.5 : 1.2)}
          linkLineDash={(l) => (isSupersedes(l) ? [4, 2] : null)}
          linkDirectionalArrowLength={5}
          linkDirectionalArrowRelPos={0.92}
          linkDirectionalArrowColor={(l) => (isSupersedes(l) ? SUPERSEDES_COLOR : "rgba(203,213,225,0.8)")}
          linkCanvasObjectMode={() => "after"}
          linkCanvasObject={(link, ctx, scale) => {
            const s = link.source as GNode
            const t = link.target as GNode
            if (typeof s !== "object" || typeof t !== "object") return
            const mx = ((s.x ?? 0) + (t.x ?? 0)) / 2
            const my = ((s.y ?? 0) + (t.y ?? 0)) / 2
            const fontSize = Math.max(8 / scale, 2)
            ctx.font = `${fontSize}px ui-monospace, monospace`
            const w = ctx.measureText(link.type).width
            ctx.fillStyle = "rgba(10,10,10,0.85)"
            ctx.fillRect(mx - w / 2 - 2, my - fontSize / 2 - 1, w + 4, fontSize + 2)
            ctx.textAlign = "center"
            ctx.textBaseline = "middle"
            ctx.fillStyle = isSupersedes(link) ? SUPERSEDES_COLOR : "rgba(203,213,225,0.9)"
            ctx.fillText(link.type, mx, my)
          }}
        />
      )}
      <div className="absolute right-3 bottom-3 flex overflow-hidden rounded-lg border bg-background/80 text-muted-foreground backdrop-blur">
        <button onClick={() => zoomBy(1.35)} className="p-1.5 transition-colors hover:bg-secondary hover:text-foreground" aria-label="Zoom in">
          <Plus className="size-3.5" />
        </button>
        <button onClick={() => zoomBy(1 / 1.35)} className="border-x p-1.5 transition-colors hover:bg-secondary hover:text-foreground" aria-label="Zoom out">
          <Minus className="size-3.5" />
        </button>
        <button
          onClick={() => {
            userMoved.current = false
            fit()
          }}
          className="px-2 text-xs transition-colors hover:bg-secondary hover:text-foreground"
        >
          Fit
        </button>
      </div>
    </div>
  )
}
