// 推送通知：服务器（server/push.py）在回复好了、要你点头、卡片更新时推一条。这里负责：
// - 拿 Expo push token 交给服务器；
// - 前台收到的通知不出系统横幅，交给 store，由 app 自己在顶上滑下小窗（components/Banner.tsx）；
// - 通知上的按钮（收件箱：同意 / 看一下；卡片：看卡片）和点通知本身，交给 store 去跳转或同意；
// - app 图标上的数字。
// expo-notifications / expo-device 是原生模块，1.0.3 起才有；这里用 require 延迟加载并容错，同一份 JS 热更新到 1.0.2 的包上不会崩，只是没有推送。
import Constants from 'expo-constants';
import { Platform } from 'react-native';
import type { PushTarget } from '../data/types';
import { L } from '../lang';
import { request } from './base';

type NotificationsModule = typeof import('expo-notifications');
type Notif = import('expo-notifications').Notification;
type NotifResponse = import('expo-notifications').NotificationResponse;

/** 原生包版本：推送模块从 1.0.3 起才编进包里。热更新的 JS 可能跑在更老的包上，那时候连 require 都不做。 */
function nativeHasPush(): boolean {
  const v = Constants.nativeAppVersion ?? Constants.expoConfig?.version ?? '0';
  const [a = 0, b = 0, c = 0] = String(v).split('.').map((x: string) => parseInt(x, 10) || 0);
  return a > 1 || (a === 1 && (b > 0 || c >= 3));
}

let cached: NotificationsModule | null | undefined;
function notifications(): NotificationsModule | null {
  if (cached !== undefined) return cached;
  if (Platform.OS === 'web' || !nativeHasPush()) { cached = null; return null; }
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const mod: NotificationsModule = require('expo-notifications');
    // 前台收到的通知：不弹系统横幅、不进通知列表、不响、不改角标。app 自己滑下小窗（store 收到后决定弹不弹）。
    // 这个回调只管前台；后台 / 锁屏时照常由系统显示。
    mod.setNotificationHandler({
      handleNotification: async () => ({ shouldShowBanner: false, shouldShowList: false, shouldPlaySound: false, shouldSetBadge: false }),
    });
    cached = mod;
  } catch {
    cached = null;  // 这个原生包里没有 expo-notifications（1.0.2 及更早）
  }
  return cached;
}

function isDevice(): boolean {
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return !!(require('expo-device') as typeof import('expo-device')).isDevice;
  } catch {
    return true;
  }
}

let registered = false;

export async function registerPush(): Promise<string | null> {
  if (registered || Platform.OS === 'web') return null;
  const N = notifications();
  if (!N || !isDevice()) return null;
  const { status: have } = await N.getPermissionsAsync();
  let status = have;
  if (status !== 'granted') status = (await N.requestPermissionsAsync()).status;
  if (status !== 'granted') return null;
  const projectId = Constants.expoConfig?.extra?.eas?.projectId ?? Constants.easConfig?.projectId;
  const token = (await N.getExpoPushTokenAsync(projectId ? { projectId } : undefined)).data;
  await request('/api/push/register', { method: 'POST', body: { token, platform: Platform.OS } });
  registered = true;
  return token;
}

/**
 * 通知上的按钮。服务器发通知时用 categoryId 选一组：
 * - inbox（要你点头）：同意（要先解锁，直接替你点同意）/ 看一下
 * - card（卡片更新）：看卡片
 * 按钮文字跟界面语言走：启动时注册一次，切换语言（整棵树重挂）后再注册一次。
 */
export function registerCategories(): void {
  const N = notifications();
  if (!N) return;
  N.setNotificationCategoryAsync('inbox', [
    { identifier: 'approve', buttonTitle: L('同意', 'Approve'), options: { opensAppToForeground: true, isAuthenticationRequired: true } },
    { identifier: 'open', buttonTitle: L('看一下', 'View'), options: { opensAppToForeground: true } },
  ]).catch(() => {});
  N.setNotificationCategoryAsync('card', [
    { identifier: 'open', buttonTitle: L('看卡片', 'View card'), options: { opensAppToForeground: true } },
  ]).catch(() => {});
}

