"""Ingest a public GitHub organisation into FalkorDB graph `brain_<org>`.

Usage:  python -m app.ingest FalkorDB [--repos repoA,repoB] [--no-clone]

Sources
  * git clone (bare, blob-less)  -> Commit, File, AUTHORED, TOUCHED, REVERTS       (no token needed)
  * GitHub GraphQL (token)        -> PR, Issue, Release, REVIEWED, CLOSES, PART_OF, email->login map
  * GitHub REST (no token)        -> limited PR/Issue basics, 60 requests/hour
Idempotent: everything is MERGEd on a stable `key`; re-running updates in place.
"""
from __future__ import annotations

import argparse
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
import subprocess
import sys
import time
from typing import Callable, Iterable

from falkordb import Graph

from . import config, github_client as gh, schema
from .db import graph_for, normalize_org

log = logging.getLogger("ingest")
Progress = Callable[[str], None]

BATCH = 400
_MENTION_RE = re.compile(r"(?<![\w/])#(\d{1,6})\b")
_REVERT_PR_RE = re.compile(r"Reverts\s+[\w.-]+/[\w.-]+#(\d+)", re.I)
_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def _chunks(items: list, n: int = BATCH) -> Iterable[list]:
    for i in range(0, len(items), n):
        yield items[i:i + n]


def person_key(login: str | None, email: str | None = None, name: str | None = None) -> str:
    if login:
        return login.lower()
    if email:
        return f"email:{email.lower()}"
    return "name:" + _SLUG_RE.sub("-", (name or "unknown").lower()).strip("-")


def _short(text: str | None, n: int = 2000) -> str:
    return (text or "")[:n]


# ---------------------------------------------------------------- writers

def write_repo(g: Graph, repo: dict) -> None:
    g.query(
        """MERGE (r:Repo {key:$key}) SET r.name=$name, r.full_name=$key, r.url=$url, r.description=$desc,
           r.stars=$stars, r.pushed_at=$pushed, r.default_branch=$branch, r.language=$lang""",
        {"key": repo["nameWithOwner"], "name": repo["name"], "url": repo["url"], "desc": _short(repo.get("description"), 500),
         "stars": repo.get("stargazerCount") or 0, "pushed": repo.get("pushedAt"),
         "branch": (repo.get("defaultBranchRef") or {}).get("name") or "main",
         "lang": (repo.get("primaryLanguage") or {}).get("name") if repo.get("primaryLanguage") else None},
    )


def write_people(g: Graph, people: list[dict]) -> None:
    for batch in _chunks(people):
        g.query(
            """UNWIND $rows AS r
               MERGE (p:Person {key:r.key})
               SET p.login = coalesce(r.login, p.login), p.name = coalesce(r.name, p.name),
                   p.url = coalesce(r.url, p.url), p.avatar = coalesce(r.avatar, p.avatar),
                   p.resolved = coalesce(r.resolved, p.resolved, false)""",
            {"rows": batch},
        )


