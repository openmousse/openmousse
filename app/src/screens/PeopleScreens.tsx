// 我 → 朋友画像（服务端 ../server/people.py，2026-09-28）：和朋友一起录播客，认人时对上人，整理完给他记几条。
//   People  一人一行（几条、下次问问几条、最近什么时候）；右上角加一个人
//   Person  一人一页：下次问问 / 在做的事 / 在意的 / 看法，每条带原话和出处（点一下听那一句），能改、标问过了、删；自己加一条；一起录过的几期
// 只有你看得到：名片 agent、主对话、世界树和库都不用它。
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Alert, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { useNavigation, useRoute } from '@react-navigation/native';
import { Check, ChevronRight, Mic, Pencil, Play, Plus, Square, Trash2, Undo2 } from '../components/icons';
import { useSheet } from '../components/Sheet';
import { timeLabel } from '../components/FriendBits';
import { Btn, Card, ListRow, NavHeader, PullRefresh, Screen, SectionLabel, Segmented, T, showError } from '../components/ui';
import * as ppl from '../api/people';
import type { NoteKind, Person, PersonNote, PersonPage } from '../api/people';
import * as pod from '../api/podcast';
import type { Episode } from '../api/podcast';
import { usePodPlayer } from '../think/podAudio';
import { L } from '../i18n';
import { radius, space, type, useTheme } from '../theme';

const KIND_ORDER: NoteKind[] = ['ask', 'doing', 'care', 'view'];
const NO_SEGMENTS: Episode['segments'] = [];  // 播放器的初始段落：每次渲染同一个数组（新数组会让它把 play 时给的段落冲掉）
const kindName = (k: NoteKind) => ({ view: L('看法', 'Views'), doing: L('在做的事', 'Up to'), care: L('在意的', 'Cares about'), ask: L('下次问问', 'Ask next time') }[k]);

function confirm(title: string, msg: string, yes: string, go: () => void) {
  if (Platform.OS === 'web') { if (window.confirm(`${title}\n${msg}`)) go(); return; }
  Alert.alert(title, msg, [{ text: L('取消', 'Cancel'), style: 'cancel' }, { text: yes, style: 'destructive', onPress: go }]);
}

function Initial({ name }: { name: string }) {
  const t = useTheme();
  return (
    <View style={[styles.avatar, { backgroundColor: t.tints.pink.soft }]}>
      <Text style={{ color: t.tints.pink.fg, fontWeight: '700', fontSize: 15 }}>{(name.trim()[0] ?? '?').toUpperCase()}</Text>
    </View>
  );
}

