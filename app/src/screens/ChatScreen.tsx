import React, { useState } from 'react';
import { agentName } from '../brand';
import { Alert, Keyboard, Platform, Pressable, ScrollView, StyleSheet, TextInput, View } from 'react-native';
import { useNavigation, useRoute } from '@react-navigation/native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { ChatView } from '../components/ChatView';
import { Archive, ArchiveRestore, CalendarDays, ChevronDown, ChevronRight, ClipboardList, Ellipsis, FolderKanban, LayoutGrid, Menu, Pencil, Plus, Settings, Trash2, User } from '../components/icons';
import { GroupBadge } from '../components/GroupIcon';
import { LensAvatar } from '../components/LensAvatar';
import { ModelSwitch } from '../components/ModelPicker';
import { ArchiveSheet, NewProjectSheet, ProjectPanel, leftWords, projectLine } from '../components/ProjectCard';
import { useSheet } from '../components/Sheet';
import { Btn, Card, CountPill, Pill, Screen, T } from '../components/ui';
import type { SideChat } from '../data/types';
import type { ChatQuote } from '../navigation';
import { L } from '../i18n';
import { useStore, useThreadOnScreen } from '../store';
import { radius, space, type, useTheme } from '../theme';
import { hideWelcome, welcomeHidden } from '../welcome';

/** 一个项目的"…"菜单：重命名 / 归档（先写结论）/ 恢复 / 删除。 */
function SideChatMenu({ chat, close, onDeleted, onArchive }: { chat: SideChat; close: () => void; onDeleted: () => void; onArchive: () => void }) {
  const t = useTheme();
  const { renameSideChat, restoreProject, archiveSideChat, deleteSideChat } = useStore();
  const [title, setTitle] = useState(chat.title);
  const [confirm, setConfirm] = useState(false);
  const run = (p: Promise<void>, after?: () => void) => p.then(() => { after?.(); close(); }).catch((e) => Alert.alert(L('没做成', "Couldn't do that"), e instanceof Error ? e.message : String(e)));
  return (
    <View style={{ gap: space.md }}>
      <View style={{ flexDirection: 'row', gap: space.sm, alignItems: 'center' }}>
        <TextInput value={title} onChangeText={setTitle} accessibilityLabel={L('项目名字', 'Project name')} style={[type.body, styles.input, { flex: 1, backgroundColor: t.surface, color: t.ink }]} />
        <Btn label={L('改名', 'Rename')} kind="quiet" icon={<Pencil size={14} color={t.ink} />} onPress={() => { if (title.trim()) run(renameSideChat(chat.id, title.trim())); }} />
      </View>
      {chat.archived
        ? <Btn label={L('恢复到侧栏', 'Restore to sidebar')} kind="quiet" icon={<ArchiveRestore size={16} color={t.ink} />}
            onPress={() => run(restoreProject(chat.id).catch(() => archiveSideChat(chat.id, false)))} />
        : <Btn label={L('归档…', 'Archive…')} kind="quiet" icon={<Archive size={16} color={t.ink} />} onPress={() => { close(); onArchive(); }} />}
      <T v="caption" color={t.ink3}>{L('归档：先让它写一份结论存进记忆，再从侧栏收进「已归档」。对话记录都在，可以恢复。', 'Archive: it first writes a summary into memory, then moves the project into "Archived". The conversation is kept and you can restore it.')}</T>
      {confirm
        ? <Btn label={L('确认删除对话记录', 'Confirm: delete the conversation')} kind="danger" icon={<Trash2 size={16} color={t.bad} />} onPress={() => run(deleteSideChat(chat.id), onDeleted)} />
        : <Btn label={L('删除', 'Delete')} kind="danger" icon={<Trash2 size={16} color={t.bad} />} onPress={() => setConfirm(true)} />}
      <T v="caption" color={t.ink3}>{L(`删除：app 里的记录和项目卡删掉（它的截止也从日程里拿掉），${agentName()} 那边的会话也删掉（OpenClaw 会压缩存档一份）。活动记录里留一行。`, `Delete: removes the conversation and the project card (its deadlines leave your schedule) and ${agentName()}'s session too (OpenClaw keeps a compressed archive copy). One line stays in Activity.`)}</T>
    </View>
  );
}

