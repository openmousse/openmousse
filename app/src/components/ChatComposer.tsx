// 对话底下那一行输入框（ChatView 用）：草稿、语音输入、发送 / 停止键。
// 草稿只记在这个组件里，每按一个键只重画这一行，上面的消息列表一概不动。以前草稿记在 ChatView，打字整页跟着重画，
// 全靠消息列表的 useMemo 挡着；10/5 转发功能给它加了一个每次都变的依赖，挡不住了，主对话一天 40 条长回复，每个字都要把整段 Markdown 重新解析一遍。
// ChatView 要改草稿、放光标走 handle（ComposerHandle）；传进来的回调要稳定（ChatView 用 handlers ref 包着），不然 memo 白做。
import React, { useEffect, useImperativeHandle, useRef, useState } from 'react';
import { Alert, Platform, Pressable, StyleSheet, TextInput, View, type NativeSyntheticEvent, type TextInputKeyPressEventData } from 'react-native';
import * as Haptics from 'expo-haptics';
import { AudioModule, RecordingPresets, setAudioModeAsync, useAudioRecorder, useAudioRecorderState } from 'expo-audio';
import { chatsApi, type SlashCommand } from '../api/chats';
import { supports } from '../api/version';
import { agentName } from '../brand';
import type { PendingFile } from '../data/types';
import { loadDraft, saveDraft } from '../drafts';
import { L } from '../i18n';
import type { ChatQuote } from '../navigation';
import { space, type, useTheme } from '../theme';
import { ArrowUp, Mic, Paperclip, Square } from './icons';
import { T } from './ui';

// 语音输入只要听清人声：16 kHz 单声道 32 kbps 的 AAC，比默认的 44.1 kHz 立体声 128 kbps 小 6 倍，上传快得多，转写质量不受影响。
const SPEECH_PRESET = { ...RecordingPresets.HIGH_QUALITY, sampleRate: 16000, numberOfChannels: 1, bitRate: 32000 };
const canRecord = Platform.OS !== 'web';

export interface ComposerHandle {
  /** 换掉输入框里的字（重新编辑：撤回的那条放回来） */
  set: (text: string) => void;
  /** 输入框空着才填（任务卡「接着做」先填一句） */
  fill: (text: string) => void;
  /** 放到已有的字前面（回复里选中一段点「引用这段」：那段在最前面，草稿留着） */
  prepend: (text: string) => void;
  focus: () => void;
}

