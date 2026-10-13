// 会议（server/meetings.py，2026-10-13）用到的小零件：Agent 的圆头像、会议的组合头像、纪要里的待办行，
// 以及主对话里的两张卡：会议卡（发起它的那条回复下面，状态跟着走）和纪要卡（「会议已结束，以下 N 项待办需要你确认：」下面）。
import React, { useState } from 'react';
import { Alert, Pressable, StyleSheet, View } from 'react-native';
import { meetingsApi, type MeetingMember, type MeetingStatus, type MeetingTodo } from '../api/meetings';
import type { AgentColor, MeetingCardInfo, MinutesCardInfo } from '../data/types';
import { L } from '../i18n';
import { navigationRef } from '../navigation';
import { useStore } from '../store';
import { agentTint, radius, space, useTheme } from '../theme';
import { GROUP_ICONS, iconKey } from './GroupIcon';
import { Check, ChevronRight, LayoutGrid } from './icons';
import { T } from './ui';

/** Agent 的圆头像（会议里一律用圆的）：浅底 + 图标，颜色跟着 Agent。 */
export function AgentAvatar({ icon, color, size = 30 }: { icon?: string | null; color?: AgentColor | null; size?: number }) {
  const t = useTheme();
  const tint = agentTint(t, color);
  const C = GROUP_ICONS.find((x) => x.key === iconKey(icon))?.C ?? LayoutGrid;
  return (
    <View style={{ width: size, height: size, borderRadius: size / 2, backgroundColor: tint.soft, alignItems: 'center', justifyContent: 'center' }}>
      <C size={Math.round(size * 0.5)} color={tint.fg} strokeWidth={size < 24 ? 2.6 : 2.3} />
    </View>
  );
}

/** 会议的头像：一个圆里放最多四位成员的小头像（2 × 2）。 */
export function MeetingAvatar({ members, size = 36 }: { members: MeetingMember[]; size?: number }) {
  const t = useTheme();
  const mini = Math.round(size * 0.36);
  const pad = Math.round(size * 0.14);
  const step = size - pad * 2 - mini;
  return (
    <View style={{ width: size, height: size, borderRadius: size / 2, backgroundColor: t.surface2 }} accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
      {members.slice(0, 4).map((m, i) => (
        <View key={m.id} style={{ position: 'absolute', left: pad + (i % 2) * step, top: pad + Math.floor(i / 2) * step }}>
          <AgentAvatar icon={m.icon} color={m.color} size={mini} />
        </View>
      ))}
    </View>
  );
}

/** 会议状态的小标签：进行中 = 青，待确认 = 金，已结束 / 群聊 = 灰。 */
export function StatusPill({ status, label }: { status: MeetingStatus; label: string }) {
  const t = useTheme();
  const [bg, fg] = status === 'running' ? [t.cyanSoft, t.cyan] : status === 'pending' ? [t.goldSoft, t.gold] : [t.surface2, t.ink2];
  return (
    <View style={[styles.pill, { backgroundColor: bg }]}>
      <T v="caption" color={fg} style={{ fontWeight: '700' }}>{label}</T>
    </View>
  );
}

/** 纪要里的一条待办：负责的 Agent 的小头像 + 时间和事 + 谁负责。 */
export function TodoRow({ todo }: { todo: MeetingTodo }) {
  const t = useTheme();
  return (
    <View style={styles.todo} accessible accessibilityLabel={`${todo.text}${L('，', ', ')}${todo.agentName}`}>
      <AgentAvatar icon={todo.icon} color={todo.color} size={22} />
      <T v="callout" numberOfLines={1} style={{ flex: 1, minWidth: 0, fontSize: 14 }}>{todo.text}</T>
      <T v="caption" color={t.ink3}>{todo.agentName}</T>
    </View>
  );
}

/** 「确认」之后的一行回执：写了几项进日程和看板，没写进去的有几项。 */
export function confirmedLine(c: { todos: number; schedule: number; failed: unknown[] } | null | undefined): string {
  if (!c) return L('已确认', 'Confirmed');
  const failed = c.failed?.length ? L(`，${c.failed.length} 项未能写入`, `; ${c.failed.length} couldn't be added`) : '';
  return c.todos ? L(`已确认 · ${c.todos} 项待办写入日程和看板${failed}`, `Confirmed · ${c.todos} to-do${c.todos === 1 ? '' : 's'} added to the schedule and dashboards${failed}`)
    : L('已确认', 'Confirmed');
}

export const openMeeting = (id: string) => navigationRef.navigate('Meeting', { id, at: Date.now() });

