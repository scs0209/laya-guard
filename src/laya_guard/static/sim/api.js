// Talking to the local server: the simulation's config, and a scenario run streamed as NDJSON (one line per stage
// event as the pipeline reaches it, then one line with the result).

var EVALUATE_TIMEOUT_MS = 90000;       // a cold model can take a while to load; a stream that never ends shouldn't hang the lab

/** The server did answer: this error came from its reply (a status, or an error line in the stream), not from a missing one. */
function answered(err) { err.answered = true; return err; }

export function createApi(bridge) {
  function fail(res, body) {
    var msg = body && typeof body.detail === "string" ? body.detail : "HTTP " + res.status;
    return answered(new Error(msg));
  }
  return {
    config: function () {
      return fetch("/api/sim/config", { cache: "no-store" }).then(function (r) {
        if (!r.ok) throw answered(new Error("HTTP " + r.status));
        return r.json();
      });
    },
    /** Judge a scenario. `onEvent(event)` hears each stage as it happens; resolves with the result line. */
    evaluate: function (scenarioId, onEvent) {
      var ctl = typeof AbortController === "function" ? new AbortController() : null;
      var timer = ctl ? setTimeout(function () { ctl.abort(); }, EVALUATE_TIMEOUT_MS) : 0;
      function finish(v) { clearTimeout(timer); return v; }
      function failed(e) { clearTimeout(timer); throw (e && e.name === "AbortError" ? new Error("no answer within " + EVALUATE_TIMEOUT_MS / 1000 + " seconds") : e); }
      return fetch("/api/sim/evaluate/" + encodeURIComponent(scenarioId), {
        method: "POST", cache: "no-store", body: "{}", signal: ctl ? ctl.signal : undefined,
        headers: { "Content-Type": "application/json", "X-Laya-Token": bridge.token },
      }).then(function (res) {
        if (!res.ok) return res.json().catch(function () { return {}; }).then(function (b) { throw fail(res, b); });
        var result = null, error = null;
        function line(text) {
          if (!text.trim()) return;
          var msg = JSON.parse(text);
          if (msg.type === "event") onEvent(msg);
          else if (msg.type === "result") result = msg;
          else if (msg.type === "error") error = answered(new Error(msg.message));
        }
        function done() {
          if (error) throw error;
          if (!result) throw new Error("the evaluator closed the stream without an answer");
          return result;
        }
        if (!res.body || !res.body.getReader) return res.text().then(function (t) { t.split("\n").forEach(line); return done(); });
        var reader = res.body.getReader(), dec = new TextDecoder(), buf = "";
        function pump() {
          return reader.read().then(function (chunk) {
            if (chunk.done) { line(buf); return done(); }
            buf += dec.decode(chunk.value, { stream: true });
            var parts = buf.split("\n");
            buf = parts.pop();
            parts.forEach(line);
            return pump();
          });
        }
        return pump();
      }).then(finish, failed);
    },
  };
}
