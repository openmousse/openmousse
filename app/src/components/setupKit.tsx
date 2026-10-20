// 第一次打开的三步和设置中心共用的那一套（2026-10-19，设计指挥部第 17、18 张，第三版）：
// 左上角的进度环、大标题、分组标题、卡片（卡里的分隔用 surface2，不画细线分区）、服务商小格、主按钮、「以后再说」、密钥框、测一下的结果、
// 「设好了」那一屏的标志（环合上、青点落到 1 点半）。颜色只用 theme 的 token；系统开了「减少动态效果」就不动。
import React, { useEffect, useState } from 'react';
import { AccessibilityInfo, ActivityIndicator, Animated, Easing, Platform, Pressable, StyleSheet, Text, TextInput, View, type TextInputProps, type ViewProps } from 'react-native';
import Svg, { Circle } from 'react-native-svg';
import * as Clipboard from 'expo-clipboard';
import { Spinner } from './ChatCards';
import { ChevronLeft, ChevronRight } from './icons';
import { Tile } from './settings';
import { T } from './ui';
import { ProgressRing } from './viz';
import type { ProbeError } from '../api/setup';
import { L } from '../i18n';
import { radius, space, type, useTheme } from '../theme';

// —— 减少动态效果：模块里记一份（导航的转场选项要同步拿到），系统设置变了跟着变 ——
let reduced = false;
AccessibilityInfo.isReduceMotionEnabled().then((v) => { reduced = !!v; }).catch(() => {});
AccessibilityInfo.addEventListener?.('reduceMotionChanged', (v: boolean) => { reduced = !!v; });

export const reduceMotion = () => reduced;

export function useReduceMotion(): boolean {
  const [v, setV] = useState(reduced);
  useEffect(() => {
    let live = true;
    AccessibilityInfo.isReduceMotionEnabled().then((x) => { if (live) setV(!!x); }).catch(() => {});
    const sub = AccessibilityInfo.addEventListener?.('reduceMotionChanged', (x: boolean) => setV(!!x));
    return () => { live = false; sub?.remove?.(); };
  }, []);
  return v;
}

/** 三步之间的转场：快（260ms），系统开了「减少动态效果」就不动。navigation.tsx 的 options 用它。 */
export const stepTransition = () => (reduced ? { animation: 'none' as const } : { animation: 'simple_push' as const, animationDuration: 260 });

const native = Platform.OS !== 'web';

/** 等宽字（密钥、型号名、命令、地址）。 */
export const monoFont = { fontFamily: Platform.select({ ios: 'Menlo', android: 'monospace', default: 'ui-monospace, Menlo, Consolas, monospace' }) };

// —— 页面的骨架 ————————————————————————————————————————————————————————————————————

/** 左上角的进度：44 的环，底圈 line，走过的一段 goldFill，一步走三分之一；旁边等宽字「1 / 3」。 */
export function StepRing({ step, total = 3 }: { step: number; total?: number }) {
  const t = useTheme();
  return (
    <View style={styles.ringRow}>
      <ProgressRing value={step} target={total} size={44} stroke={3.2} color={t.goldFill} track={t.line}
        label={L(`第 ${step} 步，共 ${total} 步`, `Step ${step} of ${total}`)} />
      <Text style={[type.caption, styles.mono, { color: t.ink3, fontWeight: '400' }]} accessibilityElementsHidden importantForAccessibility="no">{`${step} / ${total}`}</Text>
    </View>
  );
}

/** 返回（三步里点进一家以后）：圆的，和设置页的一样大。 */
export function BackButton({ onPress }: { onPress: () => void }) {
  const t = useTheme();
  return (
    <Pressable onPress={onPress} accessibilityRole="button" accessibilityLabel={L('返回', 'Back')} hitSlop={6}
      style={({ pressed }) => [styles.back, { borderColor: t.line, backgroundColor: t.surface, opacity: pressed ? 0.6 : 1 }]}>
      <ChevronLeft size={22} color={t.ink} />
    </Pressable>
  );
}

