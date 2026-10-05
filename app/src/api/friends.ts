// 朋友（server/friends.py，社交第二层）：邀请码加朋友、朋友聊天、分享发给朋友、对着分享追问（对方的名片 agent 代答）、我的名片 agent 的档位。
// 协议见 ../../docs/social-protocol.zh-CN.md。服务器之间的事（签名、投递、重试）都在服务器上，app 只读写自己的服务器。
import { L } from '../i18n';
import { request } from './base';
import type { Share } from './share';

export type Tier = 'close' | 'friend' | 'mate';
export type AnyTier = Tier | 'stranger';
export type FriendStatus = 'active' | 'removed' | 'gone' | 'blocked';

export interface FriendLast { text: string; ts: string; kind: FriendMsgKind; dir: 'in' | 'out'; by: 'person' | 'agent' }
export interface Friend {
  id: string; name: string; cardName: string; alias: string | null; tier: Tier; tierName: string; status: FriendStatus;
  caps: string[]; /** 对方有名片 agent（能替他答追问） */ agent: boolean;
  fingerprint: string; url: string; note: string | null; createdAt: string; unread: number; last: FriendLast | null;
}
export interface Invite {
  id: string; note: string; tier: Tier; createdAt: string; expiresAt: string; status: 'open' | 'used' | 'expired' | 'revoked';
  usedAt: string | null; usedBy: { id: string; name: string } | null;
  /** 只在刚生成时有：邀请码（一个网址）和它的二维码（SVG path，边长 size 格，含白边） */
  code?: string; qr?: { size: number; path: string };
}
export interface FriendsHome {
  /** 能不能加朋友：没有外面打得进来的地址（no_url）或没设称呼（no_name）就不行 */
  ready: boolean; why: 'no_url' | 'no_name' | null;
  /** suggest：还没设名字时建议的（这台机器的用户全名，可能为空） */
  me: { name: string; fingerprint: string; url: string | null; suggest?: string };
  /** 我的名片 agent 开着没有（开着才会替我答朋友的追问） */
  agent: boolean;
  friends: Friend[]; invites: Invite[];
  /** 有对外地址，但外面连不进来（Funnel 没开 /f，或者朋友的服务器试过、连不上）；publicFix = 在服务器上要跑的那一句。老服务器没有 */
  unreachable?: boolean; publicFix?: string;
}

export type FriendMsgKind = 'text' | 'share' | 'ask' | 'answer' | 'system';
export interface SharedSnapshot { sid: string; kind: string; title: string; text: string; quote: string; when: string; link: string | null; can_ask: boolean }
export interface FriendMsg {
  id: number; mid: string; dir: 'in' | 'out'; kind: FriendMsgKind; by: 'person' | 'agent'; text: string; replyTo: string | null;
  /** 发出去的：queued 发送中 / sent 送到了 / failed 没送到；收到的：new / read；都可能 revoked（收回了） */
  status: 'queued' | 'sent' | 'failed' | 'new' | 'read' | 'revoked' | 'local';
  /** 我的名片 agent 替我答的：pending 等我看 / ok 没问题 / edited 我改过 / revoked 我收回了 */
  review: 'pending' | 'ok' | 'edited' | 'revoked' | null;
  ts: string; edited: boolean;
  share?: SharedSnapshot; about?: string | null; used?: string[]; usedLabel?: string; defer?: boolean; outcome?: string; error?: string | null;
  /** 发出去试过、没送到、还在自动重试（status 仍是 queued）：下一次什么时候试；error 是上一次的原因 */
  nextTry?: string | null;
  /** 我的名片 agent 的代答过 Doorman 的结论（只在我这边） */
  sentinel?: SentinelVerdict;
}
/** Doorman（名片 agent 说出去之前再过一道）对一句的结论：pass 放行 / hold 扣下 / fail 复查不了（换成了固定的话）；
 * released = 扣下后你放行的，owner = 你自己写的 */
export interface SentinelVerdict { verdict: 'pass' | 'hold' | 'fail' | 'released' | 'owner' | string; reasons: { kind: string; detail: string }[]; via?: string; ms?: number }
export interface FriendThread { friend: Friend; messages: FriendMsg[]; recent: FriendMsg[]; agent: boolean; canAsk: boolean }

export interface CardSettings {
  tiers: Record<AnyTier, Record<ScopeKey, string>>;
  scopes: Record<ScopeKey, string[]>;
  status: string;
  people: Record<Tier, { id: string; name: string }[]>;
  agent: boolean;
  tierNames: Record<AnyTier, string>;
  /** 朋友看到的名字（server.json 的 user_name）；suggest = 还没设时建议的。老服务器没有 */
  name?: string; suggest?: string;
}
export type ScopeKey = 'calendar' | 'status' | 'shares' | 'notes' | 'address';

export const home = () => request<FriendsHome>('/api/friends');
export const newInvite = (b: { note?: string; tier: Tier; days?: number }) =>
  request<{ invite: Invite }>('/api/friends/invites', { method: 'POST', body: b }).then((j) => j.invite);
