// Grava 对话的客户端接口。两种实现：
// - HttpApi：真实对话。走 server/chat.py，SSE 流式，主对话与 Telegram 共用 main 会话。
// - OfflineApi：没连上服务器时用，不假装回复。
import { fetch as expoFetch } from 'expo/fetch';
import type { Attachment, Message, PendingFile } from '../data/types';
import { L } from '../i18n';
import { authHeaders, fileUrl, getBase } from './base';

export interface GravaApi {
  readonly connected: boolean;
  /** 发一条消息，拿回 Grava 的回复。threadId 为 'main'、groupId 或独立空间 id。onDelta 在流式输出时逐段回调。 */
  send(threadId: string, text: string, modelId: string, onDelta?: (partial: string) => void, onStart?: (userId: string) => void, files?: PendingFile[]): Promise<Message>;
  /** 语音输入：录音传上去，拿回文字。 */
  transcribe(file: PendingFile): Promise<string>;
  /** 读线程的历史记录（真实接入后从服务器取）。inFlight 表示服务端还在回上一条。 */
  history(threadId: string, day?: string): Promise<{ messages: Message[]; modelId: string; inFlight?: { text: string; modelId: string } | null } | null>;
  /** 重新接上服务端正在进行的回复。没有的话返回 null。 */
  attach(threadId: string, onDelta?: (partial: string) => void): Promise<Message | null>;
  /** 记住这个线程默认用哪个模型。 */
  setModel(threadId: string, modelId: string): Promise<void>;
  /** 只从对话记录里删掉这一条，Grava 的上下文不变。 */
  deleteMessage(threadId: string, msgId: string): Promise<void>;
  /** 会话退回到这条用户消息之前（它和之后的都去掉，Grava 也忘掉），返回原文。 */
  rewind(threadId: string, msgId: string): Promise<string>;
}

const now = () => {
  const d = new Date();
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
};

/** 没连上服务器时用：什么都不假装，发消息直接报错。 */
export class OfflineApi implements GravaApi {
  readonly connected = false;
  async send(): Promise<Message> { throw new Error(L('没连上服务器：检查「我 → 服务器」里的地址和令牌，再下拉刷新', 'Not connected to the server. Check the address and token in Me → Server, then pull to refresh.')); }
  async transcribe(): Promise<string> { throw new Error(L('没连上服务器', 'Not connected to the server')); }
  async history() { return null; }
  async attach() { return null; }
  async setModel() {}
  async deleteMessage() { throw new Error(L('没连上服务器', 'Not connected to the server')); }
  async rewind(): Promise<string> { throw new Error(L('没连上服务器', 'Not connected to the server')); }
}

/** 解析 SSE：每个事件是 `event: x\ndata: {...}\n\n`。 */
async function readSse(body: ReadableStream<Uint8Array>, onEvent: (event: string, data: any) => void) {
  const reader = body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
      let event = 'message'; let data = '';
      for (const line of chunk.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim();
        else if (line.startsWith('data:')) data += line.slice(5).trim();
      }
      if (data) { try { onEvent(event, JSON.parse(data)); } catch { /* 跳过坏行 */ } }
    }
  }
}

async function consume(r: Response, onDelta?: (partial: string) => void, onStart?: (userId: string) => void): Promise<Message> {
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j.detail ?? j.error ?? `HTTP ${r.status}`);
  }
  if (!r.body) throw new Error(L('没有收到回复流', 'No reply stream received'));
  let partial = '';
  let done: any = null;
  await readSse(r.body as unknown as ReadableStream<Uint8Array>, (event, data) => {
    if (event === 'start') onStart?.(data.userId);
    else if (event === 'delta') { partial += data.text; onDelta?.(partial); }
    else if (event === 'done') done = data;
  });
  if (!done) throw new Error(L('流中断', 'Reply stream cut off'));
  return { id: done.id, role: 'grava', time: done.time, modelId: done.modelId, fallbackFrom: done.fallbackFrom ?? undefined, body: { type: 'text', text: done.text }, error: done.status === 'error' ? done.error : undefined };
}

/** multipart 里的文件：web 上是 File 对象；原生上是 {uri, name, type}，由 RN 的原生网络层读盘上传。
 *  注意 Expo 57 起全局 fetch 是 expo/fetch，它的 FormData 不认 {uri} 这种部件（报 "Unsupported FormDataPart implementation"），
 *  所以上传一律走 XMLHttpRequest（仍是 RN 自己的实现，支持 uri 部件，web 上也能传 File）。 */
function formFile(fd: FormData, field: string, f: PendingFile) {
  if (f.file) fd.append(field, f.file, f.name);
  else fd.append(field, { uri: f.uri, name: f.name, type: f.mime || 'application/octet-stream' } as unknown as Blob);
}

