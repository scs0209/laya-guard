// One AI scenario session in the page: it starts runs, reads their events, keeps the run's state for the panel, and plays each
// event in the 3D scene, one after another, as the events arrive. The scene does exactly what the events say: the agent walks to
// the repository for a file action, a command leaves the terminal and is judged by the real evaluator (the packet waits at the
// engine for the real answer), a blocked command drops the barrier. Nothing in here decides anything: the server's run does.

import { initialAiState, reduceAiEvent, isTerminal, pickAgent } from "./ai-reduce.js";
import { createSequencer } from "./ai-sequencer.js";
import { createAiRunner } from "./ai-runner.js";
import { createAiPanel } from "./ai-panel.js";

function delay(ms, ctx) { return new Promise(function (res) { setTimeout(res, ctx.app.reduced ? 0 : ms); }); }

/** ctx: { app, bridge, panelRoot, stage(), world(), beginLive(scn), liveEvent(live, e), endLive(live, scn, result), flightDone(live),
 *         selectRun(id), selectFile(path), clearAiRuns(), showTab(name), renderAll() } */
export function createAiSession(ctx) {
  var app = ctx.app, runner = createAiRunner(ctx.bridge);
  app.ai = initialAiState();
  app.aiUi = { status: null, recent: [], notice: null, agent: null };     // agent: the id the user picked, if they did
  var live = null, scnOfStep = {}, runOfStep = {}, current = 0;

  var seq = createSequencer(play, function (e) { return e.type === "step.end"; });

  // ---- playing an event in the scene -----------------------------------------------------------------------------------
  function play(e) {
    var w = ctx.world(), stage = ctx.stage();
    switch (e.type) {
      case "run.started":
        ctx.clearAiRuns();
        if (w) { w.reset(); w.setVisible(true); }
        if (stage) { stage.refreshLabels(); }
        ctx.renderAll();
        return delay(250, ctx);
      case "world.snapshot":
        if (stage) stage.focusPoint(stage.world.stations.repo.focus.clone().setX(-15.2).setZ(8.6), 1.4, 3.6);
        return w ? w.setFiles(e.files) : delay(150, ctx);
      case "ai.action": {
        var a = e.action;
        if (w && stage) {
          if (a.type === "message" || a.type === "finish") { w.say("The AI agent", a.type === "message" ? a.text : a.outcome || "done"); stage.refreshLabels(); }
          else { w.say("The AI agent", a.type === "run_command" ? "proposes: " + a.command : a.type.replace("_", " ") + (a.path ? " " + a.path : "")); stage.refreshLabels(); }
        }
        if (a.type === "run_command") {
          return (w ? w.moveAgent("terminal") : Promise.resolve()).then(function () {
            var scn = { id: "ai-" + e.step, title: "AI step " + e.step + ": " + a.command.slice(0, 40), command: a.command, context: a.why || "", relevant: [], notes: [], package_scripts: null, ai: true, why: a.why || "" };
            scnOfStep[e.step] = scn;
            live = ctx.beginLive(scn);
            runOfStep[e.step] = live.run.id;
          });
        }
        if (a.type === "read_file" || a.type === "write_file" || a.type === "list_dir") return w ? w.moveAgent("repo") : delay(150, ctx);
        return w ? w.moveAgent("terminal") : delay(150, ctx);
      }
      case "world.read": return w ? w.touch(e.path, "read") : delay(150, ctx);
      case "world.write": return w ? w.touch(e.path, e.created ? "create" : "write").then(function () { applyFlags(); }) : delay(150, ctx);
      case "world.list": return w ? w.pulseAll() : delay(150, ctx);
      case "guard.event":
        if (live) ctx.liveEvent(live, e);
        return undefined;
      case "guard.result": {
        if (!live) return undefined;
        var l = live;
        ctx.endLive(l, scnOfStep[e.step] || l.scn, e.result);
        applyFlags();
        return ctx.flightDone(l).then(function () { if (live === l) live = null; });
      }
      case "command.outcome":
        if (e.state === "blocked" || e.state === "denied") return w ? w.block() : delay(300, ctx);
        if (stage) stage.world.pulse("execution", "allow", 1400);
        return delay(700, ctx);
      case "review.wait":
        if (stage) stage.world.pulse("review", "review", 1600);
        return delay(300, ctx);
      case "review.decision":
        if (stage) stage.world.pulse(e.decision === "approve" ? "execution" : "incident", e.decision === "approve" ? "allow" : "block", 1200);
        return delay(500, ctx);
      case "agent.message": return delay(900, ctx);
      case "run.finished": case "run.stopped": case "run.error":
        live = null;
        if (stage) { stage.rig.follow(null); stage.rig.reset(); }          // the story is told: show the whole scene again
        return w ? w.moveAgent("terminal") : undefined;
      default:
        return undefined;
    }
  }

  function applyFlags() {
    var w = ctx.world();
    if (!w) return;
    Object.keys(app.ai.files).forEach(function (p) { if (w.has(p)) w.flag(p, !!app.ai.files[p].flagged); });
  }

  // ---- events from the server ------------------------------------------------------------------------------------------
  var relayTimer = null;
  function stopRelayPoll() { if (relayTimer) { clearInterval(relayTimer); relayTimer = null; } }
  function ensureRelayPoll() {
    if (relayTimer) return;
    relayTimer = setInterval(function () {
      var liveStatuses = ["interpreting", "running", "paused", "waiting-review"];
      if (liveStatuses.indexOf(app.ai.status) < 0 || app.ai.relay) return;
      var agent = currentAgent();
      if (!agent || agent.id !== "manual-relay") return;
      runner.relayPending().then(function (j) {
        if (app.ai.relay || !j.pending || !j.pending.length) return;
        var p = j.pending[0];
        onEvent({ type: "relay.wait", seq: 0, t: new Date().toISOString(), phase: "act", step: app.ai.step || 0, attempt: 1, decision_id: p.decision_id, deadline_s: 120 });
      }).catch(function () {});
    }, 2500);
  }
  function onEvent(e) {
    reduceAiEvent(app.ai, e);
    seq.push(e);
    if (isTerminal(e.type)) { refreshRecent(); stopRelayPoll(); }
    ctx.renderAll();
  }
  function onProblem(err) {
    if (app.ai.status !== "finished" && app.ai.status !== "stopped" && app.ai.status !== "error") {
      app.ai.status = "error";
      app.ai.error = { kind: "connection", message: "Lost the connection to laya-guard (" + (err && err.message ? err.message : "no answer") + "). The run may still be going on the server." };
    }
    ctx.renderAll();
  }
  function refreshRecent() {
    runner.recent().then(function (j) { app.aiUi.recent = j.runs; ctx.renderAll(); }).catch(function () {});
  }
  function refreshStatus() {
    return runner.status().then(function (s) { app.aiUi.status = s; ctx.renderAll(); }).catch(function (err) {
      app.aiUi.notice = "Couldn't reach laya-guard to look for an agent: " + err.message; ctx.renderAll();
    });
  }

  // ---- what the panel can ask for --------------------------------------------------------------------------------------
  function begin(replayId, opts) {
    runner.detach(); seq.clear(); seq.resume(); live = null; scnOfStep = {}; runOfStep = {};
    app.aiUi.notice = null;
    app.ai = initialAiState();
    app.ai.status = "connecting";
    if (opts) app.ai.prompt = opts.prompt;
    ctx.renderAll();
  }
  var actions = {
    formChanged: function () { ctx.renderAll(); },
    example: function (text) { panel.setPrompt(text); },
    edit: function () {},
    chooseAgent: function (id) { app.aiUi.agent = id; ctx.renderAll(); },
    consent: function (allow) {
      var agent = currentAgent();
      if (!agent) return;
      runner.consent(agent.id, allow).then(refreshStatus).catch(function (err) { app.aiUi.notice = err.message; ctx.renderAll(); });
    },
    start: function (form) {
      var agent = currentAgent();
      if (!agent || !form.prompt) return;
      begin(null, form);
      runner.start(Object.assign({ agent: agent.id }, form), onEvent, onProblem).then(function (id) { app.ai.id = app.ai.id || id; ctx.showTab("ai"); ensureRelayPoll(); })
        .catch(function (err) { app.ai.status = "idle"; app.aiUi.notice = err.message; ctx.renderAll(); });
    },
    pause: function () { control("pause"); seq.pause(); },
    resume: function () { control("resume"); seq.resume(); },
    step: function () {
      if (seq.paused && seq.length > 0) { seq.stepOnce(); return; }     // held, with events here that haven't been played: play the next step of those
      control("step"); seq.resume();                                    // otherwise let the server take one more decision
    },
    stop: function () { control("stop"); seq.clear(); seq.resume(); },
    review: function (decision) { control("review", { decision: decision, step: app.ai.review ? app.ai.review.step : undefined }); },
    relayAnswer: function (decisionId, text) {
      runner.relayAnswer(decisionId, text).then(function () { app.aiUi.notice = null; ctx.renderAll(); })
        .catch(function (err) { app.aiUi.notice = err.message; ctx.renderAll(); });
    },
    replay: function (id) {
      begin(id);
      runner.replay(id, onEvent, onProblem);
    },
    selectRow: function (row) {
      if (row.kind === "guard" && runOfStep[row.step]) ctx.selectRun(runOfStep[row.step]);
      else if (row.path) ctx.selectFile(row.path);
      else if (row.kind === "world" && row.text) { var p = row.text.split(" ")[0]; if (app.ai.files[p]) ctx.selectFile(p); }
    },
  };
  function currentAgent() { return app.aiUi.status ? pickAgent(app.aiUi.status.agents, app.aiUi.agent) : null; }
  function control(action, extra) {
    if (!app.ai.id) return;
    runner.control(app.ai.id, action, extra).catch(function (err) { app.aiUi.notice = err.message; ctx.renderAll(); });
  }

  var panel = createAiPanel(ctx.panelRoot, actions);

  return {
    panel: panel,
    update: function () { panel.update(app); },
    /** The AI tab was opened: look for an agent and earlier runs. */
    activate: function () { if (!app.aiUi.status) refreshStatus(); refreshRecent(); },
    dispose: function () { stopRelayPoll(); runner.detach(); seq.dispose(); },
    sequencer: seq,
  };
}
