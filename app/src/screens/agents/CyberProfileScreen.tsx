// 赛博的资料页（主线第三步，设计稿第 43 张 · 证件）：页头「资料 · ⋯」；大头像、名字、「赛博 · 标签」、「No. 07 · 建于 09/30」；三格数字（模型 /
// 一起几天 / 记下几条）；「身份」卡（身份、语气、规矩、声音，每行能点：弹出几个改法 →「改好以后 · 等你点头」→「改 / 不改」→「刚改 · 撤销」）；
// 「记忆」卡（顶上一行「健康、饮食、睡眠不给赛博」，名片 / 记下的 / 知识库，「全部 N 条 ›」进列表）；「装载」卡（每个一句它能做什么、「卸下」；
// 「装载一个功能 Agent」弹出列表，健康类写「不给」；每天读的开关）；底部输入框「跟 X 说要怎么改」；「发消息」「拉进群聊」；最底「删除赛博」。
// 路由 CyberProfile（params id）。数据都在服务器 cybers.py：改法先给样子，点头才写进去，每次改记一个版本，能撤销。
import React, { useCallback, useState } from 'react';
import { Pressable, StyleSheet, Switch, TextInput, View } from 'react-native';
import { useFocusEffect, useNavigation, useRoute } from '@react-navigation/native';
import { ChangeCard, RecentLine } from '../../components/agents/ChangeCard';
import { CyberAvatar } from '../../components/agents/CyberAvatar';
import { Choice, errText } from '../../components/agents/NewFlow';
import { AgentFace, ConfirmDelete, MenuItem } from '../../components/ChatParts';
import { ChevronRight, Ellipsis, MessageCircle, Pencil, Plus, Send, Trash2, UsersRound } from '../../components/icons';
import { KeyboardScroll } from '../../components/keyboard';
import { useSheet } from '../../components/Sheet';
import { Btn, Card, NavHeader, Pill, PullRefresh, Screen, SectionLabel, T, showError } from '../../components/ui';
import { cybersApi, type Change, type ChangeField, type Cyber, type Loadout, type Memory, type Tweak } from '../../api/cybers';
import { useGroupsOn } from '../../api/groups';
import { hasRoute } from '../../navigation';
import { L } from '../../i18n';
import { useStore } from '../../store';
import { radius, space, type, useTheme } from '../../theme';

const TWEAKS: { key: Tweak; label: () => string }[] = [
  { key: 'stricter', label: () => L('更严格', 'Stricter') }, { key: 'gentler', label: () => L('更温和', 'Gentler') },
  { key: 'shorter', label: () => L('更简短', 'Shorter') }, { key: 'english', label: () => L('用英文', 'In English') },
  { key: 'examples', label: () => L('多举例', 'More examples') },
];
const ROWS: { field: Extract<ChangeField, 'identity' | 'tone' | 'rules' | 'voice'>; label: () => string }[] = [
  { field: 'identity', label: () => L('身份', 'Identity') }, { field: 'tone', label: () => L('语气', 'Tone') },
  { field: 'rules', label: () => L('规矩', 'Rules') }, { field: 'voice', label: () => L('声音', 'Voice') },
];
const day = (iso: string) => { const d = new Date(iso); return Number.isNaN(d.getTime()) ? '' : `${String(d.getMonth() + 1).padStart(2, '0')}/${String(d.getDate()).padStart(2, '0')}`; };

