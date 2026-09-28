import React, { useState } from 'react';
import { Alert, Platform, Pressable, RefreshControl, StyleSheet, Text, TextProps, View, ViewProps, useWindowDimensions, type RefreshControlProps } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { ChevronLeft, ChevronRight, ChevronUp, Pencil } from './icons';
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

/**
 * 下拉刷新。圆环只跟着用户自己的下拉转，不要把 store 的 loading 接到 refreshing 上：
 * iOS 上 refreshing 被代码改成 true 时，RN 会先把 ScrollView 往下推一个圆环的高度，结束时不推回去。
 * 后台重读（每条回复后读 feed、回前台读 journal……）会把没在显示的 tab 一次次往下推，
 * 切过去就是上面一大块空白、中间一个圆环，得手动滑一下才弹回来。
 */
export function PullRefresh({ onRefresh, ...rest }: Omit<RefreshControlProps, 'refreshing' | 'onRefresh'> & { onRefresh: () => unknown }) {
  const [refreshing, setRefreshing] = useState(false);
  const pull = async () => {
    setRefreshing(true);
    try { await onRefresh(); } catch { /* 读失败的原因各页自己显示 */ } finally { setRefreshing(false); }
  };
  return <RefreshControl {...rest} refreshing={refreshing} onRefresh={pull} />;
}

export function T({ v = 'body', color, style, ...rest }: TextProps & { v?: keyof typeof type; color?: string }) {
  const t = useTheme();
  return <Text style={[type[v], { color: color ?? t.ink }, style]} {...rest} />;
}

/**
 * 放钟点的定宽列（日程左边的「11:00」、日志的时间）跟着系统字号一起放宽：字号调大以后，固定的宽度会把「11:00」折成「11:0」「0」两行。
 * 最多放宽到 1.6 倍，再大的辅助字号由那一行自己缩字（numberOfLines={1} + adjustsFontSizeToFit）。
 */
