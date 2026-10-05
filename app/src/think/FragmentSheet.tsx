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
    try { await job(); } catch (e) { showError(L('操作失败', "Couldn't complete that"), e); } finally { setBusy(false); }
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
            <GrowInput value={text} onChangeText={setText} multiline autoFocus accessibilityLabel={L('想法内容', 'Thought text')}
              style={[type.body, styles.input, { minHeight: 100, maxHeight: 280, backgroundColor: t.surface, color: t.ink }]} />
          ) : null}
          <TextInput value={kw} onChangeText={setKw} placeholder={L('关键词，以空格分隔', 'Keywords, separated by spaces')} placeholderTextColor={t.ink3}
            accessibilityLabel={L('关键词', 'Keywords')} style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink }]} />
          <View style={{ flexDirection: 'row', gap: space.sm }}>
            <Btn label={L('取消', 'Cancel')} kind="quiet" flex onPress={() => { setEditing(false); setText(f.text); setKw(f.keywords.join(' ')); }} />
            <Btn label={busy ? L('正在保存…', 'Saving…') : L('保存', 'Save')} flex onPress={save} />
          </View>
          <T v="caption" color={t.ink3}>{L('修改将直接写入库中的笔记，Obsidian 中同步更新。', 'Edits apply to the note in the vault and sync to Obsidian.')}</T>
        </View>
      ) : (
        <>
          <FragmentCard f={f} full onKeyword={(k) => { close(); nav.navigate('ThinkKeyword', { k }); }} />
          {topics.map((tp) => (
            <Pressable key={tp.id} onPress={() => { close(); nav.navigate('ThinkTalk', { id: tp.id }); }} accessibilityRole="button"
              style={({ pressed }) => [styles.row, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
              <Lightbulb size={17} color={t.gold} />
              <T v="callout" style={{ flex: 1 }} numberOfLines={1}>{L(`所属主题「${tp.title}」`, `In the topic "${tp.title}"`)}</T>
            </Pressable>
          ))}
          <View style={{ flexDirection: 'row', gap: space.sm }}>
            <Btn label={L('编辑', 'Edit')} kind="quiet" flex icon={<Pencil size={15} color={t.ink} />} onPress={() => setEditing(true)} />
            <Btn label={L('单独讨论', 'Discuss alone')} kind="quiet" flex icon={<MessageCircle size={15} color={t.ink} />} onPress={talkAlone} />
          </View>
          {topicId ? <>
            <Btn label={busy ? L('正在处理…', 'Processing…') : L('移出此主题', 'Remove from this topic')} kind="quiet" onPress={() => run(async () => {
              await thinkApi.patchTopic(topicId, { remove: [id] });
              onRemoved?.();
              await refresh();
              close();
            })} />
            <T v="caption" color={t.ink3}>{L('仅从当前主题移出，该想法仍保留在碎片流和库中。', 'Removes it from this topic only; the thought stays in the stream and the vault.')}</T>
          </> : null}
          {confirm
            ? <Btn label={L('确认删除（移至库的回收站）', 'Confirm: move to the vault trash')} kind="danger" icon={<Trash2 size={15} color={t.bad} />} onPress={() => run(async () => { await removeFragment(id); close(); })} />
            : <Btn label={L('删除', 'Delete')} kind="danger" icon={<Trash2 size={15} color={t.bad} />} onPress={() => setConfirm(true)} />}
          <T v="caption" color={t.ink3}>{f.bad ? L('此笔记的属性格式有误，需在 Obsidian 中修复后才能编辑。', "This note's properties are malformed. Fix them in Obsidian before editing here.") : L(`库中路径：${f.path}`, `In the vault: ${f.path}`)}</T>
        </>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  input: { borderRadius: radius.md, paddingHorizontal: space.lg, paddingVertical: 12, textAlignVertical: 'top' },
  row: { flexDirection: 'row', alignItems: 'center', gap: space.sm, borderRadius: radius.md, paddingHorizontal: space.md, paddingVertical: 12 },
});
