// 「今天」页顶上的一天环。0 点在顶上，顺时针。青点是此刻，一屏只有今天这一个；
// 金色弧是日程占的整段时间，不按类别上色。今天 04:00 以前，0–4 点在内圈，外圈这段是虚线；
// 04:00 以后收到外圈。别的日子只有金弧。全天没有钟点，不画弧。睡眠不在今天的日程里，这里不编。
import React, { useEffect, useState } from 'react';
import { AppState, StyleSheet, Text, useWindowDimensions, View } from 'react-native';
import Svg, { Circle, Path } from 'react-native-svg';
import type { DayEvent } from '../../api/today';
import { L } from '../../i18n';
import { space, useTheme } from '../../theme';
import type { CatFilterValue } from './Cats';
import {
  RING_C, RING_DOT_R, RING_INNER_R, RING_INNER_STROKE, RING_R, RING_STROKE, RING_VB,
  arcPath, dayHasRolled, dotAt, formatHHMM, insetArc, msUntilNextMinute, nowMinute,
  parseHHMM, polar, spanToArcs, type Lane,
} from './ring';

const LABEL_R = RING_R + RING_STROKE / 2 + 16;
const HOURS = [0, 6, 12, 18] as const;

/** 环上的此刻（分钟）：跟着设备的钟在整分翻，回到前台立刻对准。时刻怎么算见 ring.ts 的 nowMinute。 */
function useRingMinute(anchorMin: number | null, anchorAt: number, live: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!live) return undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stop = false;
    const tick = () => { if (!stop) setNow(Date.now()); };
    const arm = () => {
      timer = setTimeout(() => {
        if (stop) return;
        tick();
        arm();
      }, msUntilNextMinute(Date.now()));
    };
    const first = setTimeout(tick, 0);  // 回到这一页先对准一次（不在 effect 里直接 setState）
    arm();
    const sub = AppState.addEventListener('change', (st) => { if (st === 'active') tick(); });
    return () => { stop = true; clearTimeout(first); if (timer) clearTimeout(timer); sub.remove(); };
  }, [live]);
  return nowMinute(anchorMin, anchorAt, now);
}

function paint(events: DayEvent[], rolled: boolean, cats: boolean, filter: CatFilterValue) {
  const arcs: { lane: Lane; start: number; sweep: number; opacity: number }[] = [];
  let timed = 0;
  for (const e of events) {
    const a = parseHHMM(e.slotStart);
    const b = parseHHMM(e.slotEnd);
    if (a == null || b == null) continue;
    const pieces = spanToArcs(a, b, rolled);
    if (!pieces.length) continue;
    timed += 1;
    const dim = cats && filter !== 'all' && e.category !== filter;
    const opacity = (e.state === 'skipped' ? 0.35 : 1) * (dim ? 0.22 : 1);
    for (const p of pieces) {
      const g = insetArc(p.start, p.sweep);
      if (g.sweep <= 0) continue;
      arcs.push({ lane: p.lane, start: g.start, sweep: g.sweep, opacity });
    }
  }
  return { arcs, timed };
}

