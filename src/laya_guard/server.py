"""laya-guard HTTP server. POST /guard + Jev-compatible POST /v1/systemone."""
from __future__ import annotations
import os
import time
from fastapi import FastAPI
from pydantic import BaseModel
from .guard import judge, is_mock
from .log import log_verdict

app = FastAPI(title="laya-guard")


def _judge_and_log(text: str, source: str, cwd: str | None = None):
    t0 = time.perf_counter()
    v = judge(text, source=source, cwd=cwd)
    log_verdict(
        action=v.action, risk=v.risk, policy_id=v.policy_id, reason=v.reason,
        mock=v.mock, source=source,
        latency_ms=(time.perf_counter() - t0) * 1000.0, tool_call=text, via=v.verdict_source,
    )
    return v

class GuardReq(BaseModel):
    tool_call: str = ""
    state: str = ""
    source: str = "user"  # user | tool | email | web
    cwd: str | None = None  # absolute directory the command runs in (enables package script checks)

class GuardRes(BaseModel):
    action: str
    risk: float
    policy_id: str
    reason: str
    mock: bool
    verdict_source: str  # regex | allowlist | model | heuristic | limit

@app.get("/health")
def health():
    return {"ok": True, "mock": is_mock()}

@app.post("/guard", response_model=GuardRes)
def guard(req: GuardReq):
    text = req.tool_call or req.state
    cwd = req.cwd if req.cwd and os.path.isabs(req.cwd) else None
    v = _judge_and_log(text, source=req.source, cwd=cwd)
    return GuardRes(action=v.action, risk=v.risk, policy_id=v.policy_id, reason=v.reason,
                    mock=v.mock, verdict_source=v.verdict_source)

@app.post("/v1/systemone")
def systemone(body: dict):
    # Jev drop-in: {state, questions} -> laya-guard verdict mapped back
    state = body.get("state", "") if isinstance(body, dict) else ""
    if isinstance(state, dict):
        import json
        state = json.dumps(state, ensure_ascii=False)
    v = _judge_and_log(str(state), source=str(body.get("source", "user")) if isinstance(body, dict) else "user")
    return {
        "answers": {
            "action": {"choice": v.action},
            "risk": {"score": v.risk},
        },
        "verdict": v.action,
        "policy_id": v.policy_id,
        "reason": v.reason,
        "mock": v.mock,
        "verdict_source": v.verdict_source,
    }

def run(host: str = "127.0.0.1", port: int = 8787):
    import uvicorn
    uvicorn.run(app, host=host, port=port)
