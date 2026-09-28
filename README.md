# OpenMousse

[中文](README.zh-CN.md) · **English** · [openmousse.ai](https://openmousse.ai)

**A personal-agent app for people who run a claw: [OpenClaw](https://github.com/openclaw/openclaw), or any agent with an OpenAI-compatible API.** Chat with your agent from your phone, give each part of your life its own Agent, and keep one memory that Claude, ChatGPT, Gemini and your agent all share. Self-hosted: your machine, your model accounts, your data.

<p align="center">
  <picture><source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/en-today-dark.png"><img src="docs/screenshots/en-today.png" width="30%" alt="Today: suggestion cards from two Agents"></picture>
  <picture><source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/en-chat-dark.png"><img src="docs/screenshots/en-chat.png" width="30%" alt="Chatting with the agent"></picture>
  <picture><source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/en-fitness-dark.png"><img src="docs/screenshots/en-fitness.png" width="30%" alt="A Fitness Agent's dashboard"></picture>
</p>

- **Your agent in your pocket.** An iOS and web app for the claw you already run: streaming chat, voice notes, photos and files, push notifications.
- **One Agent per part of your life.** Fitness, meals, sleep, job applications… each is a real OpenClaw agent with its own memory, skills and dashboard. Ask for one in chat and it gets built.
- **It comes to you.** A morning report, suggestion cards that refresh when new data arrives, and a daily digest so every Agent remembers yesterday.
- **One memory for every AI.** The [memory tree](tree/) is an MCP server that Claude.ai, ChatGPT, Gemini and Notion connect to. It also works without OpenClaw.

**What's OpenClaw?** An open-source personal AI agent that runs on your own machine ([openclaw.ai](https://openclaw.ai)): skills, scheduled jobs, chat channels such as Telegram, any model. OpenMousse doesn't replace it; it adds an app, Agents and a shared memory on top.

**Another claw?** OpenMousse works best on OpenClaw: every Agent is a real OpenClaw agent, and background tasks, exec approvals and scheduled jobs show up in the app. Any other claw or agent with an OpenAI-compatible chat API works too (Hermes Agent, nanobot and Letta Code have presets); give the installer its name or URL. Chat, Agents (each one a conversation of its own that knows its role and yesterday's digest), boards, goals, the schedule, Zen, the memory tree and notifications all work; what only OpenClaw can do (cutting into a running reply, background tasks, exec approvals, scheduled jobs, model billing) is hidden. Details: [server/README.md](server/README.md#other-claws).

**Not OpenMuse.** [CopilotKit's OpenMuse](https://github.com/CopilotKit/openmuse) is an agent *computer* (browser, terminal, email, forms) with its own agent built in. OpenMousse brings no agent of its own: it builds on your OpenClaw and focuses on your life data, proactive Agents and a memory every AI shares.

**Status: early.** The author uses it every day; installs on other machines are still being tried, so install reports are very welcome. The iPhone app's public TestFlight link comes after Apple's beta review.

### What leaves your machine

Conversations, memory and health data stay on your server. By design, these go out:

- messages to the model providers you configure in OpenClaw;
- push notifications through Expo and Apple (a title and a short preview);
- voice notes to the transcription service in `server.json` (OpenAI by default; point `transcribe_url` at your own Whisper server to keep them local);
- if you connect Claude.ai / ChatGPT / Gemini / Notion to the memory tree, they read and write it through the endpoint you expose.

More in the [privacy policy](https://openmousse.ai/privacy.html) and [SECURITY.md](SECURITY.md).

## Parts

| Directory | What it is | Status |
|---|---|---|
| [`tree/`](tree/) | The memory tree: one memory that Claude / ChatGPT / Gemini / Notion and your claw all attach to (over MCP). The app's "Memory" page is this | Usable (matured before the app) |
| [`app/`](app/) | iOS / Web client (Expo): chat, Agents, Today, Goals, Memory. Connects to your own server; you pick the name and icon | Usable (boards still assume the author's data sources, see packs) |
| [`server/`](server/) | Thin API layer (FastAPI): token auth, chat relay, Agent create / delete, board data, push, hosts the web build | Usable |
| [`packs/core/`](packs/core/) | What you get right after install: Agent-to-Agent handoff, creating Agents from chat, journal, the world-tree skill, the daily-close timer, and the installer itself | Usable |
| other `packs/` | Optional feature packs: fitness, diet, sleep, applications… each pack = one Agent's brief, tables, board cards, triggers | Being ported from Grava |

Design principles:

- **Blank slate.** After install there are no preset life domains. Say "I want to track sleep and weekly runs" and it creates an Agent for you. The author's own Agents ship as packs, to be used as templates.
- **Data sources are never locked in.** A pack only declares what data it needs (workouts, meals, sleep, body metrics), not where it comes from. Sources can be Apple Health, any health / fitness / diet app that exposes MCP, a small adapter script, or just telling the agent in chat. If the software you already use has MCP, plug it in; if not, write a tiny adapter.
- **Memory is the trunk.** The memory tree is not a side feature; it is the app's memory layer. Every platform and every Agent is a branch on the same tree.

## Install

Prerequisite: a Linux machine with OpenClaw installed and a model configured (`openclaw onboard` done, Gateway running). Then one command:

```bash
curl -fsSL https://raw.githubusercontent.com/openmousse/openmousse/main/install.sh | bash
```

It asks four questions (language, where OpenClaw lives, your timezone, what to call the assistant) and does the rest: clones the repo, installs Python dependencies into `~/.openmousse/venv`, writes `~/.openmousse/server.json`, generates a phone token, wires the [`packs/core`](packs/core/) skills and the daily-close timer into your OpenClaw (backing up `openclaw.json` first and validating afterwards), installs systemd services, and finally prints how to connect your phone. Two more questions are optional: an Obsidian vault folder already synced to the server (the memory tree and the thinking space then live in it), and whether to open up public HTTPS through Tailscale Funnel (AI platforms such as Claude.ai, ChatGPT, Gemini, Notion or any other MCP client can then reach the memory tree, shares get links, and friends can add you). Running it again is safe: it only fills in what is missing.

Easiest if the machine is on Tailscale: the server binds to its Tailscale address and a phone with Tailscale can connect directly. Otherwise it listens on localhost only; expose it with `tailscale serve` or a reverse proxy as HTTPS.

After install you have a blank slate: no Agents, boards show "no data source yet". Say "make me an Agent for sleep" in chat and it builds one; log things in chat, or attach a data source (see packs).

## Two ways to get the app

**1. Install the author's TestFlight build** (fastest; public link coming after Apple's beta review): after install, the connection page asks for your server address and token. The assistant's name inside the app comes from your server (`app_name` in `server.json`); Agents you create yourself; data is your own. The home-screen icon and name were fixed at build time; to change those, use option 2.

**2. Build it yourself**: clone the repo, in `app/` copy `app.local.example.json` to `app.local.json` with your own name, bundle ids and Expo project, put your icons in `app/assets/local/`, then `eas build -p ios --profile production`. See [`app/README.md`](app/README.md).

The server side is the same either way: [`server/README.md`](server/README.md).

## Origin

OpenMousse was extracted from the author's personal system, Grava. Grava is the author's own instance; OpenMousse is the shell anyone can install.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Install reports and data-source adapters are the most useful things right now.

## License

[AGPL-3.0](LICENSE), © 2026 Leo Zhou. You can run, study, change and share OpenMousse for free. If you offer a changed version to other people over a network, you have to publish your changes under the same license. Running it unchanged, or changing it just for yourself, asks nothing of you.

Want to build it into a product without publishing your changes, or run it as a paid service? Open a [GitHub issue](https://github.com/openmousse/openmousse/issues) about a commercial license.

Versions published before 28 September 2026 (up to commit `b8902a8`) were released under the MIT license and remain available under it; everything after is AGPL-3.0. Template code from Expo in `app/` keeps its MIT notice ([app/LICENSE-EXPO](app/LICENSE-EXPO)).
