// Flipper Zero 经手机蓝牙（1.0.6）：连上记住的那台，读状态，蜂鸣，列红外遥控器和按钮，发一个按钮。
// 动作名、参数和返回的样子照电脑上那座 USB 桥（flipper-bridge 分支 bridge/flipper_bridge.py：status / files / read /
// ir_buttons / ir_send / ping），连规矩也一样：只看 /ext 下面（不碰 u2f 和点开头的），只发 /ext/infrared 里 .ir 文件的按钮，
// 别的文件只报类型、不读内容。以后热更新让手机也当一座桥、接同一套服务器接口（/api/hardware/bridge/*）时，
// handle(action, args) 就是接缝：一个动作一个函数，返回值可以原样交给 bridge/result。
// ir_learn 在蓝牙上做不了：RPC 没有红外接收（那是 USB 命令行的 ir rx），还是交给电脑上的桥。
// 和 USB 桥的不同：桥说命令行，这里说 protobuf RPC（flipperRpc.ts），所以按钮名不必限 ASCII（RPC 传 UTF-8 字符串）。
import { readLocal, writeLocal } from '../deviceStore';
import { L } from '../lang';
import { cancelConnect, openSerial, type SerialLink } from './ble';
import {
  AppState, CLOSED, F, RpcClient, RpcError, Status, TIMEOUT,
  decodeAppError, decodeAppState, decodeFileList, decodeFileOf, decodeKeyValue, decodeStorageInfo, decodeVersion,
  req, statusName, utf8Decode, type Button, type Req,
} from './flipperRpc';

export const IR_DIR = '/ext/infrared';
export const ACTIONS = ['status', 'files', 'read', 'ir_buttons', 'ir_send', 'ping'] as const;
export type Action = (typeof ACTIONS)[number];

const KEY = 'mousse.flipper';  // 记住的那台：{id, name}（deviceStore，只在这台设备上）
const PATH_RE = /^\/ext(\/[\w .()+-]+)*\/?$/;
const READ_LIMIT = 64 * 1024;

export type ErrorCode = 'refused' | 'busy' | 'not_found' | 'no_device' | 'timeout' | 'closed' | 'failed';

export class FlipperError extends Error {
  code: ErrorCode;
  constructor(code: ErrorCode, message: string) {
    super(message);
    this.name = 'FlipperError';
    this.code = code;
  }
}

const refused = (message: string) => new FlipperError('refused', message);

/** 按钮名：1–64 个字符，没有控制字符（RPC 传 UTF-8，不必像命令行那样限 ASCII）。 */
const validButton = (name: string) => name.length >= 1 && name.length <= 64 && [...name].every((c) => (c.codePointAt(0) ?? 0) >= 0x20);

/** 设备上开着别的应用（ERROR_APP_SYSTEM_LOCKED = 17）。 */
const busyText = () => L('Flipper 上已经打开了另一个应用，请先回到主界面再试。', 'Another app is open on the Flipper. Go back to its home screen and try again.');

/** 把 RPC 的状态码、超时、断线翻成一句给人看的话。 */
export function explain(e: unknown): FlipperError {
  if (e instanceof FlipperError) return e;
  if (e instanceof RpcError) {
    switch (e.status) {
      case TIMEOUT: return new FlipperError('timeout', L('Flipper 没有响应', "The Flipper didn't respond"));
      case CLOSED: return new FlipperError('closed', L('连接已断开', 'Disconnected'));
      case Status.ERROR_APP_SYSTEM_LOCKED: return new FlipperError('busy', busyText());
      case Status.ERROR_BUSY: return new FlipperError('busy', L('Flipper 正忙，请稍后再试', 'The Flipper is busy. Try again shortly.'));
      case Status.ERROR_STORAGE_NOT_EXIST: return new FlipperError('not_found', L('文件或文件夹不存在', 'No such file or folder'));
      case Status.ERROR_STORAGE_NOT_READY: return new FlipperError('failed', L('没有 SD 卡，或无法读取', "No SD card, or it can't be read"));
      case Status.ERROR_STORAGE_DENIED: return new FlipperError('refused', L('Flipper 拒绝访问', 'The Flipper denied access'));
      default: return new FlipperError('failed', L(`Flipper 返回错误：${statusName(e.status)}`, `The Flipper returned an error: ${statusName(e.status)}`));
    }
  }
  return new FlipperError('failed', e instanceof Error ? e.message : String(e));
}

