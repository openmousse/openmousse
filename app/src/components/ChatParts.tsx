// 对话页（2026-10-13）的几样零件：头像外圈的状态（在做 = 青色弧，等你点头 = 琥珀点，未读数）、会议 / 群的拼图头像、
// 统一的聊天页头（返回 / 头像 / 名字 + 副标题 / ⓘ）、左滑露出的按钮、删除前的确认。
// 好友列表和 Grava 一边的列表共用 SwipeRow，四种聊天页（主对话、项目、Agent、好友）共用 ChatHeader。
import React, { useEffect, useRef, useState } from 'react';
import { Animated, Easing, Platform, Pressable, ScrollView, StyleSheet, View } from 'react-native';
import Svg, { Circle } from 'react-native-svg';
import { AudioLinesBars, BellOff, ChevronLeft, ChevronRight, FolderKanban, Info, Pin } from './icons';
import { GroupBadge, GROUP_ICONS, iconKey } from './GroupIcon';
import { LensAvatar } from './LensAvatar';
import { Btn, T } from './ui';
import type { ChatState } from '../api/chats';
import type { AgentColor, AvatarConfig } from '../data/types';
import { L } from '../i18n';
import { agentTint, space, useTheme } from '../theme';

/** 填满的删除按钮（左滑）：两套主题都用浅色主题的 bad，白字对比度 5.6:1 */
const DELETE_FILL = '#B83A30';

/** 列表右上角的时间：今天写钟点，昨天写「昨天」，今年写月 / 日，更早带年份。 */
export function rowTime(ts: string | null | undefined): string {
  if (!ts) return '';
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return '';
  const now = new Date();
  const ymd = (x: Date) => `${x.getFullYear()}-${x.getMonth()}-${x.getDate()}`;
  if (ymd(d) === ymd(now)) return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  if (ymd(d) === ymd(new Date(now.getTime() - 86400000))) return L('昨天', 'Yesterday');
  if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}/${d.getDate()}`;
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`;
}

/** 在做：头像外一圈青色弧，慢慢转（网页上不用原生驱动）。 */
function WorkingArc({ box }: { box: number }) {
  const t = useTheme();
  const [spin] = useState(() => new Animated.Value(0));
  useEffect(() => {
    const loop = Animated.loop(Animated.timing(spin, { toValue: 1, duration: 2400, easing: Easing.linear, useNativeDriver: Platform.OS !== 'web' }));
    loop.start();
    return () => loop.stop();
  }, [spin]);
  const r = box / 2 - 1.5;
  const c = 2 * Math.PI * r;
  const rotate = spin.interpolate({ inputRange: [0, 1], outputRange: ['0deg', '360deg'] });
  return (
    <Animated.View pointerEvents="none" style={[StyleSheet.absoluteFill, { transform: [{ rotate }] }]}>
      <Svg width={box} height={box}>
        <Circle cx={box / 2} cy={box / 2} r={r} fill="none" stroke={t.cyan} strokeWidth={2} strokeLinecap="round" strokeDasharray={`${c * 0.74} ${c * 0.26}`} />
      </Svg>
    </Animated.View>
  );
}

/**
 * 头像 + 状态：外圈留 4 的空（size + 8 见方），在做 = 转动的青色弧，等你点头 = 右上角琥珀点；
 * unread > 0 且不是等你点头时，右上角是未读数（免打扰 = 灰色）。
 */
