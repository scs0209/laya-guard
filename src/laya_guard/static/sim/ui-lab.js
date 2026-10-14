// The attack lab: pick a scenario, run it through the evaluator (or draw its typical outcome as simulation data),
// read what came back, and compare scenarios. It shows what the evaluator returned and nothing it didn't.

import { el, clear, fmtMs } from "./dom.js";
import { badge } from "./pipeline.js";
import { ACTION_WORD, VIA_WORD, STAGE_NAME, toneFor, statusWord } from "./vocab.js";
import { compareRows, runAction } from "./runs.js";

function chip(text, tone) { return el("span", { "class": "chip " + tone, text: text }); }
function tone(action) { return action === "allow" ? "ok" : action === "review" ? "warn" : "bad"; }

/** Who made the decision, in words, from the steps and how the verdict was made. */
export function decidedBy(run) {
  if (run.source === "simulation") return "Typically: " + decidedByFacts(run).replace(/^./, function (c) { return c.toLowerCase(); }) + " (illustration, nothing was evaluated)";
  return decidedByFacts(run);
}
function decidedByFacts(run) {
  var by = {};
  (run.steps || []).forEach(function (s) { by[s.stage] = s; });
  var via = run.verdict && run.verdict.verdict_source, policy = by.policy && by.policy.status, engine = by.engine && by.engine.status;
  if (policy === "hit" || policy === "script" || policy === "limit") return "Rules: the policy gate decided, the model wasn't needed";
  if (policy === "safe") return "The safe-command list: allowed without the model";
  if (policy === "offline") return "The hook's own offline check (the server wasn't reached)";
  if (engine === "ran") return policy === "floor" ? "The Laya model, with a review floor from a matching pattern" : "The Laya model";
  if (engine === "failed") return "Heuristics: the model was called and failed";
  if (engine === "fallback") return "Heuristics: the model isn't in use (mock mode)";
  return via ? VIA_WORD[via] || via : "not recorded";
}

