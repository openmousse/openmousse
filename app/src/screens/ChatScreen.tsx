// 「对话」tab（2026-10-13 重做，设计稿第 1、3、4 排）：顶上切换助手 | 好友。
// 助手一边 = 你的 AI：一排 Agent（外圈状态、未读）+ 列表（主对话固定第一行、会议 / Agent 群、项目、底下「已结束的会议」「已归档的项目」）；
// 好友一边 = 人：好友和好友群一个列表（FriendsHome）。点开都是同一种聊天页（ChatHeader）：主对话和项目在这里（路由 Chat），
// Agent 在 GroupScreen，好友在 FriendChatScreen；ⓘ 进资料页（InfoScreen）。
// 数据来自 /api/chats（../../server/chats.py）；老服务器没有就用 groups / sideChats / unread 拼（不能置顶 / 免打扰）。
import React, { useEffect, useMemo, useState } from 'react';
import { agentName } from '../brand';
import { Keyboard, Platform, Pressable, ScrollView, StyleSheet, TextInput, View } from 'react-native';
import { useFocusEffect, useIsFocused, useNavigation, useRoute } from '@react-navigation/native';
import { ChatView } from '../components/ChatView';
import { Archive, ArchiveRestore, Bell, BellOff, CirclePlus, FolderKanban, MessagesSquare, Pencil, Pin, PinOff, Plus, ScanLine, Search, Trash2, User, UserPlus, X } from '../components/icons';
import { AgentFace, ChatHeader, ChatRowView, ConfirmDelete, MenuItem, PillTabs, ProjectFace, RoomFace, StateFace, SwipeRow, rowTime, type Member, type SwipeAction } from '../components/ChatParts';
import { LensAvatar } from '../components/LensAvatar';
import { NewProjectSheet, ProjectPanel, leftWords, projectLine } from '../components/ProjectCard';
import { useSheet } from '../components/Sheet';
import { Btn, Card, NavHeader, PullRefresh, Screen, T, showError } from '../components/ui';
import { FriendsHome } from './FriendsScreens';
import { chatsApi, fallbackList, overlay, type ChatAgent, type ChatList, type ChatRow } from '../api/chats';
import { supports } from '../api/version';
import { hasRoute, type ChatQuote } from '../navigation';
import { L } from '../i18n';
import { useStore, useThreadOnScreen } from '../store';
import { radius, space, type, useTheme } from '../theme';
import { hideWelcome, welcomeHidden } from '../welcome';
import { useAccount } from './MeScreen';
import { initialsOf } from '../api/account';

/** Grava 一边的列表：服务器那份（或老服务器上拼的）叠上本机更新的状态和已读。 */
export function useChatList(): ChatList {
  const { chats, groups, sideChats, unread, typing, inbox, threads } = useStore();
  // 老服务器才用得上 threads（拼最后一句）：有服务器那份时不跟着 threads 重算
  const local = chats ? null : threads;
  return useMemo(() => overlay(chats ?? fallbackList(groups, sideChats, unread, local ?? {}), typing, inbox, unread), [chats, groups, sideChats, unread, typing, inbox, local]);
}

/** 「对话」tab 角标：Grava 一边没免打扰的未读 + 好友发来没看的（免打扰的不算）。 */
export function useChatBadge(): number {
  const list = useChatList();
  const { unread } = useStore();
  const friends = Object.values(unread.friends ?? {}).reduce((sum, u) => sum + (u.muted ? 0 : u.n), 0);
  return [...list.agents, ...list.rows].reduce((sum, x) => sum + (x.muted ? 0 : x.unread), 0) + friends;
}

/** 新实例第一次打开、主对话还空着（服务器说 first_run）：列表顶上的「开始使用」。「开始引导」进主对话替你发开场白，onboarding skill 接着带。 */
function WelcomeCard({ onStart, onDismiss }: { onStart: () => void; onDismiss: () => void }) {
  const t = useTheme();
  return (
    <Card style={{ gap: space.sm, marginHorizontal: space.lg, marginTop: space.md }}>
      <T v="headline">{L('开始使用', 'Get started')}</T>
      <T v="callout" color={t.ink2}>{L(`告诉 ${agentName()} 你希望它负责哪些事务，它会为你创建相应的 Agent。`, `Tell ${agentName()} what you want looked after, and it creates an Agent for you.`)}</T>
      <T v="callout" color={t.ink2}>{L('也可以先让它了解你。', 'Or let it get to know you first.')}</T>
      {/* 上下排：英文的两个按钮并排放不下，手机字号调大也不会挤成两行 */}
      <View style={{ gap: space.sm, marginTop: space.xs }}>
        <Btn label={L('开始引导', 'Walk me through it')} onPress={onStart} />
        <Btn label={L('自行探索', 'Explore on my own')} kind="quiet" onPress={onDismiss} />
      </View>
    </Card>
  );
}

