// 可视化素材库的公共件：取色、底色、量宽度、刻度、图例、点按显示数值、三态（空 / 加载 / 出错）、状态标签、变化标签、时间范围切换。
// 规则（dataviz）：文字一律用文字色（ink / ink2 / ink3），颜色只给标记；≥ 2 个系列必须有图例；状态色只表示状态，配图标和文字；
// 数值格式化由服务器或调用方做好（text），这里只按 value 画。
import React, { createContext, useCallback, useContext, useState } from 'react';
import { Pressable, StyleSheet, View, useWindowDimensions, type LayoutChangeEvent, type StyleProp, type ViewStyle } from 'react-native';
import Svg, { Path } from 'react-native-svg';
import { L } from '../../i18n';
import { radius, space, type, useTheme, vizMark, type Theme } from '../../theme';
import { ArrowDown, ArrowUp, Minus } from '../icons';
import { Segmented, T } from '../ui';

// —— 颜色 ——

/** 第 slot 个分类色（从 0 起，固定顺序）。6 个以后都是「其他」的灰：别生成第 7 种颜色，把末尾的合并成「其他」。 */
export const seriesColor = (t: Theme, slot: number) => (slot >= 0 && slot < t.viz.series.length ? t.viz.series[slot] : t.viz.other);

export type Tone = 'neutral' | 'good' | 'warn' | 'bad' | 'cyan' | 'gold';
/** 标签的 [底色, 字色]（看板服务器给的 tone 也用这一套）。 */
export const toneColors = (t: Theme, tone?: Tone | string | null): [string, string] => ({
  good: [t.goodSoft, t.good], warn: [t.warnSoft, t.warn], bad: [t.badSoft, t.bad], cyan: [t.cyanSoft, t.cyan], gold: [t.goldSoft, t.gold],
  neutral: [t.surface2, t.ink2],
} as Record<string, [string, string]>)[tone ?? 'neutral'] ?? [t.surface2, t.ink2];

/**
 * 分类标签（表格里的「状态」、看板卡片上的选项值）：第 slot 个分类色的浅底 + 同色相的字。用 theme 的 tints（字对底 ≥ 4.5:1，深浅都校验过），
 * 所以和列头色点、柱子的颜色同一色相；5 号（蓝）没有对应的 tint，和 null / 超出的一样用中性。onCard：标签放在卡片上（深色里卡片是 surface2），中性底换亮一级。
 */
export function slotColors(t: Theme, slot: number | null | undefined, onCard = false): [string, string] {
  const tint = slot != null ? (['cyan', 'gold', 'purple', 'green', null, 'pink'] as const)[slot] : null;
  if (tint) return [t.tints[tint].soft, t.tints[tint].fg];
  return [onCard && t.mode === 'dark' ? t.line : t.surface2, t.ink2];
}

/** 卡片上的标签按 tone 配色：深色里卡片是 surface2，中性标签的底要亮一级才看得出。 */
export const cardToneColors = (t: Theme, tone?: Tone | string | null): [string, string] =>
  (!tone || tone === 'neutral' ? [t.mode === 'dark' ? t.line : t.surface2, t.ink2] : toneColors(t, tone));

/**
 * 图表下面的底色：标记点外圈的 2px 描边、相邻色块之间的 2px 间隙都用它，才看得出是「留白」而不是一圈边框。
 * 默认是卡片色（t.surface）；放在别的底色上（小板子里的深色方块）时用 VizSurface 包一层。
 */
const SurfaceCtx = createContext<string | null>(null);
export function VizSurface({ color, children }: { color: string; children: React.ReactNode }) {
  return <SurfaceCtx.Provider value={color}>{children}</SurfaceCtx.Provider>;
}
export function useSurface() {
  const t = useTheme();
  return useContext(SurfaceCtx) ?? t.surface;
}

// —— 尺寸 ——

/** 量自己的宽度（第一次布局前是 0）。 */
export function useWidth(): [number, (e: LayoutChangeEvent) => void] {
  const [w, setW] = useState(0);
  const onLayout = useCallback((e: LayoutChangeEvent) => {
    const v = Math.round(e.nativeEvent.layout.width);
    setW((p) => (p === v ? p : v));
  }, []);
  return [w, onLayout];
}

/** 系统字号的倍数（1–1.6，和 useScaledWidth 同一个上限）：放字的定宽列、按字高算的位置都乘它。 */
export function useFontScale() {
  const { fontScale } = useWindowDimensions();
  return Math.min(Math.max(fontScale || 1, 1), 1.6);
}

// —— 刻度 ——

