"""Graph tools the agent can call. Every tool returns the same shape:
{"result": <plain json>, "path": {"nodes": [...], "edges": [...]}, "sources": [...], "cypher": [...], "ms": int}
All reads use ro_query; only remember() writes (an Episode node).
"""
from __future__ import annotations

import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from falkordb import Graph

from .db import PathCollector, run

_WRITE_RE = re.compile(r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD\s+CSV|CALL\s+db\.(?!idx\.\w+\.query|indexes|labels|relationshipTypes|propertyKeys))\b", re.I)
_LIMIT_RE = re.compile(r"\bLIMIT\s+\d+", re.I)
PATH_RELS = ["AUTHORED", "REVIEWED", "MERGED", "TOUCHED", "TOUCHES", "CLOSES", "REVERTS", "MENTIONS", "PART_OF", "ASSIGNED_TO", "RELEASED_IN", "HAS_FACT", "ABOUT", "SUPERSEDES", "CITES", "DOCUMENTS", "ON", "SUGGESTS"]


class ToolError(Exception):
    pass


def _pack(result: Any, col: PathCollector | None, cypher: list[str], ms: int) -> dict:
    col = col or PathCollector()
    return {"result": result, "path": col.path(), "sources": list(col.sources.values()), "cypher": cypher, "ms": ms}


def _ft_terms(text: str) -> list[str]:
    toks = re.findall(r"[A-Za-z0-9_]{2,}", text or "")
    stop = {"the", "and", "for", "with", "what", "who", "why", "how", "was", "were", "this", "that", "should", "about", "does", "did", "are", "is", "in", "on", "of", "to", "a", "an", "it", "be", "we", "our", "their", "code", "change", "changes", "review", "owns", "owner", "know", "knows"}
    return [t for t in toks if t.lower() not in stop][:8]


def _fulltext(g: Graph, label: str, text: str, limit: int, repo: str | None = None) -> tuple[list, PathCollector, list[str], int]:
    terms = _ft_terms(text)
    if not terms:
        return [], PathCollector(), [], 0
    where = "WHERE node.repo = $repo " if repo else ""
    if label == "Comment":  # what *people* said: drop bot-authored comments from search hits
        where = ("MATCH (p:Person)-[:AUTHORED]->(node) WHERE NOT coalesce(p.is_bot, false) " + ("AND node.repo = $repo " if repo else ""))
    cy = f"CALL db.idx.fulltext.queryNodes('{label}', $q) YIELD node, score {where}RETURN node, score ORDER BY score DESC LIMIT {int(limit)}"
    rows, col, ms = run(g, cy, {"q": " ".join(terms), "repo": repo})
    used = [cy.replace("$q", repr(" ".join(terms))).replace("$repo", repr(repo))]
    if not rows and len(terms) > 1:
        rows, col, ms2 = run(g, cy, {"q": "|".join(terms), "repo": repo})
        used.append(cy.replace("$q", repr("|".join(terms))).replace("$repo", repr(repo)))
        ms += ms2
    return rows, col, used, ms


# ---------------------------------------------------------------- resolve a reference to a node

def resolve_ref(g: Graph, ref: str, repo: str | None = None) -> tuple[list[dict], list[str]]:
    """Return candidate nodes (plain json) for a human reference: '#123', 'repo#123', sha, '@login', 'path/to/file', or text."""
    ref = (ref or "").strip()
    cy: list[str] = []
    m = re.match(r"^(?:([\w.-]+/[\w.-]+))?#(\d+)$", ref)
    if m:
        number = int(m.group(2))
        r = m.group(1) or repo
        if r:
            q = "MATCH (n) WHERE (n:PR OR n:Issue) AND n.key = $k RETURN n"
            rows, _, _ = run(g, q, {"k": f"{r}#{number}"})
        else:
            q = "MATCH (n) WHERE (n:PR OR n:Issue) AND n.number = $n RETURN n ORDER BY n.updated_at DESC LIMIT 5"
            rows, _, _ = run(g, q, {"n": number})
        cy.append(q)
        return [r_["n"] for r_ in rows], cy
    if re.fullmatch(r"[0-9a-f]{7,40}", ref):
        q = "MATCH (c:Commit) WHERE c.sha STARTS WITH $s RETURN c LIMIT 3"
        rows, _, _ = run(g, q, {"s": ref})
        cy.append(q)
        return [r_["c"] for r_ in rows], cy
    if ref.startswith("@") or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,60}", ref):
        login = ref.lstrip("@").lower().strip()
        q = ("MATCH (p:Person) WHERE p.key = $k OR toLower(coalesce(p.login,'')) = $k OR toLower(coalesce(p.name,'')) = $k "
             "RETURN p ORDER BY coalesce(p.is_bot, false) LIMIT 3")
        rows, _, _ = run(g, q, {"k": login})
        cy.append(q)
        if not rows and (" " in login or len(login) >= 4):
            q = ("MATCH (p:Person) WHERE toLower(coalesce(p.name,'')) CONTAINS $k OR toLower(coalesce(p.login,'')) CONTAINS $k "
                 "RETURN p ORDER BY coalesce(p.is_bot, false), size(coalesce(p.name, p.login, '')) LIMIT 3")
            rows, _, _ = run(g, q, {"k": login})
            cy.append(q)
        if rows:
            return [r_["p"] for r_ in rows], cy
    if "/" in ref or "." in ref:
        q = "MATCH (f:File) WHERE toLower(f.path) CONTAINS toLower($p) " + ("AND f.repo = $repo " if repo else "") + "RETURN f ORDER BY size(f.path) LIMIT 10"
        rows, _, _ = run(g, q, {"p": ref, "repo": repo})
        cy.append(q)
        if rows:
            return [r_["f"] for r_ in rows], cy
    out: list[dict] = []
    for label in ("PR", "Issue"):
        rows, _, used, _ = _fulltext(g, label, ref, 3)
        cy += used
        out += [r_["node"] for r_ in rows]
    return out, cy


def _node_id(n: dict) -> int:
    return int(str(n["id"]).lstrip("n"))


def _slim(n: dict | None) -> dict | None:
    """Drop the props blob so tool output stays small for the model."""
    if not n:
        return n
    return {k: v for k, v in n.items() if k != "props"}


# ---------------------------------------------------------------- tools

def run_cypher(g: Graph, query: str, params: dict | None = None) -> dict:
    """Read-only Cypher. Server-side ro_query also enforces this."""
    q = query.strip().rstrip(";")
    if _WRITE_RE.search(q):
        raise ToolError("run_cypher is read-only: CREATE/MERGE/SET/DELETE/REMOVE/DROP are not allowed")
    if not _LIMIT_RE.search(q):
        q += "\nLIMIT 50"
    rows, col, ms = run(g, q, params or {})
    return _pack(rows, col, [q], ms)


def find_path(g: Graph, from_ref: str, to_ref: str, repo: str | None = None, max_len: int = 6) -> dict:
    a, cy1 = resolve_ref(g, from_ref, repo)
    b, cy2 = resolve_ref(g, to_ref, repo)
    if not a or not b:
        return _pack({"error": f"could not resolve {'from' if not a else 'to'} reference", "from": a[:3], "to": b[:3]}, None, cy1 + cy2, 0)
    q = ("MATCH (a), (b) WHERE id(a) = $a AND id(b) = $b "
         "CALL algo.SPpaths({sourceNode: a, targetNode: b, relTypes: $rels, relDirection: 'both', maxLen: $max, pathCount: 1}) "
         "YIELD path RETURN path")
    rows, col, ms = run(g, q, {"a": _node_id(a[0]), "b": _node_id(b[0]), "rels": PATH_RELS, "max": int(max_len)})
    res = {"from": _slim(a[0]), "to": _slim(b[0]), "found": bool(rows), "hops": len(rows[0]["path"]["edges"]) if rows else None,
           "steps": [f"{e['type']}" for e in rows[0]["path"]["edges"]] if rows else []}
    return _pack(res, col, cy1 + cy2 + [q], ms)


_pagerank_cache: dict[str, tuple[float, dict[str, float]]] = {}


