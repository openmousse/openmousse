import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { Platform, useColorScheme } from 'react-native';
import type { AgentColor } from './data/types';
import { readLocal, writeLocal } from './deviceStore';

/**
 * Agent 的颜色：浅底（soft）+ 前景（fg，图标和字），另有色板上的圆点（swatch）。
 * 深浅两套都校验过：fg 在 soft 上的对比度 ≥ 4.5:1（浅色 4.7–5.4，深色 6.8–7.7），fg 在卡片底色上也 ≥ 4.5:1。
 * 浅色的青、金、绿、橙比全局的 cyan / gold / good 深一点，就是为了在自己的浅底上过 4.5。
 */
type Tint = { soft: string; fg: string; swatch: string };

// 助手的视觉身份来自头像：引力透镜。墨黑力场、金色光环、青色数据流。
// 金 = 助手自己（头像、发送、当前选中）；青 = 数据与进度；语义色只表示状态。
const light = {
  mode: 'light' as 'light' | 'dark',
  bg: '#F1F3F4',
  surface: '#FFFFFF',
  surface2: '#E7EAEC',
  /** 占位。浅色弹层不抬，读不到这一档。顺着 surface → surface2 再灰一档。 */
  surface3: '#CFD5D9',
  ink: '#12151A',
  ink2: '#4B535C',
  ink3: '#7D858F',
  /** 占位。浅色弹层不抬，读不到。和 ink3 同一个值，误读也不变样。 */
  ink3Lift: '#7D858F',
  line: '#DCE0E3',
  /** 占位。浅色弹层不抬，读不到。和 line 同一个值，误读也不变样。 */
  lineLift: '#DCE0E3',
  gold: '#9A6B16',
  goldFill: '#D9AE62',
  goldSoft: '#F5EAD3',
  onGold: '#2A1E06',
  cyan: '#0F7A93',
  cyanSoft: '#DAF0F5',
  /** 青底上的字和图标。浅色仍是白；深色用页面底色，白字压在 #5CCFE6 上只有 1.8:1。 */
  onCyan: '#FFFFFF',
  /** 青点（标志、此刻）。填充深浅都是设计系统的 #5CCFE6；浅色另描 dotEdge。文字青仍用 cyan。 */
  dot: '#5CCFE6',
  dotEdge: '#0E738A',
  chartA: '#1488A3',
  chartB: '#B07D22',
  track: '#E1E5E8',
  good: '#22794F',
  goodSoft: '#DDF1E7',
  warn: '#9A6408',
  warnSoft: '#FAEBCF',
  bad: '#B83A30',
  badSoft: '#FBE3E0',
  lensField: '#101216',
  // 可视化色板（src/components/viz/ 用，规则见下方 vizMark 上面的说明）
  viz: {
    series: ['#1488A3', '#B07D22', '#7B5CC7', '#2F8A5B', '#4A72C2', '#B24F86'],
    seq: ['#CAE9F3', '#7CBDD1', '#4DA3BC', '#1488A3', '#005A6F'],
    other: '#A6AEB7',
    emph: '#005A6F',
    grid: '#E6E9EC',
    axis: '#C9CED3',
  },
  tints: {
    cyan: { soft: '#DAF0F5', fg: '#0C6F86', swatch: '#0F7A93' },
    gold: { soft: '#F5EAD3', fg: '#8A5F12', swatch: '#B07D22' },
    green: { soft: '#DDF1E7', fg: '#20744B', swatch: '#22794F' },
    purple: { soft: '#ECE6F7', fg: '#6A4BA8', swatch: '#6A4BA8' },
    pink: { soft: '#F8E3EC', fg: '#A83A6B', swatch: '#A83A6B' },
    orange: { soft: '#FBE6D6', fg: '#A04F18', swatch: '#A5521A' },
  } as Record<AgentColor, Tint>,
};

