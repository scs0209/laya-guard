"""laya-guard HTTP server. POST /guard + Jev-compatible POST /v1/systemone."""
from __future__ import annotations
import os
import time
import secrets
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from . import dashboard
from .guard import judge, is_mock
from .log import load_entries, log_enabled, log_path, log_verdict

app = FastAPI(title="laya-guard")


def _judge_and_log(text: str, source: str, cwd: str | None = None):
    t0 = time.perf_counter()
    v = judge(text, source=source, cwd=cwd)
    log_verdict(
        action=v.action, risk=v.risk, policy_id=v.policy_id, reason=v.reason,
        mock=v.mock, source=source,
        latency_ms=(time.perf_counter() - t0) * 1000.0, tool_call=text,
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

def _require_loopback_host(request: Request) -> None:
    # DNS rebinding: a page on another origin can make its hostname resolve to 127.0.0.1 and
    # then read these responses, but the browser still sends that hostname in the Host header.
    if not dashboard.host_allowed(request.headers.get("host")):
        raise HTTPException(status_code=403, detail="the dashboard is only served to localhost")

def _version() -> str:
    try:
        from importlib.metadata import version
        return version("laya-guardrail")
    except Exception:
        return "unknown"

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request):
    _require_loopback_host(request)
    nonce = secrets.token_urlsafe(16)
    return HTMLResponse(dashboard.render_page(nonce), headers=dashboard.page_headers(nonce))

@app.get("/api/stats")
def api_stats(request: Request, window: str = "24h"):
    _require_loopback_host(request)
    if window not in dashboard.WINDOWS:
        raise HTTPException(status_code=400, detail=f"window must be one of {', '.join(dashboard.WINDOWS)}")
    stats = dashboard.build_stats(load_entries(), datetime.now(timezone.utc), window)
    stats["health"] = {"ok": True, "mock": is_mock(), "version": _version(), "logging": log_enabled(),
                       "log_path": dashboard.display_path(log_path())}
    return JSONResponse(stats, headers=dashboard.api_headers())

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
