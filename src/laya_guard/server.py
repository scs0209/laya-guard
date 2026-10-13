"""laya-guard HTTP server. POST /guard + Jev-compatible POST /v1/systemone."""
from __future__ import annotations
import hmac
import os
import time
import secrets
from datetime import datetime, timezone
from pathlib import Path
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from . import dashboard, install
from .guard import judge, is_mock
from .log import load_entries, log_enabled, log_path, log_verdict

app = FastAPI(title="laya-guard")

# Changing an agent's settings from the dashboard needs this token. It is made when the server starts
# and only the dashboard page carries it; nothing sends CORS headers, so another site can't read it.
_WRITE_TOKEN = secrets.token_urlsafe(32)


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
    return HTMLResponse(dashboard.render_page(nonce, _WRITE_TOKEN), headers=dashboard.page_headers(nonce))

@app.get("/api/stats")
def api_stats(request: Request, window: str = "24h"):
    _require_loopback_host(request)
    if window not in dashboard.WINDOWS:
        raise HTTPException(status_code=400, detail=f"window must be one of {', '.join(dashboard.WINDOWS)}")
    stats = dashboard.build_stats(load_entries(), datetime.now(timezone.utc), window)
    stats["health"] = {"ok": True, "mock": is_mock(), "version": _version(), "logging": log_enabled(),
                       "log_path": dashboard.display_path(log_path())}
    return JSONResponse(stats, headers=dashboard.api_headers())

def _require_write_access(request: Request) -> None:
    """Everything that edits a settings file: loopback Host, same-origin, a JSON body (a plain
    cross-site form post can't send one) and the token only the dashboard page has."""
    _require_loopback_host(request)
    if not dashboard.origin_allowed(request.headers.get("origin"), request.headers.get("host")):
        raise HTTPException(status_code=403, detail="cross-origin request refused")
    if not request.headers.get("content-type", "").lower().startswith("application/json"):
        raise HTTPException(status_code=415, detail="send application/json")
    if not dashboard.token_ok(request.headers.get("x-laya-token"), _WRITE_TOKEN):
        raise HTTPException(status_code=403, detail="missing or wrong token; reload the dashboard")

@app.get("/api/agents")
def api_agents(request: Request):
    """The agents that look installed on this machine, and where the hook stands for the ones laya-guard
    can protect (user scope only: that is what the dashboard changes). Others come back as "unsupported"."""
    _require_loopback_host(request)
    home, agents = Path.home(), []
    for found in install.detect_agents(home):
        if not (found["found"] or found["supported"]):
            continue                      # not here and nothing to install: don't list it
        row = dict(found, state="unsupported", scope=None, path=None, detail="", managed=None)
        if found["supported"]:
            st = install.status("user", home, Path.cwd(), found["id"])
            row.update(scope="user", path=dashboard.display_path(st.path), state=st.state, detail=st.detail,
                       managed=install.is_managed(st.command) if st.command else None)
        agents.append(row)
    return JSONResponse({"agents": agents}, headers=dashboard.api_headers())

class AgentReq(BaseModel):
    scope: str = "user"
    mode: str = "preview"        # preview: show the change; apply: make it (needs the preview's diff_id)
    diff_id: str | None = None
    force: bool = False

@app.post("/api/agents/{agent}/{action}", dependencies=[Depends(_require_write_access)])
def api_agent_action(agent: str, action: str, req: AgentReq):
    # _require_write_access runs as a dependency, so a request without the token is refused
    # before its body is even parsed
    if agent not in install.AGENTS or action not in ("install", "uninstall"):
        raise HTTPException(status_code=404, detail="unknown agent or action")
    if req.scope != "user":
        raise HTTPException(status_code=400, detail="only the user scope can be changed from the dashboard")
    if req.mode not in ("preview", "apply"):
        raise HTTPException(status_code=400, detail="mode must be preview or apply")
    try:
        plan = install.make_plan(action, agent, "user", Path.home(), Path.cwd(), force=req.force)
    except install.InstallError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    shown = dashboard.display_path(plan.settings.path)
    head, _, rest = plan.diff.partition("\n@@")                        # only the ---/+++ header lines carry the path
    diff = head.replace(str(plan.settings.path), shown) + (("\n@@" + rest) if rest else "")
    notes = ["The file has trailing commas. The agent accepts them, strict JSON doesn't, and they are dropped "
             "when the file is rewritten."] if plan.settings.repaired else []
    body = {"outcome": plan.outcome, "path": shown, "changed": plan.changed, "notes": notes,
            "diff": diff, "diff_id": plan.diff_id if plan.changed else None, "left": plan.left}
    if req.mode == "preview" or not plan.changed:
        return JSONResponse({**body, "applied": False}, headers=dashboard.api_headers())
    # applying needs the id of the diff the user was shown: if the file changed since, it won't match
    if not req.diff_id or not hmac.compare_digest(req.diff_id, plan.diff_id):
        raise HTTPException(status_code=409, detail="the settings changed since the preview; review the change again")
    try:
        backup, probes = install.apply_plan(plan)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"couldn't write the settings file: {exc.strerror or exc}")
    return JSONResponse({**body, "applied": True, "backup": dashboard.display_path(backup) if backup else None,
                         "probes": [{"label": l, "expected": e, "exit": g, "ok": e == g} for l, e, g in probes]},
                        headers=dashboard.api_headers())

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
