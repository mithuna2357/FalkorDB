"use client"

import { LogOut } from "lucide-react"

/** lucide-react 1.x dropped brand icons, so the GitHub mark is inlined. */
function Github({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" aria-hidden className={className ?? "size-4"} fill="currentColor">
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
    </svg>
  )
}
import { Button } from "@/components/ui/button"
import { SIGN_IN_PATH, useGitHubAuth } from "@/lib/auth"

/**
 * Header control: "Sign in with GitHub" (one click, GitHub asks to Allow) or the signed-in user with a sign-out button.
 * While signed in, ingestion and sync use that user's GitHub token, so reviews and PR files come through at full quota.
 */
export function GitHubAuth() {
  const { me, logout } = useGitHubAuth()
  if (!me) return null

  if (me.signed_in) {
    return (
      <div className="flex items-center gap-1.5 rounded-lg border bg-card/60 py-1 pr-1 pl-1.5" title="GitHub calls use your token">
        {me.avatar ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={me.avatar} alt="" className="size-5 rounded-full" />
        ) : (
          <Github className="size-4" />
        )}
        <span className="font-mono text-xs">{me.login}</span>
        <Button variant="ghost" size="icon" className="size-6" onClick={logout} aria-label="Sign out of GitHub" title="Sign out">
          <LogOut className="size-3.5" />
        </Button>
      </div>
    )
  }

  const enabled = me.oauth_enabled !== false
  return (
    <Button
      asChild={enabled}
      variant="outline"
      size="sm"
      disabled={!enabled}
      title={enabled ? "GitHub asks you to Allow once; no tokens to paste" : "GitHub sign-in is not configured on the backend"}
    >
      {enabled ? (
        <a href={SIGN_IN_PATH}>
          <Github /> Sign in with GitHub
        </a>
      ) : (
        <span>
          <Github /> Sign in with GitHub
        </span>
      )}
    </Button>
  )
}