def pagerank_reviewers(g: Graph) -> tuple[dict[str, float], str]:
    key = g.name
    now = time.time()
    if key in _pagerank_cache and now - _pagerank_cache[key][0] < 600:
        return _pagerank_cache[key][1], "(cached) CALL algo.pageRank('Person', 'REVIEWED_FOR')"
    q = "CALL algo.pageRank('Person', 'REVIEWED_FOR') YIELD node, score RETURN node.key AS key, score"
    try:
        rows, _, _ = run(g, q)
    except Exception:
        rows = []
    scores = {r["key"]: float(r["score"]) for r in rows if r.get("key")}
    _pagerank_cache[key] = (now, scores)
    return scores, q


def who_knows(g: Graph, topic: str, repo: str | None = None, limit: int = 5) -> dict:
    """Rank people for a topic / path / area: commits touching matching files, reviews of PRs touching them,
    plus PageRank over the reviewer->author graph."""
    cy: list[str] = []
    t0 = time.perf_counter()
    terms = [t for t in re.findall(r"[A-Za-z0-9_./-]{3,}", topic or "") if t.lower() not in ("the", "and", "for", "code", "who", "should", "review", "changes", "change", "module")]
    files: list[dict] = []
    if terms:
        q = ("UNWIND $terms AS t MATCH (f:File) WHERE toLower(f.path) CONTAINS toLower(t) "
             + ("AND f.repo = $repo " if repo else "") + "RETURN DISTINCT f LIMIT 300")
        rows, _, _ = run(g, q, {"terms": terms, "repo": repo})
        cy.append(q)
        files = [r["f"] for r in rows]
    if not files:
        rows, _, used, _ = _fulltext(g, "File", topic, 100)
        cy += used
        files = [r["node"] for r in rows]
    matched_via = "files"
    if not files:
        # fall back to PRs/issues about the topic, then the files they touched
        matched_via = "pull requests"
        rows, _, used, _ = _fulltext(g, "PR", topic, 25)
        cy += used
        pr_ids = [_node_id(r["node"]) for r in rows]
        if pr_ids:
            q = "MATCH (pr:PR)-[:TOUCHED]->(f:File) WHERE id(pr) IN $ids RETURN DISTINCT f LIMIT 300"
            rows, _, _ = run(g, q, {"ids": pr_ids})
            cy.append(q)
            files = [r["f"] for r in rows]
            if not files:  # no PR file data (REST mode): rank PR authors/reviewers directly
                q = ("MATCH (p:Person)-[r:AUTHORED|REVIEWED]->(pr:PR) WHERE id(pr) IN $ids AND NOT coalesce(p.is_bot, false) "
                     "RETURN p, sum(CASE WHEN type(r)='AUTHORED' THEN 1 ELSE 0 END) AS commits, 0 AS files, max(pr.updated_at) AS last, "
                     "sum(CASE WHEN type(r)='REVIEWED' THEN 1 ELSE 0 END) AS reviews ORDER BY commits + reviews DESC LIMIT 10")
                rows, col, ms = run(g, q, {"ids": pr_ids})
                cy.append(q)
                return _finish_who_knows(g, rows, col, cy, [], matched_via, topic, limit, t0)
    if not files:
        return _pack({"people": [], "note": f"nothing in the graph matches '{topic}'"}, None, cy, int((time.perf_counter() - t0) * 1000))
    fkeys = [f["key"] for f in files]
    q = ("MATCH (p:Person)-[t:TOUCHES]->(f:File) WHERE f.key IN $files AND NOT coalesce(p.is_bot, false) "
         "WITH p, sum(t.count) AS commits, count(DISTINCT f) AS files, max(t.last) AS last "
         "OPTIONAL MATCH (p)-[:REVIEWED]->(pr:PR)-[:TOUCHED]->(f2:File) WHERE f2.key IN $files "
         "WITH p, commits, files, last, count(DISTINCT pr) AS reviews "
         "RETURN p, commits, files, last, reviews ORDER BY commits DESC, reviews DESC LIMIT 10")
    rows, col, ms = run(g, q, {"files": fkeys})
    cy.append(q)
    return _finish_who_knows(g, rows, col, cy, fkeys, matched_via, topic, limit, t0)


def _finish_who_knows(g, rows, col, cy, fkeys, matched_via, topic, limit, t0) -> dict:
    pr_scores, prq = pagerank_reviewers(g)
    cy.append(prq)
    people = []
    for r in rows:
        p = r["p"]
        pk = p.get("key") or p.get("props", {}).get("key")
        prs = pr_scores.get(pk, 0.0)
        score = float(r["commits"] or 0) + 2.0 * float(r["reviews"] or 0) + 40.0 * prs
        people.append({"person": _slim(p), "key": pk, "commits_on_topic": r["commits"], "files_on_topic": r["files"], "reviews_on_topic": r["reviews"],
                       "last_touch": r["last"], "reviewer_pagerank": round(prs, 4), "score": round(score, 2)})
    people.sort(key=lambda x: x["score"], reverse=True)
    people = people[:limit]
    # evidence edges for the top people
    if people and fkeys:
        q = ("UNWIND $pk AS k MATCH (p:Person {key:k})-[t:TOUCHES]->(f:File) WHERE f.key IN $files "
             "WITH p, t, f ORDER BY t.count DESC WITH p, collect([t, f])[0..4] AS tf UNWIND tf AS x RETURN p, x[0] AS t, x[1] AS f")
        rows2, col2, _ = run(g, q, {"pk": [x["key"] for x in people], "files": fkeys})
        cy.append(q)
        col.nodes.update(col2.nodes); col.edges.update(col2.edges); col.sources.update(col2.sources)
        # review evidence too, so people ranked for reviewing (not committing) are connected in the drawn path
        q3 = ("UNWIND $pk AS k MATCH (p:Person {key:k})-[r:REVIEWED]->(pr:PR)-[t:TOUCHED]->(f:File) WHERE f.key IN $files "
              "WITH p, r, pr, t, f ORDER BY pr.merged_at DESC WITH p, collect([r, pr, t, f])[0..2] AS ev UNWIND ev AS e "
              "RETURN p, e[0] AS r, e[1] AS pr, e[2] AS t, e[3] AS f")
        rows3, col3, _ = run(g, q3, {"pk": [x["key"] for x in people], "files": fkeys})
        cy.append(q3)
        col.nodes.update(col3.nodes); col.edges.update(col3.edges); col.sources.update(col3.sources)
    total_ms = int((time.perf_counter() - t0) * 1000)
    return _pack({"topic": topic, "matched_via": matched_via, "files_matched": len(fkeys), "people": people,
                  "scoring": "commits_on_topic + 2*reviews_on_topic + 40*PageRank(reviewer->author)"}, col, cy, total_ms)


