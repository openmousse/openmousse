// 收藏里的一条：看存下的正文、备注和关键词，处理由你定——问问、交给 Agent、放进思考、翻译、提炼进库、删。它不会自己读。
import React, { useCallback, useEffect, useState } from 'react';
import { Image, Linking, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { useNavigation, useRoute } from '@react-navigation/native';
import { BookOpen, Check, ChevronLeft, Ellipsis, FileText, Forward, Languages, Lightbulb, MessageCircle, Plus, Trash2 } from '../components/icons';
import { setChatDraft } from '../components/chatInput';
import { GroupBadge } from '../components/GroupIcon';
import { useSheet } from '../components/Sheet';
import { Btn, Screen, T, showError } from '../components/ui';
import * as thinkApi from '../api/think';
import type { SaveItem } from '../api/think';
import { L } from '../i18n';
import { openThread } from '../navigation';
import { useStore } from '../store';
import { radius, space, type, useTheme } from '../theme';
import { GrowInput, KeywordChip, TypeTile, dayLabel, saveStatusLabel } from '../think/parts';
import { useThink } from '../think/ThinkStore';

export function SaveScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const route = useRoute<any>();
  const sheet = useSheet();
  const id = route.params?.id as string;
  const { groups } = useStore();
  const { updateSave, removeSave, openTopic } = useThink();
  const [s, setS] = useState<SaveItem | null>(null);
  const [text, setText] = useState('');
  const [more, setMore] = useState(false);
  const [note, setNote] = useState('');
  const [sent, setSent] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback((full = false) => thinkApi.getSave(id, full).then((r) => {
    setS(r.save); setText(r.text); setMore(r.more); setNote(r.save.note);
    if (!r.save.seen) updateSave(id, { seen: true }).catch(() => {});
  }).catch((e) => showError(L('读不到这条收藏', "Couldn't load this item"), e)), [id, updateSave]);
  useEffect(() => { load(); }, [load]);
  // 还在抓正文：隔几秒再看
  const fetching = s?.textStatus === 'fetching';
  useEffect(() => {
    if (!fetching) return undefined;
    const h = setInterval(() => load(), 3000);
    return () => clearInterval(h);
  }, [fetching, load]);
  if (!s) return <Screen><View style={{ padding: space.xl }}><T v="callout" color={t.ink3}>{L('正在读…', 'Loading…')}</T></View></Screen>;

  const run = async (job: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    try { await job(); } catch (e) { showError(L('没做成', "Couldn't do that"), e); } finally { setBusy(false); }
  };
  const ask = () => openThread('main', false, { saveId: s.id, title: s.title || s.name || L('一条收藏', 'a saved item') });
  const translate = () => { setChatDraft('main', L('把这篇整篇译成中文，保持原来的结构', 'Translate the whole thing into English, keeping its structure')); ask(); };
  const give = () => sheet.open({
    title: L('交给哪个 Agent？', 'Hand it to which Agent?'),
    content: (close) => (
      <View style={{ gap: space.sm }}>
        {groups.map((g) => (
          <Pressable key={g.id} onPress={() => { close(); run(async () => { const r = await thinkApi.giveSave(s.id, g.id); setS(r); setSent(g.name); }); }} accessibilityRole="button"
            style={({ pressed }) => [styles.agent, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
            <GroupBadge icon={g.icon} color={g.color} size={30} />
            <T v="headline" style={{ flex: 1 }}>{g.name}</T>
          </Pressable>
        ))}
        <T v="caption" color={t.ink3}>{L('它收到的是这条的原文（正文、备注、链接），按它自己的规矩处理，回完静默推给你。', "It gets the item as saved (text, note, link) and handles it its own way; the reply arrives quietly.")}</T>
      </View>
    ),
  });
  const toIdea = () => run(async () => {
    await thinkApi.saveToIdea(s.id);
    nav.navigate('Tabs', { screen: '思考', params: { tab: 'ideas', at: Date.now() } });
  });
  const distill = () => run(async () => {
    const f = await thinkApi.saveToIdea(s.id);
    const tp = await openTopic([f.id]);
    await thinkApi.done(tp.id);
    nav.navigate('ThinkDone', { id: tp.id });
  });
  const addKeyword = () => sheet.open({ title: L('加关键词', 'Add keywords'), content: (close) => <KeywordSheet have={s.keywords} close={close} onSave={(kws) => run(async () => { setS(await updateSave(s.id, { keywords: kws })); })} /> });
  const menu = () => sheet.open({
    title: s.title || L('这条收藏', 'This item'),
    content: (close) => <DeleteBlock onDelete={() => run(async () => { await removeSave(s.id); close(); nav.goBack(); })} />,
  });
  const status = saveStatusLabel(s);
  const actions = [
    { Icon: MessageCircle, label: L('问问', 'Ask'), sub: L('带着这篇去主对话', 'Take it to the main chat'), color: t.cyan, go: ask },
    { Icon: Forward, label: L('交给 Agent', 'Hand to an Agent'), sub: sent ? L(`交给了「${sent}」`, `Handed to "${sent}"`) : L('求职、饮食记录……', 'Jobs, diet…'), color: t.cyan, go: give },
    { Icon: Lightbulb, label: L('放进思考', 'Into Think'), sub: L('变成一条想法', 'Becomes a thought'), color: t.gold, go: toIdea },
    { Icon: Languages, label: L('翻译', 'Translate'), sub: L('整篇译成中文', 'The whole thing'), color: t.cyan, go: translate },
    { Icon: BookOpen, label: L('提炼进库', 'Distill'), sub: L('写成你的笔记', 'Into your own note'), color: t.good, go: distill },
    { Icon: Trash2, label: L('删掉', 'Delete'), sub: L('能恢复', 'Recoverable'), color: t.bad, go: menu },
  ];
  return (
    <Screen>
      <View style={[styles.head, { borderBottomColor: t.line }]}>
        <Pressable onPress={() => nav.goBack()} hitSlop={10} accessibilityRole="button" accessibilityLabel={L('返回', 'Back')} style={styles.back}><ChevronLeft size={26} color={t.gold} /></Pressable>
        <T v="headline" style={{ flex: 1 }}>{L('收藏', 'Saved')}</T>
        <Pressable onPress={menu} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('更多', 'More')} style={styles.back}><Ellipsis size={20} color={t.ink2} /></Pressable>
      </View>
      <ScrollView contentContainerStyle={{ padding: space.lg, gap: space.md, paddingBottom: space.xxl }} automaticallyAdjustKeyboardInsets keyboardShouldPersistTaps="handled">
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <TypeTile kind={s.kind} size={26} />
          <T v="caption" color={t.ink2} style={{ fontSize: 13 }}>{[s.source, `${dayLabel(s.day).split(' · ')[0]} ${s.time}`].filter(Boolean).join(' · ')}{L(' 存', ' saved')}</T>
        </View>
        <T v="title" style={{ fontSize: 22, fontWeight: '800', lineHeight: 29 }}>{s.title || s.name || L('（没有标题）', '(No title)')}</T>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
          {status ? <View style={[styles.pill, { backgroundColor: s.textStatus === 'ok' ? t.goodSoft : s.textStatus === 'fetching' ? t.surface2 : t.warnSoft }]}>
            {s.textStatus === 'ok' ? <Check size={13} color={t.good} strokeWidth={2.5} /> : null}
            <Text style={[type.caption, { color: s.textStatus === 'ok' ? t.good : s.textStatus === 'fetching' ? t.ink2 : t.warn, fontWeight: '700' }]}>{s.textStatus === 'ok' && s.kind === 'link' ? L('正文已存 · 原文删了也在', 'Text saved · survives deletion') : status}</Text>
          </View> : null}
        </View>
        {s.textNote && s.textStatus !== 'ok' ? <T v="caption" color={t.ink3}>{s.textNote}</T> : null}
        <View style={{ flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', gap: 6 }}>
          <T v="label" color={t.ink3} style={{ textTransform: 'uppercase', marginRight: 4 }}>{L('关键词', 'Keywords')}</T>
          {s.keywords.map((k) => <KeywordChip key={k} k={k} onPress={() => nav.navigate('ThinkKeyword', { k })} />)}
          <Pressable onPress={addKeyword} accessibilityRole="button" style={[styles.add, { borderColor: t.line }]}><Plus size={12} color={t.ink2} /><Text style={[type.caption, { color: t.ink2, fontWeight: '600' }]}>{L('加', 'Add')}</Text></Pressable>
        </View>
        <View style={{ gap: 6 }}>
          <T v="label" color={t.ink3} style={{ textTransform: 'uppercase' }}>{L('备注', 'Note')}</T>
          <GrowInput value={note} onChangeText={setNote} onEndEditing={() => { if (note !== s.note) updateSave(s.id, { note }).then(setS).catch((e) => showError(L('没存上', "Couldn't save"), e)); }}
            placeholder={L('加一句：为什么存它（可以不写，#词会变成关键词）', 'Why you saved it (optional; #words become keywords)')} placeholderTextColor={t.ink3} multiline
            accessibilityLabel={L('备注', 'Note')} style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink }]} />
        </View>
        {s.kind === 'image' && s.fileUrl ? <Image source={{ uri: s.fileUrl }} resizeMode="contain" style={{ width: '100%', height: 260, borderRadius: radius.md, backgroundColor: t.surface2 }} accessibilityLabel={s.name ?? ''} /> : null}
        {s.fileUrl && s.kind !== 'image' ? (
          <Pressable onPress={() => Linking.openURL(s.fileUrl!).catch(() => {})} accessibilityRole="button" style={[styles.file, { backgroundColor: t.surface }]}>
            <FileText size={20} color={t.cyan} />
            <View style={{ flex: 1 }}><T v="headline" numberOfLines={1} style={{ fontSize: 15 }}>{s.name}</T><T v="caption" color={t.ink3}>{L('点开原件', 'Open the original')}</T></View>
          </Pressable>
        ) : null}
        {s.url ? <Pressable onPress={() => Linking.openURL(s.url).catch(() => {})} accessibilityRole="link"><T v="callout" color={t.gold} numberOfLines={2}>{s.url}</T></Pressable> : null}
        {text ? (
          <View style={[styles.body, { backgroundColor: t.surface }]}>
            <T v="callout" color={t.ink2} style={{ lineHeight: 24 }}>{text}</T>
            {more ? <Pressable onPress={() => load(true)} accessibilityRole="button"><T v="callout" color={t.gold} style={{ fontWeight: '600' }}>{L(`展开全文 · 约 ${s.textLen.toLocaleString()} 字`, `Show all · about ${s.textLen.toLocaleString()} chars`)}</T></Pressable> : null}
          </View>
        ) : null}
        <T v="label" color={t.ink3} style={{ textTransform: 'uppercase', paddingTop: 4 }}>{L('怎么处理，你定', 'What to do with it: your call')}</T>
        <View style={styles.grid}>
          {actions.map(({ Icon, label, sub, color, go }) => (
            <Pressable key={label} onPress={go} disabled={busy} accessibilityRole="button" style={({ pressed }) => [styles.act, { backgroundColor: t.surface, borderColor: t.line, opacity: pressed || busy ? 0.7 : 1 }]}>
              <Icon size={19} color={color} />
              <T v="headline" style={{ fontSize: 15, color: color === t.bad ? t.bad : t.ink }}>{label}</T>
              <T v="caption" color={t.ink3} numberOfLines={2}>{sub}</T>
            </Pressable>
          ))}
        </View>
        <T v="caption" color={t.ink3} style={{ textAlign: 'center', lineHeight: 18 }}>{L('它不会自己读这条；你点了上面哪个，它才看。', "It won't read this on its own; it looks only when you pick one of the above.")}</T>
      </ScrollView>
    </Screen>
  );
}

