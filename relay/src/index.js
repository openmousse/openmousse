// OpenMousse 中继（relay.openmousse.ai）：没有公网入口的 OpenMousse 服务器，朋友也能找到它。
//
// 每台服务器用自己的 Ed25519 身份往外连 /c/<kid>（WebSocket），这里给它一个地址 https://<中继>/u/<kid>。
// 朋友的服务器往 /u/<kid>/f/… 发的请求，经那条连接转给它，回应再送回去。一个 kid 一个 Durable Object（Mailbox），只认最新的一条连接。
//
// 规矩：
// - 只转 /f/ 下面的 GET / POST / HEAD，请求和回应各最多 300 KB，一个地址每分钟最多 240 个请求。
// - 回应只放行 JSON 类（名片、A2A、端到端信封），HTML 一律不转；邀请链接的落地页由中继自己画（名字从对方名片的 JSON 里取、转义）。
//   这样别人的服务器没法借 openmousse.ai 的域名放网页。
// - 不存任何东西：对方不在线回 503，发件那边按自己的规矩重试。消息正文在新版之间是端到端信封，这里看不到。
// - 握手：先发 {"t":"challenge","nonce"}，服务器回 {"t":"hello","key":<Ed25519 JWK>,"sig"}，签的是 "openmousse-relay/1|<kid>|<nonce>"；
//   kid 必须是这把钥匙的 JWK 指纹（RFC 7638），所以别人冒充不了这个地址。
// 服务器那边的代码在 server/relay.py，协议说明在 relay/README*.md。

const PROTO = 'openmousse-relay/1';
const REQ_TIMEOUT = 110_000;
const BODY_MAX = 300_000;
const RES_MAX = 300_000;
const RATE = 240;
const PASS_HEADERS = ['content-type', 'content-digest', 'mousse-to', 'signature-input', 'signature', 'accept', 'a2a-version',
  'a2a-extensions', 'x-a2a-notification-token', 'authorization', 'user-agent'];
const OK_TYPES = ['application/json', 'application/a2a+json', 'application/jwk-set+json', 'application/openmousse-sealed+json'];

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const p = url.pathname;
    if (p === '/' || p === '/health') return json({ ok: true, service: 'openmousse-relay', v: 1 });
    if (p === '/robots.txt') return new Response('User-agent: *\nDisallow: /\n', { headers: { 'content-type': 'text/plain' } });
    let m = p.match(/^\/c\/([A-Za-z0-9_-]{43})$/);
    if (m) {
      if ((request.headers.get('Upgrade') || '').toLowerCase() !== 'websocket') return json({ ok: false, error: 'expected_websocket' }, 426);
      return mailbox(env, m[1]).fetch(request);
    }
    m = p.match(/^\/u\/([A-Za-z0-9_-]{43})\/f\/[A-Za-z0-9._~/-]*$/);
    if (m && !p.includes('..')) return mailbox(env, m[1]).fetch(request);
    return json({ ok: false, error: 'not_found' }, 404);
  },
};

function mailbox(env, kid) {
  return env.MAILBOX.get(env.MAILBOX.idFromName(kid));
}

export class Mailbox {
  constructor(ctx, env) {
    this.ctx = ctx;
    this.env = env;
    this.pending = new Map();
    this.seq = 0;
    this.hits = [];
  }

  async fetch(request) {
    const url = new URL(request.url);
    const kid = url.pathname.split('/')[2];
    if (url.pathname.startsWith('/c/')) return this.connect(kid);
    return this.forward(request, url);
  }

  connect(kid) {
    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    const nonce = b64u(crypto.getRandomValues(new Uint8Array(24)));
    this.ctx.acceptWebSocket(server);
    server.serializeAttachment({ kid, nonce, authed: false });
    server.send(JSON.stringify({ t: 'challenge', v: 1, nonce }));
    return new Response(null, { status: 101, webSocket: client });
  }