def bus_factor(g: Graph, repo: str | None = None, limit: int = 5) -> dict:
    cy = []
    t0 = time.perf_counter()
    q1 = ("CALL algo.betweenness({nodeLabels: ['Person'], relationshipTypes: ['COLLABORATES'], samplingSize: 64, samplingSeed: 7}) "
          "YIELD node, score WITH node, score WHERE score > 0 RETURN node AS p, score ORDER BY score DESC LIMIT $lim")
    rows1, col, _ = run(g, q1, {"lim": int(limit)})
    cy.append(q1)
    q2 = ("MATCH (f:File)<-[:TOUCHES]-(p:Person) WHERE NOT coalesce(p.is_bot, false) " + ("AND f.repo = $repo " if repo else "")
          + "WITH f, collect(p) AS ps WHERE size(ps) = 1 WITH ps[0] AS p, collect(f.path) AS files "
          "RETURN p, size(files) AS sole_owned_files, files[0..6] AS sample ORDER BY sole_owned_files DESC LIMIT $lim")
    rows2, col2, _ = run(g, q2, {"repo": repo, "lim": int(limit)})
    cy.append(q2)
    col.nodes.update(col2.nodes); col.sources.update(col2.sources)
    q3 = ("CALL algo.betweenness({nodeLabels: ['File'], relationshipTypes: ['CO_CHANGED'], samplingSize: 64, samplingSeed: 7}) "
          "YIELD node, score WITH node, score WHERE score > 0 " + ("AND node.repo = $repo " if repo else "") + "RETURN node AS f, score ORDER BY score DESC LIMIT $lim")
    rows3, col3, _ = run(g, q3, {"repo": repo, "lim": int(limit)})
    cy.append(q3)
    col.nodes.update(col3.nodes); col.sources.update(col3.sources)
    q4 = ("MATCH (f:File) " + ("WHERE f.repo = $repo " if repo else "") + "OPTIONAL MATCH (f)<-[:TOUCHES]-(p:Person) WHERE NOT coalesce(p.is_bot, false) "
          "WITH f, count(p) AS owners RETURN count(f) AS files, sum(CASE WHEN owners = 1 THEN 1 ELSE 0 END) AS single_owner_files")
    rows4, _, _ = run(g, q4, {"repo": repo})
    cy.append(q4)
    res = {"people_bridges": [{"person": _slim(r["p"]), "betweenness": round(float(r["score"]), 2)} for r in rows1],
           "sole_owners": [{"person": _slim(r["p"]), "sole_owned_files": r["sole_owned_files"], "sample": r["sample"]} for r in rows2],
           "critical_files": [{"file": _slim(r["f"]), "betweenness": round(float(r["score"]), 2)} for r in rows3],
           "totals": rows4[0] if rows4 else {},
           "reading": "people_bridges = betweenness over who-touched-the-same-files (losing them disconnects groups); "
                      "sole_owners = files only one human has ever touched; critical_files = betweenness over files that change together"}
    return _pack(res, col, cy, int((time.perf_counter() - t0) * 1000))


def clusters(g: Graph, repo: str | None = None, limit: int = 6) -> dict:
    t0 = time.perf_counter()
    cy = []
    q = ("CALL algo.labelPropagation({nodeLabels: ['File'], relationshipTypes: ['CO_CHANGED'], maxIterations: 10}) "
         "YIELD node, communityId WITH node, communityId " + ("WHERE node.repo = $repo " if repo else "")
         + "WITH communityId, collect(node) AS fs WHERE size(fs) >= 3 "
         "RETURN communityId, size(fs) AS files, [f IN fs[0..8] | f.path] AS sample, [f IN fs | f.key] AS keys ORDER BY files DESC LIMIT $lim")
    rows, col, _ = run(g, q, {"repo": repo, "lim": int(limit)})
    cy.append(q)
    out = []
    for r in rows:
        q2 = ("MATCH (p:Person)-[t:TOUCHES]->(f:File) WHERE f.key IN $keys AND NOT coalesce(p.is_bot, false) WITH p, sum(t.count) AS n ORDER BY n DESC LIMIT 2 RETURN p, n")
        rows2, col2, _ = run(g, q2, {"keys": r["keys"]})
        col.nodes.update(col2.nodes); col.sources.update(col2.sources)
        out.append({"community": r["communityId"], "files": r["files"], "sample": r["sample"], "main_owners": [{"person": _slim(x["p"]), "commits": x["n"]} for x in rows2]})
    if rows:
        cy.append(q2)
    return _pack({"clusters": out, "method": "CDLP label propagation over File-CO_CHANGED (files changed in the same commit)"}, col, cy, int((time.perf_counter() - t0) * 1000))


def search(g: Graph, text: str, kinds: list[str] | None = None, limit: int = 6, repo: str | None = None) -> dict:
    t0 = time.perf_counter()
    limit = max(1, min(int(limit or 6), 12))
    kinds = [k for k in (kinds or ["PR", "Issue", "Comment", "Doc", "Commit"]) if k in ("PR", "Issue", "Comment", "Doc", "Commit", "File")]
    col = PathCollector()
    cy: list[str] = []
    hits = []
    for label in kinds:
        rows, c, used, _ = _fulltext(g, label, text, limit, repo)
        cy += used
        col.nodes.update(c.nodes); col.sources.update(c.sources)
        for r in rows:
            n = r["node"]
            props = n.get("props", {})
            snippet = (props.get("text") or props.get("body") if label == "Comment" else None) or (props.get("text") or props.get("message") or props.get("title") or props.get("path") or "")
            snippet = (snippet or "")[:240]
            if label == "Comment":
                props = {**props, "on": props.get("target")}
            hits.append({"kind": label, "node": {k: v for k, v in n.items() if k != "props"}, "state": props.get("state"), "snippet": snippet, "score": round(float(r["score"]), 2)})
    hits.sort(key=lambda h: h["score"], reverse=True)
    hits = hits[: limit * 2]
    keep = {h["node"]["id"] for h in hits}
    col.nodes = {k: v for k, v in col.nodes.items() if k in keep}
    col.sources = {k: v for k, v in col.sources.items() if any(h["node"].get("key") == v.get("key") for h in hits)}
    return _pack({"query": text, "hits": hits}, col, cy, int((time.perf_counter() - t0) * 1000))


def node_context(g: Graph, ref: str, repo: str | None = None) -> dict:
    """Everything directly connected to a PR / issue / commit / person / file: reviewers, closes, reverts, commits, files."""
    t0 = time.perf_counter()
    cands, cy = resolve_ref(g, ref, repo)
    if not cands:
        return _pack({"error": f"nothing matches {ref!r}"}, None, cy, int((time.perf_counter() - t0) * 1000))
    nid = _node_id(cands[0])
    q = ("MATCH (n) WHERE id(n) = $id "
         "OPTIONAL MATCH (n)-[r]-(m) WHERE type(r) IN $rels AND NOT coalesce(m.is_bot, false) "
         "OPTIONAL MATCH (cb:Person {is_bot: true})-[:AUTHORED]->(m) "
         "WITH n, r, m WHERE cb IS NULL "
         "WITH n, r, m ORDER BY CASE type(r) WHEN 'REVERTS' THEN 0 WHEN 'CLOSES' THEN 1 WHEN 'MERGED' THEN 1 WHEN 'REVIEWED' THEN 2 WHEN 'AUTHORED' THEN 3 WHEN 'PART_OF' THEN 4 WHEN 'MENTIONS' THEN 5 WHEN 'ON' THEN 6 ELSE 7 END "
         "RETURN n, collect([r, m])[0..60] AS around")
    rows, col, ms = run(g, q, {"id": nid, "rels": PATH_RELS})
    cy.append(q)
    neighbours = []
    for pair in (rows[0]["around"] if rows else []):
        r, m = pair
        if not r or not m:
            continue
        neighbours.append({"rel": r["type"], "direction": "out" if r["from"] == f"n{nid}" else "in", "node": {k: v for k, v in m.items() if k != "props"},
                           "state": (m.get("props") or {}).get("state"), "props": r.get("props")})
    # second hop for reverts: what did the reverted thing close / which issue reported the problem
    extra = []
    if any(x["rel"] == "REVERTS" for x in neighbours):
        q2 = ("MATCH (n)-[:REVERTS]-(t) WHERE id(n) = $id OPTIONAL MATCH (t)-[r2:CLOSES|MENTIONS|PART_OF]-(z) "
              "RETURN t, collect([r2, z])[0..20] AS around2")
        rows2, col2, _ = run(g, q2, {"id": nid})
        cy.append(q2)
        col.nodes.update(col2.nodes); col.edges.update(col2.edges); col.sources.update(col2.sources)
        for r2 in rows2:
            for pair in r2["around2"]:
                if pair[0] and pair[1]:
                    extra.append({"via": r2["t"]["title"], "rel": pair[0]["type"], "node": {k: v for k, v in pair[1].items() if k != "props"}})
    node = rows[0]["n"] if rows else cands[0]
    props = {k: v for k, v in (node.get("props") or {}).items() if k not in ("body", "text")}
    body = (node.get("props") or {}).get("body") or (node.get("props") or {}).get("message")
    return _pack({"node": {k: v for k, v in node.items() if k != "props"}, "props": props, "body": (body or "")[:1200], "neighbours": neighbours, "second_hop": extra},
                 col, cy, int((time.perf_counter() - t0) * 1000))


