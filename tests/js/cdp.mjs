// A small DevTools-protocol driver shared by the browser tests: start a headless Chrome, open a page, evaluate in it, click,
// wait, and collect console errors. Node's own WebSocket (node 22+), no other dependency.
import { spawn } from "node:child_process";
import { createServer } from "node:net";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const freePort = () => new Promise((res, rej) => { const s = createServer(); s.once("error", rej); s.listen(0, "127.0.0.1", () => { const { port } = s.address(); s.close(() => res(port)); }); });

export async function openChrome(chrome, mode) {
  const port = await freePort();
  const profile = mkdtempSync(join(tmpdir(), "laya-sim-chrome-"));
  const gl = mode === "webgl" ? ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist", "--enable-webgl"] : ["--disable-gpu", "--disable-3d-apis", "--disable-webgl"];
  const proc = spawn(chrome, ["--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, "--no-first-run", "--no-default-browser-check", "--disable-extensions",
    "--window-size=1280,900", "--force-device-scale-factor=1", "--hide-scrollbars", "--no-sandbox", ...gl, "about:blank"], { stdio: "ignore" });
  let exited = null;
  proc.once("exit", (code) => { exited = code; });
  let targets = null;
  for (let i = 0; i < 200 && !targets && exited === null; i++) {
    try { targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json(); } catch (e) { await sleep(150); }
  }
  if (!targets) throw new Error(exited !== null ? `Chrome exited with status ${exited} before it could be driven` : "Chrome didn't open its debugging port");
  const ws = new WebSocket(targets.find((t) => t.type === "page").webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0;
  const pending = new Map(), errors = [], handlers = new Map();
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) { const { res, rej } = pending.get(msg.id); pending.delete(msg.id); msg.error ? rej(new Error(msg.error.message)) : res(msg.result); }
    else if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error") errors.push("console.error: " + msg.params.args.map((a) => a.value ?? a.description).join(" "));
    else if (msg.method === "Runtime.exceptionThrown") errors.push("exception: " + (msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text));
    else if (msg.method === "Log.entryAdded" && msg.params.entry.level === "error") errors.push("log: " + msg.params.entry.text + " " + (msg.params.entry.url || ""));
    else if (handlers.has(msg.method)) handlers.get(msg.method)(msg.params);
  };
  const send = (method, params = {}) => new Promise((res, rej) => { const n = ++id; pending.set(n, { res, rej }); ws.send(JSON.stringify({ id: n, method, params })); });
  const js = async (expr) => {
    const r = await send("Runtime.evaluate", { expression: `(async () => { ${expr} })()`, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
    return r.result.value;
  };
  const waitFor = async (expr, what, ms = 20000) => {
    const t0 = Date.now();
    while (Date.now() - t0 < ms) { if (await js(`return !!(${expr})`)) return; await sleep(120); }
    throw new Error("timed out waiting for " + what);
  };
  await send("Runtime.enable"); await send("Page.enable"); await send("Log.enable");
  const root = `document.getElementById('sim-root')`;
  const click = (sel, text) => js(`const els = [...${root}.querySelectorAll(${JSON.stringify(sel)})]; const e = els.find((x) => !${JSON.stringify(text || "")} || x.textContent.trim().startsWith(${JSON.stringify(text || "")})); if (!e) throw new Error('no ' + ${JSON.stringify(sel + " " + (text || ""))}); e.click(); return true;`);
  return {
    send, js, waitFor, click, root, errors, sleep,
    /** Handle a DevTools event the driver doesn't use itself (for example Page.screencastFrame). */
    on(method, handler) { handlers.set(method, handler); },
    async close() { try { ws.close(); } catch (e) { /* gone */ } proc.kill(); await sleep(300); rmSync(profile, { recursive: true, force: true }); },
  };
}