def write_commits(g: Graph, repo_key: str, repo_url: str, commits: list[gh.GitCommit], emap: dict[str, dict]) -> dict[str, int]:
    people: dict[str, dict] = {}
    crows, frows, rrows = [], [], []
    for c in commits:
        resolved = emap.get(c.author_email)
        login = resolved["login"] if resolved else None
        pk = person_key(login, c.author_email, c.author_name)
        people[pk] = {"key": pk, "login": login, "name": (resolved or {}).get("name") or c.author_name,
                      "url": f"https://github.com/{login}" if login else None, "avatar": None, "resolved": bool(login)}
        crows.append({"key": c.sha, "sha": c.sha, "repo": repo_key, "message": _short(c.message, 1000),
                      "date": c.date, "url": f"{repo_url}/commit/{c.sha}", "pkey": pk, "is_merge": len(c.parents) > 1})
        for path, add, dele in c.files:
            frows.append({"ckey": c.sha, "fkey": f"{repo_key}:{path}", "path": path, "repo": repo_key, "dir": path.rsplit("/", 1)[0] if "/" in path else "",
                          "url": f"{repo_url}/blob/HEAD/{path}", "add": add, "del": dele})
        rs = gh.reverted_sha(c.message)
        if rs:
            rrows.append({"ckey": c.sha, "target": rs})

    write_people(g, list(people.values()))
    for batch in _chunks(crows):
        g.query(
            """UNWIND $rows AS r
               MATCH (repo:Repo {key:r.repo})
               MERGE (c:Commit {key:r.key})
               SET c.sha=r.sha, c.repo=r.repo, c.message=r.message, c.date=r.date, c.url=r.url, c.is_merge=r.is_merge
               MERGE (c)-[:IN_REPO]->(repo)
               WITH c, r
               MATCH (p:Person {key:r.pkey})
               MERGE (p)-[:AUTHORED]->(c)""",
            {"rows": batch},
        )
    for batch in _chunks(frows):
        g.query(
            """UNWIND $rows AS r
               MATCH (c:Commit {key:r.ckey}), (repo:Repo {key:r.repo})
               MERGE (f:File {key:r.fkey})
               ON CREATE SET f.path=r.path, f.repo=r.repo, f.url=r.url, f.dir=r.dir
               MERGE (f)-[:IN_REPO]->(repo)
               MERGE (c)-[t:TOUCHED]->(f)
               SET t.additions=r.add, t.deletions=r.del""",
            {"rows": batch},
        )
    reverts = 0
    for batch in _chunks(rrows):
        res = g.query(
            """UNWIND $rows AS r
               MATCH (c:Commit {key:r.ckey})
               MATCH (t:Commit) WHERE t.sha STARTS WITH r.target
               MERGE (c)-[:REVERTS]->(t)
               RETURN count(*)""",
            {"rows": batch},
        )
        reverts += int(res.result_set[0][0]) if res.result_set else 0
    return {"commits": len(crows), "file_touches": len(frows), "reverts": reverts}


def write_comments(g: Graph, repo_key: str, rows: list[dict]) -> int:
    """Comment nodes (issue/PR comments and review bodies) -> (Person)-[:AUTHORED]->(Comment)-[:ON]->(PR|Issue)."""
    rows = [r for r in rows if r.get("key") and (r.get("body") or "").strip()]
    for batch in _chunks(rows):
        g.query(
            """UNWIND $rows AS r
               MATCH (t) WHERE (t:PR OR t:Issue) AND t.key = r.target
               MERGE (c:Comment {key:r.key})
               SET c.body=r.body, c.created_at=r.created_at, c.url=r.url, c.repo=r.repo, c.kind=r.kind, c.state=r.state, c.target=r.target
               MERGE (c)-[:ON]->(t)
               WITH c, r
               OPTIONAL MATCH (p:Person {key:r.pkey})
               FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END | MERGE (p)-[:AUTHORED]->(c))""",
            {"rows": batch},
        )
    return len(rows)


