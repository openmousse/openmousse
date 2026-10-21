// 群聊（主线第三步「Agents 和群聊」第二段，2026-10-21）：你和你自己的赛博、功能 Agent 碰撞想法。服务器 ../../server/groupchat.py，
// 录前讨论群 podprep.py，想法「拿去讨论」ideatalk.py。
// 服务器 server.json 的 groupchat 开着才有（接口回 404 = 关着）；useGroupsOn() 判断，没开就不显示「新建群聊」，一切照旧。
// 线程 gc-<id>：每条消息带发言人（speaker），系统行是主持人那边（main）；正在说的用 GET /api/groupchats/{id}/stream 接上（SSE，和会议一样）。
import { useEffect, useState } from 'react';
import { fetch as expoFetch } from 'expo/fetch';
import type { AgentColor, Attachment, PendingFile } from '../data/types';
import { authHeaders, fileUrl, getBase, httpStatus, request } from './base';
import { formFile, readSse, xhrUpload } from './client';
import type { CyberAvatarSpec } from './cybers';
import { supports } from './version';

export type MemberKind = 'cyber' | 'agent';
export interface GroupMember {
  id: string; kind: MemberKind; name: string; tag: string; model: string; avatar: CyberAvatarSpec | null;
  icon: string | null; color: AgentColor | null; gone: boolean;
}
/** 主持人：助手（main）/ 一位赛博；不设 = null */
export type GroupHost = { id: string; kind: 'main' | 'cyber'; name: string; avatar?: CyberAvatarSpec | null } | null;
export type GroupMode = 'mention' | 'host';
export interface GroupSource { kind: 'idea' | 'podcast'; id: string; title?: string }
export interface GroupUsage { day: string; speeches: number; calls: number; tokens: number; estimated: boolean; line: string }
export interface GroupLive { round: number; phase: 'speaking' | 'summary' | 'done'; speaker: string | null; replyTo: string | null; text: string; queue: string[]; minutes: boolean }
export interface Group {
  id: string; kind: 'group'; title: string; topic: string; members: GroupMember[]; host: GroupHost; mode: GroupMode; modeLabel: string;
  source: GroupSource | null; status: 'active' | 'ended'; round: number; createdAt: string; updatedAt: string; endedAt: string | null;
  live: GroupLive | null; usage: GroupUsage; cardsPending: number;
  memory: { decided: number; disputed: number; note: number };
  /** 钉在群顶的最近一次小结（一句话） */
  pinned: { round: number; line: string; message: number } | null;
}
export interface SummaryDecided { text: string; agreed: boolean }
export interface SummaryDisputed { point: string; sides: { who: string; name: string; says: string }[] }
export interface Summary { line: string; decided: SummaryDecided[]; disputed: SummaryDisputed[]; settled: string[]; notes: { who: string; name: string; text: string }[] }
export interface SummaryActions {
  writeback?: { kind: 'idea' | 'podcast'; id: string; at?: string; error?: string };
  meeting?: { id: string; title: string };
  podcast?: { id: string; title: string };
}
export interface GroupMsg {
  id: string; kind: 'user' | 'member' | 'system' | 'summary'; speaker: string | null; name: string; text: string; time: string; ts: string;
  status: string; round?: number | null; who?: GroupMember;
  replyTo?: { id: string; name: string }; at?: { id: string; name: string; deferred: boolean };
  event?: string; agents?: string[]; cards?: string[];
  /** 你发的附件（服务器给的 url 是相对的，withFiles 补成能直接打开的） */
  attachments?: Attachment[];
  summary?: Summary; actions?: SummaryActions; host?: { id: string; name: string; kind: 'main' | 'cyber' };
}
/** 录前讨论群（podprep.py）多给的：这一期、提纲、四格、「放进」的卡 */
export interface PrepEpisode {
  id: string; title: string; mode: string; modeLabel: string; outline: { text: string; done: boolean }[]; hostLine: string;
  steps: { current: number; labels: string[]; done: boolean }; booked: { at: string; label: string } | null; latest: string | null;
  prepGroup: string | null; participants: (GroupMember & { host: boolean })[]; waiting?: boolean;
}
export interface PrepOffer {
  id: string; episode: string; agent: string; agentName: string; cyber: string | null; cyberName: string; title: string; preview: string;
  health: boolean; status: 'offered' | 'put' | 'skipped' | 'used'; line: string; createdAt: string; usedAt: string | null;
}
/** 群里出的「仅你可见」的卡：收件箱条目的样子（InboxCard 画），多两样 */
export type GroupCard = Record<string, unknown> & { id: string; status: string; groupMessage: string | null; speaker: string; via: 'action' | 'loadout' };
export interface GroupFull { group: Group; messages: GroupMsg[]; cards: GroupCard[]; episode?: PrepEpisode; offers?: PrepOffer[] }
export interface MemoryItem { id: string; text: string; round: number; day: string; dayLabel: string; agreed: boolean; tag: string }
export interface MemoryDispute { id: string; point: string; round: number; day: string; dayLabel: string; sides: { who: string; name: string; says: string }[] }
export interface MemoryNote { id: string; who: string; name: string; text: string; round: number; day: string; dayLabel: string }
export interface GroupMemory {
  group: string; title: string; line: string; round: number | null; intro: string;
  decided: MemoryItem[]; disputed: MemoryDispute[]; notes: MemoryNote[]; counts: { decided: number; disputed: number; note: number };
}
/** 想法「拿去讨论」那一张 */
export interface IdeaDiscussion { kind: 'cyber' | 'group' | 'podcast' | 'agent'; id: string; title?: string; open?: boolean }
export interface IdeaOptions {
  idea: { id: string; text: string; title: string }; discussion: IdeaDiscussion | null;
  cybers: { id: string; name: string; tag: string; avatar: CyberAvatarSpec; last: string | null }[];
  agents: { id: string; name: string; icon: string; color: AgentColor | null; purpose: string; health: boolean }[];
  friends: { id: string; name: string }[]; episodes: { id: string; title: string; mode: string }[];
  can: { cyber: boolean; group: boolean; podcast: boolean; agent: boolean; friend: boolean };
}

