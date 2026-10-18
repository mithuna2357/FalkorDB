"use client"

import { Brain, Loader2, RefreshCw } from "lucide-react"
import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { cn } from "@/lib/utils"
import type { OrgSummary, SyncResponse } from "@/lib/types"

export const fmt = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 })

export function timeAgo(iso: string) {
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return iso
  const s = Math.round((Date.now() - t) / 1000)
  if (s < 0) return new Date(iso).toLocaleDateString()
  if (s < 60) return "just now"
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

export function OrgSwitcher({
  orgs,
  value,
  onChange,
  disabled,
}: {
  orgs: OrgSummary[]
  value: string | null
  onChange: (org: string) => void
  disabled?: boolean
}) {
  return (
    <Select value={value ?? undefined} onValueChange={onChange} disabled={disabled || orgs.length === 0}>
      <SelectTrigger className="w-auto min-w-[180px] font-mono" aria-label="Switch organisation">
        <SelectValue placeholder="Select org" />
      </SelectTrigger>
      <SelectContent>
        {orgs.map((o) => (
          <SelectItem key={o.org} value={o.org} className="font-mono">
            <span>{o.org}</span>
            <span className="ml-auto pl-3 text-xs text-muted-foreground">
              {fmt.format(o.nodes)} n · {fmt.format(o.edges)} e
            </span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

const ORG_RE = /^[a-z\d](?:[a-z\d-]{0,38})$/i

export function BuildBrainForm({
  onBuild,
  building,
  size = "default",
  className,
}: {
  onBuild: (org: string) => void
  building: boolean
  size?: "default" | "lg"
  className?: string
}) {
  const [org, setOrg] = useState("")
  const clean = org.trim().replace(/^https?:\/\/github\.com\//i, "").replace(/\/.*$/, "")
  const valid = ORG_RE.test(clean)

  return (
    <form
      className={cn("flex w-full gap-2", className)}
      onSubmit={(e) => {
        e.preventDefault()
        if (valid && !building) {
          onBuild(clean)
          setOrg("")
        }
      }}
    >
      <Input
        value={org}
        onChange={(e) => setOrg(e.target.value)}
        placeholder="GitHub organisation, e.g. FalkorDB"
        aria-label="GitHub organisation"
        className={cn("font-mono", size === "lg" && "h-11 text-base")}
        disabled={building}
      />
      <Button
        type="submit"
        disabled={!valid || building}
        size={size === "lg" ? "lg" : "default"}
        className={cn("bg-[#48d55d] text-black hover:bg-[#48d55d]/85", size === "lg" && "h-11")}
      >
        {building ? <Loader2 className="animate-spin" /> : <Brain />}
        Build brain
      </Button>
    </form>
  )
}

export function BuildProgress({ org, startedAt }: { org: string; startedAt: number }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])
  const secs = Math.max(0, Math.floor((now - startedAt) / 1000))
  const elapsed = `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`

  return (
    <div className="w-full rounded-xl border bg-card/60 p-4" role="status" aria-live="polite">
      <div className="flex items-center justify-between gap-3 text-sm">
        <span className="flex items-center gap-2">
          <Loader2 className="size-4 animate-spin text-[#48d55d]" />
          Building brain for <span className="font-mono font-semibold">{org}</span>
        </span>
        <span className="font-mono text-xs text-muted-foreground tabular-nums">{elapsed}</span>
      </div>
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-secondary">
        <div className="progress-indeterminate h-full w-1/3 rounded-full bg-[#48d55d]" />
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        Ingesting repos, issues, PRs and commits into FalkorDB. This can take a few minutes.
      </p>
    </div>
  )
}

export function SyncButton({
  onSync,
  syncing,
  result,
  disabled,
}: {
  onSync: () => void
  syncing: boolean
  result: SyncResponse | null
  disabled?: boolean
}) {
  return (
    <div className="flex items-center gap-2">
      <Button variant="outline" onClick={onSync} disabled={syncing || disabled} aria-label="Sync now" title="Sync now">
        <RefreshCw className={cn(syncing && "animate-spin")} />
        <span className="hidden sm:inline">{syncing ? "Syncing…" : "Sync now"}</span>
      </Button>
      {result && !syncing && (
        <span className="hidden sm:inline">
          <SyncCounts result={result} />
        </span>
      )}
    </div>
  )
}

export function SyncCounts({ result }: { result: SyncResponse }) {
  return (
    <span className="flex items-center gap-2 font-mono text-xs whitespace-nowrap" aria-live="polite">
      <span className="text-[#48d55d]">+{result.added} added</span>
      <span className="text-sky-300">{result.updated} updated</span>
      <span className="text-red-400">{result.superseded} superseded</span>
    </span>
  )
}
