# Changelog

[中文](CHANGELOG.zh-CN.md) · **English**

What changed in OpenMousse, newest first. A release is named after its date and tagged `v<version>` ([docs/releasing.md](docs/releasing.md)). The memory tree package keeps its own notes in [tree/CHANGELOG.md](tree/CHANGELOG.md).

## Unreleased (draft)

Changes since v2026.10.13-3.
- "Off" means off by default, and the line says how to turn it on. Nothing marked off turns on by itself.
- Where something runs on your own API key, plan or subscription, the line says so.
- `mousse` is `~/.openmousse/repo/bin/mousse`; `ln -s ~/.openmousse/repo/bin/mousse ~/.local/bin/mousse` puts it on your PATH.

### The built-in engine: OpenMousse without OpenClaw

- **It runs on its own.** Chat, Agents, background tasks, scheduled jobs and memory run inside the OpenMousse server, on models you pick. OpenClaw is no longer needed.
  - A first install on a machine without OpenClaw picks the built-in engine when you press Enter (or answer `builtin`, or set `MOUSSE_CLAW=builtin`).
  - Existing installs keep their claw. The Docker install is always built-in.
- **Models you pick.**
  - Where models come from: cloud providers with your own key (OpenAI, Anthropic, Google, xAI, DeepSeek, Kimi, Qwen, OpenRouter, Azure OpenAI, Amazon Bedrock and more), local servers found on their usual ports (Ollama, LM Studio, vLLM, llama.cpp, MLX), or any OpenAI-compatible address.
  - Choose in the app the first time it connects, or in a terminal with `setup_ctl.py`.
  - Different models per purpose (chat, background, writing, the card agent and Doorman, media): Settings → Keys → Models by purpose, or `setup_ctl.py use …`.
  - Cloud models bill your API key; local ones are free.
- **Your own subscriptions.**
  - **Claude Code:** the main chat can run on Claude Code you're signed in to on the server (`claude-code/opus`, `sonnet`, `haiku`). The models are listed once a signed-in Claude Code is found. They use your Claude Pro or Max plan, for your own turns only, at most 2 at a time; the rest wait.
  - **ChatGPT:** a ChatGPT Plus or Pro plan connects with **Continue with ChatGPT** in the app (`chatgpt/<model>`). Its tokens stay on your server. Background work uses the plan only after you allow it (`PUT /api/engine/chatgpt/background`).
  - **Grok:** a SuperGrok or X Premium+ subscription (`grok/<model>`). Sign in on the server with `setup_ctl.py grok login`; the app can't do it yet. Its tokens stay on your server, and `setup_ctl.py grok` shows how much of the allowance is used. This isn't a sign-in xAI supports for OpenMousse, and xAI may refuse it (403): then use `XAI_API_KEY` with `xai/<model>`, billed to your key. Background work uses the subscription only after `setup_ctl.py grok background on`; `engine.grok: false` turns it all off.
  - **Codex and Copilot:** your CLI logins can run the chat. Off: `engine.codex: true`, `engine.copilot: true`.
  - **Short calls on the Claude plan:** summaries and minutes can go to your Claude plan, with pay-as-you-go backups after it (`engine.use.json` / `engine.use.compact` = `["claude-code/sonnet", …]`).
  - **Picking them in the app:** Settings → Keys → Models by purpose has rows for One-shot answers and Compaction summaries. Each shows its model, whether that's your plan or pay-as-you-go, and what it switches to if it fails. Codex and Copilot plans aren't offered there.
  - **When a plan call fails,** it says why and runs only the backups you listed. It no longer runs the plan a second time or moves to a pay-as-you-go model on its own. With a backup, the plan gets at most half the time.
  - **More room for short calls.** Off: `engine.claude_code.burst: <n>`. When the usual slots are busy, up to that many extra Claude Code processes start for short calls, on your plan, at about 0.3 GB of memory each.
  - The card agent, Doorman and the API never use a plan.
