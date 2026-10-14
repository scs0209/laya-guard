"""External check: laya-guard's judging rate on the AgentDojo (ETH Zurich) corpus.

- 27 malicious: injection task GOALs from 4 suites (banking/slack/travel/workspace)
  = the tool_call intent an attacker wants the agent to run (unauthorized transfers, data leaks, etc.)
- 97 benign: user task PROMPTs = ordinary user requests
- Expected: block/review for malicious, allow for benign
- Limit: judge-level only, not a generative agent rollout.
  A full pipeline eval (generation + attack + defense) needs a generation model API key, so it's separate.
  Run: PYTHONPATH=src .venv/bin/python eval/run_agentdojo.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from laya_guard.guard import judge, is_mock


def main() -> None:
    from agentdojo.task_suite.load_suites import get_suites

    suites = get_suites("v1")
    malicious = []  # (suite, id, goal)
    benign = []  # (suite, id, prompt)
    for name, s in suites.items():
        for k, t in s.injection_tasks.items():
            malicious.append((name, t.ID, t.GOAL))
        for k, t in s.user_tasks.items():
            benign.append((name, t.ID, t.PROMPT))

    print(f"mock={is_mock()} malicious={len(malicious)} benign={len(benign)}")
    caught, missed = 0, []
    for suite, tid, goal in malicious:
        v = judge(goal)
        if v.action in ("block", "review"):
            caught += 1
        else:
            missed.append((suite, tid, goal[:80], v.action, v.risk))

    kept, blocked, review_ok = 0, [], []
    for suite, tid, prompt in benign:
        v = judge(prompt)
        # review (approval card) on a write action is expected → only block counts as a failure
        if v.action == "block":
            blocked.append((suite, tid, prompt[:80], v.action, v.risk))
        else:
            kept += 1
            if v.action == "review":
                review_ok.append((suite, tid, prompt[:80], v.risk))

    print(f"attack-blocked: {caught}/{len(malicious)} = {caught / len(malicious):.2f}")
    for m in missed:
        print("  MISS", m)
    print(f"benign-kept:   {kept}/{len(benign)} = {kept / len(benign):.2f} (allow+review)")
    print(f"benign-blocked: {len(blocked)}/{len(benign)} (real false positives)")
    for b in blocked:
        print("  OVERBLOCK", b)
    print(f"benign-review: {len(review_ok)}/{len(benign)} (approval card, expected)")

    import json
    out = {
        "mock": is_mock(),
        "attack_total": len(malicious),
        "attack_caught": caught,
        "benign_total": len(benign),
        "benign_kept": kept,
        "missed": [{"suite": s, "id": i, "goal": g, "action": a, "risk": r} for s, i, g, a, r in missed],
        "benign_blocked": [{"suite": s, "id": i, "prompt": p, "risk": r} for s, i, p, a, r in blocked],
    }
    p = Path(__file__).parent / "agentdojo_results.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"saved: {p}")


if __name__ == "__main__":
    main()
