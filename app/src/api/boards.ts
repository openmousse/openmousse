// 积木看板（server/boards.py）：每个 Agent 自己的表 + 看板配置。服务器按配置算好每块的数据、按语言格式化好，这里只管显示和你自己点的改动。
import { request } from './base';

export type BlockType = 'stat' | 'progress' | 'chart' | 'list' | 'checklist' | 'text' | 'action';
export type Tone = 'neutral' | 'good' | 'warn' | 'bad' | 'cyan' | 'gold';
export type FieldType = 'text' | 'number' | 'money' | 'date' | 'datetime' | 'bool' | 'choice' | 'photo' | 'link';

export interface FieldDef { key: string; label: string; type: FieldType; unit?: string; currency?: string; options?: string[]; required?: boolean }
export interface Collection { name: string; title: string; fields: FieldDef[]; status: 'active' | 'draft'; count?: number }

export interface BoardRow {
  id: string; title: string; sub?: string; right?: string; badge?: { text: string; tone: Tone };
  data: Record<string, unknown>; display: Record<string, string>; checked?: boolean;
}
export interface StatItem { label: string; value: number | null; text: string; sub?: string; delta?: number; deltaText?: string; tone?: Tone }
export interface ChartPoint { key: string; value: number | null; current: boolean; text: string }
export interface BoardAction {
  kind: 'ask' | 'upload' | 'form'; label: string; message?: string; collection?: string;
  accept?: ('camera' | 'photos' | 'files')[]; primary?: boolean; defaults?: Record<string, unknown>;
}

/** 每块算好的数据；出错只有 error（只影响这一块）。 */
export interface BlockData {
  error?: string;
  items?: StatItem[];                                            // stat
  value?: number | null; target?: number | null; ratio?: number | null; targetText?: string; leftText?: string | null; over?: boolean; // progress
  text?: string; updatedAt?: string | null;                     // progress / text
  points?: ChartPoint[]; by?: 'day' | 'week' | 'month'; level?: boolean; summary?: string; avgText?: string | null; maxText?: string | null; // chart（level = 体重这类水平值，不是流量）
  rows?: BoardRow[]; total?: number; collection?: string; fields?: FieldDef[]; groups?: { key: string; count: number }[]; // list / checklist
  forms?: Record<string, FieldDef[]>;                           // action（form 按钮的字段）
}

export interface Block {
  id: string; type: BlockType | string; title: string; after?: string; hidden?: boolean; caption?: string; empty?: string; pack?: string;
  limit?: number; group?: string; style?: string; label?: string; edit?: boolean; check?: string; chart?: 'bar' | 'line';
  rowActions?: { label: string; set: Record<string, unknown> }[]; actions?: BoardAction[];
  data: BlockData;
}

export interface BoardStrip { version: number; note: string; by: string; added: string[]; undoTo: number }
/** 内置看板的一节（健身的「现在」、饮食的「下一餐」……）：按服务器给的顺序排，能挪、能藏，内容是 app 自己画的。 */
export interface BoardSection { id: string; title: string; hidden: boolean }
export interface Board {
  agent: string; dashboard: string; anchors: string[]; version: number; status: string; by: string | null; note: string; updatedAt: string | null;
  blocks: Block[]; collections: Collection[]; strip: BoardStrip | null;
  sections?: BoardSection[];               // 老服务器没有：按默认顺序、都不藏
  changed?: string[]; removed?: string[];   // 提案预览：相对它照着写的那一版，哪些块是新的或改过的、哪些去掉了
  plan?: boolean; sample?: boolean;         // 建 Agent 的方案预览（Agent 还没建）：sample = 按示例行画的
}

/** Agent 的提醒：到点查表，有东西就推一条（新推送，同意了才开）。 */
export interface BoardAlert {
  id: string; key: string; agent: string; title: string; status: 'draft' | 'live' | 'paused' | 'rejected' | 'deleted' | 'superseded';
  when: string; level: 'quiet' | 'ring'; levelText: string; preview: string | null; count: number; pack: string | null;
  lastSent: string | null; lastBody: string | null; inboxId: string | null;
}

/** 功能包：几张表 + 一组积木 + 用法，装到一个 Agent 上。 */
export interface Pack {
  name: string; version: number; title: string; summary: string; for: string[]; installedOn: string[];
  tables: { name: string; title: string }[]; blocks: { id: string; type: string; title: string }[]; alerts: { id: string; title: string }[];
}

