// 对讲（第 8 批，2026-10-19）：进对讲页先问服务器语音转写配好了没有（GET /api/chat/transcribe：只看有没有钥匙，不转写、不花钱）。
// 服务器报了 'talk' 才有这个接口；转写本身和语音输入一样走 POST /api/chat/transcribe（store 的 transcribe）。
import { request } from './base';

export interface TranscribeReady { ready: boolean; why: string | null }

export async function transcribeReady(): Promise<TranscribeReady> {
  const r = await request<{ ready?: boolean; why?: string | null }>('/api/chat/transcribe');
  return { ready: !!r.ready, why: r.why ?? null };
}
