"""Tavily search enrichment for live deal sourcing demo.

Best-effort: if no key, invalid key, or any error, return None (not raise).
Disk-cached by sha256(query) to make repeated demo runs fast and stay under the
free-tier 1000 credits/month budget.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
load_dotenv()

_TAVILY_KEY = os.getenv("TAVILY_API_KEY", "")
_CACHE_DIR = Path(__file__).resolve().parent.parent / "cache" / "tavily"


def _cache_path(query: str) -> Path:
    digest = hashlib.sha256(query.encode("utf-8")).hexdigest()
    return _CACHE_DIR / f"{digest}.json"


def search_candidate(name: str, school: str = "", paper: str = "") -> dict[str, Any] | None:
    """Returns {'query': str, 'n_results': int, 'top_url': str, 'top_title': str, 'cached': bool} or None on any error."""
    if not _TAVILY_KEY or _TAVILY_KEY.startswith("TVLY-") or len(_TAVILY_KEY) < 20:
        return None

    q = " ".join(filter(None, [name, (school or "")[:60], (paper or "")[:80]]))[:200]
    if not q.strip():
        return None

    cp = _cache_path(q)
    if cp.exists():
        try:
            cached = json.loads(cp.read_text(encoding="utf-8"))
            cached["cached"] = True
            return cached
        except Exception:
            pass

    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=_TAVILY_KEY)
        r = client.search(q, max_results=3, search_depth="basic")
        results = r.get("results", []) or []
        if not results:
            out = {"query": q, "n_results": 0, "top_url": "", "top_title": ""}
        else:
            top = results[0]
            out = {
                "query": q,
                "n_results": len(results),
                "top_url": top.get("url", ""),
                "top_title": (top.get("title", "") or "")[:120],
            }
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps(out), encoding="utf-8")
        out["cached"] = False
        return out
    except Exception:
        return None
