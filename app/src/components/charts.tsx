// 老页面用的图表名字（健身、饮食、睡眠看板，目标页）。2026-10-13 起都转到可视化素材库（src/components/viz/）画，
// 这里只把老参数换成素材库的参数，样子和交互跟素材库一致。新代码直接用素材库。
import React from 'react';
import { L } from '../i18n';
import { useTheme } from '../theme';
import { BarChart, LineChart, ProgressBar, ProgressRing, dayNum } from './viz';

export { dayNum };

// 服务器给的星期是「一」…「日」（数据，不翻译）：中文拼成「周一」，英文换成「Mon」。已经是别的写法的原样显示。
const WEEKDAY_EN: Record<string, string> = { 一: 'Mon', 二: 'Tue', 三: 'Wed', 四: 'Thu', 五: 'Fri', 六: 'Sat', 日: 'Sun' };
export const weekdayName = (d: string) => (WEEKDAY_EN[d] ? L(`周${d}`, WEEKDAY_EN[d]) : d);
/** 坐标轴上的短写：中文「一」，英文「Mon」。 */
export const weekdayShort = (d: string) => L(d, WEEKDAY_EN[d] ?? d);

/** 进度环。呼应头像的光环，用在目标和当日营养上（= 素材库的 ProgressRing）。 */
export function Ring({ size = 64, stroke = 7, value, target, color, children }: { size?: number; stroke?: number; value: number; target: number; color?: string; children?: React.ReactNode }) {
  return <ProgressRing size={size} stroke={stroke} value={value} target={target} color={color}>{children}</ProgressRing>;
}

/** 横向进度条（数据色）。value / target 超过 1 就画满（= 素材库的 ProgressBar）。 */
export function Bar({ value, target, height = 8 }: { value: number; target: number; height?: number }) {
  return <ProgressBar value={value} target={target} height={height} />;
}

/**
 * 一周每天练了多久：一天一根柱子，今天用强调色，没练的画一小段灰。不画纵轴，数字在下面的列表里；点按一根显示分钟数。
 * 满格至少 60 分钟：练了 20 分钟别顶满。
 */
export function DayBars({ days, todayIndex, unit }: { days: { d: string; date: string; minutes: number; label: string }[]; todayIndex: number; unit: string }) {
  return (
    <BarChart axis={false} height={52} barWidth={14} values="none" emphasis max={60}
      data={days.map((x, i) => ({
        key: x.date, label: weekdayShort(x.d), value: x.minutes, current: i === todayIndex, text: `${x.minutes} ${unit}`,
        name: `${weekdayName(x.d)}${x.minutes > 0 ? ` ${x.label}` : L(' 未训练', ' rest')}`,
      }))} />
  );
}

// —— 读数折线（目标页的体重）——

type Reading = { date: string; value: number };

/**
 * 读数折线：主来源连成线、每次一个点，最新那次用强调色；对照来源是空心圈（和主来源同一天同一个数就正好套在点外面）；
 * 目标区间是一条浅绿的带（只有一头就一直铺到边上）。横轴按日期：隔了几天没量，线上就空几天。纵轴按数据自己的高低，
 * 把目标区间也算进去：离目标远，线就贴在一边，这正是要看的。纵轴至少撑开数值的 3%（最少 1 个单位）：体重 80.0–80.4 kg 不画成大起大落。
 * 起止日期、图例由调用方写在下面；点按或拖动显示那一天的读数。
 */
export function TrendLine({ points, secondary = [], band, from, to, height = 96, label, unit, sourceName, secondaryName }: {
  points: Reading[]; secondary?: Reading[]; band?: { low: number | null; high: number | null } | null; from: string; to: string; height?: number; label: string;
  /** 点按时数值后面的单位、两个来源的名字 */
  unit?: string; sourceName?: string; secondaryName?: string;
}) {
  const t = useTheme();
  const vals = [...points, ...secondary].map((p) => p.value);
  const mid = vals.length ? (Math.min(...vals) + Math.max(...vals)) / 2 : 0;
  const fmt = (v: number) => `${v.toLocaleString('en-GB', { maximumFractionDigits: 1 })}${unit ? ` ${unit}` : ''}`;
  return (
    <LineChart height={height} axis={false} xLabels={0} legend={false} markers emphasis dates={{ from, to }} minSpan={Math.max(Math.abs(mid) * 0.03, 1)} fmt={fmt} label={label}
      band={band && (band.low != null || band.high != null) ? { low: band.low, high: band.high, color: t.goodSoft } : null}
      series={[{ key: 'main', label: sourceName ?? L('读数', 'Reading'), points: points.map((p) => ({ key: p.date, value: p.value })) }]}
      secondary={secondary.length ? { label: secondaryName ?? L('对照', 'Other source'), points: secondary.map((p) => ({ key: p.date, value: p.value })) } : null} />
  );
}
