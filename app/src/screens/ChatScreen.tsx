import React, { useState } from 'react';
import { agentName } from '../brand';
import { Alert, Platform, Pressable, ScrollView, StyleSheet, TextInput, View } from 'react-native';
import { useNavigation, useRoute } from '@react-navigation/native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { ChatView } from '../components/ChatView';
import { Archive, ArchiveRestore, CalendarDays, ChevronDown, ChevronRight, ClipboardList, Ellipsis, LayoutGrid, Menu, MessagesSquare, Pencil, Plus, Trash2 } from '../components/icons';
import { GroupBadge } from '../components/GroupIcon';
import { LensAvatar } from '../components/LensAvatar';
import { ModelField, ModelSwitch } from '../components/ModelPicker';
import { useSheet } from '../components/Sheet';
import { Btn, Pill, Screen, T } from '../components/ui';
import type { SideChat } from '../data/types';
import { useStore } from '../store';
import { radius, space, type, useTheme } from '../theme';

/** 新建独立空间的弹层。 */
function NewSideChat({ close, onCreated }: { close: () => void; onCreated: (id: string) => void }) {
  const t = useTheme();
  const { createSideChat, connected } = useStore();
  const [title, setTitle] = useState('');
  const [purpose, setPurpose] = useState('');
  const [modelId, setModelId] = useState('anthropic/claude-opus-5-5');
  const [busy, setBusy] = useState(false);
  const create = () => {
    if (!title.trim() || busy) return;
    if (!connected) { Alert.alert('没连上服务器', '到「我 → 服务器」检查地址和令牌'); return; }
    setBusy(true);
    createSideChat({ title: title.trim(), purpose: purpose.trim(), modelId })
      .then((id) => { onCreated(id); close(); })
      .catch((e) => Alert.alert('没开成', e instanceof Error ? e.message : String(e)))
      .finally(() => setBusy(false));
  };
  return (
    <View style={{ gap: space.md }}>
      <T v="callout" color={t.ink2}>会持续几天到几个月的事，给它一个自己的空间。它有独立的上下文，主对话不用背着这些；做完可以归档。</T>
      <TextInput value={title} onChangeText={setTitle} placeholder="叫什么，比如：搬家" placeholderTextColor={t.ink3} accessibilityLabel="空间名字"
        style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink }]} />
      <TextInput value={purpose} onChangeText={setPurpose} multiline placeholder="只管什么事，一两句" placeholderTextColor={t.ink3} accessibilityLabel="空间职责"
        style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink, minHeight: 72, textAlignVertical: 'top' }]} />
      <ModelField value={modelId} onChange={setModelId} />
      <Btn label={busy ? '正在开…' : '开这个空间'} onPress={create} />
    </View>
  );
}

/** 一个空间的"…"菜单：重命名 / 归档 / 删除。 */
function SideChatMenu({ chat, close, onDeleted }: { chat: SideChat; close: () => void; onDeleted: () => void }) {
  const t = useTheme();
  const { renameSideChat, archiveSideChat, deleteSideChat } = useStore();
  const [title, setTitle] = useState(chat.title);
  const [confirm, setConfirm] = useState(false);
  const run = (p: Promise<void>, after?: () => void) => p.then(() => { after?.(); close(); }).catch((e) => Alert.alert('没做成', e instanceof Error ? e.message : String(e)));
  return (
    <View style={{ gap: space.md }}>
      <View style={{ flexDirection: 'row', gap: space.sm, alignItems: 'center' }}>
        <TextInput value={title} onChangeText={setTitle} accessibilityLabel="空间名字" style={[type.body, styles.input, { flex: 1, backgroundColor: t.surface, color: t.ink }]} />
        <Btn label="改名" kind="quiet" icon={<Pencil size={14} color={t.ink} />} onPress={() => { if (title.trim()) run(renameSideChat(chat.id, title.trim())); }} />
      </View>
      {chat.archived
        ? <Btn label="恢复到侧栏" kind="quiet" icon={<ArchiveRestore size={16} color={t.ink} />} onPress={() => run(archiveSideChat(chat.id, false))} />
        : <Btn label="归档" kind="quiet" icon={<Archive size={16} color={t.ink} />} onPress={() => run(archiveSideChat(chat.id, true))} />}
      <T v="caption" color={t.ink3}>归档：从侧栏收进「已归档」，对话记录都在，可以恢复。（把结论沉淀进记忆还没做。）</T>
      {confirm
        ? <Btn label="确认删除对话记录" kind="danger" icon={<Trash2 size={16} color={t.bad} />} onPress={() => run(deleteSideChat(chat.id), onDeleted)} />
        : <Btn label="删除" kind="danger" icon={<Trash2 size={16} color={t.bad} />} onPress={() => setConfirm(true)} />}
      <T v="caption" color={t.ink3}>{`删除：app 里的记录删掉，${agentName()} 那边的会话也删掉（OpenClaw 会压缩存档一份）。活动记录里留一行。`}</T>
    </View>
  );
}