type Side = 'grava' | 'friends';

/** 顶上一个圆按钮（搜索、新建、设置）。 */
function RoundBtn({ label, onPress, gold, on, children }: { label: string; onPress: () => void; gold?: boolean; on?: boolean; children: React.ReactNode }) {
  const t = useTheme();
  return (
    <Pressable onPress={onPress} hitSlop={4} accessibilityRole="button" accessibilityLabel={label} accessibilityState={on !== undefined ? { selected: on } : undefined}
      style={({ pressed }) => [styles.round, { backgroundColor: gold ? t.goldFill : on ? t.line : t.surface2, opacity: pressed ? 0.7 : 1 }]}>
      {children}
    </Pressable>
  );
}

function SearchBox({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  const t = useTheme();
  return (
    <View style={[styles.searchBox, { backgroundColor: t.surface2 }]}>
      <Search size={17} color={t.ink3} />
      <TextInput value={value} onChangeText={onChange} placeholder={placeholder} placeholderTextColor={t.ink3} autoFocus autoCorrect={false} returnKeyType="search"
        accessibilityLabel={L('搜索', 'Search')} style={[type.body, { flex: 1, color: t.ink, paddingVertical: 0, outlineWidth: 0 }]} />
      {value ? <Pressable onPress={() => onChange('')} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('清空', 'Clear')}><X size={16} color={t.ink3} /></Pressable> : null}
    </View>
  );
}

/** 一排 Agent：点 = 进它的对话，长按 = 置顶 / 免打扰 / 编辑 / 删除。 */
function AgentBar({ agents, onOpen, onMenu, onNew }: { agents: ChatAgent[]; onOpen: (a: ChatAgent) => void; onMenu: (a: ChatAgent) => void; onNew: () => void }) {
  const t = useTheme();
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.bar} style={{ flexGrow: 0, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }}>
      {agents.map((a) => (
        <Pressable key={a.id} onPress={() => onOpen(a)} onLongPress={() => onMenu(a)} delayLongPress={400} accessibilityRole="button"
          accessibilityHint={L('长按可置顶、免打扰、编辑或删除', 'Long-press to pin, mute, edit or delete')}
          style={({ pressed }) => [styles.barItem, { opacity: pressed ? 0.7 : 1 }]}>
          <StateFace state={a.state} unread={a.unread} muted={a.muted} label={a.name}>
            <AgentFace icon={a.icon} color={a.color} />
          </StateFace>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 2, maxWidth: 64 }}>
            {a.pinned ? <Pin size={9} color={t.ink3} /> : null}
            <T v="caption" color={t.ink2} numberOfLines={1} style={{ fontSize: 11, fontWeight: '500', flexShrink: 1 }}>{a.name}</T>
          </View>
        </Pressable>
      ))}
      {!agents.length ? (
        <Pressable onPress={onNew} accessibilityRole="button" style={({ pressed }) => [styles.barItem, { opacity: pressed ? 0.7 : 1 }]}>
          <View style={[styles.newFace, { borderColor: t.line }]}><Plus size={20} color={t.ink2} /></View>
          <T v="caption" color={t.ink2} numberOfLines={1} style={{ fontSize: 11, fontWeight: '500' }}>{L('新建 Agent', 'New Agent')}</T>
        </Pressable>
      ) : null}
    </ScrollView>
  );
}

/** 列表行的成员头像：会议给的 id 换成 Agent 的图标和颜色。 */
function useMembers() {
  const { groups } = useStore();
  return (ids: string[] | undefined): Member[] => (ids ?? []).map((id) => {
    const g = groups.find((x) => x.id === id);
    return { id, icon: g?.icon, color: g?.color ?? null };
  });
}

const roomKind = (r: ChatRow) => r.kind === 'meeting' || r.kind === 'room';
// 「+」里的扫一扫（ScanScreen，1.0.6 起）：只有手机 app 有相机扫码，网页版灰着并说明
const canScan = Platform.OS !== 'web';
const scanDesc = (what: string) => (canScan ? what : `${what} · ${L('仅限手机 app', 'phone app only')}`);

