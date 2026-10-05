// 目标页（server/goals.py）：按分类排的目标，点开看详情和操作；右上角「加一个目标」；Agent 刚改过的在最上面一条，能撤销；
// 健康那一节里有体重的读数折线（有体重目标就接在那个目标下面，带目标区间）；完成了的、不做了的折在最下面。
import { agentName } from '../brand';
import React from 'react';
import { ScrollView, View } from 'react-native';
import { AddGoalButton, CATEGORIES, categoryLabel, ClosedGoals, GoalCard, GoalChangesStrip, GoalEditor, GoalSheet, WeightCard } from '../components/Goals';
import { useSheet } from '../components/Sheet';
import { Card, LargeHeader, PullRefresh, Screen, SectionLabel, T } from '../components/ui';
import type { Goal } from '../data/types';
import { L } from '../i18n';
import { useStore } from '../store';
import { space, useTheme } from '../theme';

export function GoalsScreen() {
  const t = useTheme();
  const sheet = useSheet();
  const { goals, goalsClosed, goalsEditable, weightTrend, refreshGoals, dataErrors, connected, booting } = useStore();
  const edit = (g?: Goal) => sheet.open({ title: g ? L('编辑目标', 'Edit goal') : L('添加目标', 'New goal'), content: (close) => <GoalEditor g={g} close={close} /> });
  const open = (g: Goal) => sheet.open({ title: g.title, content: (close) => <GoalSheet g={g} close={close} onEdit={() => edit(g)} /> });
  // 体重：有体重目标就画在那个目标的卡里（带目标区间），没有就在健康那一节单独一张
  const weightGoal = goals.find((g) => g.metric === 'weight');
  const hasWeight = !!weightTrend?.summary && weightTrend.series.length > 0;
  const empty = connected && !dataErrors.goals && !goals.length;
  return (
    <Screen>
      <ScrollView contentContainerStyle={{ paddingBottom: space.xxl }} refreshControl={<PullRefresh onRefresh={refreshGoals} />}>
        <LargeHeader title={L('目标', 'Goals')}
          sub={goalsEditable ? L(`可在此编辑，或直接告诉 ${agentName()}`, `Edit them here, or tell ${agentName()}`) : undefined}
          right={goalsEditable ? <AddGoalButton onPress={() => edit()} /> : undefined} />
        <View style={{ paddingHorizontal: space.lg }}>
          {!connected ? <Card style={{ marginTop: space.md }}><T v="callout" color={t.ink2}>{booting ? L('正在连接服务器…', 'Connecting to the server…') : L('未连接服务器。', 'Not connected to the server.')}</T></Card> : null}
          {dataErrors.goals ? <Card style={{ marginTop: space.md }}><T v="callout" color={t.bad}>{L(`无法加载目标：${dataErrors.goals}`, `Couldn't load goals: ${dataErrors.goals}`)}</T></Card> : null}
          <GoalChangesStrip />
          {empty ? (
            <Card style={{ marginTop: space.md }}>
              <T v="callout" color={t.ink2}>{L(`尚无目标。点按右上角「添加目标」，或在对话中告诉 ${agentName()}。`, `No goals yet. Tap "Add goal" at the top, or tell ${agentName()} in chat.`)}</T>
            </Card>
          ) : null}
          {CATEGORIES.map((cat) => {
            const list = goals.filter((g) => g.category === cat);
            const weight = cat === '健康' && hasWeight && !weightGoal && weightTrend ? <WeightCard trend={weightTrend} /> : null;
            if (!list.length && !weight) return null;
            return (
              <View key={cat}>
                <SectionLabel>{categoryLabel(cat)}</SectionLabel>
                <View style={{ gap: space.md }}>
                  {list.map((g) => <GoalCard key={g.id} g={g} trend={g === weightGoal ? weightTrend : null} onPress={() => open(g)} />)}
                  {weight}
                </View>
              </View>
            );
          })}
          {goalsClosed.length ? <ClosedGoals onOpen={open} /> : null}
        </View>
      </ScrollView>
    </Screen>
  );
}
