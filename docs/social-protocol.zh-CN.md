# OpenMousse 社交协议 v1

[English](social-protocol.md) · 中文

> 2026-09-28 定稿第一版。社交分三层：① 分享（`/s/`，已上线）② 朋友（本文：身份、邀请码、好友、签名、朋友聊天、名片 agent 代答分享的追问）③ agent 之间（A2A，见 [a2a.md](a2a.md)）。第三层用这里的身份和签名，不另起一套。

## 0. 一句话

每个人一台自己的服务器，服务器之间直接用 HTTPS 说话。**身份是一把 Ed25519 密钥**：地址可以换，钥匙不换。加朋友 = 拿到对方给的一次性邀请码；之后服务器之间的每个请求都带签名，收的一方按钥匙认人，不按 IP、不按域名。对外只开 `/f`（和第一层的 `/s`），外面进来的请求碰不到 `/api`，也碰不到主 agent。朋友发来的一切都是资料，不是指令。

## 1. 谁管什么

| | 做什么 | 代码 |
|---|---|---|
| ① 分享 | `/s/<令牌>` 链接页、干净版卡片 | `server/share.py`、`server/public.py` |
| ② 朋友 | 身份、邀请码、好友表、请求签名、朋友聊天、分享发给朋友、分享的追问（名片 agent 代答）、档位设置 | `server/social.py`、`server/public.py`、app |
| ③ agent 之间 | A2A 名片和接口、名片 agent 的规矩（档位 → 能说什么、进来的当资料、要表态的出卡、说出去的记下来、条数上限） | `server/a2a.py`、`server/cardagent.py` |

名片 agent 调模型只走 `cardagent.py`：**不经 claw 的对话接口**（那是带工具、带主人权限的完整 agent，对方一句话就可能指挥它）。第二层代答分享的追问也调 `cardagent.answer()`，自己不调模型。

## 2. 身份

- **密钥**：每台服务器一把 Ed25519，存 `<data_dir>/social/identity.json`（0600，`{"v": 1, "seed": "<32 字节种子 base64url>", "created_at": "…"}`），第一次用到时生成，跟 data_dir 一起进备份。和 OpenClaw 设备身份（`gateway-device.json`）分开。
- **公钥**：JWK `{"kty": "OKP", "crv": "Ed25519", "x": "<公钥 32 字节 base64url>"}`。
- **kid**：JWK 指纹（RFC 7638）= `base64url(sha256('{"crv":"Ed25519","kty":"OKP","x":"<x>"}'))`，43 个字符。好友表、请求签名、名片签名、A2A 名片都用它指这把钥匙。
- **给人看的指纹**：SHA-256(公钥 32 字节) 的 base32（大写、不补 `=`）前 10 位，分两组：`K7Q2M 9XJ4P`。两个人打电话能对一下。
- **丢了钥匙 = 换了一个人**：朋友要重新加。v1 没有换钥匙的流程。

### 2.1 社交名片 `GET /f/card`

```json
{
  "openmousse": "1",
  "kid": "<kid>",
  "key": {"kty": "OKP", "crv": "Ed25519", "x": "<x>"},
  "url": "https://alex.example.ts.net",
  "name": "Alex",
  "caps": ["chat", "ask"],
  "updated_at": "2026-09-28T17:00:00+01:00",
  "signatures": [{"protected": "<b64url 头>", "signature": "<b64url 签名>"}]
}
```

- `url`：对外的根地址，只有 scheme + host（+ 端口），没有路径 = server.json 的 `share.public_url`。社交接口都在 `<url>/f/…`，反向代理不许改写 `/f` 这一段（签名签了路径）。
- `name`：server.json 的 `user_name`。没设称呼不能加朋友（app 先让你设）。
- `caps`：`chat` 朋友聊天、`ask` 分享能被追问（名片 agent 代答）；第三层上线后加 `a2a`，同时多一个字段 `"a2a": "<url>/f/a2a/agent-card.json"`。
- 名片里只放字符串、数组、对象，不放数字和小数（规范化见下）。
- 这份是社交名片，**A2A 名片另是一份**（`/f/a2a/agent-card.json`，第三层）：同一把钥匙签，A2A 名片的身份 extension 里放 `kid`、`x` 和 `"card": "<url>/f/card"`。分两份是因为两层不一定同时上线，没有 A2A 接口时 A2A 名片不成立；另外别家 SDK 验 A2A 名片时会把它转成 proto 再转回来（丢掉 proto 里没有的字段和空值），这个坑只留在给别家看的那一份上。