/** 大标题 26 粗 700；副标题 14、行高 1.6、ink2。 */
export function SetupHeading({ title, sub }: { title: string; sub?: string }) {
  const t = useTheme();
  return (
    <View style={{ gap: space.sm, marginTop: space.lg }}>
      <T v="largeTitle" accessibilityRole="header" style={{ fontSize: 26, fontWeight: '700', letterSpacing: -0.3 }}>{title}</T>
      {sub ? <T v="callout" color={t.ink2} style={{ lineHeight: 22 }}>{sub}</T> : null}
    </View>
  );
}

/** 分组标题 13 粗 600 ink2，下面一行说明 12 ink3。right：标题右边（「全部 N 家 ›」）。 */
export function GroupHead({ title, note, right }: { title: string; note?: string | null; right?: React.ReactNode }) {
  const t = useTheme();
  return (
    <View style={styles.groupHead}>
      <View style={{ flex: 1, gap: 2 }}>
        <T v="caption" color={t.ink2} accessibilityRole="header" style={{ fontSize: 13, fontWeight: '600' }}>{title}</T>
        {note ? <T v="caption" color={t.ink3} style={{ fontWeight: '400', lineHeight: 17 }}>{note}</T> : null}
      </View>
      {right}
    </View>
  );
}

/** 卡片：surface 底、圆角 18。 */
export function KitCard({ children, style, ...rest }: ViewProps) {
  const t = useTheme();
  return <View style={[{ backgroundColor: t.surface, borderRadius: radius.lg, overflow: 'hidden' }, style]} {...rest}>{children}</View>;
}

/** 卡里两段之间的分隔：surface2，从左边留出一截（和行的分隔一样）。 */
export function KitSep() {
  const t = useTheme();
  return <View style={{ height: 1, marginLeft: space.lg, backgroundColor: t.surface2 }} />;
}

/** 卡里的一行：分隔用 surface2（第一行不画）；右边 value（ink2）或自己画的 right；能点的带箭头。 */
export function KitRow({ title, sub, value, right, onPress, first, disabled, chevron, mono, tone, label, icon }: {
  title: string; sub?: string | null; value?: string | null; right?: React.ReactNode; onPress?: () => void; first?: boolean; disabled?: boolean;
  chevron?: boolean; mono?: boolean; tone?: 'muted' | 'warn' | 'bad' | 'good'; label?: string; icon?: React.ReactNode;
}) {
  const t = useTheme();
  const valueColor = tone === 'warn' ? t.warn : tone === 'bad' ? t.bad : tone === 'good' ? t.good : tone === 'muted' ? t.ink3 : t.ink2;
  const showChevron = chevron ?? (!!onPress && !disabled);
  const body = (
    <View style={[styles.row, disabled ? { opacity: 0.5 } : null]}>
      {first ? null : <View style={[styles.sep, { backgroundColor: t.surface2, left: icon ? 54 : space.lg }]} />}
      {icon ? <View style={{ width: 26, alignItems: 'center' }}>{icon}</View> : null}
      <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
        <T v="body" numberOfLines={2} style={[{ fontSize: 16 }, mono ? styles.mono : null]}>{title}</T>
        {sub ? <T v="caption" color={t.ink3} numberOfLines={3} style={{ fontWeight: '400', fontSize: 12.5, lineHeight: 17 }}>{sub}</T> : null}
      </View>
      {value ? <T v="callout" color={valueColor} numberOfLines={1} style={{ maxWidth: '52%', textAlign: 'right' }}>{value}</T> : null}
      {right}
      {showChevron ? <ChevronRight size={17} color={t.ink3} /> : null}
    </View>
  );
  if (!onPress) return body;
  return (
    <Pressable onPress={disabled ? undefined : onPress} accessibilityRole="button" accessibilityState={{ disabled: !!disabled }}
      accessibilityLabel={label ?? [title, value, sub].filter(Boolean).join(L('，', ', '))}
      style={({ pressed }) => ({ opacity: pressed && !disabled ? 0.6 : 1 })}>
      {body}
    </Pressable>
  );
}

// —— 服务商小格 ——————————————————————————————————————————————————————————————————————

