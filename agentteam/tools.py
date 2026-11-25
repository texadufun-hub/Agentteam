"""Read-only tools. Nothing here sends data anywhere except a GET of URLs
you put in the task yourself."""
import html
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

URL_RE = re.compile(r"https?://[^\s)>\]\"'<]*[^\s)>\]\"'<.,;:!?]")
MAX_CHARS = 60000
PAGE_CHARS = int(os.environ.get("AGENTTEAM_PAGE_CHARS", "1500"))


def fetch_text(url, timeout=20):
    return fetch_page(url, timeout)[0]


PRODUCT_HREF = re.compile(r"/(product|products|ip|dp|p|item|itm)/", re.I)
SKIP_HREF = re.compile(r"(gift|collection|/cat/|/category/|/shop/|/browse/|/search|/p/pl\b|refurbished)", re.I)
BUTTON_TEXT = re.compile(r"^(compare|quick view|add to cart|see details|view details|shop now|learn more|\W)+$", re.I)
_STOP = {"the", "and", "for", "with", "stand", "laptop", "black", "white", "new", "only", "at"}


def _tokens(t):
    return {w for w in re.findall(r"[^\W_]{3,}", t.lower()) if w not in _STOP}


GENERIC = _STOP | {"adjustable", "folding", "foldable", "portable", "aluminum",
    "aluminium", "desk", "table", "tables", "monitor", "holder", "riser", "notebook",
    "ergonomic", "silver", "grey", "gray", "beige", "birch", "space", "universal",
    "tabletop", "computer", "tablet", "base", "height", "inch", "cad", "best", "buy",
    "ikea", "walmart", "amazon", "canada", "shipping", "unknown", "price", "total",
    "stock", "return", "days", "free", "offer", "product", "merchant", "url", "laptops"}

# Full product-link lists from the last search, keyed by source number.
LAST_LINKS = {}


def resolve_product_links(text, links_by_source):
    """For each line citing a non-product URL, swap in a product link only
    when a distinctive name (brand/model) matches exactly one product link on
    the cited page (or same site if no [S#] is cited). Returns (text, swaps)."""
    out, swaps = [], []
    for line in text.splitlines():
        for u in URL_RE.findall(line):
            if PRODUCT_HREF.search(u) and not SKIP_HREF.search(u):
                continue
            cited = [int(n) for n in re.findall(r"\[S(\d+)\]", line)]
            pool = [l for n in cited for l in links_by_source.get(n, [])] or [
                l for ls in links_by_source.values() for l in ls if _domain(l[1]) == _domain(u)]
            pool = [l for l in pool if _domain(l[1]) == _domain(u)]
            if not pool:
                continue
            def pkey(link):  # same product id = same product, even if URLs differ
                ids = re.findall(r"\d{5,}", urllib.parse.urlparse(link).path)
                return ids[-1] if ids else link.split("?")[0].lower()
            freq = {}
            for t in {(t, pkey(pu)) for label, pu in pool for t in _tokens(label) - GENERIC}:
                freq[t[0]] = freq.get(t[0], 0) + 1
            words = (_tokens(line.replace(u, "")) - GENERIC)
            distinctive = {w for w in words if len(w) >= 4 and freq.get(w) == 1}
            # Model codes (letters+digits, e.g. LS01) must not conflict.
            model = lambda ts: {t for t in ts if re.search(r"\d", t) and re.search(r"[^\W\d_]", t)}
            mine = model(words)
            matches = [(label, pu) for label, pu in pool
                       if distinctive & _tokens(label)
                       and not (mine and model(_tokens(label)) and not mine & model(_tokens(label)))]
            if matches and len({pkey(pu) for _, pu in matches}) == 1:
                best = matches[0][1]
                line = line.replace(u, best)
                swaps.append([u, best, matches[0][0]])
        out.append(line)
    return "\n".join(out), swaps


