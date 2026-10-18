// Derived relationships computed after ingestion

// --- TOUCHES: Person -> File (commit counts, last touch)
MATCH (p:Person)-[:AUTHORED]->(c:Commit)-[t:TOUCHED]->(f:File)
WITH p, f, count(c) AS n, sum(t.additions) AS adds, sum(t.deletions) AS dels, max(c.date) AS last
MERGE (p)-[x:TOUCHES]->(f)
SET x.count = n, x.additions = adds, x.deletions = dels, x.last = last
RETURN count(x) AS edges

// --- REVIEWED_FOR: reviewer -> author
MATCH (r:Person)-[:REVIEWED]->(pr:PR)<-[:AUTHORED]-(a:Person)
WHERE r <> a AND NOT coalesce(r.is_bot, false) AND NOT coalesce(a.is_bot, false)
WITH r, a, count(DISTINCT pr) AS n
MERGE (r)-[x:REVIEWED_FOR]->(a)
SET x.count = n
RETURN count(x) AS edges

// --- COLLABORATES: people who touched the same files
MATCH (a:Person)-[:TOUCHES]->(f:File)<-[:TOUCHES]-(b:Person)
WHERE a <> b AND NOT coalesce(a.is_bot, false) AND NOT coalesce(b.is_bot, false)
WITH a, b, count(DISTINCT f) AS shared
MERGE (a)-[x:COLLABORATES]->(b) SET x.files = shared
RETURN count(x) AS edges

// --- STRONG_COLLAB: pruned collaboration graph for community detection
MATCH ()-[x:STRONG_COLLAB]->() DELETE x
WITH count(*) AS _
MATCH (a:Person)-[c:COLLABORATES]->(b:Person) WHERE c.files >= 3
MERGE (a)-[:STRONG_COLLAB]->(b)
RETURN count(*) AS edges

// --- CO_CHANGED: files changed in the same commit
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

// --- RELEASED_IN: first release after a PR merged
MATCH (pr:PR)-[:IN_REPO]->(repo:Repo)<-[:IN_REPO]-(rel:Release)
WHERE pr.merged = true AND pr.merged_at IS NOT NULL AND rel.published_at IS NOT NULL AND pr.merged_at <= rel.published_at
WITH pr, min(rel.published_at) AS first_date
MATCH (rel:Release {repo: pr.repo, published_at: first_date})
MERGE (pr)-[:RELEASED_IN]->(rel)
RETURN count(pr) AS edges
