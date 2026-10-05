// 全屏写字板：自动存草稿（本机）、数字数，写完存成一条「长文」或直接进库的「写作」。
// 冥想时间里也是它：深色、顶上倒计时、没有 tab；结束（点结束或到点）= 写的存成一条 + 看小结。
import React, { useEffect, useRef, useState } from 'react';
import { Alert, Platform, Pressable, ScrollView, StatusBar, StyleSheet, Text, TextInput, View } from 'react-native';
import { useNavigation, useRoute } from '@react-navigation/native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import * as Haptics from 'expo-haptics';
import { AudioModule, RecordingPresets, setAudioModeAsync, useAudioRecorder, useAudioRecorderState } from 'expo-audio';
import { BellOff, ChevronRight, Clock, Inbox, Mic, Minimize2, Moon, Pencil, Square, TextIcon, Timer } from '../components/icons';
import Reanimated from 'react-native-reanimated';
import { useBottomInset } from '../components/keyboard';
import { useSheet } from '../components/Sheet';
import { Btn, Screen, SectionLabel, T, showError } from '../components/ui';
import * as thinkApi from '../api/think';
import type { FocusSummary } from '../api/think';
import { loadDraft, saveDraft } from '../drafts';
import { L } from '../i18n';
import { openTarget } from '../navigation';
import { useStore } from '../store';
import { radius, space, type, useTheme } from '../theme';
import { useThink } from '../think/ThinkStore';
import { SHORTCUT_END, ZenStartSheet, runShortcut, shortcutOn } from '../think/Zen';
import { GrowInput, wordCount } from '../think/parts';

const K_TITLE = 'think:write:title';
const K_BODY = 'think:write:body';
const SPEECH_PRESET = { ...RecordingPresets.HIGH_QUALITY, sampleRate: 16000, numberOfChannels: 1, bitRate: 32000 };
// 冥想的深色：不跟随主题，一直是透镜那块墨黑
const Z = { bg: '#101216', ink: '#E8EAED', body: '#C9CED4', meta: '#8A929B', line: '#1E2228', gold: '#D9AE62' };