// —— 记住的设备和连接状态（页面用 useSyncExternalStore 订阅） ——

export interface Device { id: string; name: string }

function rememberedDevice(): Device | null {
  const raw = readLocal(KEY);
  if (!raw) return null;
  try {
    const d = JSON.parse(raw) as Partial<Device>;
    return typeof d.id === 'string' && d.id ? { id: d.id, name: typeof d.name === 'string' && d.name ? d.name : 'Flipper' } : null;
  } catch {
    return null;
  }
}

function remember(d: Device | null): void {
  writeLocal(KEY, d ? JSON.stringify(d) : '');
}

/** idle = 没连；connecting / securing = 连接中（securing 时可能在等配对码）；ready = 能说 RPC 了。 */
export type Phase = 'idle' | 'connecting' | 'securing' | 'ready' | 'error';
export interface FlipperState { phase: Phase; device: Device | null; error: string; protobuf: string }

let state: FlipperState | null = null;
const subs = new Set<() => void>();

export function flipperState(): FlipperState {
  if (!state) state = { phase: 'idle', device: rememberedDevice(), error: '', protobuf: '' };  // 第一次用时才读本机存储
  return state;
}

export function onFlipper(cb: () => void): () => void {
  subs.add(cb);
  return () => { subs.delete(cb); };
}

function set(patch: Partial<FlipperState>): void {
  state = { ...flipperState(), ...patch };
  for (const f of [...subs]) f();
}

// —— 会话 ——

interface Session { dev: Device; link: SerialLink; rpc: RpcClient; proto: { major: number; minor: number } }

let session: Session | null = null;
let connecting: Promise<void> | null = null;
let pending: string | null = null;  // 正在连的那台的 id（断开 / 忽略时用来中止）
let gen = 0;  // 每次断开加一：连到一半被断开 / 忽略的，连好了也不再用

/** 连上 dev（不给就连记住的那台）。已经连着同一台就什么都不做。 */
export function connect(dev?: Device): Promise<void> {
  const d = dev ?? flipperState().device;
  if (!d) return Promise.reject(new FlipperError('no_device', L('尚未连接过 Flipper', 'No Flipper has been connected yet')));
  if (session && session.dev.id === d.id && !session.link.isClosed()) return Promise.resolve();
  if (!connecting) {
    const p: Promise<void> = open(d).finally(() => { if (connecting === p) connecting = null; });
    connecting = p;
  }
  return connecting;
}

async function open(d: Device): Promise<void> {
  await drop();
  const my = gen;
  pending = d.id;
  set({ phase: 'connecting', device: d, error: '', protobuf: '' });
  let link: SerialLink | null = null;
  try {
    link = await openSerial(d.id, (stage) => { if (my === gen) set({ phase: stage }); });
    const rpc = new RpcClient(link, { timeoutMs: 8000 });
    // 先问协议版本：确认 RPC 会话通了，也决定按红外按钮用一条（0.25 起）还是按下 + 松开两条（官方 iOS app 也是这么分）
    const proto = decodeVersion((await rpc.one(req.protobufVersion())).content);
    if (my !== gen) throw new RpcError(CLOSED, 'cancelled');
    pending = null;
    const s: Session = { dev: d, link, rpc, proto };
    session = s;
    link.onClose((why) => {
      if (session !== s) return;  // 自己断开的：drop() 已经处理
      session = null;
      set({ phase: 'error', error: why });
    });
    if (link.isClosed()) throw new RpcError(CLOSED);
    remember(d);
    set({ phase: 'ready', device: d, protobuf: `${proto.major}.${proto.minor}` });
  } catch (e) {
    await link?.close().catch(() => {});
    const err = explain(e);
    if (my === gen) {  // 被 disconnect / forget 中止的：状态已经是它们设的
      session = null;
      pending = null;
      set({ phase: 'error', error: err.message });
    }
    throw err;
  }
}

/** 断开（记住的设备留着）；正在连的也中止。 */
async function drop(): Promise<void> {
  gen++;
  if (pending) {
    cancelConnect(pending);
    pending = null;
  }
  const s = session;
  session = null;
  if (s) {
    s.rpc.close('local');
    await s.link.close().catch(() => {});
  }
}

export async function disconnect(): Promise<void> {
  connecting = null;  // 连到一半的作废，下次 connect 重新开始
  await drop();
  set({ phase: 'idle', error: '' });
}

