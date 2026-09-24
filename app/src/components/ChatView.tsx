import React, { useEffect, useRef, useState } from 'react';
import { agentName } from '../brand';
import { useNavigation } from '@react-navigation/native';
import { Alert, Image, KeyboardAvoidingView, Linking, Platform, Pressable, RefreshControl, ScrollView, StyleSheet, TextInput, View } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import * as Haptics from 'expo-haptics';
import * as DocumentPicker from 'expo-document-picker';
import * as ImagePicker from 'expo-image-picker';
import { AudioModule, RecordingPresets, setAudioModeAsync, useAudioRecorder, useAudioRecorderState } from 'expo-audio';
import { ArrowUp, Camera, Copy, FileAudio, FileText, Film, ImageIcon, Mic, Paperclip, Pencil, Square, Trash2, Undo2, X } from './icons';
import type { Attachment, Message, PendingFile } from '../data/types';
import { useStore } from '../store';
import { radius, space, type, useTheme } from '../theme';
import { LensAvatar } from './LensAvatar';
import { modelOf } from './ModelPicker';
import { useSheet } from './Sheet';
import { T } from './ui';
import { Markdown } from './Markdown';

// 上限对齐主流 LLM 产品（服务端 files.py 同样的数）：一条消息 10 个附件，每个 30 MB，类型不限。
const MAX_FILES = 10;
const MAX_BYTES = 30 * 1024 * 1024;
const PLACEHOLDER_TEXT = '（见附件）';
// 语音输入只要听清人声：16 kHz 单声道 32 kbps 的 AAC，比默认的 44.1 kHz 立体声 128 kbps 小 6 倍，上传快得多，转写质量不受影响。
const SPEECH_PRESET = { ...RecordingPresets.HIGH_QUALITY, sampleRate: 16000, numberOfChannels: 1, bitRate: 32000 };

const human = (n: number) => (n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);
const KIND_ICON = { doc: FileText, audio: FileAudio, video: Film, file: Paperclip, image: ImageIcon } as const;

function ModelTag({ m }: { m: Message }) {
  const t = useTheme();
  const model = modelOf(m.modelId);
  if (!model) {
    return m.modelId || m.error ? <T v="caption" color={m.error ? t.bad : t.ink3} style={{ marginTop: 4 }}>{m.modelId ?? ''}{m.error ? ` · 出错：${m.error}` : ''} · {m.time}</T> : null;
  }
  const from = modelOf(m.fallbackFrom);
  return (
    <T v="caption" color={m.error ? t.bad : from ? t.warn : t.ink3} style={{ marginTop: 4 }}>
      {model.short} · {model.billing}{from ? ` · 请求的是 ${from.short}，回退链换成了它` : ''}{m.error ? ` · 出错：${m.error}` : ''} · {m.time}
    </T>
  );
}

function FileChip({ a, onPress, onRemove }: { a: Attachment; onPress?: () => void; onRemove?: () => void }) {
  const t = useTheme();
  const Icon = KIND_ICON[a.kind] ?? Paperclip;
  return (
    <Pressable onPress={onPress} accessibilityRole={onPress ? 'button' : undefined} accessibilityLabel={`附件 ${a.name}`}
      style={[styles.chip, { backgroundColor: t.surface, borderColor: t.line }]}>
      <Icon size={16} color={t.cyan} />
      <View style={{ flexShrink: 1 }}>
        <T v="callout" numberOfLines={1}>{a.name}</T>
        <T v="caption" color={a.status === 'warn' ? t.warn : t.ink3} numberOfLines={1}>{human(a.size)}{a.note ? ` · ${a.note}` : ''}</T>
      </View>
      {onRemove ? <Pressable onPress={onRemove} hitSlop={8} accessibilityRole="button" accessibilityLabel={`去掉 ${a.name}`}><X size={14} color={t.ink3} /></Pressable> : null}
    </Pressable>
  );
}

