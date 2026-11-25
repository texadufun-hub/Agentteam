"""Tiny MCP client for HTTP (streamable HTTP) servers, stdlib only.
Lets the team call the same search tools Strata's chat page uses."""
import itertools
import json
import urllib.request

_ids = itertools.count(1)


class MCP:
    def __init__(self, url, timeout=60):
        self.url, self.timeout, self.session = url, timeout, None
        self._rpc("initialize", {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "agentteam", "version": "0.1"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _send(self, msg):
        h = {"Content-Type": "application/json",
             "Accept": "application/json, text/event-stream"}
        if self.session:
            h["Mcp-Session-Id"] = self.session
        req = urllib.request.Request(self.url, json.dumps(msg).encode(), h)
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            self.session = r.headers.get("Mcp-Session-Id") or self.session
            body = r.read().decode("utf-8", "replace")
            ctype = r.headers.get("Content-Type", "")
        if not body.strip():
            return None
        if "text/event-stream" in ctype:
            msgs = [json.loads(l[5:]) for l in body.splitlines()
                    if l.startswith("data:") and l[5:].strip()]
            return next((m for m in msgs if m.get("id") == msg.get("id")),
                        msgs[-1] if msgs else None)
        return json.loads(body)

    def _rpc(self, method, params):
        res = self._send({"jsonrpc": "2.0", "id": next(_ids),
                          "method": method, "params": params})
        if res is None or "error" in res:
            raise RuntimeError(f"MCP {method} failed: {res and res.get('error')}")
        return res["result"]

    def tools(self, full=False):
        ts = self._rpc("tools/list", {})["tools"]
        return ts if full else [t["name"] for t in ts]

    def call(self, name, args):
        res = self._rpc("tools/call", {"name": name, "arguments": args})
        return "\n".join(c.get("text", "") for c in res.get("content", [])
                         if c.get("type") == "text")