/** 点一行改：五个改法、自己写新的一行、或者一句话说怎么改。都先出「改好的样子」，回到资料页上等你点「采用」。 */
function ChangeSheet({ cyber, field, label, close, onProposed }: { cyber: Cyber; field: ChangeField; label: string; close: () => void; onProposed: (c: Change[]) => void }) {
  const t = useTheme();
  const cur = field === 'voice' ? cyber.voice ?? '' : String(cyber[field as 'identity' | 'tone' | 'rules'] ?? '');
  const [value, setValue] = useState(cur);
  const [ask, setAsk] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const go = (body: { value?: string; tweak?: Tweak; ask?: string }) => {
    if (busy) return;
    setBusy(true);
    setErr('');
    cybersApi.propose(cyber.id, { field, ...body }).then((cs) => { close(); onProposed(cs); }).catch((e) => { setErr(errText(e)); setBusy(false); });
  };
  return (
    <View style={{ gap: space.md }}>
      <T v="callout" color={t.ink2}>{cur || L('（空）', '(empty)')}</T>
      {field !== 'voice' ? (
        <>
          <T v="label" color={t.ink3}>{L('改法', 'WAYS TO CHANGE IT')}</T>
          <View style={styles.chips}>{TWEAKS.map((x) => <Choice key={x.key} label={x.label()} on={false} off={busy} onPress={() => go({ tweak: x.key })} />)}</View>
          <TextInput value={ask} onChangeText={setAsk} placeholder={L(`一句话说怎么改，例如：以后每次推荐五个`, 'Say how in a sentence, e.g. always recommend five')} placeholderTextColor={t.ink3}
            accessibilityLabel={L('怎么改', 'How to change it')} style={[type.callout, styles.input, { backgroundColor: t.surface, color: t.ink }]} />
          <Btn kind="quiet" label={L('按这句改', 'Rewrite it')} onPress={() => { if (ask.trim()) go({ ask: ask.trim() }); }} />
        </>
      ) : null}
      <T v="label" color={t.ink3}>{field === 'voice' ? L('声音的名字', 'VOICE NAME') : L('或者自己写新的一行', 'OR WRITE THE NEW LINE')}</T>
      <TextInput value={value} onChangeText={setValue} multiline={field !== 'voice'} placeholder={label} placeholderTextColor={t.ink3} accessibilityLabel={label}
        style={[type.callout, styles.input, { backgroundColor: t.surface, color: t.ink, minHeight: field === 'voice' ? undefined : 72, textAlignVertical: 'top' }]} />
      <Btn label={busy ? L('正在改写…', 'Rewriting…') : L('预览', 'Preview')} onPress={() => go({ value })} />
      {err ? <T v="callout" color={t.bad}>{err}</T> : null}
    </View>
  );
}

function NameSheet({ cyber, close, onSaved }: { cyber: Cyber; close: () => void; onSaved: () => void }) {
  const t = useTheme();
  const [name, setName] = useState(cyber.name);
  const [tag, setTag] = useState(cyber.tag);
  const [err, setErr] = useState('');
  const save = () => cybersApi.patch(cyber.id, { name: name.trim(), tag: tag.trim() }).then(() => { close(); onSaved(); }).catch((e) => setErr(errText(e)));
  return (
    <View style={{ gap: space.md }}>
      <TextInput value={name} onChangeText={setName} placeholder={L('名字', 'Name')} placeholderTextColor={t.ink3} accessibilityLabel={L('名字', 'Name')}
        style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink }]} />
      <TextInput value={tag} onChangeText={setTag} maxLength={16} placeholder={L('标签', 'Tag')} placeholderTextColor={t.ink3} accessibilityLabel={L('标签', 'Tag')}
        style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink }]} />
      <Btn label={L('保存', 'Save')} onPress={save} />
      {err ? <T v="callout" color={t.bad}>{err}</T> : null}
    </View>
  );
}