function DrawerRow({ icon, label, sub, subTone, on, onPress, right, unread = 0 }: { icon: React.ReactNode; label: string; sub?: string; subTone?: 'warn'; on?: boolean; onPress: () => void; right?: React.ReactNode; unread?: number }) {
  const t = useTheme();
  return (
    <Pressable onPress={onPress} accessibilityRole="button" accessibilityState={{ selected: !!on }}
      style={({ pressed }) => [styles.row, { backgroundColor: on ? t.goldSoft : 'transparent', opacity: pressed ? 0.7 : 1 }]}>
      <View style={{ width: 28, alignItems: 'center' }}>{icon}</View>
      <View style={{ flex: 1, gap: 1 }}>
        <T v="callout" numberOfLines={1} style={{ fontWeight: on || unread ? '600' : '400' }}>{label}</T>
        {sub ? <T v="caption" color={subTone === 'warn' ? t.warn : unread ? t.ink2 : t.ink3} numberOfLines={1}>{sub}</T> : null}
      </View>
      <CountPill n={unread} small />
      {right}
    </Pressable>
  );
}

/** 侧栏分区：点标题整体折叠；展开时最多显示 limit 条，其余收进"还有 N 个"。 */
function DrawerSection({ title, right, count, limit = 3, defaultOpen = true, empty, children }: {
  title: string; right?: React.ReactNode; count: number; limit?: number; defaultOpen?: boolean; empty?: string; children: React.ReactNode[];
}) {
  const t = useTheme();
  const [open, setOpen] = useState(defaultOpen);
  const [all, setAll] = useState(false);
  const rows = React.Children.toArray(children);
  const shown = all ? rows : rows.slice(0, limit);
  const hidden = rows.length - shown.length;
  return (
    <View>
      <View style={{ flexDirection: 'row', alignItems: 'center', paddingHorizontal: space.sm, marginTop: space.lg, marginBottom: 4, gap: 4 }}>
        <Pressable onPress={() => setOpen((v) => !v)} accessibilityRole="button" accessibilityState={{ expanded: open }} accessibilityLabel={L(`${open ? '折叠' : '展开'}${title}`, `${open ? 'Collapse' : 'Expand'} ${title}`)}
          style={{ flexDirection: 'row', alignItems: 'center', gap: 4, flex: 1, paddingVertical: 2 }}>
          {open ? <ChevronDown size={14} color={t.ink3} /> : <ChevronRight size={14} color={t.ink3} />}
          <T v="label" color={t.ink3}>{title}</T>
          {!open && count ? <T v="caption" color={t.ink3}>{count}</T> : null}
        </Pressable>
        {right}
      </View>
      {open ? (
        <>
          {shown}
          {!rows.length && empty ? <T v="caption" color={t.ink3} style={{ paddingHorizontal: space.md, paddingVertical: 6 }}>{empty}</T> : null}
          {hidden > 0 ? (
            <Pressable onPress={() => setAll(true)} accessibilityRole="button" style={({ pressed }) => [styles.row, { opacity: pressed ? 0.6 : 1 }]}>
              <View style={{ width: 28 }} />
              <T v="caption" color={t.gold}>{L(`还有 ${hidden} 个`, `${hidden} more`)}</T>
            </Pressable>
          ) : all && rows.length > limit ? (
            <Pressable onPress={() => setAll(false)} accessibilityRole="button" style={({ pressed }) => [styles.row, { opacity: pressed ? 0.6 : 1 }]}>
              <View style={{ width: 28 }} />
              <T v="caption" color={t.ink3}>{L('收起', 'Show less')}</T>
            </Pressable>
          ) : null}
        </>
      ) : null}
    </View>
  );
}