/** 置顶 / 免打扰 / 删除：左滑、长按、资料页共用。改完重读列表。 */
export function useChatActions() {
  const { reload, deleteSideChat, removeGroup, claw } = useStore();
  const sheet = useSheet();
  const fail = (e: unknown) => showError(L('修改失败', "Couldn't update"), e);
  const prefs = (thread: string, body: { pinned?: boolean; muted?: boolean }) =>
    chatsApi.patch(thread, body).then(() => reload('chats', 'unread')).catch(fail);
  const deleteRow = (r: { kind: ChatRow['kind']; id: string; title: string }, after?: () => void) => {
    const name = r.title;
    const spec = r.kind === 'project'
      ? { title: L(`删除项目「${name}」？`, `Delete the project "${name}"?`), label: L('删除项目', 'Delete project'),
          body: L('对话记录和项目卡将被删除，项目的截止日期从日程中移除。需要保留结论时，请先在资料页归档。', 'The conversation and the project card are deleted, and its deadlines leave your schedule. To keep a summary, archive it from its details page first.') }
      : r.kind === 'room'
        ? { title: L(`删除群聊「${name}」？`, `Delete the group "${name}"?`), label: L('删除群聊', 'Delete group'),
            body: L('群聊记录将被删除。成员 Agent 和它们的记忆不受影响；已确认的待办保留在日程和相关 Agent 的看板中。', "The group's messages are deleted. Its Agents and their memory stay; confirmed to-dos stay in your schedule and on the Agents' dashboards.") }
        : { title: L(`删除会议「${name}」？`, `Delete the meeting "${name}"?`), label: L('删除会议', 'Delete meeting'),
            body: L('会议记录将被删除。已确认的待办保留在日程和相关 Agent 的看板中。', 'The meeting record is deleted. Confirmed to-dos stay in your schedule and on the Agents’ dashboards.') };
    sheet.open({ title: spec.title, content: (close) => (
      <ConfirmDelete body={spec.body} label={spec.label} close={close}
        run={() => (r.kind === 'project' ? deleteSideChat(r.id) : chatsApi.remove(r.id)).then(() => { reload('chats').catch(() => {}); after?.(); })} />
    ) });
  };
  const deleteAgent = (a: { id: string; name: string }, after?: () => void) => sheet.open({
    title: L(`删除 Agent「${a.name}」？`, `Delete the Agent "${a.name}"?`),
    content: (close) => (
      <ConfirmDelete label={L('删除 Agent', 'Delete Agent')} close={close}
        body={claw.caps.agentWorkspaces
          ? L('Agent 将停止工作。记忆不会删除，工作区和记忆移入归档；此处的对话记录保留为历史。', "The Agent stops working. Its memory isn't deleted: the workspace and memory move to the archive, and its chats here stay as history.")
          : L(`Agent 将从列表中移除，${claw.name} 端不受影响。此处的对话记录保留为历史。`, `It's removed from your list; nothing changes on ${claw.name}'s side. Its chats here stay as history.`)}
        run={() => removeGroup(a.id).then(() => { reload('chats').catch(() => {}); after?.(); })} />
    ),
  });
  return { prefs, deleteRow, deleteAgent };
}

