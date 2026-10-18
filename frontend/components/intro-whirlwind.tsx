"use client"

import { type RefObject, useEffect, useRef, useState } from "react"

type Particle = {
  tx: number // target centre
  ty: number
  size: number
  color: string
  r0: number // whirl radius at start
  theta0: number
  omega: number // rad/s
  wobble: number
  phase: number
  landAt: number // ms
}

const SWIRL_MS = 1800
const LAND_MS = 950
const STAGGER_MS = 550
const FADE_MS = 380

const easeInOutCubic = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2)
const easeOutCubic = (t: number) => 1 - Math.pow(1 - t, 3)
const clamp01 = (t: number) => Math.min(1, Math.max(0, t))

/**
 * Green dots of the title swirl across the screen like a whirlwind, then each flies into
 * its own cell of the CommitsGrid inside `gridRef` (cells marked with data-hl).
 * Calls onLanded when every dot is in place, then fades itself out.
 */
export function IntroWhirlwind({ gridRef, onLanded }: { gridRef: RefObject<HTMLElement | null>; onLanded: () => void }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [fading, setFading] = useState(false)
  const [gone, setGone] = useState(false)
  const landedRef = useRef(onLanded)
  landedRef.current = onLanded

  useEffect(() => {
    const canvas = canvasRef.current
    const grid = gridRef.current
    const ctx = canvas?.getContext("2d")
    if (!canvas || !grid || !ctx) {
      landedRef.current()
      setGone(true)
      return
    }

    let finished = false
    const finish = () => {
      if (finished) return
      finished = true
      cancelAnimationFrame(raf)
      landedRef.current()
      setFading(true)
      setTimeout(() => setGone(true), FADE_MS)
    }

    const w = window.innerWidth
    const h = window.innerHeight
    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    canvas.width = w * dpr
    canvas.height = h * dpr
    canvas.style.width = `${w}px`
    canvas.style.height = `${h}px`
    ctx.scale(dpr, dpr)

    const cells = Array.from(grid.querySelectorAll<HTMLElement>("[data-hl]"))
    if (cells.length === 0) {
      finish()
      return
    }
    const rects = cells.map((c) => c.getBoundingClientRect())
    // The vortex starts in the middle of the screen and drifts up onto the title as it tightens.
    const titleX = rects.reduce((a, r) => a + r.left + r.width / 2, 0) / rects.length
    const titleY = rects.reduce((a, r) => a + r.top + r.height / 2, 0) / rects.length
    // Half the screen diagonal: dots spawn at or beyond the screen edges.
    const reach = Math.hypot(w, h) * 0.5

    const particles: Particle[] = cells.map((cell, i) => {
      const r = rects[i]
      // Dots further left in the title land first, so the word "writes" itself left to right.
      const order = (r.left - rects[0].left) / Math.max(1, rects[rects.length - 1].left - rects[0].left)
      return {
        tx: r.left + r.width / 2,
        ty: r.top + r.height / 2,
        size: r.width,
        color: getComputedStyle(cell).getPropertyValue("--highlight").trim() || "#48d55d",
        r0: reach * (1 + Math.random() * 0.8),
        theta0: Math.random() * Math.PI * 2,
        omega: 2.6 + Math.random() * 2.2,
        wobble: 10 + Math.random() * 26,
        phase: Math.random() * Math.PI * 2,
        landAt: SWIRL_MS - 250 + Math.abs(order) * STAGGER_MS + Math.random() * 120,
      }
    })
    const endAt = Math.max(...particles.map((p) => p.landAt)) + LAND_MS

    const roundRect = (x: number, y: number, s: number, rad: number) => {
      ctx.beginPath()
      ctx.roundRect(x - s / 2, y - s / 2, s, s, rad)
      ctx.fill()
    }

    const start = performance.now()
    let raf = 0
    const frame = (now: number) => {
      const t = now - start
      const ts = t / 1000

      // Fade previous frame instead of clearing: gives the whirl motion trails.
      const settling = clamp01((t - SWIRL_MS) / (endAt - SWIRL_MS))
      ctx.globalCompositeOperation = "destination-out"
      ctx.fillStyle = `rgba(0,0,0,${0.28 + settling * 0.72})`
      ctx.fillRect(0, 0, w, h)
      ctx.globalCompositeOperation = "source-over"

      const intro = easeOutCubic(clamp01(t / 450))
      for (const p of particles) {
        // Whirlwind: elliptical orbit around the title that tightens over time.
        const drift = easeInOutCubic(clamp01(t / (SWIRL_MS + STAGGER_MS)))
        const cx = w / 2 + (titleX - w / 2) * drift
        const cy = h / 2 + (titleY - h / 2) * drift
        // Rush in from far away, then keep circling tighter until the dots peel off to land.
        const inward = 1 - 0.78 * easeOutCubic(clamp01(t / (SWIRL_MS + STAGGER_MS)))
        const radius = p.r0 * inward + p.wobble * Math.sin(ts * 3 + p.phase)
        const theta = p.theta0 + p.omega * ts * (1 - 0.35 * settling)
        const sx = cx + radius * Math.cos(theta)
        const sy = cy + radius * Math.sin(theta) * 0.55

        const k = easeInOutCubic(clamp01((t - p.landAt) / LAND_MS))
        const x = sx + (p.tx - sx) * k
        const y = sy + (p.ty - sy) * k
        const size = p.size * (0.5 + 0.5 * k)

        ctx.globalAlpha = intro
        ctx.fillStyle = p.color
        ctx.shadowColor = p.color
        ctx.shadowBlur = 14 * (1 - k)
        roundRect(x, y, size, Math.max(1.5, size * 0.2))
      }
      ctx.shadowBlur = 0
      ctx.globalAlpha = 1

      if (t >= endAt) finish()
      else raf = requestAnimationFrame(frame)
    }
    raf = requestAnimationFrame(frame)

    // Any input or a resize skips straight to the finished title.
    const skip = () => finish()
    window.addEventListener("resize", skip)
    window.addEventListener("keydown", skip)
    window.addEventListener("pointerdown", skip)
    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener("resize", skip)
      window.removeEventListener("keydown", skip)
      window.removeEventListener("pointerdown", skip)
    }
  }, [gridRef])

  if (gone) return null
  return (
    <canvas
      ref={canvasRef}
      aria-hidden
      className="pointer-events-none fixed inset-0 z-50 transition-opacity ease-out"
      style={{ opacity: fading ? 0 : 1, transitionDuration: `${FADE_MS}ms` }}
    />
  )
}