/** 左侧抽屉：主对话 / 项目 / Agents / 任务 / 已归档。手机上从这里进，Web 与 iPad 常驻。 */
function Drawer({ active, onPick, onClose }: { active: string; onPick: (id: string) => void; onClose: () => void }) {
  const t = useTheme();
  const nav = useNavigation<any>();
  const sheet = useSheet();
  const insets = useSafeAreaInsets();
  const { avatar, sideChats, groups, tasks, unread, claw } = useStore();
  const n = (id: string) => unread.threads[id]?.n ?? 0;
  // 按最近活动倒序：折叠掉的永远是最久没碰的。
  const byRecent = (a: SideChat, b: SideChat) => b.updatedAt - a.updatedAt;
  const live = sideChats.filter((c) => !c.archived).sort(byRecent);
  const archived = sideChats.filter((c) => c.archived).sort(byRecent);
  const running = tasks.filter((x) => x.status === '进行中').length;
  const pick = (id: string) => { onPick(id); onClose(); };
  const archive = (c: SideChat) => setTimeout(() => sheet.open({ title: L(`归档「${c.title}」`, `Archive "${c.title}"`), content: (close) => <ArchiveSheet id={c.id} title={c.title} close={close} /> }), 250);
  const menu = (c: SideChat) => sheet.open({ title: c.title, content: (close) => <SideChatMenu chat={c} close={close} onDeleted={() => { if (active === c.id) onPick('main'); }} onArchive={() => archive(c)} /> });
  return (
    <View style={StyleSheet.absoluteFill} accessibilityViewIsModal>
      <Pressable style={[StyleSheet.absoluteFill, { backgroundColor: 'rgba(0,0,0,0.45)' }]} onPress={onClose} accessibilityLabel={L('关闭侧栏', 'Close sidebar')} />
      <View style={[styles.drawer, { backgroundColor: t.bg, paddingTop: insets.top + space.sm, paddingBottom: insets.bottom + space.md }]}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingHorizontal: space.md, marginBottom: space.sm }}>
          <LensAvatar size={28} config={avatar} />
          <T v="headline" style={{ flex: 1 }}>{`${agentName()}`}</T>
        </View>
        <ScrollView showsVerticalScrollIndicator={false}>
          <DrawerRow icon={<LensAvatar size={20} config={avatar} />} label={L('主对话', 'Main chat')} sub={L('接待台，只放人话', 'Front desk, plain talk only')} on={active === 'main'} unread={n('main')} onPress={() => pick('main')} />

          <DrawerSection title={L('项目', 'Projects')} count={live.length} empty={L('持续几天、有截止的事，给它开一个。', 'Open one for anything with a goal and deadlines.')}
            right={<Pressable onPress={() => { onClose(); sheet.open({ title: L('开一个项目', 'New project'), content: (close) => <NewProjectSheet close={close} onCreated={onPick} /> }); }} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('开一个项目', 'New project')}><Plus size={16} color={t.gold} /></Pressable>}>
            {live.map((c) => (
              <DrawerRow key={c.id} icon={<FolderKanban size={18} color={active === c.id ? t.gold : t.cyan} />} label={c.title}
                sub={c.next !== undefined ? projectLine(c) : c.lastLine} subTone={c.next && c.next.left != null && c.next.left <= 3 ? 'warn' : undefined}
                on={active === c.id} unread={n(c.id)} onPress={() => pick(c.id)}
                right={<Pressable onPress={() => menu(c)} hitSlop={8} accessibilityRole="button" accessibilityLabel={L(`${c.title} 的更多操作`, `More actions for ${c.title}`)}><Ellipsis size={18} color={t.ink3} /></Pressable>} />
            ))}
          </DrawerSection>

          <DrawerSection title="Agents" count={groups.length}
            right={<Pressable onPress={() => { onClose(); nav.navigate('Tabs', { screen: 'Agents' }); }} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('全部 Agents', 'All agents')}><LayoutGrid size={15} color={t.gold} /></Pressable>}>
            {groups.map((g) => (
              <DrawerRow key={g.id} icon={<GroupBadge icon={g.icon} color={g.color} size={22} />} label={g.name} sub={g.lastLine} unread={n(g.id)} onPress={() => { onClose(); nav.navigate('Group', { id: g.id }); }} />
            ))}
          </DrawerSection>

          {claw.caps.tasks ? <DrawerSection title={L('任务', 'Tasks')} count={running}>
            {[<DrawerRow key="tasks" icon={<ClipboardList size={18} color={running ? t.cyan : t.ink3} />} label={running ? L(`${running} 个在跑`, `${running} running`) : L('任务', 'Tasks')} sub={L('派给谁、做到哪、怎么做的', "Who's on it, how far along, how it's done")} onPress={() => { onClose(); nav.navigate('Tasks'); }}
              right={running ? <Pill label={String(running)} tone="cyan" /> : undefined} />]}
          </DrawerSection> : null}

          <DrawerSection title={L('已归档', 'Archived')} count={archived.length} defaultOpen={false} empty={L('还没有归档的项目。', 'No archived projects yet.')}>
            {archived.map((c) => (
              <DrawerRow key={c.id} icon={<Archive size={16} color={t.ink3} />} label={c.title}
                sub={c.closing ? L('在写结论…', 'Writing the summary…') : c.hasSummary ? L('已归档 · 有结论', 'Archived · with summary') : L('项目 · 已归档', 'Project · Archived')} unread={n(c.id)} onPress={() => pick(c.id)}
                right={<Pressable onPress={() => menu(c)} hitSlop={8} accessibilityRole="button" accessibilityLabel={L(`${c.title} 的更多操作`, `More actions for ${c.title}`)}><Ellipsis size={18} color={t.ink3} /></Pressable>} />
            ))}
          </DrawerSection>
        </ScrollView>
        {/* 「我」（档案、记忆、日志、设置）：2026-09-27 从 tab 挪到这里，tab 让给「思考」 */}
        <Pressable onPress={() => { onClose(); nav.navigate('Me'); }} accessibilityRole="button" accessibilityLabel={L('打开「我」：档案、记忆、日志、设置', 'Open Me: profile, memory, journal, settings')}
          style={({ pressed }) => [styles.me, { borderTopColor: t.line, opacity: pressed ? 0.7 : 1 }]}>
          <View style={[styles.meIcon, { backgroundColor: t.surface2 }]}><User size={18} color={t.ink2} /></View>
          <View style={{ flex: 1, gap: 1 }}>
            <T v="callout" style={{ fontWeight: '600' }}>{L('我', 'Me')}</T>
            <T v="caption" color={t.ink3} numberOfLines={1}>{L('档案、记忆、日志、设置', 'Profile, memory, journal, settings')}</T>
          </View>
          <View style={[styles.meIcon, { backgroundColor: t.surface }]}><Settings size={18} color={t.ink2} /></View>
        </Pressable>
      </View>
    </View>
  );
}