export const withdrawInvite = (id: string) => request(`/api/friends/invites/${id}`, { method: 'DELETE' });
export const preview = (code: string) =>
  request<{ name: string; fingerprint: string; url: string; agent: boolean; already: string | null }>('/api/friends/preview', { method: 'POST', body: { code }, timeoutMs: 20000 });
/** unreachable：对方的服务器试着连回你、没连上（你的公网访问没开好）；老服务器没有这一项 */
export const accept = (b: { code: string; tier: Tier; alias?: string }) =>
  request<{ friend: Friend; unreachable?: boolean; publicFix?: string }>('/api/friends/accept', { method: 'POST', body: b, timeoutMs: 30000 });
export const patchFriend = (id: string, b: { alias?: string; tier?: Tier }) =>
  request<{ friend: Friend }>(`/api/friends/${id}`, { method: 'PATCH', body: b }).then((j) => j.friend);
export const removeFriend = (id: string) => request(`/api/friends/${id}`, { method: 'DELETE' });
export const blockFriend = (id: string, blocked: boolean) =>
  request<{ friend: Friend }>(`/api/friends/${id}/block`, { method: 'POST', body: { blocked } }).then((j) => j.friend);

export const thread = (id: string, after?: number) =>
  request<FriendThread>(`/api/friends/${id}/messages${after ? `?after=${after}` : ''}`);
export const sendText = (id: string, text: string, replyTo?: string) =>
  request<{ message: FriendMsg }>(`/api/friends/${id}/messages`, { method: 'POST', body: { text, replyTo } }).then((j) => j.message);
export const ask = (id: string, about: string, text: string) =>
  request<{ message: FriendMsg }>(`/api/friends/${id}/ask`, { method: 'POST', body: { about, text } }).then((j) => j.message);
export const markRead = (id: string, upto?: number) => request(`/api/friends/${id}/read`, { method: 'POST', body: { upto } });
export const review = (msgId: number, action: 'ok' | 'edit' | 'revoke', text?: string) =>
  request<{ message: FriendMsg }>(`/api/friends/messages/${msgId}/review`, { method: 'POST', body: { action, text } }).then((j) => j.message);
export const revokeMsg = (msgId: number) =>
  request<{ message: FriendMsg }>(`/api/friends/messages/${msgId}/revoke`, { method: 'POST' }).then((j) => j.message);
export const retry = (msgId: number) =>
  request<{ message: FriendMsg }>(`/api/friends/messages/${msgId}/retry`, { method: 'POST' }).then((j) => j.message);

/** 分享发给朋友：ask = 朋友能追问（还看对方那一档），link = 有链接的人都能看（否则只发给这几个朋友，链接不开） */
export const sendShare = (sid: string, b: { friends: string[]; ask: boolean; link: boolean; text?: string }) =>
  request<{ share: Share; sent: number }>(`/api/shares/${sid}/send`, { method: 'POST', body: b });

// —— agent 之间（社交第三层，../../server/cardagent.py + a2a.py，协议见 ../../docs/a2a.zh-CN.md） ——

/** 名片 agent 出给你的一张卡（card_asks）：卡过了 7 天不在收件箱里，靠它还能写一行结果 */
export interface CardAsk {
  /** review = Doorman 扣下的一句（照发 / 改一下 / 不发） */
  kind: 'decision' | 'private' | 'review'; status: string; outcome: string; summary: string;
  proposal: { what?: string; date?: string; start?: string; end?: string; place?: string } | null;
}
/** 名片 agent 进出的一句（card_log）。by：them 对方说的 / agent 名片 agent 说的 / owner 你在卡上点了、它替你转告的 */
export interface CardLogItem {
  id: string; ts: string; peer: string; peerName: string; tier: string; channel: string; ref: string;
  dir: 'in' | 'out'; by: 'them' | 'agent' | 'owner'; text: string; used: string[]; usedLabel: string;
  /** received 收到 / sent 说出去了 / limited 到了今天的上限 / failed 没送到 / retracted、replaced 你收回、改过（text 是空的） */
  status: string; inboxId: string | null; ask: CardAsk | null; outcome: string;
  /** 对方要它做、它没照做的；blocked：服务端拦下了它原本要说的（原因），original 是原句（只给你看） */
  declined: string[]; blocked: string[]; original: string;
  /** 说出去的：Doorman 的结论；进来的：injection = 这句像是在指挥你的名片 agent */
  sentinel: SentinelVerdict | null; injection: boolean;
}
/** 你的名片 agent 去问朋友的 agent（a2a_out）。outcome = 对方本人在卡上的决定 */
export interface A2AOut {
  id: string; friend: string; contextId: string | null; taskId: string | null; state: string | null;
  text: string; reply: string | null; outcome: string; usedLabel: string; createdAt: string; updatedAt: string;
  /** 同一个任务里后来又接着问了：进度、决定和按钮只画在最近那一条上 */
  later: boolean;
}

