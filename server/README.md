# OpenMousse server

[中文](README.zh-CN.md) · **English**

Thin API layer (FastAPI): auth, chat relayed to your OpenClaw Gateway, Agent creation and deletion, board data, attachments and voice, push notifications, and hosting of the web build.

## Install

The one-line installer at the repository root (`install.sh`) does all of the below, including the systemd service. By hand:

```bash
cd ~/openmousse/server
pip install -r requirements.txt
mkdir -p ~/.openmousse && cp server.example.json ~/.openmousse/server.json   # edit paths and timezone
python3 tokens.py add phone      # generate an access token for the app's connection page
python3 run.py                   # or install as a systemd service, see openmousse-server.service.example
```

Every field of `server.json` is documented at the top of [`config.py`](config.py). Changing tokens or adding Agents needs no restart; changing the bind address does.

## Auth

`/api/*` requires `Authorization: Bearer <token>` (`X-API-Key` also works; `?token=` only for `GET /api/files/…`, which image views can't send headers to). No credentials → 401. Two token-free doors are off by default: `auth.tailscale_nodes` (a whitelist of Tailscale device names; needs tailscale on this machine) and `auth.trust_loopback` (never enable it when a reverse proxy runs on the same host). The web build's static files are public.

## Letting the phone connect

- **Tailscale** (least effort): bind the service to the Tailscale address, install Tailscale on the phone, enter `http://100.x.x.x:8080` in the app.
- **Public HTTPS**: `tailscale serve` / `tailscale funnel`, or Caddy / nginx reverse-proxying to 127.0.0.1:8080; enter `https://your.domain` in the app.

## Agents

"New Agent" in the app = a separate agent in your OpenClaw: its own workspace (AGENTS.md / IDENTITY.md / MEMORY.md), skill allowlist, routing. See [`agents.py`](agents.py). From the command line:

```bash
python3 agent_ctl.py list
python3 agent_ctl.py create --name Sleep --purpose "Interpret last night's sleep every morning." --icon moon
python3 agent_ctl.py delete g-xxxxxxxx      # workspace archived to ~/.openclaw/archive/, never deleted
```

With `packs/core/skills/agent-builder` installed on the main agent (the installer does this) you can create Agents from chat.

## Study desk

`/study` is a wide-screen page for a computer: courses and modules on the left, study notes / slides (PDF) / flashcards / quiz in the middle, and a chat on the right that answers from this session's materials. Configure it with `study` in `server.json` (see [`study.py`](study.py)):

- `materials`: one folder per course, one subfolder per module (week / session), files inside. A module-by-module download of a learning platform (e.g. Canvas) has exactly this shape.
- `pages`: study notes, one folder per course, Markdown with YAML front matter (`session`, `title`, `sources` = material paths relative to the course folder). Notes attach to the module of their first source; videos go in `<course>/media/` named `S03 ….mp4`.
- `courses` (optional): which course folders to show, in order. `deadlines_cmd` (optional): a command that prints a JSON array of `{due, course, title, url}` shown along the top.
- `readings` (optional): a folder with one `<course>.json` reading list per course: `{"items": [{title, kind, required, instructions, sessions, file, status, url}]}`, `file` relative to the course's materials folder. Each session gets a "Readings" tab; if a required reading is missing, generating a study path / flashcards / quiz asks you to supply it first.
- `recordings` (optional): lecture captions, `<course>/index.json` listing recordings (`id, name, start, duration, sessions, file`), each `file` holding `{viewer_url, segments: [{t, text}]}`. Each session gets a "Lectures" tab (captions with timestamps that open the recording at that moment), and the captions go into the chat context. Keep captions private if your institution's recording policy says so.
- `video_cmd` (optional): a render command (argv list; `{script}` = the Manim script the assistant wrote, `{media_dir}` = a work folder). When set, each session gets "make a video": the assistant writes the script, the server renders it and hands any error back once for a fix. This runs code the assistant wrote on your machine, so only turn it on where you already trust the assistant to run code.

Every study page opens on its **study path**: 5–8 steps (what to do, which slide pages / note section / reading / recording time, roughly how long), generated from all of the session's materials, with a tick box per step; progress is saved on the server and shown in the course tree.

Questions go through the same chat channel as the app, one thread per study page. The first question of each day carries the notes, the full text of the materials, the lecture captions and the readings (as much as fits; the rest by file path), plus the study-path step you're on; flashcards, quizzes and study paths are generated the same way and saved next to the notes.

## Data sources are optional

The boards need workouts / meals / body / calendar / derived health metrics, each provided by one script in the `scripts` directory named in `server.json` (default `<workspace>/scripts`): `xunji.py` (workouts / meals / body), `calendar_ics.py` (calendar), `apple_health.py` (recovery score, energy balance, fitness trend). A script that is present gets loaded; a missing one means "not connected": `/api/health` reports `sources` so the app knows, the affected endpoints answer `ok=false` + `missing_source`, boards show an empty state, everything else works. See [`sources.py`](sources.py).

Today these three scripts still have the shape of the author's own sources (the Xunji training app, an ICS calendar, Apple Health). They are being split into optional packs (`packs/`): each pack declares the data types it needs and you map the sources.
