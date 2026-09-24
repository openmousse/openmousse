import React, { createContext, useContext, useEffect, useMemo, useState } from 'react';
import { Platform, useColorScheme } from 'react-native';

// Grava 的视觉身份来自头像：引力透镜。墨黑力场、金色光环、青色数据流。
// 金 = Grava 自己（头像、发送、当前选中）；青 = 数据与进度；语义色只表示状态。
const light = {
  mode: 'light' as 'light' | 'dark',
  bg: '#F1F3F4',
  surface: '#FFFFFF',
  surface2: '#E7EAEC',
  ink: '#12151A',
  ink2: '#4B535C',
  ink3: '#7D858F',
  line: '#DCE0E3',
  gold: '#9A6B16',
  goldFill: '#D9AE62',
  goldSoft: '#F5EAD3',
  onGold: '#2A1E06',
  cyan: '#0F7A93',
  cyanSoft: '#DAF0F5',
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
};

const dark: typeof light = {
  mode: 'dark',
  bg: '#0B0D10',
  surface: '#14171C',
  surface2: '#1E2329',
  ink: '#ECEEF0',
  ink2: '#A6AEB7',
  ink3: '#737C86',
  line: '#252A31',
  gold: '#DDB56A',
  goldFill: '#D9AE62',
  goldSoft: '#30271A',
  onGold: '#2A1E06',
  cyan: '#5CCFE6',
  cyanSoft: '#10303A',
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
};

export type Theme = typeof light;
export type Appearance = 'system' | 'light' | 'dark';

const Ctx = createContext<{ t: Theme; appearance: Appearance; setAppearance: (a: Appearance) => void }>({
  t: light,
  appearance: 'system',
  setAppearance: () => {},
});

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
  const [appearance, setAppearance] = useState<Appearance>('system');
  const value = useMemo(() => {
    const resolved = appearance === 'system' ? host ?? system ?? 'light' : appearance;
    return { t: resolved === 'dark' ? dark : light, appearance, setAppearance };
  }, [appearance, host, system]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export const useTheme = () => useContext(Ctx).t;
export const useAppearance = () => {
  const { appearance, setAppearance } = useContext(Ctx);
  return { appearance, setAppearance };
};

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
