# OpenMousse social protocol v1

English · [中文](social-protocol.zh-CN.md)

> First version, 2026-09-28. The social section has three layers: ① sharing (`/s/`, live), ② friends (this document: identity, invite codes, friends, signatures, friend chat, the card agent answering follow-up questions on shares), ③ agent to agent (A2A, see [a2a.md](a2a.md)). Layer ③ uses the identity and signatures defined here instead of its own.

## 0. In one paragraph

Everyone runs their own server, and servers talk to each other directly over HTTPS. **Identity is an Ed25519 key**: the address can change, the key doesn't. You become friends by redeeming a one-time invite code the other person gave you; after that every request between the two servers is signed, and the receiver knows who is talking by key, not by IP or domain. Only `/f` (and layer ①'s `/s`) is exposed to the internet; nothing that comes in reaches `/api` or the main agent. Everything a friend sends is data, never instructions.

## 1. Who owns what

| | What | Code |
|---|---|---|
| ① Sharing | `/s/<token>` link pages, clean image cards | `server/share.py`, `server/public.py` |
| ② Friends | identity, invite codes, the friends table, request signatures, friend chat, sending shares to friends, follow-up questions on shares (answered by the card agent), tier settings | `server/social.py`, `server/public.py`, app |
| ③ Agent to agent | the A2A card and endpoint, the card agent's rules (tier → what it may say, incoming text is data, anything needing the owner becomes an inbox card, everything said out is logged, daily caps) | `server/a2a.py`, `server/cardagent.py` |

The card agent calls a model only through `cardagent.py`, **never through the claw's chat API** (that is a full agent with tools and the owner's authority; one sentence from outside could steer it). Layer ② answers follow-up questions by calling `cardagent.answer()` and never calls a model itself.

## 2. Identity

- **Key**: one Ed25519 key per server in `<data_dir>/social/identity.json` (0600, `{"v": 1, "seed": "<32-byte seed, base64url>", "created_at": "…"}`), generated on first use and backed up with the data dir. Separate from the OpenClaw device identity (`gateway-device.json`).
- **Public key**: JWK `{"kty": "OKP", "crv": "Ed25519", "x": "<32-byte public key, base64url>"}`.
- **kid**: the JWK thumbprint (RFC 7638) = `base64url(sha256('{"crv":"Ed25519","kty":"OKP","x":"<x>"}'))`, 43 characters. The friends table, request signatures, card signatures and the A2A card all name the key by it.
- **Fingerprint for people**: the first 10 characters of base32 (upper case, no padding) of SHA-256 over the 32 public key bytes, in two groups: `K7Q2M 9XJ4P`. Two people can compare it on a call.
- **Losing the key = becoming someone else**: friends have to add you again. v1 has no key rotation.

### 2.1 Social card `GET /f/card`

```json
{
  "openmousse": "1",
  "kid": "<kid>",
  "key": {"kty": "OKP", "crv": "Ed25519", "x": "<x>"},
  "url": "https://alex.example.ts.net",
  "name": "Alex",
  "caps": ["chat", "ask"],
  "updated_at": "2026-09-28T17:00:00+01:00",
  "signatures": [{"protected": "<b64url header>", "signature": "<b64url signature>"}]
}
```

- `url`: the public origin, scheme + host (+ port) with no path, i.e. server.json's `share.public_url`. Social endpoints live at `<url>/f/…`; a reverse proxy must not rewrite the `/f` prefix (the path is signed).
- `name`: server.json's `user_name`. You can't add friends until you have one (the app asks first).
- `caps`: `chat` friend chat, `ask` shares can take follow-up questions (the card agent answers). Layer ③ adds `a2a` together with a field `"a2a": "<url>/f/a2a/agent-card.json"`.
- Only strings, arrays and objects go into the card: no numbers (see canonicalization below).
- This is the social card; **the A2A card is a separate document** (`/f/a2a/agent-card.json`, layer ③) signed with the same key, whose identity extension carries `kid`, `x` and `"card": "<url>/f/card"`. They are separate because the two layers may ship at different times and an A2A card without an A2A endpoint is meaningless, and because other vendors' SDKs verify A2A cards after a round trip through protobuf (dropping unknown fields and empty values): that trap stays on the card meant for them, not on the identity path friends rely on.

### 2.2 Card signature (the same scheme as A2A §8.4)