export function StateFace({ size = 46, state, unread = 0, muted, children, label }: {
  size?: number; state?: ChatState; unread?: number; muted?: boolean; children: React.ReactNode; label?: string;
}) {
  const t = useTheme();
  const box = size + 8;
  const dot = Math.max(9, Math.round(size * 0.24));
  const what = state === 'waiting' ? L('，等你确认', ', waiting for you') : state === 'working' ? L('，正在处理', ', working') : '';
  return (
    <View style={{ width: box, height: box }} accessible={!!label} accessibilityLabel={label ? `${label}${what}${unread ? L(`，${unread} 条未读`, `, ${unread} unread`) : ''}` : undefined}>
      {state === 'working' ? <WorkingArc box={box} /> : null}
      <View style={{ position: 'absolute', left: 4, top: 4 }}>{children}</View>
      {state === 'waiting' ? (
        <View style={{ position: 'absolute', right: 2, top: 3, width: dot + 4, height: dot + 4, borderRadius: (dot + 4) / 2, backgroundColor: t.warn, borderWidth: 2, borderColor: t.bg }} />
      ) : unread > 0 ? (
        <View style={{ position: 'absolute', right: -2, top: 0, minWidth: 18, height: 18, borderRadius: 9, paddingHorizontal: 4, backgroundColor: muted ? t.ink3 : t.cyan,
          borderWidth: 2, borderColor: t.bg, alignItems: 'center', justifyContent: 'center' }}>
          <T v="caption" color={t.surface} style={{ fontSize: 10, fontWeight: '700', lineHeight: 12 }}>{unread > 99 ? '99+' : String(unread)}</T>
        </View>
      ) : null}
    </View>
  );
}

/** 一个 Agent 的圆形头像（Agent 栏、聊天页头、资料页）。 */
export function AgentFace({ icon, color, size = 46 }: { icon?: string | null; color?: string | null; size?: number }) {
  return <GroupBadge icon={icon} color={color as AgentColor | null} size={size} round />;
}

/** 项目的头像：青色浅底上一个看板图标。 */
export function ProjectFace({ size = 46, archived }: { size?: number; archived?: boolean }) {
  const t = useTheme();
  return (
    <View style={{ width: size, height: size, borderRadius: size / 2, backgroundColor: archived ? t.surface2 : t.cyanSoft, alignItems: 'center', justifyContent: 'center' }}>
      <FolderKanban size={Math.round(size * 0.46)} color={archived ? t.ink2 : t.cyan} />
    </View>
  );
}

export type Member = { id: string; icon?: string | null; color?: string | null };

/** 会议 / Agent 群：灰底圆里拼两三个成员的小头像（主对话是金色透镜）。 */
export function RoomFace({ members, size = 46, avatar }: { members: Member[]; size?: number; avatar: AvatarConfig }) {
  const t = useTheme();
  const k = size / 46;
  const mini = Math.round(18 * k);
  const pos = members.length >= 3 ? [[14, 5], [6, 22], [22, 22]] : members.length === 2 ? [[6, 14], [22, 14]] : [[14, 14]];
  return (
    <View style={{ width: size, height: size, borderRadius: size / 2, backgroundColor: t.surface2 }}>
      {members.slice(0, 3).map((m, i) => {
        const tint = agentTint(t, m.color);
        const C = GROUP_ICONS.find((x) => x.key === iconKey(m.icon))?.C;
        return (
          <View key={`${m.id}-${i}`} style={{ position: 'absolute', left: pos[i][0] * k, top: pos[i][1] * k, width: mini, height: mini, borderRadius: mini / 2,
            backgroundColor: m.id === 'main' ? t.lensField : tint.soft, alignItems: 'center', justifyContent: 'center', overflow: 'hidden' }}>
            {m.id === 'main' ? <LensAvatar size={mini} config={avatar} /> : C ? <C size={Math.round(10 * k)} color={tint.fg} strokeWidth={2.8} /> : null}
          </View>
        );
      })}
    </View>
  );
}

/** 统一的聊天页头：返回 / 头像 / 名字 + 副标题 /（对讲）/ ⓘ（资料页）。副标题可以是一段可点的提示（未连接）。
 *  onTalk：给了就在 ⓘ 前面多一个对讲按钮（第 8 批，只有主对话给，见 src/talk/TalkScreen.tsx）。 */