def write_prs(g: Graph, repo_key: str, prs: list[dict]) -> dict[str, int]:
    people: dict[str, dict] = {}
    rows, reviews, files, closes, parts, mentions, revert_prs, comments, merged = [], [], [], [], [], [], [], [], []

    def person(login: str | None) -> str | None:
        if not login:
            return None
        pk = person_key(login)
        people.setdefault(pk, {"key": pk, "login": login, "name": None, "url": f"https://github.com/{login}", "avatar": None, "resolved": True})
        return pk

    for pr in prs:
        n = pr["number"]
        key = f"{repo_key}#{n}"
        author = person((pr.get("author") or {}).get("login"))
        mb = person((pr.get("mergedBy") or {}).get("login"))
        if mb and pr.get("merged"):
            merged.append({"key": key, "pkey": mb, "at": pr.get("mergedAt")})
        for c in (pr.get("comments") or {}).get("nodes") or []:
            if not c:
                continue
            comments.append({"key": c.get("url") or f"{key}/c{len(comments)}", "target": key, "repo": repo_key, "kind": "comment", "state": None,
                             "body": _short(c.get("body"), 1500), "created_at": c.get("createdAt"), "url": c.get("url"), "pkey": person((c.get("author") or {}).get("login"))})
        rows.append({"key": key, "number": n, "repo": repo_key, "title": pr.get("title") or "", "body": _short(pr.get("body")),
                     "state": pr.get("state"), "merged": bool(pr.get("merged")), "url": pr.get("url"),
                     "created_at": pr.get("createdAt"), "merged_at": pr.get("mergedAt"), "closed_at": pr.get("closedAt"),
                     "updated_at": pr.get("updatedAt"), "additions": pr.get("additions"), "deletions": pr.get("deletions"),
                     "base": pr.get("baseRefName"), "merge_sha": (pr.get("mergeCommit") or {}).get("oid"),
                     "labels": [l["name"] for l in ((pr.get("labels") or {}).get("nodes") or []) if l], "pkey": author})
        for rv in (pr.get("reviews") or {}).get("nodes") or []:
            rk = person((rv.get("author") or {}).get("login"))
            if rk:
                reviews.append({"key": key, "pkey": rk, "state": rv.get("state"), "at": rv.get("submittedAt")})
                if (rv.get("body") or "").strip():
                    comments.append({"key": rv.get("url") or f"{key}/r{len(comments)}", "target": key, "repo": repo_key, "kind": "review", "state": rv.get("state"),
                                     "body": _short(rv.get("body"), 1500), "created_at": rv.get("submittedAt"), "url": rv.get("url"), "pkey": rk})
        for f in (pr.get("files") or {}).get("nodes") or []:
            files.append({"key": key, "fkey": f"{repo_key}:{f['path']}", "path": f["path"], "repo": repo_key, "dir": f["path"].rsplit("/", 1)[0] if "/" in f["path"] else "",
                          "url": f"https://github.com/{repo_key}/blob/HEAD/{f['path']}", "add": f.get("additions") or 0, "del": f.get("deletions") or 0})
        for iss in (pr.get("closingIssuesReferences") or {}).get("nodes") or []:
            closes.append({"key": key, "ikey": f"{repo_key}#{iss['number']}", "number": iss["number"], "repo": repo_key})
        for c in (pr.get("commits") or {}).get("nodes") or []:
            oid = ((c or {}).get("commit") or {}).get("oid")
            if oid:
                parts.append({"key": key, "ckey": oid})
        for m in _MENTION_RE.findall(pr.get("body") or ""):
            if int(m) != n:
                mentions.append({"key": key, "ikey": f"{repo_key}#{m}"})
        rm = _REVERT_PR_RE.search(pr.get("body") or "")
        if rm:
            revert_prs.append({"key": key, "tkey": f"{repo_key}#{rm.group(1)}"})

    write_people(g, list(people.values()))
    for batch in _chunks(rows):
        g.query(
            """UNWIND $rows AS r
               MATCH (repo:Repo {key:r.repo})
               MERGE (pr:PR {key:r.key})
               SET pr.number=r.number, pr.repo=r.repo, pr.title=r.title, pr.body=r.body, pr.state=r.state, pr.merged=r.merged,
                   pr.url=r.url, pr.created_at=r.created_at, pr.merged_at=r.merged_at, pr.closed_at=r.closed_at,
                   pr.updated_at=r.updated_at, pr.additions=r.additions, pr.deletions=r.deletions, pr.base=r.base,
                   pr.merge_sha=r.merge_sha, pr.labels=r.labels
               MERGE (pr)-[:IN_REPO]->(repo)
               WITH pr, r
               OPTIONAL MATCH (p:Person {key:r.pkey})
               FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END | MERGE (p)-[:AUTHORED]->(pr))""",
            {"rows": batch},
        )
    for batch in _chunks(reviews):
        g.query(
            """UNWIND $rows AS r
               MATCH (pr:PR {key:r.key}), (p:Person {key:r.pkey})
               MERGE (p)-[x:REVIEWED]->(pr) SET x.state=r.state, x.at=r.at""",
            {"rows": batch},
        )
    for batch in _chunks(merged):
        g.query(
            """UNWIND $rows AS r
               MATCH (pr:PR {key:r.key}), (p:Person {key:r.pkey})
               MERGE (p)-[x:MERGED]->(pr) SET x.at=r.at""",
            {"rows": batch},
        )
    for batch in _chunks(files):
        g.query(
            """UNWIND $rows AS r
               MATCH (pr:PR {key:r.key}), (repo:Repo {key:r.repo})
               MERGE (f:File {key:r.fkey})
               ON CREATE SET f.path=r.path, f.repo=r.repo, f.url=r.url, f.dir=r.dir
               MERGE (f)-[:IN_REPO]->(repo)
               MERGE (pr)-[t:TOUCHED]->(f) SET t.additions=r.add, t.deletions=r.del""",
            {"rows": batch},
        )
    for batch in _chunks(closes):
        g.query(
            """UNWIND $rows AS r
               MATCH (pr:PR {key:r.key})
               MERGE (i:Issue {key:r.ikey}) ON CREATE SET i.number=r.number, i.repo=r.repo, i.url='https://github.com/' + r.repo + '/issues/' + toString(r.number), i.title='(not yet fetched)'
               MERGE (pr)-[:CLOSES]->(i)""",
            {"rows": batch},
        )
    for batch in _chunks(parts):
        g.query(
            """UNWIND $rows AS r
               MATCH (pr:PR {key:r.key}), (c:Commit {key:r.ckey})
               MERGE (c)-[:PART_OF]->(pr)""",
            {"rows": batch},
        )
    # link merge commits too
    g.query("""MATCH (pr:PR)-[:IN_REPO]->(:Repo {key:$repo}) WHERE pr.merge_sha IS NOT NULL
               MATCH (c:Commit {key: pr.merge_sha}) MERGE (c)-[:PART_OF]->(pr)""", {"repo": repo_key})
    for batch in _chunks(mentions):
        g.query(
            """UNWIND $rows AS r
               MATCH (pr:PR {key:r.key})
               OPTIONAL MATCH (i:Issue {key:r.ikey})
               OPTIONAL MATCH (p2:PR {key:r.ikey})
               FOREACH (_ IN CASE WHEN i IS NULL THEN [] ELSE [1] END | MERGE (pr)-[:MENTIONS]->(i))
               FOREACH (_ IN CASE WHEN p2 IS NULL OR p2 = pr THEN [] ELSE [1] END | MERGE (pr)-[:MENTIONS]->(p2))""",
            {"rows": batch},
        )
    for batch in _chunks(revert_prs):
        g.query(
            """UNWIND $rows AS r
               MATCH (pr:PR {key:r.key}), (t:PR {key:r.tkey})
               MERGE (pr)-[:REVERTS]->(t)""",
            {"rows": batch},
        )
    n_comments = write_comments(g, repo_key, comments)
    return {"prs": len(rows), "reviews": len(reviews), "pr_files": len(files), "closes": len(closes), "revert_prs": len(revert_prs), "merged_by": len(merged), "pr_comments": n_comments}


