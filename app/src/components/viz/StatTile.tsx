// 数字卡：一个主数字 + 说明 + 变化值（或一行小字）+ 至多两项次要数字 + 可选迷你趋势。几个并排用 StatRow。
// 一个数就说清楚的事别画图（一根柱子的柱状图、两份的饼图都换成数字卡）。大数字用比例字形，不用等宽（等宽只给要上下对齐的列）。
import React from 'react';
import { Pressable, StyleSheet, View, type StyleProp, type ViewStyle } from 'react-native';
import { radius, space, useTheme } from '../../theme';
import { ArrowDown, ArrowUp, Minus } from '../icons';
import { T } from '../ui';
import { toneColors, VizFrame, type Direction, type Tone, type VizStateProps } from './core';
import { Sparkline } from './Sparkline';

export interface StatDelta {
  /** 已格式化的变化值（「+3」「5 kg」「比上周 +12%」） */
  text: string;
  dir: Direction;
  /** 这个方向好不好：true 绿、false 红、不给灰（服务器给的是 tone 时用 tone） */
  good?: boolean | null;
  tone?: Tone;
}

export interface StatTileProps extends VizStateProps {
  /** 说明（不加冒号） */
  label: string;
  /** 主数字（已格式化）；null = 还没有数据 */
  value: string | null;
  unit?: string;
  delta?: StatDelta | null;
  /** 没有变化值时的一行小字 */
  sub?: string | null;
  /** 次要数字（至多两项）：{ value: '10', label: '本周截止' } */
  minor?: { value: string; label: string }[];
  /** 迷你趋势：最后一个是当前 */
  trend?: (number | null)[];
  /** hero 52（一页只有一个）/ lg 32 / md 26 / sm 22 */
  size?: 'hero' | 'lg' | 'md' | 'sm';
  onPress?: () => void;
  style?: StyleProp<ViewStyle>;
}

const SIZES = { hero: 52, lg: 32, md: 26, sm: 22 } as const;

function DeltaLine({ delta }: { delta: StatDelta }) {
  const t = useTheme();
  const tone: Tone = delta.tone ?? (delta.dir === 'flat' || delta.good == null ? 'neutral' : delta.good ? 'good' : 'bad');
  const fg = tone === 'neutral' ? t.ink2 : toneColors(t, tone)[1];
  const Icon = delta.dir === 'up' ? ArrowUp : delta.dir === 'down' ? ArrowDown : Minus;
  return (
    <View style={styles.delta}>
      <Icon size={12} color={fg} strokeWidth={3} />
      <T v="caption" color={fg} numberOfLines={1} style={{ fontWeight: '600', flexShrink: 1 }}>{delta.text}</T>
    </View>
  );
}

export function StatTile({ label, value, unit, delta, sub, minor, trend, size = 'lg', onPress, style, ...state }: StatTileProps) {
  const t = useTheme();
  const big = SIZES[size];
  const a11y = [label, value ?? '—', unit, delta ? delta.text : sub, ...(minor ?? []).map((m) => `${m.label} ${m.value}`)].filter(Boolean).join(' ');
  const body = (
    <View style={{ gap: size === 'hero' ? 6 : 2 }}>
      <View style={styles.valueRow}>
        <T numberOfLines={1} adjustsFontSizeToFit style={{ fontSize: big, lineHeight: Math.round(big * 1.12), fontWeight: '700', letterSpacing: -0.3, flexShrink: 1 }}>{value ?? '—'}</T>
        {unit ? <T v="callout" color={t.ink2} style={{ fontSize: Math.max(13, Math.round(big * 0.42)) }}>{unit}</T> : null}
      </View>
      <T v="caption" color={t.ink2} style={{ fontWeight: '400', fontSize: size === 'hero' ? 14 : 12 }}>{label}</T>
      {delta ? <DeltaLine delta={delta} /> : sub ? <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{sub}</T> : null}
      {minor?.length ? (
        <View style={[styles.minor, size === 'hero' && { marginTop: space.sm }]}>
          {minor.slice(0, 2).map((m) => (
            <T key={m.label} v="callout" style={{ fontSize: 13 }}>
              <T v="callout" style={{ fontSize: 13, fontWeight: '700', fontVariant: ['tabular-nums'] }}>{m.value}</T>
              <T v="callout" color={t.ink3} style={{ fontSize: 13 }}>{` ${m.label}`}</T>
            </T>
          ))}
        </View>
      ) : null}
      {trend && trend.some((v) => v != null) ? <Sparkline values={trend} height={28} style={{ marginTop: 6 }} /> : null}
    </View>
  );
  return (
    <VizFrame empty={false} state={state} label={a11y} group={!onPress} style={style}>
      {onPress ? (
        <Pressable onPress={onPress} accessibilityRole="button" accessibilityLabel={a11y} style={({ pressed }) => ({ opacity: pressed ? 0.7 : 1 })}>{body}</Pressable>
      ) : body}
    </VizFrame>
  );
}

/**
 * 一排数字卡（KPI 行）：1 个占满，2 / 4 个两列，3 个三列。boxed = 每个数单独一块底（直接放在页面上时），
 * 不给就画在调用方的卡片里。字号跟着列数：1 列 32、2 列 26、3 列 22。
 */
export function StatRow({ items, columns, boxed, style }: { items: StatTileProps[]; columns?: number; boxed?: boolean; style?: StyleProp<ViewStyle> }) {
  const t = useTheme();
  const cols = columns ?? (items.length === 4 ? 2 : Math.max(1, Math.min(3, items.length)));
  const size = cols === 1 ? 'lg' : cols === 2 ? 'md' : 'sm';
  return (
    <View style={[styles.grid, boxed && { gap: space.sm }, style]}>
      {items.map((it, i) => (
        <View key={`${i}-${it.label}`} style={boxed
          ? { flexBasis: 0, flexGrow: 1, minWidth: `${100 / cols - 4}%`, backgroundColor: t.surface, borderRadius: radius.md + 2, padding: space.md }
          : { width: `${100 / cols}%`, paddingRight: space.sm }}>
          <StatTile {...it} size={it.size ?? size} />
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  valueRow: { flexDirection: 'row', alignItems: 'baseline', gap: 4 },
  delta: { flexDirection: 'row', alignItems: 'center', gap: 3 },
  minor: { flexDirection: 'row', flexWrap: 'wrap', columnGap: 14, rowGap: 2 },
  grid: { flexDirection: 'row', flexWrap: 'wrap', rowGap: 14 },
});