export const Composer = React.memo(function Composer({ handle, threadId, placeholder, quote, busy, hasFiles, hidden, onSubmit, onStop, onAttach, transcribe }: {
  handle: React.Ref<ComposerHandle>;
  threadId: string;
  placeholder: string;
  /** 输入框上面带着的引用：决定提示文字，「改一下任务」时没有字不能发 */
  quote: ChatQuote | null;
  /** 它正在回复：输入框空着时发送键换成「停」 */
  busy: boolean;
  /** 带着附件：没有字也能发 */
  hasFiles: boolean;
  /** 多选消息时输入栏让给转发条：只是不画，草稿和录音都还在 */
  hidden?: boolean;
  /** 发出去：返回 true（或 Promise 给 true）就清空输入框，false 留着 */
  onSubmit: (text: string) => boolean | Promise<boolean>;
  onStop: () => void;
  onAttach: () => void;
  transcribe: (f: PendingFile) => Promise<string>;
}) {
  const t = useTheme();
  const input = useRef<TextInput>(null);
  const [draft, setDraft] = useState(() => loadDraft(threadId));  // 没发出去的草稿存在本机（drafts.ts），退出 app 也还在
  // 同一个 ChatView 换了线程（Agent 页没按线程重建）：换成那个线程的草稿，别把这边的带过去
  const [draftOf, setDraftOf] = useState(threadId);
  if (draftOf !== threadId) {
    setDraftOf(threadId);
    setDraft(loadDraft(threadId));
  }
  useEffect(() => { saveDraft(threadId, draft); }, [threadId, draft]);
  const [transcribing, setTranscribing] = useState(false);
  const recorder = useAudioRecorder(SPEECH_PRESET);
  const rec = useAudioRecorderState(recorder, 250);
  // 斜杠命令（服务器 supports('chat.commands')）：还在打命令名（/ 开头、没有空白）时列出这个对话能用的，点了补进输入框。
  // 列表每个线程只取一次；取不到当没有。
  const [cmds, setCmds] = useState<{ thread: string; list: SlashCommand[] } | null>(null);
  const naming = /^\/[^\s/]*$/.test(draft);
  const wantCmds = naming && cmds?.thread !== threadId && supports('chat.commands');
  useEffect(() => {
    if (!wantCmds) return;
    let live = true;
    chatsApi.commands(threadId)
      .then((list) => { if (live) setCmds({ thread: threadId, list }); })
      .catch(() => { if (live) setCmds({ thread: threadId, list: [] }); });
    return () => { live = false; };
  }, [wantCmds, threadId]);
  const typedName = draft.slice(1).toLowerCase();
  const hints = naming && cmds?.thread === threadId
    ? cmds.list.filter((c) => c.name.startsWith(typedName) || c.aliases.some((a) => a.startsWith(typedName))).slice(0, 6)
    : [];

  useImperativeHandle(handle, () => ({
    set: (text) => setDraft(text),
    fill: (text) => setDraft((d) => (d.trim() ? d : text)),
    prepend: (text) => setDraft((d) => `${text}${d}`),
    focus: () => input.current?.focus(),
  }), []);

  const fail = (e: unknown) => Alert.alert(L('操作失败', "Couldn't complete the action"), e instanceof Error ? e.message : String(e));

  const submit = () => {
    if (transcribing) return;
    const r = onSubmit(draft.trim());
    if (r === true) setDraft('');
    else if (r) r.then((ok) => { if (ok) setDraft(''); }).catch(() => {});
  };

  const startRecording = async () => {
    try {
      const perm = await AudioModule.requestRecordingPermissionsAsync();
      if (!perm.granted) { Alert.alert(L('无麦克风权限', 'No microphone access'), L(`请在系统设置中为 ${agentName()} 开启麦克风权限。`, `Turn on the microphone for ${agentName()} in Settings.`)); return; }
      await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true });
      await recorder.prepareToRecordAsync();
      recorder.record();
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => {});
    } catch (e) { fail(e); }
  };

  const stopRecording = async () => {
    try {
      await recorder.stop();
      await setAudioModeAsync({ allowsRecording: false, playsInSilentMode: true });
      const uri = recorder.uri;
      if (!uri) return;
      setTranscribing(true);
      const text = await transcribe({ uri, name: 'voice.m4a', mime: 'audio/m4a', size: 0 });
      if (text) setDraft((d) => (d.trim() ? `${d.trim()} ${text}` : text));
      else Alert.alert(L('未识别到语音', 'No speech detected'), L('录音中未识别出文字。', 'No speech was recognized in the recording.'));
    } catch (e) { fail(e); } finally { setTranscribing(false); }
  };

  if (hidden) return null;

  const typed = !!draft.trim();
  // 它正在回复也能发（服务器排队，回完一起发给它）；改一下任务不经过这个对话，要有字
  const canSend = quote?.taskId ? typed && !transcribing : (typed || hasFiles) && !transcribing;
  // 正在回复、输入框空着：发送键换成「停」
  const showStop = busy && !typed && !hasFiles && !quote?.taskId;
  // 手机上回车键是「发送」，发完键盘留着接着打（submitBehavior）。网页的多行输入框不认 submitBehavior，
  // 自己接 Enter：发送、不丢焦点；Shift+Enter 换行；输入法选字时按的 Enter 不算。
  const webEnter = Platform.OS === 'web' ? (e: NativeSyntheticEvent<TextInputKeyPressEventData>) => {
    const k = e.nativeEvent as unknown as KeyboardEvent;
    if (k.key !== 'Enter' || k.shiftKey || k.isComposing || k.keyCode === 229) return;
    e.preventDefault();
    submit();
  } : undefined;
  const secs = Math.floor((rec.durationMillis ?? 0) / 1000);

  if (rec.isRecording) {
    return (
      <View style={styles.composer}>
        <View style={[styles.recording, { backgroundColor: t.surface }]}>
          <View style={{ width: 10, height: 10, borderRadius: 5, backgroundColor: t.bad }} />
          <T v="body" style={{ flex: 1, fontVariant: ['tabular-nums'] }}>{L('正在录音', 'Recording')} {Math.floor(secs / 60)}:{String(secs % 60).padStart(2, '0')}</T>
          <T v="caption" color={t.ink3}>{L('点击停止结束录音，转写后可编辑', 'Tap stop when done, then edit the text')}</T>
        </View>
        <Pressable onPress={stopRecording} accessibilityRole="button" accessibilityLabel={L('停止录音', 'Stop recording')} style={[styles.send, { backgroundColor: t.bad }]}>
          <Square size={16} color="#fff" fill="#fff" />
        </Pressable>
      </View>
    );
  }
  // 输入框上面那一小块：命令的名字、参数写法、一句说明（ChatView 把输入栏这一列竖着排，和引用条、附件同一列）
  const hintList = hints.length ? (
    <View style={[styles.hints, { backgroundColor: t.surface }]}>
      {hints.map((c) => (
        <Pressable key={c.name} onPress={() => { setDraft(`/${c.name} `); input.current?.focus(); }} accessibilityRole="button"
          accessibilityLabel={`/${c.name} ${c.description}`} style={styles.hint}>
          <T v="body" numberOfLines={1}>
            <T v="body" style={{ fontWeight: '600' }}>/{c.name}</T>{c.args ? <T v="body" color={t.ink3}> {c.args}</T> : null}
          </T>
          <T v="caption" color={t.ink2} numberOfLines={1}>{c.description}</T>
        </Pressable>
      ))}
    </View>
  ) : null;
  return (
    <>
    {hintList}
    <View style={styles.composer}>
      <Pressable onPress={onAttach} hitSlop={6} accessibilityRole="button" accessibilityLabel={L('添加附件', 'Add attachment')} style={styles.iconBtn}>
        <Paperclip size={22} color={t.ink2} />
      </Pressable>
      <TextInput
        ref={input}
        value={draft} onChangeText={setDraft} placeholder={transcribing ? L('正在转写…', 'Transcribing…') : quote?.follow ? L('询问进展或补充要求', "Ask about progress or add what's missing")
          : quote?.replyTo ? L('回复这条消息…', 'Reply to this message…') : quote ? L('说明需要修改的内容', 'Describe the changes')
            : busy ? L('正在回复，可继续发送', 'Replying. You can keep sending.') : placeholder} placeholderTextColor={t.ink3}
        multiline numberOfLines={1} onSubmitEditing={submit} submitBehavior="submit" returnKeyType="send" enablesReturnKeyAutomatically onKeyPress={webEnter}
        accessibilityLabel={L('消息输入框', 'Message')} editable={!transcribing}
        style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink }]}
      />
      {showStop ? (
        <Pressable onPress={onStop} accessibilityRole="button" accessibilityLabel={L('停止回复', 'Stop the reply')}
          style={[styles.send, { backgroundColor: t.surface2 }]}>
          <Square size={14} color={t.ink} fill={t.ink} />
        </Pressable>
      ) : canRecord && !typed && !hasFiles && !transcribing ? (
        <Pressable onPress={startRecording} disabled={busy} accessibilityRole="button" accessibilityLabel={L('语音输入', 'Voice input')}
          style={[styles.send, { backgroundColor: t.surface2 }]}>
          <Mic size={20} color={busy ? t.ink3 : t.ink} />
        </Pressable>
      ) : (
        <Pressable onPress={submit} disabled={!canSend} accessibilityRole="button" accessibilityLabel={L('发送', 'Send')}
          style={[styles.send, { backgroundColor: canSend ? t.goldFill : t.surface2 }]}>
          <ArrowUp size={20} color={canSend ? t.onGold : t.ink3} />
        </Pressable>
      )}
    </View>
    </>
  );
});

const styles = StyleSheet.create({
  composer: { flexDirection: 'row', alignItems: 'flex-end', gap: space.sm, paddingHorizontal: space.md, paddingVertical: space.sm },
  input: { flex: 1, minHeight: 40, maxHeight: 120, borderRadius: 20, paddingHorizontal: 16, paddingTop: 9, paddingBottom: 9 },
  send: { width: 40, height: 40, borderRadius: 20, alignItems: 'center', justifyContent: 'center' },
  iconBtn: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center' },
  recording: { flex: 1, flexDirection: 'row', alignItems: 'center', gap: 8, minHeight: 40, borderRadius: 20, paddingHorizontal: 14 },
  hints: { marginHorizontal: space.md, marginTop: space.sm, borderRadius: 16, paddingVertical: 4, overflow: 'hidden' },
  hint: { paddingHorizontal: 16, paddingVertical: 8, gap: 2 },
});
