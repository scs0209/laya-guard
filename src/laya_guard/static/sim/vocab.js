// Words for the view. Kept in one place so the panels, the strip and the timeline say the same thing.

export var STATION_NAME = {
  terminal: "Agent terminal", queue: "Command queue", policy: "Policy gate", engine: "Laya engine", decision: "Decision gate",
  execution: "Execution zone", review: "Review queue", incident: "Incident & audit",
};
export var STAGE_NAME = {
  terminal: "Terminal", queue: "Queue", policy: "Policy gate", engine: "Laya engine", decision: "Decision", zone: "Outcome",
};
export var ACTION_WORD = { allow: "Allowed", review: "Review", block: "Blocked" };
export var VIA_WORD = {
  regex: "policy rules", allowlist: "safe-command list", model: "Laya model", heuristic: "heuristics", limit: "length limit",
  offline: "the hook's offline check",
};
// the tone of each status, for the colour of its chip: ok (green), warn (amber), bad (red), plain
export var STATUS_TONE = {
  "policy:hit": "bad", "policy:script": "bad", "policy:limit": "bad", "policy:floor": "warn", "policy:safe": "ok", "policy:clear": "plain",
  "policy:offline": "warn", "policy:unknown": "plain",
  "engine:ran": "ok", "engine:skipped": "plain", "engine:fallback": "warn", "engine:failed": "bad", "engine:unknown": "plain",
  "queue:bypassed": "warn",
  "decision:allow": "ok", "decision:review": "warn", "decision:block": "bad",
  "zone:execution": "ok", "zone:review": "warn", "zone:incident": "bad",
};
export function statusTone(stage, status) { return STATUS_TONE[stage + ":" + status] || "plain"; }
/** The tone of a status chip for a run: an illustration (simulation data) is never drawn in the colours of a result. */
export function toneFor(run, stage, status) { return run && run.source === "simulation" ? "sim" : statusTone(stage, status); }
export var STATUS_WORD = {
  sent: "sent", queued: "arrived", bypassed: "bypassed", hit: "rule matched", floor: "review floor", safe: "safe list", script: "script checked",
  clear: "nothing matched", limit: "too long", offline: "run by the hook", ran: "model ran", skipped: "skipped", fallback: "no model", failed: "model failed",
  unknown: "not recorded", allow: "allow", review: "review", block: "block", execution: "runs", incident: "stopped",
};
export function statusWord(stage, status) {
  if (stage === "zone" && status === "review") return "held";
  return STATUS_WORD[status] || status;
}

export var TOUR = [
  { station: "terminal:0", title: "1 · An agent asks to run a command", text: "A coding agent wants to run a shell command. Its hook sends the command to laya-guard before it runs. The two terminals are the agents; the log doesn't say which agent made a call." },
  { station: "queue", title: "2 · It reaches the local guard", text: "The call arrives at the guard service on this machine. Nothing leaves it: no hosted model, no API key." },
  { station: "policy", title: "3 · Rules look first", text: "The policy gate checks the regex policies in policy.yaml, the safe-command list and any package.json script the command would run. Each column is one policy. A match can decide the verdict without the model." },
  { station: "engine", title: "4 · The model only judges what's left", text: "The Laya model runs locally and only sees calls the rules didn't settle. When rules decide, the call never goes here, and in mock mode the model is never used." },
  { station: "decision", title: "5 · A verdict: allow, review or block", text: "Every call gets a verdict, a risk score, the policy that matched and a reason. Those fields are what you see in the inspector, exactly as the evaluator returned them." },
  { station: "incident", title: "6 · Where each verdict ends up", text: "Allowed calls go back to the agent to run (laya-guard never runs them itself), reviewed calls wait for a person, blocked calls are stopped and recorded. Try the Attack lab to send a scenario through." },
];