- **Tool levels.** Every tool runs by itself, asks first, or is off (`engine.tools.levels`). Anything irreversible or outward waits in the inbox and runs exactly as you approved it.
- **Usage.** Tokens and cost by day, model and purpose: Settings → Keys → Usage, `/usage`, `mousse status`. Plan turns show as plan, not money.
- **Spending switches.**
  - Dollar caps per day, turn, Agent or purpose. Off: `engine.spend`.
  - A fallback chain when a model fails. Off: `engine.fallbacks`; backups bill pay-as-you-go.
  - Short, plain lines go to a cheaper model. Off: `engine.auto`.
  - Claude's hour-long prompt cache kept warm. Off: `engine.cache_warming`; each warm-up costs a small Anthropic charge.

### Conversations

- **Sessions.**
  - A new session starts each day at 04:00 (`engine.reset.at`; `idle_minutes` also resets after a quiet spell). Long chats are compacted automatically, with one summary call on `engine.use.compact` or the chat's model.
  - Earlier sessions can be resumed, forked, branched and exported as Markdown, JSON or HTML (keys masked) through `/api/engine/chats/<thread>/…`. Forks and branches have no app screen yet.
  - Each message carries the local time it was sent.
- **Slash commands.** Off: `engine.commands: true` (or `{"off": [...]}` to leave some out). The app lists them as you type `/`:
  - `/help`, `/status`, `/usage`, `/context`, `/tools`, `/model`, `/stop`
  - `/new`, `/compact`, `/resume`, `/export`
  - `/think`, `/queue`, `/btw`, `/bg`, `/goal`
- **Standing goals.** The Agent keeps working until a judge says the goal is met. Off: `engine.goal: true`. Each round costs extra turns plus a judge call; cap it with `max_turns` and `max_tokens`.
- **Session extras.**
  - Session titles. Off: `engine.titles: true`; one small call per session.
  - Picking up a turn a restart cut off. Off: `engine.loop.resume: true`.
  - Thinking level per chat or Agent: `/think` or `engine.think`.
- **Other sessions and hand-offs.** Agents can read other sessions (`engine.sessions.visibility`) and hand work to each other, at most 2 hops (`engine.handoff`).
- **From a terminal.** `mousse agent` sends one message to an Agent and prints JSON. `mousse chat` is a chat that counts as your own messages.
- **While a reply runs** (built-in engine):
  - Each step it shows is in plain words ("Checking the schedule").
  - A cut-in that came too late says Missed this turn right away, and is answered when the turn ends.
  - When the reply moves to a backup model, a line under it says from which model, why, and which one answers instead.
- **Retrying tasks** (built-in engine). A task cut off by a server restart says Interrupted by a restart. Failed, timed-out, stopped and interrupted tasks have Retry on their card and the task page: it starts over from the same brief, reports back to the same chat, and counts toward today's task allowance.

### Cybers, group chats and the Butler

All of this is off by default and needs the built-in engine. After you switch one on and restart the server, close and reopen the app to see it.

- **Cybers.** Thinking partners with their own persona, memory and model, beside your functional Agents. Off: `"cybers": true`.
  - A new Cyber, or a new functional Agent, starts from a sentence or a template: a couple of questions, a draft, then a trial chat. Draw its look or upload a picture.
  - A Cyber never uses a plan. It runs on the model you give it (or `engine.use.cyber`), billed to your key; local models are free. Its model page lists open-weight models first.
  - It remembers only what you've talked about with it. A change to its profile, whether from the chat, its page or the Cyber itself, comes as a before-and-after you approve, with versions you can undo. Looking back on its own every few rounds is off: `cybers.review`, a few extra calls each time.
  - You can give it functional Agents to ask: read-only, one round, at most 3 per reply. Health, diet and sleep Agents can't be given (`cybers.health_agents` adds more). Reading their output once a day is off for each Cyber (on its page; after `cybers.daily_at`).
  - Switching it on makes two, once: Iris, who hosts, and Zhe, a layman.
  - Contacts come first in the Agent bar: the Butler (or the card agent), Cybers, functional Agents and friends.
- **Group chats.** Cybers and functional Agents in one chat. Off: `"groupchat": true` (Cyber members also need `cybers`).
  - @ one, or @ all Agents for a round. The assistant, a Cyber or nobody hosts.
  - Each round's summary goes into the group's own memory, pinned for the next round.
  - The assistant and functional Agents speak on their usual models, which can be your plan; one @ all is about one call per member, plus one.
  - Health, diet and sleep Agents don't share a group with a Cyber.
