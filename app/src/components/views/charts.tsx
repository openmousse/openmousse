// 集合块「图表」视图里的几种图：数字、柱状、横条、折线、环形。数和字都是服务器算好的（server/views.py），这里只画。
// 可视化素材库（src/components/viz/，viz-kit 分支）合进来以后换成那边的同名组件：NumberCard / BarChart / HBarChart / LineChart / DonutChart。
// 标记规范照素材库：线宽 2px、柱顶 4px 圆角、标记 8px 加 2px 底色描边、段间 2px；单系列只用 1 号色；≥ 2 个系列有图例；文字用文字色。
// 点按一根柱、一行、一段：显示它的数值，再点「查看」列出这一段的行（onDrill）。
import React, { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import Svg, { Circle, Line, Polyline } from 'react-native-svg';
import type { ChartData } from '../../api/boards';
import { L } from '../../i18n';
import { radius, space, type, useTheme } from '../../theme';
import { T, useScaledWidth } from '../ui';
import { seriesColor } from './palette';

export type Drill = (key: string | null, label: string) => void;

/** 一个图的外框：标题 + 右边一行小字；选中了某一段时右边换成「标签：数值 · 查看」。 */
function ChartCard({ title, caption, picked, onDrill, children, label }: {
  title: string; caption?: string; picked?: { key: string; label: string; text: string } | null; onDrill?: Drill; children: React.ReactNode; label: string;
}) {
  const t = useTheme();
  return (
    <View style={[styles.card, { backgroundColor: t.surface }]} accessibilityLabel={label}>
      <View style={styles.head}>
        <T v="headline" style={{ fontSize: 15, fontWeight: '700', flexShrink: 1 }} numberOfLines={1}>{title}</T>
        {picked ? (
          <Pressable onPress={onDrill ? () => onDrill(picked.key, picked.label) : undefined} disabled={!onDrill} hitSlop={8} accessibilityRole="button"
            accessibilityLabel={L(`${picked.label}：${picked.text}，查看明细`, `${picked.label}: ${picked.text}, see the rows`)} style={styles.picked}>
            <T v="caption" style={{ fontWeight: '700', fontVariant: ['tabular-nums'] }} numberOfLines={1}>{`${picked.label}：${picked.text}`}</T>
            {onDrill ? <T v="caption" color={t.gold} style={{ fontWeight: '700' }}>{L('查看', 'View')}</T> : null}
          </Pressable>
        ) : caption ? <T v="caption" color={t.ink3} style={{ fontWeight: '400' }} numberOfLines={1}>{caption}</T> : null}
      </View>
      {children}
    </View>
  );
}

/** 数字：一行最多三个。有 parts 的（「实习 / 全职」）整个数字一起点开。 */
export function NumberTiles({ charts, onDrill }: { charts: { c: ChartData; i: number }[]; onDrill?: (i: number, key: string | null, label: string) => void }) {
  const t = useTheme();
  return (
    <View style={styles.tiles}>
      {charts.map(({ c, i }) => (
        <Pressable key={i} onPress={onDrill && !c.error ? () => onDrill(i, null, c.label || c.title) : undefined} disabled={!onDrill || !!c.error}
          accessibilityRole={onDrill ? 'button' : undefined} accessibilityLabel={`${c.label || c.title} ${c.text ?? ''}`}
          style={({ pressed }) => [styles.tile, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
          <Text numberOfLines={1} adjustsFontSizeToFit style={[type.title, { fontSize: 24, fontWeight: '700', color: c.error ? t.bad : t.ink, fontVariant: ['tabular-nums'] }]}>
            {c.error ? '—' : c.text}
          </Text>
          <T v="caption" color={t.ink2} style={{ fontWeight: '400' }} numberOfLines={2}>{c.label || c.title}</T>
        </Pressable>
      ))}
    </View>
  );
}

/** 柱状：单系列 1 号色、柱顶 4px 圆角；只标关键数值（这一段、最高的那一段），点按显示别的。 */
export function BarChart({ c, onDrill }: { c: ChartData; onDrill?: Drill }) {
  const t = useTheme();
  const [sel, setSel] = useState<string | null>(null);
  const bars = c.bars ?? [];
  const max = c.max || 1;
  const H = 150;
  const TOP = 20;  // 柱子顶上留出写数值的地方（满格的那根也不压到标题）
  const yw = useScaledWidth(Math.max(24, ...(c.axis ?? []).map((a) => a.text.length * 7 + 4)));  // 「2h30」「£500」这类刻度要宽一些
  const picked = bars.find((b) => b.key === sel) ?? null;
  const wide = bars.length > 7;
  return (
    <ChartCard title={c.title} caption={c.unitLabel} picked={picked} onDrill={onDrill} label={c.title}>
      <View style={{ flexDirection: 'row' }}>
        <View style={{ width: yw, height: H + TOP }}>
          {(c.axis ?? []).map((a) => (
            <Text key={a.value} style={[type.caption, styles.axis, { color: t.ink3, bottom: (a.value / max) * H - 7, width: yw - 6 }]}>{a.text}</Text>
          ))}
        </View>
        <View style={{ flex: 1, height: H + TOP }}>
          {(c.axis ?? []).map((a) => <View key={a.value} style={[styles.grid, { bottom: (a.value / max) * H, borderTopColor: t.line }]} />)}
          <View style={[StyleSheet.absoluteFill, { flexDirection: 'row', alignItems: 'flex-end', justifyContent: 'space-around' }]}>
            {bars.map((b) => {
              const on = sel === b.key;
              const h = b.value > 0 ? Math.max(3, (b.value / max) * H) : 2;
              return (
                <Pressable key={b.key} onPress={() => setSel(on ? null : b.key)} accessibilityRole="button" accessibilityState={{ selected: on }}
                  accessibilityLabel={`${b.label}：${b.text}`} style={{ alignItems: 'center', justifyContent: 'flex-end', height: '100%', flex: 1 }}>
                  {b.show || on ? <Text style={[type.caption, { color: t.ink, fontWeight: '700', marginBottom: 4, fontVariant: ['tabular-nums'] }]}>{b.text}</Text> : null}
                  <View style={{ width: wide ? '70%' : 36, maxWidth: 36, height: h, borderTopLeftRadius: 4, borderTopRightRadius: 4,
                    backgroundColor: b.value > 0 ? seriesColor(t, 0) : t.track, opacity: sel && !on ? 0.45 : 1 }} />
                </Pressable>
              );
            })}
          </View>
        </View>
      </View>
      <View style={{ flexDirection: 'row', justifyContent: 'space-around', marginLeft: yw }}>
        {bars.map((b) => (
          <Text key={b.key} numberOfLines={1} style={[type.caption, { flex: 1, textAlign: 'center', fontSize: 11, color: b.current ? t.ink : t.ink3, fontWeight: b.current ? '700' : '400' }]}>
            {b.label}
          </Text>
        ))}
      </View>
    </ChartCard>
  );
}

/** 横条：数值写在条末；点按一行显示「查看」。 */
export function HBarChart({ c, onDrill }: { c: ChartData; onDrill?: Drill }) {
  const t = useTheme();
  const [sel, setSel] = useState<string | null>(null);
  const bars = c.bars ?? [];
  const top = Math.max(...bars.map((b) => b.value), 0) || 1;
  const lw = useScaledWidth(64);
  const vw = useScaledWidth(28);
  const picked = bars.find((b) => b.key === sel) ?? null;
  return (
    <ChartCard title={c.title} caption={c.unitLabel} picked={picked} onDrill={onDrill} label={c.title}>
      <View style={{ gap: 10 }}>
        {bars.map((b) => {
          const on = sel === b.key;
          return (
            <Pressable key={b.key} onPress={() => setSel(on ? null : b.key)} accessibilityRole="button" accessibilityState={{ selected: on }}
              accessibilityLabel={`${b.label}：${b.text}`} style={{ flexDirection: 'row', alignItems: 'center', gap: 10, opacity: sel && !on ? 0.5 : 1 }}>
              <T v="callout" color={t.ink2} numberOfLines={1} style={{ width: lw, fontSize: 13 }}>{b.label}</T>
              <View style={{ flex: 1, height: 14, justifyContent: 'center' }}>
                <View style={{ width: `${Math.max(1.5, (b.value / top) * 100)}%`, height: 14, borderTopRightRadius: 4, borderBottomRightRadius: 4, backgroundColor: seriesColor(t, 0) }} />
              </View>
              <T v="callout" numberOfLines={1} style={{ width: vw, textAlign: 'right', fontSize: 13, fontWeight: '600', fontVariant: ['tabular-nums'] }}>{b.text}</T>
            </Pressable>
          );
        })}
      </View>
    </ChartCard>
  );
}

/** 折线：2px 线，点 8px 加 2px 底色描边；没有记录的那一段断开。 */
export function LineChart({ c, onDrill }: { c: ChartData; onDrill?: Drill }) {
  const t = useTheme();
  const [w, setW] = useState(0);
  const [sel, setSel] = useState<string | null>(null);
  const pts = c.points ?? [];
  const H = 120;
  const pad = 8;
  const lo = c.lo ?? 0;
  const hi = c.hi ?? 1;
  const span = hi - lo || 1;
  const xOf = (i: number) => (pts.length > 1 ? pad + (i / (pts.length - 1)) * (w - pad * 2) : w / 2);
  const yOf = (v: number) => pad + (1 - (v - lo) / span) * (H - pad * 2);
  // 连续有值的几段各画一条线
  const runs: string[] = [];
  let cur: string[] = [];
  pts.forEach((p, i) => {
    if (p.value == null) { if (cur.length > 1) runs.push(cur.join(' ')); cur = []; return; }
    cur.push(`${xOf(i)},${yOf(p.value)}`);
  });
  if (cur.length > 1) runs.push(cur.join(' '));
  const every = pts.length > 8 ? Math.ceil(pts.length / 6) : 1;
  const picked = pts.find((p) => p.key === sel) ?? null;
  return (
    <ChartCard title={c.title} caption={c.lastText} picked={picked && picked.value != null ? picked : null} onDrill={onDrill} label={c.title}>
      <View style={{ height: H }} onLayout={(e) => setW(e.nativeEvent.layout.width)}>
        {w > 0 ? (
          <Svg width={w} height={H}>
            <Line x1={0} x2={w} y1={H - 0.5} y2={H - 0.5} stroke={t.line} strokeWidth={1} />
            {runs.map((r, i) => <Polyline key={i} points={r} fill="none" stroke={seriesColor(t, 0)} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />)}
            {pts.map((p, i) => (p.value != null ? (
              <Circle key={p.key} cx={xOf(i)} cy={yOf(p.value)} r={sel === p.key ? 5 : 4} fill={seriesColor(t, 0)} stroke={t.surface} strokeWidth={2} />
            ) : null))}
          </Svg>
        ) : null}
        <View style={[StyleSheet.absoluteFill, { flexDirection: 'row' }]}>
          {pts.map((p) => (
            <Pressable key={p.key} style={{ flex: 1 }} onPress={() => setSel(sel === p.key ? null : p.key)} accessibilityRole="button" accessibilityLabel={`${p.label}：${p.text || '—'}`} />
          ))}
        </View>
      </View>
      <View style={{ flexDirection: 'row' }}>
        {pts.map((p, i) => (
          <Text key={p.key} numberOfLines={1} style={[type.caption, { flex: 1, fontSize: 10, textAlign: 'center', color: p.current ? t.ink : t.ink3, fontWeight: p.current ? '700' : '400' }]}>
            {i % every === 0 || p.current ? p.label : ''}
          </Text>
        ))}
      </View>
    </ChartCard>
  );
}

/** 环形：3–6 份的占比，段间 2px；中间是合计；下面是图例（色块、名字、数值、比例）。 */
export function DonutChart({ c, onDrill }: { c: ChartData; onDrill?: Drill }) {
  const t = useTheme();
  const [sel, setSel] = useState<string | null>(null);
  const slices = c.slices ?? [];
  const total = slices.reduce((s, x) => s + x.value, 0) || 1;
  const size = 132;
  const stroke = 16;
  const r = (size - stroke) / 2;
  const circ = 2 * Math.PI * r;
  const gap = slices.length > 1 ? 2 : 0;
  const arcs = slices.map((x, i) => {
    const len = (x.value / total) * circ;
    const before = slices.slice(0, i).reduce((sum, y) => sum + (y.value / total) * circ, 0);
    return { key: x.key, color: seriesColor(t, x.color), dash: `${Math.max(0.5, len - gap)} ${circ}`, offset: -before };
  });
  const picked = slices.find((x) => x.key === sel) ?? null;
  return (
    <ChartCard title={c.title} picked={picked} onDrill={onDrill} label={c.title}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.lg }}>
        <View style={{ width: size, height: size, alignItems: 'center', justifyContent: 'center' }}>
          <Svg width={size} height={size} style={StyleSheet.absoluteFill}>
            {arcs.map((a) => (
              <Circle key={a.key} cx={size / 2} cy={size / 2} r={r} fill="none" stroke={a.color} strokeWidth={sel === a.key ? stroke + 3 : stroke}
                strokeDasharray={a.dash} strokeDashoffset={a.offset} opacity={sel && sel !== a.key ? 0.4 : 1} transform={`rotate(-90 ${size / 2} ${size / 2})`} />
            ))}
          </Svg>
          <Text style={[type.title, { fontSize: 22, fontWeight: '700', color: t.ink, fontVariant: ['tabular-nums'] }]}>{c.totalText}</Text>
          <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L('合计', 'Total')}</T>
        </View>
        <View style={{ flex: 1, gap: 8 }}>
          {slices.map((x) => (
            <Pressable key={x.key} onPress={() => setSel(sel === x.key ? null : x.key)} accessibilityRole="button" accessibilityState={{ selected: sel === x.key }}
              accessibilityLabel={`${x.label}：${x.text}，${x.pct}%`} style={{ flexDirection: 'row', alignItems: 'center', gap: 8, opacity: sel && sel !== x.key ? 0.5 : 1 }}>
              <View style={{ width: 10, height: 10, borderRadius: 3, backgroundColor: seriesColor(t, x.color) }} />
              <T v="callout" numberOfLines={1} style={{ flex: 1, fontSize: 13 }}>{x.label}</T>
              <T v="callout" style={{ fontSize: 13, fontWeight: '600', fontVariant: ['tabular-nums'] }}>{x.text}</T>
              <T v="caption" color={t.ink3} style={{ width: 34, textAlign: 'right', fontWeight: '400', fontVariant: ['tabular-nums'] }}>{`${x.pct}%`}</T>
            </Pressable>
          ))}
        </View>
      </View>
    </ChartCard>
  );
}