export function ChatScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const sheet = useSheet();
  const acct = useAccount();
  const route = useRoute<any>();
  const { connected, booting, avatar, unread, firstRun, threads, reload, typing, inbox, sideChats, send } = useStore();
  const list = useChatList();
  const members = useMembers();
  const { prefs, deleteRow, deleteAgent } = useChatActions();
  const server = list.server;
  // 调试用：网页版 ?friends=1 打开就在好友一边
  const [side, setSide] = useState<Side>(() => (Platform.OS === 'web' && typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('friends') === '1' ? 'friends' : 'grava'));
  // 从通知 / 别处跳到「对话」tab 时带 side
  const wantSide = route.params?.side as Side | undefined;
  const wantAt = (route.params?.at as number | undefined) ?? 0;
  const [seenAt, setSeenAt] = useState(wantAt);
  if (wantAt !== seenAt) { setSeenAt(wantAt); if (wantSide) setSide(wantSide); }
  const [searchOpen, setSearchOpen] = useState(false);
  const [q, setQ] = useState('');
  const [welcomeOff, setWelcomeOff] = useState(welcomeHidden);
  const friendsOn = unread.friends !== undefined;  // 服务器的未读里有 friends 才有好友功能
  const friendsN = Object.values(unread.friends ?? {}).reduce((sum, u) => sum + (u.muted ? 0 : u.n), 0);
  const gravaN = [...list.agents, ...list.rows].reduce((sum, x) => sum + (x.muted ? 0 : x.unread), 0);

  // 列表跟着未读、正在回复、收件箱变：重读（服务器那份；老服务器不用读，拼的跟着 store 走）。在屏幕上时有 Agent 在做就每 30 秒再读一次（后台任务做完）
  const focused = useIsFocused();
  const busySig = Object.keys(typing).filter((k) => typing[k]).join(',');
  const sig = `${JSON.stringify(unread.threads)}|${busySig}|${inbox.length}`;
  useEffect(() => { if (connected && supports('chats.list')) reload('chats').catch(() => {}); }, [sig, connected, reload]);
  useFocusEffect(React.useCallback(() => { if (supports('chats.list')) reload('chats').catch(() => {}); }, [reload]));
  const anyWorking = list.agents.some((a) => a.state === 'working') || list.rows.some((r) => r.state === 'working');
  useEffect(() => {
    if (!focused || !anyWorking || !supports('chats.list')) return undefined;
    const h = setInterval(() => { reload('chats').catch(() => {}); }, 30_000);
    return () => clearInterval(h);
  }, [focused, anyWorking, reload]);

  const openRow = (r: ChatRow) => {
    if (r.kind === 'main' || r.kind === 'project') nav.navigate('Chat', { id: r.id });
    else if (hasRoute('Meeting')) nav.navigate('Meeting', { id: r.id });
    else showError(L('无法打开会议', "Couldn't open the meeting"), L('当前版本的 app 尚不支持会议。', "This version of the app can't open meetings yet."));
  };
  const openAgent = (a: ChatAgent) => nav.navigate('Group', { id: a.id });

  const rowMenu = (r: ChatRow) => sheet.open({
    title: r.kind === 'main' ? agentName() : r.title,
    content: (close) => (
      <View>
        {server && r.kind !== 'main' ? <MenuItem icon={r.pinned ? PinOff : Pin} title={r.pinned ? L('取消置顶', 'Unpin') : L('置顶', 'Pin')} desc={L('排在主对话之后', 'Right after the main chat')}
          onPress={() => { close(); prefs(r.id, { pinned: !r.pinned }); }} /> : null}
        {server ? <MenuItem icon={r.muted ? Bell : BellOff} title={r.muted ? L('取消免打扰', 'Unmute') : L('免打扰', 'Mute')} desc={r.muted ? undefined : L('不推送通知，未读不计入角标', "No notifications; unread doesn't count in the badge")}
          onPress={() => { close(); prefs(r.id, { muted: !r.muted }); }} /> : null}
        {r.kind !== 'main' ? <MenuItem icon={Trash2} danger title={L('删除', 'Delete')} onPress={() => { close(); setTimeout(() => deleteRow(r), 250); }} /> : null}
        {r.kind === 'main' && !server ? <T v="callout" color={t.ink2}>{L('主对话固定在第一行，不能删除。', 'The main chat stays on the first row and can’t be deleted.')}</T> : null}
      </View>
    ),
  });
  const swipeActions = (r: ChatRow): SwipeAction[] => {
    if (r.kind === 'main') return [];
    const acts: SwipeAction[] = [];
    if (server) {
      acts.push({ key: 'pin', label: r.pinned ? L('取消置顶', 'Unpin') : L('置顶', 'Pin'), tone: 'plain', icon: r.pinned ? <PinOff size={18} color={t.ink} /> : <Pin size={18} color={t.ink} />, onPress: () => prefs(r.id, { pinned: !r.pinned }) });
      acts.push({ key: 'mute', label: r.muted ? L('取消免打扰', 'Unmute') : L('免打扰', 'Mute'), tone: 'gold', icon: r.muted ? <Bell size={18} color={t.onGold} /> : <BellOff size={18} color={t.onGold} />, onPress: () => prefs(r.id, { muted: !r.muted }) });
    }
    acts.push({ key: 'del', label: L('删除', 'Delete'), tone: 'danger', icon: <Trash2 size={18} color="#FFFFFF" />, onPress: () => deleteRow(r) });
    return acts;
  };
  const agentMenu = (a: ChatAgent) => sheet.open({
    title: a.name,
    content: (close) => (
      <View>
        {server ? <MenuItem icon={a.pinned ? PinOff : Pin} title={a.pinned ? L('取消置顶', 'Unpin') : L('置顶', 'Pin')} desc={L('排在 Agent 栏最前', 'First in the Agent bar')}
          onPress={() => { close(); prefs(a.id, { pinned: !a.pinned }); }} /> : null}
        {server ? <MenuItem icon={a.muted ? Bell : BellOff} title={a.muted ? L('取消免打扰', 'Unmute') : L('免打扰', 'Mute')} desc={a.muted ? undefined : L('不推送通知，未读不计入角标', "No notifications; unread doesn't count in the badge")}
          onPress={() => { close(); prefs(a.id, { muted: !a.muted }); }} /> : null}
        <MenuItem icon={Pencil} title={L('编辑 Agent', 'Edit Agent')} desc={L('名称、图标、职责、默认模型', 'Name, icon, role, default model')} onPress={() => { close(); nav.navigate('EditGroup', { id: a.id }); }} />
        <MenuItem icon={Trash2} danger title={L('删除 Agent', 'Delete Agent')} desc={L('记忆不会删除，移入归档', "Memory isn't deleted; it moves to the archive")}
          onPress={() => { close(); setTimeout(() => deleteAgent(a), 250); }} />
      </View>
    ),
  });
  const plus = () => {
    if (side === 'friends') {
      sheet.open({ title: L('新建', 'New'), sub: L('好友', 'Friends'), content: (close) => (
        <View>
          <MenuItem icon={UserPlus} title={L('添加好友', 'Add a friend')} desc={L('发送邀请链接或输入邀请码', 'Send an invite link or enter a code')} chevron onPress={() => { close(); nav.navigate('AddFriend'); }} />
          <MenuItem icon={ScanLine} title={L('扫一扫', 'Scan')} desc={scanDesc(L('扫描好友的邀请二维码', "Scan a friend's invite QR code"))} off={!canScan} chevron
            onPress={() => { close(); nav.navigate('Scan', { kind: 'friend' }); }} />
        </View>
      ) });
      return;
    }
    sheet.open({ title: L('新建', 'New'), sub: agentName(), content: (close) => (
      <View>
        {supports('meetings') && hasRoute('NewMeeting') ? (
          <MenuItem icon={MessagesSquare} title={L('发起会议', 'Start a meeting')} desc={L(`多个 Agent 共同讨论一个议题，${agentName()} 生成纪要`, `Several Agents discuss one topic; ${agentName()} writes the minutes`)} chevron
            onPress={() => { close(); nav.navigate('NewMeeting'); }} />
        ) : null}
        <MenuItem icon={CirclePlus} title={L('新建 Agent', 'New Agent')} desc={L('负责一个领域，拥有独立的记忆和看板', 'Looks after one area, with its own memory and dashboard')} chevron
          onPress={() => { close(); nav.navigate('NewGroup'); }} />
        <MenuItem icon={FolderKanban} title={L('新建项目', 'New project')} desc={L('持续多日、有截止日期的事项', 'Something that runs for days and has deadlines')} chevron
          onPress={() => { close(); setTimeout(() => sheet.open({ title: L('新建项目', 'New project'), content: (c) => <NewProjectSheet close={c} onCreated={(id) => { reload('chats').catch(() => {}); nav.navigate('Chat', { id }); }} /> }), 250); }} />
        <MenuItem icon={ScanLine} title={L('扫一扫', 'Scan')} desc={scanDesc(L('扫描配对二维码连接此设备，或扫描好友邀请码', "Scan a pairing QR code to connect this device, or a friend's invite"))} off={!canScan} chevron
          onPress={() => { close(); nav.navigate('Scan'); }} />
      </View>
    ) });
  };

  // 搜索：Grava 一边在本机按名字和最后一句筛，另有一行去历史里搜全部聊天记录；好友一边用 FriendsHome 自己的（名字 + 聊天记录）
  const words = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const hit = (...s: string[]) => words.every((w) => s.join(' ').toLowerCase().includes(w));
  const searching = searchOpen && words.length > 0;
  const agents = searching ? list.agents.filter((a) => hit(a.name, a.purpose, a.line)) : list.agents;
  const rows = searching ? list.rows.filter((r) => hit(r.kind === 'main' ? agentName() : r.title, r.line)) : list.rows;
  const toggleSearch = () => { setSearchOpen((v) => !v); setQ(''); Keyboard.dismiss(); };

  const welcome = connected && firstRun && !welcomeOff && !(threads.main?.length) ? (
    <WelcomeCard onStart={() => { send('main', L('我第一次用，带我走一遍。', "It's my first time here. Walk me through it.")); nav.navigate('Chat', { id: 'main' }); }}
      onDismiss={() => { hideWelcome(); setWelcomeOff(true); }} />
  ) : null;

  const face = (r: ChatRow) => {
    const inner = r.kind === 'main' ? <LensAvatar size={46} config={avatar} /> : r.kind === 'project' ? <ProjectFace /> : <RoomFace members={members(r.members)} avatar={avatar} />;
    return <StateFace state={r.state}>{inner}</StateFace>;
  };
  const lineOf = (r: ChatRow) => {
    const text = r.lineRole === 'user' && r.line ? L(`你：${r.line}`, `You: ${r.line}`) : r.line;
    if (r.kind === 'project') {
      const p = sideChats.find((c) => c.id === r.id);
      return text || (p ? projectLine(p) : r.purpose || L('新项目', 'New project'));
    }
    if (r.kind === 'main') return text || (connected ? L('今天暂无对话', 'Nothing here today yet') : '');
    return text;
  };
  const tagOf = (r: ChatRow): { tag?: string; tone?: 'cyan' | 'warn' } => {
    if (r.kind === 'meeting') return { tag: L('会议', 'Meeting') };
    if (r.kind === 'project') {
      const next = sideChats.find((c) => c.id === r.id)?.next;
      if (next && next.left != null && next.left <= 3) return { tag: next.left < 0 ? leftWords(next.left) : L(`${leftWords(next.left)}截止`, `due ${leftWords(next.left)}`), tone: 'warn' };
    }
    return {};
  };

  const header = (
    <View style={styles.head}>
      <T v="largeTitle" style={{ flex: 1, lineHeight: 38 }}>{L('对话', 'Chats')}</T>
      <RoundBtn label={L('搜索', 'Search')} onPress={toggleSearch} on={searchOpen}><Search size={19} color={t.ink} /></RoundBtn>
      <RoundBtn label={side === 'friends' ? L('新建：添加好友', 'New: add a friend') : L('新建', 'New')} onPress={plus} gold><Plus size={20} color={t.onGold} /></RoundBtn>
      {/* 设置（Me）：两边都在同一个位置，登录了显示账号首字母 */}
      <RoundBtn label={L('设置', 'Settings')} onPress={() => nav.navigate('Me')}>
        {acct.user ? <T v="caption" style={{ fontWeight: '700', fontSize: 13 }}>{initialsOf(acct.user)}</T> : <User size={19} color={t.ink} />}
      </RoundBtn>
    </View>
  );

  return (
    <Screen>
      {header}
      {friendsOn ? (
        <PillTabs<Side> value={side} onChange={(v) => { Keyboard.dismiss(); setSide(v); setQ(''); }} style={{ marginHorizontal: space.lg, marginTop: 14 }}
          options={[{ value: 'grava', label: agentName(), count: gravaN }, { value: 'friends', label: L('好友', 'Friends'), count: friendsN }]} />
      ) : null}
      {side === 'friends' && friendsOn ? <FriendsHome searchOpen={searchOpen} /> : (
        <>
          {searchOpen ? <View style={{ paddingHorizontal: space.lg, paddingTop: space.md }}><SearchBox value={q} onChange={setQ} placeholder={L('搜索 Agent 和对话', 'Search Agents and chats')} /></View> : null}
          {agents.length || !searching ? <AgentBar agents={agents} onOpen={openAgent} onMenu={agentMenu} onNew={() => nav.navigate('NewGroup')} /> : null}
          <ScrollView contentContainerStyle={{ paddingBottom: space.xxl }} refreshControl={<PullRefresh onRefresh={() => reload('chats', 'groups', 'sideChats', 'unread', 'inbox')} />}
            keyboardShouldPersistTaps="handled" keyboardDismissMode={Platform.OS === 'web' ? 'none' : 'on-drag'}>
            {!connected ? (
              <Pressable onPress={() => nav.navigate('Connect')} accessibilityRole="button" style={[styles.offline, { backgroundColor: t.warnSoft }]}>
                <T v="callout" color={t.warn} style={{ flex: 1 }}>{booting ? L('正在连接服务器…', 'Connecting to the server…') : L('未连接服务器，点击设置', 'Not connected to the server. Tap to set up.')}</T>
              </Pressable>
            ) : null}
            {!searching ? welcome : null}
            {rows.map((r, i) => {
              const tg = tagOf(r);
              const title = r.kind === 'main' ? agentName() : r.title;
              return (
                <SwipeRow key={r.id} actions={swipeActions(r)}>
                  <ChatRowView face={face(r)} title={title} tag={tg.tag} tagTone={tg.tone} pinned={r.pinned} muted={r.muted} time={rowTime(r.at)}
                    line={lineOf(r)} unread={r.unread} last={i === rows.length - 1 && !list.ended && !list.archived}
                    onPress={() => openRow(r)} onLongPress={() => rowMenu(r)}
                    hint={r.kind === 'main' ? undefined : L('左滑或长按可置顶、免打扰、删除', 'Swipe left or long-press to pin, mute or delete')} />
                </SwipeRow>
              );
            })}
            {searching ? (
              <Pressable onPress={() => nav.navigate('History', { thread: 'main', q: q.trim() })} accessibilityRole="button" style={({ pressed }) => [styles.foot, { opacity: pressed ? 0.6 : 1 }]}>
                <Search size={18} color={t.ink3} />
                <T v="callout" color={t.ink2} style={{ flex: 1 }} numberOfLines={1}>{L(`在聊天记录中搜索「${q.trim()}」`, `Search messages for "${q.trim()}"`)}</T>
              </Pressable>
            ) : null}
            {!searching && list.ended ? (
              <Pressable onPress={() => nav.navigate('ChatArchive', { kind: 'meeting' })} accessibilityRole="button" style={({ pressed }) => [styles.foot, { opacity: pressed ? 0.6 : 1 }]}>
                <Archive size={18} color={t.ink3} />
                <T v="callout" color={t.ink3} style={{ flex: 1 }}>{L(`已结束的会议 · ${list.ended}`, `Ended meetings · ${list.ended}`)}</T>
              </Pressable>
            ) : null}
            {!searching && list.archived ? (
              <Pressable onPress={() => nav.navigate('ChatArchive', { kind: 'project' })} accessibilityRole="button" style={({ pressed }) => [styles.foot, { opacity: pressed ? 0.6 : 1 }]}>
                <Archive size={18} color={t.ink3} />
                <T v="callout" color={t.ink3} style={{ flex: 1 }}>{L(`已归档的项目 · ${list.archived}`, `Archived projects · ${list.archived}`)}</T>
              </Pressable>
            ) : null}
          </ScrollView>
        </>
      )}
    </Screen>
  );
}

