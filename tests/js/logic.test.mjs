// The simulation view's pure logic (pipeline, replay, runs). Run by tests/test_sim_js.py, which writes the server's
// config and golden vectors from simulation.py so the page and the server are held to the same rules.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { join } from "node:path";

const SIM = pathToFileURL(join(process.env.LAYA_SIM_DIR, "/")).href;
const pipeline = await import(SIM + "pipeline.js");
const replay = await import(SIM + "replay.js");
const runs = await import(SIM + "runs.js");
const config = JSON.parse(readFileSync(process.env.LAYA_SIM_CONFIG, "utf8"));
const vectors = JSON.parse(readFileSync(process.env.LAYA_SIM_VECTORS, "utf8"));

const steps = (via, action, policy = "general", reason = "", degraded = null) => pipeline.stepsFor(config, via, action, policy, reason, degraded);

test("the page builds the same steps as the server for every kind of verdict", () => {
  assert.ok(vectors.length > 100);
  for (const v of vectors) {
    assert.deepEqual(pipeline.stepsFor(config, v.via, v.action, v.policy_id, v.reason, v.degraded), v.steps, JSON.stringify(v.args ?? v));
  }
});

test("a call with no recorded way of being judged still gets a path, marked unknown", () => {
  const s = steps(null, "allow");
  assert.deepEqual(s.map((x) => x.status), ["sent", "queued", "unknown", "unknown", "allow", "execution"]);
  assert.ok(s[2].text.includes("doesn't say"));
  assert.deepEqual(pipeline.routeOf(s), ["terminal", "queue", "policy", "decision", "execution"]);
});

test("rules decide without visiting the engine; the model is only visited if it was called", () => {
  assert.deepEqual(pipeline.routeOf(steps("regex", "block", "pipe-to-shell")), ["terminal", "queue", "policy", "decision", "incident"]);
  assert.deepEqual(pipeline.routeOf(steps("allowlist", "allow")), ["terminal", "queue", "policy", "decision", "execution"]);
  assert.deepEqual(pipeline.routeOf(steps("model", "review")), ["terminal", "queue", "policy", "engine", "decision", "review"]);
  // mock mode never calls the model: no pretending it did
  assert.deepEqual(pipeline.routeOf(steps("heuristic", "allow")), ["terminal", "queue", "policy", "decision", "execution"]);
  // a model error means it was called
  assert.deepEqual(pipeline.routeOf(steps("heuristic", "review", "general", "", "model_error")), ["terminal", "queue", "policy", "engine", "decision", "review"]);
  assert.equal(steps("heuristic", "review", "general", "", "model_error")[3].status, "failed");
  assert.equal(steps("heuristic", "review")[3].status, "fallback");
  // the hook's own offline check skips the server
  assert.deepEqual(pipeline.routeOf(steps("offline", "block", "pipe-to-shell")), ["terminal", "policy", "decision", "incident"]);
});

test("a review-level pattern is a floor, a package script is its own status", () => {
  assert.equal(steps("model", "review", "mass-delete")[2].status, "floor");
  assert.equal(steps("model", "allow", "general")[2].status, "clear");
  assert.equal(steps("regex", "block", "pipe-to-shell", "package.json script: x")[2].status, "script");
  assert.equal(steps("allowlist", "review", "package-scripts")[2].status, "script");
  assert.equal(pipeline.actionOf(steps("regex", "block", "x")), "block");
});

test("every status the steps can carry has words", () => {
  for (const via of Object.keys(config.paths)) for (const action of ["allow", "review", "block"]) {
    for (const s of steps(via, action, "mass-delete", "", "model_error")) assert.ok(s.text, `${s.stage}:${s.status}`);
  }
});

test("sources are never styled alike", () => {
  const tones = ["recorded", "real", "mock", "fallback", "simulation"].map((k) => pipeline.badge(k));
  assert.equal(new Set(tones.map((t) => t.label)).size, 5);
  assert.equal(pipeline.badge("real").tone, "ok");
  assert.equal(pipeline.badge("simulation").tone, "sim");
  assert.ok(pipeline.badge("mock").detail.includes("heuristics"));
});