const enc = encodeURIComponent;
type One = { group: Group };

/** 附件的地址补成带服务器和令牌的（<Image>、预览页直接用）。 */
export const withFiles = (m: GroupMsg): GroupMsg => (m.attachments?.length ? { ...m, attachments: m.attachments.map((a) => ({ ...a, url: fileUrl(a.url) })) } : m);

/** 先把选好的文件存成这个群的附件（/api/chat/upload，thread = 群的 id），回附件 id，发言时带上。 */
export async function uploadGroupFiles(id: string, files: PendingFile[]): Promise<string[]> {
  if (!files.length) return [];
  const fd = new FormData();
  fd.append('thread', id);
  for (const f of files) formFile(fd, 'files', f);
  const j = await xhrUpload(`${getBase()}/api/chat/upload`, fd);
  return (j.attachments as Attachment[]).map((a) => a.id);
}

/** 新建群聊、加成员时能选谁（health：管健康、饮食、睡眠的功能 Agent，不和赛博同群） */
export interface Candidates {
  cybers: (GroupMember & { about: string })[]; agents: (GroupMember & { about: string; health: boolean })[];
  host: { id: string; name: string }; min: number; max: number;
}

export const groupsApi = {
  list: (status: 'active' | 'ended' | 'all' = 'active') => request<{ groups: Group[] }>(`/api/groupchats?status=${status}`).then((j) => j.groups),
  candidates: () => request<Candidates>('/api/groupchats/candidates'),
  create: (b: { members: string[]; title?: string; topic?: string; host?: string; mode?: GroupMode; source?: GroupSource }) =>
    request<One>('/api/groupchats', { method: 'POST', body: b, timeoutMs: 60000 }).then((j) => j.group),
  get: (id: string) => request<GroupFull>(`/api/groupchats/${enc(id)}`).then((j) => ({ ...j, messages: j.messages.map(withFiles) })),
  send: (id: string, text: string, attachments: string[] = []) =>
    request<{ message: GroupMsg; how: string; group: Group }>(`/api/groupchats/${enc(id)}/send`, { method: 'POST', body: { text, attachments }, timeoutMs: 60000 })
      .then((j) => ({ ...j, message: withFiles(j.message) })),
  stop: (id: string) => request<{ stopped: boolean }>(`/api/groupchats/${enc(id)}/stop`, { method: 'POST', body: {} }),
  end: (id: string) => request<One>(`/api/groupchats/${enc(id)}/end`, { method: 'POST', body: {}, timeoutMs: 30000 }).then((j) => j.group),
  reopen: (id: string) => request<One>(`/api/groupchats/${enc(id)}/reopen`, { method: 'POST', body: {} }).then((j) => j.group),
  patch: (id: string, b: { title?: string; topic?: string; host?: string; mode?: GroupMode; members?: string[] }) =>
    request<One>(`/api/groupchats/${enc(id)}`, { method: 'PATCH', body: b }).then((j) => j.group),
  remove: (id: string) => request<{ ok: boolean }>(`/api/groupchats/${enc(id)}`, { method: 'DELETE' }),
  memory: (id: string) => request<GroupMemory>(`/api/groupchats/${enc(id)}/memory`),
  memoryAct: (id: string, item: string, action: 'agree' | 'unagree' | 'remove') =>
    request<GroupMemory>(`/api/groupchats/${enc(id)}/memory/${enc(item)}`, { method: 'POST', body: { action } }),
  /** 小结下的三个动作：写回想法 / 发起会议 / 做成播客 */
  writeback: (id: string, round: number) => request<{ message: GroupMsg }>(`/api/groupchats/${enc(id)}/summaries/${round}/writeback`, { method: 'POST', body: {} }),
  meeting: (id: string, round: number) => request<{ meeting: { id: string; title: string }; message: GroupMsg }>(`/api/groupchats/${enc(id)}/summaries/${round}/meeting`, { method: 'POST', body: {}, timeoutMs: 60000 }),
  podcast: (id: string, round: number) => request<{ episode: { id: string; title: string }; message: GroupMsg }>(`/api/groupchats/${enc(id)}/summaries/${round}/podcast`, { method: 'POST', body: {} }),
  // 录前讨论群（podprep.py）
  /** friends = 多人对谈拉的好友（好友 id）：有就是好友群（fg-…），你的管家兼主持 */
  discuss: (episode: string, b: { mode?: string; guests?: string[]; friends?: string[] }) => request<{ group: string }>(`/api/podcast/episodes/${enc(episode)}/discuss`, { method: 'POST', body: b, timeoutMs: 60000 }),
  outline: (episode: string, note = '') => request<{ message: unknown; reply: string }>(`/api/podcast/episodes/${enc(episode)}/outline`, { method: 'POST', body: { note }, timeoutMs: 120000 }),
  offer: (episode: string, agent: string) => request<{ offer: PrepOffer }>(`/api/podcast/episodes/${enc(episode)}/offers`, { method: 'POST', body: { agent } }),
  decideOffer: (offer: string, action: 'put' | 'skip') => request<{ offer: PrepOffer }>(`/api/podcast/offers/${enc(offer)}`, { method: 'POST', body: { action } }),
  book: (episode: string, bookedAt: string) => request(`/api/podcast/episodes/${enc(episode)}`, { method: 'PATCH', body: { bookedAt } }),
  // 想法「拿去讨论」（ideatalk.py）
  ideaOptions: (idea: string) => request<IdeaOptions>(`/api/think/ideas/${enc(idea)}/discuss`),
  ideaToCyber: (idea: string, cyber: string) => request<{ thread: string }>(`/api/think/ideas/${enc(idea)}/cyber`, { method: 'POST', body: { cyber } }),
  ideaToGroup: (idea: string, members: string[]) => request<{ group: Group }>(`/api/think/ideas/${enc(idea)}/group`, { method: 'POST', body: { members }, timeoutMs: 60000 }),
  ideaToPodcast: (idea: string, episode?: string) => request<{ episode: { id: string } }>(`/api/think/ideas/${enc(idea)}/podcast`, { method: 'POST', body: { episode } }),
  ideaToAgent: (idea: string, agent: string, note = '') => request<{ thread: string }>(`/api/think/ideas/${enc(idea)}/agent`, { method: 'POST', body: { agent, note } }),
  ideaPoints: (idea: string) => request<{ points: string[]; text: string }>(`/api/think/ideas/${enc(idea)}/points`, { method: 'POST', body: {}, timeoutMs: 60000 }),
};

