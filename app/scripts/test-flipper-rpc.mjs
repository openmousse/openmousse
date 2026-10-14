// node scripts/test-flipper-rpc.mjs（node 24 起直接剥类型跑 .ts；包没写 "type" 时会提示一次 MODULE_TYPELESS_PACKAGE_JSON，不影响）
// 核对 src/hardware/flipperRpc.ts：编码对照 Flipper 协议调研 3.4 节里核过的每一条线上字节（用 PBPY 的 flipper_pb2 核过；
// 字段 75 那两条是同一个编码器算的），再测解码、拆帧和客户端（分段、主动消息、超时、错误码）。不加新依赖。
import assert from 'node:assert/strict';
import {
  AppState, CLOSED, F, FrameReader, RpcClient, RpcError, Status, TIMEOUT,
  decodeAppState, decodeFileList, decodeFileOf, decodeKeyValue, decodeMain, decodeStorageInfo, decodeVersion,
  encodeMain, fBytes, fMsg, fStr, fUint, frame, parseFields, readVarint, req, utf8, utf8Decode, varint,
} from '../src/hardware/flipperRpc.ts';

let passed = 0;
const hex = (b) => Array.from(b, (x) => x.toString(16).padStart(2, '0')).join(' ');
function check(name, got, want) {
  assert.equal(hex(got), want, name);
  passed++;
  console.log(`ok  ${name.padEnd(48)} ${want}`);
}
const framed = (id, r) => frame(encodeMain({ commandId: id, field: r.field, body: r.body }));

// —— 3.4 Wire bytes (checked)：一条不落 ——
console.log('3.4 wire bytes');
check('DeviceInfoRequest, id=1', framed(1, req.deviceInfo()), '05 08 01 82 02 00');
check('StartRequest("Infrared","RPC"), id=2', framed(2, req.appStart('Infrared', 'RPC')),
  '14 08 02 82 01 0f 0a 08 49 6e 66 72 61 72 65 64 12 03 52 50 43');
check('AppButtonPressRequest(args="Power"), id=4', framed(4, req.appButtonPress({ name: 'Power' })), '0c 08 04 8a 03 07 0a 05 50 6f 77 65 72');
check('AppButtonPressReleaseRequest("Power"), id=8', framed(8, req.appButtonPressRelease({ name: 'Power' })), '0c 08 08 da 04 07 0a 05 50 6f 77 65 72');
check('AppButtonPressReleaseRequest(index=3), id=9', framed(9, req.appButtonPressRelease({ index: 3 })), '07 08 09 da 04 02 10 03');
check('AppExitRequest, id=10', framed(10, req.appExit()), '05 08 0a fa 02 00');
check('StopSession, id=5 (unframed body)', encodeMain({ commandId: 5, ...req.stopSession() }), '08 05 9a 01 00');

// —— 其余请求：字段号对照 3.3（手算） ——
console.log('\nother requests (field numbers from 3.3)');
check('ProtobufVersionRequest (39), id=3', framed(3, req.protobufVersion()), '05 08 03 ba 02 00');
check('PowerInfoRequest (44), id=6', framed(6, req.powerInfo()), '05 08 06 e2 02 00');
check('AudiovisualAlert (38), id=7', framed(7, req.audiovisualAlert()), '05 08 07 b2 02 00');
check('Ping data=01 02 (5), id=11', framed(11, req.ping([1, 2])), '08 08 0b 2a 04 0a 02 01 02');
check('StorageList("/ext/infrared") (7), id=12', framed(12, req.storageList('/ext/infrared')),
  '13 08 0c 3a 0f 0a 0d 2f 65 78 74 2f 69 6e 66 72 61 72 65 64');
check('StorageRead("/ext/a.ir") (9), id=13', framed(13, req.storageRead('/ext/a.ir')), '0f 08 0d 4a 0b 0a 09 2f 65 78 74 2f 61 2e 69 72');
check('StorageInfo("/ext") (28), id=14', framed(14, req.storageInfo('/ext')), '0b 08 0e e2 01 06 0a 04 2f 65 78 74');
check('StorageStat("/ext") (24), id=15', framed(15, req.storageStat('/ext')), '0b 08 0f c2 01 06 0a 04 2f 65 78 74');
check('AppLoadFile("/ext/a.ir") (48), id=16', framed(16, req.appLoadFile('/ext/a.ir')), '10 08 10 82 03 0b 0a 09 2f 65 78 74 2f 61 2e 69 72');
check('AppButtonRelease (50), id=17', framed(17, req.appButtonRelease()), '05 08 11 92 03 00');
check('AppGetError (63), id=18', framed(18, req.appGetError()), '05 08 12 fa 03 00');
check('PressRelease(index=0) = empty body, id=19', framed(19, req.appButtonPressRelease({ index: 0 })), '05 08 13 da 04 00');
check('has_next on a request, id=20', frame(encodeMain({ commandId: 20, hasNext: true, field: F.ping, body: [] })), '06 08 14 18 01 2a 00');
check('command id 300 (two-byte varint)', framed(300, req.deviceInfo()), '06 08 ac 02 82 02 00');

