"""laya-guard web trial on a Hugging Face ZeroGPU Space: the page in index.html and POST /api/judge (trial.py).

A free account can only host Gradio Spaces on ZeroGPU. The judge is a ~322M model that runs on CPU, so nothing here
asks for a GPU: ZeroGPU only needs one @spaces.GPU function to exist, and visitors' GPU quota is never used.
"""
import spaces  # noqa: F401  # first: on ZeroGPU it patches torch.cuda before torch is imported

from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from gradio import Server
from starlette.concurrency import run_in_threadpool

import trial
from laya_guard.guard import is_mock, warmup

warmup()                                # load the model at start-up, not on the first visitor
PAGE = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")
limits = trial.RateLimit()

app = Server(title="laya-guard trial")


@spaces.GPU(duration=5)
def _zerogpu_needs_one_gpu_function() -> None:
    """Never called: the judge runs on CPU. ZeroGPU refuses a Space without a @spaces.GPU function."""


@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
def page() -> HTMLResponse:
    return HTMLResponse(PAGE, headers={"Cache-Control": "no-store"})


@app.get("/api/health")
def health() -> dict:
    return {"model": not is_mock()}


@app.post("/api/judge")
async def api_judge(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"detail": "send JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"detail": "send a JSON object"}, status_code=400)
    if not limits.allow(trial.client_of(request.headers, request.client.host if request.client else None)):
        return JSONResponse({"detail": "too many requests; try again in a minute"}, status_code=429)
    try:                                # off the event loop: the model takes ~0.1-0.3 s
        return JSONResponse(await run_in_threadpool(trial.judge_command, body.get("command"),
                                                    body.get("agent", "claude-code")))
    except trial.Refused as e:
        return JSONResponse({"detail": e.detail}, status_code=e.status)


demo = app                              # the Spaces runtime looks for `demo`
if __name__ == "__main__":
    demo.launch(ssr_mode=False)
