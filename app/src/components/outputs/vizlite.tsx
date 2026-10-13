// 小板子要的几样图（2026-10-13）：可视化素材库（src/components/viz/，viz-kit 分支）合进来之前的最小实现。
// 组件名和参数沿用素材库（Sparkline、ChangeTag、Donut、ProgressBar 一族；目标条、前后对比是素材库第二批），合并时换成 '../viz' 的、删掉这个文件。
// 规则同素材库：分类色按顺序用（README 的色板，深浅两套 dataviz 校验过）；文字用文字色、颜色只给标记；线宽 2、标记 8 + 2 的底色描边、段间 2。
import React from 'react';
import { StyleSheet, View, type StyleProp, type ViewStyle } from 'react-native';
import Svg, { Circle, Line, Polyline } from 'react-native-svg';
import { useTheme, type Theme } from '../../theme';
import { ArrowDown, ArrowUp } from '../icons';
import { T } from '../ui';

const SERIES = {
  dark: ['#2A97B0', '#B5873A', '#8E6FD8', '#3E9E6E', '#5E86D6', '#C0679A'],
  light: ['#1488A3', '#B07D22', '#7B5CC7', '#2F8A5B', '#4A72C2', '#B24F86'],
} as const;
export const vizMark = { line: 2, dot: 8, ring: 2, gap: 2 } as const;

/** 第 slot 个分类色（从 0 起，固定顺序）。 */
export const seriesColor = (t: Theme, slot: number) => SERIES[t.mode][Math.max(0, Math.min(slot, 5))];

export type Direction = 'up' | 'down' | 'flat';

/** 变化标签：箭头 + 一句（「5 kg」「辅助减少」「持平」）。good：这个方向是不是好事（辅助减少是向上的好事），不给 = 向上算好。 */
export function ChangeTag({ dir, text, good, small, style }: { dir: Direction; text: string; good?: boolean | null; small?: boolean; style?: StyleProp<ViewStyle> }) {
  const t = useTheme();
  const ok = good ?? dir === 'up';
  const [bg, fg] = dir === 'flat' ? [t.surface2, t.ink2] : ok ? [t.goodSoft, t.good] : [t.badSoft, t.bad];
  const s = small ? 10 : 11;
  return (
    <View style={[{ flexDirection: 'row', alignItems: 'center', gap: 3, backgroundColor: bg, borderRadius: 7, paddingHorizontal: small ? 7 : 8, paddingVertical: small ? 2 : 3, alignSelf: 'flex-start' }, style]}>
      {dir === 'up' ? <ArrowUp size={s} color={fg} strokeWidth={3} /> : dir === 'down' ? <ArrowDown size={s} color={fg} strokeWidth={3} />
        : <View style={{ width: s - 2, height: 2.5, borderRadius: 1, backgroundColor: fg, marginHorizontal: 1 }} />}
      <T v="caption" color={fg} style={{ fontSize: small ? 11 : 12, fontWeight: '700' }}>{text}</T>
    </View>
  );
}

/** 迷你趋势：不画坐标轴，末端一个实心点。surface = 下面的底色（点的描边）。 */
export function Sparkline({ values, width, height = 30, color, surface, label }: { values: number[]; width: number; height?: number; color?: string; surface?: string; label?: string }) {
  const t = useTheme();
  const c = color ?? seriesColor(t, 0);
  if (values.length < 2) return <View style={{ width, height }} />;
  const pad = vizMark.dot / 2 + vizMark.ring;
  let lo = Math.min(...values);
  let hi = Math.max(...values);
  if (hi === lo) { lo -= 1; hi += 1; }
  const x = (i: number) => pad + (i / (values.length - 1)) * (width - pad * 2);
  const y = (v: number) => pad + (1 - (v - lo) / (hi - lo)) * (height - pad * 2);
  const last = values.length - 1;
  return (
    <View style={{ width, height }} accessible={!!label} accessibilityRole={label ? 'image' : undefined} accessibilityLabel={label}>
      <Svg width={width} height={height}>
        <Polyline points={values.map((v, i) => `${x(i)},${y(v)}`).join(' ')} fill="none" stroke={c} strokeWidth={vizMark.line} strokeLinejoin="round" strokeLinecap="round" />
        <Circle cx={x(last)} cy={y(values[last])} r={vizMark.dot / 2} fill={c} stroke={surface ?? t.surface} strokeWidth={vizMark.ring} />
      </Svg>
    </View>
  );
}

/** 前后对比：两个点连一条线，上次是空心灰点、本次是实心点；labels 写在点下面，数写在点旁边（compact 时都不写，浮动标签用）。
 * 字用 RN 的 Text 叠在图上（和 charts.tsx 一样），不用 SVG 的 text：网页上 SVG 的字会落到衬线体。 */
