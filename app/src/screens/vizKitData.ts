// 可视化素材库展示页的示例数据：都是 2026-10-12 的真实数据（设计稿画布第 7 排同一份）。只给展示页用，app 别处不放示例数据。
// 带文字的都写成函数：L() 要在渲染时按当前语言取，模块加载时语言设置还没读出来。
import { L } from '../i18n';

/** 恢复分 9/23–10/12（20 天，Apple 健康算出来的） */
export const recovery: { date: string; score: number }[] = [
  ['2026-09-23', 48], ['2026-09-24', 63], ['2026-09-25', 59], ['2026-09-26', 54], ['2026-09-27', 59], ['2026-09-28', 66], ['2026-09-29', 64],
  ['2026-09-30', 49], ['2026-10-01', 75], ['2026-10-02', 72], ['2026-10-03', 40], ['2026-10-04', 64], ['2026-10-05', 69], ['2026-10-06', 63],
  ['2026-10-07', 73], ['2026-10-08', 73], ['2026-10-09', 63], ['2026-10-10', 46], ['2026-10-11', 71], ['2026-10-12', 66],
].map(([date, score]) => ({ date: date as string, score: score as number }));

/** 睡眠时长 10/7–10/12（小时），目标 7–9 小时 */
export const sleepHours: { date: string; hours: number }[] = [
  ['2026-10-07', 5.5], ['2026-10-08', 5.4], ['2026-10-09', 8.9], ['2026-10-10', 5.6], ['2026-10-11', 10.4], ['2026-10-12', 8.9],
].map(([date, hours]) => ({ date: date as string, hours: hours as number }));

/** 求职看板：可投岗位按截止周 */
export const deadlineWeeks = () => [
  { key: 'w1012', label: L('10/12 周', 'w/c 12 Oct'), value: 10, current: true },
  { key: 'w1019', label: L('10/19 周', 'w/c 19 Oct'), value: 13 },
  { key: 'w1026', label: L('10/26 周', 'w/c 26 Oct'), value: 17 },
  { key: 'w1102', label: L('11/2 周', 'w/c 2 Nov'), value: 5 },
  { key: 'later', label: L('之后', 'Later'), value: 11 },
];
/** 同上，分实习 / 全职 */
export const deadlineByType = { internship: [6, 5, 11, 2, 9], fullTime: [4, 8, 6, 3, 2] };

/** 匹配分（雷达打分）分段：有序类别，按类别顺序 */
export const matchBands = () => [
  { key: 'b90', label: L('90 分以上', '90+'), value: 2 },
  { key: 'b80', label: '80–89', value: 10 },
  { key: 'b70', label: '70–79', value: 12 },
  { key: 'b60', label: '60–69', value: 20 },
  { key: 'b50', label: '50–59', value: 19 },
];

/** 岗位地区 */
export const regions = () => [
  { key: 'london', label: L('伦敦', 'London'), value: 24 },
  { key: 'apac', label: L('亚太', 'APAC'), value: 24 },
  { key: 'europe', label: L('欧洲', 'Europe'), value: 12 },
  { key: 'other', label: L('其他', 'Other'), value: 3, slot: -1 },
];

/** 岗位类型 */
export const jobTypes = () => [
  { key: 'intern', label: L('实习', 'Internship'), value: 38 },
  { key: 'full', label: L('全职', 'Full-time'), value: 25 },
];

/** 申请表（截止按日期排） */
export const applications = () => [
  { id: 'a1', org: 'Point72', role: 'Academy Investment Analyst', state: L('撰写中', 'Drafting'), tone: 'warn', due: '2026-10-16' },
  { id: 'a2', org: 'Optiver', role: 'QT Internship 2027', state: L('撰写中', 'Drafting'), tone: 'warn', due: '2026-10-18' },
  { id: 'a3', org: 'Dymon Asia', role: 'AI ELS Fellowship', state: L('已投递', 'Submitted'), tone: 'cyan', due: '2027-02-28' },
  { id: 'a4', org: 'Duke Pratt', role: 'MEng FinTech', state: L('计划', 'Planned'), tone: 'neutral', due: '2026-11-03' },
];

/** 10/12 Pull A：每个动作做了几组 */
export const pullA = () => [
  { label: L('硬拉 85 kg × 5', 'Deadlift 85 kg × 5'), done: 3, total: 3 },
  { label: L('辅助引体 25 lbs × 8', 'Assisted pull-up 25 lbs × 8'), done: 4, total: 4 },
  { label: L('杠铃划船 60 kg × 10', 'Barbell row 60 kg × 10'), done: 3, total: 3 },
];

/** M/D（示例里的日期标签） */
export const md = (iso: string) => `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))}`;

// —— 第二批 ——

/** 10 月的日历：日程 / 截止 / 待办（画布同一份） */
export const octoberMarks: Record<string, string[]> = {
  '2026-10-09': ['deadline'], '2026-10-12': ['event', 'event', 'todo'], '2026-10-14': ['todo'], '2026-10-15': ['event'], '2026-10-16': ['deadline'],
  '2026-10-18': ['deadline'], '2026-10-20': ['event'], '2026-10-23': ['deadline'], '2026-10-26': ['todo'], '2026-10-31': ['todo'],
};
export const calendarKinds = () => [
  { key: 'event', label: L('日程', 'Events') },
  { key: 'deadline', label: L('截止', 'Deadlines') },
  { key: 'todo', label: L('待办', 'To-dos') },
];

