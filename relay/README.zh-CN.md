# OpenMousse 中继

[English](README.md) · 中文

没有公网入口的 OpenMousse 服务器（没开 Tailscale Funnel、Funnel 没开成、家里的电脑），好友照样找得到它。

- 服务器自己往外连中继（`server/relay.py`，默认 `https://relay.openmousse.ai`），用社交那把 Ed25519 钥匙证明身份，保持一条 WebSocket。中继给它一个地址 `https://relay.openmousse.ai/u/<kid>`，好友的服务器往这个地址发的 `/f/…` 请求经那条连接转过去。
- 新版服务器之间，消息、握手（邀请码）和 A2A 都是**端到端信封**（`POST /f/sealed`，X25519 + HKDF-SHA256 + ChaCha20-Poly1305）：中继只看得到收件人、大小和时间，看不到内容。公开的名片照常可见。
- 中继**不存任何东西**：对方不在线回 503，发件那边自己重试（最多 3 天）。
- 只转 `/f/` 下面的 GET / POST / HEAD；请求和回应各最多 300 KB；一个地址每分钟最多 240 个请求；回应只放行 JSON 类，HTML 一律不转，邀请落地页由中继自己画。
- 一个 kid 一个 Durable Object（`Mailbox`），只认最新的一条连接；kid 必须是连上来那把钥匙的指纹（RFC 7638），别人冒充不了。

协议全文：[`docs/social-protocol.zh-CN.md`](../docs/social-protocol.zh-CN.md) 6.1。

## 服务器这边

默认开着，不用配。连上过中继才把中继地址写进名片；有能用的 `share.public_url`（Funnel / 自己的域名）就直连，名片上另带中继地址当备用。

```json
"relay": false                                   // 关掉
"relay": {"url": "https://relay.example.com"}    // 换成你自己部署的中继
```

`bash check.sh` 的「分享和好友」一节会显示连没连上。

## 本地跑

```bash
cd relay
npm install
npx wrangler dev --port 8790 --ip 127.0.0.1
```

两台测试服的 server.json 写 `"relay": {"url": "http://127.0.0.1:8790"}`、`"social": {"allow_http": true}`，其中一台去掉 `share.public_url`，就是「只靠中继」的那台。

## 部署（Cloudflare Workers）

1. `npm install`，然后 `npx wrangler login`（或者设 `CLOUDFLARE_API_TOKEN`：Workers Scripts 编辑 + 这个域名的 Workers Routes 和 DNS 编辑）。
2. `wrangler.toml` 的 `routes` 改成你的域名（官方的是 `relay.openmousse.ai`，Cloudflare 自动建 DNS 记录和证书）。
3. `npx wrangler deploy`。部署完 `curl https://<你的域名>/health` 应该回 `{"ok":true,"service":"openmousse-relay","v":1}`。

Durable Objects 用 SQLite 版（`new_sqlite_classes`），Workers 免费档就能跑；用量上来再换付费档。