export function PairChart({ values, labels, width, height, surface, compact, label }: {
  values: [number, number]; labels?: [string, string]; width: number; height: number; surface?: string; compact?: boolean; label?: string;
}) {
  const t = useTheme();
  const c = seriesColor(t, 0);
  const bg = surface ?? t.surface;
  const [a, b] = values;
  const padX = compact ? 6 : 14;
  const top = compact ? 7 : 18;
  const bottom = compact ? height - 7 : height - 20;
  const lo = Math.min(a, b);
  const hi = Math.max(a, b);
  const y = (v: number) => (hi === lo ? (top + bottom) / 2 : top + (1 - (v - lo) / (hi - lo)) * (bottom - top));
  const x1 = padX;
  const x2 = width - padX;
  const small = { fontSize: 10, fontWeight: '400' as const, fontVariant: ['tabular-nums' as const] };
  return (
    <View style={{ width, height }} accessible={!!label} accessibilityRole={label ? 'image' : undefined} accessibilityLabel={label}>
      <Svg width={width} height={height}>
        <Line x1={x1} y1={y(a)} x2={x2} y2={y(b)} stroke={c} strokeWidth={vizMark.line} strokeLinecap="round" />
        <Circle cx={x1} cy={y(a)} r={compact ? 3.5 : 4} fill={bg} stroke={t.ink3} strokeWidth={1.5} />
        <Circle cx={x2} cy={y(b)} r={compact ? 4 : 4.5} fill={c} stroke={bg} strokeWidth={vizMark.ring} />
      </Svg>
      {!compact && labels ? (
        <>
          <T v="caption" color={t.ink3} style={[small, styles.at, { left: x1 - 24, top: height - 14, width: 48, textAlign: 'center' }]}>{labels[0]}</T>
          <T v="caption" style={[small, styles.at, { left: x2 - 24, top: height - 14, width: 48, textAlign: 'center', fontWeight: '700' }]}>{labels[1]}</T>
          <T v="caption" color={t.ink3} style={[small, styles.at, { left: x1 + 8, top: y(a) - 17 }]}>{String(a)}</T>
          <T v="caption" style={[small, styles.at, { left: x2 - 56, top: y(b) - 18, width: 48, textAlign: 'right' }]}>{String(b)}</T>
        </>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({ at: { position: 'absolute' } });

export interface ShareDatum { key: string; label: string; value: number; text?: string; slot?: number }

/** 环形（3–6 份的占比）：段间留 2 的底色。center：中间的大数字和一行说明。 */
export function Donut({ data, size = 112, thickness = 14, center, surface, label }: {
  data: ShareDatum[]; size?: number; thickness?: number; center?: { value: string; label?: string }; surface?: string; label?: string;
}) {
  const t = useTheme();
  const rows = data.filter((d) => d.value > 0);
  const total = rows.reduce((s, d) => s + d.value, 0) || 1;
  const r = (size - thickness) / 2;
  const C = 2 * Math.PI * r;
  const gap = rows.length > 1 ? vizMark.gap : 0;
  const starts = rows.map((_, i) => rows.slice(0, i).reduce((sum, x) => sum + (x.value / total) * C, 0));
  return (
    <View style={{ width: size, height: size }} accessible={!!label} accessibilityRole={label ? 'image' : undefined} accessibilityLabel={label}>
      <Svg width={size} height={size}>
        <Circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={surface ?? t.surface} strokeWidth={thickness} />
        {rows.map((d, i) => (
          <Circle key={d.key} cx={size / 2} cy={size / 2} r={r} fill="none" stroke={seriesColor(t, d.slot ?? i)} strokeWidth={thickness}
            strokeDasharray={`${Math.max(0, (d.value / total) * C - gap)} ${C}`} strokeDashoffset={-starts[i]} transform={`rotate(-90 ${size / 2} ${size / 2})`} />
        ))}
      </Svg>
      {center ? (
        <View style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, alignItems: 'center', justifyContent: 'center' }} pointerEvents="none">
          <T v="title" style={{ fontSize: size > 60 ? 20 : 11, fontWeight: '700', fontVariant: ['tabular-nums'] }}>{center.value}</T>
          {center.label ? <T v="caption" color={t.ink2} style={{ fontSize: 10, fontWeight: '400' }}>{center.label}</T> : null}
        </View>
      ) : null}
    </View>
  );
}

/** 目标条：填充 = 实际，竖线 = 目标（两者的位置 0–1 由服务器算好）。 */
export function TargetBar({ fill, mark, height = 12, color, label }: { fill: number; mark?: number | null; height?: number; color?: string; label?: string }) {
  const t = useTheme();
  return (
    <View accessible={!!label} accessibilityRole={label ? 'image' : undefined} accessibilityLabel={label}
      style={{ height, borderRadius: height / 2, backgroundColor: t.track, marginVertical: mark != null ? 4 : 0 }}>
      <View style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: `${Math.max(0, Math.min(1, fill)) * 100}%`, borderRadius: height / 2, backgroundColor: color ?? seriesColor(t, 0) }} />
      {mark != null ? <View style={{ position: 'absolute', left: `${Math.max(0, Math.min(1, mark)) * 100}%`, top: -4, bottom: -4, width: 2, marginLeft: -1, borderRadius: 1, backgroundColor: t.ink }} /> : null}
    </View>
  );
}

/** 范围条：一段目标范围（浅色带）里放实际值的点，比如睡眠 7–9 小时。位置都是 0–1；surface = 下面的底色（点的描边）。 */
export function RangeBar({ value, low, high, height = 12, surface, label }: { value: number | null; low: number; high: number; height?: number; surface?: string; label?: string }) {
  const t = useTheme();
  const c = seriesColor(t, 0);
  const clamp = (v: number) => Math.max(0, Math.min(1, v));
  return (
    <View accessible={!!label} accessibilityRole={label ? 'image' : undefined} accessibilityLabel={label}
      style={{ height, borderRadius: height / 2, backgroundColor: t.track, marginVertical: 4 }}>
      <View style={{ position: 'absolute', left: `${clamp(low) * 100}%`, width: `${(clamp(high) - clamp(low)) * 100}%`, top: 0, bottom: 0, backgroundColor: c, opacity: 0.28, borderRadius: 2 }} />
      {value != null ? (
        <View style={{ position: 'absolute', left: `${clamp(value) * 100}%`, top: (height - 14) / 2, width: 14, height: 14, marginLeft: -7, borderRadius: 7, backgroundColor: c, borderWidth: 2, borderColor: surface ?? t.surface }} />
      ) : null}
    </View>
  );
}
