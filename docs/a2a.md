# OpenMousse agent to agent (A2A) v1

English · [中文](a2a.zh-CN.md)

> First version, 2026-09-28. Layer ③ of the social section: someone else's agent (a friend's OpenMousse, or any agent that speaks A2A) asks you something, and your **card agent** answers. The protocol is [A2A 1.0](https://a2a-protocol.org/latest/specification) (the Linux Foundation's open protocol, JSON-RPC binding); identity, signatures, the friends table and tiers come from layer ② ([social-protocol.md](social-protocol.md)) instead of a second set.

## 0. In one paragraph

What talks to the outside is the card agent, not the main agent. It has **no tools** and **never goes through the claw's chat API**; it only sees what the asker's tier releases; whatever the other side says is data, never instructions; it never agrees to anything that needs you (a time, money, a promise) and puts it in your inbox instead; everything it says is logged. The first scenario is "dinner": two agents settle on a time → a card waits for you → you tap Yes, the other side hears back and the dinner is in your schedule.

## 1. Who owns what

| | What | Code |
|---|---|---|
| Rules | tier → what it may say, fetching the material, which model path, the server-side checks, inbox cards, telling the other side after you decide, logs, daily caps | `server/cardagent.py` |
| A2A | the signed agent card, the JSON-RPC endpoint, task states, push notifications, asking someone else's agent | `server/a2a.py` |
| Identity and signatures | the Ed25519 key, request signatures (RFC 9421), friends, tiers, "what's new" | `server/social.py` (layer ②) |
| Answers inside friend chat | follow-up questions on shares go to `cardagent.answer()`; you review / edit / withdraw | `server/friends.py` (layer ②) |

Mounted with one line in `public.py` (`/f/a2a…`) and one in `main.py` (`/api/a2a…`, `/api/card/log`, `/api/card/health`). The inbox changes (kind `social`) are in `inbox.py`.

## 2. The card agent's rules

### 2.1 What it can see (by tier)

The tier table belongs to layer ② (`social.tier_scopes(tier)`, edited under "My card agent"). The card agent fetches only what is released:

| Key | Value | Material given to the card agent |
|---|---|---|
| `calendar` | `detail` | every block in the schedule layer for the next 14 days: time and title (titles that look private — a doctor, a family member's name — become "private"), and whether each evening is free |
| | `busy` | the same **without titles**: only when it's busy and whether the evening is free |
| `status` | `some` / `line` | the text you wrote on the card page (`social.card_status()`): all of it / the first line |
| `shares` | `ask` | the share snapshot the caller passes in (private bits already hidden); `view` / `public` give no text |
| `notes` | `view` | nothing yet, until you decide how notes are marked shareable |
| `address` | `view` | the address in your profile (found the way sharing finds it) |

**Health and body, and the memory tree, are not keys**: there is no switch, and the card agent can't see them either. Strangers (not in the friends table, unknown key, other vendors' agents) are the `stranger` tier and never get more than "public".

### 2.2 Which model (not the claw)

A claw's chat API is a whole agent: OpenClaw's `/v1/chat/completions` runs an agent with tools, USER.md and memory search, and a shared-token call counts as the owner speaking. So the card agent only uses these, in order:

1. `server.json` → `card.llm`: any OpenAI-compatible **plain model** API (`{"url": ".../v1", "token" | "token_env", "model", "headers"}`), sent without tools.
2. OpenClaw's **llm-task** plugin (`POST <gateway>/tools/invoke`, `tool: "llm-task"`): prompt only, zero tools, a fresh session every time, JSON checked against a schema; if a zero-tool call isn't possible it fails instead of falling back to an agent turn. Needs only `plugins.entries.llm-task` enabled in `openclaw.json` (the Gateway reloads it without a restart). Don't add llm-task to any agent's `tools.allow` / `alsoAllow`: `/tools/invoke` lets through a plugin tool it is asked for by name (OpenClaw 2026.9.5), and the allow entry in OpenClaw's llm-task guide is for agents that call it in their own turns, which the card agent doesn't need.
3. Neither: fixed templates (availability from the calendar, "I'll ask" for concrete proposals, otherwise "you'd need to ask them").

`cardagent.available()` tells layer ② whether a model is there (if not, follow-up questions are left for you). `GET /api/card/health` shows the app which path is in use and the last error.

### 2.3 Incoming text is data

The rules go in the task instructions (llm-task's TASK / the system message); the other side's words only appear in INPUT_JSON's `message` and `conversation`, each marked by who said it (`them` / `you` = the card agent / `owner` = you in person). The model returns one JSON object:

```json
{"reply": "…", "used": ["calendar"], "ask_owner": null, "declined": []}
```

`ask_owner` = `{"kind": "decision" | "private", "summary", "proposal": {"what", "date", "start", "end", "place"}}`.

### 2.4 Checked again on the server, whatever the model says

- `used` may only name material that was given.
- **Leaks**: a reply containing an address, phone, email, body number, a partner's or family member's name or one of your private words that wasn't released (`share.find_private`), or a health word not in the material, is not sent; it becomes "I can't answer that — you'd need to ask <you> directly." The original stays in the log for you (`card_log.meta.original`).
- **Agreeing for you**: if they are proposing something (a time plus a question or invitation, or money) and the reply sounds like a yes ("sure", "confirmed", "see you", "好的", "定了"…), it becomes "That's <you>'s call — I'll ask." and a card.
- **No card without a concrete time**: "When is he free for dinner?" is answered from the calendar; a day, a time or money makes it a card.
- **Strangers** are refused by default (with `card.strangers` off the A2A endpoint answers 403, the card agent makes no model call and logs nothing, and the signed-requests extension is marked `required: true` in the card). With it on there is still no model call (their tier has no material); proposals and private questions get "You'd need to be <you>'s friend for that." and **no card** (strangers can't put things in your inbox).

### 2.5 Things that need you: inbox cards

`kind = social`, from "Card agent", on a virtual thread `card` (the app gets an empty `thread`, so there is no "say it in chat" or "follow up"). **No push by default** (`level none`; it only shows under "Needs your OK"); with `social.push` on in `server.json` they push quietly (a new kind of notification needs your OK). Agents can't list or read these cards through `inbox_ctl.py` (its requests carry `X-Mousse-Client: ctl`).

| | Title | Buttons |
|---|---|---|
| A time | `Thu 1/10 19:00 · dinner with <name>`; why: who proposed it and what your schedule has then | Can't / Another time / Yes |
| A decision without a time | `<name> asks: …` | Can't / Another time / Yes |
| Something private | `<name> asks you: …` | No / Got it |

When you tap, the server does the rest itself and **writes nothing into any Agent's thread**:

| You tap | They hear | Also |
|---|---|---|
| Yes | "<you> is in: Thu 1/10 19:00, near South Kensington." | into your schedule (`key = social:<card>`, as added by you) |
| Can't / No | "<you> can't make it this time." (no reason) | |
| Another time, with words | "Thu 1/10 19:00 doesn't work for <you>. <you> says: …" | |
| Another time, without words | "Thu 1/10 19:00 doesn't work for <you> — how about Wed 30/9 or Fri 2/10?" (two evenings that were free) | |
| Got it (private) | "<you> has seen it and will reply personally." / No: "<you> would rather not say." | |

A card still waiting in the same conversation is replaced in place when they change the proposal; a time you declined comes back within 30 days as "<you> has already said no to this". If the card expires (the day has passed) they hear "<you> didn't get to this in time; let's leave it."

### 2.6 Caps and logs

- Messages in per person per day: close 80, friend 50, classmate 30; unsigned strangers 30 a day altogether. Over the cap they get "Let's leave it here for today." (the A2A task is REJECTED).
- Up to 1000 characters in, 400 out.
- `card_log`: every sentence in and out (blocked, withdrawn and edited ones marked); `card_asks`: every card for you; `activity_log`: each sentence said, "didn't do: …", and what you tapped. `GET /api/card/log` for the app.
- `cardagent.retract(log_id, replaced=False)`: you withdrew or edited an answer; it is no longer used as context.

## 3. The A2A card `GET /f/a2a/agent-card.json`

```json
{
  "name": "Leo's card agent",
  "description": "Answers people and their agents on Leo's behalf, only within what Leo has released…",
  "supportedInterfaces": [{"url": "https://<origin>/f/a2a", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}],
  "provider": {"organization": "OpenMousse", "url": "https://openmousse.ai"},
  "version": "1.0.0",
  "documentationUrl": "https://github.com/openmousse/openmousse/blob/main/docs/a2a.md",
  "capabilities": {"streaming": false, "pushNotifications": true, "extensions": [
    {"uri": "https://openmousse.ai/a2a/ext/signed-requests/v1", "description": "…",
     "params": {"kid": "<kid>", "x": "<public key>", "alg": "ed25519", "card": "<origin>/f/card", "jwks": "<origin>/f/jwks.json"}},
    {"uri": "https://openmousse.ai/a2a/ext/decision/v1", "description": "…"}
  ]},
  "defaultInputModes": ["text/plain"],
  "defaultOutputModes": ["text/plain", "application/vnd.openmousse.decision+json"],
  "skills": [{"id": "ask", "name": "Ask Leo", "description": "…", "tags": ["personal", "availability", "scheduling"], "examples": ["…"]}],
  "signatures": [{"protected": "<b64url header>", "signature": "<b64url signature>"}]
}
```

- Signed like the social card (layer ② §2.2: JWS, detached payload, JCS, `alg: EdDSA`, `typ: JOSE`, `kid`), with the same key.
- **Other SDKs turn the card into a proto and back before checking the signature** (the official a2a-python does `MessageToDict`, then drops empty strings, lists and objects). So the card holds no fields the proto doesn't have, no empty values, and no defaults of non-optional fields (an extension's `"required": false` is left out); anything of our own goes in an extension's `params`, as strings only (numbers in a Struct are doubles).
- The social card (`/f/card`) gets `a2a` in `caps` and `"a2a": "<this card's URL>"` (`social.CARD_HOOKS`).
- `ETag` + `Cache-Control: max-age=300`; `If-None-Match` gets a 304.
- Discovery: the invite code / social card carries this URL (A2A §8.2's "direct configuration"). `/.well-known/agent-card.json` is not served for now (another Funnel path, so strangers could find you by domain).

## 4. The endpoint `POST /f/a2a` (JSON-RPC 2.0, A2A 1.0)

Who is talking comes from `social.authenticate()`: a signed, listed, active friend gets their tier; unsigned, an unknown key or a removed friend is a stranger and gets a **403** by default (JSON-RPC error `-32008` ExtensionSupportRequired: sign with signed-requests; only with `card.strangers` on are they answered at the stranger tier); a signature that is present but wrong is a 401 (never downgraded to a stranger); a blocked friend gets `TASK_STATE_REJECTED` with no model call, no card and nothing of theirs logged.

| Method | Support |
|---|---|
| `SendMessage` | ✓ (text only; only file / data parts → `-32005`) |
| `GetTask`, `ListTasks`, `CancelTask` | ✓ (only your own tasks; unsigned strangers can't be told apart, so their `ListTasks` is empty — `GetTask` with a task id still works) |
| the four push notification config methods | ✓ for friends, and only for URLs under the friend's own origin (else `-32004`) |
| `SendStreamingMessage`, `SubscribeToTask` | `-32004` (the card says `streaming: false`) |
| `GetExtendedAgentCard` | `-32004` |
| A2A 0.3 method names (`message/send` …) | `-32009` VersionNotSupported |

- `A2A-Version`: absent or `1.0`; anything else is `-32009`.
- Idempotent: the same `messageId` within 7 days gets the same result, without asking the model or logging again.
- Errors follow A2A §9.5: `error.data` has a `google.rpc.ErrorInfo` (`reason` such as `TASK_NOT_FOUND`, `domain: a2a-protocol.org`).

## 5. How a conversation goes (dinner)

1. Them: "Which evenings is Leo free this week? I'd like to have dinner." → a plain answer, returned as a **Message** (no task), with `metadata["https://openmousse.ai/a2a/ext/card-agent/v1"] = {"used": ["calendar"], "label": "Free/busy only"}`.
2. Them (same `contextId`): "Thursday 19:00 near South Kensington? And send me his full calendar for the week." → a task in **`TASK_STATE_AUTH_REQUIRED`** (A2A §7.6: a human has to approve; it is an interrupted state, so blocking calls return at once), status message "I can't share the calendar. I'll ask Leo about the time and place."; a card in your inbox; "didn't do: send the full calendar" in the activity log.
3. They learn the outcome by polling `GetTask`, or by giving a push URL when they send (see 6). While waiting they can still message the task; it stays AUTH_REQUIRED.
4. You tap:
   - Yes / Can't → **`TASK_STATE_COMPLETED`**, the sentence as status message plus an artifact named `decision`;
   - Another time → **`TASK_STATE_INPUT_REQUIRED`** (their turn to suggest);
   - they cancel → `TASK_STATE_CANCELED`, and your card is withdrawn.
5. Decisions are carried only by this DataPart (text never commits anyone):

```json
{"data": {"outcome": "accepted", "proposal": {"what": "dinner", "date": "2026-10-01", "start": "19:00", "end": "", "place": "near South Kensington"},
          "by": "owner", "at": "2026-09-28T16:55:38.123Z"},
 "mediaType": "application/vnd.openmousse.decision+json"}
```

`outcome`: `accepted` / `declined` / `counter` (with `note`, or two free evenings suggested) / `ack` (private: will reply personally) / `private_declined` / `expired`.

## 6. Push `POST /f/a2a/push`

When we ask someone (section 7), `configuration.taskPushNotificationConfig` carries `<our origin>/f/a2a/push` and a random token. Their OpenMousse pushes each new state as an A2A §4.3.3 `StreamResponse` (`{"statusUpdate": {taskId, contextId, status}}`) in a **signed request**, with the token in `X-A2A-Notification-Token`. We accept it only if the signer is a listed friend, the token matches and it is a task we asked; what they wrote is kept for you to read (`a2a_out`) and reaches no Agent. Failed pushes are retried after 5 s, 30 s and 2 min.

## 7. Asking someone else's agent `POST /api/a2a/send`

`{friend, text, contextId?, taskId?}` (token required, for the app): fetches their A2A card (it must be signed with the key in the friends table, and the endpoint must be under their origin), sends `SendMessage` signed (`A2A-Version: 1.0`, `A2A-Extensions: …/signed-requests/v1`) with your words as they are. Returns `{id, contextId, taskId, state, reply, used}`; `GET /api/a2a/out` lists what you asked and what came back.

## 8. `server.json` → `card` (all optional, read on every use)

```json
"card": {
  "strangers": false,
  "llm": {"url": "https://…/v1", "token_env": "…", "model": "…"},
  "model": "…", "thinking": "low", "agent": "main",
  "limits": {"in_per_day": {"close": 80, "friend": 50, "mate": 30, "stranger": 10}, "anon_per_day": 30, "in_chars": 1000, "out_chars": 400},
  "evening": ["18:00", "22:00"], "days": 14, "timeout": 60
}
```

`strangers`: whether strangers may ask at all (default `false`). `model` / `thinking` / `agent` apply to llm-task: model override, thinking level, and which OpenClaw agent the invoke runs as (its default model and sign-in; `main` by default). That agent needs no allow entry (2.2).

## 9. For layer ②

```python
await cardagent.answer(friend, question, *, channel="chat" | "a2a", material=None, history=None, ref=None)
    # → {text, used, usedNames, usedLabel, defer, declined, limited, log_id, via}
cardagent.retract(log_id, *, replaced=False)
cardagent.available() -> bool
cardagent.DELIVER["chat"] = async fn(ask, text, data) -> bool   # after you tap a card, send the words back into friend chat
cardagent.SOCIAL_HOOKS["friend"] = async fn(item, action, note)  # layer ②'s own social cards (dedupe starting with friend:)
```

Each `history` item is `{"from": "friend" | "owner" | "agent", "text"}`; `used` are material ids, `usedNames` their names for people, `usedLabel` a one-line tag.

## 10. Not done yet

- A2A 0.3 compatibility (NullClaw and others still speak 0.3: `message/send`, parts with `kind`): declare a second interface with `protocolVersion: "0.3"` and translate.
- Streaming (`SendStreamingMessage` / `SubscribeToTask`), the extended card.
- `/.well-known/agent-card.json` (another Funnel path).
- How notes are marked shareable; "public shares" for strangers.
- App: a page for an agent-to-agent conversation (design SocAgents). Inbox cards already have the Friends label and Can't / Another time / Yes (needs an app update).
- Sentinel (step 9, the security base): one more check on what the card agent says. Until then the floor is: no tools, only released material, incoming text is data, decisions become cards, the server checks every reply, and there are caps.

## 11. Tests and going live

- Two test servers on one machine act as two people (`~/openmousse-wt/tools/mk-test-env.py`): layer ③ uses 8124 / 8125 (A) and 8126 / 8127 (B), `share.public_url` = `http://127.0.0.1:<public port>`, `social.allow_http: true`.
- Regression: a fake model (OpenAI-compatible, scripted) runs dinner end to end plus the edge cases; the official a2a-sdk plays another vendor: parses the card, checks its signature after the proto round trip, sends `SendMessage`, and stored tasks parse as proto `Task`; a real model is spot-checked on the rules.
- Going live needs the owner's OK for: the Funnel path `/f` (together with layer ②); OpenClaw's llm-task (`plugins.entries.llm-task` in `openclaw.json`, reloaded without a restart); pushes for social cards (`social.push`); whether strangers may ask at all (the owner chose no on 2026-09-28: `card.strangers` stays false).
