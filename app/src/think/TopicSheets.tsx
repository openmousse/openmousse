// Shared topic actions: deletion only unlinks thoughts and can be undone here.
import React, { useEffect, useRef, useState } from 'react';
import { TextInput, View } from 'react-native';
import { Btn, T, showError } from '../components/ui';
import * as thinkApi from '../api/think';
import type { TopicBrief } from '../api/think';
import { L } from '../i18n';
import { radius, space, type, useTheme } from '../theme';

type Props = { topic: TopicBrief | null; close: () => void; onDone: () => void };

export function RenameSheet({ topic, close, onDone }: Props) {
  const t = useTheme();
  const [v, setV] = useState(topic?.title ?? '');
  const [busy, setBusy] = useState(false);
  const input = useRef<TextInput>(null);
  // 不用 autoFocus：弹层打开时会先收起对话输入栏的键盘，同一刻再弹键盘会和弹层升起的动画打架（键盘上下跳、弹层被顶一下）。
  // 等弹层升到位（动画 300ms）再聚焦，键盘只弹一次，弹层跟着键盘上沿走。
  useEffect(() => { const id = setTimeout(() => input.current?.focus(), 350); return () => clearTimeout(id); }, []);
  const save = async () => {
    if (!topic || !v.trim() || busy) return;
    setBusy(true);
    try { await thinkApi.patchTopic(topic.id, { title: v.trim() }); onDone(); close(); }
    catch (e) { showError(L('没改成', "Couldn't rename"), e); }
    finally { setBusy(false); }
  };
  if (!topic) return null;
  return (
    <View style={{ gap: space.md }}>
      <TextInput ref={input} value={v} onChangeText={setV} selectTextOnFocus returnKeyType="done" onSubmitEditing={save} blurOnSubmit={false}
        maxLength={40} accessibilityLabel={L('主题名字', 'Topic name')}
        style={[type.body, { borderRadius: radius.md, paddingHorizontal: space.lg, paddingVertical: 12, backgroundColor: t.surface, color: t.ink }]} />
      <Btn label={busy ? L('正在改…', 'Renaming…') : L('改', 'Rename')} onPress={save} />
    </View>
  );
}

export function TopicActionsSheet({ topic, close, onDone }: Props) {
  const t = useTheme();
  const [mode, setMode] = useState<'actions' | 'rename' | 'confirm' | 'deleted'>('actions');
  const [busy, setBusy] = useState(false);
  const change = async (undo: boolean) => {
    if (!topic || busy) return;
    setBusy(true);
    try {
      if (undo) await thinkApi.patchTopic(topic.id, { status: 'open' });
      else await thinkApi.deleteTopic(topic.id);
      onDone();
      if (undo) close(); else setMode('deleted');
    } catch (e) { showError(undo ? L('没恢复', "Couldn't restore") : L('没删掉', "Couldn't delete"), e); }
    finally { setBusy(false); }
  };
  if (mode === 'rename') return <RenameSheet topic={topic} close={close} onDone={onDone} />;
  return (
    <View style={{ gap: space.md }}>
      {mode === 'actions' ? <>
        <Btn label={L('改名', 'Rename')} kind="quiet" onPress={() => setMode('rename')} />
        <Btn label={L('删除', 'Delete')} kind="danger" onPress={() => setMode('confirm')} />
      </> : mode === 'confirm' ? <>
        <T v="body">{L('删除这个主题？碎片会回到碎片流，不会被删；对话记录也会保留。', 'Delete this topic? Its thoughts return to the stream, not the trash. The conversation is also kept.')}</T>
        <Btn label={busy ? L('正在删除…', 'Deleting…') : L('确认删除主题', 'Delete topic')} kind="danger" onPress={() => change(false)} />
        <Btn label={L('算了', 'Cancel')} kind="quiet" onPress={close} />
      </> : <>
        <T v="body" color={t.ink2}>{L('主题已删除，碎片和对话记录都还在。', 'Topic deleted. Thoughts and the conversation are kept.')}</T>
        <Btn label={busy ? L('正在恢复…', 'Restoring…') : L('撤销', 'Undo')} onPress={() => change(true)} />
        <Btn label={L('完成', 'Done')} kind="quiet" onPress={close} />
      </>}
    </View>
  );
}
