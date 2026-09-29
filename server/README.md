# OpenMousse server

[中文](README.zh-CN.md) · **English**

Thin API layer (FastAPI): auth, chat relayed to your claw (OpenClaw's Gateway, or any OpenAI-compatible API, see [Other claws](#other-claws)), Agent creation and deletion, board data, attachments and voice, push notifications, and hosting of the web build.

## Install

The one-line installer at the repository root (`install.sh`) does all of the below, including the systemd service. By hand:

```bash
cd ~/openmousse/server
pip install -r requirements.txt
mkdir -p ~/.openmousse && cp server.example.json ~/.openmousse/server.json   # edit paths and timezone
python3 tokens.py add phone      # generate an access token for the app's connection page
python3 run.py                   # or install as a systemd service, see openmousse-server.service.example
```

Every field of `server.json` is documented at the top of [`config.py`](config.py). Changing tokens or adding Agents needs no restart; changing the bind address does. `python3 settings_ctl.py user-name <name>` sets what prompts to the model call you (`user_name`, no restart) and records it as one line in the profile USER.md; the main chat runs it while showing a new user around.

## Auth

`/api/*` requires `Authorization: Bearer <token>` (`X-API-Key` also works; `?token=` only on GET for files: `/api/files/…`, Think attachments and saved originals, podcast audio — for image views and the web build's `<audio>`, which can't send headers). No credentials → 401. Two token-free doors are off by default: `auth.tailscale_nodes` (a whitelist of Tailscale device names; needs tailscale on this machine) and `auth.trust_loopback` (never enable it when a reverse proxy runs on the same host). The web build's static files are public. The `mcp` and `mcp-<agent id>` tokens only work on `/mcp` (403 on `/api`, so a claw holding only that token can't approve its own inbox cards or pair devices), and `sentinel` only on `/api/egress`; local scripts (`*_ctl.py`, `safe_restart.py`, `check.sh`) use the first token that isn't one of those, whatever the order in `server.json`.

## Letting the phone connect

- **Tailscale** (least effort): bind the service to the Tailscale address, install Tailscale on the phone, enter `http://100.x.x.x:8080` in the app.
- **Pairing code** (no token to copy): `python3 tokens.py pair` makes a one-time code ([`pairing.py`](pairing.py): 10 minutes, one use, only its hash is stored, too many wrong tries lock it for 10 minutes), draws a QR code in the terminal and prints an `openmousse://pair?s=<address>&c=<code>` link. Scan it with the phone's camera or tap the link → the app's connect page has the address and code filled in → **Connect with the code** → `POST /api/pair` hands the app a new token of its own (`device-<device>-<time>`, removable with `tokens.py remove`). `--json` is for claws (see [`docs/connect.md`](../docs/connect.md)). `/api/pair` is under `/api`, so only on the Tailscale network. The installer makes one at the end (30 minutes).
- **Devices** (app: Settings → your claw): `GET /api/devices` lists the tokens people use (names only; program tokens like `mcp`, `mcp-<id>` and `sentinel` are left out), `DELETE /api/devices/{name}` revokes one (not the one making the request), and `POST /api/pair/new` `{server, name}` makes a pairing code for another device from an app that's already connected (link + QR as an SVG path, same rules as `tokens.py pair`).
- **Public HTTPS**: `tailscale serve` / `tailscale funnel`, or Caddy / nginx reverse-proxying to 127.0.0.1:8080; enter `https://your.domain` in the app.

## Other claws

By default the server talks to OpenClaw. For any other claw or agent with an OpenAI-compatible chat API, add a `claw` section to `server.json` (the installer writes it when you give it a URL instead of pressing Enter at "your claw"):

```json
"claw": {"kind": "openai", "name": "My claw", "url": "http://127.0.0.1:8642/v1", "token": "…", "model": "default"}
```