/**
 * 新实例第一次打开、主对话还空着时的「从这里开始」（服务器说 first_run）。
 * 「带我走一遍」替你发一句开场白，主对话的 onboarding skill 接着一步一步带；「我自己来」这台设备上不再显示。发出任何一条它就没了。
 */
function WelcomeCard({ onStart, onDismiss }: { onStart: () => void; onDismiss: () => void }) {
  const t = useTheme();
  return (
    <Card style={{ gap: space.sm }}>
      <T v="headline">{L('从这里开始', 'Start here')}</T>
      <T v="callout" color={t.ink2}>{L('一开始是空的。说说你想让它管什么，它就给你建一个 Agent。', 'It starts empty. Tell it what you want looked after, and it builds an Agent for you.')}</T>
      <T v="callout" color={t.ink2}>{L('也可以先让它认识一下你。', 'It can also get to know you first.')}</T>
      {/* 上下排：英文的两个按钮并排放不下，手机字号调大也不会挤成两行 */}
      <View style={{ gap: space.sm, marginTop: space.xs }}>
        <Btn label={L('带我走一遍', 'Walk me through it')} onPress={onStart} />
        <Btn label={L('我自己来', "I'll explore myself")} kind="quiet" onPress={onDismiss} />
      </View>
    </Card>
  );
}

