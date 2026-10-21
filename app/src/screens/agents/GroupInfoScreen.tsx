// 群资料（主线第三步第二段）：路由 GroupInfo（params id）。群名、议题（点了改）；成员（点了进赛博资料页 / 功能 Agent 的对话，「添加或移除成员」）；
// 主持（助手 / 群里的一位赛博 / 不设）、没 @ 时谁说（仅被 @ 时 / 主持人安排）；群记忆（定了几条、还在争几条、各自记下几条）；从哪来（想法 / 播客）；
// 今天的用量（服务器按群按天记，没报用量的按字数估，标「约」）；结束 / 重新开启、删除，各有一张写明后果的弹层。
import React, { useCallback, useEffect, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, TextInput, View } from 'react-native';
import { useFocusEffect, useNavigation, useRoute } from '@react-navigation/native';
import { agentName } from '../../brand';
import { groupsApi, type Candidates, type Group, type GroupMode } from '../../api/groups';
import * as pod from '../../api/podcast';
import { GroupFaces, MemberFace, roleLine } from '../../components/agents/GroupBits';
import { ArchiveRestore, Check, ChevronRight, CircleStop, Pencil, Trash2, UsersRound } from '../../components/icons';
import { useSheet } from '../../components/Sheet';
import { Btn, Card, NavHeader, PullRefresh, Screen, SectionLabel, Segmented, T, showError } from '../../components/ui';
import { L } from '../../i18n';
import { useStore } from '../../store';
import { space, type, useTheme } from '../../theme';
import { openEpisode } from '../../think/Podcast';

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** 改一行字（群名、议题）：输入框 + 保存。 */
function TextSheet({ initial, label, multiline, max, close, onSave }: { initial: string; label: string; multiline?: boolean; max: number; close: () => void; onSave: (v: string) => Promise<void> }) {
  const t = useTheme();
  const [v, setV] = useState(initial);
  const [busy, setBusy] = useState(false);
  return (
    <View style={{ gap: space.md }}>
      <TextInput value={v} onChangeText={setV} multiline={multiline} maxLength={max} autoFocus accessibilityLabel={label}
        style={[type.body, styles.input, multiline && { minHeight: 80, textAlignVertical: 'top' }, { backgroundColor: t.surface, color: t.ink }]} />
      <Btn label={busy ? L('正在保存…', 'Saving…') : L('保存', 'Save')} onPress={() => {
        if (busy) return;
        setBusy(true);
        onSave(v.trim()).then(close).catch((e) => showError(L('保存失败', "Couldn't save"), e)).finally(() => setBusy(false));
      }} />
    </View>
  );
}

