// 赛博、通讯录、一句话新建、赛博的模型（主线第三步「Agents 和群聊」，2026-10-21）。
// 服务器：../../server/cybers.py（赛博、记忆、改法、装载）、contacts.py（通讯录）、agentnew.py（一句话新建）、cybermodels.py（选模型）。
// 都挂在服务器的 cybers 开关后面：服务器没有这一项（supports('cybers') 为假），或者 server.json 没开（接口回 404），app 就不显示入口，
// 一切照旧（新建 Agent 走原来的表单）。useCybersOn() 判断这两样。
import { useEffect, useState } from 'react';
import type { PendingFile } from '../data/types';
import { getBase, httpStatus, request } from './base';
import { formFile, xhrUpload } from './client';
import { supports } from './version';

// —— 形象 ——————————————————————————————————————————————————————————————————————————
export type Machine = 'timer' | 'sides' | 'rewind';
export interface AvatarParts {
  hair: 'bun' | 'short' | 'buzz' | 'bob' | 'long' | 'pony' | 'curly' | 'bald';
  hairColor: 'black' | 'dark' | 'chestnut' | 'flax' | 'grey' | 'wine';
  beard: 'none' | 'stubble' | 'mustache' | 'full';
  collar: 'shirt' | 'v' | 'round' | 'turtle';
  clothes: 'cyan' | 'camel' | 'purple' | 'green' | 'rose' | 'rust';
  skin: 'stone' | 'warm' | 'wheat' | 'tan' | 'deep';
  bg: 'cyan' | 'gold' | 'purple' | 'green' | 'pink' | 'orange';
  accColor: 'black' | 'gold' | 'silver' | 'cyan';
  acc: { glasses: boolean; phones: boolean; earring: boolean; hat: boolean };
}
/** 捏的部件、上传的图（附件 id，只存在你的服务器上）、或者机器类的图标 */
export type CyberAvatarSpec = AvatarParts | { upload: string } | { machine: Machine; bg?: AvatarParts['bg'] };
export type CyberKind = 'pro' | 'friend' | 'machine' | 'blank';

// —— 一位赛博 ——————————————————————————————————————————————————————————————————————————
export interface Version {
  id: number; field: string; label: string; old: unknown; new: unknown; via: string; change: string | null; at: string; undone: boolean;
  /** 从卡上来的改法：那张收件箱卡的 id（对话里那张卡的回执上「撤销」认它） */
  inbox?: string | null;
}
export interface Cyber {
  id: string; thread: string; no: number; name: string; tag: string; kind: CyberKind; template: string | null; avatar: CyberAvatarSpec;
  identity: string; tone: string; rules: string; voice: string | null;
  /** 它自己选的模型（null = 跟默认）；modelInUse = 实际用的 */
  model: string | null; modelInUse: string | null; modelName: string;
  loadout: string[]; daily: boolean; builtin: string | null; createdAt: string; updatedAt: string; days: number; deleted: boolean;
  last?: { id: string; role: string; text: string; ts: string } | null;
  /** 只有取一位（资料页）时有：记下几条、知识库几份、还在等你点头的改法几个、最近一次改动（一天以内，「刚改 · 撤销」） */
  notes?: number; knowledge?: number; pending?: number; recent?: Version | null;
}
export interface Change {
  id: string; cyber: string; field: string; label: string; old: string; new: string; via: string; why: string; status: string;
  inbox: string | null; createdAt: string; decidedAt: string | null; version: number | null;
}
export interface Note { id: string; text: string; source: string; createdAt: string }
export interface Knowledge { id: string; title: string; origin: string; source: string; createdAt: string; chars: number; excerpt: string; text?: string }
export interface Memory {
  card: { label: string; text: string }[];
  notes: { count: number; items: Note[] };
  knowledge: { count: number; items: Knowledge[] };
  /** 「健康、饮食、睡眠不给赛博」 */
  rule: string;
}
export interface LoadAgent { id: string; name: string; icon: string; color: string | null; purpose: string; loaded: boolean; health: boolean; why?: string }
export interface Loadout { loaded: LoadAgent[]; available: LoadAgent[]; daily: boolean; max: number }
export type Tweak = 'stricter' | 'gentler' | 'shorter' | 'english' | 'examples';
export type ChangeField = 'identity' | 'tone' | 'rules' | 'tag' | 'voice' | 'note';

