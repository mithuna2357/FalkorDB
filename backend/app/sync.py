"""Incremental sync: pull recent changes for an org, or apply a single GitHub webhook event.
Returns {added, updated, superseded} per the API contract.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from falkordb import Graph

from . import config, github_client as gh, ingest, schema
from .db import graph_for, normalize_org

log = logging.getLogger("sync")


def _touched_files_since(g: Graph, since_iso: str) -> set[str]:
    rows = g.ro_query("MATCH (c:Commit)-[:TOUCHED]->(f:File) WHERE c.date >= $since RETURN DISTINCT f.key", {"since": since_iso}).result_set
    return {r[0] for r in rows}


def sync_org(org: str, progress=print) -> dict:
    """Re-pull recent commits / PRs / issues for every repo already in the graph, then refresh derived data."""
    org = normalize_org(org)
    g = graph_for(org)
    meta = schema.get_meta(g) or {}
    before = schema.counts(g)
    since = meta.get("lastSync") or "1970-01-01T00:00:00+00:00"
    t0 = time.time()

    # Temporarily lower limits so a sync is fast: only recent history is needed.
    saved = (config.MAX_COMMITS_PER_REPO, config.MAX_PRS_PER_REPO, config.MAX_ISSUES_PER_REPO)
    config.MAX_COMMITS_PER_REPO, config.MAX_PRS_PER_REPO, config.MAX_ISSUES_PER_REPO = 60, 40, 40
    try:
        repos = gh.list_repos(org)
        known = {r[0] for r in g.ro_query("MATCH (r:Repo) RETURN r.key").result_set}
        repos = [r for r in repos if r["nameWithOwner"] in known] or repos[: config.MAX_REPOS]
        for repo in repos:
            try:
                ingest.ingest_repo(g, org, repo, progress, clone=True)
            except Exception as e:
                log.exception("sync repo %s failed", repo.get("nameWithOwner"))
    finally:
        config.MAX_COMMITS_PER_REPO, config.MAX_PRS_PER_REPO, config.MAX_ISSUES_PER_REPO = saved

    updated_rows = g.ro_query("MATCH (n) WHERE (n:PR OR n:Issue) AND n.updated_at > $since RETURN count(n)", {"since": since}).result_set
    changed_files = _touched_files_since(g, since)
    summary = ingest.finalize(g, org, progress, only_files=changed_files or None)
    after = schema.counts(g)
    return {
        "added": max(0, after["nodes"] - before["nodes"]),
        "updated": int(updated_rows[0][0]) if updated_rows else 0,
        "superseded": summary["facts"].get("superseded", 0),
        "nodes": after["nodes"], "edges": after["edges"], "seconds": int(time.time() - t0),
        "lastSync": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def apply_webhook(event: str, payload: dict, progress=print) -> dict:
    """Apply one GitHub webhook event to the matching org graph. Supported: push, pull_request, issues."""
    repo = payload.get("repository") or {}
    full = repo.get("full_name")
    if not full:
        return {"ignored": "no repository in payload"}
    owner, name = full.split("/", 1)
    org = normalize_org(owner)
    g = graph_for(org)
    schema.ensure_indexes(g)
    repo_info = {
        "name": name, "nameWithOwner": full, "url": repo.get("html_url"), "description": repo.get("description"),
        "stargazerCount": repo.get("stargazers_count", 0), "pushedAt": repo.get("pushed_at"),
        "defaultBranchRef": {"name": repo.get("default_branch", "main")},
        "primaryLanguage": {"name": repo.get("language")} if repo.get("language") else None,
    }
    before = schema.counts(g)
    ingest.write_repo(g, repo_info)
    touched: set[str] = set()

    if event == "push":
        progress(f"[webhook push] {full}: {len(payload.get('commits', []))} commits")
        repo_dir = gh.clone_or_update(owner, name)
        commits = gh.git_history(repo_dir, 50)
        emap = gh.email_login_map(owner, name, 100) if gh.has_token() else {}
        # the pusher's login is a strong hint for the HEAD commit author
        pusher = (payload.get("sender") or {}).get("login")
        for c in payload.get("commits", []):
            email = ((c.get("author") or {}).get("email") or "").lower()
            login = (c.get("author") or {}).get("username") or pusher
            if email and login and email not in emap:
                emap[email] = {"login": login, "name": (c.get("author") or {}).get("name")}
        ingest.write_commits(g, full, repo_info["url"], commits, emap)
        ingest.write_docs(g, full, repo_info["url"], repo_dir, repo_info["defaultBranchRef"]["name"])
        for c in commits[:50]:
            for path, _, _ in c.files:
                touched.add(f"{full}:{path}")
    elif event == "pull_request":
        pr = payload.get("pull_request") or {}
        progress(f"[webhook pull_request] {full}#{pr.get('number')} {payload.get('action')}")
        prs = [p for p in gh.list_prs(owner, name) if p["number"] == pr.get("number")] if gh.has_token() else []
        if not prs:  # build from payload
            prs = [{
                "number": pr["number"], "title": pr.get("title"), "url": pr.get("html_url"), "body": pr.get("body"),
                "state": "MERGED" if pr.get("merged_at") else str(pr.get("state", "open")).upper(), "merged": bool(pr.get("merged_at")),
                "createdAt": pr.get("created_at"), "mergedAt": pr.get("merged_at"), "closedAt": pr.get("closed_at"), "updatedAt": pr.get("updated_at"),
                "additions": pr.get("additions"), "deletions": pr.get("deletions"), "baseRefName": (pr.get("base") or {}).get("ref"),
                "author": {"login": (pr.get("user") or {}).get("login")}, "mergeCommit": {"oid": pr.get("merge_commit_sha")} if pr.get("merge_commit_sha") else None,
                "labels": {"nodes": [{"name": l["name"]} for l in pr.get("labels", [])]},
                "files": {"nodes": []}, "reviews": {"nodes": []}, "commits": {"nodes": []}, "closingIssuesReferences": {"nodes": []},
            }]
        ingest.write_prs(g, full, prs)
    elif event == "issues":
        iss = payload.get("issue") or {}
        progress(f"[webhook issues] {full}#{iss.get('number')} {payload.get('action')}")
        ingest.write_issues(g, full, [{
            "number": iss["number"], "title": iss.get("title"), "url": iss.get("html_url"), "body": iss.get("body"),
            "state": str(iss.get("state", "open")).upper(), "createdAt": iss.get("created_at"), "closedAt": iss.get("closed_at"), "updatedAt": iss.get("updated_at"),
            "author": {"login": (iss.get("user") or {}).get("login")},
            "labels": {"nodes": [{"name": l["name"]} for l in iss.get("labels", [])]},
            "assignees": {"nodes": [{"login": a["login"]} for a in iss.get("assignees", [])]},
        }])
    else:
        return {"ignored": f"event {event} not handled"}

    summary = ingest.finalize(g, org, progress, only_files=touched or None)
    after = schema.counts(g)
    return {"org": org, "event": event, "added": max(0, after["nodes"] - before["nodes"]), "updated": 1,
            "superseded": summary["facts"].get("superseded", 0), "nodes": after["nodes"], "edges": after["edges"]}
