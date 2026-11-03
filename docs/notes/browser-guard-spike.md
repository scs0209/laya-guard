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
