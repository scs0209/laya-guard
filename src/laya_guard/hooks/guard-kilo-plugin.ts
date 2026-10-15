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
// (regex-flagged blocks, safe commands pass). A call it can't judge has
// nobody to ask here, so it is blocked unless LAYA_GUARD_FAIL=open.
// Override the CLI path with LAYA_GUARD_BIN.

import { spawnSync } from "node:child_process";
import { resolve } from "node:path";

type Verdict = { action: string; risk: number; policy_id: string; reason: string };

const GUARD_URL = process.env.LAYA_GUARD_URL ?? "http://localhost:8787/guard";
const GUARD_BIN = process.env.LAYA_GUARD_BIN ?? "laya-guard";
const FAIL_MODE = process.env.LAYA_GUARD_FAIL ?? "ask";
const NO_PROMPT = "this agent has no approval prompt, so it is blocked (LAYA_GUARD_FAIL=open lets it through)";

const AGENT = "kilo";

type Call = { tool_name?: string; command?: string };

// What the guard's file activity log needs to know about a call, as `laya-guard check` flags.
const callFlags = (call?: Call): string[] => [
  ...(call?.tool_name ? ["--tool-name", call.tool_name] : []),
  ...(call?.command ? ["--shell"] : []),
];

async function judge(text: string, cwd: string, call?: Call): Promise<Verdict | null> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 10000);
  try {
    const res = await fetch(GUARD_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tool_call: text, source: "tool", cwd, agent: AGENT, ...call }),
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
function judgeOffline(text: string, cwd: string, call?: Call): Verdict {
  let v: Verdict | null = null;
  try {
    const r = spawnSync(GUARD_BIN, ["check", "--offline", "--json", "--cwd", cwd, "--agent", AGENT, ...callFlags(call)], {
      input: text, encoding: "utf8", timeout: 10000, env: { ...process.env, LAYA_GUARD_FAIL: FAIL_MODE },
    });
    if (r.stdout) v = JSON.parse(r.stdout) as Verdict;
  } catch {
    v = null;
  }
  v ??= FAIL_MODE === "open"
    ? { action: "allow", risk: 0.5, policy_id: "offline", reason: "guard server and CLI unavailable; not judged" }
    : { action: "block", risk: 0.5, policy_id: "offline", reason: `guard server and CLI unavailable; not judged, ${NO_PROMPT}` };
  if (v.action === "review") v = { ...v, action: "block", reason: `server offline, not judged by the model; ${NO_PROMPT}` };
  console.error(`laya-guard: server unreachable, offline check -> ${v.action}`);
  return v;
}

const LayaGuard = async ({ directory }: { directory?: string } = {}) => ({
  "tool.execute.before": async (input: { tool: string }, output: { args: Record<string, unknown> }) => {
    const text = `${input.tool}(${JSON.stringify(output.args)})`;
    const workdir = typeof output.args["workdir"] === "string" ? (output.args["workdir"] as string) : ".";
    const cwd = resolve(directory ?? process.cwd(), workdir);
    // The offline safe-command list matches a bare command, not the tool-call wrapper the model sees.
    const command = input.tool === "bash" && typeof output.args["command"] === "string" ? (output.args["command"] as string) : "";
    const call: Call = { tool_name: input.tool, ...(command ? { command } : {}) };
    const v = (await judge(text, cwd, call)) ?? judgeOffline(command || text, cwd, call);
    if (v.action === "block") {
      throw new Error(`laya-guard blocked ${input.tool} [${v.policy_id} risk=${v.risk}]: ${v.reason}`);
    }
    if (v.action === "review") {
      console.error(`laya-guard review [${v.policy_id} risk=${v.risk}]: ${v.reason}`);
    }
  },
});

// Kilo registers a plugin from its default export; a bare named export is only accepted as a legacy form.
export default { id: "laya-guard", server: LayaGuard };
