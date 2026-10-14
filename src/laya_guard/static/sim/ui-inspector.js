// The inspector: what the selected station, policy or call is, with live numbers where there are any and recorded
// ones where there aren't. Only observable things are shown: the stage path, the verdict fields the evaluator
// returned, matches, timings. No model "reasoning" is shown because none is available.

import { el, clear, fmtMs, fmtInt, fmtClock } from "./dom.js";
import { badge } from "./pipeline.js";
import { STATION_NAME, STAGE_NAME, ACTION_WORD, VIA_WORD, statusTone, toneFor, statusWord } from "./vocab.js";
import { runAction } from "./runs.js";
import { decidedBy } from "./ui-lab.js";

function chip(text, tone) { return el("span", { "class": "chip " + tone, text: text }); }
function tone(action) { return action === "allow" ? "ok" : action === "review" ? "warn" : "bad"; }
function dl(rows) {
  var d = el("dl", { "class": "facts" });
  rows.forEach(function (r) { if (r[1] == null || r[1] === "") return; d.appendChild(el("dt", { text: r[0] })); d.appendChild(el("dd", { "class": r[2] || "", text: String(r[1]) })); });
  return d;
}
function stageAbout(app, stage) {
  var s = app.config.stages.find(function (x) { return x.id === stage; });
  return s ? s.about : "";
}
function block(title, ...children) { return el("section", { "class": "insp-block" }, title ? el("h4", { text: title }) : null, children); }

export function createInspector(root, actions) {
  var sig = "";
  return {
    update: function (app) {
      var t = app.stats && app.stats.totals;
      var s2 = [app.selection && app.selection.kind, app.selection && app.selection.id, app.focused && app.focused.id, app.focused && app.focused.source, app.inFlight,
                t ? [t.total, t.allow, t.review, t.block].join(",") : "", app.evalMock].join("|");
      if (s2 === sig) return;
      sig = s2;
      clear(root);
      var sel = app.selection;
      if (!sel) {
        root.appendChild(el("div", { "class": "insp-empty" },
          el("p", { text: "Nothing selected." }),
          el("p", { "class": "muted", text: "Click a station, a policy column or a call in the scene, or use the station buttons under it. Selecting a call replays it." })));
        return;
      }
      if (sel.kind === "run") {
        var run = app.byId[sel.id];
        root.appendChild(run ? runView(app, run, actions) : el("p", { text: "That call is no longer in the list." }));
      } else if (sel.kind === "policy") root.appendChild(policyView(app, sel.id, actions));
      else root.appendChild(stationView(app, sel.id, actions));
    },
  };
}

