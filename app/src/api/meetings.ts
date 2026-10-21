// Agent 会议（server/meetings.py，2026-10-13）：几个 Agent 围绕一个议题轮流发言，助手主持、写纪要；确认后待办写进日程和各 Agent 的看板。
// 线程 grp-<id>：每条消息带发言人（speaker），系统行（「由助手发起」「助手请 X、Y 回答」）和纪要是助手（main）。
// 一轮进行中用 GET /api/meetings/{id}/stream 接上（SSE，按发言人分段）；服务器有 supports('meetings') 才显示入口。
import { fetch as expoFetch } from 'expo/fetch';
import type { AgentColor } from '../data/types';
import { authHeaders, getBase, request } from './base';
import { readSse } from './client';
import type { CyberAvatarSpec } from './cybers';

export type MeetingStatus = 'running' | 'pending' | 'ended' | 'group';
export interface MeetingMember {
  id: string; name: string; icon: string | null; color: AgentColor | null; /** 这个 Agent 已经删了 */ gone: boolean;
  /** 主线第三步：会上也可以有赛博（只出看法、不领待办），带它的形象和标签；老服务器没有 kind = 功能 Agent */
  kind?: 'agent' | 'cyber'; avatar?: CyberAvatarSpec | null; tag?: string;
}
/** 纪要里的一条待办：负责的 Agent + 时间（dates 是具体日期，重复的有几天；start 为空 = 只有截止日） */
export interface MeetingTodo {
  agent: string; agentName: string; icon: string | null; color: AgentColor | null;
  text: string; title: string; dates: string[]; start: string | null; end: string | null;
}
export interface MeetingMinutes { round: number; decisions: string[]; todos: MeetingTodo[]; next: string[]; open: string[]; at: string; messageId?: string }
export interface MeetingConfirmed { at: string; round: number; todos: number; schedule: number; board: Record<string, number>; failed: { text: string; error: string }[] }
/** 正在进行的一轮：谁在说、说到哪、后面还有谁；phase = minutes 时在写纪要 */
export interface MeetingLive { round: number; phase: 'speaking' | 'minutes' | 'done'; speaker: string | null; replyTo: string | null; text: string; queue: string[]; minutes: boolean }
export interface MeetingLast { id: string; speaker: string | null; name: string | null; text: string; time: string; ts: string; kind: string }
export interface Meeting {
  id: string;
  /** group = 会后保留下来的 Agent 群 */
  kind: 'meeting' | 'group';
  title: string; topic: string; host: string; hostName: string;
  members: MeetingMember[];
  status: MeetingStatus; statusLabel: string; keep: boolean; round: number;
  /** manual 在 app 里发起；main 主对话发起（会议卡在 fromThread 那条回复 messageId 下面） */
  origin: 'manual' | 'main'; fromThread: string | null; messageId: string | null;
  minutes: MeetingMinutes | null; confirmed: MeetingConfirmed | null;
  /** 待确认：这一轮的纪要还没确认（对话列表的行上标出来） */
  needsYou: boolean;
  /** 主线第三步：「定了告诉我」（全自动跑完，主对话里留结果）；listed = false：对话列表里不单列，在会议记录里 */
  auto?: boolean; listed?: boolean;
  createdAt: string; updatedAt: string; endedAt: string | null;
  live: MeetingLive | null;
  /** 只在列表里有：最后一条（谁说的、说了什么） */
  last?: MeetingLast;
}
export interface MeetingMsg {
  id: string;
  kind: 'user' | 'agent' | 'system' | 'minutes';
  speaker: string | null; name: string; text: string; time: string; ts: string; status: string; round?: number | null;
  /** 这一条是在回答谁的 @ */
  replyTo?: { id: string; name: string };
  /** 这一条 @ 了谁；deferred = 那位这一轮已经说过了，问题留到下一轮 */
  at?: { id: string; name: string; deferred: boolean };
  /** 系统行是哪种事件（start / invite / mention / stopped / confirmed / group / ended …） */
  event?: string; agents?: string[];
  minutes?: MeetingMinutes;
}

/** 会议记录（主对话资料页 →「会议记录」，主线第三步）：谁发起的、结果怎样、一句话；搜的时候带一段 match */
export interface MeetingRecord {
  id: string; title: string; topic: string; origin: 'manual' | 'main'; auto: boolean; listed: boolean; status: MeetingStatus;
  initiator: string; outcome: string; line: string; day: string; createdAt: string; decisions: number; todos: number; match?: string;
  members: MeetingMember[];
}
/** all 全部 / main 助手发起 / auto 定了告诉我 / mine 你发起 */
export type RecordFilter = 'all' | 'main' | 'auto' | 'mine';

export interface MeetingOptions {
  members: (MeetingMember & { kind: 'agent' | 'cyber'; purpose: string; health: boolean })[];
  /** byOrigin：meetings.by_origin 开着（才有「定了告诉我」）；records：资料页画「会议记录」（开关开着，或者有开着时收走的会；老服务器没有） */
  keep: boolean; byOrigin: boolean; records?: boolean; min: number; max: number;
}

const enc = encodeURIComponent;
type One = { meeting: Meeting };

