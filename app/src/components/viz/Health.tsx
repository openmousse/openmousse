// 健康类：营养环（三大营养素按热量的比例）、睡眠分期（按时间的分期图，深度越深颜色越深 / 越亮）、连续打卡（最近 N 天一格一天）。
import React, { useState } from 'react';
import { StyleSheet, Text, View, type GestureResponderEvent, type StyleProp, type ViewStyle } from 'react-native';
import Svg, { Line, Rect } from 'react-native-svg';
import { L } from '../../i18n';
import { space, useTheme } from '../../theme';
import { T, useScaledWidth } from '../ui';
import { seriesColor, useFontScale, useWidth, VizFrame, vizText, type VizStateProps } from './core';
import { Donut } from './Share';

/** 分钟 → 「4 小时 56 分」/「4h 56m」 */
export const durText = (min: number) => {
  const h = Math.floor(min / 60);
  const m = Math.round(min % 60);
  return h ? L(`${h} 小时${m ? ` ${m} 分` : ''}`, `${h}h${m ? ` ${m}m` : ''}`) : L(`${m} 分`, `${m}m`);
};

// —— 营养环 ——

/**
 * 营养环：蛋白质 / 碳水 / 脂肪按热量算比例（4 / 4 / 9 kcal 每克），中间写总热量。图例写克数和比例。
 * kcal 不给就按三大营养素算。
 */
export function NutritionRing({ protein, carbs, fat, kcal, centerLabel, size = 112, style }: {
  protein: number; carbs: number; fat: number; kcal?: number; centerLabel?: string; size?: number; style?: StyleProp<ViewStyle>;
}) {
  const t = useTheme();
  const total = kcal ?? Math.round(protein * 4 + carbs * 4 + fat * 9);
  return (
    <View style={[{ gap: space.sm }, style]}>
      <Donut size={size} thickness={Math.round(size * 0.125)} pct center={{ value: total.toLocaleString('en-GB'), label: centerLabel ?? 'kcal' }}
        data={[
          { key: 'protein', label: L('蛋白质', 'Protein'), value: protein * 4, text: `${protein} g` },
          { key: 'carbs', label: L('碳水', 'Carbs'), value: carbs * 4, text: `${carbs} g` },
          { key: 'fat', label: L('脂肪', 'Fat'), value: fat * 9, text: `${fat} g` },
        ]}
        label={L(`共 ${total} 千卡；按热量计：蛋白质 ${protein} 克，碳水 ${carbs} 克，脂肪 ${fat} 克`, `${total} kcal; protein ${protein} g, carbs ${carbs} g, fat ${fat} g`)} />
      <T v="caption" color={t.ink3} style={{ fontWeight: '400', fontSize: 11 }}>{L('比例按热量计算', 'Shares are by calories')}</T>
    </View>
  );
}

// —— 睡眠分期 ——

export type SleepStage = 'awake' | 'rem' | 'core' | 'deep';
/** 一段睡眠：起止写本地时间 YYYY-MM-DDTHH:MM（只用来排位置，标签直接取 HH:MM） */
export interface SleepSeg { start: string; end: string; stage: SleepStage }

const STAGES: SleepStage[] = ['awake', 'rem', 'core', 'deep'];
const stageName = (s: SleepStage) => ({ awake: L('清醒', 'Awake'), rem: 'REM', core: L('核心', 'Core'), deep: L('深度', 'Deep') })[s];
const ms = (iso: string) => Date.parse(iso.length === 16 ? `${iso}:00Z` : iso);
const hmOf = (iso: string) => iso.slice(11, 16);

/**
 * 睡眠分期：清醒、REM、核心、深度各一行，按时间画段，段与段之间一道细竖线连起来。
 * 分期是有顺序的（越往下越深），颜色用同一色相的色阶，越深越重；清醒用灰。点按某一段在下面写出起止和时长。
 */