/** 一条通知里 app 用得到的部分。 */
export interface PushInfo {
  /** 通知本身的 id */
  id: string;
  title: string;
  subtitle: string;
  body: string;
  /** 点开去哪。老服务器只有 data.thread，也换成 target。 */
  target: PushTarget | null;
  category: string | null;
  /** ring = 值得打断（app 开着时弹小窗）；quiet = 只更新数据和未读。老服务器没有：当 ring。 */
  level: string | null;
  /** reply / card / inbox / done / report */
  kind: string | null;
}

function targetOf(data: Record<string, unknown> | null | undefined): PushTarget | null {
  let tg: unknown = data?.target;
  if (typeof tg === 'string') { try { tg = JSON.parse(tg); } catch { tg = null; } }
  if (tg && typeof tg === 'object') {
    const o = tg as Record<string, unknown>;
    const str = (k: string) => (typeof o[k] === 'string' && o[k] ? (o[k] as string) : undefined);
    const thread = str('thread');
    const id = str('id');
    if (o.type === 'thread' && thread) return { type: 'thread', thread };
    if (o.type === 'card' && id) return { type: 'card', id, thread };
    if (o.type === 'inbox' && id) return { type: 'inbox', id, thread };
    if (o.type === 'board' && str('agent')) return { type: 'board', agent: str('agent') as string, thread };
    if (o.type === 'friend' && id) return { type: 'friend', id };
    if (o.type === 'today') return { type: 'today' };
  }
  const th = data?.thread;
  if (typeof th === 'string' && th) return th === 'today' ? { type: 'today' } : { type: 'thread', thread: th };
  return null;
}

function infoOf(n: Notif): PushInfo {
  const c = n.request.content;
  const str = (v: unknown) => (typeof v === 'string' && v ? v : null);
  return {
    id: n.request.identifier, title: c.title ?? '', subtitle: c.subtitle ?? '', body: c.body ?? '', target: targetOf(c.data), category: c.categoryIdentifier ?? null,
    level: str(c.data?.level), kind: str(c.data?.kind),
  };
}

/** app 开着的时候收到一条通知（系统不显示，见上面的 handler）。 */
export function onPushReceived(cb: (p: PushInfo) => void): () => void {
  const N = notifications();
  if (!N) return () => {};
  const sub = N.addNotificationReceivedListener((n) => cb(infoOf(n)));
  return () => sub.remove();
}

export type PushAction = 'approve' | 'open';

// 处理过的「通知 id + 按钮」。切换语言会把 store 整个重挂，重挂时又会读一遍「上一次点的通知」；
// 冷启动时同一下点击也可能既进监听、又在「上一次」里。靠它每一下只处理一次（模块级，重挂不丢）。
const handled = new Set<string>();

function clearLast(N: NotificationsModule) {
  try { N.clearLastNotificationResponse(); } catch { /* 老原生包没有：靠 handled 去重 */ }
}

/** 点了通知或通知上的按钮。冷启动时把打开 app 的那一下也补上。 */
export function onPushResponse(cb: (action: PushAction, p: PushInfo) => void): () => void {
  const N = notifications();
  if (!N) return () => {};
  const deliver = (resp: NotifResponse | null | undefined) => {
    if (!resp) return;
    const key = `${resp.notification.request.identifier}|${resp.actionIdentifier}`;
    if (handled.has(key)) return;
    handled.add(key);
    clearLast(N);
    const a = resp.actionIdentifier;
    const action: PushAction | null = a === 'approve' ? 'approve' : a === 'open' || a === N.DEFAULT_ACTION_IDENTIFIER ? 'open' : null;
    if (action) cb(action, infoOf(resp.notification));
  };
  const sub = N.addNotificationResponseReceivedListener(deliver);
  try {
    deliver(N.getLastNotificationResponse());
  } catch {
    N.getLastNotificationResponseAsync().then(deliver).catch(() => {});
  }
  return () => sub.remove();
}

let lastBadge = -1;
/** app 图标上的数字 = 等你点头的 + 你发了消息、还没看的回复（服务器算好的 badge）。只在原生 app 里。 */
export function setAppBadge(n: number): void {
  const N = notifications();
  if (!N) return;
  const v = Math.max(0, Math.floor(n) || 0);
  if (v === lastBadge) return;
  lastBadge = v;
  N.setBadgeCountAsync(v).catch(() => { lastBadge = -1; });
}