/** 云端的小格：高 70、圆角、surface 底、1px surface2 描边；28×28 的字母块和 11.5 的名字；选中的外面一圈 1.5px 的 goldFill。 */
export function ProviderTile({ name, mono, selected, onPress, caption, label, disabled }: {
  name: string; mono: string; selected?: boolean; onPress: () => void; caption?: string | null; label?: string; disabled?: boolean;
}) {
  const t = useTheme();
  return (
    <Pressable onPress={disabled ? undefined : onPress} accessibilityRole="button" accessibilityState={{ selected: !!selected, disabled: !!disabled }}
      accessibilityLabel={label ?? [name, caption].filter(Boolean).join(L('，', ', '))}
      style={({ pressed }) => [styles.tileWrap, { borderColor: selected ? t.goldFill : 'transparent', opacity: disabled ? 0.5 : pressed ? 0.7 : 1 }]}>
      <View style={[styles.tile, { backgroundColor: t.surface, borderColor: t.surface2, minHeight: caption ? 84 : 70 }]}>
        <Tile size={28} mono={mono} />
        <Text style={[type.caption, { color: t.ink, fontSize: 11.5, textAlign: 'center' }]} numberOfLines={1}>{name}</Text>
        {caption ? <Text style={[type.caption, { color: t.ink3, fontSize: 11, fontWeight: '400', textAlign: 'center' }]} numberOfLines={2}>{caption}</Text> : null}
      </View>
    </Pressable>
  );
}

/** 本机跑的矮格（高 52）：名字下面一个小点加一行状态（绿 = 已找到，灰 = 没在跑 / 仅 Mac）。 */
export function LocalTile({ name, found, status, selected, onPress, disabled }: {
  name: string; found: boolean; status: string; selected?: boolean; onPress: () => void; disabled?: boolean;
}) {
  const t = useTheme();
  return (
    <Pressable onPress={disabled ? undefined : onPress} accessibilityRole="button" accessibilityState={{ selected: !!selected, disabled: !!disabled }}
      accessibilityLabel={`${name}${L('，', ', ')}${status}`}
      style={({ pressed }) => [styles.tileWrap, { borderColor: selected ? t.goldFill : 'transparent', opacity: disabled ? 0.55 : pressed ? 0.7 : 1 }]}>
      <View style={[styles.lowTile, { backgroundColor: t.surface, borderColor: t.surface2 }]}>
        <View style={{ flex: 1, minWidth: 0, gap: 3 }}>
          <Text style={[type.caption, { color: t.ink, fontSize: 11.5 }]} numberOfLines={1}>{name}</Text>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
            <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: found ? t.good : t.ink3, opacity: found ? 1 : 0.7 }} />
            <Text style={[type.caption, { color: found ? t.ink2 : t.ink3, fontSize: 11, fontWeight: '400', flexShrink: 1 }]} numberOfLines={1}>{status}</Text>
          </View>
        </View>
      </View>
    </Pressable>
  );
}

/** 一排几个小格（等宽，间距 8）。 */
export function TileRow({ children }: { children: React.ReactNode }) {
  return <View style={styles.tileRow}>{React.Children.map(children, (c) => <View style={{ flex: 1, minWidth: 0 }}>{c}</View>)}</View>;
}

// —— 按钮 ————————————————————————————————————————————————————————————————————————————

/** 主按钮：高 48、goldFill 底、onGold 字 16 粗 600；busy 时前面一个小环在转。 */
export function PrimaryButton({ label, onPress, busy, disabled }: { label: string; onPress: () => void; busy?: boolean; disabled?: boolean }) {
  const t = useTheme();
  const off = disabled || busy;
  return (
    <Pressable onPress={off ? undefined : onPress} accessibilityRole="button" accessibilityState={{ disabled: !!disabled, busy: !!busy }} accessibilityLabel={label}
      style={({ pressed }) => [styles.primary, { backgroundColor: disabled ? t.surface2 : t.goldFill, opacity: pressed && !off ? 0.8 : 1 }]}>
      {busy ? <Spinner size={16} color={t.onGold} /> : null}
      <Text style={[type.headline, { color: disabled ? t.ink3 : t.onGold }]}>{label}</Text>
    </Pressable>
  );
}

/** 次要的按钮（「换密钥」「再测」）：surface2 底。 */
export function QuietButton({ label, onPress, busy }: { label: string; onPress: () => void; busy?: boolean }) {
  const t = useTheme();
  return (
    <Pressable onPress={busy ? undefined : onPress} accessibilityRole="button" accessibilityLabel={label}
      style={({ pressed }) => [styles.quiet, { backgroundColor: t.surface2, opacity: pressed ? 0.7 : 1 }]}>
      {busy ? <Spinner size={14} color={t.ink2} /> : null}
      <Text style={[type.headline, { color: t.ink, fontSize: 15 }]}>{label}</Text>
    </Pressable>
  );
}