// —— 模型 ——————————————————————————————————————————————————————————————————————————————
export interface ModelRow {
  ref: string; name: string; vendor: string; providerName: string; open: boolean; local: boolean; billing: 'metered' | 'local' | 'plan';
  cost: 'low' | 'mid' | 'high' | null; usable: boolean; allowed: boolean; use: string; note?: string; current: boolean; inUse: boolean; reason?: string;
}
export interface ModelChannel { id: 'openrouter' | 'local' | 'keys' | 'chatgpt'; provider: string | null; name: string; line: string; connected: boolean; allowed?: boolean }
export interface CyberModels {
  cyber: string | null; class: CyberKind; current: string | null; inUse: string | null; inUseName: string;
  recommended: ModelRow[]; all: ModelRow[]; local: ModelRow[]; channels: ModelChannel[];
}

// —— 通讯录 ————————————————————————————————————————————————————————————————————————————
export interface ContactCyber { kind: 'cyber'; id: string; thread: string; name: string; tag: string; model: string | null; modelName: string; avatar: CyberAvatarSpec; class: CyberKind; no: number; line: string; at: string | null; keywords: string }
export interface ContactAgent { kind: 'agent'; id: string; name: string; icon: string; color: string | null; purpose: string; line: string; keywords: string }
export interface ContactFriend { kind: 'friend'; id: string; name: string; tier: string; line: string; at: string | null; butler: { name: string; line: string } | null; keywords: string }
/** 顶上那一行：没开管家是名片 Agent（open CardAgent）；开了是管家（open Butler，带形象：{orbit: true} = 环与点） */
export interface ContactButler { kind: 'butler'; id: string; name: string; line: string; available: boolean; open: string; keywords: string; avatar?: { orbit: true } | CyberAvatarSpec }
export type ContactFilter = 'all' | 'cyber' | 'agent' | 'friend';
export interface Contacts {
  butler: ContactButler | null;
  sections: ({ kind: 'cyber'; title: string; count: number; items: ContactCyber[] } | { kind: 'agent'; title: string; count: number; items: ContactAgent[] }
    | { kind: 'friend'; title: string; count: number; items: ContactFriend[] })[];
  filters: { id: ContactFilter; label: string; count: number }[];
}

// —— 一句话新建 ——————————————————————————————————————————————————————————————————————————
export interface Template { id: string; class: Exclude<CyberKind, 'blank'>; name: string; tag: string; line: string; identity: string; tone: string; rules: string; avatar: CyberAvatarSpec }
export interface Templates { groups: { class: Exclude<CyberKind, 'blank'>; title: string; line: string; templates: Template[] }[]; blank: { id: 'blank'; name: string; line: string } }
export interface Question { id: string; text: string; options: string[] }
export interface Answer { q: string; a: string }
export interface CyberDraft {
  kind: 'cyber'; class: CyberKind; template: string | null; name: string; tag: string; identity: string; tone: string; rules: string;
  avatar: CyberAvatarSpec; presets: CyberAvatarSpec[]; model: string | null; modelName: string; modelReason: string; cost: ModelRow['cost'];
  loadout: { id: string; name: string; icon: string; color: string | null; why: string }[]; agents: LoadAgent[]; quote: string; fallback: boolean;
}
export interface TableField { key: string; label: string; type: string; options?: string[]; unit?: string }
export interface AgentDraft {
  kind: 'agent'; name: string; icon: string; color: string | null; purpose: string; quote: string;
  table: { name: string; title: string; fields: TableField[] }; board: { kind: string; title: string }[];
  reminders: { name: string; when: string; task: string; describe: string }[];
  /** 「它会做的」三行：看板、表、提醒 */
  does: string[]; model: string; modelName: string; fallback: boolean;
}
export interface Trial { line: string; identity: string; tone: string; rules: string; tweak: Tweak | null; model: string; modelName: string }

