// The state of an AI scenario run, built by folding the server's events into it. Pure (no DOM, no three.js), so node tests
// it. Every row of the history says where it came from, and the kinds never share a look:
//   ai         what the AI agent wrote: a plan, an action, its reason, a message, a hypothesis
//   simulated  the virtual world's own bookkeeping: files held in memory, commands never run
//   guard      laya-guard's real evaluator (labelled REAL, or MOCK EVALUATOR when the server has no model)
//   engine     the engine's own facts about the run (counts, limits, how it ended)

import { badge } from "./pipeline.js";

export function initialAiState() {
  return {
    id: null, status: "idle", prompt: "", agent: null, model: null, maxSteps: 8, mode: "run", reviewPolicy: "ask", limits: null,
    plan: null, files: Object.create(null), rows: [], step: 0, thinking: null, review: null, relay: null, summary: null, error: null,
    counts: { allow: 0, review: 0, block: 0 }, commands: [], cost: 0, aiCalls: 0, lastSeq: 0,
  };
}

var TERMINAL = { "run.finished": "finished", "run.stopped": "stopped", "run.error": "error" };
export function isTerminal(type) { return !!TERMINAL[type]; }

/** The statuses a run can be in, for the controls: which buttons make sense. */
export function controlsFor(state) {
  var live = ["interpreting", "running", "paused", "waiting-review"].indexOf(state.status) >= 0;
  return {
    canStart: !live && state.status !== "connecting",
    canPause: state.status === "running" || state.status === "interpreting",
    canResume: state.status === "paused",
    canStep: state.status === "paused" || state.status === "running",
    canStop: live || state.status === "connecting",
    canReview: state.status === "waiting-review",
  };
}

/** An agent from the server's status that a run can use now: there, and not known to be logged out or refusing its key. */
export function usableAgent(a) { return !!a && !!a.found && a.logged_in !== false; }

/** The agent a run goes to: the one chosen if it is usable, else the first usable one (the server lists Claude Code first),
 *  else the first one, so the page can say why nothing is ready. */
export function pickAgent(agents, chosenId) {
  var list = agents || [];
  var chosen = list.filter(function (a) { return a.id === chosenId && usableAgent(a); })[0];
  return chosen || list.filter(usableAgent)[0] || list[0] || null;
}

export function describeAction(a) {
  switch (a.type) {
    case "read_file": return "Reads " + a.path;
    case "write_file": return "Writes " + a.path;
    case "list_dir": return "Lists " + (a.path || ".");
    case "run_command": return "Proposes a command";
    case "message": return "Says";
    case "finish": return "Finishes";
    default: return a.type;
  }
}

function row(state, e, kind, fields) {
  var r = Object.assign({ id: e.seq, seq: e.seq, step: e.step != null ? e.step : null, kind: kind, t: e.t }, fields);
  state.rows.push(r);
  return r;
}
var AI = function () { return badge("ai"); }, SIM = function () { return badge("simulated"); }, ENGINE = function () { return badge("engine"); };

