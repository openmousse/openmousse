// 可视化素材库展示页的示例数据：全部是虚构的（演示用的一位求职中的学生，日期按 2026-10-12 那一天排），不是谁的真实记录。
// 只给展示页用，app 别处不放示例数据。带文字的都写成函数：L() 要在渲染时按当前语言取，模块加载时语言设置还没读出来。
import { L } from '../i18n';

/** 恢复分 9/23–10/12（20 天） */
export const recovery: { date: string; score: number }[] = [
  ['2026-09-23', 52], ['2026-09-24', 61], ['2026-09-25', 57], ['2026-09-26', 63], ['2026-09-27', 58], ['2026-09-28', 69], ['2026-09-29', 66],
  ['2026-09-30', 51], ['2026-10-01', 74], ['2026-10-02', 70], ['2026-10-03', 44], ['2026-10-04', 62], ['2026-10-05', 67], ['2026-10-06', 65],
  ['2026-10-07', 76], ['2026-10-08', 72], ['2026-10-09', 60], ['2026-10-10', 49], ['2026-10-11', 73], ['2026-10-12', 68],
].map(([date, score]) => ({ date: date as string, score: score as number }));

/** 睡眠时长 10/7–10/12（小时），目标 7–9 小时 */
export const sleepHours: { date: string; hours: number }[] = [
  ['2026-10-07', 6.8], ['2026-10-08', 7.4], ['2026-10-09', 8.1], ['2026-10-10', 6.2], ['2026-10-11', 9.0], ['2026-10-12', 7.3],
].map(([date, hours]) => ({ date: date as string, hours: hours as number }));

/** 求职看板：可投岗位按截止周 */
export const deadlineWeeks = () => [
  { key: 'w1012', label: L('10/12 周', 'w/c 12 Oct'), value: 6, current: true },
  { key: 'w1019', label: L('10/19 周', 'w/c 19 Oct'), value: 9 },
  { key: 'w1026', label: L('10/26 周', 'w/c 26 Oct'), value: 12 },
  { key: 'w1102', label: L('11/2 周', 'w/c 2 Nov'), value: 4 },
  { key: 'later', label: L('之后', 'Later'), value: 8 },
];
/** 同上，分实习 / 全职 */
export const deadlineByType = { internship: [4, 5, 8, 1, 6], fullTime: [2, 4, 4, 3, 2] };

/** 匹配分分段：有序类别，按类别顺序 */
export const matchBands = () => [
  { key: 'b90', label: L('90 分以上', '90+'), value: 1 },
  { key: 'b80', label: '80–89', value: 6 },
  { key: 'b70', label: '70–79', value: 9 },
  { key: 'b60', label: '60–69', value: 13 },
  { key: 'b50', label: '50–59', value: 10 },
];

/** 岗位地区 */
export const regions = () => [
  { key: 'na', label: L('北美', 'North America'), value: 14 },
  { key: 'europe', label: L('欧洲', 'Europe'), value: 12 },
  { key: 'apac', label: L('亚太', 'APAC'), value: 10 },
  { key: 'other', label: L('其他', 'Other'), value: 3, slot: -1 },
];

/** 岗位类型 */
export const jobTypes = () => [
  { key: 'intern', label: L('实习', 'Internship'), value: 23 },
  { key: 'full', label: L('全职', 'Full-time'), value: 16 },
];

/** 申请表（截止按日期排） */
export const applications = () => [
  { id: 'a1', org: 'Northwind Capital', role: 'Investment Analyst Program', state: L('撰写中', 'Drafting'), tone: 'warn', due: '2026-10-16' },
  { id: 'a2', org: 'Halcyon Trading', role: 'Trading Internship 2027', state: L('撰写中', 'Drafting'), tone: 'warn', due: '2026-10-18' },
  { id: 'a3', org: 'Meridian Labs', role: 'AI Research Fellowship', state: L('已投递', 'Submitted'), tone: 'cyan', due: '2027-02-28' },
  { id: 'a4', org: 'Aster University', role: 'MSc Data Science', state: L('计划', 'Planned'), tone: 'neutral', due: '2026-11-03' },
];