def owner_history(g: Graph, file_ref: str, repo: str | None = None) -> dict:
    t0 = time.perf_counter()
    cands, cy = resolve_ref(g, file_ref, repo)
    files = [c for c in cands if c.get("label") == "File"]
    if not files:
        return _pack({"error": f"no file matches {file_ref!r}"}, None, cy, int((time.perf_counter() - t0) * 1000))
    q = ("MATCH (f:File)-[:HAS_FACT]->(x:Fact {kind:'owner'}) WHERE id(f) = $id "
         "OPTIONAL MATCH (x)-[:ABOUT]->(p:Person) OPTIONAL MATCH (x)-[s:SUPERSEDES]->(old:Fact) "
         "RETURN f, x, p, s, old ORDER BY x.computed_at DESC")
    rows, col, ms = run(g, q, {"id": _node_id(files[0])})
    cy.append(q)
    hist = [{"owner": _slim(r["p"]), "since": (r["x"].get("props") or {}).get("computed_at"), "current": (r["x"].get("props") or {}).get("current"),
             "score": (r["x"].get("props") or {}).get("score"), "superseded_previous": bool(r["old"])} for r in rows]
    q2 = "MATCH (p:Person)-[t:TOUCHES]->(f:File) WHERE id(f) = $id RETURN p, t ORDER BY t.count DESC LIMIT 5"
    rows2, col2, _ = run(g, q2, {"id": _node_id(files[0])})
    cy.append(q2)
    col.nodes.update(col2.nodes); col.edges.update(col2.edges); col.sources.update(col2.sources)
    return _pack({"file": _slim(files[0]), "owner_facts": hist, "contributors": [{"person": _slim(r["p"]), "commits": (r["t"].get("props") or {}).get("count"), "last": (r["t"].get("props") or {}).get("last")} for r in rows2]},
                 col, cy, int((time.perf_counter() - t0) * 1000))


def recall(g: Graph, question: str, limit: int = 3) -> dict:
    """Earlier questions/answers (Episode nodes) that match this question, from any agent that writes to this graph."""
    rows, col, used, ms = _fulltext(g, "Episode", question, limit)
    eps = []
    for r in rows:
        pp = r["node"].get("props") or {}
        eps.append({"episode_id": pp.get("id"), "question": pp.get("question"), "answer": (pp.get("answer") or "")[:600],
                    "at": pp.get("created_at"), "agent": pp.get("agent") or ("mcp" if "mcp" in (pp.get("tools") or []) else "chat"),
                    "tools": pp.get("tools"), "score": round(float(r["score"]), 2)})
    return _pack({"episodes": eps}, col, used, ms)


def record_action(g: Graph, org: str, kind: str, repo: str, target_key: str, body: str, url: str | None,
                  suggested_keys: list[str] | None = None, cited_keys: list[str] | None = None, posted: bool = True, agent: str = "triage") -> dict:
    """Write what an agent DID to the graph: (Action)-[:ON]->(Issue|PR), -[:SUGGESTS]->(Person), -[:CITES]->(evidence).
    Other agents (chat, MCP clients) see it through recent(kind='Action') and node_context."""
    t0 = time.perf_counter()
    aid = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    q = ("MATCH (t) WHERE (t:Issue OR t:PR) AND t.key = $target "
         "CREATE (a:Action {key:$key, id:$id, org:$org, kind:$kind, repo:$repo, target:$target, body:$body, url:$url, posted:$posted, agent:$agent, created_at:$now}) "
         "CREATE (a)-[:ON]->(t) "
         "WITH a UNWIND $suggested AS sk MATCH (p:Person {key: sk}) MERGE (a)-[:SUGGESTS]->(p) "
         "WITH DISTINCT a UNWIND $cited AS ck MATCH (n) WHERE n.key = ck AND (n:PR OR n:Issue OR n:Commit OR n:File OR n:Person OR n:Comment) MERGE (a)-[:CITES]->(n) "
         "RETURN count(DISTINCT n) AS cited")
    res = g.query(q, {"key": f"action:{aid}", "id": aid, "org": org, "kind": kind, "repo": repo, "target": target_key, "body": body[:4000], "url": url,
                      "posted": bool(posted), "agent": agent, "now": now, "suggested": suggested_keys or [], "cited": cited_keys or []})
    cited = int(res.result_set[0][0]) if res.result_set else 0
    return _pack({"action_id": aid, "kind": kind, "target": target_key, "posted": posted, "cited": cited}, None, [q], int((time.perf_counter() - t0) * 1000))


def remember(g: Graph, org: str, question: str, answer: str, source_keys: list[str] | None = None, tools_used: list[str] | None = None, agent: str = "chat") -> dict:
    t0 = time.perf_counter()
    eid = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    q = ("CREATE (e:Episode {id:$id, org:$org, question:$q, answer:$a, created_at:$now, tools:$tools, agent:$agent}) "
         "WITH e UNWIND $keys AS k MATCH (n) WHERE n.key = k AND (n:PR OR n:Issue OR n:Commit OR n:Person OR n:File OR n:Release OR n:Doc) "
         "MERGE (e)-[:CITES]->(n) RETURN count(n) AS cited")
    res = g.query(q, {"id": eid, "org": org, "q": question[:1000], "a": answer[:4000], "now": now, "tools": tools_used or [], "keys": source_keys or [], "agent": agent})
    cited = int(res.result_set[0][0]) if res.result_set else 0
    return _pack({"episode_id": eid, "cited": cited}, None, [q], int((time.perf_counter() - t0) * 1000))


def repo_overview(g: Graph) -> dict:
    q = ("MATCH (r:Repo) OPTIONAL MATCH (r)<-[:IN_REPO]-(x) WITH r, labels(x)[0] AS l, count(x) AS n "
         "RETURN r.full_name AS repo, r.description AS description, r.stars AS stars, collect([l, n]) AS counts ORDER BY stars DESC")
    rows, col, ms = run(g, q)
    return _pack(rows, None, [q], ms)


def reverts(g: Graph, repo: str | None = None, limit: int = 10) -> dict:
    """Every revert in the graph: reverting commit/PR -> reverted commit/PR, with authors, dates and the issues involved."""
    t0 = time.perf_counter()
    q = ("MATCH (a)-[rv:REVERTS]->(b) " + ("WHERE a.repo = $repo " if repo else "")
         + "OPTIONAL MATCH (pa:Person)-[:AUTHORED]->(a) OPTIONAL MATCH (pb:Person)-[:AUTHORED]->(b) "
         "OPTIONAL MATCH (a)-[:PART_OF]->(apr:PR) OPTIONAL MATCH (b)-[:PART_OF]->(bpr:PR) "
         "OPTIONAL MATCH (b)-[:CLOSES]->(bi:Issue) OPTIONAL MATCH (a)-[:MENTIONS]->(ai:Issue) "
         "RETURN a, b, rv, pa, pb, apr, bpr, collect(DISTINCT bi)[0..3] AS closed_by_original, collect(DISTINCT ai)[0..3] AS mentioned_by_revert "
         "ORDER BY coalesce(a.date, a.merged_at, a.created_at) DESC LIMIT $lim")
    rows, col, ms = run(g, q, {"repo": repo, "lim": int(limit)})
    items = []
    for r in rows:
        a, b = r["a"], r["b"]
        items.append({
            "revert": _slim(a), "revert_date": (a.get("props") or {}).get("date") or (a.get("props") or {}).get("merged_at"),
            "revert_author": _slim(r["pa"]), "revert_pr": _slim(r["apr"]),
            "reverted": _slim(b), "reverted_date": (b.get("props") or {}).get("date") or (b.get("props") or {}).get("merged_at"),
            "reverted_author": _slim(r["pb"]), "reverted_pr": _slim(r["bpr"]),
            "revert_message": ((a.get("props") or {}).get("message") or (a.get("props") or {}).get("body") or "")[:400],
            "issues_closed_by_original": [_slim(x) for x in r["closed_by_original"] if x],
            "issues_mentioned_by_revert": [_slim(x) for x in r["mentioned_by_revert"] if x],
        })
    return _pack({"reverts": items, "count": len(items), "scope": repo or "whole org",
                  "note": "REVERTS edges come from 'This reverts commit <sha>' in commit messages and 'Reverts owner/repo#N' in PR bodies"},
                 col, [q], int((time.perf_counter() - t0) * 1000))


