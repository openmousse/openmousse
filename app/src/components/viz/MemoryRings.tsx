// 年轮（记忆页第一张卡，2026-10-20，设计第 20 张第三版）：最近几天新长的记忆，一天一圈：外圈是今天，往里依次是前几天，一个点一条。
// 待确认的（你说的待确认、别人说的待定）画在最外圈外面，琥珀色（等你点头）；确认了，点从外面落到它那天的圈上。
// 点按一个点 = onPick（打开那一条）。点用绝对定位的 View 画（transform 走原生驱动，网页上也是 CSS 变换），圈用 SVG。
// 动效快（新点长出来 240ms、换位置 280ms）；系统开了「减少动态效果」就不动。
import React, { useEffect, useState } from 'react';
import { AccessibilityInfo, Animated, Easing, Platform, StyleSheet, View, type GestureResponderEvent, type StyleProp, type ViewStyle } from 'react-native';
import Svg, { Circle } from 'react-native-svg';
import { L } from '../../i18n';
import { useTheme, vizMark } from '../../theme';
import { T } from '../ui';
import { seriesColor, useSurface, useWidth, VizFrame, type VizStateProps } from './core';

const native = Platform.OS !== 'web';
const GOLDEN = 137.508;  // 每一圈的起点错开一个黄金角：几圈的点不会排成一条放射线

export interface RingDot {
  key: string;
  /** 第几天：0 = 今天（最外圈），1…days-1 = 往里依次前几天；null = 待确认（画在环外） */
  day: number | null;
  /** 这一条的一句话（读屏念、点按时给调用方） */
  label: string;
}

/** 系统的「减少动态效果」（和 setupKit 的 useReduceMotion 一样；这里不引 setupKit：它反过来引了素材库）。 */
function useStill(): boolean {
  const [v, setV] = useState(false);
  useEffect(() => {
    let live = true;
    AccessibilityInfo.isReduceMotionEnabled().then((x) => { if (live) setV(!!x); }).catch(() => {});
    const sub = AccessibilityInfo.addEventListener?.('reduceMotionChanged', (x: boolean) => setV(!!x));
    return () => { live = false; sub?.remove?.(); };
  }, []);
  return v;
}

/** 一个点：第一次出现时从 0 长到原大，位置变了（待确认 → 落到圈上）滑过去。x / y 是圆心。 */
function Dot({ x, y, color, size, ring, surface, big, still }: { x: number; y: number; color: string; size: number; ring: number; surface: string; big: boolean; still: boolean }) {
  const [pos] = useState(() => new Animated.ValueXY({ x, y }));
  const [grow] = useState(() => new Animated.Value(still ? 1 : 0));
  useEffect(() => {
    if (still) { pos.setValue({ x, y }); grow.setValue(1); return undefined; }
    const a = Animated.parallel([
      Animated.timing(pos, { toValue: { x, y }, duration: 280, easing: Easing.out(Easing.cubic), useNativeDriver: native }),
      Animated.timing(grow, { toValue: 1, duration: 240, easing: Easing.out(Easing.quad), useNativeDriver: native }),
    ]);
    a.start();
    return () => a.stop();
  }, [x, y, still, pos, grow]);
  const d = (big ? size + 4 : size) + ring * 2;  // 点本身 8，外面再包一圈 2 的底色（vizMark：8px 的点带 2px 的圈）
  return (
    <Animated.View pointerEvents="none" style={{
      position: 'absolute', left: -d / 2, top: -d / 2, width: d, height: d, borderRadius: d / 2, backgroundColor: color, borderWidth: ring, borderColor: surface,
      transform: [{ translateX: pos.x }, { translateY: pos.y }, { scale: grow }],
    }} />
  );
}

/**
 * 年轮。dots：一条一个（day = 第几天，null = 待确认）。size 不给就占满宽度（最大 360，圈要画大）。
 * 读屏念「最近 7 天新增 N 条：今天 a 条……；待确认 p 条」，label 可以换掉。没有点也照样画圈，中间写 emptyText。
 */
