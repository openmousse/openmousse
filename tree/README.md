# OpenMousse · world tree (tree)

[中文](README.zh-CN.md) · **English**

**One self-hosted personal memory that all of your AIs attach to.**

You tell ChatGPT "I switched breakfast to oats"; that evening you ask Claude what to eat tomorrow and it already knows. Your OpenClaw, Notion AI and Claude Code too. The memory lives on your own server, in one SQLite file; each platform reads and writes it over MCP, and every entry keeps its source and date.

No LLM calls, so no model cost of its own; the model cost is whatever your platforms already charge.

## The problem it solves

Every AI platform has its own "memory", none of them talk to each other, and you cannot take it with you. The world tree turns that around: **memory is the trunk, platforms are branches**. Whatever any platform learns about you goes back to the tree; before any platform speaks, it reads the tree.

## Install (Python 3.11+)

```bash
pipx install "git+https://github.com/openmousse/openmousse#subdirectory=tree"      # or pip install --user
mousse-tree init --name YourName --tz Europe/London          # create the db, generate one token per platform
mousse-tree install-service                                 # systemd, listens on 127.0.0.1:8787 only
```

If you run OpenClaw, one more step lets your agents search the tree's export:

```bash
mousse-tree install-openclaw        # backs up openclaw.json, adds the export directory to memory.search.extraPaths
systemctl --user restart openclaw-gateway
```

Profile: `mousse-tree init --profile ~/.openclaw/workspace/USER.md` points at an existing USER.md, or write one in the admin page. `- bullet` lines under `## sections` become searchable.

## Exposing it to the internet

Claude.ai, ChatGPT, Gemini and Notion connect from their clouds, so the tree needs a public HTTPS address. The least effort is Tailscale Funnel (free):

```bash
tailscale funnel --bg --set-path=/t http://127.0.0.1:8787/t
tailscale funnel --bg --set-path=/m http://127.0.0.1:8787/m
mousse-tree init --host <machine>.<tailnet>.ts.net    # add to the Host allowlist, otherwise the MCP SDK answers 421
systemctl --user restart mousse-tree
```

Only the `/t` and `/m` prefixes are exposed. Do not expose the admin page `/ui` through Funnel; open it via `tailscale serve` (tailnet only) or an SSH tunnel.

## Connecting platforms

```bash
mousse-tree urls     # prints each platform's URL (contains the secret token, only look at it in your own terminal)
```

| Platform | Where | Which address |
|---|---|---|
| Claude.ai | Settings → Connectors → Add custom connector, no auth | `/t/<token>/mcp` |
| ChatGPT (Plus) | Settings → Apps & Connectors → Advanced → Developer mode → Create, auth None | `/t/<token>/mcp` |
| Gemini | Web Settings → Connected Apps → Add a custom app (officially US only) | `/t/<token>/mcp` |
| Notion (Business+) | Custom Agent → Tools & Access → Custom MCP server, auth Bearer token | `/m/mcp` + token |
| Claude Code | `claude mcp add --transport http tree <url>` | `/t/<token>/mcp` |

One token per platform, so the server knows who wrote what without asking the model.

Then add one line to each platform's custom instructions, and switch off the platform's built-in memory:

> Call profile and recall at the start of a conversation to know me; when I state a new fact, preference, decision or update about myself, call remember.

## Tools

| Tool | Purpose |
|---|---|
| `profile()` | The whole profile, read once at the start |
| `recall(query, limit)` | Keyword search (FTS5 trigram, works for Chinese and English) |
| `remember(text, kind, tags, observed_at, supersedes)` | Write one entry; kind = fact / preference / decision / event |
| `recent(days)` | What each platform wrote in the last few days |
| `forget(memory_id)` | Forget: clears the text, keeps id and date |

## Admin page

Open the admin link printed by `mousse-tree urls` (`http://127.0.0.1:8787/ui#key=…`): browse memories by source, edit, forget, confirm pending entries, edit the profile. The page's API only answers requests that carry the admin key (`ui_token` in `config.json`); the browser remembers it after the first visit. `require_confirm: true` in `config.json` puts platform writes into "pending" until confirmed.

## Command line

```bash
mousse-tree recall --q breakfast
mousse-tree recent --days 7
mousse-tree add --source myclaw --kind decision --text "…"    # your own agents can write too
mousse-tree stats
```

## Design

- One memory = one sentence + kind + tags + source + observed_at + status. A change `supersedes` the old entry instead of appending a contradiction.
- Forgetting clears the text and keeps the skeleton, so it stays auditable.
- The profile (USER.md) is read-only into the tree; edit it in the admin page or the file.
- The export `TREE.md` serves agents that do not speak MCP (such as OpenClaw's memory_search).
- Not there yet: semantic recall (embeddings), conflict detection, multi-user. PRs welcome.

## License

MIT