/** 主对话和项目的聊天页（路由 Chat，params：id、at + quote / focus 从别处跳来时带）。 */
export function ThreadScreen() {
  const nav = useNavigation<any>();
  const { id = 'main', at = 0, quote, focus } = (useRoute<any>().params ?? {}) as { id?: string; at?: number; quote?: ChatQuote; focus?: string };
  const { connected, booting, sideChats, avatar, sharedChannels, firstRun, send, claw } = useStore();
  const list = useChatList();
  const state = list.rows.find((r) => r.id === id)?.state ?? null;
  const side = sideChats.find((c) => c.id === id);
  const [welcomeOff, setWelcomeOff] = useState(welcomeHidden);
  useThreadOnScreen(id);
  const isMain = id === 'main';
  const welcome = isMain && connected && firstRun && !welcomeOff ? (
    <WelcomeCard onStart={() => send('main', L('我第一次用，带我走一遍。', "It's my first time here. Walk me through it."))}
      onDismiss={() => { hideWelcome(); setWelcomeOff(true); }} />
  ) : undefined;
  const sub = isMain
    ? (connected ? (sharedChannels.length ? L(`主对话 · 与 ${sharedChannels.join('、')} 共用`, `Main chat · shared with ${sharedChannels.join(', ')}`) : L('主对话', 'Main chat'))
      : booting ? L('正在连接…', 'Connecting…') : L('未连接，点击设置', 'Not connected. Tap to set up.'))
    : side ? `${side.archived ? L('已归档', 'Archived') : L('项目', 'Project')} · ${side.next ? leftWords(side.next.left) : side.goal || side.purpose || L('独立上下文', 'Its own context')}` : L('项目', 'Project');
  return (
    <Screen>
      <ChatHeader title={isMain ? agentName() : side?.title ?? L('项目', 'Project')} sub={sub} subTone={isMain && !connected && !booting ? 'warn' : undefined}
        onSub={isMain && !connected && !booting ? () => nav.navigate('Connect') : undefined}
        face={<StateFace size={36} state={state}>{isMain ? <LensAvatar size={36} config={avatar} /> : <ProjectFace size={36} archived={side?.archived} />}</StateFace>}
        onBack={() => nav.goBack()} onInfo={() => nav.navigate('Info', { kind: isMain ? 'main' : 'project', id })}
        onTalk={isMain && connected && Platform.OS !== 'web' && claw.kind === 'builtin' && supports('talk') ? () => nav.navigate('Talk', { thread: id }) : undefined} />
      {side ? <ProjectPanel key={`p-${id}`} id={id} /> : null}
      <ChatView key={id} threadId={id} quote={quote} quoteAt={quote ? at : 0} focus={focus} focusAt={focus ? at : 0}
        placeholder={side ? L(`给「${side.title}」发消息`, `Message "${side.title}"`) : L(`给 ${agentName()} 发消息`, `Message ${agentName()}`)}
        empty={side ? (side.archived ? L('已归档，今天暂无新消息。更早的对话见资料页的「聊天记录」。', 'Archived; nothing new today. Earlier messages are under Chat history on the details page.')
          : L('此项目今天暂无对话。Agent 每天会先读取上方的项目卡，再接续之前的进度。', 'Nothing here today yet. It reads the project card above first each day and picks up where it left off.'))
          : sharedChannels.length ? L(`主对话与 ${sharedChannels.join('、')} 共用同一会话，此处暂无从 app 发出的消息。`, `The main chat shares one session with ${sharedChannels.join(', ')}. No messages from the app here yet.`)
            : L('今天暂无对话。', 'Nothing here today yet.')}
        welcome={welcome} />
    </Screen>
  );
}

