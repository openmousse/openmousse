# packs/core

[中文](README.zh-CN.md) · **English**

The layer you get right after install: how the main chat and the Agents cooperate, journal, world tree, daily close. The installer (`install.sh` at the repository root → `packs/core/setup.py`) wires it into your OpenClaw.

| Path | What it is |
|---|---|
| `skills/handoff` | The main chat hands a question to the Agent that owns it (`scripts/ask_agent.py` → the server's `/api/chat/relay`) |
| `skills/agent-builder` | Create / delete Agents from chat (`server/agent_ctl.py`) |
| `skills/journal` | Feelings, thoughts and decisions the user mentions go into a journal (`scripts/journal.py` → the `journal` table; readable under "Me → Journal" in the app) |
| `skills/memory-tree` | Write new facts about the user to the world tree (`mousse-tree add`), read it before answering |
| `scripts/daily_close.py` | At 03:45 sends "【自动触发】日结" (daily close) to every thread that talked today, so each agent writes its conclusions to memory before the 04:00 session reset |
| `scripts/mousse_common.py` | Shared by the scripts: reads the server address, the `local` token, the database path and the timezone from `~/.openmousse/server.json` |
| `systemd/` | Templates for `openmousse-server` and `openmousse-daily-close.timer` |
| `setup.py` | Second half of the installer (the first is `install.sh`): writes server.json, symlinks skills, patches openclaw.json (backup + validate), installs the services |

Commands inside the skills use fixed paths, `~/.openmousse/repo/…` (a symlink to the repository, created by the installer) and `~/.openmousse/venv/bin/…`, so the skill directories can be symlinked straight into a workspace and a `git pull` takes effect immediately.

What the installer changes in `openclaw.json` (each time: backup to `~/.openmousse/backups/`, then `openclaw config validate`, restore on failure):

- `agents.defaults.skills`: appends these four skills if the key is a list; if the key is absent (= unrestricted) it is left alone
- `gateway.http.endpoints.chatCompletions.enabled = true`: the app's chat goes through the Gateway's OpenAI-compatible endpoint (still loopback only)
- `session.reset = {daily, 04:00}`: one conversation per day, earlier days in the history page
- `tools.deny` gets `ask_user`: nobody can answer a tool prompt through the app channel, it would hang the session
- `memory.search.extraPaths` gets `<openclaw>/shared/digest`: the main chat can search each Agent's daily digest

The main agent's `AGENTS.md` gets a `## OpenMousse` section appended (daily close, automatic triggers, Agent cooperation, no `ask_user`, journal and world tree). Removing that section keeps chat working; the Agents just stop cooperating.