### 2.2 名片签名（和 A2A 8.4 同一个办法）

- `signatures` 是数组，每项 `{"protected", "signature"}`（`header` 可选，我们不用）。
- `protected` = base64url(JSON 头)，头 = `{"alg": "EdDSA", "kid": "<kid>", "typ": "JOSE"}`。`alg` 用 RFC 8037 的 `EdDSA`，不用 RFC 9864 的 `Ed25519`（官方 A2A SDK 用的 PyJWT 只认 `EdDSA`）；验的时候两个都认。
- 签的内容 = 名片去掉 `signatures` 以后按 JCS（RFC 8785）规范化的字节；签名输入 = `protected + "." + base64url(规范化后的名片)`（payload 分离，名片本身不放进 JWS）。
- JCS：键按字典序、没有空白、UTF-8、字符串按 JSON 最小转义。名片里没有数字，所以等于 Python 的 `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()`。
- 验名片：`kid` == 指纹(`key`)，头里的 `kid` == 名片的 `kid`，用 `key` 验签通过。**信任的来源是邀请码里带的公钥**（见 3），不是域名。

`/f/jwks.json` 同时发 `{"keys": [{"kty", "crv", "x", "kid", "use": "sig", "alg": "EdDSA"}]}`，给别家按 `jku` 找钥匙用。

## 3. 邀请码

**样子**：一个网址

```
https://alex.example.ts.net/f/i/<令牌>/<x>
```

- 令牌：16 字节随机数 base64url（22 个字符），一次性，默认 7 天过期（最长 30 天）。服务器只存 `sha256(令牌)`。
- `x`：邀请人的公钥（43 个字符）。拿到邀请码的一方用它核对对方的名片和回应，不光信域名。
- 相机扫二维码 → 打开落地页；复制粘贴 → 粘进 app 的「加朋友」。app 认任何含 `/f/i/<令牌>/<x>` 的文字。

**落地页** `GET /f/i/<令牌>/<x>`：一页纯 HTML（和分享页一样 noindex、no-referrer、不带脚本）：「Alex 邀请你在 OpenMousse 里加他为朋友」、怎么加（打开 app → 对话 → 朋友 → 加朋友 → 粘贴这个链接）、「在 app 里打开」（`openmousse://friends/add?code=<整个网址>`）。**GET 从不消耗令牌**（聊天软件的链接预览会先打开一次）。令牌不对只说「这个邀请码用过了或者过期了」。

**生成**（邀请人在 app 里）：选「给谁」（备注，只自己看）、他进来以后在哪一档（默认朋友）、几天有效 → 二维码 + 链接。没用过的能收回。同时最多 20 张有效的。

**兑换**：

1. 被邀请的人在 app 里粘贴 / 扫码 → 她的服务器拆出根地址、令牌、`x`，`GET <根地址>/f/card`，核对 `key.x == x`、签名对 → app 显示「加 Alex 为朋友？指纹 K7Q2M 9XJ4P」，她选档位。
2. 她的服务器发签名请求（见 5）`POST <根地址>/f/hello`：

   ```json
   {"v": 1, "token": "<令牌>", "card": {<她的签名名片>}}
   ```

3. 邀请人的服务器核对：令牌存在、没用过、没过期、没收回；`card` 验签通过；请求签名的 `keyid` == `card.kid`；`card.url` 是 https（测试服开了 `social.allow_http` 才收 http）。都对 → 建好友（档位 = 生成邀请时选的），令牌记为用过（`used_by` = 她的 kid），记活动记录，回：

   ```json
   {"ok": true, "card": {<邀请人的签名名片>}}
   ```

   幂等：令牌用过、但 `used_by` 就是这个 kid → 照样回 ok（网断了重试）；已经是朋友 → 回 ok，顺手更新地址和名字；以前删过的 → 重新变成朋友。