/** 一次 Pull A：每个动作做了几组 */
export const pullA = () => [
  { label: L('硬拉 60 kg × 5', 'Deadlift 60 kg × 5'), done: 3, total: 3 },
  { label: L('高位下拉 40 kg × 10', 'Lat pulldown 40 kg × 10'), done: 4, total: 4 },
  { label: L('杠铃划船 40 kg × 10', 'Barbell row 40 kg × 10'), done: 3, total: 3 },
];

/** M/D（示例里的日期标签） */
export const md = (iso: string) => `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))}`;

// —— 第二批 ——

/** 10 月的日历：日程 / 截止 / 待办 */
export const octoberMarks: Record<string, string[]> = {
  '2026-10-08': ['deadline'], '2026-10-12': ['event', 'event', 'todo'], '2026-10-14': ['todo'], '2026-10-15': ['event'], '2026-10-16': ['deadline'],
  '2026-10-18': ['deadline'], '2026-10-21': ['event'], '2026-10-23': ['deadline'], '2026-10-27': ['todo'], '2026-10-30': ['todo'],
};
export const calendarKinds = () => [
  { key: 'event', label: L('日程', 'Events') },
  { key: 'deadline', label: L('截止', 'Deadlines') },
  { key: 'todo', label: L('待办', 'To-dos') },
];

/** 一天的日程：上午到 17:00 没有安排，现在 20:10 */
export const todayBlocks = () => [
  { key: 'talk', title: L('产品设计讲座', 'Product design talk'), start: '17:00', end: '17:45', sub: L('17:00 · 已购票', '17:00 · ticket booked'), slot: 0 },
  { key: 'pull', title: L('训练 · Pull A', 'Workout · Pull A'), start: '18:20', end: '19:02', sub: L('18:20–19:02 · 原定 13:30', '18:20–19:02 · planned 13:30'), slot: 3 },
];

/** 截止时间线：从 10/12 起 */
export const deadlines = () => [
  { key: 'nw', label: 'Northwind', end: '2026-10-16' },
  { key: 'hal', label: 'Halcyon', end: '2026-10-18' },
  { key: 'grp', label: L('小组作业', 'Group project'), end: '2026-10-23' },
  { key: 'aster', label: 'Aster', end: '2026-11-03' },
  { key: 'grp2', label: L('小组作业 2', 'Group project 2'), end: '2026-11-10' },
];

/** 求职看板（按状态分列） */
export const jobBoard = () => [
  { key: 'new', title: L('新岗位', 'New'), total: 35, cards: [
    { key: 'lumen', sub: 'Lumen Bank', title: L('Quant Research 毕业生项目', 'Quant Research graduate programme'), tags: [{ text: L('匹配 88', 'Match 88'), tone: 'cyan' }, { text: L('欧洲', 'Europe'), tone: 'neutral' }] },
    { key: 'harbor', sub: 'Harbor Square Capital', title: 'Investment Analyst', tags: [{ text: L('匹配 80', 'Match 80'), tone: 'cyan' }, { text: L('北美', 'North America'), tone: 'neutral' }] },
  ] },
  { key: 'draft', title: L('撰写中', 'Drafting'), cards: [
    { key: 'nw', sub: 'Northwind Capital', title: 'Investment Analyst Program', tags: [{ text: L('10/16 截止', 'Due 16 Oct'), tone: 'warn' }] },
    { key: 'hal', sub: 'Halcyon Trading', title: 'Trading Internship 2027', tags: [{ text: L('10/18 截止', 'Due 18 Oct'), tone: 'warn' }] },
  ] },
  { key: 'sent', title: L('已投递', 'Submitted'), cards: [
    { key: 'mer1', sub: 'Meridian Labs', title: 'AI Research Intern', tags: [{ text: L('10/9 已投递', 'Sent 9 Oct'), tone: 'neutral' }] },
    { key: 'mer2', sub: 'Meridian Labs', title: 'AI Research Fellowship 2027', tags: [{ text: L('已投递', 'Sent'), tone: 'neutral' }] },
  ] },
];