test("a timeline has one frame per station, back to back, and a pose at every moment", () => {
  const tl = replay.buildTimeline(steps("model", "allow"));
  assert.deepEqual(tl.frames.map((f) => f.station), ["terminal", "queue", "policy", "engine", "decision", "execution"]);
  assert.equal(tl.frames[0].arrive, 0);
  for (let i = 1; i < tl.frames.length; i++) assert.equal(tl.frames[i].arrive, tl.frames[i - 1].leave + replay.TIMING.leg);
  assert.equal(tl.total, tl.frames.at(-1).leave);
  assert.equal(replay.poseAt(tl, 0).phase, "dwell");
  const between = tl.frames[1].arrive - 1;
  const travel = replay.poseAt(tl, tl.frames[0].leave + 1);
  assert.equal(travel.phase, "travel");
  assert.equal(travel.from.station, "terminal");
  assert.equal(travel.to.station, "queue");
  assert.ok(travel.p > 0 && travel.p < 1 && between > 0);
  assert.equal(replay.poseAt(tl, tl.total).ended, true);
  assert.equal(replay.poseAt(tl, tl.frames[3].arrive + 5).frame.stage, "engine");
});

test("reduced motion removes the travel, not the stages", () => {
  const tl = replay.buildTimeline(steps("regex", "block", "pipe-to-shell"), { reduced: true });
  assert.deepEqual(tl.frames.map((f) => f.station), ["terminal", "queue", "policy", "decision", "incident"]);
  tl.frames.slice(1).forEach((f, i) => assert.equal(f.arrive, tl.frames[i].leave));
});

test("the player plays, pauses, restarts and steps stage by stage", () => {
  const tl = replay.buildTimeline(steps("regex", "block", "pipe-to-shell"));
  const p = new replay.Player(tl);
  assert.equal(p.playing, false);
  p.play();
  p.tick(1000);
  assert.ok(p.t > 0 && p.playing);
  p.pause();
  const t = p.t;
  p.tick(500);
  assert.equal(p.t, t, "a paused player doesn't move");
  p.restart();
  assert.equal(p.t, 0);
  assert.equal(p.playing, true);
  p.pause();
  p.stepForward();
  assert.equal(p.t, tl.frames[1].arrive);
  p.stepForward();
  assert.equal(p.t, tl.frames[2].arrive);
  p.stepBack();
  assert.equal(p.t, tl.frames[1].arrive);
  p.seek(1e9);
  assert.equal(p.t, tl.total);
  assert.equal(p.ended, true);
  p.stepForward();
  assert.equal(p.t, tl.total);
  p.play();
  assert.equal(p.t, 0, "playing a finished replay starts it over");
  const seen = [];
  p.onChange((x) => seen.push(x.t));
  p.tick(10);
  assert.ok(seen.length >= 1);
});

test("a live run waits for its result instead of running ahead of it", () => {
  const prefix = replay.fromRoute(["terminal", "queue", "policy", "engine"], [{ stage: "engine", status: "running", text: "" }]);
  const player = new replay.Player(prefix);
  player.hold(replay.holdTime(prefix));
  player.play();
  player.tick(60_000);
  assert.equal(player.t, prefix.frames[3].arrive + 700, "held at the engine");
  assert.equal(player.playing, true, "still waiting, not finished");
  assert.equal(player.pose().frame.station, "engine");
  const final = replay.buildTimeline(steps("model", "review"));
  assert.deepEqual(final.frames.slice(0, 4).map((f) => [f.station, f.arrive]), prefix.frames.map((f) => [f.station, f.arrive]),
    "the result's timeline starts exactly like the live one, so nothing jumps");
  player.setTimeline(final);
  player.release();
  player.tick(60_000);
  assert.equal(player.ended, true);
  const noEngine = replay.fromRoute(["terminal", "queue", "policy"], []);
  assert.equal(replay.holdTime(noEngine), noEngine.frames[2].leave);
});

