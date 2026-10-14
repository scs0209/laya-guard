// The simulation view. `mount(root, bridge)` builds the page, loads the pipeline's config from the server, starts the
// 3D stage (or the text fallback when WebGL isn't there) and wires everything to the dashboard through `bridge`.
//
// What is drawn comes from three places and is always labelled as such: calls read from the verdict log (RECORDED),
// scenario runs judged by this machine's evaluator (REAL, or MOCK EVALUATOR when the server has no model), and, only
// when asked for or when the server doesn't answer, the typical outcome of a scenario (SIMULATION DATA).
//
// bridge: { token, windowText(), onStats(fn), openCall(row) -> bool, openCalls(filter), openPolicies() }

import { el, clear, icon, setText } from "./dom.js";
import { buildTimeline, fromRoute, holdTime, Player } from "./replay.js";
import { ZONE_STATIONS, badge } from "./pipeline.js";
import { recordedRun, scenarioRun, newRecorded, runAction } from "./runs.js";
import { createApi } from "./api.js";
import { createKpis, createControls, createStrip, createEvents } from "./ui-bars.js";
import { createLab } from "./ui-lab.js";
import { createInspector } from "./ui-inspector.js";
import { createTour, tourSeen } from "./ui-tour.js";
import { createFallback } from "./fallback.js";

var MAX_RECORDED_RUNS = 100, MAX_SCENARIO_RUNS = 40;
var MAX_ANIMATED_PER_SYNC = 6;
var POLICY_MATCH = { hit: 1, script: 1, floor: 1, limit: 1 };
var POLICY_STATE = { hit: "block", script: "block", limit: "block", floor: "review", safe: "allow", clear: "active", offline: "review" };

