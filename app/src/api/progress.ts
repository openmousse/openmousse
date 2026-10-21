// 回复进行中「在想什么、做到哪一步」（服务器 SSE 的 progress 事件：Gateway 的 WebSocket 对话通道和自己的内核上有，见 ../../server/README.md）。
// 每个对话一份快照，回复结束就清掉；不进 store：它一秒变好几次，只有对话页底下那一块要跟着重画。
import { useSyncExternalStore } from 'react';

export interface ReplyStep {
  id: string;
  /** 工具名：OpenClaw 的（exec / read / web_search …，app 按它配动词）或自己内核的（schedule、tree、memory_search …） */
  tool: string;
  /** 这一步的标题（OpenClaw：模型写的，读文件只有文件名；自己的内核：Tool.title） */
  detail: string;
  status: 'running' | 'done' | 'failed';
  /** 自己的内核（10/20 起，server/engine/steps.py）：这一步说人话（「查看日程」）、进行时（「正在查看日程」）；老服务器、OpenClaw 没有 */
  label?: string;
  doing?: string;
  /** 和 label 一起来：label 里没有的（文件名、域名、技能名），没有是空串；不放参数原文 */
  note?: string;
  /** 交给你点头了（收件箱卡在等）：后面写「等你确认」 */
  wait?: boolean;
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

/** 回复进行中备用链换了模型（自己的内核的 model_switch，10/20）：from / to 是 provider/model，reason 见 server/engine/fallback.py。
 *  同样一个对话一份、这条流结束就清（回完了看那条回复的 fallbackFrom / fallbackReason）；自己一组订阅，进度一秒变几次不连带它重画。 */
export interface ModelSwitch { from: string; to: string; reason: string }

const switches = new Map<string, ModelSwitch>();
const switchSubs = new Set<() => void>();

export function setModelSwitch(thread: string, sw: ModelSwitch | null) {
  if (sw) switches.set(thread, sw);
  else if (!switches.delete(thread)) return;
  for (const f of switchSubs) f();
}

const subscribeSwitch = (f: () => void) => { switchSubs.add(f); return () => { switchSubs.delete(f); }; };

export function useModelSwitch(thread: string): ModelSwitch | null {
  return useSyncExternalStore(subscribeSwitch, () => switches.get(thread) ?? null, () => null);
}
