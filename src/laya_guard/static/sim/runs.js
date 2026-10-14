// A "run" is one call the view can draw and inspect: a recorded call from the verdict log, or a scenario run.
// Pure functions, no DOM.

import { stepsFor, actionOf } from "./pipeline.js";

/** A recorded call (a row of /api/stats `recent`). The log keeps a hash and the length, never the command. */
export function recordedRun(config, row) {
  var action = row.action;
  return {
    id: "rec|" + row.ts + "|" + row.tool_sha + "|" + row.action,
    kind: "recorded",
    source: "recorded",
    ts: row.ts,
    label: "#" + (row.tool_sha || "").slice(0, 8) + (row.tool_len != null ? " · " + row.tool_len + " chars" : ""),
    row: row,
    terminal: terminalOf(row.tool_sha),
    verdict: { action: action, risk: row.risk, policy_id: row.policy_id, reason: row.reason, verdict_source: row.via || null, mock: null },
    latency_ms: row.latency_ms,
    steps: stepsFor(config, row.via, action, row.policy_id, row.reason, row.degraded),
  };
}

/** Which of the two agent terminals a recorded call is drawn leaving from. The log doesn't say which agent made a
 *  call, so this is only a stable way to spread them out, and the inspector says so. */
export function terminalOf(sha) {
  var c = parseInt(String(sha || "0").charAt(0), 16);
  return isNaN(c) ? 0 : c % 2;
}

var seq = 0;
/** A scenario run from the evaluator's result, or from the scenario's illustration (simulation data). */
export function scenarioRun(config, scenario, result, kind) {
  seq += 1;
  if (result) {
    return {
      id: "scn|" + scenario.id + "|" + seq, kind: "scenario", source: kind || result.mode, ts: new Date().toISOString(),
      label: scenario.title, scenario: scenario, terminal: 2, result: result,
      verdict: result.verdict, latency_ms: result.latency_ms, steps: result.steps,
    };
  }
  var ill = scenario.illustrative;
  var steps = stepsFor(config, ill.via, ill.action, ill.policy_id, "");
  return {
    id: "scn|" + scenario.id + "|" + seq, kind: "scenario", source: "simulation", ts: new Date().toISOString(),
    label: scenario.title, scenario: scenario, terminal: 2, result: null,
    verdict: { action: ill.action, risk: null, policy_id: ill.policy_id, reason: ill.note, verdict_source: ill.via, mock: null },
    latency_ms: null, steps: steps,
  };
}

export function runAction(run) { return run.verdict && run.verdict.action || actionOf(run.steps); }

/** The newest run of each scenario, in the scenarios' own order, for the comparison table. */
export function compareRows(runs, scenarios) {
  var rows = [];
  scenarios.forEach(function (s) {
    for (var i = 0; i < runs.length; i++) {
      if (runs[i].kind === "scenario" && runs[i].scenario.id === s.id) { rows.push({ scenario: s, run: runs[i] }); break; }
    }
  });
  return rows;
}

/** Entries of `recent` (newest first) that haven't been seen yet, oldest first, so they are drawn in order. */
export function newRecorded(recent, seen) {
  var fresh = [];
  recent.forEach(function (r) {
    var id = "rec|" + r.ts + "|" + r.tool_sha + "|" + r.action;
    if (!seen[id]) fresh.push(r);
  });
  return fresh.reverse();
}