/** 正在说的事件（服务器 meetings.attach，群聊借的同一套）：state 是接上时的现状，之后 speaker → delta… → said；有主持人的一轮最后 summary。 */
export type GroupEvent =
  | { event: 'state'; data: { live: GroupLive | null } }
  | { event: 'round'; data: { round: number; invited: string[]; full: boolean } }
  | { event: 'speaker'; data: { agent: string; name: string; replyTo: string | null; round: number } }
  | { event: 'delta'; data: { agent: string; text: string } }
  | { event: 'said' | 'system' | 'summary' | 'message'; data: GroupMsg }
  | { event: 'phase'; data: { phase: 'summary' } }
  | { event: 'done'; data: { round: number; status?: string | null } };

/** 接上正在说的，事件逐个交给 onEvent；说完（done）或没有在说的（204）就返回。stop() 断开。 */
export function followGroup(id: string, onEvent: (e: GroupEvent) => void): { done: Promise<void>; stop: () => void } {
  const ctl = new AbortController();
  const done = (async () => {
    const r = await expoFetch(`${getBase()}/api/groupchats/${enc(id)}/stream`, { headers: { Accept: 'text/event-stream', ...authHeaders() }, signal: ctl.signal });
    if (r.status === 204 || !r.ok || !r.body) return;
    const MSG = new Set(['said', 'system', 'summary', 'message']);
    await readSse(r.body as unknown as ReadableStream<Uint8Array>, (event, data) => onEvent({ event, data: MSG.has(event) ? withFiles(data as GroupMsg) : data } as GroupEvent));
  })().catch(() => {});
  return { done, stop: () => ctl.abort() };
}

