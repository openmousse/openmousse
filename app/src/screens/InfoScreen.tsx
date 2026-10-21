// 资料页（2026-10-13，聊天页头的 ⓘ）：每种对话一页，头上是大头像、名字和一行说明。
// Agent：看板（第一屏）/ 记忆 / 设置；主对话、项目、好友：一页设置；会议：会议那边的 MeetingInfoScreen（MeetingScreens.tsx）。置顶 / 免打扰 / 删除和对话列表的左滑、长按是同一套（useChatActions）。
// params：kind（agent | main | project | friend | meeting）、id、tab（Agent 的 board | memory | settings）、at（从推送 / 链接来时换 tab 用）。
import React, { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Switch, TextInput, View } from 'react-native';
import { useNavigation, useRoute } from '@react-navigation/native';
import { agentName } from '../brand';
import { AgentMemory } from '../components/AgentMemory';
import { useAgentBoard } from '../components/blocks/BoardContext';
import { AgentFace, PillTabs, ProjectFace } from '../components/ChatParts';
import { Archive, ArchiveRestore, Bell, Brain, CalendarDays, ChevronLeft, ClipboardList, Cpu, MessageCircle, MessagesSquare, Palette, Pencil, Pin, Settings, Trash2 } from '../components/icons';
import { LensAvatar } from '../components/LensAvatar';
import { ModelSwitch, modelOf } from '../components/ModelPicker';
import { ArchiveSheet, projectLine } from '../components/ProjectCard';
import { useSheet } from '../components/Sheet';
import { Group as Box, GroupLabel, GroupNote, Row } from '../components/settings';
import { Btn, Card, PullRefresh, Screen, T, showError } from '../components/ui';
import * as fr from '../api/friends';
import { meetingsApi } from '../api/meetings';
import type { Friend } from '../api/friends';
import { supports } from '../api/version';
import { L } from '../i18n';
import { useStore } from '../store';
import { radius, space, type, useTheme } from '../theme';
import { useChatActions, useChatList } from './ChatScreen';
import { FriendAvatar, FriendClear, FriendInfoBody } from './FriendsScreens';
import { AgentBoard } from './GroupScreen';
import { MeetingInfoScreen } from './MeetingScreens';

type Kind = 'agent' | 'main' | 'project' | 'friend' | 'meeting';
type AgentTab = 'board' | 'memory' | 'settings';

/** 页头：只有返回（设计稿：资料页不重复名字，名字在下面的大头像旁）。 */
function Top({ onBack }: { onBack: () => void }) {
  const t = useTheme();
  return (
    <View style={styles.top}>
      <Pressable onPress={onBack} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('返回', 'Back')} style={styles.back}>
        <ChevronLeft size={26} color={t.ink} />
      </Pressable>
    </View>
  );
}

function Head({ face, title, sub }: { face: React.ReactNode; title: string; sub?: string }) {
  const t = useTheme();
  return (
    <View style={styles.head}>
      {face}
      <View style={{ flex: 1, minWidth: 0, gap: 3 }}>
        <T v="title" style={{ fontSize: 22, fontWeight: '700' }} numberOfLines={2}>{title}</T>
        {sub ? <T v="callout" color={t.ink2} numberOfLines={2}>{sub}</T> : null}
      </View>
    </View>
  );
}

/** 开关一行（置顶、免打扰）。 */
function SwitchLine({ icon, title, sub, value, onChange, first }: { icon: React.ReactNode; title: string; sub?: string; value: boolean; onChange: (v: boolean) => void; first?: boolean }) {
  const t = useTheme();
  return <Row icon={icon} title={title} sub={sub} first={first} right={<Switch value={value} onValueChange={onChange} accessibilityLabel={title} trackColor={{ true: t.cyan, false: t.track }} thumbColor="#FFFFFF" />} />;
}

