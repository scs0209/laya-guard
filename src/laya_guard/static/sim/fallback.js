// What the scene area shows when WebGL isn't available (or three.js couldn't load): the same pipeline as a row of
// cards, with the stage status of the call being replayed. Everything the 3D view says is here in text too.

import { el, clear } from "./dom.js";
import { STATIONS, stageOfStation } from "./pipeline.js";
import { STATION_NAME, toneFor, statusWord } from "./vocab.js";

export function createFallback(root, actions, reason) {
  root.appendChild(el("p", { "class": "fb-note", role: "status", text: "The 3D view isn't available" + (reason ? " (" + reason + ")" : "") + ". This is the same pipeline as a list: select a stage to inspect it, run a scenario in the Attack lab, and use the replay controls below." }));
  var grid = el("ol", { "class": "fb-grid" });
  root.appendChild(grid);
  var sig = "";
  return {
    update: function (app) {
      var cur = app.player ? app.player.pose().frame.station : null;
      var s2 = [app.focused && app.focused.id, cur, app.selection && app.selection.id, app.zoneCounts.join(",")].join("|");
      if (s2 === sig) return;
      sig = s2;
      clear(grid);
      var by = {};
      ((app.focused && app.focused.steps) || []).forEach(function (s) { by[s.stage] = s; });
      STATIONS.forEach(function (st) {
        var step = by[stageOfStation(st)], isZone = st === "execution" || st === "review" || st === "incident";
        var shown = step && (!isZone || step.status === st);
        var about = app.config.stages.find(function (x) { return x.id === stageOfStation(st); });
        var node = el("button", { type: "button", "class": "fb-card" + (cur === st ? " cur" : "") + (app.selection && String(app.selection.id).split(":")[0] === st ? " sel" : ""),
          onclick: function () { actions.selectStation(st === "terminal" ? "terminal:0" : st); } },
          el("b", { text: STATION_NAME[st] }),
          shown ? el("span", { "class": "chip " + toneFor(app.focused, step.stage, step.status), text: statusWord(step.stage, step.status) }) : null,
          isZone ? el("span", { "class": "strip-n", text: "×" + app.zoneCounts[["execution", "review", "incident"].indexOf(st)] }) : null,
          el("span", { "class": "muted", text: about ? about.about : "" }));
        grid.appendChild(el("li", null, node));
      });
    },
  };
}
