"""Graph runner: Lead routing, parallel fan-out and join, a Checker quality
loop capped at 2 rounds, a human approval pause, team memory and run logs."""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import llm, tools

MAX_FIX_ROUNDS = 2


@dataclass
class Agent:
    name: str
    job: str
    input: str
    output: str
    done_well: list
    never: list
    ask_me_first: list = field(default_factory=list)
    hands_to: str = "Checker"
    tools: list = field(default_factory=list)
    model: str | None = None
    max_tokens: int | None = None
    think: bool = True

    def prompt(self):
        lines = [
            f"You are {self.name}.",
            f"JOB: {self.job}",
            f"INPUT: {self.input}",
            f"OUTPUT: {self.output}",
            "DONE WELL:\n" + "\n".join(f"- {c}" for c in self.done_well),
            "NEVER:\n" + "\n".join(f"- {c}" for c in self.never),
        ]
        if self.ask_me_first:
            lines.append(
                "ASK ME FIRST (stop and write ASK: <question> instead):\n"
                + "\n".join(f"- {c}" for c in self.ask_me_first)
            )
        return "\n".join(lines)


class RunLog:
    def __init__(self, root, team):
        self.dir = Path(root) / "runs" / team
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / time.strftime("%Y%m%d-%H%M%S.jsonl")

    def write(self, kind, **data):
        rec = {"t": time.time(), "kind": kind, **data}
        with self.path.open("a") as f:
            f.write(json.dumps(rec) + "\n")
        tag = data.get("node") or data.get("edge") or ""
        print(f"[{kind}] {tag}", data.get("summary", ""), flush=True)