const dark: typeof light = {
  mode: 'dark',
  bg: '#0B0D10',
  surface: '#14171C',
  surface2: '#1E2329',
  /** 比 surface2 再亮一档，步子同 bg → surface → surface2。深色弹层里 surface2 读成它。 */
  surface3: '#282E35',
  ink: '#ECEEF0',
  ink2: '#A6AEB7',
  ink3: '#737C86',
  /** 深色弹层里 ink3 读成它。页面上的 #737C86 在弹层卡片上不够小字的 4.5:1。 */
  ink3Lift: '#858E98',
  line: '#252A31',
  /** 深色弹层里 line 读成它。同一路灰（R、R+5、R+12）。#252A31 在抬过的卡片上太淡。 */
  lineLift: '#343940',
  gold: '#DDB56A',
  goldFill: '#D9AE62',
  goldSoft: '#30271A',
  onGold: '#2A1E06',
  cyan: '#5CCFE6',
  cyanSoft: '#10303A',
  onCyan: '#0B0D10',
  dot: '#5CCFE6',
  /** 深色不描边：描边色与填充相同。 */
  dotEdge: '#5CCFE6',
  chartA: '#2A97B0',
  chartB: '#B5873A',
  track: '#252A31',
  good: '#55C795',
  goodSoft: '#12301F',
  warn: '#E5AD4E',
  warnSoft: '#33270F',
  bad: '#EC7A70',
  badSoft: '#3A1815',
  lensField: '#050607',
  viz: {
    series: ['#2A97B0', '#B5873A', '#8E6FD8', '#3E9E6E', '#5E86D6', '#C0679A'],
    seq: ['#163038', '#1B4C59', '#23708A', '#2A97B0', '#5CCFE6'],
    other: '#525A64',
    emph: '#5CCFE6',
    grid: '#23272E',
    axis: '#30353D',
  },
  tints: {
    cyan: { soft: '#10303A', fg: '#5CCFE6', swatch: '#5CCFE6' },
    gold: { soft: '#30271A', fg: '#DDB56A', swatch: '#DDB56A' },
    green: { soft: '#12301F', fg: '#55C795', swatch: '#55C795' },
    purple: { soft: '#29213F', fg: '#B9A4F4', swatch: '#B9A4F4' },
    pink: { soft: '#3A1A2A', fg: '#F291BC', swatch: '#F291BC' },
    orange: { soft: '#3A2416', fg: '#F2A56E', swatch: '#F2A56E' },
  },
};

export type Theme = typeof light;

/** Agent 的颜色（服务器给 null 或不认识的值 = 青）。 */
export const agentTint = (t: Theme, color?: AgentColor | string | null): Tint =>
  (color && Object.prototype.hasOwnProperty.call(t.tints, color) ? t.tints[color as AgentColor] : t.tints.cyan);
export type Appearance = 'system' | 'light' | 'dark';

/** 没选过外观时用深色。选过的值存在本机，重启还在。 */
const APPEARANCE_KEY = 'mousse.appearance';

export function parseAppearance(raw: string | null | undefined): Appearance {
  return raw === 'light' || raw === 'dark' || raw === 'system' ? raw : 'dark';
}

function loadAppearance(): Appearance {
  return parseAppearance(readLocal(APPEARANCE_KEY));
}

const Ctx = createContext<{ t: Theme; appearance: Appearance; setAppearance: (a: Appearance) => void }>({
  t: dark,
  appearance: 'dark',
  setAppearance: () => {},
});

// 深色弹层里 surface 读成 surface2，surface2 读成 surface3，ink3 读成 ink3Lift，line 读成 lineLift。
// 别的颜色不抬。弹层面板在这个包裹外面，仍是真正的 surface；在外面取的 t 也是没抬的颜色。
const LiftCtx = createContext(false);

/** 包住底部弹层的内容。深色里表面两档各抬一档，说明字和细线换成弹层里够对比的那一档；浅色不抬，读到的颜色和页面上一样。 */
export function LiftSurface({ children }: { children: React.ReactNode }) {
  return <LiftCtx.Provider value={true}>{children}</LiftCtx.Provider>;
}

