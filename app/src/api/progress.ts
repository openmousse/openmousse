// 回复进行中「在想什么、做到哪一步」（服务器 SSE 的 progress 事件，只在 Gateway 的 WebSocket 对话通道上有，见 ../../server/README.md）。
// 每个对话一份快照，回复结束就清掉；不进 store：它一秒变好几次，只有对话页底下那一块要跟着重画。
import { useSyncExternalStore } from 'react';

export interface ReplyStep {
  id: string;
  /** OpenClaw 的工具名（exec / read / web_search …），app 按它配动词 */
  tool: string;
  /** 模型自己写的这一步的标题（读文件只有文件名） */
  detail: string;
  status: 'running' | 'done' | 'failed';
}

export interface ReplyProgress {
  /** 这一轮开始的 Unix 秒（服务器的时钟） */
  since: number;
  /** preparing_workspace / preparing_context / starting_model / thinking / tool … */
  phase: string;
  steps: ReplyStep[];
  /** 最近一段思考摘要（模型写的，常是英文） */
  thought: string;
}

const byThread = new Map<string, ReplyProgress>();
const subs = new Set<() => void>();

export function setReplyProgress(thread: string, p: ReplyProgress | null) {
  if (p) byThread.set(thread, p);
  else if (!byThread.delete(thread)) return;
  for (const f of subs) f();
}

const subscribe = (f: () => void) => { subs.add(f); return () => { subs.delete(f); }; };

export function useReplyProgress(thread: string): ReplyProgress | null {
  return useSyncExternalStore(subscribe, () => byThread.get(thread) ?? null, () => null);
}
