"""GitHub access: GraphQL (token required) with a limited REST fallback, plus git clone for commit history."""
from __future__ import annotations

import contextvars
import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import requests

from . import config

log = logging.getLogger("github")

GQL_URL = "https://api.github.com/graphql"
REST_URL = "https://api.github.com"


# A signed-in user's OAuth token for the current request/thread; falls back to the server token from .env.
current_token: contextvars.ContextVar[str | None] = contextvars.ContextVar("github_token", default=None)


def active_token() -> str | None:
    return current_token.get() or config.GITHUB_TOKEN


def _headers() -> dict[str, str]:
    h = {"Accept": "application/vnd.github+json", "User-Agent": "orbyte-brain"}
    tok = active_token()
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def has_token() -> bool:
    return bool(active_token())


# ---------------------------------------------------------------- GraphQL

def gql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    if not active_token():
        raise RuntimeError("a GitHub token is required for GraphQL (sign in with GitHub or set GITHUB_TOKEN)")
    for attempt in range(6):
        r = requests.post(GQL_URL, json={"query": query, "variables": variables}, headers=_headers(), timeout=60)
        body_l = r.text.lower() if r.status_code != 200 else ""
        rate_limited = (r.status_code in (403, 429) and "rate limit" in body_l) or (r.status_code == 200 and '"type": "rate_limited"' in r.text.lower().replace(" ", ""))
        if r.status_code in (502, 503, 504) or rate_limited:
            reset = r.headers.get("X-RateLimit-Reset")
            if rate_limited and reset and reset.isdigit():
                wait = max(5, min(3600, int(reset) - int(time.time()) + 2))
                log.warning("GitHub rate limit hit; sleeping %ss until reset", wait)
            else:
                wait = 2 ** attempt * 5
                log.warning("GraphQL %s, retrying in %ss", r.status_code, wait)
            time.sleep(wait)
            continue
        r.raise_for_status()
        data = r.json()
        if "errors" in data and not data.get("data"):
            raise RuntimeError(f"GraphQL errors: {data['errors']}")
        if "errors" in data:
            log.warning("GraphQL partial errors: %s", data["errors"][:2])
        return data["data"]
    raise RuntimeError("GraphQL failed after retries")


REPOS_Q = """
query($org:String!, $after:String) {
  repositoryOwner(login:$org) {
    repositories(first:50, after:$after, orderBy:{field:PUSHED_AT, direction:DESC}, isFork:false, ownerAffiliations:[OWNER]) {
      pageInfo { hasNextPage endCursor }
      nodes { name nameWithOwner url description stargazerCount pushedAt isArchived
              defaultBranchRef { name } primaryLanguage { name } }
    }
  }
}"""

PRS_Q = """
query($owner:String!, $name:String!, $after:String) {
  repository(owner:$owner, name:$name) {
    pullRequests(first:20, after:$after, orderBy:{field:UPDATED_AT, direction:DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title url body state merged createdAt mergedAt closedAt updatedAt additions deletions baseRefName
        author { login }
        mergedBy { login }
        mergeCommit { oid }
        labels(first:10) { nodes { name } }
        files(first:50) { nodes { path additions deletions } }
        reviews(first:25) { nodes { author { login } state submittedAt body url } }
        commits(first:30) { nodes { commit { oid author { email name user { login } } } } }
        closingIssuesReferences(first:10) { nodes { number } }
        comments(first:20) { nodes { author { login } body createdAt url } }
      }
    }
  }
}"""

ISSUES_Q = """
query($owner:String!, $name:String!, $after:String) {
  repository(owner:$owner, name:$name) {
    issues(first:50, after:$after, orderBy:{field:UPDATED_AT, direction:DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes { number title url body state createdAt closedAt updatedAt
              author { login } labels(first:10) { nodes { name } }
              assignees(first:5) { nodes { login } }
              comments(first:20) { nodes { author { login } body createdAt url } } }
    }
  }
}"""

RELEASES_Q = """
query($owner:String!, $name:String!) {
  repository(owner:$owner, name:$name) {
    releases(first:30, orderBy:{field:CREATED_AT, direction:DESC}) {
      nodes { tagName name url publishedAt author { login } }
    }
  }
}"""

HISTORY_Q = """
query($owner:String!, $name:String!, $after:String) {
  repository(owner:$owner, name:$name) {
    defaultBranchRef { target { ... on Commit {
      history(first:100, after:$after) {
        pageInfo { hasNextPage endCursor }
        nodes { oid author { email name user { login } } }
      } } } }
  }
}"""

USERS_Q = """
query($org:String!) {
  organization(login:$org) { membersWithRole(first:100) { nodes { login name url avatarUrl } } }
}"""


def _paginate(query: str, variables: dict, path: list[str], limit: int) -> Iterator[dict]:
    after = None
    seen = 0
    while True:
        data = gql(query, {**variables, "after": after})
        node: Any = data
        for p in path:
            node = (node or {}).get(p)
        if not node:
            return
        for item in node.get("nodes") or []:
            if item is None:
                continue
            yield item
            seen += 1
            if seen >= limit:
                return
        pi = node.get("pageInfo") or {}
        if not pi.get("hasNextPage"):
            return
        after = pi["endCursor"]