/** 断开并忘掉这台（系统蓝牙设置里的配对记录不归 app 管，要删得在系统设置里删）。 */
export async function forget(): Promise<void> {
  connecting = null;
  await drop();
  remember(null);
  set({ phase: 'idle', device: null, error: '', protobuf: '' });
}

/** 拿一个能用的会话（没连就连记住的那台）跑 fn；错误统一翻成 FlipperError。 */
async function run<T>(fn: (s: Session) => Promise<T>): Promise<T> {
  try {
    await connect();
    const s = session;
    if (!s) throw new RpcError(CLOSED);
    return await fn(s);
  } catch (e) {
    throw explain(e);
  }
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

// —— 规矩（照桥的 check_path / ir_file） ——

function checkPath(p: string): string {
  const path = String(p || '').replace(/\/+$/, '') || '/ext';
  if (!PATH_RE.test(path) || path.includes('..')) throw refused(L('只能访问 /ext 下的路径', 'Only paths under /ext'));
  const parts = path.split('/').slice(2);
  if (parts[0] === 'u2f' || parts.some((x) => x.startsWith('.'))) throw refused(L('不能访问此文件夹（U2F 密钥或系统文件）', 'This folder is off limits (U2F keys or system files)'));
  return path;
}

function irFile(p: string): string {
  const path = checkPath(p);
  if (!path.startsWith(`${IR_DIR}/`) || !path.endsWith('.ir')) throw refused(L('只能使用 /ext/infrared 中的 .ir 文件', 'Only .ir files in /ext/infrared'));
  return path;
}

export const remoteName = (path: string) => (path.split('/').pop() || path).replace(/\.ir$/, '');

// —— 文件 ——

export interface FileItem { name: string; dir: boolean; size?: number }

async function list(s: Session, path: string): Promise<FileItem[]> {
  const parts = await s.rpc.call(req.storageList(path), { timeoutMs: 15_000 });
  return parts.flatMap((m) => decodeFileList(m.content))
    .filter((f) => !f.name.startsWith('.'))
    .map((f) => (f.type === 'dir' ? { name: f.name, dir: true } : { name: f.name, dir: false, size: f.size }));
}

/** 读整个文件成文字（先 stat 看大小：蓝牙上读大文件要很久，又中途停不下来）。 */
async function text(s: Session, path: string): Promise<string> {
  const st = decodeFileOf((await s.rpc.one(req.storageStat(path))).content);
  if (st?.type === 'dir') throw refused(L('这是文件夹', 'That is a folder'));
  if (st && st.size > READ_LIMIT) throw refused(L('文件太大', 'File too large'));
  const parts = await s.rpc.call(req.storageRead(path), { timeoutMs: 15_000 });
  const chunks = parts.map((m) => decodeFileOf(m.content)?.data ?? new Uint8Array(0));
  const all = new Uint8Array(chunks.reduce((n, c) => n + c.length, 0));
  let off = 0;
  for (const c of chunks) { all.set(c, off); off += c.length; }
  return utf8Decode(all);
}

export interface IrButton { name: string; type?: string; protocol?: string }

/** .ir 文件（FlipperFormat）→ 按钮；顺序就是固件的 index（第几个 name:，从 0 起）。 */
export function parseIr(body: string): (IrButton & Record<string, string>)[] {
  const out: (IrButton & Record<string, string>)[] = [];
  let cur: (IrButton & Record<string, string>) | null = null;
  for (const ln of body.split('\n')) {
    if (ln.startsWith('#') || !ln.includes(':')) continue;
    const i = ln.indexOf(':');
    const k = ln.slice(0, i).trim();
    const v = ln.slice(i + 1).trim();
    if (k === 'name') {
      cur = { name: v };
      out.push(cur);
    } else if (cur && ['type', 'protocol', 'address', 'command', 'frequency', 'duty_cycle', 'data'].includes(k)) {
      cur[k] = v;
    }
  }
  return out;
}

const SAFE_HEADERS: Record<string, string[]> = {  // 只有这些表头字段离开设备；ID、密钥、原始数据一概不读
  sub: ['Filetype', 'Version', 'Frequency', 'Preset', 'Protocol'],
  nfc: ['Filetype', 'Version', 'Device type'],
  rfid: ['Filetype', 'Version', 'Key type'],
  ibtn: ['Filetype', 'Version', 'Protocol'],
};

function summarize(path: string, body: string): Record<string, unknown> {
  const ext = path.includes('.') ? (path.split('.').pop() ?? '').toLowerCase() : '';
  if (ext === 'ir') {
    return { path, kind: 'ir', buttons: parseIr(body).map((b) => ({ name: b.name, ...(b.type ? { type: b.type } : {}), ...(b.protocol ? { protocol: b.protocol } : {}) })) };
  }
  if (SAFE_HEADERS[ext]) {
    const fields: Record<string, string> = {};
    for (const ln of body.split('\n').slice(0, 30)) {
      const i = ln.indexOf(':');
      if (i > 0) fields[ln.slice(0, i).trim()] = ln.slice(i + 1).trim();
    }
    return { path, kind: ext, ...Object.fromEntries(SAFE_HEADERS[ext].filter((k) => k in fields).map((k) => [k, fields[k]])) };
  }
  if (['txt', 'md', 'fmf', 'js'].includes(ext) || (path.startsWith('/ext/apps_data/') && ['csv', 'json', 'log', 'conf'].includes(ext))) {
    return { path, kind: ext, text: body.slice(0, 8000) };
  }
  throw refused(L('此类文件只列出名称，不读取内容', 'This kind of file is listed by name only, not read'));
}

// —— 动作（和桥同名同参数） ——

export interface FlipperStatus {
  name?: string; model?: string; firmware?: string; branch?: string; fork?: string; build?: string; region?: string; protobuf?: string;
  battery?: number; chargeState?: string; charging?: boolean; voltage?: number; batteryHealth?: number;
  sdTotalMB?: number; sdFreeMB?: number; sd?: string;
}

/** 一条一个 key / value 的回复收成表；键里的 _ 换成 .，和桥读命令行 info device / info power 的键一样。 */
async function pairs(s: Session, r: Req): Promise<Record<string, string>> {
  const out: Record<string, string> = {};
  for (const m of await s.rpc.call(r, { timeoutMs: 10_000 })) {
    const { key, value } = decodeKeyValue(m.content);
    if (key) out[key.replace(/_/g, '.')] = value;
  }
  return out;
}

/** status：名字、固件、电量、SD 卡。不带 UID、MAC、密钥。 */
export function status(): Promise<FlipperStatus> {
  return run(async (s) => {
    const dev = await pairs(s, req.deviceInfo());
    const pw = await pairs(s, req.powerInfo()).catch(() => ({} as Record<string, string>));
    const sd = await s.rpc.one(req.storageInfo('/ext')).then((m) => decodeStorageInfo(m.content), () => null);
    const out: FlipperStatus = {
      name: dev['hardware.name'], model: dev['hardware.model'], firmware: dev['firmware.version'],
      branch: dev['firmware.branch.name'] || dev['firmware.branch'], fork: dev['firmware.origin.fork'], build: dev['firmware.build.date'],
      region: dev['hardware.region.provisioned'] || dev['hardware.region.builtin'] || dev['hardware.region'],
      protobuf: [dev['protobuf.version.major'], dev['protobuf.version.minor']].filter(Boolean).join('.') || `${s.proto.major}.${s.proto.minor}`,
    };
    if (/^\d+$/.test(pw['charge.level'] ?? '')) out.battery = Number(pw['charge.level']);
    if (pw['charge.state']) {
      out.chargeState = pw['charge.state'];  // charging / charged / discharging
      out.charging = pw['charge.state'] === 'charging';
    }
    if (/^\d+$/.test(pw['battery.voltage'] ?? '')) out.voltage = Math.round(Number(pw['battery.voltage']) / 10) / 100;
    if (/^\d+$/.test(pw['battery.health'] ?? '')) out.batteryHealth = Number(pw['battery.health']);
    if (sd && sd.total) {
      out.sdTotalMB = Math.floor(sd.total / 1024 / 1024);
      out.sdFreeMB = Math.floor(sd.free / 1024 / 1024);
    } else {
      out.sd = L('没有 SD 卡或无法读取', 'no SD card or unreadable');
    }
    return Object.fromEntries(Object.entries(out).filter(([, v]) => v !== '' && v != null)) as FlipperStatus;
  });
}

/** files：列一个文件夹（默认 /ext/infrared），最多 300 项。 */
export function files(args: { path?: string } = {}): Promise<{ path: string; items: FileItem[] }> {
  return run(async (s) => {
    const path = checkPath(args.path || IR_DIR);
    return { path, items: (await list(s, path)).slice(0, 300) };
  });
}

/** read：一个文件的摘要（.ir = 按钮；Sub-GHz / NFC / RFID / iButton 只给表头；文本类给前 8000 字）。 */
export function read(args: { path?: string } = {}): Promise<Record<string, unknown>> {
  return run(async (s) => {
    const path = checkPath(args.path || '');
    return summarize(path, await text(s, path));
  });
}

/** /ext/infrared 里的遥控器文件（不读内容）。文件夹还不存在（红外应用从没打开过）= 没有。 */
export function irRemotes(): Promise<{ file: string; name: string; size: number }[]> {
  return run(async (s) => {
    let items: FileItem[] = [];
    try {
      items = await list(s, IR_DIR);
    } catch (e) {
      if (e instanceof RpcError && e.status === Status.ERROR_STORAGE_NOT_EXIST) return [];
      throw e;
    }
    return items.filter((it) => !it.dir && it.name.endsWith('.ir'))
      .map((it) => ({ file: `${IR_DIR}/${it.name}`, name: remoteName(it.name), size: it.size ?? 0 }))
      .sort((a, b) => a.name.localeCompare(b.name));
  });
}

/** ir_buttons：一个遥控器（file）的按钮；不给 file 就是 /ext/infrared 里每个遥控器（最多 40 个）。 */
export function irButtons(args: { file?: string } = {}): Promise<{ remotes: { file: string; buttons?: string[]; error?: string }[] }> {
  return run(async (s) => {
    if (args.file) {
      const path = irFile(args.file);
      return { remotes: [{ file: path, buttons: parseIr(await text(s, path)).map((b) => b.name) }] };
    }
    const remotes: { file: string; buttons?: string[]; error?: string }[] = [];
    let items: FileItem[] = [];
    try {
      items = await list(s, IR_DIR);
    } catch (e) {
      if (e instanceof RpcError && e.status === Status.ERROR_STORAGE_NOT_EXIST) return { remotes };
      throw e;
    }
    for (const it of items) {
      if (it.dir || !it.name.endsWith('.ir') || remotes.length >= 40) continue;
      const path = `${IR_DIR}/${it.name}`;
      try {
        remotes.push({ file: path, buttons: parseIr(await text(s, path)).map((b) => b.name) });
      } catch (e) {
        const err = explain(e);
        if (err.code === 'closed' || err.code === 'timeout') throw err;
        remotes.push({ file: path, error: err.message });
      }
    }
    return { remotes };
  });
}

/**
 * 开一个应用的 RPC 模式跑 fn，跑完（成没成）都退出：应用开着时设备的 loader 是锁着的，别的都做不了。
 * 开：app_start(name, "RPC") 回 OK，应用另外主动发 APP_STARTED，两个先后不定，等齐（官方 iOS app 也是等状态）。
 * 回 17 = 设备上开着别的应用；先试一次 app_exit：如果是这条会话上次没退出的（比如发到一半断了）就能退掉再开，
 * 用户自己在 Flipper 上开着的应用 app_exit 管不到（回 21），照样报忙。
 */
async function withApp<T>(s: Session, app: string, fn: () => Promise<T>): Promise<T> {
  let opened = false;
  const start = async (retry: boolean): Promise<void> => {
    let started = false;
    let wake: (() => void) | null = null;
    const off = s.rpc.onEvent((m) => {
      if (m.field === F.appStateResponse && decodeAppState(m.content) === AppState.STARTED) {
        started = true;
        wake?.();
      }
    });
    try {
      try {
        await s.rpc.call(req.appStart(app, 'RPC'), { timeoutMs: 8000 });
      } catch (e) {
        if (retry && e instanceof RpcError && e.status === Status.ERROR_APP_SYSTEM_LOCKED) {
          const exited = await s.rpc.call(req.appExit(), { timeoutMs: 4000 }).then(() => true, () => false);
          if (exited) {
            off();
            await sleep(400);
            return start(false);
          }
        }
        throw e;
      }
      opened = true;
      if (!started) {
        await new Promise<void>((resolve, reject) => {
          const timer = setTimeout(() => { wake = null; reject(new RpcError(TIMEOUT)); }, 5000);
          wake = () => { clearTimeout(timer); resolve(); };
        });
      }
    } finally {
      off();
    }
  };
  try {
    await start(true);
    return await fn();
  } finally {
    if (opened) await s.rpc.call(req.appExit(), { timeoutMs: 4000 }).catch(() => {});
  }
}

/** 按一下：0.25 起一条 app_button_press_release（75）；更早的固件按下 → 至少 500 ms → 松开（官方 iOS app 的做法）。 */
async function pressOnce(s: Session, b: Button): Promise<void> {
  if (s.proto.major > 0 || s.proto.minor >= 25) {
    try {
      await s.rpc.call(req.appButtonPressRelease(b), { timeoutMs: 6000 });
      return;
    } catch (e) {
      if (!(e instanceof RpcError && e.status === Status.ERROR_NOT_IMPLEMENTED)) throw e;
    }
  }
  await s.rpc.call(req.appButtonPress(b), { timeoutMs: 6000 });
  try {
    await sleep(500);
  } finally {
    // 按下是连续发射：不管怎样都要松开（松不开的话 withApp 退出应用时也会停）
    await s.rpc.call(req.appButtonRelease(), { timeoutMs: 4000 }).catch(() => {});
  }
}

/**
 * ir_send：发 /ext/infrared 里一个遥控器的一个按钮，一次。按名字发（args = 按钮名，固件找第一个同名的）。
 * 红外应用开 RPC 模式 → 载入文件（不是遥控器文件、载不了回 22）→ 按一下 → 退出。
 */
export function irSend(args: { file?: string; button?: string } = {}): Promise<{ sent: string; file: string }> {
  return run(async (s) => {
    const path = irFile(args.file || '');
    const name = String(args.button ?? '');
    if (!validButton(name)) throw refused(L('按钮名不能为空，最多 64 个字符', 'The button name must be 1–64 characters'));
    await withApp(s, 'Infrared', async () => {
      try {
        await s.rpc.call(req.appLoadFile(path), { timeoutMs: 10_000 });
      } catch (e) {
        if (!(e instanceof RpcError && e.status === Status.ERROR_APP_CMD_ERROR)) throw e;
        const why = await s.rpc.one(req.appGetError(), { timeoutMs: 4000 }).then((m) => decodeAppError(m.content).text.split('\n')[0], () => '');
        throw refused(L(`无法载入遥控器 ${remoteName(path)}${why ? `（${why}）` : ''}`, `Couldn't load the remote ${remoteName(path)}${why ? ` (${why})` : ''}`));
      }
      try {
        await pressOnce(s, { name });
      } catch (e) {
        if (e instanceof RpcError && e.status === Status.ERROR_APP_CMD_ERROR) {
          throw refused(L(`${remoteName(path)} 中没有按钮「${name}」`, `No button "${name}" in ${remoteName(path)}`));
        }
        throw e;
      }
    });
    return { sent: name, file: path };
  });
}

/** ping：振动、背光闪、响一声（找设备 / 确认连着）；开着应用时也能用。 */
export function ping(): Promise<{ pong: true }> {
  return run(async (s) => {
    await s.rpc.call(req.audiovisualAlert());
    return { pong: true as const };
  });
}

const HANDLERS: Record<Action, (args: Record<string, unknown>) => Promise<unknown>> = {
  status: () => status(),
  files: (a) => files({ path: typeof a.path === 'string' ? a.path : undefined }),
  read: (a) => read({ path: typeof a.path === 'string' ? a.path : undefined }),
  ir_buttons: (a) => irButtons({ file: typeof a.file === 'string' ? a.file : undefined }),
  ir_send: (a) => irSend({ file: typeof a.file === 'string' ? a.file : undefined, button: typeof a.button === 'string' ? a.button : undefined }),
  ping: () => ping(),
};

/** 桥的接口：照服务器发来的 {action, args} 做一件事。不认识的、做不了的（ir_learn）一律拒绝。 */
export function handle(action: string, args: Record<string, unknown> = {}): Promise<unknown> {
  if (!(ACTIONS as readonly string[]).includes(action)) {
    return Promise.reject(refused(action === 'ir_learn'
      ? L('手机经蓝牙无法学习遥控器（RPC 没有红外接收），请使用电脑上的桥', "The phone can't learn a remote over Bluetooth (RPC has no IR receive); use the bridge on a computer")
      : L(`手机不执行 ${action}`, `The phone doesn't do ${action}`)));
  }
  return HANDLERS[action as Action](args);
}