def recent(g: Graph, kind: str = "PR", repo: str | None = None, state: str | None = None, limit: int = 8, person: str | None = None) -> dict:
    """Most recent PRs / issues / commits / releases, optionally filtered by repo, state (OPEN|CLOSED|MERGED) or author login."""
    t0 = time.perf_counter()
    kind = kind if kind in ("PR", "Issue", "Commit", "Release", "Action", "Episode") else "PR"
    date = {"PR": "coalesce(n.merged_at, n.updated_at, n.created_at)", "Issue": "coalesce(n.updated_at, n.created_at)", "Commit": "n.date", "Release": "n.published_at",
            "Action": "n.created_at", "Episode": "n.created_at"}[kind]
    where = []
    if repo:
        where.append("n.repo = $repo")
    if state and kind in ("PR", "Issue"):
        where.append("n.merged = true" if state.upper() == "MERGED" else "n.state = $state")
    q = (f"MATCH (n:{kind}) " + ("WHERE " + " AND ".join(where) + " " if where else "")
         + "OPTIONAL MATCH (p:Person)-[:AUTHORED]->(n) " + ("WITH n, p WHERE toLower(coalesce(p.login, p.key)) = toLower($person) " if person else "WITH n, p ")
         + f"RETURN n, p, {date} AS when_ ORDER BY when_ DESC LIMIT $lim")
    rows, col, ms = run(g, q, {"repo": repo, "state": (state or "").upper(), "person": person, "lim": int(limit)})
    items = []
    for r in rows:
        pp = r["n"].get("props") or {}
        item = {"item": _slim(r["n"]), "author": _slim(r["p"]), "when": r["when_"], "state": pp.get("state"), "merged": pp.get("merged")}
        if kind == "Action":
            item.update({"agent": pp.get("agent"), "action": pp.get("kind"), "on": pp.get("target"), "posted": pp.get("posted"), "url": pp.get("url"), "body": (pp.get("body") or "")[:500]})
        if kind == "Episode":
            item.update({"agent": pp.get("agent") or "chat", "question": pp.get("question"), "answer": (pp.get("answer") or "")[:300]})
        items.append(item)
    return _pack({"kind": kind, "items": items, "filters": {"repo": repo, "state": state, "person": person}}, col, [q], int((time.perf_counter() - t0) * 1000))


def top_reviewers(g: Graph, repo: str | None = None, limit: int = 6) -> dict:
    """Most trusted human reviewers: PageRank over reviewer->author edges, review counts, and who they review for."""
    t0 = time.perf_counter()
    pr_scores, prq = pagerank_reviewers(g)
    q = ("MATCH (r:Person)-[x:REVIEWED]->(pr:PR) WHERE NOT coalesce(r.is_bot, false) " + ("AND pr.repo = $repo " if repo else "")
         + "WITH r, count(x) AS reviews, count(DISTINCT pr.repo) AS repos "
         "OPTIONAL MATCH (r)-[f:REVIEWED_FOR]->(a:Person) WHERE NOT coalesce(a.is_bot, false) "
         "WITH r, reviews, repos, a, f ORDER BY f.count DESC "
         "RETURN r, reviews, repos, collect([a, f.count])[0..4] AS reviews_for, collect(f)[0..4] AS edges ORDER BY reviews DESC LIMIT 25")
    rows, col, ms = run(g, q, {"repo": repo})
    people = []
    for r in rows:
        key = (r["r"].get("key") or (r["r"].get("props") or {}).get("key"))
        score = pr_scores.get(key, 0.0)
        people.append({"person": _slim(r["r"]), "reviews": r["reviews"], "repos": r["repos"], "reviewer_pagerank": round(score, 4),
                       "reviews_for": [{"author": _slim(a), "count": c} for a, c in r["reviews_for"] if a]})
    people.sort(key=lambda x: (x["reviewer_pagerank"], x["reviews"]), reverse=True)
    return _pack({"reviewers": people[:limit], "method": "PageRank over Person-REVIEWED_FOR->Person (reviewer -> author), humans only; ties by review count"},
                 col, [q, prq], int((time.perf_counter() - t0) * 1000))


def _when(*vals):
    for v in vals:
        if v:
            return v
    return ""


