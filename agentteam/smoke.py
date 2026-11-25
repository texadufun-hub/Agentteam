"""Check the local model can do what the team needs before trusting it:
1) return valid JSON, 2) fail obviously bad work when acting as Checker."""
from . import llm


def run(model=None):
    ok = True
    try:
        r = llm.chat_json("You are a test.", 'Return {"answer": 4} for 2+2.',
                          model=model)
        print("JSON output:", "PASS" if r.get("answer") == 4 else f"FAIL {r}")
        ok &= r.get("answer") == 4
    except llm.LLMError as e:
        print("JSON output: FAIL", e)
        return False
    bad = "The capital of France is Berlin. 2+2=5."
    r = llm.chat_json(
        "You are a strict Checker.",
        f"Check: 'All facts are correct.' WORK: {bad}\n"
        'Return {"pass": true|false, "why": "..."}', model=model)
    caught = r.get("pass") is False
    print("Checker rejects bad work:", "PASS" if caught else f"FAIL {r}")
    ok &= caught
    print("SMOKE", "PASS" if ok else "FAIL: use a bigger checker_model")
    return ok
