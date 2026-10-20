// node scripts/test-talk.mjs（node 22.18 起直接剥类型跑 .ts）
// 核对对讲的两块纯逻辑：src/talk/text.ts（边流边念怎么切、插话以后念哪段、Markdown 去记号、杂音上编出来的句子）
// 和 src/talk/ears.ts（什么时候算开口、说完、一直没说；念的时候什么音量算主人开口打断）。不加新依赖。
import assert from 'node:assert/strict';
import { common, cut, langOf, misheard, pieces, Reading, speakable } from '../src/talk/text.ts';
import { Barge, Ears } from '../src/talk/ears.ts';

let passed = 0;
function ok(name, fn) {
  fn();
  passed++;
  console.log(`ok  ${name}`);
}
const NOTES = { code: '代码见对话。', table: '表格见对话。', link: '链接' };

/** 模拟边流边念：一个字一个字地来，每来一个字切一次，最后 final。返回每次切出来的原文。 */
function stream(full) {
  const said = [];
  let at = 0;
  for (let n = 1; n <= full.length; n++) {
    const r = cut(full.slice(0, n), at, false);
    if (r.text) { said.push(r.text); at = r.to; }
  }
  const last = cut(full, at, true);
  if (last.text) said.push(last.text);
  return said;
}

console.log('cut');
ok('中文按句号、问号切，一句说完就念', () => {
  assert.deepEqual(stream('今天有雨。记得带伞？好的'), ['今天有雨。', '记得带伞？', '好的']);
});
ok('引号跟着句号一起走', () => {
  assert.deepEqual(stream('它说：「明天见。」然后走了。'), ['它说：「明天见。」', '然后走了。']);
});
ok('英文句点后面要有空白才切，3.14 不切', () => {
  assert.deepEqual(stream('Pi is 3.14 today. Next one.'), ['Pi is 3.14 today.', ' Next one.']);
});
ok('列表序号「1.」不单独念', () => {
  const parts = stream('1. 第一件\n2. 第二件\n');
  assert.ok(parts.every((p) => !/^\d+\.\s*$/.test(p)), JSON.stringify(parts));
  assert.equal(parts.join(''), '1. 第一件\n2. 第二件\n');
});
ok('没关上的代码块先不念，关上以后整块一起', () => {
  const full = '先看这段。\n```python\nprint(1)\nprint(2)\n```\n就这样。';
  const at = cut(full.slice(0, 8), 0, false).to;
  assert.equal(at, 6);  // 「先看这段。\n」
  assert.equal(cut(full.slice(0, full.indexOf('print(2)')), at, false).text, '');  // 代码块里的换行不算
  assert.equal(cut(full.slice(0, full.indexOf('```\n') + 3), at, false).text, '');  // 刚关上、后面还没换行
  const parts = stream(full);
  assert.deepEqual(parts, ['先看这段。\n', '```python\nprint(1)\nprint(2)\n```\n', '就这样。']);
  assert.equal(speakable(parts[1], NOTES), '代码见对话。');
});
ok('表格还在往下长：等表格完了一起', () => {
  const head = '结果如下：\n| 项 | 值 |\n|---|---|\n| 甲 | 1 |\n';
  const r1 = cut(head, 0, false);
  assert.equal(r1.text, '结果如下：\n');
  const r2 = cut(head + '| 乙 | 2 |\n', r1.to, false);
  assert.equal(r2.text, '');
  const full = head + '| 乙 | 2 |\n\n就这些。';
  const r3 = cut(full, r1.to, false);
  assert.equal(speakable(r3.text, NOTES), '表格见对话。');
  assert.equal(cut(full, r3.to, true).text, '就这些。');
});
ok('一直没有句号、攒长了在逗号处先念', () => {
  const long = '这是一段很长的话，'.repeat(20);
  const r = cut(long, 0, false);
  assert.ok(r.text.length > 0 && r.text.endsWith('，'), r.text);
});
ok('回复整段换掉时从相同的开头接着念', () => {
  assert.equal(common('今天有雨。明天', '今天有雪。'), 3);
  assert.equal(common('今天有雨。', '今天有雨。明天'), 5);
});