export function PeopleScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const sheet = useSheet();
  const [people, setPeople] = useState<Person[] | null>(null);
  const [err, setErr] = useState('');
  const load = useCallback(() => ppl.list().then((j) => { setPeople(j.people); setErr(''); }).catch((e) => setErr(e instanceof Error ? e.message : String(e))), []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => nav.addListener('focus', () => { load(); }), [nav, load]);
  const add = () => sheet.open({ title: L('添加人物', 'Add someone'), content: (close) => (
    <NameSheet initial="" label={L('添加', 'Add')} onSave={async (name) => { const r = await ppl.create(name); close(); nav.navigate('Person', { id: r.person.id }); }} />
  ) });
  return (
    <Screen>
      <NavHeader title={L('朋友画像', 'Friend notes')} sub={L('仅你可见', 'Only you see these')} onBack={() => nav.goBack()}
        right={<Pressable onPress={add} hitSlop={10} accessibilityRole="button" accessibilityLabel={L('添加人物', 'Add someone')} style={{ padding: 6 }}><Plus size={22} color={t.gold} /></Pressable>} />
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl, gap: space.md }} refreshControl={<PullRefresh onRefresh={load} />}>
        <T v="callout" color={t.ink2}>{L('与朋友一起录制播客，并在标注说话人时对应到具体的人，整理完成后会为每个人记录几条笔记：在做的事、在意的、下次问问。再次一起录制时，AI 主持人可以接续话题；「今天聊点什么」也会提醒你跟进。名片 Agent、主对话、世界树和库均不会使用这些内容。',
          "Record a podcast with friends and match their voices to people: afterwards each gets a few notes (what they're up to, what they care about, what to ask next). The AI host picks up from them next time and Talk about today reminds you to follow up. Your card agent, the main chat, the memory tree and the vault never use them.")}</T>
        {err ? <Card><T v="callout" color={t.bad}>{L(`无法加载：${err}`, `Couldn't load: ${err}`)}</T></Card> : null}
        {!people && !err ? <ActivityIndicator color={t.gold} style={{ marginTop: space.xl }} /> : null}
        {people && !people.length ? (
          <Card style={{ gap: space.xs }}>
            <T v="headline">{L('暂无人物', 'Nobody yet')}</T>
            <T v="callout" color={t.ink2}>{L('在 Zen → 播客 →「约朋友」中录制一期，录制后标注每位说话人，人物即会显示在此处。也可点按右上角手动添加。', 'Record an episode in Zen → Podcast → With friends and mark who is who afterwards. Or add someone yourself (top right).')}</T>
          </Card>
        ) : null}
        {people?.length ? (
          <Card style={{ paddingVertical: space.xs }}>
            {people.map((p, i) => (
              <ListRow key={p.id} icon={<Initial name={p.name} />} title={p.name}
                sub={[L(`${p.notes} 条`, `${p.notes} notes`), p.asks ? L(`下次问问 ${p.asks} 条`, `${p.asks} to ask`) : null, p.lastAt ? L(`最近 ${timeLabel(p.lastAt)}`, `last ${timeLabel(p.lastAt)}`) : null,
                  p.friendName ? L(`好友 ${p.friendName}`, `friend ${p.friendName}`) : null].filter(Boolean).join(' · ')}
                onPress={() => nav.navigate('Person', { id: p.id })} last={i === people.length - 1} />
            ))}
          </Card>
        ) : null}
      </ScrollView>
    </Screen>
  );
}

function NameSheet({ initial, label, onSave }: { initial: string; label: string; onSave: (v: string) => Promise<void> }) {
  const t = useTheme();
  const [v, setV] = useState(initial);
  const [busy, setBusy] = useState(false);
  const go = async () => {
    if (busy || !v.trim()) return;
    setBusy(true);
    try { await onSave(v.trim()); } catch (e) { showError(L('保存失败', "Couldn't save"), e); } finally { setBusy(false); }
  };
  return (
    <View style={{ gap: space.md }}>
      <TextInput value={v} onChangeText={setV} autoFocus placeholder={L('名字', 'Name')} placeholderTextColor={t.ink3} onSubmitEditing={go} returnKeyType="done"
        style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink }]} />
      <Btn label={busy ? L('正在保存…', 'Saving…') : label} onPress={go} />
    </View>
  );
}

function NoteSheet({ kinds, initial, onSave }: { kinds: NoteKind[]; initial?: { kind: NoteKind; text: string }; onSave: (kind: NoteKind, text: string) => Promise<void> }) {
  const t = useTheme();
  const [kind, setKind] = useState<NoteKind>(initial?.kind ?? 'doing');
  const [text, setText] = useState(initial?.text ?? '');
  const [busy, setBusy] = useState(false);
  const go = async () => {
    if (busy || !text.trim()) return;
    setBusy(true);
    try { await onSave(kind, text.trim()); } catch (e) { showError(L('保存失败', "Couldn't save"), e); } finally { setBusy(false); }
  };
  return (
    <View style={{ gap: space.md }}>
      <Segmented options={kinds.map((k) => ({ value: k, label: kindName(k) }))} value={kind} onChange={setKind} />
      <TextInput value={text} onChangeText={setText} autoFocus multiline placeholder={kind === 'ask' ? L('下次要问什么？例如：面试结果如何', 'What to ask next time? e.g. how the interview went') : L('一句话', 'One line')}
        placeholderTextColor={t.ink3} style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink, minHeight: 72 }]} />
      <Btn label={busy ? L('正在保存…', 'Saving…') : L('保存', 'Save')} onPress={go} />
    </View>
  );
}

