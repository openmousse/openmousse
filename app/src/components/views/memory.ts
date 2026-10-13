// 每个集合块上次看的是哪个视图：本机记住（和草稿一样，iOS 用 React Native 自带的 Settings，网页用 localStorage）。
import { Platform, Settings } from 'react-native';

const KEY = 'mousse.views';
let picked: Record<string, string> = {};
try {
  const raw = Platform.OS === 'ios' ? Settings.get(KEY) : Platform.OS === 'web' ? globalThis.localStorage?.getItem(KEY) : null;
  const j: unknown = typeof raw === 'string' ? JSON.parse(raw) : null;
  if (j && typeof j === 'object') picked = j as Record<string, string>;
} catch {
  picked = {};
}

export const pickedView = (agent: string, block: string) => picked[`${agent}:${block}`];

export function pickView(agent: string, block: string, view: string) {
  picked = { ...picked, [`${agent}:${block}`]: view };
  const value = JSON.stringify(picked);
  try {
    if (Platform.OS === 'ios') Settings.set({ [KEY]: value });
    else if (Platform.OS === 'web') globalThis.localStorage?.setItem(KEY, value);
  } catch {
    // 存不上就只在这次打开里记得
  }
}