/** 改项目名（弹层）。 */
function RenameSheet({ title, close, save }: { title: string; close: () => void; save: (v: string) => Promise<void> }) {
  const t = useTheme();
  const [v, setV] = useState(title);
  const [busy, setBusy] = useState(false);
  const go = () => {
    const name = v.trim();
    if (!name || busy) return;
    setBusy(true);
    save(name).then(close).catch((e) => { setBusy(false); showError(L('保存失败', "Couldn't save"), e); });
  };
  return (
    <View style={{ gap: space.md }}>
      <TextInput value={v} onChangeText={setV} autoFocus onSubmitEditing={go} returnKeyType="done" accessibilityLabel={L('项目名称', 'Project name')}
        style={[type.body, { backgroundColor: t.surface, color: t.ink, borderRadius: radius.md, paddingHorizontal: space.lg, paddingVertical: 13 }]} />
      <Btn label={busy ? L('正在保存…', 'Saving…') : L('保存', 'Save')} onPress={go} />
    </View>
  );
}

export function InfoScreen() {
  const nav = useNavigation<any>();
  const { kind, id, tab, at } = (useRoute<any>().params ?? {}) as { kind: Kind; id: string; tab?: AgentTab; at?: number };
  const back = () => nav.goBack();
  if (kind === 'agent') return <AgentInfo id={id} tab={tab} at={at} onBack={back} />;
  if (kind === 'friend') return <FriendInfo id={id} onBack={back} />;
  if (kind === 'meeting') return <MeetingInfoScreen />;  // 读同一个 route 的 id
  return <ThreadInfo kind={kind} id={id} onBack={back} />;
}

/** 回到「对话」tab（删掉以后：资料页和它的聊天页一起退掉）。 */
const toList = (nav: any, side?: 'friends') => nav.navigate('Tabs', { screen: '对话', params: side ? { side, at: Date.now() } : undefined }, { pop: true });

