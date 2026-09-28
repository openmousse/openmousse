// app 和三个原生扩展（分享、小组件、通知）之间的接线，加上实时活动。原生部分在 modules/mousse-native（1.0.5 起才有）；
// 网页、Android、没有这个模块的旧包上这里的函数什么都不做。
// - shareConfig：服务器地址、令牌、名字、语言写进 App Group，扩展才知道往哪传、说哪种语言
// - flushShareOutbox：分享扩展当时没连上服务器、先存在手机上的，app 打开时补传
// - refreshWidget：取 /api/widget 写成小组件的快照并刷新小组件（小组件自己也会去取，这份是它连不上时用的）
// - live*：实时活动（锁屏 + 灵动岛）。服务器的 /api/live 说现在该有哪些，app 在前台时照着开、改、关；
//   令牌（push-to-start 和每个活动的）交给服务器，服务器配了 APNs 密钥以后，app 没开也能开、能改。
import { Platform } from 'react-native';
import { mousseNative, type EditMenuEvent } from '../../modules/mousse-native';
import { agentName } from '../brand';
import type { PendingFile } from '../data/types';
import { lang } from '../lang';
import { addFragment, addSave, uploadFragment, uploadSaves } from './think';
import { authHeaders, getBase, getToken, httpStatus, request } from './base';

export const nativeReady = () => !!mousseNative();

// —— 给扩展的共享设置 ——

export function shareConfig(): void {
  const m = mousseNative();
  if (!m || !getBase()) return;
  try {
    m.setShared(JSON.stringify({ serverUrl: getBase(), token: getToken(), appName: agentName(), lang: lang() }));
  } catch { /* 写不了就算了：扩展会提示打开 app 连一次 */ }
}

// —— 分享扩展没传上去的 ——

interface OutboxItem {
  id: string;
  dest: 'saves' | 'think';
  note?: string;
  text?: string;
  title?: string;
  url?: string;
  createdAt?: number;
  files?: { uri: string; name: string; mime: string }[];
}

let flushing = false;

/** 补传；返回传上去了几条。服务器明确拒收（4xx，比如文件太大）的丢掉，连不上的留着下次再来。 */
export async function flushShareOutbox(): Promise<number> {
  const m = mousseNative();
  if (!m || flushing || !getBase()) return 0;
  let items: OutboxItem[] = [];
  try { items = JSON.parse(m.outbox()) as OutboxItem[]; } catch { return 0; }
  if (!items.length) return 0;
  flushing = true;
  let done = 0;
  try {
    for (const it of items) {
      try {
        await uploadShared(it);
        m.removeOutbox(it.id);
        done += 1;
      } catch (e) {
        const st = httpStatus(e);
        if (st && st >= 400 && st < 500 && st !== 401 && st !== 408 && st !== 429) m.removeOutbox(it.id);
      }
    }
  } finally {
    flushing = false;
  }
  return done;
}

async function uploadShared(it: OutboxItem): Promise<void> {
  const files: PendingFile[] = (it.files ?? []).map((f) => ({ uri: f.uri, name: f.name, mime: f.mime || 'application/octet-stream', size: 0 }));
  const note = (it.note ?? '').trim();
  const text = (it.text ?? '').trim();
  const url = (it.url ?? '').trim();
  if (it.dest === 'saves') {
    if (files.length) {
      await uploadSaves(files, { note: [note, text, url].filter(Boolean).join('\n'), source: shareSource() });
    } else {
      await addSave({ url: url || undefined, text: text || (url ? undefined : note), title: it.title || undefined, note });
    }
    return;
  }
  const body = [note, text].filter(Boolean).join('\n\n');
  if (files.length) {
    await uploadFragment(files, { text: [body, url && !body.includes(url) ? url : ''].filter(Boolean).join('\n') || undefined, title: it.title || undefined });
  } else {
    const clean = url ? body.replace(url, '').trim() : body;
    await addFragment({ kind: url ? (clean ? 'text' : 'link') : 'text', text: clean, url: url || undefined, title: it.title || undefined });
  }
}

const shareSource = () => (lang() === 'zh' ? '分享' : 'Share');

// —— 小组件 ——

let widgetAt = 0;

