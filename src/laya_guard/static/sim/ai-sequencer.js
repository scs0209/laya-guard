// Plays a run's events one after another, each after the previous one's animation is done. Events arrive from the server as
// they happen (an AI answer can take seconds); the scene needs them in order and at a pace a person can follow. Pure (no
// DOM): the handler is whatever animates an event and returns a promise (or nothing) when it is finished.

/** `handler(event)` animates one event. `isStepEnd(event)` marks the end of a step, where stepping pauses again. */
export function createSequencer(handler, isStepEnd) {
  var queue = [], busy = false, paused = false, steps = 0, idleWaiters = [], dead = false, current = null;

  function settled() {
    if (!busy && (queue.length === 0 || (paused && steps === 0))) {
      var w = idleWaiters; idleWaiters = [];
      w.forEach(function (fn) { fn(); });
    }
  }

  function pump() {
    if (busy || dead) return;
    if (!queue.length || (paused && steps === 0)) { settled(); return; }
    var e = queue.shift();
    busy = true;
    current = e;
    var done = function () {
      busy = false; current = null;
      if (isStepEnd && isStepEnd(e) && steps > 0) { steps -= 1; if (steps === 0) paused = true; }
      pump();
    };
    var out;
    try { out = handler(e); } catch (err) { out = null; if (typeof console !== "undefined") console.error(err); }
    if (out && typeof out.then === "function") out.then(done, function (err) { if (typeof console !== "undefined") console.error(err); done(); });
    else done();
  }

  return {
    push: function (e) { queue.push(e); pump(); },
    /** Hold after the event being animated now. */
    pause: function () { paused = true; steps = 0; },
    resume: function () { paused = false; steps = 0; pump(); },
    /** Let events through until the next step ends, then hold again. */
    stepOnce: function () { paused = true; steps += 1; pump(); },
    clear: function () { queue = []; settled(); },
    dispose: function () { dead = true; queue = []; settled(); },
    get length() { return queue.length + (busy ? 1 : 0); },
    get paused() { return paused; },
    get current() { return current; },
    /** Resolves when nothing is playing and nothing more can play (the queue is empty, or it is held). */
    idle: function () { return new Promise(function (res) { if (!busy && (queue.length === 0 || (paused && steps === 0))) res(); else idleWaiters.push(res); }); },
  };
}
