"use client"

// Sign in with GitHub. The backend runs the OAuth flow (/auth/github/login -> GitHub "Allow" -> /auth/github/callback)
// and redirects back here with ?session=<id>&login=<login>. We keep the session id in localStorage and send it as a
// Bearer token on every /api call, so GitHub reads (ingest, sync) run with the signed-in user's token.
import { useCallback, useEffect, useState } from "react"

const KEY = "hor_session"

export type Me = {
  signed_in: boolean
  login?: string
  name?: string | null
  avatar?: string | null
  oauth_enabled?: boolean
  server_token?: boolean
}

export function getSession(): string | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage.getItem(KEY)
  } catch {
    return null
  }
}

export function authHeaders(): Record<string, string> {
  const s = getSession()
  return s ? { Authorization: `Bearer ${s}` } : {}
}

/** Pick up ?session= from the OAuth redirect, store it, and clean the URL. Returns the login if one arrived. */
export function absorbSessionFromUrl(): { login?: string; error?: string } | null {
  if (typeof window === "undefined") return null
  const url = new URL(window.location.href)
  const session = url.searchParams.get("session")
  const login = url.searchParams.get("login") ?? undefined
  const error = url.searchParams.get("auth_error") ?? undefined
  if (!session && !error) return null
  if (session) {
    try {
      window.localStorage.setItem(KEY, session)
    } catch {}
  }
  for (const k of ["session", "login", "auth_error"]) url.searchParams.delete(k)
  window.history.replaceState({}, "", url.pathname + (url.search ? url.search : "") + url.hash)
  return { login, error }
}

export async function fetchMe(): Promise<Me> {
  const res = await fetch("/auth/me", { headers: authHeaders(), cache: "no-store" })
  if (!res.ok) throw new Error(`${res.status}`)
  return res.json()
}

export async function signOut(): Promise<void> {
  try {
    await fetch("/auth/logout", { method: "POST", headers: authHeaders() })
  } catch {}
  try {
    window.localStorage.removeItem(KEY)
  } catch {}
}

export const SIGN_IN_PATH = "/auth/github/login"

export function useGitHubAuth() {
  const [me, setMe] = useState<Me | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      setMe(await fetchMe())
    } catch {
      setMe({ signed_in: false, oauth_enabled: false })
    }
  }, [])

  useEffect(() => {
    const absorbed = absorbSessionFromUrl()
    if (absorbed?.error) setError(absorbed.error)
    refresh()
  }, [refresh])

  const logout = useCallback(async () => {
    await signOut()
    await refresh()
  }, [refresh])

  return { me, error, refresh, logout }
}
