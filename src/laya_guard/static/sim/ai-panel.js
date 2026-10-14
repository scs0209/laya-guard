// The "AI scenario" panel: describe a situation, start the run, watch its decisions stream in, steer it (pause, step, stop,
// approve or deny a held command), and read the outcome. Everything the AI wrote is labelled AI-GENERATED; verdicts are
// laya-guard's own (REAL, or MOCK EVALUATOR); the virtual world's bookkeeping is SIMULATED.

import { el, clear, setText, fmtMs } from "./dom.js";
import { badge } from "./pipeline.js";
import { controlsFor, outcomeParts } from "./ai-reduce.js";
import { ACTION_WORD } from "./vocab.js";

function chip(text, tone) { return el("span", { "class": "chip " + tone, text: text }); }
function src(b) { return el("span", { "class": "src " + b.tone, title: b.detail, text: b.label }); }
function tone(action) { return action === "allow" ? "ok" : action === "review" ? "warn" : "bad"; }

/** `actions`: start(form), pause, resume, step, stop, review(decision), replay(id), consent(allow), refresh(), selectRow(row), edit. */
export function createAiPanel(root, actions) {
  var form = { prompt: "", model: "haiku", steps: "8", review: "ask", stepMode: false };
  var drawn = { run: null, rows: 0, summary: false, status: "", agent: "", lastStatus: "idle" };

  var intro = el("p", { "class": "lab-intro", text: "Describe any situation. An AI agent interprets it, plays it out in a virtual world, and every command it proposes is judged by the real laya-guard evaluator. Commands are never run." });
  var prompt = el("textarea", { "class": "ai-prompt", rows: "5", maxlength: "2000", spellcheck: "false", "aria-label": "Describe a situation", placeholder: "Describe what should happen, in your own words." });
  prompt.addEventListener("input", function () { form.prompt = prompt.value; counter.textContent = prompt.value.length + " / 2000"; actions.formChanged(); });
  var counter = el("span", { "class": "ai-count", text: "0 / 2000" });
  var examples = el("div", { "class": "ai-examples", role: "group", "aria-label": "Examples" });

  var agentBox = el("div", { "class": "ai-agent", role: "status" });
  var consentBox = el("div", { "class": "ai-consent", hidden: true });

  function select(label, id, options, onchange) {
    var s = el("select", { id: id, "aria-label": label });
    options.forEach(function (o) { s.appendChild(el("option", { value: o[0], text: o[1] })); });
    s.addEventListener("change", function () { onchange(s.value); });
    return el("label", { "class": "ai-opt" }, el("span", { text: label }), s);
  }
  var modelSel = select("Model", "ai-model", [["", "Agent's default"]], function (v) { form.model = v; });
  var stepsSel = select("Steps", "ai-steps", [["4", "4"], ["8", "8"], ["12", "12"], ["20", "20"]], function (v) { form.steps = v; });
  stepsSel.querySelector("select").value = "8";
  var reviewSel = select("Held commands", "ai-review", [["ask", "Ask me"], ["deny", "Deny"], ["approve", "Approve"]], function (v) { form.review = v; });
  var stepMode = el("input", { type: "checkbox", id: "ai-stepmode" });
  stepMode.addEventListener("change", function () { form.stepMode = stepMode.checked; });
  var options = el("div", { "class": "ai-options" }, modelSel, stepsSel, reviewSel, el("label", { "class": "ai-opt check" }, stepMode, el("span", { text: "Step by step" })));

  var bStart = el("button", { type: "button", "class": "sim-run", onclick: function () { actions.start(snapshot()); } }, "Start simulation");
  var bPause = el("button", { type: "button", "class": "sim-btn wide", onclick: function () { actions.pause(); } }, "Pause");
  var bResume = el("button", { type: "button", "class": "sim-btn wide", onclick: function () { actions.resume(); } }, "Resume");
  var bStep = el("button", { type: "button", "class": "sim-btn wide", onclick: function () { actions.step(); } }, "Step");
  var bStop = el("button", { type: "button", "class": "sim-btn wide", onclick: function () { actions.stop(); } }, "Stop");
  var bAgain = el("button", { type: "button", "class": "sim-btn wide", onclick: function () { actions.start(snapshot()); } }, "Run again");
  var bEdit = el("button", { type: "button", "class": "sim-btn wide", onclick: function () { formBox.open = true; prompt.focus(); actions.edit(); } }, "Edit scenario");
  var buttons = el("div", { "class": "ai-actions" }, bStart, bPause, bResume, bStep, bStop, bAgain, bEdit);

  var notice = el("p", { "class": "lab-error", role: "alert", hidden: true });
  var live = el("p", { "class": "ai-live", "aria-live": "polite" });
  var reviewCard = el("div", { "class": "ai-review", role: "alertdialog", "aria-label": "A command is held for review", hidden: true });
  var history = el("ol", { "class": "ai-history", "aria-label": "Actions and decisions, oldest first" });
  var outcome = el("div", { "class": "ai-outcome", hidden: true });
  var recentSel = el("select", { "aria-label": "Earlier runs", id: "ai-recent" });
  var recent = el("div", { "class": "ai-recent", hidden: true }, el("span", { "class": "muted", text: "Earlier runs this session: " }), recentSel,
    el("button", { type: "button", "class": "sim-btn wide", onclick: function () { if (recentSel.value) actions.replay(recentSel.value); } }, "Replay"));

  // the scenario form folds away once a run starts, so the history of the run is what you see; "Edit scenario" opens it again
  var formSum = el("summary", { text: "Scenario" });
  var formBox = el("details", { "class": "ai-form", open: true }, formSum, intro, prompt, el("div", { "class": "ai-promptfoot" }, examples, counter), options);
  function sumText() { return formBox.open || !form.prompt ? "Scenario" : "Scenario: " + (form.prompt.length > 90 ? form.prompt.slice(0, 90) + "…" : form.prompt); }
  formBox.addEventListener("toggle", function () { setText(formSum, sumText()); });
  root.appendChild(agentBox); root.appendChild(consentBox); root.appendChild(formBox); root.appendChild(buttons); root.appendChild(notice);
  root.appendChild(live); root.appendChild(reviewCard); root.appendChild(history); root.appendChild(outcome); root.appendChild(recent);

  function snapshot() {
    return { prompt: form.prompt.trim(), model: form.model, max_steps: Number(form.steps), mode: form.stepMode ? "step" : "run", review_policy: form.review };
  }

  function describeAgent(a) {
    if (!a) return "Looking for a coding agent…";
    if (!a.found) return a.name + " isn't available. " + (a.detail || "");
    if (a.logged_in === false) return a.name + " is installed but isn't logged in. " + (a.detail || "");
    return a.name + (a.version ? " " + a.version : "") + " is ready" + (a.logged_in ? " (logged in)." : ".");
  }

  function rowEl(r) {
    var head = el("div", { "class": "ai-row-head" }, src(r.badge), el("b", { text: r.title }), r.step != null ? el("span", { "class": "ai-step", text: "step " + r.step }) : null);
    var li = el("li", { "class": "ai-row k-" + r.kind, "data-seq": String(r.seq) }, head);
    var body = el("button", { type: "button", "class": "ai-row-body", onclick: function () { actions.selectRow(r); } });
    li.appendChild(body);
    if (r.text) body.appendChild(r.code ? el("code", { "class": "scn-cmd", text: r.text }) : el("p", { "class": "ai-text", text: r.text }));
    if (r.why) body.appendChild(el("p", { "class": "ai-why", text: "The agent's reason (its own words): " + r.why }));
    if (r.verdict) {
      var v = r.verdict;
      body.appendChild(el("div", { "class": "ai-verdict" }, chip(ACTION_WORD[v.action] || v.action, tone(v.action)),
        el("span", { "class": "muted", text: "risk " + Number(v.risk).toFixed(2) + " · " + (r.result ? r.result.latency_ms + " ms" : "") + " · the real evaluator, not the AI" })));
    }
    if (r.diff) body.appendChild(el("details", { "class": "ai-diff" }, el("summary", { text: "What changed" }), el("pre", { text: r.diff })));
    if (r.detail) {
      var d = r.detail, bits = el("div", { "class": "ai-detail" });
      if (d.goal) bits.appendChild(el("p", null, el("b", { text: "Goal: " }), d.goal));
      if (d.hazards && d.hazards.length) bits.appendChild(el("p", null, el("b", { text: "Hazards the AI sees: " }), d.hazards.join("; ")));
      if (d.watchFor) bits.appendChild(el("p", null, el("b", { text: "A good outcome: " }), d.watchFor));
      if (d.files && d.files.length) bits.appendChild(el("p", null, el("b", { text: "Virtual files: " }), d.files.map(function (f) { return f.path; }).join(", ")));
      body.appendChild(el("details", { "class": "ai-diff" }, el("summary", { text: "The AI's reading of the scenario" }), bits));
    }
    return li;
  }

  function outcomeEl(state) {
    var parts = outcomeParts(state);
    var box = el("div", { "class": "ai-outcome-body" }, el("h4", { text: "Outcome" }));
    var facts = el("section", { "class": "ai-facts" }, el("div", { "class": "res-head" }, src(badge("engine")), el("b", { text: "From the run (facts)" })));
    var ul = el("ul", null);
    parts.facts.forEach(function (f) { ul.appendChild(el("li", { text: f })); });
    facts.appendChild(ul);
    if (parts.caught !== null) facts.appendChild(el("p", { "class": "ai-caught " + (parts.caught ? "ok" : "bad"), text: parts.caught
      ? "laya-guard held or blocked at least one command."
      : "laya-guard allowed every command it judged." }));
    box.appendChild(facts);
    if (parts.ai) {
      var ai = parts.ai, sec = el("section", { "class": "ai-ai" }, el("div", { "class": "res-head" }, src(badge("ai")), el("b", { text: "The AI's account (not a measurement)" })),
        el("p", { "class": "ai-headline", text: ai.headline }), el("p", { text: ai.narrative }));
      if (ai.assessment) sec.appendChild(el("p", null, el("b", { text: "Did laya-guard catch it? " }), ai.assessment));
      var c = ai.consequences || {};
      if (c.allowed || c.reviewed || c.blocked) {
        var dl = el("dl", { "class": "facts" });
        [["If it were allowed", c.allowed], ["If a person reviewed it", c.reviewed], ["If it were blocked", c.blocked]].forEach(function (p) { if (p[1]) { dl.appendChild(el("dt", { text: p[0] })); dl.appendChild(el("dd", { text: p[1] })); } });
        sec.appendChild(el("p", { "class": "muted", text: "Hypotheses by the AI. Nothing was run, so none of this happened." }));
        sec.appendChild(dl);
      }
      if (ai.lessons && ai.lessons.length) { var l = el("ul", null); ai.lessons.forEach(function (x) { l.appendChild(el("li", { text: x })); }); sec.appendChild(l); }
      box.appendChild(sec);
    } else {
      box.appendChild(el("p", { "class": "muted", text: "The AI didn't write an account of this run." }));
    }
    return box;
  }

  return {
    /** Set the text of the prompt (an example, or what the user is editing). */
    setPrompt: function (text) { prompt.value = text; form.prompt = text; counter.textContent = text.length + " / 2000"; actions.formChanged(); },
    focusPrompt: function () { prompt.focus(); },
    form: function () { return snapshot(); },
    update: function (app) {
      var st = app.ai, ui = app.aiUi, ctl = controlsFor(st), status = ui.status, agent = status && status.agents && status.agents[0];
      // examples and model choices arrive with the server's status
      if (status && !examples.firstChild) {
        status.examples.forEach(function (text, i) { examples.appendChild(el("button", { type: "button", "class": "chip pol", onclick: function () { actions.example(text); } }, "Example " + (i + 1))); });
        if (!prompt.value) { prompt.placeholder = status.examples[0]; }
      }
      if (agent && modelSel.querySelector("select").options.length <= 1) {
        var sel = modelSel.querySelector("select");
        clear(sel);
        agent.models.forEach(function (m) { sel.appendChild(el("option", { value: m.id, text: m.label })); });
        sel.value = agent.models.some(function (m) { return m.id === form.model; }) ? form.model : "";
        form.model = sel.value;
      }
      var usable = !!agent && agent.found && agent.logged_in !== false;
      setText(agentBox, describeAgent(agent) + (agent && agent.inert && usable ? "" : ""));
      agentBox.className = "ai-agent " + (usable ? "ok" : agent ? "bad" : "");
      consentBox.hidden = !(usable && !agent.consent);
      if (!consentBox.hidden && !consentBox.firstChild) {
        consentBox.appendChild(el("p", { text: "Using " + agent.name + " sends what you type here, the virtual files and laya-guard's verdicts for this run to " + agent.name + ", which uses its own login and model as it always does. It also adds its own context to every request (for Claude Code: account details and any organisation instructions; your own CLAUDE.md and memory files are switched off for these calls). Nothing from your machine's files, logs or commands is added by laya-guard." }));
        consentBox.appendChild(el("p", { "class": "muted", text: "How it is kept from acting: " + agent.inert }));
        consentBox.appendChild(el("button", { type: "button", "class": "sim-run", onclick: function () { actions.consent(true); } }, "Allow for this session"));
      }
      if (usable && agent.consent && consentBox.firstChild) clear(consentBox);
      var ready = usable && agent.consent && form.prompt.trim().length > 0;
      bStart.hidden = !ctl.canStart || st.status !== "idle"; bStart.disabled = !ready;
      bAgain.hidden = !(ctl.canStart && st.status !== "idle"); bAgain.disabled = !ready;
      bEdit.hidden = !(ctl.canStart && st.status !== "idle");
      bPause.hidden = !ctl.canPause; bResume.hidden = !ctl.canResume; bStep.hidden = !ctl.canStep; bStop.hidden = !ctl.canStop;
      if (drawn.lastStatus === "idle" && st.status !== "idle") formBox.open = false;
      drawn.lastStatus = st.status;
      setText(formSum, sumText());
      var locked = !ctl.canStart;
      prompt.disabled = false; [modelSel, stepsSel, reviewSel].forEach(function (o) { o.querySelector("select").disabled = locked; }); stepMode.disabled = locked;
      notice.hidden = !(ui.notice || st.error);
      if (!notice.hidden) setText(notice, ui.notice || (st.error.message + (st.error.kind === "not-logged-in" ? " Run `claude` once in a terminal to log in." : "")));

      // what the agent is doing right now
      var line = "";
      if (st.status === "connecting") line = "Starting…";
      else if (st.thinking) line = (st.thinking.phase === "interpret" ? "The agent is reading your scenario" : st.thinking.phase === "summary" ? "The agent is writing the outcome" : "The agent is deciding step " + st.thinking.step) + "… " + Math.max(0, Math.round((Date.now() - Date.parse(st.thinking.t)) / 1000)) + " s";
      else if (st.status === "paused") line = "Paused. Resume, or step one decision at a time.";
      else if (st.status === "waiting-review") line = "A command is held for review.";
      else if (st.status === "running") line = "Step " + st.step + " of " + st.maxSteps + ". " + (st.cost ? "The agent reports $" + st.cost.toFixed(3) + " so far." : "");
      setText(live, line); live.hidden = !line;

      // a held command waits for you
      if (st.review && !st.review.auto) {
        if (reviewCard.hidden) {
          reviewCard.hidden = false; clear(reviewCard);
          reviewCard.appendChild(el("b", { text: "laya-guard held this command for review" }));
          reviewCard.appendChild(el("code", { "class": "scn-cmd", text: st.review.command }));
          reviewCard.appendChild(el("p", { "class": "muted", text: "In a real agent this is a permission prompt. If nobody answers in " + st.review.deadlineS + " s it is denied. It is never run either way." }));
          reviewCard.appendChild(el("div", { "class": "lab-actions" },
            el("button", { type: "button", "class": "sim-run", onclick: function () { actions.review("approve"); } }, "Approve"),
            el("button", { type: "button", "class": "sim-btn wide", onclick: function () { actions.review("deny"); } }, "Deny")));
        }
      } else if (!reviewCard.hidden) { reviewCard.hidden = true; clear(reviewCard); }

      // the history: new rows are appended, a new run starts it over
      if (drawn.run !== st.id || drawn.rows > st.rows.length) { clear(history); drawn.rows = 0; drawn.run = st.id; drawn.summary = false; outcome.hidden = true; clear(outcome); }
      for (var i = drawn.rows; i < st.rows.length; i++) history.appendChild(rowEl(st.rows[i]));
      if (st.rows.length > drawn.rows) { drawn.rows = st.rows.length; history.lastChild.scrollIntoView({ block: "nearest" }); }
      if (st.summary && !drawn.summary) { drawn.summary = true; outcome.hidden = false; outcome.appendChild(outcomeEl(st)); }

      // earlier runs
      var key = ui.recent.map(function (r) { return r.id; }).join(",");
      if (recentSel.dataset.key !== key) {
        recentSel.dataset.key = key; clear(recentSel);
        ui.recent.forEach(function (r) { recentSel.appendChild(el("option", { value: r.id, text: (r.state === "ended" ? "" : "(" + r.state + ") ") + r.prompt.slice(0, 50) })); });
        recent.hidden = ui.recent.length === 0;
      }
    },
  };
}
