// 管家（主线第三步第三段，2026-10-21；服务器 ../../server/butler.py）：名片 Agent 升级来的那一个，你只有一个对外身份。
// 资料（证件）、对外口径的例句（按档位试答：不发出去、不进「说过的话」）和「改这一句」（先给样子、点头才改、能撤销）、
// 两条固定的规矩、可以请教的赛博、说过的话（Doorman 扣下的放行 / 不放）。服务器 server.json 的 butler 开着才有（接口回 404 = 没开）：
// useButlerOn() 判断，没开就照旧是「我的名片 Agent」那一页。
import { useEffect, useState } from 'react';
import { httpStatus, request } from './base';
import type { CyberAvatarSpec } from './cybers';
import { supports } from './version';

/** 管家的形象：默认环与点（{orbit: true}），捏过的和赛博一样 */
export type ButlerAvatar = { orbit: true } | CyberAvatarSpec;
export const isOrbit = (a: ButlerAvatar | null | undefined): boolean => !a || 'orbit' in a;

export interface ButlerRule { id: 'ask' | 'health'; title: string; text: string }
export interface ButlerCyber { id: string; name: string; tag: string; avatar: CyberAvatarSpec | null; modelName: string; on: boolean }
export interface ButlerProfile {
  name: string; customName: boolean; avatar: ButlerAvatar;
  model: { id: string | null; name: string; backend: string };
  week: { said: number; held: number };
  rules: ButlerRule[]; cybers: ButlerCyber[]; tiers: { id: string; label: string }[]; note: string;
}
export interface SampleOption { level: string; label: string; example: string; current: boolean }
export interface Sample {
  key: string; question: string; category: string; categoryLabel: string; level: string | null;
  reply: string; asks: boolean; cached: boolean; options: SampleOption[];
}
export interface Samples { tier: string; tierLabel: string; items: Sample[]; intro: string }
export interface SampleChange {
  id: string; tier: string; tierLabel: string; category: string; categoryLabel: string; sample: string;
  old: string; new: string; oldLabel: string; newLabel: string; before: string; after: string;
  status: 'pending' | 'applied' | 'dropped' | 'undone'; createdAt: string; decidedAt: string | null;
}
export interface SaidItem {
  id: string; ts: string; time: string; peer: string; peerName: string; channel: string; ref: string; where: string; text: string;
  status: string; by: string; held: boolean; reasons: (string | { kind?: string; detail?: string })[]; original: string; inboxId: string | null; outcome: string;
}
/** 请教过一位赛博（仅你可见）：带去的要点、它怎么说 */
export interface Consult { id: string; cyber: string; name: string; gist: string; answer: string; status: string; ref: string; createdAt: string; line: string }

// 你的管家的形象（好友群里画它的气泡用）：读过资料就记下，改了跟着换
let avatarKnown: ButlerAvatar | null = null;
let avatarAsking: Promise<ButlerAvatar | null> | null = null;
const remember = (p: ButlerProfile): ButlerProfile => { avatarKnown = p.avatar; return p; };

export const butlerApi = {
  get: () => request<ButlerProfile>('/api/butler').then(remember),
  /** name: "" = 回到默认；reset_avatar = 回到环与点；cybers = 可以请教的赛博（整张名单） */
  patch: (body: { name?: string; avatar?: CyberAvatarSpec; reset_avatar?: boolean; cybers?: string[] }) =>
    request<ButlerProfile>('/api/butler', { method: 'PATCH', body }).then(remember),
  samples: (tier: string, fresh = false) =>
    request<Samples>(`/api/butler/samples?tier=${encodeURIComponent(tier)}${fresh ? '&fresh=1' : ''}`, { timeoutMs: 120000 }),
  /** 改这一句：先用改过的档位再试答一次（还没写进去） */
  preview: (tier: string, key: string, level: string) =>
    request<{ change: SampleChange }>(`/api/butler/samples/${encodeURIComponent(tier)}/${encodeURIComponent(key)}/preview`,
      { method: 'POST', body: { level }, timeoutMs: 120000 }).then((j) => j.change),
  decide: (cid: string, action: 'apply' | 'drop') =>
    request<{ change: SampleChange }>(`/api/butler/changes/${encodeURIComponent(cid)}`, { method: 'POST', body: { action } }).then((j) => j.change),
  undo: (change?: string) => request<{ change: SampleChange }>('/api/butler/undo', { method: 'POST', body: change ? { change } : {} }).then((j) => j.change),
  said: (days = 7) => request<{ days: number; items: SaidItem[]; week: { said: number; held: number } }>(`/api/butler/said?days=${days}`),
  /** Doorman 扣下的那句：放行 / 不放（走原来那张 review 卡） */
  actSaid: (lid: string, action: 'release' | 'keep') =>
    request<{ item: SaidItem }>(`/api/butler/said/${encodeURIComponent(lid)}`, { method: 'POST', body: { action } }),
  consults: () => request<{ items: Consult[] }>('/api/butler/consults').then((j) => j.items),
};

// —— 开着吗 ——————————————————————————————————————————————————————————————————————————————
const TTL = 60_000;
let known: { on: boolean; at: number } | null = null;
let asking: Promise<boolean> | null = null;

/** 服务器的管家开着吗：老服务器（不认 butler）= 关；接口 404 = server.json 没开。问一次记一分钟。 */
export function butlerOn(): Promise<boolean> {
  if (!supports('butler')) return Promise.resolve(false);
  if (known && Date.now() - known.at < TTL) return Promise.resolve(known.on);
  if (!asking) {
    asking = request('/api/butler')
      .then(() => true)
      .catch((e) => (httpStatus(e) === 404 ? false : (known?.on ?? false)))
      .then((on) => { known = { on, at: Date.now() }; asking = null; return on; });
  }
  return asking;
}

/** 管家开着吗：没问过之前是 null，问到了 true / false。 */
export function useButlerOn(): boolean | null {
  const [on, setOn] = useState<boolean | null>(() => (!supports('butler') ? false : known ? known.on : null));
  useEffect(() => {
    let live = true;
    butlerOn().then((v) => { if (live) setOn(v); }).catch(() => {});
    return () => { live = false; };
  }, []);
  return on;
}

/** 你的管家的形象：没读过先问一次（几处同时要只问一次），读到之前是 null（画环与点）。 */
export function useButlerAvatar(): ButlerAvatar | null {
  const [a, setA] = useState<ButlerAvatar | null>(avatarKnown);
  useEffect(() => {
    let live = true;
    if (!avatarKnown) {
      avatarAsking = avatarAsking ?? butlerApi.get().then((p) => p.avatar).catch(() => null).finally(() => { avatarAsking = null; });
      avatarAsking.then((v) => { if (live && v) setA(v); }).catch(() => {});
    }
    return () => { live = false; };
  }, []);
  return a;
}