4. 她的服务器核对回来的名片：验签通过、`kid` == 指纹(`x`)、`url` 和邀请码的根地址一样 → 建好友（档位 = 她选的）。两边对话里各出一行灰字「你们成了朋友」。

错误一律笼统：`404 {"error": "invite_invalid"}`（不说是用过了、过期了还是收回了）、`400 {"error": "bad_card"}`、`401 {"error": "bad_signature"}`。

**两边都要有公网根地址**（`share.public_url`，外面能打进来）：只有一边有，消息只能单向，所以没有的一方 app 先让开（安装脚本那一问）。

## 4. 表（grava.db，`social.py` 建）

```sql
friends          (id TEXT PRIMARY KEY,            -- fr-<8 位 hex>，本机的
                  kid TEXT NOT NULL UNIQUE,         -- 对方的钥匙
                  pub TEXT NOT NULL,                -- 对方公钥 x
                  url TEXT NOT NULL,                -- 对方根地址，接口在 <url>/f/…
                  name TEXT NOT NULL,               -- 对方名片上的名字
                  alias TEXT,                       -- 我给的备注（显示优先）
                  tier TEXT NOT NULL DEFAULT 'friend',   -- close | friend | mate
                  status TEXT NOT NULL DEFAULT 'active', -- active | removed（我删的）| gone（对方删了我）| blocked
                  caps TEXT NOT NULL DEFAULT '[]',  -- 对方名片的 caps
                  card TEXT,                        -- 对方最近一份验过的名片（JSON）
                  a2a TEXT,                         -- 对方 A2A 名片地址（没有就空）
                  note TEXT,                        -- 怎么认识的（邀请码的备注）
                  via TEXT,                         -- invite:<id>（我邀请的）| code（我兑换了对方的）
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, seen_at TEXT)
friend_invites   (id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, note TEXT, tier TEXT NOT NULL,
                  created_at TEXT NOT NULL, expires_at TEXT NOT NULL, used_at TEXT, used_by TEXT, revoked_at TEXT)
friend_messages  (id INTEGER PRIMARY KEY AUTOINCREMENT, friend TEXT NOT NULL, mid TEXT NOT NULL,
                  dir TEXT NOT NULL,                -- in | out
                  kind TEXT NOT NULL,               -- text | share | ask | answer | system
                  by TEXT NOT NULL DEFAULT 'person',-- person | agent（名片 agent 代答的）
                  text TEXT NOT NULL DEFAULT '', data TEXT,  -- data：分享快照、used、defer、log_id……（JSON）
                  reply_to TEXT, status TEXT NOT NULL,       -- 发出：queued | sent | failed；收到：new | read；都可能 revoked
                  review TEXT,                      -- 名片 agent 替我答的：pending | ok | edited | revoked
                  ts TEXT NOT NULL, recv_at TEXT, edited_at TEXT,
                  tries INTEGER NOT NULL DEFAULT 0, next_try TEXT, error TEXT,
                  UNIQUE (friend, dir, mid))
social_nonces    (kid TEXT NOT NULL, nonce TEXT NOT NULL, at REAL NOT NULL, PRIMARY KEY (kid, nonce))
social_settings  (key TEXT PRIMARY KEY, value TEXT NOT NULL)   -- tiers（各档范围 JSON）、status（近况）、announced（上次通知朋友的名片摘要）
```

## 5. 请求签名（RFC 9421 的一个固定用法）

服务器之间的每个请求（`/f/hello`、`/f/msg`，第三层的 `/f/a2a`）都签。一律 `POST` JSON，不带查询串。

请求头：

```
Content-Type: application/json
Content-Digest: sha-256=:<base64(sha256(body))>:
Mousse-To: <收件人 kid>
Signature-Input: om=("@method" "@path" "content-digest" "mousse-to");created=1790612345;nonce="<22 位 base64url>";keyid="<发件人 kid>";alg="ed25519";tag="openmousse/1"
Signature: om=:<base64(签名)>:
```