/** 消息里的附件：图片给缩略图（点开原图），其它给文件条。 */
function AttachmentList({ items, mine }: { items: Attachment[]; mine: boolean }) {
  const t = useTheme();
  const images = items.filter((a) => a.kind === 'image');
  const others = items.filter((a) => a.kind !== 'image');
  const open = (a: Attachment) => { if (a.url.startsWith('http')) Linking.openURL(a.url).catch(() => {}); };
  const side = images.length === 1 ? 200 : 96;
  return (
    <View style={{ gap: 6, alignItems: mine ? 'flex-end' : 'flex-start', maxWidth: '82%' }}>
      {images.length ? (
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6, justifyContent: mine ? 'flex-end' : 'flex-start' }}>
          {images.map((a) => (
            <Pressable key={a.id} onPress={() => open(a)} accessibilityRole="imagebutton" accessibilityLabel={a.name}>
              <Image source={{ uri: a.url.includes('/api/files/') ? `${a.url}${a.url.includes('?') ? '&' : '?'}thumb=1` : a.url }} resizeMode="cover"
                style={{ width: side, height: side, borderRadius: radius.md, backgroundColor: t.surface2 }} />
            </Pressable>
          ))}
        </View>
      ) : null}
      {others.map((a) => <FileChip key={a.id} a={a} onPress={() => open(a)} />)}
    </View>
  );
}

export function Bubble({ m, showAvatar, onLongPress }: { m: Message; showAvatar: boolean; onLongPress?: () => void }) {
  const t = useTheme();
  const { avatar } = useStore();
  const att = m.body.attachments ?? [];
  const text = att.length && m.body.text === PLACEHOLDER_TEXT ? '' : m.body.text;
  if (m.role === 'auto') {
    return (
      <View style={{ alignItems: 'center', paddingVertical: 2 }}>
        <T v="caption" color={t.ink3} style={{ textAlign: 'center' }}>{text.startsWith('【主对话转来】') ? '↪ 主对话转来：' : '⚙ '}{text.replace(/^【(自动触发|主对话转来)】/, '')} · {m.time}</T>
      </View>
    );
  }
  if (m.role === 'user') {
    return (
      <View style={{ alignItems: 'flex-end', gap: 6 }}>
        {att.length ? <AttachmentList items={att} mine /> : null}
        {text ? (
          <Pressable onLongPress={onLongPress} delayLongPress={350} accessibilityHint="长按可以复制、撤回、重新编辑或删除"
            style={({ pressed }) => [styles.userBubble, { backgroundColor: t.surface2, opacity: pressed ? 0.75 : 1 }]}>
            <T v="body">{text}</T>
          </Pressable>
        ) : null}
      </View>
    );
  }
  return (
    <Pressable onLongPress={onLongPress} delayLongPress={350} style={{ flexDirection: 'row', gap: space.sm }}>
      <View style={{ width: 28 }}>{showAvatar ? <LensAvatar size={28} config={avatar} /> : null}</View>
      <View style={{ flex: 1 }}>
        <Markdown text={m.body.text} />
        {att.length ? <View style={{ marginTop: 6 }}><AttachmentList items={att} mine={false} /></View> : null}
        <ModelTag m={m} />
      </View>
    </Pressable>
  );
}