- **Meetings.**
  - Listing by who started it. Off: `meetings.by_origin: true`. Meetings you start and run stay in your chat list; the ones the assistant starts, and the ones that settle by themselves, are filed away when they end and leave a minutes card. Meeting records, on the main chat's info page, finds them all.
  - Tell me when it's settled, with `meetings.by_origin` on: a meeting you start in the app runs to the end by itself (at most 2 rounds), and its to-dos go into your schedule and boards without asking.
  - Cybers can join a meeting; they don't take to-dos.
- **Podcast prep groups** (group chats and Cybers on). Each episode gets a group where the host (Iris, Zhe or nobody) keeps the outline pinned on top, and Put in hands material to the host. With friends (and the Butler on), prep is a friend group your Butler hosts; a recording time a friend suggests goes into your schedule only after you agree on its card.
- **Take to discuss** in Zen (Cybers or group chats on). Long-press a thought to talk it over with a Cyber, in a group, in a podcast or with a functional Agent, or to send its key points to a friend. The key points come from one tool-free call that counts as your own ask (your plan, if One-shot answers is one), and go out only after you've read them.
- **The Butler.** The card agent, upgraded. Off: `"butler": true`.
  - A profile, example answers for each tier that you change line by line (before-and-after, undoable), and what it has said.
  - Cybers it may consult: names and quotes come out first, and the answer is marked as that Cyber's view. Each consult is one tool-free call plus that Cyber's turn, pay-as-you-go.
  - It runs on the card agent's model (Models by purpose), never a plan.
- **Friend groups** (with the Butler). Group chats with friends: one Butler per server, and the owner's Butler hosts.
  - The group's owner signs the member list, and each member gets a sealed copy (end-to-end encrypted, through the relay without a public address). Both sides need this release.
  - Friends' words are material for the Butler only. They never reach your main chat or its tools, and anything they want in your schedule waits for your OK.
  - In a group the Butler answers at the tightest tier among the members, and someone who isn't your friend counts as a stranger.
  - There's no leaving a group or removing a member yet.

### Tools

- **Off until you turn them on, and only in your own turns:**
  - edits across several files at once, all or nothing: `engine.files.patch: true`;
  - commands on the server through an allowlist, with background runs: `engine.host_exec.agents.<id>`. Anything off the list asks first, and risky commands always ask;
  - a Docker sandbox per Agent for commands and a browser, online only through Doorman: `python3 server/errand.py setup` (needs Docker; not available inside the Docker install);
  - Python in the sandbox that calls the Agent's tools: `engine.tools.execute_code: true`;
  - your own Chrome, with your logins: `engine.chrome.agents.main: true` plus `CHROME_CDP_URL`. Every action asks first;
  - big tool sets offered on demand: `engine.tools.search: true`;
  - structured sub-tasks: `engine.tools.llm_task: true`;
  - diffs: `engine.tools.diff: true`.
- **Media.** Each kind has its own provider and bills pay-as-you-go per call; local options are free, and three can run on a Grok subscription (below).
  - Reading images and PDFs: `engine.use.vision`, `engine.use.pdf`.
  - Speech to text: `engine.use.stt`. Reading aloud: `engine.use.tts`.
  - Image generation: `engine.use.image`. Video understanding: `engine.use.video`.
  - Video and music generation: `engine.use.video_gen`, `engine.use.music`. Each video asks first, with a cost estimate.
  - With the Grok subscription signed in, image generation, video generation and reading aloud can run on it: `engine.use.image`, `engine.use.video_gen` or `engine.use.tts` = `grok/<model>`. They count against the subscription's allowance, not money, so spend caps don't stop them.
