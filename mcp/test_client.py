"""Smoke test: launch mcp/server.py over stdio as a real MCP client would, list tools, call a few.
Run:  python mcp/test_client.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters

ROOT = Path(__file__).resolve().parent.parent


def text(result) -> str:
    items = getattr(result, "content", None) or getattr(result, "contents", None) or []
    return "".join(getattr(c, "text", "") for c in items)


async def main() -> int:
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "mcp" / "server.py")], cwd=str(ROOT))
    async with Client(params) as client:
        print("server:", client.server_info.name if client.server_info else "?", "| protocol", client.protocol_version)
        tools = await client.list_tools()
        names = [t.name for t in tools.tools]
        print(f"{len(names)} tools:", ", ".join(names))

        orgs = json.loads(text(await client.call_tool("list_orgs", {})))
        print("orgs:", [(o["org"], o["nodes"], o["edges"]) for o in orgs])
        org = orgs[0]["org"]

        r = json.loads(text(await client.call_tool("who_knows", {"org": org, "topic": "vector index", "repo": f"{org}/{org}", "limit": 3})))
        print(f"who_knows ({r['ms']} ms):", [(p["person"]["title"], p["commits_on_topic"], p["reviews_on_topic"]) for p in r["result"]["people"]])
        print("  sources:", len(r["sources"]), "| path nodes:", len(r["path"]["nodes"]))

        r = json.loads(text(await client.call_tool("reverts", {"org": org, "limit": 3})))
        print(f"reverts ({r['ms']} ms):", [(x["revert"]["title"][:50], x["revert_author"]["title"] if x["revert_author"] else None) for x in r["result"]["reverts"]])

        r = json.loads(text(await client.call_tool("run_cypher", {"org": org, "query": "MATCH (p:Person)-[:REVIEWED]->(pr:PR) WHERE NOT coalesce(p.is_bot,false) RETURN p.login, count(pr) AS n ORDER BY n DESC LIMIT 3"})))
        print("run_cypher:", r["result"])

        bad = await client.call_tool("run_cypher", {"org": org, "query": "MATCH (n) DETACH DELETE n"})
        print("write guard:", "rejected" if bad.is_error or "read-only" in text(bad) else "NOT REJECTED", "|", text(bad)[:90])

        res = await client.read_resource("brain://orgs")
        print("resource brain://orgs:", text(res)[:80], "...")
    print("MCP OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