def fetch_page(url, timeout=20):
    """Return (text, product_links). Links are real hrefs from the page, so
    agents can cite product pages instead of inventing them."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        ctype = r.headers.get_content_type()
        if not (ctype.startswith("text/") or "html" in ctype or "json" in ctype):
            raise ValueError(f"not a web page ({ctype})")
        raw = r.read(8_000_000).decode(r.headers.get_content_charset() or "utf-8", "replace")
    return parse_html(raw, url)


TRACKING = re.compile(r"^(cmp|listid|trk|utm_\w+|ref|ref_|tag|gclid|fbclid|icid|intcmp|irclickid|clickid|cm_\w+|spm|scm)$", re.I)


def clean_url(u):
    """Drop tracking parameters so agents copy short, stable links."""
    parts = urllib.parse.urlsplit(u)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
         if not TRACKING.match(k)]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(q)))


def parse_html(raw, url):
    """(text, product_links) from raw HTML; url resolves relative links."""
    raw = re.sub(r"(?is)<(script|style|noscript|svg).*?</\1>", " ", raw)
    links, weak = [], set()  # weak: labels guessed from the URL slug
    for attrs, inner in re.findall(r'(?is)<a\b([^>]*)>(.*?)</a>', raw):
        # Unescape first: hrefs like ?cmp&#x3D;seo contain '#' entities.
        m = re.search(r'href=["\']([^"\']+)["\']', html.unescape(attrs))
        if not m or m.group(1).startswith("#"):
            continue
        full = clean_url(urllib.parse.urljoin(url, m.group(1).split("#")[0]))
        if not PRODUCT_HREF.search(full) or SKIP_HREF.search(full):
            continue
        label = re.sub(r"\s+", " ", html.unescape(re.sub(r"(?s)<[^>]+>", " ", inner))).strip()
        if BUTTON_TEXT.match(re.sub(r"\s+", " ", label)):
            label = ""
        if len(label) < 12:  # image-only anchors: try attributes, then the URL slug
            alt = re.search(r'(?:aria-label|title|alt)=["\']([^"\']{12,})["\']', attrs + inner)
            label = html.unescape(alt.group(1)) if alt else ""
        if len(label) < 12:
            slug = urllib.parse.urlparse(full).path.rstrip("/").split("/")
            label = max(slug, key=len).replace("-", " ") if slug else ""
            weak.add(full.split("?")[0].lower())
        key = full.split("?")[0].lower()
        if len(label) < 12:
            continue
        prev = next((n for n, (_, u) in enumerate(links) if u.split("?")[0].lower() == key), None)
        if prev is None:
            links.append((label[:90], full))
        elif key in weak and not label.islower():
            weak.discard(key)
            links[prev] = ((label + " " + " ".join(PRICE_RE.findall(links[prev][0])))[:120].strip(), links[prev][1])
        elif PRICE_RE.search(label) and not PRICE_RE.search(links[prev][0]):
            links[prev] = ((links[prev][0] + " " + label)[:120], links[prev][1])
    text = html.unescape(re.sub(r"(?s)<[^>]+>", " ", raw))
    return re.sub(r"\s+", " ", text).strip()[:MAX_CHARS], links


PRICE_RE = re.compile(r"(?:C?\$|CAD\s?)\s?\d[\d,]*(?:\.\d{2})?")


def price_focus(text, limit=None):
    """Category and product pages bury prices under menus. Keep the page
    start plus the text around each price so offers survive truncation."""
    limit = limit or PAGE_CHARS
    hits = [m.start() for m in PRICE_RE.finditer(text)]
    if not hits:
        return text[:limit]
    out, last = [text[:300]], 300
    for h in hits:
        a, b = max(h - 160, last), h + 60
        if a < b:
            out.append(text[a:b])
            last = b
        if sum(map(len, out)) > limit:
            break
    return " … ".join(out)[:limit]


def fetch_urls(task):
    """Return a SOURCES block for every URL in the task, plus local files
    given as file:/path."""
    out = []
    for i, url in enumerate(dict.fromkeys(URL_RE.findall(task)), 1):
        try:
            out.append(f"[S{i}] {url}\n{price_focus(fetch_text(url), 6000)}")
        except Exception as e:
            out.append(f"[S{i}] {url}\nFETCH FAILED: {e}")
    for j, p in enumerate(re.findall(r"file:(\S+)", task), len(out) + 1):
        try:
            out.append(f"[S{j}] {p}\n{Path(p).expanduser().read_text()[:6000]}")
        except Exception as e:
            out.append(f"[S{j}] {p}\nREAD FAILED: {e}")
    return "SOURCES:\n" + ("\n\n".join(out) if out else "(none provided)")


def extract_files(text):
    """Find ```file:path blocks in model output."""
    return re.findall(r"```file:([^\n`]+)\n(.*?)```", text, re.S)


# ---- search (SearXNG, self-hosted, no key) -------------------------------
import json
import os
import urllib.parse


def _search_mcp(query, n):
    from .mcp_client import MCP
    tool = os.environ.get("AGENTTEAM_SEARCH_TOOL", "searxng_web_search")
    text = MCP(os.environ["AGENTTEAM_SEARCH_MCP_URL"]).call(tool, {"query": query})
    hits = []
    for line in text.splitlines():
        for u in URL_RE.findall(line):
            if u not in [h["url"] for h in hits]:
                hits.append({"url": u, "title": line.replace(u, "").strip()[:120],
                             "snippet": ""})
    return hits[:n]


def _search_strata(query, n):
    """Let the server run its own web-search tools (Strata with MCP tools on
    for API calls), then keep only URLs it returned."""
    from . import llm
    text = llm.chat(
        "Use your web search tool. Do not answer from memory.",
        f"Search the web for: {query}\nList up to {n} results, one per line: "
        "URL | title | price if shown. Only URLs you actually got from search.")
    hits = []
    for line in text.splitlines():
        for u in URL_RE.findall(line):
            if u not in [h["url"] for h in hits]:
                hits.append({"url": u, "title": line.replace(u, "").strip(" |")[:120],
                             "snippet": ""})
    return hits[:n]


def search(query, n=6, timeout=20):
    """Search via an MCP search server (AGENTTEAM_SEARCH_MCP_URL, e.g. the
    searx server Strata uses) or a SearXNG instance (AGENTTEAM_SEARX_URL)."""
    if os.environ.get("AGENTTEAM_SEARCH", "").lower() == "strata":
        return _search_strata(query, n)
    if os.environ.get("AGENTTEAM_SEARCH_MCP_URL"):
        return _search_mcp(query, n)
    base = os.environ.get("AGENTTEAM_SEARX_URL")
    if not base:
        raise RuntimeError("Set AGENTTEAM_SEARCH_MCP_URL or AGENTTEAM_SEARX_URL")
    url = f"{base.rstrip('/')}/search?" + urllib.parse.urlencode(
        {"q": query, "format": "json"})
    req = urllib.request.Request(url, headers={"User-Agent": "agentteam"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    return [{"url": x["url"], "title": x.get("title", ""),
             "snippet": x.get("content", "")} for x in data.get("results", [])[:n]]


def search_sources(context, task, per_query=4, max_pages=5):
    """Run every 'QUERY: ...' line from the context, then read the top pages.
    URLs typed in the task are always included."""
    queries = re.findall(r"(?im)^\s*QUERY:\s*(.+)$", context)[:4] or [task]
    seen, hits, errors = set(), [], []
    for u in URL_RE.findall(task):
        seen.add(u)
        hits.append({"url": u, "title": "given by user", "snippet": ""})
    for q in queries:
        try:
            for h in search(q.strip(), per_query):
                if h["url"] not in seen:
                    seen.add(h["url"])
                    hits.append(h)
        except Exception as e:
            errors.append(f"search failed for {q!r}: {e}")
    blocks = []
    LAST_LINKS.clear()
    skip = re.compile(r"(gstatic\.com|googleusercontent|\.(jpe?g|png|gif|webp|svg|pdf)(\?|$))", re.I)
    hits = [h for h in hits if not skip.search(h["url"])]
    for i, h in enumerate(hits[:max_pages], 1):
        try:
            text, links = fetch_page(h["url"])
            body = price_focus(text)
            LAST_LINKS[i] = links
            # Show the model only links for products visible in the trimmed
            # text; the full list stays in code for link resolution.
            shown = [l for l in links if (_tokens(l[0]) - GENERIC) & _tokens(body)][:12]
            links = shown
            if links:
                body += "\nPRODUCT LINKS ON THIS PAGE:\n" + "\n".join(
                    f"- {label} -> {u}" for label, u in links)
        except Exception as e:
            body = f"FETCH FAILED: {e}"
        snip = f"Search snippet: {h['snippet']}\n" if h.get("snippet") else ""
        blocks.append(f"[S{i}] {h['title']}\n{h['url']}\n{snip}{body}")
    from . import stores
    for name in [x.strip() for x in os.environ.get("AGENTTEAM_STORES", "").split(",") if x.strip()]:
        i = len(blocks) + 1
        try:
            product = re.search(r"(?im)^\W*PRODUCT:\s*(.+)$", context)
            q = product.group(1).strip() if product else queries[0].strip()
            page_url, raw = stores.search(name, q[:60])
            text, links = parse_html(raw, page_url)
            body = price_focus(text)
            LAST_LINKS[i] = links
            shown = [l for l in links if PRICE_RE.search(l[0]) or (_tokens(l[0]) - GENERIC) & _tokens(body)][:12]
            if shown:
                body += "\nPRODUCT LINKS ON THIS PAGE:\n" + "\n".join(
                    f"- {label} -> {u}" for label, u in shown)
        except Exception as e:
            page_url, body = f"store:{name}", f"FETCH FAILED: {e}"
        blocks.append(f"[S{i}] {name} search\n{page_url}\n{body}")
    head = "SOURCES (from search):\n" + ("\n".join(errors) + "\n" if errors else "")
    return head + ("\n\n".join(blocks) if blocks else "(no results)")


# ---- affiliate linker (code, runs AFTER ranking) -------------------------
def _domain(url):
    host = urllib.parse.urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def affiliate_link(url, programs):
    """programs: {"amazon.ca": {"param": "tag", "value": "yourtag-20"}} or
    {"example.com": {"template": "https://track.net/?u={url_enc}"}}."""
    d = _domain(url)
    prog = next((p for dom, p in programs.items()
                 if d == dom or d.endswith("." + dom)), None)
    if not prog:
        return url, None
    if "template" in prog:
        return prog["template"].format(url=url, url_enc=urllib.parse.quote(url, safe="")), prog
    parts = urllib.parse.urlparse(url)
    qs = dict(urllib.parse.parse_qsl(parts.query))
    qs[prog["param"]] = prog["value"]
    return parts._replace(query=urllib.parse.urlencode(qs)).geturl(), prog


def link_offers(ranked_text, programs, join_queue_path):
    """Rewrite merchant URLs in the Ranker's output to affiliate links where
    you have a program; list the rest for you to consider joining."""
    linked, missing = ranked_text, []
    for u in dict.fromkeys(URL_RE.findall(ranked_text)):
        new, prog = affiliate_link(u, programs)
        if prog:
            linked = linked.replace(u, new)
        else:
            missing.append(_domain(u))
    if missing:
        from pathlib import Path
        p = Path(join_queue_path)
        have = p.read_text() if p.exists() else ""
        with p.open("a") as f:
            for d in dict.fromkeys(missing):
                if f"- {d}\n" not in have:
                    f.write(f"- {d}\n")
    return linked, list(dict.fromkeys(missing))