def list_repos(org: str) -> list[dict]:
    if has_token():
        repos = list(_paginate(REPOS_Q, {"org": org}, ["repositoryOwner", "repositories"], 200))
        repos = [r for r in repos if not r.get("isArchived")]
        repos.sort(key=lambda r: (r.get("stargazerCount") or 0), reverse=True)
        return repos[: config.MAX_REPOS]
    # REST fallback (60 req/h unauthenticated)
    r = requests.get(f"{REST_URL}/users/{org}/repos", params={"per_page": 100, "type": "owner", "sort": "pushed"}, headers=_headers(), timeout=60)
    r.raise_for_status()
    repos = [x for x in r.json() if not x.get("fork") and not x.get("archived")]
    repos.sort(key=lambda r: r.get("stargazers_count", 0), reverse=True)
    out = []
    for x in repos[: config.MAX_REPOS]:
        out.append({
            "name": x["name"], "nameWithOwner": x["full_name"], "url": x["html_url"],
            "description": x.get("description"), "stargazerCount": x.get("stargazers_count", 0),
            "pushedAt": x.get("pushed_at"), "defaultBranchRef": {"name": x.get("default_branch", "main")},
            "primaryLanguage": {"name": x.get("language")} if x.get("language") else None,
        })
    return out


def list_prs(owner: str, name: str) -> Iterator[dict]:
    if has_token():
        yield from _paginate(PRS_Q, {"owner": owner, "name": name}, ["repository", "pullRequests"], config.MAX_PRS_PER_REPO)
        return
    # REST fallback: basic PR info only (no files/reviews)
    page = 1
    seen = 0
    while seen < config.MAX_PRS_PER_REPO:
        r = requests.get(f"{REST_URL}/repos/{owner}/{name}/pulls", params={"state": "all", "per_page": 100, "page": page, "sort": "updated", "direction": "desc"}, headers=_headers(), timeout=60)
        if r.status_code == 403:
            log.warning("REST rate limited; stopping PR fetch for %s/%s", owner, name)
            return
        r.raise_for_status()
        items = r.json()
        if not items:
            return
        for x in items:
            yield {
                "number": x["number"], "title": x["title"], "url": x["html_url"], "body": x.get("body"),
                "state": "MERGED" if x.get("merged_at") else x["state"].upper(), "merged": bool(x.get("merged_at")),
                "createdAt": x["created_at"], "mergedAt": x.get("merged_at"), "closedAt": x.get("closed_at"), "updatedAt": x["updated_at"],
                "additions": None, "deletions": None, "baseRefName": (x.get("base") or {}).get("ref"),
                "author": {"login": (x.get("user") or {}).get("login")}, "mergeCommit": {"oid": x.get("merge_commit_sha")} if x.get("merge_commit_sha") else None,
                "labels": {"nodes": [{"name": l["name"]} for l in x.get("labels", [])]},
                "files": {"nodes": []}, "reviews": {"nodes": []}, "commits": {"nodes": []},
                "closingIssuesReferences": {"nodes": []}, "comments": {"nodes": []}, "mergedBy": None,
            }
            seen += 1
        page += 1


def list_issues(owner: str, name: str) -> Iterator[dict]:
    if has_token():
        yield from _paginate(ISSUES_Q, {"owner": owner, "name": name}, ["repository", "issues"], config.MAX_ISSUES_PER_REPO)
        return
    page = 1
    seen = 0
    while seen < config.MAX_ISSUES_PER_REPO:
        r = requests.get(f"{REST_URL}/repos/{owner}/{name}/issues", params={"state": "all", "per_page": 100, "page": page, "sort": "updated"}, headers=_headers(), timeout=60)
        if r.status_code == 403:
            log.warning("REST rate limited; stopping issue fetch for %s/%s", owner, name)
            return
        r.raise_for_status()
        items = r.json()
        if not items:
            return
        for x in items:
            if "pull_request" in x:
                continue
            yield {
                "number": x["number"], "title": x["title"], "url": x["html_url"], "body": x.get("body"),
                "state": x["state"].upper(), "createdAt": x["created_at"], "closedAt": x.get("closed_at"), "updatedAt": x["updated_at"],
                "author": {"login": (x.get("user") or {}).get("login")},
                "labels": {"nodes": [{"name": l["name"]} for l in x.get("labels", [])]},
                "assignees": {"nodes": [{"login": a["login"]} for a in x.get("assignees", [])]},
            }
            seen += 1
        page += 1


