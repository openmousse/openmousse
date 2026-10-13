// 折线（+ 目标范围）：2px 线、末端实心点，目标范围是一条浅色带；点按或按住拖动，竖线对齐到最近的一天，浮出那一天每条线的数值。
// 一条线不要图例（标题说明画的是什么）；两条及以上必须有图例。只有一根纵轴：量纲不同的两样东西分成两张图。
import React, { useState } from 'react';
import { Text, View, type GestureResponderEvent, type StyleProp, type ViewStyle } from 'react-native';
import Svg, { Circle, Line, Path, Polyline, Rect } from 'react-native-svg';
import { L } from '../../i18n';
import { useTheme, vizMark } from '../../theme';
import { useScaledWidth } from '../ui';
import { dotProps, fmtTick, Legend, niceScale, seriesColor, Tip, useFontScale, useSurface, useWidth, VizFrame, vizText, type LegendItem, type TipRow, type VizStateProps } from './core';
import { segmentsOf } from './Sparkline';

export interface LinePoint {
  key: string;
  value: number | null;
  /** 横轴上写的字（「10/12」「周一」），不给就用 key（按日期画时写成「10/12」） */
  label?: string;
  /** 已格式化的数值（「8.9 小时」），点按时显示；不给就按 fmt */
  text?: string;
}
export interface LineSeries { key: string; label: string; slot?: number; color?: string; points: LinePoint[] }

/** YYYY-MM-DD → 第几天（只拿来比远近）。 */
export const dayNum = (iso: string) => Math.round(Date.UTC(Number(iso.slice(0, 4)), Number(iso.slice(5, 7)) - 1, Number(iso.slice(8, 10))) / 86400000);
const shortDate = (iso: string) => (/^\d{4}-\d{2}-\d{2}/.test(iso) ? `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))}` : iso);
const plain = (v: number) => v.toLocaleString('en-GB', { maximumFractionDigits: 1 });

export interface LineChartProps extends VizStateProps {
  series: LineSeries[];
  /** 目标范围（只有一头就铺到图的边上）；label 写在带子里右下角（「目标 7–9 小时」） */
  band?: { low: number | null; high: number | null; label?: string; color?: string } | null;
  /** 画图区的高度（不含横轴的字） */
  height?: number;
  /** 纵轴大约几格 */
  ticks?: number;
  /** 纵轴从 0 起（流量：分钟、千卡）；不给就按数据自己的高低（水平值：体重、分数） */
  zero?: boolean;
  /** 纵轴至少撑开这么多（体重 80.0–80.4 这种小波动别画成大起大落） */
  minSpan?: number;
  /** 当前那一点的 key：横轴的字加粗 */
  current?: string;
  /** 按日期画（key 是 YYYY-MM-DD）：隔了几天没数据，横轴上就空几天 */
  dates?: { from: string; to: string };
  /** 对照来源：空心圈（和主线同一天同一个数就套在点外面） */
  secondary?: { label: string; points: { key: string; value: number; text?: string }[] } | null;
  /** 横轴写几个字（0 = 不写，调用方自己写起止） */
  xLabels?: number;
  /** 末端（当前）的点用强调色 */
  emphasis?: boolean;
  /** 单条线时线下铺 10% 的浅色 */
  area?: boolean;
  /** 每个读数画一个小点（读数稀疏时，看得出哪天量过） */
  markers?: boolean;
  /** 纵轴刻度和网格线（小卡片里可以关掉） */
  axis?: boolean;
  /** 图例：不给 = 两条及以上（或有对照来源）时画；调用方自己写了图例就给 false */
  legend?: boolean;
  fmt?: (v: number) => string;
  label?: string;
  style?: StyleProp<ViewStyle>;
}