function KeywordSheet({ have, close, onSave }: { have: string[]; close: () => void; onSave: (kws: string[]) => void }) {
  const t = useTheme();
  const [v, setV] = useState(have.join(' '));
  return (
    <View style={{ gap: space.md }}>
      <TextInput value={v} onChangeText={setV} autoFocus placeholder={L('关键词，空格分开', 'Keywords, space between')} placeholderTextColor={t.ink3}
        accessibilityLabel={L('关键词', 'Keywords')} style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink }]} />
      <Btn label={L('存', 'Save')} onPress={() => { onSave(v.split(/[\s,，、#]+/).map((x) => x.trim()).filter(Boolean)); close(); }} />
    </View>
  );
}

function DeleteBlock({ onDelete }: { onDelete: () => void }) {
  const t = useTheme();
  const [confirm, setConfirm] = useState(false);
  return (
    <View style={{ gap: space.sm }}>
      {confirm ? <Btn label={L('确认删掉', 'Confirm delete')} kind="danger" icon={<Trash2 size={15} color={t.bad} />} onPress={onDelete} />
        : <Btn label={L('删掉这条收藏', 'Delete this item')} kind="danger" icon={<Trash2 size={15} color={t.bad} />} onPress={() => setConfirm(true)} />}
      <T v="caption" color={t.ink3}>{L('从列表里拿掉；原件和正文在服务器上留着，能恢复。', 'Removed from the list; the original and its text stay on the server and can be restored.')}</T>
    </View>
  );
}

const styles = StyleSheet.create({
  head: { flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingHorizontal: space.sm, paddingVertical: space.sm, borderBottomWidth: StyleSheet.hairlineWidth },
  back: { width: 36, height: 36, alignItems: 'center', justifyContent: 'center' },
  pill: { flexDirection: 'row', alignItems: 'center', gap: 4, borderRadius: radius.pill, paddingHorizontal: 9, paddingVertical: 3 },
  add: { flexDirection: 'row', alignItems: 'center', gap: 3, height: 24, paddingHorizontal: 9, borderRadius: 12, borderWidth: 1, borderStyle: 'dashed' },
  input: { borderRadius: radius.md, paddingHorizontal: space.lg, paddingVertical: 12, minHeight: 44 },
  file: { flexDirection: 'row', alignItems: 'center', gap: space.md, borderRadius: radius.md, padding: space.md },
  body: { borderRadius: radius.lg - 2, padding: space.md, gap: 10 },
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 10 },
  act: { width: '48%', flexGrow: 1, borderRadius: radius.md + 2, borderWidth: StyleSheet.hairlineWidth, padding: space.md, gap: 5 },
  agent: { flexDirection: 'row', alignItems: 'center', gap: space.md, borderRadius: radius.md, padding: space.md },
});
