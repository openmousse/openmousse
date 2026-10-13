import React, { useEffect, useRef } from 'react';
import { View } from 'react-native';
import { Sparkles } from '../components/icons';
import { useNavigation, useRoute } from '@react-navigation/native';
import { AllBlocks, BoardFooter, BoardProvider, hasBlocks, UndoStrip, type useAgentBoard } from '../components/blocks/BoardContext';
import { SectionedBoard } from '../components/blocks/Sections';
import { AgentFace, ChatHeader, StateFace } from '../components/ChatParts';
import { ChatView } from '../components/ChatView';
import { DietBoard } from '../components/DietBoard';
import { FitnessBoard } from '../components/FitnessBoard';
import { LiveRecoveryCard, NoSourceCard } from '../components/LiveBoards';
import { ApplicationsBoard, SleepReportSection } from '../components/Records';
import { StudyBoard } from '../components/StudyBoard';
import { Btn, Card, NavHeader, Screen, T } from '../components/ui';
import type { Group } from '../data/types';
import { L } from '../i18n';
import type { ChatQuote } from '../navigation';
import { useStore, useThreadOnScreen } from '../store';
import { space, useTheme } from '../theme';
import { useChatList } from './ChatScreen';

/**
 * 一个 Agent 的看板（资料页第一屏，InfoScreen）：内置看板（健身、饮食、睡眠、申请、学习）或它自己的积木。
 * blocks 由外面读（useAgentBoard），下拉刷新也在外面；onChat：看板上「问它」之类的按钮回到它的对话。
 */
export function AgentBoard({ g, blocks, onChat, onHistory }: { g: Group; blocks: ReturnType<typeof useAgentBoard>; onChat: () => void; onHistory: () => void }) {
  const t = useTheme();
  const { live, liveErrors, liveLoading, connected, booting, applications, send, typing } = useStore();
  return (
    <BoardProvider agent={g.id} board={blocks.board} error={blocks.error} reload={blocks.reload} onChat={onChat}>
      <UndoStrip />
      {g.dashboard === 'none' || !g.dashboard ? (
        hasBlocks(blocks.board) ? <AllBlocks /> : (
          <Card style={{ marginTop: space.md, gap: space.md }}>
            <T v="callout" color={t.ink2}>{L(`此 Agent 尚无看板。可请它根据所负责的事务提出方案：记录哪些数据、显示哪些模块；你可在「待你确认」中预览后再决定。`, "This Agent has no dashboard yet. Ask it to propose one for what it looks after: what to track and which blocks to show. You'll see a preview in Needs your OK before anything changes.")}</T>
            <Btn label={typing[g.id] ? L(`${g.name} 正在回复…`, `${g.name} is replying…`) : L('请求提案', 'Request a proposal')} kind="primary" icon={<Sparkles size={14} color={t.onGold} />}
              onPress={() => { if (typing[g.id]) return; send(g.id, L('帮我设计一下你的看板：要记哪些数据、放哪几块。先交提案，我看了预览再定。', 'Design your dashboard: what data to keep and which blocks to show. Send it as a proposal so I can see the preview first.')); onChat(); }} />
          </Card>
        )
      ) : g.dashboard === 'study' ? (
        <StudyBoard onChat={onChat} />
      ) : !live ? (
        <>
          <Card style={{ marginTop: space.md }}><T v="callout" color={t.ink2}>{booting || liveLoading ? L('正在加载…', 'Loading…') : !connected ? L('未连接服务器。请检查「设置 → 我的 claw」后返回此页。', 'Not connected to the server. Check Settings → My claws, then come back here.') : L('无法加载看板数据。', "Couldn't load the dashboard data.")}</T></Card>
          <AllBlocks />
        </>
      ) : g.dashboard === 'fitness' ? (
        <FitnessBoard groupId={g.id} onAsk={onChat} />
      ) : g.dashboard === 'diet' ? (
        live.diet ? <DietBoard diet={live.diet} energy={live.energy} energyError={liveErrors.energy} groupId={g.id} onAsk={onChat} />
          : <><View style={{ marginTop: space.md }}>{live.sources.meals === false ? <NoSourceCard kind={L('饮食', 'meal')} /> : <Card><T v="callout" color={t.bad}>{L(`无法加载饮食数据${liveErrors.diet ? `：${liveErrors.diet}` : ''}`, `Couldn't load meal data${liveErrors.diet ? `: ${liveErrors.diet}` : ''}`)}</T></Card>}</View><AllBlocks /></>
      ) : g.dashboard === 'health' ? (
        <SectionedBoard els={{ 'health.sleep': <SleepReportSection groupId={g.id} onAsk={onChat} />, 'health.recovery': <LiveRecoveryCard /> }} />
      ) : g.dashboard === 'apply' || g.dashboard === 'masters' ? (
        <SectionedBoard els={{
          [`${g.dashboard}.list`]: <View style={{ marginTop: space.md }}><ApplicationsBoard apps={applications.filter((a) => (a.kind === 'masters') === (g.dashboard === 'masters'))} school={g.dashboard === 'masters'} /></View>,
        }} />
      ) : <AllBlocks />}
      <BoardFooter onHistory={onHistory} />
      {blocks.error ? <Card style={{ marginTop: space.md }}><T v="callout" color={t.bad}>{L(`无法加载看板积木：${blocks.error}`, `Couldn't load the dashboard blocks: ${blocks.error}`)}</T></Card> : null}
    </BoardProvider>
  );
}