test("recorded calls keep a hash and a length, never a command, and sort into the two agent terminals", () => {
  const row = { ts: "2026-10-13T10:00:00Z", action: "block", risk: 0.92, policy_id: "pipe-to-shell", reason: "r", source: "tool", latency_ms: 3.1, tool_len: 41, tool_sha: "a1b2c3d4", via: "regex", degraded: null };
  const run = runs.recordedRun(config, row);
  assert.equal(run.kind, "recorded");
  assert.equal(run.source, "recorded");
  assert.equal(run.label, "#a1b2c3d4 · 41 chars");
  assert.ok(!JSON.stringify(run).includes("command"));
  assert.equal(run.verdict.action, "block");
  assert.equal(pipeline.routeOf(run.steps).at(-1), "incident");
  assert.equal(runs.terminalOf("a1b2c3d4"), runs.terminalOf("a1b2c3d4"));
  assert.ok([0, 1].includes(runs.terminalOf("ffff")) && [0, 1].includes(runs.terminalOf("0000")) && [0, 1].includes(runs.terminalOf("")));
  assert.notEqual(runs.terminalOf("0"), runs.terminalOf("1"));
});

test("only calls that haven't been drawn yet come back, oldest first", () => {
  const mk = (n) => ({ ts: `2026-10-13T10:00:0${n}Z`, action: "allow", tool_sha: "s" + n });
  const recent = [mk(3), mk(2), mk(1)];
  const seen = { [runs.recordedRun(config, { ...mk(1), via: "model" }).id]: true };
  const fresh = runs.newRecorded(recent, seen);
  assert.deepEqual(fresh.map((r) => r.tool_sha), ["s2", "s3"]);
});

test("scenario runs from the evaluator and from simulation data are marked apart, and compare per scenario", () => {
  const scenarios = config.scenarios;
  const s = scenarios[1];
  const result = { mode: "real", verdict: { action: "block", risk: 0.92, policy_id: "destructive-fs", reason: "r", mock: false, verdict_source: "regex" }, latency_ms: 0.4, steps: steps("regex", "block", "destructive-fs") };
  const real = runs.scenarioRun(config, s, result);
  const sim = runs.scenarioRun(config, s, null);
  assert.equal(real.source, "real");
  assert.equal(sim.source, "simulation");
  assert.equal(sim.verdict.risk, null, "an illustration has no measured risk");
  assert.equal(sim.latency_ms, null);
  assert.ok(sim.verdict.reason.includes("not measured"));
  const rows = runs.compareRows([sim, real], scenarios);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].run, sim, "the newest run of a scenario is the one compared");
  assert.equal(runs.runAction(real), "block");
});

test("a call's position follows its pose: out of its terminal, along the rails, through the gates, into its zone", async () => {
  const layout = await import(SIM + "layout.js");
  const tl = replay.buildTimeline(steps("model", "review"));
  const at = (t) => layout.positionAt(replay.poseAt(tl, t), 0);
  const start = at(0), end = at(tl.total);
  const t0 = layout.LAYOUT.terminals[0];
  assert.deepEqual([start.x, start.z], [t0.x, t0.z], "it starts at its terminal");
  assert.deepEqual([end.x, end.z], layout.LAYOUT.review.center, "a reviewed call ends in the review zone");
  for (let t = 0; t <= tl.total; t += 50) {
    const pos = at(t);
    assert.ok(Number.isFinite(pos.x) && Number.isFinite(pos.z) && pos.lift >= 0, `finite at ${t}`);
  }
  // through a gate: at the policy gate's dwell it is between its in and out ports
  const inGate = layout.positionAt(replay.poseAt(tl, tl.frames[2].arrive + replay.TIMING.dwell.policy / 2), 0);
  assert.ok(inGate.x > layout.LAYOUT.policy.in[0] && inGate.x < layout.LAYOUT.policy.out[0]);
  // between two gates the call is on the straight rail between the out port of one and the in port of the other
  const travelling = replay.poseAt(tl, tl.frames[1].leave + replay.TIMING.leg / 2);
  const mid = layout.positionAt(travelling, 0), a = layout.LAYOUT.queue.out, b = layout.LAYOUT.policy.in;
  assert.ok(Math.abs(mid.x - (a[0] + b[0]) / 2) < 1e-6 && mid.lift > 0);
  for (const zone of ["execution", "review", "incident"]) {
    const s = layout.slotIn(zone, 0);
    assert.ok(Number.isFinite(s.x) && Number.isFinite(s.z));
  }
  assert.equal(layout.isZone("incident"), true);
  assert.equal(layout.isZone("engine"), false);
});

