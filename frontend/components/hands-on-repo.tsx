"use client"

import { CircleCheck, Database, Plus, TriangleAlert, X } from "lucide-react"
import { AnimatePresence, motion } from "motion/react"
import { useCallback, useEffect, useRef, useState } from "react"
import type { ChatMessage } from "@/components/answer-card"
import { BrainCarousel } from "@/components/brain-carousel"
import { ChatPanel } from "@/components/chat-panel"
import { ConnectMcpButton } from "@/components/connect-mcp"
import { GitHubAuth } from "@/components/github-auth"
import { GitHubLink } from "@/components/github-link"
import { IntroWhirlwind } from "@/components/intro-whirlwind"
import { LogoMark, Wordmark } from "@/components/logo-mark"
import {
  BuildBrainForm,
  BuildProgress,
  OrgSwitcher,
  SyncButton,
  SyncCounts,
  fmt,
  timeAgo,
} from "@/components/org-controls"
import { PathView } from "@/components/path-view"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { CommitsGrid } from "@/components/ui/commits-grid"
import { Workspace } from "@/components/workspace"
import { DATA_MODE, ask, getOrgMap, getOrgs, getSuggestions, sync } from "@/lib/api"
import { useLenis, useOncePerLoad, useReducedMotion } from "@/lib/hooks"
import type { GraphPath, OrgSummary, SyncResponse } from "@/lib/types"
import { cn } from "@/lib/utils"

const EASE = [0.22, 1, 0.36, 1] as const

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e))
const sameOrg = (a: string, b: string) => a.toLowerCase() === b.toLowerCase()