function AgentInfo({ id, tab: wanted, at, onBack }: { id: string; tab?: AgentTab; at?: number; onBack: () => void }) {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { groups, threadModel, setThreadModel, refreshBoards, reload, claw } = useStore();
  const list = useChatList();
  const { prefs, deleteAgent } = useChatActions();
  const g = groups.find((x) => x.id === id);
  const row = list.agents.find((a) => a.id === id);
  const [tab, setTab] = useState<AgentTab>(wanted ?? 'board');
  const [seenAt, setSeenAt] = useState(at);
  if (at !== seenAt) { setSeenAt(at); if (wanted) setTab(wanted); }
  const blocks = useAgentBoard(id, tab === 'board' && !!g);
  if (!g) return <Screen><Top onBack={onBack} /><T v="callout" color={t.ink2} style={{ padding: space.lg }}>{L('此 Agent 已删除。', 'This Agent has been deleted.')}</T></Screen>;
  const model = modelOf(g.modelId)?.short ?? g.modelId;
  const toChat = () => nav.navigate('Group', { id: g.id });
  const rooms = list.rows.filter((r) => (r.kind === 'meeting' || r.kind === 'room') && r.members?.includes(g.id));
  const refresh = tab === 'board' ? () => Promise.all([refreshBoards(), blocks.reload()]) : tab === 'memory' ? () => reload('memories', 'journal') : () => reload('chats', 'groups');
  return (
    <Screen>
      <Top onBack={onBack} />
      <ScrollView contentContainerStyle={{ paddingBottom: space.xxl }} refreshControl={<PullRefresh onRefresh={refresh} />}>
        <Head face={<AgentFace icon={g.icon} color={g.color} size={60} />} title={g.name} sub={[g.purpose, model].filter(Boolean).join(' · ')} />
        <PillTabs<AgentTab> value={tab} onChange={setTab} style={{ marginHorizontal: space.lg }}
          options={[{ value: 'board', label: L('看板', 'Dashboard') }, { value: 'memory', label: L('记忆', 'Memory') }, { value: 'settings', label: L('设置', 'Settings') }]} />
        {tab === 'board' ? (
            <>
              <View style={{ paddingHorizontal: space.lg }}>
                <AgentBoard g={g} blocks={blocks} onChat={toChat} onHistory={() => nav.navigate('BoardHistory', { id: g.id })} />
              </View>
              {rooms.length ? (
                <>
                  <GroupLabel>{L('所在群聊', 'Groups it is in')}</GroupLabel>
                  <Box>
                    {rooms.map((r, i) => (
                      <Row key={r.id} first={i === 0} title={r.title} icon={<MessageCircle size={18} color={t.ink2} />}
                        onPress={() => nav.navigate('Meeting', { id: r.id })} />
                    ))}
                  </Box>
                </>
              ) : null}
            </>
          ) : tab === 'memory' ? <View style={{ paddingTop: space.md, paddingHorizontal: space.lg }}><AgentMemory groupId={g.id} /></View> : (
            <>
              <GroupLabel>{L('对话', 'Chat')}</GroupLabel>
              <Box>
                <Row first icon={<Cpu size={18} color={t.ink2} />} title={L('本对话的模型', 'Model for this chat')} chevron={false}
                  right={<ModelSwitch value={threadModel[g.id] ?? g.modelId} onChange={(m) => setThreadModel(g.id, m)} />} />
                <Row icon={<CalendarDays size={18} color={t.ink2} />} title={L('聊天记录', 'Chat history')} onPress={() => nav.navigate('History', { thread: g.id })} />
                {list.server ? <SwitchLine icon={<Pin size={18} color={t.ink2} />} title={L('置顶', 'Pin')} sub={L('排在 Agent 栏最前', 'First in the Agent bar')} value={!!row?.pinned} onChange={(v) => prefs(g.id, { pinned: v })} /> : null}
                {list.server ? <SwitchLine icon={<Bell size={18} color={t.ink2} />} title={L('免打扰', 'Mute')} sub={L('不推送通知，未读不计入角标', "No notifications; unread doesn't count in the badge")} value={!!row?.muted} onChange={(v) => prefs(g.id, { muted: v })} /> : null}
              </Box>
              <GroupLabel>{L('Agent', 'Agent')}</GroupLabel>
              <Box>
                <Row first icon={<Pencil size={18} color={t.ink2} />} title={L('编辑 Agent', 'Edit Agent')} sub={L('名称、图标、职责、默认模型', 'Name, icon, role, default model')} onPress={() => nav.navigate('EditGroup', { id: g.id })} />
                <Row icon={<Trash2 size={18} color={t.bad} />} title={L('删除 Agent', 'Delete Agent')} danger onPress={() => deleteAgent(g, () => toList(nav))} />
              </Box>
              <GroupNote>{claw.caps.agentWorkspaces ? L('删除后记忆不会删除，工作区和记忆移入归档。', "Deleting keeps its memory: the workspace and memory move to the archive.") : L(`删除后 ${claw.name} 端不受影响。`, `Deleting changes nothing on ${claw.name}'s side.`)}</GroupNote>
            </>
          )}
      </ScrollView>
    </Screen>
  );
}

