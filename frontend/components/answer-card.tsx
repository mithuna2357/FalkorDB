"use client"

import { Brain, Check, ChevronRight, Copy, ExternalLink, Network, RotateCcw, TriangleAlert, Wrench } from "lucide-react"
import { AnimatePresence, motion } from "motion/react"
import { useEffect, useMemo, useRef, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { LogoMark } from "@/components/logo-mark"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import type { AskResponse, MemoryItem, Source, Usage } from "@/lib/types"

export type ChatMessage = {
  id: string
  question: string
  status: "pending" | "done" | "error"
  response?: AskResponse
  error?: string
  /** true until the answer has finished its typing animation once */
  stream?: boolean
}

const EASE = [0.22, 1, 0.36, 1] as const

const SOURCE_STYLES: Record<string, string> = {
  PR: "border-sky-500/40 text-sky-300",
  Issue: "border-amber-500/40 text-amber-300",
  Commit: "border-slate-400/40 text-slate-300",
  Person: "border-[#48d55d]/40 text-[#48d55d]",
  File: "border-emerald-500/30 text-emerald-200/80",
  Release: "border-cyan-500/40 text-cyan-300",
  Doc: "border-indigo-400/40 text-indigo-300",
  Comment: "border-teal-400/40 text-teal-300",
  Action: "border-orange-500/40 text-orange-300",
  Repo: "border-lime-500/40 text-lime-300",
}

const AGENT_LABEL: Record<string, string> = { chat: "this chat", triage: "the triage agent", mcp: "an MCP client (Cursor / Claude Desktop)" }

/** Earlier episodes the agent recalled from the graph before answering (memory across sessions and agents). */
function MemoryStrip({ memory }: { memory: MemoryItem[] }) {
  if (!memory.length) return null
  return (
    <div className="mb-2 flex items-start gap-2 rounded-lg border border-[#48d55d]/25 bg-[#48d55d]/[0.06] px-3 py-2 text-xs">
      <Brain className="mt-0.5 size-3.5 shrink-0 text-[#48d55d]" />
      <div className="min-w-0 space-y-0.5">
        <p className="font-medium text-[#48d55d]">Remembered from earlier</p>
        {memory.slice(0, 2).map((m, i) => (
          <p key={m.episode_id ?? i} className="truncate text-muted-foreground" title={m.answer_excerpt}>
            <span className="text-foreground/90">{m.question}</span>
            <span className="ml-1.5 font-mono text-[10px]">
              {AGENT_LABEL[m.agent ?? "chat"] ?? m.agent}
              {m.at ? ` · ${new Date(m.at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}` : ""}
            </span>
          </p>
        ))}
      </div>
    </div>
  )
}

/** Model and token cost for this answer (gpt-5-mini list price: $0.25 / M in, $2 / M out). */
function UsageLine({ usage, graphMs }: { usage?: Usage; graphMs: number }) {
  if (!usage?.model) return null
  const inT = usage.prompt_tokens ?? 0
  const outT = usage.completion_tokens ?? 0
  const cost = inT * 0.25e-6 + outT * 2e-6
  return (
    <p className="pt-2 font-mono text-[10px] text-muted-foreground">
      graph {graphMs} ms · {usage.model} · {(inT / 1000).toFixed(1)}k in / {outT} out · ~${cost.toFixed(4)}
    </p>
  )
}

export function AnswerCard({
  message,
  selected,
  onSelect,
  onRetry,
  onStreamed,
}: {
  message: ChatMessage
  selected: boolean
  onSelect: () => void
  onRetry: () => void
  onStreamed: () => void
}) {
  const { question, status, response, error } = message
  // The card mounts while the question is pending, so derive "typing" from the message rather than initial state.
  const [finished, setFinished] = useState(false)
  const typing = !!message.stream && !finished

  return (
    <article className="space-y-4">
      <motion.div
        className="flex justify-end"
        initial={{ opacity: 0, y: 14, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.35, ease: EASE }}
      >
        <p className="max-w-[85%] rounded-3xl bg-secondary px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap">{question}</p>
      </motion.div>

      <motion.div
        className="flex gap-3"
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35, delay: 0.08, ease: EASE }}
      >
        <LogoMark className="mt-0.5" />
        <div
          className={cn(
            "min-w-0 flex-1 rounded-2xl transition-[background-color,box-shadow] duration-300",
            status === "done" && selected && "-m-2 bg-[#48d55d]/[0.04] p-2 ring-1 ring-[#48d55d]/25",
          )}
        >
          {status === "pending" && <p className="shimmer-text pt-0.5 text-sm">Traversing the graph…</p>}

          {status === "error" && (
            <div className="flex items-start justify-between gap-3 rounded-xl border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm">
              <p className="flex items-start gap-2">
                <TriangleAlert className="mt-0.5 size-4 shrink-0 text-destructive" />
                <span>
                  Couldn&apos;t get an answer. <span className="text-muted-foreground">{error}</span>
                </span>
              </p>
              <Button size="sm" variant="outline" onClick={onRetry}>
                <RotateCcw /> Retry
              </Button>
            </div>
          )}

          {status === "done" && response && (
            <>
              <MemoryStrip memory={response.memory ?? []} />
              <StreamingMarkdown
                text={response.answer}
                animate={typing}
                onDone={() => {
                  setFinished(true)
                  onStreamed()
                }}
              />
              <AnimatePresence>
                {!typing && (
                  <motion.div
                    initial="hidden"
                    animate="show"
                    variants={{ show: { transition: { staggerChildren: 0.07 } } }}
                  >
                    <Reveal>
                      <div className="flex flex-wrap items-center gap-2 pt-3">
                        <Button
                          size="sm"
                          variant={selected ? "secondary" : "outline"}
                          onClick={onSelect}
                          disabled={!response.path?.nodes?.length}
                          className="rounded-full"
                        >
                          <Network /> {selected ? "Showing path" : "Show path"}
                        </Button>
                        {response.toolCalls?.map((t, i) => (
                          <Badge
                            key={i}
                            variant="outline"
                            className="rounded-full font-mono text-[11px] text-muted-foreground"
                            title={JSON.stringify(t.args)}
                          >
                            <Wrench className="size-3" /> {t.tool} · {t.ms}ms
                          </Badge>
                        ))}
                      </div>
                    </Reveal>
                    <Reveal>
                      <Sources sources={response.sources ?? []} />
                    </Reveal>
                    <Reveal>
                      <CypherBlock queries={response.cypher ?? []} />
                    </Reveal>
                    <Reveal>
                      <UsageLine usage={response.usage} graphMs={(response.toolCalls ?? []).reduce((a, t) => a + (t.ms || 0), 0)} />
                    </Reveal>
                  </motion.div>
                )}
              </AnimatePresence>
            </>
          )}
        </div>
      </motion.div>
    </article>
  )
}

