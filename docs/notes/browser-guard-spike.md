# Browser guard spike (feasibility check, 2026-11-03)

Question: can laya-guard judge Claude Code's browser-control tool calls with the same PreToolUse hook and rules-only
checks it uses for shell commands? Short answer: **yes for interception and for some checks (secrets in typed text,
URLs); no for clicks today**, because a click names an opaque element id or pixel coordinates, not what it clicks.

## 1. Tool names and field shapes

### Real calls found

Transcripts read: `~/.claude/projects/*/*.jsonl`. Every folder mentions "shopl" somewhere, mostly in text the harness
injects into every session (agent and skill listings, the account email). Folders were skipped when the mention was
real company material: `dropzone-zero` (a company file name in a listing) and one scratch workspace with company work.
In the rest, only the `laya-guard` folder had browser calls: **23 calls, one session, all `mcp__Claude_Browser__*`**
(the desktop app's built-in browser). No `computer`-use built-in, Playwright or Chrome-extension calls.

| tool | calls | fields (value kind) |
|---|---|---|
| `browser_batch` | 8 | `actions`: list of `{name, input}`; inside: `navigate` ×11, `computer` ×9, `javascript_tool` ×2, `get_page_text` ×2, `find` ×1 |
| `javascript_tool` | 6 (+2 in batches) | `action` = `javascript_exec`, `text` = JS source (90–280 chars), `tabId` |
| `resize_window` | 4 | `colorScheme` (enum), `tabId` |
| `computer` | 3 (+9 in batches) | `action` = `screenshot`/`zoom` only, `scale` (number), `region` (4 numbers), `tabId` |
| `read_console_messages` | 2 | `onlyErrors` (bool) |
| `navigate` (in batches) | 11 | `url`: localhost ×6, `file://` ×2, github ×2, other public sites (no query strings) |

The real data has **no click, type, form fill, upload or download**: those sessions only looked at pages. The shapes
below come from the tool schemas loaded in this session.

### Shapes of the actions that matter (from schemas)

| family | navigate | type | click | upload / download |
|---|---|---|---|---|
| `mcp__Claude_Browser__*` (built-in) | `navigate {url}` | `computer {action:"type", text}`, `form_input {ref, value}`, `computer {action:"key", text:"cmd+Return"}` | `computer {action:"left_click", coordinate:[x,y] \| ref:"ref_N", action_summary}` | no tool; a download is a `navigate` or a click |
| `mcp__claude-in-chrome__*` (user's Chrome) | `navigate {url}` | same `computer` shape, `form_input` | same as above (`coordinate` or `ref`, `action_summary`) | `file_upload {paths[], ref}` |
| `mcp__plugin_chrome-devtools-mcp_*` | `navigate_page {type, url, initScript}` | `fill {uid, value}`, `type_text {text, submitKey}` | `click {uid, dblClick}` | `upload_file {uid, filePaths[]}` |

Also code execution: `javascript_tool {text}` / `evaluate_script`, and `navigate_page.initScript`.

## 2. Can the hook intercept them?

- **Matcher: yes.** Claude Code's hook docs say a PreToolUse matcher is a regex on the tool name and MCP tools are
  named `mcp__<server>__<tool>`, so `mcp__Claude_Browser__.*|mcp__claude-in-chrome__.*` works. The hook gets the same
  JSON as for Bash: `tool_name`, `tool_input` (the fields above), `cwd`, `session_id`, `transcript_path`.
  *Not tested locally* (settings files are write-protected in this sandbox). Also unverified: whether the desktop app's
  built-in browser tools go through PreToolUse hooks like other MCP tools.
- **laya-guard today:** `install claude-code` writes matcher `Bash`; `--files` writes
  `Bash|Read|Edit|MultiEdit|Write|NotebookEdit|Glob|Grep`; `--matcher` accepts any regex, so browser tools can be
  added with no code change.
- **What the hook sends for a non-Bash, non-file tool** (`hooks/guard-hook.sh`): `tool_call = "<tool_name>: " + the
  input's values joined by spaces`, plus `tool_name`. Nested values (`browser_batch.actions`) are flattened with
  Python's `str()`. The server (`server._judge_and_log`) sees it isn't a file tool and runs the normal shell `judge()`
  on that string: regexes, safe lane, then the model (trained on shell commands). Path checks (`protect.yaml`) only
  run for the file tools, so `upload_file {filePaths:[~/.aws/credentials]}` is not path-checked.

## 3. What comes back today (scratch run, `LAYA_GUARD_MOCK=1`, fake values)

`judge(ask=True)` = the server with the mock scorer, not the real model. `judge_offline` = the hook with the server down.

| input | server (mock) | offline |
|---|---|---|
| navigate to an unknown domain | allow | review |
| navigate to localhost | allow | review |
| navigate to `file://…/.ssh/id_rsa` | review | review |
| type a GitHub-token-shaped string | **block** (`secrets-leak`) | **block** |
| type an AWS-key-shaped string | **block** (`secrets-leak`) | **block** |
| `form_input` with a password-like value | allow | review |
| click `ref` + summary "Pay now" / "Place order" / "Delete account" | allow | review |
| click with summary "결제하기" | review (mock keyword) | review |
| click with coordinates only | allow | review |
| key `cmd+Return` | allow | review |
| navigate to a `.dmg` | allow | review |
| `javascript_tool` sending `document.cookie` out | allow | review |
| `browser_batch` [navigate, type token] | **block** | **block** |
| devtools `upload_file` of `~/.aws/credentials` | review (mock keyword) | review |

What works: secret-shaped typed text is blocked, also inside a batch. Everything else is luck: the mock allows almost
all of it, offline asks about all of it (nothing is in the safe lane), and the real model has never seen this input
shape (not run here). So today the result would be either too quiet or too noisy.

## 4. What makes a click undecidable

- `coordinate: [x, y]` alone: nothing in the call says what is under the pointer.
- `ref: "ref_31"` / `uid: "e12"`: ids into a page snapshot the hook never sees; ids are only valid for that page.
- `action_summary` (built-in browser and Chrome extension only) is the one readable hint, but it is written by the
  agent itself, is optional, and can be wrong or omitted. Usable as a trigger for "ask", never as a reason to allow.
- chrome-devtools `click {uid}` has no summary at all.
- `key` like `Return` / `cmd+Return` can submit a form just like a click.

So a rules-only first stage can't tell "Pay" from "Next" by itself. Options later: read the latest page snapshot from
the transcript (`transcript_path`) to resolve `ref`/`uid` to its label, or ask on every click on a not-allowed site.

## 5. Proposed minimal first rule set

New input-aware path in the server (like `judge_file_call`): pick fields by tool name instead of judging the flattened
string with the shell rules. Site list = localhost/`*.localhost`/`*.test` + a user list in a new `browser.yaml`.

| action | allow | review (ask) | block |
|---|---|---|---|
| **navigate** (`url`) | listed sites, localhost | unknown domain; `file://`; `javascript:`/`data:` URLs; URL with token-like query param | URL containing a secret-shaped string |
| **type** (`computer type`, `form_input`, `fill`, `type_text`) | ordinary text on a listed site | any text into an unknown site's field when it looks like a password/card/ID number | secret-shaped text (existing `secrets-leak` regexes), anywhere |
| **click / key** | on a listed site with no risky summary | `action_summary` or label matches pay/order/buy/delete/remove/send/submit/confirm (+ 결제/주문/삭제/전송); any click on an unknown site; coordinates-only on an unknown site; `Return` key on an unknown site | — (not decidable from the call) |
| **download / upload** | — | navigate to an executable/archive extension; any `file_upload`/`upload_file` | upload of a protected path (reuse `protect.yaml`) |
| **run JS** (`javascript_tool`, `evaluate_script`, `initScript`) | listed sites | everything else | JS reading `document.cookie`/storage and calling `fetch`/`XMLHttpRequest` to another host |

Also needed: unpack `browser_batch.actions` and judge each step (worst verdict wins), and skip the shell model for
these tools. Read-only actions (`screenshot`, `zoom`, `find`, `get_page_text`, `read_page`, `read_console_messages`,
`resize_window`, `tabs_*`) → allow.

## Not checked

- No local hook test (settings write-protected here); built-in browser + hooks unverified.
- Real model not run on these inputs (mock only).
- Real data is one session of 23 read-only calls; click/type shapes come from schemas, not real use.

## 6. Follow-up: look up the screen at hook time (desktop computer use)

A coordinates-only click is undecidable from the call, but the hook runs on the same machine right before the click,
so it can ask macOS what is there: `AXUIElementCopyElementAtPosition(x, y)` gives the element's role, title, and for a
Finder item its file path (`AXURL`); the frontmost app's `AXFocusedUIElement` says where typed text goes. Rules could
then judge "double-click on a file under `~/.ssh`" or "click on a button titled 결제하기" with no model.

Probe: [`axprobe/axprobe.swift`](axprobe/axprobe.swift) (prints role/title/path of the element under the mouse and its
parents; typed values as length only).

Result so far (2026-11-03): compiles and runs, but returns nothing here: `AXIsProcessTrusted=false`, AXError -25211
(API disabled) outside the sandbox, -25200 inside it. The Claude app that runs the hook has no Accessibility
permission. Granting it is the user's call (a security setting).

Second run, by the user with the permission granted: `AXIsProcessTrusted=true`, the lookup works (app name and bundle
id, the focused element, the element at the point and its parents). The mouse stayed over the Claude desktop app
(Electron): every element was an untitled `AXGroup`; the only label was a parent's description ("bash 코드"), and the
focused element's ("채팅 메시지"). So web-content apps give little at the leaf; the label, when there is one, is up the
parent chain.

Third run (Finder list view, Chrome, Claude app):
- **Finder: the file path is there.** With the pointer on a file's name, the element is an `AXTextField` with `AXURL`
  (full path) and `AXFilename`. On the rest of the row (other columns, padding) it is an `AXCell`/`AXRow` with no
  path; the name cell is a child of the same `AXOutlineRow`, so the lookup has to go up to the row and down to its
  name cell. That is enough for "double-click on a file under a protected path → block/ask".
- **Chrome web page:** like Electron, untitled `AXGroup`s; the nearest label was a landmark region's title. Buttons
  and inputs inside the page not checked.
- One point gave no element (AXError -25200).
- Native app buttons (System Settings etc.): **not tested**.

The probe now prints file names as `<name len=N>.ext`. The raw output of this run showed real file names, so it
stays out of this note.

Design note: whatever holds the Accessibility permission can also drive the UI. Granting it to the terminal or the
Claude app would hand the same power to the agent's own shell commands. Better: a separate laya-guard helper (or the
server process) holds it and the hook asks it.

Open points: screenshot-to-screen coordinate scaling, apps with poor accessibility data (canvas apps, some web
content) → ask, and the short race between lookup and click.

## 7. Live check of the first stage (2026-11-03)

The rules in `src/laya_guard/browser.py` were tried in a real Claude desktop session, with a project-local hook on the
browser tools only (matcher `browser.BROWSER_MATCHER`, the branch's offline judge, a logger that kept field names
only). All three calls went through the hook:

| call | verdict | what happened |
|---|---|---|
| `mcp__Claude_Browser__navigate` to an unlisted site | review | the permission prompt appeared; the user allowed it |
| `mcp__Claude_Browser__computer` screenshot | allow | ran |
| `mcp__Claude_Browser__computer` type of a fake token | block (`secrets-leak`) | cancelled, the agent saw the reason |

So the PreToolUse matcher does reach the desktop app's built-in browser tools, and both `ask` and exit 2 work there.
The hook input also carries `transcript_path`, `session_id`, `tool_use_id` and `mcp_server` next to `tool_input`
(`type` came with `action_summary`, as in the schema). `transcript_path` is the way to resolve a `ref` to its label
from the last page snapshot later. Not tried live: Claude in Chrome, chrome-devtools and Playwright MCP.

## 8. Desktop computer use: live check (2026-11-03)

`src/laya_guard/desktop.py` judges computer-use calls (Anthropic's `computer` tool shape) by asking the
`laya-guard-ax` helper what is at the click's coordinates. Checked with a Finder window showing a fake key file and an
ordinary file, a trial rule blocking the fake key, and a laya-guard server on port 8788:

| call | what was under the point | verdict |
|---|---|---|
| `double_click` on the fake key's row | Finder `AXTextField` with the file's `AXURL` | **block** (`protected-path`), hook exit 2 |
| `double_click` on the ordinary file's row | Finder row, ordinary path | allow |
| `double_click` on the fake key's old coordinates after the Claude window covered them | Claude window | allow (correct: that click would land on Claude, not the file) |

Learned on the way:
- **Accessibility follows the responsible app.** A command typed with `!` in Claude, or in Claude's terminal panel,
  runs under the Claude app, and so does a server started there. Granting Terminal does nothing for those. A real
  setup needs the server started by launchd (`laya-guard service install`) and Accessibility granted to that, not to
  the Claude app. Not tried yet.
- The lookup reads what is on top at that moment, which is what the click will hit. Coordinates the agent got from an
  older screenshot can point at something else by the time it clicks; the verdict follows the screen, not the
  screenshot.
- The desktop app did not load the trial folder's `.mcp.json`, so the call came from the hook script fed the same
  JSON Claude Code sends (that shape was confirmed live with the browser tools in section 7), not from a live session.
- Not tried: a real computer-use MCP server and its coordinate scale (`LAYA_GUARD_AX_SCALE`), Return/cmd+O on a
  selection, desktop icons, Open/Save dialogs.
