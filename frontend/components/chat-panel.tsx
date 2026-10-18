"use client"

import { ArrowDown, ArrowUp, MessageSquare } from "lucide-react"
import { AnimatePresence, motion } from "motion/react"
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react"
import { AnswerCard, type ChatMessage } from "@/components/answer-card"
import { useLenis } from "@/lib/hooks"

// Example questions from the HandsOnRepo spec.
const SUGGESTIONS = [
  "Who should review a change to the vector index code, and why them?",
  "Which PRs or commits were reverted, and why? Who decided?",
  "What happens if the top contributor leaves? Who could cover?",
  "What teams exist here and who leads each?",
  "Who are the most trusted reviewers, and who do they review for?",
  "What did I ask earlier, and what did the triage agent do recently?",
]

const EASE = [0.22, 1, 0.36, 1] as const

export function ChatPanel({
  org,
  suggestions,
  messages,
  selectedId,
  onAsk,
  onSelect,
  onRetry,
  onStreamed,
}: {
  org: string
  /** questions generated from this org's graph; falls back to generic examples */
  suggestions?: string[]
  messages: ChatMessage[]
  selectedId: string | null
  onAsk: (question: string) => void
  onSelect: (id: string) => void
  onRetry: (id: string) => void
  onStreamed: (id: string) => void
}) {
  const [draft, setDraft] = useState("")
  const scrollRef = useRef<HTMLDivElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const lenis = useLenis({ wrapper: scrollRef, content: contentRef })
  const pending = messages.some((m) => m.status === "pending")

  // Follow the conversation while the user is at the bottom; stop following once they scroll up.
  const stick = useRef(true)
  const userScrolling = useRef(false)
  const [atBottom, setAtBottom] = useState(true)

  const scrollToBottom = useCallback(
    (smooth = true) => {
      const el = scrollRef.current
      if (!el) return
      if (lenis) {
        lenis.resize()
        lenis.scrollTo(el.scrollHeight, smooth ? { lerp: 0.1, force: true } : { immediate: true, force: true })
      } else {
        el.scrollTo({ top: el.scrollHeight, behavior: smooth ? "smooth" : "auto" })
      }
    },
    [lenis],
  )

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    let idle: ReturnType<typeof setTimeout>
    const markUser = () => {
      userScrolling.current = true
      clearTimeout(idle)
      idle = setTimeout(() => (userScrolling.current = false), 200)
    }
    const onScroll = () => {
      const target = lenis ? lenis.targetScroll : el.scrollTop
      const bottom = el.scrollHeight - el.clientHeight - target < 80
      if (userScrolling.current) stick.current = bottom
      setAtBottom(el.scrollHeight - el.clientHeight - el.scrollTop < 80)
    }
    el.addEventListener("wheel", markUser, { passive: true })
    el.addEventListener("touchmove", markUser, { passive: true })
    el.addEventListener("keydown", markUser)
    el.addEventListener("scroll", onScroll, { passive: true })
    return () => {
      clearTimeout(idle)
      el.removeEventListener("wheel", markUser)
      el.removeEventListener("touchmove", markUser)
      el.removeEventListener("keydown", markUser)
      el.removeEventListener("scroll", onScroll)
    }
  }, [lenis])

  // Content grows while answers stream in: keep the latest text in view.
  useEffect(() => {
    const content = contentRef.current
    if (!content) return
    const ro = new ResizeObserver(() => {
      if (stick.current) scrollToBottom()
    })
    ro.observe(content)
    return () => ro.disconnect()
  }, [scrollToBottom])

  // Switching org: jump to the end of that org's thread.
  useLayoutEffect(() => {
    stick.current = true
    scrollToBottom(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [org])

  const submit = (q: string) => {
    const question = q.trim()
    if (!question || pending) return
    stick.current = true
    onAsk(question)
    setDraft("")
    requestAnimationFrame(() => scrollToBottom())
  }

  // Auto-grow the composer like ChatGPT.
  useLayoutEffect(() => {
    const ta = inputRef.current
    if (!ta) return
    ta.style.height = "auto"
    ta.style.height = `${Math.min(ta.scrollHeight, 160)}px`
  }, [draft])

  return (
    <section className="flex h-full min-h-0 flex-col overflow-hidden rounded-xl border bg-card/40">
      <header className="flex shrink-0 items-center gap-2 border-b px-4 py-3">
        <MessageSquare className="size-4 text-[#48d55d]" />
        <h2 className="truncate text-sm font-semibold">
          Ask the <span className="font-mono">{org}</span> brain
        </h2>
      </header>

      <div className="relative min-h-0 flex-1">
        <div ref={scrollRef} className="thin-scroll absolute inset-0 overflow-x-hidden overflow-y-auto overscroll-contain" tabIndex={-1}>
          <div ref={contentRef} className="mx-auto max-w-3xl space-y-8 px-4 py-6">
            {messages.length === 0 ? (
              <EmptyState org={org} suggestions={suggestions} onPick={submit} />
            ) : (
              messages.map((m) => (
                <AnswerCard
                  key={m.id}
                  message={m}
                  selected={m.id === selectedId}
                  onSelect={() => onSelect(m.id)}
                  onRetry={() => onRetry(m.id)}
                  onStreamed={() => onStreamed(m.id)}
                />
              ))
            )}
          </div>
        </div>

        <AnimatePresence>
          {!atBottom && messages.length > 0 && (
            <motion.button
              initial={{ opacity: 0, y: 8, scale: 0.9 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 8, scale: 0.9 }}
              transition={{ duration: 0.2 }}
              onClick={() => {
                stick.current = true
                scrollToBottom()
              }}
              className="absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full border bg-background/90 p-2 shadow-lg backdrop-blur hover:bg-secondary"
              aria-label="Scroll to latest"
            >
              <ArrowDown className="size-4" />
            </motion.button>
          )}
        </AnimatePresence>
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault()
          submit(draft)
        }}
        className="shrink-0 px-3 pt-1 pb-3"
      >
        <div className="mx-auto flex max-w-3xl items-end gap-2 rounded-3xl border bg-secondary/50 p-2 pl-4 shadow-lg shadow-black/20 transition-colors focus-within:border-[#48d55d]/50">
          <textarea
            ref={inputRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault()
                submit(draft)
              }
            }}
            rows={1}
            placeholder={`Ask anything about ${org}`}
            className="max-h-40 min-h-9 flex-1 resize-none bg-transparent py-2 text-sm outline-none placeholder:text-muted-foreground"
          />
          <motion.button
            type="submit"
            disabled={!draft.trim() || pending}
            whileTap={{ scale: 0.9 }}
            className="grid size-9 shrink-0 place-items-center rounded-full bg-foreground text-background transition-opacity disabled:opacity-30"
            aria-label="Ask"
          >
            <ArrowUp className="size-4" />
          </motion.button>
        </div>
      </form>
    </section>
  )
}

