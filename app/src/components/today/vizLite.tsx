// 「今天」页要的几样图（最小实现）：迷你趋势、日时间格、月历（点 + 图例）。
// 可视化素材库（src/components/viz/，viz-kit 分支）合进来以后换成那边的同名组件，色板换成 theme 的 t.viz.series。
import React, { useMemo, useState } from 'react';
import { Pressable, StyleSheet, Text, View, type LayoutChangeEvent } from 'react-native';
import Svg, { Circle, Polyline } from 'react-native-svg';
import { L } from '../../i18n';
import { type Theme, type as typo, useTheme } from '../../theme';
import { T, useScaledWidth } from '../ui';

/** 分类色（README 色板，dataviz 校验过；按顺序用，不循环）。素材库合进来后用 t.viz.series。 */
const SERIES = {
  dark: ['#2A97B0', '#B5873A', '#8E6FD8', '#3E9E6E', '#5E86D6', '#C0679A'],
  light: ['#1488A3', '#B07D22', '#7B5CC7', '#2F8A5B', '#4A72C2', '#B24F86'],
};
export const seriesOf = (t: Theme) => SERIES[t.mode];

const hhmm = (m: number) => `${String(Math.floor(m / 60) % 25).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;

// —— 迷你趋势 ——————————————————————————————————————————————————————————

/** 一条 2px 的线，末端一个点（8px，外加 2px 卡片底色描边）；null 处断开。不画坐标轴。 */
export function Sparkline({ values, width, height = 44, color, surface, label }: {
  values: (number | null)[]; width: number; height?: number; color?: string; surface?: string; label: string;
}) {
  const t = useTheme();
  const c = color ?? seriesOf(t)[0];
  const nums = values.filter((v): v is number => v != null);
  if (nums.length < 2 || width <= 0) return <View style={{ width, height }} accessible accessibilityLabel={label} />;
  const lo = Math.min(...nums), hi = Math.max(...nums);
  const pad = 6;
  const x = (i: number) => pad + (i * (width - pad * 2)) / Math.max(1, values.length - 1);
  const y = (v: number) => (hi === lo ? height / 2 : pad + ((hi - v) * (height - pad * 2)) / (hi - lo));
  // 连续的一段画成一条线
  const runs: string[] = [];
  let cur: string[] = [];
  values.forEach((v, i) => {
    if (v == null) { if (cur.length > 1) runs.push(cur.join(' ')); cur = []; return; }
    cur.push(`${x(i).toFixed(1)},${y(v).toFixed(1)}`);
  });
  if (cur.length > 1) runs.push(cur.join(' '));
  const lastI = values.length - 1 - [...values].reverse().findIndex((v) => v != null);
  const last = values[lastI] as number;
  return (
    <View accessible accessibilityRole="image" accessibilityLabel={label}>
      <Svg width={width} height={height}>
        {runs.map((pts, i) => <Polyline key={i} points={pts} fill="none" stroke={c} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />)}
        <Circle cx={x(lastI)} cy={y(last)} r={4} fill={c} stroke={surface ?? t.surface} strokeWidth={2} />
      </Svg>
    </View>
  );
}

// —— 日时间格 ——————————————————————————————————————————————————————————

export interface GridBlock {
  id: string;
  /** 分钟（0–1440） */
  start: number;
  end: number;
  title: string;
  /** 标题后面的灰字（「原定 13:30」） */
  note?: string;
  /** 时间（短的放右边，高的放第二行） */
  time: string;
  color: { fg: string; soft: string };
  dim?: boolean;
  strike?: boolean;
  a11y: string;
}

const HOUR = 56;              // 一小时的高度
const PX = HOUR / 60;
const GAP_ROW = 30;           // 收起来的空闲时段一行多高
const MIN_GAP = 120;          // 空闲多久收成一行
const NEAR = 20;              // 开始时间相差不到这么多分钟的重叠，并排放；否则后开始的往右缩进叠在上面

type Seg = { kind: 'hours' | 'gap'; from: number; to: number; y: number; h: number };

function segments(blocks: GridBlock[], now: number | null): { segs: Seg[]; lo: number; hi: number } {
  const pts = blocks.flatMap((b) => [b.start, b.end]);
  if (now != null) pts.push(now);
  const lo = Math.floor(Math.min(...pts) / 60) * 60;
  const hi = Math.max(lo + 60, Math.min(24 * 60, Math.ceil(Math.max(...pts) / 60) * 60));
  const busy = [...blocks.map((b) => [b.start, b.end] as [number, number]), ...(now != null ? [[now, now + 1] as [number, number]] : [])]
    .sort((a, b) => a[0] - b[0]);
  const raw: { kind: 'hours' | 'gap'; from: number; to: number }[] = [];
  let segStart = lo;
  let covered = lo;
  for (const [s, e] of busy) {
    const gFrom = Math.ceil(covered / 60) * 60;
    const gTo = Math.floor(s / 60) * 60;
    if (gTo - gFrom >= MIN_GAP) {
      if (gFrom > segStart) raw.push({ kind: 'hours', from: segStart, to: gFrom });
      raw.push({ kind: 'gap', from: gFrom, to: gTo });
      segStart = gTo;
    }
    covered = Math.max(covered, e);
  }
  raw.push({ kind: 'hours', from: segStart, to: hi });
  let y = 0;
  const segs = raw.map((r) => { const h = r.kind === 'gap' ? GAP_ROW : (r.to - r.from) * PX; const s = { ...r, y, h }; y += h; return s; });
  return { segs, lo, hi };
}

const yAt = (segs: Seg[], m: number) => {
  const s = segs.find((x) => x.kind === 'hours' && m >= x.from && m <= x.to) ?? segs[segs.length - 1];
  return s.y + (Math.min(Math.max(m, s.from), s.to) - s.from) * PX;
};

type Placed = { b: GridBlock; left: number; width: number; depth: number };
const INDENT = 24;            // 后开始的重叠块往右缩进多少（px）：前一条的色条和标题还露在外面

/** 重叠的怎么放：差不多同时开始的并排（left / width 是比例）；后开始的往右缩进 depth × INDENT、叠在上面。 */
function place(blocks: GridBlock[]): Placed[] {
  const out: Placed[] = [];
  for (const b of [...blocks].sort((a, c) => a.start - c.start || (c.end - c.start) - (a.end - a.start))) {
    const over = out.filter((p) => p.b.start < b.end && b.start < p.b.end);
    if (!over.length) { out.push({ b, left: 0, width: 1, depth: 0 }); continue; }
    const near = over.filter((p) => b.start - p.b.start < NEAR);
    if (near.length) {
      const base = Math.min(...near.map((p) => p.left));
      const row = [...near, { b, left: 0, width: 0, depth: Math.max(...near.map((p) => p.depth)) } as Placed];
      row.forEach((p, i) => { p.left = base + (i * (1 - base)) / row.length; p.width = (1 - base) / row.length; });
      out.push(row[row.length - 1]);
    } else {
      const host = over.reduce((a, p) => (p.depth >= a.depth ? p : a));
      out.push({ b, left: host.left, width: host.width, depth: Math.min(3, host.depth + 1) });
    }
  }
  return out;
}

/**
 * 日时间格：方块高度按时长，空闲两小时以上收成一行，金线标出现在。
 * 只画有时间的；全天的、没有时间的由调用方另放。now = 现在几分钟（只在今天给）。
 */
export function DayTimeGrid({ blocks, now, dayStart = 8 * 60, onPress }: {
  blocks: GridBlock[]; now: number | null; dayStart?: number; onPress?: (id: string) => void;
}) {
  const t = useTheme();
  const labelW = useScaledWidth(46);
  const [w, setW] = useState(0);
  const { segs, lo, hi } = useMemo(() => segments(blocks, now), [blocks, now]);
  const placed = useMemo(() => place(blocks), [blocks]);
  const total = segs.length ? segs[segs.length - 1].y + segs[segs.length - 1].h : 0;
  const area = Math.max(0, w - labelW - 6);
  const hours: { m: number; y: number }[] = [];
  const nowY = now != null ? yAt(segs, now) : null;
  segs.forEach((s) => { if (s.kind === 'hours') for (let m = s.from; m <= s.to; m += 60) hours.push({ m, y: s.y + (m - s.from) * PX }); });
  const freeText = (a: number, b: number) => L(`${hhmm(a)}–${hhmm(b)} 无安排`, `Free ${hhmm(a)}–${hhmm(b)}`);
  return (
    <View style={{ gap: 6 }}>
      {lo - dayStart >= 60 ? <T v="caption" color={t.ink3} style={{ paddingLeft: labelW + 6, fontWeight: '400' }}>{freeText(dayStart, lo)}</T> : null}
      <View onLayout={(e: LayoutChangeEvent) => setW(Math.round(e.nativeEvent.layout.width))} style={{ height: total + 8, marginTop: 8 }}>
        {hours.map((h) => (
          <View key={h.m} style={[styles.hourLine, { top: h.y, borderTopColor: t.line }]}>
            {nowY != null && Math.abs(nowY - h.y) < 14 ? null  /* 离现在线太近的整点不写字，现在的钟点写在那里 */
              : <Text style={[styles.hourLabel, { color: t.ink3, backgroundColor: t.surface, width: labelW }]} numberOfLines={1}>{hhmm(h.m)}</Text>}
          </View>
        ))}
        {segs.filter((s) => s.kind === 'gap').map((s) => (
          <View key={`gap${s.from}`} style={[styles.gap, { top: s.y, height: s.h, left: labelW + 6 }]}>
            <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{freeText(s.from, s.to)}</T>
          </View>
        ))}
        {area > 0 ? placed.map(({ b, left, width, depth }) => {
          const top = yAt(segs, b.start) + 1;
          const h = Math.max(22, yAt(segs, b.end) - yAt(segs, b.start) - 2);
          const tall = h >= 42;
          const x = left * area + depth * INDENT;
          return (
            <Pressable key={b.id} onPress={onPress ? () => onPress(b.id) : undefined} accessibilityRole="button" accessibilityLabel={b.a11y}
              style={({ pressed }) => [styles.block, {
                top, height: h, left: labelW + 6 + x, width: Math.max(40, (left + width) * area - x),
                backgroundColor: b.color.soft, opacity: (b.dim ? 0.55 : 1) * (pressed ? 0.75 : 1), zIndex: 1 + depth,
                borderColor: depth > 0 || left > 0 ? t.surface : 'transparent', borderWidth: depth > 0 || left > 0 ? 2 : 0,
              }]}>
              <View style={[styles.bar, { backgroundColor: b.color.fg }]} />
              {tall ? (
                <View style={{ flex: 1, minWidth: 0, paddingHorizontal: 10, paddingVertical: 6, gap: 2 }}>
                  <Text numberOfLines={1} style={[styles.title, { color: t.ink }, b.strike && styles.strike]}>
                    {b.title}{b.note ? <Text style={[styles.note, { color: t.ink2 }]}>{` · ${b.note}`}</Text> : null}
                  </Text>
                  <Text numberOfLines={1} style={[styles.time, { color: t.ink2 }]}>{b.time}</Text>
                </View>
              ) : (
                <View style={{ flex: 1, minWidth: 0, flexDirection: 'row', alignItems: 'center', paddingHorizontal: 10, gap: 8 }}>
                  <Text numberOfLines={1} style={[styles.title, { flex: 1, color: t.ink }, b.strike && styles.strike]}>
                    {b.title}{b.note ? <Text style={[styles.note, { color: t.ink2 }]}>{` · ${b.note}`}</Text> : null}
                  </Text>
                  <Text numberOfLines={1} style={[styles.time, { color: t.ink2 }]}>{b.time}</Text>
                </View>
              )}
            </Pressable>
          );
        }) : null}
        {nowY != null && now != null ? (
          <View pointerEvents="none" style={[styles.now, { top: nowY - 7 }]} accessible accessibilityLabel={L(`现在 ${hhmm(now)}`, `Now ${hhmm(now)}`)}>
            <Text style={[styles.nowLabel, { color: t.gold, width: labelW, backgroundColor: t.surface }]} numberOfLines={1}>{hhmm(now)}</Text>
            <View style={[styles.nowDot, { backgroundColor: t.gold }]} />
            <View style={[styles.nowLine, { backgroundColor: t.gold }]} />
          </View>
        ) : null}
      </View>
      {hi < 24 * 60 ? <T v="caption" color={t.ink3} style={{ paddingLeft: labelW + 6, fontWeight: '400' }}>{L(`${hhmm(hi)} 后无安排`, `Free after ${hhmm(hi)}`)}</T> : null}
    </View>
  );
}

// —— 月历 ————————————————————————————————————————————————————————————

const pad2 = (n: number) => String(n).padStart(2, '0');
export const isoOfDate = (d: Date) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;

/** 月历的格子：从这个月 1 号所在那周的周一开始，满 6 周（42 天）。 */
export function monthCells(year: number, month: number): string[] {
  const first = new Date(year, month, 1);
  const start = new Date(year, month, 1 - ((first.getDay() + 6) % 7));
  return Array.from({ length: 42 }, (_, i) => isoOfDate(new Date(start.getFullYear(), start.getMonth(), start.getDate() + i)));
}

/** 月历：每天最多三个点（颜色由调用方按类别给），今天金底，选中的一圈描边。只画到这个月最后一天所在的那一周。 */
export function MonthCalendar({ year, month, today, selected, marks, onSelect }: {
  year: number; month: number; today: string; selected: string | null; marks: Record<string, string[]>; onSelect: (iso: string) => void;
}) {
  const t = useTheme();
  const cells = monthCells(year, month);
  const lastIdx = cells.findIndex((c) => c === isoOfDate(new Date(year, month + 1, 0)));
  const rows = Math.ceil((lastIdx + 1) / 7);
  const dow = L('一二三四五六日', 'MTWTFSS').split('');
  return (
    <View>
      <View style={{ flexDirection: 'row' }}>
        {dow.map((d, i) => <Text key={i} style={[styles.dow, { color: t.ink3 }]}>{d}</Text>)}
      </View>
      {Array.from({ length: rows }, (_, r) => (
        <View key={r} style={{ flexDirection: 'row' }}>
          {cells.slice(r * 7, r * 7 + 7).map((iso) => {
            const inMonth = Number(iso.slice(5, 7)) === month + 1;
            const isToday = iso === today;
            const sel = iso === selected;
            const dots = inMonth ? (marks[iso] ?? []).slice(0, 3) : [];
            return (
              <Pressable key={iso} onPress={() => onSelect(iso)} disabled={!inMonth} accessibilityRole="button"
                accessibilityLabel={`${Number(iso.slice(8))}${L('日', '')}${dots.length ? L(`，${dots.length} 项`, `, ${dots.length} items`) : ''}`}
                accessibilityState={{ selected: sel }} style={styles.cell}>
                <View style={[styles.num, isToday && { backgroundColor: t.goldFill }, sel && !isToday && { borderWidth: 1.5, borderColor: t.ink }]}>
                  <Text style={[typo.callout, { fontSize: 15, fontWeight: isToday || sel ? '700' : '400', color: !inMonth ? t.ink3 : isToday ? t.onGold : t.ink, opacity: inMonth ? 1 : 0.45 }]}>
                    {Number(iso.slice(8))}
                  </Text>
                </View>
                <View style={styles.dots}>{dots.map((c, i) => <View key={i} style={[styles.dot, { backgroundColor: c }]} />)}</View>
              </Pressable>
            );
          })}
        </View>
      ))}
    </View>
  );
}

/** 图例：小圆点 + 文字（文字用文字色，不用系列色）。 */
export function DotLegend({ items }: { items: { color: string; label: string }[] }) {
  const t = useTheme();
  return (
    <View style={styles.legend}>
      {items.map((x) => (
        <View key={x.label} style={styles.legendItem}>
          <View style={[styles.legendDot, { backgroundColor: x.color }]} />
          <T v="caption" color={t.ink2} style={{ fontWeight: '400' }}>{x.label}</T>
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  hourLine: { position: 'absolute', left: 0, right: 0, height: 0, borderTopWidth: StyleSheet.hairlineWidth },
  hourLabel: { position: 'absolute', left: 0, top: -8, fontSize: 11, lineHeight: 15, fontVariant: ['tabular-nums'], paddingRight: 6 },
  gap: { position: 'absolute', right: 0, justifyContent: 'center' },
  block: { position: 'absolute', borderRadius: 8, flexDirection: 'row', overflow: 'hidden' },
  bar: { width: 3 },
  title: { fontSize: 14, fontWeight: '600', lineHeight: 18 },
  note: { fontSize: 14, fontWeight: '400' },
  time: { fontSize: 12, lineHeight: 16, fontVariant: ['tabular-nums'] },
  strike: { textDecorationLine: 'line-through' },
  now: { position: 'absolute', left: 0, right: 0, height: 14, flexDirection: 'row', alignItems: 'center', zIndex: 10 },
  nowLabel: { fontSize: 11, fontWeight: '700', fontVariant: ['tabular-nums'] },
  nowDot: { width: 7, height: 7, borderRadius: 4, marginLeft: 6 },
  nowLine: { flex: 1, height: 1 },
  dow: { flex: 1, textAlign: 'center', fontSize: 12, height: 24, lineHeight: 24 },
  cell: { flex: 1, height: 54, alignItems: 'center', paddingTop: 4, gap: 4 },
  num: { width: 30, height: 30, borderRadius: 15, alignItems: 'center', justifyContent: 'center' },
  dots: { flexDirection: 'row', gap: 3, height: 5 },
  dot: { width: 5, height: 5, borderRadius: 3 },
  legend: { flexDirection: 'row', gap: 16, flexWrap: 'wrap' },
  legendItem: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  legendDot: { width: 8, height: 8, borderRadius: 4 },
});
