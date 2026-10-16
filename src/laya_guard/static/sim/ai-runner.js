// Talking to the server about AI scenario runs: which agent is available, the user's consent, starting a run, controlling it,
// and reading its events as a stream. The stream is read on its own (so a slow animation never stops the connection from being
// drained) and reconnects from where it left off if it drops.

import { isTerminal } from "./ai-reduce.js";

var RETRIES = 4;

export function createAiRunner(bridge) {
  var current = null;                    // {id, abort, stopped}

  function json(method, url, body) {
    var opts = { method: method, cache: "no-store", headers: {} };
    if (method !== "GET") {
      opts.headers["Content-Type"] = "application/json"; opts.headers["X-Laya-Token"] = bridge.token;
      opts.body = JSON.stringify(body || {});
    }
    return fetch(url, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) { var err = new Error(typeof j.detail === "string" ? j.detail : "HTTP " + r.status); err.status = r.status; throw err; }
        return j;
      });
    });
  }

  /** Read a run's events from `after` on, calling onEvent for each (not pings), until the run ends or `stop()`. */
  function stream(id, after, onEvent, onProblem) {
    var handle = { id: id, abort: typeof AbortController === "function" ? new AbortController() : null, stopped: false, last: after || 0 };
    current = handle;
    var attempts = 0;
    function open() {
      return fetch("/api/sim/ai/runs/" + encodeURIComponent(id) + "/events?after=" + handle.last, { cache: "no-store", signal: handle.abort ? handle.abort.signal : undefined }).then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        var buf = "", ended = false;
        function line(text) {
          if (!text.trim()) return;
          var e = JSON.parse(text);
          if (e.type === "ping") return;
          if (e.seq) handle.last = Math.max(handle.last, e.seq);
          if (isTerminal(e.type)) ended = true;
          attempts = 0;
          onEvent(e);
        }
        if (!res.body || !res.body.getReader) return res.text().then(function (t) { t.split("\n").forEach(line); });
        var reader = res.body.getReader(), dec = new TextDecoder();
        function pump() {
          return reader.read().then(function (chunk) {
            if (chunk.done) { line(buf); return ended; }
            buf += dec.decode(chunk.value, { stream: true });
            var parts = buf.split("\n"); buf = parts.pop(); parts.forEach(line);
            return handle.stopped ? true : pump();
          });
        }
        return pump();
      }).then(function (ended) {
        if (ended === false && !handle.stopped) return retry(new Error("the connection closed"));
      }).catch(function (err) {
        if (handle.stopped || (err && err.name === "AbortError")) return;
        return retry(err);
      });
    }
    function retry(err) {
      attempts += 1;
      if (attempts > RETRIES) { onProblem(err); return; }
      return new Promise(function (res) { setTimeout(res, 400 * attempts); }).then(function () { return handle.stopped ? null : open(); });
    }
    open();
    return handle;
  }

  return {
    status: function () { return json("GET", "/api/sim/ai/status"); },
    consent: function (agent, allow) { return json("POST", "/api/sim/ai/consent", { agent: agent, allow: allow !== false }); },
    recent: function () { return json("GET", "/api/sim/ai/runs"); },
    /** Start a run and read its events. Resolves with the run id once it exists. */
    start: function (opts, onEvent, onProblem) {
      return json("POST", "/api/sim/ai/runs", opts).then(function (j) { stream(j.id, 0, onEvent, onProblem); return j.id; });
    },
    /** Read an existing run from the beginning (a replay of a finished one, or reconnecting to a live one). */
    replay: function (id, onEvent, onProblem) { this.detach(); stream(id, 0, onEvent, onProblem); return id; },
    control: function (id, action, extra) { return json("POST", "/api/sim/ai/runs/" + encodeURIComponent(id) + "/control", Object.assign({ action: action }, extra || {})); },
    relayPending: function () { return json("GET", "/api/sim/ai/relay/pending"); },
    relayAnswer: function (decisionId, text) { return json("POST", "/api/sim/ai/relay/answer", { decision_id: decisionId, text: text }); },
    /** Stop reading (the run itself keeps going on the server unless it is stopped with control). */
    detach: function () { if (current) { current.stopped = true; if (current.abort) current.abort.abort(); current = null; } },
  };
}