export interface BoardVersion {
  version: number; status: 'live' | 'old' | 'draft' | 'rejected'; note: string; by: string; inboxId: string | null; basedOn: number | null; createdAt: string;
  blocks: { id: string; type: string; title: string }[];
  hiddenSections?: string[];   // 这一版藏着的内置小节（名字）
}
export interface TableRow { id: string; data: Record<string, unknown>; display: Record<string, string>; createdAt: string; deletedAt: string | null }

const enc = encodeURIComponent;
/** 存回去的配置不带算好的数据。 */
export const configOf = (blocks: Block[]) => blocks.map(({ data: _data, ...rest }) => rest);

export const boardsApi = {
  get: (agent: string) => request<Board & { ok: true }>(`/api/boards/${enc(agent)}`),
  proposal: (inboxId: string) => request<Board & { ok: true }>(`/api/boards/proposal/${enc(inboxId)}`),
  revert: (agent: string, version: number) => request<{ ok: true; version: number }>(`/api/boards/${enc(agent)}/revert`, { method: 'POST', body: { version } }),
  ack: (agent: string) => request<{ ok: true }>(`/api/boards/${enc(agent)}/ack`, { method: 'POST', body: {} }),
  /** 你自己在 app 里挪、藏、删：整份配置换成这一版（by user，不出撤回条）。sections 不给 = 内置小节照现在的。 */
  put: (agent: string, blocks: Block[], note: string, sections?: { id: string; hidden?: boolean }[]) =>
    request<{ ok: true; version: number }>(`/api/boards/${enc(agent)}`, {
      method: 'PUT', body: { blocks: configOf(blocks), note, mode: 'apply', by: 'user', ...(sections ? { sections: sections.map((x) => ({ id: x.id, hidden: !!x.hidden })) } : {}) },
    }),
  /** 建 Agent 的方案卡（Agent 还没建）：按方案画出来的看板。 */
  plan: (inboxId: string) => request<Board & { ok: true }>(`/api/boards/plan/${enc(inboxId)}`),
  alerts: (agent: string) => request<{ ok: true; alerts: BoardAlert[] }>(`/api/alerts/${enc(agent)}`),
  alertProposal: (inboxId: string) => request<BoardAlert & { ok: true }>(`/api/alerts/proposal/${enc(inboxId)}`),
  setAlert: (id: string, status: 'live' | 'paused' | 'deleted') => request<{ ok: true }>(`/api/alerts/item/${enc(id)}`, { method: 'POST', body: { status } }),
  packs: () => request<{ ok: true; packs: Pack[] }>('/api/packs'),
  /** 你在 app 里点「装上」：直接装（表、积木；包里的提醒另外出卡）。 */
  installPack: (name: string, agent: string) =>
    request<{ ok: true; version: number | null; changes: string[]; alertCards?: string[] }>(`/api/packs/${enc(name)}/install`, { method: 'POST', body: { agent, mode: 'apply' } }),
  history: (agent: string) => request<{ ok: true; versions: BoardVersion[] }>(`/api/boards/${enc(agent)}/history`),
  collections: (agent: string) => request<{ ok: true; collections: Collection[] }>(`/api/collections/${enc(agent)}`),
  rows: (agent: string, name: string, deleted = false) =>
    request<{ ok: true; total: number; rows: TableRow[]; collection: Collection }>(`/api/collections/${enc(agent)}/${enc(name)}/rows?limit=200${deleted ? '&deleted=1' : ''}`),
  /** 改一行：data 是要改的字段（rowActions 的 "-1" / "today" 这类写法由服务器换算）。 */
  patchRow: (rid: string, data: Record<string, unknown>) => request<{ ok: true }>(`/api/rows/${enc(rid)}`, { method: 'PATCH', body: { data, by: 'user' } }),
  deleteRow: (rid: string) => request<{ ok: true }>(`/api/rows/${enc(rid)}`, { method: 'DELETE' }),
  restoreRow: (rid: string) => request<{ ok: true }>(`/api/rows/${enc(rid)}/restore`, { method: 'POST', body: {} }),
  addRow: (agent: string, collection: string, row: Record<string, unknown>) =>
    request<{ ok: true; ids: string[] }>(`/api/collections/${enc(agent)}/${enc(collection)}/rows`, { method: 'POST', body: { rows: [row], by: 'user' } }),
};
