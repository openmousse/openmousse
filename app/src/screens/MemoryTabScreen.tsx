// 记忆（底栏第四格，2026-10-20；主人 10/16 选的「第四格换什么」方案 A，设计第 20 张第三版）：原来的「目标」换成这里。
// 三张卡：记忆（共几条、最近 7 天新增、年轮、待确认）→ 方向（原来的目标）→ 全部记忆（按枝 / 档案、朋友画像、日志、来源）。
// 新用户（一片叶子都没有、也没接任何平台）第一屏是「接到你的 AI」（TreeConnect.tsx 的 ConnectSection），有叶子的放在页尾。
// 两种排法主人还没选：排法一 = 三张卡一路往下（默认，和方案 A 原稿一样）；排法二 = 第一屏只放第一张，方向和全部记忆收成两行入口，
// 点进去再看（MemorySection 页）。改 LAYOUT 一个值就换。
// 角标（底栏）= 待确认的条数（你说的待确认 + 别人说的待定），store 启动那一批读完再读世界树和待定（不拖慢启动）。
// 顶上的搜索（设计第 21 张）在世界树的叶子里本地找（components/memory/MemorySearch.tsx），框里有字时三张卡换成结果。
// 周日出的每周修剪卡（components/PruneCard.tsx，设计第 22 张）放在卡一上面，逐条定。
import { useFocusEffect, useNavigation, useRoute } from '@react-navigation/native';
import React, { useCallback, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, View } from 'react-native';
import { loadConnect } from '../api/tree';
import { ChevronRight } from '../components/icons';
import { AllMemoryCard } from '../components/memory/AllMemory';
import { DirectionsCard } from '../components/memory/Directions';
import { SearchBox, SearchResults } from '../components/memory/MemorySearch';
import { RecentCard } from '../components/memory/RecentCard';
import { PruneCard } from '../components/PruneCard';
import { ConnectSection } from '../components/TreeConnect';
import { Card, LargeHeader, NavHeader, PullRefresh, Screen, T } from '../components/ui';
import type { TreeConnect } from '../data/types';
import { L } from '../i18n';
import { useStore } from '../store';
import { space, useTheme } from '../theme';

/** 排法：stack = 排法一（三张卡一路往下）；entries = 排法二（第一屏只有「记忆」，另两张收成入口）。等主人定。 */
export const LAYOUT: 'stack' | 'entries' = 'stack';

/** 「接到你的 AI」：只有这一页和世界树页用，不进 store；每次进来、下拉、加删平台后重读。 */
function useConnect() {
  const { connected } = useStore();
  const [info, setInfo] = useState<TreeConnect | null>(null);
  const load = useCallback(() => (connected ? loadConnect().then(setInfo).catch(() => setInfo(null)) : Promise.resolve()), [connected]);
  return { info, load };
}

function EntryRow({ title, value, onPress }: { title: string; value?: string; onPress: () => void }) {
  const t = useTheme();
  return (
    <Pressable onPress={onPress} accessibilityRole="button" style={({ pressed }) => ({ opacity: pressed ? 0.7 : 1 })}>
      <Card style={styles.entry}>
        <T v="headline" style={{ flex: 1, fontSize: 17 }}>{title}</T>
        {value ? <T v="callout" color={t.ink2} style={{ fontVariant: ['tabular-nums'] }}>{value}</T> : null}
        <ChevronRight size={18} color={t.ink3} />
      </Card>
    </Pressable>
  );
}

export function MemoryTabScreen() {
  const nav = useNavigation<any>();
  const { tree, goals, connected, reload, refreshGoals } = useStore();
  const { info, load } = useConnect();
  const [q, setQ] = useState('');
  // 每次回到这一格按最新的读：世界树、待定、收件箱（修剪卡；角标也跟着变）
  useFocusEffect(useCallback(() => {
    if (!connected) return;
    reload('tree', 'held', 'inbox').catch(() => {});
    load();
  }, [connected, reload, load]));
  const data = tree?.kind === 'ok' ? tree.data : null;
  const fresh = !!data && !data.leaves.length && !(info?.platforms ?? []).some((p) => p.lastWrote);  // 新用户：一片叶子都没有、也没有平台写过
  const connect = info ? <ConnectSection info={info} onChanged={load} /> : null;
  const searching = !!data && !!q.trim();
  return (
    <Screen>
      <ScrollView contentContainerStyle={{ paddingBottom: space.xxl }} keyboardShouldPersistTaps="handled"
        refreshControl={<PullRefresh onRefresh={() => Promise.all([reload('tree', 'held', 'inbox', 'profile', 'journal', 'memories'), refreshGoals(), load()])} />}>
        <LargeHeader title={L('记忆', 'Memory')} />
        <View style={{ paddingHorizontal: space.lg, gap: space.md }}>
          {/* 搜索：世界树有叶子才出；框里有字时下面换成结果 */}
          {data?.leaves.length ? <SearchBox value={q} onChange={setQ} /> : null}
          {searching ? <SearchResults leaves={data.leaves} q={q} /> : null}
          {!searching && fresh ? connect : null}
          {/* 每周修剪（周日出，等你逐条定）：卡一上面 */}
          {!searching ? <PruneCard /> : null}
          {!searching ? <RecentCard /> : null}
          {searching ? null : LAYOUT === 'stack' ? (
            <>
              <DirectionsCard />
              <AllMemoryCard />
            </>
          ) : (
            <>
              <EntryRow title={L('方向', 'Direction')} value={String(goals.length)} onPress={() => nav.navigate('MemorySection', { section: 'goals' })} />
              <EntryRow title={L('全部记忆', 'All memories')} value={data ? L(`${data.counts.total} 条`, `${data.counts.total}`) : undefined}
                onPress={() => nav.navigate('MemorySection', { section: 'all' })} />
            </>
          )}
          {!searching && !fresh ? connect : null}
        </View>
      </ScrollView>
    </Screen>
  );
}

/** 排法二点进去的那一页：方向，或者全部记忆（同一张卡，单独一页）。 */
export function MemorySectionScreen() {
  const nav = useNavigation<any>();
  const { section } = (useRoute<any>().params ?? {}) as { section?: 'goals' | 'all' };
  const { reload, refreshGoals } = useStore();
  const goals = section === 'goals';
  return (
    <Screen>
      <NavHeader title={goals ? L('方向', 'Direction') : L('全部记忆', 'All memories')} onBack={() => nav.goBack()} />
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl }}
        refreshControl={<PullRefresh onRefresh={() => (goals ? refreshGoals() : reload('tree', 'profile', 'journal', 'memories'))} />}>
        {goals ? <DirectionsCard /> : <AllMemoryCard />}
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  entry: { flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingVertical: space.lg },
});