/** 主对话、项目：一页设置。 */
function ThreadInfo({ kind, id, onBack }: { kind: Kind; id: string; onBack: () => void }) {
  const t = useTheme();
  const nav = useNavigation<any>();
  const sheet = useSheet();
  const { avatar, threadModel, setThreadModel, sharedChannels, sideChats, claw, renameSideChat, restoreProject, archiveSideChat, reload } = useStore();
  const list = useChatList();
  const { prefs, deleteRow } = useChatActions();
  const row = list.rows.find((r) => r.id === id);
  const side = sideChats.find((c) => c.id === id);
  const isMain = kind === 'main';
  const [records, setRecords] = useState(false);  // 「会议记录」：meetings.by_origin 开着，或者有开着时收走的会（服务器 options 的 records）
  useEffect(() => {
    if (!isMain || !supports('meetings.records')) return undefined;
    let live = true;
    meetingsApi.options().then((o) => { if (live) setRecords(o.records ?? o.byOrigin); }).catch(() => {});
    return () => { live = false; };
  }, [isMain]);
  const title = isMain ? agentName() : side?.title ?? row?.title ?? '';
  const model = modelOf(threadModel[id] ?? threadModel.main)?.short;
  const face = isMain ? <LensAvatar size={60} config={avatar} /> : <ProjectFace size={60} archived={side?.archived} />;
  const sub = isMain ? [L('主对话', 'Main chat'), model].filter(Boolean).join(' · ')
    : side ? `${side.archived ? L('已归档', 'Archived') : L('项目', 'Project')} · ${projectLine(side)}` : L('项目', 'Project');
  const after = () => toList(nav);
  return (
    <Screen>
      <Top onBack={onBack} />
      <ScrollView contentContainerStyle={{ paddingBottom: space.xxl }} refreshControl={<PullRefresh onRefresh={() => reload('chats', 'sideChats')} />}>
        <Head face={face} title={title} sub={sub} />
        <GroupLabel>{L('对话', 'Chat')}</GroupLabel>
        <Box>
          <Row first icon={<Cpu size={18} color={t.ink2} />} title={L('本对话的模型', 'Model for this chat')} chevron={false}
            right={<ModelSwitch value={threadModel[id] ?? threadModel.main} onChange={(m) => setThreadModel(id, m)} />} />
          <Row icon={<CalendarDays size={18} color={t.ink2} />} title={L('聊天记录', 'Chat history')} onPress={() => nav.navigate('History', { thread: id })} />
          {isMain && records ? <Row icon={<MessagesSquare size={18} color={t.ink2} />} title={L('会议记录', 'Meeting records')} sub={L(`${agentName()} 发起的、定了告诉我的，开完都在这里`, `Meetings ${agentName()} started and ones that ran on their own`)} onPress={() => nav.navigate('MeetingRecords')} /> : null}
          {list.server && !isMain && !side?.archived ? <SwitchLine icon={<Pin size={18} color={t.ink2} />} title={L('置顶', 'Pin')} sub={L('排在主对话之后', 'Right after the main chat')} value={!!row?.pinned} onChange={(v) => prefs(id, { pinned: v })} /> : null}
          {list.server ? <SwitchLine icon={<Bell size={18} color={t.ink2} />} title={L('免打扰', 'Mute')} sub={L('不推送通知，未读不计入角标', "No notifications; unread doesn't count in the badge")} value={!!row?.muted} onChange={(v) => prefs(id, { muted: v })} /> : null}
          {isMain ? <Row icon={<MessageCircle size={18} color={t.ink2} />} title={L('共用会话的渠道', 'Channels sharing this session')} value={sharedChannels.length ? sharedChannels.join('、') : L('仅 app', 'App only')} chevron={false} /> : null}
        </Box>
        {isMain ? (
          <>
            <GroupLabel>{agentName()}</GroupLabel>
            <Box>
              <Row first icon={<Brain size={18} color={t.ink2} />} title={L('记忆', 'Memory')} onPress={() => nav.navigate('Memory')} />
              {claw.caps.tasks ? <Row icon={<ClipboardList size={18} color={t.ink2} />} title={L('任务', 'Tasks')} sub={L('执行者、进度与过程', 'Assignee, progress and steps')} onPress={() => nav.navigate('Tasks')} /> : null}
              <Row icon={<Palette size={18} color={t.ink2} />} title={L('形象', 'Avatar')} onPress={() => nav.navigate('Avatar')} />
              <Row icon={<Settings size={18} color={t.ink2} />} title={L('设置', 'Settings')} onPress={() => nav.navigate('Me')} />
            </Box>
            <GroupNote>{L('主对话固定在对话列表第一行，不能删除。', 'The main chat stays on the first row of your chats and can’t be deleted.')}</GroupNote>
          </>
        ) : kind === 'project' && side ? (
          <>
            <GroupLabel>{L('项目', 'Project')}</GroupLabel>
            <Box>
              <Row first icon={<Pencil size={18} color={t.ink2} />} title={L('重命名', 'Rename')} onPress={() => sheet.open({ title: L('重命名项目', 'Rename project'), content: (close) => <RenameSheet title={side.title} close={close} save={(v) => renameSideChat(id, v)} /> })} />
              {side.archived ? (
                <Row icon={<ArchiveRestore size={18} color={t.ink2} />} title={L('恢复', 'Restore')} onPress={() => { restoreProject(id).catch(() => archiveSideChat(id, false)).then(() => reload('chats')).catch((e) => showError(L('恢复失败', "Couldn't restore"), e)); }} />
              ) : (
                <Row icon={<Archive size={18} color={t.ink2} />} title={L('归档', 'Archive')} sub={L('Agent 先撰写结论存入记忆，对话记录保留', 'It writes a summary into memory first; the chat is kept')}
                  onPress={() => sheet.open({ title: L(`归档「${side.title}」`, `Archive "${side.title}"`), content: (close) => <ArchiveSheet id={id} title={side.title} close={close} /> })} />
              )}
              <Row icon={<Trash2 size={18} color={t.bad} />} title={L('删除项目', 'Delete project')} danger onPress={() => deleteRow({ kind: 'project', id, title: side.title }, after)} />
            </Box>
          </>
        ) : null}
      </ScrollView>
    </Screen>
  );
}

