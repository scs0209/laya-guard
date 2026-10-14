// The small, always-visible parts of the view: the KPI cards, the replay controls, the station strip (the
// no-3D-needed way to reach every station) and the event timeline. Each `create…` returns { update(app) }.

import { el, clear, icon, setText, fmtMs, fmtInt, fmtClock } from "./dom.js";
import { badge, STATIONS, stageOfStation } from "./pipeline.js";
import { STATION_NAME, ACTION_WORD, VIA_WORD, toneFor, statusWord } from "./vocab.js";
import { runAction } from "./runs.js";

// --- KPI cards -------------------------------------------------------------------------------------------------

export function createKpis(root) {
  var cards = {};
  function card(id, label, tone) {
    var value = el("b", { "class": "k-value", text: "–" }), sub = el("span", { "class": "k-sub" });
    var node = el("div", { "class": "kpi " + tone, "data-kpi": id }, el("span", { "class": "k-label", text: label }), value, sub);
    root.appendChild(node);
    cards[id] = { value: value, sub: sub };
  }
  card("allow", "Allowed", "ok"); card("review", "Held for review", "warn"); card("block", "Blocked", "bad");
  card("latency", "Judging time", "plain"); card("flight", "In flight", "plain");
  return {
    update: function (app) {
      var t = app.stats && app.stats.totals, src = app.stats ? "recorded · " + app.windowText : "service not reachable";
      ["allow", "review", "block"].forEach(function (k) {
        setText(cards[k].value, t ? fmtInt(t[k]) : "–");
        setText(cards[k].sub, src);
      });
      setText(cards.latency.value, t && t.total ? fmtMs(t.avg_latency_ms) : "–");
      setText(cards.latency.sub, t && t.total ? "average, p95 " + fmtMs(t.p95_latency_ms) + " · recorded" : src);
      setText(cards.flight.value, String(app.inFlight));
      setText(cards.flight.sub, "calls being drawn now");
    },
  };
}

// --- replay controls ---------------------------------------------------------------------------------------------

export function createControls(root, actions) {
  function btn(label, ic, fn) {
    var b = el("button", { type: "button", "class": "sim-btn", "aria-label": label, title: label, onclick: fn }, icon(ic));
    return b;
  }
  var restart = btn("Restart the replay", "restart", function () { actions.restart(); });
  var back = btn("Step back one stage", "back", function () { actions.stepBack(); });
  var play = btn("Play", "play", function () { actions.togglePlay(); });
  var fwd = btn("Step forward one stage", "forward", function () { actions.stepForward(); });
  var scrub = el("input", { type: "range", min: 0, max: 1000, value: 0, step: 1, "class": "sim-scrub", "aria-label": "Replay position" });
  scrub.addEventListener("input", function () { actions.seek(Number(scrub.value) / 1000); });
  var time = el("span", { "class": "sim-time", text: "" });
  var now = el("p", { "class": "sim-now", "aria-live": "polite", text: "Pick a call or run a scenario to replay it." });
  var row = el("div", { "class": "sim-ctl-row" }, restart, back, play, fwd, scrub, time);
  root.appendChild(row); root.appendChild(now);
  var lastText = "", lastPlaying = null;
  return {
    update: function (app) {
      var p = app.player, has = !!p && !(app.focused && app.focused.source === "pending");      // nothing to replay until the answer is in
      [restart, back, play, fwd, scrub].forEach(function (n) { n.disabled = !has; });
      if (!has) { time.textContent = ""; scrub.value = 0; return; }
      var playing = p.playing && !p.ended;
      if (playing !== lastPlaying) {
        lastPlaying = playing;
        var path = play.querySelector("path");
        path.setAttribute("d", playing ? "M8 5v14M16 5v14" : "M7 5v14l11-7z");
        play.setAttribute("aria-label", playing ? "Pause" : p.ended ? "Replay" : "Play");
        play.title = play.getAttribute("aria-label");
      }
      var v = p.tl.total ? Math.round(p.t / p.tl.total * 1000) : 0;
      if (document.activeElement !== scrub || String(v) !== scrub.value) scrub.value = v;
      time.textContent = (p.t / 1000).toFixed(1) + " / " + (p.tl.total / 1000).toFixed(1) + " s";
      var pose = p.pose(), step = pose.frame, text = STATION_NAME[step.station] + (step.text ? ": " + step.text : "");
      if (app.waiting) text = "Waiting for the evaluator's answer…";
      if (text !== lastText) { lastText = text; now.textContent = text; }
    },
  };
}

