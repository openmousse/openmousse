// 横条：类别多或名字长时用。有序类别（分数段、截止周）按类别顺序，无序类别（地区、来源）按数值从大到小；数值写在条末。
// 单系列只用一个颜色（别按大小给条上不同的颜色，长短已经说了大小）。
import React, { useState } from 'react';
import { Pressable, Text, View, type StyleProp, type ViewStyle } from 'react-native';
import { L } from '../../i18n';
import { useTheme, vizMark } from '../../theme';
import { useScaledWidth } from '../ui';
import { seriesColor, VizFrame, vizText, type VizStateProps } from './core';

export interface HBarDatum { key: string; label: string; value: number | null; text?: string; slot?: number }

const plain = (v: number) => v.toLocaleString('en-GB', { maximumFractionDigits: 1 });

export function HBarChart({ data, sort = 'none', max, slot = 0, labelWidth = 56, onPress, fmt = plain, label, style, ...state }: {
  data: HBarDatum[];
  /** desc：按数值从大到小（无序类别）；none：照给的顺序（有序类别） */
  sort?: 'desc' | 'none';
  /** 条的满格是多少（几张图要能比时给同一个数）；不给 = 最大值 */
  max?: number;
  slot?: number;
  /** 左边名字的宽度（跟着系统字号放宽） */
  labelWidth?: number;
  onPress?: (d: HBarDatum) => void;
  fmt?: (v: number) => string;
  label?: string;
  style?: StyleProp<ViewStyle>;
} & VizStateProps) {
  const t = useTheme();
  const lw = useScaledWidth(labelWidth);
  const vw = useScaledWidth(30);
  const [sel, setSel] = useState<string | null>(null);
  const rows = sort === 'desc' ? [...data].sort((a, b) => (b.value ?? 0) - (a.value ?? 0)) : data;
  const top = max ?? Math.max(0, ...rows.map((d) => d.value ?? 0));
  const text = (d: HBarDatum) => d.text ?? (d.value != null ? fmt(d.value) : '—');
  const auto = rows.map((d) => `${d.label} ${text(d)}`).join(L('，', ', '));
  return (
    <VizFrame empty={!rows.length} state={state} label={label ?? auto} group={false} style={[{ gap: 10 }, style]}>
      {rows.map((d) => {
        const p = top > 0 && d.value ? Math.max(0, Math.min(1, d.value / top)) : 0;
        const on = sel === d.key;
        return (
          <Pressable key={d.key} onPress={() => { setSel((k) => (k === d.key ? null : d.key)); onPress?.(d); }}
            accessibilityRole={onPress ? 'button' : 'text'} accessibilityLabel={`${d.label} ${text(d)}`}
            style={{ flexDirection: 'row', alignItems: 'center', gap: 10, minHeight: 20 }}>
            <Text numberOfLines={1} style={[vizText.axis, { width: lw, fontSize: 13, color: on ? t.ink : t.ink2, fontWeight: on ? '600' : '400' }]}>{d.label}</Text>
            <View style={{ flex: 1, height: 14, justifyContent: 'center' }}>
              <View style={{
                width: p > 0 ? `${p * 100}%` : 2, minWidth: 2, height: 14, backgroundColor: p > 0 ? seriesColor(t, d.slot ?? slot) : t.track,
                borderTopRightRadius: vizMark.radius, borderBottomRightRadius: vizMark.radius, opacity: sel && !on ? 0.55 : 1,
              }} />
            </View>
            <Text numberOfLines={1} style={[vizText.value, { minWidth: vw, fontSize: 13, fontWeight: '600', textAlign: 'right', color: t.ink }]}>{text(d)}</Text>
          </Pressable>
        );
      })}
    </VizFrame>
  );
}
