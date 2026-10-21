# Changelog

Versions of `openmousse-tree` (the `mousse-tree` command and its MCP server). Older databases and note folders are upgraded in place the first time a newer version opens them; no memory is lost.

## 0.7.0 (2026-10-21)

- **Secrets**: `remember` (the MCP tool and `mousse-tree add`) replaces recognized keys and tokens with a placeholder before the leaf is stored. So do the weekly pruning's merges and rewrites (including the quote, tags and cues they carry over from older leaves), edits from the admin page, and sentences pruning moves into the profile. The leaf records how many places were redacted (`redacted` on the row; Markdown notes say `隐去过 N 处` or `redacted N`) and does not keep the original value.
- **Export**: `TREE.md` and the profile bundle handed to platforms are redacted as they go out. Leaves already in the tree stay as they are; an old leaf is cleaned only in that export.
- Shapes: vendor prefixes (Notion `ntn_` / `secret_`, Perplexity `pplx-`, OpenAI `sk-` / `sk-proj-`, Anthropic `sk-ant-`, GitHub `ghp_` / `github_pat_` / `gho_`, xAI `xai-`, Google `AIza`, Slack `xox[abpr]-`, AWS `AKIA`, Stripe `sk_live_` / `rk_live_`), JWTs, PEM private-key blocks, `token=` / `key=` query values, high-entropy strings of 32 or more characters (not paths, slugs or identifiers whose pieces read as words), and a UUID or a 32/40/64-digit hex id (commit hashes, page ids) only when a word like key, token, secret, password or bearer stands beside it.

## 0.6.0 (2026-10-19)

- **Manuals**: a written procedure (how you write documents, log a workout, prepare a meeting) kept once next to the tree, with a name in everyday words, a few "when to use it" phrases, a one-line description and the body. Markdown storage keeps one note per manual in `manuals/` (edit them in Obsidian like any note); SQLite storage keeps them in the `manual` table of `tree.db`. Manuals are not memories: never leaves, never merged by pruning, never moved into the profile, never exported.
- **Versions**: every edit, rename, disable, removal and restore keeps the whole previous manual under `~/.mousse-tree/versions/manuals/<name>/` (the last 20).
- **Command line**: `mousse-tree manual add / edit / list / show / find / rm / pointers / history / restore`. `pointers` prints the four-line pointer to paste into CLAUDE.md, AGENTS.md or a platform's custom instructions.
- **MCP**: `find_manual` and `read_manual`, both read-only. Their descriptions tell every platform to fetch the body before acting, to stop and say so when it can't be had, and never to improvise from a one-line description.
- **Reads are counted** per platform and day, so OpenMousse's weekly pruning can list the manuals nobody used this month.
- `openmousse_tree.match`: the scorer behind `manual find` (names and when-to-use phrases weigh more than descriptions, the body only a little). OpenMousse's built-in engine uses the same one to search manuals and skills for each message.

## 0.5.0 (2026-10-19)

- The default `rules.md` gains "who may write what" (the memory gate: experience is recorded automatically, leaves grow directly, the profile needs your OK, how things are done is proposed first, other people's words are held) and "who can read what". A `rules.md` you already have is left as it is.
- Trust tiers when reading: profile > leaves with your own words (or written by you) > leaves a platform inferred. `recall` and `recent` show each leaf's tier.

## 0.4.0

- Leaves can carry where they were said (`quote`, `platform`, `thread`, `message`) and "useful when" cues; weekly pruning checks merges and rewrites against what was actually said.

## 0.3.0

- Branches, `rules.md` and weekly pruning (`prune-context`, `prune-plan`, `prune-apply`).