export interface CyberIn {
  name: string; tag?: string; kind?: CyberKind; template?: string | null; avatar?: CyberAvatarSpec; identity?: string; tone?: string; rules?: string;
  voice?: string | null; model?: string | null; loadout?: string[];
}

const p = (cid: string) => `/api/cybers/${encodeURIComponent(cid)}`;

export const cybersApi = {
  list: () => request<{ cybers: Cyber[] }>('/api/cybers').then((j) => j.cybers),
  get: (cid: string) => request<{ cyber: Cyber }>(p(cid)).then((j) => j.cyber),
  create: (body: CyberIn) => request<{ cyber: Cyber }>('/api/cybers', { method: 'POST', body }).then((j) => j.cyber),
  /** 只带要改的；model / voice 给 null = 回到默认 */
  patch: (cid: string, body: Partial<Omit<CyberIn, 'kind' | 'template' | 'loadout'>>) =>
    request<{ cyber: Cyber; changed: string[] }>(p(cid), { method: 'PATCH', body }),
  remove: (cid: string) => request(p(cid), { method: 'DELETE' }),
  memory: (cid: string) => request<Memory>(`${p(cid)}/memory`),
  notes: (cid: string) => request<{ notes: Note[]; count: number }>(`${p(cid)}/notes`),
  deleteNote: (cid: string, nid: string) => request(`${p(cid)}/notes/${encodeURIComponent(nid)}`, { method: 'DELETE' }),
  clearNotes: (cid: string) => request<{ deleted: number }>(`${p(cid)}/notes`, { method: 'DELETE' }),
  knowledge: (cid: string) => request<{ items: Knowledge[]; count: number }>(`${p(cid)}/knowledge`),
  addKnowledge: (cid: string, body: { title?: string; text?: string; file?: string }) => request<{ item: Knowledge }>(`${p(cid)}/knowledge`, { method: 'POST', body }),
  deleteKnowledge: (cid: string, kid: string) => request(`${p(cid)}/knowledge/${encodeURIComponent(kid)}`, { method: 'DELETE' }),
  /** 等你点头的改法 + 最近的版本 */
  changes: (cid: string) => request<{ pending: Change[]; versions: Version[] }>(`${p(cid)}/changes`),
  /** 资料页的改法：先给改好的样子（不写进去）。value = 自己写好的；tweak = 五个改法之一；ask = 一句话；只给 ask = 由模型挑改哪几项 */
  propose: (cid: string, body: { field?: ChangeField; value?: string; tweak?: Tweak; ask?: string; why?: string }) =>
    request<{ changes: Change[] }>(`${p(cid)}/changes`, { method: 'POST', body, timeoutMs: 90000 }).then((j) => j.changes),
  decide: (cid: string, change: string, action: 'apply' | 'drop') =>
    request<{ change?: Change; version?: Version }>(`${p(cid)}/changes/${encodeURIComponent(change)}`, { method: 'POST', body: { action } }),
  undo: (cid: string, version?: number) => request<{ version: Version; cyber: Cyber }>(`${p(cid)}/undo`, { method: 'POST', body: version === undefined ? {} : { version } }),
  loadout: (cid: string) => request<Loadout>(`${p(cid)}/loadout`),
  load: (cid: string, agent: string) => request<Loadout>(`${p(cid)}/loadout`, { method: 'POST', body: { agent } }),
  unload: (cid: string, agent: string) => request<Loadout>(`${p(cid)}/loadout/${encodeURIComponent(agent)}`, { method: 'DELETE' }),
  daily: (cid: string, on: boolean) => request<Loadout>(`${p(cid)}/daily`, { method: 'POST', body: { on } }),
  /** 选模型的页：给一位（cyber）或者按一类（新建时还没有这位）；probe = 现探一次本机 */
  models: (q: { cyber?: string; kind?: CyberKind; probe?: boolean }) => {
    const qs = new URLSearchParams();
    if (q.cyber) qs.set('cyber', q.cyber);
    if (q.kind) qs.set('class', q.kind);
    if (q.probe) qs.set('probe', '1');
    return request<CyberModels>(`/api/cyber-models?${qs.toString()}`);
  },
  contacts: (q = '', filter: ContactFilter = 'all') => request<Contacts>(`/api/contacts?${new URLSearchParams({ q, filter }).toString()}`),
  templates: () => request<Templates>('/api/new/templates'),
  questions: (body: { kind: 'cyber' | 'agent'; text: string; template?: string | null }) =>
    request<{ questions: Question[]; fallback: boolean }>('/api/new/questions', { method: 'POST', body, timeoutMs: 90000 }),
  cyberDraft: (body: { text: string; template?: string | null; answers?: Answer[] }) =>
    request<{ draft: CyberDraft }>('/api/new/draft', { method: 'POST', body: { kind: 'cyber', ...body }, timeoutMs: 90000 }).then((j) => j.draft),
  agentDraft: (body: { text: string; answers?: Answer[] }) =>
    request<{ draft: AgentDraft }>('/api/new/draft', { method: 'POST', body: { kind: 'agent', ...body }, timeoutMs: 90000 }).then((j) => j.draft),
  trial: (body: { name: string; identity: string; tone: string; rules: string; model?: string | null; tweak?: Tweak; previous?: string[] }) =>
    request<Trial>('/api/new/trial', { method: 'POST', body, timeoutMs: 90000 }),
  createAgent: (body: { name: string; icon: string; color?: string | null; purpose: string; model?: string; table?: AgentDraft['table']; board?: AgentDraft['board']; reminders?: AgentDraft['reminders'] }) =>
    request<{ id: string; blocks: number; reminders: { job: string; inbox: string; name: string; when: string }[]; problems: string[] }>('/api/new/agent', { method: 'POST', body, timeoutMs: 90000 }),
};