- **Web search.** Brave, Tavily, Exa, Perplexity, SearXNG or DuckDuckGo; Gemini, Grok, Kimi and Firecrawl only when named.
  - There's no `web_search` until you pick a provider: the app's Keys → Search, or `setup_ctl.py search`.
  - It bills your search provider's key. Opening a link (`web_fetch`) needs no setup.
  - With the Grok subscription signed in and `grok` as the search provider, searches go through the subscription first; xAI hasn't said whether search is included. `engine.grok.search: false` keeps them on `XAI_API_KEY`.
  - Searching public posts on X (`x_search`). Off: `engine.web.x_search: true`. It runs on `XAI_API_KEY`, which xAI bills by the posts it fetches; usage can't price it, so spend caps don't count it. `engine.grok.x_search: true` tries the subscription first.

### Automation

- **Scheduled jobs.** They run on the job's model, or `engine.use.background`. A job an Agent creates stays off until you approve its card.
  - A job can just run a script (`command`, listed in `engine.jobs.scripts`), with no model call.
  - It can skip the model when nothing changed (`watch`, `pre`).
  - It can retry on a backoff. Off: `engine.jobs.retry: true`.
- **Heartbeats.** One turn per beat. Off: `engine.heartbeat.<agent>`.
- **Triggers.** None until you set one up.
  - Webhooks at `/webhooks/<name>`, with signature checks: `engine.triggers.webhooks`.
  - An IMAP mailbox watched read-only: `engine.triggers.mail`.
  - Webhooks and mail are read once by a pay-as-you-go model, never a plan.
  - Small workflows with approval steps: `engine.flows`.
  - Reminders that fire on your own words, with no model call. Off: `engine.intents.enabled: true`.
  - One message as a background task: `POST /api/engine/bg` or `/bg`.
  - A standing-orders skill, for work an Agent looks after for good. You approve each order.
- **Notice cards.** A card can just tell you something, such as a weekly report (`inbox_ctl.py add --kind info`). It isn't pushed or sent to your channels, and Got it folds it away.
- **Emergency stop.** `mousse pause [--stop-running]` and `mousse resume`, or `POST /api/engine/pause`. It stops jobs, heartbeats, triggers, background work, write-back and goals; chatting and approving still work.

### Skills, hooks and MCP

- **Skills.**
  - Skills written for OpenClaw or Hermes work as they are. One whose programs, keys or OS are missing is hidden, and the settings API says why.
  - Per-skill settings and keys go under `engine.skills.entries`.
  - Every Agent sees a shared skills folder (`<data_dir>/skills`). Per-Agent lists: `engine.skills.agents`.
- **Skill market.** Off: `engine.skills.market.enabled: true`. No app screen yet.
  - `mousse skills search | install | update | list | remove`, from ClawHub, GitHub, skills.sh, agentskills.io sites or a SKILL.md link.
  - Downloads are scanned. Dangerous ones never install, and the rest wait for an inbox card.
- **Weekly skill review.** One call per Agent per week. Off: `engine.skills.weekly: true`.
- **Hooks and plugins.**
  - User hooks run your own scripts on engine events (`engine.hooks.<event>`); each hook is approved before it runs.
  - Code plugins. Off: `engine.plugins.enabled` and `mousse plugins install`. They run inside the server without a sandbox.
- **MCP servers** for your own turns: `mcp_servers`, `mousse mcp list | add | remove | probe`.
  - Their prompts become tools.
  - Sampling is off unless you give a server a pay-as-you-go model.
- **Connectors**, the outside apps you connect in the app:
  - Turn single tools off in `server.json`: `apps.<id>.disabled_tools`, or `enabled_tools` to allow only the ones listed (an empty list turns them all off). There's no screen for it yet.
  - With the built-in engine, each tool's description is cut to 600 characters (`apps.desc_max`), and ask-first cards expire after 24 hours (`apps.ask_ttl_hours`).

### Channels

- **Telegram, Discord and email.** Off: `channels.<kind>.enabled: true` with an account (tokens go in `secrets.env`), or `mousse channels …`. No app screen yet.
- **Who gets answered.**
  - You become the owner with a code made on your own device (`mousse channels owner-code`, then `/start <code>`).
  - Contacts are answered by the card agent after you approve them; strangers get nothing.
  - Email counts as yours only when it passes DMARC or aligned DKIM.
- **Forwarded and quoted text** is treated as data, never as your words.
- **A `message` tool** for Agents: messages to you go straight out; messages to a contact ask first.

### Operations