def decision_trail(g: Graph, ref: str, repo: str | None = None, max_comments: int = 12) -> dict:
    """The full story of a PR (or of the PR that closed an issue), in time order: the issue that motivated it and what was
    said there, the PR itself, review verdicts and review comments, discussion comments, who merged it and when, the release
    it shipped in, and any revert (with the revert's own discussion). This is the 'trace a decision to its source' tool."""
    t0 = time.perf_counter()
    cands, cy = resolve_ref(g, ref, repo)
    if not cands:
        return _pack({"error": f"nothing matches {ref!r}"}, None, cy, int((time.perf_counter() - t0) * 1000))
    node = cands[0]
    # an issue -> follow to the PR(s) that closed it
    if node.get("label") == "Issue":
        q0 = "MATCH (i:Issue)<-[:CLOSES]-(pr:PR) WHERE id(i) = $id RETURN pr ORDER BY pr.merged_at DESC LIMIT 1"
        rows0, _, _ = run(g, q0, {"id": _node_id(node)})
        cy.append(q0)
        if rows0:
            node = rows0[0]["pr"]
    if node.get("label") == "Commit":
        q0 = "MATCH (c:Commit)-[:PART_OF]->(pr:PR) WHERE id(c) = $id RETURN pr LIMIT 1"
        rows0, _, _ = run(g, q0, {"id": _node_id(node)})
        cy.append(q0)
        if rows0:
            node = rows0[0]["pr"]
    if node.get("label") != "PR":
        return _pack({"error": "decision_trail needs a PR, an issue closed by a PR, or a commit that is part of a PR", "resolved": _slim(node)}, None, cy, int((time.perf_counter() - t0) * 1000))
    nid = _node_id(node)
    q = ("MATCH (pr:PR) WHERE id(pr) = $id "
         "OPTIONAL MATCH (author:Person)-[au:AUTHORED]->(pr) "
         "OPTIONAL MATCH (merger:Person)-[m:MERGED]->(pr) "
         "OPTIONAL MATCH (pr)-[cl:CLOSES]->(iss:Issue) OPTIONAL MATCH (ia:Person)-[iau:AUTHORED]->(iss) "
         "OPTIONAL MATCH (rv:Person)-[r:REVIEWED]->(pr) WHERE NOT coalesce(rv.is_bot, false) "
         "OPTIONAL MATCH (pr)-[ri:RELEASED_IN]->(rel:Release) "
         "OPTIONAL MATCH (pr)<-[rvx:REVERTS]-(revpr:PR) OPTIONAL MATCH (rva:Person)-[rvau:AUTHORED]->(revpr) "
         "OPTIONAL MATCH (pr)-[:REVERTS]->(orig:PR) "
         "OPTIONAL MATCH (c:Commit)-[:PART_OF]->(pr) "
         "OPTIONAL MATCH (c)<-[rvcx:REVERTS]-(rvc:Commit) OPTIONAL MATCH (rvca:Person)-[rvcau:AUTHORED]->(rvc) "
         "RETURN pr, author, au, merger, m, collect(DISTINCT [iss, ia, cl, iau]) AS issues, collect(DISTINCT [rv, r]) AS reviews, rel, ri, "
         "collect(DISTINCT [revpr, rva, rvx, rvau]) AS reverted_by_prs, orig, "
         "collect(DISTINCT CASE WHEN rvc IS NULL THEN NULL ELSE [rvc, rvca, c, rvcx, rvcau] END) AS reverted_by_commits, count(DISTINCT c) AS commits")
    rows, col, ms = run(g, q, {"id": nid})
    cy.append(q)
    r0 = rows[0]
    pr = r0["pr"]; pp = pr.get("props") or {}
    r0["issues"] = [x for x in r0["issues"] if x and x[0]]
    r0["reverted_by_prs"] = [x for x in r0["reverted_by_prs"] if x and x[0]]
    r0["reverted_by_commits"] = [x for x in r0["reverted_by_commits"] if x and x[0]]
    issue_keys = [i[0]["key"] for i in r0["issues"]]
    targets = [pr["key"]] + issue_keys + [x[0]["key"] for x in r0["reverted_by_prs"]]
    qc = ("MATCH (c:Comment)-[on:ON]->(t) WHERE t.key IN $targets "
          "OPTIONAL MATCH (p:Person)-[pa:AUTHORED]->(c) "
          "WITH c, p, t, on, pa WHERE NOT coalesce(p.is_bot, false) "
          "RETURN c, p, on, pa, t.key AS on_key ORDER BY c.created_at ASC LIMIT $lim")
    rowsc, colc, _ = run(g, qc, {"targets": targets, "lim": int(max_comments) * 2})
    cy.append(qc)
    qb = "MATCH (c:Comment)-[:ON]->(t) WHERE t.key IN $targets MATCH (p:Person {is_bot:true})-[:AUTHORED]->(c) RETURN count(c)"
    rowsb, _, _ = run(g, qb, {"targets": targets})
    cy.append(qb)
    bot_comments = int(rowsb[0]["count(c)"]) if rowsb else 0
    col.nodes.update(colc.nodes); col.edges.update(colc.edges); col.sources.update(colc.sources)

    timeline: list[dict] = []
    for iss, ia, _cl, _iau in r0["issues"]:
        if iss:
            ip = iss.get("props") or {}
            timeline.append({"when": ip.get("created_at"), "event": "issue opened", "who": _slim(ia), "node": _slim(iss), "text": ((ip.get("body") or "")[:300])})
    timeline.append({"when": pp.get("created_at"), "event": "PR opened", "who": _slim(r0["author"]), "node": _slim(pr), "text": (pp.get("body") or "")[:400]})
    for rv, r in r0["reviews"]:
        if rv and r and not (rv.get("props") or {}).get("is_bot"):
            rp = r.get("props") or {}
            timeline.append({"when": rp.get("at"), "event": f"review: {rp.get('state')}", "who": _slim(rv), "node": _slim(pr), "text": ""})
    for rc in rowsc:
        c = rc["c"]; cp = c.get("props") or {}
        on = rc["on_key"]
        kind = "review comment" if cp.get("kind") == "review" else "comment"
        where = "on the PR" if on == pr["key"] else ("on the revert PR" if on not in issue_keys else "on the issue")
        timeline.append({"when": cp.get("created_at"), "event": f"{kind} {where}", "who": _slim(rc["p"]), "node": _slim(c), "text": " ".join((cp.get("body") or "").split())[:300]})
    if r0["merger"] or pp.get("merged_at"):
        mp = (r0["m"] or {}).get("props") or {}
        timeline.append({"when": mp.get("at") or pp.get("merged_at"), "event": "merged", "who": _slim(r0["merger"]), "node": _slim(pr), "text": f"merge commit {str(pp.get('merge_sha') or '')[:7]}"})
    if r0["rel"]:
        relp = r0["rel"].get("props") or {}
        timeline.append({"when": relp.get("published_at"), "event": "shipped in release", "who": None, "node": _slim(r0["rel"]), "text": relp.get("tag") or ""})
    for revpr, rva, _rvx, _rvau in r0["reverted_by_prs"]:
        if revpr:
            rp = revpr.get("props") or {}
            timeline.append({"when": rp.get("merged_at") or rp.get("created_at"), "event": "REVERTED by PR", "who": _slim(rva), "node": _slim(revpr), "text": (rp.get("body") or rp.get("title") or "")[:300]})
    for rvc, rvca, c, _rvcx, _rvcau in r0["reverted_by_commits"]:
        if rvc:
            cp = rvc.get("props") or {}
            timeline.append({"when": cp.get("date"), "event": "REVERTED by commit", "who": _slim(rvca), "node": _slim(rvc), "text": (cp.get("message") or "")[:300]})
    timeline = [t for t in timeline if t.get("node")]
    timeline.sort(key=lambda t: str(t.get("when") or ""))
    summary = {
        "pr": _slim(pr), "title": pp.get("title"), "state": pp.get("state"), "merged": pp.get("merged"),
        "author": _slim(r0["author"]), "merged_by": _slim(r0["merger"]), "merged_at": pp.get("merged_at"),
        "motivating_issues": [_slim(i[0]) for i in r0["issues"]],
        "approvals": [_slim(rv) for rv, r in r0["reviews"] if rv and not (rv.get("props") or {}).get("is_bot") and ((r.get("props") or {}).get("state") == "APPROVED")],
        "changes_requested_by": [_slim(rv) for rv, r in r0["reviews"] if rv and ((r.get("props") or {}).get("state") == "CHANGES_REQUESTED")],
        "release": _slim(r0["rel"]), "commits": r0["commits"],
        "reverted": bool(r0["reverted_by_prs"] or r0["reverted_by_commits"]),
        "this_pr_reverts": _slim(r0["orig"]),
        "human_comments": len(rowsc), "bot_comments_hidden": bot_comments,
    }
    return _pack({"summary": summary, "timeline": timeline[: max_comments + 12],
                  "reading": "Chronological trail: motivating issue -> PR -> reviews and comments -> merged by -> release -> revert. Comments are the recorded discussion; quote them when explaining why."},
                 col, cy, int((time.perf_counter() - t0) * 1000))