/** 主对话里的会议卡：标题、成员、状态，点开进会议。 */
export function MeetingChip({ card }: { card: MeetingCardInfo }) {
  const t = useTheme();
  const names = card.members.map((m) => m.name).join(L('、', ', '));
  return (
    <Pressable onPress={() => openMeeting(card.meeting)} accessibilityRole="button" accessibilityLabel={L(`会议「${card.title}」，${card.statusLabel}`, `Meeting "${card.title}", ${card.statusLabel}`)}
      accessibilityHint={L('打开会议', 'Opens the meeting')} style={({ pressed }) => [styles.chip, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
      <MeetingAvatar members={card.members} size={40} />
      <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
        <T v="headline" numberOfLines={1} style={{ fontSize: 15 }}>{card.title}</T>
        <T v="caption" color={t.ink3} numberOfLines={1}>{L(`会议 · ${names}`, `Meeting · ${names}`)}</T>
      </View>
      <StatusPill status={card.status} label={card.statusLabel} />
    </Pressable>
  );
}

/** 主对话里的纪要卡：待办（没有待办就列决定）+ 确认 / 查看会议。确认过、被下一轮取代、没确认就结束的收成一行。 */
export function MinutesChip({ card }: { card: MinutesCardInfo }) {
  const t = useTheme();
  const { refreshThread, reload } = useStore();
  const [busy, setBusy] = useState(false);
  const confirm = async () => {
    setBusy(true);
    try {
      await meetingsApi.confirm(card.meeting);
      if (card.thread) await refreshThread(card.thread);  // 重读对话和卡片：这张卡变成回执
      reload('schedule', 'remember').catch(() => {});  // 待办进了日程：「今天」跟着变
    } catch (e) {
      Alert.alert(L('确认失败', "Couldn't confirm"), e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const head = card.round > 1 ? L(`纪要 · ${card.title} · 第 ${card.round} 轮`, `Minutes · ${card.title} · round ${card.round}`) : L(`纪要 · ${card.title}`, `Minutes · ${card.title}`);
  const state = card.status === 'confirmed' ? confirmedLine(card.confirmed) : card.status === 'superseded' ? L('已被下一轮的纪要取代', 'Replaced by the next round’s minutes')
    : card.status === 'ended' ? L('会议已结束，纪要未确认', 'The meeting ended without confirming these') : null;
  return (
    <View style={[styles.card, { backgroundColor: t.surface }]}>
      <T v="caption" color={t.ink2} style={{ fontWeight: '700' }}>{head}</T>
      {card.todos.length ? card.todos.map((td, i) => <TodoRow key={`${td.agent}-${i}`} todo={td} />)
        : card.decisions.slice(0, 3).map((d, i) => <T key={i} v="callout" color={t.ink2} style={{ fontSize: 14 }}>{`· ${d}`}</T>)}
      {state ? (
        <Pressable onPress={() => openMeeting(card.meeting)} accessibilityRole="button" style={({ pressed }) => [styles.receipt, { opacity: pressed ? 0.7 : 1 }]}>
          {card.status === 'confirmed' ? <Check size={14} color={t.good} /> : null}
          <T v="caption" color={card.status === 'confirmed' ? t.good : t.ink3} style={{ flex: 1 }}>{state}</T>
          <T v="caption" color={t.gold} style={{ fontWeight: '600' }}>{L('查看会议', 'Open')}</T>
          <ChevronRight size={14} color={t.gold} />
        </Pressable>
      ) : (
        <View style={{ flexDirection: 'row', gap: space.sm, marginTop: 2 }}>
          <Pressable onPress={confirm} disabled={busy} accessibilityRole="button" accessibilityLabel={L('确认纪要', 'Confirm the minutes')}
            accessibilityHint={L('待办写入日程和对应 Agent 的看板', "Adds the to-dos to the schedule and the Agents' dashboards")}
            style={({ pressed }) => [styles.btn, { backgroundColor: t.goldFill, opacity: busy ? 0.5 : pressed ? 0.8 : 1 }]}>
            <T v="caption" color={t.onGold} style={{ fontSize: 13, fontWeight: '700' }}>{busy ? L('正在写入…', 'Adding…') : L('确认', 'Confirm')}</T>
          </Pressable>
          <Pressable onPress={() => openMeeting(card.meeting)} accessibilityRole="button" style={({ pressed }) => [styles.btn, { backgroundColor: t.surface2, opacity: pressed ? 0.8 : 1 }]}>
            <T v="caption" style={{ fontSize: 13, fontWeight: '600' }}>{L('查看会议', 'Open meeting')}</T>
          </Pressable>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  pill: { borderRadius: 6, paddingHorizontal: 7, paddingVertical: 2, flexShrink: 0 },
  todo: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  chip: { flexDirection: 'row', alignItems: 'center', gap: space.md, borderRadius: radius.lg - 2, padding: space.md },
  card: { borderRadius: radius.lg - 2, padding: space.md, gap: 10 },
  btn: { height: 34, paddingHorizontal: 16, borderRadius: 17, alignItems: 'center', justifyContent: 'center' },
  receipt: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingTop: 2 },
});
