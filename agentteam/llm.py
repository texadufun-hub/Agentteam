"""Minimal OpenAI-compatible chat client (stdlib only).

Config comes from environment variables only:
  AGENTTEAM_BASE_URL  e.g. http://100.97.220.76:8094/v1
  AGENTTEAM_MODEL     e.g. qwen3.8-flash-next-q2_0
  AGENTTEAM_API_KEY   optional
"""
import json
import os
import re
import threading
import urllib.request


# Strata handles one request at a time: every model call waits its turn,
# even when graph stages run agents "in parallel". Raise with
# AGENTTEAM_CONCURRENCY once the server can take more.
_SLOTS = threading.BoundedSemaphore(int(os.environ.get("AGENTTEAM_CONCURRENCY", "1") or 1))


class LLMError(RuntimeError):
    pass


def _load_dotenv():
    """Read KEY=VALUE lines from .env in the current folder (git-ignored),
    so the key never has to be typed into a shell or chat."""
    p = os.path.join(os.getcwd(), ".env")
    if not os.path.exists(p):
        return
    for line in open(p):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


def _cfg(model=None):
    base = os.environ.get("AGENTTEAM_BASE_URL")
    mdl = model or os.environ.get("AGENTTEAM_MODEL")
    if not base or not mdl:
        raise LLMError("Set AGENTTEAM_BASE_URL and AGENTTEAM_MODEL")
    return base.rstrip("/"), mdl, os.environ.get("AGENTTEAM_API_KEY")


def chat(system, user, model=None, temperature=0.2, timeout=None, retries=1,
         max_tokens=None):
    """Retry once on a refused/reset connection, never on a timeout: a local
    server keeps generating after we give up, so a retry would pile up work."""
    timeout = timeout or int(os.environ.get("AGENTTEAM_TIMEOUT", "900"))
    for attempt in range(retries + 1):
        try:
            cap = max_tokens or int(os.environ.get("AGENTTEAM_MAX_TOKENS", "2000"))
            with _SLOTS:
                text, cut = _chat_once(system, user, model, temperature, timeout, cap)
            if cut:  # hit the cap: one retry with more room, then trim
                with _SLOTS:
                    text, cut = _chat_once(system, user, model, temperature,
                                           timeout, min(cap * 2, 8000))
            if cut:
                # Drop the half-written last line (it may hold a broken URL).
                text = text.rsplit("\n", 1)[0] + "\n[OUTPUT CUT OFF]"
            return text
        except LLMError as e:
            if attempt == retries or "timed out" in str(e).lower():
                raise


def _chat_once(system, user, model, temperature, timeout, max_tokens):
    base, mdl, key = _cfg(model)
    body = {
        "model": mdl,
        "temperature": temperature,
        # Cap output so one runaway generation can't hold the server for
        # half an hour. Per agent via "max_tokens" in its team file.
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(
        f"{base}/chat/completions", json.dumps(body).encode(), headers
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except Exception as e:  # network, HTTP, decode
        raise LLMError(f"LLM call failed: {e}") from e
    choice = data["choices"][0]
    text = choice["message"].get("content") or ""
    # Strip reasoning blocks some local models emit.
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    return text, choice.get("finish_reason") == "length"


def extract_json(text):
    """Pull the first JSON object out of a model reply."""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = m.group(1) if m else text[text.find("{"): text.rfind("}") + 1]
    try:
        return json.loads(candidate)
    except Exception as e:
        raise LLMError(f"No valid JSON in reply: {text[:300]!r}") from e


def chat_json(system, user, model=None, retries=1):
    sys2 = system + "\n\nReply with ONE JSON object only. No prose."
    last = None
    for _ in range(retries + 1):
        try:
            return extract_json(chat(sys2, user, model=model, temperature=0))
        except LLMError as e:
            last = e
    raise last