export function createLab(root, actions) {
  var sig = "";
  return {
    update: function (app) {
      var scn = app.config.scenarios.find(function (s) { return s.id === app.scenarioId; }) || app.config.scenarios[0];
      var rows = compareRows(app.runs, app.config.scenarios);
      var mine = rows.find(function (r) { return r.scenario.id === scn.id; });
      var s2 = [scn.id, app.mode, app.busy, app.lastError, mine && mine.run.id, rows.map(function (r) { return r.run.id; }).join(","),
                app.evalMock, app.focused && app.focused.id].join("|");
      if (s2 === sig) return;
      sig = s2;
      clear(root);

      root.appendChild(el("p", { "class": "lab-intro", text: "Scenarios are judged by the evaluator and never run. A scenario run isn't written to the verdict log and doesn't count as a call." }));

      // how a run is made
      var real = el("button", { type: "button", "aria-pressed": String(app.mode === "evaluator"), onclick: function () { actions.setMode("evaluator"); } }, "Real evaluator");
      var sim = el("button", { type: "button", "aria-pressed": String(app.mode === "simulation"), onclick: function () { actions.setMode("simulation"); } }, "Simulation data");
      root.appendChild(el("div", { "class": "seg lab-mode", role: "group", "aria-label": "How a scenario runs" }, real, sim));
      root.appendChild(el("p", { "class": "lab-modenote", text: app.mode === "simulation"
        ? "Nothing is evaluated. The scene shows a typical outcome and labels it SIMULATION DATA."
        : app.evalMock ? "The server runs in mock mode: the rules are real, the model is replaced by heuristics, and results say so."
          : "Uses this machine's evaluator: the policy rules and the local Laya model." }));

      // the scenarios
      var list = el("div", { "class": "scn-list", role: "radiogroup", "aria-label": "Scenario" });
      app.config.scenarios.forEach(function (s) {
        list.appendChild(el("button", { type: "button", role: "radio", "aria-checked": String(s.id === scn.id), "class": "scn" + (s.id === scn.id ? " sel" : ""),
          onclick: function () { actions.setScenario(s.id); } }, s.title));
      });
      root.appendChild(list);

      // the chosen one
      var detail = el("div", { "class": "scn-detail" },
        el("code", { "class": "scn-cmd", text: scn.command }),
        el("p", { text: scn.context }));
      if (scn.package_scripts) detail.appendChild(el("p", { "class": "muted", text: "scripts.test of the throwaway package.json: " + scn.package_scripts.test }));
      scn.notes.forEach(function (n) { detail.appendChild(el("p", { "class": "muted", text: n })); });
      var matched = {};
      if (mine && mine.run.result) mine.run.result.relevant.forEach(function (r) { matched[r.policy_id] = r; });
      var rel = el("div", { "class": "scn-rel" }, el("span", { "class": "muted", text: "Relevant checks: " }));
      if (!scn.relevant.length) rel.appendChild(el("span", { "class": "muted", text: "no policy pattern is expected to match" }));
      scn.relevant.forEach(function (id) {
        var m = matched[id], state = !m ? "" : m.decided ? " · matched, decided it" : m.matched ? " · matched, but an earlier rule decided" : " · didn't match";
        rel.appendChild(el("button", { type: "button", "class": "chip pol " + (m && m.matched ? "bad" : "plain"), onclick: function () { actions.selectPolicy(id); } }, id + state));
      });
      detail.appendChild(rel);
      root.appendChild(detail);

      var go = el("button", { type: "button", "class": "sim-run", onclick: function () { actions.runScenario(); } }, app.busy ? "Judging…" : "Run scenario");
      go.disabled = !!app.busy;
      var replay = el("button", { type: "button", "class": "sim-btn wide", onclick: function () { if (mine) actions.selectRun(mine.run.id); } }, "Replay last run");
      replay.disabled = !mine;
      root.appendChild(el("div", { "class": "lab-actions" }, go, replay));
      if (app.lastError) root.appendChild(el("p", { "class": "lab-error", role: "alert", text: app.lastError }));

      // the result of the last run of this scenario
      if (mine) root.appendChild(resultOf(mine.run, scn));

      // comparison
      if (rows.length) {
        var table = el("table", { "class": "scn-compare" }, el("caption", { text: "Latest run of each scenario" }),
          el("thead", null, el("tr", null, ["Scenario", "Verdict", "Decided by", "Risk", "Time", "Source"].map(function (h) { return el("th", { scope: "col", text: h }); }))));
        var body = el("tbody");
        rows.forEach(function (r) {
          var a = runAction(r.run), b = badge(r.run.source);
          body.appendChild(el("tr", { "class": r.scenario.id === scn.id ? "sel" : "" },
            el("td", null, el("button", { type: "button", onclick: function () { actions.setScenario(r.scenario.id); actions.selectRun(r.run.id); } }, r.scenario.title)),
            el("td", null, chip(ACTION_WORD[a] || a, r.run.source === "simulation" ? "sim" : tone(a))),
            el("td", { text: decidedBy(r.run).split(":")[0] }),
            el("td", { text: r.run.verdict.risk != null ? Number(r.run.verdict.risk).toFixed(2) : "–" }),
            el("td", { text: r.run.latency_ms != null ? fmtMs(r.run.latency_ms) : "–" }),
            el("td", null, el("span", { "class": "src " + b.tone, title: b.detail, text: b.label }))));
        });
        table.appendChild(body);
        root.appendChild(el("div", { "class": "scn-compare-wrap" }, table));
      }
    },
  };
}

function resultOf(run, scn) {
  var v = run.verdict, b = badge(run.source), a = runAction(run);
  var box = el("div", { "class": "scn-result" },
    el("div", { "class": "res-head" }, el("span", { "class": "src " + b.tone, text: b.label }), chip(ACTION_WORD[a] || a, run.source === "simulation" ? "sim" : tone(a))),
    el("p", { "class": "muted", text: b.detail }));
  var dl = el("dl", { "class": "facts" });
  function row(k, val, cls) { dl.appendChild(el("dt", { text: k })); dl.appendChild(el("dd", { "class": cls || "", text: val })); }
  row("Decided by", decidedBy(run));
  row("Risk score", v.risk != null ? Number(v.risk).toFixed(2) : "not measured (simulation data)");
  row("Confidence", run.source === "simulation" ? "not measured" : "the evaluator returns none");
  row("Policy", v.policy_id || "–");
  if (run.result) row("Judged as", "a tool call (source=tool), the way most agent hooks send them: stricter thresholds than a typed command");
  row(run.source === "simulation" ? "Note" : "Reason", v.reason || "–");
  row("Judged in", run.latency_ms != null ? fmtMs(run.latency_ms) : "–");
  box.appendChild(dl);
  if (run.source === "simulation") box.appendChild(el("p", { "class": "muted", text: "The path below is a typical one, drawn for illustration: nothing was measured." }));
  var path = el("ol", { "class": "path" });
  run.steps.forEach(function (s) {
    path.appendChild(el("li", null, el("span", { "class": "path-stage", text: STAGE_NAME[s.stage] }), chip(statusWord(s.stage, s.status), toneFor(run, s.stage, s.status))));
  });
  box.appendChild(path);
  return box;
}
