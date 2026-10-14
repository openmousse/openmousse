// 蓝牙（1.0.6）：react-native-ble-manager 的薄封装，只做连 Flipper Zero 用得到的：启动、蓝牙状态、安卓权限、扫描、
// 连接 + 发现服务 + 订阅 + 带流控的写、断开，以及事件订阅的清理。
// 原生模块只在手机上按需加载（require 写在 Platform.OS !== 'web' 的分支里，网页打包时整段删掉）；
// 1.0.6 以前的安装包里没有这个模块，bleAvailable() 为 false，页面据此提示要 1.0.6。
//
// Flipper 的蓝牙串口服务（固件 1.4.3 targets/f7/ble_glue/services/serial_service.c；官方 iOS / Android app 用的同一组 UUID）：
// - 手机从 …61fe… 收（Flipper 发出，INDICATE），往 …62fe… 写（带回应写，和官方 iOS app 一样）。名字按手机这一侧取：
//   Flipper 源码叫它们 TX / RX，正好反过来
// - 流控 …63fe…：4 字节大端 uint32 = 还能写多少字节。连上时是 1024（RPC 缓冲大小），每写一次自己减；
//   减到 0 以后，固件把缓冲读空时把额度重置成 1024 并通知，收到通知才接着写。写超了固件只记一条日志，但会丢数据
// - 广播里只有 16 位的 0x3080 | 机身颜色（3081 黑、3082 白、3083 透明），没有串口服务的 128 位 UUID，
//   所以扫描不能按串口服务过滤：不加过滤扫，在 JS 里按这几个 16 位 UUID 或名字「Flipper …」挑
// - 串口特征要求加密 + 认证：第一次访问时系统弹配对框，Flipper 屏幕上显示 6 位配对码。一次只能连一台手机
import { Platform } from 'react-native';
import type { BleScanMode, Peripheral } from 'react-native-ble-manager';
import { L } from '../lang';

type Manager = typeof import('react-native-ble-manager').default;

export const SERIAL_SERVICE = '8fe5b3d5-2e7f-4a98-2a48-7acc60fe0000';
/** Flipper → 手机（固件里的 TX，READ + INDICATE）。 */
export const CHAR_IN = '19ed82ae-ed21-4c9d-4145-228e61fe0000';
/** 手机 → Flipper（固件里的 RX，WRITE / WRITE_WITHOUT_RESPONSE）。 */
export const CHAR_OUT = '19ed82ae-ed21-4c9d-4145-228e62fe0000';
/** 流控：大端 uint32，还能写的字节数（READ + NOTIFY）。 */
export const CHAR_FLOW = '19ed82ae-ed21-4c9d-4145-228e63fe0000';
/** RPC 状态（1 = 会话开着）。写 0 会让 Flipper 重启串口：这里只读不写。 */
export const CHAR_RPC_STATUS = '19ed82ae-ed21-4c9d-4145-228e64fe0000';

/** 广播里的 0x3080 | 颜色。iOS 报短的「3082」，安卓报完整的 00003082-0000-1000-8000-00805f9b34fb。 */
const FLIPPER_ADV = /^(0000)?308[0-3](-0000-1000-8000-00805f9b34fb)?$/i;

const CONNECT_MS = 15_000;  // iOS 的 connect 自己不会超时（设备不在附近就一直等）
const SECURE_MS = 60_000;   // 留够输入配对码的时间
const FLOW_WAIT_MS = 4_000; // 额度用完后等通知；等不到就读一次流控特征

// —— 原生模块 ——

let mgr: Manager | null | undefined;

function manager(): Manager | null {
  if (mgr !== undefined) return mgr;
  mgr = null;
  if (Platform.OS !== 'web') {
    try {
      // 先看安装包里有没有这个原生模块：库的入口在没有时会直接抛错（1.0.6 以前的包）
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const { TurboModuleRegistry } = require('react-native') as typeof import('react-native');
      if (TurboModuleRegistry.get('BleManager')) {
        // eslint-disable-next-line @typescript-eslint/no-require-imports
        mgr = (require('react-native-ble-manager') as typeof import('react-native-ble-manager')).default;
      }
    } catch {
      mgr = null;
    }
  }
  return mgr;
}

/** 这台设备上的 app 能不能用蓝牙（网页版、1.0.6 以前的包：不能）。 */
export function bleAvailable(): boolean {
  return !!manager();
}