def write_issues(g: Graph, repo_key: str, issues: list[dict]) -> dict[str, int]:
    people: dict[str, dict] = {}
    rows, assigned, mentions, comments = [], [], [], []

    def person(login: str | None) -> str | None:
        if not login:
            return None
        pk = person_key(login)
        people.setdefault(pk, {"key": pk, "login": login, "name": None, "url": f"https://github.com/{login}", "avatar": None, "resolved": True})
        return pk

    for it in issues:
        n = it["number"]
        key = f"{repo_key}#{n}"
        for c in (it.get("comments") or {}).get("nodes") or []:
            if not c:
                continue
            comments.append({"key": c.get("url") or f"{key}/c{len(comments)}", "target": key, "repo": repo_key, "kind": "comment", "state": None,
                             "body": _short(c.get("body"), 1500), "created_at": c.get("createdAt"), "url": c.get("url"), "pkey": person((c.get("author") or {}).get("login"))})
        rows.append({"key": key, "number": n, "repo": repo_key, "title": it.get("title") or "", "body": _short(it.get("body")),
                     "state": it.get("state"), "url": it.get("url"), "created_at": it.get("createdAt"), "closed_at": it.get("closedAt"),
                     "updated_at": it.get("updatedAt"), "labels": [l["name"] for l in ((it.get("labels") or {}).get("nodes") or []) if l],
                     "pkey": person((it.get("author") or {}).get("login"))})
        for a in (it.get("assignees") or {}).get("nodes") or []:
            ak = person(a.get("login"))
            if ak:
                assigned.append({"key": key, "pkey": ak})
        for m in _MENTION_RE.findall(it.get("body") or ""):
            if int(m) != n:
                mentions.append({"key": key, "okey": f"{repo_key}#{m}"})

    write_people(g, list(people.values()))
    for batch in _chunks(rows):
        g.query(
            """UNWIND $rows AS r
               MATCH (repo:Repo {key:r.repo})
               MERGE (i:Issue {key:r.key})
               SET i.number=r.number, i.repo=r.repo, i.title=r.title, i.body=r.body, i.state=r.state, i.url=r.url,
                   i.created_at=r.created_at, i.closed_at=r.closed_at, i.updated_at=r.updated_at, i.labels=r.labels
               MERGE (i)-[:IN_REPO]->(repo)
               WITH i, r
               OPTIONAL MATCH (p:Person {key:r.pkey})
               FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END | MERGE (p)-[:AUTHORED]->(i))""",
            {"rows": batch},
        )
    for batch in _chunks(assigned):
        g.query("""UNWIND $rows AS r MATCH (i:Issue {key:r.key}), (p:Person {key:r.pkey}) MERGE (i)-[:ASSIGNED_TO]->(p)""", {"rows": batch})
    for batch in _chunks(mentions):
        g.query(
            """UNWIND $rows AS r
               MATCH (i:Issue {key:r.key})
               OPTIONAL MATCH (o:Issue {key:r.okey})
               OPTIONAL MATCH (p:PR {key:r.okey})
               FOREACH (_ IN CASE WHEN o IS NULL OR o = i THEN [] ELSE [1] END | MERGE (i)-[:MENTIONS]->(o))
               FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END | MERGE (i)-[:MENTIONS]->(p))""",
            {"rows": batch},
        )
    n_comments = write_comments(g, repo_key, comments)
    return {"issues": len(rows), "assigned": len(assigned), "issue_comments": n_comments}