export function DayRing({ isToday, live, anchorMin, anchorAt, events, allDay, cats, filter }: {
  isToday: boolean;
  /** 这一页在前台时才每分钟走一格；回到前台立刻对准。 */
  live: boolean;
  anchorMin: number | null;
  anchorAt: number;
  events: DayEvent[];
  allDay: number;
  cats: boolean;
  filter: CatFilterValue;
}) {
  const t = useTheme();
  const win = useWindowDimensions();
  const minute = useRingMinute(anchorMin, anchorAt, live && isToday);
  const rolled = dayHasRolled(isToday ? minute : null, isToday);
  const dot = isToday ? dotAt(minute, rolled) : null;
  const { arcs, timed } = paint(events, rolled, cats, filter);
  const box = Math.min(320, Math.max(220, win.width - space.lg * 2));
  const light = t.mode === 'light';
  const track = light ? t.line : t.surface2;
  const time = formatHHMM(minute);
  const bits = [
    timed ? L(`环上 ${timed} 段`, `${timed} on the ring`) : '',
    allDay ? L(`另有 ${allDay} 件全天`, `${allDay} all day`) : '',
  ].filter(Boolean);
  const body = bits.length ? bits.join(L('，', ', ')) : L('没有排进环上的时段', 'nothing scheduled on the ring');
  const label = dot
    ? L(`一天的环，现在 ${time}。${body}。`, `A day ring. Now ${time}. ${body}.`)
    : L(`一天的环。${body}。`, `A day ring. ${body}.`);
  const laneR = (lane: Lane) => (lane === 'inner' ? RING_INNER_R : RING_R);
  const laneW = (lane: Lane) => (lane === 'inner' ? RING_INNER_STROKE : RING_STROKE);
  const dotP = dot ? polar(RING_C, RING_C, laneR(dot.lane), dot.angle) : null;
  const scale = box / RING_VB;
  return (
    <View style={[styles.wrap, { width: box, height: box }]} accessible accessibilityRole="image" accessibilityLabel={label}>
      <Svg width={box} height={box} viewBox={`0 0 ${RING_VB} ${RING_VB}`} accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
        {rolled ? (
          <Circle cx={RING_C} cy={RING_C} r={RING_R} fill="none" stroke={track} strokeWidth={RING_STROKE} />
        ) : (
          <>
            <Path d={arcPath(RING_C, RING_C, RING_R, 60, 300)} fill="none" stroke={track} strokeWidth={RING_STROKE} strokeLinecap="butt" />
            <Path d={arcPath(RING_C, RING_C, RING_R, 0, 60)} fill="none" stroke={t.ink3} strokeWidth={RING_STROKE} strokeLinecap="butt" strokeDasharray="3 5" />
            <Path d={arcPath(RING_C, RING_C, RING_INNER_R, 0, 60)} fill="none" stroke={track} strokeWidth={RING_INNER_STROKE} strokeLinecap="butt" />
          </>
        )}
        {arcs.map((a, i) => (
          <Path key={i} d={arcPath(RING_C, RING_C, laneR(a.lane), a.start, a.sweep)} fill="none" stroke={t.goldFill} strokeWidth={laneW(a.lane)}
            strokeLinecap="round" strokeOpacity={a.opacity} />
        ))}
        {dot && dotP ? (
          <>
            <Circle cx={dotP.x} cy={dotP.y} r={RING_DOT_R * 2.25} fill={t.dot} opacity={0.16} />
            <Circle cx={dotP.x} cy={dotP.y} r={RING_DOT_R} fill={t.dot} stroke={light ? t.dotEdge : 'none'} strokeWidth={light ? 1.4 : 0} />
          </>
        ) : null}
      </Svg>
      {HOURS.map((h) => {
        const p = polar(RING_C, RING_C, LABEL_R, h * 15);
        return (
          <Text key={h} accessible={false} importantForAccessibility="no" style={[styles.hour, { color: t.ink3, left: p.x * scale - 16, top: p.y * scale - 8 }]}>{h}</Text>
        );
      })}
      {dot ? (
        <View pointerEvents="none" accessibilityElementsHidden importantForAccessibility="no-hide-descendants" style={styles.center}>
          <Text style={[styles.time, { color: t.ink }]}>{time}</Text>
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { alignSelf: 'center', marginTop: space.lg },
  hour: { position: 'absolute', width: 32, height: 16, textAlign: 'center', fontSize: 12, fontWeight: '500', fontVariant: ['tabular-nums'] },
  center: { position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, alignItems: 'center', justifyContent: 'center' },
  time: { fontSize: 20, fontWeight: '500', fontVariant: ['tabular-nums'], letterSpacing: -0.3 },
});
