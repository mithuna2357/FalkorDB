"""Graph schema: indexes, derived relationships, and owner Facts with SUPERSEDES history.

Node labels : Person, Repo, Issue, PR, Commit, File, Release, Doc, Fact, Episode, Meta
Raw edges   : AUTHORED, REVIEWED, MERGED, TOUCHED, CLOSES, REVERTS, MENTIONS, PART_OF, IN_REPO, ASSIGNED_TO, RELEASED_IN, ON (Comment->PR|Issue)
Derived     : TOUCHES (Person->File), REVIEWED_FOR (reviewer->author), COLLABORATES (Person<->Person), CO_CHANGED (File<->File)
Facts       : (File)-[:HAS_FACT]->(Fact {kind:'owner'})-[:ABOUT]->(Person); (new)-[:SUPERSEDES]->(old)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from falkordb import Graph

log = logging.getLogger("schema")

RANGE_INDEXES = [
    ("Person", "key"), ("Repo", "key"), ("Issue", "key"), ("PR", "key"), ("Commit", "key"),
    ("File", "key"), ("Release", "key"), ("Doc", "key"), ("Fact", "subject"), ("Episode", "id"),
    ("Issue", "repo"), ("PR", "repo"), ("File", "repo"), ("Commit", "repo"), ("Comment", "key"), ("Comment", "target"), ("Action", "key"), ("Action", "target"),
]
FULLTEXT_INDEXES = [
    ("Issue", ("title", "body")),
    ("PR", ("title", "body")),
    ("Doc", ("text",)),
    ("Commit", ("message",)),
    ("File", ("path",)),
    ("Episode", ("question", "answer")),
    ("Comment", ("body",)),
    ("Action", ("body",)),
]


def _existing_indexes(graph: Graph) -> set[tuple[str, str, str]]:
    out = set()
    try:
        res = graph.ro_query("CALL db.indexes() YIELD label, properties, types")
        for label, props, types in res.result_set:
            for p in props:
                tlist = types.get(p, []) if isinstance(types, dict) else types
                for t in (tlist if isinstance(tlist, list) else [tlist]):
                    out.add((label, p, str(t).upper()))
    except Exception as e:
        log.debug("db.indexes failed: %s", e)
    return out


def ensure_indexes(graph: Graph) -> None:
    have = _existing_indexes(graph)
    for label, prop in RANGE_INDEXES:
        if (label, prop, "RANGE") in have:
            continue
        try:
            graph.create_node_range_index(label, prop)
        except Exception as e:
            if "already indexed" not in str(e).lower():
                log.warning("range index %s.%s: %s", label, prop, e)
    for label, props in FULLTEXT_INDEXES:
        if all((label, p, "FULLTEXT") in have for p in props):
            continue
        try:
            graph.create_node_fulltext_index(label, *props)
        except Exception as e:
            if "already indexed" not in str(e).lower():
                log.warning("fulltext index %s%s: %s", label, props, e)


# ---------------------------------------------------------------- derived edges

DERIVE_TOUCHES = """
MATCH (p:Person)-[:AUTHORED]->(c:Commit)-[t:TOUCHED]->(f:File)
WITH p, f, count(c) AS n, sum(t.additions) AS adds, sum(t.deletions) AS dels, max(c.date) AS last
MERGE (p)-[x:TOUCHES]->(f)
SET x.count = n, x.additions = adds, x.deletions = dels, x.last = last
RETURN count(x) AS edges
"""

DERIVE_REVIEWED_FOR = """
MATCH (r:Person)-[:REVIEWED]->(pr:PR)<-[:AUTHORED]-(a:Person)
WHERE r <> a AND NOT coalesce(r.is_bot, false) AND NOT coalesce(a.is_bot, false)
WITH r, a, count(DISTINCT pr) AS n
MERGE (r)-[x:REVIEWED_FOR]->(a)
SET x.count = n
RETURN count(x) AS edges
"""

DERIVE_STRONG_COLLAB = """
MATCH ()-[x:STRONG_COLLAB]->() DELETE x
WITH count(*) AS _
MATCH (a:Person)-[c:COLLABORATES]->(b:Person) WHERE c.files >= 3
MERGE (a)-[:STRONG_COLLAB]->(b)
RETURN count(*) AS edges
"""

DERIVE_CO_CHANGED = """
MATCH (c:Commit)-[:TOUCHED]->(f:File)
WITH c, collect(f) AS fs
WHERE size(fs) >= 2 AND size(fs) <= 12
UNWIND fs AS a
UNWIND fs AS b
WITH a, b WHERE id(a) < id(b)
WITH a, b, count(*) AS n
WHERE n >= 2
MERGE (a)-[x:CO_CHANGED]->(b)
SET x.count = n
RETURN count(x) AS edges
"""

DERIVE_COLLABORATES = """
MATCH (a:Person)-[:TOUCHES]->(f:File)<-[:TOUCHES]-(b:Person)
WHERE a <> b AND NOT coalesce(a.is_bot, false) AND NOT coalesce(b.is_bot, false)
WITH a, b, count(DISTINCT f) AS shared
MERGE (a)-[x:COLLABORATES]->(b) SET x.files = shared
RETURN count(x) AS edges
"""

DERIVE_RELEASED_IN = """
MATCH (pr:PR)-[:IN_REPO]->(repo:Repo)<-[:IN_REPO]-(rel:Release)
WHERE pr.merged = true AND pr.merged_at IS NOT NULL AND rel.published_at IS NOT NULL AND pr.merged_at <= rel.published_at
WITH pr, min(rel.published_at) AS first_date
MATCH (rel:Release {repo: pr.repo, published_at: first_date})
MERGE (pr)-[:RELEASED_IN]->(rel)
RETURN count(pr) AS edges
"""


RESOLVE_BY_NOREPLY = """
MATCH (e:Person) WHERE e.key STARTS WITH 'email:' AND e.key CONTAINS '@users.noreply.github.com'
WITH e, split(split(e.key, '@')[0], '+') AS parts
WITH e, toLower(CASE WHEN size(parts) = 2 THEN parts[1] ELSE replace(parts[0], 'email:', '') END) AS login
MERGE (p:Person {key: login}) ON CREATE SET p.login = login, p.url = 'https://github.com/' + login, p.resolved = true
SET p.name = coalesce(p.name, e.name)
WITH e, p WHERE e <> p
MATCH (e)-[r:AUTHORED]->(x)
MERGE (p)-[:AUTHORED]->(x)
DELETE r
WITH DISTINCT e DELETE e
RETURN count(*) AS folded
"""

RESOLVE_BY_NAME = """
MATCH (e:Person) WHERE e.resolved = false AND e.name IS NOT NULL
MATCH (p:Person) WHERE p.resolved = true AND p.name IS NOT NULL AND toLower(p.name) = toLower(e.name)
WITH e, collect(p) AS ps WHERE size(ps) = 1
WITH e, ps[0] AS p
MATCH (e)-[r:AUTHORED]->(x)
MERGE (p)-[:AUTHORED]->(x)
DELETE r
WITH DISTINCT e DELETE e
RETURN count(*) AS folded
"""


RESOLVE_BY_LOCALPART = """
MATCH (e:Person) WHERE e.resolved = false AND e.key STARTS WITH 'email:'
WITH e, toLower(split(replace(e.key, 'email:', ''), '@')[0]) AS local
MATCH (p:Person {key: local}) WHERE p.resolved = true AND p <> e
SET p.name = coalesce(p.name, e.name)
WITH e, p
MATCH (e)-[r:AUTHORED]->(x)
MERGE (p)-[:AUTHORED]->(x)
DELETE r
WITH DISTINCT e DELETE e
RETURN count(*) AS folded
"""

MARK_BOTS = """
MATCH (p:Person)
SET p.is_bot = (p.key CONTAINS '[bot]' OR coalesce(p.login,'') ENDS WITH '[bot]' OR coalesce(p.name,'') ENDS WITH '[bot]'
                OR p.key STARTS WITH 'email:noreply@' OR p.key STARTS WITH 'email:no-reply@'
                OR p.key ENDS WITH '-ai' OR p.key ENDS WITH '-bot' OR p.key ENDS WITH '-agent' OR p.key STARTS WITH 'copilot' OR p.key CONTAINS 'pull-request-reviewer'
                OR p.key IN ['dependabot', 'renovate', 'github-actions', 'claude', 'anthropic-code-agent', 'coderabbitai', 'copilot',
                             'copilot-pull-request-reviewer', 'cubic-dev-ai', 'overcut-ai', 'sourcery-ai', 'codecov', 'codecov-commenter', 'greptile-apps', 'ellipsis-dev',
                             'augmentcode', 'graphite-app', 'vercel', 'netlify', 'sonarcloud', 'gemini-code-assist', 'devin-ai-integration', 'cursor',
                             'github-advanced-security', 'railway-app', 'railway', 'github-actions', 'codecov-io', 'allcontributors', 'stale', 'mergify', 'semantic-release-bot'])
