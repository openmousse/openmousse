// 连接（server/connectors.py）：助手接着的每一样东西现在怎么样。服务器缓存 60 秒；fresh = 跳过缓存重新查（下拉刷新）。
import type { Connector, ConnectorsInfo, ConnectorsState } from '../data/types';
import { httpStatus, request } from './base';

const STATUS: Connector['status'][] = ['ok', 'warn', 'off'];

function norm(j: Partial<ConnectorsInfo>): ConnectorsInfo {
  const str = (v: unknown) => (typeof v === 'string' ? v : '');
  const groups = (Array.isArray(j.groups) ? j.groups : []).filter((g) => g && typeof g.id === 'string').map((g) => ({
    id: g.id, title: str(g.title),
    items: (Array.isArray(g.items) ? g.items : []).filter((x) => x && typeof x.id === 'string').map((x): Connector => ({
      id: x.id, name: str(x.name) || x.id, icon: str(x.icon), status: STATUS.includes(x.status) ? x.status : 'warn', line: str(x.line),
      facts: Array.isArray(x.facts) ? x.facts.filter((f) => f && f.label != null).map((f) => ({ label: String(f.label), value: String(f.value ?? '') })) : [],
      uses: str(x.uses), fix: typeof x.fix === 'string' && x.fix ? x.fix : null,
      open: x.open && typeof x.open.screen === 'string' ? { screen: x.open.screen, label: str(x.open.label) } : null,
    })),
  })).filter((g) => g.items.length);
  const counts = { ok: 0, warn: 0, off: 0 };
  for (const g of groups) for (const x of g.items) counts[x.status] += 1;
  return { groups, counts, checkedAt: str(j.checkedAt) };
}

export async function load(fresh = false): Promise<ConnectorsState> {
  try {
    return { kind: 'ok', data: norm(await request<Partial<ConnectorsInfo>>(`/api/connectors${fresh ? '?fresh=1' : ''}`, { timeoutMs: 60000 })) };
  } catch (e) {
    const s = httpStatus(e);
    if (s === 404 || s === 405) return { kind: 'unsupported' };
    throw e;
  }
}