function DrawerRow({ icon, label, sub, on, onPress, right }: { icon: React.ReactNode; label: string; sub?: string; on?: boolean; onPress: () => void; right?: React.ReactNode }) {
  const t = useTheme();
  return (
    <Pressable onPress={onPress} accessibilityRole="button" accessibilityState={{ selected: !!on }}
      style={({ pressed }) => [styles.row, { backgroundColor: on ? t.goldSoft : 'transparent', opacity: pressed ? 0.7 : 1 }]}>
      <View style={{ width: 28, alignItems: 'center' }}>{icon}</View>
      <View style={{ flex: 1, gap: 1 }}>
        <T v="callout" numberOfLines={1} style={{ fontWeight: on ? '600' : '400' }}>{label}</T>
        {sub ? <T v="caption" color={t.ink3} numberOfLines={1}>{sub}</T> : null}
      </View>
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
        <Pressable onPress={() => setOpen((v) => !v)} accessibilityRole="button" accessibilityState={{ expanded: open }} accessibilityLabel={`${open ? '折叠' : '展开'}${title}`}
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
              <T v="caption" color={t.gold}>还有 {hidden} 个</T>
            </Pressable>
          ) : all && rows.length > limit ? (
            <Pressable onPress={() => setAll(false)} accessibilityRole="button" style={({ pressed }) => [styles.row, { opacity: pressed ? 0.6 : 1 }]}>
              <View style={{ width: 28 }} />
              <T v="caption" color={t.ink3}>收起</T>
            </Pressable>
          ) : null}
        </>
      ) : null}
    </View>
  );
}

/** 左侧抽屉：主对话 / 独立空间 / Agents / 任务 / 已归档。手机上从这里进，Web 与 iPad 常驻。 */
function Drawer({ active, onPick, onClose }: { active: string; onPick: (id: string) => void; onClose: () => void }) {
  const t = useTheme();
  const nav = useNavigation<any>();
  const sheet = useSheet();
  const insets = useSafeAreaInsets();
  const { avatar, sideChats, groups, tasks } = useStore();
  // 按最近活动倒序：折叠掉的永远是最久没碰的。
  const byRecent = (a: SideChat, b: SideChat) => b.updatedAt - a.updatedAt;
  const live = sideChats.filter((c) => !c.archived).sort(byRecent);
  const archived = sideChats.filter((c) => c.archived).sort(byRecent);
  const running = tasks.filter((x) => x.status === '进行中').length;
  const pick = (id: string) => { onPick(id); onClose(); };
  const menu = (c: SideChat) => sheet.open({ title: c.title, content: (close) => <SideChatMenu chat={c} close={close} onDeleted={() => { if (active === c.id) onPick('main'); }} /> });
  return (
    <View style={StyleSheet.absoluteFill} accessibilityViewIsModal>
      <Pressable style={[StyleSheet.absoluteFill, { backgroundColor: 'rgba(0,0,0,0.45)' }]} onPress={onClose} accessibilityLabel="关闭侧栏" />
      <View style={[styles.drawer, { backgroundColor: t.bg, paddingTop: insets.top + space.sm, paddingBottom: insets.bottom + space.md }]}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingHorizontal: space.md, marginBottom: space.sm }}>
          <LensAvatar size={28} config={avatar} />
          <T v="headline" style={{ flex: 1 }}>{`${agentName()}`}</T>
        </View>
        <ScrollView showsVerticalScrollIndicator={false}>
          <DrawerRow icon={<LensAvatar size={20} config={avatar} />} label="主对话" sub="接待台，只放人话" on={active === 'main'} onPress={() => pick('main')} />

          <DrawerSection title="独立空间" count={live.length} empty="会持续几天的事，给它开一个。"
            right={<Pressable onPress={() => { onClose(); sheet.open({ title: '开一个独立空间', content: (close) => <NewSideChat close={close} onCreated={onPick} /> }); }} hitSlop={8} accessibilityRole="button" accessibilityLabel="开一个独立空间"><Plus size={16} color={t.gold} /></Pressable>}>
            {live.map((c) => (
              <DrawerRow key={c.id} icon={<MessagesSquare size={18} color={active === c.id ? t.gold : t.cyan} />} label={c.title} sub={c.lastLine} on={active === c.id} onPress={() => pick(c.id)}
                right={<Pressable onPress={() => menu(c)} hitSlop={8} accessibilityRole="button" accessibilityLabel={`${c.title} 的更多操作`}><Ellipsis size={18} color={t.ink3} /></Pressable>} />
            ))}
          </DrawerSection>

          <DrawerSection title="Agents" count={groups.length}
            right={<Pressable onPress={() => { onClose(); nav.navigate('Tabs', { screen: 'Agents' }); }} hitSlop={8} accessibilityRole="button" accessibilityLabel="全部 Agents"><LayoutGrid size={15} color={t.gold} /></Pressable>}>
            {groups.map((g) => (
              <DrawerRow key={g.id} icon={<GroupBadge icon={g.icon} size={22} />} label={g.name} sub={g.lastLine} onPress={() => { onClose(); nav.navigate('Group', { id: g.id }); }} />
            ))}
          </DrawerSection>

          <DrawerSection title="任务" count={running}>
            {[<DrawerRow key="tasks" icon={<ClipboardList size={18} color={running ? t.cyan : t.ink3} />} label={running ? `${running} 个在跑` : '任务'} sub="派给谁、做到哪、怎么做的" onPress={() => { onClose(); nav.navigate('Tasks'); }}
              right={running ? <Pill label={String(running)} tone="cyan" /> : undefined} />]}
          </DrawerSection>

          <DrawerSection title="已归档" count={archived.length} defaultOpen={false} empty="还没有归档的空间。">
            {archived.map((c) => (
              <DrawerRow key={c.id} icon={<Archive size={16} color={t.ink3} />} label={c.title} sub="独立空间 · 已归档" onPress={() => pick(c.id)}
                right={<Pressable onPress={() => menu(c)} hitSlop={8} accessibilityRole="button" accessibilityLabel={`${c.title} 的更多操作`}><Ellipsis size={18} color={t.ink3} /></Pressable>} />
            ))}
          </DrawerSection>
        </ScrollView>
      </View>
    </View>
  );
}

