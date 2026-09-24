import React, { useState } from 'react';
import { Alert, StyleSheet, TextInput, View } from 'react-native';
import { Send } from './icons';
import type { Task } from '../data/types';
import { L } from '../i18n';
import { useStore } from '../store';
import { radius, space, type, useTheme } from '../theme';
import { modelOf } from './ModelPicker';
import { Btn, T } from './ui';

// 任务 = OpenClaw 的子会话。现在由 Grava 在对话里用 sessions_spawn 派出去；app 负责看过程、取消、发修改意见。
// （原型里在对话中编辑任务卡草稿、一键派发的流程要等 Grava 能输出结构化任务卡之后再接回来。）

export const originName = (origin: string, groups: { id: string; name: string }[], sideChats: { id: string; title: string }[]) =>
  origin === 'main' ? L('主对话', 'Main chat') : groups.find((g) => g.id === origin)?.name ?? sideChats.find((c) => c.id === origin)?.title ?? origin;
export const fmtTokens = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));
export const modelName = (id: string | null | undefined) => (id ? modelOf(id)?.short ?? id.split('/').pop() ?? id : '—');

/** 修改意见弹层：发给同一个子会话。 */
export function ReviseSheetContent({ task, close }: { task: Task; close: () => void }) {
  const t = useTheme();
  const { reviseTask } = useStore();
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = () => {
    if (!note.trim() || busy) return;
    setBusy(true);
    reviseTask(task.id, note.trim()).then(close).catch((e) => Alert.alert(L('没发出去', "Couldn't send"), e instanceof Error ? e.message : String(e))).finally(() => setBusy(false));
  };
  return (
    <View style={{ gap: space.md }}>
      <T v="callout" color={t.ink2}>{L(`意见会发给做这件事的同一个子会话（${modelName(task.modelId)}），它记得前面做了什么，只改你说的部分。`, `Your notes go to the same sub-session that did this (${modelName(task.modelId)}). It remembers what it did and only changes what you point out.`)}</T>
      <TextInput value={note} onChangeText={setNote} multiline autoFocus placeholder={L('哪里要改', 'What should change')} placeholderTextColor={t.ink3}
        accessibilityLabel={L('修改意见', 'Revision notes')} style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink, minHeight: 88, textAlignVertical: 'top' }]} />
      <Btn label={busy ? L('发送中…', 'Sending…') : L(`发给 ${modelName(task.modelId)}`, `Send to ${modelName(task.modelId)}`)} icon={<Send size={16} color={t.onGold} />} onPress={submit} />
    </View>
  );
}

const styles = StyleSheet.create({
  input: { borderRadius: radius.md, paddingHorizontal: space.md, paddingVertical: 10 },
});
