// 对比：前后对比（两个点一条线，上次空心、这次实心）、对比清单（每行这次 / 上次 + 变化标签）、目标条（实际对目标，目标处一道竖线）。
import React from 'react';
import { StyleSheet, Text, View, type StyleProp, type ViewStyle } from 'react-native';
import Svg, { Circle, Line } from 'react-native-svg';
import { L } from '../../i18n';
import { space, useTheme, vizMark } from '../../theme';
import { T } from '../ui';
import { ChangeTag, dotProps, seriesColor, useSurface, VizFrame, vizText, type Direction, type VizStateProps } from './core';

export interface ComparePoint { label: string; value: number; text?: string }

/** 前后对比（哑铃图）：上次空心灰点，这次实心带底色描边，中间 2px 线；下面写两次的日期，点旁写数（上升写在点上方，下降写在下方）。 */
export function BeforeAfter({ before, after, width = 120, height = 72, color, label }: {
  before: ComparePoint; after: ComparePoint; width?: number; height?: number; color?: string; label?: string;
}) {
  const t = useTheme();
  const surface = useSurface();
  const c = color ?? seriesColor(t, 0);
  const lo = Math.min(before.value, after.value);
  const hi = Math.max(before.value, after.value);
  const plotH = height - 18;  // 下面 18 写日期
  const pad = 14;
  const yOf = (v: number) => (hi === lo ? plotH / 2 : pad + (1 - (v - lo) / (hi - lo)) * (plotH - pad * 2));
  const x0 = 14;
  const x1 = width - 14;
  const y0 = yOf(before.value);
  const y1 = yOf(after.value);
  const up = after.value >= before.value;
  const labelTop = (y: number) => (up ? Math.max(0, y - 16) : Math.min(plotH - 12, y + 3));
  const bt = before.text ?? String(before.value);
  const at = after.text ?? String(after.value);
  return (
    <View style={{ width, height }} accessible accessibilityRole="image" accessibilityLabel={label ?? `${before.label} ${bt}${L('，', ', ')}${after.label} ${at}`}>
      <Svg width={width} height={height} style={StyleSheet.absoluteFill}>
        <Line x1={x0} y1={y0} x2={x1} y2={y1} stroke={c} strokeWidth={vizMark.line} strokeLinecap="round" />
        <Circle cx={x0} cy={y0} r={4} fill={surface} stroke={t.ink3} strokeWidth={1.5} />
        <Circle cx={x1} cy={y1} {...dotProps(c, surface, 4.5)} />
      </Svg>
      <Text numberOfLines={1} style={[vizText.axis, styles.abs, { left: x0 + 6, top: labelTop(y0), color: t.ink3 }]}>{bt}</Text>
      <Text numberOfLines={1} style={[vizText.axis, styles.abs, { right: width - x1 + 6, top: labelTop(y1), color: t.ink, textAlign: 'right' }]}>{at}</Text>
      <Text numberOfLines={1} style={[vizText.axis, styles.abs, { left: x0 - 18, width: 36, top: height - 14, textAlign: 'center', color: t.ink3 }]}>{before.label}</Text>
      <Text numberOfLines={1} style={[vizText.axis, styles.abs, { left: x1 - 18, width: 36, top: height - 14, textAlign: 'center', color: t.ink, fontWeight: '700' }]}>{after.label}</Text>
    </View>
  );
}

export interface CompareItem {
  key: string;
  name: string;
  /** 这次（已格式化：「85 kg × 5 × 3」） */
  now: string;
  /** 上次 */
  last?: string;
  dir: Direction;
  /** 变化标签上的字：「进步」「持平」「辅助减少」「+5 kg」 */
  verdict: string;
  /** 这个变化好不好（决定颜色）；不给 = 灰 */
  good?: boolean | null;
}