function need(): Manager {
  const m = manager();
  if (!m) throw new Error(L('当前版本不支持蓝牙', 'This version has no Bluetooth support'));
  return m;
}

// —— 事件：原生那边每种只挂一个监听，分发给这里的订阅者 ——

export type BtState = 'on' | 'off' | 'unauthorized' | 'unsupported' | 'unknown' | 'resetting' | 'turning_on' | 'turning_off';

const valueSubs = new Set<(peripheral: string, characteristic: string, value: number[]) => void>();
const disconnectSubs = new Set<(peripheral: string) => void>();
const stateSubs = new Set<(s: BtState) => void>();
const discoverSubs = new Set<(p: Peripheral) => void>();
const stopScanSubs = new Set<() => void>();
let wired: { remove(): void }[] = [];

function each<A extends unknown[]>(subs: Set<(...a: A) => void>, ...args: A) {
  for (const f of [...subs]) {
    try { f(...args); } catch { /* 订阅的人自己的错，不影响别人 */ }
  }
}

function wire(m: Manager) {
  if (wired.length) return;
  wired = [
    m.onDidUpdateValueForCharacteristic((e) => each(valueSubs, String(e.peripheral).toLowerCase(), String(e.characteristic).toLowerCase(), e.value)),
    m.onDisconnectPeripheral((e) => each(disconnectSubs, String(e.peripheral).toLowerCase())),
    m.onDidUpdateState((e) => each(stateSubs, e.state as BtState)),
    m.onDiscoverPeripheral((p) => each(discoverSubs, p)),
    m.onStopScan(() => each(stopScanSubs)),
  ];
}

/** 摘掉原生那边的监听（下次 bleStart 再挂上）。 */
export function bleShutdown(): void {
  for (const s of wired) s.remove();
  wired = [];
}

let started: Promise<void> | null = null;

/**
 * 启动（只启动一次）。iOS 上这一步建 CBCentralManager，第一次会弹蓝牙权限框：所以只在进硬件页时才调，不在 app 启动时。
 */
export function bleStart(): Promise<void> {
  const m = need();
  if (!started) {
    started = m.start({ showAlert: false }).catch((e: unknown) => {
      started = null;
      throw e instanceof Error ? e : new Error(String(e));
    });
  }
  return started.then(() => wire(m));
}

export async function bleState(): Promise<BtState> {
  await bleStart();
  return (await need().checkState()) as BtState;
}

export function onBleState(cb: (s: BtState) => void): () => void {
  stateSubs.add(cb);
  return () => { stateSubs.delete(cb); };
}

/** 安卓：弹系统框打开蓝牙。iOS 没有这个，得用户自己去控制中心开。 */
export async function bleEnable(): Promise<void> {
  if (Platform.OS !== 'android') return;
  await bleStart();
  await need().enableBluetooth();
}

// —— 安卓运行时权限 ——

export type Permission = 'granted' | 'denied' | 'blocked';

/**
 * 安卓 12 起要 BLUETOOTH_SCAN + BLUETOOTH_CONNECT（扫描声明了 neverForLocation，不要定位）。
 * 安卓 11 及以下没有这两个权限，扫描要 ACCESS_FINE_LOCATION：插件只给 SDK ≤ 30 声明了它，正好是这些系统。
 * iOS 不用这里：系统在第一次用蓝牙时自己问，被拒了 bleState() 是 unauthorized。
 * request = false 只查不问。
 */
export async function blePermission(request = true): Promise<Permission> {
  if (Platform.OS !== 'android') return 'granted';
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { PermissionsAndroid } = require('react-native') as typeof import('react-native');
  const P = PermissionsAndroid.PERMISSIONS;
  const want = Number(Platform.Version) >= 31 ? [P.BLUETOOTH_SCAN, P.BLUETOOTH_CONNECT] : [P.ACCESS_FINE_LOCATION];
  const has = await Promise.all(want.map((p) => PermissionsAndroid.check(p)));
  if (has.every(Boolean)) return 'granted';
  if (!request) return 'denied';
  const res = await PermissionsAndroid.requestMultiple(want);
  const got = want.map((p) => res[p]);
  if (got.every((v) => v === PermissionsAndroid.RESULTS.GRANTED)) return 'granted';
  return got.some((v) => v === PermissionsAndroid.RESULTS.NEVER_ASK_AGAIN) ? 'blocked' : 'denied';
}

// —— 扫描 ——

export interface Found { id: string; name: string; rssi: number }