export function ThinkWriteScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const route = useRoute<any>();
  const sheet = useSheet();
  const insets = useSafeAreaInsets();
  const zen = !!route.params?.zen;
  const { focus, capture, endFocus, loadFocus } = useThink();
  const { transcribe } = useStore();
  const [title, setTitle] = useState(() => loadDraft(K_TITLE));
  const [body, setBody] = useState(() => {
    const saved = loadDraft(K_BODY);
    const handed = route.params?.text as string | undefined;
    if (handed && route.params?.from) saveDraft(route.params.from, '');  // 从输入栏展开过来的：那边的草稿搬到这里
    return handed ? (saved ? `${saved}\n\n${handed}` : handed) : saved;
  });
  const [busy, setBusy] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const root = useRef<View>(null);
  const bottom = useBottomInset(root);
  const recorder = useAudioRecorder(SPEECH_PRESET);
  const rec = useAudioRecorderState(recorder, 250);
  const ended = useRef(false);
  const count = wordCount(body);

  useEffect(() => { saveDraft(K_TITLE, title); }, [title]);
  useEffect(() => { saveDraft(K_BODY, body); }, [body]);
  useEffect(() => {
    if (!zen) return undefined;
    const h = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(h);
  }, [zen]);

  const endsAt = focus ? Date.parse(focus.endsAt) : null;
  const left = endsAt ? Math.max(0, Math.round((endsAt - now) / 1000)) : 0;
  const total = focus ? focus.minutes * 60 : 1;

  const clear = () => { setTitle(''); setBody(''); saveDraft(K_TITLE, ''); saveDraft(K_BODY, ''); };
  const finish = async () => {
    if (ended.current) return;
    ended.current = true;
    setBusy(L('正在结束…', 'Ending…'));
    try {
      let wrote: { id: string; title: string; chars: number } | null = null;
      const text = body.trim();
      if (text) {
        const f = await capture({ kind: 'long', title: title.trim() || undefined, text });
        wrote = { id: f.id, title: f.title || title.trim(), chars: count };
      }
      const summary = await endFocus({ words: count, notes: wrote ? 1 : 0 });
      if (shortcutOn()) runShortcut(SHORTCUT_END);
      clear();
      nav.replace('ZenEnd', { summary, wrote, text, title: title.trim() });
    } catch (e) {
      ended.current = false;
      showError(L('无法结束', "Couldn't end focus time"), e);
    } finally { setBusy(null); }
  };
  // 到点了：自己收尾（服务器那边也按到点结束）。finish 每次渲染都是新的，经 ref 调
  const timeUp = zen && !!endsAt && left === 0;
  const finishRef = useRef(finish);
  useEffect(() => { finishRef.current = finish; });
  useEffect(() => {
    if (!timeUp) return undefined;
    const h = setTimeout(() => { finishRef.current(); }, 0);
    return () => clearTimeout(h);
  }, [timeUp]);
  // 冥想中途从别处（比如另一台设备）结束了：回到普通写字板
  useEffect(() => { if (zen && !focus && !ended.current) loadFocus(); }, [zen, focus, loadFocus]);

  const saveAs = async (where: 'long' | 'writing') => {
    const text = body.trim();
    if (!text || busy) return;
    setBusy(L('正在保存…', 'Saving…'));
    try {
      if (where === 'writing') {
        const path = await thinkApi.writeNote({ title: title.trim(), text });
        Alert.alert(L('已保存到「写作」', 'Saved to Writing'), path);
      } else {
        await capture({ kind: 'long', title: title.trim() || undefined, text });
      }
      clear();
      nav.goBack();
    } catch (e) { showError(L('保存失败', "Couldn't save"), e); } finally { setBusy(null); }
  };

  const record = async () => {
    try {
      if (rec.isRecording) {
        await recorder.stop();
        await setAudioModeAsync({ allowsRecording: false, playsInSilentMode: true });
        if (!recorder.uri) return;
        setBusy(L('正在转写…', 'Transcribing…'));
        const said = await transcribe({ uri: recorder.uri, name: 'voice.m4a', mime: 'audio/m4a', size: 0 });
        if (said) setBody((b) => (b.trim() ? `${b.trimEnd()}\n\n${said}` : said));
        setBusy(null);
        return;
      }
      const perm = await AudioModule.requestRecordingPermissionsAsync();
      if (!perm.granted) { Alert.alert(L('无麦克风权限', 'No microphone access'), L('请在系统设置中开启麦克风权限。', 'Turn on microphone access in Settings.')); return; }
      await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true });
      await recorder.prepareToRecordAsync();
      recorder.record();
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => {});
    } catch (e) { setBusy(null); showError(L('录音失败', "Couldn't record"), e); }
  };

  const ink = zen ? Z.ink : t.ink;
  const meta = zen ? Z.meta : t.ink3;
  const bg = zen ? Z.bg : t.surface;
  const mm = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}`;
  return (
    <Reanimated.View ref={root} onLayout={bottom.onLayout} style={[{ flex: 1, backgroundColor: bg, paddingTop: insets.top }, bottom.style]}>
      {zen ? <StatusBar barStyle="light-content" /> : null}
      {zen ? (
        <View style={{ paddingHorizontal: 20, paddingTop: 12, gap: 10 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <Timer size={16} color={Z.meta} />
            <Text style={[type.callout, { color: Z.meta, fontVariant: ['tabular-nums'] }]}>{focus ? L(`剩余 ${mm}`, `${mm} left`) : L('冥想时间', 'Focus time')}</Text>
            <View style={{ flex: 1 }} />
            <Pressable onPress={finish} disabled={!!busy} accessibilityRole="button" style={({ pressed }) => [styles.end, { opacity: pressed ? 0.7 : 1 }]}>
              <Text style={[type.callout, { color: Z.gold, fontWeight: '600' }]}>{busy ?? L('结束', 'End')}</Text>
            </Pressable>
          </View>
          <View style={{ height: 3, borderRadius: 2, backgroundColor: Z.line }}>
            <View style={{ width: `${Math.min(100, Math.round(((total - left) / total) * 100))}%`, height: 3, borderRadius: 2, backgroundColor: Z.gold }} />
          </View>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
            <BellOff size={13} color={Z.meta} />
            <T v="caption" color={Z.meta}>{L('推送已暂停，结束后统一送达', 'Notifications are held until the end')}</T>
          </View>
        </View>
      ) : (
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingHorizontal: space.md, paddingTop: space.sm }}>
          <Pressable onPress={() => nav.goBack()} accessibilityRole="button" accessibilityLabel={L('收起', 'Close')} style={[styles.round, { backgroundColor: t.bg }]}>
            <Minimize2 size={18} color={t.ink} />
          </Pressable>
          <View style={{ flex: 1, alignItems: 'center' }}>
            <T v="headline" style={{ fontSize: 14, fontVariant: ['tabular-nums'] }}>{L(`${count} 字`, `${count} words`)}</T>
            <T v="caption" color={t.ink3}>{busy ?? L('已自动保存 · 不会发送给 Agent', 'Autosaved · not sent to the Agent')}</T>
          </View>
          <Pressable onPress={() => sheet.open({ title: L('进入冥想时间', 'Focus time'), content: (close) => <ZenStartSheet close={close} /> })} accessibilityRole="button"
            accessibilityLabel={L('进入冥想时间', 'Start focus time')} style={[styles.round, { backgroundColor: t.lensField }]}>
            <Moon size={18} color={t.goldFill} />
          </Pressable>
        </View>
      )}
      <ScrollView style={{ flex: 1 }} contentContainerStyle={{ paddingHorizontal: zen ? 26 : 24, paddingTop: zen ? 36 : 18, paddingBottom: 40, gap: 14 }} keyboardShouldPersistTaps="handled">
        <TextInput value={title} onChangeText={setTitle} placeholder={L('标题（可选）', 'Title (optional)')} placeholderTextColor={meta}
          accessibilityLabel={L('标题', 'Title')} style={[styles.title, { color: ink }]} />
        <GrowInput value={body} onChangeText={setBody} multiline scrollEnabled={false} autoFocus={!body}
          placeholder={zen ? L('只有你与文字。', 'Just you and the words.') : L('开始书写，篇幅不限。', 'Write as much as you like.')} placeholderTextColor={meta}
          accessibilityLabel={L('正文', 'Body')} style={[styles.body, { color: zen ? Z.body : t.ink, fontSize: zen ? 18 : 17, lineHeight: zen ? 33 : 31 }]} />
      </ScrollView>
      <View style={[styles.bottom, { borderTopColor: zen ? 'transparent' : t.line }]}>
        {Platform.OS !== 'web' ? (
          <Pressable onPress={record} accessibilityRole="button" accessibilityLabel={rec.isRecording ? L('停止并转写', 'Stop and transcribe') : L('语音输入', 'Dictate')}
            style={[styles.round, { backgroundColor: rec.isRecording ? t.bad : zen ? Z.line : t.bg }]}>
            {rec.isRecording ? <Square size={15} color="#fff" fill="#fff" /> : <Mic size={19} color={zen ? Z.body : t.ink2} />}
          </Pressable>
        ) : null}
        <View style={{ flex: 1 }} />
        {zen ? (
          <T v="caption" color={Z.meta} style={{ fontVariant: ['tabular-nums'] }}>{L(`${count} 字 · 已自动保存`, `${count} words · autosaved`)}</T>
        ) : (
          <>
            <Btn label={L('存入「写作」', 'To Writing')} kind="quiet" onPress={() => saveAs('writing')} />
            <Btn label={L('存为想法', 'Save as a thought')} onPress={() => saveAs('long')} />
          </>
        )}
      </View>
    </Reanimated.View>
  );
}

export function ZenEndScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const route = useRoute<any>();
  const s = route.params?.summary as FocusSummary;
  const wrote = route.params?.wrote as { id: string; title: string; chars: number } | null | undefined;
  const [toWriting, setToWriting] = useState<string | null>(null);
  if (!s) return null;
  const line = [`${s.from} – ${s.to}`, s.words ? L(`书写 ${s.words} 字`, `${s.words} words`) : '', s.notes ? L(`记录 ${s.notes} 条`, `${s.notes} saved`) : ''].filter(Boolean).join(' · ');
  const next = s.next[0];
  const writing = async () => {
    try {
      const path = await thinkApi.writeNote({ title: route.params?.title || wrote?.title || '', text: route.params?.text || '' });
      setToWriting(path);
    } catch (e) { showError(L('保存失败', "Couldn't save"), e); }
  };
  return (
    <Screen>
      <ScrollView contentContainerStyle={{ padding: space.lg, gap: space.md }}>
        <View style={{ alignItems: 'center', gap: 8, paddingTop: space.lg, paddingBottom: space.sm }}>
          <View style={[styles.moon, { backgroundColor: t.lensField }]}><Moon size={26} color={t.goldFill} /></View>
          <T v="title" style={{ fontSize: 24, fontWeight: '800' }}>{L(`冥想 ${s.minutes} 分钟`, `${s.minutes} minutes of focus`)}</T>
          <T v="callout" color={t.ink3}>{line}</T>
        </View>
        {next ? (
          <View style={[styles.next, { backgroundColor: t.warnSoft }]}>
            <Clock size={16} color={t.warn} />
            <T v="callout" color={t.warn} style={{ flex: 1, fontWeight: '600' }}>{L(`接下来：${next.time} ${next.title}`, `Next: ${next.time} ${next.title}`)}</T>
          </View>
        ) : null}
        {wrote ? (
          <>
            <SectionLabel>{L('本次书写', 'What you wrote')}</SectionLabel>
            <View style={[styles.card, { backgroundColor: t.surface }]}>
              <View style={[styles.tile, { backgroundColor: t.tints.gold.soft }]}><Pencil size={16} color={t.tints.gold.fg} /></View>
              <View style={{ flex: 1, gap: 2 }}>
                <T v="headline" numberOfLines={1}>{wrote.title || L('长文', 'A long piece')}</T>
                <T v="caption" color={t.ink2}>{toWriting ? L(`已同时存入 ${toWriting}`, `Also in ${toWriting}`) : L(`长文 · ${wrote.chars} 字 · 已存为想法`, `${wrote.chars} words · saved as a thought`)}</T>
              </View>
              {!toWriting ? <Btn label={L('存入「写作」', 'To Writing')} kind="quiet" onPress={writing} /> : null}
            </View>
          </>
        ) : null}
        <SectionLabel>{s.held.length ? L(`期间暂缓的推送 · ${s.held.length}`, `Held while you focused · ${s.held.length}`) : L('期间暂缓的推送', 'Held while you focused')}</SectionLabel>
        {s.held.length ? (
          <View style={[styles.list, { backgroundColor: t.surface }]}>
            {s.held.map((h, i) => (
              <Pressable key={i} onPress={() => openTarget(h.target && h.target.type ? h.target : { type: h.thread && h.thread !== 'today' ? 'thread' : 'today', thread: h.thread ?? 'today' } as any)}
                accessibilityRole="button" style={({ pressed }) => [styles.row, i ? { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line } : null, { opacity: pressed ? 0.7 : 1 }]}>
                <Inbox size={16} color={t.cyan} />
                <View style={{ flex: 1, gap: 2 }}>
                  <T v="headline" numberOfLines={1} style={{ fontSize: 15 }}>{h.title}</T>
                  <T v="caption" color={t.ink2} numberOfLines={2}>{h.body}</T>
                </View>
                <T v="caption" color={t.ink3}>{h.at}</T>
                <ChevronRight size={16} color={t.ink3} />
              </Pressable>
            ))}
          </View>
        ) : <T v="callout" color={t.ink3}>{L('期间无推送。', 'Nothing came in.')}</T>}
        {s.inbox ? <T v="callout" color={t.ink2}>{L(`${s.inbox} 项待你确认。`, `${s.inbox} waiting for your OK.`)}</T> : null}
        <Btn label={L('前往「今天」', 'Open Today')} onPress={() => nav.navigate('Tabs', { screen: '今天' })} />
        <T v="caption" color={t.ink3} style={{ textAlign: 'center' }}>{L('期间未推送、未提醒，内容均汇总于此', 'Nothing buzzed; everything waited here')}</T>
        <Btn label={L('返回 Zen', 'Back to Zen')} kind="quiet" icon={<TextIcon size={15} color={t.ink} />} onPress={() => nav.navigate('Tabs', { screen: '思考' })} />
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  round: { width: 40, height: 40, borderRadius: 20, alignItems: 'center', justifyContent: 'center' },
  end: { height: 34, paddingHorizontal: 14, borderRadius: 17, backgroundColor: Z.line, justifyContent: 'center' },
  // 全屏写字不要网页的焦点框（outlineWidth 在 iOS 上不起作用）
  title: { fontSize: 26, fontWeight: '800', letterSpacing: -0.3, paddingVertical: 4, outlineWidth: 0 },
  body: { minHeight: 300, textAlignVertical: 'top', paddingVertical: 0, outlineWidth: 0 },
  bottom: { flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingHorizontal: space.md, paddingVertical: space.sm, borderTopWidth: StyleSheet.hairlineWidth },
  moon: { width: 56, height: 56, borderRadius: 28, alignItems: 'center', justifyContent: 'center' },
  next: { flexDirection: 'row', alignItems: 'center', gap: 10, borderRadius: radius.md, paddingHorizontal: 12, paddingVertical: 10 },
  card: { flexDirection: 'row', alignItems: 'center', gap: space.md, borderRadius: radius.md + 2, padding: space.md },
  tile: { width: 32, height: 32, borderRadius: 10, alignItems: 'center', justifyContent: 'center' },
  list: { borderRadius: radius.md + 2, paddingHorizontal: space.md },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 12 },
});
