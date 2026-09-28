// 想法页底下的输入栏：打字（句子里的 #xx 也算关键词）、# 键打关键词、录一段语音（原声留着、转成文字）、照片 / 文件、展开全屏写。
// 发出去只是记下来：不发给模型。
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View, type NativeSyntheticEvent, type TextInputKeyPressEventData } from 'react-native';
import * as Haptics from 'expo-haptics';
import { AudioModule, RecordingPresets, setAudioModeAsync, useAudioRecorder, useAudioRecorderState } from 'expo-audio';
import { useNavigation } from '@react-navigation/native';
import { ArrowUp, Camera, FileText, Hash, ImageIcon, Maximize2, Mic, Paperclip, Square, X } from '../components/icons';
import { MAX_FILES, pickDocuments, pickMedia } from '../components/chatInput';
import { useSheet } from '../components/Sheet';
import { editMenu, nativeReady } from '../api/native';
import { T } from '../components/ui';
import { loadDraft, saveDraft } from '../drafts';
import type { PendingFile } from '../data/types';
import { L } from '../i18n';
import { radius, space, type, useTheme } from '../theme';
import { GrowInput } from './parts';
import { useThink } from './ThinkStore';

const DRAFT = 'think:capture';
const SPEECH_PRESET = { ...RecordingPresets.HIGH_QUALITY, sampleRate: 16000, numberOfChannels: 1, bitRate: 32000 };
const split = (s: string) => s.split(/[\s,，、#]+/).map((x) => x.trim()).filter(Boolean);

export function CaptureBar({ keyword, placeholder, onSaved }: {
  /** 在关键词页里记：自动带上这个关键词 */
  keyword?: string; placeholder?: string;
  /** 记下了（关键词页要重读） */
  onSaved?: () => void;
}) {
  const t = useTheme();
  const nav = useNavigation<any>();
  const sheet = useSheet();
  const { capture, captureFiles, keywords } = useThink();
  const draftKey = keyword ? `think:capture:${keyword}` : DRAFT;
  const [text, setText] = useState(() => loadDraft(draftKey));
  const [kwMode, setKwMode] = useState(false);
  const [chips, setChips] = useState<string[]>([]);
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const input = useRef<TextInput>(null);
  // 回车直接记下，换行在长按菜单里（照微信）：要 1.0.5 起的原生菜单，没有的包上回车照旧是换行
  const nativeMenu = Platform.OS === 'ios' && nativeReady();
  const menuKey = `think-capture:${draftKey}`;
  const latest = useRef({ text, sel: { start: 0, end: 0 } });
  useEffect(() => { latest.current.text = text; }, [text]);
  const recorder = useAudioRecorder(SPEECH_PRESET);
  const rec = useAudioRecorderState(recorder, 250);
  const canRecord = Platform.OS !== 'web';

  const change = (v: string) => { setText(v); saveDraft(draftKey, v); };
  const fail = (e: unknown) => Alert.alert(L('没记下', "Couldn't save"), e instanceof Error ? e.message : String(e));

  // 打关键词时的补全：正在打的这个词对得上的已有关键词（开头对上的在前），没在打就给最常用的几个
  const suggestions = useMemo(() => {
    const have = new Set(chips.map((c) => c.replace(/\s+/g, '').toLowerCase()));
    const q = token.replace(/\s+/g, '').toLowerCase();
    const pool = keywords.filter((k) => !have.has(k.k.replace(/\s+/g, '').toLowerCase()));
    if (!q) return pool.slice(0, 6);
    const norm = (k: string) => k.replace(/\s+/g, '').toLowerCase();
    return [...pool.filter((k) => norm(k.k).startsWith(q)), ...pool.filter((k) => !norm(k.k).startsWith(q) && norm(k.k).includes(q))].slice(0, 5);
  }, [keywords, chips, token]);
  const exact = keywords.some((k) => k.k.replace(/\s+/g, '').toLowerCase() === token.replace(/\s+/g, '').toLowerCase());

  const addChip = (k: string) => {
    const v = k.trim().replace(/^#/, '');
    if (v && !chips.some((c) => c.toLowerCase() === v.toLowerCase())) setChips((c) => [...c, v]);
    setToken('');
  };
  const onToken = (v: string) => {
    // 打了空格 / 逗号：前面的变成一个关键词
    if (/[\s,，、]$/.test(v)) { split(v).forEach(addChip); return; }
    setToken(v);
  };

  const submit = async () => {
    if (busy) return;
    try {
      if (kwMode) {
        const all = [...chips, ...split(token)];
        if (!all.length) return;
        setBusy(L('正在记…', 'Saving…'));
        await capture({ kind: 'keywords', keywords: keyword ? [keyword, ...all] : all });
        setChips([]); setToken('');
        onSaved?.();
        Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => {});
        return;
      }
      const v = text.trim();
      if (!v) return;
      setBusy(L('正在记…', 'Saving…'));
      await capture({ text: v, keywords: keyword ? [keyword] : undefined });
      change('');
      onSaved?.();
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => {});
    } catch (e) { fail(e); } finally { setBusy(null); }
  };

  const withFiles = async (files: PendingFile[]) => {
    if (!files.length) return;
    try {
      setBusy(L('正在传…', 'Uploading…'));
      await captureFiles(files.slice(0, MAX_FILES), { text: text.trim() || undefined, keywords: keyword ? [keyword] : undefined });
      change('');
      onSaved?.();
    } catch (e) { fail(e); } finally { setBusy(null); }
  };
  const openAttach = () => {
    if (Platform.OS === 'web') { pickDocuments().then(withFiles).catch(fail); return; }
    sheet.open({
      title: L('记一张照片或一个文件', 'Add a photo or a file'),
      content: (close) => (
        <View style={{ gap: space.sm }}>
          {[
            { Icon: Camera, label: L('拍照', 'Take a photo'), go: () => pickMedia(true) },
            { Icon: ImageIcon, label: L('相册', 'Photo library'), go: () => pickMedia(false) },
            { Icon: FileText, label: L('文件', 'Files'), go: () => pickDocuments() },
          ].map(({ Icon, label, go }) => (
            <Pressable key={label} onPress={() => { close(); go().then(withFiles).catch(fail); }} accessibilityRole="button"
              style={({ pressed }) => [styles.action, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
              <Icon size={20} color={t.ink} /><T v="headline">{label}</T>
            </Pressable>
          ))}
          <T v="caption" color={t.ink3}>{L('输入框里写的字会一起记在这条上。5 MB 以内的放进库，Obsidian 里也看得到；大的留在服务器上。', 'Whatever you typed goes on the same note. Files up to 5 MB go into the vault (visible in Obsidian); bigger ones stay on the server.')}</T>
        </View>
      ),
    });
  };

  const startRecording = async () => {
    try {
      const perm = await AudioModule.requestRecordingPermissionsAsync();
      if (!perm.granted) { Alert.alert(L('没有麦克风权限', 'No microphone access'), L('去系统设置里打开麦克风。', 'Turn on the microphone in Settings.')); return; }
      await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true });
      await recorder.prepareToRecordAsync();
      recorder.record();
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => {});
    } catch (e) { fail(e); }
  };
  const stopRecording = async () => {
    try {
      const secs = (rec.durationMillis ?? 0) / 1000;
      await recorder.stop();
      await setAudioModeAsync({ allowsRecording: false, playsInSilentMode: true });
      const uri = recorder.uri;
      if (!uri) return;
      setBusy(L('正在转文字…', 'Transcribing…'));
      await captureFiles([{ uri, name: `voice-${Date.now()}.m4a`, mime: 'audio/m4a', size: 0 }], { kind: 'voice', duration: secs, text: text.trim() || undefined, keywords: keyword ? [keyword] : undefined });
      change('');
      onSaved?.();
    } catch (e) { fail(e); } finally { setBusy(null); }
  };

  const expand = () => nav.navigate('ThinkWrite', { text: text.trim() ? text : undefined, from: draftKey });

  // 长按输入框的系统菜单里加「换行」「全屏写」。换行原生那边直接插进光标处；插不了（handled=false）就在这里按记下的光标位置插。
  useEffect(() => {
    if (!nativeMenu) return undefined;
    const off = editMenu(menuKey, [
      { id: 'newline', title: L('换行', 'New line'), icon: 'return' },
      { id: 'full', title: L('全屏写', 'Full screen'), icon: 'arrow.up.left.and.arrow.down.right' },
    ], (e) => {
      const cur = latest.current.text;
      if (e.id === 'full') {
        nav.navigate('ThinkWrite', { text: cur.trim() ? cur : undefined, from: draftKey });
      } else if (e.id === 'newline' && !e.handled) {
        const { start, end } = latest.current.sel;
        const a = Math.min(start, cur.length);
        const next = `${cur.slice(0, a)}\n${cur.slice(Math.min(Math.max(end, a), cur.length))}`;
        setText(next);
        saveDraft(draftKey, next);
      }
    });
    return () => { off?.(); };
  }, [nativeMenu, menuKey, draftKey, nav]);
  const webEnter = Platform.OS === 'web' ? (e: NativeSyntheticEvent<TextInputKeyPressEventData>) => {
    const k = e.nativeEvent as unknown as KeyboardEvent;
    if (k.key !== 'Enter' || k.shiftKey || k.isComposing || k.keyCode === 229) return;
    e.preventDefault();
    if (kwMode && token.trim()) { addChip(token); return; }
    submit();
  } : undefined;
  const canSend = kwMode ? chips.length > 0 || !!token.trim() : !!text.trim();
  const secs = Math.floor((rec.durationMillis ?? 0) / 1000);

  return (
    <View style={[styles.wrap, { borderTopColor: t.line, backgroundColor: kwMode ? t.surface : t.bg }]}>
      {kwMode ? (
        <View style={{ paddingHorizontal: space.md, paddingTop: space.sm, gap: 6 }}>
          <T v="caption" color={t.ink3}>{token ? L('已有的关键词，点一下就行', 'Existing keywords, tap to add') : L('常用的', 'Most used')}</T>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} keyboardShouldPersistTaps="always" contentContainerStyle={{ gap: 8 }}>
            {suggestions.map((k) => (
              <Pressable key={k.k} onPress={() => addChip(k.k)} accessibilityRole="button" style={[styles.sug, { backgroundColor: t.tints.cyan.soft, borderColor: t.tints.cyan.soft }]}>
                <Text style={[type.callout, { color: t.tints.cyan.fg, fontWeight: '600' }]}>#{k.k}</Text>
                <Text style={[type.caption, { color: t.ink3 }]}>{k.n}</Text>
              </Pressable>
            ))}
            {token.trim() && !exact ? (
              <Pressable onPress={() => addChip(token)} accessibilityRole="button" style={[styles.sug, { borderStyle: 'dashed', borderColor: t.cyan, backgroundColor: t.surface }]}>
                <Text style={[type.callout, { color: t.tints.cyan.fg, fontWeight: '600' }]}>{L(`新建 #${token.trim()}`, `New #${token.trim()}`)}</Text>
              </Pressable>
            ) : null}
          </ScrollView>
        </View>
      ) : null}
      {rec.isRecording ? (
        <View style={styles.bar}>
          <View style={[styles.recording, { backgroundColor: t.surface }]}>
            <View style={{ width: 10, height: 10, borderRadius: 5, backgroundColor: t.bad }} />
            <T v="body" style={{ flex: 1, fontVariant: ['tabular-nums'] }}>{L('正在录', 'Recording')} {Math.floor(secs / 60)}:{String(secs % 60).padStart(2, '0')}</T>
            <T v="caption" color={t.ink3}>{L('原声会留着', 'The recording is kept')}</T>
          </View>
          <Pressable onPress={stopRecording} accessibilityRole="button" accessibilityLabel={L('停止录音', 'Stop recording')} style={[styles.send, { backgroundColor: t.bad }]}>
            <Square size={16} color="#fff" fill="#fff" />
          </Pressable>
        </View>
      ) : (
        <View style={styles.bar}>
          {!kwMode ? (
            <Pressable onPress={openAttach} disabled={!!busy} hitSlop={4} accessibilityRole="button" accessibilityLabel={L('照片或文件', 'Photo or file')} style={styles.icon}>
              <Paperclip size={22} color={t.ink2} />
            </Pressable>
          ) : null}
          {canRecord && !kwMode ? (
            <Pressable onPress={startRecording} disabled={!!busy} hitSlop={4} accessibilityRole="button" accessibilityLabel={L('录一段语音', 'Record a voice note')} style={styles.icon}>
              <Mic size={22} color={t.ink2} />
            </Pressable>
          ) : null}
          <Pressable onPress={() => { setKwMode((v) => !v); setTimeout(() => input.current?.focus(), 80); }} hitSlop={4} accessibilityRole="button"
            accessibilityState={{ selected: kwMode }} accessibilityLabel={kwMode ? L('回到打字', 'Back to text') : L('打关键词', 'Add keywords')}
            style={[styles.icon, kwMode ? { backgroundColor: t.goldFill, borderRadius: 20 } : null]}>
            <Hash size={21} color={kwMode ? t.onGold : t.ink2} />
          </Pressable>
          {kwMode ? (
            <View style={[styles.field, styles.kwField, { backgroundColor: t.bg, borderColor: t.cyan }]}>
              {chips.map((c) => (
                <View key={c} style={[styles.chipk, { backgroundColor: t.cyan }]}>
                  <Text style={[type.callout, { color: t.surface, fontWeight: '600' }]}>#{c}</Text>
                  <Pressable onPress={() => setChips((cur) => cur.filter((x) => x !== c))} hitSlop={6} accessibilityRole="button" accessibilityLabel={L(`去掉 ${c}`, `Remove ${c}`)}>
                    <X size={13} color={t.surface} />
                  </Pressable>
                </View>
              ))}
              <TextInput ref={input} value={token} onChangeText={onToken} onSubmitEditing={() => (token.trim() ? addChip(token) : submit())} submitBehavior="submit" returnKeyType="done"
                onKeyPress={(e) => { if (webEnter) webEnter(e); else if (e.nativeEvent.key === 'Backspace' && !token && chips.length) setChips((c) => c.slice(0, -1)); }}
                placeholder={chips.length ? '' : L('打关键词，空格分开', 'Type keywords, space between')} placeholderTextColor={t.ink3}
                accessibilityLabel={L('关键词', 'Keywords')} style={[type.body, { flexGrow: 1, minWidth: 80, color: t.ink, paddingVertical: 4 }]} />
            </View>
          ) : (
            <View style={[styles.field, { backgroundColor: t.surface, borderColor: t.line }]}>
              <GrowInput ref={input} value={text} onChangeText={change} multiline placeholder={busy ?? placeholder ?? L('记下来，它不会看', "Jot it down. It won't read it.")} placeholderTextColor={t.ink3}
                onKeyPress={webEnter} editable={!busy} accessibilityLabel={L('记一条想法', 'Jot down a thought')} testID={menuKey}
                {...(nativeMenu ? { submitBehavior: 'submit' as const, returnKeyType: 'done' as const, onSubmitEditing: () => { submit(); } } : null)}
                onSelectionChange={(e) => { latest.current.sel = e.nativeEvent.selection; }}
                style={[type.body, { flex: 1, color: t.ink, paddingTop: 9, paddingBottom: 9, maxHeight: 120 }]} />
              <Pressable onPress={expand} hitSlop={6} accessibilityRole="button" accessibilityLabel={L('展开，全屏写', 'Expand to write full screen')} style={styles.expand}>
                <Maximize2 size={17} color={t.ink2} />
              </Pressable>
            </View>
          )}
          <Pressable onPress={submit} disabled={!canSend || !!busy} accessibilityRole="button" accessibilityLabel={L('记下', 'Save')}
            style={[styles.send, { backgroundColor: canSend ? t.goldFill : t.surface2 }]}>
            <ArrowUp size={20} color={canSend ? t.onGold : t.ink3} />
          </Pressable>
        </View>
      )}
      {kwMode ? <T v="caption" color={t.ink3} style={{ paddingHorizontal: space.md, paddingBottom: 6 }}>{L('空格分开，回车记下 · 句子里写 #护城河 也算 · 已经记下的，点开也能加', 'Space between, Enter to save · #word inside a sentence counts too · add to saved ones by opening them')}</T> : null}
      {busy && !kwMode ? <T v="caption" color={t.ink3} style={{ paddingHorizontal: space.md, paddingBottom: 6 }}>{busy}</T> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { borderTopWidth: StyleSheet.hairlineWidth },
  bar: { flexDirection: 'row', alignItems: 'flex-end', gap: 2, paddingHorizontal: space.sm, paddingVertical: space.sm },
  icon: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center' },
  field: { flex: 1, minHeight: 40, borderRadius: 20, borderWidth: StyleSheet.hairlineWidth, flexDirection: 'row', alignItems: 'flex-end', paddingLeft: 14, paddingRight: 4, marginHorizontal: 4 },
  kwField: { flexWrap: 'wrap', alignItems: 'center', gap: 6, paddingVertical: 4, borderWidth: 1.5 },
  expand: { width: 34, height: 38, alignItems: 'center', justifyContent: 'center' },
  send: { width: 40, height: 40, borderRadius: 20, alignItems: 'center', justifyContent: 'center' },
  recording: { flex: 1, flexDirection: 'row', alignItems: 'center', gap: 8, minHeight: 40, borderRadius: 20, paddingHorizontal: 14 },
  action: { flexDirection: 'row', alignItems: 'center', gap: space.md, borderRadius: radius.md, padding: space.lg },
  sug: { flexDirection: 'row', alignItems: 'center', gap: 5, height: 34, paddingHorizontal: 12, borderRadius: 17, borderWidth: 1 },
  chipk: { flexDirection: 'row', alignItems: 'center', gap: 4, height: 28, paddingLeft: 10, paddingRight: 8, borderRadius: 14 },
});
