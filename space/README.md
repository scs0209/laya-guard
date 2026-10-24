---
title: laya-guard trial
emoji: 🛡️
colorFrom: indigo
colorTo: gray
sdk: gradio
sdk_version: 6.30.0
app_file: app.py
python_version: "3.12"
startup_duration_timeout: 30m
pinned: false
license: mit
short_description: See what laya-guard says about an agent's shell command
models:
  - convaiinnovations/laya
tags:
  - security
  - guardrails
  - ai-agents
  - coding-agents
  - claude-code
  - shell
  - prompt-injection
thumbnail: https://huggingface.co/spaces/Ayaannnnnnn/laya-guard-trial/resolve/main/thumbnail.png
# the files laya-guard's judge loads, fetched at build time so a woken Space doesn't download them
preload_from_hub:
  - convaiinnovations/laya multilingual/model.safetensors,multilingual/rl_agent_config.json,multilingual/encoder/config.json,multilingual/tokenizer/tokenizer.json,multilingual/tokenizer/tokenizer_config.json
---

# laya-guard web trial

Paste a shell command a coding agent might run and see what [laya-guard](https://github.com/scs0209/laya-guard)'s hook
would answer: allow, review or block, from regex policies, a safe-command lane and the ~322M
[Laya](https://huggingface.co/convaiinnovations/laya) model (Apache-2.0).

This Space runs only the judge: the page in `index.html`, `POST /api/judge` and `GET /api/health`, served by
`gr.Server` in `app.py`, with the judging in `trial.py`. The full `laya-guard serve` app is not used, because its
dashboard can change an agent's settings and must stay on localhost.

- Commands are judged in memory and not logged or stored. Nothing is run.
- There is no project folder, so unlike the hook it doesn't check the package.json scripts that `npm test` and the
  like would run.
- Up to 4,000 characters per command, 20 requests a minute per visitor and 300 a minute in all (a visitor can forge
  the forwarded address, not the total).
- Pick the agent whose hook you want to see: Claude Code and Cursor ask you about a review; Codex and Gemini CLI let it
  run with a warning, so a model-only block stays a block for them.
- The model runs on CPU. The Space is on ZeroGPU (the free tier for Gradio Spaces) only because that is where a free
  account can host one; no request ever asks for a GPU, so visitors' GPU quota isn't used.

## Deploying

The files live in [`space/`](https://github.com/scs0209/laya-guard/tree/main/space) of the laya-guard repository.

```bash
hf repos create <you>/laya-guard-trial --type space --space-sdk gradio --flavor zero-a10g --public \
    --env GRADIO_SSR_MODE=false
hf upload <you>/laya-guard-trial space --repo-type space --exclude "**/__pycache__/**"
```

`GRADIO_SSR_MODE=false` is needed for `gr.Server` to serve the custom page at `/`. `requirements.txt` pins the
released `laya-guardrail`; when the page needs an unreleased change, point it at a git ref until the next release.
