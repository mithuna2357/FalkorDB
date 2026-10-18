"""Agent actions on GitHub: when an issue is opened on the demo repo, find the owner via the graph and post a triage comment.
Deterministic (no LLM needed), so it is cheap and reproducible; the comment cites graph evidence.
"""
from __future__ import annotations

import logging
import re

from falkordb import Graph

from . import config, github_client as gh, tools
from .db import graph_for

log = logging.getLogger("actions")

_PATH_RE = re.compile(r"(?<![\w/])((?:[\w.-]+/)*[\w.-]+\.(?:py|ts|tsx|js|jsx|go|rs|java|c|cpp|h|md|yml|yaml|json|toml|sql|sh))\b")


def allowed_repo(full_name: str) -> bool:
    return bool(config.ACTION_REPO) and full_name.lower() == config.ACTION_REPO.lower()


def triage_comment(g: Graph, full_name: str, number: int, title: str, body: str) -> tuple[str, dict]:
    text = f"{title}\n{body or ''}"
    paths = _PATH_RE.findall(text)
    topic = " ".join(paths) if paths else title
    wk = tools.who_knows(g, topic, repo=full_name, limit=3)
    people = wk["result"].get("people", [])
    related = tools.search(g, title, kinds=["Issue", "PR"], limit=3)
    hits = [h for h in related["result"]["hits"] if h["node"].get("key") != f"{full_name}#{number}"][:3]

    def mention(p: dict) -> str:
        key = p["key"]
        name = p["person"].get("title") or key
        if key.startswith(("email:", "name:")):
            return f"**{name}** (no GitHub login on record)"
        return f"@{key} ({name})" if name.lower() != key.lower() else f"@{key}"

    lines = ["### HandsOnRepo triage", ""]
    if people:
        top = people[0]
        lines.append(f"**Likely owner:** {mention(top)} — {int(top['commits_on_topic'])} commits on {top['files_on_topic']} related file(s), "
                     f"{int(top['reviews_on_topic'])} review(s) there, last touch {str(top['last_touch'] or '')[:10]}.")
        if len(people) > 1:
            lines.append("**Backup:** " + ", ".join(mention(p) for p in people[1:]))
        lines.append("")
        lines.append(f"_Matched via {wk['result']['matched_via']}: {wk['result']['files_matched']} file(s) for `{topic[:80]}`. "
                     f"Scoring: {wk['result']['scoring']}._")
    else:
        lines.append("No owner could be inferred from the graph for this issue yet (no matching files or PRs).")
    if hits:
        lines += ["", "**Related history:**"]
        for h in hits:
            n = h["node"]
            lines.append(f"- [{n['title']}]({n.get('url', '')}) ({h['kind']}, {h.get('state') or 'n/a'})")
    lines += ["", "<sub>Posted by the HandsOnRepo triage agent (FalkorDB graph). Every claim above comes from graph traversals, not guesses.</sub>"]
    comment = "\n".join(lines)
    evidence = {"people": people, "related": hits, "cypher": wk["cypher"] + related["cypher"]}
    return comment, evidence


def triage_issue(org: str, full: str, number: int, title: str, body: str, post: bool = True) -> dict:
    """Run the triage agent for one issue: decide from the graph, optionally post to GitHub, and ALWAYS record what it did as an
    Action node so the chat agent and MCP clients can see it (shared state between agents through the graph)."""
    g = graph_for(org)
    comment, evidence = triage_comment(g, full, number, title, body)
    url = None
    posted = False
    if post:
        res = gh.post_issue_comment(*full.split("/", 1), number, comment)
        url = res.get("html_url")
        posted = True
        log.info("posted triage comment on %s#%s", full, number)
    suggested = [p["key"] for p in evidence["people"][:3] if not str(p["key"]).startswith(("email:", "name:"))]
    cited = [h["node"].get("key") for h in evidence["related"] if h["node"].get("key")]
    act = tools.record_action(g, org, "triage_comment", full, f"{full}#{number}", comment, url, suggested, cited, posted=posted, agent="triage")
    return {"posted": url, "recorded_action": act["result"], "comment": comment, "evidence": evidence}


def handle_issue_opened(payload: dict) -> dict:
    repo = payload.get("repository") or {}
    full = repo.get("full_name") or ""
    issue = payload.get("issue") or {}
    if not allowed_repo(full):
        return {"skipped": f"{full} is not ACTION_REPO"}
    if (issue.get("user") or {}).get("type") == "Bot":
        return {"skipped": "bot-authored issue"}
    org = full.split("/", 1)[0]
    return triage_issue(org, full, issue["number"], issue.get("title") or "", issue.get("body") or "", post=True)
