"use client"

import { ArrowRight, ChevronLeft, ChevronRight, Database } from "lucide-react"
import { motion, useReducedMotion } from "motion/react"
import { useEffect, useMemo, useRef, useState } from "react"
import { fmt, timeAgo } from "@/components/org-controls"
import { cn } from "@/lib/utils"
import type { OrgSummary } from "@/lib/types"

const SPACING = 190 // px between card centres at |offset| = 1
const SPRING = { type: "spring", stiffness: 210, damping: 28, mass: 0.9 } as const

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))

/** Stacked 3D card carousel of existing brains. Drag, swipe, side-scroll, arrows or keys to move. */
export function BrainCarousel({
  orgs,
  onOpen,
  disabled,
}: {
  orgs: OrgSummary[]
  onOpen: (org: string) => void
  disabled?: boolean
}) {
  const reduced = useReducedMotion()
  const [active, setActive] = useState(0)
  const [drag, setDrag] = useState(0)
  const dragged = useRef(false)
  const stageRef = useRef<HTMLDivElement>(null)
  const n = orgs.length

  useEffect(() => {
    setActive((a) => clamp(a, 0, Math.max(0, n - 1)))
  }, [n])

  const go = (i: number) => setActive(clamp(i, 0, n - 1))
  const goRef = useRef(go)
  goRef.current = go
  const activeRef = useRef(active)
  activeRef.current = active

  // Horizontal wheel / trackpad swipe steps the carousel; vertical wheel still scrolls the page.
  useEffect(() => {
    const el = stageRef.current
    if (!el) return
    let acc = 0
    let lockUntil = 0
    const onWheel = (e: WheelEvent) => {
      const dx = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.shiftKey ? e.deltaY : 0
      if (!dx) return
      e.preventDefault()
      const now = performance.now()
      if (now < lockUntil) return
      acc += dx
      if (Math.abs(acc) > 40) {
        goRef.current(activeRef.current + Math.sign(acc))
        acc = 0
        lockUntil = now + 260
      }
    }
    el.addEventListener("wheel", onWheel, { passive: false })
    return () => el.removeEventListener("wheel", onWheel)
  }, [])

  const cards = useMemo(() => orgs.map((o) => ({ ...o, cells: miniGrid(o.org) })), [orgs])
  if (n === 0) return null

  return (
    <div className="w-full">
      <motion.div
        ref={stageRef}
        role="region"
        aria-roledescription="carousel"
        aria-label="Existing brains"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "ArrowRight") go(active + 1)
          if (e.key === "ArrowLeft") go(active - 1)
          if (e.key === "Enter" && !disabled) onOpen(orgs[active].org)
        }}
        onPanStart={() => {
          dragged.current = true
        }}
        onPan={(_, info) => setDrag(info.offset.x)}
        onPanEnd={(_, info) => {
          const steps = Math.round(-(info.offset.x + info.velocity.x * 0.18) / SPACING)
          go(active + steps)
          setDrag(0)
          setTimeout(() => (dragged.current = false), 0)
        }}
        className="relative mx-auto h-[230px] w-full cursor-grab touch-pan-y select-none outline-none active:cursor-grabbing focus-visible:ring-2 focus-visible:ring-[#48d55d]/40 rounded-2xl"
        style={{ perspective: 1100 }}
      >
        {cards.map((o, i) => {
          const off = i - active + drag / SPACING
          const abs = Math.abs(off)
          const x = Math.sign(off) * (Math.min(abs, 1) * SPACING + Math.max(0, abs - 1) * SPACING * 0.5)
          const isActive = i === active && drag === 0
          return (
            <motion.div
              key={o.org}
              className="absolute inset-0 m-auto h-fit w-[280px] max-w-[78vw]"
              style={{ zIndex: 100 - Math.round(abs * 10), transformStyle: "preserve-3d" }}
              initial={false}
              animate={{
                x,
                rotateY: clamp(-off * 24, -42, 42),
                rotateZ: clamp(off * 2.5, -5, 5),
                scale: 1 - Math.min(abs, 3) * 0.09,
                opacity: abs > 2.6 ? 0 : 1 - abs * 0.16,
                filter: `brightness(${1 - Math.min(abs, 2) * 0.22})`,
              }}
              transition={drag !== 0 || reduced ? { duration: 0 } : SPRING}
            >
              <button
                type="button"
                disabled={disabled}
                onClick={() => {
                  if (dragged.current) return
                  if (i === active) onOpen(o.org)
                  else go(i)
                }}
                className={cn(
                  "group block w-full rounded-2xl border bg-card p-4 text-left shadow-2xl shadow-black/50 transition-colors disabled:opacity-60",
                  isActive ? "border-[#48d55d]/50" : "border-border",
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="flex items-center gap-1.5 font-mono text-[11px] text-muted-foreground">
                    <Database className="size-3" /> brain_{o.org}
                  </span>
                  <span className="font-mono text-[11px] text-muted-foreground">{timeAgo(o.lastSync)}</span>
                </div>
                <div className="mt-2 truncate font-mono text-xl font-semibold">{o.org}</div>
                <div className="mt-3 grid grid-cols-[repeat(18,minmax(0,1fr))] gap-[3px]" aria-hidden>
                  {o.cells.map((c, j) => (
                    <span key={j} className="aspect-square rounded-[2px]" style={{ background: c }} />
                  ))}
                </div>
                <div className="mt-3 flex items-center justify-between font-mono text-xs">
                  <span className="text-muted-foreground">
                    <span className="text-foreground">{fmt.format(o.nodes)}</span> nodes ·{" "}
                    <span className="text-foreground">{fmt.format(o.edges)}</span> edges
                  </span>
                  <span
                    className={cn(
                      "flex items-center gap-1 text-[#48d55d] transition-opacity",
                      isActive ? "opacity-100" : "opacity-0",
                    )}
                  >
                    Open <ArrowRight className="size-3.5 transition-transform group-hover:translate-x-0.5" />
                  </span>
                </div>
              </button>
            </motion.div>
          )
        })}
      </motion.div>

      {n > 1 && (
        <div className="mt-2 flex items-center justify-center gap-3">
          <button
            onClick={() => go(active - 1)}
            disabled={active === 0}
            className="rounded-full border p-1.5 text-muted-foreground transition-colors hover:text-foreground disabled:opacity-30"
            aria-label="Previous brain"
          >
            <ChevronLeft className="size-4" />
          </button>
          <div className="flex gap-1.5">
            {orgs.map((o, i) => (
              <button
                key={o.org}
                onClick={() => go(i)}
                aria-label={`Show ${o.org}`}
                className={cn(
                  "h-1.5 rounded-full transition-all duration-300",
                  i === active ? "w-5 bg-[#48d55d]" : "w-1.5 bg-muted-foreground/40 hover:bg-muted-foreground",
                )}
              />
            ))}
          </div>
          <button
            onClick={() => go(active + 1)}
            disabled={active === n - 1}
            className="rounded-full border p-1.5 text-muted-foreground transition-colors hover:text-foreground disabled:opacity-30"
            aria-label="Next brain"
          >
            <ChevronRight className="size-4" />
          </button>
        </div>
      )}
    </div>
  )
}

// Decorative contribution-style strip, deterministic per org name.
const SHADES = ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353"]
function miniGrid(seed: string) {
  let h = 2166136261
  for (const ch of seed) h = Math.imul(h ^ ch.charCodeAt(0), 16777619)
  return Array.from({ length: 18 * 4 }, () => {
    h = Math.imul(h ^ (h >>> 15), 2246822507) ^ Math.imul(h ^ (h >>> 13), 3266489909)
    const r = ((h >>> 0) % 100) / 100
    return SHADES[r < 0.35 ? 0 : r < 0.6 ? 1 : r < 0.78 ? 2 : r < 0.92 ? 3 : 4]
  })
}