export function isFlipper(p: Peripheral): boolean {
  if ((p.advertising?.serviceUUIDs ?? []).some((u) => FLIPPER_ADV.test(u))) return true;
  return /^flipper\s/i.test(p.advertising?.localName || p.name || '');
}

/**
 * 扫附近的 Flipper，seconds 秒后自己停。每发现一台（或信号变了）调 onFound；停了调 onDone。
 * 返回停止扫描的函数（离开页面时调）。
 */
export async function scanFlippers(onFound: (d: Found) => void, onDone: () => void, seconds = 10): Promise<() => void> {
  await bleStart();
  const m = need();
  let live = true;
  const finish = () => {
    if (!live) return;
    live = false;
    discoverSubs.delete(found);
    stopScanSubs.delete(finish);
    onDone();
  };
  const found = (p: Peripheral) => {
    if (live && isFlipper(p)) onFound({ id: p.id, name: p.advertising?.localName || p.name || 'Flipper', rssi: p.rssi });
  };
  discoverSubs.add(found);
  stopScanSubs.add(finish);
  try {
    await m.scan({ serviceUUIDs: [], seconds, allowDuplicates: false, scanMode: 2 as BleScanMode /* LowLatency：只扫十秒，在前台 */ });
  } catch (e) {
    finish();
    throw e;
  }
  return () => {
    if (!live) return;
    m.stopScan().catch(() => {});
    finish();
  };
}

// —— 串口连接 ——

/** 中止正在连的（openSerial 随后失败）。 */
export function cancelConnect(id: string): void {
  manager()?.disconnect(id).catch(() => {});
}

/** 一条连着的蓝牙串口：RPC 客户端的传输（flipperRpc.ts 的 Transport）。 */
export interface SerialLink {
  id: string;
  /** 一次写多少字节（安卓 = MTU − 3，iOS = 系统给的不带回应写的上限）。 */
  chunk: number;
  send(bytes: Uint8Array): Promise<void>;
  onData(cb: (chunk: ArrayLike<number>) => void): () => void;
  onClose(cb: (reason: string) => void): () => void;
  close(): Promise<void>;
  isClosed(): boolean;
}

export type Stage = 'connecting' | 'securing';

function timeout<T>(p: Promise<T>, ms: number, message: string, onTimeout?: () => void): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => { onTimeout?.(); reject(new Error(message)); }, ms);
    p.then((v) => { clearTimeout(timer); resolve(v); }, (e: unknown) => { clearTimeout(timer); reject(e instanceof Error ? e : new Error(String(e))); });
  });
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** 配对还没完成时，访问要认证的特征会先失败：隔一会儿再试，直到 deadline。 */
async function retrying<T>(fn: () => Promise<T>, deadline: number): Promise<T> {
  for (;;) {
    try {
      return await fn();
    } catch (e) {
      if (Date.now() + 1500 > deadline) throw e instanceof Error ? e : new Error(String(e));
      await sleep(1500);
    }
  }
}

const be32 = (v: number[]) => (v.length >= 4 ? ((v[0] << 24) >>> 0) + (v[1] << 16) + (v[2] << 8) + v[3] : 0);

/**
 * 连上一台 Flipper 并打开串口：连接 →（安卓）配对 → 发现服务 →（安卓）请求 MTU → 订阅收数据和流控 → 读一次流控。
 * Flipper 那边一连上就自动开 RPC 会话（固件 bt.c），所以返回以后直接说 RPC。失败会断开再抛。
 */
