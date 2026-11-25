"""Store readers for sites plain HTTP can't read. Each returns
(page_url, raw_html) for a search query.

newegg, staples, costco: run ufetch's newegg_ca / staples_ca / costco_ca search in its headless browser, then reads
the page copy ufetch saves (its own Newegg card parser is out of date).
walmart: plain HTTP search page; listings from its embedded __NEXT_DATA__ JSON.
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
    # ufetch parsed listings itself (it then saves no page copy). Use them if
    # they carry prices; otherwise a fresh saved page, if any, is better.
    fresh = path.exists() and time.time() - path.stat().st_mtime <= 900
    if listed and ("$" in listed or not fresh):
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


def costco(query):
    url = "https://www.costco.ca/s?" + urllib.parse.urlencode({"keyword": query})
    return url, _ufetch_search("costco_ca", query)


def _walk_products(node, base, out):
    """Find product-like dicts (a name, a price, a URL or id) anywhere in a
    page's embedded JSON."""
    if isinstance(node, dict):
        name = node.get("name") or node.get("title")
        url = node.get("canonicalUrl") or node.get("productUrl") or node.get("url")
        price = node.get("price") or node.get("priceInfo") or node.get("currentPrice")
        if isinstance(price, dict):
            price = (price.get("currentPrice") or price.get("linePrice") or price.get("price")
                     or price.get("priceString") or price.get("displayPrice"))
            if isinstance(price, dict):
                price = price.get("priceString") or price.get("price")
        if isinstance(name, str) and isinstance(url, str) and price not in (None, "", {}):
            p = str(price)
            seller = node.get("sellerName") or node.get("sellerDisplayName")
            note = f" sold by {seller}" if isinstance(seller, str) and seller else ""
            if "classType=VARIANT" in url:
                note += " (from price, varies by option)"
            out.append((f"{name} {p if '$' in p else '$' + p}{note}", urllib.parse.urljoin(base, url)))
        for v in node.values():
            _walk_products(v, base, out)
    elif isinstance(node, list):
        for v in node:
            _walk_products(v, base, out)


def _plain_with_json(url, product_path):
    """Plain HTTP fetch; listings come from the embedded __NEXT_DATA__ JSON
    when the HTML itself carries only a few of them."""
    import html, json, re, urllib.request
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept-Language": "en-CA,en;q=0.9"})
    import urllib.error
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read(8_000_000).decode(r.headers.get_content_charset() or "utf-8", "replace")
            break
        except urllib.error.HTTPError as e:
            # Throttled: wait as asked (capped at 60s) and try once more.
            if e.code != 429 or attempt:
                raise
            wait = e.headers.get("Retry-After", "")
            time.sleep(min(int(wait) if wait.isdigit() else 30, 60))
    items = []
    m = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', raw, re.S)
    if m:
        try:
            _walk_products(json.loads(m.group(1)), url, items)
        except ValueError:
            pass
    seen, rows = set(), []
    for t, u in items:
        if product_path in u and u not in seen:
            seen.add(u)
            rows.append(f'<a href="{html.escape(u)}">{html.escape(t)}</a> {html.escape(t)}')
    return "\n".join(rows) if rows else raw


def _cached(name, query, fetch):
    """Keep a store page for 15 minutes so repeat runs don't re-hit (and
    get throttled by) the site."""
    import hashlib
    d = Path(os.environ.get("AGENTTEAM_STORE_CACHE", "~/.cache/agentteam_stores")).expanduser()
    f = d / f"{name}-{hashlib.sha1(query.lower().encode()).hexdigest()[:12]}.html"
    if f.exists() and time.time() - f.stat().st_mtime <= 900:
        return f.read_text(errors="replace")
    raw = fetch()
    d.mkdir(parents=True, exist_ok=True)
    f.write_text(raw)
    return raw


def walmart(query):
    url = "https://www.walmart.ca/en/search?" + urllib.parse.urlencode({"q": query})
    return url, _cached("walmart", query, lambda: _plain_with_json(url, "/ip/"))


READERS = {"newegg": newegg, "staples": staples, "walmart": walmart, "costco": costco}


def search(name, query):
    if name not in READERS:
        raise ValueError(f"no store reader named {name}; have {sorted(READERS)}")
    return READERS[name](query)
