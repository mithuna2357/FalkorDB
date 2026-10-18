// Queries and algorithm calls behind each agent tool (as executed against the FalkorDB org graph)

// --- who_knows('asyncio') [1]
UNWIND $terms AS t MATCH (f:File) WHERE toLower(f.path) CONTAINS toLower(t) RETURN DISTINCT f LIMIT 300

// --- who_knows('asyncio') [2]
MATCH (p:Person)-[t:TOUCHES]->(f:File) WHERE f.key IN $files AND NOT coalesce(p.is_bot, false) WITH p, sum(t.count) AS commits, count(DISTINCT f) AS files, max(t.last) AS last OPTIONAL MATCH (p)-[:REVIEWED]->(pr:PR)-[:TOUCHED]->(f2:File) WHERE f2.key IN $files WITH p, commits, files, last, count(DISTINCT pr) AS reviews RETURN p, commits, files, last, reviews ORDER BY commits DESC, reviews DESC LIMIT 10

// --- who_knows('asyncio') [3]
CALL algo.pageRank('Person', 'REVIEWED_FOR') YIELD node, score RETURN node.key AS key, score

// --- who_knows('asyncio') [4]
UNWIND $pk AS k MATCH (p:Person {key:k})-[t:TOUCHES]->(f:File) WHERE f.key IN $files WITH p, t, f ORDER BY t.count DESC WITH p, collect([t, f])[0..4] AS tf UNWIND tf AS x RETURN p, x[0] AS t, x[1] AS f

// --- bus_factor() [1]
CALL algo.betweenness({nodeLabels: ['Person'], relationshipTypes: ['COLLABORATES'], samplingSize: 64, samplingSeed: 7}) YIELD node, score WITH node, score WHERE score > 0 RETURN node AS p, score ORDER BY score DESC LIMIT $lim

// --- bus_factor() [2]
MATCH (f:File)<-[:TOUCHES]-(p:Person) WHERE NOT coalesce(p.is_bot, false) WITH f, collect(p) AS ps WHERE size(ps) = 1 WITH ps[0] AS p, collect(f.path) AS files RETURN p, size(files) AS sole_owned_files, files[0..6] AS sample ORDER BY sole_owned_files DESC LIMIT $lim

// --- bus_factor() [3]
CALL algo.betweenness({nodeLabels: ['File'], relationshipTypes: ['CO_CHANGED'], samplingSize: 64, samplingSeed: 7}) YIELD node, score WITH node, score WHERE score > 0 RETURN node AS f, score ORDER BY score DESC LIMIT $lim

// --- bus_factor() [4]
MATCH (f:File) OPTIONAL MATCH (f)<-[:TOUCHES]-(p:Person) WHERE NOT coalesce(p.is_bot, false) WITH f, count(p) AS owners RETURN count(f) AS files, sum(CASE WHEN owners = 1 THEN 1 ELSE 0 END) AS single_owner_files

// --- clusters() [1]
CALL algo.labelPropagation({nodeLabels: ['File'], relationshipTypes: ['CO_CHANGED'], maxIterations: 10}) YIELD node, communityId WITH node, communityId WITH communityId, collect(node) AS fs WHERE size(fs) >= 3 RETURN communityId, size(fs) AS files, [f IN fs[0..8] | f.path] AS sample, [f IN fs | f.key] AS keys ORDER BY files DESC LIMIT $lim

// --- clusters() [2]
MATCH (p:Person)-[t:TOUCHES]->(f:File) WHERE f.key IN $keys AND NOT coalesce(p.is_bot, false) WITH p, sum(t.count) AS n ORDER BY n DESC LIMIT 2 RETURN p, n

// --- search('connection pool timeout') [1]
CALL db.idx.fulltext.queryNodes('PR', 'connection pool timeout') YIELD node, score RETURN node, score ORDER BY score DESC LIMIT 3

// --- search('connection pool timeout') [2]
CALL db.idx.fulltext.queryNodes('Issue', 'connection pool timeout') YIELD node, score RETURN node, score ORDER BY score DESC LIMIT 3

// --- search('connection pool timeout') [3]
CALL db.idx.fulltext.queryNodes('Issue', 'connection|pool|timeout') YIELD node, score RETURN node, score ORDER BY score DESC LIMIT 3

// --- search('connection pool timeout') [4]
CALL db.idx.fulltext.queryNodes('Comment', 'connection pool timeout') YIELD node, score RETURN node, score ORDER BY score DESC LIMIT 3

// --- search('connection pool timeout') [5]
CALL db.idx.fulltext.queryNodes('Doc', 'connection pool timeout') YIELD node, score RETURN node, score ORDER BY score DESC LIMIT 3

