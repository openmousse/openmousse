import React, { useState } from 'react';
import { Pressable, ScrollView, View } from 'react-native';
import { CalendarDays, Sparkles } from '../components/icons';
import { useNavigation, useRoute } from '@react-navigation/native';
import { AgentMemory } from '../components/AgentMemory';
import { AllBlocks, BoardFooter, BoardProvider, hasBlocks, UndoStrip, useAgentBoard } from '../components/blocks/BoardContext';
import { SectionedBoard } from '../components/blocks/Sections';
import { ChatView } from '../components/ChatView';
import { DietBoard } from '../components/DietBoard';
import { FitnessBoard } from '../components/FitnessBoard';
import { LiveRecoveryCard, NoSourceCard } from '../components/LiveBoards';
import { ApplicationsBoard, SleepReportSection } from '../components/Records';
import { StudyBoard } from '../components/StudyBoard';
import { ModelSwitch } from '../components/ModelPicker';
import { Btn, Card, NavHeader, PullRefresh, Screen, Segmented, T } from '../components/ui';
import { L } from '../i18n';
import type { ChatQuote } from '../navigation';
import { useStore, useThreadOnScreen } from '../store';
import { space, useTheme } from '../theme';

type Tab = 'chat' | 'board' | 'memory';

export function GroupScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { id, tab: initialTab, at, quote, focus } = useRoute<any>().params as { id: string; tab?: Tab; at?: number; quote?: ChatQuote; focus?: string };
  const { groups, threadModel, setThreadModel, live, liveErrors, liveLoading, connected, booting, applications, refreshBoards, reload, send, typing } = useStore();
  const g = groups.find((x) => x.id === id);
  const [tab, setTab] = useState<Tab>(initialTab ?? 'chat');
  // 从通知 / 小窗 / 收件箱点进来（带 at）：切到要看的那个 tab（一般是对话）
  const [seenAt, setSeenAt] = useState(at);
  if (at !== seenAt) {
    setSeenAt(at);
    if (initialTab) setTab(initialTab);
  }
  // 对话 tab 在屏幕上：不为它弹小窗，新消息直接算已读
  useThreadOnScreen(tab === 'chat' && g ? g.id : null);
  // 积木看板：Agent 自己的表和积木（看板 tab 在屏幕上时读，切过来重读一次）
  const blocks = useAgentBoard(id, tab === 'board' && !!g);
  if (!g) return <Screen><NavHeader title="Agent" onBack={() => nav.goBack()} /></Screen>;
  const toChat = () => setTab('chat');
  return (
    <Screen>
      {/* 点名字（旁边一支小笔）进编辑页：名字、图标和颜色、职责、默认模型、删除 */}
      <NavHeader title={g.name} onBack={() => nav.goBack()} onTitlePress={() => nav.navigate('EditGroup', { id: g.id })} titleHint={L('编辑 Agent', 'Edit agent')} right={(
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
      {tab === 'chat' ? <ChatView threadId={g.id} quote={quote} quoteAt={quote ? at ?? 0 : 0} focus={focus} focusAt={focus ? at ?? 0 : 0} placeholder={L(`在「${g.name}」里说`, `Message "${g.name}"`)} empty={g.purpose ? L(`这个 Agent 负责：${g.purpose}`, `This agent handles: ${g.purpose}`) : undefined} /> : (
        <ScrollView contentContainerStyle={{ padding: space.lg, paddingTop: 0, paddingBottom: space.xxl }}
          refreshControl={<PullRefresh onRefresh={tab === 'board' ? () => Promise.all([refreshBoards(), blocks.reload()]) : () => reload('memories', 'journal')} />}>
          {tab === 'board' ? (
            <BoardProvider agent={g.id} board={blocks.board} error={blocks.error} reload={blocks.reload} onChat={toChat}>
              <UndoStrip />
              {g.dashboard === 'none' || !g.dashboard ? (
                hasBlocks(blocks.board) ? <AllBlocks /> : (
                  <Card style={{ marginTop: space.md, gap: space.md }}>
                    <T v="callout" color={t.ink2}>{L(`这个 Agent 还没有看板。让它按自己管的事提一个：记哪些数据、放哪几块，你在「等你点头」里看预览再定。`, "This agent has no dashboard yet. Ask it to propose one for what it looks after: what to keep track of and which blocks to show. You'll see a preview in Needs your OK before anything changes.")}</T>
                    <Btn label={typing[g.id] ? L(`${g.name} 正在回…`, `${g.name} is replying…`) : L('让它提一个', 'Ask it to propose one')} kind="primary" icon={<Sparkles size={14} color={t.onGold} />}
                      onPress={() => { if (typing[g.id]) return; send(g.id, L('帮我设计一下你的看板：要记哪些数据、放哪几块。先交提案，我看了预览再定。', 'Design your dashboard: what data to keep and which blocks to show. Send it as a proposal so I can see the preview first.')); toChat(); }} />
                  </Card>
                )
              ) : g.dashboard === 'study' ? (
                <StudyBoard onChat={toChat} />
              ) : !live ? (
                <>
                  <Card style={{ marginTop: space.md }}><T v="callout" color={t.ink2}>{booting || liveLoading ? L('正在读…', 'Loading…') : !connected ? L('没连上服务器。检查「我 → 服务器」后回到这一页。', 'Not connected to the server. Check Me → Server, then come back here.') : L('看板数据没读到。', "Couldn't load the dashboard data.")}</T></Card>
                  <AllBlocks />
                </>
              ) : g.dashboard === 'fitness' ? (
                <FitnessBoard groupId={g.id} onAsk={toChat} />
              ) : g.dashboard === 'diet' ? (
                live.diet ? <DietBoard diet={live.diet} energy={live.energy} energyError={liveErrors.energy} groupId={g.id} onAsk={toChat} />
                  : <><View style={{ marginTop: space.md }}>{live.sources.meals === false ? <NoSourceCard kind={L('饮食', 'meal')} /> : <Card><T v="callout" color={t.bad}>{L(`饮食数据没读到${liveErrors.diet ? `：${liveErrors.diet}` : ''}`, `Couldn't load meal data${liveErrors.diet ? `: ${liveErrors.diet}` : ''}`)}</T></Card>}</View><AllBlocks /></>
              ) : g.dashboard === 'health' ? (
                <SectionedBoard els={{ 'health.sleep': <SleepReportSection groupId={g.id} onAsk={toChat} />, 'health.recovery': <LiveRecoveryCard /> }} />
              ) : g.dashboard === 'apply' || g.dashboard === 'masters' ? (
                <SectionedBoard els={{
                  [`${g.dashboard}.list`]: <View style={{ marginTop: space.md }}><ApplicationsBoard apps={applications.filter((a) => (a.kind === 'masters') === (g.dashboard === 'masters'))} school={g.dashboard === 'masters'} /></View>,
                }} />
              ) : <AllBlocks />}
              <BoardFooter onHistory={() => nav.navigate('BoardHistory', { id: g.id })} />
              {blocks.error ? <Card style={{ marginTop: space.md }}><T v="callout" color={t.bad}>{L(`看板里的积木没读到：${blocks.error}`, `Couldn't load the dashboard blocks: ${blocks.error}`)}</T></Card> : null}
            </BoardProvider>
          ) : <View style={{ paddingTop: space.sm }}><AgentMemory groupId={g.id} /></View>}
        </ScrollView>
      )}
    </Screen>
  );
}