RETURN count(p)
"""


def resolve_identities(graph: Graph) -> dict[str, int]:
    out = {}
    for name, q in (("noreply", RESOLVE_BY_NOREPLY), ("name", RESOLVE_BY_NAME), ("localpart", RESOLVE_BY_LOCALPART), ("bots", MARK_BOTS)):
        try:
            res = graph.query(q, timeout=120000)
            out[name] = int(res.result_set[0][0]) if res.result_set else 0
        except Exception as e:
            log.warning("resolve %s failed: %s", name, e)
            out[name] = -1
    return out


CLEAN_BOT_DERIVED = """
MATCH (a:Person)-[x:REVIEWED_FOR|COLLABORATES|STRONG_COLLAB]-(b:Person)
WHERE coalesce(a.is_bot, false) OR coalesce(b.is_bot, false)
DELETE x RETURN count(*)
"""


def derive_edges(graph: Graph) -> dict[str, int]:
    out: dict[str, int] = {}
    try:
        graph.query(CLEAN_BOT_DERIVED, timeout=120000)
    except Exception as e:
        log.warning("clean bot derived edges failed: %s", e)
    for name, q in (("TOUCHES", DERIVE_TOUCHES), ("REVIEWED_FOR", DERIVE_REVIEWED_FOR), ("COLLABORATES", DERIVE_COLLABORATES),
                    ("STRONG_COLLAB", DERIVE_STRONG_COLLAB), ("CO_CHANGED", DERIVE_CO_CHANGED), ("RELEASED_IN", DERIVE_RELEASED_IN)):
        try:
            res = graph.query(q, timeout=120000)
            out[name] = int(res.result_set[0][0]) if res.result_set else 0
        except Exception as e:
            log.warning("derive %s failed: %s", name, e)
            out[name] = -1
    return out


# ---------------------------------------------------------------- owner facts + SUPERSEDES

TOP_OWNER_Q = """
MATCH (p:Person)-[t:TOUCHES]->(f:File)
WITH f, p, t ORDER BY t.count DESC, t.last DESC
WITH f, collect({pkey: p.key, count: t.count, last: t.last}) AS owners
RETURN f.key AS file, owners[0].pkey AS owner, owners[0].count AS cnt, owners[0].last AS last
"""

CURRENT_FACTS_Q = """
MATCH (f:File)-[:HAS_FACT]->(x:Fact {kind:'owner', current:true})
RETURN f.key AS file, x.value AS owner, id(x) AS fid
"""

WRITE_FACT_Q = """
UNWIND $rows AS r
MATCH (f:File {key: r.file}), (p:Person {key: r.owner})
CREATE (x:Fact {kind:'owner', subject: r.file, value: r.owner, score: r.cnt, last_touch: r.last,
                computed_at: $now, current: true})