- `url` goes up to `/v1`; the server POSTs to `<url>/chat/completions` with `stream: true` (a plain JSON reply works too) and checks `<url>/models` on the Connections page. `token` (or `token_env`, a variable name looked up in the environment and in `env_file`) is sent as a Bearer token. `models` lists what the app may switch between; `headers` adds headers your claw needs.
- `session` says how a conversation continues: `{"mode": "history", "turns": 40}` (default) sends today's messages of that conversation every time, for APIs with no sessions of their own (a model's API, say); unsending just drops messages here. Claws that keep sessions get only the new message and a session key: `{"mode": "header", "header": "X-Session-Id"}`, `{"mode": "body", "field": "session_id"}` or `{"mode": "user"}`. The key is `mousse:<conversation>:<day>`, a new one every day at 04:00 (most claws never reset on their own; the daily digest carries yesterday over); `"daily": false` keeps `mousse:<conversation>`. `"model": ""` leaves `model` out of the request.
- Presets ([`claw_presets.py`](claw_presets.py)), which the installer fills in when you answer with the name: **Hermes Agent** (`hermes`: port 8642, `X-Hermes-Session-Id`, the token read from `API_SERVER_KEY` in `~/.hermes/.env`, skills in `~/.hermes/skills`), **nanobot** (`nanobot`: port 8900, `session_id` in the body, no `model`), **Letta Code** (`letta`: port 4500, `x-letta-chat-key`, the agent's name as `model`).
- Not yet: claws without an OpenAI-compatible chat API (ZeroClaw's webhook, Moltis's RPC, NullClaw's A2A, PicoClaw's WebSocket, Agent Zero, NanoClaw, TinyAGI) and IronClaw's Responses API need a driver of their own.
- Agents: each Agent is a conversation of its own with the same claw. Its first message of the day carries its name and role, and the last daily digest.
- Daily digest: at 03:45 `daily_close.py` asks each conversation that talked today for 5–10 lines on the day; the server keeps the reply in `<data_dir>/digest/<conversation>/<day>.md` and puts it in front of the next day's first message.
- Skills and rules: give the installer your claw's skills folder and the rules file it reads every turn (AGENTS.md or similar), and OpenMousse's skills are linked in and a short rules section appended. Skills run `python3 ~/.openmousse/repo/server/…_ctl.py`, so your claw needs a shell tool to use them. A claw that can connect to MCP doesn't need that: it can use `/mcp` instead (see "MCP" below).
- `/api/health` reports `claw: {kind, name, caps}`; the app hides what the claw can't do: cutting into a running reply, background tasks, exec approvals, OpenClaw scheduled jobs, model billing, per-Agent workspaces. Those endpoints answer with empty lists instead of calling `openclaw`.

## MCP: the features without a shell

The server has an MCP endpoint at `/mcp` (Streamable HTTP, stateless, [`mcp_bridge.py`](mcp_bridge.py)) through which a claw uses boards, the inbox, goals, projects, the schedule, Agents, the background-task allowance, nightly proposals, the journal, handing off to an Agent, what to call the user, and the memory tree. Sandboxed Agents, Hermes on a docker or ssh terminal backend, and claws hosted in the cloud can't run the skills' commands; they can use this.

- Address: `http://<server>/mcp/<token>` (token in the path), or `/mcp` with `Authorization: Bearer <token>`. The token is the one named `mcp` under `auth.tokens` in `server.json` (the installer creates it; `python3 tokens.py add mcp` works too); `/mcp` accepts no other token. A token named `mcp-<agent id>` is bound to that Agent and can only act for it.
- Tools: each one bridges an existing command, written exactly as in the skills: `args` = the words after the script name (one word per item, no shell, no quoting), `input` = what would go to standard input, `agent` = which Agent you are (the command runs in its workspace and boards and the inbox know it's that Agent; Agents of other claws, which have no workspace, are recognised too). Each call starts a subprocess (about 0.1 s), at most 4 at a time, 120 s at most (handoff: 300 s).
- OpenClaw: the installer adds `openmousse` under `mcp.servers` in `openclaw.json` (the Gateway hot-reloads it, no restart); the tools are named like `openmousse__board`. The skills' commands still work; both paths exist side by side and each skill says to prefer the tools when they're there.
- Other claws: the installer prints the address and how to add it to that claw (Hermes's `mcp_servers`, nanobot's `tools.mcpServers`, Letta Code's `/mcp add`). An Agent's note carries its id, to put in `agent`.
- `mcp.scripts` in `server.json` replaces or adds the command behind a tool (`{"journal": ["python3", "~/…/my_journal.py"]}`; `null` = don't offer it); restart the server after changing it.
- The tools of the apps you connect (see [Connectors](#connectors-apps-over-mcp)) are listed after these, named `<app id>__<tool>`; connecting, removing or changing a permission needs no restart.
- Context per turn: the 11 tool definitions come to about 7,500 characters, about the same as the 11 skill descriptions.

## Agents

"New Agent" in the app = a separate agent in your OpenClaw: its own workspace (AGENTS.md / IDENTITY.md / MEMORY.md), skill allowlist, routing. See [`agents.py`](agents.py). From the command line:

```bash
python3 agent_ctl.py list
python3 agent_ctl.py create --name Sleep --purpose "Interpret last night's sleep every morning." --icon moon --color purple
python3 agent_ctl.py update g-xxxxxxxx --name "Sleep & recovery" --color default   # only the fields you give change
python3 agent_ctl.py delete g-xxxxxxxx      # workspace archived to ~/.openclaw/archive/, never deleted
```

With `packs/core/skills/agent-builder` installed on the main agent (the installer does this) you can create Agents from chat.

| Endpoint | What it does |
|---|---|
| `GET /api/groups` | Every Agent: `{id, name, icon, color, purpose, modelId, dashboard, lastLine}`. `color` is null when unset (the app's default color) |
| `POST /api/groups` | `{name, purpose?, icon?, color?, model, skills?}` → `{ok, id}`: builds the OpenClaw agent, then the row; if a step fails nothing is left behind |
| `PATCH /api/groups/{id}` | `{name?, icon?, color?, purpose?, model?}` → `{ok, group}` (`group` has the same shape as in GET). Only fields you send that actually differ change; `color: null` = back to the default |
| `DELETE /api/groups/{id}` | Removes the OpenClaw entry and the routing; the workspace moves to `archive/` |

POST and PATCH check the same things: the name can't be empty (400) or the same as another Agent's, ignoring case (409); `icon` is an icon key, lowercase letters and hyphens, at most 24 characters (the app ships moon, dumbbell, utensils, book, wallet, briefcase, heart, plane, coffee, music, camera, code, cart, home, car, paw, leaf, gamepad, palette, globe, graduation, lightbulb, trophy, pill); `color` is one of cyan, gold, green, purple, pink, orange (400 otherwise).

What a PATCH touches besides the database:

- **icon, color**: nothing else.
- **name or purpose**: the Agent's `IDENTITY.md`, so the agent knows. Only the block between `<!-- mousse:role -->` and `<!-- /mousse:role -->` is replaced: a "Role (set in the app; this wins)" section with the name and the purpose. Everything outside the markers, hand-written or written by the agent, stays byte-for-byte as it was. No block yet → it's appended at the end (the file is created if missing); new Agents get it from the start. The old file is copied to `backup_dir` first.
- **model**: the Agent's default model in `openclaw.json` (`agents.entries.<id>.model`, nothing else in the file), with the same steps as creating an Agent: back up, write, `openclaw config validate`, restore on failure. An existing `{primary, fallbacks}` keeps its fallbacks; an Agent that followed the default model gets the default fallback chain copied, so it keeps falling back. An id without its own entry (such as main) → 400. The app's thread for this Agent switches to the new model too.
- Everything is checked before anything is written. If the model can't be written (502), `IDENTITY.md` is put back and the database is unchanged.

## Needs your OK (the inbox)

Anything an Agent needs your OK for (mostly: its own ideas, things that reach other people or can't be undone, new scheduled jobs and notifications, code and config changes) goes into the inbox, listed under "Needs your OK" on the app's Today page. OpenClaw's exec approvals (`openclaw approvals pending`) are merged in, with ids of the form `exec:<approval id>`. See [`inbox.py`](inbox.py); Agents follow the rules in `packs/core/skills/inbox`.

```bash
python3 inbox_ctl.py add --kind send --source apply --title "Reply to HR to confirm Wednesday's interview" --why "HR asked Wed or Thu" --change "Email hr@…" --dedupe apply:hr:0926
python3 inbox_ctl.py done ib-xxxxxxxx --result "Sent"
python3 inbox_ctl.py list [--status recent]
```

| Endpoint | What it does |
|---|---|
| `GET /api/inbox?status=pending` | Items waiting for you (anything past its `expiresAt` is marked expired first) plus OpenClaw exec approvals, newest first |
| `GET /api/inbox?status=recent` | Items decided or finished in the last 7 days, at most 50 |
| `GET /api/inbox?thread=<thread>` | That thread's items (waiting for you + decided or finished in the last 7 days), oldest first: the chat shows them as cards in its timeline, under the reply given by `messageId` |
| `GET /api/inbox/{id}` | One item |
| `POST /api/inbox` | An Agent submits `{kind, title, source?, thread?, why?, changes?, detail?, approveLabel?, level?, dedupe?, expiresAt?}` → `{ok, id}`. Same `dedupe` as a pending item → updated in place (`updated: true`, pushed again); rejected in the last 30 days → 409 `{ok: false, error: "rejected_before", rejectedAt, note}` |
| `POST /api/inbox/{id}` | Your decision: `{action: approve / reject / revise, note?}`. Approve → a "【收件箱】Approved …" message goes to the item's thread so the Agent does it (if that thread is mid-reply, it waits until the reply is done); for `exec:` items approve = allow-once, reject = deny |
| `PATCH /api/inbox/{id}` | The Agent resubmits a revised item: back to pending, pushed again |
| `POST /api/inbox/{id}/result` | The Agent reports `{status: done / failed, result}`; a quiet notification follows |
| `POST /api/inbox/{id}/withdraw` | The Agent withdraws an undecided item |

Asking for changes: reply to the Agent in its chat with the card quoted; `/api/chat/send` carries `inboxId`, the item becomes revising with your words as its note, and the model also sees which item you're replying to and how to resubmit (the chat history shows only your words).

Following up: the same `inboxId` on an item that is already settled (Handled → an item → Follow up) records `followedAt` / `followNote` instead; done and failed items go back to approved (in progress), and the model sees the item, its status and result, and how to report again with `done` / `fail`. Declined, withdrawn and expired items keep their status; the model is told to submit a new item if it should happen after all. Items also carry `day` (the logical day they were raised on), which the app uses to open that day's history at the message.

Item: `{id, kind, source, sourceName, thread, title, why, changes: [], detail, approveLabel, fields?, status, note, result, level, createdAt, updatedAt, decidedAt, expiresAt, messageId}`. `messageId`: an item an Agent submits during a reply is attached to that reply (`messages.id`) when the reply finishes; items submitted outside a reply have null. kind: task / write / send / spend / schedule / push / skill / agent / block / code / calendar / app (a connector call waiting for your OK, see Connectors) / other (exec only comes from OpenClaw and carries `fields`); status: pending / approved / rejected / revising / done / failed / withdrawn / expired. The old `/api/approvals` endpoints still work for older app builds.

## Chat: queueing, stopping, quoting

- A conversation has one reply running at a time. What you send while it's replying (`/api/chat/send`) no longer gets a 409: it's stored right away (status `queued`, shown as "Queued" in the app), the SSE answers with a `queued` event and the connection waits. When the reply ends (or is stopped), the queued messages go to the model as one turn (numbered, after a note asking it to start each separate answer with its own line `> 「their words」`; the app draws that as a quote that jumps back to the message), and every waiting connection attaches to that turn. On startup, messages still queued from before a restart are sent if they're under 30 minutes old, older ones are marked as not sent. System triggers (`/api/chat/trigger`, relays) still get a 409 when the thread is busy.
- `POST /api/chat/stop` `{thread}` stops the reply in progress by closing the connection to the Gateway, which aborts the turn. What was already said stays, with "(Stopped)" appended, status `stopped`, no push. Queued messages go out next.
- Long-press → Quote: `/api/chat/send` carries `replyTo` (`db<id>`). The model gets the quoted line as context, the message stores `reply_to`, and `/api/chat/history` returns `replyTo {id, role, text}` for the app to show above the bubble.
- With the Gateway's WebSocket chat channel (server.json `chat.transport: "ws"`, see `gateway_ws.py`), what you send mid-reply is not queued but **cut in**: `chat.send` with `queueMode: steer` hands it to the model at the turn's next step, and it stays one reply (the message is stored as `steered`, shown as "Cut in"). If the Gateway couldn't steer it and ran it as its own turn, the server adopts that turn as this message's reply. Stop uses `chat.abort`. Turns run to completion inside the Gateway: a reply still running when the server restarts is adopted or recovered from `chat.history` on startup. Messages with images still go over HTTP (and queue). The first connection pairs automatically on loopback; the device identity lives in `<data_dir>/gateway-device.json`.
- **When a background task finishes** (see [`settle.py`](settle.py)): OpenClaw runs one more turn in the conversation that started the task so the model can report back (run id `announce:v1:<sub-session>:<run>`, or `announce:requester-settle:…` when the starting turn yielded or several tasks settle together). Its reply is delivered to the channel the task came from, which for the app used to mean only the Gateway's own web UI. Now, for tasks started from the app (`requesterOrigin.channel` = webchat) in a thread the app knows, the chat gets a grey line "Background task finished: <title>" (role `auto`) followed by that turn's reply. Over the WebSocket channel the server adopts the turn live (the reply streams like any other, cards from that turn hang under it); on both transports a catch-up pass every 30 s reads `chat.history` for tasks delivered in the last 30 minutes and fills in any it missed (HTTP transport, restarts, dropped connections). `settle_replies` in grava.db records which turns were handled, so nothing is written twice. A task that finishes while the turn that started it is still running is folded into that turn by OpenClaw, and there's nothing extra to add. Tasks started from Telegram are answered there. No extra push: the quiet "task done" push already covers it; the reply counts as unread.
- `GET /api/chat/busy` → `{running, queued, idle}`. To restart the server use `python3 safe_restart.py --unit <service>`: it waits until no reply is running and nothing is queued (up to 10 minutes), since a restart cuts off replies in progress.

## Attachments

Files you attach in a chat (`POST /api/chat/upload`, up to 10 per message, 30 MB each; see [`files.py`](files.py)) open in the app instead of the browser ([`preview.py`](preview.py)):

| Route | What |
|---|---|
| `GET /api/files/{id}` | The original. `thumb=1`: 512 px thumbnail; `preview=1` (images): the original if the web can show it and it's under 8 MB, else a JPEG up to 3000 px (HEIC, TIFF, BMP); `inline=1`: shown in the browser instead of downloaded, only for images, PDF, plain text, audio and video (never HTML or SVG) |
| `GET /api/files/{id}/preview` | How to show it: `view` = `image` / `pages` (PDF, EPUB, XPS, CBZ, SVG: page sizes) / `doc` (blocks of Markdown and tables: Word, PowerPoint with notes, Excel and CSV sheets up to 200 rows × 26 columns, Markdown, notebooks) / `text` (code, JSON pretty-printed, logs, a zip's file list, a web page's text) / `audio` (with the transcript) / `video` / `none`, plus a `label` like "PDF · 3 pages" |
| `GET /api/files/{id}/page/{n}?w=1200` | Page n as a JPEG, width rounded up to 800 / 1200 / 1600 / 2000, rendered once and kept next to the file |

Rendering pages needs PyMuPDF (in `requirements.txt`); without it a PDF shows the text extracted at upload. Thumbnails, previews and pages are sent with `Cache-Control: private, max-age=604800` (a file never changes; every other `/api` answer stays `no-store`). Transparent images get a white background when they become JPEG (thumbnail, preview, and the copy the model sees).

## Handoff and task cards

When the main chat hands a question to an Agent (`scripts/ask_agent.py` → `/api/chat/relay`) or starts a background task (OpenClaw `sessions_spawn`), the chat shows a card. See [`cards.py`](cards.py).

- **Handoff card**: appears as soon as the question goes out ("asking Diet…", with the question as sent), turns into "handed to Diet · 34 s" when the answer is back, and sits under the reply that asked. Tap it to open the Agent's chat at that question. The relay run itself is recorded in `handoffs` (grava.db); the reply that asked is the one mid-reply when the relay arrives (the script can't tell which session called it; main-agent threads win, then the newest). A relay refused with 409 (the Agent is busy) is recorded as `busy`.
- **Task card**: read from OpenClaw's task ledger (`<openclaw_home>/state/openclaw.sqlite`, tables `task_runs` / `subagent_runs`, read-only, a few ms per read instead of a 2-second `tasks.list`). While a reply is running the server looks every 2 s for tasks that session started and sends them over the same SSE stream (`event: card`), so the card shows up mid-reply; when the reply ends they are attached to it (`task_links`). Running: the step it's on ("reading L2.pdf", from the sub-session's transcript, read at most every 20 s). Done: the start of the result. "Revise" sends notes to the same sub-session (`POST /api/tasks/{id}/revise`); rounds, notes and each round's result come from the `task:<id>` thread.
- **Allowance**: `server.json` → `tasks: {daily_limit: 10, max_minutes: 30, notify_done: true}`. It's a number for the Agents to check (`tasks_ctl.py quota`, exit code 3 when used up); what actually stops a long task is OpenClaw's `agents.defaults.subagents.runTimeoutSeconds`.

```bash
python3 tasks_ctl.py quota   # started today, the limit, how many are left
python3 tasks_ctl.py list
```

| Endpoint | What it does |
|---|---|
| `GET /api/chat/cards?thread=&day=` | `{cards, incoming, tasksAvailable}`: that thread's handoff and task cards for the day (from 04:00), oldest first, each with `messageId` (the reply it belongs under; null while that reply is still running); `incoming` = handoffs other threads sent to this one |
| `GET /api/tasks/quota` | `{today, running, limit, left, maxMinutes}`; `today` / `left` are null when the ledger can't be read |
| `GET /api/tasks` | now also `quota` (plus `tokens` used today), and per task `minutes`, `timedOut`, `step` |

Handoff card: `{kind: "handoff", id, thread, messageId, createdAt, status: running / done / error / busy / lost, to, toName, from, fromName, question, seconds, relayId, replyId, error}`. Task card: `{kind: "task", id, thread, messageId, createdAt, status (进行中 / 完成 / 失败 / 已取消), timedOut, title, deliverable: [], modelId, minutes, startedAt, finishedAt, tools, step, result, error, round, roundStatus, note, roundResult, tokens, limitMinutes, seq, dailyLimit}` (`seq` = the how-manyth task started today). `deliverable` is the task text's "要交：" / "Deliverable:" line plus the list under it.

## Schedule and "To remember"

One timeline per day and one list of things to remember, both editable. See [`schedule.py`](schedule.py).

- **Schedule** = the timetable (the optional `calendar` source, read-only) + a layer the server keeps itself (`schedule_items` in grava.db: the user's own plans and the ones Agents put in) + deadlines due that day, each with a tick. The timetable can't change, but this layer can mark a class "not going" (`series` = every week), move its place, add a note. Past days record what actually happened: went / didn't, done / didn't, actual times (a workout planned with key `<agent>:training:<date>` gets its actual time from the workouts source).
- **To remember** = coursework deadlines (`study.deadlines_cmd`) + things pulled out of email (`remember.mail`, below) + application deadlines (`applications`) + the user's own deadlines. Each thing shows up once: the day it's due it moves into that day's timeline. Groups: `security` (pinned), `overdue`, `tomorrow`, `week`, `later`, `nodate`, `news` (undated money / status mail).
- **Tick** = done or not needed (`schedule_marks.done_at`): gone from the list, and scripts that remind (Grava's watcher reads `schedule_marks`) stop mentioning it. Ticking a mail item also runs `remember.mail.cmd --done <id>` (untick: `--undo`); fixing one (`/api/remember/edit`) runs `--edit <id> --json …`.
- **Changes by Agents** (via `schedule_ctl.py`, which calls this API with `source`): while an Agent is replying, a `schedule` card goes out on its SSE stream; when the reply ends the change is attached to it (`schedule_log.message_id`). Every change can be undone (`/api/schedule/undo/{id}`). An Agent re-adding with the same `key` updates that item, but never overrides a time the user moved himself.
- **iPhone calendar**: `GET /cal/<token>.ics` is outside `/api` and needs no token header; the token in the link is the password (rotate it in the app). Four switches: classes (off by default, so a timetable already on the phone isn't doubled), yours and the Agents', deadlines, email events.

```bash
python3 schedule_ctl.py day [--date tomorrow] [--days 3]
python3 schedule_ctl.py remember
python3 schedule_ctl.py add --title "Workout · Push A" --date today --start 17:30 --end 18:30 --key fitness:training:2026-09-28
python3 schedule_ctl.py skip "ics:2026-09-28T13:00|Office Hours" --every-week
python3 schedule_ctl.py done "mail:1a0d…:todo"
python3 schedule_ctl.py undo 42
```

| Endpoint | What it does |
|---|---|
| `GET /api/schedule?from=&days=` | The merged timeline (1–14 days from `from`, default today). Each entry: `id` (the ref to change it: `item:` / `ics:` / `canvas:` / `mail:` / `app:`), `kind`, `origin`, `title`, `date`, `start`, `end`, `allDay`, `badge`, `by`, `link`, `done`, `skip`, `series`, `attended`, `actualStart`, `actualEnd`, `clash`, `past` |
| `POST /api/schedule` | Add `{title, date, start?, end?, kind: event/deadline, location?, note?, key?, source?}` |
| `PATCH /api/schedule/{id}` / `DELETE` | Change / delete your own (soft delete, undoable) |
| `POST /api/schedule/mark` | A class: `{ref, skip?, series?, location?, note?, attended?, actualStart?, actualEnd?}` |
| `GET /api/remember?all=` | To remember, grouped (`all=1` includes ticked ones) |
| `POST /api/remember/done` | `{ref, done}`: tick / untick |
| `POST /api/remember/edit` | `{ref, title?, due?, detail?, type?}`: fix a mail item (or your own deadline) |
| `POST /api/schedule/undo/{log}` | Undo a change (`{redo: true}` to redo) |
| `GET/POST /api/schedule/feed` | The iPhone subscription: `{path, include}`; `{include}` to switch categories, `{rotate: true}` for a new link |

`server.json` → `remember.mail` (optional): `{"items": "<the JSON the mail extractor writes>", "cmd": ["python3", ".../mail_digest.py"], "sources": {"<key>": "<label>"}, "link": "https://mail.google.com/mail/?authuser=…#all/{thread_id}"}`.

## Projects

Things with an end: they run for days or weeks and have a goal and deadlines (a group assignment, a job-hunt sprint). A project is a chat thread (the old "side chat", id `sc-…`) with a **project card** on top: goal, deadlines, next steps, decisions, progress, the tasks started inside it, and a summary once it's archived. See [`projects.py`](projects.py).

- **Deadlines live in the schedule layer**: your own are `schedule_items` rows (kind deadline, key `project:<id>:…`); existing ones (coursework, mail items, applications, other deadlines) are linked by their ref. Ticking, reminders and "To remember" all work the same, and `/api/remember` / `/api/schedule` entries carry `project: {id, title}`.
- **Carried across the daily reset**: on the first message of the day in a project, and after the card changes, `chat.start_run` puts the card in front of the message (the model sees it, the chat doesn't show it; `side_chats.fed_rev` / `fed_at`). The daily digest script sends each project "【自动触发】日结（项目）" so the Agent updates progress and next steps.
- **Changes by Agents** (`project_ctl.py`, which calls this API with `source`): a `project` card goes out on the reply's SSE stream and is attached to the reply at the end (`project_log.message_id`), undoable via `/api/projects/undo/{id}`. Deadline changes are schedule changes and show `schedule` cards.
- **Opening**: the app or an Agent asked by the user (`POST /api/projects`, optional `brief` handed over into the new project like a handoff). An Agent's own idea goes through the inbox (`POST /api/projects/propose` → kind `project`); approving it opens the project on the server and hands the brief over.
- **Archiving**: `POST /api/projects/{id}/archive {summarize}` moves it to Archived right away and, with `summarize`, sends "【自动触发】项目归档" into the project so the Agent writes the summary (`/conclude`) and its memory entries. `POST /api/projects/review` (called by the daily digest) asks once, through the inbox, to archive projects whose last deadline passed 3+ days ago.

```bash
python3 project_ctl.py list
python3 project_ctl.py create --title "Group project" --goal "…" --deadline "Rehearsal|2026-10-01 18:00" --link "canvas:…" --brief "…"
python3 project_ctl.py add sc-1a2b3c4d decision "Video under 8 minutes"
python3 project_ctl.py done sc-1a2b3c4d pi-5e6f7a8b
python3 project_ctl.py ask sc-1a2b3c4d "What's left before Friday?"
python3 project_ctl.py conclude sc-1a2b3c4d --done "…" --learned "…"
```

| Endpoint | What it does |
|---|---|
| `GET /api/projects?all=` | Projects that aren't archived (`all=1`: all), each with `goal`, `next` (nearest open deadline), `stepsLeft`, `hasSummary` |
| `POST /api/projects` | Open one: `{title, goal?, model?, deadlines: [{title, due} or {ref}], steps?, decisions?, brief?, source?}` |
| `GET /api/projects/{id}` | The card: `goal, progress, deadlines, steps, decisions, next, stepsLeft, tasks, summary, archived, closing, rev` |
| `PATCH /api/projects/{id}` | `{title?, goal?, progress?}` |
| `POST /api/projects/{id}/items` | `{kind: step/decision/deadline, text, due?, ref?}` |
| `POST /api/projects/{id}/items/update` / `…/delete` | `{id, text?, due?, done?}` / `{id}` (a linked deadline can only be ticked or unlinked) |
| `POST /api/projects/undo/{log}` | Undo a card change (`{redo: true}` to redo) |
| `POST /api/projects/propose` | An Agent's proposal → inbox kind `project` |
| `POST /api/projects/{id}/archive` / `restore` / `conclude` | Archive (`{summarize}`), restore, write the summary (`{done, decided: [], learned, saved}`) |
| `POST /api/projects/review` | Ask "archive?" for projects whose deadlines are all past (daily digest) |
| `GET /api/sidechats` | The sidebar list, now with the same summary fields per project |

## Goals

Long-term goals by area (health / study / career / finance: the category values are the Chinese words 健康 / 学业 / 职业 / 财务, the app translates them), editable by the user in the app and by the Agents from the command line. See [`goals.py`](goals.py).

- **A goal** = a row in `goals`: title, note, due (`YYYY-MM-DD`, `YYYY-MM` or words like "fall 2027"), an optional number target (`targetLow` / `targetHigh` / `unit`, one end is enough), an optional `metric` the server reads by itself (`bodyfat`, `weight`), the Agent that keeps an eye on it (`groupId`), and `status`: active / done / dropped. Nothing is ever deleted: "drop it" is `dropped`, folded at the bottom of the page.
- **Readings**: the body data source (`xunji.py`, one cached query for weight and body fat over 400 days, shared by `/api/goals` and the trend) comes first; Apple Health's daily averages (`health_metrics` BodyMass / BodyFatPercentage) are the cross-check. The current value is the latest of the two (the body source wins on the same day). Body fat is never calculated, only read. Progress runs from the start (the reading on or before the day the goal was set) to the target range, down or up; inside the range is 100 %. A reading older than 30 days is `stale`.
- **Every change** writes a `goal_log` row (who, before / after) and an activity line. Undo restores only the fields that change touched and nobody changed since (`kept` lists the rest; all of them changed since = 409); undoing an add hides the goal. Changes by Agents in the last 24 hours that the user hasn't dismissed come back as `recent`: the app shows them at the top of the Goals page with Undo.
- **Agents** use `goals_ctl.py` (`list`, `trend`, `log`, `add`, `update`, `done`, `drop`, `reopen`, `undo`; `--source` = who is changing it). The skill (`packs/core/skills/goals`): a change the user asked for is made at once; the Agent's own idea goes through the inbox first; no goals the user never stated.

```bash
python3 goals_ctl.py list [--all]
python3 goals_ctl.py add --title "Back under 75 kg" --category health --metric weight --low 72 --high 75 --due 2026-12-31 --agent fitness
python3 goals_ctl.py update bodyfat --low 14 --high 16
python3 goals_ctl.py drop goal-1a2b3c
python3 goals_ctl.py trend --metric weight
python3 goals_ctl.py undo 12
```

| Endpoint | What it does |
|---|---|
| `GET /api/goals?fresh=` | `{goals (active, with current, currentDate, currentSource, start, progress, direction, state, stale, daysLeft), closed (done / dropped), recent (Agent changes to undo), metrics}`; the first fields of each goal are unchanged, so older apps keep working. `fresh=1` re-reads the body data source if its cache is over 90 s old |
| `POST /api/goals` | Add `{title, category, detail?, due?, unit?, targetLow?, targetHigh?, metric?, groupId?, position?, source?}` |
| `PATCH /api/goals/{id}` | Only the fields given (`null` clears detail / due / unit / targets / metric / groupId), plus `status` and `position` |
| `POST /api/goals/undo/{log}` | Undo a change (`{redo: true}` to redo) |
| `GET /api/goals/log?limit=&goal=` | Recent changes, newest first |
| `POST /api/goals/seen` | `{ids}`: dismiss changes from the top of the Goals page |
| `GET /api/goals/trend?metric=weight&days=180&fresh=` | `{series: [{date, value, source: body / health}], summary: {latest, avg7, change30: {value, since}, check}, sources}`; nothing connected = an empty series, not an error |

## Boards, feature packs and reminders

Each Agent has its own tables and a dashboard of blocks it lays out itself; the app draws seven block types from the server-computed data and never computes. See [`boards.py`](boards.py), [`packs.py`](packs.py), [`alerts.py`](alerts.py); Agents use [`board_ctl.py`](board_ctl.py).

| Endpoint | What it does |
|---|---|
| `GET /api/boards/{agent}` | The live board: `blocks` (the Agent's blocks with their data), `sections` (the built-in dashboard's sections in display order, `{id, title, hidden}`), `collections`, and `strip` (undo strip after the Agent changed it) |
| `PUT /api/boards/{agent}` | A whole new board `{blocks, sections?, note, mode: apply / propose, by}`. `sections` (order and hidden flags of the built-in sections) is kept from the current version when left out, so an Agent adding a block never undoes what the user moved or hid |
| `GET /api/boards/proposal/{inboxId}` | Preview of a board proposal (kind `block`): the changed blocks drawn with today's data |
| `POST /api/boards/plan`, `PUT` / `GET /api/boards/plan/{inboxId}` | A new Agent's plan `{tables (with optional sample rows), blocks}`: validated and drawn in an in-memory database, so the new-Agent card can show its board before the Agent exists (`inbox_ctl.py add --board-file`) |
| `GET /api/packs`, `GET /api/packs/{name}` | Feature packs in `packs/<name>/pack.json` (tables + blocks + reminders + `GUIDE.md`), and where each is installed |
| `POST /api/packs/{name}/install` | `{agent, mode: apply / propose / check}`: missing tables are created, existing ones only gain missing fields and choice options, blocks already on the board are not added twice; propose goes through the inbox with the finished board as preview. The pack's reminders are proposed separately |
| `POST /api/packs/{name}/remove` | Takes the pack's blocks off the board (tables and data stay) |
| `GET /api/alerts/{agent}`, `POST /api/alerts/item/{id}` | Reminders that are on or paused; pause / resume / delete |
| `POST /api/alerts/{agent}/check`, `POST /api/alerts/{agent}/propose` | A reminder rule `{id, title, source (a list query), row, message, at, days, level}`: check shows what it would send today; propose makes an inbox card of kind `push` (a new notification always needs the user's OK). Once approved the server checks every 30 s and sends one push when the query returns rows (up to 3 hours late if the server was down); tapping it opens the Agent's board |

## Nightly proposals

After the nightly digest the main chat looks back over the week and turns what keeps coming up into a proposal: **add a skill** (a way of doing something, given to the Agents that need it) or, rarely, **create an Agent**. It lands in the inbox; nothing changes until the user approves. See [`proposals.py`](proposals.py); the main chat uses [`proposals_ctl.py`](proposals_ctl.py) and the `proposals` skill, and `packs/core/scripts/daily_close.py` sends the nightly trigger.

| Endpoint | What it does |
|---|---|
| `GET /api/proposals/context?days=7` | The material to look back at: what the user said in every chat (with the start of each reply), existing skills and who has them, Agents, past proposals with the reasons they were declined, today's quota |
| `POST /api/proposals` | `{kind: skill / agent, slug, title, why, evidence: [{date, thread, quote}], changes?, skill: {name, agents, markdown} / agent: {name, purpose, icon, color, board}}` → an inbox card of kind `skill` / `agent`. At most 2 a day (429); the same `slug` only once (409 while pending, done or declined; after the user asks for changes, the same slug updates the card in place); a skill name that already exists is 409 |
| `GET /api/proposals`, `GET /api/proposals/{id or inboxId}` | Past proposals and their status (pending / installed / rejected / withdrawn / failed / expired) |

Approving is done by the server itself (an inbox hook): a skill is written to `<workspace>/skills/<name>/SKILL.md` and added to those Agents' skill allowlists in `openclaw.json` (backed up, validated, restored on failure; an Agent without an allowlist can already use every skill), an Agent is created the same way as "New Agent" with its tables and starter board. The card turns into a receipt and the main chat only gets a note; if it fails, the card says why.

```bash
python3 proposals_ctl.py context
python3 proposals_ctl.py skill --slug meal-swap --name meal-swap --agents diet --title "…" --why "Third time this week…" \
    --evidence "9/24|Diet|how much can I eat if I swap in salmon?" --file SKILL.md
python3 proposals_ctl.py agent --slug reading --name Reading --purpose "…" --icon book --board-file board.json --title "…" --why "…" --evidence "…"
```

## Notifications

Three levels: **ring** (sound, interruptionLevel active), **quiet** (no sound, goes to Notification Center, passive), **none** (not sent). During `push.quiet_hours` in `server.json` (default `["23:00", "07:30"]`, in `timezone`; `[]` turns it off) ring is downgraded to quiet.

| When | Level |
|---|---|
| A reply to something you sent | ring |
| The main chat handing a question to an Agent (relay), the study desk | none |
| System triggers (`/api/chat/trigger`) | the request's `level`; only `notify: false` = none; neither = quiet |
| A new (or resubmitted) inbox item | the item's level (task / write / send / spend / calendar default to ring, the rest to quiet) |
| An inbox item done / failed | quiet |
| A background task started from the app finished, failed or hit its time limit; a revision round finished (`tasks.notify_done`) | quiet (tasks started on Telegram are answered there by OpenClaw; cancelled ones aren't pushed) |
| `/api/push/send` (wake-up report, deadline reminders, …) | the request's `level`, default ring |
| Anything above during focus time | held (the response says `held: true`) and handed over in the focus summary |

If the reply wrote a card (a new `feed_items` row whose group_id is this thread; for main, cards without an Agent), the card is pushed (subtitle = the card type, body = "card title · first point"); otherwise the start of the reply (Markdown stripped, cut at a sentence boundary). `data` carries `thread` (all older app builds read), `target` (`{type: thread | card | inbox | today, …}`), `level` (the level actually used after quiet hours) and `kind` (reply / card / inbox / done / report). The badge is inbox items waiting for you plus unread replies to your own messages. `/api/push/send` takes `{title, body, thread?, thread_id?, subtitle?, level?, category?, collapse?, target?}`.

Card and inbox pushes also carry `data.card`, which the app's notification content extension (1.0.5) draws when you long-press the notification: `k` type label, `t` title, `s` up to three `[label, value]` numbers, `r` `[value, max, label]` for a ring, `l` up to five lines, `f` a footnote, `c` a color (an Agent color name or `#RRGGBB`). Keys are short because the whole push is limited to 4 KB; `rich_card` in `push.py` builds it from meal and training cards and falls back to the first lines of the card body.

## Widgets and Live Activities

`GET /api/widget` (`widget.py`) is the small payload behind the iOS widgets (app 1.0.5): today's recovery (only once last night's sleep is in), the next meal from today's latest `meal_plan` card, today's and tomorrow's upcoming schedule items (`start` / `end` in Unix seconds so the widget can roll "next" forward on its own, a `time` label, `title`, `place`, `kind` class / training / deadline / event, at most 8) and the "To remember" count for the next week. Everything is formatted in the request's language; each part fails on its own; cached 60 s per language.

Live Activities (`live.py`): `GET /api/live` lists what should be on the Lock Screen / Dynamic Island right now as `{key, kind, state, staleAt}` (`state` is the Swift `ContentState`: `title`, `subtitle`, `icon` (SF Symbol), `accent`, `startAt` / `endAt` (Unix seconds), `progress`, `lines`, `done`). Built in: focus time (`focus:<id>`, from `think_focus`) and the post-workout meal countdown (`meal:post`: a reply that writes a `meal_plan` card with an upcoming 练后 / post-workout meal starts it, counting down to that meal; a card without one ends it). Anything else: `POST /api/live {key, kind, state, staleAt?, endsAt?, minutes?}` and `POST /api/live/{key}/end {state?}`. The app starts / updates / ends activities to match while it is in the foreground; it posts push-to-start and per-activity tokens to `POST /api/live/token {type: start | activity, token, key?, id?}` and a swipe-away to `POST /api/live/dismissed {key}` (that one isn't reopened until a new one with the same key starts). With `apns` in `server.json` (`{key_file, key_id, team_id, topic: <bundle id>, sandbox?}`, an APNs `.p8` key; the Expo push service doesn't relay Live Activities) the server also pushes start / update / end straight to Apple over HTTP/2 (`curl --http2`), so they appear without opening the app.

## Unread

`GET /api/unread` → `{threads: {<thread>: {n, mine, last: {id, text, ts, origin}}}, feedNew: [card ids], inbox, badge}`. Only threads with something unread are listed (main, every Agent, projects that aren't archived); n = assistant replies after the read mark, mine = those answering something you sent (`messages.origin = user`; timer- and inbox-triggered ones don't count); inbox = items waiting for you (exec approvals included); badge = inbox + the sum of mine. `POST /api/unread/read {thread, upto?}` moves the read mark forward (never back) and returns the same summary. New cards on the Today page: every item of `GET /api/feed` has `seen`; `POST /api/feed/seen {ids}` marks them seen.

## Study desk

`/study` is a wide-screen page for a computer: courses and modules on the left, study notes / slides (PDF) / flashcards / quiz in the middle, and a chat on the right that answers from this session's materials. Configure it with `study` in `server.json` (see [`study.py`](study.py)):

- `materials`: one folder per course, one subfolder per module (week / session), files inside. A module-by-module download of a learning platform (e.g. Canvas) has exactly this shape.
- `pages`: study notes, one folder per course, Markdown with YAML front matter (`session`, `title`, `sources` = material paths relative to the course folder). Notes attach to the module of their first source; videos go in `<course>/media/` named `S03 ….mp4`.
- `courses` (optional): which course folders to show, in order. `deadlines_cmd` (optional): a command that prints a JSON array of `{due, course, title, url}` shown along the top.
- `readings` (optional): a folder with one `<course>.json` reading list per course: `{"items": [{title, kind, required, instructions, sessions, file, status, url}]}`, `file` relative to the course's materials folder. Each session gets a "Readings" tab; if a required reading is missing, generating a study path / flashcards / quiz asks you to supply it first.
- `recordings` (optional): lecture captions, `<course>/index.json` listing recordings (`id, name, start, duration, sessions, file`), each `file` holding `{viewer_url, segments: [{t, text}]}`. Each session gets a "Lectures" tab (captions with timestamps that open the recording at that moment), and the captions go into the chat context. Keep captions private if your institution's recording policy says so.
- `video_cmd` (optional): a render command (argv list; `{script}` = the Manim script the assistant wrote, `{media_dir}` = a work folder). When set, each session gets "make a video": the assistant writes the script, the server renders it and hands any error back once for a fix. This runs code the assistant wrote on your machine, so only turn it on where you already trust the assistant to run code.

Every study page opens on its **study path**: 5–8 steps (what to do, which slide pages / note section / reading / recording time, roughly how long), generated from all of the session's materials, with a tick box per step; progress is saved on the server and shown in the course tree.

**Review**: what a podcast's Feynman check found wrong or missed can be added to the course with one tap (`POST /api/study/review {course, page?, items, …}`, stored in `pages/<course>/.gen/review.json`, attached to that session); opening the session on the study desk shows a Review box at the top, and "Reviewed" ticks an item off (`POST /api/study/review/done`). `GET /api/study/review?course=&page=&all=`.

Questions go through the same chat channel as the app, one thread per study page. The first question of each day carries the notes, the full text of the materials, the lecture captions and the readings (as much as fits; the rest by file path), plus the study-path step you're on; flashcards, quizzes and study paths are generated the same way and saved next to the notes.

## Memory tree

Me → Memory tree in the app. The memory your AI apps share (Claude, ChatGPT, Gemini, Claude Code and your own Agents) lives in an Obsidian vault, one note per memory, hung on branches. The workspace script `memory_tree.py` owns the index, the writes and the forgetting; this server only reads it and passes three actions through (optional source `tree`: without the script both endpoints answer `ok=false` + `missing_source: tree`). See [`memtree.py`](memtree.py).

| Route | What |
|---|---|
| `GET /api/tree` | `branches` in pre-order (a big branch, then its small ones; `leaves` = hung directly on it, `total` = including its small branches, `agents` = Agents whose memories land there by default), `leaves` (current ones: active and pending, profile lines excluded; `source` as written in the note, `origin` = the app that first saved it, traced through `supersedes` for leaves the weekly pruning rewrote), `trunk: {name, count}` (the profile), `counts: {total, pending, bySource}` (by origin), `issues` (notes skipped because of bad formatting) |
| `POST /api/tree/{id}` | `{action: confirm}` pending → active; `{action: forget}` the note becomes an empty shell in the archive (no text, no tags) and leaves the index; `{action: move, branch}` hangs it on another branch (the trunk's name = straight on the trunk). The activity-log line, written by `memory_tree.py`, never contains the memory itself |

## Connections

Me → Connections: everything the assistant is connected to, and how it's doing. `GET /api/connectors[?fresh=1]` → `{groups: [{id, title, items}], counts: {ok, warn, off}, checkedAt}`; each item is `{id, name, icon, status: ok | warn | off, line, facts: [{label, value}], uses, fix, open}` (`open` = a screen in the app to jump to). The result is cached for 60 s per language (`fresh=1` skips it) and the chat channels' status (`openclaw channels status`) for 2 minutes. Every check is best effort and never returns a secret: only whether key names exist, file times, counts and systemd unit states. Checks for things your machine doesn't have (a script, a unit, a folder) are left out; built-ins you don't use yet (Apple Health, the calendar feed, push) show as not connected. The calendar feed (`/cal/<token>.ics`) now remembers when a calendar last picked it up, and roughly which kind (iPhone / Mac / Google / Outlook). See [`connectors.py`](connectors.py).

## Connectors (apps over MCP)

Settings → Connectors: third-party apps connected to this server over MCP (Notion, Linear… or any remote MCP address). Your claw uses their tools through `/mcp`; the tokens stay on this server. See [`apps.py`](apps.py).

- **Connecting** follows the MCP authorization spec: the server finds the app's authorization server (the 401's `WWW-Authenticate` → protected-resource metadata → authorization-server metadata; a server without resource metadata is looked up the older way, on the MCP address's own host), gets a client (the `apps.client_id_url` document when the server supports client ID metadata documents, otherwise dynamic client registration, cached per authorization server and callback address) and gives the app an authorization URL (PKCE S256, `state`, `resource` = the MCP address, `scope` as advertised). The phone signs in, the provider sends it to `<app scheme>://oauth/callback?code&state`, the app hands that to `/api/apps/oauth/callback`, and the server exchanges the code and lists the tools. An authorization has 10 minutes and works once; starting again while one is unfinished registers a new client (the provider may have forgotten the old one). Tokens are refreshed shortly before they expire and once after a 401; if refreshing fails the app shows as needing to be reconnected. Removing an app revokes its tokens at the provider when it can.
- **Using**: each connected app's tools appear on `/mcp` as `<app id>__<tool>` (with an optional `agent` argument, `mousse_agent` when the tool has its own `agent`). Each tool is `auto` (runs), `ask` (every call becomes an inbox card of kind `app` that rings; approving runs exactly that call and posts the result into the Agent's chat; asking for a change makes the Agent call again with new arguments) or `off`. Defaults: read tools auto, write tools ask (the tool's `readOnlyHint`, otherwise its name: get / list / search / read / fetch / find / query / view / describe / show / lookup are reads). Which Agents may use an app is set per app (main by default); the caller is the Agent an `mcp-<id>` token is bound to, else the `agent` argument, else main. Claws that keep a tool list per session see changes when they reconnect.
- Calls: one short MCP session each (streamable HTTP, or SSE for servers that only have that), 60 s at most, 4 at a time; text is capped at 30,000 characters, images and other binary content become a one-line note.
- Safety: addresses learned from a server (metadata, endpoints) must be https and resolve to public addresses, unless they're on the MCP address's own origin; a custom MCP address is https, or plain http only for this machine or a Tailscale address. Tokens are never returned or logged. An `app` card edited after it was sent is not run.

| Endpoint | What it does |
|---|---|
| `GET /api/apps` | `{apps: [app], catalog: [{id, name, url, category, desc, mono, bg, fg, border, auth, hint, installed}], agents: [{id, name}]}` (main first) |
| `POST /api/apps` | `{catalog, redirect_uri}` or `{name, url, auth: oauth / token / none, token?, redirect_uri?}` → oauth: `{app, authorizeUrl, state}`; token / none: connects and lists the tools → `{app}`. 409 if it's already connected |
| `POST /api/apps/{id}/connect` | Reconnect or switch accounts: `{redirect_uri}` → `{app, authorizeUrl, state}`; a token app takes `{token}` → `{app}` |
| `POST /api/apps/oauth/callback` | `{state, code?, error?, error_description?, iss?}` → `{app}`; an unknown, used or expired `state` → 400 |
| `GET /api/apps/{id}` | `{app}` with `tools: [{name, title, description, kind, level, overridden}]` |
| `PATCH /api/apps/{id}` | `{policy?: {read?, write?}, overrides?: {tool: auto / ask / off / null}, agents?: [ids]}` → `{app}` |
| `POST /api/apps/{id}/refresh` | Lists the tools again → `{app}` |
| `DELETE /api/apps/{id}` | Revokes the tokens (best effort), forgets them and the entry, withdraws its pending cards → `{revoked}` |

App: `{id, name, url, catalog, custom, category, desc, auth, mono, bg, fg, border, status: connected / needs_auth / error, error, account, connectedAt, createdAt, updatedAt, toolsAt, toolCount, readCount, writeCount, offCount, policy, overrides, agents}`. An `app` inbox item also carries `app: {app, appName, tool, args, agent, mono, bg, fg, border}`.

The catalog lists servers checked on 2026-09-29: Notion, Todoist, Atlassian, Canva, Figma, Linear, Sentry, Stripe and Hugging Face (OAuth), GitHub (a personal access token: its authorization server has no dynamic registration), Cloudflare Docs, DeepWiki and Context7 (no sign-in). `server.json` → `apps`: `catalog` (add, change or hide entries), `client_id_url`, `redirect_uris` (http(s) callbacks for the web build), `allow_local` (tests only). State lives in `<data_dir>/apps/` (directory 0700, files 0600).

## Thinking space and Saved

The Think tab in the app (labelled **Zen** since 2026-09-28; the code and API still say think): drop a thought the moment it comes (a sentence, a few `#keywords`, a voice note, a photo, a file, a link, a long piece of writing) and nothing answers. The model only joins when you pick a few thoughts and tap **Talk** or **Done thinking**. **Saved** keeps things from other apps (links, files, screenshots) for later, also without calling a model. **Focus time** holds every notification until it ends. See [`think.py`](think.py) and [`saves.py`](saves.py).

- **A thought is a Markdown note.** With `think.vault` in `server.json` (e.g. `"think": {"vault": "~/vault", "obsidian_vault": "Vault name"}`) it goes into the vault's inbox folder, so Obsidian sees it and edits made there come back; without it, into `<data_dir>/think/`. Folder names default by language (zh: 收件箱 / 收件箱/已想完 / 收件箱/附件 / 笔记 / 写作; override with `think.inbox_dir`, `done_dir`, `attach_dir`, `notes_dir`, `writing_dir`). Properties: `id, kind, created_at, source, keywords, tags, topics, note, files, url`; attachments are embedded at the end as `![[…]]`. Notes you create in Obsidian show up too. Files are re-read only when their mtime or size changes, anything modified in the last second waits for the next read (sync clients don't write atomically), and every write is a temp file + rename. Deleting moves the note into the vault's `.trash/`; finished thoughts move to the done folder, never deleted.
- **Keywords** = the `keywords` property plus `#words` in the text (after CJK text too). The keyword page lists every thought and saved item with it, the topics that used it and the words that often come with it.
- **Topics** (`think_topics`, id `tp-…`, which is also the chat thread and the OpenClaw session `agent:main:grava:tp-…`): **Talk** sends "【自动触发】聊聊" so the model asks before it concludes; on the first message of each day, and whenever the topic's thoughts change, `chat.start_run` puts the thoughts and the "think with them" rules in front of the message (not shown in the chat). **Just note** in a topic saves a thought the model doesn't see (`note: true`, a dashed line in the chat, never sent to the Gateway); it is used when you finish. **Done thinking** drafts a note in the background in a separate session (title, one line, points with the thoughts they came from, still open, next steps, keywords, one line for the memory tree), you edit it, and **save** writes it into the notes or writing folder, moves the thoughts to the done folder and, if you keep it, adds a memory-tree leaf through the workspace's `memory_tree.py`.
- **Saved** (`think_saves`, originals in `<data_dir>/saves/`, `think.saves_dir` to change): the originals stay out of the vault. The text is extracted once when saved (web pages in the background, WeChat articles included, so a deleted article survives; PDF / Word / spreadsheets); when a page can't be fetched the reason is kept next to the link. From there you choose: ask the main chat about it (`save` on `/api/chat/send` gives the model the text), hand it to an Agent (a quiet round in its thread), turn it into a thought, distil it into a note, or delete it (soft, restorable).
- **Search** is literal and in memory over thoughts, saved items, talked topics and saved notes (two CJK characters are enough), with highlighted parts; no model call. **History** counts thoughts and saved items per day.
- **Focus time** (`think_focus`, 25 / 45 / 90 minutes or open-ended = 3 hours): before it starts the app shows what's scheduled in that window; while it runs `push.send_push` sends nothing and records what it would have sent (`think_focus_held`); when it ends (tapped or timed out) the summary merges them by where they lead (one per chat thread, the latest), plus what's waiting in the inbox and what's next on the schedule. A summary nobody has seen comes back on the next `GET /api/think/focus`.

| Endpoint | What it does |
|---|---|
| `GET /api/think/stream?before=&limit=` | Thoughts newest first (private notes left out), open `topics` (`count` = thoughts, `notes` = private notes), `savesNew`, `vault`, `folder`, `obsidianVault` |
| `POST /api/think/fragments` | `{kind?, text?, title?, keywords?, url?, topic?}`; with `topic` it's a private note in that topic |
| `POST /api/think/fragments/upload` | Multipart: up to 10 `files`, `text`, `kind` (voice is transcribed, the audio kept), `keywords` (JSON array), `duration`, `title` |
| `GET` / `PATCH` / `DELETE /api/think/fragments/{id}` | One thought; `{text?, title?, keywords?}` rewrites the note; delete = vault trash |
| `GET /api/think/file/{id}/{index}?thumb=1` | An attachment (thumbnails are cached outside the vault) |
| `POST /api/think/notes` | `{title, text, folder}`: a long piece straight into the writing (or notes) folder |
| `POST /api/think/topics` / `GET /api/think/topics?status=` | Open a topic `{fragments, title?}` / list them |
| `GET` / `PATCH /api/think/topics/{id}` | The topic with its thoughts and draft / `{title?, add?, remove?, status: open?}` |
| `POST /api/think/topics/{id}/talk` | Start talking (the model asks first) |
| `POST /api/think/topics/{id}/done?fresh=` | Draft the note in the background (`draftStatus` running → ready / failed); `fresh=1` drafts again |
| `POST /api/think/topics/{id}/save` | `{title, oneLine, points, open, next, keywords, folder: notes / writing, tree?, branch?}` |
| `GET /api/think/search?q=&scope=` | `scope`: all / idea / save / topic / note |
| `GET /api/think/keywords` / `GET /api/think/keyword?k=` | Keywords by use / one keyword's page |
| `GET /api/think/days?month=` / `GET /api/think/day?day=` | Per-day counts for a month / one day's thoughts and saved items |
| `GET /api/think/focus`, `GET /api/think/focus/preview?minutes=` | Current focus time and the unseen summary / what's scheduled in the window |
| `POST /api/think/focus/start` / `end` | `{minutes}` (0 = open-ended) / `{words?, notes?}` → the summary |
| `GET /api/think/focus/summary/{id}`, `POST /api/think/focus/seen/{id}` | An earlier summary / mark it seen |
| `GET /api/think/saves?filter=` | `filter`: all / new / link / file / image / text, plus the `new` count |
| `POST /api/think/saves` / `…/upload` / `…/from-message` | Save a link or text `{url?, text?, title?, note?, source?, keywords?}` / files / a chat message `{thread, id}` |
| `GET` / `PATCH` / `DELETE /api/think/saves/{id}` | `?full=1` for the whole text / `{title?, note?, keywords?, seen?}` / soft delete (`…/restore` undoes) |
| `GET /api/think/saves/{id}/file?thumb=1` | The original |
| `POST /api/think/saves/{id}/give` / `…/to-idea` | Hand it to an Agent `{agent}` / turn it into a thought |

## Podcast

The third part of Think (Zen): say it out loud, and afterwards it turns it into a note. It is the voice version of Talk and ends the same way: a note in your own words → saved to Notes / Writing / Study → anything worth remembering long term is offered to the memory tree, and nothing is added unless you tap. Audio and transcripts stay on the server (`<data_dir>/podcast/<episode>/`, `podcast.dir`), never in the vault. See [`podcast.py`](podcast.py).

- **Talk about today**: the model picks 4 from your unfinished Zen topics, the "still open" lines in your vault notes, recent study-desk sessions, deadlines in the next 6 days and the memory tree, writes each as a concrete question and says where it came from; cached per day, "Others" picks again.
- **Four modes**: solo (it just listens) / with a host (it asks one follow-up when you pause; skip or rephrase) / Feynman (it plays a smart layperson, then checks your explanation against the session's slides, notes and lecture captions: right, wrong, missed, with sources; the standard view when there are no materials) / with friends (one phone in the room, voices told apart afterwards, you name who's who the first time; only your words go into the note, everyone gets minutes).
- **Talk it through first**: it asks for your first reaction, you answer (type or speak), and it drafts a 3–5 point outline card from your words that stays on screen while you record. An outline, never a script.
- **Take by take**: every pause uploads a take and the server transcribes it at once: `gpt-transcribe` for the words (vocabulary keywords, per-token confidence → unclear words are marked), `whisper-1` for sentence timing, aligned character by character, so every sentence plays on tap. Vocabulary = words you corrected + `transcribe_prompt` terms + course, Agent and project names + tree branches (profile names and addresses stay out). Fixing a sentence adds the changed words.
- **Organizing** (in the background; the app polls): proofread names and misheard words → title, one line, your own words with timestamps, still open, keywords (suggestions only when tapped), an optional tree line → compare with earlier notes (did your view change) → the Feynman check.
- **Models** go through [`llmjson.py`](llmjson.py): OpenClaw's `llm-task` (no tools, no chat; a host question takes 2–3 s), or a throwaway session when it's off. Transcription needs `OPENAI_API_KEY` (same as voice input): about $0.0045/min for `gpt-transcribe` and $0.006/min for `whisper-1`.
- **Materials**: an episode can take things you already have: messages from any chat (long-press → Add to a podcast, or pick them inside the episode), chats with friends (both sides), files (PDF, Word, Excel, PowerPoint, text; recordings are transcribed), Zen thoughts and topics, saved items. The text is taken when you add it (up to 12,000 characters each, 40 per episode), and the prep chat, the host's questions, organizing (newest first, 8k / 6k / 24k characters), comparing with earlier notes (only your own words) and the Feynman check (files count as course materials) use it. A friend's words stay in that episode: never quoted, never in the title or the tree; the saved note's Drew on section only says "Drew on a chat with Sam". If `llm-task` isn't available and a call falls back to a normal chat turn (which has tools), friends' words are left out of it (`fallback_input` in `llmjson.ask`). See [`podmaterials.py`](podmaterials.py).
- **Friend notes** (with friends): pick who's here before recording; when naming voices, match each voice to a person (someone you recorded with before, a friend, or a new name; or just a name and no notes). After organizing, each matched person gets 3–8 notes (views / up to / cares about / ask next time), each resting on one of their sentences (tap to hear it). The same thing again isn't written twice, a change replaces the old note (kept as history), and an "ask next time" that got its answer is marked asked. Re-organizing or deleting an episode takes back the notes it wrote (ones you edited stay). Only you see them: the host and the prep chat can pick up a thread ("Last time Sam said…") and Talk about today can suggest catching up with someone; nothing else reads them — not the card agent at any tier, not the main chat or Agents, not the vault or the memory tree. Friends are told: the recording screen says notes are kept, and each person's minutes end with a line saying so. With friends, the title, one line and keywords come only from what you said, since they can end up in the vault. Notes are only written through `llm-task` (no tools; the whole input is someone else's words): without it the done page says no notes were kept this time. A fallback chat turn never carries friend notes, and with friends it carries only your own sentences. See [`people.py`](people.py).
- **Recording together remotely isn't built; the interface is reserved**: later (possibly a call inside the app) each person becomes one track, uploaded to the same episode with a `track` (who) and the time since recording started (to line tracks up), so organizing splits people by track instead of guessing voices. Today's takes are one timeline, one after another.

`podcast` in `server.json` (all optional): `dir`, `text_model` (`gpt-transcribe`), `time_model` (`whisper-1`; `""` = no sentence timing), `thinking` (`low`).

| Endpoint | What it does |
|---|---|
| `GET /api/podcast` | Today's topics (`null` until picked; `mode: friends` with `source.person` = catch up with someone), the last 40 episodes (`chip` = the list label), `study` (is the study desk set up), `materials` / `people` (this server has them) |
| `POST /api/podcast/suggest` | `{exclude?}` picks 4 (pass the current ones for "Others") |
| `POST /api/podcast/episodes` | `{title, mode: solo / host / feynman / friends, source?, people?}` (with friends: who's here, `[{id} \| {friend} \| {name}]`) |
| `GET` / `PATCH` / `DELETE /api/podcast/episodes/{id}` | Everything about one episode (outline, sentences with times per take, questions asked, result with `people` counts, Feynman, where it was saved, `materials` count, `people` / `speakerPeople`) / `{title?, mode?, outline?, done?, cur?, speakers?, people?}` (naming voices: `{"A": "@me", "B": "Sam"}`; `people: {"B": {id} \| {friend} \| {name} \| {name, skip}}` matches a voice to a person, whose name it takes) / delete audio, transcript, materials and the friend notes it wrote (a saved note and notes you edited stay) |
| `POST /api/podcast/episodes/{id}/prep` | Before recording: `{}` it asks first / `{text}` your answer / `{outline: true}` outline now; `…/prep/voice` (multipart `file`, `duration`) to speak instead |
| `POST /api/podcast/episodes/{id}/segments` | multipart `file`, `idx` (from 0; the same idx again = re-upload), `duration`: one take, transcribed right away |
| `POST /api/podcast/episodes/{id}/ask` | `{how: next / again / skip}`: wait for the take and ask one / rephrase / skip |
| `POST /api/podcast/episodes/{id}/finish` | Done recording; organizes in the background (`status` processing → ready / naming (with friends: name the voices first) / failed); again = re-organize |
| `PATCH /api/podcast/episodes/{id}/sentence` | `{idx, i, text}` fixes one sentence (changed words join the vocabulary) |
| `POST /api/podcast/episodes/{id}/save` | `{folder: notes / writing / study, title, oneLine, quotes, open, keywords, relates, explain?, tree?, branch?}` writes the vault note (saving again rewrites the same note); the tree only with `tree` |
| `POST /api/podcast/episodes/{id}/review` | Adds the Feynman check's wrong and missed points to study review |
| `GET /api/podcast/episodes/{id}/audio/{idx}` | One take's audio (Range requests; the web build may use `?token=`) |
| `GET` / `POST /api/podcast/episodes/{id}/materials` | The episode's materials / `{items: [{kind: chat / friend / idea / topic / save, ref}]}` adds some (the same source isn't added twice) |
| `POST /api/podcast/episodes/{id}/materials/upload` | multipart `files` (up to 10, 25 MB each): text is extracted, recordings transcribed; the file stays in the episode folder |
| `DELETE /api/podcast/episodes/{id}/materials/{mid}` | Removes one |
| `GET /api/podcast/pick?kind=chat\|friend\|idea\|topic\|save[&friend=][&days=]` | Candidates: what you said in chats, the friend list → one friend's chat, Zen, saved items |
| `POST /api/podcast/materials/quick` | `{kind, ref, episode?}`: long-press → add to an episode, or start a new one (a friend's words never become its title) |
| `GET` / `POST /api/people` | People with note counts, and friends not matched to anyone yet / `{name, friend?}` |
| `GET` / `PATCH` / `DELETE /api/people/{id}` | One person: notes (with the episode and sentence each came from, and the notes they replaced) and the episodes together / `{name?, friend?}` / delete the person and every note |
| `POST /api/people/{id}/notes` | `{kind: view / doing / care / ask, text}` adds one yourself |
| `PATCH` / `DELETE /api/people/notes/{nid}` | `{text?, kind?, status?: active / done}` / delete (deleting an update brings back the note it replaced) |

## Sharing

Share a reply or a Done-thinking note as a link page or a clean image card: long-press a message → Share, or Zen → Done thinking → Share; Me → Shared lists what went out. See [`share.py`](share.py).

- **A share is a snapshot.** The text is copied into `shares` when you share; editing the original later doesn't change what went out.
- **Private bits are hidden first.** Addresses and the names of your partner and family found in the profile (`USER.md`), emails, phone numbers, body numbers (weight, body fat, heart rate, sleep…) and the words in `share.private_words` start hidden, and you show them one by one. Hidden text never leaves the server: the link page and the cards draw a grey bar instead, titles included.
- **Two looks.** *With link*: `/s/<token>` is a plain page anyone can open without the app, and `/s/<token>/card.png` is the preview image chat apps show. *Clean*: a 3:4 image with no URL, QR code or app name, for platforms that don't allow links. Cards are drawn on the server with Pillow; Chinese needs a CJK font (Noto Sans CJK is found automatically, `share.font` / `share.font_bold` pick another).
- **Reaching the link from outside.** Set `share.public_port` and `run.py` also starts a small app on `127.0.0.1:<port>` that serves only `/s/` ([`public.py`](public.py): no `/api`, no tokens, no trusted devices). Point Tailscale Funnel or a reverse proxy at it (`tailscale funnel --bg --set-path /s http://127.0.0.1:<port>/s`) and set `share.public_url` to the address people see. Without `public_url` the app offers the clean card only. The main server serves `/s/` too, for your own devices; views are counted only through the public app, link-preview bots excluded.
- **Withdraw** empties the snapshot (the hidden-bits title stays in your list) and the link says it was withdrawn. Drafts nobody published are deleted after 7 days. No model is called.

| Route | What |
|---|---|
| `POST /api/shares` | `{kind: message, thread, id}` / `{kind: note, topic or path}` / `{kind: text, title?, text}` → a draft with `masks` (label, the hidden text and a few words around it, released) and `segments`; sharing the same message again returns the same share |
| `GET /api/shares` | Published and withdrawn shares; `canLink` = a public address is configured |
| `GET` / `PATCH /api/shares/{id}` | `{release?: [mask ids], hide?: [...], quote?, title?, withQuestion?}` (`""` = back to the default; `withQuestion` takes the snapshot again) |
| `POST /api/shares/{id}/publish` | The link starts working (`url` when `public_url` is set) |
| `DELETE /api/shares/{id}` | Withdraw (a draft is simply deleted) |
| `GET /api/shares/{id}/card?style=clean\|link` | The card as a data URI; `/card.png` for the image itself |
| `GET /s/{token}`, `/s/{token}/card.png` | Public: the link page and its preview image |

## Friends

Chat → Friends in the app. Your server and a friend's server talk directly: identity is an Ed25519 key per server (not the address), you become friends through a one-time invite, and every request between the two servers is signed. The protocol is [`../docs/social-protocol.md`](../docs/social-protocol.md); the code is [`social.py`](social.py) (identity, card, signatures, tables, tiers) and [`friends.py`](friends.py) (invites, chat, delivery, shares to friends, follow-up questions).

- **Both sides need a public address.** Friends reach you through the small public app ([`public.py`](public.py)) under `/f`: point Tailscale Funnel or a reverse proxy at it (`tailscale funnel --bg --set-path /f http://127.0.0.1:<share.public_port>/f`) and set `share.public_url`. Without it, or without `user_name`, the app explains why you can't add friends yet. A reverse proxy must keep the `/f` prefix as it is (the path is signed).
- **Invites.** An invite is a link `<your address>/f/i/<token>/<your public key>`: it works once, lasts 7 days by default, and only its hash is stored. Opening it in a browser shows a landing page (with a QR code for phones and an "Open in the app" link); the app pastes it, checks the other server's card against the key in the link and redeems it with a signed `POST /f/hello`. Fingerprints (10 characters) let two people compare on a call.
- **Chat.** Each message is one signed `POST /f/msg`. Outgoing messages queue in `friend_messages` and a delivery loop sends them in order per friend, retrying for up to 3 days; edits, withdrawals, a changed name or address, and removing a friend travel as messages too. Blocking drops their messages silently.
- **Sharing to friends.** On the Share page, pick friends: they get the snapshot with the same hidden bits (`▇▇▇`; the original never leaves the server). Without a link, the share's status is `friends` and `/s/` stays closed. Withdrawing a share withdraws every copy.
- **Follow-up questions and your card agent.** Friends can ask about something you shared; when the share allows it and their tier's `shares` is `ask`, your card agent (`cardagent.py`, never the claw, no tools) answers within that tier and says what it used. You see every answer with Fine / Rewrite / Withdraw. Without a card agent, questions just wait for you. Tiers (close / friend / classmate / stranger) and your status line are set in Chat → Friends → My card agent (`/api/card`); health and the memory tree are never on offer. Everything the card agent says first goes through **Sentinel** (`sentinel.py`, step 9 of the security base): rules plus a separate, independent model review; anything off is held for you to send, rewrite or drop, and a line that can't be reviewed becomes a fixed one (details in `docs/a2a.md` 2.4.1).
- **Push** for friends is a new kind of notification: off until `social.push` in `server.json` turns it on (`{"message": "ring", "answered": "quiet", "friend": "quiet"}`; `agents`, when what your card agent asked a friend's agent comes back decided, follows `answered` unless set). Friends' messages count as unread and in the badge either way.
- `social.allow_http: true` is for test servers on one machine (`http://127.0.0.1:<port>` addresses); never set it on a real server.

| Endpoint | |
|---|---|
| `GET /api/friends` | Whether you can add friends (`ready`, `why`), your name and fingerprint, friends with their last message and unread count, open invites |
| `POST /api/friends/invites`, `DELETE /api/friends/invites/{id}` | `{note?, tier, days?}` → the invite link and its QR code (an SVG path, shown once); withdraw an unused one |
| `POST /api/friends/preview`, `POST /api/friends/accept` | `{code}` → who it is (checked against the key in the link); `{code, tier, alias?}` → redeem it |
| `PATCH` / `DELETE /api/friends/{id}`, `POST /api/friends/{id}/block` | Tier and alias; remove (tells them); block / unblock |
| `GET /api/friends/{id}/messages?after=`, `POST …/messages`, `POST …/ask`, `POST …/read` | The chat (with `recent` for status changes); send; ask about one of their shares; mark read |
| `POST /api/friends/messages/{id}/review`, `…/revoke`, `…/retry` | `{action: ok \| edit \| revoke, text?}` on a card agent answer; withdraw your own message; retry a failed one |
| `POST /api/shares/{id}/send` | `{friends, ask, link, text?}` |
| `GET` / `PATCH /api/card` | Tiers and your status line |
| `GET /f/card`, `/f/jwks.json`, `/f/i/{token}/{key}`; `POST /f/hello`, `/f/msg` | Public (on the small app only): the signed card, the key, the invite page; redeeming an invite and delivering a message (signed) |

## Errand agent and Sentinel egress (preview)

An agent that runs errands outside for you (research, forms, mail, bookings) from inside OpenClaw's Docker sandbox, with Sentinel as its only way online: reads pass, anything that submits, sends, uses your credentials or carries your private details waits for "Let it through" in the inbox (kind `egress`). Credentials are placeholders swapped in on the way out, for their bound hosts only. Off until `python3 errand.py setup`; everything (layers, rules, credentials, rollback) is in [`../sandbox/errand/README.md`](../sandbox/errand/README.md). Modules: `egress.py` (decisions, `/api/egress/*`, the inbox hook), `egress_proxy.py` + `sentinel_run.py` (the proxy), `errand.py` (setup / status / secrets).

## Data sources are optional

The boards need workouts / meals / body / calendar / derived health metrics, each provided by one script in the `scripts` directory named in `server.json` (default `<workspace>/scripts`): `xunji.py` (workouts / meals / body), `calendar_ics.py` (calendar), `apple_health.py` (recovery score, energy balance, fitness trend, and whether you are up yet: `/api/health/wake` reads the raw sleep segments the phone pushes to `/api/health/sleep` and the wake signals posted to `/api/health/signal`). `memory_tree.py` provides the memory tree (Me → Memory tree). A script that is present gets loaded; a missing one means "not connected": `/api/health` reports `sources` so the app knows, the affected endpoints answer `ok=false` + `missing_source`, boards show an empty state, everything else works. See [`sources.py`](sources.py).

Today these three scripts still have the shape of the author's own sources (the Xunji training app, an ICS calendar, Apple Health). They are being split into optional packs (`packs/`): each pack declares the data types it needs and you map the sources.