// --- search('connection pool timeout') [6]
CALL db.idx.fulltext.queryNodes('Commit', 'connection pool timeout') YIELD node, score RETURN node, score ORDER BY score DESC LIMIT 3

// --- node_context('FalkorDB/FalkorDB#3374') [1]
MATCH (n) WHERE (n:PR OR n:Issue) AND n.key = $k RETURN n

// --- node_context('FalkorDB/FalkorDB#3374') [2]
MATCH (n) WHERE id(n) = $id OPTIONAL MATCH (n)-[r]-(m) WHERE type(r) IN $rels WITH n, r, m ORDER BY CASE type(r) WHEN 'REVERTS' THEN 0 WHEN 'CLOSES' THEN 1 WHEN 'REVIEWED' THEN 2 WHEN 'AUTHORED' THEN 3 WHEN 'PART_OF' THEN 4 WHEN 'MENTIONS' THEN 5 ELSE 6 END RETURN n, collect([r, m])[0..60] AS around

// --- owner_history('falkordb/falkordb.py') [1]
MATCH (f:File) WHERE toLower(f.path) CONTAINS toLower($p) RETURN f ORDER BY size(f.path) LIMIT 10

// --- owner_history('falkordb/falkordb.py') [2]
MATCH (f:File)-[:HAS_FACT]->(x:Fact {kind:'owner'}) WHERE id(f) = $id OPTIONAL MATCH (x)-[:ABOUT]->(p:Person) OPTIONAL MATCH (x)-[s:SUPERSEDES]->(old:Fact) RETURN f, x, p, s, old ORDER BY x.computed_at DESC

// --- owner_history('falkordb/falkordb.py') [3]
MATCH (p:Person)-[t:TOUCHES]->(f:File) WHERE id(f) = $id RETURN p, t ORDER BY t.count DESC LIMIT 5

// --- find_path('gkorland', 'FalkorDB/FalkorDB#3374') [1]
MATCH (p:Person) WHERE p.key = $k OR toLower(coalesce(p.login,'')) = $k OR toLower(coalesce(p.name,'')) = $k RETURN p ORDER BY coalesce(p.is_bot, false) LIMIT 3

// --- find_path('gkorland', 'FalkorDB/FalkorDB#3374') [2]
MATCH (n) WHERE (n:PR OR n:Issue) AND n.key = $k RETURN n

// --- find_path('gkorland', 'FalkorDB/FalkorDB#3374') [3]
MATCH (a), (b) WHERE id(a) = $a AND id(b) = $b CALL algo.SPpaths({sourceNode: a, targetNode: b, relTypes: $rels, relDirection: 'both', maxLen: $max, pathCount: 1}) YIELD path RETURN path

// --- recall('asyncio') [1]
CALL db.idx.fulltext.queryNodes('Episode', 'asyncio') YIELD node, score RETURN node, score ORDER BY score DESC LIMIT 3

// --- reverts() [1]
MATCH (a)-[rv:REVERTS]->(b) OPTIONAL MATCH (pa:Person)-[:AUTHORED]->(a) OPTIONAL MATCH (pb:Person)-[:AUTHORED]->(b) OPTIONAL MATCH (a)-[:PART_OF]->(apr:PR) OPTIONAL MATCH (b)-[:PART_OF]->(bpr:PR) OPTIONAL MATCH (b)-[:CLOSES]->(bi:Issue) OPTIONAL MATCH (a)-[:MENTIONS]->(ai:Issue) RETURN a, b, rv, pa, pb, apr, bpr, collect(DISTINCT bi)[0..3] AS closed_by_original, collect(DISTINCT ai)[0..3] AS mentioned_by_revert ORDER BY coalesce(a.date, a.merged_at, a.created_at) DESC LIMIT $lim

// --- recent('PR', repo='FalkorDB/GraphRAG-SDK', state='MERGED') [1]
MATCH (n:PR) WHERE n.repo = $repo AND n.merged = true OPTIONAL MATCH (p:Person)-[:AUTHORED]->(n) WITH n, p RETURN n, p, coalesce(n.merged_at, n.updated_at, n.created_at) AS when_ ORDER BY when_ DESC LIMIT $lim

// --- top_reviewers() [1]
MATCH (r:Person)-[x:REVIEWED]->(pr:PR) WHERE NOT coalesce(r.is_bot, false) WITH r, count(x) AS reviews, count(DISTINCT pr.repo) AS repos OPTIONAL MATCH (r)-[f:REVIEWED_FOR]->(a:Person) WHERE NOT coalesce(a.is_bot, false) WITH r, reviews, repos, a, f ORDER BY f.count DESC RETURN r, reviews, repos, collect([a, f.count])[0..4] AS reviews_for, collect(f)[0..4] AS edges ORDER BY reviews DESC LIMIT 25