export const cardLog = (peer: string) =>
  request<{ items: Partial<CardLogItem>[] }>(`/api/card/log?peer=${encodeURIComponent(peer)}&channel=a2a&limit=300`)
    // 老服务器没有 channel 筛选，也没有 by / usedLabel / ask 这些：补上默认值，只留 A2A 的
    .then((j) => (j.items ?? []).filter((x) => x.channel === 'a2a').map((x): CardLogItem => ({
      id: x.id ?? '', ts: x.ts ?? '', peer: x.peer ?? '', peerName: x.peerName ?? '', tier: x.tier ?? '', channel: 'a2a', ref: x.ref ?? '',
      dir: x.dir === 'in' ? 'in' : 'out', by: x.by ?? (x.dir === 'in' ? 'them' : 'agent'), text: x.text ?? '', used: x.used ?? [],
      usedLabel: x.usedLabel ?? '', status: x.status ?? '', inboxId: x.inboxId ?? null, ask: x.ask ?? null, outcome: x.outcome ?? '',
      declined: x.declined ?? [], blocked: x.blocked ?? [], original: x.original ?? '', sentinel: x.sentinel ?? null, injection: !!x.injection,
    })));
const outOf = (x: Partial<A2AOut>, friend: string): A2AOut => ({
  id: x.id ?? '', friend: x.friend ?? friend, contextId: x.contextId ?? null, taskId: x.taskId ?? null, state: x.state ?? null,
  text: x.text ?? '', reply: x.reply ?? null, outcome: x.outcome ?? '', usedLabel: x.usedLabel ?? '', createdAt: x.createdAt ?? '', updatedAt: x.updatedAt ?? '',
  later: !!x.later,
});
/** refresh：还在等对方本人的，服务器顺手问一下对方到哪了（下一次读就是新的） */
export const a2aOut = (friend: string, refresh = false) =>
  request<{ items: Partial<A2AOut>[] }>(`/api/a2a/out?friend=${encodeURIComponent(friend)}&limit=100${refresh ? '&refresh=true' : ''}`)
    .then((j) => (j.items ?? []).map((x) => outOf(x, friend)));
/** 让你的名片 agent 去问朋友的 agent 一句（原样发过去；对方的回话只给你看）。接着上一轮说：带上那一轮的 contextId / taskId。
 * 对方的名片 agent 要调模型，可能要等十几秒。 */
export const a2aSend = (friend: string, text: string, prev?: { contextId: string | null; taskId: string | null } | null) =>
  request<{ id: string; contextId: string | null; taskId: string | null; state: string | null; reply: string; used: string; item?: Partial<A2AOut> }>(
    '/api/a2a/send', { method: 'POST', body: { friend, text, contextId: prev?.contextId || undefined, taskId: prev?.taskId || undefined }, timeoutMs: 100000 })
    // 老服务器不回 item：按回的几个字段拼一条
    .then((j) => outOf(j.item ?? { id: j.id, contextId: j.contextId, taskId: j.taskId, state: j.state, text, reply: j.reply, usedLabel: j.used,
      createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() }, friend));
export const a2aRefresh = (id: string, friend: string) =>
  request<{ item: Partial<A2AOut> }>(`/api/a2a/out/${encodeURIComponent(id)}/refresh`, { method: 'POST', timeoutMs: 40000 }).then((j) => outOf(j.item, friend));

export const card = () => request<CardSettings>('/api/card');
/** 名片 agent 走哪条路；sentinel：复查走哪条路（off = 只有规则）、今天查了几句 / 扣下几句 / 复查不了几句。老服务器没有 sentinel。 */
export interface CardHealth {
  backend: string; lastError: { at: string; backend: string; error: string } | null;
  sentinel?: { backend: string; today: { checked: number; held: number; failed: number }; lastError: { at: string; error: string } | null };
}
export const cardHealth = () => request<CardHealth>('/api/card/health');
export const patchCard = (b: { tiers?: Partial<Record<AnyTier, Partial<Record<ScopeKey, string>>>>; status?: string; name?: string }) =>
  request<CardSettings>('/api/card', { method: 'PATCH', body: b });
/** 设对外名字。老服务器不认 name（悄悄忽略、回来的名片里也没有 name）：报错，说清要升级服务器或在服务器上用命令设。 */
export const setMyName = async (name: string) => {
  const c = await patchCard({ name });
  if (c.name === undefined) {
    throw new Error(L(`服务器版本较旧，暂不支持在 app 中设置名字。请更新服务器，或在服务器上运行：python3 ~/.openmousse/repo/server/settings_ctl.py user-name "${name}"`,
      `This server is too old to set the name from the app. Update the server, or run on it: python3 ~/.openmousse/repo/server/settings_ctl.py user-name "${name}"`));
  }
  return c;
};

/** 贴进来的文字里有没有邀请码（…/f/i/<令牌>/<公钥>，或 openmousse://friends/add?code=…）。 */
export function findCode(text: string): string | null {
  let s = text || '';
  const q = /[?&]code=([^&\s]+)/.exec(s);
  if (q && !s.includes('/f/i/')) { try { s = decodeURIComponent(q[1]); } catch { /* 原样 */ } }
  const m = /(https?:\/\/[^\s/?#<>"']+)\/f\/i\/([A-Za-z0-9_-]{22})\/([A-Za-z0-9_-]{43})/.exec(s);
  return m ? m[0] : null;
}