/** 「以后再说」：没有底的字按钮。 */
export function LaterButton({ onPress, label }: { onPress: () => void; label?: string }) {
  const t = useTheme();
  const text = label ?? L('以后再说', 'Later');
  return (
    <Pressable onPress={onPress} accessibilityRole="button" accessibilityLabel={text} style={styles.later}>
      <T v="callout" color={t.ink2} style={{ fontWeight: '500' }}>{text}</T>
    </Pressable>
  );
}

/** 金色的链接（「去 OpenAI 拿密钥 ›」「全部 24 家 ›」）。 */
export function GoldLink({ label, onPress }: { label: string; onPress: () => void }) {
  const t = useTheme();
  return (
    <Pressable onPress={onPress} accessibilityRole="link" hitSlop={8} style={({ pressed }) => ({ opacity: pressed ? 0.6 : 1 })}>
      <T v="caption" color={t.gold} style={{ fontSize: 13, fontWeight: '600' }}>{`${label} ›`}</T>
    </Pressable>
  );
}

// —— 输入框 ——————————————————————————————————————————————————————————————————————————

/** 一个输入框（地址、名字、型号名）：surface 底、圆角；focus 时青色的框。mono：等宽字。 */
export function Field({ mono, style, ...rest }: TextInputProps & { mono?: boolean }) {
  const t = useTheme();
  const [focus, setFocus] = useState(false);
  return (
    <TextInput placeholderTextColor={t.ink3} autoCapitalize="none" autoCorrect={false} {...rest}
      onFocus={(e) => { setFocus(true); rest.onFocus?.(e); }} onBlur={(e) => { setFocus(false); rest.onBlur?.(e); }}
      style={[type.body, styles.field, { backgroundColor: t.surface, color: t.ink, borderColor: focus ? t.cyan : t.surface2 }, mono ? styles.mono : null, style]} />
  );
}

/** 密钥：等宽字的密码框，右边「粘贴」（expo-clipboard）。密钥只在这台设备的内存里，存好以后只显示后四位。 */
export function KeyField({ value, onChange, placeholder, label, autoFocus }: { value: string; onChange: (v: string) => void; placeholder?: string; label: string; autoFocus?: boolean }) {
  const t = useTheme();
  const [focus, setFocus] = useState(false);
  const paste = async () => {
    try {
      const s = (await Clipboard.getStringAsync()).trim();
      if (s) onChange(s);
    } catch { /* 读不到剪贴板（网页没给权限）：手动粘 */ }
  };
  return (
    <View style={[styles.keyBox, { backgroundColor: t.surface, borderColor: focus ? t.cyan : t.surface2 }]}>
      <TextInput value={value} onChangeText={onChange} placeholder={placeholder} placeholderTextColor={t.ink3} secureTextEntry autoCapitalize="none"
        autoCorrect={false} autoComplete="off" textContentType="none" spellCheck={false} accessibilityLabel={label} autoFocus={autoFocus}
        onFocus={() => setFocus(true)} onBlur={() => setFocus(false)}
        style={[type.body, styles.mono, { flex: 1, color: t.ink, paddingVertical: 13, fontSize: 15 }]} />
      <Pressable onPress={paste} accessibilityRole="button" accessibilityLabel={L('粘贴', 'Paste')} hitSlop={8} style={({ pressed }) => [styles.paste, { opacity: pressed ? 0.6 : 1 }]}>
        <T v="callout" color={t.gold} style={{ fontWeight: '600' }}>{L('粘贴', 'Paste')}</T>
      </Pressable>
    </View>
  );
}

// —— 测一下的结果 ————————————————————————————————————————————————————————————————————