CREATE (f)-[:HAS_FACT]->(x)
CREATE (x)-[:ABOUT]->(p)
WITH x, r
OPTIONAL MATCH (old:Fact) WHERE r.old_id IS NOT NULL AND id(old) = r.old_id
FOREACH (_ IN CASE WHEN old IS NULL THEN [] ELSE [1] END |
  SET old.current = false, old.superseded_at = $now
  CREATE (x)-[:SUPERSEDES]->(old))
RETURN count(x) AS created
"""


def refresh_owner_facts(graph: Graph, only_files: set[str] | None = None) -> dict[str, int]:
    """Recompute the owner Fact for every file (or only_files). Returns {created, superseded}."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tops = graph.ro_query(TOP_OWNER_Q, timeout=120000).result_set
    current = {row[0]: (row[1], row[2]) for row in graph.ro_query(CURRENT_FACTS_Q, timeout=120000).result_set}
    rows = []
    superseded = 0
    for file_key, owner, cnt, last in tops:
        if only_files is not None and file_key not in only_files:
            continue
        cur = current.get(file_key)
        if cur and cur[0] == owner:
            continue
        rows.append({"file": file_key, "owner": owner, "cnt": cnt, "last": last, "old_id": cur[1] if cur else None})
        if cur:
            superseded += 1
    created = 0
    for i in range(0, len(rows), 500):
        res = graph.query(WRITE_FACT_Q, {"rows": rows[i:i + 500], "now": now}, timeout=120000)
        created += int(res.result_set[0][0]) if res.result_set else 0
    return {"created": created, "superseded": superseded}


def counts(graph: Graph) -> dict[str, int]:
    try:
        n = graph.ro_query("MATCH (n) RETURN count(n)").result_set[0][0]
    except Exception as e:
        if "empty key" in str(e).lower():
            return {"nodes": 0, "edges": 0}
        raise
    e = graph.ro_query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
    return {"nodes": int(n), "edges": int(e)}


def set_meta(graph: Graph, org: str, extra: dict | None = None) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    c = counts(graph)
    graph.query(
        "MERGE (m:Meta {org:$org}) SET m.last_sync=$now, m.nodes=$n, m.edges=$e, m.extra=$extra",
        {"org": org, "now": now, "n": c["nodes"], "e": c["edges"], "extra": str(extra or {})},
    )


def get_meta(graph: Graph) -> dict | None:
    try:
        res = graph.ro_query("MATCH (m:Meta) RETURN m.org, m.last_sync, m.nodes, m.edges").result_set
    except Exception as e:  # a graph that does not exist yet raises "Invalid graph operation on empty key"
        if "empty key" in str(e).lower():
            return None
        raise
    if not res:
        return None
    org, last, n, e = res[0]
    return {"org": org, "lastSync": last, "nodes": int(n or 0), "edges": int(e or 0)}