def impact_of_leaving(g: Graph, person: str, limit: int = 8) -> dict:
    """Blast radius if one person leaves: files only they touched (by directory), directories they dominate, authors only they
    review, their open work, and who could cover (strongest collaborators per area)."""
    t0 = time.perf_counter()
    cands, cy = resolve_ref(g, person)
    people = [c for c in cands if c.get("label") == "Person"]
    if not people:
        return _pack({"error": f"no person matches {person!r}"}, None, cy, int((time.perf_counter() - t0) * 1000))
    pid = _node_id(people[0])
    col = PathCollector()
    # 1) files only this person has touched (humans only), grouped by directory
    q1 = ("MATCH (p:Person)-[t:TOUCHES]->(f:File) WHERE id(p) = $id "
          "OPTIONAL MATCH (o:Person)-[:TOUCHES]->(f) WHERE o <> p AND NOT coalesce(o.is_bot, false) "
          "WITH p, f, t, count(o) AS others "
          "WITH p, sum(CASE WHEN others = 0 THEN 1 ELSE 0 END) AS sole, count(f) AS total, "
          "     collect(CASE WHEN others = 0 THEN {dir: f.repo + ':' + coalesce(f.dir, ''), path: f.path, commits: t.count} ELSE NULL END) AS sole_files "
          "RETURN p, sole, total, [x IN sole_files WHERE x IS NOT NULL] AS sole_files")
    rows1, c1, _ = run(g, q1, {"id": pid})
    cy.append(q1)
    col.nodes.update(c1.nodes); col.sources.update(c1.sources)
    r1 = rows1[0] if rows1 else {"sole": 0, "total": 0, "sole_files": []}
    by_dir: dict[str, dict] = {}
    for x in r1["sole_files"]:
        d = by_dir.setdefault(x["dir"], {"dir": x["dir"], "files": 0, "commits": 0, "sample": []})
        d["files"] += 1; d["commits"] += int(x["commits"] or 0)
        if len(d["sample"]) < 3:
            d["sample"].append(x["path"])
    sole_dirs = sorted(by_dir.values(), key=lambda d: d["files"], reverse=True)[:limit]
    # 2) directories this person dominates (share of commits >= 60%, at least 3 files)
    q2 = ("MATCH (p:Person)-[t:TOUCHES]->(f:File) WHERE id(p) = $id "
          "WITH p, f.repo + ':' + coalesce(f.dir, '') AS dir, sum(t.count) AS mine, collect(DISTINCT f.key) AS fkeys "
          "WHERE size(fkeys) >= 3 "
          "MATCH (o:Person)-[t2:TOUCHES]->(f2:File) WHERE f2.key IN fkeys AND NOT coalesce(o.is_bot, false) "
          "WITH dir, mine, size(fkeys) AS files, sum(t2.count) AS total "
          "WITH dir, files, mine, total, toFloat(mine) / toFloat(total) AS share WHERE share >= 0.6 "
          "RETURN dir, files, mine, total, round(share * 100) AS share_pct ORDER BY share_pct DESC, files DESC LIMIT $lim")
    rows2, _, _ = run(g, q2, {"id": pid, "lim": int(limit)})
    cy.append(q2)
    # 3) authors only this person reviews (human reviewers)
    q3 = ("MATCH (p:Person)-[:REVIEWED]->(pr:PR)<-[:AUTHORED]-(a:Person) WHERE id(p) = $id AND a <> p AND NOT coalesce(a.is_bot, false) "
          "WITH p, a, count(DISTINCT pr) AS n "
          "OPTIONAL MATCH (o:Person)-[:REVIEWED]->(pr2:PR)<-[:AUTHORED]-(a) WHERE o <> p AND NOT coalesce(o.is_bot, false) "
          "WITH a, n, count(DISTINCT o) AS other_reviewers WHERE other_reviewers = 0 "
          "RETURN a, n ORDER BY n DESC LIMIT $lim")
    rows3, c3, _ = run(g, q3, {"id": pid, "lim": int(limit)})
    cy.append(q3)
    col.nodes.update(c3.nodes); col.sources.update(c3.sources)
    # 4) open work: open PRs authored, open issues assigned
    q4 = ("MATCH (p:Person) WHERE id(p) = $id "
          "OPTIONAL MATCH (p)-[:AUTHORED]->(pr:PR) WHERE pr.state = 'OPEN' "
          "OPTIONAL MATCH (i:Issue)-[:ASSIGNED_TO]->(p) WHERE i.state = 'OPEN' "
          "RETURN collect(DISTINCT pr)[0..6] AS open_prs, count(DISTINCT pr) AS n_prs, collect(DISTINCT i)[0..6] AS open_issues, count(DISTINCT i) AS n_issues")
    rows4, c4, _ = run(g, q4, {"id": pid})
    cy.append(q4)
    col.nodes.update(c4.nodes); col.sources.update(c4.sources)
    # 5) who could cover: strongest collaborators overall, and per dominated/sole directory the next most active human
    q5 = ("MATCH (p:Person)-[c:COLLABORATES]->(o:Person) WHERE id(p) = $id RETURN o, c.files AS shared ORDER BY shared DESC LIMIT 5")
    rows5, c5, _ = run(g, q5, {"id": pid})
    cy.append(q5)
    col.nodes.update(c5.nodes); col.edges.update(c5.edges); col.sources.update(c5.sources)
    dirs_for_cover = [d["dir"] for d in sole_dirs[:5]] + [r["dir"] for r in rows2[:5]]
    cover: dict[str, dict] = {}
    if dirs_for_cover:
        q6 = ("UNWIND $dirs AS d WITH d, split(d, ':')[0] AS repo, substring(d, size(split(d, ':')[0]) + 1) AS dir "
              "MATCH (o:Person)-[t:TOUCHES]->(f:File) WHERE f.repo = repo AND coalesce(f.dir, '') = dir AND id(o) <> $id AND NOT coalesce(o.is_bot, false) "
              "WITH d, o, sum(t.count) AS n ORDER BY n DESC WITH d, collect([o, n])[0..2] AS best RETURN d, best")
        rows6, c6, _ = run(g, q6, {"dirs": list(dict.fromkeys(dirs_for_cover)), "id": pid})
        cy.append(q6)
        col.nodes.update(c6.nodes); col.sources.update(c6.sources)
        for r in rows6:
            cover[r["d"]] = [{"person": _slim(o), "commits": n} for o, n in r["best"] if o]
    r4 = rows4[0] if rows4 else {}
    total = int(r1["total"] or 0); sole = int(r1["sole"] or 0)
    res = {
        "person": _slim(people[0]),
        "files_touched": total, "files_only_they_touched": sole, "sole_share_pct": round(100.0 * sole / total) if total else 0,
        "sole_owned_directories": [{**d, "who_could_cover": cover.get(d["dir"], [])} for d in sole_dirs],
        "dominated_directories": [{**r, "who_could_cover": cover.get(r["dir"], [])} for r in rows2],
        "authors_only_they_review": [{"author": _slim(r["a"]), "prs_reviewed": r["n"]} for r in rows3],
        "open_work": {"open_prs": r4.get("n_prs", 0), "open_prs_sample": [_slim(x) for x in (r4.get("open_prs") or []) if x],
                      "open_issues_assigned": r4.get("n_issues", 0), "open_issues_sample": [_slim(x) for x in (r4.get("open_issues") or []) if x]},
        "closest_collaborators": [{"person": _slim(r["o"]), "shared_files": r["shared"]} for r in rows5],
        "reading": "sole-owned = files no other human has ever touched; dominated = directories where this person made >=60% of commits; "
                   "who_could_cover = the next most active humans in that directory",
    }
    return _pack(res, col, cy, int((time.perf_counter() - t0) * 1000))


def teams(g: Graph, limit: int = 8) -> dict:
    """Two complementary views of team structure, both derived from the graph rather than an org chart:
    product_teams = people grouped by the repository they commit to most; collaboration_clusters = label propagation over the
    pruned collaboration graph (people who repeatedly touch the same files). Each group lists members, owned repos/directories
    and its internal lead reviewer."""
    t0 = time.perf_counter()
    cy = []

    def enrich(groups):
        out = []
        for gid, members in groups:
            keys = [m.get("key") or (m.get("props") or {}).get("key") for m in members]
            q2 = ("MATCH (p:Person)-[t:TOUCHES]->(f:File) WHERE p.key IN $keys "
                  "WITH f.repo AS repo, sum(t.count) AS n ORDER BY n DESC RETURN collect([repo, n])[0..3] AS repos")
            rows2, _, _ = run(g, q2, {"keys": keys})
            q3 = ("MATCH (p:Person)-[t:TOUCHES]->(f:File) WHERE p.key IN $keys AND coalesce(f.dir, '') <> '' "
                  "WITH f.repo + ':' + f.dir AS dir, sum(t.count) AS n ORDER BY n DESC RETURN collect([dir, n])[0..5] AS dirs")
            rows3, _, _ = run(g, q3, {"keys": keys})
            q4 = ("MATCH (r:Person)-[x:REVIEWED]->(pr:PR)<-[:AUTHORED]-(a:Person) WHERE r.key IN $keys AND a.key IN $keys AND r <> a "
                  "WITH r, count(x) AS n ORDER BY n DESC LIMIT 1 RETURN r, n")
            rows4, _, _ = run(g, q4, {"keys": keys})
            if not out:
                cy.extend([q2, q3, q4])
            out.append({"team": str(gid), "size": len(keys), "members": [_slim(m) for m in members][:12],
                        "repos": [{"repo": a, "commits": b} for a, b in (rows2[0]["repos"] if rows2 else [])],
                        "areas": [{"dir": a, "commits": b} for a, b in (rows3[0]["dirs"] if rows3 else [])],
                        "lead_reviewer": {"person": _slim(rows4[0]["r"]), "internal_reviews": rows4[0]["n"]} if rows4 else None})
        return out

    qa = ("MATCH (p:Person)-[:AUTHORED]->(c:Commit)-[:IN_REPO]->(r:Repo) WHERE NOT coalesce(p.is_bot, false) "
          "WITH p, r, count(c) AS n ORDER BY n DESC WITH p, collect(r.name)[0] AS main_repo "
          "WITH main_repo, collect(p) AS members WHERE size(members) >= 2 "
          "RETURN main_repo AS gid, members ORDER BY size(members) DESC LIMIT $lim")
    rows_a, col, _ = run(g, qa, {"lim": int(limit)})
    cy.append(qa)
    qb = ("CALL algo.labelPropagation({nodeLabels: ['Person'], relationshipTypes: ['STRONG_COLLAB'], maxIterations: 20}) "
          "YIELD node, communityId WITH communityId, node WHERE NOT coalesce(node.is_bot, false) "
          "WITH communityId, collect(node) AS members WHERE size(members) >= 2 "
          "RETURN 'cluster-' + toString(communityId) AS gid, members ORDER BY size(members) DESC LIMIT $lim")
    rows_b, col_b, _ = run(g, qb, {"lim": int(limit)})
    cy.append(qb)
    col.nodes.update(col_b.nodes); col.sources.update(col_b.sources)
    product = enrich([(r["gid"], r["members"]) for r in rows_a])
    clusters_ = enrich([(r["gid"], r["members"]) for r in rows_b])
    return _pack({"product_teams": product, "collaboration_clusters": clusters_,
                  "method": "product_teams: each person's most-committed repo; collaboration_clusters: CDLP label propagation over STRONG_COLLAB (people sharing >= 3 files)",
                  "reading": "Inferred from who works on what, not from an org chart. A person in a small product team but a big collaboration cluster is a bridge between teams."},
                 col, cy, int((time.perf_counter() - t0) * 1000))