test("a pose is defined at any time, even a negative or missing one, and a player never runs backwards", () => {
  const tl = replay.buildTimeline(steps("regex", "block", "pipe-to-shell"));
  for (const t of [-5, -0.001, 0, NaN, undefined, tl.total + 99]) {
    const pose = replay.poseAt(tl, t);
    assert.ok(pose.frame && pose.frame.station, String(t));
  }
  const p = new replay.Player(tl);
  p.play();
  p.tick(-40);
  p.tick(NaN);
  assert.equal(p.t, 0);
  assert.equal(p.pose().phase, "dwell");
});

test("simulation data is never drawn in the colours of a result, and a real run still is", async () => {
  const vocab = await import(SIM + "vocab.js");
  const s = config.scenarios[2];
  const sim = runs.scenarioRun(config, s, null);
  const real = runs.scenarioRun(config, s, { mode: "real", verdict: { action: "block", risk: 0.9, policy_id: "pipe-to-shell", reason: "r", mock: false, verdict_source: "regex" }, latency_ms: 1, steps: steps("regex", "block", "pipe-to-shell") });
  assert.equal(vocab.toneFor(sim, "policy", "hit"), "sim");
  assert.equal(vocab.toneFor(sim, "decision", "block"), "sim");
  assert.equal(vocab.toneFor(real, "policy", "hit"), "bad");
  assert.equal(vocab.toneFor(null, "decision", "allow"), "ok");
});

// ---- AI scenario runs: the event fold ---------------------------------------------------------------------------------

test("a whole AI run folds into the state the panel shows, and each row says where it came from", async () => {
  const ai = await import(SIM + "ai-reduce.js");
  const events = JSON.parse(readFileSync(process.env.LAYA_SIM_AI_EVENTS, "utf8"));
  const s = ai.reduceAll(events);
  assert.equal(s.status, "finished");
  assert.equal(s.plan.title, "Untrusted repo");
  assert.deepEqual(s.counts, { allow: 0, review: 0, block: 1 });
  assert.equal(s.lastSeq, events.at(-1).seq);
  assert.equal(s.files["package.json"].changed, true, "the agent changed package.json");
  assert.deepEqual(s.files["package.json"].flagged, { key: "test", policy: "pipe-to-shell" }, "laya-guard's script finding flags the file");
  assert.equal(s.files["package.json"].history.length, 1);
  assert.ok(s.files["package.json"].content.includes("curl"));
  assert.equal(s.files["src/index.js"].changed, false);
  const byKind = (k) => s.rows.filter((r) => r.kind === k);
  assert.ok(byKind("ai").every((r) => r.badge.label === "AI-GENERATED"), "everything the agent wrote is labelled as AI");
  assert.ok(byKind("world").every((r) => r.badge.label === "SIMULATED"));
  assert.deepEqual(byKind("guard").map((r) => r.badge.label), ["MOCK EVALUATOR"], "a verdict carries the evaluator's own label, never the AI's");
  assert.equal(byKind("guard")[0].verdict.action, "block");
  const parts = ai.outcomeParts(s);
  assert.equal(parts.caught, true);
  assert.ok(parts.facts[0].startsWith("1 command judged by laya-guard: 0 allowed, 0 held for review, 1 blocked"));
  assert.equal(parts.ai.headline, "laya-guard stopped the poisoned script.");
  assert.ok(!parts.facts.join(" ").includes("poisoned"), "the AI's words are kept out of the facts");
});

