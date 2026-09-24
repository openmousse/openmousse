import React from 'react';
import { Pressable, StyleSheet, Text, TextProps, View, ViewProps } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { ChevronLeft, ChevronRight } from './icons';
import { L } from '../i18n';
import { radius, space, type, useTheme } from '../theme';

export function Screen({ children, style, ...rest }: ViewProps) {
  const t = useTheme();
  const insets = useSafeAreaInsets();
  return (
    <View style={[{ flex: 1, backgroundColor: t.bg, paddingTop: insets.top }, style]} {...rest}>
      {children}
    </View>
  );
}

export function T({ v = 'body', color, style, ...rest }: TextProps & { v?: keyof typeof type; color?: string }) {
  const t = useTheme();
  return <Text style={[type[v], { color: color ?? t.ink }, style]} {...rest} />;
}

export function LargeHeader({ title, sub, right }: { title: string; sub?: string; right?: React.ReactNode }) {
  const t = useTheme();
  return (
    <View style={styles.largeHeader}>
      <View style={{ flex: 1 }}>
        <T v="largeTitle">{title}</T>
        {sub ? <T v="callout" color={t.ink2} style={{ marginTop: 2 }}>{sub}</T> : null}
      </View>
      {right}
    </View>
  );
}

export function NavHeader({ title, onBack, right }: { title: string; onBack: () => void; right?: React.ReactNode }) {
  const t = useTheme();
  return (
    <View style={[styles.navHeader, { borderBottomColor: t.line }]}>
      <Pressable onPress={onBack} hitSlop={12} accessibilityRole="button" accessibilityLabel={L('返回', 'Back')} style={styles.navSide}>
        <ChevronLeft size={26} color={t.gold} />
      </Pressable>
      <T v="headline" numberOfLines={1} style={{ flex: 1, textAlign: 'center' }}>{title}</T>
      <View style={[styles.navSide, { alignItems: 'flex-end' }]}>{right}</View>
    </View>
  );
}

export function Card({ children, style, ...rest }: ViewProps) {
  const t = useTheme();
  return <View style={[{ backgroundColor: t.surface, borderRadius: radius.lg, padding: space.lg }, style]} {...rest}>{children}</View>;
}

export function SectionLabel({ children, right }: { children: string; right?: React.ReactNode }) {
  const t = useTheme();
  return (
    <View style={styles.sectionLabel}>
      <T v="label" color={t.ink3} style={{ textTransform: 'uppercase' }}>{children}</T>
      {right}
    </View>
  );
}

export function Pill({ label, tone = 'neutral' }: { label: string; tone?: 'neutral' | 'gold' | 'cyan' | 'good' | 'warn' | 'bad' }) {
  const t = useTheme();
  const map = {
    neutral: [t.surface2, t.ink2], gold: [t.goldSoft, t.gold], cyan: [t.cyanSoft, t.cyan],
    good: [t.goodSoft, t.good], warn: [t.warnSoft, t.warn], bad: [t.badSoft, t.bad],
  } as const;
  const [bg, fg] = map[tone];
  return (
    <View style={{ backgroundColor: bg, borderRadius: radius.pill, paddingHorizontal: 8, paddingVertical: 3 }}>
      <Text style={[type.caption, { color: fg }]}>{label}</Text>
    </View>
  );
}

export function Btn({ label, onPress, kind = 'primary', icon, flex }: { label: string; onPress: () => void; kind?: 'primary' | 'quiet' | 'danger'; icon?: React.ReactNode; flex?: boolean }) {
  const t = useTheme();
  const bg = kind === 'primary' ? t.goldFill : kind === 'danger' ? t.badSoft : t.surface2;
  const fg = kind === 'primary' ? t.onGold : kind === 'danger' ? t.bad : t.ink;
  return (
    <Pressable onPress={onPress} accessibilityRole="button" style={({ pressed }) => [styles.btn, { backgroundColor: bg, opacity: pressed ? 0.75 : 1 }, flex ? { flex: 1 } : null]}>
      {icon}
      <Text style={[type.headline, { color: fg, fontSize: 15 }]}>{label}</Text>
    </Pressable>
  );
}

export function ListRow({ icon, title, sub, right, onPress, last }: { icon?: React.ReactNode; title: string; sub?: string; right?: React.ReactNode; onPress?: () => void; last?: boolean }) {
  const t = useTheme();
  const body = (
    <View style={[styles.row, !last && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }]}>
      {icon ? <View style={styles.rowIcon}>{icon}</View> : null}
      <View style={{ flex: 1, gap: 2 }}>
        <T v="body" numberOfLines={2}>{title}</T>
        {sub ? <T v="callout" color={t.ink2} numberOfLines={2}>{sub}</T> : null}
      </View>
      {right ?? (onPress ? <ChevronRight size={18} color={t.ink3} /> : null)}
    </View>
  );
  return onPress ? <Pressable onPress={onPress} accessibilityRole="button" style={({ pressed }) => ({ opacity: pressed ? 0.6 : 1 })}>{body}</Pressable> : body;
}

export function Segmented<V extends string>({ options, value, onChange }: { options: { value: V; label: string }[]; value: V; onChange: (v: V) => void }) {
  const t = useTheme();
  return (
    <View style={[styles.seg, { backgroundColor: t.surface2 }]} accessibilityRole="tablist">
      {options.map((o) => {
        const on = o.value === value;
        return (
          <Pressable key={o.value} onPress={() => onChange(o.value)} accessibilityRole="tab" accessibilityState={{ selected: on }}
            style={[styles.segItem, on && { backgroundColor: t.surface }]}>
            <Text style={[type.caption, { fontSize: 13, color: on ? t.ink : t.ink2 }]}>{o.label}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  largeHeader: { flexDirection: 'row', alignItems: 'flex-end', paddingHorizontal: space.lg, paddingTop: space.md, paddingBottom: space.md, gap: space.md },
  navHeader: { flexDirection: 'row', alignItems: 'center', height: 48, paddingHorizontal: space.sm, borderBottomWidth: StyleSheet.hairlineWidth },
  // 两侧至少 72 宽让标题大体居中；右侧放了日历按钮 + 模型选择这种宽内容时按内容撑开，不能挤出屏幕
  navSide: { minWidth: 72, flexShrink: 0, justifyContent: 'center' },
  sectionLabel: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingHorizontal: space.xs, marginTop: space.xl, marginBottom: space.sm },
  btn: { flexDirection: 'row', gap: 6, alignItems: 'center', justifyContent: 'center', borderRadius: radius.md, paddingVertical: 11, paddingHorizontal: space.lg },
  row: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: 13 },
  rowIcon: { width: 28, alignItems: 'center' },
  seg: { flexDirection: 'row', borderRadius: 10, padding: 2 },
  segItem: { flex: 1, alignItems: 'center', paddingVertical: 7, borderRadius: 8 },
});
