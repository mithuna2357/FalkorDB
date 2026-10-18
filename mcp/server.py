"""HandsOnRepo MCP server.

Exposes the same FalkorDB graph tools the web agent uses, over the Model Context Protocol (stdio),
so Claude Desktop, Cursor, Claude Code or any MCP client can ask an organisation's brain directly.

Run:   python mcp/server.py            (reads backend/.env for the FalkorDB connection)
Env:   BRAIN_DEFAULT_ORG=FalkorDB      optional default org when a tool call omits `org`
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from mcp.server.mcpserver import MCPServer  # noqa: E402

from app import schema, tools  # noqa: E402
from app.db import graph_for, list_org_graphs, normalize_org  # noqa: E402

DEFAULT_ORG = os.getenv("BRAIN_DEFAULT_ORG") or None

mcp = MCPServer(
    "handsonrepo",
    version="0.1.0",
    instructions=(
        "HandsOnRepo: a FalkorDB graph of a GitHub organisation (people, commits, PRs, issues, files, reviews, reverts, "
        "ownership facts). Call list_orgs first if you do not know which organisations are loaded. Every tool returns "
        "{result, sources, cypher, ms}; cite the sources (PR/issue numbers, commit shas, file paths, people) in answers."
    ),
)


def _g(org: str | None):
    org = org or DEFAULT_ORG
    if not org:
        raise ValueError("org is required (call list_orgs to see what is loaded)")
    g = graph_for(normalize_org(org))
    if not schema.get_meta(g):
        raise ValueError(f"no brain for {org}; ingest it first via the backend (POST /api/ingest)")
    return g


def _out(res: dict, with_path: bool = False) -> str:
    """Compact JSON for the model: result + sources + cypher + ms (path only on request)."""
    slim = {"result": res["result"], "sources": res["sources"][:20], "cypher": res["cypher"], "ms": res["ms"]}
    if with_path:
        slim["path"] = res["path"]
    return json.dumps(slim, default=str)


def _safe(fn):
    """Turn expected tool errors (read-only guard, unknown org, bad reference) into a JSON error the model can read."""
    import functools

    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except (tools.ToolError, ValueError) as e:
            return json.dumps({"error": str(e)})
    return wrapper


@mcp.tool()
@_safe
def list_orgs() -> str:
    """List the GitHub organisations that have a brain loaded, with node/edge counts and last sync time."""
    out = []
    for org in list_org_graphs():
        meta = schema.get_meta(graph_for(org))
        if meta:
            out.append(meta)
    return json.dumps(out, default=str)


@mcp.tool()
@_safe
def repo_overview(org: str | None = None) -> str:
    """Repositories in the org's graph with counts of PRs, issues, commits, files and releases."""
    return _out(tools.repo_overview(_g(org)))


@mcp.tool()
@_safe
def who_knows(topic: str, org: str | None = None, repo: str | None = None, limit: int = 5) -> str:
    """Rank the people who know a topic, module, file path or area best (commits touching matching files, reviews of PRs
    touching them, PageRank over reviewer->author edges). Use for 'who owns', 'who should review', 'who is the expert'."""
    return _out(tools.who_knows(_g(org), topic, repo, limit), with_path=True)


@mcp.tool()
@_safe
def top_reviewers(org: str | None = None, repo: str | None = None, limit: int = 6) -> str:
    """Most trusted human reviewers (PageRank over reviewer->author edges), review counts and who they review for."""
    return _out(tools.top_reviewers(_g(org), repo, limit))


@mcp.tool()
@_safe
def decision_trail(ref: str, org: str | None = None, repo: str | None = None, max_comments: int = 12) -> str:
    """Full story of a PR (or the PR that closed an issue / that a commit belongs to) in time order: motivating issue and its
    discussion, PR, review verdicts and comments, who merged it and when, release, and any revert with its discussion.
    Use for 'why was this done / reverted / merged', 'who decided', 'trace this decision to its source'."""
    return _out(tools.decision_trail(_g(org), ref, repo, max_comments), with_path=True)


@mcp.tool()
@_safe
def reverts(org: str | None = None, repo: str | None = None, limit: int = 10) -> str:
    """Every revert (commit or PR): who reverted what, when, the revert message and the issues involved."""
    return _out(tools.reverts(_g(org), repo, limit))


@mcp.tool()
@_safe
def recent(org: str | None = None, kind: str = "PR", repo: str | None = None, state: str | None = None, person: str | None = None, limit: int = 8) -> str:
    """Most recent PRs, issues, commits, releases, agent Actions (what the triage agent did) or Episodes (earlier Q&A from any agent);
    filter by repo (owner/name), state (OPEN|CLOSED|MERGED) or author login. kind: PR|Issue|Commit|Release|Action|Episode."""
    return _out(tools.recent(_g(org), kind, repo, state, limit, person))