- **Health:** `mousse doctor [--fix]`, `mousse status`, `mousse logs`.
- **Backups:** `mousse backup create | verify | list` and `mousse restore`.
- **Updates:** `mousse update`.
  - The stable channel follows the newest release tag; `--channel main` follows main.
  - It takes a snapshot first and rolls back if the server doesn't come back up. It refuses to run inside Docker.
- **Config and upkeep:** `mousse config validate | get | set | unset`, `mousse security audit [--fix]`, `mousse sessions cleanup`.
- **Diagnostics:** `mousse diagnostics`, a zip without conversations, keys or emails.
- **Uninstall:** `mousse uninstall [--full]`, with a backup before `--full`.
- None of these call a model or print tokens.
- **With OpenClaw,** removing an Agent also clears what points to it elsewhere in `openclaw.json`, so OpenClaw can still load its config.

### APIs

- Off, `engine.api: true`: an OpenAI-compatible API (`/v1/models`, `/v1/chat/completions`, model `openmousse/<agent>`).
  - It uses its own tokens (`python3 server/tokens.py add api`).
  - It runs on the Agent's model, swapped for pay-as-you-go when that's a plan.
- Also off, on top of `engine.api`:
  - `/v1/responses`: `engine.api.responses: true`;
  - A2A 1.0 for your own other agents: `engine.api.a2a: true`.
- An ACP adapter for editors: `server/engine/acp.py`.

### Devices

- **Device nodes.** Off: `"engine": {"nodes": {}}`.
  - Pair a computer (`tokens.py pair --node`, or Settings → Hardware → Devices and nodes) and set each command to off, ask or allow.
  - Allowed commands run only in turns you started.
  - The Mac as hands: `mousse node pair | run | install`. Computer use on macOS asks before every action.
  - The phone as a node (location, photos, notifications): iPhone app 1.0.7, while the app is open.
- **Talk mode** in the iPhone app (1.0.7, built-in engine): you speak, it sends what you said and reads the reply aloud as it streams.
  - It needs transcription on the server (`OPENAI_API_KEY` or `engine.use.stt`), billed to your key.
  - The voice is the phone's own.

### Setup and moving over

- **First connect.** The app walks through three steps when a server has no model yet: model, web search, memory. Then you can name the assistant and give it a line of character and a look. The settings center covers connections, keys, usage and hands.
- **Trial install.** `MOUSSE_TRIAL=1` (or `--trial`), on Linux and macOS: the built-in engine, this computer only, nothing public, OpenClaw untouched. Everything installed is listed in `~/.openmousse/install-manifest.json`.
- **Docker.** `docker compose -f docker/compose.yaml up -d --build` ([docs/docker.md](docs/docker.md)).
- **From OpenClaw or Hermes.** `mousse migrate openclaw|hermes` reports first; `--apply` writes, after a backup. Jobs arrive paused and sign-in tokens aren't copied. The installer offers this when it finds either.
- **Machines without systemd.** `check.sh` has a section for the built-in engine and works on these machines, and the installer prints how to start the services yourself.

### Memory

- **Memory tree 0.7** ([tree/CHANGELOG.md](tree/CHANGELOG.md)): branches (the trunk is your profile, plus People and one per Agent), rules, duplicate checks, leaves that keep where they were said, trust tiers and manuals.
- **Keys kept out of memory** (new in 0.7). Anything that looks like a key or token becomes a placeholder before a leaf is stored, whether it comes from an AI platform, write-back, pruning, the admin page or a move into your profile. `TREE.md` and the profile handed to AI platforms are cleaned on the way out. Leaves already in the tree aren't rewritten.
- **Weekly pruning.** One inbox card on Sundays, with a choice per step.
  - On with the built-in engine and the open-source tree; off with `"tree": {"prune": false}`.
  - It costs one unattended turn a week.
- **Memory search** over memory files, digests and past chats: on, keyword-based, nothing leaves the server.
- **Search by meaning.** Off: `engine.recall.embeddings`.
  - Providers: Gemini, OpenAI, or any OpenAI-compatible service, a local Ollama included.
  - When on, the text of memory files, digests, chats and tree leaves goes to that service, billed to your key (local is free).
