// 占比：环形（3–6 份）和百分比条（两份，或者要和别的条并排比较时）。
// 只有两份别用环形：用百分比条或者直接写数字。超过 6 份，按大小留前 5 份，其余合并成「其他」（灰，带文字）。
import React, { useState } from 'react';
import { Pressable, StyleSheet, Text, View, type StyleProp, type ViewStyle } from 'react-native';
import Svg, { Circle } from 'react-native-svg';
import { L } from '../../i18n';
import { space, useTheme, vizMark } from '../../theme';
import { T } from '../ui';
import { Legend, seriesColor, SwatchMark, VizFrame, vizText, type VizStateProps } from './core';

export interface ShareDatum { key: string; label: string; value: number; text?: string; slot?: number }

const plain = (v: number) => v.toLocaleString('en-GB', { maximumFractionDigits: 1 });
const pctOf = (v: number, total: number) => (total > 0 ? Math.round((v / total) * 100) : 0);

/** 超过 max 份：按大小留前 max - 1 份，其余合并成「其他」（slot = -1 → 灰）。 */
export function foldOther(data: ShareDatum[], max = 6, fmt: (v: number) => string = plain): ShareDatum[] {
  if (data.length <= max) return data;
  const sorted = [...data].sort((a, b) => b.value - a.value);
  const keep = sorted.slice(0, max - 1);
  const rest = sorted.slice(max - 1).reduce((a, d) => a + d.value, 0);
  return [...keep, { key: '__other', label: L('其他', 'Other'), value: rest, text: fmt(rest), slot: -1 }];
}

export interface DonutProps extends VizStateProps {
  data: ShareDatum[];
  size?: number;
  thickness?: number;
  /** 中间的大数字和一行说明（「63」「个岗位」）；点了某一份就换成那一份 */
  center?: { value: string; label?: string };
  /** 图例里写百分比 */
  pct?: boolean;
  fmt?: (v: number) => string;
  label?: string;
  style?: StyleProp<ViewStyle>;
}

export function Donut({ data, size = 120, thickness = 16, center, pct, fmt = plain, label, style, ...state }: DonutProps) {
  const t = useTheme();
  const [sel, setSel] = useState<string | null>(null);
  const rows = foldOther(data.filter((d) => d.value > 0), 6, fmt);
  const total = rows.reduce((a, d) => a + d.value, 0);
  const colorOf = (d: ShareDatum, i: number) => seriesColor(t, d.slot ?? i);
  const text = (d: ShareDatum) => d.text ?? fmt(d.value);
  const auto = rows.map((d) => L(`${d.label} ${text(d)}（${pctOf(d.value, total)}%）`, `${d.label} ${text(d)} (${pctOf(d.value, total)}%)`)).join(L('，', ', '));
  if (rows.length === 2) return <PercentBar parts={rows} fmt={fmt} label={label} style={style} {...state} />;
  const r = (size - thickness) / 2;
  const C = 2 * Math.PI * r;
  const gap = rows.length > 1 ? vizMark.gap : 0;
  const arcs: { key: string; color: string; dash: number; offset: number; dim: boolean }[] = [];
  for (let i = 0, acc = 0; i < rows.length; i++) {
    const d = rows[i];
    const len = (d.value / (total || 1)) * C;
    arcs.push({ key: d.key, color: colorOf(d, i), dash: Math.max(0.5, len - gap), offset: -acc, dim: !!sel && sel !== d.key });
    acc += len;
  }
  const picked = rows.find((d) => d.key === sel);
  const mid = picked ? { value: text(picked), label: `${picked.label} · ${pctOf(picked.value, total)}%` } : center;
  return (
    <VizFrame empty={!rows.length} height={size} state={state} label={label ?? auto} group={false} style={[styles.donutRow, style]}>
      <View style={{ width: size, height: size, alignItems: 'center', justifyContent: 'center' }} accessible accessibilityRole="image" accessibilityLabel={label ?? auto}>
        <Svg width={size} height={size} style={StyleSheet.absoluteFill}>
          {arcs.map((a) => (
            <Circle key={a.key} cx={size / 2} cy={size / 2} r={r} fill="none" stroke={a.color} strokeWidth={thickness} strokeOpacity={a.dim ? 0.3 : 1}
              strokeDasharray={`${a.dash} ${C}`} strokeDashoffset={a.offset} transform={`rotate(-90 ${size / 2} ${size / 2})`} />
          ))}
        </Svg>
        {mid ? (
          <View style={{ alignItems: 'center', maxWidth: size - thickness * 2 - 6 }}>
            <T numberOfLines={1} adjustsFontSizeToFit style={{ fontSize: Math.round(size * 0.18), lineHeight: Math.round(size * 0.22), fontWeight: '700' }}>{mid.value}</T>
            {mid.label ? <T v="caption" color={t.ink2} numberOfLines={2} style={{ fontSize: 10, fontWeight: '400', textAlign: 'center' }}>{mid.label}</T> : null}
          </View>
        ) : null}
      </View>
      <View style={{ flex: 1, gap: 9 }}>
        {rows.map((d, i) => {
          const on = sel === d.key;
          return (
            <Pressable key={d.key} onPress={() => setSel((k) => (k === d.key ? null : d.key))} accessibilityRole="button" accessibilityState={{ selected: on }}
              accessibilityLabel={L(`${d.label} ${text(d)}，${pctOf(d.value, total)}%`, `${d.label} ${text(d)}, ${pctOf(d.value, total)}%`)} style={styles.legendRow}>
              <SwatchMark color={colorOf(d, i)} />
              <T v="caption" color={on ? t.ink : t.ink2} numberOfLines={1} style={{ flex: 1, fontSize: 12, fontWeight: on ? '600' : '400' }}>{d.label}</T>
              <T v="caption" style={{ fontSize: 12, fontWeight: '600', fontVariant: ['tabular-nums'] }}>{text(d)}</T>
              {pct ? <T v="caption" color={t.ink3} style={{ fontSize: 12, fontWeight: '400', minWidth: 32, textAlign: 'right', fontVariant: ['tabular-nums'] }}>{`${pctOf(d.value, total)}%`}</T> : null}
            </Pressable>
          );
        })}
      </View>
    </VizFrame>
  );
}