/** 图表算不出来（字段被删了这类）：只有这一个图显示原因。 */
export function ChartError({ c }: { c: ChartData }) {
  const t = useTheme();
  return (
    <View style={[styles.card, { backgroundColor: t.surface }]}>
      <T v="headline" style={{ fontSize: 15, fontWeight: '700' }}>{c.title || L('图表', 'Chart')}</T>
      <T v="callout" color={t.bad}>{L(`无法显示：${c.error}`, `Can't show this: ${c.error}`)}</T>
    </View>
  );
}

/** 不认识的图表种类（服务器比 app 新）。 */
export function ChartUnknown({ c }: { c: ChartData }) {
  const t = useTheme();
  return (
    <View style={[styles.card, { backgroundColor: t.surface }]}>
      <T v="headline" style={{ fontSize: 15, fontWeight: '700' }}>{c.title || L('图表', 'Chart')}</T>
      <T v="callout" color={t.ink2}>{L('此图表需要更新 app 后才能显示。', 'This chart needs a newer version of the app.')}</T>
    </View>
  );
}

const styles = StyleSheet.create({
  card: { borderRadius: radius.lg - 2, padding: 14, gap: 10 },
  head: { flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between', gap: space.sm, minHeight: 20 },
  picked: { flexDirection: 'row', alignItems: 'baseline', gap: 8, flexShrink: 1 },
  tiles: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  tile: { flexGrow: 1, flexBasis: '30%', minWidth: 96, borderRadius: radius.md + 2, padding: 12, gap: 2 },
  axis: { position: 'absolute', left: 0, fontSize: 11, textAlign: 'right', fontVariant: ['tabular-nums'] },
  grid: { position: 'absolute', left: 0, right: 0, height: 0, borderTopWidth: 1 },
});