@mcp.tool()
@_safe
def node_context(ref: str, org: str | None = None, repo: str | None = None) -> str:
    """Everything directly connected to a PR ('#123' or 'owner/repo#123'), issue, commit sha, '@login' or file path:
    author, reviewers, closes, reverts (both directions), commits, files, mentions. Use for 'why was X reverted/closed'."""
    return _out(tools.node_context(_g(org), ref, repo), with_path=True)


@mcp.tool()
@_safe
def search(text: str, org: str | None = None, kinds: list[str] | None = None, repo: str | None = None, limit: int = 6) -> str:
    """Full-text search over PR/issue titles and bodies, docs, commit messages and file paths. kinds: PR, Issue, Doc, Commit, File."""
    return _out(tools.search(_g(org), text, kinds, limit, repo))


@mcp.tool()
@_safe
def find_path(from_ref: str, to_ref: str, org: str | None = None, repo: str | None = None, max_len: int = 6) -> str:
    """Shortest path between two things (person, PR, issue, commit, file) as the chain of relationships connecting them."""
    return _out(tools.find_path(_g(org), from_ref, to_ref, repo, max_len), with_path=True)


@mcp.tool()
@_safe
def impact_of_leaving(person: str, org: str | None = None, limit: int = 8) -> str:
    """Blast radius if a person leaves: files only they touched, directories they dominate, authors only they review,
    their open work, and who could cover each area. Use for 'what happens if X leaves', 'handover plan for X'."""
    return _out(tools.impact_of_leaving(_g(org), person, limit), with_path=True)


@mcp.tool()
@_safe
def teams(org: str | None = None, limit: int = 8) -> str:
    """Inferred teams: communities of people who repeatedly work on the same files, with members, owned repos/directories
    and lead reviewer. Use for 'what teams exist', 'who works with whom', 'which team owns X'."""
    return _out(tools.teams(_g(org), limit))


@mcp.tool()
@_safe
def bus_factor(org: str | None = None, repo: str | None = None, limit: int = 5) -> str:
    """Knowledge-risk analysis: people bridges (betweenness over shared files), files only one person ever touched,
    and critical files (betweenness over files that change together)."""
    return _out(tools.bus_factor(_g(org), repo, limit))


@mcp.tool()
@_safe
def clusters(org: str | None = None, repo: str | None = None, limit: int = 6) -> str:
    """Module clusters via label propagation over files that change together, with each cluster's main owners."""
    return _out(tools.clusters(_g(org), repo, limit))


@mcp.tool()
@_safe
def owner_history(file_ref: str, org: str | None = None, repo: str | None = None) -> str:
    """Who owns a file now, who owned it before (owner Facts with SUPERSEDES history) and all contributors."""
    return _out(tools.owner_history(_g(org), file_ref, repo), with_path=True)


@mcp.tool()
@_safe
def recall(question: str, org: str | None = None, limit: int = 3) -> str:
    """Recall earlier questions and answers stored in this org's memory (Episode nodes)."""
    return _out(tools.recall(_g(org), question, limit))


@mcp.tool()
@_safe
def remember(question: str, answer: str, org: str | None = None, source_keys: list[str] | None = None) -> str:
    """Store a question/answer pair as an Episode in the org's memory, citing source node keys (e.g. 'owner/repo#123', a sha, a login)."""
    g = _g(org)
    return _out(tools.remember(g, (org or DEFAULT_ORG), question, answer, source_keys, ["mcp"], agent="mcp"))


@mcp.tool()
@_safe
def run_cypher(query: str, org: str | None = None) -> str:
    """Read-only Cypher against the org graph (writes are rejected). Schema: (Person{key,login,name})-[:AUTHORED]->(Commit{sha,message,date}|PR{number,title,state,merged}|Issue{number,title,state});
    (Person)-[:REVIEWED]->(PR); (Commit|PR)-[:TOUCHED]->(File{path,repo}); (PR)-[:CLOSES]->(Issue); (Commit)-[:REVERTS]->(Commit); (PR)-[:REVERTS]->(PR);
    (Commit)-[:PART_OF]->(PR); (x)-[:IN_REPO]->(Repo{full_name}); (Person)-[:TOUCHES{count,last}]->(File); (PR)-[:RELEASED_IN]->(Release{tag});
    (File)-[:HAS_FACT]->(Fact{kind,value,current})-[:ABOUT]->(Person); (Fact)-[:SUPERSEDES]->(Fact)."""
    return _out(tools.run_cypher(_g(org), query))


@mcp.resource("brain://orgs")
def orgs_resource() -> str:
    """Loaded organisations and their graph sizes."""
    return list_orgs()


if __name__ == "__main__":
    mcp.run(transport="stdio")
