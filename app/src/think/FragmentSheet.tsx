// 点开一条想法：看全文、改文字和关键词、单独聊这一条、删（挪进库的回收站）。
import React, { useState } from 'react';
import { Pressable, StyleSheet, TextInput, View } from 'react-native';
import { Lightbulb, MessageCircle, Pencil, Trash2 } from '../components/icons';
import { Btn, T, showError } from '../components/ui';
import * as thinkApi from '../api/think';
import type { Fragment } from '../api/think';
import { L } from '../i18n';
import { radius, space, type, useTheme } from '../theme';
import { FragmentCard, GrowInput } from './parts';
import { navigationRef } from '../navigation';
import { useThink } from './ThinkStore';

export function FragmentSheet({ id, initial, close, topicId, onRemoved }: { id: string; initial: Fragment; close: () => void; topicId?: string; onRemoved?: () => void }) {
  const t = useTheme();
  const nav = navigationRef;  // 弹层画在导航容器外面，用不了 useNavigation
  const { editFragment, removeFragment, openTopic, stream, refresh } = useThink();
  const [f, setF] = useState(initial);
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(initial.text);
  const [kw, setKw] = useState(initial.keywords.join(' '));
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const run = async (job: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    try { await job(); } catch (e) { showError(L('没做成', "Couldn't do that"), e); } finally { setBusy(false); }
  };
  const save = () => run(async () => {
    const keywords = kw.split(/[\s,，、#]+/).map((x) => x.trim()).filter(Boolean);
    const next = await editFragment(id, f.kind === 'keywords' ? { keywords } : { text, keywords });
    setF(next);
    setEditing(false);
  });
  const talkAlone = () => run(async () => {
    const tp = await openTopic([id]);
    await thinkApi.talk(tp.id);
    close();
    nav.navigate('ThinkTalk', { id: tp.id });
  });
  const topics = (stream?.topics ?? []).filter((x) => f.topics.includes(x.id));
  return (
    <View style={{ gap: space.md }}>
      {editing ? (
        <View style={{ gap: space.sm }}>
          {f.kind !== 'keywords' ? (
            <GrowInput value={text} onChangeText={setText} multiline autoFocus accessibilityLabel={L('这条想法', 'This thought')}
              style={[type.body, styles.input, { minHeight: 100, maxHeight: 280, backgroundColor: t.surface, color: t.ink }]} />
          ) : null}
          <TextInput value={kw} onChangeText={setKw} placeholder={L('关键词，空格分开', 'Keywords, space between')} placeholderTextColor={t.ink3}
            accessibilityLabel={L('关键词', 'Keywords')} style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink }]} />
          <View style={{ flexDirection: 'row', gap: space.sm }}>
            <Btn label={L('算了', 'Cancel')} kind="quiet" flex onPress={() => { setEditing(false); setText(f.text); setKw(f.keywords.join(' ')); }} />
            <Btn label={busy ? L('正在存…', 'Saving…') : L('存', 'Save')} flex onPress={save} />
          </View>
          <T v="caption" color={t.ink3}>{L('改的是库里那篇笔记本身，Obsidian 里跟着变。', 'This edits the note in the vault; Obsidian follows.')}</T>
        </View>
      ) : (
        <>
          <FragmentCard f={f} full onKeyword={(k) => { close(); nav.navigate('ThinkKeyword', { k }); }} />
          {topics.map((tp) => (
            <Pressable key={tp.id} onPress={() => { close(); nav.navigate('ThinkTalk', { id: tp.id }); }} accessibilityRole="button"
              style={({ pressed }) => [styles.row, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
              <Lightbulb size={17} color={t.gold} />
              <T v="callout" style={{ flex: 1 }} numberOfLines={1}>{L(`在主题「${tp.title}」里`, `In the topic "${tp.title}"`)}</T>
            </Pressable>
          ))}
          <View style={{ flexDirection: 'row', gap: space.sm }}>
            <Btn label={L('改一下', 'Edit')} kind="quiet" flex icon={<Pencil size={15} color={t.ink} />} onPress={() => setEditing(true)} />
            <Btn label={L('单独聊聊', 'Talk about it')} kind="quiet" flex icon={<MessageCircle size={15} color={t.ink} />} onPress={talkAlone} />
          </View>
          {topicId ? <>
            <Btn label={busy ? L('正在处理…', 'Working…') : L('移出这个主题', 'Remove from this topic')} kind="quiet" onPress={() => run(async () => {
              await thinkApi.patchTopic(topicId, { remove: [id] });
              onRemoved?.();
              await refresh();
              close();
            })} />
            <T v="caption" color={t.ink3}>{L('只移出当前主题，碎片仍在碎片流和库里。', 'Only removes it from this topic; the thought stays in the stream and vault.')}</T>
          </> : null}
          {confirm
            ? <Btn label={L('确认删掉（挪进库的回收站）', 'Confirm: move to the vault trash')} kind="danger" icon={<Trash2 size={15} color={t.bad} />} onPress={() => run(async () => { await removeFragment(id); close(); })} />
            : <Btn label={L('删掉', 'Delete')} kind="danger" icon={<Trash2 size={15} color={t.bad} />} onPress={() => setConfirm(true)} />}
          <T v="caption" color={t.ink3}>{f.bad ? L('这篇笔记的属性格式坏了，要在 Obsidian 里修好才能改。', "This note's properties are malformed; fix it in Obsidian before editing here.") : L(`在库里：${f.path}`, `In the vault: ${f.path}`)}</T>
        </>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  input: { borderRadius: radius.md, paddingHorizontal: space.lg, paddingVertical: 12, textAlignVertical: 'top' },
  row: { flexDirection: 'row', alignItems: 'center', gap: space.sm, borderRadius: radius.md, paddingHorizontal: space.md, paddingVertical: 12 },
});