/** Fold one event into the state (mutates and returns it). Unknown events are ignored, so a newer server is fine. */
export function reduceAiEvent(state, e) {
  if (e.seq) state.lastSeq = Math.max(state.lastSeq, e.seq);
  switch (e.type) {
    case "run.started":
      state.id = e.run_id; state.status = "interpreting"; state.prompt = e.prompt; state.agent = e.agent; state.model = e.model;
      state.maxSteps = e.max_steps; state.mode = e.mode; state.reviewPolicy = e.review_policy; state.limits = e.limits || null;
      row(state, e, "system", { badge: ENGINE(), title: "Run started", text: e.agent.name + (e.model ? " (" + e.model + ")" : "") + ", up to " + e.max_steps + " steps" + (e.mode === "step" ? ", step by step" : "") });
      break;
    case "ai.thinking":
      state.thinking = { phase: e.phase, step: e.step, attempt: e.attempt, t: e.t };
      state.aiCalls += 1;
      break;
    case "ai.reply":
      state.thinking = null; state.relay = null;
      state.cost = e.total_cost_usd != null ? e.total_cost_usd : state.cost;
      break;
    case "ai.invalid":
      state.relay = null;
      row(state, e, "system", { badge: ENGINE(), title: "The agent's reply was rejected", text: e.errors.join("; ") + (e.will_retry ? " (it is asked again)" : "") });
      break;
    case "relay.wait":
      state.relay = { decisionId: e.decision_id, phase: e.phase, step: e.step, attempt: e.attempt, deadlineS: e.deadline_s, since: e.t };
      row(state, e, "system", { badge: ENGINE(), title: "Waiting for your answer", text: "The manual relay is holding step " + (e.step || 0) + " (" + e.phase + "). Answer below so the run can go on." });
      break;
    case "plan":
      state.plan = e.plan; state.status = state.status === "paused" ? "paused" : "running";
      row(state, e, "ai", { badge: AI(), title: "Plan: " + e.plan.title, text: e.plan.summary, detail: { goal: e.plan.goal, hazards: e.plan.hazards, watchFor: e.plan.watch_for, actors: e.plan.actors, files: e.plan.files } });
      break;
    case "world.snapshot":
      state.files = Object.create(null);                 // paths are the AI's words: no inherited keys such as __proto__
      Object.keys(e.files).forEach(function (p) { state.files[p] = { content: e.files[p], size: e.files[p].length, created: false, changed: false, flagged: null, history: [] }; });
      break;
    case "step.begin":
      state.step = e.step;
      break;
    case "ai.action":
      row(state, e, "ai", { badge: AI(), title: describeAction(e.action), action: e.action, text: e.action.type === "run_command" ? e.action.command : e.action.type === "message" ? e.action.text : e.action.type === "finish" ? e.action.outcome : e.action.path || "",
        why: e.action.why || "" , code: e.action.type === "run_command" });
      break;
    case "world.read":
      row(state, e, "world", { badge: SIM(), title: e.ok ? "File read" : "No such file", text: e.path + (e.ok ? " (" + e.chars + " characters)" : "") });
      break;
    case "world.write": {
      var f = state.files[e.path] || (state.files[e.path] = { content: "", size: 0, created: false, changed: false, flagged: null, history: [] });
      f.content = e.content; f.size = e.after_chars; f.created = e.created; f.changed = !e.created; f.history.push({ step: e.step, diff: e.diff, created: e.created });
      f.flagged = null;
      row(state, e, "world", { badge: SIM(), title: e.created ? "File created" : "File changed", text: e.path + " (" + e.before_chars + " → " + e.after_chars + " characters)", diff: e.diff, path: e.path });
      break;
    }
    case "world.list":
      row(state, e, "world", { badge: SIM(), title: "Directory listing", text: (e.path || ".") + ": " + (e.entries.join(", ") || "(empty)") });
      break;
    case "guard.event":
      break;                                         // the page animates these as they come; the result row carries the facts
    case "guard.result": {
      var r = e.result, v = r.verdict;
      state.counts[v.action] = (state.counts[v.action] || 0) + 1;
      state.commands.push({ step: e.step, command: e.command, action: v.action, policy: v.policy_id, mode: r.mode });
      (r.segments || []).forEach(function (s) {
        if (s.script && !s.script.clean) {
          Object.keys(state.files).forEach(function (p) { if (p.split("/").pop() === "package.json") state.files[p].flagged = { key: s.script.key, policy: s.script.policy_id }; });
        }
      });
      row(state, e, "guard", { badge: badge(r.mode), title: "laya-guard: " + v.action, verdict: v, result: r, command: e.command, text: v.policy_id + " · " + v.reason });
      break;
    }
    case "command.outcome":
      row(state, e, "world", { badge: SIM(), title: { executed: "Executed (inert)", "approved-executed": "Approved, executed (inert)", blocked: "Not run: blocked", denied: "Not run: denied" }[e.state] || e.state, text: e.text, outcome: e.state });
      break;
    case "review.wait":
      state.status = "waiting-review";
      state.review = { step: e.step, command: e.command, deadlineS: e.deadline_s, auto: !!e.auto, since: e.t };
      row(state, e, "review", { badge: ENGINE(), title: e.auto ? "Held for review (decided by the run's policy)" : "Held for review: waiting for you", text: e.command, code: true });
      break;
    case "review.decision":
      state.review = null; state.status = "running";
      row(state, e, "review", { badge: ENGINE(), title: "Review: " + (e.decision === "approve" ? "approved" : "denied"), text: { user: "by you", timeout: "no answer in time, so denied", "auto-deny": "by the run's policy (deny)", "auto-approve": "by the run's policy (approve)" }[e.by] || e.by });
      break;
    case "agent.message":
      break;                                           // the ai.action row of the same step already shows it
    case "run.paused":
      state.status = "paused";
      row(state, e, "system", { badge: ENGINE(), title: "Paused", text: "waiting before step " + e.step });
      break;
    case "run.resumed":
      state.status = "running";
      break;
    case "summary":
      state.summary = { facts: e.facts, ai: e.ai };
      break;
    case "run.finished":
      state.status = "finished"; state.thinking = null; state.review = null; state.relay = null;
      row(state, e, "system", { badge: ENGINE(), title: "Finished", text: { "agent-finished": "the agent finished", "step-limit": "the step limit was reached" }[e.reason] || e.reason });
      break;
    case "run.stopped":
      state.status = "stopped"; state.thinking = null; state.review = null; state.relay = null;
      row(state, e, "system", { badge: ENGINE(), title: "Stopped", text: e.message || "stopped" });
      break;
    case "run.error":
      state.status = "error"; state.thinking = null; state.review = null; state.relay = null; state.error = { kind: e.kind, message: e.message };
      row(state, e, "system", { badge: ENGINE(), title: "The run ended with an error", text: e.message });
      break;
    default:
      break;
  }
  return state;
}

/** Fold a whole list of events (a replay of a finished run). */
export function reduceAll(events) {
  var s = initialAiState();
  events.forEach(function (e) { reduceAiEvent(s, e); });
  return s;
}

/** The facts of a finished run that the engine computed, kept apart from what the AI said about them. */
export function outcomeParts(state) {
  var sm = state.summary;
  if (!sm) return null;
  var f = sm.facts, v = f.verdicts, blocked = v.block + v.review;
  return {
    facts: [
      f.commands_judged + " command" + (f.commands_judged === 1 ? "" : "s") + " judged by laya-guard: " + v.allow + " allowed, " + v.review + " held for review, " + v.block + " blocked",
      f.commands_judged === 0 ? "The agent proposed no command, so laya-guard had nothing to judge" : null,
      f.files_written.length ? "Files the agent wrote: " + f.files_written.join(", ") : "The agent wrote no files",
      "Ended because: " + ({ "agent-finished": "the agent finished", "step-limit": "the step limit was reached" }[f.ended_because] || f.ended_because),
    ].filter(Boolean),
    caught: f.commands_judged === 0 ? null : blocked > 0,
    ai: sm.ai,
  };
}