console.log('Reading');
/** 一个字一个字地喂给 Reading（从第 from 个字起），final 再给一次完整的；返回每次要念的原文 */
function feed(r, full, from = 0, final = true) {
  const said = [];
  for (let n = from + 1; n <= full.length; n++) {
    const x = r.take(full.slice(0, n), false);
    if (x) said.push(x);
  }
  if (final) { const x = r.take(full, true); if (x) said.push(x); }
  return said;
}
ok('平常一问一答：从头念到尾', () => {
  const r = new Reading();
  r.start(undefined);
  assert.deepEqual(feed(r, '今天晴。气温二十度。'), ['今天晴。', '气温二十度。']);
});
ok('回答它的反问（插进同一条回复）：问题念过了，只念后面接着来的，回完也不从头重念', () => {
  const q = '订哪家？\n\n1. 甲\n2. 乙\n\n回个数字、选项的字，或者直接说你的想法。';
  const r = new Reading();
  r.start(undefined);
  const asked = feed(r, q, 0, false).join('');
  assert.ok(!asked.includes('回个数字'), asked);  // 正好停在最后的那句：流着的时候不念
  assert.equal(r.flush(q), '回个数字、选项的字，或者直接说你的想法。');  // 停了一会儿没再长：念掉
  r.start(q);  // 主人回答了（这时流着的就是问题）
  const full = `${q}\n\n好的，订了甲，晚上七点。`;
  const said = feed(r, full, q.length, false);
  r.end();  // 这一轮回完了
  said.push(r.take(full, true));
  assert.equal(said.join(''), '\n\n好的，订了甲，晚上七点。');
  assert.equal(r.shown(full), '\n\n好的，订了甲，晚上七点。');
});
ok('插话没赶上、排成了下一轮：上一轮后面说完的照念，下一轮从头念', () => {
  const r = new Reading();
  const old = '今天晴，气温二十度。明天有雨';
  r.start('今天晴，');  // 上一轮流到这里时插了一句
  const said = feed(r, old, 4, false);
  r.end();  // 上一轮回完了
  said.push(...feed(r, '好的，改成明天。'));
  assert.deepEqual(said, ['气温二十度。', '好的，改成明天。']);
});
ok('回完的整条结尾和流出来的有出入：接着念，不从头重念', () => {
  const r = new Reading();
  r.start('第一段。\n\n');
  const said = feed(r, '第一段。\n\n第二段。 ', 6, false);
  r.end();
  said.push(r.take('第一段。\n\n第二段。\n\n（补的一句。）', true));
  assert.equal(said.join('').replace(/\s+/g, ''), '第二段。（补的一句。）');
});
ok('停住时代码块还没关上：先不念，等它关上整块一起', () => {
  const r = new Reading();
  r.start(undefined);
  const t = '看这段。\n```js\nx()\n';
  assert.deepEqual(feed(r, t, 0, false), ['看这段。\n']);
  assert.equal(r.flush(t), '');
  assert.equal(r.take(`${t}\`\`\`\n就这样。`, true), '```js\nx()\n```\n就这样。');
});

console.log('speakable');
ok('去掉标题、加粗、列表、行内代码，标题补句号', () => {
  assert.equal(speakable('## 安排\n- **上午**：跑步\n- 下午读 `notes.md`', NOTES), '安排。\n上午：跑步。\n下午读 notes.md。');
});
ok('代码块换成一句，网址不念、链接只念字', () => {
  assert.equal(speakable('看 [文档](https://example.com/a) 和 https://example.com/b\n```js\nx()\n```', NOTES), '看 文档 和 链接。\n代码见对话。');
});
ok('合并回复的「> 「原话」」引用行不念，普通引用去掉 >', () => {
  assert.equal(speakable('> 「几点了」\n现在三点。\n> 这是引用', NOTES), '现在三点。\n这是引用。');
});
ok('英文行补英文句点，表情符号去掉', () => {
  assert.equal(speakable('Done 🎉\nAll good', NOTES), 'Done.\nAll good.');
});

console.log('pieces / langOf');
ok('太长的一行按句子拆，每段不超过上限', () => {
  const p = pieces('第一句话。'.repeat(100), 50);
  assert.ok(p.length >= 10 && p.every((x) => x.length <= 50), JSON.stringify(p.slice(0, 3)));
  assert.equal(p.join(''), '第一句话。'.repeat(100));
});
ok('有汉字念中文，没有念英文', () => {
  assert.equal(langOf('明天 9 点 standup'), 'zh');
  assert.equal(langOf('See you at 9.'), 'en');
});

