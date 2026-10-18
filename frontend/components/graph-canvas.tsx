"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Minus, Plus } from "lucide-react"
import ForceGraph2D, { type ForceGraphMethods, type LinkObject, type NodeObject } from "react-force-graph-2d"
import { DERIVED_EDGE_COLOR, EDGE_COLOR, NODE_STROKE, SUPERSEDES_COLOR, isDerivedEdge, nodeColor } from "@/lib/graph-style"
import type { GraphPath } from "@/lib/types"

type GNode = NodeObject<{ id: string; label: string; title: string; url?: string; weight?: number }>
type GLink = LinkObject<GNode, { type: string }>

// Visual weight per label: people and tickets are the story, comments / commits / files are supporting detail.
const RADIUS: Record<string, number> = { Person: 7, PR: 6, Issue: 6, Release: 5.5, Repo: 6.5, Fact: 5, Action: 5.5, Episode: 5 }
const DETAIL_LABELS = new Set(["Comment", "Commit", "File", "Doc"])
const LABEL_ZOOM = 2.1 // detail labels and edge labels appear from this zoom level (zoom in deliberately)
const MAX_CHARS = 26

const radiusOf = (label: string) => RADIUS[label] ?? 4.2

// Short, readable caption. Comments and commits carry long bodies; keep a glimpse and let hover show the rest.
function caption(n: GNode): string {
  let t = n.title ?? ""
  if (n.label === "Comment") t = t.replace(/^(comment|review):\s*/i, "“") + "”"
  if (n.label === "Commit") t = t.replace(/^[0-9a-f]{7}\s+/i, "")
  if (n.label === "File") t = t.split("/").slice(-2).join("/")
  if (n.label === "Action") t = t.replace(/^\w+ action:\s*/i, "")
  if (n.label === "PR" || n.label === "Issue") {
    const m = t.match(/^(#\d+)\s+(.*)$/)
    if (m) t = `${m[1]} ${m[2]}`
  }
  t = t.replace(/\s+/g, " ").trim()
  return t.length > MAX_CHARS ? t.slice(0, MAX_CHARS - 1).trimEnd() + "…" : t
}

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

  // Neighbourhood of the hovered node: everything else is dimmed while hovering.
  const neighbours = useMemo(() => {
    const map = new Map<string, Set<string>>()
    for (const l of data.links) {
      const s = typeof l.source === "object" ? String(l.source.id) : String(l.source)
      const t = typeof l.target === "object" ? String(l.target.id) : String(l.target)
      if (!map.has(s)) map.set(s, new Set())
      if (!map.has(t)) map.set(t, new Set())
      map.get(s)!.add(t)
      map.get(t)!.add(s)
    }
    return map
  }, [data])
  const degree = useCallback((id: string) => neighbours.get(id)?.size ?? 0, [neighbours])

  // Keep the whole path in view: refit when the layout settles, on new data and on resize,
  // unless the user has panned/zoomed themselves.
  const userMoved = useRef(false)
  const fit = useCallback(
    (ms = 400) => {
      const ready = data.nodes.length > 0 && data.nodes.every((n) => Number.isFinite(n.x) && Number.isFinite(n.y))
      if (ready) fgRef.current?.zoomToFit(ms, 56)
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

  // More room between nodes than the defaults: labels need it. Runs again once the canvas has a size,
  // because the graph instance only exists after the first measured render.
  useEffect(() => {
    const fg = fgRef.current
    if (!fg || size.width === 0) return
    const n = data.nodes.length
    // scale repulsion with size: small answer paths stay tight, the org overview gets room
    fg.d3Force("charge")?.strength(n > 60 ? -900 : n > 24 ? -480 : -220)
    fg.d3Force("link")?.distance((l: GLink) => {
      if (l.type === "CONTRIBUTES") return 150
      if (l.type === "COLLABORATES") return 190
      return l.type === "ON" || l.type === "TOUCHED" ? 42 : 70
    })
    fg.d3ReheatSimulation()
    // The layout keeps moving for a moment after the reheat; refit a few times unless the user has taken over.
    const timers = [500, 1200, 2500, 4000].map((ms) => setTimeout(() => { if (!userMoved.current) fit(250) }, ms))
    return () => timers.forEach(clearTimeout)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, size.width, size.height])


  const zoomBy = (factor: number) => {
    const fg = fgRef.current
    if (!fg) return
    userMoved.current = true
    fg.zoom(fg.zoom() * factor, 300)
  }

  const isSupersedes = (l: GLink) => l.type === "SUPERSEDES"
  const linkEnd = (v: GLink["source"]) => (typeof v === "object" && v ? String(v.id) : String(v))
  const touchesHover = (l: GLink) => !!hoverId && (linkEnd(l.source) === hoverId || linkEnd(l.target) === hoverId)
  const dimmed = (id: string) => !!hoverId && id !== hoverId && !neighbours.get(hoverId)?.has(id)

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
          cooldownTicks={140}
          d3VelocityDecay={0.3}
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
          onNodeClick={(n) => {
            if (n.url) window.open(n.url, "_blank", "noopener")
          }}
          nodeRelSize={6}
          nodeLabel={(n) => `<div style="max-width:320px;white-space:normal"><b>${n.label}</b> · ${n.title}${n.url ? "<br/><span style='opacity:.7'>click to open on GitHub</span>" : ""}</div>`}
          onNodeHover={(n) => setHoverId(n ? String(n.id) : null)}
          nodeCanvasObject={(node, ctx, scale) => {
            const id = String(node.id)
            const x = node.x ?? 0
            const y = node.y ?? 0
            const w = typeof node.weight === "number" && node.weight > 0 ? Math.min(3, Math.log10(node.weight + 1) * 1.1) : 0
            const r = radiusOf(node.label) + (w || Math.min(2, degree(id) * 0.25))
            const hovered = hoverId === id
            const faded = dimmed(id)

            ctx.globalAlpha = faded ? 0.18 : 1
            ctx.beginPath()
            ctx.arc(x, y, hovered ? r + 1.5 : r, 0, 2 * Math.PI)
            ctx.fillStyle = nodeColor(node.label)
            ctx.fill()
            ctx.lineWidth = 1.2 / scale
            ctx.strokeStyle = hovered ? "rgba(255,255,255,0.9)" : NODE_STROKE
            ctx.stroke()

            // Captions: story nodes always; detail nodes only when zoomed in, hovered, or next to the hovered node.
            const isDetail = DETAIL_LABELS.has(node.label)
            const nearHover = !!hoverId && neighbours.get(hoverId)?.has(id)
            // Small paths can afford every caption; big ones only caption the story nodes until you zoom or hover.
            const small = data.nodes.length <= 40
            const showCaption = !faded && (hovered || nearHover || !isDetail || small || scale >= LABEL_ZOOM)
            if (showCaption) {
              const fontSize = Math.max((isDetail ? 9 : 10.5) / scale, 2.2)
              const text = hovered ? (node.title ?? "").replace(/\s+/g, " ").slice(0, 90) : caption(node)
              ctx.font = `${hovered ? 600 : 500} ${fontSize}px ui-sans-serif, system-ui, sans-serif`
              ctx.textAlign = "center"
              ctx.textBaseline = "top"
              const w = ctx.measureText(text).width
              const pad = 3 / scale
              const ty = y + r + 3 / scale
              ctx.fillStyle = hovered ? "rgba(12,12,12,0.95)" : "rgba(10,10,10,0.72)"
              ctx.beginPath()
              ctx.roundRect(x - w / 2 - pad, ty - pad / 2, w + pad * 2, fontSize + pad, 3 / scale)
              ctx.fill()
              ctx.fillStyle = isDetail && !hovered ? "rgba(200,200,200,0.85)" : "rgba(240,240,240,0.97)"
              ctx.fillText(text, x, ty)
            }
            ctx.globalAlpha = 1
          }}
          nodePointerAreaPaint={(node, color, ctx) => {
            ctx.fillStyle = color
            ctx.beginPath()
            const w = typeof node.weight === "number" && node.weight > 0 ? Math.min(3, Math.log10(node.weight + 1) * 1.1) : 0
            ctx.arc(node.x ?? 0, node.y ?? 0, radiusOf(node.label) + w + 4, 0, 2 * Math.PI)
            ctx.fill()
          }}
          linkColor={(l) => {
            if (isSupersedes(l)) return SUPERSEDES_COLOR
            if (hoverId) return touchesHover(l) ? "rgba(203,213,225,0.95)" : "rgba(148,163,184,0.12)"
            return isDerivedEdge(l.type) ? DERIVED_EDGE_COLOR : EDGE_COLOR
          }}
          linkWidth={(l) => (isSupersedes(l) ? 2.5 : touchesHover(l) ? 1.8 : 1)}
          // solid = read from GitHub, dashed = derived inside FalkorDB, red dashed = history (SUPERSEDES)
          linkLineDash={(l) => (isSupersedes(l) ? [4, 2] : isDerivedEdge(l.type) ? [3, 3] : null)}
          linkDirectionalArrowLength={4.5}
          linkDirectionalArrowRelPos={0.92}
          linkDirectionalArrowColor={(l) => (isSupersedes(l) ? SUPERSEDES_COLOR : hoverId && !touchesHover(l) ? "rgba(203,213,225,0.15)" : "rgba(203,213,225,0.75)")}
          linkCanvasObjectMode={() => "after"}
          linkCanvasObject={(link, ctx, scale) => {
            // Edge labels only when they can be read: zoomed in, on the hovered node's edges, or the red SUPERSEDES edge.
            const small = data.nodes.length <= 12 && data.links.length <= 14
            if (!(isSupersedes(link) || touchesHover(link) || small || scale >= LABEL_ZOOM)) return
            if (hoverId && !touchesHover(link) && !isSupersedes(link)) return
            const s = link.source as GNode
            const t = link.target as GNode
            if (typeof s !== "object" || typeof t !== "object") return
            const mx = ((s.x ?? 0) + (t.x ?? 0)) / 2
            const my = ((s.y ?? 0) + (t.y ?? 0)) / 2
            const fontSize = Math.max(7.5 / scale, 1.8)
            ctx.font = `500 ${fontSize}px ui-monospace, monospace`
            const w = ctx.measureText(link.type).width
            ctx.fillStyle = "rgba(10,10,10,0.85)"
            ctx.beginPath()
            ctx.roundRect(mx - w / 2 - 2 / scale, my - fontSize / 2 - 1 / scale, w + 4 / scale, fontSize + 2 / scale, 2 / scale)
            ctx.fill()
            ctx.textAlign = "center"
            ctx.textBaseline = "middle"
            ctx.fillStyle = isSupersedes(link) ? SUPERSEDES_COLOR : "rgba(203,213,225,0.9)"
            ctx.fillText(link.type, mx, my)
          }}
        />
      )}
      <div className="pointer-events-none absolute left-3 bottom-3 rounded-md bg-background/70 px-2 py-1 text-[10px] text-muted-foreground backdrop-blur">
        hover a node for details · zoom in for edge labels · click opens GitHub
      </div>
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
