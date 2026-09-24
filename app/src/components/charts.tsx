import React, { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import Svg, { Circle } from 'react-native-svg';
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
        <Circle cx={size / 2} cy={size / 2} r={r} stroke={color ?? t.chartA} strokeWidth={stroke} fill="none"
          strokeDasharray={`${c * p} ${c}`} strokeLinecap="round" transform={`rotate(-90 ${size / 2} ${size / 2})`} />
      </Svg>
      {children}
    </View>
  );
}

/** 横向进度条：值 / 目标，数值用文字色，不用系列色。 */
export function Meter({ label, value, target, unit }: { label: string; value: number; target?: number; unit: string }) {
  const t = useTheme();
  const p = target ? Math.max(0, Math.min(1, value / target)) : 0;
  return (
    <View style={{ gap: 6 }} accessibilityLabel={target ? `${label} ${value} / ${target} ${unit}` : `${label} ${value} ${unit}`}>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <Text style={[type.callout, { color: t.ink2 }]}>{label}</Text>
        <Text style={[type.callout, { color: t.ink, fontVariant: ['tabular-nums'] }]}>
          {value}<Text style={{ color: t.ink3 }}>{target ? ` / ${target} ${unit}` : ` ${unit}`}</Text>
        </Text>
      </View>
      {target ? (
        <View style={{ height: 6, borderRadius: 3, backgroundColor: t.track, overflow: 'hidden' }}>
          <View style={{ width: `${p * 100}%`, height: 6, borderRadius: 3, backgroundColor: t.chartA }} />
        </View>
      ) : null}
    </View>
  );
}

/** 一周柱状图，单系列。点一根柱子显示它的数值（手机上的 hover 替代）。 */
export function WeekBars({ days, unit, todayIndex }: { days: { d: string; minutes: number; label: string }[]; unit: string; todayIndex: number }) {
  const t = useTheme();
  const [sel, setSel] = useState<number>(todayIndex);
  const max = Math.max(60, ...days.map((x) => x.minutes));
  const H = 96;
  const cur = days[sel];
  return (
    <View>
      <Text style={[type.callout, { color: t.ink2, marginBottom: 10, fontVariant: ['tabular-nums'] }]}>
        {weekdayName(cur.d)} · {cur.label}{cur.minutes > 0 ? ` · ${cur.minutes} ${unit}` : sel > todayIndex ? L(' · 未开始', ' · Not yet') : ' · 0'}
      </Text>
      <View style={{ flexDirection: 'row', alignItems: 'flex-end', height: H, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }}>
        {days.map((x, i) => {
          const h = Math.round((x.minutes / max) * (H - 4));
          const on = i === sel;
          return (
            <Pressable key={x.d} onPress={() => setSel(i)} style={{ flex: 1, height: H, alignItems: 'center', justifyContent: 'flex-end' }}
              accessibilityRole="button" accessibilityLabel={`${weekdayName(x.d)} ${x.label} ${x.minutes} ${unit}`}>
              <View style={{ width: 14, height: Math.max(h, x.minutes > 0 ? 4 : 0), backgroundColor: t.chartA, opacity: on ? 1 : 0.55, borderTopLeftRadius: 4, borderTopRightRadius: 4 }} />
            </Pressable>
          );
        })}
      </View>
      <View style={{ flexDirection: 'row', marginTop: 6 }}>
        {days.map((x, i) => (
          <Text key={x.d} style={[type.caption, { flex: 1, textAlign: 'center', color: i === sel ? t.ink : t.ink3, fontWeight: i === todayIndex ? '700' : '500' }]}>{weekdayShort(x.d)}</Text>
        ))}
      </View>
    </View>
  );
}