// —— varint / UTF-8 ——
console.log('\nvarint / utf8');
for (const n of [0, 1, 127, 128, 300, 16383, 16384, 0xffffffff]) {
  const v = Uint8Array.from(varint(n));
  assert.deepEqual(readVarint(v, 0), [n, v.length], `varint ${n}`);
  assert.equal(readVarint(v.subarray(0, v.length - 1), 0), null, `partial varint ${n}`);
  passed++;
}
assert.throws(() => varint(-1));
assert.throws(() => varint(1.5));
for (const s of ['Power', 'Vol_up', 'Ünïcødé ✓', '电视 开关', '😀 emoji']) {
  assert.equal(utf8Decode(Uint8Array.from(utf8(s))), s, `utf8 ${s}`);
  passed++;
}
assert.equal(hex(utf8('é✓😀')), 'c3 a9 e2 9c 93 f0 9f 98 80');
passed++;
console.log('ok  varint and utf8 round trips');

// —— 解码：照固件的样子编几条回复，再解回来 ——
console.log('\ndecode round trips');
// 回复用到的编码：Main{command_id, command_status, has_next, content}
const reply = (id, field, body, { status = 0, hasNext = false } = {}) =>
  frame(Uint8Array.from([...fUint(1, id), ...fUint(2, status), ...fUint(3, hasNext), ...fMsg(field, body)]));
const kv = (k, v) => [...fStr(1, k), ...fStr(2, v)];
const file = (type, name, size, data) => [...fUint(1, type), ...fStr(2, name), ...fUint(3, size), ...fBytes(4, data)];

{
  const m = decodeMain(reply(7, F.deviceInfoResponse, kv('hardware_name', 'Abc'), { hasNext: true }).subarray(1));
  assert.deepEqual([m.commandId, m.status, m.hasNext, m.field], [7, 0, true, F.deviceInfoResponse]);
  assert.deepEqual(decodeKeyValue(m.content), { key: 'hardware_name', value: 'Abc' });
  const v = decodeMain(reply(8, F.protobufVersionResponse, [...fUint(1, 0), ...fUint(2, 25)]).subarray(1));
  assert.deepEqual(decodeVersion(v.content), { major: 0, minor: 25 });
  const l = decodeMain(reply(9, F.storageListResponse, [...fMsg(1, file(1, 'assets', 0)), ...fMsg(1, file(0, 'TV.ir', 1234))]).subarray(1));
  assert.deepEqual(decodeFileList(l.content).map((f) => [f.type, f.name, f.size]), [['dir', 'assets', 0], ['file', 'TV.ir', 1234]]);
  const r = decodeMain(reply(10, F.storageReadResponse, fMsg(1, file(0, '', 0, utf8('name: Power\n')))).subarray(1));
  assert.equal(utf8Decode(decodeFileOf(r.content).data), 'name: Power\n');
  // uint64 超过 32 位（SD 卡容量）
  const big = 64 * 1024 ** 3;
  const enc64 = (n) => { const o = []; let x = n; while (x >= 128) { o.push((x % 128) | 128); x = Math.floor(x / 128); } o.push(x); return o; };
  const si = decodeMain(reply(11, F.storageInfoResponse, [0x08, ...enc64(big), 0x10, ...enc64(big / 2)]).subarray(1));
  assert.deepEqual(decodeStorageInfo(si.content), { total: big, free: big / 2 });
  const st = decodeMain(reply(0, F.appStateResponse, fUint(1, AppState.STARTED)).subarray(1));
  assert.deepEqual([st.commandId, decodeAppState(st.content)], [0, AppState.STARTED]);
  const e = decodeMain(reply(12, F.empty, [], { status: Status.ERROR_APP_SYSTEM_LOCKED }).subarray(1));
  assert.deepEqual([e.status, e.field, e.content.length], [17, F.empty, 0]);
  // 不认识的字段 / wire type 1、5 跳过；组（3）是坏消息
  assert.equal(parseFields(Uint8Array.from([0x09, 1, 2, 3, 4, 5, 6, 7, 8, 0x15, 1, 2, 3, 4])).length, 2);
  assert.throws(() => parseFields(Uint8Array.from([0x0b])));
  assert.throws(() => parseFields(Uint8Array.from([0x0a, 0x05, 1])));
  passed += 12;
  console.log('ok  Main, key/value, version, file list, read data, uint64, app state, status, unknown fields');
}

// —— 拆帧：任意切开都能拼回来 ——
{
  const stream = Uint8Array.from([
    ...reply(1, F.deviceInfoResponse, kv('a', '1'), { hasNext: true }),
    ...reply(1, F.deviceInfoResponse, kv('b', 'x'.repeat(300))),  // 两字节长度前缀
    ...reply(0, F.appStateResponse, fUint(1, 1)),
  ]);
  for (let cut = 0; cut <= stream.length; cut++) {
    const fr = new FrameReader();
    const got = [...fr.push(stream.subarray(0, cut)), ...fr.push(stream.subarray(cut))];
    assert.equal(got.length, 3, `cut at ${cut}`);
    assert.equal(fr.buf.length, 0);
  }
  const fr = new FrameReader();
  const one = [];
  for (const b of stream) one.push(...fr.push([b]));
  assert.equal(one.length, 3);
  assert.throws(() => new FrameReader().push([0xff, 0xff, 0x7f]), /too long/);
  passed += 3;
  console.log('ok  frame reader: every split point, byte by byte, oversize length');
}

