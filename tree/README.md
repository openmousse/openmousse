# OpenMousse · memory tree (tree)

[中文](README.zh-CN.md) · **English**

**One self-hosted personal memory that all of your AIs attach to.**

You tell ChatGPT "I switched breakfast to oats"; that evening you ask Claude what to eat tomorrow and it already knows. Your OpenClaw, Notion AI and Claude Code too. The memory lives on your own server, in one SQLite file or in a folder of Markdown notes (say, inside your Obsidian vault, so you can read and edit it on your phone); each platform reads and writes it over MCP, and every entry keeps its source and date.

No LLM calls, so no model cost of its own; the model cost is whatever your platforms already charge.

## The problem it solves

Every AI platform has its own "memory", none of them talk to each other, and you cannot take it with you. The memory tree turns that around: **memory is the trunk, platforms are branches**. Whatever any platform learns about you goes back to the tree; before any platform speaks, it reads the tree.

## Install (Python 3.11+)

```bash
pipx install "git+https://github.com/openmousse/openmousse#subdirectory=tree"      # or pip install --user
mousse-tree init --name YourName --tz Europe/London          # create the db, generate one token per platform
mousse-tree install-service                                 # systemd, listens on 127.0.0.1:8787 only
```

Language: `mousse-tree init --lang en` (or `zh`) sets the language of everything the platforms see (instructions, tool descriptions, tool replies) and of the command line. Without it, the first `init` follows `LANG` (`zh…` → Chinese, anything else → English); configs created before this option keep Chinese. Restart the service after changing it.

If you run OpenClaw, one more step lets your agents search the tree's export:

```bash
mousse-tree install-openclaw        # backs up openclaw.json, adds the export directory to memory.search.extraPaths (--openclaw-home DIR for a non-default OpenClaw)
systemctl --user restart openclaw-gateway
```

Profile: `mousse-tree init --profile ~/.openclaw/workspace/USER.md` points at an existing USER.md, or write one in the admin page. `- bullet` lines under `## sections` become searchable.

## Storage: SQLite or a folder of Markdown notes

By default the memories live in `~/.mousse-tree/tree.db`. You can keep them as one Markdown note per memory instead, for example in a folder of your Obsidian vault:

```bash
mousse-tree migrate markdown --notes ~/vault/Memory --profile-note Profile.md   # existing memories become notes; tree.db is kept
systemctl --user restart mousse-tree
```

On a new install: `mousse-tree init --storage markdown --notes ~/vault/Memory`.

- Each note's properties (front matter) hold `id`, `kind`, `source`, `observed_at`, `status`, `tags`, `supersedes`, `created_at` and `updated_at`; the body is the sentence. The file name is the date plus the start of the sentence.
- The folder root holds only current memories. Superseded ones move to `Archive/` (`归档/` in Chinese), and anything you move there stops counting. Forgetting turns the note into an empty shell (properties only, renamed to its id) in `Archive/`, drops it from the index and logs one line without the content. If a sync service (Obsidian Sync, iCloud, git…) carries the folder, its version history may still hold old copies for a while.
- The notes are the source of truth. SQLite (`index.db`) is only the search index plus the activity log, rebuilt from the notes whenever the folder changes; the service looks every 5 seconds, so a note you edit on your phone shows up in the next recall. `mousse-tree rebuild` rebuilds it by hand.
- Notes you create by hand without properties count too (id from the path, source `owner`). A note with broken properties is skipped while everything else keeps working, and the admin page lists the problem (so does `mousse-tree check`). Symlinks and hidden files are ignored, so the MCP tools never reach outside the folder.
- `--profile-note Profile.md` keeps a copy of the profile in the folder, synced both ways with `profile_path`: edit either one. If both change at once the note wins and the other version goes to `~/.mousse-tree/profile-conflicts.md`. It is a copy rather than a symlink on purpose: OpenClaw's memory search skips symlinked files.
- `mousse-tree migrate sqlite` switches back; the notes are written back into `tree.db`.

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

Open the admin link printed by `mousse-tree urls` (`http://127.0.0.1:8787/ui#key=…`): browse memories by source, edit, forget, confirm pending entries, edit the profile. The page's API only answers requests that carry the admin key (`ui_token` in `config.json`); the browser remembers it after the first visit. The page itself follows your browser's language. `require_confirm: true` in `config.json` puts platform writes into "pending" until confirmed.

## Command line

```bash
mousse-tree recall --q breakfast
mousse-tree recent --days 7
mousse-tree add --source myclaw --kind decision --text "…"    # your own agents can write too
mousse-tree stats
mousse-tree check      # Markdown storage: notes with format problems
mousse-tree rebuild    # Markdown storage: rebuild the index from the notes
```

## Design

- One memory = one sentence + kind + tags + source + observed_at + status. A change `supersedes` the old entry instead of appending a contradiction.
- Forgetting clears the text and keeps the skeleton, so it stays auditable.
- Storage is a SQLite file or a folder of Markdown notes (see Storage); the tools, the export and the admin page are the same either way.
- The profile (USER.md) is read-only into the tree; edit it in the admin page or the file.
- The export `TREE.md` serves agents that do not speak MCP (such as OpenClaw's memory_search).
- Not there yet: semantic recall (embeddings), conflict detection, multi-user. PRs welcome.

## License

[AGPL-3.0](LICENSE), like the rest of OpenMousse; for a commercial license open a [GitHub issue](https://github.com/openmousse/openmousse/issues). Versions published before 28 September 2026 were MIT and remain available under it.