function EmptyState({ org, onPick, suggestions }: { org: string; onPick: (q: string) => void; suggestions?: string[] }) {
  return (
    <motion.div
      key={org}
      className="mx-auto max-w-xl pt-[8vh] text-center"
      initial="hidden"
      animate="show"
      variants={{ show: { transition: { staggerChildren: 0.06 } } }}
    >
      <motion.h3
        className="text-xl font-semibold tracking-tight"
        variants={{ hidden: { opacity: 0, y: 10 }, show: { opacity: 1, y: 0, transition: { duration: 0.45, ease: EASE } } }}
      >
        What do you want to know about <span className="font-mono text-[#48d55d]">{org}</span>?
      </motion.h3>
      <motion.p
        className="mt-2 text-sm text-muted-foreground"
        variants={{ hidden: { opacity: 0, y: 10 }, show: { opacity: 1, y: 0, transition: { duration: 0.45, ease: EASE } } }}
      >
        Every answer comes with its sources, the graph path that produced it and the Cypher it ran.
      </motion.p>
      <div className="mt-6 grid gap-2 sm:grid-cols-2">
        {(suggestions && suggestions.length ? suggestions : SUGGESTIONS).map((s) => (
          <motion.button
            key={s}
            onClick={() => onPick(s)}
            variants={{ hidden: { opacity: 0, y: 12 }, show: { opacity: 1, y: 0, transition: { duration: 0.45, ease: EASE } } }}
            whileHover={{ y: -2 }}
            whileTap={{ scale: 0.98 }}
            className="rounded-2xl border bg-card/60 px-4 py-3 text-left text-sm transition-colors hover:border-[#48d55d]/50 hover:bg-secondary/60"
          >
            {s}
          </motion.button>
        ))}
      </div>
    </motion.div>
  )
}
