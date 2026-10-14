// Flipper Zero 的 protobuf RPC（协议 0.25）：手写的最小编解码，加一个按 command_id 收发的客户端。
// 依据 flipperzero-protobuf@1c84fa4（flipper.proto 等）和固件 1.4.3 的 applications/services/rpc/：
// - 每条消息 = varint32 长度 + 一个 PB.Main（rpc.c 用 PB_DECODE_DELIMITED / PB_ENCODE_DELIMITED）
// - Main：command_id = 1（我们给，非 0，每段回复原样带回）、command_status = 2、has_next = 3、其余字段号是 oneof 的内容
// - 多段回复（设备信息、电量、文件列表、读文件）除最后一段都带 has_next；command_id = 0 的是设备自己发的
//   （应用状态 58、解码错误的 Empty）
// - 发出一个解不开的字节，固件回 ERROR_DECODE 并关掉会话，蓝牙上还会重启串口、把连接断掉：所以只编下面确实要用的几种请求，
//   字段号和线上字节核对过（scripts/test-flipper-rpc.mjs）
// 不依赖 react-native，也不用 enum / 参数属性：node 能直接跑（类型剥离）。

export type Bytes = number[];

// —— 字段号（flipper.proto 的 Main.content 和各子消息） ——

export const F = {
  empty: 4,
  ping: 5, pingResponse: 6,
  storageList: 7, storageListResponse: 8,
  storageRead: 9, storageReadResponse: 10,
  appStart: 16,
  stopSession: 19,
  storageStat: 24, storageStatResponse: 25,
  storageInfo: 28, storageInfoResponse: 29,
  deviceInfo: 32, deviceInfoResponse: 33,
  audiovisualAlert: 38,
  protobufVersion: 39, protobufVersionResponse: 40,
  powerInfo: 44, powerInfoResponse: 45,
  appExit: 47, appLoadFile: 48, appButtonPress: 49, appButtonRelease: 50,
  appStateResponse: 58,
  appGetError: 63, appGetErrorResponse: 64,
  appButtonPressRelease: 75,  // 0.25 起：按一下发一次
} as const;

/** CommandStatus（flipper.proto）。 */
export const Status = {
  OK: 0,
  ERROR: 1,
  ERROR_DECODE: 2,
  ERROR_NOT_IMPLEMENTED: 3,
  ERROR_BUSY: 4,
  ERROR_STORAGE_NOT_READY: 5,
  ERROR_STORAGE_EXIST: 6,
  ERROR_STORAGE_NOT_EXIST: 7,
  ERROR_STORAGE_INVALID_PARAMETER: 8,
  ERROR_STORAGE_DENIED: 9,
  ERROR_STORAGE_INVALID_NAME: 10,
  ERROR_STORAGE_INTERNAL: 11,
  ERROR_STORAGE_NOT_IMPLEMENTED: 12,
  ERROR_STORAGE_ALREADY_OPEN: 13,
  ERROR_CONTINUOUS_COMMAND_INTERRUPTED: 14,
  ERROR_INVALID_PARAMETERS: 15,
  ERROR_APP_CANT_START: 16,
  ERROR_APP_SYSTEM_LOCKED: 17,  // 设备上开着别的应用
  ERROR_STORAGE_DIR_NOT_EMPTY: 18,
  ERROR_APP_NOT_RUNNING: 21,
  ERROR_APP_CMD_ERROR: 22,
} as const;

/** 客户端自己的「状态」：不是设备回的，是等不到 / 连接没了。 */
export const TIMEOUT = -1;
export const CLOSED = -2;

export function statusName(code: number): string {
  if (code === TIMEOUT) return 'TIMEOUT';
  if (code === CLOSED) return 'CLOSED';
  for (const [k, v] of Object.entries(Status)) if (v === code) return k;
  return `STATUS_${code}`;
}

export const AppState = { CLOSED: 0, STARTED: 1 } as const;

// —— 编码 ——

