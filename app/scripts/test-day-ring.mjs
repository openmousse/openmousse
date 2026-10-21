// node scripts/test-day-ring.mjs（node 22 起直接剥类型跑 .ts）
// 一天环：时刻 → 角度，时段 → 弧，04:00 以前内圈、以后外圈；此刻跟着设备的钟走。
import assert from 'node:assert/strict';
import {
  INNER_RATIO, RING_INNER_R, RING_R,
  arcPath, clockMinute, dayHasRolled, dotAt, formatHHMM, insetArc, liveMinute, minuteToAngle, msUntilNextMinute,
  nowMinute, parseHHMM, polar, spanToArcs, zoneOffset,
} from '../src/components/today/ring.ts';

// 设备的钟按伦敦算：夏令时那一条要它，别的几条在哪个时区跑都一样
process.env.TZ = 'Europe/London';

let passed = 0;
function ok(name, fn) {
  fn();
  passed++;
  console.log(`ok  ${name}`);
}
const near = (a, b, eps = 0.001) => Math.abs(a - b) < eps;
const same = (a, b) => {
  assert.equal(a.length, b.length);
  a.forEach((x, i) => {
    assert.equal(x.lane, b[i].lane);
    assert.ok(near(x.start, b[i].start), `${x.start} vs ${b[i].start}`);
    assert.ok(near(x.sweep, b[i].sweep), `${x.sweep} vs ${b[i].sweep}`);
  });
};

console.log('angle');
ok('0 点在顶上，整点落到整刻', () => {
  assert.equal(minuteToAngle(0), 0);
  assert.equal(minuteToAngle(90), 22.5);
  assert.equal(minuteToAngle(360), 90);
  assert.equal(minuteToAngle(720), 180);
  assert.equal(minuteToAngle(1080), 270);
  assert.equal(minuteToAngle(1440), 0);
  assert.equal(minuteToAngle(-60), 345);
});
ok('0° 在顶上，90° 在右边', () => {
  const top = polar(100, 100, 50, 0);
  const right = polar(100, 100, 50, 90);
  assert.ok(near(top.x, 100) && near(top.y, 50), top);
  assert.ok(near(right.x, 150) && near(right.y, 100), right);
});
ok('一段 90° 的路径和样例一致', () => {
  assert.equal(arcPath(100, 100, 50, 0, 90), 'M100 50A50 50 0 0 1 150 100');
});
ok('一整圈收成画得动的弧', () => {
  const d = arcPath(100, 100, 50, 0, 360);
  assert.ok(d.startsWith('M100 50A50 50 0 1 1 '), d);
});

console.log('clock');
ok('钟点', () => {
  assert.equal(parseHHMM('3:05'), 185);
  assert.equal(parseHHMM('24:00'), 1440);
  assert.equal(parseHHMM('24:01'), null);
  assert.equal(parseHHMM('25:00'), null);
  assert.equal(parseHHMM('12:60'), null);
  assert.equal(parseHHMM(''), null);
  assert.equal(formatHHMM(0), '00:00');
  assert.equal(formatHHMM(1439), '23:59');
});

console.log('arcs');
ok('9:00–10:30 在外圈', () => {
  same(spanToArcs(540, 630, true), [{ lane: 'outer', start: 135, sweep: 22.5 }]);
});
ok('0:30–2:00 没过 04:00 在内圈，过了就在外圈', () => {
  same(spanToArcs(30, 120, false), [{ lane: 'inner', start: 7.5, sweep: 22.5 }]);
  same(spanToArcs(30, 120, true), [{ lane: 'outer', start: 7.5, sweep: 22.5 }]);
});
ok('3:00–5:00 没过 04:00 切开，过了是一整段外圈', () => {
  same(spanToArcs(180, 300, false), [
    { lane: 'inner', start: 45, sweep: 15 },
    { lane: 'outer', start: 60, sweep: 15 },
  ]);
  same(spanToArcs(180, 300, true), [{ lane: 'outer', start: 45, sweep: 30 }]);
});
ok('22:00–23:30 在外圈', () => {
  same(spanToArcs(1320, 1410, false), [{ lane: 'outer', start: 330, sweep: 22.5 }]);
});
ok('23:00–1:00 过午夜，凌晨落在内圈或外圈', () => {
  same(spanToArcs(1380, 60, false), [
    { lane: 'outer', start: 345, sweep: 15 },
    { lane: 'inner', start: 0, sweep: 15 },
  ]);
  same(spanToArcs(1380, 60, true), [
    { lane: 'outer', start: 345, sweep: 15 },
    { lane: 'outer', start: 0, sweep: 15 },
  ]);
});
ok('从 04:00 起就是外圈', () => {
  same(spanToArcs(240, 300, false), [{ lane: 'outer', start: 60, sweep: 15 }]);
});
ok('空的、不是数的，不画', () => {
  assert.deepEqual(spanToArcs(10, 10, true), []);
  assert.deepEqual(spanToArcs(0, 0, false), []);
  assert.deepEqual(spanToArcs(Number.NaN, 30, false), []);
  assert.deepEqual(spanToArcs(30, Number.NaN, true), []);
});
ok('一整天留在外圈，不会被折成空的', () => {
  same(spanToArcs(0, 1440, true), [{ lane: 'outer', start: 0, sweep: 360 }]);
});
ok('内圈是外圈的 120/140', () => {
  assert.equal(INNER_RATIO, 120 / 140);
  assert.ok(near(RING_INNER_R / RING_R, 120 / 140));
});
ok('短弧不留缝，长弧两边各让 1.2°', () => {
  assert.deepEqual(insetArc(0, 1), { start: 0, sweep: 1 });
  const g = insetArc(135, 22.5);
  assert.ok(near(g.start, 136.2) && near(g.sweep, 20.1), g);
});

