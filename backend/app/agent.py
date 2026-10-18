"""Agent loop: OpenAI-compatible chat completions with tool calling over the graph tools.
Returns the /api/ask contract: answer, sources, path, cypher, toolCalls.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from falkordb import Graph
from openai import OpenAI

from . import config, tools
from .db import PathCollector

log = logging.getLogger("agent")

MAX_STEPS = 8

TOOL_SPECS = [
    {"type": "function", "function": {"name": "who_knows", "description": "Rank the people who know a topic, module, file path or area best. Uses commits touching matching files, reviews of PRs touching them, and PageRank over the reviewer->author graph. Use for 'who owns', 'who should review', 'who is the expert'.",
        "parameters": {"type": "object", "properties": {"topic": {"type": "string", "description": "path fragment, module name or keywords, e.g. 'vector index', 'falkordb/asyncio', 'docker'"}, "repo": {"type": "string", "description": "optional owner/name to restrict to"}, "limit": {"type": "integer", "default": 5}}, "required": ["topic"]}}},
    {"type": "function", "function": {"name": "node_context", "description": "Everything directly connected to a PR, issue, commit, person, release or file: author, reviewers, closes, REVERTS (both directions), commits, files, mentions. Use for 'why was X reverted/closed', 'what is PR #N about', 'what did this commit change'.",
        "parameters": {"type": "object", "properties": {"ref": {"type": "string", "description": "'#123', 'owner/repo#123', a commit sha, '@login', or a file path"}, "repo": {"type": "string"}}, "required": ["ref"]}}},
    {"type": "function", "function": {"name": "search", "description": "Full-text search over PR and issue titles/bodies, docs, and commit messages. Use to find the PRs/issues about a topic before asking who/why.",
        "parameters": {"type": "object", "properties": {"text": {"type": "string", "description": "plain keywords only; no 'repo:' or 'state:' qualifiers (use the repo parameter / the recent tool instead)"}, "kinds": {"type": "array", "items": {"type": "string", "enum": ["PR", "Issue", "Doc", "Commit", "File"]}}, "limit": {"type": "integer", "default": 6, "maximum": 12}, "repo": {"type": "string", "description": "owner/name to restrict to"}}, "required": ["text"]}}},
    {"type": "function", "function": {"name": "find_path", "description": "Shortest path in the graph between two things (person, PR, issue, commit, file). Use to show how two things are connected, e.g. a person to a PR, an issue to the commit that fixed it.",
        "parameters": {"type": "object", "properties": {"from_ref": {"type": "string"}, "to_ref": {"type": "string"}, "repo": {"type": "string"}, "max_len": {"type": "integer", "default": 6}}, "required": ["from_ref", "to_ref"]}}},
    {"type": "function", "function": {"name": "impact_of_leaving", "description": "Blast radius if a specific person leaves: files only they have touched (by directory), directories they dominate, authors only they review, their open PRs and issues, and who could cover each area. Use for 'what happens if X leaves', 'how dependent are we on X', 'handover plan for X'.",
        "parameters": {"type": "object", "properties": {"person": {"type": "string", "description": "login or name"}, "limit": {"type": "integer", "default": 8}}, "required": ["person"]}}},
    {"type": "function", "function": {"name": "teams", "description": "Inferred teams: communities of people who repeatedly work on the same files, with members, the repos and directories each team owns, and its lead reviewer. Use for 'what teams exist', 'who works with whom', 'which team owns X'.",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "default": 8}}}}},
    {"type": "function", "function": {"name": "bus_factor", "description": "Bus-factor / knowledge-risk analysis: betweenness centrality over the person-file graph (bridges) and files only one person has ever touched.",
        "parameters": {"type": "object", "properties": {"repo": {"type": "string"}, "limit": {"type": "integer", "default": 5}}}}},
    {"type": "function", "function": {"name": "clusters", "description": "Module clusters: label propagation over files that change together, with the main owners of each cluster. Use for 'how is the codebase organised', 'what areas exist'.",
        "parameters": {"type": "object", "properties": {"repo": {"type": "string"}, "limit": {"type": "integer", "default": 6}}}}},
    {"type": "function", "function": {"name": "owner_history", "description": "Who owns a file now and who owned it before (owner Facts with SUPERSEDES history), plus all contributors. Use for 'what changed since', 'who owned this before'.",
        "parameters": {"type": "object", "properties": {"file_ref": {"type": "string"}, "repo": {"type": "string"}}, "required": ["file_ref"]}}},
    {"type": "function", "function": {"name": "recall", "description": "Recall earlier questions and answers from this org's memory (Episode nodes). Call it first when the question refers to something asked before.",
        "parameters": {"type": "object", "properties": {"question": {"type": "string"}, "limit": {"type": "integer", "default": 3}}, "required": ["question"]}}},
    {"type": "function", "function": {"name": "decision_trail", "description": "The full story of a PR (or of the PR that closed an issue, or the PR a commit belongs to) in time order: motivating issue and its discussion, PR, review verdicts and review comments, discussion comments, who merged it and when, release, and any revert with its discussion. THE tool for 'why was this done / reverted / merged', 'who decided', 'trace this decision to its source'.",
        "parameters": {"type": "object", "properties": {"ref": {"type": "string", "description": "'#123', 'owner/repo#123', a commit sha, or a PR/issue title fragment"}, "repo": {"type": "string"}, "max_comments": {"type": "integer", "default": 12}}, "required": ["ref"]}}},
    {"type": "function", "function": {"name": "reverts", "description": "List every revert (commit or PR) with who reverted what, when, and the issues involved. Use for 'what was reverted', 'why was X reverted'. Then node_context on a specific item for more detail.",
        "parameters": {"type": "object", "properties": {"repo": {"type": "string", "description": "owner/name to restrict to; omit for the whole org"}, "limit": {"type": "integer", "default": 10}}}}},
    {"type": "function", "function": {"name": "recent", "description": "Most recent PRs, issues, commits, releases, agent Actions (what the triage agent did) or Episodes (earlier Q&A), optionally filtered by repo, state (OPEN, CLOSED, MERGED) or author login. Use for 'latest', 'most recent', 'what happened recently', 'what did X work on', 'what did the agent do'.",
        "parameters": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["PR", "Issue", "Commit", "Release", "Action", "Episode"], "default": "PR"}, "repo": {"type": "string"}, "state": {"type": "string", "enum": ["OPEN", "CLOSED", "MERGED"]}, "person": {"type": "string", "description": "author login"}, "limit": {"type": "integer", "default": 8}}}}},
    {"type": "function", "function": {"name": "top_reviewers", "description": "Most trusted human reviewers (PageRank over reviewer->author edges), their review counts and who they review for. Use for 'who are the key reviewers / maintainers / gatekeepers'.",
        "parameters": {"type": "object", "properties": {"repo": {"type": "string"}, "limit": {"type": "integer", "default": 6}}}}},
    {"type": "function", "function": {"name": "repo_overview", "description": "List the repositories in this org's graph with counts of PRs, issues, commits and files.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "run_cypher", "description": "Run a read-only Cypher query when no other tool fits. Schema: (Person{key,login,name})-[:AUTHORED]->(Commit{sha,message,date}|PR{number,title,state,merged}|Issue{number,title,state}); (Person)-[:REVIEWED{state}]->(PR); (Person)-[:MERGED{at}]->(PR); (Person)-[:AUTHORED]->(Comment{body,kind,created_at})-[:ON]->(PR|Issue); (Commit|PR)-[:TOUCHED]->(File{path,repo}); (PR)-[:CLOSES]->(Issue); (Commit)-[:REVERTS]->(Commit); (PR)-[:REVERTS]->(PR); (Commit)-[:PART_OF]->(PR); (x)-[:IN_REPO]->(Repo{full_name}); (Person)-[:TOUCHES{count,last}]->(File); (PR)-[:RELEASED_IN]->(Release{tag}); (File)-[:HAS_FACT]->(Fact{kind,value,current})-[:ABOUT]->(Person).",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
]

SYSTEM = """You are HandsOnRepo, the company brain for the GitHub organisation "{org}". Your only knowledge is a FalkorDB graph of its repositories: people, commits, pull requests, issues, files, releases, reviews, reverts and derived ownership facts. Repositories in the graph: {repos}.