export function ChatScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { threadModel, setThreadModel, connected, booting, sideChats, tasks, avatar, sharedChannels, unread, firstRun, send } = useStore();
  // 「从这里开始」这台设备上收起来过没有（本机记着，见 welcome.ts）
  const [welcomeOff, setWelcomeOff] = useState(welcomeHidden);
  // 调试用：网页版 ?thread=<id> 直接打开某个线程，方便截图。
  // 当前线程：自己在侧栏选的，或者推送通知点进来带的导航参数（thread + at 时间戳；at 比上次选择新就以它为准）。
  const route = useRoute<any>();
  const wanted = route.params?.thread as string | undefined;
  const wantedAt = (route.params?.at as number | undefined) ?? 0;
  const [sel, setSel] = useState<{ id: string; at: number }>(() => ({ id: (Platform.OS === 'web' && typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('thread')) || 'main', at: 0 }));
  const active = wanted && wantedAt > sel.at ? wanted : sel.id;
  const setActive = (id: string) => setSel({ id, at: Math.max(sel.at, wantedAt) });
  // 调试用：网页版 ?drawer=1 打开时就展开侧栏，方便截图。
  const [open, setOpen] = useState(() => Platform.OS === 'web' && typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('drawer') === '1');
  // 从收件箱「去对话里说」过来：带着那件事的引用（只给跳转过来的那个对话）
  const quote = wanted && wantedAt > sel.at && active === wanted ? (route.params?.quote as ChatQuote | undefined) : undefined;
  // 点转交卡 / 「主对话转来」过来：滚到那一条闪一下
  const focus = wanted && wantedAt > sel.at && active === wanted ? (route.params?.focus as string | undefined) : undefined;
  const side = sideChats.find((c) => c.id === active);
  const running = tasks.filter((x) => x.status === '进行中').length;
  // 这个对话在屏幕上：不为它弹小窗，新消息直接算已读
  useThreadOnScreen(active);
  // 侧栏里别的对话有没看的：菜单按钮上也亮一个点
  const elsewhere = Object.entries(unread.threads).some(([tid, u]) => tid !== active && u.n > 0);
  // 新实例第一次打开：主对话空着时顶上放「从这里开始」。开场白按 app 的语言发，onboarding skill 认这两句
  const welcome = active === 'main' && connected && firstRun && !welcomeOff ? (
    <WelcomeCard onStart={() => send('main', L('我第一次用，带我走一遍。', "It's my first time here. Walk me through it."))}
      onDismiss={() => { hideWelcome(); setWelcomeOff(true); }} />
  ) : undefined;
  return (
    <Screen>
      <View style={[styles.head, { borderBottomColor: t.line }]}>
        <Pressable onPress={() => { Keyboard.dismiss(); setOpen(true); }} hitSlop={10} accessibilityRole="button" accessibilityLabel={L('打开侧栏', 'Open sidebar')} style={styles.menuBtn}>
          <Menu size={22} color={t.ink} />
          {running || elsewhere ? <View style={[styles.dot, { backgroundColor: t.cyan }]} /> : null}
        </Pressable>
        {side ? <View style={[styles.sideIcon, { backgroundColor: side.archived ? t.surface2 : t.cyanSoft }]}>{side.archived ? <Archive size={17} color={t.ink2} /> : <FolderKanban size={18} color={t.cyan} />}</View> : <LensAvatar size={34} config={avatar} />}
        <View style={{ flex: 1 }}>
          <T v="headline" numberOfLines={1}>{side ? side.title : `${agentName()}`}</T>
          {side
            ? <T v="caption" color={t.ink3} numberOfLines={1}>{side.archived ? L('已归档 · ', 'Archived · ') : L('项目 · ', 'Project · ')}{side.next ? leftWords(side.next.left) : side.goal || side.purpose || L('自己的上下文', 'Its own context')}</T>
            : <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                {connected ? <Pill label={sharedChannels.length ? L(`与 ${sharedChannels.join('、')} 共用主会话`, `Main session, shared with ${sharedChannels.join(', ')}`) : L('主会话', 'Main session')} tone="good" /> : <Pressable onPress={() => nav.navigate('Connect')} accessibilityRole="button" accessibilityLabel={L('设置服务器', 'Set up server')}><Pill label={booting ? L('正在连接…', 'Connecting…') : L('未连接服务器，点这里设置', 'Not connected, tap to set up')} tone="warn" /></Pressable>}
              </View>}
        </View>
        <Pressable onPress={() => nav.navigate('History', { thread: active })} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('历史与搜索', 'History and search')} style={styles.menuBtn}>
          <CalendarDays size={20} color={t.ink2} />
        </Pressable>
        <ModelSwitch value={threadModel[active] ?? threadModel.main} onChange={(id) => setThreadModel(active, id)} />
      </View>
      {side ? <ProjectPanel key={`p-${active}`} id={active} /> : null}
      <ChatView key={active} threadId={active} quote={quote} quoteAt={quote ? wantedAt : 0} focus={focus} focusAt={focus ? wantedAt : 0} placeholder={side ? L(`跟「${side.title}」说点什么`, `Message "${side.title}"`) : L(`跟 ${agentName()} 说点什么`, `Message ${agentName()}`)}
        empty={side ? (side.archived ? L('已归档，今天没有新消息。以前的对话在历史里（右上角的日历）。', 'Archived; nothing new today. Earlier messages are in History (calendar icon, top right).')
          : L('这个项目今天还没聊过。它每天会先看一遍上面的项目卡，接着昨天做。', 'Nothing here today yet. It reads the project card above first each day and picks up where it left off.'))
          : sharedChannels.length ? L(`主对话和 ${sharedChannels.join('、')} 共用同一个会话，这里还没有 app 发出的消息。`, `The main chat shares one session with ${sharedChannels.join(', ')}. No messages from the app here yet.`)
            : L('今天还没聊过。', 'Nothing here today yet.')}
        welcome={welcome} />
      {open ? <Drawer active={active} onPick={setActive} onClose={() => setOpen(false)} /> : null}
    </Screen>
  );
}