console.log('dot');
ok('青点：没有、内圈、04:00 在外圈', () => {
  assert.equal(dotAt(null, false), null);
  assert.deepEqual(dotAt(90, false), { lane: 'inner', angle: 22.5 });
  assert.deepEqual(dotAt(0, false), { lane: 'inner', angle: 0 });
  assert.deepEqual(dotAt(240, true), { lane: 'outer', angle: 60 });
  assert.deepEqual(dotAt(240, false), { lane: 'outer', angle: 60 });
});
ok('不是今天、或已过 04:00，环已经收成一圈', () => {
  assert.equal(dayHasRolled(100, false), true);
  assert.equal(dayHasRolled(null, true), true);
  assert.equal(dayHasRolled(239, true), false);
  assert.equal(dayHasRolled(240, true), true);
  assert.equal(dayHasRolled(0, true), false);
});

console.log('live');
const local = (h, m, s = 0) => new Date(2026, 9, 21, h, m, s).getTime();
ok('跟状态栏同时翻分钟：读到时刻那一下在分钟中间也一样', () => {
  const at = local(10, 15, 50);  // 10:15:50 读到，服务器说 10:15
  assert.equal(liveMinute(615, at, at), 615);
  assert.equal(liveMinute(615, at, at + 9_999), 615);
  assert.equal(liveMinute(615, at, at + 10_000), 616);  // 10:16:00，不是 10:16:50
  assert.equal(liveMinute(615, at, at + 70_000), 617);
});
ok('服务器和设备不在一个时区：差几刻钟照加，几秒、一分钟的出入不算', () => {
  const at = local(9, 0, 20);
  assert.equal(zoneOffset(840, at), 300);  // 服务器 14:00，设备 09:00
  assert.equal(liveMinute(840, at, at + 61 * 60_000), 901);
  assert.equal(zoneOffset(539, at), 0);  // 服务器那一分钟往下取整
  assert.equal(zoneOffset(541, at), 0);
  assert.equal(zoneOffset(540 - 345, at), -345);  // 45 分的时区
  assert.equal(zoneOffset(1430, local(0, 5)), -15);  // 跨午夜取近的那边
  assert.equal(zoneOffset(600, 0), 0);
});
ok('过了服务器的午夜、还没重读到第二天：停在 23:59', () => {
  const at = local(23, 50);
  assert.equal(liveMinute(1430, at, at + 9 * 60_000 + 59_000), 1439);
  assert.equal(liveMinute(1430, at, at + 10 * 60_000), 1439);  // 设备 00:00
  assert.equal(liveMinute(1430, at, at + 3 * 3_600_000), 1439);
  const late = local(23, 59, 55);  // 设备比服务器快几秒，先过了 0 点
  assert.equal(liveMinute(1439, late, late + 10_000), 1439);
});
ok('夏令时那一夜跟着设备的钟回拨，不再快一小时', () => {
  const at = Date.UTC(2026, 9, 25, 0, 30);  // 伦敦 01:30 BST；02:00 BST 回拨成 01:00 GMT
  assert.equal(clockMinute(at), 90);
  assert.equal(liveMinute(90, at, at + 40 * 60_000), 70);  // 01:10 GMT，不是 02:10
});
ok('没有新读到的时刻（缓存）：用设备的钟，连设备的时刻也没有就用服务器给的', () => {
  assert.equal(liveMinute(600, 0, local(10, 30)), 600);
  assert.equal(nowMinute(600, 0, local(10, 30)), 630);
  assert.equal(nowMinute(600, 0, 0), 600);
  assert.equal(nowMinute(null, 0, local(7, 5)), 425);
  assert.equal(nowMinute(615, local(10, 15, 50), local(10, 16)), 616);
});
ok('下一格对准墙上的整分', () => {
  assert.equal(msUntilNextMinute(1000), 59000);
  assert.equal(msUntilNextMinute(60_000), 60000);
  assert.equal(msUntilNextMinute(119_999), 1);
});

console.log(`\n${passed} passed`);
