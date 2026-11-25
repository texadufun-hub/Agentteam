# Agentteam

A team of AI agents that runs on your own local LLM through any
OpenAI-compatible endpoint (Strata, llama.cpp, vLLM, LM Studio).

**First team: Deal finder.** A consumer asks for a product. The team searches,
verifies real offers and total cost, checks seller risk, ranks on value only,
and **then** attaches your affiliate link where you have one. Merchants with
no program are shown anyway with a plain link and added to `join_queue.md` so
you can decide whether to join. A disclosure line is always appended by code.

Plain Python 3.10+, standard library only. Nothing to install.

## Run in 3 steps

1. Point it at your model and a search source:
   ```bash
   export AGENTTEAM_BASE_URL=http://100.97.220.76:8094/v1
   export AGENTTEAM_MODEL=qwen3.8-flash-next-q2_0
   # export AGENTTEAM_API_KEY=...            # only if your server needs one
   # Search, pick one:
   export AGENTTEAM_SEARCH=strata          # Strata runs its own search tools
   # ... or call an MCP search server directly:
   # export AGENTTEAM_SEARCH_MCP_URL=http://HOST:PORT/mcp
   # ... or a SearXNG instance with JSON output enabled:
   # export AGENTTEAM_SEARX_URL=http://HOST:PORT
   ```
   `python -m agentteam mcp-tools $AGENTTEAM_SEARCH_MCP_URL` lists its tools.

   Extra stores read through ufetch's headless browser:
   `export AGENTTEAM_STORES=newegg,staples` (staples needs the staples_ca ufetch profile) (see `agentteam/stores.py`).
2. Check the model: `python -m agentteam smoke`
3. Run: `python -m agentteam run "best laptop stand under $60"`

Affiliate programs go in `affiliates.json` (copy `affiliates.example.json`).
It is git-ignored.

You see every agent's output and Checker score live, then approve or hold the
reply. Runs are logged to `runs/<team>/`, memory to `memory/<team>.md`.

## Integrity rules enforced in code

- The Ranker never sees affiliate data; links are added after ranking.
- A link guard flags any link in the reply that the linker did not produce.
- The disclosure is appended by code, not left to the model.

## Weekly improvement

```bash
python -m agentteam improve            # Lead proposes ONE prompt change
python -m agentteam improve --apply    # only after you agree
```

Other teams: `--team deal_researcher` (vet one deal from links),
`--team dev_company` (draft). See [TEAMS.md](TEAMS.md).
