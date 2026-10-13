# Agent meetings

[中文](meetings.zh-CN.md)

Several Agents work through one topic together. The main chat's agent (the "host"; the app shows the name you gave your assistant) runs the meeting and writes the minutes; once you confirm them, the to-dos land in your schedule and on each Agent's dashboard. A meeting can stay on afterwards as a standing Agent group. Server: [`server/meetings.py`](../server/meetings.py); app: `app/src/screens/MeetingScreens.tsx`.

## Starting one

- **By hand**: Chat → **+** → New meeting: write the topic and tick the Agents (2–8). About a second after you stop typing, the host suggests who should attend and a short name (tool-free model, see below); once you tick anything yourself it stops overriding you. You can turn on "Keep as a group chat".
- **From the main chat**: when something you ask in the main chat involves several Agents that need to work it out together (training, meals and sleep in the week before an interview, say), the main agent starts a meeting through the handoff skill (`ask_agent.py --meet "topic" <Agent id>…`). Its one-line reply ("This involves several Agents; I've started a meeting.") carries a meeting card (in progress). When the round is over the main chat gets a line "The meeting "…" is over. N to-dos need your confirmation:" with the minutes card under it (Confirm / Open meeting).

## Rules

- **Each Agent speaks at most once per round.** Round 1: every member, in order. Later rounds: whoever you @ in your note; with no @ the host decides — from your note if you added one (tool-free model, 1–3 Agents), otherwise (Keep discussing) the `next` list in the last minutes, otherwise everyone.
- **@ goes one hop.** A message may @ one other member (the first one counts). If that Agent hasn't spoken this round it goes next and its message is marked "replying to X"; an @ inside that reply is ignored. If it already spoke this round it doesn't get a second turn; the question goes to the minutes' open items and the next round.
- **Usage**: a round is at most one reply per member + 1 for the minutes (the host's pick is a very short tool-free call, not a reply). The new-meeting page and the meeting info page say so.
- **Discussion only.** The members' instructions say: don't change the schedule, dashboards or records, don't file inbox cards, don't start background tasks; put what should happen as a suggestion, and the server writes it once the minutes are confirmed. Members may use their own memory and tools to look things up.
- **Minutes**: after each round the host writes decisions, to-dos (exactly one owning Agent each, with concrete dates; a start time makes it an event, no start time a deadline), open questions and who to ask next. A new round's minutes replace the previous ones.
- **Confirm**: every to-do goes into the schedule per date (`schedule_items`, `source` = the owning Agent, `key = meeting:<meeting>:<round>:<n>:<date>`, so confirming twice doesn't duplicate) and onto that Agent's dashboard checklist "Meeting to-dos" (table `meeting_todos`; if no block uses it, a checklist block is added as an Agent revision you can undo from the top of the dashboard). The meeting ends, or becomes a group chat if "Keep as a group chat" is on.
- **Group chat**: each message you send starts a round: whoever you @ answers, otherwise the host picks (as above). No minutes. The info page can close it (end); an ended meeting can be reopened as a group.
- **End / delete**: End moves it to Ended meetings and keeps the record; unconfirmed minutes don't touch the schedule. Delete removes the meeting's messages, minutes and each member's meeting session (OpenClaw keeps a compressed archive copy) plus the minutes notices in the main chat; confirmed to-dos stay in your schedule and dashboards.
- **No push.** Minutes only count as unread (one in the meeting, one in the main chat). No new notifications.

## How each member takes part

- A meeting is a thread `grp-<8 hex>`. Each member has its own OpenClaw session in it, `agent:<id>:grava:grp-<id>` (older Agents still living under main: `agent:main:grava:grp-<id>-<id>`), reached through the Gateway's OpenAI-compatible endpoint with its own agent id (`x-openclaw-agent-id`): its own memory, tools and the model picked in its chat.
- On its turn it gets the meeting messages it hasn't seen yet, each labelled with who said it (`[Meals] …`, `[your name] …`, `[assistant · minutes of round 1] …`), without its own (they're in its session). The first turn also carries the topic, who is present, who it is and the rules; OpenClaw resets sessions daily at 04:00, so the first turn on a new day carries them again (with at most the last 30 messages). Other claws (Hermes, nanobot…) don't keep sessions and get the whole thing every time.
- The Agent that was @-ed also reads ""X" @-mentioned you: "…" Answer that first; don't @ anyone in this reply."
- One turn may take up to 5 minutes; after that it counts as unfinished and the next member is asked.

## Tool-free model

Suggesting attendees, picking who answers and writing minutes all go through [`server/llmjson.py`](../server/llmjson.py): OpenClaw's `llm-task` plugin when it's enabled (no tools, a fresh session each time, the outer shape checked against a schema), otherwise a one-off temporary session. All three only see your own words and your own Agents' messages, never a friend's.

## The main chat knows the outcome

When a meeting the main chat started gets minutes, is confirmed or ends, the main chat's next turn carries that in its preamble once (the minutes plus confirmed / waiting / ended unconfirmed; `meetings.news` / `told`).

## Data

- `meetings`: `id, title, topic, host (main), members (JSON, speaking order), status (running / pending (to confirm) / ended / group), keep, round, origin (manual / main), from_thread, message_id (the reply the meeting card hangs on), minutes (latest minutes, JSON), confirmed (JSON), news, told, created_at, updated_at, ended_at`
- `meeting_seen`: `(meeting, agent) → last_id, day`: how far each member has read, and on which day (a new day re-sends the rules).
- `messages` gains two columns: `speaker` (Agent id; the host's system lines and minutes are `main`; yours is empty) and `meta` (JSON: `round`, `replyTo`, `at`, `deferred`, `kind: minutes | system | meeting`, `event`, `agents`, `minutes`). Roles: you = `user`; members and minutes = `grava` with `origin = meeting` (unread, but not a "reply to you" for the badge); system lines = `auto`.
- A server restart interrupts a running round: the next read marks the meeting to-confirm with a line "The server restarted; this round didn't finish", and you can continue. A running round counts in `/api/chat/busy`, so `safe_restart` waits for it.

## API

| Endpoint | What it does |
|---|---|
| `GET /api/meetings?status=active\|ended\|all` | `{meetings, endedCount}`: active = running, to confirm and groups (the chat list's meeting rows), newest first; each row has `last` (`name`, `text`, `time`, `kind`) and `needsYou` (to confirm) |
| `POST /api/meetings` | `{topic, members, title?, keep?, origin?}` → starts it and round 1; 2–8 existing Agents. With `origin: main` the meeting card goes straight to the main chat's running reply over SSE |
| `POST /api/meetings/suggest` | `{topic}` → `{agents, title}` (empty `agents` when the model isn't available) |
| `GET /api/meetings/{id}` | `{meeting, messages}`: every message (oldest first, up to 500); `meeting.live` = the running round (`round, phase, speaker, replyTo, text, queue`) |
| `GET /api/meetings/{id}/stream` | Attach to the running round (SSE; 204 if none), split by speaker: `state` (snapshot) → `round` / `speaker` / `delta` / `said` / `system` / `message` / `phase` (minutes = writing the minutes) / `minutes` / `status` → `done` |
| `POST /api/meetings/{id}/send` | `{text}`: add a note. To confirm / group: starts the next round; running: just recorded, members yet to speak see it; ended: 409 |
| `POST /api/meetings/{id}/continue` | Keep discussing (no note) |
| `POST /api/meetings/{id}/confirm` | Confirm this round's minutes → `{meeting, confirmed: {todos, schedule, board, failed}}` |
| `POST /api/meetings/{id}/stop` | Stop the running round (the current speaker is marked stopped, nobody else is asked, no minutes) |
| `POST /api/meetings/{id}/end` | End it (close, for a group) |
| `PATCH /api/meetings/{id}` | `{keep?, title?}`: keep on an ended meeting reopens it as a group; keep off on a group ends it |
| `DELETE /api/meetings/{id}` | Delete the record (confirmed to-dos stay) |
| `GET /api/chat/cards?thread=` | Two more card kinds: `{kind: "meeting", meeting, messageId, title, members, status, statusLabel, round}` (under the reply that started it) and `{kind: "minutes", meeting, messageId, round, status: pending / confirmed / superseded / ended, decisions, todos, open, confirmed}` (under the minutes notice) |

`/api/unread` → `threads` includes meetings that haven't ended (`grp-…`). `/api/chat/send` refuses meeting threads (400). `/api/health` → `server.features` has `meetings`; the app shows the entry points only when `supports('meetings')`.

```bash
python3 packs/core/scripts/ask_agent.py --meet "Training, meals and sleep before next Wednesday's mock interview" apply fitness diet health --title "Interview week"
```