function Reveal({ children }: { children: React.ReactNode }) {
  return (
    <motion.div
      variants={{ hidden: { opacity: 0, y: 8 }, show: { opacity: 1, y: 0, transition: { duration: 0.4, ease: EASE } } }}
    >
      {children}
    </motion.div>
  )
}

/** Types the answer out word by word (like ChatGPT), then renders it in full. */
function StreamingMarkdown({ text, animate, onDone }: { text: string; animate: boolean; onDone: () => void }) {
  const tokens = useMemo(() => text.match(/\s*\S+/g) ?? [], [text])
  const [count, setCount] = useState(animate ? 0 : tokens.length)
  const doneRef = useRef(onDone)
  useEffect(() => {
    doneRef.current = onDone
  })

  useEffect(() => {
    if (!animate) return
    const total = tokens.length
    const duration = Math.min(3200, Math.max(700, total * 32))
    const start = performance.now()
    let raf = 0
    let shown = 0
    const tick = (now: number) => {
      const p = Math.min(1, (now - start) / duration)
      const next = Math.ceil(total * p)
      if (next !== shown) {
        shown = next
        setCount(next)
      }
      if (p < 1) raf = requestAnimationFrame(tick)
      else doneRef.current()
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [animate, tokens])

  const typing = animate && count < tokens.length
  let visible = typing ? tokens.slice(0, count).join("") : text
  // Close a dangling **bold** so half-typed markdown doesn't flash raw asterisks.
  if (typing && (visible.match(/\*\*/g)?.length ?? 0) % 2 === 1) visible += "**"

  return (
    <div className={cn("answer-md text-sm leading-relaxed", typing && "streaming")}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          // eslint-disable-next-line @typescript-eslint/no-unused-vars
          a: ({ node, ...props }) => <a {...props} target="_blank" rel="noreferrer" />,
        }}
      >
        {visible}
      </ReactMarkdown>
    </div>
  )
}

function Sources({ sources }: { sources: Source[] }) {
  if (sources.length === 0) return null
  return (
    <div className="pt-4">
      <h3 className="mb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">Sources ({sources.length})</h3>
      <ul className="grid grid-cols-1 gap-1">
        {sources.map((s) => (
          <li key={`${s.type}-${s.id}`}>
            <a
              href={s.url}
              target="_blank"
              rel="noreferrer"
              className="group flex items-center gap-2 rounded-lg border border-transparent px-2 py-1.5 text-sm transition-colors duration-200 hover:border-border hover:bg-secondary/60"
            >
              <Badge variant="outline" className={cn("w-16 justify-center font-mono text-[10px]", SOURCE_STYLES[s.type])}>
                {s.type}
              </Badge>
              <span className="shrink-0 font-mono text-xs text-muted-foreground">
                {s.type === "PR" || s.type === "Issue" ? `#${s.id}` : s.type === "Commit" ? s.id.slice(0, 7) : ""}
              </span>
              <span className="min-w-0 flex-1 truncate">{s.title}</span>
              <ExternalLink className="size-3.5 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
            </a>
          </li>
        ))}
      </ul>
    </div>
  )
}

function CypherBlock({ queries }: { queries: string[] }) {
  const [open, setOpen] = useState(false)
  const [copied, setCopied] = useState<number | null>(null)
  if (queries.length === 0) return null

  const copy = async (q: string, i: number) => {
    try {
      await navigator.clipboard.writeText(q)
      setCopied(i)
      setTimeout(() => setCopied(null), 1200)
    } catch {}
  }

  return (
    <div className="mt-3 border-t pt-2">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center gap-1.5 py-1 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
      >
        <ChevronRight className={cn("size-3.5 transition-transform duration-300", open && "rotate-90")} />
        Cypher queries ({queries.length})
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.35, ease: EASE }}
            className="overflow-hidden"
          >
            <div className="space-y-2 pt-2 pb-1">
              {queries.map((q, i) => (
                <div key={i} className="group relative">
                  <pre className="overflow-x-auto rounded-lg border bg-black/40 p-3 pr-10 font-mono text-xs leading-relaxed text-[#9be9a8]">
                    <code>{q}</code>
                  </pre>
                  <button
                    onClick={() => copy(q, i)}
                    className="absolute top-2 right-2 rounded p-1 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 hover:bg-secondary hover:text-foreground focus:opacity-100"
                    aria-label="Copy query"
                  >
                    {copied === i ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
                  </button>
                </div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
