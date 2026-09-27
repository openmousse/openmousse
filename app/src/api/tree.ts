// 世界树（server/memtree.py）：各 AI 平台共用的记忆，枝和叶子；确认 / 忘记 / 挪到别的枝。
// 服务器没接世界树（missing_source=tree）或者版本太老（404）时不算出错，页面显示对应的空状态。
import type { TreeAction, TreeBranch, TreeInfo, TreeLeaf, TreeState } from '../data/types';
import { httpStatus, request } from './base';

const KINDS: TreeLeaf['kind'][] = ['fact', 'preference', 'decision', 'event'];

/** 服务器在另一边同时开发：字段缺了也别让界面崩。 */
function norm(j: Partial<TreeInfo>): TreeInfo {
  const str = (v: unknown) => (typeof v === 'string' ? v : '');
  const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : 0);
  const branches: TreeBranch[] = (Array.isArray(j.branches) ? j.branches : []).filter((b) => b && typeof b.name === 'string').map((b) => ({
    name: b.name, parent: str(b.parent), about: str(b.about), depth: num(b.depth) || 1, leaves: num(b.leaves), total: num(b.total),
    agents: Array.isArray(b.agents) ? b.agents.filter((a) => a && typeof a.id === 'string').map((a) => ({ id: a.id, name: str(a.name) || a.id })) : [],
  }));
  const trunk = { name: str(j.trunk?.name) || '档案', count: num(j.trunk?.count) };
  const leaves: TreeLeaf[] = (Array.isArray(j.leaves) ? j.leaves : []).filter((l) => l && typeof l.id === 'string').map((l) => ({
    id: l.id, text: str(l.text), kind: KINDS.includes(l.kind) ? l.kind : 'fact',
    source: str(l.source), sourceName: str(l.sourceName) || str(l.source), origin: str(l.origin) || str(l.source),
    originName: str(l.originName) || str(l.sourceName) || str(l.source), agent: typeof l.agent === 'string' ? l.agent : null,
    status: l.status === 'pending' ? 'pending' : 'active', branch: str(l.branch) || trunk.name,
    tags: Array.isArray(l.tags) ? l.tags.map(String) : [], observedAt: str(l.observedAt), createdAt: str(l.createdAt),
  }));
  const bySource = Array.isArray(j.counts?.bySource) ? j.counts.bySource.filter((s) => s && typeof s.source === 'string') : [];
  return {
    trunk, branches, leaves,
    counts: { total: leaves.length, pending: leaves.filter((l) => l.status === 'pending').length, bySource },
    issues: num(j.issues),
  };
}

export async function load(): Promise<TreeState> {
  try {
    return { kind: 'ok', data: norm(await request<Partial<TreeInfo>>('/api/tree')) };
  } catch (e) {
    const data = (e as { data?: { missing_source?: unknown; hint?: unknown } } | null)?.data;
    if (data && data.missing_source === 'tree') return { kind: 'missing', hint: typeof data.hint === 'string' ? data.hint : '' };
    const s = httpStatus(e);
    if (s === 404 || s === 405) return { kind: 'unsupported' };
    throw e;
  }
}

/** 确认（待确认 → 当前）/ 忘记（笔记掏空、挪进归档）/ 挪到别的枝（branch 写枝名，主干写档案）。 */
export const act = (id: string, action: TreeAction, branch?: string) =>
  request<{ ok: boolean; changed?: boolean; branch?: string }>(`/api/tree/${encodeURIComponent(id)}`, { method: 'POST', body: branch ? { action, branch } : { action } });
