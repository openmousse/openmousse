// 推送通知：回复在服务器上生成完就会推一条（server/push.py）。这里负责拿 Expo push token 交给服务器、前台时压掉通知、点通知跳到对话。
// expo-notifications / expo-device 是原生模块，1.0.3 起才有；这里用 require 延迟加载并容错，同一份 JS 热更新到 1.0.2 的包上不会崩，只是没有推送。
import Constants from 'expo-constants';
import { AppState, Platform } from 'react-native';
import { request } from './base';

type NotificationsModule = typeof import('expo-notifications');

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
    // 前台时不弹横幅（回复已经在流式显示了）；后台 / 锁屏时正常弹。
    mod.setNotificationHandler({
      handleNotification: async () => {
        const active = AppState.currentState === 'active';
        return { shouldShowBanner: !active, shouldShowList: true, shouldPlaySound: !active, shouldSetBadge: false };
      },
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

const threadOf = (n: { request: { content: { data?: Record<string, unknown> | null } } } | null | undefined): string | null => {
  const th = n?.request.content.data?.thread;
  return typeof th === 'string' ? th : null;
};

/** 点通知：回调对话 id。冷启动时也把最后一次点的补上。没有推送模块时什么都不做。 */
export function onNotificationOpen(cb: (thread: string) => void): () => void {
  if (Platform.OS === 'web') return () => {};
  const N = notifications();
  if (!N) return () => {};
  const sub = N.addNotificationResponseReceivedListener((resp) => { const th = threadOf(resp.notification); if (th) cb(th); });
  N.getLastNotificationResponseAsync().then((resp) => { const th = threadOf(resp?.notification); if (th) cb(th); }).catch(() => {});
  return () => sub.remove();
}