/** 百分比条：两份时两头各写名字、数值和比例；更多份时下面一行图例。段与段之间留 2px。 */
export function PercentBar({ parts, height = 16, fmt = plain, label, style, ...state }: {
  parts: ShareDatum[]; height?: number; fmt?: (v: number) => string; label?: string; style?: StyleProp<ViewStyle>;
} & VizStateProps) {
  const t = useTheme();
  const rows = parts.filter((d) => d.value > 0);
  const total = rows.reduce((a, d) => a + d.value, 0);
  const colorOf = (d: ShareDatum, i: number) => seriesColor(t, d.slot ?? i);
  const text = (d: ShareDatum) => d.text ?? fmt(d.value);
  const auto = rows.map((d) => L(`${d.label} ${text(d)}，占 ${pctOf(d.value, total)}%`, `${d.label} ${text(d)}, ${pctOf(d.value, total)}%`)).join(L('；', '; '));
  const two = rows.length === 2;
  const end = (d: ShareDatum, i: number, right?: boolean) => (
    <View style={[styles.end, right && { flexDirection: 'row-reverse' }]}>
      <SwatchMark color={colorOf(d, i)} />
      <Text style={[vizText.value, { fontSize: 13, color: t.ink }]} numberOfLines={1}>{`${d.label} ${text(d)}`}</Text>
      <Text style={[vizText.axis, { fontSize: 13, color: t.ink3 }]}>{`${pctOf(d.value, total)}%`}</Text>
    </View>
  );
  return (
    <VizFrame empty={!rows.length} height={height + 24} state={state} label={label ?? auto} style={[{ gap: space.sm }, style]}>
      {two ? <View style={{ flexDirection: 'row', justifyContent: 'space-between', gap: space.sm }}>{end(rows[0], 0)}{end(rows[1], 1, true)}</View> : null}
      <View style={{ flexDirection: 'row', gap: vizMark.gap, height }}>
        {rows.map((d, i) => (
          <View key={d.key} style={{
            flex: d.value, minWidth: 2, backgroundColor: colorOf(d, i),
            borderTopLeftRadius: i === 0 ? vizMark.radius : 0, borderBottomLeftRadius: i === 0 ? vizMark.radius : 0,
            borderTopRightRadius: i === rows.length - 1 ? vizMark.radius : 0, borderBottomRightRadius: i === rows.length - 1 ? vizMark.radius : 0,
          }} />
        ))}
      </View>
      {!two ? <Legend items={rows.map((d, i) => ({ label: d.label, color: colorOf(d, i), value: `${text(d)} · ${pctOf(d.value, total)}%` }))} /> : null}
    </VizFrame>
  );
}

const styles = StyleSheet.create({
  donutRow: { flexDirection: 'row', alignItems: 'center', gap: space.lg },
  legendRow: { flexDirection: 'row', alignItems: 'center', gap: 6, minHeight: 20 },
  end: { flexDirection: 'row', alignItems: 'center', gap: 5, flexShrink: 1 },
});