class Team:
    def __init__(self, root, name, config):
        self.root = Path(root)
        self.name = name
        self.cfg = config
        self.agents = {a["name"]: Agent(**a) for a in config["agents"]}
        self.checker_model = config.get("checker_model")
        (self.root / "memory").mkdir(exist_ok=True)
        self.memory_path = self.root / "memory" / f"{name}.md"
        self._sources = None
        self._order = []

    # ---- Lead ---------------------------------------------------------
    def route(self, task):
        """Clear if/then rules: first rule whose keywords appear wins."""
        low = task.lower()
        for rule in self.cfg.get("routes", []):
            if any(k.lower() in low for k in rule["if_any"]):
                return rule["stages"], rule.get("name", "rule")
        return self.cfg["default_stages"], "default"

    def memory(self):
        return self.memory_path.read_text() if self.memory_path.exists() else ""

    def remember(self, line):
        with self.memory_path.open("a") as f:
            f.write(f"- {time.strftime('%Y-%m-%d')} {line}\n")

    # ---- Checker ------------------------------------------------------
    def check(self, agent, task, work, seen=""):
        checks = "\n".join(f"{i+1}. {c}" for i, c in enumerate(agent.done_well))
        system = (
            "You are the Checker. Score work strictly against its DONE WELL "
            "checks. Be harsh: pass a check only if the work clearly meets it."
        )
        user = (
            f"TASK:\n{task}\n\nINPUT THE AGENT WAS GIVEN (may be cut):\n{seen[-4000:]}"
            f"\n\nAGENT: {agent.name}\nDONE WELL:\n{checks}\n\n"
            f"WORK:\n{work}\n\n"
            'Return {"checks":[{"n":1,"pass":true,"why":"..."}],'
            '"fixes":["exact fix", "..."]}'
        )
        res = llm.chat_json(system, user, model=self.checker_model)
        results = res.get("checks", [])
        passed = sum(1 for c in results if c.get("pass") is True)
        total = len(agent.done_well)
        ok = passed == total and len(results) >= total
        return ok, f"{passed}/{total}", res.get("fixes", []), results

    # ---- one node + its quality loop ----------------------------------
    def run_node(self, name, task, context, log):
        agent = self.agents[name]
        extra = ""
        if "fetch_urls" in agent.tools:
            extra = f"\n\n{self._sources}"
        if "sources" in agent.tools and self._search:
            extra += f"\n\n{self._search}"
        user = (f"MEMORY:\n{self.memory()}\n\nTASK:\n{task}{extra}"
                f"\n\nCONTEXT:\n{context}")
        work = llm.chat(agent.prompt(), user, model=agent.model, max_tokens=agent.max_tokens, think=agent.think)
        log.write("node", node=name, round=0, output=work, llm=dict(llm.LAST_USAGE),
                  summary=work[:80])
        allowed = {u.lower() for u in tools.URL_RE.findall(user)}
        for rnd in range(MAX_FIX_ROUNDS + 1):
            # URL guard (code, not model): every link must come from the input.
            invented = [u for u in tools.URL_RE.findall(work) if u.lower() not in allowed]
            # Link keeper: agents listed in "must_keep_links" must carry every
            # linked offer they were given (so #1 never loses its link).
            dropped = []
            if name in self.cfg.get("must_keep_links", []) and self._linked:
                have = {u.lower() for u in tools.URL_RE.findall(work)}
                picks = list(dict.fromkeys(tools.URL_RE.findall(self._linked)))[:3]
                dropped = [u for u in picks if u.lower() not in have]
            # Order keeper: picks must follow the code-computed PRICE ORDER.
            misordered = []
            if name in self.cfg.get("must_follow_order", []) and self._order:
                pos = {u.lower(): i for i, u in enumerate(self._order)}
                picks = [u for u in dict.fromkeys(tools.URL_RE.findall(work)) if u.lower() in pos]
                for a, b in zip(picks, picks[1:]):
                    if pos[b.lower()] < pos[a.lower()]:
                        misordered.append(f"Put {b} before {a}: PRICE ORDER has it cheaper. Keep PRICE ORDER.")
                if len(picks) > 3:
                    misordered.append("List only your top 3 offers.")
            if misordered and not invented:
                ok, score, detail = False, "0/1 order", []
                fixes = misordered[:5]
                log.write("check", node=name, round=rnd, score=score, ok=False,
                          fixes=fixes, detail="order keeper", summary="order broken")
            elif dropped and not invented:
                ok, score, detail = False, "0/1 links", []
                fixes = [f"Add this link to the pick it belongs to, copied exactly: {u}" for u in dropped[:5]]
                log.write("check", node=name, round=rnd, score=score, ok=False,
                          fixes=fixes, detail="link keeper", summary="dropped links")
            elif invented:
                ok, score, detail = False, "0/1 links", []
                fixes = [f"Remove this link, it is not in your input; use only links copied from your input, or write NO LINK: {u}"
                         for u in invented[:5]]
                log.write("check", node=name, round=rnd, score=score, ok=False,
                          fixes=fixes, detail="URL guard", summary="invented links")
            else:
                ok, score, fixes, detail = self.check(agent, task, work, context)
                log.write("check", node=name, round=rnd, score=score, ok=ok,
                          fixes=fixes, detail=detail, summary=score)
            if ok:
                return {"node": name, "output": self.resolve(name, work, log),
                        "score": score, "ok": True}
            if rnd == MAX_FIX_ROUNDS:
                break
            log.write("edge", edge=f"Checker->{name}", carries="fixes",
                      fixes=fixes, summary=f"round {rnd+1}")
            fix_user = (
                f"{user}\n\nYOUR PREVIOUS WORK:\n{work}\n\n"
                "Apply exactly these fixes and return the full revised work:\n"
                + "\n".join(f"- {f}" for f in fixes)
            )
            work = llm.chat(agent.prompt(), fix_user, model=agent.model, max_tokens=agent.max_tokens, think=agent.think)
            log.write("node", node=name, round=rnd + 1, output=work,
                      summary=work[:80])
        return {"node": name, "output": self.resolve(name, work, log),
                "score": score, "ok": False, "still_wrong": fixes}

    def resolve(self, name, work, log):
        """Code step: swap category-page links for the matching product link."""
        if name not in self.cfg.get("resolve_product_links", []) or not self._search:
            return work
        new, swaps = tools.resolve_product_links(work, self._links)
        if swaps:
            log.write("tool", node="product_links", swaps=swaps,
                      summary=f"{len(swaps)} links pointed to product pages")
        return new

    # ---- code tool nodes (deterministic, no LLM) ----------------------
    def run_tool(self, name, task, context, results, log):
        if name == "@search":
            # Kept out of the shared context: only agents with the "sources"
            # tool see it, which keeps later prompts short for a local model.
            out = tools.search_sources(context, task)
            self._search = out
            self._links = dict(tools.LAST_LINKS)
            log.write("tool", node="search", output=out,
                      summary=f"{out.count('[S')} pages")
            return "" if "[S1]" in out else out
        if name == "@order":
            src = self.cfg.get("order_from", "Verifier")
            offers = next((r["output"] for r in reversed(results) if r["node"] == src), "")
            # Products the store itself lists as not shippable stay out,
            # even if the Verifier didn't copy that note.
            blocked = {u.lower() for ls in self._links.values() for label, u in ls
                       if tools.NO_SHIP.search(label)}
            # Price guard: an offer whose price isn't shown next to that
            # product in the sources is dropped, not ranked.
            labels = {}
            for ls in self._links.values():
                for label, u in ls:
                    labels.setdefault(u.lower(), []).append(label)
            dropped = []
            rows = tools.price_order(offers, blocked=blocked, evidence=self._search or "",
                                     labels_by_url=labels, dropped=dropped)
            if dropped:
                log.write("check", node="PriceGuard", round=0, score=f"{len(dropped)} dropped",
                          ok=False, fixes=dropped, summary=f"{len(dropped)} prices not in sources")
            self._order = [r[2] for r in rows]
            lines = [f"{i}. ${b:.2f} {'confirmed total' if c else 'item price, shipping unknown'} | {ln}"
                     for i, (b, c, u, ln) in enumerate(rows, 1)]
            log.write("tool", node="order", output="\n".join(lines),
                      summary=f"{len(rows)} offers ordered")
            return ("\n\n### PRICE ORDER (computed by code: cheapest first; within $5 confirmed "
                    "shipping first). Keep this order. Skip an offer only for a stated reason "
                    "(wrong variant, high risk, delivery unavailable).\n" + "\n".join(lines))
        if name == "@link":
            # Runs only after ranking, so ranking never sees affiliate status.
            ranked = next(r["output"] for r in reversed(results)
                          if r["node"] == self.cfg.get("link_from", "Ranker"))
            path = self.root / "affiliates.json"
            programs = json.loads(path.read_text()) if path.exists() else {}
            linked, missing = tools.link_offers(
                ranked, programs, self.root / "join_queue.md")
            self._linked = linked
            log.write("tool", node="link", output=linked, missing=missing,
                      summary=f"no program: {missing}" if missing else "all linked")
            return ("\n\n### LINKED OFFERS (use these links exactly)\n" + linked)
        raise ValueError(f"unknown tool node {name}")

    def safe_node(self, name, task, context, log):
        """A model error fails this node, not the whole run."""
        try:
            return self.run_node(name, task, context, log)
        except llm.LLMError as e:
            log.write("error", node=name, error=str(e), summary=str(e)[:80])
            return {"node": name, "output": f"[{name} failed: {e}]",
                    "score": "error", "ok": False, "still_wrong": [str(e)]}

    # ---- whole graph --------------------------------------------------
    def run(self, task, approve=None):
        log = RunLog(self.root, self.name)
        self._sources = None
        self._linked = None
        self._search = None
        self._links = {}
        self._order = []
        if any("fetch_urls" in a.tools for a in self.agents.values()):
            self._sources = tools.fetch_urls(task)
            log.write("tool", node="fetch_urls", output=self._sources,
                      summary=f"{len(self._sources)} chars")
        stages, rule = self.route(task)
        log.write("route", node="Lead", rule=rule, stages=stages,
                  summary=f"{rule}: {stages}")
        context, results = "", []
        for stage in stages:
            if isinstance(stage, str) and stage.startswith("@"):
                out = self.run_tool(stage, task, context, results, log)
                context += out
                if stage == "@search" and "[S1]" not in (self._search or ""):
                    # Nothing to verify: stop instead of making agents argue
                    # with the Checker over offers that don't exist.
                    log.write("route", node="Lead", rule="no-results",
                              summary="search empty, stopping early")
                    results.append({"node": "Lead", "ok": False, "score": "0/0",
                                    "output": "No offers found: search returned no results.",
                                    "still_wrong": ["search returned no results; check the search setting"]})
                    break
                continue
            # fan-out: independent agents in a stage run in parallel
            with ThreadPoolExecutor(max_workers=len(stage)) as ex:
                outs = list(ex.map(
                    lambda n: self.safe_node(n, task, context, log), stage))
            # join
            for o in outs:
                log.write("edge", edge=f"{o['node']}->join",
                          carries="checked output", summary=o["score"])
            context += "".join(
                f"\n\n### {o['node']} (score {o['score']})\n{o['output']}"
                for o in outs)
            results += outs

        flagged = [r for r in results if not r["ok"]]
        want = self.cfg.get("final_from")
        picked = [r for r in results if not want or r["node"] in want] or results
        final = "\n\n".join(r["output"] for r in picked[-len(want or [1]):])
        if self._linked is not None:
            allowed = set(tools.URL_RE.findall(self._linked))
            bad = [u for u in tools.URL_RE.findall(final) if u not in allowed]
            if bad:
                flagged.append({"node": "LinkGuard", "still_wrong":
                                [f"link not from LINKED OFFERS: {u}" for u in bad]})
                log.write("check", node="LinkGuard", round=0, score="0/1",
                          ok=False, fixes=bad, summary=f"{len(bad)} bad links")
        if self.cfg.get("disclosure") and tools.URL_RE.search(final):
            final += "\n\n" + self.cfg["disclosure"]
        log.write("final", node="Lead", flagged=[f["node"] for f in flagged],
                  summary="ready for approval")

        # human-in-the-loop pause before anything leaves
        approve = approve or _ask_human
        decision = approve(final, flagged)
        log.write("approval", node="You", approved=decision,
                  summary="approved" if decision else "held")
        if decision and self.cfg.get("write_files"):
            ws = self.root / "workspace" / log.path.stem
            for rel, body in tools.extract_files(final):
                dest = (ws / rel.strip()).resolve()
                if ws.resolve() in dest.parents:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(body)
            log.write("files", node="workspace", path=str(ws), summary=str(ws))
        self.remember(
            f"rule={rule} scores="
            + ",".join(f"{r['node']}:{r['score']}" for r in results)
            + f" approved={decision}")
        return {"final": final, "results": results, "approved": decision,
                "log": str(log.path)}


def _ask_human(final, flagged):
    print("\n========== FINAL (awaiting your approval) ==========\n")
    print(final)
    for f in flagged:
        print(f"\n!! {f['node']} still failing after {MAX_FIX_ROUNDS} rounds:")
        for s in f.get("still_wrong", []):
            print(f"   - {s}")
    try:
        return input("\nApprove and release? [y/N] ").strip().lower() == "y"
    except EOFError:
        return False


def load_team(name="deal_finder", root="."):
    root = Path(root)
    cfg = json.loads((root / "teams" / f"{name}.json").read_text())
    return Team(root, name, cfg)
