"""Environment configuration. Everything comes from .env or real env vars."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# backend/.env is the single source of secrets. Never commit it.
BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


# FalkorDB
FALKORDB_HOST = os.getenv("FALKORDB_HOST", "localhost")
FALKORDB_PORT = _int("FALKORDB_PORT", 6379)
FALKORDB_USERNAME = os.getenv("FALKORDB_USERNAME") or None
FALKORDB_PASSWORD = os.getenv("FALKORDB_PASSWORD") or None
FALKORDB_SSL = os.getenv("FALKORDB_SSL", "false").lower() == "true"
GRAPH_PREFIX = os.getenv("GRAPH_PREFIX", "brain_")

# GitHub
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN") or None
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET") or None
# Repo the agent is allowed to write comments to (owner/name). Empty = never write.
ACTION_REPO = os.getenv("ACTION_REPO") or None

# LLM (OpenAI-compatible chat completions with tool calling)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or None
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL") or None
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-5-mini")
# For gpt-5 / o-series models: minimal|low|medium|high. Low keeps answers fast and cheap.
LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "low")
EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")
EMBED_DIM = _int("EMBED_DIM", 1536)

# Ingestion limits (keep the demo fast and within GitHub rate limits)
MAX_REPOS = _int("MAX_REPOS", 8)
FETCH_WORKERS = _int("FETCH_WORKERS", 4)
MAX_COMMITS_PER_REPO = _int("MAX_COMMITS_PER_REPO", 400)
MAX_PRS_PER_REPO = _int("MAX_PRS_PER_REPO", 300)
MAX_ISSUES_PER_REPO = _int("MAX_ISSUES_PER_REPO", 300)
MAX_FILES_PER_COMMIT = _int("MAX_FILES_PER_COMMIT", 40)
CLONE_DIR = Path(os.getenv("CLONE_DIR", BACKEND_DIR / ".clones"))