/** 对比清单：每行名字、这次 · 上次，右边变化标签（方向箭头 + 字）。 */
export function CompareList({ items, title, style, ...state }: { items: CompareItem[]; title?: string; style?: StyleProp<ViewStyle> } & VizStateProps) {
  const t = useTheme();
  return (
    <VizFrame empty={!items.length} state={state} group={false} style={style}>
      {title ? <T v="caption" color={t.ink3} style={{ fontWeight: '700', paddingBottom: 4 }}>{title}</T> : null}
      {items.map((it, i) => (
        <View key={it.key} style={[styles.row, i > 0 && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }]}
          accessible accessibilityLabel={`${it.name}：${it.now}${it.last ? L(`，上次 ${it.last}`, `, last ${it.last}`) : ''}，${it.verdict}`}>
          <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
            <Text numberOfLines={1} style={{ fontSize: 14, fontWeight: '600', color: t.ink }}>{it.name}</Text>
            <Text numberOfLines={1} style={{ fontSize: 12, color: t.ink2, fontVariant: ['tabular-nums'] }}>
              {it.now}{it.last ? <Text style={{ color: t.ink3 }}>{L(` · 上次 ${it.last}`, ` · last ${it.last}`)}</Text> : null}
            </Text>
          </View>
          <ChangeTag dir={it.dir} text={it.verdict} good={it.good} />
        </View>
      ))}
    </VizFrame>
  );
}

/**
 * 目标条：实际对目标。整条的满格比目标长一点（超过目标也画得出来），目标处一道竖线。
 * 例：蛋白质 83 / 136 g。超过目标且超过不好时（热量）给 over="warn"。
 */
export function TargetBar({ label, value, target, max, text, targetText, note, color, over, style }: {
  label: string; value: number; target: number;
  /** 满格是多少（不给 = 目标和实际里大的那个再加 15%） */
  max?: number;
  /** 已格式化的实际值、目标（「83」「136 g」） */
  text?: string; targetText?: string;
  /** 下面一行小字 */
  note?: string;
  color?: string;
  /** 超过目标时：warn 用注意色（配文字「超出」） */
  over?: 'warn';
  style?: StyleProp<ViewStyle>;
}) {
  const t = useTheme();
  const full = max ?? Math.max(target, value) * 1.15;
  const p = full > 0 ? Math.max(0, Math.min(1, value / full)) : 0;
  const tp = full > 0 ? Math.max(0, Math.min(1, target / full)) : 0;
  const isOver = value > target;
  const fill = isOver && over === 'warn' ? t.warn : color ?? seriesColor(t, 0);
  const vt = text ?? String(value);
  const tt = targetText ?? String(target);
  return (
    <View style={[{ gap: space.sm }, style]} accessible accessibilityRole="progressbar"
      accessibilityLabel={L(`${label} ${vt}，目标 ${tt}${isOver ? '，已超出' : ''}`, `${label} ${vt}, target ${tt}${isOver ? ', over' : ''}`)}>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline', gap: space.sm }}>
        <Text numberOfLines={1} style={{ fontSize: 13, color: t.ink2, flexShrink: 1 }}>{label}</Text>
        <Text style={{ fontSize: 13, color: t.ink3, fontVariant: ['tabular-nums'] }}>
          <Text style={{ fontWeight: '700', color: t.ink }}>{vt}</Text>{` / ${tt}`}{isOver && over === 'warn' ? <Text style={{ color: t.warn, fontWeight: '600' }}>{L(' 超出', ' over')}</Text> : null}
        </Text>
      </View>
      <View style={{ height: 12, borderRadius: 6, backgroundColor: t.track, justifyContent: 'center' }}>
        <View style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: `${p * 100}%`, borderRadius: 6, backgroundColor: fill }} />
        <View style={{ position: 'absolute', left: `${tp * 100}%`, top: -4, width: 2, height: 20, marginLeft: -1, borderRadius: 1, backgroundColor: t.ink }} />
      </View>
      {note ? <Text style={{ fontSize: 11, color: t.ink3 }}>{note}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  abs: { position: 'absolute' },
  row: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: 9 },
});
