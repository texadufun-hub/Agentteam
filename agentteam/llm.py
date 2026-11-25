"""Minimal OpenAI-compatible chat client (stdlib only).

Config comes from environment variables only:
  AGENTTEAM_BASE_URL  e.g. http://100.97.220.76:8094/v1
  AGENTTEAM_MODEL     e.g. qwen3.8-flash-next-q2_0
  AGENTTEAM_API_KEY   optional
"""
import json
import os
import re
import urllib.request


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


def chat(system, user, model=None, temperature=0.2, timeout=600):
    base, mdl, key = _cfg(model)
    body = {
        "model": mdl,
        "temperature": temperature,
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
    text = data["choices"][0]["message"].get("content") or ""
    # Strip reasoning blocks some local models emit.
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


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