// --- top_reviewers() [2]
(cached) CALL algo.pageRank('Person', 'REVIEWED_FOR')

// --- impact_of_leaving('gkorland') [1]
MATCH (p:Person) WHERE p.key = $k OR toLower(coalesce(p.login,'')) = $k OR toLower(coalesce(p.name,'')) = $k RETURN p ORDER BY coalesce(p.is_bot, false) LIMIT 3

// --- impact_of_leaving('gkorland') [2]
MATCH (p:Person)-[t:TOUCHES]->(f:File) WHERE id(p) = $id OPTIONAL MATCH (o:Person)-[:TOUCHES]->(f) WHERE o <> p AND NOT coalesce(o.is_bot, false) WITH p, f, t, count(o) AS others WITH p, sum(CASE WHEN others = 0 THEN 1 ELSE 0 END) AS sole, count(f) AS total,      collect(CASE WHEN others = 0 THEN {dir: f.repo + ':' + coalesce(f.dir, ''), path: f.path, commits: t.count} ELSE NULL END) AS sole_files RETURN p, sole, total, [x IN sole_files WHERE x IS NOT NULL] AS sole_files

// --- impact_of_leaving('gkorland') [3]
MATCH (p:Person)-[t:TOUCHES]->(f:File) WHERE id(p) = $id WITH p, f.repo + ':' + coalesce(f.dir, '') AS dir, sum(t.count) AS mine, collect(DISTINCT f.key) AS fkeys WHERE size(fkeys) >= 3 MATCH (o:Person)-[t2:TOUCHES]->(f2:File) WHERE f2.key IN fkeys AND NOT coalesce(o.is_bot, false) WITH dir, mine, size(fkeys) AS files, sum(t2.count) AS total WITH dir, files, mine, total, toFloat(mine) / toFloat(total) AS share WHERE share >= 0.6 RETURN dir, files, mine, total, round(share * 100) AS share_pct ORDER BY share_pct DESC, files DESC LIMIT $lim

// --- impact_of_leaving('gkorland') [4]
MATCH (p:Person)-[:REVIEWED]->(pr:PR)<-[:AUTHORED]-(a:Person) WHERE id(p) = $id AND a <> p AND NOT coalesce(a.is_bot, false) WITH p, a, count(DISTINCT pr) AS n OPTIONAL MATCH (o:Person)-[:REVIEWED]->(pr2:PR)<-[:AUTHORED]-(a) WHERE o <> p AND NOT coalesce(o.is_bot, false) WITH a, n, count(DISTINCT o) AS other_reviewers WHERE other_reviewers = 0 RETURN a, n ORDER BY n DESC LIMIT $lim

// --- impact_of_leaving('gkorland') [5]
MATCH (p:Person) WHERE id(p) = $id OPTIONAL MATCH (p)-[:AUTHORED]->(pr:PR) WHERE pr.state = 'OPEN' OPTIONAL MATCH (i:Issue)-[:ASSIGNED_TO]->(p) WHERE i.state = 'OPEN' RETURN collect(DISTINCT pr)[0..6] AS open_prs, count(DISTINCT pr) AS n_prs, collect(DISTINCT i)[0..6] AS open_issues, count(DISTINCT i) AS n_issues

// --- impact_of_leaving('gkorland') [6]
MATCH (p:Person)-[c:COLLABORATES]->(o:Person) WHERE id(p) = $id RETURN o, c.files AS shared ORDER BY shared DESC LIMIT 5

// --- impact_of_leaving('gkorland') [7]
UNWIND $dirs AS d WITH d, split(d, ':')[0] AS repo, substring(d, size(split(d, ':')[0]) + 1) AS dir MATCH (o:Person)-[t:TOUCHES]->(f:File) WHERE f.repo = repo AND coalesce(f.dir, '') = dir AND id(o) <> $id AND NOT coalesce(o.is_bot, false) WITH d, o, sum(t.count) AS n ORDER BY n DESC WITH d, collect([o, n])[0..2] AS best RETURN d, best

// --- teams() [1]
MATCH (p:Person)-[:AUTHORED]->(c:Commit)-[:IN_REPO]->(r:Repo) WHERE NOT coalesce(p.is_bot, false) WITH p, r, count(c) AS n ORDER BY n DESC WITH p, collect(r.name)[0] AS main_repo WITH main_repo, collect(p) AS members WHERE size(members) >= 2 RETURN main_repo AS gid, members ORDER BY size(members) DESC LIMIT $lim