export function useScaledWidth(base: number): number {
  const { fontScale } = useWindowDimensions();
  return Math.round(base * Math.min(Math.max(fontScale || 1, 1), 1.6));
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

/** 页头。onTitlePress：标题变成按钮，旁边一支小笔（Agent 页点名字进编辑）。 */
export function NavHeader({ title, sub, onBack, right, onTitlePress, titleHint }: { title: string; sub?: string; onBack: () => void; right?: React.ReactNode; onTitlePress?: () => void; titleHint?: string }) {
  const t = useTheme();
  return (
    <View style={[styles.navHeader, sub ? { height: 56 } : null, { borderBottomColor: t.line }]}>
      <Pressable onPress={onBack} hitSlop={12} accessibilityRole="button" accessibilityLabel={L('返回', 'Back')} style={styles.navSide}>
        <ChevronLeft size={26} color={t.gold} />
      </Pressable>
      {sub ? (
        <View style={{ flex: 1, alignItems: 'center' }}>
          <T v="headline" numberOfLines={1} style={{ textAlign: 'center' }}>{title}</T>
          <T v="caption" color={t.ink2} numberOfLines={1} style={{ textAlign: 'center', fontWeight: '400', marginTop: 1 }}>{sub}</T>
        </View>
      ) : onTitlePress ? (
        <Pressable onPress={onTitlePress} hitSlop={6} accessibilityRole="button" accessibilityLabel={titleHint ? `${title}${L('，', ', ')}${titleHint}` : title}
          style={({ pressed }) => [styles.navTitleBtn, { opacity: pressed ? 0.6 : 1 }]}>
          <T v="headline" numberOfLines={1} style={{ flexShrink: 1 }}>{title}</T>
          <Pencil size={15} color={t.ink3} />
        </Pressable>
      ) : <T v="headline" numberOfLines={1} style={{ flex: 1, textAlign: 'center' }}>{title}</T>}
      <View style={[styles.navSide, { alignItems: 'flex-end' }]}>{right}</View>
    </View>
  );
}

export function Card({ children, style, ...rest }: ViewProps) {
  const t = useTheme();
  return <View style={[{ backgroundColor: t.surface, borderRadius: radius.lg, padding: space.lg }, style]} {...rest}>{children}</View>;
}

/** 小标题。caps={false}：原样显示（小标题是用户自己的数据时，比如记忆的小节名，不改大小写）。 */
export function SectionLabel({ children, right, caps = true }: { children: string; right?: React.ReactNode; caps?: boolean }) {
  const t = useTheme();
  return (
    <View style={styles.sectionLabel}>
      <T v="label" color={t.ink3} style={caps ? { textTransform: 'uppercase' } : undefined}>{children}</T>
      {right}
    </View>
  );
}

/** 小标签。colors = [底色, 字色]，给 Agent 自己的颜色用；不给就按 tone。 */
export function Pill({ label, tone = 'neutral', colors, lines }: { label: string; tone?: 'neutral' | 'gold' | 'cyan' | 'good' | 'warn' | 'bad'; colors?: readonly [string, string]; lines?: number }) {
  const t = useTheme();
  const map = {
    neutral: [t.surface2, t.ink2], gold: [t.goldSoft, t.gold], cyan: [t.cyanSoft, t.cyan],
    good: [t.goodSoft, t.good], warn: [t.warnSoft, t.warn], bad: [t.badSoft, t.bad],
  } as const;
  const [bg, fg] = colors ?? map[tone];
  // lines：挤的地方（聊天顶栏）只占这么多行，放不下就省略号，不压到旁边的按钮底下
  return (
    <View style={[{ backgroundColor: bg, borderRadius: radius.pill, paddingHorizontal: 8, paddingVertical: 3 }, lines ? { flexShrink: 1 } : null]}>
      <Text style={[type.caption, { color: fg }]} numberOfLines={lines}>{label}</Text>
    </View>
  );
}

/** 青色的未读数（Agent 列表、侧栏）。0 不显示。 */
export function CountPill({ n, small }: { n: number; small?: boolean }) {
  const t = useTheme();
  if (!n) return null;
  const h = small ? 20 : 24;
  return (
    <View accessible accessibilityLabel={L(`${n} 条未读`, `${n} unread`)}
      style={{ minWidth: h, height: h, borderRadius: h / 2, paddingHorizontal: small ? 6 : 7, backgroundColor: t.cyan, alignItems: 'center', justifyContent: 'center' }}>
      <Text style={{ color: t.surface, fontSize: small ? 11 : 13, fontWeight: '700' }}>{n > 99 ? '99+' : String(n)}</Text>
    </View>
  );
}

/** 点开 / 收起的箭头：收着朝右，展开朝上（看板的一行、记忆的一条都用它）。 */
export function Disclosure({ open, size = 16 }: { open: boolean; size?: number }) {
  const t = useTheme();
  return open ? <ChevronUp size={size} color={t.ink3} /> : <ChevronRight size={size} color={t.ink3} />;
}

/** 出错提示。react-native-web 的 Alert 什么都不做，网页上改用浏览器自己的 alert，免得点了没反应。 */
export function showError(title: string, e: unknown) {
  const msg = e instanceof Error ? e.message : String(e ?? '');
  if (Platform.OS === 'web') {
    if (typeof window !== 'undefined' && typeof window.alert === 'function') window.alert(msg ? `${title}\n${msg}` : title);
    return;
  }
  Alert.alert(title, msg);
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
  navTitleBtn: { flex: 1, minWidth: 0, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6, paddingVertical: 6 },
  sectionLabel: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingHorizontal: space.xs, marginTop: space.xl, marginBottom: space.sm },
  btn: { flexDirection: 'row', gap: 6, alignItems: 'center', justifyContent: 'center', borderRadius: radius.md, paddingVertical: 11, paddingHorizontal: space.lg },
  row: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: 13 },
  rowIcon: { width: 28, alignItems: 'center' },
  seg: { flexDirection: 'row', borderRadius: 10, padding: 2 },
  segItem: { flex: 1, alignItems: 'center', paddingVertical: 7, borderRadius: 8 },
});