export function SleepStages({ segments, totals, style, ...state }: {
  segments: SleepSeg[];
  /** 各期合计（分钟），不给就按段加 */
  totals?: Partial<Record<SleepStage, number>>;
  style?: StyleProp<ViewStyle>;
} & VizStateProps) {
  const t = useTheme();
  const fs = useFontScale();
  const lw = useScaledWidth(40);
  const [w, onLayout] = useWidth();
  const [sel, setSel] = useState<number | null>(null);
  const segs = [...segments].sort((a, b) => ms(a.start) - ms(b.start));
  const t0 = segs.length ? ms(segs[0].start) : 0;
  const t1 = segs.length ? ms(segs[segs.length - 1].end) : 1;
  const plotW = Math.max(0, w - lw);
  const lane = 18;
  const gap = 6;
  const H = STAGES.length * lane + (STAGES.length - 1) * gap;
  const xOf = (iso: string) => ((ms(iso) - t0) / Math.max(1, t1 - t0)) * plotW;
  const yMid = (s: SleepStage) => STAGES.indexOf(s) * (lane + gap) + lane / 2;
  const colorOf = (s: SleepStage) => (s === 'awake' ? t.viz.other : s === 'rem' ? t.viz.seq[2] : s === 'core' ? t.viz.seq[3] : t.viz.seq[4]);
  const minutes = (sg: SleepSeg) => Math.max(0, Math.round((ms(sg.end) - ms(sg.start)) / 60000));
  const sum: Record<SleepStage, number> = { awake: 0, rem: 0, core: 0, deep: 0 };
  for (const sg of segs) sum[sg.stage] += minutes(sg);
  const tot = { ...sum, ...totals };
  // 时间刻度：开头、结尾，中间每两小时一个整点
  const ticks: { x: number; label: string }[] = [];
  if (segs.length && plotW > 0) {
    const startMin = Number(hmOf(segs[0].start).slice(0, 2)) * 60 + Number(hmOf(segs[0].start).slice(3, 5));
    const span = (t1 - t0) / 60000;
    const edge = Math.round(44 * fs);  // 离两头的起止时间太近的整点不写，免得叠在一起
    for (let m = Math.ceil(startMin / 120) * 120; m - startMin <= span; m += 120) {
      const x = ((m - startMin) / span) * plotW;
      if (x < edge || x > plotW - edge) continue;
      ticks.push({ x, label: `${String(Math.floor(m / 60) % 24).padStart(2, '0')}:00` });
    }
  }
  const onTouch = (e: GestureResponderEvent) => {
    const x = e.nativeEvent.locationX;
    const i = segs.findIndex((sg) => xOf(sg.start) <= x && x <= xOf(sg.end) + 1);
    setSel((p) => (i < 0 || p === i ? null : i));
  };
  const picked = sel != null ? segs[sel] : null;
  const fsLine = Math.round(14 * fs);
  const label = `${L('睡眠分期', 'Sleep stages')}${L('：', ': ')}${STAGES.map((s) => `${stageName(s)} ${durText(tot[s] ?? 0)}`).join(L('，', ', '))}`;
  return (
    <VizFrame empty={!segs.length} height={H} state={state} label={label} style={[{ gap: space.sm }, style]}>
      <View style={{ flexDirection: 'row' }}>
        <View style={{ width: lw, height: H }}>
          {STAGES.map((s) => (
            <Text key={s} numberOfLines={1} style={[vizText.axis, { position: 'absolute', top: yMid(s) - fsLine / 2, height: fsLine, fontSize: 11, color: t.ink2 }]}>{stageName(s)}</Text>
          ))}
        </View>
        <View style={{ flex: 1, height: H }} onLayout={onLayout}>
          {w > 0 ? (
            <Svg width={plotW} height={H}>
              {STAGES.map((s) => <Line key={s} x1={0} x2={plotW} y1={yMid(s)} y2={yMid(s)} stroke={t.viz.grid} strokeWidth={1} />)}
              {segs.slice(1).map((sg, i) => (segs[i].stage !== sg.stage ? (
                <Line key={`c${i}`} x1={xOf(sg.start)} x2={xOf(sg.start)} y1={yMid(segs[i].stage)} y2={yMid(sg.stage)} stroke={t.viz.axis} strokeWidth={1} />
              ) : null))}
              {segs.map((sg, i) => (
                <Rect key={i} x={xOf(sg.start)} y={yMid(sg.stage) - 6} width={Math.max(1.5, xOf(sg.end) - xOf(sg.start))} height={12} rx={3}
                  fill={colorOf(sg.stage)} opacity={sel != null && sel !== i ? 0.45 : 1} />
              ))}
            </Svg>
          ) : null}
          <View style={StyleSheet.absoluteFill} onStartShouldSetResponder={() => true} onResponderGrant={onTouch} />
        </View>
      </View>
      <View style={{ marginLeft: lw, height: fsLine }}>
        {segs.length ? <Text style={[vizText.axis, { position: 'absolute', left: 0, color: t.ink3 }]}>{hmOf(segs[0].start)}</Text> : null}
        {ticks.map((k) => <Text key={k.label} style={[vizText.axis, { position: 'absolute', left: k.x - 16, width: 32, textAlign: 'center', color: t.ink3 }]}>{k.label}</Text>)}
        {segs.length ? <Text style={[vizText.axis, { position: 'absolute', right: 0, color: t.ink3 }]}>{hmOf(segs[segs.length - 1].end)}</Text> : null}
      </View>
      {picked ? (
        <Text style={[vizText.axis, { fontSize: 12, color: t.ink }]}>{`${stageName(picked.stage)} · ${hmOf(picked.start)}–${hmOf(picked.end)} · ${durText(minutes(picked))}`}</Text>
      ) : null}
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', columnGap: space.md, rowGap: 6 }}>
        {[...STAGES].reverse().map((s) => (
          <View key={s} style={{ flexDirection: 'row', alignItems: 'center', gap: 5 }}>
            <View style={{ width: 10, height: 10, borderRadius: 3, backgroundColor: colorOf(s) }} />
            <Text style={[vizText.axis, { fontSize: 12, color: t.ink2 }]}>{stageName(s)}</Text>
            <Text style={[vizText.value, { fontSize: 12, fontWeight: '600', color: t.ink }]}>{durText(tot[s] ?? 0)}</Text>
          </View>
        ))}
      </View>
    </VizFrame>
  );
}