export async function openSerial(id: string, onStage?: (s: Stage) => void): Promise<SerialLink> {
  await bleStart();
  const m = need();
  const key = id.toLowerCase();
  onStage?.('connecting');
  await timeout(m.connect(id), CONNECT_MS, L('连接超时：Flipper 不在附近，或蓝牙已关闭', 'Connection timed out: the Flipper is out of range or its Bluetooth is off'),
    () => { m.disconnect(id).catch(() => {}); });

  let free = 0;
  let chunk = 20;
  let closed = false;
  let wake: (() => void) | null = null;
  const dataSubs = new Set<(c: ArrayLike<number>) => void>();
  const closeSubs = new Set<(reason: string) => void>();

  const onValue = (p: string, c: string, value: number[]) => {
    if (p !== key) return;
    if (c === CHAR_IN) each(dataSubs, value);
    else if (c === CHAR_FLOW) {
      free = be32(value);
      const w = wake;
      wake = null;
      w?.();
    }
  };
  const shut = (reason: string) => {
    if (closed) return;
    closed = true;
    valueSubs.delete(onValue);
    disconnectSubs.delete(onDisconnect);
    const w = wake;
    wake = null;
    w?.();
    each(closeSubs, reason);
  };
  const onDisconnect = (p: string) => { if (p === key) shut(L('连接已断开', 'Disconnected')); };
  valueSubs.add(onValue);
  disconnectSubs.add(onDisconnect);

  try {
    const deadline = Date.now() + SECURE_MS;
    onStage?.('securing');
    if (Platform.OS === 'android') {
      // 安卓先明确配对（系统框里输 Flipper 屏幕上的码）；已经配过的立刻返回。
      // Flipper 自己先发起了配对时 createBond 会报「请求失败」：不算错，下面订阅时等配对完成；只有用户拒绝才停
      await timeout(m.createBond(id), SECURE_MS, L('配对超时', 'Pairing timed out')).catch((e: unknown) => {
        if (/denied|timed out|超时/i.test(e instanceof Error ? e.message : String(e))) throw e;
      });
    }
    await m.retrieveServices(id, [SERIAL_SERVICE]);
    if (Platform.OS === 'android') {
      // Flipper 的 ATT MTU 上限是 414（app_conf.h），协商结果取小的那个
      const mtu = await m.requestMTU(id, 512).catch(() => 23);
      chunk = Math.max(20, mtu - 3);
    } else {
      chunk = Math.max(20, await m.getMaximumWriteValueLengthForWithoutResponse(id).catch(() => 20));
    }
    // iOS 在这里第一次碰到要认证的特征时弹配对框；没配好之前订阅会失败，重试到超时
    await retrying(() => m.startNotification(id, SERIAL_SERVICE, CHAR_IN), deadline);
    await retrying(() => m.startNotification(id, SERIAL_SERVICE, CHAR_FLOW), deadline);
    // 连上时固件把额度设成 1024；之后的通知（如果先到了）也是同一个数
    free = be32(await retrying(() => m.read(id, SERIAL_SERVICE, CHAR_FLOW), deadline));
  } catch (e) {
    shut('setup failed');
    await m.disconnect(id).catch(() => {});
    const why = e instanceof Error ? e.message : String(e);
    // 最常见的原因：Flipper 那边重置过配对，手机还留着旧的
    throw new Error(L(`无法建立加密连接（${why}）。如果 Flipper 重置过配对，先在手机的蓝牙设置中忽略该设备，再重新连接。`,
      `Couldn't set up an encrypted connection (${why}). If the Flipper's pairing was reset, forget it in the phone's Bluetooth settings and connect again.`));
  }

  /** 额度用完：等 Flipper 的通知；等不到就自己读一次（通知可能丢了）。 */
  const waitBudget = async () => {
    const got = await new Promise<boolean>((resolve) => {
      const timer = setTimeout(() => { wake = null; resolve(false); }, FLOW_WAIT_MS);
      wake = () => { clearTimeout(timer); resolve(true); };
    });
    if (closed) throw new Error(L('连接已断开', 'Disconnected'));
    if (!got) free = be32(await m.read(id, SERIAL_SERVICE, CHAR_FLOW));
    if (free <= 0) throw new Error(L('Flipper 没有接收数据', 'The Flipper stopped accepting data'));
  };

  const writeAll = async (bytes: Uint8Array) => {
    let off = 0;
    while (off < bytes.length) {
      if (closed) throw new Error(L('连接已断开', 'Disconnected'));
      if (free <= 0) await waitBudget();
      // 每次写 min(剩下的额度, 一包的上限)：额度正好用到 0，Flipper 才会在读空后通知新的额度
      const n = Math.min(free, chunk, bytes.length - off);
      free -= n;
      await m.write(id, SERIAL_SERVICE, CHAR_OUT, Array.from(bytes.subarray(off, off + n)), n);
      off += n;
    }
  };

  let queue: Promise<void> = Promise.resolve();
  return {
    id,
    chunk,
    send(bytes) {
      const p = queue.then(() => writeAll(bytes));
      queue = p.catch(() => {});
      return p;
    },
    onData(cb) {
      dataSubs.add(cb);
      return () => { dataSubs.delete(cb); };
    },
    onClose(cb) {
      closeSubs.add(cb);
      return () => { closeSubs.delete(cb); };
    },
    async close() {
      if (closed) return;
      shut(L('已断开', 'Disconnected'));
      await m.disconnect(id).catch(() => {});
    },
    isClosed: () => closed,
  };
}