export function LineChart({ series, band, height = 140, ticks = 3, zero, minSpan, current, dates, secondary, xLabels = 4, emphasis, area, markers, axis = true, legend: showLegend, fmt = plain, label, style, ...state }: LineChartProps) {
  const t = useTheme();
  const surface = useSurface();
  const fs = useFontScale();
  const gutter = useScaledWidth(28);
  const gw = axis ? gutter : 0;
  const [w, onLayout] = useWidth();
  const [sel, setSel] = useState<string | null>(null);

  // 横轴上的点：按出现顺序（按日期画时按日期排）
  const keys: string[] = [];
  const names = new Map<string, string>();
  for (const s of series) for (const p of s.points) {
    if (!names.has(p.key)) { keys.push(p.key); names.set(p.key, p.label ?? (dates ? shortDate(p.key) : p.key)); }
  }
  const sec = secondary?.points ?? [];
  for (const p of sec) if (!names.has(p.key)) { keys.push(p.key); names.set(p.key, dates ? shortDate(p.key) : p.key); }
  if (dates) keys.sort();
  const index = new Map(keys.map((k, i) => [k, i]));

  const vals = series.flatMap((s) => s.points.map((p) => p.value)).filter((v): v is number => v != null);
  const edges = [band?.low, band?.high].filter((v): v is number => v != null);
  const all = [...vals, ...sec.map((p) => p.value), ...edges];
  let lo = all.length ? Math.min(...all) : 0;
  let hi = all.length ? Math.max(...all) : 1;
  if (zero) lo = Math.min(0, lo);
  if (minSpan && hi - lo < minSpan) { const mid = (lo + hi) / 2; lo = mid - minSpan / 2; hi = mid + minSpan / 2; }
  let min: number; let max: number; let tickVals: number[] = [];
  if (axis) ({ min, max, ticks: tickVals } = niceScale(lo, hi, ticks));
  else {
    const m = (hi - lo || Math.abs(hi) || 1) * 0.12;
    min = zero ? lo : lo - m; max = hi + m;
  }

  const padX = vizMark.dot / 2 + vizMark.ring;  // 端点的圆和外圈不被切掉
  const padY = padX;
  const plotW = Math.max(0, w - gw);
  const d0 = dates ? dayNum(dates.from) : 0;
  const span = dates ? Math.max(1, dayNum(dates.to) - d0) : 1;
  const xOf = (k: string) => {
    if (dates) return gw + padX + ((dayNum(k) - d0) / span) * Math.max(0, plotW - padX * 2);
    const i = index.get(k) ?? 0;
    return gw + (keys.length > 1 ? padX + (i / (keys.length - 1)) * Math.max(0, plotW - padX * 2) : plotW / 2);
  };
  const yOf = (v: number) => padY + (1 - (v - min) / (max - min || 1)) * (height - padY * 2);

  const drawn = series.map((s, si) => {
    const color = s.color ?? seriesColor(t, s.slot ?? si);
    const byKey = new Map(s.points.map((p) => [p.key, p]));
    const ordered = dates ? [...s.points].sort((a, b) => (a.key < b.key ? -1 : 1)) : keys.map((k) => byKey.get(k) ?? { key: k, value: null });
    const pts = ordered.map((p) => (p.value == null ? null : { x: xOf(p.key), y: yOf(p.value) }));
    let last: LinePoint | null = null;
    for (let i = ordered.length - 1; i >= 0 && !last; i--) if (ordered[i].value != null) last = ordered[i];
    return { s, color, byKey, ordered, segs: segmentsOf(pts), last };
  });
  const secByKey = new Map(sec.map((p) => [p.key, p]));
  const hasValue = (k: string) => secByKey.has(k) || drawn.some((d) => d.byKey.get(k)?.value != null);

  const fsLine = Math.round(14 * fs);
  const xb = xLabels > 0 ? fsLine + 6 : 0;
  const onTouch = (e: GestureResponderEvent, first: boolean) => {
    const x = e.nativeEvent.locationX + gw;
    let best: string | null = null;
    let bd = Infinity;
    for (const k of keys) {
      if (!hasValue(k)) continue;
      const d = Math.abs(xOf(k) - x);
      if (d < bd) { bd = d; best = k; }
    }
    setSel((prev) => (first && best === prev ? null : best));
  };

  // 横轴写哪几个：从最后一个（通常是当前）往前，每隔同样几个写一个，间距均匀；当前那一点一定写
  const withData = keys.filter(hasValue);
  const pickFrom = dates ? withData : keys;
  const shown = new Set<string>();
  const step = Math.max(1, Math.ceil((pickFrom.length - 1) / Math.max(1, xLabels - 1)));
  if (xLabels > 0) {
    for (let i = pickFrom.length - 1; i >= 0; i -= step) shown.add(pickFrom[i]);
    if (current && names.has(current)) shown.add(current);
  }
  const lw = Math.round(44 * fs);

  const legend: LegendItem[] | null = showLegend ?? (series.length > 1 || !!secondary)
    ? [...drawn.map((d) => ({ label: d.s.label, color: d.color, shape: 'line' as const })), ...(secondary ? [{ label: secondary.label, color: t.ink3, shape: 'ring' as const }] : [])]
    : null;

  const valText = (p: { value: number | null; text?: string }) => (p.text ?? (p.value != null ? fmt(p.value) : '—'));
  const auto = drawn.map((d) => {
    const ps = d.ordered.filter((p) => p.value != null);
    const body = ps.length <= 31 ? ps.map((p) => `${names.get(p.key)} ${valText(p)}`).join(L('，', ', '))
      : L(`最低 ${fmt(Math.min(...ps.map((p) => p.value as number)))}，最高 ${fmt(Math.max(...ps.map((p) => p.value as number)))}，最新 ${valText(ps[ps.length - 1])}`,
        `low ${fmt(Math.min(...ps.map((p) => p.value as number)))}, high ${fmt(Math.max(...ps.map((p) => p.value as number)))}, latest ${valText(ps[ps.length - 1])}`);
    return `${d.s.label}${L('：', ': ')}${body}`;
  }).join(L('；', '; ')) + (band && (band.low != null || band.high != null) ? L(`；目标 ${band.low ?? ''}–${band.high ?? ''}`, `; target ${band.low ?? ''}–${band.high ?? ''}`) : '');

  const bandTop = band?.high != null ? Math.max(0, yOf(band.high)) : 0;
  const bandBottom = band?.low != null ? Math.min(height, yOf(band.low)) : height;
  const hasBand = !!band && (band.low != null || band.high != null) && bandBottom > bandTop;
  const selRows: TipRow[] = [];
  if (sel) {
    for (const d of drawn) { const p = d.byKey.get(sel); if (p?.value != null) selRows.push({ label: d.s.label, value: valText(p), color: d.color }); }
    const sp = secByKey.get(sel);
    if (sp && secondary) selRows.push({ label: secondary.label, value: sp.text ?? fmt(sp.value), color: t.ink3, shape: 'ring' });
  }

  return (
    <VizFrame empty={!vals.length && !sec.length} height={height} state={state} label={label ?? auto} style={style}>
      {legend ? <Legend items={legend} style={{ marginBottom: 10 }} /> : null}
      <View onLayout={onLayout} style={{ height: height + xb }}>
        {w > 0 ? (
          <>
            <Svg width={w} height={height} style={{ position: 'absolute', left: 0, top: 0 }}>
              {hasBand ? <Rect x={gw} y={bandTop} width={plotW} height={Math.max(2, bandBottom - bandTop)} rx={4} fill={band?.color ?? seriesColor(t, 0)} fillOpacity={band?.color ? 1 : vizMark.band} /> : null}
              {tickVals.map((v) => <Line key={v} x1={gw} x2={w} y1={yOf(v)} y2={yOf(v)} stroke={t.viz.grid} strokeWidth={1} />)}
              {area && drawn.length === 1 ? drawn[0].segs.map((sg, i) => (sg.length > 1 ? (
                <Path key={`a${i}`} d={`M${sg[0].x},${yOf(min)} ${sg.map((p) => `L${p.x},${p.y}`).join(' ')} L${sg[sg.length - 1].x},${yOf(min)} Z`} fill={drawn[0].color} fillOpacity={vizMark.area} />
              ) : null)) : null}
              {sec.map((p) => <Circle key={`s${p.key}`} cx={xOf(p.key)} cy={yOf(p.value)} r={4.5} fill="none" stroke={t.ink3} strokeOpacity={0.8} strokeWidth={1.2} />)}
              {drawn.map((d) => d.segs.map((sg, i) => (sg.length > 1 ? (
                <Polyline key={`${d.s.key}${i}`} points={sg.map((p) => `${p.x},${p.y}`).join(' ')} fill="none" stroke={d.color} strokeWidth={vizMark.line} strokeLinejoin="round" strokeLinecap="round" />
              ) : null)))}
              {markers ? drawn.map((d) => d.ordered.map((p) => (p.value != null && p !== d.last ? <Circle key={`m${d.s.key}${p.key}`} cx={xOf(p.key)} cy={yOf(p.value)} r={3} fill={d.color} /> : null))) : null}
              {sel ? <Line x1={xOf(sel)} x2={xOf(sel)} y1={0} y2={height} stroke={t.ink3} strokeWidth={1} /> : null}
              {drawn.map((d) => (d.last?.value != null ? <Circle key={`e${d.s.key}`} cx={xOf(d.last.key)} cy={yOf(d.last.value)} {...dotProps(emphasis ? t.viz.emph : d.color, surface)} /> : null))}
              {sel ? drawn.map((d) => { const p = d.byKey.get(sel); return p?.value != null ? <Circle key={`h${d.s.key}`} cx={xOf(sel)} cy={yOf(p.value)} {...dotProps(d.color, surface)} /> : null; }) : null}
            </Svg>
            {tickVals.map((v) => (
              <View key={`y${v}`} pointerEvents="none" style={{ position: 'absolute', left: 0, width: gw - 6, top: yOf(v) - fsLine / 2, height: fsLine, justifyContent: 'center' }}>
                <Text style={[vizText.axis, { color: t.ink3, textAlign: 'right' }]} numberOfLines={1}>{fmtTick(v)}</Text>
              </View>
            ))}
            {hasBand && band?.label ? (
              <View pointerEvents="none" style={{ position: 'absolute', right: 4, top: bandBottom - bandTop >= fsLine + 4 ? bandBottom - fsLine - 2 : Math.max(0, bandTop - fsLine - 2), height: fsLine, justifyContent: 'center' }}>
                <Text style={[vizText.axis, { color: t.ink2 }]} numberOfLines={1}>{band.label}</Text>
              </View>
            ) : null}
            {keys.filter((key) => shown.has(key)).map((key) => {
              const x = xOf(key);
              const left = Math.max(0, Math.min(w - lw, x - lw / 2));
              const align = left === 0 && x - lw / 2 < 0 ? 'left' : left === w - lw && x + lw / 2 > w ? 'right' : 'center';
              const cur = key === current;
              return (
                <View key={`x${key}`} pointerEvents="none" style={{ position: 'absolute', top: height + 4, left, width: lw }}>
                  <Text numberOfLines={1} style={[vizText.axis, { textAlign: align, color: cur ? t.ink : t.ink3, fontWeight: cur ? '700' : '400' }]}>{names.get(key)}</Text>
                </View>
              );
            })}
            <View testID="viz-line-touch" style={{ position: 'absolute', left: gw, top: 0, width: plotW, height }}
              onStartShouldSetResponder={() => true} onResponderTerminationRequest={() => true}
              onResponderGrant={(e) => onTouch(e, true)} onResponderMove={(e) => onTouch(e, false)} />
            {sel && selRows.length ? <Tip x={xOf(sel)} width={w} title={names.get(sel)} rows={selRows} /> : null}
          </>
        ) : null}
      </View>
    </VizFrame>
  );
}
