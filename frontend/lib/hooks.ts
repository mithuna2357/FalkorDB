"use client"

import Lenis from "lenis"
import { type RefObject, useEffect, useRef, useState, useSyncExternalStore } from "react"

export function useMediaQuery(query: string, serverValue = false) {
  return useSyncExternalStore(
    (onChange) => {
      const mql = window.matchMedia(query)
      mql.addEventListener("change", onChange)
      return () => mql.removeEventListener("change", onChange)
    },
    () => window.matchMedia(query).matches,
    () => serverValue,
  )
}

export const useReducedMotion = () => useMediaQuery("(prefers-reduced-motion: reduce)")

/**
 * Inertial smooth scrolling (Lenis). With no wrapper it drives the window; with a wrapper/content
 * pair it drives that scroll container. Elements marked `data-lenis-prevent` keep native wheel
 * handling (the graph canvas uses this so the wheel zooms instead of scrolling).
 */
export function useLenis({
  wrapper,
  content,
  enabled = true,
}: {
  wrapper?: RefObject<HTMLElement | null>
  content?: RefObject<HTMLElement | null>
  enabled?: boolean
} = {}) {
  const reduced = useReducedMotion()
  const [lenis, setLenis] = useState<Lenis | null>(null)

  useEffect(() => {
    if (!enabled || reduced) return
    if (wrapper && (!wrapper.current || !content?.current)) return
    const instance = new Lenis({
      wrapper: wrapper?.current ?? undefined,
      content: content?.current ?? undefined,
      lerp: 0.075,
      smoothWheel: true,
      wheelMultiplier: 0.9,
      touchMultiplier: 1.2,
      autoRaf: true,
    })
    setLenis(instance)
    return () => {
      instance.destroy()
      setLenis(null)
    }
  }, [enabled, reduced, wrapper, content])

  return lenis
}

/** Run once per page load (survives remounts, resets on reload). */
const ranOnce = new Set<string>()
export function useOncePerLoad(key: string) {
  const first = useRef(!ranOnce.has(key))
  useEffect(() => {
    ranOnce.add(key)
  }, [key])
  return first.current
}