export function ChatHeader({ title, sub, subTone, onSub, face, onBack, onInfo, infoLabel, onTalk }: {
  title: string; sub?: string; subTone?: 'warn' | 'good'; onSub?: () => void; face: React.ReactNode; onBack: () => void; onInfo?: () => void; infoLabel?: string; onTalk?: () => void;
}) {
  const t = useTheme();
  const subColor = subTone === 'warn' ? t.warn : subTone === 'good' ? t.good : t.ink3;
  const subEl = sub ? <T v="caption" color={subColor} numberOfLines={1} style={{ fontWeight: '400' }}>{sub}</T> : null;
  return (
    <View style={[styles.head, { borderBottomColor: t.line }]}>
      <Pressable onPress={onBack} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('返回', 'Back')} style={styles.headBtn}>
        <ChevronLeft size={26} color={t.ink} />
      </Pressable>
      <Pressable onPress={onInfo} disabled={!onInfo} accessibilityRole={onInfo ? 'button' : undefined} accessibilityLabel={onInfo ? `${title}${L('，资料', ', details')}` : title}
        style={({ pressed }) => [styles.headMid, { opacity: pressed ? 0.7 : 1 }]}>
        {face}
        <View style={{ flex: 1, minWidth: 0, gap: 1 }}>
          <T v="headline" numberOfLines={1}>{title}</T>
          {onSub && subEl ? <Pressable onPress={onSub} hitSlop={6} accessibilityRole="button" style={{ alignSelf: 'flex-start' }}>{subEl}</Pressable> : subEl}
        </View>
      </Pressable>
      {onTalk ? (
        <Pressable onPress={onTalk} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('对讲', 'Talk')} style={styles.headBtn}>
          <AudioLinesBars size={22} color={t.ink2} />
        </Pressable>
      ) : null}
      {onInfo ? (
        <Pressable onPress={onInfo} hitSlop={8} accessibilityRole="button" accessibilityLabel={infoLabel ?? L('资料与设置', 'Details and settings')} style={styles.headBtn}>
          <Info size={22} color={t.ink2} />
        </Pressable>
      ) : <View style={styles.headBtn} />}
    </View>
  );
}

/**
 * 列表里的一行（Grava 一边和好友一边一样）：72 高，左边 54 见方的头像位，右边两行：
 * 名字 + 标签 …… 置顶钉 + 时间；最后一句（有未读时深色）…… 免打扰铃 + 未读数（免打扰灰色）。分隔线从文字开始。
 */
export function ChatRowView({ face, title, tag, tagTone = 'cyan', pinned, muted, time, line, unread = 0, onPress, onLongPress, hint, last }: {
  face: React.ReactNode; title: string; tag?: string; tagTone?: 'cyan' | 'warn' | 'neutral'; pinned?: boolean; muted?: boolean; time?: string; line: string;
  unread?: number; onPress: () => void; onLongPress?: () => void; hint?: string; last?: boolean;
}) {
  const t = useTheme();
  const tags = { cyan: [t.cyanSoft, t.cyan], warn: [t.warnSoft, t.warn], neutral: [t.surface2, t.ink2] } as const;
  const [tagBg, tagFg] = tags[tagTone];
  return (
    <Pressable onPress={onPress} onLongPress={onLongPress} delayLongPress={400} accessibilityRole="button" accessibilityHint={hint}
      style={({ pressed }) => [styles.row, { backgroundColor: pressed ? t.surface : t.bg }]}>
      <View style={styles.rowFace}>{face}</View>
      <View style={[styles.rowText, { borderBottomColor: last ? 'transparent' : t.line }]}>
        <View style={styles.rowLine}>
          <T v="headline" numberOfLines={1} style={{ flexShrink: 1 }}>{title}</T>
          {tag ? <View style={[styles.tag, { backgroundColor: tagBg }]}><T v="caption" color={tagFg} style={{ fontSize: 11, fontWeight: '600' }}>{tag}</T></View> : null}
          <View style={{ flex: 1 }} />
          {pinned ? <Pin size={12} color={t.ink3} /> : null}
          {time ? <T v="caption" color={t.ink3} style={{ fontSize: 13, fontWeight: '400' }}>{time}</T> : null}
        </View>
        <View style={styles.rowLine}>
          <T v="callout" color={unread && !muted ? t.ink : t.ink2} numberOfLines={1} style={{ flex: 1 }}>{line}</T>
          {muted ? <BellOff size={14} color={t.ink3} /> : null}
          {unread ? (
            <View accessible accessibilityLabel={L(`${unread} 条未读`, `${unread} unread`)} style={[styles.count, { backgroundColor: muted ? t.ink3 : t.cyan }]}>
              <T v="caption" color={t.surface} style={{ fontWeight: '700' }}>{unread > 99 ? '99+' : String(unread)}</T>
            </View>
          ) : null}
        </View>
      </View>
    </Pressable>
  );
}