// —— 开着吗 ——————————————————————————————————————————————————————————————————————————————
const TTL = 60_000;
let known: { on: boolean; at: number } | null = null;
let asking: Promise<boolean> | null = null;

/** 服务器的群聊开着吗：老服务器（不认 groupchat）= 关；接口 404 = server.json 没开。问一次记一分钟。 */
export function groupsOn(): Promise<boolean> {
  if (!supports('groupchat')) return Promise.resolve(false);
  if (known && Date.now() - known.at < TTL) return Promise.resolve(known.on);
  if (!asking) {
    asking = request('/api/groupchats?status=active')
      .then(() => true)
      .catch((e) => (httpStatus(e) === 404 ? false : (known?.on ?? false)))
      .then((on) => { known = { on, at: Date.now() }; asking = null; return on; });
  }
  return asking;
}

/** 群聊开着吗：没问过之前是 null（入口先不画），问到了 true / false。 */
export function useGroupsOn(): boolean | null {
  const [on, setOn] = useState<boolean | null>(() => (!supports('groupchat') ? false : known ? known.on : null));
  useEffect(() => {
    let live = true;
    groupsOn().then((v) => { if (live) setOn(v); }).catch(() => {});
    return () => { live = false; };
  }, []);
  return on;
}

/** 群聊里的 @名字（成员的名字，长的先认；「@所有 Agent」也认）：给气泡上色用。 */
export function splitAt(text: string, members: { id: string; name: string }[]): { text: string; who: string | null }[] {
  const names = [...members].sort((a, b) => b.name.length - a.name.length);
  const ALL = /^(所有\s*Agent|所有|All Agents|all|everyone)/i;
  const out: { text: string; who: string | null }[] = [];
  let i = 0;
  let buf = '';
  while (i < text.length) {
    const ch = text[i];
    if (ch === '@' || ch === '＠') {
      const rest = text.slice(i + 1);
      const gap = rest.length - rest.trimStart().length;
      const hit = names.find((m) => rest.slice(gap).startsWith(m.name));
      const all = hit ? null : ALL.exec(rest.slice(gap));
      if (hit || all) {
        if (buf) out.push({ text: buf, who: null });
        buf = '';
        const len = 1 + gap + (hit ? hit.name.length : all![0].length);
        out.push({ text: text.slice(i, i + len), who: hit ? hit.id : '*' });
        i += len;
        continue;
      }
    }
    buf += ch;
    i += 1;
  }
  if (buf) out.push({ text: buf, who: null });
  return out;
}