def write_releases(g: Graph, repo_key: str, releases: list[dict]) -> int:
    rows = [{"key": f"{repo_key}@{r['tagName']}", "tag": r["tagName"], "name": r.get("name"), "url": r.get("url"),
             "published_at": r.get("publishedAt"), "repo": repo_key, "pkey": person_key((r.get("author") or {}).get("login")) if (r.get("author") or {}).get("login") else None}
            for r in releases if r and r.get("tagName")]
    people = [{"key": r["pkey"], "login": r["pkey"], "name": None, "url": f"https://github.com/{r['pkey']}", "avatar": None, "resolved": True} for r in rows if r["pkey"]]
    write_people(g, people)
    for batch in _chunks(rows):
        g.query(
            """UNWIND $rows AS r
               MATCH (repo:Repo {key:r.repo})
               MERGE (rel:Release {key:r.key})
               SET rel.tag=r.tag, rel.name=r.name, rel.url=r.url, rel.published_at=r.published_at, rel.repo=r.repo
               MERGE (rel)-[:IN_REPO]->(repo)
               WITH rel, r
               OPTIONAL MATCH (p:Person {key:r.pkey})
               FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END | MERGE (p)-[:AUTHORED]->(rel))""",
            {"rows": batch},
        )
    return len(rows)


