import { agentName } from '../brand';
import React from 'react';
import { Pressable, RefreshControl, ScrollView, View } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { Ring } from '../components/charts';
import { Card, LargeHeader, Screen, SectionLabel, T } from '../components/ui';
import type { Goal, GoalCategory } from '../data/types';
import { useStore } from '../store';
import { space, useTheme } from '../theme';

const ORDER: GoalCategory[] = ['健康', '学业', '职业', '财务'];

/** 往下走的目标（体脂）：从起点降到目标上限算 100%。已经在目标区间里就是满的。 */
function progress(g: Goal): number | null {
  if (g.current == null || g.targetHigh == null) return null;
  if (g.current <= g.targetHigh) return 1;
  if (g.start == null || g.start <= g.current) return 0;
  return Math.max(0, Math.min(1, (g.start - g.current) / (g.start - g.targetHigh)));
}

function NumericGoal({ g, group }: { g: Goal; group?: string }) {
  const t = useTheme();
  const p = progress(g);
  const range = g.targetLow != null && g.targetHigh != null ? `${g.targetLow}–${g.targetHigh}${g.unit ?? ''}` : '';
  return (
    <Card style={{ gap: space.md }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.lg }}>
        <Ring size={64} stroke={6} value={p ?? 0} target={1} color={g.stale ? t.ink3 : undefined}>
          <T v="headline" style={{ fontVariant: ['tabular-nums'] }}>{g.current ?? '—'}</T>
        </Ring>
        <View style={{ flex: 1, gap: 3 }}>
          <T v="headline">{g.title}</T>
          <T v="callout" color={t.ink2} style={{ fontVariant: ['tabular-nums'] }}>
            {g.current != null ? `现在 ${g.current}${g.unit ?? ''}，目标 ${range}` : `目标 ${range}，还没有读数`}
          </T>
          <T v="caption" color={t.ink3}>{[g.currentSource && g.currentDate ? `${g.currentSource} · ${g.currentDate}` : null, g.due ? `截止 ${g.due}` : null, group].filter(Boolean).join(' · ')}</T>
        </View>
      </View>
      {g.stale ? (
        <T v="callout" color={t.warn}>最新读数已经是 {g.currentDate} 的了，算不了进度。量一次体脂（记进你的训练软件，或者用能写入 Apple 健康的体脂秤），这里会自动更新。</T>
      ) : null}
      {g.detail ? <T v="caption" color={t.ink3}>{g.detail}</T> : null}
    </Card>
  );
}

function TextGoal({ g }: { g: Goal }) {
  const t = useTheme();
  return (
    <Card style={{ gap: 4 }}>
      <T v="headline">{g.title}</T>
      {g.detail ? <T v="callout" color={t.ink2}>{g.detail}</T> : null}
      {g.due ? <T v="caption" color={t.ink3}>时间 {g.due}</T> : null}
    </Card>
  );
}

export function GoalsScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { goals, groups, reload, loading, dataErrors, connected, booting } = useStore();
  return (
    <Screen>
      <ScrollView contentContainerStyle={{ paddingBottom: space.xxl }}
        refreshControl={<RefreshControl refreshing={!!loading.goals} onRefresh={() => reload('goals')} />}>
        <LargeHeader title="目标" sub="取自你的档案「目标（当前）」。能用数字追踪的，自动读你接的数据源和 Apple 健康的最新数据" />
        <View style={{ paddingHorizontal: space.lg }}>
          {!connected ? <Card style={{ marginTop: space.md }}><T v="callout" color={t.ink2}>{booting ? '正在连服务器…' : '没连上服务器。'}</T></Card> : null}
          {dataErrors.goals ? <Card style={{ marginTop: space.md }}><T v="callout" color={t.bad}>读不到目标：{dataErrors.goals}</T></Card> : null}
          {ORDER.map((cat) => {
            const list = goals.filter((g) => g.category === cat);
            if (!list.length) return null;
            return (
              <View key={cat}>
                <SectionLabel>{cat}</SectionLabel>
                <View style={{ gap: space.md }}>
                  {list.map((g) => {
                    const group = groups.find((x) => x.id === g.groupId);
                    return (
                      <Pressable key={g.id} disabled={!group} onPress={() => group && nav.navigate('Group', { id: group.id, tab: 'board' })} accessibilityRole={group ? 'button' : undefined}>
                        {g.targetHigh != null ? <NumericGoal g={g} group={group?.name} /> : <TextGoal g={g} />}
                      </Pressable>
                    );
                  })}
                </View>
              </View>
            );
          })}
          {goals[0]?.source ? <T v="caption" color={t.ink3} style={{ marginTop: space.lg, paddingHorizontal: space.xs }}>来源：{goals[0].source}。目标存在服务器的数据库里；{agentName()} 还没有改这张表的工具。</T> : null}
        </View>
      </ScrollView>
    </Screen>
  );
}