/**
 * 一个 Agent 的对话（2026-10-13 起只有对话）：页头 返回 / 头像（外圈状态）/ 名字 + 职责 / ⓘ，看板、记忆、设置都在 ⓘ 的资料页。
 * 老的 params tab = board | memory（推送、看板链接、深链）：打开时顺带把资料页叠上去，返回就是它的对话。
 */
export function GroupScreen() {
  const nav = useNavigation<any>();
  const { id, tab, at, quote, focus } = useRoute<any>().params as { id: string; tab?: 'chat' | 'board' | 'memory'; at?: number; quote?: ChatQuote; focus?: string };
  const { groups } = useStore();
  const list = useChatList();
  const g = groups.find((x) => x.id === id);
  const state = list.agents.find((a) => a.id === id)?.state ?? null;
  // 对话在屏幕上：不为它弹小窗，新消息直接算已读
  useThreadOnScreen(g ? g.id : null);
  const opened = useRef<number | undefined>(undefined);
  useEffect(() => {
    if (!g || (tab !== 'board' && tab !== 'memory') || opened.current === (at ?? 0)) return;
    opened.current = at ?? 0;
    nav.navigate('Info', { kind: 'agent', id: g.id, tab, at });
  }, [g, tab, at, nav]);
  if (!g) return <Screen><NavHeader title="Agent" onBack={() => nav.goBack()} /></Screen>;
  return (
    <Screen>
      <ChatHeader title={g.name} sub={g.purpose || 'Agent'} face={<StateFace size={36} state={state}><AgentFace icon={g.icon} color={g.color} size={36} /></StateFace>}
        onBack={() => nav.goBack()} onInfo={() => nav.navigate('Info', { kind: 'agent', id: g.id })} infoLabel={L('看板、记忆与设置', 'Dashboard, memory and settings')} />
      <ChatView key={g.id} threadId={g.id} quote={quote} quoteAt={quote ? at ?? 0 : 0} focus={focus} focusAt={focus ? at ?? 0 : 0} placeholder={L(`向「${g.name}」发送消息`, `Message "${g.name}"`)}
        empty={g.purpose ? L(`此 Agent 负责：${g.purpose}`, `This Agent handles: ${g.purpose}`) : undefined} />
    </Screen>
  );
}