test("statuses follow the events: thinking, pausing, a held command, the end", async () => {
  const ai = await import(SIM + "ai-reduce.js");
  const s = ai.initialAiState();
  const ev = (type, extra = {}) => ({ seq: s.lastSeq + 1, t: "2026-10-14T00:00:00.000Z", type, ...extra });
  ai.reduceAiEvent(s, ev("run.started", { run_id: "r1", prompt: "p", agent: { id: "claude-code", name: "Claude Code" }, model: null, max_steps: 6, mode: "step", review_policy: "ask" }));
  assert.equal(s.status, "interpreting");
  ai.reduceAiEvent(s, ev("ai.thinking", { phase: "interpret", step: 0, attempt: 1 }));
  assert.equal(s.thinking.phase, "interpret");
  ai.reduceAiEvent(s, ev("ai.reply", { phase: "interpret", step: 0, attempt: 1, total_cost_usd: 0.01 }));
  assert.equal(s.thinking, null);
  assert.equal(s.cost, 0.01);
  ai.reduceAiEvent(s, ev("run.paused", { step: 1 }));
  assert.equal(s.status, "paused");
  assert.deepEqual(Object.entries(ai.controlsFor(s)).filter(([, v]) => v).map(([k]) => k).sort(), ["canResume", "canStep", "canStop"]);
  ai.reduceAiEvent(s, ev("run.resumed", { step: 1 }));
  assert.equal(s.status, "running");
  ai.reduceAiEvent(s, ev("review.wait", { step: 1, command: "rm x", deadline_s: 120, auto: false }));
  assert.equal(s.status, "waiting-review");
  assert.equal(s.review.command, "rm x");
  assert.equal(ai.controlsFor(s).canReview, true);
  ai.reduceAiEvent(s, ev("review.decision", { step: 1, decision: "deny", by: "timeout" }));
  assert.equal(s.review, null);
  assert.equal(s.rows.at(-1).text, "no answer in time, so denied");
  ai.reduceAiEvent(s, ev("ai.invalid", { phase: "act", step: 2, attempt: 1, errors: ["type must be one of ..."], will_retry: true }));
  assert.ok(s.rows.at(-1).text.includes("it is asked again"));
  ai.reduceAiEvent(s, ev("run.error", { kind: "timeout", message: "no answer in 120 s" }));
  assert.equal(s.status, "error");
  assert.deepEqual(s.error, { kind: "timeout", message: "no answer in 120 s" });
  assert.deepEqual(ai.controlsFor(s), { canStart: true, canPause: false, canResume: false, canStep: false, canStop: false, canReview: false });
  assert.equal(ai.isTerminal("run.stopped"), true);
  assert.equal(ai.isTerminal("step.end"), false);
});

test("a run where the agent proposed no command says so as a fact, and does not claim laya-guard caught anything", async () => {
  const ai = await import(SIM + "ai-reduce.js");
  const s = ai.initialAiState();
  ai.reduceAiEvent(s, { seq: 1, type: "summary", facts: { commands_judged: 0, verdicts: { allow: 0, review: 0, block: 0 }, files_written: [], ended_because: "agent-finished" }, ai: null });
  const parts = ai.outcomeParts(s);
  assert.equal(parts.caught, null);
  assert.ok(parts.facts.includes("The agent proposed no command, so laya-guard had nothing to judge"));
  assert.equal(parts.ai, null);
});

test("a path that is a name every object already has cannot reach the page's own objects", async () => {
  const ai = await import(SIM + "ai-reduce.js");
  const s = ai.initialAiState();
  ai.reduceAiEvent(s, { seq: 1, type: "world.snapshot", files: { "a.txt": "x", constructor: "c" } });
  for (const [i, path] of ["__proto__", "constructor", "toString", "hasOwnProperty"].entries()) {
    ai.reduceAiEvent(s, { seq: 2 + i, type: "world.write", step: 1, path, created: true, content: "evil", before_chars: 0, after_chars: 4, diff: "" });
  }
  assert.equal(({}).content, undefined, "Object.prototype was not touched");
  assert.equal(({}).size, undefined);
  assert.equal(typeof Object.prototype.toString, "function");
  assert.equal(typeof ({}).hasOwnProperty, "function");
  assert.equal(s.files.constructor.content, "evil", "the AI's file called constructor is just a file");
  assert.equal(Object.getPrototypeOf(s.files), null);
  assert.deepEqual(Object.keys(s.files).sort(), ["__proto__", "a.txt", "constructor", "hasOwnProperty", "toString"]);
});

