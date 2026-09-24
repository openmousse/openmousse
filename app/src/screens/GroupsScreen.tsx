import React from 'react';
import { Pressable, RefreshControl, ScrollView, StyleSheet, View } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { Plus } from '../components/icons';
import { GroupBadge } from '../components/GroupIcon';
import { modelOf } from '../components/ModelPicker';
import { LargeHeader, Pill, Screen, T } from '../components/ui';
import { useStore, type DataKey } from '../store';
import { radius, space, useTheme } from '../theme';

export function GroupsScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { groups, approvals, live, connected, booting, dataErrors, loading, reload } = useStore();
  // 还没在 Agent 里聊过的，副标题用看板上的真实数据或者职责。
  const subtitle = (id: string, lastLine: string, purpose: string) => {
    if (lastLine) return lastLine;
    if (id === 'fitness' && live?.week) return `本周 ${live.week.sessions} 次训练 · ${live.week.total_minutes} 分钟 · ${live.week.total_sets} 组`;
    if (id === 'diet' && live?.diet) return live.diet.item_count ? `今天 ${live.diet.totals.kcal} kcal · 蛋白质 ${live.diet.totals.protein} g` : '今天训记里还没有饮食记录';
    return purpose;
  };
  return (
    <Screen>
      <ScrollView contentContainerStyle={{ paddingBottom: space.xxl }} refreshControl={<RefreshControl refreshing={['groups', 'feed'].some((k) => loading[k as DataKey])} onRefresh={() => reload('groups', 'feed')} />}>
        <LargeHeader title="Agents" sub="每个 Agent 管一件事，记忆各自独立"
          right={
            <Pressable onPress={() => nav.navigate('NewGroup')} accessibilityRole="button" accessibilityLabel="新建 Agent"
              style={[styles.add, { backgroundColor: t.goldFill }]}>
              <Plus size={20} color={t.onGold} />
            </Pressable>
          } />
        <View style={{ paddingHorizontal: space.lg, gap: space.md }}>
          {!connected || dataErrors.groups ? (
            <T v="callout" color={dataErrors.groups ? t.bad : t.ink2}>{dataErrors.groups ? `读不到 Agents：${dataErrors.groups}` : booting ? '正在连服务器…' : '没连上服务器。'}</T>
          ) : null}
          {groups.map((g) => {
            const pending = approvals.filter((a) => a.groupId === g.id).length;
            return (
              <Pressable key={g.id} onPress={() => nav.navigate('Group', { id: g.id })} accessibilityRole="button"
                style={({ pressed }) => [styles.card, { backgroundColor: t.surface, opacity: pressed ? 0.75 : 1 }]}>
                <GroupBadge icon={g.icon} />
                <View style={{ flex: 1, gap: 4 }}>
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                    <T v="headline">{g.name}</T>
                    {g.dashboard === 'fitness' || g.dashboard === 'diet' ? <Pill label={live?.week?.source || live?.diet?.source || '数据源'} tone="good" /> : null}
                    {pending ? <Pill label={`${pending} 个待审批`} tone="gold" /> : null}
                  </View>
                  <T v="callout" color={t.ink2} numberOfLines={2}>{subtitle(g.id, g.lastLine, g.purpose)}</T>
                  <T v="caption" color={t.ink3}>{modelOf(g.modelId)?.short ?? g.modelId}</T>
                </View>
              </Pressable>
            );
          })}
          <Pressable onPress={() => nav.navigate('NewGroup')} accessibilityRole="button"
            style={[styles.empty, { borderColor: t.line }]}>
            <Plus size={18} color={t.ink2} />
            <T v="callout" color={t.ink2}>新建一个 Agent，比如「睡眠」「申请季」「记账」</T>
          </Pressable>
        </View>
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  add: { width: 38, height: 38, borderRadius: 19, alignItems: 'center', justifyContent: 'center', marginBottom: 4 },
  card: { flexDirection: 'row', gap: space.md, borderRadius: radius.lg, padding: space.lg, alignItems: 'flex-start' },
  empty: { flexDirection: 'row', gap: space.sm, alignItems: 'center', justifyContent: 'center', borderRadius: radius.lg, borderWidth: 1, borderStyle: 'dashed', padding: space.lg },
});
