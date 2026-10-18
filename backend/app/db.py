"""FalkorDB connection, graph naming, and serialisation of graph values to the API contract."""
from __future__ import annotations

import re
import time
from functools import lru_cache
from typing import Any

from falkordb import FalkorDB, Graph
from falkordb.edge import Edge
from falkordb.node import Node
from falkordb.path import Path

from . import config

_ORG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$")


@lru_cache(maxsize=1)
def client() -> FalkorDB:
    kwargs: dict[str, Any] = dict(host=config.FALKORDB_HOST, port=config.FALKORDB_PORT)
    if config.FALKORDB_USERNAME:
        kwargs["username"] = config.FALKORDB_USERNAME
    if config.FALKORDB_PASSWORD:
        kwargs["password"] = config.FALKORDB_PASSWORD
    if config.FALKORDB_SSL:
        kwargs["ssl"] = True
    return FalkorDB(**kwargs)


def normalize_org(org: str) -> str:
    org = org.strip()
    if not _ORG_RE.match(org):
        raise ValueError(f"invalid GitHub org name: {org!r}")
    return org


def graph_name(org: str) -> str:
    return f"{config.GRAPH_PREFIX}{normalize_org(org).lower()}"


def graph_for(org: str) -> Graph:
    return client().select_graph(graph_name(org))


def list_org_graphs() -> list[str]:
    names = client().list_graphs()
    return [n[len(config.GRAPH_PREFIX):] for n in names if n.startswith(config.GRAPH_PREFIX)]


# ---------- serialisation to the API contract ----------

def _title_for(node: Node) -> str:
    p = node.properties
    labels = node.labels or []
    if "Person" in labels:
        name = p.get("name") or ""
        if "\\" in name or "/" in name:  # git names like DESKTOP-XYZ\user are machine names, not people
            name = ""
        return name or p.get("login") or "?"
    if "Repo" in labels:
        return p.get("full_name") or p.get("name") or "?"
    if "Issue" in labels or "PR" in labels:
        return f"#{p.get('number')} {p.get('title', '')}".strip()
    if "Commit" in labels:
        msg = (p.get("message") or "").splitlines()[0] if p.get("message") else ""
        return f"{str(p.get('sha', ''))[:7]} {msg}".strip()
    if "File" in labels:
        return p.get("path") or "?"
    if "Release" in labels:
        return p.get("tag") or p.get("name") or "?"
    if "Fact" in labels:
        return f"{p.get('kind')}: {p.get('subject')} -> {p.get('value')}"
    if "Doc" in labels:
        return f"{p.get('path')} (chunk {p.get('chunk')})"
    if "Episode" in labels:
        return (p.get("question") or "")[:80]
    if "Action" in labels:
        return f"{p.get('agent') or 'agent'} action: {p.get('kind')} on {p.get('target')}"
    if "Comment" in labels:
        body = " ".join((p.get("body") or "").split())
        return f"{p.get('kind') or 'comment'}: {body[:70]}{'…' if len(body) > 70 else ''}"
    return p.get("title") or p.get("name") or p.get("key") or str(node.id)


def node_json(node: Node) -> dict[str, Any]:
    p = node.properties
    out = {
        "id": f"n{node.id}",
        "label": (node.labels or ["Node"])[0],
        "title": _title_for(node),
    }
    if p.get("url"):
        out["url"] = p["url"]
    if p.get("key"):
        out["key"] = p["key"]
    return out


def edge_json(edge: Edge) -> dict[str, Any]:
    src = edge.src_node.id if isinstance(edge.src_node, Node) else edge.src_node
    dst = edge.dest_node.id if isinstance(edge.dest_node, Node) else edge.dest_node
    out = {"from": f"n{src}", "to": f"n{dst}", "type": edge.relation}
    if edge.properties:
        out["props"] = {k: v for k, v in edge.properties.items() if k != "embedding"}
    return out


def source_json(node: Node) -> dict[str, Any] | None:
    """A citation entry for a node, or None if the node is not a cite-able thing."""
    labels = node.labels or []
    p = node.properties
    kind = next((l for l in ("PR", "Issue", "Commit", "Release", "Doc", "Comment", "Action", "Person", "File", "Repo") if l in labels), None)
    if not kind:
        return None
    ident = p.get("number") or p.get("sha") or p.get("login") or p.get("path") or p.get("tag") or p.get("key") or node.id
    return {"type": kind, "id": str(ident), "title": _title_for(node), "url": p.get("url") or ""}


class PathCollector:
    """Accumulates nodes/edges/sources from query results, de-duplicated."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}
        self.edges: dict[tuple, dict] = {}
        self.sources: dict[str, dict] = {}

    def add(self, value: Any) -> None:
        if isinstance(value, Node):
            nj = node_json(value)
            self.nodes.setdefault(nj["id"], nj)
            sj = source_json(value)
            if sj and sj["url"]:
                self.sources.setdefault(f"{sj['type']}:{sj['id']}", sj)
        elif isinstance(value, Edge):
            ej = edge_json(value)
            self.edges.setdefault((ej["from"], ej["to"], ej["type"]), ej)
            for n in (value.src_node, value.dest_node):
                if isinstance(n, Node):
                    self.add(n)
        elif isinstance(value, Path):
            for n in value.nodes():
                self.add(n)
            for e in value.edges():
                self.add(e)
        elif isinstance(value, (list, tuple)):
            for v in value:
                self.add(v)

    def path(self) -> dict[str, list]:
        return {"nodes": list(self.nodes.values()), "edges": list(self.edges.values())}


def plain(value: Any) -> Any:
    """Convert a query cell to JSON-safe plain data (no graph objects)."""
    if isinstance(value, Node):
        props = {}
        for k, v in value.properties.items():
            if k == "embedding":
                continue
            if k in ("body", "text", "message") and isinstance(v, str):
                v = v[:1500] if "Comment" in (value.labels or []) else v[:600]
            props[k] = v
        return {**node_json(value), "props": props}
    if isinstance(value, Edge):
        return edge_json(value)
    if isinstance(value, Path):
        return {"nodes": [plain(n) for n in value.nodes()], "edges": [plain(e) for e in value.edges()]}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    return value


def run(graph: Graph, cypher: str, params: dict | None = None, read_only: bool = True, timeout_ms: int = 20000):
    """Run a query, return (rows_as_plain, collector, elapsed_ms)."""
    t0 = time.perf_counter()
    res = graph.ro_query(cypher, params or {}, timeout=timeout_ms) if read_only else graph.query(cypher, params or {}, timeout=timeout_ms)
    ms = int((time.perf_counter() - t0) * 1000)
    header = [h[1] if isinstance(h, (list, tuple)) else str(h) for h in (res.header or [])]
    col = PathCollector()
    rows = []
    for rec in res.result_set or []:
        row = {}
        for name, cell in zip(header, rec):
            col.add(cell)
            row[name] = plain(cell)
        rows.append(row)
    return rows, col, ms
