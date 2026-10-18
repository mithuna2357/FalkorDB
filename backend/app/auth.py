"""Sign in with GitHub (OAuth web flow). The user clicks "Allow" once; we keep their token server-side in a session
and use it for GitHub calls they trigger (ingest, sync, actions). No personal access token needed by end users.

Setup (one time, by the team): GitHub -> Settings -> Developer settings -> OAuth Apps -> New OAuth App
  Homepage URL:            <FRONTEND_URL>
  Authorization callback:  <BACKEND_URL>/auth/github/callback
Then put GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET / FRONTEND_URL / BACKEND_URL in backend/.env.

Routes
  GET  /auth/github/login            -> 302 to GitHub's Allow screen
  GET  /auth/github/callback         -> exchanges the code, creates a session, 302 to FRONTEND_URL/?session=<id>
  GET  /auth/me                      -> {login, name, avatar, scopes} for the current session (cookie or Bearer)
  POST /auth/logout
Sessions are in memory (fine for the hackathon; a restart signs everyone out).
"""
from __future__ import annotations

import logging
import os
import secrets
import time
from typing import Optional

import requests
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from . import config, github_client as gh

log = logging.getLogger("auth")
router = APIRouter()

GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID") or None
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET") or None
FRONTEND_URL = (os.getenv("FRONTEND_URL") or "http://localhost:3001").rstrip("/")
BACKEND_URL = (os.getenv("BACKEND_URL") or "http://localhost:8000").rstrip("/")
# public_repo lets the signed-in user's token post the triage comment on the demo repo. Leave empty for read-only.
OAUTH_SCOPE = os.getenv("GITHUB_OAUTH_SCOPE", "public_repo")
SESSION_COOKIE = "brain_session"
SESSION_TTL = 12 * 3600

_sessions: dict[str, dict] = {}
_states: dict[str, float] = {}


def enabled() -> bool:
    return bool(GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET)


def session_from_request(request: Request) -> Optional[dict]:
    sid = None
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        sid = auth[7:].strip()
    sid = sid or request.cookies.get(SESSION_COOKIE)
    if not sid:
        return None
    s = _sessions.get(sid)
    if not s:
        return None
    if time.time() - s["created"] > SESSION_TTL:
        _sessions.pop(sid, None)
        return None
    return s


def token_from_request(request: Request) -> Optional[str]:
    s = session_from_request(request)
    return s["token"] if s else None


@router.get("/auth/github/login")
def login(next: str | None = None):
    if not enabled():
        raise HTTPException(503, "GitHub OAuth is not configured (GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET)")
    state = secrets.token_urlsafe(24)
    _states[state] = time.time()
    for k, t in list(_states.items()):
        if time.time() - t > 600:
            _states.pop(k, None)
    params = {
        "client_id": GITHUB_CLIENT_ID,
        "redirect_uri": f"{BACKEND_URL}/auth/github/callback",
        "scope": OAUTH_SCOPE,
        "state": state,
        "allow_signup": "true",
    }
    url = "https://github.com/login/oauth/authorize?" + "&".join(f"{k}={requests.utils.quote(str(v), safe='')}" for k, v in params.items())
    return RedirectResponse(url, status_code=302)


@router.get("/auth/github/callback")
def callback(code: str | None = None, state: str | None = None, error: str | None = None):
    if error or not code or not state or state not in _states:
        return RedirectResponse(f"{FRONTEND_URL}/?auth_error={requests.utils.quote(error or 'invalid_state')}", status_code=302)
    _states.pop(state, None)
    r = requests.post(
        "https://github.com/login/oauth/access_token",
        data={"client_id": GITHUB_CLIENT_ID, "client_secret": GITHUB_CLIENT_SECRET, "code": code, "redirect_uri": f"{BACKEND_URL}/auth/github/callback"},
        headers={"Accept": "application/json"}, timeout=30,
    )
    data = r.json() if r.ok else {}
    token = data.get("access_token")
    if not token:
        log.warning("oauth exchange failed: %s", data)
        return RedirectResponse(f"{FRONTEND_URL}/?auth_error=exchange_failed", status_code=302)
    me = requests.get("https://api.github.com/user", headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}, timeout=30)
    user = me.json() if me.ok else {}
    sid = secrets.token_urlsafe(32)
    _sessions[sid] = {"token": token, "login": user.get("login"), "name": user.get("name"), "avatar": user.get("avatar_url"),
                      "scopes": data.get("scope", ""), "created": time.time()}
    log.info("github login: %s", user.get("login"))
    resp = RedirectResponse(f"{FRONTEND_URL}/?session={sid}&login={requests.utils.quote(user.get('login') or '')}", status_code=302)
    secure = BACKEND_URL.startswith("https://")
    resp.set_cookie(SESSION_COOKIE, sid, max_age=SESSION_TTL, httponly=True, samesite="none" if secure else "lax", secure=secure)
    return resp


@router.get("/auth/me")
def me(request: Request):
    s = session_from_request(request)
    if not s:
        return {"signed_in": False, "oauth_enabled": enabled(), "server_token": gh.has_token()}
    return {"signed_in": True, "login": s["login"], "name": s["name"], "avatar": s["avatar"], "scopes": s["scopes"], "oauth_enabled": True}


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    auth = request.headers.get("authorization") or ""
    sid = auth[7:].strip() if auth.lower().startswith("bearer ") else request.cookies.get(SESSION_COOKIE)
    if sid:
        _sessions.pop(sid, None)
    response.delete_cookie(SESSION_COOKIE)
    return {"signed_out": True}
