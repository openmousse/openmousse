// 这台手机当节点（第 8 批，13.2，2026-10-19；服务器那边见 server/nodes.py、Agent 的工具见 server/engine/nodes.py）。
// 主人在「设置 → 硬件 → 这台手机」里打开的几样：定位、拍照、通知。只在 app 在前台时连：长轮询等服务器交活；
// 切到后台先跟服务器说一声（bye），服务器那边马上回「手机 app 不在前台」；回到前台再报一遍（hello）接着等。
// - 定位：expo-location（新的原生模块：要 1.0.7 起的安装包；只要「使用期间」的权限，不要后台定位）。按开的那一档直接回，
//   回一次在本机记一条（最近 20 条，这一页能看到）。
// - 拍照：每次弹出 NodeCamera（全屏、看得到取景），主人点了「拍照」才拍；关掉、切到后台、服务器不等了 = 不拍。
// - 通知：服务器直接推送（push.py），这里只报一声「能收」，app 在后台也收得到。
// 命令面不交收件箱卡：主人就拿着这台手机，在这里打开就是批准（hello 带上每一项开到哪一档）。
// 原生模块在才 require（同 src/hardware/ble.ts）：网页版、老安装包都不碰它们。
import { AppState, Platform, type AppStateStatus } from 'react-native';
import { AuthError, getBase, getToken, httpStatus } from '../api/base';
import { nodesApi, pollNodes, type NodeInvoke, type NodeLevel } from '../api/nodes';
import { supports } from '../api/version';
import { readLocal, writeLocal } from '../deviceStore';
import { L } from '../lang';

type LocationModule = typeof import('expo-location');
type CameraModule = typeof import('expo-camera');

const KEY = 'mousse.phoneNode';
const LOG_KEY = 'mousse.phoneNode.log';
const LOG_MAX = 20;
const WAIT = 20;
const BACKOFF_MAX = 30_000;
const LOCATION_MS = 20_000;

// —— 原生模块（在才用）——

let locMod: LocationModule | null | undefined;
/** expo-location：网页版、1.0.7 以前的安装包 → null。 */
export function locationModule(): LocationModule | null {
  if (locMod !== undefined) return locMod;
  locMod = null;
  if (Platform.OS !== 'web') {
    try {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const { requireOptionalNativeModule } = require('expo-modules-core') as typeof import('expo-modules-core');
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      if (requireOptionalNativeModule('ExpoLocation')) locMod = require('expo-location') as LocationModule;
    } catch {
      locMod = null;
    }
  }
  return locMod;
}

let camMod: CameraModule | null | undefined;
/** expo-camera（1.0.6 起就有）：网页版、更老的安装包 → null。 */
export function cameraModule(): CameraModule | null {
  if (camMod !== undefined) return camMod;
  camMod = null;
  if (Platform.OS !== 'web') {
    try {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      camMod = require('expo-camera') as CameraModule;
    } catch {
      camMod = null;
    }
  }
  return camMod;
}

function deviceLabel(): string {
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const d = require('expo-device') as typeof import('expo-device');
    return (d.modelName || '').trim() || (Platform.OS === 'ios' ? 'iPhone' : 'Android');
  } catch {
    return Platform.OS === 'ios' ? 'iPhone' : 'Android';
  }
}

// —— 这台手机打开了哪几样（本机记着；每一项开到哪一档在服务器上）——

export interface Caps { location: boolean; camera: boolean; notify: boolean }
const NONE: Caps = { location: false, camera: false, notify: false };

function loadCaps(): Caps {
  try {
    const v = JSON.parse(readLocal(KEY) || '{}') as Partial<Caps>;
    return { location: v.location === true, camera: v.camera === true, notify: v.notify === true };
  } catch {
    return NONE;
  }
}

let caps: Caps = Platform.OS === 'web' ? NONE : loadCaps();
export const phoneCaps = () => caps;

function commandsOf(c: Caps): string[] {
  return [...(c.location && locationModule() ? ['location.get'] : []), ...(c.camera && cameraModule() ? ['camera.snap'] : []),
    ...(c.notify ? ['system.notify'] : [])];
}

// —— 给页面看的状态 ——

