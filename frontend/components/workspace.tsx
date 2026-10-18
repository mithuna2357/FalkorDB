"use client"

import { type ReactNode, useCallback, useEffect, useRef, useState } from "react"
import { useGroupRef, usePanelRef } from "react-resizable-panels"
import type { PaneMode } from "@/components/path-view"
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable"
import { useMediaQuery } from "@/lib/hooks"

type Orientation = "horizontal" | "vertical"

const STORAGE_KEY = "hor-layout-"
const STRIP_PX = 44 // minimised graph strip
const DEFAULT_SPLIT = { chat: 55, graph: 45 }

function loadSplit(orientation: Orientation) {
  return loadLayout(`${orientation}-split`) ?? DEFAULT_SPLIT
}

function loadLayout(orientation: string) {
  try {
    const raw = localStorage.getItem(STORAGE_KEY + orientation)
    return raw ? (JSON.parse(raw) as Record<string, number>) : undefined
  } catch {
    return undefined
  }
}

function saveLayout(orientation: string, layout: Record<string, number>) {
  try {
    localStorage.setItem(STORAGE_KEY + orientation, JSON.stringify(layout))
  } catch {}
}

/**
 * Chat + graph side by side (stacked on small screens) with a draggable divider, like window
 * snapping in Windows. The graph can be minimised to a strip or maximised over the chat; the
 * chosen split is remembered.
 */
export function Workspace({
  chat,
  graph,
}: {
  chat: ReactNode
  graph: (p: {
    mode: PaneMode
    orientation: Orientation
    minimize: () => void
    maximize: () => void
    restore: () => void
  }) => ReactNode
}) {
  const wide = useMediaQuery("(min-width: 1024px)", true)
  const orientation: Orientation = wide ? "horizontal" : "vertical"
  const groupRef = useGroupRef()
  const chatRef = usePanelRef()
  const graphRef = usePanelRef()
  const [mode, setMode] = useState<PaneMode>("split")
  const [animating, setAnimating] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined)

  const syncMode = useCallback(() => {
    if (chatRef.current?.isCollapsed()) setMode("max")
    else if (graphRef.current?.isCollapsed()) setMode("min")
    else setMode("split")
  }, [chatRef, graphRef])

  // Button-driven changes glide (CSS transition on flex-grow); dragging the divider stays 1:1.
  const animated = (fn: () => void) => {
    setAnimating(true)
    clearTimeout(timer.current)
    requestAnimationFrame(fn)
    timer.current = setTimeout(() => setAnimating(false), 500)
  }

  // Explicit layouts (percentages) rather than panel collapse()/expand(), whose "previous size"
  // bookkeeping isn't reliable when the two are mixed. The last dragged split is what restore returns to.
  const lastSplit = useRef<Record<string, number>>(loadSplit(orientation))
  useEffect(() => {
    lastSplit.current = loadSplit(orientation)
  }, [orientation])
  const groupEl = useRef<HTMLDivElement>(null)
  const graphStripPct = () => {
    const el = groupEl.current
    const total = el ? (orientation === "horizontal" ? el.clientWidth : el.clientHeight) : 0
    return total > 0 ? (STRIP_PX / total) * 100 : 4
  }

  const minimize = () =>
    animated(() => {
      const g = graphStripPct()
      groupRef.current?.setLayout({ chat: 100 - g, graph: g })
    })
  const maximize = () => animated(() => groupRef.current?.setLayout({ chat: 0, graph: 100 }))
  const restore = () => animated(() => groupRef.current?.setLayout(lastSplit.current))

  const onLayoutChange = (layout: Record<string, number>) => {
    saveLayout(orientation, layout)
    if (layout.chat >= 20 && layout.graph >= 15) {
      lastSplit.current = layout
      saveLayout(`${orientation}-split`, layout)
    }
  }

  return (
    <ResizablePanelGroup
      key={orientation}
      orientation={orientation}
      groupRef={groupRef}
      elementRef={groupEl}
      defaultLayout={loadLayout(orientation)}
      onLayoutChange={onLayoutChange}
      data-animating={animating ? "" : undefined}
      className="workspace-group"
    >
      <ResizablePanel id="chat" panelRef={chatRef} defaultSize="55" minSize="25" collapsible collapsedSize={0} onResize={syncMode}>
        <div className="h-full min-h-0 min-w-0">{chat}</div>
      </ResizablePanel>
      <ResizableHandle withHandle className="workspace-handle" />
      <ResizablePanel id="graph" panelRef={graphRef} defaultSize="45" minSize="20" collapsible collapsedSize={STRIP_PX} onResize={syncMode}>
        <div className="h-full min-h-0 min-w-0">{graph({ mode, orientation, minimize, maximize, restore })}</div>
      </ResizablePanel>
    </ResizablePanelGroup>
  )
}
