// Owner Facts with SUPERSEDES history (keeps knowledge current)

// --- top owner per file
MATCH (p:Person)-[t:TOUCHES]->(f:File)
WITH f, p, t ORDER BY t.count DESC, t.last DESC
WITH f, collect({pkey: p.key, count: t.count, last: t.last}) AS owners
RETURN f.key AS file, owners[0].pkey AS owner, owners[0].count AS cnt, owners[0].last AS last

// --- current facts
MATCH (f:File)-[:HAS_FACT]->(x:Fact {kind:'owner', current:true})
RETURN f.key AS file, x.value AS owner, id(x) AS fid

// --- write new fact and supersede the old one
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