// --- teams() [2]
CALL algo.labelPropagation({nodeLabels: ['Person'], relationshipTypes: ['STRONG_COLLAB'], maxIterations: 20}) YIELD node, communityId WITH communityId, node WHERE NOT coalesce(node.is_bot, false) WITH communityId, collect(node) AS members WHERE size(members) >= 2 RETURN 'cluster-' + toString(communityId) AS gid, members ORDER BY size(members) DESC LIMIT $lim

// --- teams() [3]
MATCH (p:Person)-[t:TOUCHES]->(f:File) WHERE p.key IN $keys WITH f.repo AS repo, sum(t.count) AS n ORDER BY n DESC RETURN collect([repo, n])[0..3] AS repos

// --- teams() [4]
MATCH (p:Person)-[t:TOUCHES]->(f:File) WHERE p.key IN $keys AND coalesce(f.dir, '') <> '' WITH f.repo + ':' + f.dir AS dir, sum(t.count) AS n ORDER BY n DESC RETURN collect([dir, n])[0..5] AS dirs

// --- teams() [5]
MATCH (r:Person)-[x:REVIEWED]->(pr:PR)<-[:AUTHORED]-(a:Person) WHERE r.key IN $keys AND a.key IN $keys AND r <> a WITH r, count(x) AS n ORDER BY n DESC LIMIT 1 RETURN r, n

// --- teams() [6]
MATCH (p:Person)-[t:TOUCHES]->(f:File) WHERE p.key IN $keys WITH f.repo AS repo, sum(t.count) AS n ORDER BY n DESC RETURN collect([repo, n])[0..3] AS repos

// --- teams() [7]
MATCH (p:Person)-[t:TOUCHES]->(f:File) WHERE p.key IN $keys AND coalesce(f.dir, '') <> '' WITH f.repo + ':' + f.dir AS dir, sum(t.count) AS n ORDER BY n DESC RETURN collect([dir, n])[0..5] AS dirs

// --- teams() [8]
MATCH (r:Person)-[x:REVIEWED]->(pr:PR)<-[:AUTHORED]-(a:Person) WHERE r.key IN $keys AND a.key IN $keys AND r <> a WITH r, count(x) AS n ORDER BY n DESC LIMIT 1 RETURN r, n

// --- decision_trail('FalkorDB/GraphRAG-SDK#43') [1]
MATCH (n) WHERE (n:PR OR n:Issue) AND n.key = $k RETURN n

// --- decision_trail('FalkorDB/GraphRAG-SDK#43') [2]
MATCH (pr:PR) WHERE id(pr) = $id OPTIONAL MATCH (author:Person)-[:AUTHORED]->(pr) OPTIONAL MATCH (merger:Person)-[m:MERGED]->(pr) OPTIONAL MATCH (pr)-[:CLOSES]->(iss:Issue) OPTIONAL MATCH (ia:Person)-[:AUTHORED]->(iss) OPTIONAL MATCH (rv:Person)-[r:REVIEWED]->(pr) OPTIONAL MATCH (pr)-[:RELEASED_IN]->(rel:Release) OPTIONAL MATCH (pr)<-[:REVERTS]-(revpr:PR) OPTIONAL MATCH (rva:Person)-[:AUTHORED]->(revpr) OPTIONAL MATCH (pr)-[:REVERTS]->(orig:PR) OPTIONAL MATCH (c:Commit)-[:PART_OF]->(pr) OPTIONAL MATCH (c)<-[:REVERTS]-(rvc:Commit) OPTIONAL MATCH (rvca:Person)-[:AUTHORED]->(rvc) RETURN pr, author, merger, m, collect(DISTINCT [iss, ia]) AS issues, collect(DISTINCT [rv, r]) AS reviews, rel, collect(DISTINCT [revpr, rva]) AS reverted_by_prs, orig, collect(DISTINCT [rvc, rvca, c]) AS reverted_by_commits, count(DISTINCT c) AS commits

// --- decision_trail('FalkorDB/GraphRAG-SDK#43') [3]
MATCH (c:Comment)-[:ON]->(t) WHERE t.key IN $targets OPTIONAL MATCH (p:Person)-[:AUTHORED]->(c) WITH c, p, t WHERE NOT coalesce(p.is_bot, false) RETURN c, p, t.key AS on ORDER BY c.created_at ASC LIMIT $lim

// --- decision_trail('FalkorDB/GraphRAG-SDK#43') [4]
MATCH (c:Comment)-[:ON]->(t) WHERE t.key IN $targets MATCH (p:Person {is_bot:true})-[:AUTHORED]->(c) RETURN count(c)
