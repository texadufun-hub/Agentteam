"""Read-only tools. Nothing here sends data anywhere except a GET of URLs
you put in the task yourself."""
import html
import re
import urllib.request
from pathlib import Path

URL_RE = re.compile(r"https?://[^\s)>\]\"']+")
MAX_CHARS = 6000


def fetch_text(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(2_000_000).decode(r.headers.get_content_charset() or "utf-8", "replace")
    raw = re.sub(r"(?is)<(script|style|noscript|svg).*?</\1>", " ", raw)
    text = html.unescape(re.sub(r"(?s)<[^>]+>", " ", raw))
    return re.sub(r"\s+", " ", text).strip()[:MAX_CHARS]


def fetch_urls(task):
    """Return a SOURCES block for every URL in the task, plus local files
    given as file:/path."""
    out = []
    for i, url in enumerate(dict.fromkeys(URL_RE.findall(task)), 1):
        try:
            out.append(f"[S{i}] {url}\n{fetch_text(url)}")
        except Exception as e:
            out.append(f"[S{i}] {url}\nFETCH FAILED: {e}")
    for j, p in enumerate(re.findall(r"file:(\S+)", task), len(out) + 1):
        try:
            out.append(f"[S{j}] {p}\n{Path(p).expanduser().read_text()[:MAX_CHARS]}")
        except Exception as e:
            out.append(f"[S{j}] {p}\nREAD FAILED: {e}")
    return "SOURCES:\n" + ("\n\n".join(out) if out else "(none provided)")


def extract_files(text):
    """Find ```file:path blocks in model output."""
    return re.findall(r"```file:([^\n`]+)\n(.*?)```", text, re.S)