- `signatures` is an array of `{"protected", "signature"}` (`header` is optional; we don't use it).
- `protected` = base64url of the JSON header `{"alg": "EdDSA", "kid": "<kid>", "typ": "JOSE"}`. `alg` is RFC 8037's `EdDSA`, not RFC 9864's `Ed25519` (the official A2A SDK verifies with PyJWT, which only knows `EdDSA`); verifiers accept both.
- What gets signed: the card without `signatures`, canonicalized with JCS (RFC 8785). Signing input = `protected + "." + base64url(canonical card)` (a detached payload; the card itself is not inside the JWS).
- JCS: keys sorted, no whitespace, UTF-8, minimal JSON string escaping. With no numbers in the card this equals Python's `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()`.
- Verifying a card: `kid` == thumbprint(`key`), the header's `kid` == the card's `kid`, and the signature verifies with `key`. **Trust comes from the public key in the invite code** (section 3), not from the domain.

`/f/jwks.json` also publishes `{"keys": [{"kty", "crv", "x", "kid", "use": "sig", "alg": "EdDSA"}]}` for others who look keys up by `jku`.

## 3. Invite codes

**Shape**: a URL

```
https://alex.example.ts.net/f/i/<token>/<x>
```

- Token: 16 random bytes, base64url (22 characters), one use, expires after 7 days by default (30 at most). The server stores only `sha256(token)`.
- `x`: the inviter's public key (43 characters). Whoever redeems the code checks the inviter's card and reply against it instead of trusting the domain alone.
- Scanning the QR code with a phone camera opens the landing page; copying and pasting goes into the app's "Add a friend". The app accepts any text containing `/f/i/<token>/<x>`.

**Landing page** `GET /f/i/<token>/<x>`: one plain HTML page (noindex, no-referrer, no scripts, like share pages): "Alex invited you to be friends on OpenMousse", how (open the app → Chat → Friends → Add a friend → paste this link), and "Open in the app" (`openmousse://friends/add?code=<the whole URL>`). **A GET never uses up the token** (chat apps fetch links for previews). An invalid token only says "This invite has been used or has expired".

**Creating one** (the inviter, in the app): who it's for (a note only you see), the tier they land in (Friend by default), how many days → QR code + link. Unused invites can be withdrawn. At most 20 open invites at a time.

**Redeeming**:

1. The invitee pastes or scans it in her app → her server splits out origin, token and `x`, fetches `GET <origin>/f/card` and checks `key.x == x` and the signature → the app shows "Add Alex as a friend? Fingerprint K7Q2M 9XJ4P" and she picks a tier.
2. Her server sends a signed request (section 5) `POST <origin>/f/hello`:

   ```json
   {"v": 1, "token": "<token>", "card": {<her signed card>}}
   ```

3. The inviter's server checks: the token exists, is unused, unexpired and not withdrawn; `card` verifies; the request's `keyid` == `card.kid`; `card.url` is https (http only when a test server sets `social.allow_http`). If all hold it creates the friend (tier = the one chosen for the invite), marks the token used (`used_by` = her kid), writes an activity line and replies:

   ```json
   {"ok": true, "card": {<the inviter's signed card>}}
   ```

   Idempotent: a used token whose `used_by` is this same kid still gets ok (a retry after a dropped connection); an existing friend gets ok and their address and name are refreshed; someone removed earlier becomes a friend again.
4. Her server checks the returned card: it verifies, `kid` == thumbprint(`x`), and `url` is the origin from the code → creates the friend (tier = the one she picked). Both chats get a grey line "You're now friends".

Errors are deliberately vague: `404 {"error": "invite_invalid"}` (never says used, expired or withdrawn), `400 {"error": "bad_card"}`, `401 {"error": "bad_signature"}`.

**Both sides need a public origin** (`share.public_url`, reachable from outside): with only one, messages flow one way, so the app asks the side without one to set it up first (the installer's question).

## 4. Tables (grava.db, created by `social.py`)

```sql
friends          (id TEXT PRIMARY KEY,            -- fr-<8 hex>, local
                  kid TEXT NOT NULL UNIQUE,         -- their key
                  pub TEXT NOT NULL,                -- their public key x
                  url TEXT NOT NULL,                -- their origin; endpoints at <url>/f/…
                  name TEXT NOT NULL,               -- the name on their card
                  alias TEXT,                       -- my name for them (shown first)
                  tier TEXT NOT NULL DEFAULT 'friend',   -- close | friend | mate
                  status TEXT NOT NULL DEFAULT 'active', -- active | removed (I removed them) | gone (they removed me) | blocked
                  caps TEXT NOT NULL DEFAULT '[]',  -- their card's caps
                  card TEXT,                        -- their latest verified card (JSON)
                  a2a TEXT,                         -- their A2A card URL, if any
                  note TEXT,                        -- how we met (the invite's note)
                  via TEXT,                         -- invite:<id> (I invited them) | code (I redeemed theirs)
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, seen_at TEXT)
friend_invites   (id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, note TEXT, tier TEXT NOT NULL,
                  created_at TEXT NOT NULL, expires_at TEXT NOT NULL, used_at TEXT, used_by TEXT, revoked_at TEXT)
friend_messages  (id INTEGER PRIMARY KEY AUTOINCREMENT, friend TEXT NOT NULL, mid TEXT NOT NULL,
                  dir TEXT NOT NULL,                -- in | out
                  kind TEXT NOT NULL,               -- text | share | ask | answer | system
                  by TEXT NOT NULL DEFAULT 'person',-- person | agent (the card agent answered)
                  text TEXT NOT NULL DEFAULT '', data TEXT,  -- data: share snapshot, used, defer, log_id… (JSON)
                  reply_to TEXT, status TEXT NOT NULL,       -- out: queued | sent | failed; in: new | read; either may be revoked
                  review TEXT,                      -- card agent answers on my side: pending | ok | edited | revoked
                  ts TEXT NOT NULL, recv_at TEXT, edited_at TEXT,
                  tries INTEGER NOT NULL DEFAULT 0, next_try TEXT, error TEXT,
                  UNIQUE (friend, dir, mid))
social_nonces    (kid TEXT NOT NULL, nonce TEXT NOT NULL, at REAL NOT NULL, PRIMARY KEY (kid, nonce))
social_settings  (key TEXT PRIMARY KEY, value TEXT NOT NULL)   -- tiers (JSON per tier), status (the owner's status text), announced (digest of the card friends last heard about)
```

## 5. Request signatures (a fixed profile of RFC 9421)

Every request between servers (`/f/hello`, `/f/msg`, layer ③'s `/f/a2a`) is signed. Always `POST` with a JSON body and no query string.

Headers:

```
Content-Type: application/json
Content-Digest: sha-256=:<base64(sha256(body))>:
Mousse-To: <recipient kid>
Signature-Input: om=("@method" "@path" "content-digest" "mousse-to");created=1790612345;nonce="<22 chars base64url>";keyid="<sender kid>";alg="ed25519";tag="openmousse/1"
Signature: om=:<base64(signature)>:
```

Signature base (RFC 9421 §2.5, lines joined by `\n`, no trailing newline):

```
"@method": POST
"@path": /f/msg
"content-digest": sha-256=:…:
"mousse-to": <recipient kid>
"@signature-params": ("@method" "@path" "content-digest" "mousse-to");created=1790612345;nonce="…";keyid="…";alg="ed25519";tag="openmousse/1"
```

- `Content-Digest` is RFC 9530; these two headers use standard base64 (with `=`) wrapped in `:…:` as the RFCs require. Everywhere else base64url is unpadded.
- `Mousse-To`: the recipient's kid, covered by the signature, so a friend can't replay a request I sent them to another friend (behind Funnel or a reverse proxy the Host header isn't reliable, so `@authority` isn't covered).
- JSON-RPC (A2A) posts every method to the same URL with the method in the body, which is why `content-digest` must be covered.
- Signed requests to an A2A endpoint also send `A2A-Extensions: https://openmousse.ai/a2a/ext/signed-requests/v1` (layer ③ declares it in the A2A card as an extension with `required: false`).

**Verifying** (`social.authenticate()`):

1. The body is capped (16 KB for `/f/hello`, 256 KB otherwise): 413 beyond that.
2. No `Signature` / `Signature-Input` → a stranger (`/f/hello` and `/f/msg` answer 401; A2A treats it as the stranger tier).
3. A signature that is present must be right in every respect, otherwise **401**, never a silent downgrade to stranger: label `om`; all four components covered; `alg` = `ed25519`; `created` within ±300 s of now; `nonce` of 16–64 characters and unseen for this kid in the last 10 minutes (kept in `social_nonces`); `Content-Digest` matches the body; `Mousse-To` == my kid; the key found by `keyid` (the friends table; for `/f/hello` the `key` of the card in the body, with `keyid == card.kid`) verifies the signature.
4. The result is `Peer(kid, friend, tier, signed, status)`; `authenticate()` only says who it is and leaves decisions to the route: `status` is the friends row's status (`None` when there is no row, else `active` / `removed` / `gone` / `blocked`, with `peer.blocked` as a shortcut). Only an `active` row gives a `friend` and its `tier`; everything else is `friend = None`, `tier = "stranger"`. A signature whose `keyid` isn't in the friends table can't be checked and counts as an unsigned stranger (`Peer()`, without the `kid`, which may be made up). `/f/msg` answers 403 `not_friends` for `removed` / `gone`, 200 and does nothing for `blocked`, 401 `unknown_sender` without a row; the A2A endpoint answers REJECTED for `blocked`.

Responses are not signed (HTTPS already says which server answered); the card in `/f/hello`'s reply carries its own signature. Clocks must be right (NTP): ±300 s.

## 6. Public paths

One more public path: Tailscale Funnel (or a reverse proxy) `/f` → `http://127.0.0.1:<share.public_port>/f`, the same small app as `/s` (`public.py`): no `/api`, no tokens, no device trust. It is open only if you want friends: the installer's public question opens it, or run the `tailscale funnel` command yourself.

| Path | Caller | Signed | What |
|---|---|---|---|
| `GET /f/card` | anyone | — | social card (2.1) |
| `GET /f/jwks.json` | anyone | — | public key (JWKS) |
| `GET /f/i/<token>/<x>` | a browser | — | invite landing page; never uses the token |
| `POST /f/hello` | the invitee's server | yes | redeem an invite (3) |
| `POST /f/msg` | a friend's server | yes | deliver one message (7) |
| `/f/a2a`, `/f/a2a/agent-card.json` | other agents | optional | layer ③ (A2A JSON-RPC and card) |

`/.well-known/agent-card.json` (so strangers and other vendors' agents can find the card agent by domain) needs another Funnel path; it stays closed for now and is asked about together with `/f`.

The main server (the private 8080) doesn't mount `/f`: friends only come in from the public side. The app uses `/api/friends…` and `/api/card` (token required).

**Limits**: per friend 30 requests a minute and 500 messages a day; `/f/hello` 30 an hour in total (behind Funnel every request comes from 127.0.0.1); `text` 4,000 characters, `ask` 1,000, a shared body 60,000. Over a limit: 429 with `Retry-After`. The card agent's own caps (count and length per friend per day) live in `cardagent.py` (layer ③).

## 7. Messages `POST /f/msg`

One message per request:

```json
{"v": 1, "id": "<32 hex>", "kind": "text", "at": "2026-09-28T19:05:12+01:00", "text": "Yes, the next episode is about exactly that", "reply_to": "<optional: another message's id>"}
```

`id` is made by the sender (uuid4 hex) and the receiver dedupes on (friend, id); `at` is when the sender wrote it (with offset).

| kind | fields | meaning |
|---|---|---|
| `text` | `text`, `reply_to?` | what a person said |
| `share` | `share: {sid, kind, title, text, quote, when, link?, can_ask}`, `text?` | a share sent to a friend: the snapshot after private bits were hidden (hidden spots are `▇▇▇`; the original never leaves the server); `link` only when anyone with the link may see it; `can_ask` = whether it takes follow-up questions (the share's switch × the friend's tier) |
| `ask` | `about` (the share message's id), `text` | a follow-up question for the other person's card agent |
| `answer` | `about` (the question's id), `text`, `used` (what it drew on, e.g. `["this episode"]`), `defer` (true = "you'd have to ask him") | the card agent's answer (`by = agent`) |
| `edit` | `target`, `text`, `by?` | replace the text of a message I sent (when Alex rewrites the card agent's answer, `by = person` and the friend sees "edited by Alex") |
| `revoke` | `target` | withdraw a message I sent (their copy is emptied and shows "Alex withdrew this") |
| `card` | `card` | my card changed (new address, new name, new caps): verified and applied when the kid matches |
| `bye` | — | I removed you: mark me `gone` and stop sending |

The receiver answers `200 {"ok": true, "id": "<id>", "dup": false}` (`dup: true` for a repeat); an `edit` / `revoke` whose `target` isn't a message from this sender still gets 200 with `"ignored": true`. Errors: `400 bad_request`, `401 bad_signature`, `403 not_friends`, `413 too_large`, `429 slow_down`.

**Delivery**: the sender writes the message into `friend_messages` first (`status = queued`) and then sends it; network errors, 5xx and 429 are retried after 30 s, 2 min, 10 min, 1 h, 6 h and then every 6 h, and after 3 days it's marked `failed` (the app shows "Not delivered · Retry"); any other 4xx fails at once. When the address or the name changes (`share.public_url`, `user_name`) the server sends every friend a `card` by itself.

**Follow-up questions on a share (answered by the card agent)**:

1. On the friend's side, "Ask" under a share sends an `ask` to Alex's server.
2. Alex's server checks: `about` is a share I sent to this friend, it hasn't been withdrawn, `can_ask` is still on, and the friend's tier still has `shares = ask`. If not, no model is called and an `answer` goes back with `defer: true` ("you'd have to ask him").
3. Otherwise `cardagent.answer(friend, question, channel="chat", material=[{"id": "share:<sid>", "kind": "share", "title", "text"}], history=<the last few turns of this chat>, ref="share:<sid>")` (layer ③) → `{text, used, defer, declined, limited, log_id}` is stored as an `answer` (`by = agent`, `review = pending`, `data.log_id`) and sent.
4. In Alex's app the answer carries a box only he sees: "Fine" (`review = ok`) / "I'll rewrite it" (sends `edit` with `by = person`; `cardagent.retract(log_id, replaced=True)`, logged as "rewrote an answer") / "Withdraw" (sends `revoke`; `cardagent.retract(log_id)`). Withdrawn and rewritten originals are no longer given to the card agent as context.
5. Until `cardagent.py` exists (layer ③ not live), questions are never answered automatically: they just show up in Alex's chat for him to answer.

**Not in v1**: group chats ("CS group · 4 people"), link-only contacts (people without OpenMousse, like Dad: layer ①'s links already cover them), image and voice attachments, read receipts, typing indicators, key rotation, friends of friends.

## 8. Tiers

The card agent answers people by tier, and Alex decides what each tier can get in "My card agent":

| Key | Values | Close `close` | Friend `friend` | Classmate `mate` | Stranger `stranger` |
|---|---|---|---|---|---|
| `calendar` | `detail` / `busy` / `none` | detail | busy | busy | none |
| `status` | `some` / `line` / `none` | some | line | none | none |
| `shares` | `ask` / `view` / `public` | ask | ask | view | public |
| `notes` (study notes) | `view` / `none` | view | view | view | none |
| `address` | `view` / `none` | view | none | none | none |

- A stranger is anyone not in the friends table: unsigned, signed by an unknown key, another vendor's agent. Strangers have no row, so nobody can be put in that tier.
- **Health and body, and the memory tree, are not keys**: there is no switch that could open them, and the card agent can't see them either.
- Status = a paragraph Alex writes on the card page (`some` gives all of it, `line` the first line); study notes = only those Alex marked shareable (none by default); address from the profile; calendar from the schedule layer (`busy` = free slots only). Fetching these is layer ③'s job; storing tiers and the settings page are layer ②'s.
- Stored in `social_settings.tiers`; the defaults above apply until changed. Friends never learn their tier.

## 9. What `social.py` gives layer ③

```python
identity() -> {"kid", "x", "jwk"}                    # created on first use
fingerprint(x) -> "K7Q2M 9XJ4P"
jcs(obj) -> bytes                                     # canonical form of string-only JSON such as cards
sign_jws(obj) -> {"protected", "signature"}           # signs the JCS of obj without signatures, detached
verify_jws(obj, x) -> bool
await authenticate(request) -> (body: bytes, peer: Peer)   # Peer(kid, friend, tier, signed, status); a bad signature raises 401
await signed_post(url, payload, *, to_kid, headers=None) -> httpx.Response
tier_scopes(tier) -> dict
friend(fid) / friend_by_kid(kid) -> dict | None       # a friends row; name already prefers alias
card_status() -> str                                  # the status text Alex wrote on the card page, empty if none (layer ③ cuts it by tier)
```

In the other direction layer ② uses layer ③'s `cardagent.answer(...)` and `cardagent.retract(log_id, *, replaced=False)`. Cards where the card agent needs the owner's say are inbox items of kind `social` (the `inbox.py` changes belong to layer ③); if layer ② ever needs a social card it uses a dedupe starting with `friend:` and registers its handler in `cardagent.SOCIAL_HOOKS["friend"]`. Hooks for this kind never post anything into the main agent's thread: not one word the other side wrote reaches the main agent.

## 10. Push and unread

- Incoming `text` / `share` / `ask` count as unread (the Friends side of the Chat tab and the app badge); they're marked read while that chat is on screen.
- Push for friends is a new kind of notification and **stays off until the owner agrees** (server.json `social.push`). Proposed: a friend's message → ring (quiet during quiet hours); the card agent answered for you → quiet; someone used your invite → quiet.

## 11. Testing and going live

- Two test servers on one machine can act as two people, each with its own data directory and key. `share.public_url` is `http://127.0.0.1:<public port>` and `social.allow_http: true`.
- Going live: both sides need a public address with `/f` on it (the installer's public question sets `share.public_url` / `share.public_port` and opens Funnel `/s` and `/f`).
