// The first-run tour: six steps along the pipeline, each moving the camera to its station. It starts by itself the
// first time the view opens (remembered in localStorage when that works) and can be replayed from the top bar.

import { el, clear } from "./dom.js";
import { TOUR } from "./vocab.js";

var KEY = "laya-guard:sim-tour";

var seenHere = false;      // when storage is blocked the tour still shows once per page load, not every time the view opens
export function tourSeen() {
  if (seenHere) return true;
  try { return localStorage.getItem(KEY) === "done"; } catch (e) { return false; }
}
function remember() {
  seenHere = true;
  try { localStorage.setItem(KEY, "done"); } catch (e) { /* it shows again on the next page load */ }
}

export function createTour(root, actions) {
  var step = -1;
  root.hidden = true;
  function show() {
    clear(root);
    var s = TOUR[step];
    root.hidden = false;
    root.appendChild(el("p", { "class": "tour-n", text: "Guided tour · " + (step + 1) + " of " + TOUR.length }));
    root.appendChild(el("h4", { text: s.title }));
    root.appendChild(el("p", { text: s.text }));
    root.appendChild(el("div", { "class": "tour-actions" },
      el("button", { type: "button", "class": "sim-btn wide", onclick: end }, "Skip"),
      step > 0 ? el("button", { type: "button", "class": "sim-btn wide", onclick: function () { go(step - 1); } }, "Back") : null,
      el("button", { type: "button", "class": "sim-run", onclick: function () { if (step < TOUR.length - 1) go(step + 1); else end(); } }, step < TOUR.length - 1 ? "Next" : "Done")));
    actions.selectStation(s.station);
  }
  function go(n) { step = n; show(); }
  function end() { step = -1; root.hidden = true; remember(); }
  return {
    start: function () { go(0); },
    end: end,
    active: function () { return step >= 0; },
  };
}