/** 上传的头像：先存成这台服务器上的一个图片附件（/api/chat/upload，放在 cyber-avatars 下），回附件 id，形象写成 {upload: id}。 */
export async function uploadAvatar(f: PendingFile): Promise<string> {
  const fd = new FormData();
  fd.append('thread', 'cyber-avatars');
  formFile(fd, 'files', f);
  const j = await xhrUpload(`${getBase()}/api/chat/upload`, fd);
  const a = (j.attachments ?? [])[0] as { id: string; kind?: string } | undefined;
  if (!a?.id) throw new Error('upload');
  return a.id;
}

// —— 开没开 ————————————————————————————————————————————————————————————————————————————
// 服务器有这一项还不够：server.json 的 cybers 没开时接口一律 404。问一次记一分钟（换了服务器、刚开了开关，过一会儿就跟上）。
let known: { on: boolean; at: number } | null = null;
let asking: Promise<boolean> | null = null;
const TTL = 60_000;

export function cybersOn(): Promise<boolean> {
  if (!supports('cybers')) return Promise.resolve(false);
  if (known && Date.now() - known.at < TTL) return Promise.resolve(known.on);
  if (!asking) {
    asking = request('/api/new/templates')
      .then(() => true)
      .catch((e) => (httpStatus(e) === 404 ? false : (known?.on ?? false)))
      .then((on) => { known = { on, at: Date.now() }; asking = null; return on; });
  }
  return asking;
}

/** 这台服务器上赛博开着吗：没问过之前是 null（入口先不画），问到了 true / false。 */
export function useCybersOn(): boolean | null {
  const [on, setOn] = useState<boolean | null>(() => (!supports('cybers') ? false : known ? known.on : null));
  useEffect(() => {
    let live = true;
    cybersOn().then((v) => { if (live) setOn(v); }).catch(() => {});
    return () => { live = false; };
  }, []);
  return on;
}

/** 新建 / 删除以后别等一分钟：下次再问。 */
export const forgetCybersOn = () => { known = null; };