export function ChatScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { threadModel, setThreadModel, connected, booting, sideChats, tasks, avatar, sharedChannels } = useStore();
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
  const side = sideChats.find((c) => c.id === active);
  const running = tasks.filter((x) => x.status === '进行中').length;
  return (
    <Screen>
      <View style={[styles.head, { borderBottomColor: t.line }]}>
        <Pressable onPress={() => setOpen(true)} hitSlop={10} accessibilityRole="button" accessibilityLabel="打开侧栏" style={styles.menuBtn}>
          <Menu size={22} color={t.ink} />
          {running ? <View style={[styles.dot, { backgroundColor: t.cyan }]} /> : null}
        </Pressable>
        {side ? <View style={[styles.sideIcon, { backgroundColor: t.cyanSoft }]}><MessagesSquare size={18} color={t.cyan} /></View> : <LensAvatar size={34} config={avatar} />}
        <View style={{ flex: 1 }}>
          <T v="headline" numberOfLines={1}>{side ? side.title : `${agentName()}`}</T>
          {side
            ? <T v="caption" color={t.ink3} numberOfLines={1}>{side.archived ? '已归档 · ' : '独立空间 · '}{side.purpose || '自己的上下文'}</T>
            : <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                {connected ? <Pill label={sharedChannels.length ? `与 ${sharedChannels.join('、')} 共用主会话` : '主会话'} tone="good" /> : <Pressable onPress={() => nav.navigate('Connect')} accessibilityRole="button" accessibilityLabel="设置服务器"><Pill label={booting ? '正在连接…' : '未连接服务器，点这里设置'} tone="warn" /></Pressable>}
              </View>}
        </View>
        <Pressable onPress={() => nav.navigate('History', { thread: active })} hitSlop={8} accessibilityRole="button" accessibilityLabel="历史与搜索" style={styles.menuBtn}>
          <CalendarDays size={20} color={t.ink2} />
        </Pressable>
        <ModelSwitch value={threadModel[active] ?? threadModel.main} onChange={(id) => setThreadModel(active, id)} />
      </View>
      <ChatView key={active} threadId={active} placeholder={side ? `跟「${side.title}」说点什么` : `跟 ${agentName()} 说点什么`}
        empty={side ? (side.purpose ? `这个空间只管：${side.purpose}` : '新空间，说点什么开始吧。') : '主对话和 Telegram 共用同一个会话，这里还没有 app 发出的消息。'} />
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
});