function xhrUpload(url: string, fd: FormData, timeoutMs = 10 * 60 * 1000): Promise<any> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    xhr.timeout = timeoutMs;
    xhr.setRequestHeader('Accept', 'application/json');
    for (const [k, v] of Object.entries(authHeaders())) xhr.setRequestHeader(k, v);
    xhr.onload = () => {
      let j: any = {};
      try { j = JSON.parse(xhr.responseText || '{}'); } catch { /* 非 JSON */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(j);
      else reject(new Error(j.detail ?? j.error ?? L(`上传失败 HTTP ${xhr.status}`, `Upload failed: HTTP ${xhr.status}`)));
    };
    xhr.onerror = () => reject(new Error(L('上传失败：网络不通', 'Upload failed: network error')));
    xhr.ontimeout = () => reject(new Error(L('上传超时', 'Upload timed out')));
    xhr.send(fd);
  });
}

async function uploadFiles(threadId: string, files: PendingFile[]): Promise<Attachment[]> {
  const fd = new FormData();
  fd.append('thread', threadId);
  for (const f of files) formFile(fd, 'files', f);
  const j = await xhrUpload(`${getBase()}/api/chat/upload`, fd);
  return (j.attachments as Attachment[]).map((a) => ({ ...a, url: fileUrl(a.url) }));
}

const withBase = (m: any): Attachment[] | undefined => (m.attachments ? (m.attachments as Attachment[]).map((a) => ({ ...a, url: fileUrl(a.url) })) : undefined);

export class HttpApi implements GravaApi {
  readonly connected = true;
  async send(threadId: string, text: string, modelId: string, onDelta?: (partial: string) => void, onStart?: (userId: string) => void, files?: PendingFile[]): Promise<Message> {
    const attachments = files?.length ? (await uploadFiles(threadId, files)).map((a) => a.id) : [];
    const r = await expoFetch(`${getBase()}/api/chat/send`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream', ...authHeaders() },
      body: JSON.stringify({ thread: threadId, text, model: modelId, attachments }),
    });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new Error(j.detail ?? j.error ?? `HTTP ${r.status}`);  // 比如 409：上一条还没回完
    }
    let userId: string | null = null;
    const started = (id: string) => { userId = id; onStart?.(id); };
    try {
      return await consume(r as unknown as Response, onDelta, started);
    } catch (e) {
      // 流断了（切后台、锁屏、5G/Wi-Fi 切换）：服务端照样跑完。先重新接上（15 分钟内回完的也接得上）；
      // 再不行就去历史记录里找这条之后的回复。只有都找不到才算真的没发出去。
      const again = await this.attach(threadId, onDelta).catch(() => null);
      if (again) return again;
      const h = await this.history(threadId).catch(() => null);
      const msgs = h?.messages ?? [];
      const mine = userId ? msgs.findIndex((m) => m.id === userId) : msgs.map((m) => m.body.text.trim() === text.trim() && m.role === 'user').lastIndexOf(true);
      const reply = mine >= 0 ? msgs.slice(mine + 1).find((m) => m.role === 'grava') : null;
      if (reply) return reply;
      throw e;
    }
  }
  async attach(threadId: string, onDelta?: (partial: string) => void): Promise<Message | null> {
    const r = await expoFetch(`${getBase()}/api/chat/stream?thread=${encodeURIComponent(threadId)}`, { headers: { Accept: 'text/event-stream', ...authHeaders() } });
    if (r.status === 204) return null;
    return consume(r as unknown as Response, onDelta);
  }
  async history(threadId: string, day?: string) {
    const r = await expoFetch(`${getBase()}/api/chat/history?thread=${encodeURIComponent(threadId)}${day ? `&day=${day}` : ''}`, { headers: { Accept: 'application/json', ...authHeaders() } });
    if (!r.ok) return null;
    const j = await r.json();
    const messages: Message[] = (j.messages as any[]).map((m) => ({ id: m.id, role: m.role, time: m.time, modelId: m.modelId ?? undefined, fallbackFrom: m.fallbackFrom ?? undefined, body: { type: 'text', text: m.text, attachments: withBase(m) }, error: m.status === 'error' ? L('上次没拿到回复', 'No reply was received') : undefined }));
    return { messages, modelId: j.modelId as string, inFlight: j.inFlight ?? null };
  }
  async setModel(threadId: string, modelId: string) {
    await expoFetch(`${getBase()}/api/chat/model`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify({ thread: threadId, model: modelId }) }).catch(() => {});
  }
  private async post(path: string, body: object) {
    const r = await expoFetch(`${getBase()}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail ?? `HTTP ${r.status}`);
    return j;
  }
  async transcribe(file: PendingFile): Promise<string> {
    const fd = new FormData();
    formFile(fd, 'file', file);
    const j = await xhrUpload(`${getBase()}/api/chat/transcribe`, fd, 3 * 60 * 1000);
    return (j.text as string) ?? '';
  }
  async deleteMessage(threadId: string, msgId: string) {
    await this.post('/api/chat/delete', { thread: threadId, id: msgId });
  }
  async rewind(threadId: string, msgId: string) {
    return (await this.post('/api/chat/rewind', { thread: threadId, id: msgId })).text as string;
  }
}

export const timeNow = now;