export function PersonScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const route = useRoute<any>();
  const sheet = useSheet();
  const id = route.params?.id as string;
  const [page, setPage] = useState<PersonPage | null>(null);
  const [err, setErr] = useState('');
  const [openOld, setOpenOld] = useState(false);
  const player = usePodPlayer(NO_SEGMENTS);
  const eps = useRef<Record<string, Episode>>({});
  const load = useCallback(() => ppl.get(id).then((p) => { setPage(p); setErr(''); }).catch((e) => setErr(e instanceof Error ? e.message : String(e))), [id]);
  useEffect(() => { load(); }, [load]);

  const hear = async (n: PersonNote) => {
    if (!n.episode || n.episode.gone || !n.sid) return;
    try {
      const e = eps.current[n.episode.id] ?? await pod.get(n.episode.id);
      eps.current[n.episode.id] = e;
      const [a, b] = n.sid.split('.').map(Number);
      const seg = e.segments.find((x) => x.idx === a);
      const s = seg?.sentences.find((x) => x.i === b);
      if (seg && s) player.play(n.id, seg.idx, s.t0, s.t1 + 0.3, false, e.segments);
    } catch (err2) { showError(L('播放失败', "Couldn't play"), err2); }
  };
  const act = (n: PersonNote) => sheet.open({ title: n.text, content: (close) => (
    <View style={{ gap: space.sm }}>
      <Row icon={<Pencil size={18} color={t.ink} />} label={L('编辑', 'Edit')} onPress={() => sheet.open({ title: L('编辑笔记', 'Edit this note'), content: (c2) => (
        <NoteSheet kinds={KIND_ORDER} initial={{ kind: n.kind, text: n.text }} onSave={async (kind, text) => { setPage(await ppl.patchNote(n.id, { kind, text })); c2(); }} />
      ) })} />
      {n.kind === 'ask' && n.status === 'active' ? <Row icon={<Check size={18} color={t.ink} />} label={L('标记为已问', 'Asked')} note={L('移至「已问过及旧记录」，不再提醒', "Moves to Asked; no more reminders")}
        onPress={() => { close(); ppl.patchNote(n.id, { status: 'done' }).then(setPage).catch((e) => showError(L('修改失败', "Couldn't update"), e)); }} /> : null}
      {n.status === 'done' ? <Row icon={<Undo2 size={18} color={t.ink} />} label={L('标记为未问', 'Not asked yet')} onPress={() => { close(); ppl.patchNote(n.id, { status: 'active' }).then(setPage).catch((e) => showError(L('修改失败', "Couldn't update"), e)); }} /> : null}
      <Row icon={<Trash2 size={18} color={t.bad} />} label={L('删除', 'Delete')} danger note={n.replaces ? L('删除此条更新，并恢复之前的记录', 'Deletes this update; the earlier note comes back') : undefined}
        onPress={() => { close(); ppl.removeNote(n.id).then(setPage).catch((e) => showError(L('删除失败', "Couldn't delete"), e)); }} />
    </View>
  ) });
  const add = () => sheet.open({ title: L('添加笔记', 'Add a note'), content: (close) => (
    <NoteSheet kinds={KIND_ORDER} onSave={async (kind, text) => { setPage(await ppl.addNote(id, kind, text)); close(); }} />
  ) });
  const rename = () => page && sheet.open({ title: L('重命名', 'Rename'), content: (close) => (
    <NameSheet initial={page.person.name} label={L('保存', 'Save')} onSave={async (name) => { setPage(await ppl.rename(id, name)); close(); }} />
  ) });
  const del = () => page && confirm(L(`删除${page.person.name}？`, `Delete ${page.person.name}?`), L('此人及其画像将被删除；已录制的节目不受影响，说话人标注中仅保留名字。', 'Deletes this person and every note; the episodes stay, with just the name.'), L('删除', 'Delete'),
    () => { ppl.remove(id).then(() => nav.goBack()).catch((e) => showError(L('删除失败', "Couldn't delete"), e)); });

  const notes = page?.notes ?? [];
  const active = notes.filter((n) => n.status === 'active');
  const byId = new Map(notes.map((n) => [n.id, n]));
  const old = notes.filter((n) => n.status !== 'active' && !notes.some((x) => x.replaces === n.id && x.status === 'active'));
  const note = (n: PersonNote, last: boolean) => {
    const prev = n.replaces ? byId.get(n.replaces) : undefined;
    const src = n.by === 'me' ? L('手动添加', 'Added by you') : n.episode?.gone ? L('该期已删除', 'Episode deleted')
      : `${n.episode?.title ? `《${n.episode.title}》` : ''}${n.at != null ? ` ${pod.clock(n.at)}` : ''}`;
    return (
      <Pressable key={n.id} onPress={() => act(n)} accessibilityRole="button" style={({ pressed }) => [styles.note, !last && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }, { opacity: pressed ? 0.7 : 1 }]}>
        <T v="body" color={n.status === 'active' ? t.ink : t.ink3} style={n.status === 'replaced' ? { textDecorationLine: 'line-through' } : undefined}>{n.text}</T>
        {n.quote ? <T v="callout" color={t.ink2}>「{n.quote}」</T> : null}
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          {n.episode && !n.episode.gone && n.sid ? (
            <Pressable onPress={() => hear(n)} hitSlop={6} accessibilityRole="button" accessibilityLabel={L('播放原话', 'Hear it')} style={[styles.src, { backgroundColor: t.surface2 }]}>
              {player.playing === n.id ? <Square size={9} color={t.ink} fill={t.ink} /> : <Play size={9} color={t.ink} fill={t.ink} />}
              <Text style={[type.caption, { color: t.ink }]} numberOfLines={1}>{src}</Text>
            </Pressable>
          ) : <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{src}</T>}
          <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{[timeLabel(n.createdAt), n.edited && n.by !== 'me' ? L('已编辑', 'edited') : null].filter(Boolean).join(' · ')}</T>
        </View>
        {prev ? <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L(`之前：${prev.text}（${timeLabel(prev.createdAt)}）`, `Before: ${prev.text} (${timeLabel(prev.createdAt)})`)}</T> : null}
      </Pressable>
    );
  };
  return (
    <Screen>
      <NavHeader title={page?.person.name ?? ''} onTitlePress={page ? rename : undefined} titleHint={L('重命名', 'rename')} onBack={() => nav.goBack()}
        right={<Pressable onPress={add} hitSlop={10} accessibilityRole="button" accessibilityLabel={L('添加笔记', 'Add a note')} style={{ padding: 6 }}><Plus size={22} color={t.gold} /></Pressable>} />
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl }} refreshControl={<PullRefresh onRefresh={load} />}>
        {err ? <Card><T v="callout" color={t.bad}>{L(`无法加载：${err}`, `Couldn't load: ${err}`)}</T></Card> : null}
        {!page && !err ? <ActivityIndicator color={t.gold} style={{ marginTop: space.xl }} /> : null}
        {page ? (
          <>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.md }}>
              <Initial name={page.person.name} />
              <T v="callout" color={t.ink2} style={{ flex: 1 }}>{[L('仅你可见', 'Only you see this'), page.episodes.length ? L(`共同录制 ${page.episodes.length} 期`, `${page.episodes.length} episodes together`) : null,
                page.person.friendName ? L(`好友 ${page.person.friendName}`, `friend ${page.person.friendName}`) : null].filter(Boolean).join(' · ')}</T>
            </View>
            {!active.length ? (
              <Card style={{ gap: space.xs, marginTop: space.lg }}>
                <T v="headline">{L('暂无记录', 'No notes yet')}</T>
                <T v="callout" color={t.ink2}>{L(`与${page.person.name}一起录制一期播客，并在标注说话人时选择${page.person.name}；也可点按右上角手动添加。`, `Record an episode with ${page.person.name} and pick them when you mark who is who, or add a note yourself (top right).`)}</T>
              </Card>
            ) : null}
            {KIND_ORDER.map((k) => {
              const list = active.filter((n) => n.kind === k);
              if (!list.length) return null;
              return (
                <View key={k}>
                  <SectionLabel>{`${kindName(k)} · ${list.length}`}</SectionLabel>
                  <Card style={{ paddingVertical: space.xs }}>{list.map((n, i) => note(n, i === list.length - 1))}</Card>
                </View>
              );
            })}
            {old.length ? (
              <View>
                <Pressable onPress={() => setOpenOld((v) => !v)} accessibilityRole="button" accessibilityState={{ expanded: openOld }}>
                  <SectionLabel right={<ChevronRight size={16} color={t.ink3} style={{ transform: [{ rotate: openOld ? '90deg' : '0deg' }] }} />}>
                    {L(`已问过及旧记录 · ${old.length}`, `Asked and earlier notes · ${old.length}`)}
                  </SectionLabel>
                </Pressable>
                {openOld ? <Card style={{ paddingVertical: space.xs }}>{old.map((n, i) => note(n, i === old.length - 1))}</Card> : null}
              </View>
            ) : null}
            {page.episodes.length ? (
              <View>
                <SectionLabel>{L('共同录制', 'Recorded together')}</SectionLabel>
                <Card style={{ paddingVertical: space.xs }}>
                  {page.episodes.map((e, i) => (
                    <ListRow key={e.id} icon={<Mic size={18} color={t.tints.pink.fg} />} title={e.title} sub={timeLabel(e.createdAt)}
                      onPress={() => nav.navigate(e.status === 'recording' ? 'PodRec' : e.status === 'prep' ? 'PodPrep' : 'PodDone', { id: e.id })} last={i === page.episodes.length - 1} />
                  ))}
                </Card>
              </View>
            ) : null}
            <T v="caption" color={t.ink3} style={{ fontWeight: '400', marginTop: space.lg }}>{L('点按一条笔记可编辑、标记为已问或删除。笔记仅来自你们共同录制的播客及你的手动修改；名片 Agent、主对话、世界树和库均不会使用这些内容。',
              'Tap a note to edit, mark as asked or delete it. Notes only come from episodes you recorded together, plus your own edits; your card agent, the main chat, the memory tree and the vault never use them.')}</T>
            <Pressable onPress={del} accessibilityRole="button" style={{ alignSelf: 'center', padding: space.md, marginTop: space.md }}>
              <T v="callout" color={t.bad}>{L('删除此人及画像', 'Delete this person and the notes')}</T>
            </Pressable>
          </>
        ) : null}
      </ScrollView>
    </Screen>
  );
}

function Row({ icon, label, note, danger, onPress }: { icon: React.ReactNode; label: string; note?: string; danger?: boolean; onPress: () => void }) {
  const t = useTheme();
  return (
    <Pressable onPress={onPress} accessibilityRole="button" style={({ pressed }) => [styles.row, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
      {icon}
      <View style={{ flex: 1, gap: 2 }}>
        <T v="headline" color={danger ? t.bad : t.ink}>{label}</T>
        {note ? <T v="callout" color={t.ink2}>{note}</T> : null}
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  avatar: { width: 32, height: 32, borderRadius: 16, alignItems: 'center', justifyContent: 'center' },
  input: { borderRadius: radius.md, paddingHorizontal: space.lg, paddingVertical: 12 },
  note: { gap: 4, paddingVertical: 12 },
  src: { flexDirection: 'row', alignItems: 'center', gap: 4, height: 24, paddingHorizontal: 8, borderRadius: 12, maxWidth: '100%' },
  row: { flexDirection: 'row', alignItems: 'center', gap: space.md, borderRadius: radius.md, padding: space.md },
});
