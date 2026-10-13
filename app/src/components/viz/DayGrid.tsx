// 日时间格：一天的事按时长比例排在时间轴上，方块高度 = 时长；连续 2 小时以上的空闲收成一行；金线标出现在。
// 同一时间重叠的事并排分列。时间写 HH:MM（当天的钟点）。
import React from 'react';
import { Pressable, StyleSheet, Text, View, type StyleProp, type ViewStyle } from 'react-native';
import { L } from '../../i18n';
import { useTheme } from '../../theme';
import { useScaledWidth } from '../ui';
import { seriesColor, useFontScale, useSurface, VizFrame, vizText, type VizStateProps } from './core';

export interface DayBlock {
  key: string;
  title: string;
  /** HH:MM */
  start: string;
  /** HH:MM；不给 = 30 分钟 */
  end?: string;
  /** 第二行小字（实际时间、地点）；不给就写起止时间 */
  sub?: string;
  /** 颜色：第几个分类色（按种类，不按先后） */
  slot?: number;
}

const toMin = (hm: string) => { const [h, m] = hm.split(':').map(Number); return h * 60 + (m || 0); };
const hm = (min: number) => `${String(Math.floor(min / 60) % 24).padStart(2, '0')}:${String(min % 60).padStart(2, '0')}`;
/** #RRGGBB + 透明度 → #RRGGBBAA */
export const withAlpha = (hex: string, a: number) => `${hex.slice(0, 7)}${Math.round(Math.max(0, Math.min(1, a)) * 255).toString(16).padStart(2, '0')}`;

export interface DayGridProps extends VizStateProps {
  items: DayBlock[];
  /** 现在（HH:MM）：金线 */
  now?: string | null;
  /** 显示的起止（整点）；不给就按事情和现在算 */
  from?: string;
  to?: string;
  /** 每小时多高 */
  hourHeight?: number;
  /** 连续空闲多少小时以上收成一行 */
  collapse?: number;
  onPressItem?: (item: DayBlock) => void;
  style?: StyleProp<ViewStyle>;
}

type Seg = { a: number; b: number; folded: boolean };