export async function mount(root, bridge) {
  var api = createApi(bridge);
  var mq = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : { matches: false, addEventListener: function () {} };
  var app = {
    config: null, stats: null, windowText: "", runs: [], byId: {}, focused: null, player: null, selection: null,
    scenarioId: null, mode: "evaluator", busy: false, lastError: null, inFlight: 0, waiting: false, reduced: mq.matches,
    paused: false, evalMock: false, zoneCounts: [0, 0, 0], webgl: true, active: false, synced: false, window: null,
  };
  var flights = new Map(), stage = null, ui = {}, raf = 0, last = 0, liveSeq = 0;

  // ---- the page ------------------------------------------------------------------------------------------------
  root.classList.add("sim");
  clear(root);
  var chipData = el("span", { "class": "chip plain", "data-chip": "data" }), chipEval = el("span", { "class": "chip plain", "data-chip": "eval" }),
      chipSvc = el("span", { "class": "chip plain", "data-chip": "service" });
  var btnPause = el("button", { type: "button", "class": "sim-btn wide", "aria-pressed": "false", onclick: function () { actions.togglePause(); } }, "Pause simulation");
  var btnTour = el("button", { type: "button", "class": "sim-btn wide", onclick: function () { actions.tour(); } }, icon("tour"), "Tour");
  var btnReset = el("button", { type: "button", "class": "sim-btn wide", onclick: function () { actions.resetView(); } }, icon("home"), "Reset view");
  var bar = el("div", { "class": "sim-bar" }, el("div", { "class": "sim-chips", role: "status", "aria-label": "Mode" }, chipData, chipEval, chipSvc),
    el("div", { "class": "sim-actions" }, btnPause, btnTour, btnReset));
  var kpisEl = el("div", { "class": "sim-kpis", role: "group", "aria-label": "Summary of recorded calls" });

  var canvas = el("canvas", { "class": "sim-canvas", tabindex: "0", role: "application",
    "aria-label": "Interactive 3D view of the guardrail pipeline. Everything in it is also available through the station buttons under the view, the Attack lab and the Inspector." });
  var labelsEl = el("div", { "class": "sim-labels", "aria-hidden": "true" });
  var legend = el("ul", { "class": "sim-legend", "aria-label": "Key" },
    el("li", null, el("i", { "class": "k cy" }), "in transit"), el("li", null, el("i", { "class": "k ok" }), "allow"),
    el("li", null, el("i", { "class": "k warn" }), "review"), el("li", null, el("i", { "class": "k bad" }), "block"),
    el("li", null, el("i", { "class": "k ring" }), "scenario run"));
  function camBtn(label, ic, fn) { return el("button", { type: "button", "class": "sim-btn", "aria-label": label, title: label, onclick: fn }, icon(ic)); }
  var cam = el("div", { "class": "sim-cam", role: "group", "aria-label": "Camera" },
    camBtn("Zoom in", "plus", function () { stage && stage.rig.zoomBy(1.25); }), camBtn("Zoom out", "minus", function () { stage && stage.rig.zoomBy(0.8); }),
    camBtn("Rotate left", "rotl", function () { stage && stage.rig.rotateBy(-20); }), camBtn("Rotate right", "rotr", function () { stage && stage.rig.rotateBy(20); }));
  var tourBox = el("div", { "class": "sim-tour", role: "dialog", "aria-label": "Guided tour" });
  var msgEl = el("div", { "class": "sim-msg", role: "status" }, "Loading the pipeline…");
  var wrap = el("div", { "class": "sim-wrap" }, canvas, labelsEl, legend, cam, msgEl);
  var fbEl = el("div", { "class": "sim-fallback", hidden: true });
  var labPanel = el("div", { "class": "sim-panel", id: "sim-panel-lab", role: "tabpanel" });
  var inspPanel = el("div", { "class": "sim-panel", id: "sim-panel-inspector", role: "tabpanel", hidden: true });
  var tabLab = el("button", { type: "button", role: "tab", "aria-selected": "true", "aria-controls": "sim-panel-lab", onclick: function () { showTab("lab"); } }, icon("flask"), "Attack lab");
  var tabInsp = el("button", { type: "button", role: "tab", "aria-selected": "false", "aria-controls": "sim-panel-inspector", onclick: function () { showTab("inspector"); } }, "Inspector");
  var side = el("div", { "class": "sim-side" }, el("div", { "class": "sim-tabs", role: "tablist" }, tabLab, tabInsp), labPanel, inspPanel);
  var main = el("div", { "class": "sim-main" }, el("div", { "class": "sim-view" }, wrap, fbEl, tourBox), side);
  var controlsEl = el("div", { "class": "sim-controls" });
  var stripEl = el("ol", { "class": "sim-strip", "aria-label": "Pipeline stations" });
  var eventsEl = el("section", { "class": "sim-events", "aria-label": "Event timeline" }, el("h3", { text: "Events" }));
  root.appendChild(bar); root.appendChild(kpisEl); root.appendChild(main); root.appendChild(controlsEl); root.appendChild(stripEl); root.appendChild(eventsEl);

  function showTab(name) {
    var lab = name === "lab";
    tabLab.setAttribute("aria-selected", String(lab)); tabInsp.setAttribute("aria-selected", String(!lab));
    labPanel.hidden = !lab; inspPanel.hidden = lab;
  }

  // ---- config ---------------------------------------------------------------------------------------------------
  try {
    app.config = await api.config();
  } catch (e) {
    // the view can't start without its config; "Try again" builds it for real and the stub hands over to it
    var wanted = false, real = null;
    setText(msgEl, "The guard service didn't answer, so the pipeline can't be loaded (" + e.message + ").");
    msgEl.appendChild(el("button", { type: "button", "class": "sim-run", onclick: function () {
      mount(root, bridge).then(function (api) { real = api; api.activate(wanted); });
    } }, "Try again"));
    return { activate: function (on) { wanted = !!on; if (real) real.activate(on); }, inspect: null };
  }
  app.scenarioId = app.config.scenarios[0].id;
  app.evalMock = !!app.config.evaluator.mock;

  // ---- actions (everything the panels and the scene can ask for) ---------------------------------------------------
  var actions = {
    selectStation: function (id) {
      app.selection = { kind: "station", id: id };
      stage && stage.focusStation(id);
      showTab("inspector"); renderAll();
    },
    selectPolicy: function (id) {
      app.selection = { kind: "policy", id: id };
      if (stage) { stage.focusPolicy(id); stage.world.clearPolicies(); stage.world.lightPolicy(id, true); }
      showTab("inspector"); renderAll();
    },
    selectRun: function (id) { selectRun(id, true); },
    replayRun: function (id) { selectRun(id, false); },
    clearSelection: function () {
      app.selection = null;
      stage && stage.clearSelection();
      renderAll();
    },
    setScenario: function (id) { app.scenarioId = id; app.lastError = null; renderAll(); },
    setMode: function (m) { app.mode = m; renderAll(); },
    runScenario: runScenario,
    togglePlay: function () { var f = judgedFlight(); if (!f) return; if (f.player.ended) restartFlight(f); else f.player.toggle(); renderFast(); },
    restart: function () { var f = judgedFlight(); if (f) restartFlight(f); },
    stepForward: function () { var f = judgedFlight(); if (f) f.player.stepForward(); },
    stepBack: function () { var f = judgedFlight(); if (f) f.player.stepBack(); },
    seek: function (frac) { var f = judgedFlight(); if (f) { f.player.pause(); f.player.seek(frac * f.player.tl.total); } },
    togglePause: function () {
      app.paused = !app.paused;
      stage && stage.setPaused(app.paused);
      btnPause.setAttribute("aria-pressed", String(app.paused));
      setText(btnPause, app.paused ? "Resume simulation" : "Pause simulation");
      renderAll();
    },
    tour: function () { ui.tour.start(); },
    resetView: function () { stage && stage.rig.reset(); },
    openInCalls: function (run) { return bridge.openCall(run.row); },
    openCalls: function (filter) { bridge.openCalls(filter); },
    openPolicies: function () { bridge.openPolicies(); },
  };

  ui.kpis = createKpis(kpisEl);
  ui.controls = createControls(controlsEl, actions);
  ui.strip = createStrip(stripEl, actions);
  ui.events = createEvents(eventsEl, actions);
  ui.lab = createLab(labPanel, actions);
  ui.insp = createInspector(inspPanel, actions);
  ui.tour = createTour(tourBox, actions);

  // ---- the stage (3D) or the text fallback ---------------------------------------------------------------------------
  try {
    var gl = await import("./stage.js");
    if (!gl.webglAvailable()) throw new Error("WebGL isn't available in this browser");
    stage = gl.createStage(canvas, labelsEl, app.config, { onPick: onPick, beforeFrame: tick, onLost: function () { useFallback("the WebGL context was lost"); } });
    stage.setReducedMotion(app.reduced);
    msgEl.hidden = true;
    root.dataset.simMode = "webgl";
  } catch (e) {
    useFallback(e && e.message ? e.message : String(e));
  }
  function useFallback(reason) {
    if (stage) { stage.dispose(); stage = null; }
    app.webgl = false;
    wrap.hidden = true; fbEl.hidden = false; clear(fbEl);
    ui.fallback = createFallback(fbEl, actions, reason);
    root.dataset.simMode = "fallback";
    startFallbackLoop();
    renderAll();
  }
  function startFallbackLoop() {
    if (raf) return;
    last = performance.now();
    var loop = function (now) {
      if (!app.active || app.webgl) { raf = 0; return; }
      raf = requestAnimationFrame(loop);
      var dt = Math.max(0, Math.min(50, now - last));
      last = now;
      tick(app.paused ? 0 : dt);
    };
    raf = requestAnimationFrame(loop);
  }

  // ---- runs and flights -----------------------------------------------------------------------------------------------
  function zoneOf(run) { return ZONE_STATIONS[runAction(run)]; }
  function addRun(run) {
    app.runs.unshift(run); app.byId[run.id] = run;
    var limit = run.kind === "scenario" ? MAX_SCENARIO_RUNS : MAX_RECORDED_RUNS, same = 0;
    for (var i = 0; i < app.runs.length; i++) {
      if (app.runs[i].kind !== run.kind || ++same <= limit) continue;
      var old = app.runs.splice(i--, 1)[0];       // the oldest of its own kind goes
      delete app.byId[old.id]; flights.delete(old.id);
    }
  }
  function timelineOf(run) { return buildTimeline(run.steps, { reduced: app.reduced }); }
  function makeFlight(run, timeline) {
    var f = flights.get(run.id);
    if (!f) {
      f = { run: run, player: new Player(timeline || timelineOf(run)), settled: false, lastI: -1, dirty: true, live: false, lit: [], beacon: null };
      f.player.onChange(function () { f.dirty = true; });
      flights.set(run.id, f);
    }
    return f;
  }
  function restartFlight(f) {
    clearEffects(f);
    f.settled = false; f.lastI = -1; f.live = false;
    f.player.release();
    f.player.setTimeline(timelineOf(f.run));
    f.player.restart();
  }
  function focusedFlight() { return app.focused ? flights.get(app.focused.id) : null; }
  /** The focused run's flight, unless it is still waiting for the evaluator (there is nothing to replay yet, and
   *  restarting it would draw a path that isn't known). */
  function judgedFlight() { var f = focusedFlight(); return f && f.run.source !== "pending" ? f : null; }
  function selectRun(id, openInspector) {
    var run = app.byId[id];
    if (!run) return;
    app.selection = { kind: "run", id: id };
    app.focused = run;
    var f = makeFlight(run);
    if (run.source !== "pending") restartFlight(f);
    app.player = f.player;
    if (stage) { stage.packets.place(run, f.player.pose(), app.reduced); stage.focusRun(id); }
    if (openInspector) showTab("inspector");
    renderAll();
  }

  // ---- what the scene does as calls move ----------------------------------------------------------------------------
  function matchedPolicies(run) {
    var ids = {}, steps = {};
    (run.steps || []).forEach(function (s) { steps[s.stage] = s; });
    if (run.result) {
      run.result.policy_hits.forEach(function (h) { if (h.result === "hit-block" || h.result === "hit-review") ids[h.policy_id] = 1; });
      run.result.segments.forEach(function (s) { if (s.script && s.script.policy_id) ids[s.script.policy_id] = 1; });
    }
    var pid = run.verdict && run.verdict.policy_id;
    if (pid && steps.policy && POLICY_MATCH[steps.policy.status]) ids[pid] = 1;
    return Object.keys(ids);
  }
  function clearEffects(f) {
    if (!stage) return;
    f.lit.forEach(function (id) { if (!(app.selection && app.selection.kind === "policy" && app.selection.id === id)) stage.world.lightPolicy(id, false); });
    f.lit = [];
    if (f.beacon) { stage.world.lightBeacon(f.beacon, false); f.beacon = null; }
  }
  function enterFrame(f, frame) {
    if (!stage) return;
    var w = stage.world, run = f.run, action = runAction(run);
    if (f.beacon && frame.stage !== "decision") { w.lightBeacon(f.beacon, false); f.beacon = null; }
    if (frame.stage !== "policy" && f.lit.length) clearEffects(f);
    switch (frame.stage) {
      case "terminal": w.pulse("terminal:" + run.terminal, "active", 700); break;
      case "queue": w.pulse("queue", "active", 700); break;
      case "policy":
        w.pulse("policy", POLICY_STATE[frame.status] || "active", 1000);
        matchedPolicies(run).forEach(function (id) { w.lightPolicy(id, true); f.lit.push(id); });
        break;
      case "engine": if (frame.status === "failed") w.pulse("engine", "fail", 1100); break;
      case "decision": if (action) { w.pulse("decision", action, 900); w.lightBeacon(action, true); f.beacon = action; } break;
      case "zone": if (action) w.pulse(frame.station, action, 1200); break;
    }
  }

  function tick(dt) {
    var inFlight = 0, atEngine = 0, waiting = false;
    flights.forEach(function (f) {
      var moved = f.player.tick(dt), pose = f.player.pose();
      var ended = pose.ended && !f.live;              // a live run's timeline ends where it waits for its result
      if (f.live && f.player.holdAt != null && f.player.t >= f.player.holdAt - 1) waiting = true;
      if (!ended) inFlight += 1;
      if (pose.phase === "dwell" && pose.frame.station === "engine" && !ended && f.run.source !== "simulation") atEngine += 1;
      if (!(moved || f.dirty) || !stage) { f.dirty = false; return; }
      f.dirty = false;
      if (ended) {
        if (!f.settled && f.run.verdict) {
          stage.packets.settle(f.run, zoneOf(f.run)); f.settled = true; clearEffects(f); enterFrame(f, pose.frame);
          if (f.run.kind === "recorded" && app.focused !== f.run) flights.delete(f.run.id);
        }
        return;
      }
      f.settled = false;
      stage.packets.place(f.run, pose, app.reduced);
      if (pose.phase !== "travel" && pose.i !== f.lastI) { f.lastI = pose.i; enterFrame(f, pose.frame); }
    });
    app.inFlight = inFlight; app.waiting = waiting;
    if (stage) {
      stage.world.setEngineMode(atEngine > 0 ? "active" : app.evalMock ? "offline" : "idle");
      stage.world.setQueueDepth(Math.min(6, inFlight));
      app.zoneCounts = [stage.packets.countIn("execution"), stage.packets.countIn("review"), stage.packets.countIn("incident")];
    } else {
      app.zoneCounts = zoneCountsFromRuns();
    }
    renderFast();
  }

  /** Without the 3D scene: how many of the latest calls rest in each zone (the scene keeps the latest 24 per zone). */
  function zoneCountsFromRuns() {
    var n = { allow: 0, review: 0, block: 0 };
    app.runs.forEach(function (r) { var a = runAction(r); if (a && n[a] < 24 && r.source !== "pending") n[a] += 1; });
    return [n.allow, n.review, n.block];
  }

  // ---- the attack lab -------------------------------------------------------------------------------------------------
  function scenarioById(id) { return app.config.scenarios.find(function (s) { return s.id === id; }); }

  function finishSimulation(scn) {
    var run = scenarioRun(app.config, scn, null);
    addRun(run);
    selectRun(run.id, false);
    showTab("lab");
  }

  async function runScenario() {
    if (app.busy) return;
    var scn = scenarioById(app.scenarioId);
    app.lastError = null;
    if (app.mode === "simulation") { finishSimulation(scn); return; }
    app.busy = true;
    var run = { id: "scn|" + scn.id + "|live" + (++liveSeq), kind: "scenario", source: "pending", scenario: scn, terminal: 2, label: scn.title,
                ts: new Date().toISOString(), steps: [], verdict: null, latency_ms: null, result: null };
    var prefix = fromRoute(["terminal", "queue", "policy"], [], { reduced: app.reduced });
    var f = makeFlight(run, prefix);
    f.live = true;
    f.player.hold(holdTime(prefix));
    f.player.restart();
    app.byId[run.id] = run;
    app.focused = run; app.player = f.player; app.selection = { kind: "run", id: run.id };
    if (stage) { stage.packets.place(run, f.player.pose(), app.reduced); stage.focusRun(run.id); }
    renderAll();
    var result;
    try {
      result = await api.evaluate(scn.id, function (e) {
        if (e.stage === "engine" && e.status === "running") {         // the model really is running now: the call goes to the engine and waits there
          var tl = fromRoute(["terminal", "queue", "policy", "engine"], [], { reduced: app.reduced });
          f.player.setTimeline(tl);
          f.player.hold(holdTime(tl));
        }
      });
    } catch (err) {
      flights.delete(run.id); delete app.byId[run.id];
      if (stage) stage.packets.forget(run);
      app.busy = false;
      app.lastError = "The guard service didn't answer (" + err.message + "), so this is simulation data, not an evaluation.";
      finishSimulation(scn);
      return;
    }
    var final = scenarioRun(app.config, scn, result);
    final.id = run.id;
    Object.assign(run, final);
    run.source = result.mode;
    addRun(run);
    f.live = false;
    f.player.setTimeline(timelineOf(run));
    f.player.release();
    app.busy = false;
    renderAll();
  }

  // ---- data from the dashboard (the verdict log) ------------------------------------------------------------------------
  function resetRecorded() {
    app.runs = app.runs.filter(function (r) { if (r.kind === "recorded") { delete app.byId[r.id]; flights.delete(r.id); return false; } return true; });
    if (stage) stage.packets.clearRecorded();
    app.synced = false;
    if (app.focused && app.focused.kind === "recorded") { app.focused = null; app.player = null; app.selection = null; }
  }
  var seen = {};
  function onStats(data) {
    app.stats = data;
    app.windowText = bridge.windowText();
    if (!data) { renderAll(); return; }
    app.evalMock = data.health ? !!data.health.mock : app.evalMock;
    if (data.window !== app.window) { app.window = data.window; seen = {}; resetRecorded(); }
    var fresh = newRecorded(data.recent, seen);
    if (!app.synced) {
      // the first look: the latest calls rest in their zones, nothing flies, and nothing pretends to be live
      fresh.slice(-24).forEach(function (row) { var run = recordedRun(app.config, row); addRun(run); if (stage) stage.packets.rest(run, zoneOf(run)); });
      fresh.forEach(function (row) { seen["rec|" + row.ts + "|" + row.tool_sha + "|" + row.action] = true; });
      app.synced = true;
    } else {
      var animate = app.active && !app.paused ? MAX_ANIMATED_PER_SYNC : 0;
      fresh.forEach(function (row, i) {
        var run = recordedRun(app.config, row);
        seen[run.id] = true;
        addRun(run);
        if (stage && i >= fresh.length - animate) makeFlight(run).player.play();
        else if (stage) stage.packets.rest(run, zoneOf(run));
      });
    }
    renderAll();
  }

  function onPick(pick) {
    if (!pick) { actions.clearSelection(); return; }
    if (pick.kind === "station") actions.selectStation(pick.id);
    else if (pick.kind === "policy") actions.selectPolicy(pick.id);
    else if (pick.kind === "run") actions.selectRun(pick.id);
  }

  // ---- drawing the panels ---------------------------------------------------------------------------------------------
  function setChip(node, text, tone, title) { node.className = "chip " + tone; setText(node, text); if (title) node.title = title; }
  function renderAll() {
    var f = app.focused, b = f ? badge(f.source === "pending" ? "real" : f.source) : null;
    if (f && f.source === "pending") setChip(chipData, "JUDGING…", "plain", "Waiting for the evaluator's answer.");
    else if (b) setChip(chipData, "Showing: " + b.label, b.tone, b.detail);
    else setChip(chipData, "Live: RECORDED CALLS", "plain", "Calls are drawn as they are read from the local verdict log.");
    if (!app.stats) setChip(chipEval, "Evaluator: unknown", "plain");
    else if (app.evalMock) setChip(chipEval, "Mock mode · no model", "warn", "The model isn't loaded: regexes and heuristics stand in.");
    else setChip(chipEval, "Local Laya model", "ok", "Judgments run on this machine.");
    if (app.stats) setChip(chipSvc, "Guard service · running" + (app.stats.health && app.stats.health.version && app.stats.health.version !== "unknown" ? " v" + app.stats.health.version : ""), "ok");
    else setChip(chipSvc, "Guard service · unreachable", "bad", "Scenario runs fall back to simulation data.");
    ui.kpis.update(app); ui.lab.update(app); ui.insp.update(app); ui.events.update(app);
    renderFast();
  }
  function renderFast() {
    ui.kpis.update(app); ui.controls.update(app); ui.strip.update(app);
    if (ui.fallback) ui.fallback.update(app);
  }

  // ---- lifecycle -------------------------------------------------------------------------------------------------------
  mq.addEventListener && mq.addEventListener("change", function (e) { app.reduced = e.matches; stage && stage.setReducedMotion(e.matches); });
  function sync() {
    if (stage) { if (app.active && !document.hidden) stage.start(); else stage.stop(); }
    else if (app.active && !document.hidden) startFallbackLoop();
  }
  document.addEventListener("visibilitychange", sync);
  bridge.onStats(onStats);
  renderAll();
  root.dataset.simReady = "1";

  return {
    activate: function (on) {
      app.active = !!on;
      sync();
      if (on && !tourSeen() && !ui.tour.active()) ui.tour.start();
    },
    /** For tests and debugging: the view's state and its 3D stage (null without WebGL). */
    inspect: function () { return { app: app, stage: stage, flights: flights }; },
  };
}
