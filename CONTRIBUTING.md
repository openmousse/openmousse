# Contributing

[中文](CONTRIBUTING.zh-CN.md) · **English**

Thanks for looking. OpenMousse is small and early; the most useful contributions right now are, in order:

1. **Install reports.** Run `install.sh` on your machine and open an issue with what broke or what was unclear. Every rough edge you hit becomes a line in the docs.
2. **Data-source adapters and packs.** If you track workouts, meals, sleep or anything else in some app, an adapter that exposes it to the boards is exactly what `packs/` is for. Open an issue first describing the app and its API so we agree on the shape.
3. **Bug fixes** anywhere.
4. Features: open an issue before writing code so it fits the design (blank slate, no locked-in data sources, memory first — see the README).

## Setting up

You need an OpenClaw instance to try the whole thing, but each part can be worked on alone.

```bash
git clone https://github.com/openmousse/openmousse && cd openmousse

# app (Expo): typecheck + lint are the gate; `npm run web` runs it in a browser against any server
cd app && npm install && npm run typecheck && npm run lint

# server (FastAPI): point it at a throwaway config and it starts on a blank instance
cd ../server && pip install -r requirements.txt
MOUSSE_SERVER_CONFIG=/tmp/server.json python3 run.py        # see server.example.json and config.py

# tree (memory over MCP)
cd ../tree && pip install -e . && MOUSSE_TREE_HOME=/tmp/tree mousse-tree init --name Dev
```

The CI workflow (`.github/workflows/ci.yml`) shows exactly what is checked: app typecheck + lint, Python pyflakes, `install.sh` syntax, a smoke test that boots the server on an empty instance, and the tree CLI.

## Pull requests

- Fork, branch, PR against `main`. Keep one change per PR.
- Say how you tested it. For the app that means the commands above plus what you clicked; for the server, which endpoints.
- Bilingual docs: if you change behaviour described in a README, update both `README.md` and `README.zh-CN.md` (a rough translation is fine, we will polish it).
- No personal data, tokens or addresses in code, docs, screenshots or logs. Identity files (`app.local*.json`, `~/.openmousse/*`) never enter git.
- Comments in the code are mostly Chinese today; write new ones in whichever language you are comfortable in. Commit messages in English.

## Design rules that PRs are checked against

- **Blank slate**: nothing about a specific person's life ships in the code. Domain features live in `packs/`.
- **No locked-in data sources**: a pack declares what data it needs; where it comes from is the user's choice (an MCP-capable app, an adapter script, or chat).
- **Memory first**: facts about the user go to the world tree, not into an Agent's private notes.
- **The app is a shell**: no sample data, no fake replies; when the server is unreachable it says so.

## Labels

`good first issue` — small, self-contained, described well enough to start without asking · `pack` — a new feature pack · `data-source` — an adapter for some app or API · `app` / `server` / `tree` — which part.
