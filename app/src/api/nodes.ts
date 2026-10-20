// 设备节点（第 8 批，server/nodes.py，2026-10-19）：主人这一边（列表、某一项开到哪一档、直接批准、移除、给电脑出配对码）
// 和手机自己当节点那一边（hello / 长轮询等活 / 回结果 / 切到后台时 bye）。服务器没开节点（engine.nodes）时这些接口都是 404。
import { authHeaders, getBase, HttpError, request } from './base';

export type NodeLevel = 'off' | 'ask' | 'allow';

export interface NodeCommand {
  name: string;
  label: string;
  /** 节点报了这一样 */
  declared: boolean;
  approved: boolean;
  /** 隐私的、撤不回的：批了也默认关 */
  private: boolean;
  level: NodeLevel;
  /** 最多能开到哪一档（操作电脑只能 ask） */
  maxLevel: NodeLevel;
}

export interface NodeInfo {
  id: string;
  kind: 'host' | 'phone';
  label: string;
  platform: string;
  version: string;
  online: boolean;
  away: string | null;
  foreground: boolean | null;
  seenAt: string | null;
  createdAt: string;
  commands: NodeCommand[];
  pending: { card: string; commands: string[] } | null;
  permissions: Record<string, boolean>;
  folders: { name: string; write?: boolean }[];
  /** 服务器这一边跑命令的放行名单 */
  exec: string[];
}

export interface NodeInvoke { id: string; command: string; params: Record<string, unknown>; timeoutMs: number }

export interface NodeHello {
  id: string;
  kind: 'host' | 'phone';
  label: string;
  approved: string[];
  usable: string[];
  levels: Record<string, NodeLevel>;
  ignored: string[];
  poll: { wait: number };
}

export interface NodePair { code: string; expires: string; link: string; command: string; mousse: string }

const enc = encodeURIComponent;

export const nodesApi = {
  list: () => request<{ nodes: NodeInfo[]; agents: string[] }>('/api/nodes'),
  get: (id: string) => request<{ node: NodeInfo }>(`/api/nodes/${enc(id)}`),
  allow: (id: string, command: string, level: NodeLevel) =>
    request<{ node: NodeInfo }>(`/api/nodes/${enc(id)}/allow`, { method: 'POST', body: { command, level } }),
  /** commands 不给 = 它报了、还没批的全部 */
  approve: (id: string, commands?: string[]) =>
    request<{ node: NodeInfo }>(`/api/nodes/${enc(id)}/approve`, { method: 'POST', body: commands ? { commands } : {} }),
  remove: (id: string) => request<{ ok: boolean }>(`/api/nodes/${enc(id)}`, { method: 'DELETE' }),
  /** server：这台手机连服务器用的地址（配对链接里写它）。 */
  pair: (server: string, name?: string) => request<NodePair>('/api/nodes/pair', { method: 'POST', body: { server, name } }),
  hello: (body: { platform: string; name: string; version: string; commands: string[]; foreground: boolean; allow?: Record<string, NodeLevel> }) =>
    request<NodeHello>('/api/nodes/hello', { method: 'POST', body, timeoutMs: 15_000 }),
  result: (id: string, body: { ok: true; payload: unknown } | { ok: false; error: { code: string; message: string } }) =>
    request<{ ok: boolean }>('/api/nodes/result', { method: 'POST', body: { id, ...body }, timeoutMs: 60_000 }),
  bye: (reason: 'background' | 'shutdown') => request<{ ok: boolean }>('/api/nodes/bye', { method: 'POST', body: { reason }, timeoutMs: 5_000 }),
};

/** 长轮询等活：服务器最多等 wait 秒；外面的 signal 断了（切到后台、关掉）就马上停。→ 交来的活、叫停的活。 */
export async function pollNodes(wait: number, signal: AbortSignal): Promise<{ invokes: NodeInvoke[]; cancel: string[] }> {
  const ctl = new AbortController();
  const stop = () => ctl.abort();
  signal.addEventListener('abort', stop);
  const timer = setTimeout(stop, (wait + 20) * 1000);  // 网断了、请求挂住：别一直等
  try {
    const r = await fetch(`${getBase()}/api/nodes/poll?wait=${wait}`, { signal: ctl.signal, headers: { Accept: 'application/json', ...authHeaders() } });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new HttpError((typeof j.detail === 'string' && j.detail) || j.error || `HTTP ${r.status}`, r.status, j);
    return { invokes: Array.isArray(j.invokes) ? j.invokes : [], cancel: Array.isArray(j.cancel) ? j.cancel : [] };
  } finally {
    clearTimeout(timer);
    signal.removeEventListener('abort', stop);
  }
}
