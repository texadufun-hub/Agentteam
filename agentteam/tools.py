"""Read-only tools. Nothing here sends data anywhere except a GET of URLs
you put in the task yourself."""
import html
import os
import re
import urllib.request
from pathlib import Path

URL_RE = re.compile(r"https?://[^\s)>\]\"']+")
MAX_CHARS = 60000
PAGE_CHARS = int(os.environ.get("AGENTTEAM_PAGE_CHARS", "1500"))


def fetch_text(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        ctype = r.headers.get_content_type()
        if not (ctype.startswith("text/") or "html" in ctype or "json" in ctype):
            raise ValueError(f"not a web page ({ctype})")
        raw = r.read(2_000_000).decode(r.headers.get_content_charset() or "utf-8", "replace")
    raw = re.sub(r"(?is)<(script|style|noscript|svg).*?</\1>", " ", raw)
    text = html.unescape(re.sub(r"(?s)<[^>]+>", " ", raw))
    return re.sub(r"\s+", " ", text).strip()[:MAX_CHARS]


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
    skip = re.compile(r"(gstatic\.com|googleusercontent|\.(jpe?g|png|gif|webp|svg|pdf)(\?|$))", re.I)
    hits = [h for h in hits if not skip.search(h["url"])]
    for i, h in enumerate(hits[:max_pages], 1):
        try:
            body = price_focus(fetch_text(h["url"]))
        except Exception as e:
            body = f"FETCH FAILED: {e}"
        snip = f"Search snippet: {h['snippet']}\n" if h.get("snippet") else ""
        blocks.append(f"[S{i}] {h['title']}\n{h['url']}\n{snip}{body}")
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