export function MemoryRings({ dots, days = 7, size: fixed, onPick, label, style, ...state }: {
  dots: RingDot[]; days?: number; size?: number; onPick?: (key: string) => void; label?: string; style?: StyleProp<ViewStyle>;
} & VizStateProps) {
  const t = useTheme();
  const surface = useSurface();
  const still = useStill();
  const [w, onLayout] = useWidth();
  const [sel, setSel] = useState<string | null>(null);
  const size = Math.max(0, Math.min(fixed ?? w, 360));
  const c = size / 2;
  const dot = vizMark.dot;
  const outer = c - dot - 10;              // 最外圈（今天）；外面留一圈给待确认的点
  const inner = Math.max(size * 0.12, 18);  // 最里圈
  const step = days > 1 ? (outer - inner) / (days - 1) : 0;
  const radius = (day: number) => outer - day * step;

  // 每一圈的点平均排开（从 12 点起，顺时针）；待确认的排在最外圈外面，从 12 点起往两边
  const placed: { key: string; x: number; y: number; pending: boolean; label: string }[] = [];
  const perDay = new Map<number, RingDot[]>();
  const pending: RingDot[] = [];
  for (const d of dots) {
    if (d.day == null) pending.push(d);
    else if (d.day >= 0 && d.day < days) perDay.set(d.day, [...(perDay.get(d.day) ?? []), d]);
  }
  for (const [day, list] of perDay) {
    const r = radius(day);
    const start = -90 + day * GOLDEN;
    list.forEach((d, i) => {
      const a = ((start + (360 / list.length) * i) * Math.PI) / 180;
      placed.push({ key: d.key, x: c + r * Math.cos(a), y: c + r * Math.sin(a), pending: false, label: d.label });
    });
  }
  const pr = outer + dot / 2 + 6;
  const gap = Math.min(22, (2 * Math.PI * pr) / Math.max(1, pending.length));  // 弧上相邻两点的角距（度换算前是弧长）
  pending.forEach((d, i) => {
    const k = i === 0 ? 0 : (i % 2 ? 1 : -1) * Math.ceil(i / 2);  // 12 点、右、左、右……
    const a = -Math.PI / 2 + (k * gap) / pr;
    placed.push({ key: d.key, x: c + pr * Math.cos(a), y: c + pr * Math.sin(a), pending: true, label: d.label });
  });

  // 点按：找离手指最近的点（24pt 以内），点小也点得中
  const pick = (e: GestureResponderEvent) => {
    if (!onPick) return;
    const { locationX: x, locationY: y } = e.nativeEvent;
    let best: { key: string; d: number } | null = null;
    for (const p of placed) {
      const d = Math.hypot(p.x - x, p.y - y);
      if (d <= vizMark.hit && (!best || d < best.d)) best = { key: p.key, d };
    }
    if (best) { setSel(best.key); onPick(best.key); }
  };

  const total = dots.filter((d) => d.day != null).length;
  const auto = [
    L(`最近 ${days} 天新增 ${total} 条记忆`, `${total} new ${total === 1 ? 'memory' : 'memories'} in the last ${days} days`),
    ...Array.from({ length: days }, (_, day) => {
      const n = perDay.get(day)?.length ?? 0;
      if (!n) return '';
      return day === 0 ? L(`今天 ${n} 条`, `today ${n}`) : day === 1 ? L(`昨天 ${n} 条`, `yesterday ${n}`) : L(`${day} 天前 ${n} 条`, `${day} days ago ${n}`);
    }).filter(Boolean),
    pending.length ? L(`待确认 ${pending.length} 条`, `${pending.length} to confirm`) : '',
  ].filter(Boolean).join(L('；', '; '));
  return (
    <VizFrame empty={false} state={state} label={label ?? auto} style={[{ alignItems: 'center' }, style]}>
      <View onLayout={fixed ? undefined : onLayout} style={{ width: '100%', alignItems: 'center' }}>
        {size ? (
          <View style={{ width: size, height: size }}>
            <Svg width={size} height={size} style={StyleSheet.absoluteFill}>
              {Array.from({ length: days }, (_, day) => (
                <Circle key={day} cx={c} cy={c} r={radius(day)} fill="none" stroke={day === 0 ? t.viz.axis : t.viz.grid} strokeWidth={day === 0 ? 1.5 : 1} />
              ))}
            </Svg>
            {placed.map((p) => (
              <Dot key={p.key} x={p.x} y={p.y} size={dot} ring={vizMark.ring} surface={surface} still={still} big={sel === p.key}
                color={p.pending ? t.warn : sel === p.key ? t.viz.emph : seriesColor(t, 0)} />
            ))}
            {!dots.length && state.emptyText ? (
              <View pointerEvents="none" style={[StyleSheet.absoluteFill, { alignItems: 'center', justifyContent: 'center', padding: inner * 2 }]}>
                <T v="caption" color={t.ink3} style={{ textAlign: 'center', fontWeight: '400' }}>{state.emptyText}</T>
              </View>
            ) : null}
            {onPick ? (
              <View style={StyleSheet.absoluteFill} onStartShouldSetResponder={() => true} onResponderTerminationRequest={() => true}
                onResponderRelease={pick} />
            ) : null}
          </View>
        ) : null}
      </View>
    </VizFrame>
  );
}
