"""What the rule layer (policy.yaml's regexes and the safe-command lane) answers for every reference command, without
the model, so a change to the rules shows up as a diff in tests/data/rule_verdicts.json.

    python scripts/rule_snapshot.py            # compare with the saved answers; exit 1 and list what changed
    python scripts/rule_snapshot.py --update   # save the current answers (commit the file with the rule change)

Each answer is `block:<policy>` (a rule blocked it, the model is skipped), `review:<policy>` (a review-level rule
matched: the model judges, the floor is review), `allow:safe` (the safe-command lane let it through) or `model` (no
rule decided). The reference commands are the benchmark's cases, the eval sets and the lists in tests/data/.
Where the line between the rules and the model is: docs/guide/how-it-works.md, "What the rules catch".
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ["LAYA_GUARD_MOCK"] = "1"              # the rules only; mock mode never loads the model
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from laya_guard import guard  # noqa: E402

SNAPSHOT = ROOT / "tests" / "data" / "rule_verdicts.json"


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def reference_commands() -> dict[str, str]:
    """{key: command}, in a stable order."""
    refs = {f"bench:{c['id']}": c["command"] for c in _jsonl(ROOT / "bench" / "cases.jsonl")}
    for name in ("incidents", "ko_30", "shell"):
        refs.update({f"eval-{name}:{r['id']}": r["tool_call"] for r in _jsonl(ROOT / "eval" / f"testset_{name}.jsonl")})
    for name in ("never_blocked", "rewrites_blocked"):
        refs.update({f"{name}:{i}": r["command"] for i, r in enumerate(_jsonl(ROOT / "tests" / "data" / f"{name}.jsonl"))})
    return refs


def rule_answer(command: str) -> str:
    v = guard.judge(command, ask=True)
    if v.verdict_source == "regex":
        return f"{v.action}:{v.policy_id}"
    if v.verdict_source == "allowlist":
        return "allow:safe"
    if v.policy_id not in ("general", "offline"):         # a review-level rule set the floor; the model would judge
        return f"review:{v.policy_id}"
    return "model"


def current() -> dict[str, dict[str, str]]:
    return {key: {"command": cmd, "rule": rule_answer(cmd)} for key, cmd in reference_commands().items()}


def changes(saved: dict, now: dict) -> list[str]:
    out = []
    for key in sorted(saved.keys() | now.keys()):
        a, b = saved.get(key), now.get(key)
        if a == b:
            continue
        if a is None:
            out.append(f"+ {key}: {b['rule']}  {b['command']!r}")
        elif b is None:
            out.append(f"- {key}: {a['rule']}  {a['command']!r}")
        else:
            out.append(f"~ {key}: {a['rule']} -> {b['rule']}  {b['command']!r}")
    return out


def main(argv: list[str]) -> int:
    now = current()
    if "--update" in argv:
        SNAPSHOT.write_text(json.dumps(now, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"saved {len(now)} answers to {SNAPSHOT.relative_to(ROOT)}")
        return 0
    saved = json.loads(SNAPSHOT.read_text(encoding="utf-8")) if SNAPSHOT.exists() else {}
    diff = changes(saved, now)
    print("\n".join(diff) if diff else f"{len(now)} answers, unchanged")
    return 1 if diff else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
