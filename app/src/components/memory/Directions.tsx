// 记忆页第二张卡「方向」（2026-10-20，原来底栏的「目标」）：目标的数据和 Agent 改目标的命令（goals_ctl）都不动，只是换个地方放。
// 一个目标一行：名字、分类 · 什么时候、现在的读数；Agent 24 小时内改过的照旧带「撤销」（GoalChangesStrip，方案 A：Agent 直接改、能撤销）；
// 体重那行写「体重趋势见健身看板 ›」（折线在健身 Agent 的看板上；没有健身 Agent 的，折线照原来目标页那样画在这张卡下面）。
// 点一行照旧开 GoalSheet，「添加」照旧开 GoalEditor，做完和不做的照旧折在最后。
import React from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import { agentName } from '../../brand';
import type { Goal } from '../../data/types';
import { L } from '../../i18n';
import { openTarget } from '../../navigation';
import { useStore } from '../../store';
import { space, useTheme } from '../../theme';
import { CATEGORIES, categoryLabel, ClosedGoals, dueText, fmt, GoalChangesStrip, GoalEditor, GoalSheet, WeightCard } from '../Goals';
import { ChevronRight, Plus } from '../icons';
import { useSheet } from '../Sheet';
import { Card, T } from '../ui';

/** 现在的读数：76.2 kg / 18%；没有读数的不写。 */
function reading(g: Goal): string {
  if (g.current == null) return '';
  const u = (g.unit ?? '').trim();
  return !u || u === '%' ? `${fmt(g.current)}${u}` : `${fmt(g.current)} ${u}`;
}

/** 打开目标的弹层（行、「添加」都用）。 */
export function useGoalSheets() {
  const sheet = useSheet();
  const edit = (g?: Goal) => sheet.open({ title: g ? L('编辑目标', 'Edit goal') : L('添加目标', 'New goal'), content: (close) => <GoalEditor g={g} close={close} /> });
  const open = (g: Goal) => sheet.open({ title: g.title, content: (close) => <GoalSheet g={g} close={close} onEdit={() => edit(g)} /> });
  return { edit, open };
}

function GoalRow({ g, first, onPress }: { g: Goal; first: boolean; onPress: () => void }) {
  const t = useTheme();
  const { groups } = useStore();
  const fitness = groups.find((x) => x.dashboard === 'fitness');
  const now = reading(g);
  const when = g.due ? dueText(g.due) : '';
  return (
    <View style={!first ? { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line } : undefined}>
      <Pressable onPress={onPress} accessibilityRole="button" accessibilityHint={L('打开详情：编辑、标记完成、放弃', 'Opens details: edit, mark done, drop')}
        style={({ pressed }) => [styles.row, { opacity: pressed ? 0.6 : 1 }]}>
        <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
          <T v="body" numberOfLines={2} style={{ fontWeight: '600' }}>{g.title}</T>
          <T v="caption" color={t.ink3} numberOfLines={1} style={{ fontWeight: '400' }}>{[categoryLabel(g.category), when].filter(Boolean).join(' · ')}</T>
        </View>
        {now ? <T v="callout" color={g.stale ? t.warn : t.ink2} style={styles.tnum}>{now}</T> : null}
        <ChevronRight size={16} color={t.ink3} />
      </Pressable>
      {g.metric === 'weight' && fitness ? (
        <Pressable onPress={() => openTarget({ type: 'board', agent: fitness.id })} accessibilityRole="link" hitSlop={4}
          style={({ pressed }) => [styles.link, { opacity: pressed ? 0.6 : 1 }]}>
          <T v="caption" color={t.ink2} style={{ fontWeight: '500' }}>{L('体重趋势见健身看板 ›', 'Weight trend on the fitness board ›')}</T>
        </Pressable>
      ) : null}
    </View>
  );
}

export function DirectionsCard() {
  const t = useTheme();
  const { goals, goalsClosed, goalsEditable, dataErrors, connected, booting, groups, weightTrend } = useStore();
  const { edit, open } = useGoalSheets();
  // 照原来目标页的顺序：按分类（健康、学业、职业、财务），分类里照服务器给的顺序
  const rows = CATEGORIES.flatMap((c) => goals.filter((g) => g.category === c));
  // 体重折线没处放（没有健身 Agent 的看板）：画在这张卡下面，免得读数没地方看
  const weight = !groups.some((x) => x.dashboard === 'fitness') && weightTrend?.summary && weightTrend.series.length ? weightTrend : null;
  const card = (
    <Card style={{ gap: space.sm }}>
      <View style={styles.head}>
        <T v="headline" style={styles.title}>{L(`方向 · ${goals.length}`, `Direction · ${goals.length}`)}</T>
        {goalsEditable ? (
          <Pressable onPress={() => edit()} accessibilityRole="button" accessibilityLabel={L('添加目标', 'Add goal')} hitSlop={8}
            style={({ pressed }) => [styles.add, { opacity: pressed ? 0.6 : 1 }]}>
            <Plus size={16} color={t.gold} />
            <T v="callout" color={t.gold} style={{ fontWeight: '600' }}>{L('添加', 'Add')}</T>
          </Pressable>
        ) : null}
      </View>
      {!connected ? <T v="callout" color={t.ink2}>{booting ? L('正在连接服务器…', 'Connecting to the server…') : L('未连接服务器。', 'Not connected to the server.')}</T> : null}
      {dataErrors.goals ? <T v="callout" color={t.bad}>{L(`无法加载目标：${dataErrors.goals}`, `Couldn't load goals: ${dataErrors.goals}`)}</T> : null}
      <GoalChangesStrip />
      {connected && !dataErrors.goals && !goals.length ? (
        <T v="callout" color={t.ink2}>{goalsEditable ? L(`尚无目标。点「添加」，或在对话中告诉 ${agentName()}。`, `No goals yet. Tap Add, or tell ${agentName()} in chat.`)
          : L(`尚无目标。可在对话中告诉 ${agentName()}。`, `No goals yet. Tell ${agentName()} in chat.`)}</T>
      ) : null}
      {rows.length ? <View>{rows.map((g, i) => <GoalRow key={g.id} g={g} first={i === 0} onPress={() => open(g)} />)}</View> : null}
      {goalsClosed.length ? <ClosedGoals onOpen={open} flat /> : null}
    </Card>
  );
  return weight ? <View style={{ gap: space.md }}>{card}<WeightCard trend={weight} goal={goals.find((g) => g.metric === 'weight')} /></View> : card;
}

const styles = StyleSheet.create({
  head: { flexDirection: 'row', alignItems: 'center', gap: space.sm },
  title: { flex: 1, fontSize: 18, fontWeight: '700' },
  add: { flexDirection: 'row', alignItems: 'center', gap: 3 },
  row: { flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingVertical: 11 },
  link: { paddingBottom: 10, marginTop: -4 },
  tnum: { fontVariant: ['tabular-nums'] },
});
