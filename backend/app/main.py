"""FastAPI backend for HandsOnRepo (the company brain for a GitHub organisation).

Contract (see TEAM_BRIEF.md section 7):
  GET  /api/orgs                      -> [{org, nodes, edges, lastSync}]
  POST /api/ask   {org, question}     -> {answer, sources[], path{nodes,edges}, cypher[], toolCalls[]}
  POST /api/sync  {org}               -> {added, updated, superseded}
Additions (for the "Build brain" button and the demo repo):
  POST /api/ingest {org, repos?}      -> {status:"started"}      (background)
  GET  /api/ingest/{org}/status       -> {state, log[], summary?}
  GET  /api/graph/{org}/overview      -> repos with counts
  POST /webhook/github                -> applies push / pull_request / issues events, triggers the triage action
  GET  /health
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import threading
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import actions, agent, auth, config, github_client as gh, ingest, schema, sync, tools
from .db import client, graph_for, list_org_graphs, normalize_org

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("api")

app = FastAPI(title="HandsOnRepo API (FalkorDB)", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"], allow_credentials=False)
app.include_router(auth.router)


@app.middleware("http")
async def _github_token_middleware(request: Request, call_next):
    """If the caller is signed in with GitHub, GitHub calls in this request use their token."""
    tok = auth.token_from_request(request)
    token_ctx = gh.current_token.set(tok)
    try:
        return await call_next(request)
    finally:
        gh.current_token.reset(token_ctx)


class AskBody(BaseModel):
    org: str
    question: str = Field(min_length=2, max_length=2000)
    history: list[dict[str, Any]] | None = None


class OrgBody(BaseModel):
    org: str
    repos: list[str] | None = None


_ingest_state: dict[str, dict] = {}
_ingest_lock = threading.Lock()


def _mode() -> str:
    return "full (GitHub token: reviews, PR files, issues)" if gh.has_token() else "limited (no GitHub token: commits/files from git, PR and issue basics only, no reviews)"


@app.get("/health")
def health() -> dict:
    try:
        client().list_graphs()
        db = "ok"
    except Exception as e:
        db = f"error: {e}"
    return {"status": "ok", "falkordb": db, "llm": config.LLM_MODEL if config.OPENAI_API_KEY else "missing OPENAI_API_KEY",
            "github": "token" if gh.has_token() else "no token", "github_oauth": auth.enabled(), "action_repo": config.ACTION_REPO}


@app.get("/api/orgs")
def orgs() -> list[dict]:
    out = []
    for org in list_org_graphs():
        meta = schema.get_meta(graph_for(org))
        if meta:
            out.append({"org": meta["org"], "nodes": meta["nodes"], "edges": meta["edges"], "lastSync": meta["lastSync"]})
        # graphs without metadata are still being built (or were abandoned); the ingest status endpoint reports them
    out.sort(key=lambda o: o["nodes"], reverse=True)
    return out


@app.post("/api/ask")
def ask(body: AskBody) -> dict:
    try:
        org = normalize_org(body.org)
    except ValueError as e:
        raise HTTPException(400, str(e))
    g = graph_for(org)
    meta = schema.get_meta(g)
    if not meta:
        raise HTTPException(404, f"no brain for {org}; POST /api/ingest first")
    repos = [r[0] for r in g.ro_query("MATCH (r:Repo) RETURN r.full_name ORDER BY r.stars DESC").result_set]
    try:
        return agent.ask(g, org, body.question, repos, _mode(), body.history)
    except RuntimeError as e:
        raise HTTPException(503, str(e))


@app.post("/api/sync")
def api_sync(body: OrgBody, response: Response) -> dict:
    """Known org: incremental sync (seconds). Unknown org: start a full ingestion in the background and answer 202
    {status:"building"}; the UI polls GET /api/ingest/{org}/status until state == done."""
    try:
        org = normalize_org(body.org)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not schema.get_meta(graph_for(org)):
        with _ingest_lock:
            cur = _ingest_state.get(org)
            if not cur or cur["state"] != "running":
                _ingest_state[org] = {"state": "running", "started": datetime.now(timezone.utc).isoformat(timespec="seconds"), "log": [], "summary": None}
                threading.Thread(target=_run_ingest, args=(org, body.repos, gh.active_token()), daemon=True).start()
        response.status_code = 202
        return {"added": 0, "updated": 0, "superseded": 0, "status": "building", "org": org, "mode": _mode()}
    return sync.sync_org(org, progress=log.info)


def _run_ingest(org: str, repos: list[str] | None, token: str | None = None) -> None:
    st = _ingest_state[org]
    gh.current_token.set(token)  # background thread: carry the signed-in user's token over

    def progress(msg: str) -> None:
        st["log"].append(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {msg}")
        log.info("[ingest %s] %s", org, msg)

    try:
        st["summary"] = ingest.ingest_org(org, repos, clone=True, progress=progress)
        st["state"] = "done"
    except Exception as e:
        log.exception("ingest failed")
        st["state"] = "error"
        st["error"] = str(e)


@app.post("/api/ingest")
def api_ingest(body: OrgBody, request: Request) -> dict:
    try:
        org = normalize_org(body.org)
    except ValueError as e:
        raise HTTPException(400, str(e))
    with _ingest_lock:
        cur = _ingest_state.get(org)
        if cur and cur["state"] == "running":
            return {"status": "already running", "org": org}
        _ingest_state[org] = {"state": "running", "started": datetime.now(timezone.utc).isoformat(timespec="seconds"), "log": [], "summary": None}
    threading.Thread(target=_run_ingest, args=(org, body.repos, gh.active_token()), daemon=True).start()
    return {"status": "started", "org": org, "mode": _mode()}


@app.get("/api/ingest/{org}/status")
def ingest_status(org: str) -> dict:
    st = _ingest_state.get(normalize_org(org))
    if not st:
        meta = schema.get_meta(graph_for(org))
        return {"state": "done" if meta else "none", "log": [], "summary": meta}
    return {k: v for k, v in st.items() if k != "summary"} | {"summary": st.get("summary")}


@app.get("/api/graph/{org}/map")
def org_map(org: str, people: int = 28) -> dict:
    """Overview graph for the UI: repos, most active humans, who contributes where, strongest collaborations."""
    g = graph_for(org)
    if not schema.get_meta(g):
        raise HTTPException(404, "no brain for this org")
    res = tools.org_map(g, people)
    return {"path": res["path"], "stats": res["result"], "cypher": res["cypher"], "ms": res["ms"]}


@app.get("/api/graph/{org}/suggest")
def suggest(org: str, limit: int = 6) -> dict:
    """Example questions generated from this org's own graph (its busiest area, latest revert, top contributor...)."""
    g = graph_for(org)
    if not schema.get_meta(g):
        raise HTTPException(404, "no brain for this org")
    res = tools.suggest_questions(g, limit)
    return {"questions": res["result"]["questions"], "cypher": res["cypher"], "ms": res["ms"]}