function stationView(app, id, actions) {
  var st = id.split(":")[0], t = app.stats && app.stats.totals, root = el("div", { "class": "insp" });
  root.appendChild(el("h3", { text: id.indexOf("terminal:") === 0 ? app.config.stages[0].label + (id === "terminal:2" ? " (scenario)" : " " + (Number(id.split(":")[1]) + 1)) : STATION_NAME[st] }));
  var stage = st === "execution" || st === "review" || st === "incident" ? "zone" : st;
  root.appendChild(el("p", { text: stage === "zone" ? app.config.status_text["zone:" + st] : stageAbout(app, stage) }));
  var rows = [];
  if (st === "terminal") {
    rows.push(["Source", id === "terminal:2" ? "Scenario terminal: simulation. Commands from the attack lab start here and are only judged, never run." : "Agent terminal. The log doesn't say which agent made a call, so recorded calls are only spread over the two terminals by their hash."]);
    rows.push(["Recorded calls", t ? fmtInt(t.total) + " " + app.windowText : "service not reachable"]);
  } else if (st === "queue") {
    rows.push(["Being drawn now", fmtInt(app.inFlight) + " call" + (app.inFlight === 1 ? "" : "s")]);
    rows.push(["Note", "laya-guard has no waiting line of its own: calls are judged as they arrive. The slots light up with the calls being drawn."]);
  } else if (st === "policy") {
    root.appendChild(dl(rows));
    root.appendChild(block("Policies", policyList(app, actions)));
    root.appendChild(openRow(actions.openPolicies, "Open Policies"));
    return root;
  } else if (st === "engine") {
    rows.push(["Evaluator", app.evalMock ? "Mock mode: the model isn't loaded, heuristics stand in" : "Local Laya model (runs on this machine)"]);
    if (t && t.total) { rows.push(["Judging time (all calls)", "average " + fmtMs(t.avg_latency_ms) + ", p95 " + fmtMs(t.p95_latency_ms) + " · recorded"]); }
    var d = app.stats && app.stats.degraded;
    if (d) rows.push(["Judged without the model", fmtInt(d.total) + " " + app.windowText + " (" + fmtInt(d.offline) + " with the server down, " + fmtInt(d.model_error) + " after a model error)"]);
  } else if (st === "decision") {
    if (t) { rows.push(["Allowed", fmtInt(t.allow) + " " + app.windowText]); rows.push(["Held for review", fmtInt(t.review)]); rows.push(["Blocked", fmtInt(t.block)]); }
    rows.push(["Returned for each call", "action, risk score, policy id, reason. No confidence value."]);
  } else {
    var action = st === "execution" ? "allow" : st === "review" ? "review" : "block";
    rows.push([ACTION_WORD[action], t ? fmtInt(t[action]) + " " + app.windowText + " · recorded" : "service not reachable"]);
    rows.push(["Resting here", fmtInt(app.zoneCounts[["execution", "review", "incident"].indexOf(st)]) + " most recent calls drawn as tokens (up to 24)"]);
    root.appendChild(dl(rows));
    root.appendChild(openRow(function () { actions.openCalls(action); }, "Show these calls"));
    return root;
  }
  root.appendChild(dl(rows));
  return root;
}

function policyList(app, actions) {
  var counts = {};
  ((app.stats && app.stats.policies) || []).forEach(function (p) { counts[p.policy_id] = p; });
  var ul = el("ul", { "class": "pol-list" });
  app.config.policies.forEach(function (p) {
    var c = counts[p.id];
    ul.appendChild(el("li", null, el("button", { type: "button", onclick: function () { actions.selectPolicy(p.id); } },
      el("b", { text: p.id }), el("span", { "class": "muted", text: p.title }),
      chip(p.on_match === "block" ? "blocks" : "reviews", p.on_match === "block" ? "bad" : "warn"),
      el("span", { "class": "strip-n", text: c ? fmtInt(c.count) : "0" }))));
  });
  return ul;
}

function policyView(app, id, actions) {
  var p = app.config.policies.find(function (x) { return x.id === id; });
  var root = el("div", { "class": "insp" }, el("h3", { text: id }));
  if (!p) { root.appendChild(el("p", { text: "Unknown policy." })); return root; }
  root.appendChild(el("p", { text: p.title }));
  var rec = ((app.stats && app.stats.policies) || []).find(function (x) { return x.policy_id === id; });
  root.appendChild(dl([
    ["A pattern match", p.on_match === "block" ? "blocks the call, the model is skipped" : "holds the call for review (a floor: the model still judges and can't go lower)"],
    ["Recorded verdicts with this policy", rec ? fmtInt(rec.count) + " " + app.windowText + " (" + fmtInt(rec.allow) + " allow, " + fmtInt(rec.review) + " review, " + fmtInt(rec.block) + " block)" : "none " + app.windowText + " · recorded"],
    ["Attack-lab scenarios that bear on it", app.config.scenarios.filter(function (s) { return s.relevant.indexOf(id) >= 0; }).map(function (s) { return s.title; }).join("; ") || "none"],
  ]));
  root.appendChild(openRow(actions.openPolicies, "Open Policies"));
  return root;
}