签名底稿（RFC 9421 §2.5，每行之间 `\n`，最后没有换行）：

```
"@method": POST
"@path": /f/msg
"content-digest": sha-256=:…:
"mousse-to": <收件人 kid>
"@signature-params": ("@method" "@path" "content-digest" "mousse-to");created=1790612345;nonce="…";keyid="…";alg="ed25519";tag="openmousse/1"
```

- `Content-Digest` 是 RFC 9530；base64 是标准 base64（带 `=`），这两个头按规范用 `:…:` 包起来。其它地方的 base64url 都不补 `=`。
- `Mousse-To`：收件人的 kid，签进去，防止朋友把我发给他的请求原样转给另一个朋友（Funnel / 反向代理后面 Host 不可靠，所以不签 `@authority`）。
- JSON-RPC（A2A）所有方法都 POST 同一个地址、方法名在 body 里，所以 `content-digest` 一定要签。
- 发到 A2A 接口的签名请求另加 `A2A-Extensions: https://openmousse.ai/a2a/ext/signed-requests/v1`（第三层在 A2A 名片里把它声明成 `required: false` 的 extension）。

**收的一方怎么验**（`social.authenticate()`）：

1. body 有上限（`/f/hello` 16 KB，其余 256 KB），超了 413。
2. 没有 `Signature` / `Signature-Input` → 陌生人（`/f/hello`、`/f/msg` 直接 401；A2A 接口当陌生档）。
3. 有签名就必须全对，否则 **401**，不降级成陌生人：标签 `om`；四个部件都在；`alg` = `ed25519`；`created` 在现在 ±300 秒以内；`nonce` 16–64 个字符、这个 kid 10 分钟内没用过（存 `social_nonces`）；`Content-Digest` 和 body 对得上；`Mousse-To` == 我的 kid；按 `keyid` 找到公钥（好友表；`/f/hello` 用 body 里名片的 `key`，且要 `keyid == card.kid`）后验签通过。
4. 结果是 `Peer(kid, friend, tier, signed, status)`，`authenticate()` 只认人、不替路由做决定：`status` = 好友行的状态（表里没有 = `None`，否则 `active` / `removed` / `gone` / `blocked`，另有 `peer.blocked`）。只有 `active` 的 `friend` 非空、`tier` 是它的档，其余一律 `friend = None`、`tier = "stranger"`。`keyid` 不在好友表里的签名验不了，当没签名的陌生人（`Peer()`，`kid` 也不留：可能是乱写的）。`/f/msg`：`removed` / `gone` 回 403 `not_friends`，`blocked` 回 200 但什么都不做，表里没有回 401 `unknown_sender`；A2A 接口 `blocked` 回 REJECTED。

回应不签名（HTTPS 保证是对方的服务器回的）；`/f/hello` 回的名片自己带签名。时钟要准（NTP），±300 秒。

## 6. 公网路径

公网只多开一条：Tailscale Funnel（或反向代理）`/f` → `http://127.0.0.1:<share.public_port>/f`，和 `/s` 同一个小服务（`public.py`），没有 `/api`、不认令牌也不认设备。要加朋友才开：安装器问「开公网」时答 y 就会开，或者自己跑那条 `tailscale funnel` 命令。

| 路径 | 谁调 | 签名 | 做什么 |
|---|---|---|---|
| `GET /f/card` | 任何人 | — | 社交名片（2.1） |
| `GET /f/jwks.json` | 任何人 | — | 公钥（JWKS） |
| `GET /f/i/<令牌>/<x>` | 浏览器 | — | 邀请落地页，不消耗令牌 |
| `POST /f/hello` | 被邀请人的服务器 | 要 | 兑换邀请码（3） |
| `POST /f/msg` | 朋友的服务器 | 要 | 投一条消息（7） |
| `/f/a2a`、`/f/a2a/agent-card.json` | 别的 agent | 可选 | 第三层（A2A JSON-RPC 和名片） |

`/.well-known/agent-card.json`（让陌生人 / 别家 agent 凭域名找到 Alex 的名片 agent）要再开一条 Funnel 路径，先不开。

