// 对讲页（第 8 批，13.6，2026-10-19；route Talk，全屏，params thread）。入口是主对话页头的对讲按钮（ChatHeader 的 onTalk）。
// 一圈：正在听… → 正在思考… → 正在朗读… → 接着听（逻辑在 loop.ts）。下面的大按钮：听的时候 = 说完了，想的时候 = 停止，
// 念的时候 = 打断（点头像、开口也行），停下来时 = 说话。左上角随时退出。切到后台就暂停。说的话和回复照常在对话里。
// 网页版不做（入口不显示）；安装包没有朗读模块、服务器太旧、没配转写、没给麦克风，都在这一页说清楚。设计稿还没有，先照硬件页的样子做最简的。
import React, { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { AppState, Linking, Pressable, ScrollView, StyleSheet, View } from 'react-native';
import { useNavigation, useRoute } from '@react-navigation/native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { RecordingPresets, useAudioRecorder } from 'expo-audio';
import { agentName } from '../brand';
import { usePartial } from '../api/partial';
import { StateFace } from '../components/ChatParts';
import { CircleStop, Mic, Square, X } from '../components/icons';
import { LensAvatar } from '../components/LensAvatar';
import { Btn, Card, T } from '../components/ui';
import { L } from '../i18n';
import { useStore, useThreadOnScreen } from '../store';
import { space, useTheme } from '../theme';
import { Talk, type Phase } from './loop';
import { speakable } from './text';

// 和语音输入一样 16 kHz 单声道 m4a，带音量；安卓用通话的音源（带回声消除，念的时候开口打断更准）
const PRESET = {
  ...RecordingPresets.HIGH_QUALITY, sampleRate: 16000, numberOfChannels: 1, bitRate: 32000, isMeteringEnabled: true,
  android: { ...RecordingPresets.HIGH_QUALITY.android, audioSource: 'voice_communication' as const },
};
const FACE = 112;

function phaseText(p: Phase): string {
  switch (p) {
    case 'listening': return L('正在听…', 'Listening…');
    case 'thinking': return L('正在思考…', 'Thinking…');
    case 'speaking': return L('正在朗读…', 'Reading aloud…');
    case 'paused': return L('已暂停', 'Paused');
    case 'blocked': return L('无法使用对讲', 'Talk mode is unavailable');
    default: return L('正在准备…', 'Getting ready…');
  }
}

export function TalkScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const thread = String(useRoute<any>().params?.thread ?? 'main');
  const insets = useSafeAreaInsets();
  const { avatar, send, stop, transcribe, typing, threads } = useStore();
  const recorder = useAudioRecorder(PRESET);
  const [talk] = useState(() => new Talk(thread, { recorder, send, transcribe, stopReply: stop }));
  const st = useSyncExternalStore(talk.subscribe, talk.snapshot);
  const partial = usePartial(thread);
  const busy = !!typing[thread];
  const list = threads[thread];
  const last = list?.[list.length - 1];
  const scroll = useRef<ScrollView>(null);
  useThreadOnScreen(thread);

  useEffect(() => { talk.use({ recorder, send, transcribe, stopReply: stop }); }, [talk, recorder, send, transcribe, stop]);
  useEffect(() => {
    talk.start();
    return () => talk.close();
  }, [talk]);
  useEffect(() => { talk.partial(partial); }, [talk, partial]);
  useEffect(() => { talk.replying(busy, last); }, [talk, busy, last]);
  useEffect(() => {
    const sub = AppState.addEventListener('change', (s) => { if (s === 'background') talk.background(); });
    return () => sub.remove();
  }, [talk]);

  const close = () => { if (nav.canGoBack()) nav.goBack(); };
  const main = st.phase === 'listening' ? { label: L('说完了', 'Done'), icon: <CircleStop size={22} color={t.onGold} /> }
    : st.phase === 'thinking' ? { label: L('停止', 'Stop'), icon: <Square size={18} color={t.onGold} /> }
      : st.phase === 'speaking' ? { label: L('打断', 'Interrupt'), icon: <Mic size={22} color={t.onGold} /> }
        : st.phase === 'paused' ? { label: L('说话', 'Talk'), icon: <Mic size={22} color={t.onGold} /> } : null;
  const ring = st.phase === 'listening' ? t.cyan : st.phase === 'speaking' ? t.goldFill : 'transparent';
  const grow = st.phase === 'listening' ? 1 + st.level * 0.35 : st.phase === 'speaking' ? 1.12 : 1;
  const reply = st.reply ? speakable(st.reply, { code: L('（代码见对话）', '(code in the chat)'), table: L('（表格见对话）', '(table in the chat)'), link: L('链接', 'link') }) : '';

  return (
    <View style={[styles.root, { backgroundColor: t.bg, paddingTop: insets.top, paddingBottom: Math.max(insets.bottom, space.lg) }]}>
      <View style={styles.top}>
        <Pressable onPress={close} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('退出对讲', 'Leave talk mode')} style={styles.close}>
          <X size={24} color={t.ink} />
        </Pressable>
        <View style={{ flex: 1, alignItems: 'center' }}>
          <T v="headline" numberOfLines={1} accessibilityRole="header">{L('对讲', 'Talk')}</T>
          <T v="caption" color={t.ink3} numberOfLines={1}>{agentName()}</T>
        </View>
        <View style={styles.close} />
      </View>

      <Pressable onPress={() => { if (st.phase === 'speaking') talk.tap(); }} disabled={st.phase !== 'speaking'} accessibilityRole={st.phase === 'speaking' ? 'button' : undefined}
        accessibilityLabel={st.phase === 'speaking' ? L('打断朗读', 'Stop reading aloud') : undefined} style={styles.stage}>
        <View style={styles.faceBox}>
          <View style={[styles.ring, { borderColor: ring, transform: [{ scale: grow }] }]} />
          <StateFace size={FACE} state={st.phase === 'thinking' ? 'working' : null}>
            <LensAvatar size={FACE} config={avatar} />
          </StateFace>
        </View>
        <T v="title" style={{ marginTop: space.lg }} accessibilityLiveRegion="polite">{phaseText(st.phase)}</T>
        {st.note ? <T v="callout" color={t.ink2} style={styles.note}>{st.note}</T> : null}
      </Pressable>

      {st.block ? (
        <Card style={styles.block}>
          <T v="callout">{st.block.text}</T>
          <View style={styles.blockBtns}>
            {st.block.kind === 'mic' ? <Btn label={L('打开设置', 'Open Settings')} kind="quiet" onPress={() => { Linking.openSettings().catch(() => {}); }} /> : null}
            {st.block.kind === 'mic' || st.block.kind === 'error' || st.block.kind === 'transcribe' ? <Btn label={L('重试', 'Retry')} onPress={() => { talk.start(); }} /> : null}
          </View>
        </Card>
      ) : (
        <ScrollView ref={scroll} style={styles.words} contentContainerStyle={{ gap: space.md, paddingBottom: space.md }}
          onContentSizeChange={() => scroll.current?.scrollToEnd({ animated: true })}>
          {st.heard ? <T v="callout" color={t.ink2}>{st.heard}</T> : null}
          {reply ? <T v="body">{reply}</T> : null}
        </ScrollView>
      )}

      {main ? (
        <View style={styles.bottom}>
          <Pressable onPress={() => talk.tap()} accessibilityRole="button" accessibilityLabel={main.label}
            style={({ pressed }) => [styles.mainBtn, { backgroundColor: t.goldFill, opacity: pressed ? 0.8 : 1 }]}>
            {main.icon}
          </Pressable>
          <T v="caption" color={t.ink2}>{main.label}</T>
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  top: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: space.sm, paddingVertical: space.sm },
  close: { width: 44, height: 44, alignItems: 'center', justifyContent: 'center' },
  stage: { alignItems: 'center', justifyContent: 'center', paddingTop: space.xl, paddingBottom: space.lg },
  faceBox: { width: FACE + 24, height: FACE + 24, alignItems: 'center', justifyContent: 'center' },
  ring: { position: 'absolute', left: 0, top: 0, right: 0, bottom: 0, borderRadius: (FACE + 24) / 2, borderWidth: 3 },
  note: { marginTop: space.sm, textAlign: 'center', paddingHorizontal: space.xl },
  words: { flex: 1, paddingHorizontal: space.xl },
  block: { marginHorizontal: space.lg, gap: space.md },
  blockBtns: { flexDirection: 'row', gap: space.sm, flexWrap: 'wrap' },
  bottom: { alignItems: 'center', gap: space.xs, paddingTop: space.md },
  mainBtn: { width: 72, height: 72, borderRadius: 36, alignItems: 'center', justifyContent: 'center' },
});
