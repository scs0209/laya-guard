"""The simulation view's pure logic, run with node against the server's own config and golden vectors.

Skipped when node isn't installed. The page and simulation.py must build the same steps for every kind of verdict,
so the vectors come from `steps_for` and the page's `stepsFor` is held to them."""
import itertools
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from laya_guard import simulation  # noqa: E402

NODE = shutil.which("node")


def vectors() -> list[dict]:
    out = []
    for via, action, policy, reason, degraded in itertools.product(
            simulation.PATHS, ("allow", "review", "block"), (None, "general", "pipe-to-shell", "package-scripts"),
            ("", "package.json script: x"), (None, "model_error")):
        out.append({"via": via, "action": action, "policy_id": policy, "reason": reason, "degraded": degraded,
                    "steps": simulation.steps_for(via, action, policy, reason, degraded)})
    return out


def ai_events() -> list[dict]:
    """The events of a real engine run (scripted agent, mock evaluator), so the page folds exactly what the server emits."""
    import threading
    from laya_guard import aisim, guard
    sys.path.insert(0, str(ROOT / "tests"))
    from test_aisim import Scripted, story
    guard._MOCK = True
    run = aisim.Run("An agent edits package.json so npm test runs a suspicious command.", Scripted(story()), agent_timeout=20)
    run.thread.start()
    run.thread.join(30)
    return run.events


@pytest.mark.skipif(NODE is None, reason="node isn't installed")
def test_page_logic_matches_the_server(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps(simulation.config()))
    (tmp_path / "vectors.json").write_text(json.dumps(vectors()))
    (tmp_path / "ai_events.json").write_text(json.dumps(ai_events()))
    env = {"PATH": str(Path(NODE).parent), "LAYA_SIM_DIR": str(ROOT / "src" / "laya_guard" / "static" / "sim"),
           "LAYA_SIM_CONFIG": str(tmp_path / "config.json"), "LAYA_SIM_VECTORS": str(tmp_path / "vectors.json"),
           "LAYA_SIM_AI_EVENTS": str(tmp_path / "ai_events.json")}
    # the file itself, not the folder: older node versions would also run every other script in it
    p = subprocess.run([NODE, "--test", str(ROOT / "tests" / "js" / "logic.test.mjs")], capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode == 0, p.stdout[-3000:] + p.stderr[-1500:]