/** 好读的刻度：0 / 5 / 10、0 / 1,000 / 2,000。lo、hi 是数据（和目标范围）的高低。 */
export function niceScale(lo: number, hi: number, count = 3): { min: number; max: number; ticks: number[] } {
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) { lo = 0; hi = 1; }
  if (hi <= lo) { hi = lo + (Math.abs(lo) || 1); }
  const raw = (hi - lo) / Math.max(1, count);
  const mag = 10 ** Math.floor(Math.log10(raw));
  const f = raw / mag;
  const step = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * mag;
  const min = Math.floor(lo / step + 1e-9) * step;
  const max = Math.ceil(hi / step - 1e-9) * step;
  const ticks: number[] = [];
  for (let v = min; v <= max + step / 2; v += step) ticks.push(Number(v.toFixed(10)));
  return { min, max, ticks };
}

/** 坐标轴上的数：千分位，一万以上写 12k。 */
export function fmtTick(v: number): string {
  if (Math.abs(v) >= 10000) return `${Number((v / 1000).toFixed(1))}k`;
  return v.toLocaleString('en-GB', { maximumFractionDigits: 1 });
}

// —— 三态：空 / 加载 / 出错 ——

export interface VizStateProps {
  /** 正在读。没有数据时画骨架；已经有图时保留原图、调淡，重读不闪。 */
  loading?: boolean;
  /** 读失败的原因：显示「无法加载」和重试。 */
  error?: string | null;
  onRetry?: () => void;
  /** 没有数据时的说明，默认「暂无数据」。 */
  emptyText?: string;
  /** 没有数据时的动作（例如「添加」）。 */
  onAdd?: () => void;
  addLabel?: string;
}

/**
 * 每个组件都套这一层：出错 → 出错行；没数据还在读 → 骨架；没数据 → 空状态；有数据 → 图（在读就调淡）。
 * label 是读屏念的整段说明（图里每个数都要能从这里或文字里读到）；group = false 时不合成一个元素（表格、看板、日历这种里面有能点的格子）。
 */
export function VizFrame({ empty, height = 96, state, label, group = true, style, children }: {
  empty: boolean; height?: number; state?: VizStateProps; label?: string; group?: boolean; style?: StyleProp<ViewStyle>; children: React.ReactNode;
}) {
  if (state?.error) return <VizError message={state.error} onRetry={state.onRetry} style={style} />;
  if (empty && state?.loading) return <VizLoading height={height} style={style} />;
  if (empty) return <VizEmpty text={state?.emptyText} onAdd={state?.onAdd} addLabel={state?.addLabel} style={style} />;
  return (
    <View style={[style, state?.loading ? { opacity: 0.5 } : null]} accessible={group} accessibilityRole={group ? 'image' : undefined}
      accessibilityLabel={group ? label : undefined} accessibilityState={state?.loading ? { busy: true } : undefined}>
      {children}
    </View>
  );
}

export function VizEmpty({ text, onAdd, addLabel, style }: { text?: string; onAdd?: () => void; addLabel?: string; style?: StyleProp<ViewStyle> }) {
  const t = useTheme();
  return (
    <View style={[styles.stateRow, { borderWidth: 1, borderStyle: 'dashed', borderColor: t.viz.axis }, style]}>
      <T v="callout" color={t.ink2} style={{ flex: 1 }}>{text || L('暂无数据', 'No data yet')}</T>
      {onAdd ? (
        <Pressable onPress={onAdd} hitSlop={10} accessibilityRole="button">
          <T v="callout" color={t.gold} style={{ fontWeight: '600' }}>{addLabel || L('添加', 'Add')}</T>
        </Pressable>
      ) : null}
    </View>
  );
}

export function VizLoading({ height = 96, style }: { height?: number; style?: StyleProp<ViewStyle> }) {
  const t = useTheme();
  const bar = { height: 10, borderRadius: 5, backgroundColor: t.surface2 };
  return (
    <View style={[styles.stateBox, { minHeight: Math.min(height, 120), backgroundColor: t.bg }, style]}
      accessible accessibilityLabel={L('正在加载…', 'Loading…')} accessibilityState={{ busy: true }}>
      <View style={[bar, { width: '60%' }]} />
      <View style={[bar, { width: '85%' }]} />
      <View style={[bar, { width: '40%' }]} />
    </View>
  );
}