主服务（私网 8080）不挂 `/f`：朋友只从公网来。app 用的是 `/api/friends…`、`/api/card`（要令牌）。

**限流**：每个朋友每分钟 30 个请求、每天 500 条；`/f/hello` 全局每小时 30 次（Funnel 后面看到的来源都是 127.0.0.1）；文字上限：`text` 4000 字、`ask` 1000 字、分享正文 60000 字。超了 429，带 `Retry-After`。名片 agent 自己的条数、长度上限在 `cardagent.py`（第三层）。

## 7. 消息 `POST /f/msg`

一个请求一条消息：

```json
{"v": 1, "id": "<32 位 hex>", "kind": "text", "at": "2026-09-28T19:05:12+01:00", "text": "对，下期专门讲这个", "reply_to": "<可选：另一条消息的 id>"}
```

`id` 由发的一方生成（uuid4 的 hex），收的一方按 (朋友, id) 去重；`at` 是发的一方写下它的时间（带时区）。

| kind | 字段 | 说明 |
|---|---|---|
| `text` | `text`、`reply_to?` | 人说的话 |
| `share` | `share: {sid, kind, title, text, quote, when, link?, can_ask}`、`text?` | 分享发给朋友：挡过私事以后的快照（挡住的地方是 `▇▇▇`，原文不出服务器）；`link` 只在「有链接的人都能看」时带；`can_ask` = 这条能不能追问（分享页的开关 × 对方的档位） |
| `ask` | `about`（分享那条消息的 id）、`text` | 对着分享追问对方的名片 agent |
| `answer` | `about`（追问那条的 id）、`text`、`used`（用了什么，比如 `["这期节目"]`）、`defer`（true = 「得问他本人」） | 名片 agent 代答（`by = agent`） |
| `edit` | `target`、`text`、`by?` | 改我先前发的一条（Alex 改名片 agent 的代答时 `by = person`，对方显示「Alex 改过」） |
| `revoke` | `target` | 收回我先前发的一条（对方那边清空正文，显示「Alex 收回了这条」） |
| `card` | `card` | 我的名片变了（换了地址、改了名字、多了能力）：对方验签、kid 一样就更新 |
| `bye` | — | 我把你删了：对方标 `gone`，不再往这边发 |

收的一方回 `200 {"ok": true, "id": "<id>", "dup": false}`（重复的 `dup: true`）；`edit` / `revoke` 的 `target` 不是这个人发给我的，照样 200、带 `"ignored": true`。错误：`400 bad_request`、`401 bad_signature`、`403 not_friends`、`413 too_large`、`429 slow_down`。

**投递**：发的一方先写进 `friend_messages`（`status = queued`）再发；网络错、5xx、429 按 30 秒、2 分钟、10 分钟、1 小时、6 小时，之后每 6 小时重试，3 天还没送到标 `failed`（app 里「没送到 · 重发」）；其余 4xx 直接 `failed`。地址换了（`share.public_url` 或名字变了）服务器自己给所有朋友发一条 `card`。

**分享的追问（名片 agent 代答）**：

1. 朋友那边在分享下面点「追问」→ `ask` 发到 Alex 的服务器。
2. Alex 的服务器核对：`about` 是我发给这个朋友的一条分享、分享没收回、`can_ask` 还开着、对方这一档的 `shares` 还是 `ask`。不满足 → 不调模型，回一条 `answer`（`defer: true`，「这个得问他本人」）。
3. 满足 → `cardagent.answer(friend, 问题, channel="chat", material=[{"id": "share:<sid>", "kind": "share", "title", "text"}], history=<这段聊天最近几轮>, ref="share:<sid>")`（第三层）→ `{text, used, defer, declined, limited, log_id}`，存成一条 `answer`（`by = agent`、`review = pending`、`data.log_id`），发出去。
4. Alex 的 app 里这条下面有一个只有他看得到的框：「没问题」（`review = ok`）/「我来改」（发 `edit`，`by = person`；`cardagent.retract(log_id, replaced=True)`，活动记录写「改了一条代答」）/「收回」（发 `revoke`；`cardagent.retract(log_id)`）。收回和改过的原文不再给名片 agent 当上下文。
5. `cardagent.py` 还没有（第三层没上线）时，追问一律不自动答，只出现在 Alex 的聊天里等他自己回。