def write_docs(g: Graph, repo_key: str, repo_url: str, repo_dir, branch: str) -> int:
    """README and docs/*.md from the bare clone, chunked into Doc nodes (fulltext indexed)."""
    try:
        ls = subprocess.run(["git", "-C", str(repo_dir), "ls-tree", "-r", "--name-only", "HEAD"],
                            capture_output=True, text=True, timeout=120).stdout.splitlines()
    except Exception:
        return 0
    md = [p for p in ls if p.lower().endswith((".md", ".mdx")) and (p.lower().startswith(("readme", "docs/", "doc/")) or p.lower() == "contributing.md" or "/adr" in p.lower())]
    md = md[:25]
    rows = []
    for path in md:
        try:
            text = subprocess.run(["git", "-C", str(repo_dir), "show", f"HEAD:{path}"], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=120).stdout
        except Exception:
            continue
        if not text.strip():
            continue
        for i in range(0, min(len(text), 30000), 1500):
            rows.append({"key": f"{repo_key}:{path}#{i // 1500}", "path": path, "chunk": i // 1500, "text": text[i:i + 1500],
                         "repo": repo_key, "url": f"{repo_url}/blob/{branch}/{path}", "fkey": f"{repo_key}:{path}"})
    for batch in _chunks(rows):
        g.query(
            """UNWIND $rows AS r
               MATCH (repo:Repo {key:r.repo})
               MERGE (d:Doc {key:r.key}) SET d.path=r.path, d.chunk=r.chunk, d.text=r.text, d.repo=r.repo, d.url=r.url
               MERGE (d)-[:IN_REPO]->(repo)
               WITH d, r
               OPTIONAL MATCH (f:File {key:r.fkey})
               FOREACH (_ IN CASE WHEN f IS NULL THEN [] ELSE [1] END | MERGE (d)-[:DOCUMENTS]->(f))""",
            {"rows": batch},
        )
    return len(rows)


# ---------------------------------------------------------------- orchestration

def fetch_repo(repo: dict, progress: Progress, clone: bool = True) -> dict:
    """Network-bound part: clone/log + GitHub API. Safe to run in parallel across repos."""
    t0 = time.time()
    repo_key = repo["nameWithOwner"]
    owner, name = repo_key.split("/", 1)
    data: dict = {"repo": repo, "commits": [], "emap": {}, "repo_dir": None, "prs": [], "issues": [], "releases": []}
    if clone:
        progress(f"[{repo_key}] cloning history")
        data["repo_dir"] = gh.clone_or_update(owner, name)
        data["commits"] = gh.git_history(data["repo_dir"], config.MAX_COMMITS_PER_REPO)
        data["emap"] = gh.email_login_map(owner, name, min(config.MAX_COMMITS_PER_REPO, 300)) if gh.has_token() else {}
        progress(f"[{repo_key}] {len(data['commits'])} commits (t+{int(time.time()-t0)}s)")
    progress(f"[{repo_key}] fetching pull requests (t+{int(time.time()-t0)}s)")
    data["prs"] = list(gh.list_prs(owner, name))
    progress(f"[{repo_key}] fetching issues (t+{int(time.time()-t0)}s)")
    data["issues"] = list(gh.list_issues(owner, name))
    data["releases"] = gh.list_releases(owner, name)
    data["fetch_seconds"] = int(time.time() - t0)
    return data


def write_repo_data(g: Graph, data: dict, progress: Progress) -> dict:
    """Graph-write part: runs serially so MERGEs never race."""
    t0 = time.time()
    repo = data["repo"]
    repo_key = repo["nameWithOwner"]
    repo_url = repo["url"]
    branch = (repo.get("defaultBranchRef") or {}).get("name") or "main"
    stats: dict = {"repo": repo_key}
    write_repo(g, repo)
    if data["repo_dir"] is not None:
        progress(f"[{repo_key}] writing {len(data['commits'])} commits")
        stats.update(write_commits(g, repo_key, repo_url, data["commits"], data["emap"]))
        stats["docs"] = write_docs(g, repo_key, repo_url, data["repo_dir"], branch)
    stats.update(write_prs(g, repo_key, data["prs"]))
    stats.update(write_issues(g, repo_key, data["issues"]))
    stats["releases"] = write_releases(g, repo_key, data["releases"])
    stats["fetch_seconds"] = data.get("fetch_seconds")
    stats["write_seconds"] = int(time.time() - t0)
    return stats