// --- station strip -----------------------------------------------------------------------------------------------

export function createStrip(root, actions) {
  var items = {};
  STATIONS.forEach(function (st) {
    var chip = el("span", { "class": "chip plain", text: "" }), count = el("span", { "class": "strip-n", text: "" });
    var b = el("button", { type: "button", "class": "strip-btn", "data-station": st, onclick: function () { actions.selectStation(st === "terminal" ? "terminal:0" : st); } },
      el("span", { "class": "strip-name", text: STATION_NAME[st] }), chip, count);
    root.appendChild(el("li", null, b));
    items[st] = { btn: b, chip: chip, count: count };
  });
  var sig = "";
  return {
    update: function (app) {
      var steps = app.focused ? app.focused.steps : null, by = {}, cur = null;
      (steps || []).forEach(function (s) { by[s.stage] = s; });
      if (app.player) cur = app.player.pose().frame.station;
      var s2 = [app.focused && app.focused.id, app.focused && app.focused.source, cur, app.selection && app.selection.id, app.zoneCounts.join(",")].join("|");
      if (s2 === sig) return;
      sig = s2;
      STATIONS.forEach(function (st) {
        var it = items[st], step = by[stageOfStation(st)], tone = "plain", word = "";
        if (step && (st !== "execution" && st !== "review" && st !== "incident" || step.status === st)) {
          tone = toneFor(app.focused, step.stage, step.status); word = statusWord(step.stage, step.status);
        }
        it.chip.className = "chip " + tone; it.chip.textContent = word;
        it.count.textContent = st === "execution" ? "×" + app.zoneCounts[0] : st === "review" ? "×" + app.zoneCounts[1] : st === "incident" ? "×" + app.zoneCounts[2] : "";
        it.btn.classList.toggle("on-path", !!step && (st === "terminal" || st === "queue" || st === "policy" || st === "decision" || (st === "engine" && step.status !== "skipped") || step.status === st));
        if (cur === st) it.btn.setAttribute("aria-current", "step"); else it.btn.removeAttribute("aria-current");
        var picked = app.selection && String(app.selection.id).split(":")[0] === st && app.selection.kind === "station";
        it.btn.classList.toggle("sel", !!picked);
      });
    },
  };
}

// --- event timeline ----------------------------------------------------------------------------------------------

export function createEvents(root, actions) {
  var sig = "";
  var list = el("ol", { "class": "ev-list", "aria-label": "Events, newest first" });
  var empty = el("p", { "class": "ev-empty", text: "No calls yet. Recorded calls appear here as agents send them; scenario runs appear when you run one." });
  root.appendChild(list); root.appendChild(empty);
  return {
    update: function (app) {
      var s2 = app.runs.length + "|" + (app.runs[0] && app.runs[0].id) + "|" + (app.focused && app.focused.id);
      if (s2 === sig) return;
      sig = s2;
      clear(list);
      empty.hidden = app.runs.length > 0;
      app.runs.slice(0, 60).forEach(function (run) {
        var a = runAction(run), b = badge(run.source), via = run.source === "simulation" ? "" : run.verdict && run.verdict.verdict_source;
        var row = el("button", { type: "button", "class": "ev-row" + (app.focused && app.focused.id === run.id ? " sel" : ""), "data-run": run.id, onclick: function () { actions.selectRun(run.id); } },
          el("span", { "class": "ev-time", text: fmtClock(run.ts) }),
          el("span", { "class": "src " + b.tone, title: b.detail, text: b.label }),
          el("span", { "class": "ev-label", text: run.label }),
          el("span", { "class": "chip " + (run.source === "simulation" ? "sim" : a === "allow" ? "ok" : a === "review" ? "warn" : "bad"), text: ACTION_WORD[a] || a }),
          el("span", { "class": "ev-policy", text: run.verdict && run.verdict.policy_id || "" }),
          el("span", { "class": "ev-via", text: via ? VIA_WORD[via] || via : "" }),
          el("span", { "class": "ev-ms", text: run.latency_ms != null ? fmtMs(run.latency_ms) : "" }));
        list.appendChild(el("li", null, row));
      });
    },
  };
}
