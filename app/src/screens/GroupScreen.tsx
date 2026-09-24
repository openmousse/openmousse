import React, { useState } from 'react';
import { agentName } from '../brand';
import { Alert, Pressable, RefreshControl, ScrollView, View } from 'react-native';
import { CalendarDays } from '../components/icons';
import { useNavigation, useRoute } from '@react-navigation/native';
import { ChatView } from '../components/ChatView';
import { LiveDietBoard, LiveFitnessBoard, LiveFitnessTrendCard, LiveRecoveryCard, NoSourceCard } from '../components/LiveBoards';
import { ApplicationsBoard, JournalList, SleepReportSection, TrainingPlanSection } from '../components/Records';
import { MemoryList } from '../components/MemoryList';
import { ModelSwitch } from '../components/ModelPicker';
import { Btn, Card, NavHeader, Screen, SectionLabel, Segmented, T } from '../components/ui';
import { L } from '../i18n';
import { useStore } from '../store';
import { space, useTheme } from '../theme';

type Tab = 'chat' | 'board' | 'memory';

export function GroupScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { id, tab: initialTab } = useRoute<any>().params as { id: string; tab?: Tab };
  const { groups, threadModel, setThreadModel, live, liveErrors, liveLoading, connected, booting, journal, applications, loading, refreshBoards, removeGroup } = useStore();
  const g = groups.find((x) => x.id === id);
  const [tab, setTab] = useState<Tab>(initialTab ?? 'chat');
  if (!g) return <Screen><NavHeader title="Agent" onBack={() => nav.goBack()} /></Screen>;
  return (
    <Screen>
      <NavHeader title={g.name} onBack={() => nav.goBack()} right={(
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
          <Pressable onPress={() => nav.navigate('History', { thread: g.id })} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('历史与搜索', 'History and search')} style={{ width: 32, height: 32, alignItems: 'center', justifyContent: 'center' }}>
            <CalendarDays size={20} color={t.ink2} />
          </Pressable>
          <ModelSwitch value={threadModel[g.id]} onChange={(m) => setThreadModel(g.id, m)} />
        </View>
      )} />
      <View style={{ paddingHorizontal: space.lg, paddingVertical: space.sm }}>
        <Segmented value={tab} onChange={setTab} options={[{ value: 'chat', label: L('对话', 'Chat') }, { value: 'board', label: L('看板', 'Dashboard') }, { value: 'memory', label: L('记忆', 'Memory') }]} />
      </View>
      {tab === 'chat' ? <ChatView threadId={g.id} placeholder={L(`在「${g.name}」里说`, `Message "${g.name}"`)} empty={g.purpose ? L(`这个 Agent 负责：${g.purpose}`, `This agent handles: ${g.purpose}`) : undefined} /> : (
        <ScrollView contentContainerStyle={{ padding: space.lg, paddingTop: space.sm, paddingBottom: space.xxl }}
          refreshControl={<RefreshControl refreshing={liveLoading || !!loading.feed || !!loading.journal} onRefresh={() => refreshBoards().catch(() => {})} />}>
          {tab === 'board' ? (
            !live ? (
              <Card><T v="callout" color={t.ink2}>{booting || liveLoading ? L('正在读…', 'Loading…') : !connected ? L('没连上服务器。检查「我 → 服务器」后回到这一页。', 'Not connected to the server. Check Me → Server, then come back here.') : L('看板数据没读到。', "Couldn't load the dashboard data.")}</T></Card>
            ) : g.dashboard === 'fitness' ? (
              <>
                <TrainingPlanSection groupId={g.id} onAsk={() => setTab('chat')} />
                <LiveRecoveryCard />
                {live.week ? <LiveFitnessBoard week={live.week} /> : live.sources.workouts === false ? <NoSourceCard kind={L('训练', 'workout')} /> : <Card><T v="callout" color={t.bad}>{L(`训练数据没读到${liveErrors.week ? `：${liveErrors.week}` : ''}`, `Couldn't load workout data${liveErrors.week ? `: ${liveErrors.week}` : ''}`)}</T></Card>}
                {live.trend ? <LiveFitnessTrendCard trend={live.trend} /> : null}
              </>
            ) : g.dashboard === 'diet' ? (
              live.diet ? <LiveDietBoard diet={live.diet} energy={live.energy} energyError={liveErrors.energy} groupId={g.id} onAsk={() => setTab('chat')} /> : live.sources.meals === false ? <NoSourceCard kind={L('饮食', 'meal')} /> : <Card><T v="callout" color={t.bad}>{L(`饮食数据没读到${liveErrors.diet ? `：${liveErrors.diet}` : ''}`, `Couldn't load meal data${liveErrors.diet ? `: ${liveErrors.diet}` : ''}`)}</T></Card>
            ) : g.dashboard === 'health' ? (
              <>
                <SleepReportSection groupId={g.id} onAsk={() => setTab('chat')} />
                <LiveRecoveryCard />
              </>
            ) : g.dashboard === 'apply' || g.dashboard === 'masters' ? (
              <ApplicationsBoard apps={applications.filter((a) => (a.kind === 'masters') === (g.dashboard === 'masters'))} school={g.dashboard === 'masters'} />
            ) : (
              <Card><T v="callout" color={t.ink2}>{L(`这个 Agent 还没有看板。等它开始记录结构化数据后，${agentName()} 会按数据类型生成一个。`, `This agent has no dashboard yet. Once it starts recording structured data, ${agentName()} will build one for that kind of data.`)}</T></Card>
            )
          ) : (
            <>
              <T v="callout" color={t.ink2} style={{ marginBottom: space.md }}>{g.purpose}</T>
              <SectionLabel>{L('日志', 'Journal')}</SectionLabel>
              <JournalList entries={journal.filter((e) => e.groupId === g.id)} empty={L(`还没有记录。在对话里说感受、想法或决定，${agentName()} 会记在这里。`, `Nothing yet. Share feelings, thoughts or decisions in chat and ${agentName()} will log them here.`)} />
              <SectionLabel>{L('长期记忆', 'Long-term memory')}</SectionLabel>
              <MemoryList scope={g.id} />
              <T v="caption" color={t.ink3} style={{ marginTop: space.md, paddingHorizontal: space.xs }}>{L(`这个 Agent 有自己的长期记忆（workspace-${g.id}/MEMORY.md），每天 04:00 前做日结、把结论写进这里。点垃圾桶让它忘记。`, `This agent has its own long-term memory (workspace-${g.id}/MEMORY.md). Before 04:00 each day it writes a daily digest and saves the conclusions here. Tap the trash icon to make it forget something.`)}</T>
              <View style={{ marginTop: space.xl }}>
                <Btn label={L('删除这个 Agent', 'Delete this agent')} kind="danger" onPress={() => Alert.alert(L(`删除「${g.name}」？`, `Delete "${g.name}"?`), L('它在服务器上的 OpenClaw agent 会去掉，工作区和记忆归档到 archive/（不删）。这里的对话记录、日志和卡片留着当历史。', 'Its OpenClaw agent on the server is removed, and its workspace and memory are moved to archive/ (not deleted). Its chats, journal and cards here are kept as history.'), [
                  { text: L('取消', 'Cancel'), style: 'cancel' },
                  { text: L('删除', 'Delete'), style: 'destructive', onPress: () => removeGroup(g.id).then(() => nav.goBack()).catch((e) => Alert.alert(L('删不了', "Couldn't delete it"), e instanceof Error ? e.message : String(e))) },
                ])} />
              </View>
            </>
          )}
        </ScrollView>
      )}
    </Screen>
  );
}