export function VizError({ message, onRetry, style }: { message?: string | null; onRetry?: () => void; style?: StyleProp<ViewStyle> }) {
  const t = useTheme();
  return (
    <View style={[styles.stateRow, { backgroundColor: t.badSoft }, style]} accessible={!onRetry} accessibilityLabel={L(`无法加载。${message ?? ''}`, `Couldn't load. ${message ?? ''}`)}>
      <StatusGlyph status="bad" size={14} />
      <View style={{ flex: 1, gap: 2 }}>
        <T v="callout" color={t.bad} style={{ fontWeight: '600' }}>{L('无法加载', "Couldn't load")}</T>
        {message ? <T v="caption" color={t.ink2} numberOfLines={2} style={{ fontWeight: '400' }}>{message}</T> : null}
      </View>
      {onRetry ? (
        <Pressable onPress={onRetry} hitSlop={10} accessibilityRole="button">
          <T v="callout" style={{ fontWeight: '600' }}>{L('重试', 'Retry')}</T>
        </Pressable>
      ) : null}
    </View>
  );
}

// —— 图例 ——

export type Swatch = 'rect' | 'line' | 'dot' | 'ring' | 'diamond';
export interface LegendItem { label: string; slot?: number; color?: string; value?: string; shape?: Swatch }

/** 标记的小样：柱子和色块用方块，折线用短线，点用圆点；空心圈、菱形给日历和时间线。 */
export function SwatchMark({ shape = 'rect', color, size = 10 }: { shape?: Swatch; color: string; size?: number }) {
  if (shape === 'line') return <View style={{ width: size + 4, height: 2, borderRadius: 1, backgroundColor: color }} />;
  if (shape === 'dot') return <View style={{ width: size - 2, height: size - 2, borderRadius: size, backgroundColor: color }} />;
  if (shape === 'ring') return <View style={{ width: size - 2, height: size - 2, borderRadius: size, borderWidth: 1.5, borderColor: color }} />;
  if (shape === 'diamond') return <View style={{ width: size - 3, height: size - 3, borderRadius: 1.5, backgroundColor: color, transform: [{ rotate: '45deg' }] }} />;
  return <View style={{ width: size, height: size, borderRadius: 3, backgroundColor: color }} />;
}

/** 图例：小样 + 名字（次要字色）+ 数值（主字色）。两个及以上系列必须有。 */
export function Legend({ items, style, column }: { items: LegendItem[]; style?: StyleProp<ViewStyle>; column?: boolean }) {
  const t = useTheme();
  return (
    <View style={[column ? { gap: 9 } : styles.legendRow, style]}>
      {items.map((it, i) => (
        <View key={`${i}-${it.label}`} style={[styles.legendItem, column && { width: '100%' }]}>
          <SwatchMark shape={it.shape} color={it.color ?? seriesColor(t, it.slot ?? i)} />
          <T v="caption" color={t.ink2} numberOfLines={1} style={[{ fontWeight: '400', fontSize: 12 }, column && { flex: 1 }]}>{it.label}</T>
          {it.value ? <T v="caption" style={{ fontSize: 12, fontWeight: '600', fontVariant: ['tabular-nums'] }}>{it.value}</T> : null}
        </View>
      ))}
    </View>
  );
}

// —— 点按显示数值 ——

export interface TipRow { label: string; value: string; color?: string; shape?: Swatch }

/**
 * 点按后浮出的数值框：数值在前、加粗，名字在后、次要；多个系列一行一个，用短线标出是哪条。
 * x 是指着的位置（相对图的左边），框自己量宽度、不出图的左右边界。
 */
export function Tip({ x, top = 0, width, title, rows }: { x: number; top?: number; width: number; title?: string; rows: TipRow[] }) {
  const t = useTheme();
  const [w, setW] = useState(0);
  const left = Math.max(0, Math.min(width - w, x - w / 2));
  return (
    <View pointerEvents="none" onLayout={(e) => setW(Math.round(e.nativeEvent.layout.width))}
      style={[styles.tip, { top, left, opacity: w ? 1 : 0, backgroundColor: t.surface2, borderColor: t.line }]}>
      {title ? <T v="caption" color={t.ink2} style={{ fontWeight: '400', fontSize: 11 }}>{title}</T> : null}
      {rows.map((r, i) => (
        <View key={`${i}-${r.label}`} style={styles.tipRow}>
          {r.color ? <SwatchMark shape={r.shape ?? 'line'} color={r.color} size={8} /> : null}
          <T v="caption" style={{ fontSize: 13, fontWeight: '700', fontVariant: ['tabular-nums'] }}>{r.value}</T>
          {r.label ? <T v="caption" color={t.ink2} style={{ fontSize: 12, fontWeight: '400' }}>{r.label}</T> : null}
        </View>
      ))}
    </View>
  );
}

// —— 状态与变化 ——

export type Status = 'good' | 'warn' | 'bad';