/** 非负整数的 varint（uint32 / 非负 int32 / bool / 枚举都够用）。 */
export function varint(n: number): Bytes {
  if (!Number.isInteger(n) || n < 0 || n > 0xffffffff) throw new RangeError(`varint out of range: ${n}`);
  const out: Bytes = [];
  let v = n;
  while (v >= 0x80) {
    out.push((v % 0x80) | 0x80);
    v = Math.floor(v / 0x80);
  }
  out.push(v);
  return out;
}

/** UTF-8（Hermes 不一定有 TextEncoder / TextDecoder，自己写）。路径和按钮名在上层已限定 ASCII。 */
export function utf8(s: string): Bytes {
  const out: Bytes = [];
  for (const ch of s) {
    const c = ch.codePointAt(0) ?? 0xfffd;
    if (c < 0x80) out.push(c);
    else if (c < 0x800) out.push(0xc0 | (c >> 6), 0x80 | (c & 63));
    else if (c < 0x10000) out.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
    else out.push(0xf0 | (c >> 18), 0x80 | ((c >> 12) & 63), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
  }
  return out;
}

export function utf8Decode(b: Uint8Array): string {
  const cps: number[] = [];
  let s = '';
  for (let i = 0; i < b.length;) {
    const c = b[i++];
    let cp = 0xfffd;
    if (c < 0x80) cp = c;
    else if (c >= 0xc0 && c < 0xe0 && i < b.length) cp = ((c & 31) << 6) | (b[i++] & 63);
    else if (c >= 0xe0 && c < 0xf0 && i + 1 < b.length) cp = ((c & 15) << 12) | ((b[i++] & 63) << 6) | (b[i++] & 63);
    else if (c >= 0xf0 && c < 0xf8 && i + 2 < b.length) cp = ((c & 7) << 18) | ((b[i++] & 63) << 12) | ((b[i++] & 63) << 6) | (b[i++] & 63);
    cps.push(cp > 0x10ffff ? 0xfffd : cp);
    if (cps.length >= 4096) { s += String.fromCodePoint(...cps); cps.length = 0; }
  }
  return s + String.fromCodePoint(...cps);
}

const tag = (field: number, wireType: number): Bytes => varint(field * 8 + wireType);

/** 标量字段（wire type 0）。proto3：0 / false / 没给 就不写。 */
export function fUint(field: number, v: number | boolean | undefined | null): Bytes {
  const n = v === true ? 1 : typeof v === 'number' ? v : 0;
  return n ? [...tag(field, 0), ...varint(n)] : [];
}

/** bytes 字段（wire type 2）；空的不写。 */
export function fBytes(field: number, v: ArrayLike<number> | undefined | null): Bytes {
  return v && v.length ? [...tag(field, 2), ...varint(v.length), ...Array.from(v)] : [];
}

/** string 字段；空串不写。 */
export function fStr(field: number, s: string | undefined | null): Bytes {
  return s ? fBytes(field, utf8(s)) : [];
}

/** 子消息字段：空的也要写（oneof 靠它知道是哪种请求，比如 DeviceInfoRequest = 82 02 00）。 */
export function fMsg(field: number, body: Bytes): Bytes {
  return [...tag(field, 2), ...varint(body.length), ...body];
}

/** 一条请求：Main 里 oneof 的字段号 + 子消息的内容。 */
export interface Req { field: number; body: Bytes }

/** Main：command_id，has_next（只有分段写文件用），再是内容。command_status 请求里不带。 */
export function encodeMain(m: { commandId: number; hasNext?: boolean; field: number; body?: Bytes }): Uint8Array {
  return Uint8Array.from([...fUint(1, m.commandId), ...fUint(3, !!m.hasNext), ...fMsg(m.field, m.body ?? [])]);
}

/** 加上 varint 长度前缀，就是线上发的一帧。 */
export function frame(main: Uint8Array): Uint8Array {
  const len = varint(main.length);
  const out = new Uint8Array(len.length + main.length);
  out.set(len);
  out.set(main, len.length);
  return out;
}

/** 红外按钮：按名字（args）或按位置（index，文件里第几个 name:，从 0 起）。固件 args 非空就用 args，否则用 index。 */
export type Button = { name: string } | { index: number };

function buttonBody(b: Button): Bytes {
  if ('name' in b) return fStr(1, b.name);
  if (!Number.isInteger(b.index) || b.index < 0) throw new RangeError('button index');
  return fUint(2, b.index);
}

/** 请求构造（字段号见 F；子消息的字段见各行）。 */
export const req = {
  /** PingRequest{bytes data = 1}，回 PingResponse 原样带回。 */
  ping: (data: Bytes = []): Req => ({ field: F.ping, body: fBytes(1, data) }),
  /** 回 {major = 1, minor = 2}，现行固件是 0.25。 */
  protobufVersion: (): Req => ({ field: F.protobufVersion, body: [] }),
  /** 一条一个 key / value（键用 _ 分隔），最后一条 has_next = false。 */
  deviceInfo: (): Req => ({ field: F.deviceInfo, body: [] }),
  powerInfo: (): Req => ({ field: F.powerInfo, body: [] }),
  /** 振动、背光闪、响一声；开着应用时也能用。 */
  audiovisualAlert: (): Req => ({ field: F.audiovisualAlert, body: [] }),
  /** {path = 1} → {uint64 total_space = 1; uint64 free_space = 2} */
  storageInfo: (path: string): Req => ({ field: F.storageInfo, body: fStr(1, path) }),
  /** {path = 1} → {File file = 1} */
  storageStat: (path: string): Req => ({ field: F.storageStat, body: fStr(1, path) }),
  /** {path = 1; bool include_md5 = 2; uint32 filter_max_size = 3}，每段最多 8 个 File，分段带 has_next。 */
  storageList: (path: string, o: { md5?: boolean; maxSize?: number } = {}): Req =>
    ({ field: F.storageList, body: [...fStr(1, path), ...fUint(2, !!o.md5), ...fUint(3, o.maxSize)] }),
  /** {path = 1}，每段 File.data 最多 512 字节，分段带 has_next。 */
  storageRead: (path: string): Req => ({ field: F.storageRead, body: fStr(1, path) }),
  /** StartRequest{name = 1; args = 2}；红外用 ("Infrared", "RPC")。 */
  appStart: (name: string, args = ''): Req => ({ field: F.appStart, body: [...fStr(1, name), ...fStr(2, args)] }),
  appLoadFile: (path: string): Req => ({ field: F.appLoadFile, body: fStr(1, path) }),
  /** 按下：开始连续发射，要再发 release 停。 */
  appButtonPress: (b: Button): Req => ({ field: F.appButtonPress, body: buttonBody(b) }),
  appButtonRelease: (): Req => ({ field: F.appButtonRelease, body: [] }),
  /** 按一下：发一次就回到已载入（0.25 起）。 */
  appButtonPressRelease: (b: Button): Req => ({ field: F.appButtonPressRelease, body: buttonBody(b) }),
  appExit: (): Req => ({ field: F.appExit, body: [] }),
  /** → {uint32 code = 1; string text = 2}：载入失败等的原因。 */
  appGetError: (): Req => ({ field: F.appGetError, body: [] }),
  /** 结束会话（USB 上用来回到命令行；蓝牙上不用，断开连接就行）。 */
  stopSession: (): Req => ({ field: F.stopSession, body: [] }),
};

// —— 解码 ——

/** 读一个 varint：[值, 下一个位置]；字节还没到齐返回 null。超过 2^53 的部分不准（这里用不到）。 */
export function readVarint(b: Uint8Array, pos: number): [number, number] | null {
  let v = 0;
  let mul = 1;
  for (let i = pos; i < b.length && i < pos + 10; i++) {
    v += (b[i] & 0x7f) * mul;
    if (!(b[i] & 0x80)) return [v, i + 1];
    mul *= 0x80;
  }
  if (b.length >= pos + 10) throw new Error('varint too long');
  return null;
}

export interface Field { no: number; wt: number; int: number; bytes: Uint8Array | null }

/** 拆一个消息的所有字段。wire type 0 / 1 / 2 / 5；组（3 / 4）和截断的一律当坏消息。 */
export function parseFields(b: Uint8Array): Field[] {
  const out: Field[] = [];
  let pos = 0;
  const need = (r: [number, number] | null): [number, number] => {
    if (!r) throw new Error('truncated message');
    return r;
  };
  while (pos < b.length) {
    const [key, p1] = need(readVarint(b, pos));
    const no = Math.floor(key / 8);
    const wt = key % 8;
    if (no === 0) throw new Error('field 0');
    pos = p1;
    if (wt === 0) {
      const [v, p2] = need(readVarint(b, pos));
      out.push({ no, wt, int: v, bytes: null });
      pos = p2;
    } else if (wt === 2) {
      const [len, p2] = need(readVarint(b, pos));
      if (p2 + len > b.length) throw new Error('truncated field');
      out.push({ no, wt, int: 0, bytes: b.subarray(p2, p2 + len) });
      pos = p2 + len;
    } else if (wt === 1 || wt === 5) {
      const n = wt === 1 ? 8 : 4;
      if (pos + n > b.length) throw new Error('truncated field');
      out.push({ no, wt, int: 0, bytes: b.subarray(pos, pos + n) });
      pos += n;
    } else {
      throw new Error(`wire type ${wt}`);
    }
  }
  return out;
}

const EMPTY = new Uint8Array(0);

/** 收到的一条 Main。field = oneof 内容的字段号（4 = Empty），content = 它的字节。 */
export interface Msg { commandId: number; status: number; hasNext: boolean; field: number; content: Uint8Array }

export function decodeMain(body: Uint8Array): Msg {
  const m: Msg = { commandId: 0, status: 0, hasNext: false, field: 0, content: EMPTY };
  for (const f of parseFields(body)) {
    if (f.no === 1 && f.wt === 0) m.commandId = f.int;
    else if (f.no === 2 && f.wt === 0) m.status = f.int;
    else if (f.no === 3 && f.wt === 0) m.hasNext = f.int !== 0;
    else if (f.no >= 4 && f.wt === 2 && f.bytes) { m.field = f.no; m.content = f.bytes; }
  }
  return m;
}

const str = (f: Field | undefined) => (f?.bytes ? utf8Decode(f.bytes) : '');
const pick = (fields: Field[], no: number) => fields.find((f) => f.no === no);

/** DeviceInfoResponse / PowerInfoResponse / PropertyGetResponse：{key = 1; value = 2}。 */
export function decodeKeyValue(content: Uint8Array): { key: string; value: string } {
  const fs = parseFields(content);
  return { key: str(pick(fs, 1)), value: str(pick(fs, 2)) };
}

export function decodeVersion(content: Uint8Array): { major: number; minor: number } {
  const fs = parseFields(content);
  return { major: pick(fs, 1)?.int ?? 0, minor: pick(fs, 2)?.int ?? 0 };
}

export interface PbFile { type: 'file' | 'dir'; name: string; size: number; data: Uint8Array; md5: string }

/** File{FileType type = 1 (FILE = 0, DIR = 1); name = 2; uint32 size = 3; bytes data = 4; md5sum = 5}。 */
export function decodeFile(content: Uint8Array): PbFile {
  const fs = parseFields(content);
  return {
    type: pick(fs, 1)?.int === 1 ? 'dir' : 'file', name: str(pick(fs, 2)), size: pick(fs, 3)?.int ?? 0,
    data: pick(fs, 4)?.bytes ?? EMPTY, md5: str(pick(fs, 5)),
  };
}

/** ListResponse{repeated File file = 1}。 */
export function decodeFileList(content: Uint8Array): PbFile[] {
  return parseFields(content).filter((f) => f.no === 1 && f.bytes).map((f) => decodeFile(f.bytes as Uint8Array));
}

/** ReadResponse / StatResponse：{File file = 1}。 */
export function decodeFileOf(content: Uint8Array): PbFile | null {
  const f = pick(parseFields(content), 1);
  return f?.bytes ? decodeFile(f.bytes) : null;
}

export function decodeStorageInfo(content: Uint8Array): { total: number; free: number } {
  const fs = parseFields(content);
  return { total: pick(fs, 1)?.int ?? 0, free: pick(fs, 2)?.int ?? 0 };
}

/** AppStateResponse{AppState state = 1}（APP_CLOSED = 0, APP_STARTED = 1）。 */
export function decodeAppState(content: Uint8Array): number {
  return pick(parseFields(content), 1)?.int ?? 0;
}

export function decodeAppError(content: Uint8Array): { code: number; text: string } {
  const fs = parseFields(content);
  return { code: pick(fs, 1)?.int ?? 0, text: str(pick(fs, 2)) };
}

export function decodePing(content: Uint8Array): Uint8Array {
  return pick(parseFields(content), 1)?.bytes ?? EMPTY;
}

// —— 拆帧：蓝牙一次送来的可能是半条，也可能是几条 ——

const MAX_MESSAGE = 64 * 1024;  // 固件单条最大也就几百字节（读文件每段 512），超过这么多一定是错位了

export class FrameReader {
  buf: Uint8Array = EMPTY;

  /** 喂一段字节，返回凑齐了的每条 Main 的字节。错位（长度离谱）抛错，调用的人该断开重连。 */
  push(chunk: ArrayLike<number>): Uint8Array[] {
    const merged = new Uint8Array(this.buf.length + chunk.length);
    merged.set(this.buf);
    merged.set(chunk, this.buf.length);
    const out: Uint8Array[] = [];
    let pos = 0;
    for (;;) {
      const r = readVarint(merged, pos);
      if (!r) break;
      const [len, start] = r;
      if (len > MAX_MESSAGE) throw new Error(`frame too long: ${len}`);
      if (start + len > merged.length) break;
      out.push(merged.slice(start, start + len));
      pos = start + len;
    }
    this.buf = merged.slice(pos);
    return out;
  }
}

// —— 客户端 ——

/** 底下的传输（蓝牙串口；以后也可以是 USB）。send 自己管流控和分包。 */
export interface Transport {
  send(bytes: Uint8Array): Promise<void>;
  onData(cb: (chunk: ArrayLike<number>) => void): () => void;
  onClose(cb: (reason: string) => void): () => void;
}

export class RpcError extends Error {
  status: number;
  constructor(status: number, message?: string) {
    super(message ?? statusName(status));
    this.name = 'RpcError';
    this.status = status;
  }
}

interface Pending {
  id: number;
  parts: Msg[];
  timeoutMs: number;
  timer: ReturnType<typeof setTimeout> | null;
  resolve: (parts: Msg[]) => void;
  reject: (e: Error) => void;
}

/**
 * 一次只有一条请求在路上（排队）：固件的缓冲只有 1024 字节，官方 app 也是一条一条发。
 * call() 收齐所有分段（has_next）再返回；状态不是 OK 就抛 RpcError；每收到一段，超时重新计。
 * command_id = 0 的（应用状态、解码错误）交给 onEvent；解码错误后固件会关会话，这边也跟着关。
 */
export class RpcClient {
  transport: Transport;
  timeoutMs: number;
  closed = false;
  closeReason = '';
  nextId = 1;
  pending: Pending | null = null;
  chain: Promise<unknown> = Promise.resolve();
  reader = new FrameReader();
  events = new Set<(m: Msg) => void>();
  offs: (() => void)[] = [];

  constructor(transport: Transport, opts: { timeoutMs?: number } = {}) {
    this.transport = transport;
    this.timeoutMs = opts.timeoutMs ?? 8000;
    this.offs.push(transport.onData((chunk) => this.receive(chunk)));
    this.offs.push(transport.onClose((reason) => this.close(reason)));
  }

  /** 设备主动发来的消息（command_id = 0）。返回取消订阅。 */
  onEvent(cb: (m: Msg) => void): () => void {
    this.events.add(cb);
    return () => { this.events.delete(cb); };
  }

  call(r: Req, opts: { timeoutMs?: number } = {}): Promise<Msg[]> {
    const run = () => this.send(r, opts.timeoutMs ?? this.timeoutMs);
    const p = this.chain.then(run, run);
    this.chain = p.catch(() => {});
    return p;
  }

  /** call 的单段版：只要第一段（或唯一一段）。 */
  async one(r: Req, opts: { timeoutMs?: number } = {}): Promise<Msg> {
    return (await this.call(r, opts))[0];
  }

  close(reason = 'closed'): void {
    if (this.closed) return;
    this.closed = true;
    this.closeReason = reason;
    for (const off of this.offs) off();
    this.offs = [];
    this.fail(new RpcError(CLOSED, reason));
  }

  takeId(): number {
    const id = this.nextId;
    this.nextId = this.nextId >= 0xffffffff ? 1 : this.nextId + 1;
    return id;
  }

  send(r: Req, timeoutMs: number): Promise<Msg[]> {
    if (this.closed) return Promise.reject(new RpcError(CLOSED, this.closeReason));
    const id = this.takeId();
    return new Promise<Msg[]>((resolve, reject) => {
      const p: Pending = { id, parts: [], timeoutMs, timer: null, resolve, reject };
      this.pending = p;
      this.arm(p);
      const bytes = frame(encodeMain({ commandId: id, field: r.field, body: r.body }));
      Promise.resolve().then(() => this.transport.send(bytes)).catch((e: unknown) => {
        if (this.pending === p) this.settle(p, e instanceof Error ? e : new Error(String(e)));
      });
    });
  }

  arm(p: Pending): void {
    if (p.timer) clearTimeout(p.timer);
    p.timer = setTimeout(() => { if (this.pending === p) this.settle(p, new RpcError(TIMEOUT)); }, p.timeoutMs);
  }

  settle(p: Pending, err: Error | null): void {
    if (p.timer) clearTimeout(p.timer);
    p.timer = null;
    if (this.pending === p) this.pending = null;
    if (err) p.reject(err);
    else p.resolve(p.parts);
  }

  fail(err: Error): void {
    if (this.pending) this.settle(this.pending, err);
  }

  receive(chunk: ArrayLike<number>): void {
    if (this.closed) return;
    let bodies: Uint8Array[];
    try {
      bodies = this.reader.push(chunk);
    } catch (e) {
      this.close(`bad stream: ${e instanceof Error ? e.message : String(e)}`);
      return;
    }
    for (const body of bodies) {
      let m: Msg;
      try {
        m = decodeMain(body);
      } catch {
        continue;  // 一条解不开的跳过：长度前缀对得上，后面的还能接着读
      }
      this.dispatch(m);
      if (this.closed) return;
    }
  }

  dispatch(m: Msg): void {
    if (m.commandId === 0) {
      for (const cb of [...this.events]) {
        try { cb(m); } catch { /* 订阅的人自己的错 */ }
      }
      if (m.status === Status.ERROR_DECODE) this.close('device reported a decode error');
      return;
    }
    const p = this.pending;
    if (!p || p.id !== m.commandId) return;  // 超时以后才到的旧回复
    if (m.status !== Status.OK) { this.settle(p, new RpcError(m.status)); return; }
    p.parts.push(m);
    if (m.hasNext) this.arm(p);
    else this.settle(p, null);
  }
}