Rules:
- Always answer from tool results. Never guess names, numbers or reasons. If the graph has no evidence, say exactly that.
- Start with the most specific tool: who_knows for ownership/experts/who should review a topic; top_reviewers for trusted reviewers and maintainers; reverts to list what was reverted, then decision_trail on the item to explain WHY (it returns the recorded discussion, approvals, who merged, release and revert); decision_trail also for 'why was this merged/closed', 'who decided'; recent for latest/most-recent items; node_context for why/what about a specific PR, issue or commit; search to find the PRs/issues about a topic (keywords only, use the repo parameter for filtering); find_path to show how two things connect; bus_factor for org-wide risk, impact_of_leaving for one named person, teams for who works with whom, clusters for code structure; owner_history for changes over time; recall when the user refers to an earlier question.
- Repositories are named owner/name (e.g. FalkorDB/FalkorDB); when the user names one, pass it as the repo parameter.
- Other agents share this graph: a triage agent posts owner suggestions on new issues and records each as an Action node; MCP clients (Claude Desktop, Cursor) can remember Episodes here too. For "what did the triage agent / the bot do", "what actions were taken", use recent(kind='Action'); for "what did I/we ask before", use recall or recent(kind='Episode').
- Ignore bots (dependabot, copilot, coderabbit, cubic, overcut and similar) when naming people, unless asked about bots.
- Do not end with offers like "If you want, I can...". Answer and stop.
- If the message is conversational rather than a question about the organisation (a greeting, thanks, "what can you do", feedback), reply in one or two friendly sentences and suggest two example questions. Do not call any tool for that, and do not mention memory or episodes.
- Prefer 1-3 tool calls. Stop as soon as you can answer.
- Cite evidence inline using the exact references the tools return: PR/issue numbers like #123, commit short shas, file paths, and people by login. The UI shows links and the graph path separately, so do not list URLs.
- Explain the graph reasoning in one short sentence (e.g. "ranked by commits touching 7 matching files plus reviews; PageRank over reviewer->author edges breaks ties").
- Be concise: a direct answer first, then 2-5 bullet points of evidence. Markdown is fine.
- Write for humans: never echo raw field names (say "33 commits on 7 matching files", not "commits_on_topic"). Refer to people as "Name (@login)". If a person has no GitHub login (key starts with "email:" or "name:"), use their name only and never print the email.
- Dates: show just the day (2026-08-13), never full timestamps.
- Data mode: {mode}."""


def _client() -> OpenAI:
    if not config.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set")
    kwargs: dict[str, Any] = {"api_key": config.OPENAI_API_KEY}
    if config.OPENAI_BASE_URL:
        kwargs["base_url"] = config.OPENAI_BASE_URL
    return OpenAI(**kwargs)


def _run_tool(g: Graph, name: str, args: dict) -> dict:
    fn = {
        "who_knows": lambda: tools.who_knows(g, args.get("topic", ""), args.get("repo"), int(args.get("limit") or 5)),
        "node_context": lambda: tools.node_context(g, args.get("ref", ""), args.get("repo")),
        "search": lambda: tools.search(g, args.get("text", ""), args.get("kinds"), int(args.get("limit") or 6), args.get("repo")),
        "reverts": lambda: tools.reverts(g, args.get("repo"), int(args.get("limit") or 10)),
        "decision_trail": lambda: tools.decision_trail(g, args.get("ref", ""), args.get("repo"), int(args.get("max_comments") or 12)),
        "recent": lambda: tools.recent(g, args.get("kind") or "PR", args.get("repo"), args.get("state"), int(args.get("limit") or 8), args.get("person")),
        "top_reviewers": lambda: tools.top_reviewers(g, args.get("repo"), int(args.get("limit") or 6)),
        "find_path": lambda: tools.find_path(g, args.get("from_ref", ""), args.get("to_ref", ""), args.get("repo"), int(args.get("max_len") or 6)),
        "bus_factor": lambda: tools.bus_factor(g, args.get("repo"), int(args.get("limit") or 5)),
        "impact_of_leaving": lambda: tools.impact_of_leaving(g, args.get("person", ""), int(args.get("limit") or 8)),
        "teams": lambda: tools.teams(g, int(args.get("limit") or 8)),
        "clusters": lambda: tools.clusters(g, args.get("repo"), int(args.get("limit") or 6)),
        "owner_history": lambda: tools.owner_history(g, args.get("file_ref", ""), args.get("repo")),
        "recall": lambda: tools.recall(g, args.get("question", ""), int(args.get("limit") or 3)),
        "repo_overview": lambda: tools.repo_overview(g),
        "run_cypher": lambda: tools.run_cypher(g, args.get("query", "")),
    }.get(name)
    if not fn:
        raise tools.ToolError(f"unknown tool {name}")
    return fn()


def _trim(obj: Any, limit: int = 12000) -> str:
    s = json.dumps(obj, default=str)
    return s if len(s) <= limit else s[:limit] + f'... (truncated {len(s) - limit} chars)'


_GREETING_RE = re.compile(
    r"^\s*(hi|hii+|hello|hey|heya|yo|sup|good (morning|afternoon|evening)|namaste|hola|thanks?|thank you|ty|ok|okay|cool|nice|great|"
    r"who are you|what are you|what can you do|what do you do|help|how does this work|what is this)[\s!.?,]*(there|handsonrepo|brain)?[\s!.?,]*$",
    re.I,
)


def _small_talk(org: str, repos: list[str], question: str) -> dict | None:
    """Instant, free answer for greetings and 'what can you do'. Returns None for real questions."""
    if not _GREETING_RE.match(question or ""):
        return None
    q = question.lower()
    thanks = any(w in q for w in ("thank", "ty", "cool", "nice", "great"))
    shown = [r.split("/", 1)[-1] for r in repos[:5]]
    more = f" and {len(repos) - 5} more" if len(repos) > 5 else ""
    intro = ("You're welcome. " if thanks else "Hi! ") + (
        f"I'm HandsOnRepo, the company brain for **{org}**. I only know what is in its graph: "
        f"{', '.join(shown)}{more} — people, commits, pull requests, issues, reviews, comments and releases, all connected."
    )
    examples = [
        "Who should review a change to the vector index code, and why them?",
        "Which PRs or commits were reverted, and why? Who decided?",
        "What happens if the top contributor leaves? Who could cover?",
        "What teams exist here and who leads each?",
    ]
    answer = (
        intro
        + "\n\nAsk me things like:\n"
        + "\n".join(f"- {e}" for e in examples)
        + "\n\nEvery answer comes with its sources, the graph path and the Cypher that ran."
    )
    return {"answer": answer, "sources": [], "path": {"nodes": [], "edges": []}, "cypher": [], "toolCalls": [], "memory": [],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "model": "none (greeting)"}}


def ask(g: Graph, org: str, question: str, repos: list[str], mode: str, history: list[dict] | None = None) -> dict:
    quick = _small_talk(org, repos, question)
    if quick:
        return quick
    client = _client()
    messages: list[dict] = [{"role": "system", "content": SYSTEM.format(org=org, repos=", ".join(repos) or "(none yet)", mode=mode)}]
    for h in (history or [])[-6:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": str(h["content"])[:4000]})
    # Memory across sessions: recall earlier episodes (from this agent, the triage agent or MCP clients) before answering.
    memory: list[dict] = []
    try:
        rec = tools.recall(g, question, limit=3)
        for ep in rec["result"]["episodes"]:
            if ep.get("score", 0) >= 1.0 and ep.get("question") and ep["question"].strip().lower() != question.strip().lower():
                memory.append({"episode_id": ep.get("episode_id"), "question": ep["question"], "answer_excerpt": (ep.get("answer") or "")[:300],
                               "at": ep.get("at"), "agent": ep.get("agent"), "score": ep.get("score")})
            elif ep.get("question") and ep["question"].strip().lower() == question.strip().lower():
                memory.append({"episode_id": ep.get("episode_id"), "question": ep["question"], "answer_excerpt": (ep.get("answer") or "")[:300],
                               "at": ep.get("at"), "agent": ep.get("agent"), "score": ep.get("score"), "same_question": True})
        if memory:
            lines = "\n".join(f"- [{m.get('at','')[:16]}] ({m.get('agent') or 'chat'}) Q: {m['question'][:160]} | A: {m['answer_excerpt'][:200]}" for m in memory[:3])
            messages.append({"role": "system", "content": "Memory from earlier sessions in this org's graph (Episode nodes):\n" + lines + "\n\n"
                             "Rules for memory: (1) If the user asks what was asked, answered or decided earlier, answer from this memory (recall gives more). "
                             "(2) For any question about the organisation itself, ALWAYS run the proper graph tool and answer from fresh results; memory is never a substitute, "
                             "because the graph may have changed since. If the fresh answer differs from the remembered one, say what changed in one line."})
    except Exception as e:
        log.warning("auto-recall failed: %s", e)
    messages.append({"role": "user", "content": question})

    col = PathCollector()
    cypher: list[str] = []
    tool_calls: list[dict] = []
    sources: dict[str, dict] = {}
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    answer = ""

    for step in range(MAX_STEPS):
        t0 = time.perf_counter()
        kwargs: dict[str, Any] = {}
        if config.LLM_REASONING_EFFORT and (config.LLM_MODEL.startswith(("gpt-5", "o1", "o3", "o4"))):
            kwargs["reasoning_effort"] = config.LLM_REASONING_EFFORT
        resp = client.chat.completions.create(model=config.LLM_MODEL, messages=messages, tools=TOOL_SPECS, tool_choice="auto", **kwargs)
        if resp.usage:
            usage["prompt_tokens"] += resp.usage.prompt_tokens or 0
            usage["completion_tokens"] += resp.usage.completion_tokens or 0
        msg = resp.choices[0].message
        if not msg.tool_calls:
            answer = msg.content or ""
            break
        messages.append({"role": "assistant", "content": msg.content or None, "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            t1 = time.perf_counter()
            try:
                out = _run_tool(g, name, args)
                ok = True
            except Exception as e:  # tool errors go back to the model, not to the user
                log.warning("tool %s failed: %s", name, e)
                out = {"result": {"error": str(e)}, "path": {"nodes": [], "edges": []}, "sources": [], "cypher": [], "ms": 0}
                ok = False
            ms = int((time.perf_counter() - t1) * 1000)
            tool_calls.append({"tool": name, "args": args, "ms": ms, "ok": ok})
            cypher += [c for c in out.get("cypher", []) if c not in cypher]
            # Episodes are the agent's own memory, not evidence about the org: keep them out of the drawn path.
            skip = {n["id"] for n in out["path"]["nodes"] if n.get("label") == "Episode"}
            for n in out["path"]["nodes"]:
                if n["id"] not in skip:
                    col.nodes.setdefault(n["id"], n)
            for e in out["path"]["edges"]:
                if e["from"] not in skip and e["to"] not in skip:
                    col.edges.setdefault((e["from"], e["to"], e["type"]), e)
            for s in out.get("sources", []):
                sources.setdefault(f"{s['type']}:{s['id']}", s)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": _trim(out["result"])})
        log.info("step %d: %s (%.1fs)", step, [t["tool"] for t in tool_calls[-len(msg.tool_calls):]], time.perf_counter() - t0)
    else:
        answer = "I ran out of steps before reaching a confident answer. Here is what I found so far:\n\n" + "\n".join(f"- {t['tool']}({json.dumps(t['args'])})" for t in tool_calls)

    # memory: store the episode, citing the sources used (skip chit-chat answers that used no tools)
    try:
        if not tool_calls and len(answer) < 400:
            raise RuntimeError("skip: conversational reply")
        keys = [s.get("key") for s in sources.values() if s.get("key")]
        ep = tools.remember(g, org, question, answer, keys, [t["tool"] for t in tool_calls])
        cypher += ep["cypher"]
    except Exception as e:
        log.warning("remember failed: %s", e)

    return {
        "answer": answer,
        "sources": list(sources.values())[:30],
        "path": col.path(),
        "cypher": cypher,
        "toolCalls": tool_calls,
        "memory": memory[:3],
        "usage": {**usage, "model": config.LLM_MODEL},
    }