@app.get("/api/graph/{org}/overview")
def overview(org: str) -> dict:
    g = graph_for(org)
    if not schema.get_meta(g):
        raise HTTPException(404, "no brain for this org")
    return {"meta": schema.get_meta(g), "repos": tools.repo_overview(g)["result"]}


def _verify_signature(secret: str | None, body: bytes, signature: str | None) -> bool:
    if not secret:
        return True  # no secret configured: accept (dev only)
    if not signature or not signature.startswith("sha256="):
        return False
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature[7:], digest)


@app.post("/webhook/github")
async def github_webhook(request: Request, x_github_event: str = Header(default=""), x_hub_signature_256: str | None = Header(default=None)) -> dict:
    raw = await request.body()
    if not _verify_signature(config.GITHUB_WEBHOOK_SECRET, raw, x_hub_signature_256):
        raise HTTPException(401, "bad signature")
    payload = await request.json()
    event = x_github_event
    if event == "ping":
        return {"pong": True}
    result: dict = {"event": event}
    try:
        result["sync"] = sync.apply_webhook(event, payload, progress=log.info)
    except Exception as e:
        log.exception("webhook sync failed")
        result["sync"] = {"error": str(e)}
    if event == "issues" and payload.get("action") == "opened":
        try:
            result["action"] = actions.handle_issue_opened(payload)
        except Exception as e:
            log.exception("triage action failed")
            result["action"] = {"error": str(e)}
    return result
