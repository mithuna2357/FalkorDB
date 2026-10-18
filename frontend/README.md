# HandsOnRepo — Frontend

Next.js + Tailwind (shadcn/ui) UI for HandsOnRepo: type a public GitHub organisation, build its
"company brain" in FalkorDB, then chat with an agent about it. Every answer shows its **sources**,
the **graph path** that produced it, and the **Cypher** it ran.

## Features

- **Org input + "Build brain"** with a progress state, then "Brain ready: N nodes, M edges".
- **Org switcher** (list from `GET /api/orgs`), with a separate chat thread per org.
- **Chat panel**: question input, markdown answer, tool calls, clickable GitHub **Sources**, collapsible **Cypher** block.
- **Path view**: `path.nodes` / `path.edges` drawn with `react-force-graph-2d`, coloured by node label; `SUPERSEDES` edges in red.
- **"Sync now"** calls `POST /api/sync` and shows added / updated / superseded counts.

## Run

```bash
npm install
npm run dev
```

With no backend configured the app runs on `fixtures/*.json` (a "Fixture mode" badge shows in the header).

## Connect the backend

Copy `.env.example` to `.env.local` and set `BACKEND_URL` to the FastAPI base URL:

```
BACKEND_URL=http://localhost:8000
```

Next.js proxies `/api/*` to it, so the backend needs no CORS setup. Set `USE_FIXTURES=true` to force fixtures.
Restart `npm run dev` after changing env vars. On Vercel, set `BACKEND_URL` in the project's environment variables.

## API contract

| Endpoint | Request | Response |
| --- | --- | --- |
| `GET /api/orgs` | — | `[{ org, nodes, edges, lastSync }]` |
| `POST /api/ask` | `{ org, question }` | `{ answer, sources[{type,id,title,url}], path{nodes[{id,label,title}], edges[{from,to,type}]}, cypher[], toolCalls[{tool,args,ms}] }` |
| `POST /api/sync` | `{ org }` | `{ added, updated, superseded }` |

"Build brain" calls `POST /api/sync` for the new org, then refreshes `GET /api/orgs`.

## Layout

```
app/                     page + layout, globals.css (theme, CommitsGrid keyframes)
components/
  hands-on-repo.tsx      app shell: header, landing, state
  org-controls.tsx       org switcher, Build brain form + progress, Sync now
  chat-panel.tsx         chat input + thread
  answer-card.tsx        answer, sources, Cypher, tool calls
  path-view.tsx          path panel + legend
  graph-canvas.tsx       react-force-graph-2d renderer
  ui/                    shadcn components + commits-grid
fixtures/                ask.json, orgs.json, sync.json
lib/                     api client, contract types, graph colours
```
