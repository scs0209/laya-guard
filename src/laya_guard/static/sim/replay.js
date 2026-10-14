// Replay: a timeline of where a call is at each moment, and a player that moves along it. Pure (no DOM, no three.js).
//
// The timeline is built from the steps of a call, so replaying a recorded call shows the stages that were recorded
// for it. The durations are for drawing only: the one measured number, the whole evaluation's latency, is shown
// next to the picture and never stretched to fit it.

import { routeOf, stageOfStation } from "./pipeline.js";

export var TIMING = {
  leg: 700,
  dwell: { terminal: 400, queue: 450, policy: 700, engine: 1100, decision: 550, execution: 1000, review: 1000, incident: 1000 },
};
var EPS = 1;

/** One frame per station on `route`: when the call arrives there and when it leaves. */
export function fromRoute(route, steps, opts) {
  opts = opts || {};
  var by = {};
  steps.forEach(function (s) { by[s.stage] = s; });
  var frames = [], t = 0;
  route.forEach(function (station, i) {
    if (i > 0 && !opts.reduced) t += TIMING.leg;
    var dwell = TIMING.dwell[station] || 500;
    var stage = stageOfStation(station), step = by[stage] || null;
    frames.push({ i: i, station: station, stage: stage, status: step ? step.status : null, text: step ? step.text : "",
                  arrive: t, leave: t + dwell });
    t += dwell;
  });
  return { frames: frames, total: t, steps: steps, route: route };
}

export function buildTimeline(steps, opts) { return fromRoute(routeOf(steps), steps, opts); }

/** Where the call is at time `t`: dwelling at a frame, travelling between two, or finished. */
export function poseAt(tl, t) {
  var f = tl.frames, last = f.length - 1;
  if (!(t > 0)) t = 0;                       // before the start (or not a number): the start
  if (t >= tl.total) return { phase: "end", i: last, frame: f[last], from: f[last], to: null, p: 1, ended: true };
  for (var i = 0; i <= last; i++) {
    if (t < f[i].arrive) {
      var a = f[i - 1], span = f[i].arrive - a.leave;
      return { phase: "travel", i: i, frame: f[i], from: a, to: f[i], p: span > 0 ? (t - a.leave) / span : 1, ended: false };
    }
    if (t < f[i].leave) {
      return { phase: "dwell", i: i, frame: f[i], from: f[i], to: null, p: (t - f[i].arrive) / (f[i].leave - f[i].arrive), ended: false };
    }
  }
  return { phase: "end", i: last, frame: f[last], from: f[last], to: null, p: 1, ended: true };
}

export class Player {
  constructor(timeline) {
    this.tl = timeline; this.t = 0; this.playing = false; this.holdAt = null; this.speed = 1; this._cb = [];
  }
  onChange(fn) { this._cb.push(fn); }
  _emit() { var self = this; this._cb.forEach(function (fn) { fn(self); }); }
  get ended() { return this.t >= this.tl.total; }
  pose() { return poseAt(this.tl, this.t); }
  setTimeline(tl) { this.tl = tl; if (this.t > tl.total) this.t = tl.total; this._emit(); }
  play() { if (this.t >= this.tl.total) this.t = 0; this.playing = true; this._emit(); }
  pause() { this.playing = false; this._emit(); }
  toggle() { if (this.playing) this.pause(); else this.play(); }
  restart() { this.t = 0; this.playing = true; this._emit(); }
  seek(t) { this.t = Math.max(0, Math.min(this.tl.total, t)); this._emit(); }
  /** Jump to the start of the next station (or the end). Pauses, so each stage can be looked at. */
  stepForward() {
    this.playing = false;
    var t = this.t, next = null;
    for (var i = 0; i < this.tl.frames.length; i++) { if (this.tl.frames[i].arrive > t + EPS) { next = this.tl.frames[i].arrive; break; } }
    this.seek(next == null ? this.tl.total : next);
  }
  stepBack() {
    this.playing = false;
    var t = this.t, prev = 0;
    for (var i = 0; i < this.tl.frames.length; i++) { if (this.tl.frames[i].arrive < t - EPS) prev = this.tl.frames[i].arrive; }
    this.seek(prev);
  }
  /** While a result is still on its way, the call waits here instead of running ahead of what is known. */
  hold(t) { this.holdAt = t; }
  release() { this.holdAt = null; }
  tick(dtMs) {
    if (!this.playing || !(dtMs > 0)) return false;
    var limit = this.holdAt == null ? this.tl.total : Math.min(this.tl.total, this.holdAt);
    var before = this.t;
    this.t = Math.min(limit, this.t + dtMs * this.speed);
    if (this.t >= this.tl.total && this.holdAt == null) this.playing = false;      // held at the end of a live prefix isn't finished
    if (this.t !== before || !this.playing) this._emit();
    return this.t !== before;
  }
}

/** Where a live run waits for its result: at the engine (if it is running) a moment into its stay, otherwise at
 *  the end of the policy gate. */
export function holdTime(tl) {
  var last = tl.frames[tl.frames.length - 1];
  return last.station === "engine" ? last.arrive + 700 : last.leave;
}
