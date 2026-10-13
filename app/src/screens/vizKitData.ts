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