export const meetingsApi = {
  /** active = 进行中 / 待确认 / 群聊；ended = 已结束的会议 */
  list: (status: 'active' | 'ended' | 'all' = 'active') => request<{ meetings: Meeting[]; endedCount: number }>(`/api/meetings?status=${status}`),
  get: (id: string) => request<{ meeting: Meeting; messages: MeetingMsg[] }>(`/api/meetings/${enc(id)}`),
  /** keep 不给 = 服务器的默认（meetings.by_origin 开着时你发起的默认留着）；auto =「定了告诉我」 */
  create: (b: { topic: string; members: string[]; title?: string; keep?: boolean; auto?: boolean }) => request<One>('/api/meetings', { method: 'POST', body: b }).then((j) => j.meeting),
  /** 发起会议那一页：能请谁（功能 Agent 在前；health 的不和赛博同会）、「会后保留为群聊」的默认值（主线第三步；老服务器 404） */
  options: () => request<MeetingOptions>('/api/meetings/options'),
  records: (filter: RecordFilter = 'all', q = '') => request<{ records: MeetingRecord[]; intro: string }>(`/api/meetings/records?filter=${filter}&q=${enc(q)}`),
  /** 助手按议题推荐人选和会议名（不带工具的模型；用不了时 agents 为空） */
  suggest: (topic: string) => request<{ agents: string[]; title: string }>('/api/meetings/suggest', { method: 'POST', body: { topic }, timeoutMs: 60000 }),
  /** 补充一句：待确认 / 群聊时开下一轮，进行中只记下 */
  send: (id: string, text: string) => request<{ message: MeetingMsg; meeting: Meeting }>(`/api/meetings/${enc(id)}/send`, { method: 'POST', body: { text }, timeoutMs: 60000 }),
  next: (id: string) => request<One>(`/api/meetings/${enc(id)}/continue`, { method: 'POST', body: {}, timeoutMs: 60000 }).then((j) => j.meeting),
  confirm: (id: string) => request<One & { confirmed?: MeetingConfirmed }>(`/api/meetings/${enc(id)}/confirm`, { method: 'POST', body: {}, timeoutMs: 90000 }),
  stop: (id: string) => request<{ stopped: boolean }>(`/api/meetings/${enc(id)}/stop`, { method: 'POST', body: {} }),
  end: (id: string) => request<One>(`/api/meetings/${enc(id)}/end`, { method: 'POST', body: {}, timeoutMs: 30000 }).then((j) => j.meeting),
  patch: (id: string, b: { keep?: boolean; title?: string }) => request<One>(`/api/meetings/${enc(id)}`, { method: 'PATCH', body: b }).then((j) => j.meeting),
  remove: (id: string) => request<{ ok: boolean }>(`/api/meetings/${enc(id)}`, { method: 'DELETE' }),
};

/** 一轮进行中的事件（服务器 meetings.attach）：state 是接上时的现状，之后按发言人分段 speaker → delta… → said，最后 done。 */
export type MeetingEvent =
  | { event: 'state'; data: { live: MeetingLive | null } }
  | { event: 'round'; data: { round: number; invited: string[]; minutes: boolean } }
  | { event: 'speaker'; data: { agent: string; name: string; replyTo: string | null; round: number } }
  | { event: 'delta'; data: { agent: string; text: string } }
  | { event: 'said' | 'system' | 'minutes' | 'message'; data: MeetingMsg }
  | { event: 'phase'; data: { phase: 'minutes' } }
  | { event: 'status'; data: { status: MeetingStatus } }
  | { event: 'done'; data: { round: number; status?: MeetingStatus | null } };

/** 接上正在进行的一轮，事件逐个交给 onEvent；这一轮结束（done）或没有在进行的（204）就返回。stop() 断开。 */
export function followMeeting(id: string, onEvent: (e: MeetingEvent) => void): { done: Promise<void>; stop: () => void } {
  const ctl = new AbortController();
  const done = (async () => {
    const r = await expoFetch(`${getBase()}/api/meetings/${enc(id)}/stream`, { headers: { Accept: 'text/event-stream', ...authHeaders() }, signal: ctl.signal });
    if (r.status === 204 || !r.ok || !r.body) return;
    await readSse(r.body as unknown as ReadableStream<Uint8Array>, (event, data) => onEvent({ event, data } as MeetingEvent));
  })().catch(() => {});
  return { done, stop: () => ctl.abort() };
}

/** 文字里的 @名字（成员的名字，长的先认）：给气泡上色用，返回 [文字段, 被 @ 的成员 id | null] 交替的几段。 */
export function splitMentions(text: string, members: MeetingMember[]): { text: string; agent: string | null }[] {
  const names = [...members].sort((a, b) => b.name.length - a.name.length);
  const out: { text: string; agent: string | null }[] = [];
  let i = 0;
  let buf = '';
  while (i < text.length) {
    const ch = text[i];
    if (ch === '@' || ch === '＠') {
      const rest = text.slice(i + 1);
      const space = rest.length - rest.trimStart().length;
      const hit = names.find((m) => rest.slice(space).startsWith(m.name));
      if (hit) {
        if (buf) out.push({ text: buf, agent: null });
        buf = '';
        const len = 1 + space + hit.name.length;
        out.push({ text: text.slice(i, i + len), agent: hit.id });
        i += len;
        continue;
      }
    }
    buf += ch;
    i += 1;
  }
  if (buf) out.push({ text: buf, agent: null });
  return out;
}