test("unknown events and an empty run are harmless, and no summary means no outcome", async () => {
  const ai = await import(SIM + "ai-reduce.js");
  const s = ai.initialAiState();
  ai.reduceAiEvent(s, { seq: 1, type: "something.new", x: 1 });
  ai.reduceAiEvent(s, { type: "ping" });
  assert.equal(s.rows.length, 0);
  assert.equal(ai.outcomeParts(s), null);
  assert.equal(ai.describeAction({ type: "write_file", path: "a" }), "Writes a");
  assert.equal(ai.describeAction({ type: "list_dir" }), "Lists .");
});

// ---- the sequencer ----------------------------------------------------------------------------------------------------

const tick = (ms = 0) => new Promise((r) => setTimeout(r, ms));

test("events play strictly one after another, each waiting for the previous animation", async () => {
  const { createSequencer } = await import(SIM + "ai-sequencer.js");
  const log = [];
  const seq = createSequencer(async (e) => { log.push("start " + e.n); await tick(15); log.push("end " + e.n); });
  [1, 2, 3].forEach((n) => seq.push({ n }));
  assert.equal(seq.length, 3);
  await seq.idle();
  assert.deepEqual(log, ["start 1", "end 1", "start 2", "end 2", "start 3", "end 3"]);
  assert.equal(seq.length, 0);
});

test("a handler that returns nothing, throws or rejects doesn't stall the rest", async () => {
  const { createSequencer } = await import(SIM + "ai-sequencer.js");
  const seen = [];
  const quiet = console.error; console.error = () => {};
  const seq = createSequencer((e) => { seen.push(e.n); if (e.n === 2) throw new Error("boom"); if (e.n === 3) return Promise.reject(new Error("no")); });
  [1, 2, 3, 4].forEach((n) => seq.push({ n }));
  await seq.idle();
  console.error = quiet;
  assert.deepEqual(seen, [1, 2, 3, 4]);
});

test("pausing holds after the event being played; resuming carries on", async () => {
  const { createSequencer } = await import(SIM + "ai-sequencer.js");
  const seen = [];
  const seq = createSequencer(async (e) => { seen.push(e.n); await tick(20); });
  seq.push({ n: 1 }); seq.push({ n: 2 }); seq.push({ n: 3 });
  seq.pause();
  await seq.idle();
  assert.deepEqual(seen, [1], "the event already playing finishes, then it holds");
  assert.equal(seq.paused, true);
  await tick(60);
  assert.deepEqual(seen, [1]);
  seq.push({ n: 4 });
  assert.deepEqual(seen, [1], "new events wait too");
  seq.resume();
  await seq.idle();
  assert.deepEqual(seen, [1, 2, 3, 4]);
});

test("stepping plays up to the end of the next step and holds again", async () => {
  const { createSequencer } = await import(SIM + "ai-sequencer.js");
  const seen = [];
  const seq = createSequencer((e) => { seen.push(e.t); }, (e) => e.t === "step.end");
  seq.pause();
  ["step.begin", "action", "step.end", "step.begin", "action", "step.end"].forEach((t) => seq.push({ t }));
  assert.deepEqual(seen, []);
  seq.stepOnce();
  await seq.idle();
  assert.deepEqual(seen, ["step.begin", "action", "step.end"]);
  assert.equal(seq.paused, true);
  seq.stepOnce();
  await seq.idle();
  assert.equal(seen.length, 6);
  seq.stepOnce(); seq.stepOnce();
  seq.push({ t: "step.begin" }); seq.push({ t: "step.end" }); seq.push({ t: "step.begin" }); seq.push({ t: "step.end" });
  await seq.idle();
  assert.equal(seen.length, 10, "two steps asked for, two steps played");
});

test("clearing drops what hasn't played, and a disposed sequencer plays nothing more", async () => {
  const { createSequencer } = await import(SIM + "ai-sequencer.js");
  const seen = [];
  const seq = createSequencer(async (e) => { seen.push(e.n); await tick(15); });
  [1, 2, 3].forEach((n) => seq.push({ n }));
  seq.clear();
  await seq.idle();
  assert.deepEqual(seen, [1]);
  seq.dispose();
  seq.push({ n: 9 });
  await tick(30);
  assert.deepEqual(seen, [1]);
});
