// 设置页那一套样子（2026-09-29，照 Claude 的设置页）：圆按钮的页头、大圆角的分组、行（图标 + 名字 + 右边的值 + 箭头，
// 分隔线从文字开始）、应用图标块、数字小圆、状态点。设置、claw、账号、连接器几页共用。
import React from 'react';
import { Pressable, StyleSheet, Text, View, type ViewProps } from 'react-native';
import { ChevronLeft, ChevronRight, X } from './icons';
import { T } from './ui';
import { L } from '../i18n';
import { space, useTheme } from '../theme';

/** 带细边框的圆按钮（页头两边）。 */
export function RoundButton({ onPress, label, children }: { onPress: () => void; label: string; children: React.ReactNode }) {
  const t = useTheme();
  return (
    <Pressable onPress={onPress} accessibilityRole="button" accessibilityLabel={label} hitSlop={6}
      style={({ pressed }) => [styles.round, { borderColor: t.line, backgroundColor: t.surface, opacity: pressed ? 0.6 : 1 }]}>
      {children}
    </Pressable>
  );
}

/** 页头：左边圆的返回（close = 叉，设置页本身），中间标题，右边可以放一个圆按钮。 */
export function SettingsHeader({ title, onBack, close, right }: { title: string; onBack?: () => void; close?: boolean; right?: React.ReactNode }) {
  const t = useTheme();
  return (
    <View style={styles.header}>
      <View style={styles.side}>
        {onBack ? (
          <RoundButton onPress={onBack} label={close ? L('关上', 'Close') : L('返回', 'Back')}>
            {close ? <X size={20} color={t.ink} /> : <ChevronLeft size={22} color={t.ink} />}
          </RoundButton>
        ) : null}
      </View>
      <T v="headline" numberOfLines={1} style={styles.title}>{title}</T>
      <View style={[styles.side, { alignItems: 'flex-end' }]}>{right}</View>
    </View>
  );
}

/** 一组设置：大圆角白底，里面是一行行 Row。 */
export function Group({ children, style, ...rest }: ViewProps) {
  const t = useTheme();
  return <View style={[styles.group, { backgroundColor: t.surface }, style]} {...rest}>{children}</View>;
}

/** 组上面的小标题。 */
export function GroupLabel({ children, right }: { children: string; right?: React.ReactNode }) {
  const t = useTheme();
  return (
    <View style={styles.groupLabel}>
      <T v="callout" color={t.ink3} style={{ fontWeight: '500', flexShrink: 1 }}>{children}</T>
      {right}
    </View>
  );
}

/** 组下面的一行小字说明。 */
export function GroupNote({ children }: { children: string }) {
  const t = useTheme();
  return <T v="caption" color={t.ink3} style={styles.note}>{children}</T>;
}

type Tone = 'good' | 'warn' | 'bad' | 'accent' | 'muted';

export function toneColor(t: ReturnType<typeof useTheme>, tone?: Tone): string {
  switch (tone) {
    case 'good': return t.good;
    case 'warn': return t.warn;
    case 'bad': return t.bad;
    case 'accent': return t.cyan;
    case 'muted': return t.ink3;
    default: return t.ink2;
  }
}

/**
 * 一行：icon（24 左右的图标或 Tile）、title、sub（第二行小字）、value（右边的值，tone 给颜色）、right（右边自己画的东西）。
 * first：组里第一行（上面不画分隔线）。danger：红字（退出、删除）。accent：青字（「添加 claw」这种动作行）。chevron 默认跟着 onPress。
 */
export function Row({ icon, title, sub, value, tone, right, onPress, first, danger, accent, chevron, label, disabled }: {
  icon?: React.ReactNode; title: string; sub?: string; value?: string; tone?: Tone; right?: React.ReactNode; onPress?: () => void;
  first?: boolean; danger?: boolean; accent?: boolean; chevron?: boolean; label?: string; disabled?: boolean;
}) {
  const t = useTheme();
  const showChevron = chevron ?? (!!onPress && !danger && !accent);
  const body = (
    <View style={[styles.row, disabled ? { opacity: 0.5 } : null]}>
      {first ? null : <View style={[styles.sep, { left: icon ? 60 : 18, backgroundColor: t.line }]} />}
      {icon ? <View style={styles.rowIcon}>{icon}</View> : null}
      <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
        <T v="body" numberOfLines={2} color={danger ? t.bad : accent ? t.cyan : undefined} style={{ fontSize: 17, fontWeight: accent ? '500' : '400' }}>{title}</T>
        {sub ? <T v="callout" color={t.ink2} numberOfLines={2}>{sub}</T> : null}
      </View>
      {value ? <T v="body" numberOfLines={1} color={toneColor(t, tone)} style={styles.value}>{value}</T> : null}
      {right}
      {showChevron ? <ChevronRight size={18} color={t.ink3} /> : null}
    </View>
  );
  if (!onPress) return body;
  return (
    <Pressable onPress={disabled ? undefined : onPress} accessibilityRole="button" accessibilityLabel={label ?? (value ? `${title}${L('，', ', ')}${value}` : title)}
      style={({ pressed }) => ({ opacity: pressed ? 0.6 : 1 })}>
      {body}
    </Pressable>
  );
}