def org_map(g: Graph, people: int = 28) -> dict:
    """Overview graph for the UI before any question: Repo nodes, the most active humans, CONTRIBUTES edges
    (Person -> Repo, weighted by commits) and the strongest COLLABORATES edges between those people."""
    t0 = time.perf_counter()
    cy = []
    q1 = ("MATCH (p:Person)-[:AUTHORED]->(c:Commit)-[:IN_REPO]->(r:Repo) WHERE NOT coalesce(p.is_bot, false) "
          "WITH p, r, count(c) AS n WITH p, sum(n) AS total, collect([r, n]) AS per_repo ORDER BY total DESC LIMIT $lim "
          "RETURN p, total, per_repo")
    rows, col, _ = run(g, q1, {"lim": int(people)})
    cy.append(q1)
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    keys = []
    for r in rows:
        p = r["p"]; keys.append(p.get("key") or (p.get("props") or {}).get("key"))
        nodes[p["id"]] = {**_slim(p), "weight": r["total"]}
        per_repo = sorted([x for x in r["per_repo"] if x and x[0]], key=lambda x: -x[1])
        for i, (repo, n) in enumerate(per_repo):
            nodes.setdefault(repo["id"], {**_slim(repo), "weight": 0})
            if n >= 2 or i == 0:  # always link a person to their main repo so nobody floats unconnected
                edges.append({"from": p["id"], "to": repo["id"], "type": "CONTRIBUTES", "props": {"commits": n}})
    # every repo gets its top 3 human contributors, so no repo floats unconnected in the overview
    q2 = ("MATCH (p:Person)-[:AUTHORED]->(c:Commit)-[:IN_REPO]->(r:Repo) WHERE NOT coalesce(p.is_bot, false) "
          "WITH r, p, count(c) AS n ORDER BY n DESC WITH r, collect([p, n])[0..3] AS top RETURN r, top")
    rows2, _, _ = run(g, q2)
    cy.append(q2)
    seen = {(e["from"], e["to"]) for e in edges}
    for r in rows2:
        repo = r["r"]
        nodes.setdefault(repo["id"], {**_slim(repo), "weight": 0})
        for p, n in r["top"]:
            if not p:
                continue
            nodes.setdefault(p["id"], {**_slim(p), "weight": n})
            if (p["id"], repo["id"]) not in seen:
                edges.append({"from": p["id"], "to": repo["id"], "type": "CONTRIBUTES", "props": {"commits": n}})
                seen.add((p["id"], repo["id"]))
    q2b = "MATCH (r:Repo) RETURN r"
    rows2b, _, _ = run(g, q2b)
    for r in rows2b:
        nodes.setdefault(r["r"]["id"], {**_slim(r["r"]), "weight": 0})
    rows2 = rows2b
    q3 = ("MATCH (a:Person)-[c:COLLABORATES]->(b:Person) WHERE a.key IN $keys AND b.key IN $keys AND a.key < b.key AND c.files >= 8 "
          "RETURN a, b, c.files AS files ORDER BY files DESC LIMIT 40")
    rows3, _, _ = run(g, q3, {"keys": keys})
    cy.append(q3)
    for r in rows3:
        edges.append({"from": r["a"]["id"], "to": r["b"]["id"], "type": "COLLABORATES", "props": {"files": r["files"]}})
    return _pack({"people": len(keys), "repos": len(rows2), "edges": len(edges)},
                 None, cy, int((time.perf_counter() - t0) * 1000)) | {"path": {"nodes": list(nodes.values()), "edges": edges}}


def suggest_questions(g: Graph, limit: int = 6) -> dict:
    """Questions worth asking THIS org, built from its own graph: its busiest area, its latest revert, its top
    contributor, its most recent merged PR. Shown on the empty chat screen instead of generic examples."""
    t0 = time.perf_counter()
    cy: list[str] = []
    out: list[str] = []
    q1 = ("MATCH (p:Person)-[t:TOUCHES]->(f:File) WHERE NOT coalesce(p.is_bot, false) AND coalesce(f.dir, '') <> '' "
          "AND NOT f.dir STARTS WITH '.' AND NOT f.dir CONTAINS 'test' AND NOT f.dir CONTAINS 'docs' "
          "WITH f.repo AS repo, f.dir AS dir, sum(t.count) AS n, count(DISTINCT p) AS people WHERE people >= 2 "
          "RETURN repo, dir, n ORDER BY n DESC LIMIT 1")
    r1, _, _ = run(g, q1); cy.append(q1)
    if r1:
        repo = r1[0]["repo"].split("/")[-1]; d = r1[0]["dir"]
        out.append(f"Who should review a change under {d} in {repo}, and why them?")
    q2 = ("MATCH (a)-[:REVERTS]->(b) OPTIONAL MATCH (a)-[:PART_OF]->(pr:PR) "
          "RETURN a, b, pr ORDER BY coalesce(a.date, a.merged_at, a.created_at) DESC LIMIT 1")
    r2, _, _ = run(g, q2); cy.append(q2)
    if r2:
        a, b, pr = r2[0]["a"], r2[0]["b"], r2[0]["pr"]
        props = (b.get("props") or {})
        repo = (props.get("repo") or "").split("/")[-1]
        if b.get("label") == "PR":
            out.append(f"Why was PR #{props.get('number')} in {repo} reverted, and who decided?")
        elif pr:
            out.append(f"Why was PR #{(pr.get('props') or {}).get('number')} in {repo} reverted, and who decided?")
        else:
            out.append(f"Why was commit {str(props.get('sha', ''))[:7]} in {repo} reverted, and who decided?")
    else:
        out.append("Which PRs or commits were reverted, and why? Who decided?")
    q3 = ("MATCH (p:Person)-[:AUTHORED]->(c:Commit) WHERE NOT coalesce(p.is_bot, false) "
          "RETURN coalesce(p.name, p.login) AS name, count(c) AS n ORDER BY n DESC LIMIT 1")
    r3, _, _ = run(g, q3); cy.append(q3)
    if r3 and r3[0]["name"]:
        out.append(f"What happens if {r3[0]['name']} leaves? Who could cover?")
    out.append("What teams exist here and who leads each?")
    q4 = ("MATCH (pr:PR {merged: true})-[:CLOSES]->(i:Issue) RETURN pr ORDER BY pr.merged_at DESC LIMIT 1")
    r4, _, _ = run(g, q4); cy.append(q4)
    if r4:
        pp = r4[0]["pr"].get("props") or {}
        out.append(f"What was the story behind PR #{pp.get('number')} in {(pp.get('repo') or '').split('/')[-1]}: which issue, who approved, who merged?")
    out.append("Who are the most trusted reviewers, and who do they review for?")
    out.append("What did I ask earlier, and what did the triage agent do recently?")
    return _pack({"questions": out[:limit]}, None, cy, int((time.perf_counter() - t0) * 1000))