/** 取 /api/widget 写成快照、刷新小组件。force 以外 5 分钟最多一次（小组件每天能刷的次数有限）。 */
export async function refreshWidget(force = false): Promise<void> {
  const m = mousseNative();
  if (!m || !getBase()) return;
  const now = Date.now();
  if (!force && now - widgetAt < 5 * 60_000) return;
  widgetAt = now;
  try {
    const r = await fetch(`${getBase()}/api/widget`, { headers: { Accept: 'application/json', ...authHeaders() } });
    if (!r.ok) return;
    m.writeWidgetSnapshot(await r.text());
  } catch { /* 连不上：小组件接着用上一份 */ }
}

// —— 实时活动 ——

export interface LiveItem {
  key: string;
  kind: string;
  /** 画在锁屏上的内容（字段见 modules/mousse-native/ios/LiveActivities.swift 的 ContentState） */
  state: { title: string; subtitle?: string; icon?: string; accent?: string; startAt?: number; endAt?: number; progress?: number; lines?: string[]; done?: boolean };
  /** 过了这个时刻系统把它画成过期的样子（Unix 秒） */
  staleAt?: number | null;
}

let liveSubs: { remove(): void }[] = [];

/** 开始把实时活动的令牌交给服务器（app 启动连上以后调一次）。 */
export function startLive(): void {
  const m = mousseNative();
  if (!m || liveSubs.length) return;
  liveSubs = [
    m.addListener('onLiveToken', (e) => {
      request('/api/live/token', { method: 'POST', body: { type: e.type, token: e.token, key: e.key ?? null, id: e.id ?? null, platform: Platform.OS } }).catch(() => {});
    }),
    m.addListener('onLiveState', (e) => {
      // 在锁屏上被划掉了：告诉服务器，这一轮别再开
      if (e.state === 'dismissed') request('/api/live/dismissed', { method: 'POST', body: { key: e.key } }).catch(() => {});
    }),
  ];
  try { m.liveObserve(); } catch { /* 系统不支持实时活动 */ }
}

let syncing = false;

/** 照服务器的 /api/live 对一遍：该有的开上（或改成最新的），服务器那边已经没有的关掉。app 在前台时才能新开。 */
export async function syncLive(): Promise<void> {
  const m = mousseNative();
  if (!m || syncing || !getBase()) return;
  let supported = false;
  try { supported = m.liveSupported(); } catch { supported = false; }
  if (!supported) return;
  syncing = true;
  try {
    let want: LiveItem[];
    try {
      want = (await request<{ items: LiveItem[] }>('/api/live')).items ?? [];
    } catch (e) {
      if (httpStatus(e) === 404) return;  // 老服务器没有实时活动
      throw e;
    }
    const keys = new Set(want.map((w) => w.key));
    for (const w of want) {
      await m.liveStart(w.key, w.kind, JSON.stringify(w.state), w.staleAt ?? null).catch(() => {});
    }
    let have: { key: string; state: string }[] = [];
    try { have = JSON.parse(m.liveList()); } catch { have = []; }
    for (const h of have) {
      if (!keys.has(h.key) && (h.state === 'active' || h.state === 'stale')) await m.liveEnd(h.key, null).catch(() => {});
    }
  } catch { /* 连不上：下次再对 */ } finally {
    syncing = false;
  }
}

// —— 输入框长按菜单 ——

export interface MenuItem { id: string; title: string; icon?: string }

/**
 * 给某个输入框（testID = key）的系统长按菜单加几项。返回取消的函数。
 * onPick 收到点了哪一项；handled = 原生已经做了（比如换行已经插进去了）。没有原生模块时返回 null，调用方自己兜底。
 */
export function editMenu(key: string, items: MenuItem[], onPick: (e: EditMenuEvent) => void): (() => void) | null {
  const m = mousseNative();
  if (!m) return null;
  try {
    m.setEditMenu(key, JSON.stringify(items));
  } catch {
    return null;
  }
  const sub = m.addListener('onEditMenu', (e) => { if (e.key === key) onPick(e); });
  return () => {
    sub.remove();
    try { m.setEditMenu(key, '[]'); } catch { /* 模块没了就算了 */ }
  };
}