  async webSocketMessage(ws, message) {
    if (typeof message !== 'string') return;
    let d;
    try { d = JSON.parse(message); } catch { return; }
    const a = ws.deserializeAttachment() || {};
    if (!a.authed) {
      if (d.t !== 'hello' || !(await verifyHello(a.kid, a.nonce, d))) {
        try { ws.send(JSON.stringify({ t: 'error', error: 'bad_hello' })); } catch { /* 已经断了 */ }
        ws.close(4003, 'bad hello');
        return;
      }
      ws.serializeAttachment({ ...a, authed: true });
      for (const other of this.ctx.getWebSockets()) {
        if (other !== ws) { try { other.close(4000, 'replaced'); } catch { /* 已经断了 */ } }
      }
      ws.send(JSON.stringify({ t: 'ok' }));
      return;
    }
    if (d.t === 'res') {
      const p = this.pending.get(d.id);
      if (p) { this.pending.delete(d.id); p(d); }
    }
  }

  async webSocketClose(ws, code, reason) {
    try { ws.close(code, reason); } catch { /* 已经关了 */ }
  }

  async webSocketError() { /* 下一次请求会发现没有连接，回 503 */ }

  socket() {
    return this.ctx.getWebSockets().find((w) => (w.deserializeAttachment() || {}).authed);
  }

  rateOk() {
    const now = Date.now();
    this.hits = this.hits.filter((t) => now - t < 60_000);
    if (this.hits.length >= RATE) return false;
    this.hits.push(now);
    return true;
  }

  ask(ws, frame) {
    const id = ++this.seq;
    return new Promise((resolve) => {
      const timer = setTimeout(() => { this.pending.delete(id); resolve(null); }, REQ_TIMEOUT);
      this.pending.set(id, (d) => { clearTimeout(timer); resolve(d); });
      try {
        ws.send(JSON.stringify({ t: 'req', id, ...frame }));
      } catch {
        clearTimeout(timer);
        this.pending.delete(id);
        resolve(null);
      }
    });
  }

  async forward(request, url) {
    if (!['GET', 'POST', 'HEAD'].includes(request.method)) return json({ ok: false, error: 'method_not_allowed' }, 405);
    if (!this.rateOk()) return json({ ok: false, error: 'slow_down' }, 429, { 'retry-after': '60' });
    const ws = this.socket();
    if (!ws) return json({ ok: false, error: 'offline' }, 503, { 'retry-after': '60' });
    const inv = url.pathname.match(/^\/u\/[A-Za-z0-9_-]{43}\/f\/i\/[A-Za-z0-9_-]{22}\/([A-Za-z0-9_-]{43})$/);
    if (inv && request.method === 'GET') return this.invitePage(ws, request, url, inv[1]);
    const body = request.method === 'POST' ? new Uint8Array(await request.arrayBuffer()) : new Uint8Array();
    if (body.length > BODY_MAX) return json({ ok: false, error: 'too_large' }, 413);
    const h = {};
    for (const k of PASS_HEADERS) {
      const v = request.headers.get(k);
      if (v !== null) h[k] = v;
    }
    const res = await this.ask(ws, { m: request.method, p: url.pathname, q: url.search, h, b: b64u(body) });
    if (!res) return json({ ok: false, error: 'timeout' }, 504);
    const rh = res.h || {};
    const type = String(rh['content-type'] || '').split(';')[0].trim().toLowerCase();
    const out = unb64u(String(res.b || ''));
    if (out.length > RES_MAX) return json({ ok: false, error: 'too_large' }, 502);
    if (out.length && !OK_TYPES.includes(type)) return json({ ok: false, error: 'blocked_content' }, 502);
    return new Response(request.method === 'HEAD' ? null : out, {
      status: Number(res.s) || 502,
      headers: {
        'content-type': rh['content-type'] || 'application/json',
        'cache-control': rh['cache-control'] || 'no-store',
        'x-content-type-options': 'nosniff',
        'x-robots-tag': 'noindex, nofollow',
      },
    });
  }

  async invitePage(ws, request, url, x) {
    const cardPath = url.pathname.replace(/\/f\/i\/.*$/, '/f/card');
    const res = await this.ask(ws, { m: 'GET', p: cardPath, q: '', h: { accept: 'application/json' }, b: '' });
    let name = '';
    try {
      const card = JSON.parse(new TextDecoder().decode(unb64u(String(res && res.b || ''))));
      if (card && card.key && card.key.x === x) name = String(card.name || '').slice(0, 60);
    } catch { /* 读不到名片：页面上不写名字 */ }
    const zh = /^zh/i.test(request.headers.get('accept-language') || '');
    const html = invitePageHtml({ name, fp: await fingerprint(x), link: url.toString(), zh });
    return new Response(html, {
      headers: {
        'content-type': 'text/html; charset=utf-8',
        'cache-control': 'no-store',
        'x-robots-tag': 'noindex, nofollow',
        'content-security-policy': "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'",
        'referrer-policy': 'no-referrer',
      },
    });
  }
}