/** 添加或移除成员：和新建时一样的规矩（健康类 Agent 不和赛博同群）；新加的从现在起进群，以前的只读小结。 */
function MembersSheet({ group, close, onSave }: { group: Group; close: () => void; onSave: (members: string[]) => Promise<void> }) {
  const t = useTheme();
  const [c, setC] = useState<Candidates | null>(null);
  const [picked, setPicked] = useState<string[]>(group.members.filter((m) => !m.gone).map((m) => m.id));
  const [busy, setBusy] = useState(false);
  useEffect(() => { groupsApi.candidates().then(setC).catch((e) => showError(L('无法加载', "Couldn't load"), e)); }, []);
  const cyberIds = new Set((c?.cybers ?? []).map((x) => x.id));
  const healthIds = new Set((c?.agents ?? []).filter((a) => a.health).map((a) => a.id));
  const withCyber = picked.some((id) => cyberIds.has(id)) || group.host?.kind === 'cyber';
  const withHealth = picked.some((id) => healthIds.has(id));
  const all = [...(c?.cybers ?? []), ...(c?.agents ?? [])];
  const toggle = (id: string) => setPicked((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));
  if (!c) return <T v="callout" color={t.ink3}>{L('正在加载…', 'Loading…')}</T>;
  return (
    <View style={{ gap: space.sm }}>
      <ScrollView style={{ maxHeight: 380 }}>
        {all.map((m) => {
          const on = picked.includes(m.id);
          const off = !on && ((m.kind === 'cyber' && withHealth) || (healthIds.has(m.id) && withCyber));
          return (
            <Pressable key={m.id} onPress={() => toggle(m.id)} disabled={off} accessibilityRole="checkbox" accessibilityState={{ checked: on, disabled: off }}
              style={({ pressed }) => [styles.memberRow, { opacity: off ? 0.45 : pressed ? 0.7 : 1 }]}>
              <MemberFace m={m} size={32} />
              <View style={{ flex: 1, minWidth: 0, gap: 1 }}>
                <T v="body" numberOfLines={1}>{m.name}</T>
                <T v="caption" color={t.ink3} numberOfLines={1}>{off ? (m.kind === 'cyber' ? L('群里有健康类 Agent', 'A health Agent is in this group') : L('管健康、饮食或睡眠：不和赛博同群', 'Health, diet or sleep: not with Cybers')) : roleLine(m)}</T>
              </View>
              {on ? <View style={[styles.check, { backgroundColor: t.cyan }]}><Check size={14} color={t.bg} strokeWidth={3} /></View> : <View style={[styles.uncheck, { borderColor: t.ink3 }]} />}
            </Pressable>
          );
        })}
      </ScrollView>
      <T v="caption" color={t.ink3}>{L('新加的成员从现在起进群，之前的只读到小结。', 'New members join from now on; they read the summaries of what came before.')}</T>
      <Btn label={busy ? L('正在保存…', 'Saving…') : L(`保存 · ${picked.length} 位`, `Save · ${picked.length}`)} onPress={() => {
        if (busy || picked.length < c.min || picked.length > c.max) return;
        setBusy(true);
        onSave(picked).then(close).catch((e) => showError(L('保存失败', "Couldn't save"), e)).finally(() => setBusy(false));
      }} />
    </View>
  );
}

