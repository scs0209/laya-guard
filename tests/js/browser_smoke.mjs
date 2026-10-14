// Drives the simulation view in a real (headless) Chrome through the DevTools protocol, with node's own WebSocket and
// no other dependency. Run by tests/test_sim_browser.py, which starts the server and checks what this prints.
//
//   node browser_smoke.mjs <chrome> <dashboard url> <webgl|nowebgl>
//
// It prints one JSON object: what it saw at each step, and every console error or uncaught exception.
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [chrome, url, mode] = process.argv.slice(2);
const port = 9300 + Math.floor(Math.random() * 500);
const profile = mkdtempSync(join(tmpdir(), "laya-sim-chrome-"));
const flags = ["--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, "--no-first-run", "--no-default-browser-check",
  "--disable-extensions", "--window-size=1280,900", "--force-device-scale-factor=1", "--hide-scrollbars", "--no-sandbox",
  ...(mode === "webgl" ? ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist", "--enable-webgl"] : ["--disable-gpu", "--disable-3d-apis", "--disable-webgl"]),
  "about:blank"];
const proc = spawn(chrome, flags, { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const errors = [];
const result = { mode, steps: {} };

async function main() {
  let targets = null;
  for (let i = 0; i < 100 && !targets; i++) {
    try { targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json(); } catch (e) { await sleep(150); }
  }
  const page = targets.find((t) => t.type === "page");
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0;
  const pending = new Map();
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) { const { res, rej } = pending.get(msg.id); pending.delete(msg.id); msg.error ? rej(new Error(msg.error.message)) : res(msg.result); }
    else if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error") errors.push("console.error: " + msg.params.args.map((a) => a.value ?? a.description).join(" "));
    else if (msg.method === "Runtime.exceptionThrown") errors.push("exception: " + (msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text));
    else if (msg.method === "Log.entryAdded" && msg.params.entry.level === "error") errors.push("log: " + msg.params.entry.text + " " + (msg.params.entry.url || ""));
  };
  const send = (method, params = {}) => new Promise((res, rej) => { const n = ++id; pending.set(n, { res, rej }); ws.send(JSON.stringify({ id: n, method, params })); });
  // run an expression in the page (it may be async) and return its value
  const js = async (expr) => {
    const r = await send("Runtime.evaluate", { expression: `(async () => { ${expr} })()`, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
    return r.result.value;
  };
  const waitFor = async (expr, what, ms = 15000) => {
    const t0 = Date.now();
    while (Date.now() - t0 < ms) { if (await js(`return !!(${expr})`)) return; await sleep(120); }
    throw new Error("timed out waiting for " + what);
  };

  await send("Runtime.enable"); await send("Page.enable"); await send("Log.enable");
  await send("Page.navigate", { url: url + "#/simulation" });
  await waitFor(`document.querySelector('#sim-root[data-sim-ready]')`, "the view to mount");
  const S = result.steps;
  const root = `document.getElementById('sim-root')`;
  const click = (sel, text) => js(`const els = [...${root}.querySelectorAll(${JSON.stringify(sel)})]; const e = els.find((x) => !${JSON.stringify(text || "")} || x.textContent.trim().startsWith(${JSON.stringify(text || "")})); if (!e) throw new Error('no ' + ${JSON.stringify(sel + " " + (text || ""))}); e.click(); return true;`);

  S.mounted = await js(`return {mode: ${root}.dataset.simMode, webglCanvas: !!${root}.querySelector('.sim-canvas'), kpis: [...${root}.querySelectorAll('.kpi')].map((k) => k.dataset.kpi + ':' + k.querySelector('.k-value').textContent)}`);

  // the first-run tour starts by itself and can be skipped
  S.tour = await js(`const box = ${root}.querySelector('.sim-tour'); const shown = !box.hidden && box.textContent.includes('Guided tour · 1 of 6'); [...box.querySelectorAll('button')].find((b) => b.textContent === 'Skip').click(); return {shown, hiddenAfter: box.hidden, remembered: localStorage.getItem('laya-guard:sim-tour')}`);

  // a scenario through the real evaluator (the server runs in mock mode: the label must say so)
  await click(".scn", "Remote script");
  await click(".sim-run", "Run scenario");
  await waitFor(`${root}.querySelector('.scn-result')`, "the scenario result");
  S.scenario = await js(`const r = ${root}.querySelector('.scn-result'); return {badge: r.querySelector('.src').textContent, verdict: r.querySelector('.chip').textContent, facts: r.querySelector('.facts').textContent, firstRow: ${root}.querySelector('.ev-row').textContent, chip: ${root}.querySelector('[data-chip=data]').textContent}`);

  // the inspector shows the finished run, not the "waiting" card it showed while the evaluator was still answering
  await click("[role=tab]", "Inspector");
  S.scenario.inspector = await js(`const p = ${root}.querySelector('#sim-panel-inspector'); return {text: p.textContent, waiting: p.textContent.includes('Waiting for the evaluator')}`);
  await click("[role=tab]", "Attack lab");

  // while the evaluator hasn't answered there is nothing to replay: the controls are off, and the call waits
  await js(`window.__realFetch = window.fetch; window.__gate = new Promise((r) => (window.__release = r));
            window.fetch = (u, o) => String(u).includes('/api/sim/evaluate') ? window.__gate.then(() => window.__realFetch(u, o)) : window.__realFetch(u, o); return true`);
  await click(".scn", "Harmless");
  await click(".sim-run", "Run scenario");
  await waitFor(`(() => { const f = [...${root}.simApi.inspect().flights.values()].find((x) => x.run.source === 'pending'); return f && f.player.holdAt !== null && f.player.t >= f.player.holdAt - 1; })()`, "the waiting call to reach where it waits", 30000);
  await sleep(700);                                  // and it stays there: nothing runs ahead of the answer
  S.pending = await js(`const api = ${root}.simApi.inspect(); const f = [...api.flights.values()].find((x) => x.run.source === 'pending');
    return {controlsOff: [...${root}.querySelectorAll('.sim-ctl-row button, .sim-ctl-row input')].every((b) => b.disabled), chip: ${root}.querySelector('[data-chip=data]').textContent,
            held: !!f && f.player.holdAt !== null, stuckAt: f && f.player.pose().frame.station, busy: api.app.busy, runs: api.app.runs.filter((r) => r.kind === 'scenario').length}`);
  await js(`window.__release(); window.fetch = window.__realFetch; return true`);
  await waitFor(`!${root}.simApi.inspect().app.busy`, "the held run to finish");
  S.afterPending = await js(`const { app } = ${root}.simApi.inspect(); return {source: app.runs[0].source, action: app.runs[0].verdict.action, controlsOn: !${root}.querySelector('.sim-ctl-row button').disabled}`);

  // the service stops answering mid-lab: the run becomes simulation data and says so
  await js(`window.fetch = (u, o) => String(u).includes('/api/sim/evaluate') ? Promise.reject(new Error('connection refused')) : window.__realFetch(u, o); return true`);
  await click(".scn", "Destructive");
  await click(".sim-run", "Run scenario");
  await waitFor(`${root}.querySelector('.lab-error')`, "the service-down notice");
  S.down = await js(`const { app } = ${root}.simApi.inspect(); return {error: ${root}.querySelector('.lab-error').textContent, source: app.runs[0].source, badge: ${root}.querySelector('.scn-result .src').textContent,
                       decided: ${root}.querySelector('.scn-result .facts').textContent, busy: app.busy}`);
  // the service answers, but with an error (a wrong token, an evaluator failure): the text must not claim it didn't answer
  await js(`window.fetch = (u, o) => String(u).includes('/api/sim/evaluate') ? Promise.resolve(new Response(JSON.stringify({detail: 'bad token'}), {status: 403, headers: {'Content-Type': 'application/json'}})) : window.__realFetch(u, o); return true`);
  await click(".scn", "Harmless");
  await click(".sim-run", "Run scenario");
  await waitFor(`${root}.querySelector('.lab-error')?.textContent.startsWith("The scenario couldn't be evaluated")`, "the service-error notice");
  S.answeredWithError = await js(`return ${root}.querySelector('.lab-error').textContent`);
  await js(`window.fetch = window.__realFetch; return true`);

  // simulation data is labelled as such and has no measured numbers
  await click(".lab-mode button", "Simulation data");
  await click(".scn", "Destructive");
  await click(".sim-run", "Run scenario");
  await waitFor(`${root}.querySelector('.scn-result .src')?.textContent === 'SIMULATION DATA'`, "the simulation-data result");
  S.simulation = await js(`const r = ${root}.querySelector('.scn-result'); return {facts: r.querySelector('.facts').textContent, note: ${root}.querySelector('.lab-modenote').textContent}`);
  await click(".lab-mode button", "Real evaluator");

  // the stations, reachable without the 3D scene
  await click(".strip-btn[data-station=policy]");
  S.policyGate = await js(`return {title: ${root}.querySelector('.insp h3').textContent, policies: ${root}.querySelectorAll('.pol-list li').length}`);
  await click(".pol-list button");
  S.policy = await js(`return ${root}.querySelector('.insp h3').textContent`);

  // replay controls on the last scenario run
  await click(".ev-row");
  S.replay = await js(`
    const ctl = (l) => ${root}.querySelector('.sim-ctl-row button[aria-label="' + l + '"]');
    const frames = (n) => new Promise((res) => { let i = 0; (function f() { if (++i >= n) res(); else requestAnimationFrame(f); })(); });
    await frames(4);
    const time = () => ${root}.querySelector('.sim-time').textContent;
    const out = {playing: !!ctl('Pause')};
    ctl('Pause').click(); await frames(3); const t = time(); await new Promise((r) => setTimeout(r, 300)); out.frozen = time() === t;
    ctl('Step forward one stage').click(); await frames(2); out.stepped = time() !== t;
    ctl('Restart the replay').click(); await frames(3); out.restarted = !!ctl('Pause');
    return out;`);

  // a recorded call: inspect it, then jump to it in Calls
  await js(`${root}.querySelector('.ev-row .src.plain')?.closest('button').click(); return true`);
  S.recorded = await js(`return {title: ${root}.querySelector('.insp h3').textContent, badge: ${root}.querySelector('.insp .src').textContent}`);
  await click(".lab-actions button", "Open in Calls");
  await waitFor(`document.getElementById('app').dataset.view === 'calls' && document.querySelector('#calls .call.sel')`, "the Calls view");
  S.calls = await js(`return {view: document.getElementById('app').dataset.view, detail: document.getElementById('call-pane').textContent.slice(0, 40)}`);

  // the other views are untouched
  S.views = {};
  for (const v of ["overview", "agents", "policies", "calls"]) {
    await js(`document.querySelector('[data-view=${v}]').click(); return true`);
    await sleep(250);
    S.views[v] = await js(`const p = document.getElementById('v-${v}'); return !p.hidden && p.textContent.trim().length > 20`);
  }
  await js(`document.querySelector('[data-view=simulation]').click(); return true`);

  if (mode === "webgl" && S.mounted.mode === "webgl") {
    // picking in the 3D scene: click where the policy gate and a call actually are on screen
    S.picking = await js(`
      const api = ${root}.simApi.inspect(); const { stage, app } = api; const rect = document.querySelector('.sim-canvas').getBoundingClientRect();
      const frames = (n) => new Promise((res) => { let i = 0; (function f() { if (++i >= n) res(); else requestAnimationFrame(f); })(); });
      stage.rig.reset(); await new Promise((r) => setTimeout(r, 900)); await frames(3);
      const where = (v) => { const p = v.clone().project(stage.rig.camera); return [rect.left + (p.x * 0.5 + 0.5) * rect.width, rect.top + (-p.y * 0.5 + 0.5) * rect.height]; };
      const token = stage.packets.pickables().find((m) => m.userData.zone);      // a resting token: a flying packet moves while we click
      return {tokenAt: where(token.position), n: stage.world.pickables.length, tokens: stage.packets.pickables().length, runs: app.runs.length};`);
    const clickAt = async ([x, y]) => {
      for (const type of ["mousePressed", "mouseReleased"]) await send("Input.dispatchMouseEvent", { type, x, y, button: "left", buttons: type === "mousePressed" ? 1 : 0, clickCount: 1 });
      await sleep(400);
    };
    // the camera: wheel zooms, a drag pans, a right-drag rotates
    const view = () => js(`const v = ${root}.simApi.inspect().stage.rig.view; return {zoom: v.zoom, x: v.target.x, z: v.target.z, az: v.az}`);
    const centre = await js(`const r = document.querySelector('.sim-canvas').getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]`);
    const [cx, cy] = centre;
    const v0 = await view();
    await send("Input.dispatchMouseEvent", { type: "mouseWheel", x: cx, y: cy, deltaX: 0, deltaY: -400 });
    await sleep(150);
    const v1 = await view();
    await send("Input.dispatchMouseEvent", { type: "mousePressed", x: cx, y: cy, button: "left", buttons: 1, clickCount: 1 });
    for (let i = 1; i <= 6; i++) await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: cx + i * 12, y: cy + i * 6, button: "left", buttons: 1 });
    await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: cx + 72, y: cy + 36, button: "left", buttons: 0, clickCount: 1 });
    await sleep(150);
    const v2 = await view();
    await send("Input.dispatchMouseEvent", { type: "mousePressed", x: cx, y: cy, button: "right", buttons: 2, clickCount: 1 });
    for (let i = 1; i <= 6; i++) await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: cx + i * 15, y: cy, button: "right", buttons: 2 });
    await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: cx + 90, y: cy, button: "right", buttons: 0, clickCount: 1 });
    await sleep(150);
    const v3 = await view();
    // a key the scene handles stays in the scene (the dashboard also binds "0" to "all calls")
    await js(`document.querySelector('.sim-canvas').focus(); return true`);
    await send("Input.dispatchKeyEvent", { type: "keyDown", key: "0", code: "Digit0", text: "0", windowsVirtualKeyCode: 48 });
    await send("Input.dispatchKeyEvent", { type: "keyUp", key: "0", code: "Digit0", windowsVirtualKeyCode: 48 });
    await sleep(300);
    S.keyboard = await js(`return {view: document.getElementById('app').dataset.view}`);
    S.camera = { zoomed: v1.zoom > v0.zoom, panned: Math.hypot(v2.x - v1.x, v2.z - v1.z) > 0.5, rotated: Math.abs(v3.az - v2.az) > 0.1 };
    S.picking.canvasUntouchedByClicks = (await js(`return ${root}.querySelector('.insp h3')?.textContent`)) !== null;
    await js(`${root}.simApi.inspect().stage.rig.reset(); return true`);
    await sleep(900);
    S.picking.tokenAt = await js(`
      const { stage } = ${root}.simApi.inspect(); const rect = document.querySelector('.sim-canvas').getBoundingClientRect();
      const frames = (n) => new Promise((res) => { let i = 0; (function f() { if (++i >= n) res(); else requestAnimationFrame(f); })(); });
      await frames(3);
      const token = stage.packets.pickables().find((m) => m.userData.zone); const p = token.position.clone().project(stage.rig.camera);
      return [rect.left + (p.x * 0.5 + 0.5) * rect.width, rect.top + (-p.y * 0.5 + 0.5) * rect.height];`);
    await clickAt(S.picking.tokenAt);
    S.picking.afterTokenClick = await js(`return ${root}.querySelector('.insp h3')?.textContent`);
    S.picking.ring = await js(`const { stage } = ${root}.simApi.inspect(); return stage.packets.positionOf(${root}.simApi.inspect().app.selection.id) !== null`);
  }
  // "Try again" after the config failed to load builds a working view, not a dead one
  await send("Page.addScriptToEvaluateOnNewDocument", { source: `(() => { let fail = true; const real = window.fetch.bind(window);
    window.fetch = (u, o) => (fail && String(u).includes('/api/sim/config')) ? (fail = false, Promise.reject(new Error('refused'))) : real(u, o); })()` });
  errors.length = 0;
  await send("Page.reload");
  await waitFor(`document.querySelector('#sim-root .sim-msg') && document.querySelector('#sim-root .sim-msg').textContent.includes("can't be loaded")`, "the load-failed notice");
  S.retryNotice = await js(`return ${root}.querySelector('.sim-msg').textContent`);
  await js(`${root}.querySelector('.sim-msg button').click(); return true`);
  await waitFor(`${root}.dataset.simReady`, "the retried view");
  await click(".sim-run", "Run scenario");
  await waitFor(`${root}.querySelectorAll('.ev-row .src.warn').length > 0`, "a run in the retried view");
  await sleep(1200);
  S.retry = await js(`const t = ${root}.querySelector('.sim-time').textContent; await new Promise((r) => setTimeout(r, 900)); return {mode: ${root}.dataset.simMode, before: t, after: ${root}.querySelector('.sim-time').textContent}`);
  S.errorsAfterRetry = errors.slice();
  S.errors = [...S.errors || [], ...[]];
  ws.close();
}

try { await main(); } catch (e) { result.failure = String(e && e.stack || e); }
result.errors = errors;
proc.kill();
await sleep(300);
rmSync(profile, { recursive: true, force: true });
console.log(JSON.stringify(result));
process.exit(0);