// —— 连续打卡 ——

export interface StreakDay { date: string; done: boolean | null }

/**
 * 连续打卡：最近 N 天一格一天，做了的上色、没做的是轨道色、没数据的只画细线，今天描金边。
 * 上面一个主数字（「7 次」）和说明，sub 写连续几天、最长几天。
 */
export function Streak({ days, value, title, sub, today, style, ...state }: {
  days: StreakDay[]; value: string; title: string; sub?: string; today?: string; style?: StyleProp<ViewStyle>;
} & VizStateProps) {
  const t = useTheme();
  const md = (iso: string) => `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))}`;
  const label = `${title} ${value}${sub ? `${L('，', ', ')}${sub}` : ''}${L('。', '. ')}${days.map((d) => `${md(d.date)} ${d.done == null ? L('无数据', 'no data') : d.done ? L('有', 'yes') : L('无', 'no')}`).join(L('，', ', '))}`;
  return (
    <VizFrame empty={!days.length} state={state} label={label} style={[{ gap: space.sm }, style]}>
      <View style={{ flexDirection: 'row', alignItems: 'baseline', gap: 8 }}>
        <T style={{ fontSize: 28, lineHeight: 34, fontWeight: '700' }}>{value}</T>
        <T v="callout" color={t.ink2}>{title}</T>
      </View>
      {sub ? <T v="caption" color={t.ink3} style={{ fontWeight: '400', marginTop: -4 }}>{sub}</T> : null}
      <View style={{ flexDirection: 'row', gap: 4 }}>
        {days.map((d) => (
          <View key={d.date} style={{
            flex: 1, height: 22, borderRadius: 5,
            backgroundColor: d.done ? seriesColor(t, 0) : d.done === false ? t.track : 'transparent',
            borderWidth: d.date === today ? 1.5 : d.done == null ? 1 : 0, borderColor: d.date === today ? t.gold : t.line,
          }} />
        ))}
      </View>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
        <Text style={[vizText.axis, { color: t.ink3 }]}>{md(days[0]?.date ?? '')}</Text>
        <Text style={[vizText.axis, { color: days[days.length - 1]?.date === today ? t.ink : t.ink3, fontWeight: days[days.length - 1]?.date === today ? '700' : '400' }]}>{md(days[days.length - 1]?.date ?? '')}</Text>
      </View>
    </VizFrame>
  );
}
