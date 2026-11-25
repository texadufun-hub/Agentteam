# Agentteam

A small team of AI agents that runs on your own local LLM through any
OpenAI-compatible endpoint. The first team is a **Deal researcher**: give it a
deal (links or local files) and it returns BUY, NEGOTIATE or WALK with the true
total cost and the top risk. A **Dev company** team is drafted for v2.

Plain Python 3.10+, standard library only. Nothing to install.

## Run in 3 steps

1. Point it at your model (key only if your server needs one):
   ```bash
   export AGENTTEAM_BASE_URL=http://100.97.220.76:8094/v1
   export AGENTTEAM_MODEL=qwen3.8-flash-next-q2_0
   # export AGENTTEAM_API_KEY=...
   ```
2. Check the model can do the job: `python -m agentteam smoke`
3. Run the team:
   ```bash
   python -m agentteam run "Is this a good deal? https://example.com/offer"
   ```

You see every agent's output and score live, then approve or hold the final
answer. Each run is logged to `runs/<team>/*.jsonl`; team memory lives in
`memory/<team>.md` and is read before every run.

## Weekly improvement

```bash
python -m agentteam improve            # Lead proposes ONE prompt change
python -m agentteam improve --apply    # only after you agree
```

See [TEAMS.md](TEAMS.md) for the agents, their prompts and the graph.
