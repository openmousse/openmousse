# Errand agent + Doorman egress (preview)

The errand agent runs errands outside for you: research, price checks, reading pages, filling in forms, sending mail, booking. One thing sets it apart from the other agents: **it runs in OpenClaw's Docker sandbox and its only way online is Doorman**. Reading is free; anything that submits, sends, uses your credentials or carries your private details is held by Doorman until you tap "Let it through" in the app's inbox.

> Doorman was called Sentinel until 2026-10. Internal names keep the old word so installed servers keep working: the `openmousse-sentinel` service, `<data_dir>/sentinel/`, the `sentinel` token, the `sentinel.*` config keys and the `"sentinel"` field in replies.

> Preview: nothing changes until you run `errand.py setup`. Payments aren't open; sending mail and taking over the browser to log in aren't built yet.

## Install

Needs OpenClaw (Docker sandbox), Docker, sudo (for the host firewall step) and a venv with mitmproxy.

```bash
python3 -m venv ~/.openmousse/sentinel-venv && ~/.openmousse/sentinel-venv/bin/pip install mitmproxy
cd ~/.openmousse/repo/server
python3 errand.py setup            # the first run stops at "sandbox images missing": run the next line
bash ../sandbox/errand/build.sh <data_dir>/sentinel      # builds four images (the browser one is 1.7 GB, a few minutes)
python3 errand.py setup            # again, all ✓
python3 errand.py status
```

Setup (every step can be rerun): Doorman's own CA (`<data_dir>/sentinel/`) → proxy config and the `sentinel` token (it may only call `/api/egress/*`) → Docker network `mousse-errand` (`br-mousse-err`, 172.30.99.0/24, **no NAT**) → host firewall `openmousse-errand-net.service` (root: nothing on that bridge is forwarded anywhere; towards the host only 172.30.99.1:3128 is open) → proxy service `openmousse-sentinel` (user) → a global `group:ui` tool deny is replaced by its members except browser (otherwise no agent can get the browser back) → `agents.entries.errand` in `openclaw.json`, its workspace, and "Errand" in the app.

## How it's locked down

| Layer | What it does |
|---|---|
| Sandbox | `mode all`, `workspaceAccess none` (sees no workspace; can't even edit its own AGENTS.md), read-only root, `capDrop ALL`, 1 GB, DNS pointed at 127.0.0.1 (no outside names resolve) |
| Tools | minimal profile + `exec` / `process` / file tools / sandboxed browser. Host-side tools (web_fetch, web_search, messaging, memory search, sub-sessions) are denied: they don't run in the sandbox and would bypass Doorman. No skills |
| Network | no NAT; `DOCKER-USER` drops everything leaving the bridge; only Doorman's port is open on the host. Direct IPs, UDP, DNS, and the host's SSH / Gateway / OpenMousse are all unreachable |
| Certificates | the shell image trusts only Doorman's CA; the browser image wraps Chromium: `--proxy-server` fixed (loopback included), only Doorman's CA key accepted, background services off |
| Doorman | below |

## What Doorman decides

The proxy (`server/egress_proxy.py`, mounted straight into mitmproxy by `sentinel_run.py`) blocks on its own: private / loopback / cloud metadata / CGNAT / this host's own addresses (resolved at connect time and pinned, so DNS rebinding can't swap them), ports other than 80 / 443, WebSockets and anything that isn't HTTP, a Host header that disagrees with the real destination, and placeholders headed anywhere but the hosts they're bound to. Everything else asks the server (`server/egress.py`):

- tracking, analytics, browser background traffic → 204, no card
- writes to payment sites → refused
- reads (GET etc.) → pass; a URL or host carrying your private details (address, email, phone, family names, body numbers) → held; a long encoded blob → a model looks first
- writes (POST etc.) → held when they carry credentials, are a page form submission or carry private details; pass when listed in `sentinel.write_hosts`; otherwise a model sorts them (search, paging, autocomplete → pass; submit, send, log in, book, buy, unsure → held)
- model: OpenClaw's llm-task (no tools, a fresh session each time, never sees the errand's context); errors hold; at most 60 an hour
- a hold is an inbox card (kind `egress`, rings, unreadable by agents): Let it through = that exact request may go for 30 minutes; No / Revise = the errand gets a 403 with your words. At most 3 cards wait at once

If the proxy's own code breaks, the server is unreachable, or a hook throws, the request is blocked (fail closed).

## Credentials without handing them over

The errand never sees your passwords. Add a placeholder on the server, bound to exact hosts:

```bash
python3 errand.py secret set BOOKING_PASSWORD --host www.example.com   # value read from stdin, not echoed
python3 errand.py secret list
python3 errand.py secret rm BOOKING_PASSWORD
```

Short-lived tokens (Gmail sending, say) use OAuth: `errand.py oauth start GMAIL_SEND --host gmail.googleapis.com --client-file <desktop OAuth client JSON> --login-hint <address>` prints the consent address; after you allow, the browser stops on a `127.0.0.1` page that won't load: give its full address to `errand.py oauth finish '<address>'`. The proxy then refreshes the access token on its own. Send requests are decoded, so the card shows recipients, subject and body, and every email waits for you.

The errand writes `MOUSSE_SECRET_BOOKING_PASSWORD` into its request; Doorman swaps in the real value only for `www.example.com` (anywhere else it's blocked) and swaps it back if the site echoes it. Writes carrying credentials always wait for your OK. The "Credentials you can use" section of the errand's AGENTS.md lists names and hosts automatically.

## Check, watch, roll back

- `python3 errand.py status`: ✓ / ✗ for every piece
- API: `GET /api/egress/health` (up or not, today's pass / hold / block counts), `GET /api/egress/log` (recent requests, kept 14 days)
- cut the errand off: `systemctl --user stop openmousse-sentinel` (the sandbox then reaches nothing)
- remove it: `python3 errand.py remove` (errand leaves OpenClaw, workspace goes to archive/) → `systemctl --user disable --now openmousse-sentinel` → `sudo systemctl disable --now openmousse-errand-net` → `docker network rm mousse-errand`

## Known limits

- When a browser submit is held, OpenClaw's browser tool can't wait 10 minutes and times out first; the request stays open and completes once let through. The errand's rules say: don't submit twice, read the page again later.
- Reads pass by default: things in a URL that are neither your private details nor look encoded can leave. Writes and private details are the checkpoints.
- Payments, sending mail (needs a mailbox's send permission) and taking over the browser to log in (noVNC) aren't built yet.