async function verifyHello(kid, nonce, d) {
  try {
    const key = d.key;
    if (!key || key.kty !== 'OKP' || key.crv !== 'Ed25519' || typeof key.x !== 'string' || typeof d.sig !== 'string') return false;
    if ((await thumbprint(key.x)) !== kid) return false;
    const pub = await crypto.subtle.importKey('raw', unb64u(key.x), { name: 'Ed25519' }, false, ['verify']);
    return await crypto.subtle.verify({ name: 'Ed25519' }, pub, unb64u(d.sig), new TextEncoder().encode(`${PROTO}|${kid}|${nonce}`));
  } catch {
    return false;
  }
}

async function thumbprint(x) {
  const j = `{"crv":"Ed25519","kty":"OKP","x":"${x}"}`;
  return b64u(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(j))));
}

// 给人看的指纹：SHA-256(公钥 32 字节) 的 base32 前 10 位，分两组（和 server/social.py 的 fingerprint 一样）
async function fingerprint(x) {
  const h = new Uint8Array(await crypto.subtle.digest('SHA-256', unb64u(x)));
  const A = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  let bits = 0;
  let val = 0;
  let out = '';
  for (const byte of h) {
    val = (val << 8) | byte;
    bits += 8;
    while (bits >= 5 && out.length < 10) {
      out += A[(val >>> (bits - 5)) & 31];
      bits -= 5;
    }
    if (out.length >= 10) break;
  }
  return `${out.slice(0, 5)} ${out.slice(5, 10)}`;
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function invitePageHtml({ name, fp, link, zh }) {
  const deep = `openmousse://friends/add?code=${encodeURIComponent(link)}`;
  const t = zh
    ? { title: name ? `${name} 邀请你成为好友` : 'OpenMousse 好友邀请', steps: '<li>打开 OpenMousse</li><li>对话 → 好友 → 添加好友</li><li>粘贴下方链接</li>',
      fp: '核对指纹', open: '在 app 中打开', once: '此链接仅可使用一次。' }
    : { title: name ? `${name} invited you to be friends` : 'OpenMousse friend invite', steps: '<li>Open OpenMousse</li><li>Chat → Friends → Add a friend</li><li>Paste the link below</li>',
      fp: 'Check the fingerprint', open: 'Open in the app', once: 'This link works once.' };
  return `<!doctype html><html lang="${zh ? 'zh-CN' : 'en'}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>${esc(t.title)}</title>
<style>:root{color-scheme:light dark}body{margin:0;font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f4f2ee;color:#1d1b18}
main{max-width:440px;margin:0 auto;padding:40px 20px}h1{font-size:22px;margin:0 0 16px}ol{padding-left:20px}
.fp{font:600 15px ui-monospace,Menlo,monospace;letter-spacing:.06em}.link{word-break:break-all;font:13px ui-monospace,Menlo,monospace;background:#fff;border-radius:10px;padding:10px 12px}
a.btn{display:block;text-align:center;background:#c8963e;color:#fff;text-decoration:none;border-radius:12px;padding:13px;font-weight:600;margin:20px 0 8px}
.muted{color:#7a756d;font-size:14px}@media (prefers-color-scheme:dark){body{background:#151412;color:#ece8e1}.link{background:#24221f}}</style></head>
<body><main><h1>${esc(t.title)}</h1><ol>${t.steps}</ol><p class="link">${esc(link)}</p>
<p>${esc(t.fp)}：<span class="fp">${esc(fp)}</span></p><a class="btn" href="${esc(deep)}">${esc(t.open)}</a>
<p class="muted">${esc(t.once)}</p></main></body></html>`;
}

function json(obj, status = 200, extra = {}) {
  return new Response(JSON.stringify(obj), { status, headers: { 'content-type': 'application/json', 'cache-control': 'no-store', ...extra } });
}

function b64u(bytes) {
  let s = '';
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function unb64u(text) {
  const s = String(text).replace(/-/g, '+').replace(/_/g, '/');
  const bin = atob(s + '='.repeat((4 - (s.length % 4)) % 4));
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}
