# OpenMousse relay

English · [中文](README.zh-CN.md)

Friends can reach an OpenMousse server that has no public entry point (no Tailscale Funnel, a Funnel that didn't come up, a computer at home).

- The server connects out to the relay (`server/relay.py`, default `https://relay.openmousse.ai`), proves its identity with its social Ed25519 key and keeps a WebSocket open. The relay gives it the address `https://relay.openmousse.ai/u/<kid>`; `/f/…` requests that friends' servers send there come down that connection.
- Between current servers, messages, the invite handshake and A2A travel in **end-to-end envelopes** (`POST /f/sealed`, X25519 + HKDF-SHA256 + ChaCha20-Poly1305): the relay sees the recipient, sizes and times, not the content. Public cards stay visible as usual.
- The relay **stores nothing**: an offline server gets a 503 and the sender retries on its own (for up to 3 days).
- Only GET / POST / HEAD under `/f/`; requests and responses up to 300 KB each; 240 requests a minute per address; only JSON-like responses pass, never HTML, and the relay draws invite landing pages itself.
- One Durable Object (`Mailbox`) per kid, keeping only the newest connection; the kid must be the thumbprint (RFC 7638) of the key that connected, so nobody can take someone else's address.

The full protocol: [`docs/social-protocol.md`](../docs/social-protocol.md) 6.1.

## On the server

On by default, nothing to set up. The relay address goes on the card only after a successful connection; with a usable `share.public_url` (Funnel / your own domain) friends connect directly and the card carries the relay address as a fallback.

```json
"relay": false                                   // turn it off
"relay": {"url": "https://relay.example.com"}    // use a relay you deployed
```

The "Sharing and friends" section of `bash check.sh` shows whether it's connected.

## Run it locally

```bash
cd relay
npm install
npx wrangler dev --port 8790 --ip 127.0.0.1
```

Give two test servers `"relay": {"url": "http://127.0.0.1:8790"}` and `"social": {"allow_http": true}` in server.json, and remove `share.public_url` from one of them: that one relies on the relay alone.

## Deploy (Cloudflare Workers)

1. `npm install`, then `npx wrangler login` (or set `CLOUDFLARE_API_TOKEN`: Workers Scripts edit + Workers Routes and DNS edit on the domain).
2. Point `routes` in `wrangler.toml` at your domain (the official one is `relay.openmousse.ai`; Cloudflare creates the DNS record and certificate).
3. `npx wrangler deploy`. Afterward `curl https://<your domain>/health` should return `{"ok":true,"service":"openmousse-relay","v":1}`.

Durable Objects use the SQLite backend (`new_sqlite_classes`), which runs on the Workers free plan; move to a paid plan as usage grows.
