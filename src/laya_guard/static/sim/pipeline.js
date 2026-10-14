// The pipeline as the page knows it. Pure functions with no DOM and no three.js, so node can test them.
//
// The stage table (stages, how each kind of verdict travels, the words for each status) comes from the server
// (/api/sim/config) and is the one the server itself uses for scenario runs: the page never has its own idea of how
// a call is judged.

export var STATIONS = ["terminal", "queue", "policy", "engine", "decision", "execution", "review", "incident"];
export var ZONE_STATIONS = { allow: "execution", review: "review", block: "incident" };

var STAGE_OF_STATION = { execution: "zone", review: "zone", incident: "zone" };
export function stageOfStation(station) { return STAGE_OF_STATION[station] || station; }

var UNKNOWN = "This entry doesn't say how it was judged (it predates the log's `via` field).";

/** The steps a call went through, from what the verdict log records: how it was judged (`via`), the action, the
 *  policy id and the reason. The same rules as `steps_for` in simulation.py, driven by the same table. */
export function stepsFor(config, via, action, policyId, reason, degraded) {
  var path = via ? config.paths[via] : null;
  if (!path) {
    return [
      { stage: "terminal", status: "sent", text: config.status_text["terminal:sent"] },
      { stage: "queue", status: "queued", text: config.status_text["queue:queued"] },
      { stage: "policy", status: "unknown", text: UNKNOWN },
      { stage: "engine", status: "unknown", text: UNKNOWN },
      { stage: "decision", status: action, text: config.status_text["decision:" + action] || "" },
      { stage: "zone", status: config.zones[action], text: config.status_text["zone:" + config.zones[action]] || "" },
    ];
  }
  var scripted = String(reason || "").indexOf("package.json script") === 0 || policyId === "package-scripts";
  var generic = config.generic_policy_ids || [];
  return path.map(function (pair) {
    var stage = pair[0], status = pair[1];
    if (stage === "decision") status = action;
    else if (stage === "zone") status = config.zones[action];
    else if (stage === "policy" && (via === "regex" || via === "allowlist") && scripted) status = "script";
    else if (stage === "policy" && (via === "model" || via === "heuristic") && policyId && generic.indexOf(policyId) < 0) status = "floor";
    else if (stage === "engine" && via === "heuristic" && degraded === "model_error") status = "failed";
    return { stage: stage, status: status, text: config.status_text[stage + ":" + status] || "" };
  });
}

function byStage(steps) {
  var by = {};
  steps.forEach(function (s) { by[s.stage] = s; });
  return by;
}

/** The stations a call visits, in order. The engine is visited when the model was called (it ran, or it failed);
 *  mock mode never calls it, so there the call goes past without pretending inference happened. */
export function routeOf(steps) {
  var by = byStage(steps), route = ["terminal"];
  if (by.queue && by.queue.status !== "bypassed") route.push("queue");
  route.push("policy");
  if (by.engine && (by.engine.status === "ran" || by.engine.status === "failed")) route.push("engine");
  route.push("decision");
  route.push(by.zone ? by.zone.status : "execution");
  return route;
}

/** Which verdict a call ended with, from its steps. */
export function actionOf(steps) {
  var d = byStage(steps).decision;
  return d ? d.status : null;
}

/** The label and tone for where a piece of data came from. Real, mock and recorded data are never styled alike. */
export function badge(kind) {
  var table = {
    recorded: { label: "RECORDED", tone: "plain", detail: "Read from the local verdict log: what a hook sent and what was decided." },
    real: { label: "REAL", tone: "ok", detail: "Judged just now by the local evaluator (this server)." },
    mock: { label: "MOCK EVALUATOR", tone: "warn", detail: "The server runs in mock mode: the rules ran as usual, but the model was replaced by heuristics." },
    fallback: { label: "HEURISTIC FALLBACK", tone: "warn", detail: "The model failed on this call, so regexes and heuristics stood in." },
    simulation: { label: "SIMULATION DATA", tone: "sim", detail: "Nothing was evaluated. This is a typical outcome, drawn for illustration." },
  };
  return table[kind] || table.recorded;
}
