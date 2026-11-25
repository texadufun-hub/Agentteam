# Teams

Agent definitions live in `teams/*.json`. Each agent has JOB, INPUT, OUTPUT,
DONE WELL (3 to 5 checks), NEVER and ASK ME FIRST, which the runner turns into
its prompt. You talk only to the Lead (the runner's routing rules plus the
final approval step).

## Deal researcher (v1, first job)

Why first: it's read-only, the output is short and checkable, and it suits a
small local model better than writing code.

| Agent | Job | Hands to |
|---|---|---|
| Scout | Extract exact terms and true total cost from the sources | Market, Risk |
| Market | Compare the price against alternatives in the sources | Advisor |
| Risk | Find hidden fees, lock-in, refund limits, seller issues | Advisor |
| Advisor | First line BUY / NEGOTIATE / WALK, 3 reasons, total cost, top risk | You |
| Checker | Scores every output against that agent's DONE WELL | back to agent, max 2 rounds |

Lead routing rules (first match wins):
- task contains "quick" or "fast": Scout, then Advisor
- task contains "scam", "legit" or "red flag": Scout, then Risk, then Advisor
- otherwise: Scout, then Market and Risk in parallel, then Advisor

```mermaid
flowchart LR
  You -->|task and links| Lead
  Lead -->|urls| Fetch
  Fetch -->|sources| Scout
  Scout -->|terms sheet| Checker
  Checker -->|fixes max 2| Scout
  Checker -->|passed terms| Market
  Checker -->|passed terms| Risk
  Market -->|price verdict| Join
  Risk -->|ranked risks| Join
  Join -->|all findings| Advisor
  Advisor -->|verdict| Approve
  Approve -->|yes| Memory
  Approve -->|final answer| You
```

Paste this code at mermaid.live to see the picture.

Plain version: You -> Lead -> Fetch -> Scout -> Checker -> (Market || Risk) -> Join -> Advisor -> Checker -> your approval -> answer + memory.
(Market, Risk and Advisor each also go through the Checker; failures go back with exact fixes, at most 2 rounds, then reach you flagged.)

## Dev company (v2, drafted)

Product (spec) -> Architect (design) -> Engineer (code) || QA (tests from the
spec, not the code) -> approval -> files written to `workspace/<run>/`.
Run with `--team dev_company`. Not yet tested on the local model.

## Loops

- **Quality:** the Checker returns pass/fail per check and exact fixes; the
  agent revises; at most 2 rounds, then the work reaches you with what is
  still wrong.
- **Improvement:** weekly, `improve` finds the agent with the lowest
  first-try pass rate and proposes ONE change. Nothing changes until you run
  `--apply`.