// 网页预览时，宿主页面可能在 <html> 上标 data-theme，优先于系统设置。
function useHostTheme(): 'light' | 'dark' | null {
  const [v, setV] = useState<'light' | 'dark' | null>(null);
  useEffect(() => {
    if (Platform.OS !== 'web' || typeof document === 'undefined') return;
    const root = document.documentElement;
    const read = () => {
      const a = root.getAttribute('data-theme');
      setV(a === 'dark' || a === 'light' ? a : null);
    };
    read();
    const mo = new MutationObserver(read);
    mo.observe(root, { attributes: true, attributeFilter: ['data-theme'] });
    return () => mo.disconnect();
  }, []);
  return v;
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const system = useColorScheme();
  const host = useHostTheme();
  const [appearance, setAppearanceState] = useState<Appearance>(loadAppearance);
  const setAppearance = useCallback((a: Appearance) => {
    setAppearanceState(a);
    writeLocal(APPEARANCE_KEY, a);
  }, []);
  const value = useMemo(() => {
    // 跟随系统但系统没给深浅时，落在深色（和没选过一样），不闪一下浅色。
    const resolved = appearance === 'system' ? host ?? system ?? 'dark' : appearance;
    return { t: resolved === 'dark' ? dark : light, appearance, setAppearance };
  }, [appearance, host, system, setAppearance]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useTheme(): Theme {
  const base = useContext(Ctx).t;
  const lift = useContext(LiftCtx);
  return useMemo(
    () => (lift && base.mode === 'dark'
      ? { ...base, surface: base.surface2, surface2: base.surface3, ink3: base.ink3Lift, line: base.lineLift }
      : base),
    [base, lift],
  );
}
export const useAppearance = () => {
  const { appearance, setAppearance } = useContext(Ctx);
  return { appearance, setAppearance };
};

/**
 * 可视化色板 t.viz（2026-10-13，dataviz 的 validate_palette.js 深浅两套都跑过）：
 * - series：分类色 6 个，同一色相顺序（青、金、紫、绿、蓝、粉），在卡片和页面底色上都通过色觉、对比度校验。
 *   按顺序用、不循环；颜色跟着这一类走，不跟名次走（筛掉一类，其余的不换色）；超过 6 类把末尾的合并成「其他」。
 *   series[0] / series[1] 就是原来的 chartA / chartB。散点、小多图这类任意两点都可能挨着的图最多用前 3 个
 *   （深色前三个任意两两 7.5，在警告区，必须再加一种区分：形状、直接标注或间隙）。
 * - seq：顺序色阶 5 级，低 → 高（深色由暗到亮，浅色由浅到深），单一色相，热力图这类连续量用。
 *   第 0 级贴近底色、表示接近 0；分档的格子用 1–4 级（浅色 1–4 级按有序色阶校验通过，深色第 1 级对底色 1.9:1，属于连续量的低端）。
 * - other：「其他」与弱化的灰，对底色不到 3:1，用到它的地方必须有文字（图例或数值）。和各分类色挨着时色觉差都在 8 以上。
 * - emph：强调（当前这一项、今天），同一色相高一级。
 * - grid / axis：网格线、基线，发丝线、实线、不抢数据。
 * 状态色不在这里：用 good / warn / bad，只表示状态，必须配图标和文字（viz 的 StatusTag）。
 */
/**
 * 可视化标记规范：线宽 2；柱顶 4 圆角、贴基线一端直角；柱子最粗 24；标记点直径 8 外加 2 的底色描边；
 * 相邻色块（堆叠段、相邻柱、环形的段）之间留 2 的底色间隙；点按区域至少 24；面积填充 10%；目标范围带 14%。
 */
export const vizMark = { line: 2, radius: 4, barMax: 24, dot: 8, ring: 2, gap: 2, hit: 24, area: 0.1, band: 0.14 } as const;

export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32 };
export const radius = { sm: 8, md: 12, lg: 18, pill: 999 };
export const type = {
  largeTitle: { fontSize: 32, fontWeight: '700' as const, letterSpacing: -0.4 },
  title: { fontSize: 20, fontWeight: '600' as const, letterSpacing: -0.2 },
  headline: { fontSize: 16, fontWeight: '600' as const },
  body: { fontSize: 16, fontWeight: '400' as const, lineHeight: 23 },
  callout: { fontSize: 14, fontWeight: '400' as const, lineHeight: 20 },
  caption: { fontSize: 12, fontWeight: '500' as const },
  label: { fontSize: 11, fontWeight: '600' as const, letterSpacing: 0.6 },
};