/** 应用图标块：1–3 个字，目录给的底色和字色（深色模式统一用深底白字，品牌色在深底上看不清）；也可以放一个图标。 */
export function Tile({ mono, bg, fg, border, size = 36, children }: { mono?: string | null; bg?: string | null; fg?: string | null; border?: string | boolean | null; size?: number; children?: React.ReactNode }) {
  const t = useTheme();
  const dark = t.mode === 'dark';
  const text = (mono || '').slice(0, 3);
  return (
    <View style={{
      width: size, height: size, borderRadius: Math.round(size * 0.28), alignItems: 'center', justifyContent: 'center',
      backgroundColor: dark ? t.surface2 : (bg || t.surface2),
      borderWidth: !dark && border ? StyleSheet.hairlineWidth : 0, borderColor: t.line,
    }}>
      {children ?? <Text style={{ color: dark ? t.ink : (fg || t.ink), fontSize: Math.round(size * (text.length > 2 ? 0.3 : 0.38)), fontWeight: '700' }}>{text}</Text>}
    </View>
  );
}

/** 右边的数字小圆（连接器能用的工具数）。 */
export function Count({ n, label }: { n: number; label?: string }) {
  const t = useTheme();
  return (
    <View accessible accessibilityLabel={label ?? String(n)} style={[styles.count, { backgroundColor: t.cyanSoft }]}>
      <Text style={{ color: t.cyan, fontSize: 14, fontWeight: '700' }}>{n}</Text>
    </View>
  );
}

/** 小胶囊（「要重新授权」「在线」这种）。 */
export function Chip({ label, tone = 'muted' }: { label: string; tone?: Tone }) {
  const t = useTheme();
  const bg = tone === 'good' ? t.goodSoft : tone === 'warn' ? t.warnSoft : tone === 'bad' ? t.badSoft : tone === 'accent' ? t.cyanSoft : t.surface2;
  return (
    <View style={[styles.chip, { backgroundColor: bg }]}>
      <Text style={{ color: toneColor(t, tone === 'muted' ? undefined : tone), fontSize: 13, fontWeight: '600' }}>{label}</Text>
    </View>
  );
}

/** 状态点：绿 = 在线 / 在用，琥珀 = 要注意，灰 = 离线 / 没接。 */
export function Dot({ tone }: { tone: 'good' | 'warn' | 'off' }) {
  const t = useTheme();
  return <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: tone === 'good' ? t.good : tone === 'warn' ? t.warn : t.ink3, opacity: tone === 'off' ? 0.6 : 1 }} />;
}

/** 行右边的箭头（自己画 right 时要箭头就加它）。 */
export function Chevron() {
  const t = useTheme();
  return <ChevronRight size={18} color={t.ink3} />;
}

export const settingsStyles = { pad: space.lg };

const styles = StyleSheet.create({
  header: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: space.lg, paddingTop: 6, paddingBottom: 6, minHeight: 56 },
  side: { width: 56, justifyContent: 'center' },
  title: { flex: 1, textAlign: 'center', fontSize: 17 },
  round: { width: 42, height: 42, borderRadius: 21, borderWidth: StyleSheet.hairlineWidth, alignItems: 'center', justifyContent: 'center' },
  group: { borderRadius: 22, overflow: 'hidden', marginHorizontal: space.lg },
  groupLabel: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: space.md, marginTop: 26, marginBottom: 8, marginHorizontal: space.lg + 16 },
  note: { marginTop: 8, marginHorizontal: space.lg + 16, lineHeight: 18, fontWeight: '400' },
  row: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingLeft: 18, paddingRight: 16, paddingVertical: 14, minHeight: 56 },
  sep: { position: 'absolute', top: 0, right: 0, height: StyleSheet.hairlineWidth },
  rowIcon: { width: 30, alignItems: 'center', justifyContent: 'center' },
  value: { maxWidth: '48%', fontSize: 16, textAlign: 'right' },
  count: { minWidth: 30, height: 28, borderRadius: 14, paddingHorizontal: 9, alignItems: 'center', justifyContent: 'center' },
  chip: { height: 26, borderRadius: 13, paddingHorizontal: 10, alignItems: 'center', justifyContent: 'center' },
});