**v1 不做**：群聊（「CS 小组 · 4 人」）、只收链接的联系人（「爸爸」这种没装 OpenMousse 的，第一层的链接已经够用）、图片和语音附件、已读回执、正在输入、换钥匙、朋友的朋友。

## 8. 档位

名片 agent 按人分档，每档能问到什么 Alex 在「我的名片 agent」里定：

| 键 | 取值 | 亲近 `close` | 朋友 `friend` | 同学 `mate` | 陌生 `stranger` |
|---|---|---|---|---|---|
| `calendar` 日程 | `detail` / `busy` / `none` | detail | busy | busy | none |
| `status` 近况 | `some` / `line` / `none` | some | line | none | none |
| `shares` 分享过的东西 | `ask` / `view` / `public` | ask | ask | view | public |
| `notes` 学习笔记 | `view` / `none` | view | view | view | none |
| `address` 住址 | `view` / `none` | view | none | none | none |

- 陌生 = 好友表里没有的：没签名、签名认不出、别家的 agent。它没有好友行，档位不能给某个人选。
- **健康和身体、世界树不是键**：没有能打开的开关，名片 agent 自己也看不到。
- 近况 = Alex 在名片页自己写的一段（`some` 给全部，`line` 只给第一行）；学习笔记只给 Alex 标了能分享的（默认一篇都没有）；住址读档案；日程读日程层（`busy` = 只给空闲时段）。取这些内容是第三层的事，存档位和设置页是第二层的事。
- 存在 `social_settings.tiers`；没存过就是上面的默认值。朋友不知道自己在哪一档。

## 9. `social.py` 给第三层的函数

```python
identity() -> {"kid", "x", "jwk"}                    # 没有就生成
fingerprint(x) -> "K7Q2M 9XJ4P"
jcs(obj) -> bytes                                     # 名片这种只有字符串的 JSON 的规范化
sign_jws(obj) -> {"protected", "signature"}           # 签 obj 去掉 signatures 后的 JCS，payload 分离
verify_jws(obj, x) -> bool
await authenticate(request) -> (body: bytes, peer: Peer)   # Peer(kid, friend, tier, signed, status)；签名不对直接 401
await signed_post(url, payload, *, to_kid, headers=None) -> httpx.Response
tier_scopes(tier) -> dict
friend(fid) / friend_by_kid(kid) -> dict | None       # friends 表一行；name 已按 alias 优先
card_status() -> str                                  # Alex 在名片页写的近况原文，没写就空（第三层按档位切）
```

反过来第二层用第三层的：`cardagent.answer(...)`、`cardagent.retract(log_id, *, replaced=False)`。名片 agent 要 Alex 表态的卡是收件箱的 `social` 类（`inbox.py` 的改动归第三层）；第二层要出社交卡的话 dedupe 用 `friend:` 开头，处理函数注册进 `cardagent.SOCIAL_HOOKS["friend"]`。这类卡的钩子不往主 agent 的线程里发任何话：对方说的一个字都不进主 agent。

## 10. 推送、未读

- 朋友发来的 `text` / `share` / `ask` 算未读（「对话」tab 的「朋友」段和 app 角标都算）；app 开着那段聊天时标为已读。
- 推送是新的推送类型，**默认不开，主人同意了才开**（server.json `social.push`）：提议朋友发来的话 → 响（静默时段降成静音）；名片 agent 替你答了一条 → 静音；有人用了你的邀请码 → 静音。

## 11. 测试和上线

- 本机起两套测试服就能当两个人（各自的数据目录和钥匙）。`share.public_url` 填 `http://127.0.0.1:<公网端口>`，`social.allow_http: true`。
- 上线：两边都要有公网地址、上面开着 `/f`（安装器问「开公网」时答 y，会写 `share.public_url` / `share.public_port` 并开 Funnel `/s` 和 `/f`）。