/** 已结束的会议 / 已归档的项目（列表底下的入口，params kind）。 */
export function ChatArchiveScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { kind } = (useRoute<any>().params ?? {}) as { kind?: 'meeting' | 'project' };
  const { avatar, sideChats, restoreProject, archiveSideChat, reload } = useStore();
  const members = useMembers();
  const { deleteRow } = useChatActions();
  const [fetched, setFetched] = useState<ChatRow[] | null>(null);
  const [err, setErr] = useState('');
  const server = supports('chats.list');
  const load = React.useCallback(() => (server
    ? chatsApi.archived().then((r) => { setFetched(r); setErr(''); }).catch((e) => setErr(e instanceof Error ? e.message : String(e)))
    : reload('sideChats').then(() => undefined)), [server, reload]);
  useEffect(() => { if (server) load(); }, [server, load, sideChats]);
  // 老服务器：只有已归档的项目，直接用 sideChats
  const rows = server ? fetched : sideChats.filter((c) => c.archived).sort((a, b) => b.updatedAt - a.updatedAt).map((c): ChatRow => ({
    kind: 'project', id: c.id, title: c.title, line: c.lastLine, lineRole: 'grava', at: new Date(c.updatedAt).toISOString(), unread: 0, pinned: false, muted: false, state: null }));
  const shown = (rows ?? []).filter((r) => (kind === 'project' ? r.kind === 'project' : roomKind(r)));
  const restore = (r: ChatRow) => restoreProject(r.id).catch(() => archiveSideChat(r.id, false)).then(() => { load(); reload('chats').catch(() => {}); })
    .catch((e) => showError(L('恢复失败', "Couldn't restore"), e));
  return (
    <Screen>
      <NavHeader title={kind === 'project' ? L('已归档的项目', 'Archived projects') : L('已结束的会议', 'Ended meetings')} onBack={() => nav.goBack()} />
      <ScrollView contentContainerStyle={{ paddingBottom: space.xxl }} refreshControl={<PullRefresh onRefresh={load} />}>
        {err ? <Card style={{ margin: space.lg }}><T v="callout" color={t.bad}>{L(`无法加载：${err}`, `Couldn't load: ${err}`)}</T></Card> : null}
        {rows && !shown.length ? <T v="callout" color={t.ink3} style={{ padding: space.lg }}>{kind === 'project' ? L('暂无已归档的项目。', 'No archived projects.') : L('暂无已结束的会议。', 'No ended meetings.')}</T> : null}
        {shown.map((r, i) => {
          const p = sideChats.find((c) => c.id === r.id);
          const acts: SwipeAction[] = [
            ...(r.kind === 'project' ? [{ key: 'restore', label: L('恢复', 'Restore'), tone: 'plain' as const, icon: <ArchiveRestore size={18} color={t.ink} />, onPress: () => restore(r) }] : []),
            { key: 'del', label: L('删除', 'Delete'), tone: 'danger', icon: <Trash2 size={18} color="#FFFFFF" />, onPress: () => deleteRow(r, load) },
          ];
          return (
            <SwipeRow key={r.id} actions={acts}>
              <ChatRowView face={r.kind === 'project' ? <ProjectFace archived /> : <RoomFace members={members(r.members)} avatar={avatar} />} title={r.title} time={rowTime(r.at)}
                line={(p?.closing ? L('正在撰写结论…', 'Writing the summary…') : p?.hasSummary ? L('已归档 · 有结论', 'Archived · with summary') : '') || r.line}
                last={i === shown.length - 1} hint={L('左滑可恢复或删除', 'Swipe left to restore or delete')}
                onPress={() => (r.kind === 'project' ? nav.navigate('Chat', { id: r.id }) : hasRoute('Meeting') ? nav.navigate('Meeting', { id: r.id }) : undefined)} />
            </SwipeRow>
          );
        })}
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  head: { flexDirection: 'row', alignItems: 'flex-end', gap: space.sm, paddingHorizontal: space.lg, paddingTop: 14 },
  round: { width: 38, height: 38, borderRadius: 19, alignItems: 'center', justifyContent: 'center', marginBottom: 2 },
  searchBox: { flexDirection: 'row', alignItems: 'center', gap: space.sm, borderRadius: radius.md, paddingHorizontal: space.md, height: 40 },
  bar: { gap: 4, paddingHorizontal: 10, paddingTop: 14, paddingBottom: 12 },
  barItem: { width: 64, alignItems: 'center', gap: 5 },
  newFace: { width: 46, height: 46, borderRadius: 23, borderWidth: 1, borderStyle: 'dashed', alignItems: 'center', justifyContent: 'center', margin: 4 },
  offline: { margin: space.lg, marginBottom: 0, padding: space.md, borderRadius: radius.md, flexDirection: 'row', alignItems: 'center' },
  foot: { flexDirection: 'row', alignItems: 'center', gap: 10, height: 52, paddingLeft: 20, paddingRight: 16 },
});
