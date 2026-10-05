// 回复进行中已经出来的字（流式），每个对话一份。不进 store：一秒变几十次，store 一变全 app 读 store 的组件都要重画
// （2026-10-05 量过：每来一段字 400–700 个组件，打字跟着卡），这里只有显示着这个对话的那一块跟着重画。
// 回复结束时用 quiet 清掉、不通知：同一刻 store 把这条回复加进消息列表，两件事落在同一次重画里，
// 不会先闪一下「在想」再出消息，也不会新消息和还没清的流式文字叠着出现一帧。
import { useSyncExternalStore } from 'react';

const byThread = new Map<string, string>();
const subs = new Set<() => void>();

/** text = null：这一轮完了。quiet：不通知（紧接着 store 会更新，跟着那次一起重画） */
export function setPartial(thread: string, text: string | null, quiet = false) {
  if (text === null) { if (!byThread.delete(thread)) return; }
  else if (byThread.get(thread) === text) return;
  else byThread.set(thread, text);
  if (!quiet) for (const f of subs) f();
}

const subscribe = (f: () => void) => { subs.add(f); return () => { subs.delete(f); }; };

/** 这个对话正在出的字：还没出字是 ''，没有在回复是 undefined */
export function usePartial(thread: string): string | undefined {
  return useSyncExternalStore(subscribe, () => byThread.get(thread), () => undefined);
}
