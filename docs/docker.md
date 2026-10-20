# OpenMousse in Docker

[中文](docker.zh-CN.md) · **English**

One container runs the OpenMousse server with its built-in engine (no OpenClaw needed) and the memory tree. Everything that changes lives in one volume, `/data`. The files are [`docker/Dockerfile`](../docker/Dockerfile), [`docker/entrypoint.py`](../docker/entrypoint.py) and [`docker/compose.yaml`](../docker/compose.yaml).

## Start it

From the repository root:

```bash
MOUSSE_NAME=Pebble MOUSSE_TZ=Europe/Lisbon MOUSSE_LANG=en docker compose -f docker/compose.yaml up -d --build
docker compose -f docker/compose.yaml logs openmousse
```

The first start (the volume has no `server.json` yet) writes `/data/server.json`. The claw is `builtin`, the server listens on `0.0.0.0:8080` inside the container, and the tokens are random and stay in the volume. The name, language and timezone come from `MOUSSE_NAME`, `MOUSSE_LANG` and `MOUSSE_TZ`; without them it's Mousse, `LANG`'s language, and `TZ` or UTC. It also creates the memory tree and prints a pairing link (see below). Later starts leave `server.json` as it is, whatever the variables say. Change settings in the app, or edit the file in the volume.

| In `/data` | What |
| --- | --- |
| `server.json` | the configuration (`MOUSSE_SERVER_CONFIG`) |
| `secrets.env` | the keys you save in the app (0600) |
| `data/` | the database, uploads, backups (`data_dir`) |
| `workspace/` | the main chat's workspace (`USER.md`, `MEMORY.md`, `memory/`, `skills/`); new Agents get `workspace-<id>/` next to it |
| `tree/` | the memory tree (`MOUSSE_TREE_HOME`) |
| `home/` | `HOME`, so anything written to the home folder survives too |

The container runs as user `mousse` (uid 1000), not root. With a bind mount instead of the named volume, the folder must belong to uid 1000 (`sudo chown -R 1000:1000 ./data`). `HEALTHCHECK` calls `/api/health` with the local token from `server.json`.

On the first visit the app walks you through choosing a model, web search and memory (the keys go to `/data/secrets.env`).

## Connect your phone

The compose file publishes the port on `127.0.0.1` only, so nothing else on the network can reach it. Phones and other computers come in through Tailscale on the host:

```bash
tailscale serve --bg 8080          # https://<machine>.<tailnet>.ts.net, only on your tailnet
```

Put that address in `MOUSSE_PUBLIC_URL` before the first start and the log has a pairing link and QR code (30 minutes, one use). Later, or if you didn't set it, make a new code:

```bash
docker compose -f docker/compose.yaml exec openmousse python3 tokens.py pair --server https://<machine>.<tailnet>.ts.net
```

The phone needs to be on the same tailnet. Pairing goes through `/api/pair`, which isn't exposed publicly.

## The web version

The image has no Node.js and doesn't build the web app. To serve it at `http://127.0.0.1:8080`, build it before the image (`cd app && npm ci && npm run web:build`): `.dockerignore` lets `app/dist` into the image and keeps the rest of `app/` out. Without it, `/` only shows a note, and the phone app works as usual.

## What the container doesn't have

- **Docker sandbox.** There's no Docker inside the container (no docker-in-docker), so `engine.sandbox` stays off. Agents get no sandbox tools and there's no errand Agent. Host commands (`engine.host_exec`), if you turn them on, run inside the container, not on the host.
- **Claude Code / Codex sign-ins.** Neither CLI is in the image, so `claude-code/…` models aren't available: use API keys. If you need them, build your own image on top that installs the CLI, and mount its login yourself. A ChatGPT plan connected from the app keeps its tokens under `/data/data/chatgpt/`.
- **Tailscale.** Use the host's. `auth.tailscale_nodes` (signing in without a token by device name) doesn't work inside the container.

## Updating

Rebuild or pull a new image (`git pull && docker compose -f docker/compose.yaml up -d --build`). `mousse update` refuses to run in the container (`MOUSSE_INSTALL=docker`) and tells you to pull a new image. The volume stays as it is.

## Backups

Everything is in the volume. Stop the container, then archive the volume:

```bash
docker compose -f docker/compose.yaml stop
docker run --rm -v openmousse_openmousse-data:/data:ro -v "$PWD":/backup busybox tar czf /backup/openmousse-data.tgz -C / data
```

## Moving over from OpenClaw or Hermes

Mount the old home into the container (the commented lines in `compose.yaml`) and run [`mousse migrate`](../server/README.md#moving-over-from-openclaw-or-hermes-built-in-engine):

```bash
docker compose -f docker/compose.yaml exec openmousse mousse migrate openclaw --openclaw-home /migrate/openclaw          # report only
docker compose -f docker/compose.yaml exec openmousse mousse migrate openclaw --openclaw-home /migrate/openclaw --apply
```

A read-only mount is enough: the migration never writes to the source. Workspaces aren't copied, though. Agents keep using theirs where they are (the `agent_workspaces` paths point into `/migrate/…`), so mount it read-write if those Agents should keep their memory up to date. The main chat keeps `/data/workspace`, and the report says how to point it elsewhere.

## Behind a TLS-inspecting proxy

Hand pip the proxy's CA as a build secret. It isn't stored in any layer:

```bash
docker build --secret id=ca_bundle,src=/path/to/ca.pem -f docker/Dockerfile -t openmousse .
```