export type SwipeAction ={ key: string; label: string; icon: React.ReactNode; tone: 'plain' | 'gold' | 'danger'; onPress: () => void };

/** 左滑露出的按钮（横向 ScrollView：二进制里没有 gesture-handler）。宽度没量出来之前就是普通的一行。 */
export function SwipeRow({ children, actions }: { children: React.ReactNode; actions: SwipeAction[] }) {
  const t = useTheme();
  const [w, setW] = useState(0);
  const ref = useRef<ScrollView>(null);
  const each = 72;
  const total = each * actions.length;
  if (!actions.length) return <>{children}</>;
  const colors = { plain: [t.surface2, t.ink], gold: [t.goldFill, t.onGold], danger: [DELETE_FILL, '#FFFFFF'] } as const;
  return (
    <View onLayout={(e) => setW(Math.round(e.nativeEvent.layout.width))}>
      {w ? (
        <ScrollView ref={ref} horizontal showsHorizontalScrollIndicator={false} bounces={false} decelerationRate="fast" snapToOffsets={[0, total]}
          disableIntervalMomentum directionalLockEnabled>
          <View style={{ width: w }}>{children}</View>
          {actions.map((a) => {
            const [bg, fg] = colors[a.tone];
            return (
              <Pressable key={a.key} onPress={() => { ref.current?.scrollTo({ x: 0, animated: true }); a.onPress(); }} accessibilityRole="button" accessibilityLabel={a.label}
                style={({ pressed }) => [styles.swipeBtn, { width: each, backgroundColor: bg, opacity: pressed ? 0.8 : 1 }]}>
                {a.icon}
                <T v="caption" color={fg} style={{ fontWeight: '600' }}>{a.label}</T>
              </Pressable>
            );
          })}
        </ScrollView>
      ) : children}
    </View>
  );
}

/** 设计稿里的分段（对话页 Grava | 好友、资料页 看板 | 记忆 | 设置、Zen 想法 | 播客）：深色底上选中的一格浅一点；count 是青色的未读数，
 * dot 是琥珀点（那一边有等你处理的）。 */
export function PillTabs<V extends string>({ options, value, onChange, style }: {
  options: { value: V; label: string; count?: number; dot?: boolean; dotLabel?: string }[]; value: V; onChange: (v: V) => void; style?: object;
}) {
  const t = useTheme();
  const dark = t.mode === 'dark';
  return (
    <View style={[styles.tabs, { backgroundColor: dark ? t.surface : t.surface2 }, style]} accessibilityRole="tablist">
      {options.map((o) => {
        const on = o.value === value;
        const n = o.count ?? 0;
        return (
          <Pressable key={o.value} onPress={() => onChange(o.value)} accessibilityRole="tab" accessibilityState={{ selected: on }}
            accessibilityLabel={[o.label, n ? L(`${n} 条未读`, `${n} unread`) : '', o.dot ? o.dotLabel ?? L('有等你处理的', 'something is waiting for you') : ''].filter(Boolean).join(L('，', ', '))}
            style={[styles.tab, on && { backgroundColor: dark ? t.line : t.surface }]}>
            <T v="callout" color={on ? t.ink : t.ink2} style={{ fontWeight: '600' }} numberOfLines={1}>{o.label}</T>
            {n ? <T v="caption" color={t.cyan} style={{ fontWeight: '700' }}>{n > 99 ? '99+' : String(n)}</T> : null}
            {o.dot ? <View style={{ width: 7, height: 7, borderRadius: 4, backgroundColor: t.warn }} /> : null}
          </Pressable>
        );
      })}
    </View>
  );
}