export type Phase = 'off' | 'connecting' | 'online' | 'background' | 'serverOff' | 'error';
export interface PhoneNodeState { phase: Phase; error: string; id: string | null; levels: Record<string, NodeLevel> }
let state: PhoneNodeState = { phase: 'off', error: '', id: null, levels: {} };
const subs = new Set<() => void>();
function set(next: Partial<PhoneNodeState>) {
  state = { ...state, ...next };
  for (const f of [...subs]) f();
}
export const phoneNodeState = () => state;
export function onPhoneNode(cb: () => void): () => void {
  subs.add(cb);
  return () => { subs.delete(cb); };
}

// —— 定位记录 ——

export interface LocationRead { at: number; accuracy: number | null; precise: boolean }
export function locationLog(): LocationRead[] {
  try {
    const v = JSON.parse(readLocal(LOG_KEY) || '[]') as unknown;
    return Array.isArray(v) ? (v as LocationRead[]).filter((x) => typeof x?.at === 'number') : [];
  } catch {
    return [];
  }
}
function logRead(r: LocationRead) {
  writeLocal(LOG_KEY, JSON.stringify([r, ...locationLog()].slice(0, LOG_MAX)));
  set({});
}

// —— 错误：带稳定的 code 回给服务器（照 OpenClaw 的 LOCATION_* / NODE_BACKGROUND_UNAVAILABLE）——

class NodeFail extends Error {
  code: string;
  constructor(code: string, message: string) { super(message); this.code = code; }
}
const background = () => new NodeFail('NODE_BACKGROUND_UNAVAILABLE', L('手机 app 不在前台', "The phone app isn't in the foreground"));

// —— 拍照：交给 NodeCamera 那一页（全屏取景，主人点了才拍）——

export interface PhotoRequest { id: string; facing: 'back' | 'front'; resolve: (p: Record<string, unknown>) => void; reject: (e: Error) => void }
const photos = new Map<string, PhotoRequest>();
/** NodeCamera 那一页用：这件活还在等吗。 */
export const photoRequest = (id: string) => photos.get(id) ?? null;
let openCamera: ((id: string) => boolean) | null = null;
/** navigation.tsx 挂上：打开 NodeCamera（打不开 → false）。 */
export function setCameraOpener(fn: ((id: string) => boolean) | null) { openCamera = fn; }

function snap(inv: NodeInvoke): Promise<Record<string, unknown>> {
  if (!caps.camera || !cameraModule()) return Promise.reject(new NodeFail('CAMERA_DISABLED', L('这台手机没打开拍照', "Photos aren't turned on on this phone")));
  if (AppState.currentState !== 'active') return Promise.reject(background());
  if (photos.size) return Promise.reject(new NodeFail('BUSY', L('手机上已经有一张在等着拍', 'Another photo is already waiting on the phone')));
  return new Promise((resolve, reject) => {
    const facing = inv.params.facing === 'front' ? 'front' : 'back';
    // 定下来（拍了、没拍、服务器不等了）就从表里拿掉，再通知一声：相机页看到它没了就自己关上
    const done = () => { photos.delete(inv.id); set({}); };
    photos.set(inv.id, { id: inv.id, facing, resolve: (p) => { done(); resolve(p); }, reject: (e) => { done(); reject(e); } });
    if (!openCamera || !openCamera(inv.id)) photos.get(inv.id)?.reject(new NodeFail('UNAVAILABLE', L('打不开相机页', "Couldn't open the camera")));
  });
}

/** NodeCamera 关掉、主人没拍：告诉服务器没拍成。 */
export const declined = () => new NodeFail('CAMERA_DECLINED', L('主人没有拍', "The user didn't take the photo"));

// —— 定位 ——