console.log('misheard');
ok('杂音上编出来的句子不发', () => {
  for (const t of ['', '  ', 'Thank you.', 'thank you for watching!', 'You', 'Thank you. Thank you. Thank you.', 'ご視聴ありがとうございました', '字幕由Amara.org社区提供', '请不吝点赞 订阅 转发 打赏支持明镜与点点栏目']) {
    assert.equal(misheard(t), true, t);
  }
});
ok('正常的话照发', () => {
  for (const t of ['明天几点开会？', 'Thank you for the summary, now book it.', '你好', 'Remind me at 5.']) {
    assert.equal(misheard(t), false, t);
  }
});

/** 一串音量喂给耳朵，每次 100 毫秒；返回最后的状态和第几次变的 */
function hear(levels, ears = new Ears()) {
  let state = 'waiting';
  let at = -1;
  for (let i = 0; i < levels.length; i++) {
    const s = ears.feed(levels[i], 100);
    if (s !== state) { state = s; at = i; }
    if (s === 'done' || s === 'nothing') break;
  }
  return { state, at };
}
const run = (db, n) => Array(n).fill(db);

console.log('Ears');
ok('一直安静：15 秒后不等了', () => {
  assert.deepEqual(hear(run(-55, 200)), { state: 'nothing', at: 149 });
});
ok('开口、说两秒、停 1.5 秒：说完', () => {
  const r = hear([...run(-55, 10), ...run(-25, 20), ...run(-55, 30)]);
  assert.equal(r.state, 'done');
  assert.equal(r.at, 10 + 20 + 15 - 1);
});
ok('一下咳嗽（200 毫秒）不算开口', () => {
  const r = hear([...run(-55, 10), ...run(-20, 2), ...run(-55, 20)]);
  assert.equal(r.state, 'waiting');
});
ok('字和字之间的短停顿不算说完', () => {
  const words = [];
  for (let i = 0; i < 10; i++) words.push(...run(-25, 6), ...run(-55, 5));
  const r = hear([...run(-55, 5), ...words, ...run(-55, 20)]);
  assert.equal(r.state, 'done');
  assert.ok(r.at > 5 + words.length, String(r.at));
});
ok('吵的地方底噪跟上去：一直的 -35 dB 不算说话', () => {
  assert.equal(hear(run(-35, 100)).state, 'waiting');
});
ok('一进来就在说（底噪还没量）：字缝里降下来，照样认得出说完', () => {
  const words = [];
  for (let i = 0; i < 6; i++) words.push(...run(-25, 5), ...run(-55, 3));
  assert.equal(hear([...words, ...run(-55, 20)]).state, 'done');
});
ok('念的时候被打断接着录：一停就算说完', () => {
  const r = hear(run(-55, 20), new Ears(true));
  assert.deepEqual(r, { state: 'done', at: 14 });
});
ok('说到最长也算说完', () => {
  assert.equal(hear([...run(-55, 10), ...run(-25, 1000)], new Ears(false, { maxMs: 5000 })).state, 'done');
});

console.log('Barge');
const barge = (levels, b = new Barge()) => levels.findIndex((x) => b.feed(x, 100));
ok('喇叭的声音在 -30…-22 之间起伏：不打断', () => {
  const echo = Array.from({ length: 200 }, (_, i) => -30 + 8 * Math.abs(Math.sin(i / 3)));
  assert.equal(barge(echo), -1);
});
ok('主人开口（比念的声音高出一截、连着 300 毫秒）：打断', () => {
  const echo = Array.from({ length: 50 }, (_, i) => -30 + 6 * Math.abs(Math.sin(i / 3)));
  assert.equal(barge([...echo, ...run(-6, 5)]), 52);
});
ok('一下响声（200 毫秒）不打断', () => {
  assert.equal(barge([...run(-30, 30), ...run(-5, 2), ...run(-30, 30)]), -1);
});
ok('开头 400 毫秒只量不判', () => {
  assert.equal(barge(run(-5, 4)), -1);
});
ok('喇叭开得很响（-8 dB）：门槛封顶，不自己打断', () => {
  assert.equal(barge(run(-8, 100)), -1);
});
ok('朗读引擎晚出声：从开麦就量会把自己打断；从出声起量（loop.ts 等 voice 的 start）不会', () => {
  const room = run(-55, 5);  // 麦开了 500 毫秒，喇叭还没出声
  const voice = Array.from({ length: 100 }, (_, i) => -26 + 4 * Math.sin(i / 2));
  assert.notEqual(barge([...room, ...voice]), -1);  // 底噪当了底：门槛 -30，自己念的 -30…-22 就打断了
  assert.equal(barge(voice), -1);
});

console.log(`\nall ${passed} checks passed`);