export function DayGrid({ items, now, from, to, hourHeight = 52, collapse = 2, onPressItem, style, ...state }: DayGridProps) {
  const t = useTheme();
  const surface = useSurface();
  const fs = useFontScale();
  const lw = useScaledWidth(42);
  const fold = Math.round(26 * fs);
  const blocks = items.map((it) => {
    const a = toMin(it.start);
    const b = it.end ? Math.max(a + 5, toMin(it.end)) : a + 30;
    return { it, a, b };
  }).sort((x, y) => x.a - y.a);
  const nowMin = now ? toMin(now) : null;
  const lo = from ? toMin(from) : Math.floor(Math.min(...blocks.map((x) => x.a), nowMin ?? Infinity) / 60) * 60;
  const hi = to ? toMin(to) : Math.ceil(Math.max(...blocks.map((x) => x.b), nowMin ?? -Infinity) / 60) * 60;
  // 时间轴分段：有事的地方按比例；整点到整点之间连续 collapse 小时都空着就收成一行
  const busy: [number, number][] = [];
  for (const x of blocks) {
    const lastBusy = busy[busy.length - 1];
    if (lastBusy && x.a <= lastBusy[1]) lastBusy[1] = Math.max(lastBusy[1], x.b);
    else busy.push([x.a, x.b]);
  }
  if (nowMin != null) busy.push([nowMin, nowMin]);
  busy.sort((p, q) => p[0] - q[0]);
  const segs: Seg[] = [];
  let cur = lo;
  for (const [a, b] of busy) {
    const gapA = Math.ceil(cur / 60) * 60;
    const gapB = Math.floor(a / 60) * 60;
    if (gapB - gapA >= collapse * 60) {
      if (gapA > cur) segs.push({ a: cur, b: gapA, folded: false });
      segs.push({ a: gapA, b: gapB, folded: true });
      cur = gapB;
    }
    if (b > cur) { segs.push({ a: cur, b, folded: false }); cur = b; }
  }
  if (hi > cur) {
    const gapA = Math.ceil(cur / 60) * 60;
    if (hi - gapA >= collapse * 60) {
      if (gapA > cur) segs.push({ a: cur, b: gapA, folded: false });
      segs.push({ a: gapA, b: hi, folded: true });
    } else segs.push({ a: cur, b: hi, folded: false });
  }
  const yOf = (min: number) => {
    let y = 0;
    for (const s of segs) {
      if (min <= s.a) break;
      if (s.folded) y += min >= s.b ? fold : fold * ((min - s.a) / (s.b - s.a));
      else y += ((Math.min(min, s.b) - s.a) / 60) * hourHeight;
    }
    return y;
  };
  const total = yOf(hi) + 2;
  // 重叠的并排：贪心分列
  const cols: number[] = [];
  const colOf = new Map<string, number>();
  const groupOf = new Map<string, number>();
  let group = 0;
  let groupEnd = -1;
  const groupCols = new Map<number, number>();
  for (const x of blocks) {
    if (x.a >= groupEnd) { group++; cols.length = 0; }
    let c = cols.findIndex((end) => end <= x.a);
    if (c < 0) { c = cols.length; cols.push(x.b); } else cols[c] = x.b;
    colOf.set(x.it.key, c);
    groupOf.set(x.it.key, group);
    groupEnd = Math.max(groupEnd, x.b);
    groupCols.set(group, Math.max(groupCols.get(group) ?? 0, cols.length));
  }
  const hours: number[] = [];
  for (const s of segs) if (!s.folded) for (let h = Math.ceil(s.a / 60) * 60; h <= s.b; h += 60) if (!hours.includes(h)) hours.push(h);
  const fsLine = Math.round(14 * fs);
  return (
    <VizFrame empty={!blocks.length} state={state} group={false} style={style}>
      <View style={{ height: total }}>
        {hours.map((h) => (
          <View key={h} pointerEvents="none" style={{ position: 'absolute', left: 0, right: 0, top: yOf(h), height: 1, backgroundColor: t.viz.grid }}>
            <Text style={[vizText.axis, { position: 'absolute', left: 0, top: -fsLine / 2, width: lw - 6, color: t.ink3, backgroundColor: surface }]} numberOfLines={1}>{hm(h)}</Text>
          </View>
        ))}
        {segs.filter((s) => s.folded).map((s) => (
          <View key={`f${s.a}`} pointerEvents="none" style={{ position: 'absolute', left: lw, right: 0, top: yOf(s.a) + 3, height: fold - 6, borderRadius: 6, backgroundColor: t.bg, justifyContent: 'center', paddingHorizontal: 8 }}>
            <Text numberOfLines={1} style={[vizText.axis, { fontSize: 11, color: t.ink3 }]}>{L(`空闲 ${hm(s.a)}–${hm(s.b)}`, `Free ${hm(s.a)}–${hm(s.b)}`)}</Text>
          </View>
        ))}
        <View style={{ position: 'absolute', left: lw, right: 0, top: 0, height: total }}>
          {blocks.map(({ it, a, b }) => {
            const top = yOf(a) + 1;
            const h = Math.max(fsLine + 10, yOf(b) - yOf(a) - 2);
            const n = groupCols.get(groupOf.get(it.key) ?? 0) ?? 1;
            const c = colOf.get(it.key) ?? 0;
            const color = seriesColor(t, it.slot ?? 0);
            const sub = it.sub ?? (it.end ? `${it.start}–${it.end}` : it.start);
            const twoLines = h >= fsLine * 2 + 10;
            return (
              <Pressable key={it.key} onPress={onPressItem ? () => onPressItem(it) : undefined} disabled={!onPressItem}
                accessibilityRole={onPressItem ? 'button' : 'text'} accessibilityLabel={`${it.title} ${sub}`}
                style={{ position: 'absolute', top, height: h, left: `${(c / n) * 100}%`, width: `${100 / n}%`, paddingRight: c < n - 1 ? 2 : 0 }}>
                <View style={{ flex: 1, flexDirection: 'row', overflow: 'hidden', borderRadius: 7, backgroundColor: withAlpha(color, t.mode === 'dark' ? 0.18 : 0.12) }}>
                  <View style={{ width: 3, backgroundColor: color }} />
                  <View style={{ flex: 1, paddingHorizontal: 8, justifyContent: 'center' }}>
                    {twoLines ? (
                      <>
                        <Text numberOfLines={1} style={{ fontSize: 12, fontWeight: '600', color: t.ink }}>{it.title}</Text>
                        <Text numberOfLines={1} style={{ fontSize: 12, color: t.ink2, fontVariant: ['tabular-nums'] }}>{sub}</Text>
                      </>
                    ) : (
                      <Text numberOfLines={1} style={{ fontSize: 12, fontWeight: '600', color: t.ink }}>{it.title} <Text style={{ fontWeight: '400', color: t.ink2, fontVariant: ['tabular-nums'] }}>{sub}</Text></Text>
                    )}
                  </View>
                </View>
              </Pressable>
            );
          })}
        </View>
        {nowMin != null && nowMin >= lo && nowMin <= hi ? (
          <View pointerEvents="none" style={[styles.now, { top: yOf(nowMin) - fsLine / 2, height: fsLine }]}>
            <Text style={[vizText.value, { width: lw - 4, fontSize: 10, color: t.gold }]}>{now}</Text>
            <View style={{ flex: 1, height: 1, backgroundColor: t.gold }} />
          </View>
        ) : null}
      </View>
    </VizFrame>
  );
}

const styles = StyleSheet.create({
  now: { position: 'absolute', left: 0, right: 0, flexDirection: 'row', alignItems: 'center', gap: 4 },
});