async function locate(params: Record<string, unknown>): Promise<Record<string, unknown>> {
  const Loc = locationModule();
  if (!Loc) throw new NodeFail('UNAVAILABLE', L('这个版本的 app 不能定位', "This version of the app can't get the location"));
  if (!caps.location) throw new NodeFail('LOCATION_DISABLED', L('这台手机没打开定位', "Location isn't turned on on this phone"));
  if (AppState.currentState !== 'active') throw background();
  const perm = await Loc.getForegroundPermissionsAsync();
  if (perm.status !== 'granted') throw new NodeFail('LOCATION_PERMISSION_REQUIRED', L('系统没给定位权限', "Location permission isn't granted"));
  if (!(await Loc.hasServicesEnabledAsync())) throw new NodeFail('LOCATION_DISABLED', L('手机的定位服务关着', 'Location Services are off on this phone'));
  const accuracy = params.accuracy === 'precise' ? Loc.Accuracy.High : params.accuracy === 'coarse' ? Loc.Accuracy.Low : Loc.Accuracy.Balanced;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const late = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new NodeFail('LOCATION_TIMEOUT', L('没在时限内定到位', 'No location fix in time'))), LOCATION_MS);
  });
  try {
    const pos = await Promise.race([Loc.getCurrentPositionAsync({ accuracy }), late]);
    const c = pos.coords;
    logRead({ at: Date.now(), accuracy: c.accuracy ?? null, precise: params.accuracy === 'precise' });
    return {
      lat: c.latitude, lon: c.longitude, accuracyMeters: c.accuracy ?? null, altitudeMeters: c.altitude ?? null,
      speedMps: c.speed ?? null, headingDeg: c.heading ?? null, timestamp: new Date(pos.timestamp).toISOString(),
    };
  } finally {
    if (timer) clearTimeout(timer);
  }
}

// —— 干活、回结果 ——

async function handle(inv: NodeInvoke): Promise<void> {
  try {
    const payload = inv.command === 'location.get' ? await locate(inv.params)
      : inv.command === 'camera.snap' ? await snap(inv)
        : (() => { throw new NodeFail('UNKNOWN_COMMAND', L(`这台手机不会做 ${inv.command}`, `This phone can't do ${inv.command}`)); })();
    await nodesApi.result(inv.id, { ok: true, payload }).catch(() => {});
  } catch (e) {
    const code = e instanceof NodeFail ? e.code : 'NODE_ERROR';
    const message = e instanceof Error ? e.message : String(e);
    await nodesApi.result(inv.id, { ok: false, error: { code, message } }).catch(() => {});
  }
}

function cancelLocal(id: string) {
  photos.get(id)?.reject(new NodeFail('CANCELLED', L('服务器不等了', 'The server stopped waiting')));
}

// —— 连着：前台长轮询 ——

let run = 0;
let ctl: AbortController | null = null;  // 正在跑的那一趟自己的（停的时候只停它）
let live = 0;  // 还在跑的那一趟的号（0 = 没有）：sync() 看它，不看页面上的 phase
let registered = false;  // 跟服务器报过（关掉以后还要再报一次空的）
let active = false;  // PhoneNodeRunner 开着（这台服务器有设备节点）：停了以后回前台也不再自己连（审查：换到别的底座还每次 hello）

const ready = () => active && Platform.OS !== 'web' && supports('nodes') && !!getBase() && !!getToken();

function appVersion(): string {
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const C = (require('expo-constants') as typeof import('expo-constants')).default;
    return String(C.nativeAppVersion ?? C.expoConfig?.version ?? '');
  } catch {
    return '';
  }
}

async function hello(levels?: Record<string, NodeLevel>): Promise<void> {
  const h = await nodesApi.hello({ platform: Platform.OS, name: deviceLabel(), version: appVersion(), commands: commandsOf(caps), foreground: true, allow: levels });
  registered = true;
  set({ id: h.id, levels: h.levels || {} });
}

async function loop(me: number, signal: AbortSignal): Promise<void> {
  live = me;
  let backoff = 1000;
  let said = false;
  set({ phase: 'connecting', error: '' });
  try {
    while (me === run) {
      try {
        if (!said) {
          await hello();
          if (me !== run) {
            // 报到的时候这一趟被停了（切到后台、改了开关）：页面别改回「在线」，也别接着等活。
            // 人已经不在前台就再说一声 bye：这一声 hello 可能比切后台时那声 bye 晚到，服务器会当手机还在前台
            if (AppState.currentState !== 'active') nodesApi.bye('background').catch(() => {});
            return;
          }
          said = true;
          set({ phase: 'online', error: '' });
        }
        const got = await pollNodes(WAIT, signal);
        got.cancel.forEach(cancelLocal);
        // 这一趟停了也把交到手上的活回掉：handle 自己看前台和开关（回「不在前台」「没打开」），服务器不用干等到超时
        for (const inv of got.invokes) handle(inv).catch(() => {});
        if (me !== run) return;
        backoff = 1000;
      } catch (e) {
        if (me !== run) return;
        const st = httpStatus(e);
        if (st === 404) { set({ phase: 'serverOff', error: '' }); return; }   // 服务器上没开设备节点
        // 令牌不认了：request() 抛的 AuthError 不带 status，也算（审查：原来认不出、每 30 秒重来）
        if (st === 401 || st === 403 || e instanceof AuthError) { set({ phase: 'error', error: e instanceof Error ? e.message : String(e) }); return; }
        said = false;
        set({ phase: 'error', error: e instanceof Error ? e.message : String(e) });
        await new Promise((r) => setTimeout(r, backoff * (1 + Math.random() * 0.2)));
        backoff = Math.min(backoff * 2, BACKOFF_MAX);
      }
    }
  } finally {
    if (live === me) live = 0;
  }
}