/** 状态的小图标：正常 = 勾，注意 = 叹号，异常 = 叉（状态色永远配它和文字，不单靠颜色）。 */
export function StatusGlyph({ status, size = 13, color }: { status: Status; size?: number; color?: string }) {
  const t = useTheme();
  const c = color ?? (status === 'good' ? t.good : status === 'warn' ? t.warn : t.bad);
  const d = status === 'good' ? 'M20 6 9 17l-5-5' : status === 'warn' ? 'M12 6v8M12 18.5h.01' : 'M18 6 6 18M6 6l12 12';
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24">
      <Path d={d} stroke={c} strokeWidth={3} strokeLinecap="round" strokeLinejoin="round" fill="none" />
    </Svg>
  );
}

/** 状态标签：图标 + 文字，状态色。只用来表示状态（正常 / 注意 / 异常），别拿来区分系列。 */
export function StatusTag({ status, label, style }: { status: Status; label: string; style?: StyleProp<ViewStyle> }) {
  const t = useTheme();
  const [bg, fg] = toneColors(t, status);
  return (
    <View style={[styles.tag, { backgroundColor: bg }, style]} accessible accessibilityLabel={label}>
      <StatusGlyph status={status} size={12} color={fg} />
      <T v="caption" color={fg} style={{ fontSize: 12, fontWeight: '600' }}>{label}</T>
    </View>
  );
}

export type Direction = 'up' | 'down' | 'flat';
/**
 * 变化标签：方向箭头 + 文字（「5 kg」「进步」「持平」）。颜色 = 方向 × 这个方向好不好：
 * good = true 绿、false 红、不给（无所谓好坏）灰；持平永远是灰。
 */
export function ChangeTag({ dir, text, good, style }: { dir: Direction; text: string; good?: boolean | null; style?: StyleProp<ViewStyle> }) {
  const t = useTheme();
  const tone: Tone = dir === 'flat' || good == null ? 'neutral' : good ? 'good' : 'bad';
  const [bg, fg] = toneColors(t, tone);
  const Icon = dir === 'up' ? ArrowUp : dir === 'down' ? ArrowDown : Minus;
  const word = dir === 'up' ? L('上升', 'up') : dir === 'down' ? L('下降', 'down') : L('持平', 'flat');
  return (
    <View style={[styles.tag, { backgroundColor: bg }, style]} accessible accessibilityLabel={`${word} ${text}`}>
      <Icon size={11} color={fg} strokeWidth={3} />
      <T v="caption" color={fg} style={{ fontSize: 12, fontWeight: '700', fontVariant: ['tabular-nums'] }}>{text}</T>
    </View>
  );
}

// —— 时间范围切换 ——

export type RangeDays = 7 | 30 | 90;
/** 时间范围：7 / 30 / 90 天。放在它管的那几张图上面一行，几张图跟着同一个范围。 */
export function RangeSwitch({ value, onChange, options = [7, 30, 90] }: { value: RangeDays; onChange: (v: RangeDays) => void; options?: RangeDays[] }) {
  return (
    <Segmented options={options.map((d) => ({ value: String(d), label: L(`${d} 天`, `${d} days`) }))} value={String(value)} onChange={(v) => onChange(Number(v) as RangeDays)} />
  );
}

/** 画在 SVG 里的标记点：实心、外圈一道底色（压在线上也看得清）。 */
export const dotProps = (fill: string, surface: string, r = vizMark.dot / 2) => ({ r, fill, stroke: surface, strokeWidth: vizMark.ring });

export const vizText = StyleSheet.create({
  axis: { ...type.caption, fontSize: 10, fontWeight: '400', fontVariant: ['tabular-nums'] },
  value: { ...type.caption, fontSize: 11, fontWeight: '700', fontVariant: ['tabular-nums'] },
});

const styles = StyleSheet.create({
  stateRow: { flexDirection: 'row', alignItems: 'center', gap: space.sm, padding: space.md, borderRadius: radius.md },
  stateBox: { gap: 6, padding: space.md, borderRadius: radius.md, justifyContent: 'center' },
  legendRow: { flexDirection: 'row', flexWrap: 'wrap', columnGap: space.md, rowGap: 6 },
  legendItem: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  tip: { position: 'absolute', borderRadius: 8, borderWidth: StyleSheet.hairlineWidth, paddingHorizontal: 8, paddingVertical: 5, gap: 2, maxWidth: 220 },
  tipRow: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  tag: { flexDirection: 'row', alignItems: 'center', gap: 4, borderRadius: 7, paddingHorizontal: 7, paddingVertical: 3, alignSelf: 'flex-start' },
});
