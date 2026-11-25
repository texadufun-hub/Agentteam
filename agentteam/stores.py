"""Store readers for sites plain HTTP can't read. Each returns
(page_url, raw_html) for a search query.

newegg, staples: run ufetch's newegg_ca / staples_ca search in its headless browser, then reads
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
    out = MCP(UFETCH, timeout=300).call("fetch_search", {"site": site, "query": query})
    listed = _listings_as_html(out)
    if listed:  # ufetch parsed listings itself (it then saves no page copy)
        return listed
    if not path.exists():
        raise RuntimeError(f"ufetch saved no page at {path}")
    # ufetch serves repeat searches from a 15-minute cache without re-saving,
    # so accept a copy up to 15 minutes old; older means it's another query.
    if time.time() - path.stat().st_mtime > 900:
        raise RuntimeError("ufetch page copy is older than 15 minutes")
    return path.read_text(errors="replace")


def _listings_as_html(out):
    """Turn ufetch's parsed listings (JSON or text with title/price/url)
    into minimal HTML anchors our extractor already understands."""
    import html, json, re
    items = []
    try:
        data = json.loads(out)
        rows = data if isinstance(data, list) else next(
            (v for v in data.values() if isinstance(v, list)), [])
        for r in rows:
            if isinstance(r, dict) and r.get("url"):
                items.append((f"{r.get('title', '')} {r.get('price', '')}".strip(), r["url"]))
    except (ValueError, AttributeError, StopIteration):
        for line in out.splitlines():
            m = re.search(r"https?://\S+", line)
            if m:
                items.append((line.replace(m.group(0), "").strip(" |-:"), m.group(0)))
    return "".join(f'<a href="{html.escape(u)}">{html.escape(t)}</a> {html.escape(t)}\n'
                   for t, u in items if t)


def newegg(query):
    url = "https://www.newegg.ca/p/pl?" + urllib.parse.urlencode({"d": query})
    return url, _ufetch_search("newegg_ca", query)


def staples(query):
    url = "https://www.staples.ca/search?" + urllib.parse.urlencode({"query": query})
    return url, _ufetch_search("staples_ca", query)


READERS = {"newegg": newegg, "staples": staples}


def search(name, query):
    if name not in READERS:
        raise ValueError(f"no store reader named {name}; have {sorted(READERS)}")
    return READERS[name](query)