function Action({ icon: Icon, label, note, danger, onPress }: { icon: typeof Copy; label: string; note?: string; danger?: boolean; onPress: () => void }) {
  const t = useTheme();
  const color = danger ? t.bad : t.ink;
  return (
    <Pressable onPress={onPress} accessibilityRole="button" accessibilityLabel={label}
      style={({ pressed }) => [styles.action, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
      <Icon size={20} color={color} />
      <View style={{ flex: 1, gap: 2 }}>
        <T v="headline" color={color}>{label}</T>
        {note ? <T v="callout" color={t.ink2}>{note}</T> : null}
      </View>
    </Pressable>
  );
}

// —— 选文件 ——

async function pickDocuments(): Promise<PendingFile[]> {
  const res = await DocumentPicker.getDocumentAsync({ type: '*/*', multiple: true, copyToCacheDirectory: true });
  if (res.canceled) return [];
  return res.assets.map((a) => ({ uri: a.uri, name: a.name, mime: a.mimeType ?? '', size: a.size ?? 0, file: (a as { file?: File }).file }));
}

async function pickMedia(camera: boolean): Promise<PendingFile[]> {
  const perm = camera ? await ImagePicker.requestCameraPermissionsAsync() : await ImagePicker.requestMediaLibraryPermissionsAsync();
  if (!perm.granted) throw new Error(camera ? '没有相机权限，去系统设置里打开。' : '没有相册权限，去系统设置里打开。');
  const res = camera
    ? await ImagePicker.launchCameraAsync({ mediaTypes: ['images', 'videos'], quality: 0.9 })
    : await ImagePicker.launchImageLibraryAsync({ mediaTypes: ['images', 'videos'], allowsMultipleSelection: true, selectionLimit: MAX_FILES, quality: 0.9 });
  if (res.canceled) return [];
  return res.assets.map((a, i) => {
    const ext = a.uri.split('?')[0].split('.').pop()?.toLowerCase() || (a.type === 'video' ? 'mov' : 'jpg');
    return { uri: a.uri, name: a.fileName ?? `${a.type === 'video' ? 'video' : 'photo'}-${Date.now()}-${i}.${ext}`,
      mime: a.mimeType ?? (a.type === 'video' ? 'video/quicktime' : 'image/jpeg'), size: a.fileSize ?? 0, file: (a as { file?: File }).file };
  });
}

export function ChatView({ threadId, placeholder, empty }: { threadId: string; placeholder: string; empty?: string }) {
  const t = useTheme();
  const { threads, typing, send, avatar, streaming, connected, booting, deleteMessage, rewindMessage, transcribe, refreshThread, sharedChannels } = useStore();
  const [refreshing, setRefreshing] = useState(false);
  const pull = () => { setRefreshing(true); refreshThread(threadId).catch(() => {}).finally(() => setRefreshing(false)); };
  const sheet = useSheet();
  const msgs = threads[threadId] ?? [];
  const partial = streaming[threadId];
  const [draft, setDraft] = useState('');
  const [pending, setPending] = useState<PendingFile[]>([]);
  const [transcribing, setTranscribing] = useState(false);
  const scroller = useRef<ScrollView>(null);
  const nav = useNavigation<any>();
  const busy = !!typing[threadId];
  const recorder = useAudioRecorder(SPEECH_PRESET);
  const rec = useAudioRecorderState(recorder, 250);
  const canRecord = Platform.OS !== 'web';

  useEffect(() => {
    const h = setTimeout(() => scroller.current?.scrollToEnd({ animated: true }), 60);
    return () => clearTimeout(h);
  }, [msgs.length, busy, partial?.length]);

  const fail = (e: unknown) => Alert.alert('没做成', e instanceof Error ? e.message : String(e));

  const addFiles = (files: PendingFile[]) => {
    if (!files.length) return;
    const tooBig = files.filter((f) => f.size > MAX_BYTES);
    if (tooBig.length) Alert.alert('文件太大', `${tooBig.map((f) => f.name).join('、')} 超过 ${MAX_BYTES / 1024 / 1024} MB，没有加进去。`);
    const ok = files.filter((f) => f.size <= MAX_BYTES);
    setPending((cur) => {
      const next = [...cur, ...ok];
      if (next.length > MAX_FILES) Alert.alert('太多了', `一条消息最多 ${MAX_FILES} 个附件，多出来的没加。`);
      return next.slice(0, MAX_FILES);
    });
  };

  const openAttach = () => {
    if (Platform.OS === 'web') { pickDocuments().then(addFiles).catch(fail); return; }
    const close = sheet.close;
    sheet.open({
      title: '添加附件',
      content: () => (
        <View style={{ gap: space.sm }}>
          <Action icon={Camera} label="拍照或录像" onPress={() => { close(); pickMedia(true).then(addFiles).catch(fail); }} />
          <Action icon={ImageIcon} label="相册" note={`照片和视频，${agentName()} 直接看图`} onPress={() => { close(); pickMedia(false).then(addFiles).catch(fail); }} />
          <Action icon={FileText} label="文件" note={`PDF、Word、表格、PPT、代码、录音，什么类型都行。每个 ${MAX_BYTES / 1024 / 1024} MB 以内，一条最多 ${MAX_FILES} 个。`} onPress={() => { close(); pickDocuments().then(addFiles).catch(fail); }} />
        </View>
      ),
    });
  };

  const submit = () => {
    const text = draft.trim();
    if ((!text && !pending.length) || busy || transcribing) return;
    send(threadId, text, pending.length ? pending : undefined);
    setDraft('');
    setPending([]);
  };

  const startRecording = async () => {
    try {
      const perm = await AudioModule.requestRecordingPermissionsAsync();
      if (!perm.granted) { Alert.alert('没有麦克风权限', `去系统设置里给 ${agentName()} 打开麦克风。`); return; }
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
      else Alert.alert('没听清', '录音里没有识别出文字。');
    } catch (e) { fail(e); } finally { setTranscribing(false); }
  };

  const openActions = (m: Message) => {
    const text = m.body.text;
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});
    const after = msgs.length - msgs.findIndex((x) => x.id === m.id) - 1;
    const shared = threadId === 'main' && sharedChannels.length ? `主对话和 ${sharedChannels.join('、')} 共用，那边在这之后的消息也会一起退掉。` : '';
    const tail = after > 0 ? `之后的 ${after} 条也会一起去掉，` : '';
    const close = sheet.close;
    const rewind = (edit: boolean) => { close(); rewindMessage(threadId, m.id).then((txt) => { if (edit) setDraft(txt || text); }).catch(fail); };
    sheet.open({
      title: m.role === 'user' ? '这条消息' : `${agentName()} 的回复`,
      content: () => (
        <View style={{ gap: space.sm }}>
          <Action icon={Copy} label="复制" onPress={() => { close(); Clipboard.setStringAsync(text).catch(() => {}); }} />
          {m.role === 'user' && !busy ? <>
            <Action icon={Pencil} label="重新编辑" note={`放回输入框改完再发。${tail}${agentName()} 也会忘掉这段。${shared}${m.body.attachments?.length ? '附件要重新加。' : ''}`} onPress={() => rewind(true)} />
            <Action icon={Undo2} label="撤回" note={`${tail}${agentName()} 也会忘掉这段。${shared}`} onPress={() => rewind(false)} />
          </> : null}
          {m.role === 'user' && busy ? <T v="callout" color={t.ink3}>{`${agentName()} 回完之后才能撤回或重新编辑。`}</T> : null}
          <Action icon={Trash2} label="删除" danger note={`只从这里的记录删掉，${agentName()} 仍然记得。`} onPress={() => { close(); deleteMessage(threadId, m.id).catch(fail); }} />
        </View>
      ),
    });
  };

  const canSend = (!!draft.trim() || pending.length > 0) && !busy && !transcribing;
  const secs = Math.floor((rec.durationMillis ?? 0) / 1000);

  return (
    <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined} keyboardVerticalOffset={90}>
      <ScrollView ref={scroller} style={{ flex: 1 }} contentContainerStyle={{ padding: space.lg, gap: space.lg }} keyboardShouldPersistTaps="handled"
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={pull} />}>
        {connected ? (
          <Pressable onPress={() => nav.navigate('History', { thread: threadId })} accessibilityRole="button" style={{ alignSelf: 'center', paddingVertical: 2 }}>
            <T v="caption" color={t.ink3}>这里只有今天的（04:00 起）· <T v="caption" color={t.gold}>之前的在历史里</T></T>
          </Pressable>
        ) : null}
        {!msgs.length && !busy ? (
          <View style={{ alignItems: 'center', gap: space.sm, paddingVertical: space.xxl, paddingHorizontal: space.lg }}>
            <LensAvatar size={40} config={avatar} />
            <T v="callout" color={t.ink3} style={{ textAlign: 'center' }}>
              {booting ? '正在连服务器…' : !connected ? '没连上服务器。检查「我 → 服务器」，再回到这一页。' : empty ?? '还没有对话。'}
            </T>
          </View>
        ) : null}
        {msgs.map((m, i) => <Bubble key={m.id} m={m} showAvatar={m.role === 'grava' && msgs[i - 1]?.role !== 'grava'} onLongPress={() => openActions(m)} />)}
        {busy ? (
          <View style={{ flexDirection: 'row', gap: space.sm, alignItems: partial ? 'flex-start' : 'center' }}>
            <LensAvatar size={28} config={avatar} />
            {partial ? <View style={{ flex: 1 }}><Markdown text={partial} /></View> : <T v="callout" color={t.ink3}>{`发给 ${agentName()} 了，等回复…`}</T>}
          </View>
        ) : null}
      </ScrollView>
      <View style={[styles.composerWrap, { borderTopColor: t.line, backgroundColor: t.bg }]}>
        {pending.length ? (
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: space.sm, paddingHorizontal: space.md, paddingTop: space.sm }} keyboardShouldPersistTaps="handled">
            {pending.map((f, i) => (
              f.mime.startsWith('image/') ? (
                <View key={`${f.uri}-${i}`} style={{ position: 'relative' }}>
                  <Image source={{ uri: f.uri }} style={{ width: 56, height: 56, borderRadius: radius.md, backgroundColor: t.surface2 }} accessibilityLabel={f.name} />
                  <Pressable onPress={() => setPending((cur) => cur.filter((_, k) => k !== i))} hitSlop={8} accessibilityRole="button" accessibilityLabel={`去掉 ${f.name}`}
                    style={[styles.removeDot, { backgroundColor: t.ink }]}><X size={12} color={t.bg} /></Pressable>
                </View>
              ) : (
                <FileChip key={`${f.uri}-${i}`} a={{ id: `p${i}`, name: f.name, mime: f.mime, size: f.size, kind: f.mime.startsWith('audio/') ? 'audio' : f.mime.startsWith('video/') ? 'video' : 'doc', url: f.uri }}
                  onRemove={() => setPending((cur) => cur.filter((_, k) => k !== i))} />
              )
            ))}
          </ScrollView>
        ) : null}
        {rec.isRecording ? (
          <View style={styles.composer}>
            <View style={[styles.recording, { backgroundColor: t.surface }]}>
              <View style={{ width: 10, height: 10, borderRadius: 5, backgroundColor: t.bad }} />
              <T v="body" style={{ flex: 1, fontVariant: ['tabular-nums'] }}>正在录音 {Math.floor(secs / 60)}:{String(secs % 60).padStart(2, '0')}</T>
              <T v="caption" color={t.ink3}>说完点停止，转成文字后可以改</T>
            </View>
            <Pressable onPress={stopRecording} accessibilityRole="button" accessibilityLabel="停止录音" style={[styles.send, { backgroundColor: t.bad }]}>
              <Square size={16} color="#fff" fill="#fff" />
            </Pressable>
          </View>
        ) : (
          <View style={styles.composer}>
            <Pressable onPress={openAttach} disabled={busy} hitSlop={6} accessibilityRole="button" accessibilityLabel="添加附件" style={styles.iconBtn}>
              <Paperclip size={22} color={busy ? t.ink3 : t.ink2} />
            </Pressable>
            <TextInput
              value={draft} onChangeText={setDraft} placeholder={transcribing ? '正在转文字…' : placeholder} placeholderTextColor={t.ink3}
              multiline numberOfLines={1} onSubmitEditing={submit} blurOnSubmit accessibilityLabel="消息输入框" editable={!transcribing}
              style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink }]}
            />
            {canRecord && !draft.trim() && !pending.length && !transcribing ? (
              <Pressable onPress={startRecording} disabled={busy} accessibilityRole="button" accessibilityLabel="录音输入"
                style={[styles.send, { backgroundColor: t.surface2 }]}>
                <Mic size={20} color={busy ? t.ink3 : t.ink} />
              </Pressable>
            ) : (
              <Pressable onPress={submit} disabled={!canSend} accessibilityRole="button" accessibilityLabel="发送"
                style={[styles.send, { backgroundColor: canSend ? t.goldFill : t.surface2 }]}>
                <ArrowUp size={20} color={canSend ? t.onGold : t.ink3} />
              </Pressable>
            )}
          </View>
        )}
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  userBubble: { maxWidth: '82%', borderRadius: radius.lg, borderBottomRightRadius: 6, paddingHorizontal: 14, paddingVertical: 9 },
  composerWrap: { borderTopWidth: StyleSheet.hairlineWidth },
  composer: { flexDirection: 'row', alignItems: 'flex-end', gap: space.sm, paddingHorizontal: space.md, paddingVertical: space.sm },
  input: { flex: 1, minHeight: 40, maxHeight: 120, borderRadius: 20, paddingHorizontal: 16, paddingTop: 9, paddingBottom: 9 },
  action: { flexDirection: 'row', alignItems: 'center', gap: space.md, borderRadius: radius.md, padding: space.lg },
  send: { width: 40, height: 40, borderRadius: 20, alignItems: 'center', justifyContent: 'center' },
  iconBtn: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center' },
  chip: { flexDirection: 'row', alignItems: 'center', gap: 8, borderWidth: StyleSheet.hairlineWidth, borderRadius: radius.md, paddingHorizontal: 10, paddingVertical: 6, maxWidth: 260 },
  recording: { flex: 1, flexDirection: 'row', alignItems: 'center', gap: 8, minHeight: 40, borderRadius: 20, paddingHorizontal: 14 },
  removeDot: { position: 'absolute', top: -6, right: -6, width: 20, height: 20, borderRadius: 10, alignItems: 'center', justifyContent: 'center' },
});