// —— 客户端：假传输 ——
function fakeTransport() {
  const data = new Set();
  const close = new Set();
  const t = {
    sent: [],
    async send(b) { t.sent.push(b); t.onSend?.(b); },
    onData(cb) { data.add(cb); return () => data.delete(cb); },
    onClose(cb) { close.add(cb); return () => close.delete(cb); },
    feed(b, split = 3) { for (let i = 0; i < b.length; i += split) for (const cb of [...data]) cb(b.subarray(i, i + split)); },
    drop(why) { for (const cb of [...close]) cb(why); },
  };
  return t;
}
const sentMain = (b) => decodeMain(b.subarray(readVarint(b, 0)[1]));
const tick = () => new Promise((r) => setTimeout(r, 0));

console.log('\nclient');
{
  const t = fakeTransport();
  const c = new RpcClient(t, { timeoutMs: 200 });
  const events = [];
  c.onEvent((m) => events.push(m));
  // 设备信息分三段回，中间夹一条主动消息；id 从 1 起
  t.onSend = (b) => {
    const m = sentMain(b);
    if (m.field === F.deviceInfo) {
      t.feed(Uint8Array.from([
        ...reply(m.commandId, F.deviceInfoResponse, kv('hardware_name', 'Abc'), { hasNext: true }),
        ...reply(0, F.appStateResponse, fUint(1, 1)),
        ...reply(m.commandId, F.deviceInfoResponse, kv('firmware_version', '1.4.3'), { hasNext: true }),
        ...reply(m.commandId, F.deviceInfoResponse, kv('protobuf_version_minor', '25')),
      ]), 5);
    } else if (m.field === F.appStart) {
      t.feed(reply(m.commandId, F.empty, [], { status: Status.ERROR_APP_SYSTEM_LOCKED }));
    } else if (m.field === F.ping) {
      t.feed(reply(m.commandId + 1000, F.pingResponse, []));  // 别人的 id：不算
    }
  };
  const parts = await c.call(req.deviceInfo());
  assert.deepEqual(parts.map((p) => decodeKeyValue(p.content).key), ['hardware_name', 'firmware_version', 'protobuf_version_minor']);
  assert.equal(sentMain(t.sent[0]).commandId, 1);
  assert.equal(events.length, 1);
  assert.equal(decodeAppState(events[0].content), 1);
  await assert.rejects(c.call(req.appStart('Infrared', 'RPC')), (e) => e instanceof RpcError && e.status === 17);
  assert.equal(sentMain(t.sent[1]).commandId, 2);
  await assert.rejects(c.call(req.ping(), { timeoutMs: 50 }), (e) => e instanceof RpcError && e.status === TIMEOUT);
  // 排队：前一条没回之前不发下一条
  t.onSend = null;
  const a = c.call(req.powerInfo());
  const b2 = c.call(req.audiovisualAlert());
  await tick();
  assert.equal(t.sent.length, 4);
  t.feed(reply(sentMain(t.sent[3]).commandId, F.powerInfoResponse, kv('charge_level', '87')));
  await a;
  await tick();
  assert.equal(t.sent.length, 5);
  t.feed(reply(sentMain(t.sent[4]).commandId, F.empty, []));
  assert.equal((await b2)[0].field, F.empty);
  // 连接断了：在路上的和后来的都失败
  const c3 = c.call(req.deviceInfo());
  await tick();
  t.drop('ble disconnected');
  await assert.rejects(c3, (e) => e instanceof RpcError && e.status === CLOSED);
  await assert.rejects(c.call(req.ping()), (e) => e instanceof RpcError && e.status === CLOSED);
  passed += 10;
  console.log('ok  ids from 1, multi-part reassembly, unsolicited events, status 17, foreign ids, timeout, queueing, close');
}
{
  // 设备报解码错误（command_id 0 + ERROR_DECODE）：会话没了，客户端跟着关
  const t = fakeTransport();
  const c = new RpcClient(t, { timeoutMs: 500 });
  const p = c.call(req.ping());
  await tick();
  t.feed(reply(0, F.empty, [], { status: Status.ERROR_DECODE }));
  await assert.rejects(p, (e) => e instanceof RpcError && e.status === CLOSED);
  assert.equal(c.closed, true);
  // 流里长度离谱：错位了，关掉
  const t2 = fakeTransport();
  const c2 = new RpcClient(t2);
  t2.feed(Uint8Array.from([0xff, 0xff, 0xff, 0x0f]));
  assert.equal(c2.closed, true);
  passed += 3;
  console.log('ok  device decode error and a garbled stream close the client');
}

console.log(`\nall ${passed} checks passed`);
