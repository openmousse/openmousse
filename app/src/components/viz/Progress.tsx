// 进度：环形（单一目标）、进度条（实际对目标的一条）、分段条（逐项完成：一格一组）。
import React from 'react';
import { StyleSheet, View, type StyleProp, type ViewStyle } from 'react-native';
import Svg, { Circle } from 'react-native-svg';
import { L } from '../../i18n';
import { space, useTheme, vizMark } from '../../theme';
import { T } from '../ui';
import { seriesColor, VizFrame, type VizStateProps } from './core';

const clamp01 = (v: number) => Math.max(0, Math.min(1, v));
const ratioOf = (value: number, target: number) => (target > 0 ? clamp01(value / target) : 0);

/**
 * 进度环：呼应头像的光环，用在单一目标（恢复分、今天的热量、一节训练做完几组）。中间放数字（children）。
 * 超过目标画满；0 不画（圆头线帽会在顶上留一个点，像是有进度）。label：读屏念的一句（「Pull A 完成 10 / 10 组」）。
 */
export function ProgressRing({ value, target, size = 64, stroke, color, track, label, children, style }: {
  value: number; target: number; size?: number; stroke?: number; color?: string; track?: string; label?: string;
  children?: React.ReactNode; style?: StyleProp<ViewStyle>;
}) {
  const t = useTheme();
  const sw = stroke ?? Math.max(5, Math.round(size * 0.1));
  const r = (size - sw) / 2;
  const c = 2 * Math.PI * r;
  const p = ratioOf(value, target);
  return (
    <View style={[{ width: size, height: size, alignItems: 'center', justifyContent: 'center' }, style]}
      accessible={!!label} accessibilityRole={label ? 'progressbar' : undefined} accessibilityLabel={label}
      accessibilityValue={label ? { min: 0, max: 100, now: Math.round(p * 100) } : undefined}>
      <Svg width={size} height={size} style={StyleSheet.absoluteFill}>
        <Circle cx={size / 2} cy={size / 2} r={r} stroke={track ?? t.track} strokeWidth={sw} fill="none" />
        {p > 0 ? (
          <Circle cx={size / 2} cy={size / 2} r={r} stroke={color ?? seriesColor(t, 0)} strokeWidth={sw} fill="none"
            strokeDasharray={`${c * p} ${c}`} strokeLinecap="round" transform={`rotate(-90 ${size / 2} ${size / 2})`} />
        ) : null}
      </Svg>
      {children}
    </View>
  );
}

/** 进度条：实际对目标（超过画满）。轨道和填充两头都是圆的；颜色默认 1 号分类色。 */
export function ProgressBar({ value, target, height = 8, color, track, label, style }: {
  value: number; target: number; height?: number; color?: string; track?: string; label?: string; style?: StyleProp<ViewStyle>;
}) {
  const t = useTheme();
  const p = ratioOf(value, target);
  return (
    <View style={[{ height, borderRadius: height / 2, backgroundColor: track ?? t.track, overflow: 'hidden' }, style]}
      accessible={!!label} accessibilityRole={label ? 'progressbar' : undefined} accessibilityLabel={label}
      accessibilityValue={label ? { min: 0, max: 100, now: Math.round(p * 100) } : undefined}>
      <View style={{ width: `${p * 100}%`, height, borderRadius: height / 2, backgroundColor: color ?? seriesColor(t, 0) }} />
    </View>
  );
}

export interface SegmentItem { label: string; done: number; total: number; text?: string }

/** 一排格子：做了的上色，没做的是轨道色，格子之间留 2px。total 太大（> 24）时改画进度条，格子会细得看不清。 */
export function Segments({ done, total, height = 8, color, label }: { done: number; total: number; height?: number; color?: string; label?: string }) {
  const t = useTheme();
  const c = color ?? seriesColor(t, 0);
  if (total > 24) return <ProgressBar value={done} target={total} height={height} color={c} label={label} />;
  return (
    <View style={{ flexDirection: 'row', gap: vizMark.gap }} accessible={!!label} accessibilityLabel={label}>
      {Array.from({ length: Math.max(0, total) }, (_, i) => (
        <View key={i} style={{ flex: 1, height, borderRadius: 2, backgroundColor: i < done ? c : t.track }} />
      ))}
    </View>
  );
}

/** 分段条：逐项完成情况（每个动作做了几组）。每行：名字、「3/3」、一排格子。 */
export function SegmentBars({ items, style, ...state }: { items: SegmentItem[]; style?: StyleProp<ViewStyle> } & VizStateProps) {
  const t = useTheme();
  return (
    <VizFrame empty={!items.length} state={state} group={false} style={[{ gap: space.md }, style]}>
      {items.map((m, i) => (
        <View key={`${i}-${m.label}`} style={{ gap: 5 }} accessible accessibilityLabel={L(`${m.label}：完成 ${m.done} / ${m.total}`, `${m.label}: ${m.done} of ${m.total} done`)}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: space.sm }}>
            <T v="caption" color={t.ink2} numberOfLines={1} style={{ flex: 1, fontSize: 12, fontWeight: '400' }}>{m.label}</T>
            <T v="caption" style={{ fontSize: 12, fontWeight: '600', fontVariant: ['tabular-nums'] }}>{m.text ?? `${m.done}/${m.total}`}</T>
          </View>
          <Segments done={m.done} total={m.total} />
        </View>
      ))}
    </VizFrame>
  );
}
