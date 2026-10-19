# OpenMousse · memory tree (tree)

[中文](README.zh-CN.md) · **English**

**One self-hosted personal memory that all of your AIs attach to.**

You tell ChatGPT "I switched breakfast to oats"; that evening you ask Claude what to eat tomorrow and it already knows. Your OpenClaw, Notion AI and Claude Code too. The memory lives on your own server, in one SQLite file or in a folder of Markdown notes (say, inside your Obsidian vault, so you can read and edit it on your phone); each platform reads and writes it over MCP, and every entry keeps its source and date.

No LLM calls, so no model cost of its own; the model cost is whatever your platforms already charge.

## The problem it solves

Every AI platform has its own "memory", none of them talk to each other, and you cannot take it with you. The memory tree turns that around: one tree that every platform reads and grows. **The trunk is your profile, branches are areas of your life (and one per agent), leaves are individual memories.** Whatever any platform learns about you, or about the people around you, goes back to the tree; before any platform speaks, it reads the tree and its rules. Once a week the tree is pruned, and only with your OK.

## Install (Python 3.11+)

```bash
pipx install "git+https://github.com/openmousse/openmousse#subdirectory=tree"      # or pip install --user
mousse-tree init --name YourName --tz Europe/London          # create the db, generate one token per platform
mousse-tree install-service                                 # starts at login (Linux: systemd; macOS: launchd), listens on 127.0.0.1:8787 only
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

- Each note's properties (front matter) hold `id`, `kind`, `source`, `observed_at`, `status`, `branch` (`"[[People]]"`; `"[[Profile]]"` = straight on the trunk), `tags`, `supersedes`, `created_at` and `updated_at`, and optionally (0.4) `quote`, `platform`, `thread`, `message` (where it was said) and `cues` ("useful when"); the body is the sentence. The file name is the date plus the start of the sentence. Properties you add yourself in Obsidian are kept as they are whenever the tree rewrites a note (confirming, superseding, moving it).
- Branches are notes too, one per branch in `branches/` (see below), and the rules live in `rules.md` at the folder root; neither counts as a memory.
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

> At the start of a conversation, call profile to read my profile and the memory tree's rules, and follow them. When I share something new about myself, the people around me or a standing commitment, recall first: if it's already there, supersede the old entry; otherwise remember it on the right branch.

## Tools

| Tool | Purpose |
|---|---|
| `profile()` | The profile, the tree's rules and the list of branches, read once at the start |
| `recall(query, limit)` | Keyword search (FTS5 trigram, works for Chinese and English); pending entries are included and marked |
| `remember(text, kind, tags, observed_at, supersedes, branch, quote, conversation, cues)` | Write one entry; kind = fact / preference / decision / event; branch is optional (see below); quote, conversation and cues are optional (see below) |
| `recent(days)` | What each platform wrote in the last few days |
| `forget(memory_id, also_profile)` | Forget: clears the text, keeps id and date; lists profile lines that came from it (see below) |
| `find_manual(query, limit)` | Find a manual (0.6, see below) for what the user said: name, when to use it, a one-line description, a score |
| `read_manual(name)` | A manual's body; a clear "no such manual" when there is none, so the caller stops and says so |

## Branches, rules and weekly pruning

- **Shape**: the trunk is the profile (`Profile`, or `档案` in Chinese; the name is fixed on first run). Branches follow areas of life (large branches) and functions (small branches, one per OpenMousse agent); each leaf hangs on one branch. A fresh tree has the trunk and one branch, **People** (aliases: family, partner, friends, classmates, colleagues, commitments). OpenMousse grows a small branch for each agent you create; everything else grows at pruning time. Upgrading from 0.2 leaves every existing memory where it was (no branch = the trunk) and adds People on first run.
- **Where branches live**: SQLite storage keeps them in the `branch` table of `tree.db`. Markdown storage keeps one note per branch in `branches/`: the file name is the branch name, the properties are `parent` (`"[[Profile]]"` for a large branch), `aliases`, `agents` (agent ids that write here by default) and `order`, and the first line of the body says what the branch covers. Edit them in Obsidian like any note.
- **Where a new leaf goes**: the branch the writer names (`branch`, a name or an alias) > the branch of the leaf it supersedes > the writing agent's own branch (the one listing it in `agents`; the CLI reads the agent from `MOUSSE_AGENT` and records it as the source) > a branch whose name or alias matches one of the tags > the trunk.
- **Duplicates**: before writing, the tree compares the sentence (without markup and dates) with current leaves and profile points. 0.9 or more similar is not written; the reply points at the existing one so the writer can supersede it. 0.6 or more is written, and the reply lists the near ones.
- **Rules**: `rules.md` in the tree folder (the notes folder for Markdown storage, `~/.mousse-tree` for SQLite), written once in your language with your name in it, never overwritten afterwards. `profile` hands it to every platform together with the profile and the branches, so edit it to change how every AI remembers. Empty it to have no rules; delete it to get the default back.
- **Weekly pruning**: `mousse-tree prune-context` lists what could be pruned: leaves that held for two weeks (they can move into the matching profile section), recent-life leaves older than 30 days, near-duplicates, misplaced leaves and branches with more than 30 leaves. A model writes a plan (`merge`, `rewrite`, `archive`, `promote` into the profile, `branch` to move, `sprout` a new branch) and `mousse-tree prune-plan --file plan.json` checks and stores it; `mousse-tree prune-apply` carries it out, skipping steps already done. OpenMousse runs this every Sunday evening: its main chat writes the plan, the plan arrives as an inbox card, and nothing changes until you tap *Prune as planned*.
- **Sources and "useful when"** (0.4): a leaf can carry the user's own words (`quote`) and the conversation it came from (`conversation` over MCP; `--thread` / `--message` / `--platform` on the CLI, which OpenMousse fills in from `MOUSSE_THREAD` / `MOUSSE_MESSAGE`). `recall` shows the quote. `cues` are two to five ways the user might ask about it later; they are searched together with the text and tags and go into the export, so a differently worded question still finds the leaf. Weekly pruning checks merges, rewrites and profile lines against what the user actually said (when the conversation is on the same server, i.e. OpenMousse) and leaves the ones that don't match out by default. Old databases gain the new columns the first time they are opened; no leaf changes.
- **Where things came from**: merged and rewritten leaves keep the ids of the leaves they came from, and every line pruning adds to the profile remembers its leaf (`~/.mousse-tree/origins.json`). Forgetting a leaf lists the profile lines that came from it, so you can remove them too (`forget --with-profile`, `also_profile`, or the admin page asks).

## Manuals (0.6)

The same procedure (how you write documents, log a workout, prepare a meeting) used to be copied into CLAUDE.md, AGENTS.md and a dozen agent rule files, and the copies drifted apart. A **manual** keeps the body once, next to the tree; everywhere else keeps a four-line pointer:

```
## Writing documents
When to use: enriching document material; revising an SOP; preparing a reference letter
Body: Memory tree › Manuals › Writing documents
Fetch the body before you start; if you can't, stop and say so.
```

- **Where they live**: Markdown storage keeps one note per manual in `manuals/` (properties `name`, `when`, `about`, `status`, `agents`, `created_at`, `updated_at`; the body is the procedure; edit them in Obsidian like any note). SQLite storage keeps them in the `manual` table of `tree.db`. Both read and write the same way. Manuals are not memories: they are never leaves, never merged by pruning, never moved into the profile, never exported.
- **Names** are everyday words ("log a client visit", not "add a row to the visits table"): they are what search matches first, then `when` (a few phrases you'd actually say), then the one-line `about`, and only lightly the body.
- **Versions**: every edit, rename, disable and removal keeps the whole previous manual under `~/.mousse-tree/versions/manuals/<name>/` (the last 20); `history` lists them and `restore` brings one back.
- **Every platform reads the same copy**: `find_manual` and `read_manual` over MCP (Claude.ai, ChatGPT, Gemini, Claude Code in the cloud), `mousse-tree manual show` for your own agents, and OpenMousse's built-in engine searches them before each turn. The rule is the same everywhere: fetch the body before you act; if you can't, stop and say so; never improvise from the one-line description.
- **Who changes them**: you, directly, from the command line or in Obsidian. Platforms over MCP can only read them. OpenMousse's agents only propose a change (`manual_manage` makes an approval card; manuals are "how things are done" in its memory gate), and the old version is kept when you approve it. Changes are listed in [CHANGELOG.md](CHANGELOG.md).
- **Reads are counted** (which platform read which manual on which day), so weekly pruning can list the manuals nobody used this month. A manual that never gets used usually has a name nobody would say: fix the description before deleting anything.

```bash
mousse-tree manual add "Writing documents" --when "revise my SOP; prepare a reference letter" --about "Draft and revise documents in my voice" --file writing.md
mousse-tree manual edit "Writing documents" --when "revise my SOP; polish my CV" [--rename …] [--disable | --enable]
mousse-tree manual list | show "Writing documents" | find "help me revise my SOP" | rm "Writing documents"
mousse-tree manual pointers [names…]        # the pointers to paste into CLAUDE.md / AGENTS.md
mousse-tree manual history "Writing documents" | restore "Writing documents" v3
```

## Admin page

Open the admin link printed by `mousse-tree urls` (`http://127.0.0.1:8787/ui#key=…`): browse memories by source, edit, forget, confirm pending entries, edit the profile. The page's API only answers requests that carry the admin key (`ui_token` in `config.json`); the browser remembers it after the first visit. The page itself follows your browser's language. `require_confirm: true` in `config.json` puts platform writes into "pending" until confirmed.