const styles = StyleSheet.create({
  head: { flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingHorizontal: space.md, paddingVertical: space.sm, borderBottomWidth: StyleSheet.hairlineWidth },
  menuBtn: { width: 36, height: 36, alignItems: 'center', justifyContent: 'center' },
  dot: { position: 'absolute', top: 7, right: 6, width: 7, height: 7, borderRadius: 4 },
  sideIcon: { width: 34, height: 34, borderRadius: 17, alignItems: 'center', justifyContent: 'center' },
  input: { borderRadius: radius.md, paddingHorizontal: space.lg, paddingVertical: 13 },
  drawer: { position: 'absolute', left: 0, top: 0, bottom: 0, width: 292, borderTopRightRadius: radius.lg + 4, borderBottomRightRadius: radius.lg + 4, paddingHorizontal: space.sm },
  row: { flexDirection: 'row', alignItems: 'center', gap: space.sm, borderRadius: radius.md, paddingHorizontal: space.sm, paddingVertical: 9 },
  me: { flexDirection: 'row', alignItems: 'center', gap: space.sm, borderTopWidth: StyleSheet.hairlineWidth, paddingHorizontal: space.sm, paddingTop: space.md, marginTop: space.sm },
  meIcon: { width: 36, height: 36, borderRadius: 18, alignItems: 'center', justifyContent: 'center' },
});
