// node scripts/test-mark.mjs（node 22 起直接剥类型跑 .ts）
// 标志的几何要和设计系统 SVG 对上：环半径、线宽、1 点半的青点。
import assert from 'node:assert/strict';
import { MARK_DOT_R, MARK_R, MARK_STROKE, markMetrics } from '../src/components/markGeom.ts';

let passed = 0;
function ok(name, fn) {
  fn();
  passed++;
  console.log(`ok  ${name}`);
}
const near = (a, b, eps = 0.02) => Math.abs(a - b) < eps;

console.log('mark');
ok('120 的画布和 SVG 同一个点', () => {
  const m = markMetrics(120);
  assert.equal(m.ringR, MARK_R);
  assert.equal(m.stroke, MARK_STROKE);
  assert.equal(m.dotR, MARK_DOT_R);
  assert.ok(near(m.dotX, 86.87), m.dotX);
  assert.ok(near(m.dotY, 33.13), m.dotY);
  assert.ok(near(m.stroke, 0.3 * m.ringR));
  assert.ok(near(m.dotR, 0.26 * m.ringR));
});
ok('点心压在环的中线上', () => {
  const m = markMetrics(88);
  const dx = m.dotX - 44;
  const dy = m.dotY - 44;
  assert.ok(near(Math.hypot(dx, dy), m.ringR, 0.001), Math.hypot(dx, dy));
});
ok('缩小以后比例不变', () => {
  const m = markMetrics(60);
  assert.ok(near(m.ringR, 19));
  assert.ok(near(m.stroke, 5.7));
  assert.ok(near(m.dotR, 4.94));
});

console.log(`\n${passed} passed`);