def list_releases(owner: str, name: str) -> list[dict]:
    if has_token():
        data = gql(RELEASES_Q, {"owner": owner, "name": name})
        return ((data.get("repository") or {}).get("releases") or {}).get("nodes") or []
    r = requests.get(f"{REST_URL}/repos/{owner}/{name}/releases", params={"per_page": 30}, headers=_headers(), timeout=60)
    if r.status_code != 200:
        return []
    return [{"tagName": x["tag_name"], "name": x.get("name"), "url": x["html_url"], "publishedAt": x.get("published_at"), "author": {"login": (x.get("author") or {}).get("login")}} for x in r.json()]


def email_login_map(owner: str, name: str, limit: int) -> dict[str, dict]:
    """email -> {login, name} learned from default-branch history. Needs a token."""
    out: dict[str, dict] = {}
    if not has_token():
        return out
    for c in _paginate(HISTORY_Q, {"owner": owner, "name": name}, ["repository", "defaultBranchRef", "target", "history"], limit):
        a = c.get("author") or {}
        login = (a.get("user") or {}).get("login")
        if a.get("email") and login:
            out[a["email"].lower()] = {"login": login, "name": a.get("name")}
    return out


def rate_limit() -> dict:
    if not has_token():
        return {}
    try:
        d = gql("query { rateLimit { remaining limit resetAt cost } }", {})
        return d.get("rateLimit") or {}
    except Exception as e:
        log.info("rateLimit query failed: %s", e)
        return {}


def org_members(org: str) -> list[dict]:
    if not has_token():
        return []
    try:
        data = gql(USERS_Q, {"org": org})
        return ((data.get("organization") or {}).get("membersWithRole") or {}).get("nodes") or []
    except Exception as e:  # members may be hidden
        log.info("members not visible: %s", e)
        return []


def fetch_single_issue(owner: str, name: str, number: int) -> dict | None:
    r = requests.get(f"{REST_URL}/repos/{owner}/{name}/issues/{number}", headers=_headers(), timeout=30)
    if r.status_code != 200:
        return None
    return r.json()


def post_issue_comment(owner: str, name: str, number: int, body: str) -> dict:
    if not active_token():
        raise RuntimeError("a GitHub token is required to post comments (sign in with GitHub or set GITHUB_TOKEN)")
    r = requests.post(f"{REST_URL}/repos/{owner}/{name}/issues/{number}/comments", json={"body": body}, headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------- git clone history

@dataclass
class GitCommit:
    sha: str
    author_name: str
    author_email: str
    date: str
    message: str
    parents: list[str]
    files: list[tuple[str, int, int]] = field(default_factory=list)  # (path, additions, deletions)


_REVERT_RE = re.compile(r"This reverts commit ([0-9a-f]{7,40})", re.I)


def clone_or_update(owner: str, name: str) -> Path:
    config.CLONE_DIR.mkdir(parents=True, exist_ok=True)
    dest = config.CLONE_DIR / f"{owner}__{name}.git"
    url = f"https://github.com/{owner}/{name}.git"
    if dest.exists():
        subprocess.run(["git", "-C", str(dest), "fetch", "--quiet", "--filter=blob:none", "origin"], check=False, timeout=600)
    else:
        subprocess.run(["git", "clone", "--quiet", "--bare", "--filter=blob:none", url, str(dest)], check=True, timeout=900)
    return dest


def git_history(repo_dir: Path, max_commits: int) -> list[GitCommit]:
    fmt = "%x1e%H%x1f%an%x1f%ae%x1f%aI%x1f%P%x1f%B"
    cmd = ["git", "-C", str(repo_dir), "log", "--all", f"--max-count={max_commits}", "--numstat", "--no-renames", f"--format={fmt}"]
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600).stdout
    commits: list[GitCommit] = []
    for block in out.split("\x1e"):
        if not block.strip():
            continue
        head, _, rest = block.partition("\n")
        parts = head.split("\x1f")
        if len(parts) < 6:
            # body may contain newlines: re-split on the whole block
            parts = block.split("\x1f", 5)
            if len(parts) < 6:
                continue
            body_and_stats = parts[5]
        else:
            body_and_stats = parts[5] + "\n" + rest
        sha, an, ae, date, parents = parts[0].strip(), parts[1], parts[2], parts[3], parts[4]
        # numstat lines look like "12\t3\tpath" and follow a blank line after the body
        files: list[tuple[str, int, int]] = []
        msg_lines: list[str] = []
        for line in body_and_stats.splitlines():
            m = re.match(r"^(\d+|-)\t(\d+|-)\t(.+)$", line)
            if m:
                a = 0 if m.group(1) == "-" else int(m.group(1))
                d = 0 if m.group(2) == "-" else int(m.group(2))
                files.append((m.group(3), a, d))
            else:
                msg_lines.append(line)
        message = "\n".join(msg_lines).strip()
        commits.append(GitCommit(sha=sha, author_name=an, author_email=ae.lower(), date=date, message=message,
                                 parents=[p for p in parents.split() if p], files=files[: config.MAX_FILES_PER_COMMIT]))
    return commits


def reverted_sha(message: str) -> str | None:
    m = _REVERT_RE.search(message or "")
    return m.group(1) if m else None
