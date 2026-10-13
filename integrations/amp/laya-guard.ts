// laya-guard for Amp — gates shell commands through a local judge (a `tool.call` plugin).
//
// Install: `laya-guard install amp`, or copy to ~/.config/amp/plugins/laya-guard.ts
// (project: .amp/plugins/laya-guard.ts) and run "plugins: reload" in Amp.
// Needs: laya-guard server on localhost:8787 (laya-guard serve).
// Override: LAYA_GUARD_URL env (default http://localhost:8787/guard).
//
// Semantics: block -> the call is rejected with the reason (Amp tells the model, the command never runs).
// review -> allowed with a stderr note: a plugin has no ask lane of its own. Only shell commands are
// judged; every other tool call passes. Guard unreachable or slower than 10s -> `laya-guard check --offline`
// (regex-flagged blocks, safe commands pass). A command it can't judge has nobody to ask here, so it is
// rejected unless LAYA_GUARD_FAIL=open.
// Override the CLI path with LAYA_GUARD_BIN.
//
// Amp's older `delegate` permissions helper (amp/permissions-helper) still exists for people on the
// legacy rules; Amp recommends plugins for this now.

import type { PluginAPI } from '@ampcode/plugin'
import { spawnSync } from 'child_process'

type Verdict = { action: string; risk: number; policy_id: string; reason: string }

const GUARD_URL = process.env.LAYA_GUARD_URL ?? 'http://localhost:8787/guard'
const GUARD_BIN = process.env.LAYA_GUARD_BIN ?? 'laya-guard'
const FAIL_MODE = process.env.LAYA_GUARD_FAIL ?? 'ask'
const NO_PROMPT = 'this agent has no approval prompt, so it is blocked (LAYA_GUARD_FAIL=open lets it through)'

async function judge(text: string, cwd: string): Promise<Verdict | null> {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), 10000)
  try {
    const res = await fetch(GUARD_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tool_call: text, source: 'tool', cwd }),
      signal: ctrl.signal,
    })
    if (!res.ok) return null
    return (await res.json()) as Verdict
  } catch {
    return null
  } finally {
    clearTimeout(timer)
  }
}

// Server down or too slow: regex + safe-command check without the model.
function judgeOffline(text: string, cwd: string): Verdict {
  let v: Verdict | null = null
  try {
    const r = spawnSync(GUARD_BIN, ['check', '--offline', '--json', '--cwd', cwd], {
      input: text, encoding: 'utf8', timeout: 10000, env: { ...process.env, LAYA_GUARD_FAIL: FAIL_MODE },
    })
    if (r.stdout) v = JSON.parse(r.stdout) as Verdict
  } catch {
    v = null
  }
  v ??= FAIL_MODE === 'open'
    ? { action: 'allow', risk: 0.5, policy_id: 'offline', reason: 'guard server and CLI unavailable; not judged' }
    : { action: 'block', risk: 0.5, policy_id: 'offline', reason: `guard server and CLI unavailable; not judged, ${NO_PROMPT}` }
  if (v.action === 'review') v = { ...v, action: 'block', reason: `server offline, not judged by the model; ${NO_PROMPT}` }
  console.error(`laya-guard: server unreachable, offline check -> ${v.action}`)
  return v
}

// The shell command of a tool call, or null when the call isn't a shell command.
function shellCommand(amp: PluginAPI, event: any): string | null {
  try {
    const found: any = (amp as any).helpers?.shellCommandFromToolCall?.(event)
    const command = typeof found === 'string' ? found : found?.command
    if (typeof command === 'string' && command) return command
  } catch {
    // fall through to the plain lookup
  }
  const input = event?.input
  if (input && typeof input === 'object' && /^(bash|shell|run_shell_command)$/i.test(String(event?.tool))) {
    for (const key of ['cmd', 'command']) {
      if (typeof input[key] === 'string' && input[key]) return input[key]
    }
  }
  return null
}

export default function (amp: PluginAPI) {
  amp.on('tool.call', async (event: any) => {
    const command = shellCommand(amp, event)
    if (!command) return { action: 'allow' }
    const cwd = process.cwd()
    const v = (await judge(command, cwd)) ?? judgeOffline(command, cwd)
    if (v.action === 'block') {
      return { action: 'reject-and-continue', message: `laya-guard blocked this command [${v.policy_id} risk=${v.risk}]: ${v.reason}` }
    }
    if (v.action === 'review') {
      console.error(`laya-guard review [${v.policy_id} risk=${v.risk}]: ${v.reason}`)
    }
    return { action: 'allow' }
  })
}
