// 我的 claw（2026-09-29）：这台设备连过的服务器（每台背后是一个 claw），设置页「我的 claw」列出来，点「换到这台」切换。
// 地址、名字这些放一个列表（mousse.claws），每台的令牌单独存一项（mousse.claw.<id>，iOS 钥匙串 / 浏览器 localStorage），
// 正在用的那台照旧写在 mousse.server / mousse.token（base.ts 的 saveServerConfig）：别处的代码不用知道有好几台。
// 网页版和服务同源，只有「这一台」，不存列表。
import { Platform } from 'react-native';
import { acceptLanguage } from '../lang';
import { getBase, getToken, normalizeBase, readItem, saveServerConfig, writeItem } from './base';

const KEY_LIST = 'mousse.claws';
const tokenKey = (id: string) => `mousse.claw.${id}`;

export interface Claw {
  id: string;
  /** 服务器地址（normalizeBase 过的） */
  base: string;
  /** 助手的名字（/api/health 的 app_name），列表里显示它 */
  name: string;
  /** openclaw / hermes / …（/api/health 的 claw.kind）和给人看的名字 */
  clawKind?: string;
  clawName?: string;
  addedAt: string;
  lastOkAt?: string;
}

let cache: Claw[] | null = null;

const newId = () => Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);

async function readList(): Promise<Claw[]> {
  if (cache) return cache;
  let list: Claw[] = [];
  try {
    const raw = await readItem(KEY_LIST);
    const v: unknown = raw ? JSON.parse(raw) : [];
    if (Array.isArray(v)) list = v.filter((c): c is Claw => !!c && typeof c.id === 'string' && typeof c.base === 'string');
  } catch {
    list = [];
  }
  cache = list;
  return list;
}

async function writeList(list: Claw[]): Promise<void> {
  cache = list;
  await writeItem(KEY_LIST, JSON.stringify(list));
}

/** 这台设备连过的 claw。以前只存一台：第一次读时把现在连着的那台放进列表。 */
export async function listClaws(fallbackName?: string): Promise<Claw[]> {
  if (Platform.OS === 'web') return [];
  const list = await readList();
  const base = getBase();
  if (!base || list.some((c) => c.base === base)) return list;
  const c: Claw = { id: newId(), base, name: fallbackName || 'OpenMousse', addedAt: new Date().toISOString() };
  await writeItem(tokenKey(c.id), getToken());
  const next = [...list, c];
  await writeList(next);
  return next;
}

/** 正在用的那台：地址和 base.ts 现在的地址对得上的那一项。 */
export const activeClawId = (list: Claw[]): string | null => list.find((c) => c.base === getBase())?.id ?? null;

/** 连上了一台（配对或填令牌成功）：记进列表；同一个地址就更新名字和令牌。 */
export async function rememberClaw(info: { base: string; token: string; name?: string; clawKind?: string; clawName?: string }): Promise<Claw | null> {
  if (Platform.OS === 'web') return null;
  const base = normalizeBase(info.base);
  if (!base) return null;
  const list = await readList();
  const now = new Date().toISOString();
  const old = list.find((c) => c.base === base);
  const c: Claw = old
    ? { ...old, name: info.name || old.name, clawKind: info.clawKind ?? old.clawKind, clawName: info.clawName ?? old.clawName, lastOkAt: now }
    : { id: newId(), base, name: info.name || 'OpenMousse', clawKind: info.clawKind, clawName: info.clawName, addedAt: now, lastOkAt: now };
  await writeItem(tokenKey(c.id), info.token.trim());
  await writeList(old ? list.map((x) => (x.id === c.id ? c : x)) : [...list, c]);
  return c;
}

/** 连着的时候顺手更新这一台的名字和 claw 种类（设置页打开时调）。没变就不写。 */
export async function touchClaw(base: string, info: { name?: string; clawKind?: string; clawName?: string }): Promise<void> {
  if (Platform.OS === 'web') return;
  const list = await readList();
  const c = list.find((x) => x.base === base);
  if (!c) return;
  const next = { ...c, name: info.name || c.name, clawKind: info.clawKind ?? c.clawKind, clawName: info.clawName ?? c.clawName };
  if (next.name === c.name && next.clawKind === c.clawKind && next.clawName === c.clawName) return;
  await writeList(list.map((x) => (x.id === c.id ? { ...next, lastOkAt: new Date().toISOString() } : x)));
}

/** 换到另一台：把它的地址和令牌写成当前的（之后由 store 的 refreshLive 从头连）。 */
export async function switchToClaw(id: string): Promise<Claw | null> {
  const c = (await readList()).find((x) => x.id === id);
  if (!c) return null;
  await saveServerConfig(c.base, await readItem(tokenKey(id)));
  return c;
}

/** 从这台设备上拿掉一台（删它的令牌，claw 那边什么都不动）。拿掉的是正在用的：换到剩下的第一台；一台都不剩就清空（回到连接页）。
 *  → 换到的那台，没换就 null。 */
export async function forgetClaw(id: string): Promise<Claw | null> {
  const list = await readList();
  const c = list.find((x) => x.id === id);
  const rest = list.filter((x) => x.id !== id);
  await writeItem(tokenKey(id), '');
  await writeList(rest);
  if (!c || c.base !== getBase()) return null;
  const next = rest[0];
  if (!next) {
    await saveServerConfig('', '');
    return null;
  }
  await saveServerConfig(next.base, await readItem(tokenKey(next.id)));
  return next;
}

/** 看一眼某一台在不在（给不是当前的那几台打点）：5 秒超时，不改任何配置。 */
export async function pingClaw(c: Claw): Promise<'ok' | 'auth' | 'down'> {
  const token = await readItem(tokenKey(c.id));
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 5000);
  try {
    const r = await fetch(`${c.base}/api/health`, {
      signal: ctl.signal,
      headers: { Accept: 'application/json', 'Accept-Language': acceptLanguage(), ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    });
    if (r.status === 401) return 'auth';
    return r.ok ? 'ok' : 'down';
  } catch {
    return 'down';
  } finally {
    clearTimeout(timer);
  }
}