/** 没通的一句话（按 error.kind）。 */
export function failLine(kind: string | undefined): string {
  switch (kind) {
    case 'auth': return L('密钥不对，或者没有权限', 'The key is wrong, or it lacks permission');
    case 'billing': return L('余额或额度不够', 'Out of credit or quota');
    case 'network': return L('连接不上', "Couldn't connect");
    case 'no_key': return L('还没有密钥', 'No key yet');
    case 'no_model': return L('这一家列不了型号，要写一个型号名', "This provider can't list its models; enter a model name");
    case 'not_found': return L('地址或型号不对', 'Wrong address or model');
    case 'rate': return L('被限流，稍后再试', 'Rate-limited; try again later');
    case 'server': return L('对方的服务出错', "The provider's service failed");
    case 'shape': return L('回的不像这种服务', "The reply doesn't look like this kind of service");
    default: return L('请求被拒绝', 'The request was refused');
  }
}

/** 服务器的 message 是「一句话（对方原话）」：拿出括号里的原话（密钥服务器已经抹成 ***）；没有括号 = 没有原话。 */
export function detailOf(message: string | undefined | null): string {
  const m = /^[^（(]*[（(]([\s\S]*)[）)]\s*$/.exec(String(message ?? '').trim());
  return m ? m[1].trim() : '';
}

/** 没通过：一句话、对方原话、两个按钮。 */
export function TestFail({ error, onChangeKey, onRetry, busy, changeLabel }: { error: ProbeError | null; onChangeKey?: () => void; onRetry: () => void; busy?: boolean; changeLabel?: string }) {
  const t = useTheme();
  const detail = detailOf(error?.message);
  return (
    <KitCard style={{ padding: space.lg, gap: space.sm }} accessibilityLiveRegion="polite">
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
        <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: t.bad }} />
        <T v="callout" color={t.ink} style={{ flex: 1, fontWeight: '600' }}>{L(`没通过：${failLine(error?.kind)}`, `Didn't pass: ${failLine(error?.kind)}`)}</T>
      </View>
      {detail ? <T v="caption" color={t.ink3} selectable style={[styles.mono, { fontWeight: '400', lineHeight: 17 }]}>{L(`对方原话：${detail}`, `Their reply: ${detail}`)}</T> : null}
      <View style={{ flexDirection: 'row', gap: space.sm, marginTop: space.xs }}>
        {onChangeKey ? <View style={{ flex: 1 }}><QuietButton label={changeLabel ?? L('换密钥', 'New key')} onPress={onChangeKey} /></View> : null}
        <View style={{ flex: 1 }}><QuietButton label={L('再测', 'Test again')} onPress={onRetry} busy={busy} /></View>
      </View>
    </KitCard>
  );
}

/** 通了的那一行：绿点 + 「已连通 · N 个型号能用」。 */
export function OkLine({ text }: { text: string }) {
  const t = useTheme();
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }} accessibilityLiveRegion="polite">
      <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: t.good }} />
      <T v="callout" style={{ fontWeight: '600', flex: 1 }}>{text}</T>
    </View>
  );
}

/** 读的时候、读不出来的时候（照设置页的样子：一行灰字 / 红字 + 重试）。 */
export function LoadState({ error, onRetry }: { error?: string | null; onRetry?: () => void }) {
  const t = useTheme();
  if (!error) {
    return (
      <View style={{ alignItems: 'center', paddingVertical: space.xl, gap: space.sm }}>
        <ActivityIndicator color={t.ink3} />
        <T v="callout" color={t.ink3}>{L('正在加载…', 'Loading…')}</T>
      </View>
    );
  }
  return (
    <KitCard style={{ padding: space.lg, gap: space.md, marginTop: space.lg }}>
      <T v="callout" color={t.bad}>{L(`无法加载：${error}`, `Couldn't load: ${error}`)}</T>
      {onRetry ? <QuietButton label={L('重试', 'Retry')} onPress={onRetry} /> : null}
    </KitCard>
  );
}

// —— 「设好了」：环合上、青点落到 1 点半 ——————————————————————————————————————————————————

const AnimatedCircle = Animated.createAnimatedComponent(Circle);

/**
 * OpenMousse 的标志：一个环和 1 点半位置上的一颗点。先把整圈描完（900ms，cubic-bezier(.65,0,.35,1)），描完时在 1 点半弹出 t.cyan 的点
 * （560ms，cubic-bezier(.34,1.4,.64,1)）。系统开了「减少动态效果」就直接画好。
 */