## Command line

```bash
mousse-tree recall --q breakfast
mousse-tree recent --days 7
mousse-tree add --source myclaw --kind decision --text "…"    # your own agents can write too (--branch NAME to pick a branch)
mousse-tree profile                                           # the profile, the rules and the branches, as platforms see them
mousse-tree branches                                          # the branches
mousse-tree sprout Fitness --about "Training and recovery" --agent fitness   # grow a branch by hand
mousse-tree move <id> People                                  # move a leaf to another branch
mousse-tree forget <id> [--with-profile]                      # forget; --with-profile also removes profile lines that came from it
mousse-tree prune-context | prune-plan --file plan.json | prune-apply   # weekly pruning by hand
mousse-tree stats
mousse-tree check      # Markdown storage: notes with format problems
mousse-tree rebuild    # Markdown storage: rebuild the index from the notes
```

## Design

- One memory = one sentence + kind + tags + source + observed_at + status + branch. A change `supersedes` the old entry instead of appending a contradiction; near-duplicates are caught before they are written.
- Forgetting clears the text and keeps the skeleton, so it stays auditable.
- Storage is a SQLite file or a folder of Markdown notes (see Storage); the tools, the export and the admin page are the same either way.
- The profile (USER.md) is read-only into the tree; edit it in the admin page or the file. The one exception is weekly pruning, which appends a line to a section after you approve the plan.
- The export `TREE.md` serves agents that do not speak MCP (such as OpenClaw's memory_search).
- Not there yet: semantic recall (embeddings), multi-user, a pruning screen in the admin page (OpenMousse shows the plan as an inbox card). PRs welcome.

## License

[AGPL-3.0](LICENSE), like the rest of OpenMousse; for a commercial license open a [GitHub issue](https://github.com/openmousse/openmousse/issues). Versions published before 28 September 2026 were MIT and remain available under it.