/** 10/12 的日程（今天页同一份）：上午到 17:00 没有安排，现在 20:10 */
export const todayBlocks = () => [
  { key: 'monolith', title: L('Monolith AI 炉边对话', 'Monolith AI fireside chat'), start: '17:00', end: '17:45', sub: L('17:00 · 已购票', '17:00 · ticket booked'), slot: 0 },
  { key: 'pull', title: L('训练 · Pull A', 'Workout · Pull A'), start: '18:18', end: '18:54', sub: L('18:18–18:54 · 原定 13:30', '18:18–18:54 · planned 13:30'), slot: 3 },
];

/** 截止时间线：从 10/12 起 */
export const deadlines = () => [
  { key: 'p72', label: 'Point72', end: '2026-10-16' },
  { key: 'opt', label: 'Optiver', end: '2026-10-18' },
  { key: 'grp', label: L('小组作业', 'Group project'), end: '2026-10-23' },
  { key: 'duke', label: 'Duke Pratt', end: '2026-11-03' },
  { key: 'grp2', label: L('小组作业 2', 'Group project 2'), end: '2026-11-10' },
];

/** 求职看板（按状态分列） */
export const jobBoard = () => [
  { key: 'new', title: L('新岗位', 'New'), total: 58, cards: [
    { key: 'bnp', sub: 'BNP Paribas', title: L('Quant Research 毕业生项目', 'Quant Research graduate programme'), tags: [{ text: L('匹配 88', 'Match 88'), tone: 'cyan' }, { text: L('伦敦', 'London'), tone: 'neutral' }] },
    { key: 'psq', sub: 'Park Square Capital', title: 'Investment Analyst', tags: [{ text: L('匹配 80', 'Match 80'), tone: 'cyan' }, { text: L('伦敦', 'London'), tone: 'neutral' }] },
  ] },
  { key: 'draft', title: L('撰写中', 'Drafting'), cards: [
    { key: 'p72', sub: 'Point72', title: 'Academy Investment Analyst Program', tags: [{ text: L('10/16 截止', 'Due 16 Oct'), tone: 'warn' }] },
    { key: 'opt', sub: 'Optiver', title: 'Quantitative Trading Internship 2027', tags: [{ text: L('10/18 截止', 'Due 18 Oct'), tone: 'warn' }] },
  ] },
  { key: 'sent', title: L('已投递', 'Submitted'), cards: [
    { key: 'dymon1', sub: 'Dymon Asia', title: 'AI Management Intern', tags: [{ text: L('10/9 已投递', 'Sent 9 Oct'), tone: 'neutral' }] },
    { key: 'dymon2', sub: 'Dymon Asia', title: '2027 AI ELS Fellowship', tags: [{ text: L('已投递', 'Sent'), tone: 'neutral' }] },
  ] },
];

/** 10/12 练后报告：和 9/29 的 Pull A 比 */
export const pullCompare = () => [
  { key: 'dl', name: L('硬拉', 'Deadlift'), now: '85 kg × 5 × 3', last: '80 kg × 6 × 3', dir: 'up' as const, verdict: L('进步', 'Up'), good: true },
  { key: 'pu', name: L('弹力带辅助引体', 'Band-assisted pull-up'), now: '15 / 25 lbs × 8 × 4', last: '25 lbs × 8 × 4', dir: 'up' as const, verdict: L('辅助减少', 'Less assist'), good: true },
  { key: 'row', name: L('杠铃划船', 'Barbell row'), now: '60 kg × 10 × 3', last: '60 kg × 9 × 4', dir: 'flat' as const, verdict: L('持平', 'Level') },
];

/** 10/12 晚餐建议（两顿合计） */
export const dinner = { protein: 83, carbs: 105, fat: 21, kcal: 961, proteinTarget: 136 };

/** 10/12 睡眠（03:03–13:21，Apple Watch 分期）；合计取 Apple 健康的日汇总 */
export const sleepNight: { start: string; end: string; stage: 'awake' | 'rem' | 'core' | 'deep' }[] = ([
  ['03:03', '03:41', 'core'], ['03:41', '04:10', 'deep'], ['04:10', '04:11', 'core'], ['04:11', '04:16', 'deep'], ['04:16', '04:20', 'core'],
  ['04:20', '05:08', 'rem'], ['05:08', '06:26', 'core'], ['06:26', '06:40', 'deep'], ['06:40', '06:44', 'core'], ['06:44', '07:03', 'rem'],
  ['07:03', '07:12', 'core'], ['07:12', '07:12', 'awake'], ['07:12', '07:59', 'core'], ['07:59', '09:03', 'rem'], ['09:03', '09:33', 'core'],
  ['09:33', '10:31', 'awake'], ['10:31', '10:42', 'core'], ['10:42', '10:44', 'awake'], ['10:44', '10:50', 'core'], ['10:50', '11:06', 'rem'],
  ['11:06', '11:55', 'core'], ['11:55', '12:41', 'rem'], ['12:41', '12:53', 'awake'], ['12:53', '13:02', 'core'], ['13:02', '13:04', 'awake'],
  ['13:04', '13:05', 'core'], ['13:05', '13:13', 'awake'], ['13:13', '13:21', 'core'],
] as const).map(([a, b, stage]) => ({ start: `2026-10-12T${a}`, end: `2026-10-12T${b}`, stage }));
export const sleepTotals = { deep: 46, rem: 194, core: 296, awake: 83 };

/** 近 14 天（9/29–10/12）哪天练了（训记） */
export const trainingDays: { date: string; done: boolean }[] = (() => {
  const done = new Set(['2026-09-29', '2026-10-01', '2026-10-02', '2026-10-05', '2026-10-06', '2026-10-11', '2026-10-12']);
  const out: { date: string; done: boolean }[] = [];
  for (let n = Date.UTC(2026, 8, 29); n <= Date.UTC(2026, 9, 12); n += 86400000) {
    const iso = new Date(n).toISOString().slice(0, 10);
    out.push({ date: iso, done: done.has(iso) });
  }
  return out;
})();