function stopLoop() {
  run += 1;
  ctl?.abort();
  ctl = null;
}

/** 照现在的情况开、关：前台、服务器有这个功能、打开了至少一样 → 连着等活；别的都停。 */
export function sync(): void {
  if (!ready()) { stopLoop(); set({ phase: 'off' }); return; }
  const want = commandsOf(caps).length > 0;
  if (!want) {
    stopLoop();
    set({ phase: 'off' });
    if (registered) hello().catch(() => {});  // 都关了：报一遍空的，服务器那边就没有能用的了
    return;
  }
  if (AppState.currentState !== 'active') return;
  if (live !== 0 && live === run) return;  // 这一趟还在跑（停掉的那几趟自己会退）
  const me = ++run;
  const c = new AbortController();
  ctl = c;
  loop(me, c.signal).catch(() => {});
}

function onAppState(s: AppStateStatus) {
  if (s === 'active') { sync(); return; }
  if (s !== 'background') return;
  const wasOn = state.phase === 'online' || state.phase === 'connecting';
  stopLoop();
  for (const p of [...photos.values()]) p.reject(background());
  if (wasOn) {
    set({ phase: 'background' });
    nodesApi.bye('background').catch(() => {});
  }
}

let started = false;
/** 启动（navigation.tsx 的 PhoneNodeRunner 连上服务器后调一次）。 */
export function startPhoneNode(): () => void {
  if (Platform.OS === 'web') return () => {};
  if (!started) {
    started = true;
    AppState.addEventListener('change', onAppState);
  }
  active = true;
  sync();
  return () => { active = false; stopLoop(); set({ phase: 'off' }); };
}

/**
 * 打开 / 关掉一样，顺带定它的档（levels：{"location.get": "allow" | "ask"}）。马上跟服务器报一遍（就是批准），再照新的情况连着或停下。
 * 关掉的那几样同时在服务器上设成 off（这台手机上报得慢一步也不会被用到）。
 */
export async function setPhoneCaps(next: Partial<Caps>, levels?: Record<string, NodeLevel>): Promise<void> {
  const before = caps;
  caps = { ...caps, ...next };
  writeLocal(KEY, JSON.stringify(caps));
  const off: Record<string, NodeLevel> = {};
  if (before.location && !caps.location) off['location.get'] = 'off';
  if (before.camera && !caps.camera) off['camera.snap'] = 'off';
  if (before.notify && !caps.notify) off['system.notify'] = 'off';
  set({});
  if (!ready()) return;
  try {
    await hello({ ...(levels || {}) });
  } catch (e) {
    // 服务器没收下（没开设备节点、连不上、令牌不认）：这次打开的几样退回关（关掉的照旧关着：本机这道门先关上）
    const undo: Partial<Caps> = {};
    for (const k of Object.keys(next) as (keyof Caps)[]) if (next[k] && !before[k]) undo[k] = false;
    caps = { ...caps, ...undo };
    writeLocal(KEY, JSON.stringify(caps));
    set({});
    throw e;
  }
  for (const [cmd, lv] of Object.entries(off)) {
    if (state.id) await nodesApi.allow(state.id, cmd, lv).catch(() => {});
  }
  stopLoop();
  set({ phase: 'off' });
  sync();
}

/** 只改一项的档（已经打开的定位：每次问 / 直接给）。 */
export async function setPhoneLevel(command: string, level: NodeLevel): Promise<void> {
  if (!state.id) await hello();
  if (!state.id) return;
  const r = await nodesApi.allow(state.id, command, level);
  const lv = Object.fromEntries(r.node.commands.map((c) => [c.name, c.level])) as Record<string, NodeLevel>;
  set({ levels: { ...state.levels, ...lv } });
}
