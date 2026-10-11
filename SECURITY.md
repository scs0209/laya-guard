# Security Policy

## Supported versions

Only the latest release on PyPI (`laya-guardrail`) receives fixes.

## Reporting a vulnerability

Please **do not open a public issue** for vulnerabilities in laya-guard itself.
Report privately via GitHub: **Security → Report a vulnerability** on
[scs0209/laya-guard](https://github.com/scs0209/laya-guard/security/advisories/new).

Expect an acknowledgement within 7 days. Please include a minimal reproduction
(input, command, version, and the verdict you got).

### In scope (report privately)

- Raw `tool_call` text, or anything derived from it beyond the documented
  truncated SHA-256, written to the telemetry log
- The server or a hook adapter executing, or enabling execution of, the
  content it is judging
- A crafted `tool_call` that crashes or hangs the server, or that takes the
  offline fallback longer than its timeout
- An adapter returning `allow` for a `block` verdict, or mishandling its
  agent's hook contract
- Malicious `policy.yaml` handling beyond the documented trust model
  (the policy file is trusted local configuration)

### Out of scope (open a normal issue)

- A single input judged wrongly (missed attack or false positive). These are
  quality bugs — please open an issue with the input and the verdict so it
  can become an eval case.
- With the server unreachable, calls that are neither regex-flagged nor safe
  commands pass unless `LAYA_GUARD_FAIL=closed` — documented and intentional.
- Exposing the server beyond localhost with `--host 0.0.0.0` — it has no
  authentication by design.
