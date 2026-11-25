"""Weekly improvement loop: find the weakest agent from run logs and propose
ONE prompt change. Nothing changes until you approve it."""
import json
from collections import defaultdict
from pathlib import Path

from . import llm


def weakest(team, root="."):
    stats = defaultdict(lambda: [0, 0])  # node -> [passed checks, total]
    for p in sorted(Path(root, "runs", team).glob("*.jsonl")):
        for line in p.read_text().splitlines():
            r = json.loads(line)
            if r["kind"] == "check" and r["round"] == 0:
                a, b = map(int, r["score"].split("/"))
                stats[r["node"]][0] += a
                stats[r["node"]][1] += b
    if not stats:
        return None, {}
    rates = {n: a / b if b else 1 for n, (a, b) in stats.items()}
    return min(rates, key=rates.get), rates


def propose(team, root="."):
    name, rates = weakest(team, root)
    if not name:
        print("No runs logged yet.")
        return None
    cfg = json.loads(Path(root, "teams", f"{team}.json").read_text())
    agent = next(a for a in cfg["agents"] if a["name"] == name)
    fails = []
    for p in sorted(Path(root, "runs", team).glob("*.jsonl")):
        for line in p.read_text().splitlines():
            r = json.loads(line)
            if r["kind"] == "check" and r["node"] == name and not r["ok"]:
                fails += r.get("fixes", [])
    prop = llm.chat_json(
        "You are the Lead reviewing your team's week.",
        f"First-try pass rates: {rates}\nWeakest agent: {json.dumps(agent)}\n"
        f"Checker fixes it needed: {fails[-20:]}\n"
        "Propose ONE change to one field (job, input, output, never, or one "
        'done_well item). Return {"field":"...","new_value":...,"why":"..."}')
    prop["agent"] = name
    Path(root, f"proposal-{team}.json").write_text(json.dumps(prop, indent=2))
    print(json.dumps(prop, indent=2))
    print(f"\nSaved. Run `python -m agentteam improve --team {team} --apply`"
          " only if you approve.")
    return prop


def apply(team, root="."):
    prop = json.loads(Path(root, f"proposal-{team}.json").read_text())
    path = Path(root, "teams", f"{team}.json")
    cfg = json.loads(path.read_text())
    for a in cfg["agents"]:
        if a["name"] == prop["agent"]:
            a[prop["field"]] = prop["new_value"]
    path.write_text(json.dumps(cfg, indent=2))
    Path(root, f"proposal-{team}.json").unlink()
    print(f"Applied to {prop['agent']}.{prop['field']}")