export function CyberProfileScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const sheet = useSheet();
  const { id } = (useRoute<any>().params ?? {}) as { id: string };
  const { reload } = useStore();
  const groups = useGroupsOn() === true;  // 「拉进群聊」：群聊开着才有
  const [cy, setCy] = useState<Cyber | null>(null);
  const [mem, setMem] = useState<Memory | null>(null);
  const [lo, setLo] = useState<Loadout | null>(null);
  const [pending, setPending] = useState<Change[]>([]);
  const [err, setErr] = useState('');
  const [ask, setAsk] = useState('');
  const [asking, setAsking] = useState(false);
  const [acting, setActing] = useState<string | null>(null);

  const load = useCallback(() => Promise.all([
    cybersApi.get(id).then(setCy), cybersApi.memory(id).then(setMem), cybersApi.loadout(id).then(setLo),
    cybersApi.changes(id).then((c) => setPending(c.pending)),
  ]).then(() => setErr('')).catch((e) => setErr(errText(e))), [id]);
  useFocusEffect(useCallback(() => { load(); }, [load]));

  const proposed = (cs: Change[]) => setPending((p) => [...cs, ...p.filter((x) => !cs.some((c) => c.id === x.id))]);
  const decide = (c: Change, action: 'apply' | 'drop') => {
    setActing(c.id);
    cybersApi.decide(id, c.id, action).then(() => load()).catch((e) => showError(action === 'apply' ? L('修改失败', "Couldn't update") : L('操作失败', "Couldn't do that"), e))
      .finally(() => setActing(null));
  };
  const undo = (version: number) => cybersApi.undo(id, version).then(() => load()).catch((e) => showError(L('撤销失败', "Couldn't undo"), e));
  const openRow = (field: ChangeField, label: string) => cy && sheet.open({ title: label, content: (close) => <ChangeSheet cyber={cy} field={field} label={label} close={close} onProposed={proposed} /> });
  const freeAsk = () => {
    if (!ask.trim() || asking) return;
    setAsking(true);
    cybersApi.propose(id, { ask: ask.trim() }).then((cs) => { proposed(cs); setAsk(''); }).catch((e) => showError(L('没改写成', "Couldn't rewrite it"), e)).finally(() => setAsking(false));
  };
  const loadOne = () => lo && sheet.open({ title: L('装载一个功能 Agent', 'Load an Agent'), content: (close) => (
    <View>
      {lo.available.length ? lo.available.map((a) => (
        <Pressable key={a.id} disabled={a.health} accessibilityRole="button" style={({ pressed }) => [styles.agentRow, { opacity: a.health ? 0.45 : pressed ? 0.6 : 1 }]}
          onPress={() => { close(); cybersApi.load(id, a.id).then(setLo).catch((e) => showError(L('装载失败', "Couldn't load it"), e)); }}>
          <AgentFace icon={a.icon} color={a.color} size={36} />
          <View style={{ flex: 1, gap: 2 }}>
            <T v="headline" numberOfLines={1}>{a.name}</T>
            <T v="callout" color={t.ink2} numberOfLines={1} style={{ fontSize: 13 }}>{a.health ? a.why ?? L('不给', 'Not given') : a.purpose}</T>
          </View>
          {a.health ? <Pill label={L('不给', 'Not given')} /> : <Plus size={18} color={t.ink2} />}
        </Pressable>
      )) : <T v="callout" color={t.ink2}>{L('没有别的功能 Agent 可以装载了。', 'No other Agents to load.')}</T>}
    </View>
  ) });
  const more = () => cy && sheet.open({ title: cy.name, content: (close) => (
    <View>
      <MenuItem icon={Pencil} title={L('改名字和标签', 'Edit name and tag')} onPress={() => { close(); setTimeout(() => sheet.open({ title: L('名字和标签', 'Name and tag'), content: (c) => <NameSheet cyber={cy} close={c} onSaved={load} /> }), 250); }} />
      <MenuItem icon={Pencil} title={L('换形象', 'Change avatar')} chevron onPress={() => { close(); nav.navigate('CyberAvatarEdit', { avatar: cy.avatar, name: cy.name, model: cy.modelName, cyber: cy.id }); }} />
      <MenuItem icon={Trash2} danger title={L('删除赛博', 'Delete Cyber')} desc={L('记忆移入归档', 'Its memory moves to the archive')} onPress={() => { close(); setTimeout(remove, 250); }} />
    </View>
  ) });
  const remove = () => cy && sheet.open({ title: L(`删除赛博「${cy.name}」？`, `Delete the Cyber "${cy.name}"?`), content: (close) => (
    <ConfirmDelete label={L('删除赛博', 'Delete Cyber')} close={close}
      body={L('它不再出现在通讯录和对话列表里。记下的和知识库移入归档，不会真删；和它的对话记录保留为历史。', "It leaves your contacts and chat list. Its notes and knowledge move to the archive and aren't erased; your chats with it stay as history.")}
      run={() => cybersApi.remove(cy.id).then(() => { reload('chats').catch(() => {}); nav.goBack(); })} />
  ) });

  if (!cy) {
    return (
      <Screen>
        <NavHeader title={L('资料', 'Profile')} onBack={() => nav.goBack()} />
        <T v="callout" color={err ? t.bad : t.ink3} style={{ padding: space.lg }}>{err ? L(`无法加载：${err}`, `Couldn't load: ${err}`) : L('正在加载…', 'Loading…')}</T>
      </Screen>
    );
  }
  const notes = mem?.notes.count ?? cy.notes ?? 0;
  const recent = cy.recent && !cy.recent.undone ? cy.recent : null;
  return (
    <Screen>
      <NavHeader title={L('资料', 'Profile')} onBack={() => nav.goBack()}
        right={<Pressable onPress={more} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('更多', 'More')}><Ellipsis size={22} color={t.ink2} /></Pressable>} />
      <KeyboardScroll contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl }} keyboardShouldPersistTaps="handled" keyboardDismissMode="interactive"
        refreshControl={<PullRefresh onRefresh={load} />}>
        <View style={{ alignItems: 'center', gap: 6 }}>
          <Pressable onPress={() => nav.navigate('CyberAvatarEdit', { avatar: cy.avatar, name: cy.name, model: cy.modelName, cyber: cy.id })} accessibilityRole="button"
            accessibilityLabel={L('换形象', 'Change avatar')}>
            <CyberAvatar avatar={cy.avatar} size={96} />
          </Pressable>
          <T v="title" style={{ fontWeight: '700' }}>{cy.name}</T>
          <T v="callout" color={t.ink2}>{cy.tag ? L(`赛博 · ${cy.tag}`, `Cyber · ${cy.tag}`) : L('赛博', 'Cyber')}</T>
          <T v="caption" color={t.ink3}>{L(`No. ${String(cy.no).padStart(2, '0')} · 建于 ${day(cy.createdAt)}`, `No. ${String(cy.no).padStart(2, '0')} · since ${day(cy.createdAt)}`)}</T>
        </View>
        <View style={styles.stats}>
          <Pressable onPress={() => nav.navigate('CyberModel', { id: cy.id })} accessibilityRole="button" style={[styles.stat, { backgroundColor: t.surface }]}>
            <T v="headline" numberOfLines={1}>{cy.modelName || '—'}</T><T v="caption" color={t.ink3}>{L('模型', 'Model')}</T>
          </Pressable>
          <View style={[styles.stat, { backgroundColor: t.surface }]}>
            <T v="headline">{L(`${cy.days} 天`, `${cy.days} d`)}</T><T v="caption" color={t.ink3}>{L('一起', 'Together')}</T>
          </View>
          <Pressable onPress={() => nav.navigate('CyberMemory', { id: cy.id })} accessibilityRole="button" style={[styles.stat, { backgroundColor: t.surface }]}>
            <T v="headline">{L(`${notes} 条`, `${notes}`)}</T><T v="caption" color={t.ink3}>{L('记下', 'Kept')}</T>
          </Pressable>
        </View>

        {pending.map((c) => <View key={c.id} style={{ marginTop: space.md }}><ChangeCard change={c} busy={acting === c.id} onApply={() => decide(c, 'apply')} onDrop={() => decide(c, 'drop')} /></View>)}
        {recent ? <View style={{ marginTop: space.sm }}><RecentLine version={recent} onUndo={() => undo(recent.id)} /></View> : null}

        <SectionLabel>{L('身份', 'Identity')}</SectionLabel>
        <Card style={{ paddingVertical: space.xs }}>
          {ROWS.map((r) => {
            const v = r.field === 'voice' ? cy.voice : cy[r.field];
            return (
              <Pressable key={r.field} onPress={() => openRow(r.field, r.label())} accessibilityRole="button" style={({ pressed }) => [styles.line, { opacity: pressed ? 0.6 : 1 }]}>
                <T v="callout" color={t.ink3} style={{ width: 52 }}>{r.label()}</T>
                <T v="callout" style={{ flex: 1 }} numberOfLines={3}>{v || L('（空）', '(empty)')}</T>
                <ChevronRight size={16} color={t.ink3} />
              </Pressable>
            );
          })}
        </Card>

        <SectionLabel>{L('记忆', 'Memory')}</SectionLabel>
        <Card style={{ gap: space.sm }}>
          <T v="caption" color={t.ink3}>{mem?.rule ?? L('健康、饮食、睡眠不给赛博', 'Health, diet and sleep are never given to a Cyber')}</T>
          <View style={styles.memRow}><T v="callout" color={t.ink3} style={{ width: 52 }}>{L('名片', 'Card')}</T>
            <T v="callout" style={{ flex: 1 }} numberOfLines={2}>{mem?.card.length ? mem.card.map((x) => x.label).join(L('、', ', ')) : L('你的名片还空着', 'Your card is empty')}</T></View>
          <View style={styles.memRow}><T v="callout" color={t.ink3} style={{ width: 52 }}>{L('记下的', 'Kept')}</T>
            <T v="callout" style={{ flex: 1 }} numberOfLines={2}>{mem?.notes.items.map((n) => n.text).join(' · ') || L('还没记下什么', 'Nothing kept yet')}</T></View>
          <View style={styles.memRow}><T v="callout" color={t.ink3} style={{ width: 52 }}>{L('知识库', 'Knowledge')}</T>
            <T v="callout" style={{ flex: 1 }} numberOfLines={2}>{mem?.knowledge.items.map((k) => k.title).join(' · ') || L('还没有资料', 'No material yet')}</T></View>
          <Pressable onPress={() => nav.navigate('CyberMemory', { id: cy.id })} accessibilityRole="button" style={({ pressed }) => [styles.more, { opacity: pressed ? 0.6 : 1 }]}>
            <T v="callout" color={t.gold}>{L(`全部 ${notes} 条`, `All ${notes}`)}</T><ChevronRight size={15} color={t.gold} />
          </Pressable>
        </Card>

        <SectionLabel>{L('装载', 'Loadout')}</SectionLabel>
        <Card style={{ gap: space.xs, paddingVertical: space.sm }}>
          {(lo?.loaded ?? []).map((a) => (
            <View key={a.id} style={styles.agentRow}>
              <AgentFace icon={a.icon} color={a.color} size={36} />
              <View style={{ flex: 1, gap: 2 }}>
                <T v="headline" numberOfLines={1}>{a.name}</T>
                <T v="callout" color={t.ink2} numberOfLines={1} style={{ fontSize: 13 }}>{a.health ? a.why ?? '' : a.purpose}</T>
              </View>
              <Btn kind="quiet" label={L('卸下', 'Unload')} onPress={() => cybersApi.unload(id, a.id).then(setLo).catch((e) => showError(L('卸下失败', "Couldn't unload it"), e))} />
            </View>
          ))}
          <Pressable onPress={loadOne} accessibilityRole="button" style={({ pressed }) => [styles.agentRow, { opacity: pressed ? 0.6 : 1 }]}>
            <Plus size={18} color={t.ink2} />
            <T v="callout" color={t.ink2}>{L('装载一个功能 Agent', 'Load an Agent')}</T>
          </Pressable>
          {lo?.loaded.length ? (
            <View style={styles.agentRow}>
              <View style={{ flex: 1, gap: 2 }}>
                <T v="callout">{L('每天读', 'Read daily')}</T>
                <T v="caption" color={t.ink3}>{L('每天读一次装载的功能 Agent 的产出，和你聊读到的（每天一轮，按量计费）', 'Reads what the loaded Agents produced once a day and talks it over (one turn a day, billed by use)')}</T>
              </View>
              <Switch value={!!lo.daily} onValueChange={(v) => cybersApi.daily(id, v).then(setLo).catch((e) => showError(L('修改失败', "Couldn't update"), e))}
                trackColor={{ true: t.goldFill, false: t.line }} thumbColor={t.surface} />
            </View>
          ) : null}
        </Card>

        <SectionLabel>{L('改法', 'Change it')}</SectionLabel>
        <View style={[styles.askBox, { backgroundColor: t.surface }]}>
          <TextInput value={ask} onChangeText={setAsk} placeholder={L(`跟 ${cy.name} 说要怎么改`, `Tell ${cy.name} how to change`)} placeholderTextColor={t.ink3}
            accessibilityLabel={L('怎么改', 'How to change it')} onSubmitEditing={freeAsk} returnKeyType="send" style={[type.callout, { flex: 1, color: t.ink, paddingVertical: 0, outlineWidth: 0 }]} />
          <Pressable onPress={freeAsk} disabled={asking || !ask.trim()} accessibilityRole="button" accessibilityLabel={L('发送', 'Send')} hitSlop={8}>
            {asking ? <T v="caption" color={t.ink3}>{L('改写中', 'Working')}</T> : <Send size={18} color={ask.trim() ? t.gold : t.ink3} />}
          </Pressable>
        </View>
        <T v="caption" color={t.ink3} style={{ marginTop: space.xs }}>{L('先给改好的样子，你点「采用」才生效，改了能撤销。', 'You see the result first; it takes effect only when you tap Apply, and you can undo it.')}</T>

        <View style={{ flexDirection: 'row', gap: space.sm, marginTop: space.xl }}>
          <Btn flex label={L('发消息', 'Message')} icon={<MessageCircle size={16} color={t.onGold} />} onPress={() => nav.navigate('CyberChat', { id: cy.id })} />
          {groups && hasRoute('NewRoom') ? <Btn flex kind="quiet" label={L('拉进群聊', 'Add to a group')} icon={<UsersRound size={16} color={t.ink} />} onPress={() => nav.navigate('NewRoom', { members: [cy.id] })} /> : null}
        </View>
        <Pressable onPress={remove} accessibilityRole="button" style={({ pressed }) => [styles.delete, { opacity: pressed ? 0.6 : 1 }]}>
          <T v="callout" color={t.bad}>{L('删除赛博 · 记忆移入归档', 'Delete Cyber · memory moves to the archive')}</T>
        </Pressable>
        {err ? <T v="callout" color={t.bad}>{L(`无法加载：${err}`, `Couldn't load: ${err}`)}</T> : null}
      </KeyboardScroll>
    </Screen>
  );
}


const styles = StyleSheet.create({
  stats: { flexDirection: 'row', gap: space.sm, marginTop: space.lg },
  stat: { flex: 1, borderRadius: radius.md, paddingVertical: space.md, paddingHorizontal: space.sm, alignItems: 'center', gap: 2 },
  line: { flexDirection: 'row', alignItems: 'flex-start', gap: space.md, paddingVertical: space.md },
  memRow: { flexDirection: 'row', alignItems: 'flex-start', gap: space.md },
  more: { flexDirection: 'row', alignItems: 'center', alignSelf: 'flex-end', gap: 2, paddingTop: space.xs },
  agentRow: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: space.sm },
  askBox: { flexDirection: 'row', alignItems: 'center', gap: space.sm, borderRadius: radius.md, paddingHorizontal: space.md, height: 46 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: space.sm },
  input: { borderRadius: radius.md, paddingHorizontal: space.md, paddingVertical: 10 },
  delete: { alignItems: 'center', paddingVertical: space.lg, marginTop: space.md },
});
