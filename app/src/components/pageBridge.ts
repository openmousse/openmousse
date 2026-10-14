// 页面盒子的桥（网页版 iframe 和手机 WebView 共用，src/components/PageHost*.tsx）：页面发来的消息长什么样、回给页面的结果长什么样。
// 页面那头的脚本是服务器包进文档的（server/pages.py 的 bridge_js）；收到消息以后怎么办（读数据、往输入框放字、报错）在 PageScreen 的 onBridge。

export interface BridgeMsg { mousse: 1; type: string; id?: number; payload?: { name?: unknown; text?: unknown; message?: unknown } }
export type BridgeReply = { ok: true; data: unknown } | { ok: false; error: { code: string; message: string } };
export type BridgeHandler = (m: BridgeMsg, reply: (r: BridgeReply) => void) => void;

/** 盒子停下的原因：left = 页面试图打开别的地址（或第二次加载）；crashed = 手机上网页进程没了；flood = 手机上一秒内消息太多。 */
export type PageStop = 'left' | 'crashed' | 'flood';

/** 两种盒子同一套参数：html = 服务器包好的文档；onStop 以后盒子要卸掉（PageScreen 换成一句说明）。 */
export interface PageHostProps { html: string; title: string; onBridge: BridgeHandler; onStop: (why: PageStop) => void }

/** 认得的消息才往下交：{mousse: 1, type: 字符串, …}；别的一律当没收到。 */
export function bridgeMsg(d: unknown): BridgeMsg | null {
  const m = d as BridgeMsg | null;
  return m && typeof m === 'object' && m.mousse === 1 && typeof m.type === 'string' ? m : null;
}

/** 回给页面的结果：和网页版一直用的形状一样（桥按 id 找回对应的 Promise）。 */
export const bridgeResult = (id: number | undefined, r: BridgeReply) => ({ mousse: 1 as const, type: 'result' as const, id, ...r });