/** 弹层里的一项（「新建」、长按 Agent）：圆底图标 + 名称 + 一行说明；danger = 红色；off = 灰掉、点不了（说明里写原因）。 */
export function MenuItem({ icon: Icon, title, desc, danger, off, chevron, onPress }: {
  icon: typeof Info; title: string; desc?: string; danger?: boolean; off?: boolean; chevron?: boolean; onPress?: () => void;
}) {
  const t = useTheme();
  const fg = danger ? t.bad : t.ink;
  return (
    <Pressable onPress={onPress} disabled={off || !onPress} accessibilityRole="button" accessibilityState={{ disabled: !!off }} accessibilityLabel={desc ? `${title}，${desc}` : title}
      style={({ pressed }) => [styles.menuItem, { opacity: off ? 0.45 : pressed ? 0.7 : 1 }]}>
      <View style={[styles.menuIcon, { backgroundColor: danger ? t.badSoft : t.surface2 }]}><Icon size={20} color={fg} /></View>
      <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
        <T v="headline" color={fg}>{title}</T>
        {desc ? <T v="callout" color={t.ink3}>{desc}</T> : null}
      </View>
      {chevron && !off ? <ChevronRight size={16} color={t.ink3} /> : null}
    </Pressable>
  );
}

/** 删除前的确认（放在 Sheet 里）：写明后果，红色按钮写清删的是什么，下面是取消。 */
export function ConfirmDelete({ body, label, close, run }: { body: string; label: string; close: () => void; run: () => Promise<unknown> }) {
  const t = useTheme();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const go = () => {
    if (busy) return;
    setBusy(true);
    setErr('');
    run().then(() => close()).catch((e) => { setErr(e instanceof Error ? e.message : String(e)); setBusy(false); });
  };
  return (
    <View style={{ gap: space.sm }}>
      <T v="callout" color={t.ink2} style={{ marginBottom: space.sm }}>{body}</T>
      {err ? <T v="callout" color={t.bad}>{L(`删除失败：${err}`, `Couldn't delete: ${err}`)}</T> : null}
      <Btn kind="danger" label={busy ? L('正在删除…', 'Deleting…') : label} onPress={go} />
      <Btn kind="quiet" label={L('取消', 'Cancel')} onPress={close} />
    </View>
  );
}

const styles = StyleSheet.create({
  head: { flexDirection: 'row', alignItems: 'center', gap: 4, paddingLeft: 4, paddingRight: 8, paddingVertical: 8, borderBottomWidth: StyleSheet.hairlineWidth },
  headBtn: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center' },
  headMid: { flex: 1, minWidth: 0, flexDirection: 'row', alignItems: 'center', gap: 10 },
  swipeBtn: { alignItems: 'center', justifyContent: 'center', gap: 5 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingLeft: 12, minHeight: 72 },
  tabs: { flexDirection: 'row', padding: 3, borderRadius: 12, gap: 3 },
  tab: { flex: 1, height: 34, borderRadius: 9, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6, paddingHorizontal: 6 },
  menuItem: { flexDirection: 'row', alignItems: 'center', gap: 14, paddingVertical: 10 },
  menuIcon: { width: 42, height: 42, borderRadius: 21, alignItems: 'center', justifyContent: 'center' },
  rowFace: { width: 54, height: 54 },
  rowText: { flex: 1, minWidth: 0, minHeight: 72, paddingRight: 16, paddingVertical: 10, justifyContent: 'center', gap: 4, borderBottomWidth: StyleSheet.hairlineWidth },
  rowLine: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  tag: { borderRadius: 6, paddingHorizontal: 6, paddingVertical: 1 },
  count: { minWidth: 20, height: 20, borderRadius: 10, paddingHorizontal: 6, alignItems: 'center', justifyContent: 'center', marginLeft: 2 },
});
