"""Store readers for sites plain HTTP can't read. Each returns
(page_url, raw_html) for a search query.

newegg: runs ufetch's newegg_ca search in its headless browser, then reads
the page copy ufetch saves (its own Newegg card parser is out of date).
Settings: AGENTTEAM_UFETCH_MCP_URL, AGENTTEAM_UFETCH_CACHE.
"""
import os
import time
import urllib.parse
from pathlib import Path

from .mcp_client import MCP

UFETCH = os.environ.get("AGENTTEAM_UFETCH_MCP_URL",
                        "http://127.0.0.1:8001/servers/universal_fetch/mcp")
CACHE = Path(os.environ.get("AGENTTEAM_UFETCH_CACHE",
                            "~/.cache/universal_fetch")).expanduser()


def _ufetch_search(site, query):
    path = CACHE / f"last_{site}.html"
    MCP(UFETCH, timeout=300).call("fetch_search", {"site": site, "query": query})
    if not path.exists():
        raise RuntimeError(f"ufetch saved no page at {path}")
    # ufetch serves repeat searches from a 15-minute cache without re-saving,
    # so accept a copy up to 15 minutes old; older means it's another query.
    if time.time() - path.stat().st_mtime > 900:
        raise RuntimeError("ufetch page copy is older than 15 minutes")
    return path.read_text(errors="replace")


def newegg(query):
    url = "https://www.newegg.ca/p/pl?" + urllib.parse.urlencode({"d": query})
    return url, _ufetch_search("newegg_ca", query)


READERS = {"newegg": newegg}


def search(name, query):
    if name not in READERS:
        raise ValueError(f"no store reader named {name}; have {sorted(READERS)}")
    return READERS[name](query)