export function HandsOnRepo() {
  const [orgs, setOrgs] = useState<OrgSummary[]>([])
  const [orgsStatus, setOrgsStatus] = useState<"loading" | "ready" | "error">("loading")
  const [orgsError, setOrgsError] = useState<string | null>(null)
  const [activeOrg, setActiveOrg] = useState<string | null>(null)

  const [building, setBuilding] = useState<{ org: string; startedAt: number } | null>(null)
  const [buildError, setBuildError] = useState<string | null>(null)
  const [ready, setReady] = useState<OrgSummary | null>(null)
  const [adding, setAdding] = useState(false)

  const [syncing, setSyncing] = useState<string | null>(null)
  const [syncResults, setSyncResults] = useState<Record<string, SyncResponse>>({})
  const [syncError, setSyncError] = useState<string | null>(null)

  const [threads, setThreads] = useState<Record<string, ChatMessage[]>>({})
  const [selected, setSelected] = useState<Record<string, string | null>>({})
  // Example questions generated from each org's own graph, shown on the empty chat screen.
  const [suggestions, setSuggestions] = useState<Record<string, string[]>>({})
  useEffect(() => {
    if (!activeOrg || suggestions[activeOrg]) return
    let cancelled = false
    getSuggestions(activeOrg)
      .then((q) => {
        if (!cancelled) setSuggestions((o) => ({ ...o, [activeOrg]: q }))
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [activeOrg, suggestions])
  // Org overview graph (repos + most active people), shown in the path pane until an answer is selected.
  const [overview, setOverview] = useState<Record<string, GraphPath>>({})
  useEffect(() => {
    if (!activeOrg || overview[activeOrg]) return
    let cancelled = false
    getOrgMap(activeOrg)
      .then((p) => {
        if (!cancelled) setOverview((o) => ({ ...o, [activeOrg]: p }))
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [activeOrg, overview])

  // Smooth inertial page scroll on the landing page (the workspace is a fixed-height layout).
  useLenis({ enabled: !activeOrg })

  const refreshOrgs = useCallback(async () => {
    try {
      const list = await getOrgs()
      setOrgs(list)
      setOrgsStatus("ready")
      setOrgsError(null)
      return list
    } catch (e) {
      setOrgsStatus("error")
      setOrgsError(errMsg(e))
      return null
    }
  }, [])

  useEffect(() => {
    refreshOrgs()
  }, [refreshOrgs])

  const current = orgs.find((o) => activeOrg && sameOrg(o.org, activeOrg)) ?? null

  // ---- Build brain (POST /api/sync for the new org, then GET /api/orgs) ----
  const build = async (org: string, repos?: string[]) => {
    setBuilding({ org, startedAt: Date.now() })
    setBuildError(null)
    setReady(null)
    try {
      await sync(org, repos)
      const list = await refreshOrgs()
      const found = list?.find((o) => sameOrg(o.org, org))
      setActiveOrg(found?.org ?? org)
      setReady(found ?? null)
      setAdding(false)
    } catch (e) {
      setBuildError(`Couldn't build the brain for ${org}: ${errMsg(e)}`)
    } finally {
      setBuilding(null)
    }
  }

  // ---- Sync now ----
  const syncNow = async () => {
    if (!activeOrg) return
    const org = activeOrg
    setSyncing(org)
    setSyncError(null)
    try {
      const res = await sync(org)
      setSyncResults((r) => ({ ...r, [org]: res }))
      await refreshOrgs()
    } catch (e) {
      setSyncError(`Sync failed: ${errMsg(e)}`)
    } finally {
      setSyncing(null)
    }
  }

  // ---- Chat ----
  const patch = (org: string, id: string, update: Partial<ChatMessage>) =>
    setThreads((t) => ({ ...t, [org]: (t[org] ?? []).map((m) => (m.id === id ? { ...m, ...update } : m)) }))

  const run = async (org: string, id: string, question: string) => {
    try {
      const response = await ask(org, question)
      patch(org, id, { status: "done", response, error: undefined, stream: true })
      if (response.path?.nodes?.length) setSelected((s) => ({ ...s, [org]: id }))
    } catch (e) {
      patch(org, id, { status: "error", error: errMsg(e) })
    }
  }

  const onAsk = (question: string) => {
    if (!activeOrg) return
    const org = activeOrg
    const id = crypto.randomUUID()
    setThreads((t) => ({ ...t, [org]: [...(t[org] ?? []), { id, question, status: "pending" }] }))
    run(org, id, question)
  }

  const onRetry = (id: string) => {
    if (!activeOrg) return
    const msg = threads[activeOrg]?.find((m) => m.id === id)
    if (!msg) return
    patch(activeOrg, id, { status: "pending", error: undefined })
    run(activeOrg, id, msg.question)
  }

  const messages = activeOrg ? (threads[activeOrg] ?? []) : []
  const selectedId = activeOrg ? (selected[activeOrg] ?? null) : null
  const selectedMsg = messages.find((m) => m.id === selectedId && m.status === "done") ?? null

  const switchOrg = (org: string) => {
    setActiveOrg(org)
    setReady(null)
    setSyncError(null)
  }
  // After a sync the overview may have changed; drop the cached one so it reloads.
  useEffect(() => {
    if (syncing === null && activeOrg && syncResults[activeOrg]) setOverview((o) => {
      const rest = { ...o }
      delete rest[activeOrg]
      return rest
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [syncResults])

  return (
    <div className={cn("flex flex-col", activeOrg ? "h-dvh overflow-hidden" : "min-h-dvh")}>
      {/* Header */}
      <header className="sticky top-0 z-20 shrink-0 border-b bg-background/80 backdrop-blur-md">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2.5">
          <button onClick={() => setActiveOrg(null)} className="flex items-center gap-2" aria-label="HandsOnRepo home">
            <LogoMark className="size-7" />
            <Wordmark />
          </button>

          {DATA_MODE === "fixtures" && (
            <Badge variant="outline" className="border-amber-500/40 text-amber-300" title="Set BACKEND_URL to use the live API">
              Fixture mode
            </Badge>
          )}

          <div className="ml-auto flex flex-wrap items-center gap-2">
            <ConnectMcpButton />
            <GitHubAuth />
            {orgs.length > 0 && (
              <OrgSwitcher orgs={orgs} value={current?.org ?? null} onChange={switchOrg} disabled={!!building} />
            )}
            {activeOrg && (
              <>
                <Button variant="ghost" size="icon" onClick={() => setAdding((a) => !a)} aria-label="Add organisation" title="Build another org's brain">
                  {adding ? <X /> : <Plus />}
                </Button>
                <SyncButton
                  onSync={syncNow}
                  syncing={syncing === activeOrg}
                  result={syncResults[activeOrg] ?? null}
                  disabled={!!building}
                />
              </>
            )}
            <GitHubLink />
          </div>
        </div>

        {activeOrg && current && (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t px-4 py-1.5 font-mono text-xs text-muted-foreground">
            <span className="flex items-center gap-1.5">
              <Database className="size-3.5" /> brain_{current.org}
            </span>
            <span>{fmt.format(current.nodes)} nodes</span>
            <span>{fmt.format(current.edges)} edges</span>
            <span title={new Date(current.lastSync).toLocaleString()}>synced {timeAgo(current.lastSync)}</span>
            {syncResults[current.org] && syncing !== current.org && (
              <span className="sm:hidden">
                <SyncCounts result={syncResults[current.org]} />
              </span>
            )}
          </div>
        )}
      </header>

      <AnimatePresence mode="wait" initial={false}>
        {activeOrg ? (
          <motion.div
            key="workspace"
            className="flex min-h-0 flex-1 flex-col"
            initial={{ opacity: 0, scale: 0.985 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.985 }}
            transition={{ duration: 0.3, ease: EASE }}
          >
            {/* Banners */}
            <AnimatePresence initial={false}>
              {(adding || building || buildError || ready || syncError) && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.3, ease: EASE }}
                  className="shrink-0 overflow-hidden"
                >
                  <div className="space-y-2 px-4 pt-3">
                    {adding && !building && <BuildBrainForm onBuild={build} building={!!building} className="max-w-xl" />}
                    {building && <BuildProgress org={building.org} startedAt={building.startedAt} />}
                    {buildError && <Banner tone="error" onClose={() => setBuildError(null)}>{buildError}</Banner>}
                    {syncError && <Banner tone="error" onClose={() => setSyncError(null)}>{syncError}</Banner>}
                    {ready && (
                      <Banner tone="ok" onClose={() => setReady(null)}>
                        Brain ready: <b>{ready.nodes.toLocaleString()}</b> nodes, <b>{ready.edges.toLocaleString()}</b> edges
                      </Banner>
                    )}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            <main className="min-h-0 flex-1 p-3 sm:p-4">
              <Workspace
                chat={
                  <ChatPanel
                    org={activeOrg}
                    suggestions={suggestions[activeOrg]}
                    messages={messages}
                    selectedId={selectedId}
                    onAsk={onAsk}
                    onSelect={(id) => setSelected((s) => ({ ...s, [activeOrg]: id }))}
                    onRetry={onRetry}
                    onStreamed={(id) => patch(activeOrg, id, { stream: false })}
                  />
                }
                graph={({ mode, orientation, minimize, maximize, restore }) => (
                  <PathView
                    path={selectedMsg?.response?.path ?? overview[activeOrg] ?? null}
                    question={selectedMsg?.question ?? null}
                    overview={!selectedMsg && !!overview[activeOrg]}
                    mode={mode}
                    orientation={orientation}
                    onMinimize={minimize}
                    onMaximize={maximize}
                    onRestore={restore}
                  />
                )}
              />
            </main>
          </motion.div>
        ) : (
          <motion.div
            key="landing"
            className="flex flex-1 flex-col"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0, scale: 0.985 }}
            transition={{ duration: 0.3, ease: EASE }}
          >
            <Landing
              orgs={orgs}
              orgsStatus={orgsStatus}
              orgsError={orgsError}
              onRetryOrgs={refreshOrgs}
              onOpen={switchOrg}
              onBuild={build}
              building={building}
              buildError={buildError}
            />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function Landing({
  orgs,
  orgsStatus,
  orgsError,
  onRetryOrgs,
  onOpen,
  onBuild,
  building,
  buildError,
}: {
  orgs: OrgSummary[]
  orgsStatus: "loading" | "ready" | "error"
  orgsError: string | null
  onRetryOrgs: () => void
  onOpen: (org: string) => void
  onBuild: (org: string) => void
  building: { org: string; startedAt: number } | null
  buildError: string | null
}) {
  // Client-only: CommitsGrid uses Math.random(), which would mismatch on hydration.
  const [mounted, setMounted] = useState(false)
  useEffect(() => setMounted(true), [])

  // The whirlwind intro plays once per page load; reduced-motion users get the plain title.
  const reduced = useReducedMotion()
  const firstVisit = useOncePerLoad("intro")
  const playIntro = firstVisit && !reduced
  const [landed, setLanded] = useState(!playIntro)
  const gridRef = useRef<HTMLDivElement>(null)

  const item = (i: number) => ({
    initial: { opacity: 0, y: 18 },
    animate: landed ? { opacity: 1, y: 0 } : { opacity: 0, y: 18 },
    transition: { duration: 0.6, delay: landed ? 0.08 * i : 0, ease: EASE },
  })

  return (
    <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col items-center px-4 py-12 sm:py-16">
      <div ref={gridRef} className="w-full max-w-3xl">
        {mounted ? (
          <CommitsGrid text="HandsOnRepo" className="max-w-3xl" revealed={landed} animateIn={!playIntro} />
        ) : (
          <div className="aspect-[67/9] w-full rounded-[15px] border bg-card" />
        )}
      </div>
      {mounted && playIntro && <IntroWhirlwind gridRef={gridRef} onLanded={() => setLanded(true)} />}

      <motion.h1 {...item(0)} className="mt-8 text-center text-2xl font-semibold tracking-tight text-balance sm:text-3xl">
        Your GitHub org&apos;s company brain, as a graph
      </motion.h1>
      <motion.p {...item(1)} className="mt-3 max-w-xl text-center text-sm text-muted-foreground text-pretty sm:text-base">
        Type the name of any public GitHub organisation. HandsOnRepo builds its brain inside FalkorDB, then you chat with
        an agent about it. Every answer shows its sources, its path through the graph and the Cypher it ran.
      </motion.p>

      <motion.div {...item(2)} className="mt-8 w-full max-w-xl space-y-3">
        <BuildBrainForm onBuild={onBuild} building={!!building} size="lg" />
        <p className="text-center text-xs text-muted-foreground">
          Works for any <b>public</b> GitHub organisation or user account. Type the name (<span className="font-mono">FalkorDB</span>) or paste the
          URL (<span className="font-mono">https://github.com/FalkorDB</span>); we take it from there. Building reads public repos, PRs, issues
          and comments and takes 2 to 5 minutes.
        </p>
        <AnimatePresence initial={false}>
          {building && (
            <motion.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
              <BuildProgress org={building.org} startedAt={building.startedAt} />
            </motion.div>
          )}
        </AnimatePresence>
        {buildError && <Banner tone="error">{buildError}</Banner>}
      </motion.div>

      <motion.section {...item(3)} className="mt-14 w-full">
        <h2 className="mb-2 text-center text-xs font-medium tracking-wide text-muted-foreground uppercase">Existing brains</h2>
        {orgsStatus === "loading" && <div className="mx-auto h-[180px] w-[280px] animate-pulse rounded-2xl border bg-card/40" />}
        {orgsStatus === "error" && (
          <Banner tone="error">
            Couldn&apos;t load organisations: {orgsError}{" "}
            <button onClick={onRetryOrgs} className="underline underline-offset-2">
              Retry
            </button>
          </Banner>
        )}
        {orgsStatus === "ready" && orgs.length === 0 && (
          <p className="mx-auto max-w-xl rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">
            No brains yet. Build one above.
          </p>
        )}
        {orgsStatus === "ready" && <BrainCarousel orgs={orgs} onOpen={onOpen} disabled={!!building} />}
      </motion.section>
    </main>
  )
}

function Banner({
  tone,
  children,
  onClose,
}: {
  tone: "ok" | "error"
  children: React.ReactNode
  onClose?: () => void
}) {
  const ok = tone === "ok"
  return (
    <div
      role={ok ? "status" : "alert"}
      className={
        ok
          ? "flex items-center gap-2 rounded-xl border border-[#48d55d]/40 bg-[#48d55d]/10 px-4 py-2.5 text-sm"
          : "flex items-center gap-2 rounded-xl border border-destructive/40 bg-destructive/10 px-4 py-2.5 text-sm"
      }
    >
      {ok ? <CircleCheck className="size-4 shrink-0 text-[#48d55d]" /> : <TriangleAlert className="size-4 shrink-0 text-destructive" />}
      <span className="flex-1">{children}</span>
      {onClose && (
        <button onClick={onClose} className="text-muted-foreground hover:text-foreground" aria-label="Dismiss">
          <X className="size-4" />
        </button>
      )}
    </div>
  )
}
