# OpenMousse server

[中文](README.zh-CN.md) · **English**

Thin API layer (FastAPI): auth, chat relayed to your OpenClaw Gateway, Agent creation and deletion, board data, attachments and voice, push notifications, and hosting of the web build.

## Install

The one-line installer at the repository root (`install.sh`) does all of the below, including the systemd service. By hand:

```bash
cd ~/openmousse/server
pip install -r requirements.txt
mkdir -p ~/.openmousse && cp server.example.json ~/.openmousse/server.json   # edit paths and timezone
python3 tokens.py add phone      # generate an access token for the app's connection page
python3 run.py                   # or install as a systemd service, see openmousse-server.service.example
```

Every field of `server.json` is documented at the top of [`config.py`](config.py). Changing tokens or adding Agents needs no restart; changing the bind address does.

## Auth

`/api/*` requires `Authorization: Bearer <token>` (`X-API-Key` also works; `?token=` only for `GET /api/files/…`, which image views can't send headers to). No credentials → 401. Two token-free doors are off by default: `auth.tailscale_nodes` (a whitelist of Tailscale device names; needs tailscale on this machine) and `auth.trust_loopback` (never enable it when a reverse proxy runs on the same host). The web build's static files are public.

## Letting the phone connect

- **Tailscale** (least effort): bind the service to the Tailscale address, install Tailscale on the phone, enter `http://100.x.x.x:8080` in the app.
- **Public HTTPS**: `tailscale serve` / `tailscale funnel`, or Caddy / nginx reverse-proxying to 127.0.0.1:8080; enter `https://your.domain` in the app.

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

Item: `{id, kind, source, sourceName, thread, title, why, changes: [], detail, approveLabel, fields?, status, note, result, level, createdAt, updatedAt, decidedAt, expiresAt, messageId}`. `messageId`: an item an Agent submits during a reply is attached to that reply (`messages.id`) when the reply finishes; items submitted outside a reply have null. kind: task / write / send / spend / schedule / push / skill / agent / block / code / calendar / other (exec only comes from OpenClaw and carries `fields`); status: pending / approved / rejected / revising / done / failed / withdrawn / expired. The old `/api/approvals` endpoints still work for older app builds.

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

If the reply wrote a card (a new `feed_items` row whose group_id is this thread; for main, cards without an Agent), the card is pushed (subtitle = the card type, body = "card title · first point"); otherwise the start of the reply (Markdown stripped, cut at a sentence boundary). `data` carries `thread` (all older app builds read), `target` (`{type: thread | card | inbox | today, …}`), `level` (the level actually used after quiet hours) and `kind` (reply / card / inbox / done / report). The badge is inbox items waiting for you plus unread replies to your own messages. `/api/push/send` takes `{title, body, thread?, thread_id?, subtitle?, level?, category?, collapse?, target?}`.

## Unread

`GET /api/unread` → `{threads: {<thread>: {n, mine, last: {id, text, ts, origin}}}, feedNew: [card ids], inbox, badge}`. Only threads with something unread are listed (main, every Agent, side chats that aren't archived); n = assistant replies after the read mark, mine = those answering something you sent (`messages.origin = user`; timer- and inbox-triggered ones don't count); inbox = items waiting for you (exec approvals included); badge = inbox + the sum of mine. `POST /api/unread/read {thread, upto?}` moves the read mark forward (never back) and returns the same summary. New cards on the Today page: every item of `GET /api/feed` has `seen`; `POST /api/feed/seen {ids}` marks them seen.

## Study desk

`/study` is a wide-screen page for a computer: courses and modules on the left, study notes / slides (PDF) / flashcards / quiz in the middle, and a chat on the right that answers from this session's materials. Configure it with `study` in `server.json` (see [`study.py`](study.py)):

- `materials`: one folder per course, one subfolder per module (week / session), files inside. A module-by-module download of a learning platform (e.g. Canvas) has exactly this shape.
- `pages`: study notes, one folder per course, Markdown with YAML front matter (`session`, `title`, `sources` = material paths relative to the course folder). Notes attach to the module of their first source; videos go in `<course>/media/` named `S03 ….mp4`.
- `courses` (optional): which course folders to show, in order. `deadlines_cmd` (optional): a command that prints a JSON array of `{due, course, title, url}` shown along the top.
- `readings` (optional): a folder with one `<course>.json` reading list per course: `{"items": [{title, kind, required, instructions, sessions, file, status, url}]}`, `file` relative to the course's materials folder. Each session gets a "Readings" tab; if a required reading is missing, generating a study path / flashcards / quiz asks you to supply it first.
- `recordings` (optional): lecture captions, `<course>/index.json` listing recordings (`id, name, start, duration, sessions, file`), each `file` holding `{viewer_url, segments: [{t, text}]}`. Each session gets a "Lectures" tab (captions with timestamps that open the recording at that moment), and the captions go into the chat context. Keep captions private if your institution's recording policy says so.
- `video_cmd` (optional): a render command (argv list; `{script}` = the Manim script the assistant wrote, `{media_dir}` = a work folder). When set, each session gets "make a video": the assistant writes the script, the server renders it and hands any error back once for a fix. This runs code the assistant wrote on your machine, so only turn it on where you already trust the assistant to run code.

Every study page opens on its **study path**: 5–8 steps (what to do, which slide pages / note section / reading / recording time, roughly how long), generated from all of the session's materials, with a tick box per step; progress is saved on the server and shown in the course tree.

Questions go through the same chat channel as the app, one thread per study page. The first question of each day carries the notes, the full text of the materials, the lecture captions and the readings (as much as fits; the rest by file path), plus the study-path step you're on; flashcards, quizzes and study paths are generated the same way and saved next to the notes.

## Data sources are optional

The boards need workouts / meals / body / calendar / derived health metrics, each provided by one script in the `scripts` directory named in `server.json` (default `<workspace>/scripts`): `xunji.py` (workouts / meals / body), `calendar_ics.py` (calendar), `apple_health.py` (recovery score, energy balance, fitness trend). A script that is present gets loaded; a missing one means "not connected": `/api/health` reports `sources` so the app knows, the affected endpoints answer `ok=false` + `missing_source`, boards show an empty state, everything else works. See [`sources.py`](sources.py).

Today these three scripts still have the shape of the author's own sources (the Xunji training app, an ICS calendar, Apple Health). They are being split into optional packs (`packs/`): each pack declares the data types it needs and you map the sources.