function runView(app, run, actions) {
  if (run.source === "pending") {
    return el("div", { "class": "insp" }, el("h3", { text: run.scenario.title }), el("code", { "class": "scn-cmd", text: run.scenario.command }),
      el("p", { text: "Waiting for the evaluator's answer. The call waits at the stage the evaluator has reached; the result and its stages appear here when it arrives." }));
  }
  var b = badge(run.source), a = runAction(run), v = run.verdict;
  var root = el("div", { "class": "insp" },
    el("h3", { text: run.kind === "scenario" ? run.scenario.title : "Recorded call " + run.label }),
    el("div", { "class": "res-head" }, el("span", { "class": "src " + b.tone, text: b.label }), chip(ACTION_WORD[a] || a, run.source === "simulation" ? "sim" : tone(a))),
    el("p", { "class": "muted", text: b.detail }));
  if (run.kind === "scenario") root.appendChild(el("code", { "class": "scn-cmd", text: run.scenario.command }));
  root.appendChild(dl([
    ["Decided by", decidedBy(run)],
    ["Risk score", v.risk != null ? Number(v.risk).toFixed(2) : run.source === "simulation" ? "not measured (simulation data)" : "–"],
    ["Confidence", run.kind === "recorded" ? "not logged" : run.source === "simulation" ? "not measured" : "the evaluator returns none"],
    ["Policy", v.policy_id],
    ["Judged as", run.result ? "a tool call (source=tool), the way most agent hooks send them: stricter thresholds than a typed command" : ""],
    [run.source === "simulation" ? "Note" : "Reason", v.reason],
    ["Judged by", run.source === "simulation" ? "" : v.verdict_source ? VIA_WORD[v.verdict_source] || v.verdict_source : "not recorded"],
    ["Judged in", run.latency_ms != null ? fmtMs(run.latency_ms) : ""],
    ["When", run.kind === "recorded" ? fmtClock(run.ts) : ""],
    ["Hash and length", run.kind === "recorded" ? run.label + " (the log keeps no command text)" : ""],
  ]));
  var path = el("ol", { "class": "path detail" });
  run.steps.forEach(function (s) {
    path.appendChild(el("li", null, el("div", { "class": "path-head" }, el("span", { "class": "path-stage", text: STAGE_NAME[s.stage] }), chip(statusWord(s.stage, s.status), toneFor(run, s.stage, s.status))),
      el("p", { "class": "muted", text: s.text })));
  });
  root.appendChild(block(run.source === "simulation" ? "A typical path (illustration, not measured)" : "Stages this call went through", path));
  if (run.kind === "scenario" && run.result && run.result.segments && run.result.segments.length > 1) {
    var seg = el("ul", { "class": "seg-list" });
    run.result.segments.forEach(function (s) {
      seg.appendChild(el("li", null, el("code", { text: s.whole ? "(the whole command, checked as one string)" : s.text }),
        chip(s.policy ? statusWord("policy", s.policy) : "–", s.policy ? statusTone("policy", s.policy) : "plain")));
    });
    root.appendChild(block("Each command of the chain", seg));
  }
  var actionsRow = el("div", { "class": "lab-actions" },
    el("button", { type: "button", "class": "sim-run", onclick: function () { actions.replayRun(run.id); } }, "Replay"));
  if (run.kind === "recorded") actionsRow.appendChild(el("button", { type: "button", "class": "sim-btn wide", onclick: function () { if (!actions.openInCalls(run)) root.appendChild(el("p", { "class": "muted", text: "That call isn't in the current time window of the Calls view. Widen the window and try again." })); } }, "Open in Calls"));
  else actionsRow.appendChild(el("span", { "class": "muted", text: "Scenario runs aren't logged, so there is nothing to open in Calls." }));
  root.appendChild(actionsRow);
  return root;
}

function openRow(fn, label) {
  return el("div", { "class": "lab-actions" }, el("button", { type: "button", "class": "sim-btn wide", onclick: fn }, label));
}
