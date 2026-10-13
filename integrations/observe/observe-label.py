#!/usr/bin/env python3
"""Attach human ground-truth labels to the verdicts in observe.log -> labels.jsonl.

  observe-label.py                  walk through flagged (review/block/error) unlabeled calls
  observe-label.py --all            include allow too (to hunt for missed attacks; can be long)
  observe-label.py --last N         only the N most recent calls
  observe-label.py add "<command>" allow|review|block   add a case that is not in the log
  observe-label.py stats            confusion matrix, overblock and missed-attack counts

At each prompt: a / r / b = the correct verdict (allow / review / block), Enter = the model was
right, s = skip, q = quit (Ctrl-C also saves what you have done so far). Labels typed by a human
are the only ground truth: "it ran after I approved it" is shown as a hint but never becomes a
label by itself, so a command you were tricked into approving can't be learned as safe.
Already-labeled calls are skipped; to change a label, delete its line in labels.jsonl.
"""
import hashlib
import sys
import time

from observe_common import LABELS, OBSERVE_LOG, OUTCOME_LOG, append_jsonl, mask, read_jsonl, server_verdict

ACTIONS = {"a": "allow", "r": "review", "b": "block"}
FLAGGED = ("review", "block", "error")


def key_of(entry: dict) -> str:
    return entry.get("id") or hashlib.sha1((entry.get("ts", "") + entry.get("command", "")).encode()).hexdigest()[:12]


def load_labels() -> dict:
    return {row["key"]: row for row in read_jsonl(LABELS) if row.get("key")}


def executed_ids() -> set:
    return {r["id"] for r in read_jsonl(OUTCOME_LOG) if r.get("id")}


def hint(entry: dict, executed: set) -> str:
    if not entry.get("id"):
        return "no id (old log line)"
    if entry["id"] in executed:
        return "ran"
    return "blocked" if entry.get("verdict") == "block" else "did not run (denied/interrupted)"


def save(entry: dict, label: str, executed: set) -> None:
    append_jsonl(LABELS, {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "key": key_of(entry), "origin": "observe",
        "label": label, "model": entry.get("verdict"), "agrees": label == entry.get("verdict"),
        "risk": entry.get("risk"), "policy": entry.get("policy"), "source": entry.get("source"),
        "executed": (entry["id"] in executed) if entry.get("id") else None,
        "session": entry.get("session"), "command": entry.get("command_full") or entry.get("command") or "",
    })


def interactive(include_allow: bool, last) -> int:
    entries = read_jsonl(OBSERVE_LOG)
    if last:
        entries = entries[-last:]
    labels, executed = load_labels(), executed_ids()
    todo = [e for e in entries if key_of(e) not in labels and (include_allow or e.get("verdict") in FLAGGED)]
    if not todo:
        print("Nothing to label.")
        return 0
    print(f"{len(todo)} unlabeled. a=allow (false positive)  r=review  b=block | Enter=model was right | s=skip | q=quit\n")
    done = 0
    for i, e in enumerate(todo, 1):
        cmd = e.get("command_full") or e.get("command") or ""
        print(f"[{i}/{len(todo)}] {e.get('ts', '')[11:19]}  model: {e.get('verdict')}  policy: {e.get('policy')}  "
              f"risk {e.get('risk')}  source {e.get('source')}  | {hint(e, executed)}")
        print("  " + cmd[:700].replace("\n", "\n  ") + (" ..." if len(cmd) > 700 else ""))
        try:
            ans = input("correct verdict? > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if ans == "q":
            break
        if ans == "s":
            continue
        if ans == "":
            if e.get("verdict") not in ACTIONS.values():
                print("  (no verdict on this call, type a / r / b)")
                continue
            label = e["verdict"]
        elif ans in ACTIONS:
            label = ACTIONS[ans]
        else:
            print("  (one of a / r / b / Enter / s / q) skipped")
            continue
        save(e, label, executed)
        done += 1
        print(f"  -> {label}\n")
    print(f"{done} saved to {LABELS}")
    return 0


def add(command: str, label: str) -> int:
    if label not in ACTIONS.values():
        print("label must be allow, review or block")
        return 2
    masked = mask(command)
    verdict = server_verdict(command)
    model = verdict.get("action") if verdict else None
    append_jsonl(LABELS, {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "key": "m-" + hashlib.sha1(masked.encode()).hexdigest()[:12], "origin": "manual",
        "label": label, "model": model, "agrees": (label == model) if model else None,
        "executed": None, "command": masked[:4000],
    })
    print(f"added: label {label}, model {model or '(server unreachable)'}")
    return 0


def stats() -> int:
    rows = list(load_labels().values())
    if not rows:
        print("No labels yet. Run observe-label.py first.")
        return 0
    cols = ("allow", "review", "block")
    matrix = {m: {c: 0 for c in cols} for m in cols + ("none",)}
    for r in rows:
        matrix[r["model"] if r.get("model") in cols else "none"][r["label"]] += 1
    manual = sum(1 for r in rows if r.get("origin") == "manual")
    print(f"{len(rows)} labels ({manual} added manually)")
    print(f"{'model/label':<12}" + "".join(f"{c:>8}" for c in cols))
    for m in cols + ("none",):
        if any(matrix[m].values()):
            print(f"{m:<12}" + "".join(f"{matrix[m][c]:>8}" for c in cols))
    flagged = sum(matrix[m][c] for m in ("review", "block") for c in cols)
    false_pos = matrix["review"]["allow"] + matrix["block"]["allow"]
    allowed = sum(matrix["allow"].values())
    missed = matrix["allow"]["review"] + matrix["allow"]["block"]
    if flagged:
        print(f"\noverblock (flagged, correct verdict allow): {false_pos}/{flagged} = {false_pos / flagged:.0%}"
              f" | block that should be review: {matrix['block']['review']}")
    if allowed:
        print(f"missed (allowed, correct verdict review/block): {missed}/{allowed}")
    else:
        print("\nmissed attacks: label some allow calls with --all to measure this")
    return 0


def main(argv: list) -> int:
    if argv and argv[0] == "stats":
        return stats()
    if argv and argv[0] == "add":
        if len(argv) != 3:
            print('usage: observe-label.py add "<command>" allow|review|block')
            return 2
        return add(argv[1], argv[2])
    last = None
    if "--last" in argv:
        try:
            last = int(argv[argv.index("--last") + 1])
        except (IndexError, ValueError):
            print("--last needs a number")
            return 2
    return interactive("--all" in argv, last)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
