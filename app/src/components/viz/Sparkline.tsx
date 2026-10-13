// 迷你趋势：放在数字旁边，不画坐标轴；末端一个实心点标出当前值，必要时标出最低点（空心）。
import React from 'react';
import { View, type StyleProp, type ViewStyle } from 'react-native';
import Svg, { Circle, Path, Polyline } from 'react-native-svg';
import { useTheme, vizMark } from '../../theme';
import { dotProps, seriesColor, useSurface, useWidth } from './core';

/** 最后一个有数的位置（没有 = -1）。 */
export function lastIndex(values: (number | null)[]): number {
  for (let i = values.length - 1; i >= 0; i--) if (values[i] != null) return i;
  return -1;
}

/** 按 null 断开的几段折线（没数据的那几天不连）。 */
export function segmentsOf(pts: ({ x: number; y: number } | null)[]): { x: number; y: number }[][] {
  const out: { x: number; y: number }[][] = [];
  let cur: { x: number; y: number }[] = [];
  for (const p of pts) {
    if (p) cur.push(p);
    else if (cur.length) { out.push(cur); cur = []; }
  }
  if (cur.length) out.push(cur);
  return out;
}

export function Sparkline({ values, height = 40, width, color, min = false, area = false, label, style }: {
  values: (number | null)[];
  height?: number;
  /** 不给就占满父元素的宽度 */
  width?: number;
  color?: string;
  /** 标出最低点（空心圈） */
  min?: boolean;
  /** 线下面一层 10% 的浅色 */
  area?: boolean;
  /** 读屏念的一句：「近 20 天恢复分，最低 40，当前 66」 */
  label?: string;
  style?: StyleProp<ViewStyle>;
}) {
  const t = useTheme();
  const surface = useSurface();
  const [measured, onLayout] = useWidth();
  const w = width ?? measured;
  const c = color ?? seriesColor(t, 0);
  const nums = values.filter((v): v is number => v != null);
  const pad = vizMark.dot / 2 + vizMark.ring;  // 末端的点和外圈不被切掉
  let lo = Math.min(...nums);
  let hi = Math.max(...nums);
  if (!nums.length) { lo = 0; hi = 1; }
  if (hi === lo) { lo -= 1; hi += 1; }
  const n = values.length;
  const xOf = (i: number) => (n > 1 ? pad + (i / (n - 1)) * Math.max(0, w - pad * 2) : w / 2);
  const yOf = (v: number) => pad + (1 - (v - lo) / (hi - lo)) * (height - pad * 2);
  const pts = values.map((v, i) => (v == null ? null : { x: xOf(i), y: yOf(v) }));
  const segs = segmentsOf(pts);
  const lastI = lastIndex(values);
  const minI = min && nums.length > 2 ? values.indexOf(Math.min(...nums)) : -1;
  return (
    <View style={[{ height, width }, style]} onLayout={width == null ? onLayout : undefined} accessible={!!label} accessibilityRole={label ? 'image' : undefined} accessibilityLabel={label}>
      {w > 0 && nums.length ? (
        <Svg width={w} height={height}>
          {area ? segs.map((s, k) => (s.length > 1 ? (
            <Path key={`a${k}`} d={`M${s[0].x},${height} ${s.map((p) => `L${p.x},${p.y}`).join(' ')} L${s[s.length - 1].x},${height} Z`} fill={c} fillOpacity={vizMark.area} />
          ) : null)) : null}
          {segs.map((s, k) => (s.length > 1 ? (
            <Polyline key={k} points={s.map((p) => `${p.x},${p.y}`).join(' ')} fill="none" stroke={c} strokeWidth={vizMark.line} strokeLinejoin="round" strokeLinecap="round" />
          ) : null))}
          {minI >= 0 && minI !== lastI ? <Circle cx={xOf(minI)} cy={yOf(values[minI] as number)} r={3} fill={surface} stroke={t.ink2} strokeWidth={1.5} /> : null}
          {lastI >= 0 ? <Circle cx={xOf(lastI)} cy={yOf(values[lastI] as number)} {...dotProps(c, surface)} /> : null}
        </Svg>
      ) : null}
    </View>
  );
}
