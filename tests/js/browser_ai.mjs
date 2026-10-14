// Drives the AI scenario panel in a real headless Chrome against a server whose `claude` is a scripted fake.
//   node browser_ai.mjs <chrome> <dashboard url> <webgl|nowebgl> <flow>
// flows: main (consent, a full run, the history, the outcome, the scene, inspecting, modifying and replaying), controls (step,
// pause, stop), review (a held command waits for approval), error (the agent fails). Prints one JSON object.
import { openChrome } from "./cdp.mjs";

const [chrome, url, mode, flow, decision = "approve"] = process.argv.slice(2);
const result = { mode, flow, steps: {} };
const PROMPT = "A coding agent is working in an untrusted repository. It modifies package.json to make npm test execute a suspicious shell command. Simulate what happens next.";

const page = await openChrome(chrome, mode);
const { js, waitFor, click, root, send, sleep } = page;
const S = result.steps;
const ai = `${root}.simApi.inspect().app.ai`;
const aiPanel = `${root}.querySelector('#sim-panel-ai')`;

async function main() {
  await send("Page.addScriptToEvaluateOnNewDocument", { source: "try { localStorage.setItem('laya-guard:sim-tour', 'done'); } catch (e) {}" });
  await send("Page.navigate", { url: url + "#/simulation" });
  await waitFor(`document.querySelector('#sim-root[data-sim-ready]')`, "the view to mount");
  S.mounted = await js(`return ${root}.dataset.simMode`);
  await click("[role=tab]", "AI scenario");
  await waitFor(`${aiPanel}.textContent.includes('is ready')`, "the agent status");

  const type = (text) => js(`const t = ${aiPanel}.querySelector('.ai-prompt'); t.value = ${JSON.stringify(text)}; t.dispatchEvent(new Event('input')); return true`);
  const consent = async () => { await click(".ai-consent button", "Allow for this session"); await waitFor(`${aiPanel}.querySelector('.ai-consent').hidden`, "consent to be recorded"); };
  // the run's state is folded as events arrive; the scene plays them at a pace a person can follow, so wait for both
  const played = () => js(`await ${root}.simApi.inspect().ai.sequencer.idle(); return true`);
  const finished = async (status = "finished") => { await waitFor(`${ai}.status === ${JSON.stringify(status)}`, "the run to reach " + status, 60000); await played(); };

  if (flow === "main") {
    // nothing can start before the user agrees
    await click(".ai-examples button", "Example 1");
    S.example = await js(`return ${aiPanel}.querySelector('.ai-prompt').value.length > 40`);
    S.beforeConsent = await js(`const p = ${aiPanel}; return {startDisabled: p.querySelector('.ai-actions .sim-run').disabled, notice: p.querySelector('.ai-consent').textContent, agent: p.querySelector('.ai-agent').textContent}`);
    await consent();
    S.afterConsent = await js(`return {startDisabled: ${aiPanel}.querySelector('.ai-actions .sim-run').disabled}`);
    await click(".ai-actions button", "Start simulation");
    await finished();
    S.run = await js(`const st = ${ai}; return {
      kinds: st.rows.map((r) => r.kind), titles: st.rows.map((r) => r.title), counts: st.counts, plan: st.plan && st.plan.title, step: st.step, status: st.status,
      badges: [...new Set(st.rows.map((r) => r.badge.label))].sort(), flagged: st.files['package.json'].flagged, changed: st.files['package.json'].changed,
      outcome: ${aiPanel}.querySelector('.ai-outcome').textContent, shown: ${aiPanel}.querySelectorAll('.ai-row').length,
      guardRow: ${aiPanel}.querySelector('.ai-row.k-guard').textContent, aiRow: ${aiPanel}.querySelector('.ai-row.k-ai').textContent,
      formOpen: ${aiPanel}.querySelector('.ai-form').open, formSummary: ${aiPanel}.querySelector('.ai-form > summary').textContent,
      runs: ${root}.simApi.inspect().app.runs.filter((r) => r.ai).map((r) => [r.label, r.source, r.verdict.action])}`);
    S.eventsList = await js(`return ${root}.querySelector('.ev-row').textContent`);
    if (mode === "webgl") {
      S.scene = await js(`const { stage } = ${root}.simApi.inspect(); const files = stage.world.pickables.filter((m) => m.userData.pick && m.userData.pick.kind === 'file');
        return {files: files.length, repoShown: !stage.world.stations.repo.hidden, agentShown: !stage.world.stations.agent.hidden, labels: [...${root}.querySelectorAll('.sim-label')].map((l) => l.textContent).filter((t) => /repository|AI agent/.test(t))}`);
    }
    // a row of the history opens what it is about
    await js(`${aiPanel}.querySelector('.ai-row.k-world .ai-row-body') && [...${aiPanel}.querySelectorAll('.ai-row.k-world')].find((r) => r.textContent.includes('File changed')).querySelector('.ai-row-body').click(); return true`);
    S.file = await js(`const p = ${root}.querySelector('#sim-panel-inspector'); return {title: p.querySelector('h3').textContent, text: p.textContent}`);
    await js(`${aiPanel}.querySelector('.ai-row.k-guard .ai-row-body').click(); return true`);
    S.command = await js(`const p = ${root}.querySelector('#sim-panel-inspector'); return {title: p.querySelector('h3').textContent, text: p.textContent}`);
    // modify the scenario and run it again: the first run's packets are gone, the new run starts clean, the old one can be replayed
    await click("[role=tab]", "AI scenario");
    await click(".ai-actions button", "Edit scenario");
    S.editOpens = await js(`return ${aiPanel}.querySelector('.ai-form').open`);
    await type(PROMPT + " This time the agent is told to avoid scripts.");
    const firstId = await js(`return ${ai}.id`);
    await click(".ai-actions button", "Run again");
    await waitFor(`${ai}.id !== ${JSON.stringify(firstId)}`, "the second run to begin");
    await finished();
    S.second = await js(`const st = ${ai}; return {rows: st.rows.length, aiRuns: ${root}.simApi.inspect().app.runs.filter((r) => r.ai).length, prompt: st.prompt, recent: [...${aiPanel}.querySelectorAll('#ai-recent option')].length}`);
    await js(`const s = ${aiPanel}.querySelector('#ai-recent'); s.selectedIndex = s.options.length - 1; [...${aiPanel}.querySelectorAll('.ai-recent button')].find((b) => b.textContent === 'Replay').click(); return true`);
    await waitFor(`${ai}.id === ${JSON.stringify(firstId)} && ${ai}.status === 'finished'`, "the replay to finish");
    S.replay = await js(`const st = ${ai}; return {rows: st.rows.length, edited: st.prompt.includes('avoid scripts'), counts: st.counts, id: st.id}`);
    S.firstId = firstId;
  }

  if (flow === "controls") {
    await consent();
    await type(PROMPT);
    await js(`${aiPanel}.querySelector('#ai-stepmode').click(); return true`);
    await click(".ai-actions button", "Start simulation");
    await waitFor(`${ai}.status === 'paused' && ${ai}.plan`, "the run to wait after its plan");
    S.beforeStep = await js(`return {actions: ${ai}.rows.filter((r) => r.title === 'Reads package.json').length, buttons: [...${aiPanel}.querySelectorAll('.ai-actions button')].filter((b) => !b.hidden).map((b) => b.textContent)}`);
    await click(".ai-actions button", "Step");
    await waitFor(`${ai}.rows.some((r) => r.title === 'Reads package.json') && ${ai}.status === 'paused'`, "the first step");
    S.afterStep = await js(`return {actions: ${ai}.rows.filter((r) => r.kind === 'ai' && r.action).length, step: ${ai}.step}`);
    await click(".ai-actions button", "Resume");
    await waitFor(`${ai}.rows.filter((r) => r.kind === 'ai' && r.action).length >= 2`, "the run to go on");
    await click(".ai-actions button", "Pause");
    await waitFor(`${ai}.status === 'paused'`, "the pause");
    S.paused = await js(`const n = ${ai}.rows.length; await new Promise((r) => setTimeout(r, 1200)); return {still: ${ai}.rows.length === n, status: ${ai}.status}`);
    await click(".ai-actions button", "Stop");
    await finished("stopped");
    S.stopped = await js(`return {summary: !!${ai}.summary, last: ${ai}.rows.at(-1).title, startShown: [...${aiPanel}.querySelectorAll('.ai-actions button')].some((b) => !b.hidden && (b.textContent === 'Run again' || b.textContent === 'Start simulation'))}`);
  }

  if (flow === "review") {
    await consent();
    await type(PROMPT);
    await click(".ai-actions button", "Start simulation");
    await waitFor(`${ai}.status === 'waiting-review'`, "the command to be held", 60000);
    S.held = await js(`const card = ${aiPanel}.querySelector('.ai-review'); return {shown: !card.hidden, text: card.textContent, command: ${ai}.review.command, runBusy: ${ai}.status}`);
    await click(".ai-review button", decision === "deny" ? "Deny" : "Approve");
    await finished();
    S.approved = await js(`const st = ${ai}; return {counts: st.counts, titles: st.rows.map((r) => r.title), outcome: ${aiPanel}.querySelector('.ai-outcome').textContent, reviewCard: ${aiPanel}.querySelector('.ai-review').hidden}`);
  }

  if (flow === "error") {
    await consent();
    await type(PROMPT);
    await click(".ai-actions button", "Start simulation");
    await finished("error");
    S.error = await js(`return {notice: ${aiPanel}.querySelector('.lab-error').textContent, startShown: [...${aiPanel}.querySelectorAll('.ai-actions button')].some((b) => !b.hidden && (b.textContent === 'Run again' || b.textContent === 'Start simulation')), last: ${ai}.rows.at(-1).title}`);
  }
  S.errors = page.errors.slice();
}

try { await main(); } catch (e) {
  result.failure = String(e && e.stack || e);
  try { result.state = await js(`const st = ${ai}; return {status: st.status, step: st.step, titles: st.rows.map((r) => r.title), notice: ${aiPanel}.querySelector('.lab-error').textContent, buttons: [...${aiPanel}.querySelectorAll('.ai-actions button')].filter((b) => !b.hidden).map((b) => b.textContent)}`); } catch (e2) { /* the page is gone */ }
}
result.errors = page.errors;
await page.close();
console.log(JSON.stringify(result));
process.exit(0);