def ingest_repo(g: Graph, org: str, repo: dict, progress: Progress, clone: bool = True) -> dict:
    return write_repo_data(g, fetch_repo(repo, progress, clone), progress)


def finalize(g: Graph, org: str, progress: Progress, only_files: set[str] | None = None) -> dict:
    progress("resolving identities")
    resolved = schema.resolve_identities(g)
    progress("deriving TOUCHES / REVIEWED_FOR / CO_CHANGED / RELEASED_IN")
    derived = schema.derive_edges(g)
    progress("refreshing owner facts")
    facts = schema.refresh_owner_facts(g, only_files)
    schema.set_meta(g, org, {"derived": derived, "facts": facts, "resolved": resolved})
    return {"resolved": resolved, "derived": derived, "facts": facts, **schema.counts(g)}


def ingest_org(org: str, repos_filter: list[str] | None = None, clone: bool = True, progress: Progress = print) -> dict:
    org = normalize_org(org)
    t0 = time.time()
    g = graph_for(org)
    schema.ensure_indexes(g)
    mode = "GraphQL (token)" if gh.has_token() else "REST fallback (no token: no reviews / PR files, 60 req/h)"
    progress(f"ingesting org {org} using {mode}")
    rl = gh.rate_limit()
    if rl:
        progress(f"GitHub GraphQL budget: {rl.get('remaining')}/{rl.get('limit')} points, resets {rl.get('resetAt')}")

    repos = gh.list_repos(org)
    if repos_filter:
        wanted = {r.lower() for r in repos_filter}
        repos = [r for r in repos if r["name"].lower() in wanted]
    progress(f"{len(repos)} repos: {', '.join(r['name'] for r in repos)}")

    members = gh.org_members(org)
    write_people(g, [{"key": person_key(m["login"]), "login": m["login"], "name": m.get("name"), "url": m.get("url"), "avatar": m.get("avatarUrl"), "resolved": True} for m in members if m])

    # Fetch all repos in parallel (network-bound), write serially (MERGE safety).
    per_repo = []
    token = gh.current_token.get()
    def _fetch(repo):
        gh.current_token.set(token)
        return fetch_repo(repo, progress, clone)
    with ThreadPoolExecutor(max_workers=min(config.FETCH_WORKERS, max(1, len(repos)))) as pool:
        futures = {pool.submit(_fetch, repo): repo for repo in repos}
        fetched: dict[str, dict] = {}
        for fut in as_completed(futures):
            repo = futures[fut]
            try:
                fetched[repo["nameWithOwner"]] = fut.result()
            except Exception as e:  # keep going; one bad repo must not kill the run
                log.exception("repo %s fetch failed", repo.get("nameWithOwner"))
                per_repo.append({"repo": repo.get("nameWithOwner"), "error": str(e)})
    for repo in repos:
        data = fetched.get(repo["nameWithOwner"])
        if not data:
            continue
        try:
            per_repo.append(write_repo_data(g, data, progress))
        except Exception as e:
            log.exception("repo %s write failed", repo.get("nameWithOwner"))
            per_repo.append({"repo": repo.get("nameWithOwner"), "error": str(e)})

    summary = finalize(g, org, progress)
    summary["repos"] = per_repo
    summary["seconds"] = int(time.time() - t0)
    progress(f"done in {summary['seconds']}s: {summary['nodes']} nodes, {summary['edges']} edges")
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ingest a GitHub org into FalkorDB")
    ap.add_argument("org")
    ap.add_argument("--repos", help="comma-separated repo names to limit to")
    ap.add_argument("--no-clone", action="store_true", help="skip git history (PRs/issues only)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    summary = ingest_org(args.org, args.repos.split(",") if args.repos else None, clone=not args.no_clone)
    for r in summary["repos"]:
        print("  ", r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