/** 好友的资料页：原来聊天页「…」里的设置（Agent 之间、档位、备注、置顶、免打扰、指纹、屏蔽、删除好友）+ 删除聊天记录。 */
function FriendInfo({ id, onBack }: { id: string; onBack: () => void }) {
  const t = useTheme();
  const nav = useNavigation<any>();
  const sheet = useSheet();
  const { reload } = useStore();
  const [f, setF] = useState<Friend | null>(null);
  const [err, setErr] = useState('');
  const load = useCallback(() => fr.home().then((h) => { setF(h.friends.find((x) => x.id === id) ?? null); setErr(''); }).catch((e) => setErr(e instanceof Error ? e.message : String(e))), [id]);
  useEffect(() => { load(); }, [load]);
  const clear = (x: Friend) => sheet.open({
    title: L(`删除与 ${x.name} 的聊天记录？`, `Delete your chat with ${x.name}?`),
    content: (close) => <FriendClear name={x.name} close={close} run={() => fr.clearChat(x.id).then(() => { reload('unread').catch(() => {}); toList(nav, 'friends'); })} />,
  });
  return (
    <Screen>
      <Top onBack={onBack} />
      <ScrollView contentContainerStyle={{ paddingBottom: space.xxl }} refreshControl={<PullRefresh onRefresh={load} />}>
        {err ? <Card style={{ marginHorizontal: space.lg }}><T v="callout" color={t.bad}>{L(`无法加载：${err}`, `Couldn't load: ${err}`)}</T></Card> : null}
        {!f && !err ? <ActivityIndicator color={t.gold} style={{ marginTop: space.xl }} /> : null}
        {f ? (
          <>
            <Head face={<FriendAvatar id={f.id} name={f.name} size={60} />} title={f.name} sub={[f.alias && f.cardName !== f.name ? f.cardName : '', f.tierName].filter(Boolean).join(' · ')} />
            <View style={{ paddingHorizontal: space.lg }}>
              <FriendInfoBody f={f} close={() => {}} onChanged={setF} onGone={() => toList(nav, 'friends')} onAgents={() => nav.navigate('FriendAgents', { id: f.id })} />
            </View>
            {supports('friends.clear') ? (
              <Box style={{ marginTop: space.lg }}>
                <Row first icon={<Trash2 size={18} color={t.bad} />} title={L('删除聊天记录', 'Delete chat history')} danger onPress={() => clear(f)} />
              </Box>
            ) : null}
          </>
        ) : null}
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  top: { height: 48, flexDirection: 'row', alignItems: 'center', paddingHorizontal: 4 },
  back: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center' },
  head: { flexDirection: 'row', alignItems: 'center', gap: 14, paddingHorizontal: 20, paddingTop: 6, paddingBottom: 18 },
});
