// Folding git author emails into GitHub logins

// --- noreply emails -> login
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

// --- same display name (unique)
MATCH (e:Person) WHERE e.resolved = false AND e.name IS NOT NULL
MATCH (p:Person) WHERE p.resolved = true AND p.name IS NOT NULL AND toLower(p.name) = toLower(e.name)
WITH e, collect(p) AS ps WHERE size(ps) = 1
WITH e, ps[0] AS p
MATCH (e)-[r:AUTHORED]->(x)
MERGE (p)-[:AUTHORED]->(x)
DELETE r
WITH DISTINCT e DELETE e
RETURN count(*) AS folded

// --- email local part == login
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

// --- mark bots
MATCH (p:Person)
SET p.is_bot = (p.key CONTAINS '[bot]' OR coalesce(p.login,'') ENDS WITH '[bot]' OR coalesce(p.name,'') ENDS WITH '[bot]'
                OR p.key ENDS WITH '-ai' OR p.key ENDS WITH '-bot' OR p.key ENDS WITH '-agent' OR p.key STARTS WITH 'copilot' OR p.key CONTAINS 'pull-request-reviewer'
                OR p.key IN ['dependabot', 'renovate', 'github-actions', 'claude', 'anthropic-code-agent', 'coderabbitai', 'copilot',
                             'copilot-pull-request-reviewer', 'cubic-dev-ai', 'overcut-ai', 'sourcery-ai', 'codecov', 'codecov-commenter', 'greptile-apps', 'ellipsis-dev',
                             'augmentcode', 'graphite-app', 'vercel', 'netlify', 'sonarcloud', 'gemini-code-assist', 'devin-ai-integration', 'cursor'])
RETURN count(p)
