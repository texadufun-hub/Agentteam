"""One command runs a team:
  python -m agentteam run "Is this a good deal? https://..."
  python -m agentteam run --team dev_company "Build a CLI that ..."
"""
import argparse
import sys

from . import graph, improve, smoke


def main():
    p = argparse.ArgumentParser(prog="agentteam")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--team", default="deal_researcher")
    r.add_argument("task")
    sub.add_parser("smoke")
    i = sub.add_parser("improve")
    i.add_argument("--team", default="deal_researcher")
    i.add_argument("--apply", action="store_true")
    a = p.parse_args()
    if a.cmd == "smoke":
        sys.exit(0 if smoke.run() else 1)
    if a.cmd == "improve":
        improve.apply(a.team) if a.apply else improve.propose(a.team)
        return
    out = graph.load_team(a.team).run(a.task)
    print("\nlog:", out["log"])


if __name__ == "__main__":
    main()
