# Why the graph is load-bearing

Rule 3 of Graph Hacks: if the product still works once FalkorDB is taken out, the graph is decoration. This page shows, per
question the agent answers, the traversal or algorithm it depends on and why a relational table or a vector store cannot
produce the same answer without rebuilding a graph first.

Every query below is executed live against `brain_<org>` and returned to the user as the "Cypher that ran" panel.
The full text of each statement is in the `.cypher` files next to this document.

| Question | Graph work | Why not SQL or vectors |
|---|---|---|
| Who should review a change to X? | `Person -TOUCHES-> File` filtered by path, joined with `Person -REVIEWED-> PR -TOUCHED-> File`, then `algo.pageRank('Person','REVIEWED_FOR')` to break ties | Needs a 3-hop join across three entity types plus a global centrality score. SQL can express the joins but PageRank over the reviewer graph is iterative and global; a vector store has no notion of who touched what. |
| Why was this reverted? Who decided? | `decision_trail`: `Issue <-CLOSES- PR <-MERGED- Person`, `PR <-ON- Comment <-AUTHORED- Person`, `PR <-REVERTS- PR`, `Commit -PART_OF-> PR`, `PR -RELEASED_IN-> Release`, ordered by time | This is a path through seven node types. The "reason" lives on a Comment two hops away from the thing the user asked about. Vector search finds similar text, not the comment that belongs to *this* PR's revert. |
| What happens if this person leaves? | Files with exactly one human `TOUCHES` edge, directory share of commits, authors with no other reviewer (`REVIEWED` anti-join), `COLLABORATES` neighbours as successors | "Only person who ever touched" is a negative existence check over a bipartite graph; "who could cover" is the nearest neighbour in a projection that only exists because the graph derived it. |
| Bus factor across the org | `algo.betweenness` over `Person -COLLABORATES-> Person` and over `File -CO_CHANGED-> File` | Betweenness counts shortest paths between all pairs. There is no SQL equivalent short of implementing BFS in application code. |
| What teams exist? | `algo.labelPropagation` over `STRONG_COLLAB` plus grouping by each person's dominant repo | Community detection is a graph algorithm by definition. |
| Who are the trusted reviewers? | `algo.pageRank` over `REVIEWED_FOR` (reviewer -> author), humans only | A count of reviews is a GROUP BY. *Who reviews the people who review everyone else* is PageRank. |
| How are two things connected? | `algo.SPpaths` with `relDirection: 'both'` over 15 relationship types | Shortest path across heterogeneous edges is the canonical graph query. |
| Is this still true? Who owned it before? | `File -HAS_FACT-> Fact -SUPERSEDES-> Fact`, with `current` flags written by the sync | Temporal supersession as explicit edges, so the agent never returns a stale owner. A row update would lose the history; a vector store has no time. |
| What did people say about X? | `db.idx.fulltext.queryNodes('Comment', ...)` then `Comment -ON-> PR|Issue` to attach each hit to its ticket and author | Full-text alone returns text. The graph says *which* PR the comment is on and *who* said it, in one hop. |

## What breaks without FalkorDB

- `who_knows`, `top_reviewers`, `bus_factor`, `teams`, `clusters` call FalkorDB procedures (`algo.pageRank`, `algo.betweenness`,
  `algo.labelPropagation`, `algo.SPpaths`). They have no fallback path; the tools raise and the agent reports that the graph is unavailable.
- Derived relationships (`TOUCHES`, `REVIEWED_FOR`, `COLLABORATES`, `CO_CHANGED`, `RELEASED_IN`) are computed *inside* FalkorDB
  with Cypher after ingestion (`01_schema_derived.cypher`). Nothing in the application re-computes them.
- Owner Facts and their `SUPERSEDES` chain are written by Cypher (`03_owner_facts_supersedes.cypher`); the webhook sync depends on them.
- Multi-tenancy is FalkorDB multigraph: one graph per organisation (`brain_<org>`) in a single instance. Switching org is `select_graph`.

## Latency

Every graph tool runs in 2 to 85 ms on a 21k-node, 74k-edge org graph (measured, shown in the UI per tool call). The
agent's wall time is the language model, not the database, which is what makes it viable to call the graph on every step of the loop.