- **Write-back.** On with the built-in engine; off with `"engine": {"writeback": false}`.
  - After a chat goes quiet, up to 3 leaves in your own words go into the tree.
  - It uses a pay-as-you-go model, never a plan, at most 40 calls a day.
  - Changes to the profile, rules, manuals and skills come as cards first. What other people say is held for the weekly card.

### Pages, Today and the app

- **Pages.** An Agent can publish a sandboxed web page for what boards can't draw, and you can pin it to a board. Reading your data needs your OK for each version. On the phone with app 1.0.6 or later.
- **Today.**
  - Categories (study, life, work), undated to-dos, a filter and Dismiss. Off: `"today": {"categories": true}`.
  - An engineering board card fed by a to-do file or a GitHub Projects board. Off: `today.engineering`.
- **The Memory tab** (the fourth tab):
  - Memory: the last 7 days' new memories as rings, and the ones waiting for you, with Keep and Don't keep.
  - Direction (your goals) and All memories: about you, friends, journal, memory notes and sources.
  - Search your memories, with no model call. A leaf shows where it came from: the original words, when it comes up, and the chat it was said in.
  - On Sundays the weekly pruning card sits on top, decided item by item: Approve, Change or Decline. Change redoes that item from your note with one tool-free call: on your plan when One-shot answers (`engine.use.json`) is one, otherwise pay-as-you-go.
- **Replies.** On the iPhone you can select part of a reply and quote, save or forward just that. Every reply has ⋯ with Copy message.
- **Look.** Dark by default, and a new mark on the sign-in, setup and About screens: a gold ring with a cyan dot.
- **App 1.0.6.**
  - Scan pairing and friend QR codes, and share a card as an image.
  - Flipper Zero over Bluetooth.
  - Pages and videos on the phone, and a native date picker.
  - Podcast recording keeps going in the background, and widgets open Today.
  - On Android, the app reaches http servers, keeps drafts, Back closes sheets, and forms stay above the keyboard.
- **App 1.0.7 (iPhone).** Location and speech for the phone node and Talk, native modules for later features, and clearer permission text. Android stays on 1.0.6 for now.

### Before you update

- **No migration needed.** Existing installs keep their claw, models and settings.
- **Coming from v2026.10.13-3.** That release has no `mousse update`, so run the installer again (`curl -fsSL https://openmousse.ai/install | bash`): it pulls the new version, reinstalls the dependencies and the memory tree, and leaves `server.json` as it is. From this release on, `mousse update` does it.
- **Optional owner migration.** `mousse owner migrate` replaces the old value that stands for "you" in the database (`leo`) with `owner`. It only reports unless you add `--apply`, which backs up first. Nothing depends on it.
- **Profile tags.** Lines you confirmed are tagged `[U]`. The old `[L]` and `[Gr]` are still read, and old profiles aren't rewritten.
- **The app opens dark.** Until now it followed the system every time; now it's dark unless you choose otherwise, on existing installs too. Settings → General → Appearance switches back (System, Light, Dark), and the app remembers the choice.
- **The fourth tab is Memory.** Goals moved into it as Direction, and the weight chart moved to the fitness board's Long term section. Profile, memory notes, journal and friend notes moved from Settings → More to the Memory tab.
- **Long-press on the iPhone selects text.** Long-pressing a finished reply now selects its text, and the menu adds Quote this, Save this and Forward this. Copying the whole message and the other actions are under the reply's ⋯.
- **`/mcp` checks where requests come from.** A request whose Host isn't localhost, the specific address the server listens on, or your `share.public_url` now gets 421, and a mismatched Origin gets 403. If an AI platform or claw reaches `/mcp` by another name or address, add it to `mcp.allowed_hosts` in `server.json` (read each time, no restart).
- **Redaction needs tree 0.7.0.** Keys are kept out of memory only once the server's Python environment has tree 0.7.0. `mousse update` installs it and restarts the services; Docker gets it when you rebuild the image. After a plain `git pull`, run `~/.openmousse/venv/bin/python -m pip install ~/.openmousse/repo/tree` and restart. Until then nothing is redacted, and nothing says so; `mousse doctor` shows the installed version.
