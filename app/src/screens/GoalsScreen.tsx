import { agentName } from '../brand';
import React from 'react';
import { Pressable, RefreshControl, ScrollView, View } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { Ring } from '../components/charts';
import { Card, LargeHeader, Screen, SectionLabel, T } from '../components/ui';
import type { Goal, GoalCategory } from '../data/types';
import { L } from '../i18n';
import { useStore } from '../store';
import { space, useTheme } from '../theme';

const ORDER: GoalCategory[] = ['健康', '学业', '职业', '财务'];
// 分类是服务器数据库里的值，拿来比较，不翻译；只换显示的文字。
const categoryLabel = (c: GoalCategory) =>
  ({ 健康: L('健康', 'Health'), 学业: L('学业', 'Study'), 职业: L('职业', 'Career'), 财务: L('财务', 'Finance') })[c] ?? c;

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
            {g.current != null
              ? L(`现在 ${g.current}${g.unit ?? ''}，目标 ${range}`, `Now ${g.current}${g.unit ?? ''}, target ${range}`)
              : L(`目标 ${range}，还没有读数`, `Target ${range}, no reading yet`)}
          </T>
          <T v="caption" color={t.ink3}>{[g.currentSource && g.currentDate ? `${g.currentSource} · ${g.currentDate}` : null, g.due ? L(`截止 ${g.due}`, `Due ${g.due}`) : null, group].filter(Boolean).join(' · ')}</T>
        </View>
      </View>
      {g.stale ? (
        <T v="callout" color={t.warn}>{L(
          `最新读数已经是 ${g.currentDate} 的了，算不了进度。量一次体脂（记进你的训练软件，或者用能写入 Apple 健康的体脂秤），这里会自动更新。`,
          `The latest reading is from ${g.currentDate}, too old to track progress. Measure your body fat once (log it in your workout app, or use a scale that writes to Apple Health) and this updates automatically.`,
        )}</T>
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
      {g.due ? <T v="caption" color={t.ink3}>{L(`时间 ${g.due}`, `When: ${g.due}`)}</T> : null}
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
        <LargeHeader title={L('目标', 'Goals')} sub={L(
          '取自你的档案「目标（当前）」。能用数字追踪的，自动读你接的数据源和 Apple 健康的最新数据',
          'From "Goals (current)" in your profile. Goals with numbers pull the latest from your data sources and Apple Health',
        )} />
        <View style={{ paddingHorizontal: space.lg }}>
          {!connected ? <Card style={{ marginTop: space.md }}><T v="callout" color={t.ink2}>{booting ? L('正在连服务器…', 'Connecting to the server…') : L('没连上服务器。', 'Not connected to the server.')}</T></Card> : null}
          {dataErrors.goals ? <Card style={{ marginTop: space.md }}><T v="callout" color={t.bad}>{L(`读不到目标：${dataErrors.goals}`, `Couldn't load goals: ${dataErrors.goals}`)}</T></Card> : null}
          {ORDER.map((cat) => {
            const list = goals.filter((g) => g.category === cat);
            if (!list.length) return null;
            return (
              <View key={cat}>
                <SectionLabel>{categoryLabel(cat)}</SectionLabel>
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
          {goals[0]?.source ? <T v="caption" color={t.ink3} style={{ marginTop: space.lg, paddingHorizontal: space.xs }}>{L(
            `来源：${goals[0].source}。目标存在服务器的数据库里；${agentName()} 还没有改这张表的工具。`,
            `Source: ${goals[0].source}. Goals live in the server's database; ${agentName()} has no tool to edit them yet.`,
          )}</T> : null}
        </View>
      </ScrollView>
    </Screen>
  );
}
