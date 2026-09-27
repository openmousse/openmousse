import React, { useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import Svg, { Circle, Polyline, Rect } from 'react-native-svg';
import { L } from '../i18n';
import { type, useTheme } from '../theme';

// 服务器给的星期是「一」…「日」（数据，不翻译）：中文拼成「周一」，英文换成「Mon」。已经是别的写法的原样显示。
const WEEKDAY_EN: Record<string, string> = { 一: 'Mon', 二: 'Tue', 三: 'Wed', 四: 'Thu', 五: 'Fri', 六: 'Sat', 日: 'Sun' };
export const weekdayName = (d: string) => (WEEKDAY_EN[d] ? L(`周${d}`, WEEKDAY_EN[d]) : d);
/** 坐标轴上的短写：中文「一」，英文「Mon」。 */
export const weekdayShort = (d: string) => L(d, WEEKDAY_EN[d] ?? d);

/** 进度环。呼应头像的光环，用在目标和当日营养上。 */
export function Ring({ size = 64, stroke = 7, value, target, color, children }: { size?: number; stroke?: number; value: number; target: number; color?: string; children?: React.ReactNode }) {
  const t = useTheme();
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const p = Math.max(0, Math.min(1, target === 0 ? 0 : value / target));
  return (
    <View style={{ width: size, height: size, alignItems: 'center', justifyContent: 'center' }}>
      <Svg width={size} height={size} style={StyleSheet.absoluteFill}>
        <Circle cx={size / 2} cy={size / 2} r={r} stroke={t.track} strokeWidth={stroke} fill="none" />
        {p > 0 ? (  // 0 不画：圆头的线帽会在顶上留一个点，像是有进度
          <Circle cx={size / 2} cy={size / 2} r={r} stroke={color ?? t.chartA} strokeWidth={stroke} fill="none"
            strokeDasharray={`${c * p} ${c}`} strokeLinecap="round" transform={`rotate(-90 ${size / 2} ${size / 2})`} />
        ) : null}
      </Svg>
      {children}
    </View>
  );
}

/** 横向进度条（数据色）。value / target 超过 1 就画满。 */
export function Bar({ value, target, height = 8 }: { value: number; target: number; height?: number }) {
  const t = useTheme();
  const p = target > 0 ? Math.max(0, Math.min(1, value / target)) : 0;
  return (
    <View style={{ height, borderRadius: height / 2, backgroundColor: t.track, overflow: 'hidden' }}>
      <View style={{ width: `${p * 100}%`, height, borderRadius: height / 2, backgroundColor: t.chartA }} />
    </View>
  );
}

/** 一周每天练了多久：一天一根柱子，今天的深一点，没练的画一小段灰。只看形状，数字在下面的列表里。 */
export function DayBars({ days, todayIndex, unit }: { days: { d: string; date: string; minutes: number; label: string }[]; todayIndex: number; unit: string }) {
  const t = useTheme();
  const max = Math.max(60, ...days.map((x) => x.minutes));
  const H = 52;
  return (
    <View style={{ flexDirection: 'row', alignItems: 'flex-end' }}>
      {days.map((x, i) => {
        const today = i === todayIndex;
        const h = x.minutes > 0 ? Math.max(4, Math.round((x.minutes / max) * H)) : 2;
        return (
          <View key={x.date} style={{ flex: 1, alignItems: 'center', gap: 4 }} accessible
            accessibilityLabel={`${weekdayName(x.d)}${x.minutes > 0 ? ` ${x.label} ${x.minutes} ${unit}` : L(' 没练', ' rest')}`}>
            <View style={{ height: H, justifyContent: 'flex-end' }}>
              <View style={{ width: 14, height: h, borderRadius: x.minutes > 0 ? 4 : 1, backgroundColor: x.minutes > 0 ? (today ? t.cyan : t.chartA) : t.track }} />
            </View>
            <Text style={[type.caption, { fontSize: 11, color: today ? t.ink : t.ink3, fontWeight: today ? '700' : '500' }]}>{weekdayShort(x.d)}</Text>
          </View>
        );
      })}
    </View>
  );
}

// —— 读数折线（目标页的体重）——

type Reading = { date: string; value: number };
/** YYYY-MM-DD → 第几天（只拿来比远近）。 */
export const dayNum = (iso: string) => Math.round(Date.UTC(Number(iso.slice(0, 4)), Number(iso.slice(5, 7)) - 1, Number(iso.slice(8, 10))) / 86400000);

/**
 * 读数折线：主来源连成线、每次一个点，最新那次大一点（青）；对照来源是淡淡的空心圈（和主来源同一天同一个数就正好套在点外面）；
 * 目标区间是一条浅绿的带（只有一头就一直铺到边上）。横轴按日期：隔了几天没量，线上就空几天。纵轴按数据自己的高低，
 * 把目标区间也算进去：离目标远，线就贴在一边，这正是要看的。
 */
export function TrendLine({ points, secondary = [], band, from, to, height = 96, label }: {
  points: Reading[]; secondary?: Reading[]; band?: { low: number | null; high: number | null } | null; from: string; to: string; height?: number; label: string;
}) {
  const t = useTheme();
  const [w, setW] = useState(0);
  const H = height;
  const pad = 8;
  const vals = [...points, ...secondary].map((p) => p.value);
  const edges = [band?.low, band?.high].filter((v): v is number => v != null);
  let lo = Math.min(...vals, ...edges);
  let hi = Math.max(...vals, ...edges);
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) { lo = 0; hi = 1; }
  const margin = (hi - lo || Math.abs(hi) || 1) * 0.12;
  lo -= margin; hi += margin;
  const d0 = dayNum(from);
  const span = Math.max(1, dayNum(to) - d0);
  const xOf = (iso: string) => pad + ((dayNum(iso) - d0) / span) * Math.max(0, w - pad * 2);
  const yOf = (v: number) => pad + (1 - (v - lo) / (hi - lo)) * (H - pad * 2);
  const line = points.map((p) => `${xOf(p.date)},${yOf(p.value)}`).join(' ');
  const last = points[points.length - 1];
  const top = band?.high != null ? yOf(band.high) : 0;
  const bottom = band?.low != null ? yOf(band.low) : H;
  return (
    <View style={{ height: H }} onLayout={(e) => setW(e.nativeEvent.layout.width)} accessible accessibilityLabel={label}>
      {w > 0 ? (
        <Svg width={w} height={H}>
          {band && (band.low != null || band.high != null) ? <Rect x={0} y={Math.max(0, top)} width={w} height={Math.max(2, Math.min(H, bottom) - Math.max(0, top))} rx={6} fill={t.goodSoft} /> : null}
          {secondary.map((p) => <Circle key={`s${p.date}`} cx={xOf(p.date)} cy={yOf(p.value)} r={4.5} fill="none" stroke={t.ink3} strokeOpacity={0.7} strokeWidth={1.2} />)}
          {points.length > 1 ? <Polyline points={line} fill="none" stroke={t.chartA} strokeWidth={2.5} strokeLinejoin="round" strokeLinecap="round" /> : null}
          {points.map((p) => (p === last ? null : <Circle key={p.date} cx={xOf(p.date)} cy={yOf(p.value)} r={2.5} fill={t.chartA} />))}
          {last ? <Circle cx={xOf(last.date)} cy={yOf(last.value)} r={4.5} fill={t.cyan} stroke={t.surface} strokeWidth={1.5} /> : null}
        </Svg>
      ) : null}
    </View>
  );
}