export function GroupInfoScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const sheet = useSheet();
  const { id } = useRoute<any>().params as { id: string };
  const { reload } = useStore();
  const [g, setG] = useState<Group | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => groupsApi.get(id).then((r) => { setG(r.group); setError(null); }).catch((e) => setError(errText(e))), [id]);
  useFocusEffect(useCallback(() => { load(); }, [load]));
  if (!g) {
    return (
      <Screen>
        <NavHeader title={L('群资料', 'Group info')} onBack={() => nav.goBack()} />
        <T v="callout" color={error ? t.bad : t.ink3} style={{ padding: space.xl, textAlign: 'center' }}>{error ? L(`无法加载：${error}`, `Couldn't load: ${error}`) : L('正在加载…', 'Loading…')}</T>
      </Screen>
    );
  }
  const patch = async (b: Parameters<typeof groupsApi.patch>[1]) => {
    const next = await groupsApi.patch(id, b);
    setG(next);
    reload('chats').catch(() => {});
  };
  const save = (b: Parameters<typeof groupsApi.patch>[1]) => { patch(b).catch((e) => showError(L('保存失败', "Couldn't save"), e)); };
  const rename = () => sheet.open({ title: L('群名', 'Name'), content: (close) => <TextSheet initial={g.title} label={L('群名', 'Name')} max={60} close={close} onSave={(v) => patch({ title: v })} /> });
  const editTopic = () => sheet.open({ title: L('议题', 'Topic'), content: (close) => <TextSheet initial={g.topic} label={L('议题', 'Topic')} multiline max={400} close={close} onSave={(v) => patch({ topic: v })} /> });
  const editMembers = () => sheet.open({ title: L('添加或移除成员', 'Add or remove members'), content: (close) => <MembersSheet group={g} close={close} onSave={(members) => patch({ members })} /> });
  const cyberMembers = g.members.filter((m) => m.kind === 'cyber' && !m.gone);
  const pickHost = () => sheet.open({
    title: L('主持', 'Host'),
    sub: L('主持人每轮写小结，「主持人安排」时也由它请人接着说', 'The host writes each round’s summary and, with “Host decides”, picks who speaks next'),
    content: (close) => (
      <View style={{ gap: 2 }}>
        {[{ id: 'main', name: agentName(), main: true, m: null }, ...cyberMembers.map((m) => ({ id: m.id, name: m.name, main: false, m }))].map((h) => (
          <Pressable key={h.id} accessibilityRole="button" onPress={() => { close(); save({ host: h.id }); }} style={({ pressed }) => [styles.memberRow, { opacity: pressed ? 0.6 : 1 }]}>
            <MemberFace m={h.m} main={h.main} size={32} />
            <T v="body" style={{ flex: 1 }}>{h.name}</T>
            {g.host?.id === h.id ? <Check size={16} color={t.cyan} /> : null}
          </Pressable>
        ))}
        <Pressable accessibilityRole="button" onPress={() => { close(); save({ host: '' }); }} style={({ pressed }) => [styles.memberRow, { opacity: pressed ? 0.6 : 1 }]}>
          <View style={[styles.none, { borderColor: t.ink3 }]} />
          <View style={{ flex: 1, gap: 1 }}>
            <T v="body">{L('不设主持', 'No host')}</T>
            <T v="caption" color={t.ink3}>{L('没有小结；只有被 @ 的说', 'No summaries; only whoever you @ speaks')}</T>
          </View>
          {!g.host ? <Check size={16} color={t.cyan} /> : null}
        </Pressable>
      </View>
    ),
  });
  const end = () => sheet.open({
    title: L('结束群聊', 'End the group chat'),
    content: (close) => (
      <View style={{ gap: space.md }}>
        <T v="callout" color={t.ink2}>
          {g.source?.kind === 'idea'
            ? L('群聊归入「已结束」，记录和群记忆保留；这条想法标为已想完。主对话里会留一行和最近的小结。', 'The group moves to Ended; the record and its memory stay, and the thought is marked as done. The main chat gets one line with the latest summary.')
            : L('群聊归入「已结束」，记录和群记忆保留。主对话里会留一行和最近的小结。', 'The group moves to Ended; the record and its memory stay. The main chat gets one line with the latest summary.')}
        </T>
        <Btn label={L('结束', 'End')} kind="danger" icon={<CircleStop size={16} color={t.bad} />}
          onPress={() => { close(); groupsApi.end(id).then((x) => { setG(x); reload('chats', 'unread').catch(() => {}); }).catch((e) => showError(L('操作失败', "Couldn't complete the action"), e)); }} />
      </View>
    ),
  });
  const reopen = () => { groupsApi.reopen(id).then((x) => { setG(x); reload('chats').catch(() => {}); }).catch((e) => showError(L('操作失败', "Couldn't complete the action"), e)); };
  const remove = () => sheet.open({
    title: L('删除群聊', 'Delete the group chat'),
    content: (close) => (
      <View style={{ gap: space.md }}>
        <T v="callout" color={t.ink2}>{L('删除群聊记录和群记忆。成员不受影响：赛博记下的、你在收件箱里点过的都留着。', "Deletes the group's messages and memory. Members aren't affected: what the Cybers kept and what you approved in the inbox stay.")}</T>
        <Btn label={L('删除', 'Delete')} kind="danger" icon={<Trash2 size={16} color={t.bad} />} onPress={() => {
          close();
          groupsApi.remove(id).then(() => { reload('chats', 'unread').catch(() => {}); nav.pop(2); }).catch((e) => showError(L('删除失败', "Couldn't delete"), e));
        }} />
      </View>
    ),
  });
  const openSource = () => {
    if (g.source?.kind === 'podcast') pod.get(g.source.id).then((e) => openEpisode(nav, e)).catch((e) => showError(L('无法打开', "Couldn't open"), e));
  };
  const ended = g.status === 'ended';
  const mem = g.memory;
  return (
    <Screen>
      <NavHeader title={L('群资料', 'Group info')} onBack={() => nav.goBack()} />
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl }} refreshControl={<PullRefresh onRefresh={load} />}>
        <View style={{ alignItems: 'center', gap: space.sm, paddingVertical: space.md }}>
          <GroupFaces members={g.members} size={64} />
          <Pressable onPress={rename} accessibilityRole="button" accessibilityLabel={L(`群名：${g.title}，点按修改`, `Name: ${g.title}. Tap to change`)} style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
            <T v="title" style={{ textAlign: 'center' }}>{g.title}</T>
            <Pencil size={15} color={t.ink3} />
          </Pressable>
          <T v="caption" color={t.ink3}>{ended ? L('群聊 · 已结束', 'Group chat · Ended') : L(`群聊 · 第 ${g.round} 轮`, `Group chat · Round ${g.round}`)}</T>
        </View>

        <SectionLabel>{L('议题', 'Topic')}</SectionLabel>
        <Pressable onPress={editTopic} accessibilityRole="button">
          <Card style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
            <T v="body" color={g.topic ? t.ink : t.ink3} style={{ flex: 1 }}>{g.topic || L('没有议题 · 点按填写', 'No topic · Tap to add one')}</T>
            <ChevronRight size={16} color={t.ink3} />
          </Card>
        </Pressable>

        <SectionLabel>{L(`成员 · ${g.members.length}`, `Members · ${g.members.length}`)}</SectionLabel>
        <Card style={{ paddingVertical: space.xs }}>
          {g.members.map((m) => (
            <Pressable key={m.id} disabled={m.gone} onPress={() => (m.kind === 'cyber' ? nav.navigate('CyberProfile', { id: m.id }) : nav.navigate('Group', { id: m.id }))}
              accessibilityRole="button" style={({ pressed }) => [styles.memberRow, { opacity: pressed ? 0.6 : 1 }]}>
              <MemberFace m={m} size={32} />
              <View style={{ flex: 1, minWidth: 0, gap: 1 }}>
                <T v="body" numberOfLines={1}>{m.name}</T>
                <T v="caption" color={t.ink3} numberOfLines={1}>{m.gone ? L('已删除', 'Deleted') : [roleLine(m), m.model].filter(Boolean).join(' · ')}</T>
              </View>
              {m.gone ? null : <ChevronRight size={16} color={t.ink3} />}
            </Pressable>
          ))}
          {ended ? null : (
            <Pressable onPress={editMembers} accessibilityRole="button" style={({ pressed }) => [styles.memberRow, { opacity: pressed ? 0.6 : 1 }]}>
              <View style={[styles.addFace, { backgroundColor: t.surface2 }]}><UsersRound size={16} color={t.ink2} /></View>
              <T v="body" color={t.cyan} style={{ flex: 1 }}>{L('添加或移除成员', 'Add or remove members')}</T>
            </Pressable>
          )}
        </Card>

        <SectionLabel>{L('主持和发言', 'Host and turns')}</SectionLabel>
        <Card style={{ gap: space.md }}>
          <Pressable onPress={ended ? undefined : pickHost} accessibilityRole="button" style={{ flexDirection: 'row', alignItems: 'center', gap: space.md }}>
            <MemberFace m={g.host?.kind === 'cyber' ? { kind: 'cyber', avatar: g.host.avatar ?? null, icon: null, color: null } : null} main={g.host?.kind === 'main'} size={32} />
            <View style={{ flex: 1, gap: 1 }}>
              <T v="body">{g.host ? L(`${g.host.name} 主持`, `Hosted by ${g.host.name}`) : L('不设主持', 'No host')}</T>
              <T v="caption" color={t.ink3}>{g.host ? L('每轮写小结，记进群记忆', 'Writes each round’s summary into the group’s memory') : L('没有小结', 'No summaries')}</T>
            </View>
            {ended ? null : <ChevronRight size={16} color={t.ink3} />}
          </Pressable>
          <View style={{ gap: 6 }}>
            <T v="caption" color={t.ink3}>{L('没 @ 谁的时候', 'When you don’t @ anyone')}</T>
            <Segmented<GroupMode> value={g.mode} onChange={(v) => { if (!ended && v !== g.mode) save({ mode: v }); }}
              options={[{ value: 'mention', label: L('仅被 @ 时说', 'Only when @-mentioned') }, { value: 'host', label: L('主持人安排', 'Host decides') }]} />
            {!g.host && g.mode === 'mention' ? <T v="caption" color={t.ink3}>{L('「主持人安排」要先设一位主持人。', '“Host decides” needs a host first.')}</T> : null}
            <T v="caption" color={t.ink3}>{L('@所有 Agent：每位成员各说一次。', '@All Agents: each member speaks once.')}</T>
          </View>
        </Card>

        <SectionLabel>{L('群记忆', "Group's memory")}</SectionLabel>
        <Pressable onPress={() => nav.navigate('GroupMemory', { id })} accessibilityRole="button">
          <Card style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
            <View style={{ flex: 1, gap: 2 }}>
              <T v="body">{L(`定了 ${mem.decided} · 还在争 ${mem.disputed} · 各自记下 ${mem.note}`, `Decided ${mem.decided} · Disputed ${mem.disputed} · Kept ${mem.note}`)}</T>
              {g.pinned ? <T v="caption" color={t.ink3} numberOfLines={2}>{L(`最近一次小结：${g.pinned.line}`, `Latest summary: ${g.pinned.line}`)}</T> : null}
            </View>
            <ChevronRight size={16} color={t.ink3} />
          </Card>
        </Pressable>

        {g.source ? (
          <>
            <SectionLabel>{L('来处', 'Comes from')}</SectionLabel>
            <Pressable onPress={openSource} disabled={g.source.kind !== 'podcast'} accessibilityRole={g.source.kind === 'podcast' ? 'button' : undefined}>
              <Card style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
                <T v="body" style={{ flex: 1 }}>{g.source.kind === 'podcast' ? L(`播客「${g.source.title ?? ''}」`, `Episode "${g.source.title ?? ''}"`) : L(`想法「${g.source.title ?? ''}」`, `Thought "${g.source.title ?? ''}"`)}</T>
                {g.source.kind === 'podcast' ? <ChevronRight size={16} color={t.ink3} /> : null}
              </Card>
            </Pressable>
            <T v="caption" color={t.ink3} style={styles.note}>{L('「写回」把群记忆写到这里。', '“Write back” puts the group’s memory here.')}</T>
          </>
        ) : null}

        <SectionLabel>{L('用量', 'Usage')}</SectionLabel>
        <Card><T v="callout" color={t.ink2}>{g.usage.line}</T></Card>
        <T v="caption" color={t.ink3} style={styles.note}>
          {L('赛博用它自己的模型，不花你的 Claude Code / ChatGPT 套餐；没报用量的按字数估，标「约」。', "Cybers run on their own models and never use your Claude Code / ChatGPT plan; where a model doesn't report usage it's estimated from the text and marked “about”.")}
        </T>

        <View style={{ gap: space.sm, marginTop: space.xl }}>
          {ended ? <Btn label={L('重新开启', 'Reopen')} kind="quiet" icon={<ArchiveRestore size={16} color={t.ink} />} onPress={reopen} />
            : <Btn label={L('结束群聊', 'End the group chat')} kind="quiet" icon={<CircleStop size={16} color={t.ink} />} onPress={end} />}
          <Btn label={L('删除群聊', 'Delete the group chat')} kind="danger" icon={<Trash2 size={16} color={t.bad} />} onPress={remove} />
        </View>
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  input: { borderRadius: 14, paddingHorizontal: 14, paddingVertical: 10, fontSize: 15 },
  memberRow: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: 10 },
  addFace: { width: 32, height: 32, borderRadius: 16, alignItems: 'center', justifyContent: 'center' },
  none: { width: 32, height: 32, borderRadius: 16, borderWidth: 1.5, borderStyle: 'dashed' },
  check: { width: 22, height: 22, borderRadius: 11, alignItems: 'center', justifyContent: 'center' },
  uncheck: { width: 20, height: 20, borderRadius: 10, borderWidth: 1.5 },
  note: { fontSize: 12, lineHeight: 17, paddingHorizontal: space.xs, paddingTop: 6 },
});