/** 练后报告：和上一次（9/29）的 Pull A 比 */
export const pullCompare = () => [
  { key: 'dl', name: L('硬拉', 'Deadlift'), now: '60 kg × 5 × 3', last: '55 kg × 6 × 3', dir: 'up' as const, verdict: L('进步', 'Up'), good: true },
  { key: 'pd', name: L('高位下拉', 'Lat pulldown'), now: '40 kg × 10 × 4', last: '35 kg × 10 × 4', dir: 'up' as const, verdict: L('加重', 'Heavier'), good: true },
  { key: 'row', name: L('杠铃划船', 'Barbell row'), now: '40 kg × 10 × 3', last: '40 kg × 9 × 4', dir: 'flat' as const, verdict: L('持平', 'Level') },
];

/** 晚餐建议（两顿合计） */
export const dinner = { protein: 64, carbs: 90, fat: 22, kcal: 814, proteinTarget: 100 };

/** 一晚的睡眠分期（10/11 23:40 – 10/12 07:10） */
export const sleepNight: { start: string; end: string; stage: 'awake' | 'rem' | 'core' | 'deep' }[] = ([
  ['2026-10-11T23:40', '2026-10-12T00:05', 'core'], ['2026-10-12T00:05', '2026-10-12T00:50', 'deep'], ['2026-10-12T00:50', '2026-10-12T01:30', 'core'],
  ['2026-10-12T01:30', '2026-10-12T01:55', 'rem'], ['2026-10-12T01:55', '2026-10-12T02:40', 'core'], ['2026-10-12T02:40', '2026-10-12T03:05', 'deep'],
  ['2026-10-12T03:05', '2026-10-12T03:50', 'core'], ['2026-10-12T03:50', '2026-10-12T04:25', 'rem'], ['2026-10-12T04:25', '2026-10-12T04:30', 'awake'],
  ['2026-10-12T04:30', '2026-10-12T05:20', 'core'], ['2026-10-12T05:20', '2026-10-12T05:35', 'deep'], ['2026-10-12T05:35', '2026-10-12T06:10', 'core'],
  ['2026-10-12T06:10', '2026-10-12T06:50', 'rem'], ['2026-10-12T06:50', '2026-10-12T07:00', 'core'], ['2026-10-12T07:00', '2026-10-12T07:10', 'awake'],
] as const).map(([start, end, stage]) => ({ start, end, stage }));
export const sleepTotals = { deep: 85, rem: 100, core: 250, awake: 15 };

/** 近 14 天（9/29–10/12）哪天练了 */
export const trainingDays: { date: string; done: boolean }[] = (() => {
  const done = new Set(['2026-09-29', '2026-10-01', '2026-10-03', '2026-10-06', '2026-10-08', '2026-10-11', '2026-10-12']);
  const out: { date: string; done: boolean }[] = [];
  for (let n = Date.UTC(2026, 8, 29); n <= Date.UTC(2026, 9, 12); n += 86400000) {
    const iso = new Date(n).toISOString().slice(0, 10);
    out.push({ date: iso, done: done.has(iso) });
  }
  return out;
})();

/** 年轮：最近 7 天（10/6–10/12）新长的记忆，0 = 今天；day 为 null 的两条还待确认（一条自己说的，一条朋友的 Agent 说的） */
export const memoryDots = () => ([
  [0, L('周三晚上有吉他课', 'Guitar lesson on Wednesday evenings')], [0, L('投行申请写到第二篇文书', 'On the second essay of the bank application')],
  [0, L('不喝含糖饮料', 'Avoids sugary drinks')], [1, L('11 月想去一趟海边', 'Wants a trip to the sea in November')],
  [1, L('导师建议先投两家实习', 'The tutor suggested two internships first')], [2, L('早上 7 点前训练状态最好', 'Trains best before 7 am')],
  [3, L('数据科学硕士放在第二志愿', 'MSc Data Science is the second choice')], [3, L('周末不排面试', 'No interviews on weekends')],
  [3, L('喜欢先看结论再看细节', 'Likes the conclusion before the details')], [5, L('膝盖旧伤，深蹲控制在 80 kg 以内', 'Old knee injury: squats stay under 80 kg')],
  [6, L('室友下个月搬走', 'The flatmate moves out next month')],
  [null, L('下周四有小组展示', 'Group presentation next Thursday')], [null, L('对芒果过敏', 'Allergic to mango')],
] as const).map(([day, label], i) => ({ key: `m${i}`, day, label }));
