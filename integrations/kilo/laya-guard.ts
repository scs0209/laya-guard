// laya-guard for Kilo Code — gates tool execution through a local judge.
//
// Install (project): copy to .kilo/plugin/laya-guard.ts
// Install (global):  copy to ~/.config/kilo/plugin/laya-guard.ts
// Needs: laya-guard server on localhost:8787 (laya-guard serve).
// Override: LAYA_GUARD_URL env (default http://localhost:8787/guard).
//
// Semantics: block -> throw (tool never runs). review -> allow + stderr
// warning, because tool.execute.before cannot trigger Kilo's ask flow;
// use `permission` rules in kilo.jsonc if you want hard asks.
// Guard unreachable or slower than 10s -> `laya-guard check --offline`
// (regex-flagged blocks, safe commands pass, the rest passes unless
// LAYA_GUARD_FAIL=closed). Override the CLI path with LAYA_GUARD_BIN.

import { spawnSync } from "node:child_process";

type Verdict = { action: string; risk: number; policy_id: string; reason: string };

const GUARD_URL = process.env.LAYA_GUARD_URL ?? "http://localhost:8787/guard";
const GUARD_BIN = process.env.LAYA_GUARD_BIN ?? "laya-guard";
const FAIL_CLOSED = process.env.LAYA_GUARD_FAIL === "closed";

async function judge(text: string): Promise<Verdict | null> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 10000);
  try {
    const res = await fetch(GUARD_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tool_call: text, source: "tool" }),
      signal: ctrl.signal,
    });
    if (!res.ok) return null;
    return (await res.json()) as Verdict;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

// Server down or too slow: regex + safe-command check without the model.
function judgeOffline(text: string): Verdict {
  let v: Verdict | null = null;
  try {
    const r = spawnSync(GUARD_BIN, ["check", "--offline", "--json"], { input: text, encoding: "utf8", timeout: 10000 });
    if (r.stdout) v = JSON.parse(r.stdout) as Verdict;
  } catch {
    v = null;
  }
  v ??= { action: FAIL_CLOSED ? "block" : "allow", risk: 0.5, policy_id: "offline", reason: "guard server and CLI unavailable; not judged" };
  console.error(`laya-guard: server unreachable, offline check -> ${v.action}`);
  return v;
}

export const LayaGuard = async () => ({
  "tool.execute.before": async (input: { tool: string }, output: { args: Record<string, unknown> }) => {
    const text = `${input.tool}(${JSON.stringify(output.args)})`;
    const v = (await judge(text)) ?? judgeOffline(text);
    if (v.action === "block") {
      throw new Error(`laya-guard blocked ${input.tool} [${v.policy_id} risk=${v.risk}]: ${v.reason}`);
    }
    if (v.action === "review") {
      console.error(`laya-guard review [${v.policy_id} risk=${v.risk}]: ${v.reason}`);
    }
  },
});