export function BrandRing({ size = 88 }: { size?: number }) {
  const t = useTheme();
  const still = useReduceMotion();
  const sw = Math.max(4, size * 0.072);
  const r = (size - sw) / 2 - size * 0.06;
  const c = 2 * Math.PI * r;
  const [draw] = useState(() => new Animated.Value(0));
  const [pop] = useState(() => new Animated.Value(0));
  useEffect(() => {
    if (still) { draw.setValue(1); pop.setValue(1); return undefined; }
    draw.setValue(0);
    pop.setValue(0);
    const a = Animated.sequence([
      Animated.timing(draw, { toValue: 1, duration: 900, easing: Easing.bezier(0.65, 0, 0.35, 1), useNativeDriver: false }),
      Animated.timing(pop, { toValue: 1, duration: 560, easing: Easing.bezier(0.34, 1.4, 0.64, 1), useNativeDriver: native }),
    ]);
    a.start();
    return () => a.stop();
  }, [still, draw, pop]);
  const offset = draw.interpolate({ inputRange: [0, 1], outputRange: [c, 0] });
  const dot = size * 0.2;
  // 1 点半：从 12 点顺时针 45°
  const cx = size / 2 + r * Math.sin(Math.PI / 4);
  const cy = size / 2 - r * Math.cos(Math.PI / 4);
  return (
    <View style={{ width: size, height: size }} accessible accessibilityRole="image" accessibilityLabel={L('设置完成', 'Setup complete')}>
      <Svg width={size} height={size}>
        <Circle cx={size / 2} cy={size / 2} r={r} stroke={t.line} strokeWidth={sw} fill="none" />
        <AnimatedCircle cx={size / 2} cy={size / 2} r={r} stroke={t.goldFill} strokeWidth={sw} fill="none" strokeLinecap="round"
          strokeDasharray={`${c} ${c}`} strokeDashoffset={offset} transform={`rotate(-90 ${size / 2} ${size / 2})`} />
      </Svg>
      <Animated.View style={{
        position: 'absolute', left: cx - dot / 2, top: cy - dot / 2, width: dot, height: dot, borderRadius: dot / 2, backgroundColor: t.cyan,
        borderWidth: Math.max(2, size * 0.03), borderColor: t.bg, opacity: pop.interpolate({ inputRange: [0, 0.15, 1], outputRange: [0, 1, 1] }),
        transform: [{ scale: pop }],
      }} />
    </View>
  );
}

const styles = StyleSheet.create({
  mono: monoFont,
  ringRow: { flexDirection: 'row', alignItems: 'center', gap: space.sm },
  back: { width: 42, height: 42, borderRadius: 21, borderWidth: StyleSheet.hairlineWidth, alignItems: 'center', justifyContent: 'center' },
  groupHead: { flexDirection: 'row', alignItems: 'flex-end', gap: space.md, marginTop: space.xl, marginBottom: space.sm + 2, paddingHorizontal: space.xs },
  row: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingHorizontal: space.lg, paddingVertical: 13, minHeight: 54 },
  sep: { position: 'absolute', top: 0, right: 0, height: 1 },
  tileRow: { flexDirection: 'row', gap: space.sm },
  tileWrap: { borderRadius: radius.md + 3, borderWidth: 1.5, padding: 1.5 },
  tile: { borderRadius: radius.md, borderWidth: 1, alignItems: 'center', justifyContent: 'center', gap: 6, paddingHorizontal: 4, paddingVertical: space.sm },
  lowTile: { height: 52, borderRadius: radius.md, borderWidth: 1, flexDirection: 'row', alignItems: 'center', paddingHorizontal: space.md },
  primary: { height: 48, borderRadius: radius.md, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: space.sm, paddingHorizontal: space.lg },
  quiet: { height: 44, borderRadius: radius.md, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6, paddingHorizontal: space.md },
  later: { height: 44, alignItems: 'center', justifyContent: 'center', marginTop: 2 },
  field: { borderRadius: radius.md, borderWidth: 1, paddingHorizontal: space.lg, paddingVertical: 13 },
  keyBox: { flexDirection: 'row', alignItems: 'center', borderRadius: radius.md, borderWidth: 1, paddingLeft: space.lg, paddingRight: space.sm },
  paste: { paddingHorizontal: space.md, paddingVertical: space.sm },
});
