import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
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
