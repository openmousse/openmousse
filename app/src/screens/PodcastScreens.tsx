// 播客的几页（服务端 ../server/podcast.py，首页在 ../think/Podcast.tsx）：
//   PodPrep  录前先聊聊：它先问一句第一反应，你答（打字或说），它按你的话排一张提纲卡；不进主对话。
//   PodRec   录：暂停一次就是一段，马上传上去转写；有主持人 / 费曼时停下它问一个（跳过、换个问法）。锁屏 / 切后台 = 暂停并传上去。
//   PodDone  录完：整理（轮询）→ 标题、一句话、你的原话（点一句听一句）、跟以前的笔记比、还没想清的、关键词、存进库、世界树不点不记；
//            费曼加讲对 / 讲错 / 漏了（对照学习台，一键加进复习）；坐一起录的先认谁是谁。
//   PodFriends 约朋友：一台手机放中间（远程每人一条音轨还没做）。
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Alert, AppState, Linking, Platform, Pressable, ScrollView, StatusBar, StyleSheet, Switch, Text, TextInput, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import Reanimated from 'react-native-reanimated';
import { useNavigation, useRoute } from '@react-navigation/native';
import { ArrowUp, Check, ChevronDown, ChevronLeft, ChevronRight, ChevronUp, CircleAlert, GraduationCap, MessageCircle, Mic, NotebookText, Paperclip, Pause, Pencil, Play, Plus, Square, TextAlignStart, TreeDeciduous, User, Users, X } from '../components/icons';
import { useBottomInset } from '../components/keyboard';
import { useSheet } from '../components/Sheet';
import { Btn, Screen, T, showError } from '../components/ui';
import * as pod from '../api/podcast';
import type { Episode, PersonPick, PodMode, PodSegment, PodSentence } from '../api/podcast';
import * as ppl from '../api/people';
import { L } from '../i18n';
import { radius, space, type, useTheme } from '../theme';
import { GrowInput, KeywordChip } from '../think/parts';
import { modeLook, modeName } from '../think/Podcast';
import { keepAwake, locate, usePodPlayer, useTakeRecorder } from '../think/podAudio';
import { MaterialsRow, MaterialsSheet } from '../think/Materials';

// 录音页不跟深浅色走：一直是深的（设计稿 PodRec）
const D = { bg: '#101216', panel: '#1A1E24', panel2: '#15181D', line: '#2A3038', line2: '#3A414B', ink: '#ECEEF0', ink2: '#A6AEB7', ink3: '#8A929B', gold: '#DDB56A', purple: '#B9A4F4', rec: '#EC7A70', btn: '#1E2329' };

function Lens({ size = 28, dark }: { size?: number; dark?: boolean }) {
  const t = useTheme();
  const r = Math.round(size / 2);
  return (
    <View style={{ width: size, height: size, borderRadius: r, backgroundColor: dark ? '#050607' : t.lensField, borderWidth: dark ? 1 : 0, borderColor: D.line, alignItems: 'center', justifyContent: 'center' }}>
      <View style={{ width: Math.round(size / 2), height: Math.round(size / 2), borderRadius: r, borderWidth: 2, borderColor: t.goldFill, borderTopColor: '#5CCFE6' }} />
    </View>
  );
}

function Header({ title, sub, onBack, icon, right }: { title: string; sub: string; onBack: () => void; icon: React.ReactNode; right?: React.ReactNode }) {
  const t = useTheme();
  return (
    <View style={[styles.head, { borderBottomColor: t.line }]}>
      <Pressable onPress={onBack} hitSlop={10} accessibilityRole="button" accessibilityLabel={L('返回', 'Back')} style={styles.back}>
        <ChevronLeft size={26} color={t.gold} />
      </Pressable>
      {icon}
      <View style={{ flex: 1, minWidth: 0 }}>
        <T v="headline" numberOfLines={1}>{title}</T>
        <T v="caption" color={t.ink3} numberOfLines={1}>{sub}</T>
      </View>
      {right}
    </View>
  );
}

const hostName = (m: PodMode) => (m === 'feynman' ? L('外行', 'Layperson') : L('主持人', 'Host'));

// —— 录前先聊聊 ——————————————————————————————————————————————————————

export function PodPrepScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const route = useRoute<any>();
  const id = route.params?.id as string;
  const [e, setE] = useState<Episode | null>(null);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState<null | 'think' | 'voice'>(null);
  const [recording, setRecording] = useState(false);
  const [editing, setEditing] = useState(false);
  const [pending, setPending] = useState<string | null>(null);  // 发出去还没回的那句：先显示成气泡
  const rec = useTakeRecorder();
  const scroller = useRef<ScrollView>(null);
  const input = useRef<TextInput>(null);
  const root = useRef<View>(null);
  const bottom = useBottomInset(root);

  const opening = useCallback(async (ep: Episode) => {
    setE(ep);
    if (ep.turns.some((x) => x.phase === 'prep')) return;
    setBusy('think');  // 还没聊过：它先问
    try { setE(await pod.prep(id, {})); } catch (err) { showError(L('它没接上', 'No reply'), err); } finally { setBusy(null); }
  }, [id]);
  useEffect(() => { pod.get(id).then(opening).catch((err) => showError(L('打不开这一期', "Couldn't open this episode"), err)); }, [id, opening]);

  const say = async (v: string, outline = false) => {
    if (busy) return;
    setBusy('think');
    setPending(v || null);
    setText('');
    try {
      setE(await pod.prep(id, { text: v || undefined, outline }));
      setEditing(false);
      setTimeout(() => scroller.current?.scrollToEnd({ animated: true }), 80);
    } catch (err) { setText(v); showError(L('它没接上', 'No reply'), err); } finally { setBusy(null); setPending(null); }
  };
  const mic = async () => {
    if (busy) return;
    if (!recording) {
      const ok = await rec.start().catch((err) => { showError(L('录不了', "Can't record"), err); return 'err' as const; });
      if (ok === 'denied') showError(L('没有麦克风权限', 'No microphone access'), L('去系统设置里打开麦克风。', 'Turn on the microphone in Settings.'));
      if (ok === 'ok') setRecording(true);
      return;
    }
    setRecording(false);
    setBusy('voice');
    try {
      const r = await rec.stop();
      if (r) setE(await pod.prepVoice(id, r.file, r.seconds));
      setTimeout(() => scroller.current?.scrollToEnd({ animated: true }), 80);
    } catch (err) { showError(L('没听清', "Didn't catch that"), err); } finally { setBusy(null); }
  };
  const record = () => nav.replace('PodRec', { id });

  const turns = e?.turns.filter((x) => x.phase === 'prep') ?? [];
  const outline = e?.outline ?? [];
  const lk = modeLook(t, e?.mode ?? 'host');
  return (
    <Screen>
      <Header title={L('录前先聊聊', 'Before recording')} sub={e ? L(`${e.title} · 不进主对话`, `${e.title} · not in the main chat`) : ''} onBack={() => nav.goBack()}
        icon={<View style={[styles.icon, { backgroundColor: t.tints.purple.soft }]}><TextAlignStart size={17} color={t.tints.purple.fg} /></View>}
        right={<Pressable onPress={record} hitSlop={8} accessibilityRole="button" style={styles.headBtn}><T v="callout" color={t.gold} style={{ fontWeight: '600' }}>{L('直接录', 'Just record')}</T></Pressable>} />
      {e && e.materials !== undefined ? (
        <View style={{ paddingHorizontal: space.lg, paddingTop: space.sm }}>
          <MaterialsRow id={id} count={e.materials} onCount={(n) => setE((x) => (x ? { ...x, materials: n } : x))} />
        </View>
      ) : null}
      <Reanimated.View ref={root} onLayout={bottom.onLayout} style={[{ flex: 1 }, bottom.style]}>
        <ScrollView ref={scroller} contentContainerStyle={{ padding: space.lg, gap: 14 }} keyboardShouldPersistTaps="handled"
          onContentSizeChange={() => scroller.current?.scrollToEnd({ animated: false })}>
          {turns.map((x) => (x.role === 'host' ? (
            <View key={x.id} style={{ flexDirection: 'row', gap: 10, alignItems: 'flex-start' }}>
              <Lens />
              <T v="body" style={{ flex: 1, lineHeight: 25 }}>{x.text}</T>
            </View>
          ) : (
            <View key={x.id} style={[styles.me, { backgroundColor: t.surface2 }]}>
              {x.voice ? (
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
                  <Mic size={12} color={t.ink3} /><T v="caption" color={t.ink3}>{L(`语音 ${pod.clock(x.voice)} · 转成了文字`, `Voice ${pod.clock(x.voice)} · transcribed`)}</T>
                </View>
              ) : null}
              <T v="body">{x.text}</T>
            </View>
          )))}
          {pending ? <View style={[styles.me, { backgroundColor: t.surface2, opacity: 0.7 }]}><T v="body">{pending}</T></View> : null}
          {busy ? (
            <View style={{ flexDirection: 'row', gap: 10, alignItems: 'center' }}>
              <Lens /><ActivityIndicator color={t.gold} />
              <T v="callout" color={t.ink3}>{busy === 'voice' ? L('在听你说的…', 'Listening to what you said…') : L('在想…', 'Thinking…')}</T>
            </View>
          ) : null}
          {outline.length ? (
            <View style={[styles.outline, { backgroundColor: t.surface, borderColor: t.goldFill }]}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                <View style={[styles.mi, { backgroundColor: t.goldSoft }]}><TextAlignStart size={15} color={t.gold} /></View>
                <T v="headline" style={{ flex: 1, fontWeight: '800' }}>{L('提纲卡', 'Outline card')}</T>
                <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L('录的时候一直在屏幕上', 'Stays on screen while you record')}</T>
              </View>
              {outline.map((o, i) => (
                <View key={i} style={{ flexDirection: 'row', gap: 10, alignItems: 'flex-start' }}>
                  <View style={[styles.num, { backgroundColor: t.goldSoft }]}><Text style={{ color: t.gold, fontSize: 12, fontWeight: '700' }}>{i + 1}</Text></View>
                  <T v="body" style={{ flex: 1, fontSize: 15, lineHeight: 22 }}>{o.text}</T>
                </View>
              ))}
              <View style={{ flexDirection: 'row', gap: 8, paddingTop: 4 }}>
                <Btn label={L('改一下', 'Change it')} kind="quiet" flex onPress={() => { setEditing(true); input.current?.focus(); }} />
                <Btn label={L('按这个开录', 'Record with this')} flex icon={<lk.Icon size={16} color={t.onGold} />} onPress={record} />
              </View>
            </View>
          ) : turns.some((x) => x.role === 'me') && !busy ? (
            <Pressable onPress={() => say('', true)} accessibilityRole="button" style={{ alignSelf: 'flex-start' }}>
              <T v="callout" color={t.gold} style={{ fontWeight: '600' }}>{L('差不多了，排提纲吧', "That's enough, draft the outline")}</T>
            </Pressable>
          ) : null}
        </ScrollView>
        <View style={[styles.composer, { borderTopColor: t.line, backgroundColor: t.bg }]}>
          <Pressable onPress={mic} disabled={busy === 'think'} accessibilityRole="button" accessibilityLabel={recording ? L('说完了', 'Done talking') : L('说一段，转成文字', 'Say it, get text')}
            style={[styles.micBtn, recording ? { backgroundColor: t.badSoft } : null]}>
            {recording ? <Square size={16} color={t.bad} fill={t.bad} /> : <Mic size={22} color={t.ink2} />}
          </Pressable>
          <TextInput ref={input} value={text} onChangeText={setText} editable={!recording} placeholder={recording ? L('在听…说完点左边', 'Listening… tap left when done') : editing ? L('说说哪条要改…', 'Which point should change…') : L('接着聊…', 'Keep talking…')}
            placeholderTextColor={t.ink3} onSubmitEditing={() => text.trim() && say(text.trim())} returnKeyType="send" accessibilityLabel={L('接着聊', 'Keep talking')}
            style={[type.body, styles.field, { backgroundColor: t.surface, borderColor: t.line, color: t.ink }]} />
          <Pressable onPress={() => text.trim() && say(text.trim())} disabled={!text.trim() || !!busy} accessibilityRole="button" accessibilityLabel={L('发出去', 'Send')}
            style={[styles.send, { backgroundColor: text.trim() ? t.goldFill : t.surface2 }]}>
            <ArrowUp size={20} color={text.trim() ? t.onGold : t.ink3} />
          </Pressable>
        </View>
      </Reanimated.View>
    </Screen>
  );
}

// —— 录 ——————————————————————————————————————————————————————————————

interface Take { idx: number; seconds: number; state: 'uploading' | 'failed' | 'done'; file: import('../data/types').PendingFile }

export function PodRecScreen() {
  const nav = useNavigation<any>();
  const route = useRoute<any>();
  const id = route.params?.id as string;
  const hostOn = route.params?.host !== false;  // 约朋友时可以关掉主持人
  const [e, setE] = useState<Episode | null>(null);
  const [takes, setTakes] = useState<Take[]>([]);
  const [phase, setPhase] = useState<'idle' | 'recording' | 'paused' | 'asking' | 'ending'>('idle');
  const [note, setNote] = useState<string | null>(null);
  const [meters, setMeters] = useState<number[]>([]);
  const [lastMeter, setLastMeter] = useState<number | undefined>(undefined);
  const rec = useTakeRecorder();
  const insets = useSafeAreaInsets();  // 这一页不用 Screen（整页深色）：顶上让出状态栏 / 灵动岛，底下让出 Home 条
  const chain = useRef<Promise<unknown>>(Promise.resolve());
  const phaseRef = useRef(phase);
  useEffect(() => { phaseRef.current = phase; }, [phase]);

  useEffect(() => { pod.get(id).then(setE).catch((err) => showError(L('打不开这一期', "Couldn't open this episode"), err)); }, [id]);
  useEffect(() => () => { keepAwake(false); }, []);

  // 音量 → 波形（按「渲染时对比上一次」更新，不在 effect 里同步 setState）
  const m = rec.state.metering;
  if (phase === 'recording' && m !== undefined && m !== lastMeter) {
    setLastMeter(m);
    setMeters((a) => [...a.slice(-35), Math.max(0.08, Math.min(1, (m + 60) / 60))]);
  }

  const mode: PodMode = e?.mode ?? 'host';
  const asks = mode !== 'solo' && (mode !== 'friends' || hostOn);
  const serverIdx = new Set((e?.segments ?? []).map((s) => s.idx));
  const committed = (e?.segments ?? []).reduce((a, s) => a + s.duration, 0) + takes.filter((x) => !serverIdx.has(x.idx)).reduce((a, x) => a + x.seconds, 0);
  const current = phase === 'recording' ? (rec.state.durationMillis ?? 0) / 1000 : 0;
  const nextIdx = Math.max(-1, ...(e?.segments ?? []).map((s) => s.idx), ...takes.map((x) => x.idx)) + 1;
  const uploading = takes.some((x) => x.state === 'uploading');
  const failed = takes.filter((x) => x.state === 'failed');

  const upload = useCallback((take: Take) => {
    chain.current = chain.current.then(async () => {
      try {
        await pod.uploadSegment(id, take.idx, take.file, take.seconds);
        setTakes((a) => a.map((x) => (x.idx === take.idx ? { ...x, state: 'done' } : x)));
      } catch {
        setTakes((a) => a.map((x) => (x.idx === take.idx ? { ...x, state: 'failed' } : x)));
      }
    });
    return chain.current;
  }, [id]);

  const start = async () => {
    setNote(null);
    try {
      const ok = await rec.start();
      if (ok === 'denied') { showError(L('没有麦克风权限', 'No microphone access'), L('去系统设置里打开麦克风。', 'Turn on the microphone in Settings.')); return; }
      setPhase('recording');
      keepAwake(true);
    } catch (err) { showError(L('录不了', "Can't record"), err); }
  };
  /** 停下这一段：传上去。then = 'ask' 传完问一个。 */
  const stopTake = useCallback(async (then: 'ask' | 'none') => {
    if (phaseRef.current !== 'recording') return;
    setPhase(then === 'ask' ? 'asking' : 'paused');
    keepAwake(false);
    try {
      const r = await rec.stop();
      if (r && r.seconds >= 0.8) {
        const take: Take = { idx: nextIdx, seconds: r.seconds, state: 'uploading', file: r.file };
        setTakes((a) => [...a, take]);
        await upload(take);
      }
      if (then === 'ask') setE(await pod.ask(id, 'next'));
    } catch (err) { showError(then === 'ask' ? L('它没问出来', 'No question this time') : L('这一段没存上', "This take wasn't saved"), err); } finally {
      setPhase((p) => (p === 'asking' || p === 'recording' ? 'paused' : p));
    }
  }, [id, nextIdx, rec, upload]);

  // 锁屏、切到后台：原生包没开后台录音，这一段先停下传上去
  useEffect(() => {
    const sub = AppState.addEventListener('change', (s) => {
      if (s !== 'active' && phaseRef.current === 'recording') {
        stopTake('none');
        setNote(L('切到后台时停了，这一段已经存上。点下面接着录。', 'Stopped when the app went to the background; that take is saved. Tap below to continue.'));
      }
    });
    return () => sub.remove();
  }, [stopTake]);

  const big = () => (phase === 'recording' ? stopTake(asks ? 'ask' : 'none') : phase === 'idle' || phase === 'paused' ? start() : undefined);
  const askNow = async () => {
    if (phase === 'recording') { stopTake('ask'); return; }
    if (phase !== 'paused' && phase !== 'idle') return;
    setPhase('asking');
    try { await chain.current; setE(await pod.ask(id, 'next')); } catch (err) { showError(L('它没问出来', 'No question this time'), err); } finally { setPhase('paused'); }
  };
  const again = async (how: 'again' | 'skip') => {
    if (phase === 'asking') return;
    if (how === 'again') setPhase('asking');
    try { setE(await pod.ask(id, how)); } catch (err) { showError(L('没换成', "Couldn't change it"), err); } finally { if (how === 'again') setPhase('paused'); }
  };
  const retryFailed = () => { for (const x of failed) { setTakes((a) => a.map((y) => (y.idx === x.idx ? { ...y, state: 'uploading' } : y))); upload(x); } };
  const end = async () => {
    if (phase === 'ending') return;
    if (phase === 'recording') await stopTake('none');
    setPhase('ending');
    try {
      await chain.current;
      if (!(e?.segments.length || takes.some((x) => x.state === 'done'))) { setPhase('paused'); showError(L('还没录到东西', 'Nothing recorded yet'), ''); return; }
      await pod.finish(id);
      nav.replace('PodDone', { id });
    } catch (err) { setPhase('paused'); showError(L('没收成尾', "Couldn't finish"), err); }
  };
  const minimize = async () => {
    if (phase === 'recording') await stopTake('none');
    nav.goBack();
  };
  const toggleDone = async (i: number) => {
    if (!e) return;
    const done = e.outline.map((o, k) => (k === i ? !o.done : o.done)).flatMap((d, k) => (d ? [k] : []));
    try { setE(await pod.patch(id, { done })); } catch { /* 下次再说 */ }
  };

  const q = [...(e?.turns ?? [])].reverse().find((x) => x.phase === 'rec' && x.role === 'host');
  const shown = q && q.status === 'asked' ? q : null;
  const recLabel = phase === 'recording' ? L('在录', 'Recording') : phase === 'ending' ? L('收尾', 'Finishing') : L('暂停了', 'Paused');
  const n = (e?.segments.length ?? 0) + takes.filter((x) => !serverIdx.has(x.idx)).length + (phase === 'recording' ? 1 : 0);
  const outline = e?.outline ?? [];
  const cur = e?.cur ?? 0;
  return (
    <View style={{ flex: 1, backgroundColor: D.bg, paddingTop: insets.top }}>
      <StatusBar barStyle="light-content" />
      <View style={styles.recHead}>
        <Pressable onPress={minimize} hitSlop={10} accessibilityRole="button" accessibilityLabel={L('收起（这一段先停下存上）', 'Close (saves this take)')} style={styles.back}>
          <ChevronDown size={24} color={D.ink} />
        </Pressable>
        <View style={{ flex: 1, minWidth: 0 }}>
          <Text numberOfLines={1} style={[type.headline, { color: D.ink, fontWeight: '700' }]}>{e?.title ?? ''}</Text>
          <Text style={[type.caption, { color: D.ink2, fontWeight: '400' }]}>{L(`${modeName(mode)} · 原声存在你的服务器`, `${modeName(mode)} · audio stays on your server`)}</Text>
        </View>
        {phase !== 'idle' || committed > 0 ? (
          <View style={styles.recPill}>
            <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: phase === 'recording' ? D.rec : D.ink2 }} />
            <Text style={{ color: D.ink, fontSize: 13, fontWeight: '700' }}>{recLabel}</Text>
          </View>
        ) : null}
      </View>

      <ScrollView contentContainerStyle={{ paddingBottom: space.lg }}>
        <View style={{ alignItems: 'center', gap: 4, paddingTop: 22, paddingBottom: 10 }}>
          <Text style={{ color: D.ink, fontSize: 56, fontWeight: '700', letterSpacing: -1, fontVariant: ['tabular-nums'] }}>{pod.clock(committed + current)}</Text>
          <Text style={{ color: D.ink2, fontSize: 13 }}>{[n ? L(`第 ${n} 段`, `Take ${n}`) : L('还没开始', 'Not started'), outline.length ? L(`提纲第 ${cur + 1} 条`, `outline point ${cur + 1}`) : null].filter(Boolean).join(' · ')}</Text>
        </View>
        <View style={styles.wave} accessible={false}>
          {(meters.length ? meters : Array.from({ length: 36 }, () => 0.1)).map((h, i, a) => (
            <View key={i} style={{ width: 4, height: Math.round(6 + h * 36), borderRadius: 2, backgroundColor: i >= a.length - 6 && phase === 'recording' ? D.gold : D.purple, opacity: phase === 'recording' ? 1 : 0.45 }} />
          ))}
        </View>

        <View style={{ paddingHorizontal: space.lg, paddingTop: 18, gap: 12 }}>
          {note ? <Text style={{ color: D.gold, fontSize: 14, lineHeight: 20 }}>{note}</Text> : null}
          {failed.length ? (
            <Pressable onPress={retryFailed} accessibilityRole="button" style={[styles.qBox, { borderColor: D.rec }]}>
              <Text style={{ color: D.rec, fontSize: 14 }}>{L(`${failed.length} 段没传上去（网络？）。点这里重传，录音还在手机上。`, `${failed.length} take(s) didn't upload (network?). Tap to retry; the audio is still on the phone.`)}</Text>
            </Pressable>
          ) : null}
          {asks ? (phase === 'asking' ? (
            <View style={[styles.qBox, { backgroundColor: D.panel, borderColor: D.line, flexDirection: 'row', alignItems: 'center', gap: 10 }]}>
              <Lens size={26} dark /><ActivityIndicator color={D.gold} />
              <Text style={{ color: D.ink2, fontSize: 14 }}>{uploading ? L('在传这一段…', 'Uploading this take…') : L(`${hostName(mode)}在想问什么…`, `The ${hostName(mode).toLowerCase()} is thinking…`)}</Text>
            </View>
          ) : shown ? (
            <View style={[styles.qBox, { backgroundColor: D.panel, borderColor: D.line, gap: 10 }]}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                <Lens size={26} dark />
                <Text style={{ color: D.gold, fontSize: 13, fontWeight: '700' }}>{hostName(mode)}</Text>
                <Text style={{ color: D.ink2, fontSize: 12 }}>{mode === 'feynman' ? L('没学过这门课', "hasn't taken the course") : L('你停下时问的', 'asked when you paused')}</Text>
              </View>
              <Text style={{ color: D.ink, fontSize: 18, lineHeight: 27, fontWeight: '600' }}>{shown.text}</Text>
              <View style={{ flexDirection: 'row', gap: 8 }}>
                <Pressable onPress={() => again('skip')} accessibilityRole="button" style={styles.dq}><Text style={styles.dqText}>{L('跳过这个', 'Skip it')}</Text></Pressable>
                <Pressable onPress={() => again('again')} accessibilityRole="button" style={styles.dq}><Text style={styles.dqText}>{L('换个问法', 'Ask differently')}</Text></Pressable>
              </View>
            </View>
          ) : (
            <View style={[styles.qBox, { borderColor: D.line2, borderStyle: 'dashed' }]}>
              <Text style={{ color: D.ink2, fontSize: 14, lineHeight: 21 }}>{mode === 'feynman'
                ? L('讲完一段停一下，它会像外行一样追问。', "Pause after a stretch and it asks like a layperson would.")
                : L(`说完停一下，${hostName(mode)}会问你一个问题。`, 'Pause when you finish a thought and the host asks you one question.')}</Text>
            </View>
          )) : (
            <View style={[styles.qBox, { borderColor: D.line2, borderStyle: 'dashed' }]}>
              <Text style={{ color: D.ink2, fontSize: 14, lineHeight: 21 }}>{mode === 'friends' ? L('只录，它不插话。录完按声音分人。', "Just recording, no questions. It tells voices apart afterwards.") : L('只录，它不插话。', "Just recording; it won't interrupt.")}</Text>
            </View>
          )}

          {!outline.length && phase === 'idle' && !committed && mode !== 'friends' ? (
            <Pressable onPress={() => nav.replace('PodPrep', { id })} accessibilityRole="button" style={[styles.qBox, { backgroundColor: D.panel2, borderColor: D.panel2, flexDirection: 'row', alignItems: 'center', gap: 10 }]}>
              <TextAlignStart size={18} color={D.gold} />
              <View style={{ flex: 1, gap: 2 }}>
                <Text style={{ color: D.ink, fontSize: 15, fontWeight: '600' }}>{L('先聊几句，出一张提纲', 'Talk it through first, get an outline')}</Text>
                <Text style={{ color: D.ink2, fontSize: 13 }}>{L('不想也行：直接点下面开始，想到哪说到哪。', 'Or just start below and say whatever comes.')}</Text>
              </View>
              <ChevronLeft size={18} color={D.ink2} style={{ transform: [{ rotate: '180deg' }] }} />
            </Pressable>
          ) : null}
          {outline.length ? (
            <View style={[styles.qBox, { backgroundColor: D.panel2, borderColor: D.panel2, gap: 7 }]}>
              <Text style={{ color: D.ink2, fontSize: 12, fontWeight: '700', letterSpacing: 0.7 }}>{L('提纲', 'OUTLINE')}</Text>
              {outline.map((o, i) => (
                <Pressable key={i} onPress={() => toggleDone(i)} accessibilityRole="checkbox" accessibilityState={{ checked: o.done }} style={{ flexDirection: 'row', gap: 8, alignItems: 'flex-start' }}>
                  <Text style={{ width: 14, fontWeight: '700', fontSize: 14, color: i === cur && !o.done ? D.gold : D.ink2 }}>{i + 1}</Text>
                  <Text style={{ flex: 1, fontSize: 14, lineHeight: 20, color: o.done ? D.ink3 : i === cur ? D.ink : D.ink2, fontWeight: i === cur && !o.done ? '600' : '400', textDecorationLine: o.done ? 'line-through' : 'none' }}>{o.text}</Text>
                </Pressable>
              ))}
            </View>
          ) : null}
          {e && e.materials !== undefined ? <MaterialsRow id={id} count={e.materials} dark onCount={(n) => setE((x) => (x ? { ...x, materials: n } : x))} /> : null}
        </View>
      </ScrollView>

      <View style={[styles.controls, { paddingBottom: Math.max(insets.bottom, 12) + 16 }]}>
        {asks ? (
          <Pressable onPress={askNow} disabled={phase === 'asking' || phase === 'ending'} accessibilityRole="button" style={[styles.side, { opacity: phase === 'asking' ? 0.5 : 1 }]}>
            <View style={styles.sideCircle}><MessageCircle size={22} color={D.ink} /></View>
            <Text style={styles.sideText}>{L('让它问', 'Ask me')}</Text>
          </Pressable>
        ) : <View style={styles.side} />}
        <Pressable onPress={big} disabled={phase === 'asking' || phase === 'ending'} accessibilityRole="button"
          accessibilityLabel={phase === 'recording' ? L('暂停', 'Pause') : L('开始录', 'Record')} style={[styles.bigBtn, { opacity: phase === 'asking' || phase === 'ending' ? 0.6 : 1 }]}>
          {phase === 'recording' ? <Pause size={30} color={D.ink} fill={D.ink} /> : <Mic size={30} color={D.ink} />}
        </Pressable>
        <Pressable onPress={end} disabled={phase === 'ending' || (!committed && phase !== 'recording')} accessibilityRole="button" style={[styles.side, { opacity: !committed && phase !== 'recording' ? 0.4 : 1 }]}>
          <View style={styles.sideCircle}>{phase === 'ending' ? <ActivityIndicator color={D.ink} /> : <Square size={18} color={D.ink} fill={D.ink} />}</View>
          <Text style={styles.sideText}>{L('结束', 'Finish')}</Text>
        </Pressable>
      </View>
    </View>
  );
}

// —— 录完 ——————————————————————————————————————————————————————————————

interface Form { title: string; oneLine: string; open: string[]; keywords: string[]; suggest: string[]; tree: string; branch: string; explain: string }

export function PodDoneScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const route = useRoute<any>();
  const sheet = useSheet();
  const id = route.params?.id as string;
  const [e, setE] = useState<Episode | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [formFor, setFormFor] = useState<string | null>(null);
  const [folder, setFolder] = useState<'notes' | 'writing' | 'study' | null>(null);
  const [tree, setTree] = useState<'ask' | 'yes' | 'no'>('ask');
  const [busy, setBusy] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [rightOpen, setRightOpen] = useState(false);
  const scroller = useRef<ScrollView>(null);
  const player = usePodPlayer(e?.segments ?? []);

  const load = useCallback(() => pod.get(id).then(setE).catch((err) => showError(L('打不开这一期', "Couldn't open this episode"), err)), [id]);
  useEffect(() => { load(); }, [load]);
  const working = e?.status === 'processing';
  useEffect(() => {
    if (!working) return undefined;
    const h = setInterval(load, 2000);
    return () => clearInterval(h);
  }, [working, load]);

  // 整理结果到了：拿它填表（同一份只填一次，你改过的不被冲掉）
  const key = e?.result ? `${e.id}:${e.updatedAt}:${e.result.title}` : null;
  if (e?.result && e.status !== 'processing' && key !== formFor && (!form || e.status === 'ready')) {
    setFormFor(key);
    setForm({ title: e.result.title, oneLine: e.result.oneLine, open: [...e.result.open], keywords: [...e.result.keywords], suggest: [...e.result.suggest],
      tree: e.result.tree, branch: e.result.branch, explain: e.feynman?.explain ?? '' });
    if (folder === null) setFolder(e.saved?.folder ?? (e.mode === 'feynman' && e.source.course ? 'study' : 'notes'));
  }
  const set = (p: Partial<Form>) => setForm((f) => (f ? { ...f, ...p } : f));
  const segs = e?.segments ?? [];
  const sentence = (sid: string): { seg: PodSegment; s: PodSentence } | null => {
    const [a, b] = sid.split('.').map(Number);
    const seg = segs.find((x) => x.idx === a);
    const s = seg?.sentences.find((x) => x.i === b);
    return seg && s ? { seg, s } : null;
  };
  const playSentence = (seg: PodSegment, s: PodSentence) => player.play(`${seg.idx}.${s.i}`, seg.idx, s.t0, s.t1);
  const playAt = (k: string, at: number | null, sid?: string) => {
    const hit = sid ? sentence(sid) : null;
    if (hit) { playSentence(hit.seg, hit.s); return; }
    const loc = at != null ? locate(segs, at) : null;
    if (loc) player.play(k, loc.idx, loc.t, loc.t + 12);
  };

  const retry = async () => { try { setE(await pod.finish(id)); } catch (err) { showError(L('没开始整理', "Couldn't start"), err); } };
  const save = async () => {
    if (!e || !form || busy) return;
    setBusy('save');
    try {
      const r = await pod.save(id, {
        folder: folder ?? 'notes', title: form.title, oneLine: form.oneLine, quotes: (e.result?.quotes ?? []).map((q) => ({ text: q.text, at: q.at })),
        open: form.open.filter((x) => x.trim()), keywords: form.keywords, relates: e.result?.relates ?? [], explain: e.feynman ? form.explain : null,
        tree: tree === 'yes' ? form.tree : null, branch: tree === 'yes' ? form.branch : null,
      });
      setE(r.episode);
      scroller.current?.scrollTo({ y: 0, animated: true });
    } catch (err) { showError(L('没存进去', "Couldn't save"), err); } finally { setBusy(null); }
  };
  const addReview = async () => {
    if (busy) return;
    setBusy('review');
    try { setE((await pod.review(id)).episode); } catch (err) { showError(L('没加进去', "Couldn't add"), err); } finally { setBusy(null); }
  };
  const edit = (seg: PodSegment, s: PodSentence) => sheet.open({ title: L('改这一句', 'Fix this sentence'), content: (close) => (
    <EditSentence initial={s.text} flag={s.flag} close={close} onSave={async (v) => {
      const r = await pod.editSentence(id, seg.idx, s.i, v);
      setE(r.episode);
    }} />
  ) });
  const del = () => {
    const go = () => pod.remove(id).then(() => nav.goBack()).catch((err) => showError(L('没删掉', "Couldn't delete"), err));
    const msg = e?.mode === 'friends'
      ? L('原声、逐字稿、素材，还有从这期记的朋友画像一起删（你改过的画像留着）；存进库的笔记还在。', "Deletes the audio, transcript, materials and the friend notes from this episode (ones you edited stay); a note you saved stays in the vault.")
      : L('原声、逐字稿和素材一起删；存进库的笔记还在。', 'Deletes the audio, transcript and materials; a note you saved stays in the vault.');
    if (Platform.OS === 'web') { if (window.confirm(msg)) go(); return; }
    Alert.alert(L('删掉这一期？', 'Delete this episode?'), msg, [{ text: L('取消', 'Cancel'), style: 'cancel' }, { text: L('删掉', 'Delete'), style: 'destructive', onPress: go }]);
  };

  const mode = e?.mode ?? 'host';
  const lk = modeLook(t, mode);
  const fy = e?.feynman;
  const flags = segs.reduce((a, s) => a + s.sentences.filter((x) => x.flag).length, 0);
  const fixed = segs.reduce((a, s) => a + s.sentences.filter((x) => x.fixed?.length).length, 0);
  const saved = e?.saved;
  const folderName = (f: 'notes' | 'writing' | 'study') => ({ notes: L('笔记', 'Notes'), writing: L('写作', 'Writing'), study: L('学习', 'Study') }[f]);
  const folders: ('notes' | 'writing' | 'study')[] = mode === 'feynman' && e?.source.course ? ['study', 'notes', 'writing'] : ['notes', 'writing'];
  const done = e?.status === 'ready' || e?.status === 'saved';

  return (
    <Screen>
      <Header title={L('录完了', 'Recorded')} sub={e ? [pod.clock(e.duration), modeName(mode), saved ? L('存好了', 'Saved') : done ? L('点哪改哪', 'Edit anything') : ''].filter(Boolean).join(' · ') : ''}
        onBack={() => nav.goBack()} icon={<View style={[styles.icon, { backgroundColor: lk.soft }]}><lk.Icon size={17} color={lk.fg} /></View>} />
      <ScrollView ref={scroller} contentContainerStyle={{ padding: space.md, gap: space.md, paddingBottom: space.xxl }} automaticallyAdjustKeyboardInsets keyboardShouldPersistTaps="handled">
        {saved ? (
          <View style={[styles.ok, { backgroundColor: t.goodSoft }]}>
            <T v="headline" color={t.good}>{L('存好了', 'Saved')}</T>
            <T v="callout" color={t.good}>{L(`库 › ${saved.path}，Obsidian 里也能看。${saved.tree ? '世界树记了一条。' : '世界树没记。'}`, `Vault › ${saved.path}, also in Obsidian.${saved.tree ? ' One memory-tree leaf added.' : ' Nothing added to the memory tree.'}`)}</T>
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm, marginTop: 4 }}>
              <Btn label={L('分享给朋友', 'Share')} kind="quiet" onPress={() => nav.navigate('Share', { from: { kind: 'note', path: saved.path } })} />
              {saved.obsidian ? <Btn label={L('在 Obsidian 里打开', 'Open in Obsidian')} kind="quiet" onPress={() => Linking.openURL(saved.obsidian!).catch(() => {})} /> : null}
              <Btn label={L('回到 Zen', 'Back to Zen')} onPress={() => nav.navigate('Tabs', { screen: '思考', params: { tab: 'podcast', at: Date.now() } })} />
            </View>
          </View>
        ) : null}

        {!e || working ? (
          <View style={[styles.wait, { backgroundColor: t.surface }]}>
            <ActivityIndicator color={t.gold} />
            <T v="callout" color={t.ink2} style={{ flex: 1 }}>{mode === 'feynman'
              ? L('正在整理…转文字、校对，再对照学习台的课件和录播，一般一分钟左右。', 'Organizing… transcribing, proofreading, then checking against your study materials. About a minute.')
              : L('正在整理…转文字、校对专有名词，再理成你的笔记，一般半分钟左右。', 'Organizing… transcribing, fixing names, then turning it into your note. About half a minute.')}</T>
          </View>
        ) : e.status === 'failed' ? (
          <View style={[styles.wait, { backgroundColor: t.warnSoft, flexDirection: 'column', alignItems: 'stretch' }]}>
            <T v="callout" color={t.warn}>{L(`没整理出来：${e.error ?? ''}`, `Couldn't organize it: ${e.error ?? ''}`)}</T>
            <Btn label={L('再试一次', 'Try again')} onPress={retry} />
          </View>
        ) : e.status === 'naming' ? (
          <Naming e={e} onDone={setE} play={(seg, s) => playSentence(seg, s)} playing={player.playing} />
        ) : null}

        {e && done && e.result?.people?.length ? e.result.people.map((x) => (
          <Pressable key={x.person} onPress={() => nav.navigate('Person', { id: x.person })} accessibilityRole="button"
            style={({ pressed }) => [styles.okRow, { backgroundColor: t.tints.pink.soft, opacity: pressed ? 0.7 : 1 }]}>
            <User size={16} color={t.tints.pink.fg} />
            <View style={{ flex: 1, gap: 1 }}>
              <T v="callout" color={t.tints.pink.fg} style={{ fontWeight: '600' }}>{x.added ? L(`${x.name}的画像多了 ${x.added} 条`, `${x.added} new notes about ${x.name}`) : L(`${x.name}的画像没有新的`, `Nothing new about ${x.name}`)}</T>
              {x.replaced || x.answered ? (
                <T v="caption" color={t.tints.pink.fg} style={{ fontWeight: '400' }}>
                  {[x.replaced ? L(`更新了 ${x.replaced} 条旧说法`, `${x.replaced} updated`) : null, x.answered ? L(`下次问问的问过了 ${x.answered} 条`, `${x.answered} follow-ups answered`) : null].filter(Boolean).join(' · ')}
                </T>
              ) : null}
            </View>
            <ChevronRight size={16} color={t.tints.pink.fg} />
          </Pressable>
        )) : null}
        {e && done && e.materials ? (
          <Pressable onPress={() => sheet.open({ title: L('这一期的素材', 'Materials for this episode'), content: () => <MaterialsSheet id={id} onCount={(n) => setE((x) => (x ? { ...x, materials: n } : x))} /> })}
            accessibilityRole="button" style={({ pressed }) => [styles.matLine, { opacity: pressed ? 0.6 : 1 }]}>
            <Paperclip size={14} color={t.ink3} />
            <T v="caption" color={t.ink3} style={{ fontWeight: '400', flex: 1 }}>{L(`参考了 ${e.materials} 条素材 · 笔记里只写出处，朋友说的不进库`, `Drew on ${e.materials} materials · the note only lists sources`)}</T>
            <ChevronRight size={14} color={t.ink3} />
          </Pressable>
        ) : null}
        {e && done && form ? (
          <>
            {fy ? (
              <>
                <View style={{ flexDirection: 'row', gap: 8 }}>
                  {([[fy.right.length, L('讲对', 'Right'), t.goodSoft, t.good], [fy.wrong.length, L('讲错', 'Wrong'), t.badSoft, t.bad], [fy.missed.length, L('漏了', 'Missed'), t.warnSoft, t.warn]] as const).map(([n, label, bg, fg]) => (
                    <View key={label} style={[styles.stat, { backgroundColor: bg }]}>
                      <Text style={{ color: fg, fontSize: 26, fontWeight: '800' }}>{n}</Text><Text style={{ color: fg, fontSize: 14, fontWeight: '700' }}>{label}</Text>
                    </View>
                  ))}
                </View>
                {fy.against ? <T v="caption" color={t.ink3} style={{ marginTop: -6 }}>{L(`对照：${fy.against}`, `Checked against: ${fy.against}`)}</T> : <T v="caption" color={t.ink3} style={{ marginTop: -6 }}>{L('没有课件可对照，按公认的讲法对照。', 'No course materials; checked against the standard view.')}</T>}
                {fy.questions.length ? (
                  <View style={[styles.card, { backgroundColor: t.surface, borderColor: t.line, gap: 10, paddingVertical: space.md }]}>
                    <T v="label" color={t.ink3}>{L('它当外行，问了你', 'IT ASKED, AS A LAYPERSON')}</T>
                    {fy.questions.map((x, i) => (
                      <View key={i} style={{ flexDirection: 'row', gap: 10, alignItems: 'flex-start' }}>
                        <Lens size={24} /><T v="body" style={{ flex: 1, fontSize: 15 }}>「{x.text}」</T><T v="caption" color={t.ink3}>{pod.clock(x.at)}</T>
                      </View>
                    ))}
                  </View>
                ) : null}
                {fy.wrong.length ? (
                  <View style={[styles.card, { backgroundColor: t.surface, borderColor: t.line, gap: 10, paddingVertical: space.md }]}>
                    <View style={styles.ch}><View style={[styles.cb, { backgroundColor: t.badSoft }]}><X size={15} color={t.bad} strokeWidth={2.5} /></View><T v="headline" style={{ fontSize: 15 }}>{L('讲错的', 'Got wrong')}</T></View>
                    {fy.wrong.map((w, i) => (
                      <View key={i} style={{ gap: 6 }}>
                        <View style={[styles.say, { backgroundColor: t.badSoft }]}>
                          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                            <T v="caption" color={t.ink3}>{L(`你说 · ${pod.clock(w.at)}`, `You said · ${pod.clock(w.at)}`)}</T>
                            <Pressable onPress={() => playAt(`w${i}`, w.at, w.id)} accessibilityRole="button" style={[styles.mini, { backgroundColor: t.surface }]}>
                              {player.playing === w.id ? <Square size={10} color={t.ink} fill={t.ink} /> : <Play size={10} color={t.ink} fill={t.ink} />}
                              <Text style={[type.caption, { color: t.ink, fontWeight: '600' }]}>{L('听原话', 'Hear it')}</Text>
                            </Pressable>
                          </View>
                          <T v="body" style={{ fontSize: 15 }}>{w.said}</T>
                        </View>
                        <View style={[styles.say, { backgroundColor: t.cyanSoft }]}>
                          <T v="caption" color={t.ink3}>{fy.against ? L('课上', 'In class') : L('公认的讲法', 'The standard view')}</T>
                          <T v="body" style={{ fontSize: 15 }}>{w.correct}</T>
                          {w.source ? <View style={[styles.src, { backgroundColor: t.surface }]}><Text style={[type.caption, { color: t.cyan }]}>{w.source}</Text></View> : null}
                        </View>
                      </View>
                    ))}
                  </View>
                ) : null}
                {fy.missed.length ? (
                  <View style={[styles.card, { backgroundColor: t.surface, borderColor: t.line, gap: 8, paddingVertical: space.md }]}>
                    <View style={styles.ch}><View style={[styles.cb, { backgroundColor: t.warnSoft }]}><CircleAlert size={15} color={t.warn} /></View><T v="headline" style={{ fontSize: 15 }}>{L('漏了的', 'Missed')}</T></View>
                    {fy.missed.map((m, i) => (
                      <View key={i} style={styles.li}>
                        <View style={[styles.dot, { backgroundColor: t.ink3 }]} />
                        <View style={{ flex: 1, gap: 2 }}>
                          <T v="body" style={{ fontSize: 15 }}>{m.text}</T>
                          {m.source ? <T v="caption" color={t.cyan}>{m.source}</T> : null}
                        </View>
                      </View>
                    ))}
                  </View>
                ) : null}
                {fy.right.length ? (
                  <View style={[styles.card, { backgroundColor: t.surface, borderColor: t.line, paddingVertical: 4 }]}>
                    <Pressable onPress={() => setRightOpen((v) => !v)} accessibilityRole="button" accessibilityState={{ expanded: rightOpen }} style={[styles.ch, { paddingVertical: 10 }]}>
                      <View style={[styles.cb, { backgroundColor: t.goodSoft }]}><Check size={15} color={t.good} strokeWidth={2.5} /></View>
                      <T v="headline" style={{ fontSize: 15, flex: 1 }}>{L(`讲对的 · ${fy.right.length} 处`, `Got right · ${fy.right.length}`)}</T>
                      {rightOpen ? <ChevronUp size={18} color={t.ink3} /> : <ChevronDown size={18} color={t.ink3} />}
                    </Pressable>
                    {rightOpen ? fy.right.map((x, i) => (
                      <View key={i} style={[styles.li, { paddingBottom: 8 }]}><View style={[styles.dot, { backgroundColor: t.ink3 }]} /><T v="body" style={{ flex: 1, fontSize: 15 }}>{x}</T></View>
                    )) : null}
                  </View>
                ) : null}
                {e.source.course && (fy.wrong.length || fy.missed.length) ? (e.reviewAt ? (
                  <View style={[styles.okRow, { backgroundColor: t.goodSoft }]}>
                    <Check size={16} color={t.good} strokeWidth={2.5} />
                    <T v="callout" color={t.good} style={{ fontWeight: '600', flex: 1 }}>{L(`加进了 ${e.source.course} · 学习台打开这一节先看这 ${fy.wrong.length + fy.missed.length} 条`, `Added to ${e.source.course} · the study desk shows these ${fy.wrong.length + fy.missed.length} first`)}</T>
                  </View>
                ) : (
                  <Btn label={busy === 'review' ? L('正在加…', 'Adding…') : L('讲错和漏了的，加进学习台复习', 'Add what was wrong or missed to study review')} kind="quiet"
                    icon={<GraduationCap size={16} color={t.ink} />} onPress={addReview} />
                )) : null}
                <View style={[styles.card, { backgroundColor: t.surface, borderColor: t.line, gap: 8, paddingVertical: space.md }]}>
                  <T v="label" color={t.ink3}>{L('你的讲解 · 整理成笔记', 'YOUR EXPLANATION · AS A NOTE')}</T>
                  <GrowInput value={form.explain} onChangeText={(v) => set({ explain: v })} multiline accessibilityLabel={L('你的讲解', 'Your explanation')} style={[type.body, { color: t.ink2, fontSize: 15 }]} />
                </View>
              </>
            ) : null}

            <View style={[styles.card, { backgroundColor: t.surface, borderColor: t.line }]}>
              <TextInput value={form.title} onChangeText={(v) => set({ title: v })} accessibilityLabel={L('标题', 'Title')} style={[styles.titleIn, { color: t.ink, borderBottomColor: t.line }]} />
              <Pressable onPress={() => setShowAll((v) => !v)} accessibilityRole="button" accessibilityState={{ expanded: showAll }} style={[styles.block, { borderTopColor: t.line, flexDirection: 'row', alignItems: 'center', gap: 8 }]}>
                <TextAlignStart size={16} color={t.ink2} />
                <T v="callout" style={{ flex: 1, fontWeight: '600' }}>{L(`逐字稿${flags ? ` · ${flags} 处听不准，已标出` : ''}${fixed ? ` · 校对改了 ${fixed} 处` : ''}`, `Transcript${flags ? ` · ${flags} unclear, marked` : ''}${fixed ? ` · ${fixed} fixed` : ''}`)}</T>
                {showAll ? <ChevronUp size={18} color={t.ink3} /> : <ChevronDown size={18} color={t.ink3} />}
              </Pressable>
              {showAll ? (
                <View style={{ gap: 2, paddingBottom: 10 }}>
                  <T v="caption" color={t.ink3} style={{ marginBottom: 6 }}>{L('点一句听一句，点笔改字（改过的词记进词表，下次就认得）。', 'Tap a sentence to hear it, the pen to fix it (fixed words go into the vocabulary).')}</T>
                  {segs.flatMap((seg) => seg.sentences.map((s) => {
                    const k = `${seg.idx}.${s.i}`;
                    const on = player.playing === k;
                    return (
                      <View key={k} style={[styles.sent, on ? { backgroundColor: t.goldSoft } : null]}>
                        <Pressable onPress={() => playSentence(seg, s)} accessibilityRole="button" style={{ flex: 1, flexDirection: 'row', gap: 8 }}>
                          <Text style={[type.caption, { color: t.ink3, width: 38, marginTop: 3, fontVariant: ['tabular-nums'] }]}>{pod.clock(seg.offset + s.t0)}</Text>
                          <View style={{ flex: 1, gap: 2 }}>
                            {s.speaker ? <T v="caption" color={t.tints.pink.fg}>{e.speakers[s.speaker] === '@me' ? L('我', 'Me') : e.speakers[s.speaker] ?? s.speaker}</T> : null}
                            <T v="body" style={{ fontSize: 15 }}>{s.text}</T>
                            {s.flag ? <T v="caption" color={t.warn}>{L(`听不准：${s.flag}`, `Unclear: ${s.flag}`)}</T> : null}
                            {s.fixed?.length ? <T v="caption" color={t.ink3}>{s.fixed.map(([a, b]) => `${a}→${b}`).join('、')}</T> : null}
                          </View>
                        </Pressable>
                        <Pressable onPress={() => edit(seg, s)} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('改这一句', 'Fix this sentence')} style={{ padding: 4 }}>
                          <Pencil size={14} color={t.ink3} />
                        </Pressable>
                      </View>
                    );
                  }))}
                  {segs.some((x) => x.status === 'failed') ? <T v="caption" color={t.warn}>{L('有一段没转出文字（原声还在）。', 'One take has no transcript (the audio is still there).')}</T> : null}
                </View>
              ) : null}
              <Block label={L('一句话', 'In one line')}>
                <GrowInput value={form.oneLine} onChangeText={(v) => set({ oneLine: v })} multiline accessibilityLabel={L('一句话', 'In one line')} style={[type.body, { color: t.ink, lineHeight: 23 }]} />
              </Block>
              {e.result?.quotes.length ? (
                <Block label={L('你的原话 · 点一句听一句', 'IN YOUR WORDS · TAP TO HEAR')}>
                  {e.result.quotes.map((q, i) => (
                    <Pressable key={i} onPress={() => playAt(`q${i}`, q.at, q.id)} accessibilityRole="button" style={styles.quote}>
                      <T v="body" style={{ flex: 1, fontSize: 15 }}>「{q.text}」</T>
                      <View style={[styles.mini, { backgroundColor: player.playing === q.id ? t.goldSoft : t.bg }]}>
                        {player.playing === q.id ? <Square size={9} color={t.ink} fill={t.ink} /> : <Play size={9} color={t.ink} fill={t.ink} />}
                        <Text style={[type.caption, { color: t.ink }]}>{pod.clock(q.at)}</Text>
                      </View>
                    </Pressable>
                  ))}
                </Block>
              ) : null}
              {e.result?.relates.length ? (
                <Block label={L('跟你以前说的', 'COMPARED WITH BEFORE')}>
                  {e.result.relates.map((x, i) => (
                    <View key={i} style={[styles.rel, { backgroundColor: t.bg }]}>
                      <T v="caption" color={t.ink3}>{L(`《${x.title}》 · 库 › ${x.path.split('/')[0]}`, `“${x.title}” · Vault › ${x.path.split('/')[0]}`)}</T>
                      <T v="body" style={{ fontSize: 15 }}>{x.then}</T>
                      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                        <View style={[styles.src, { backgroundColor: x.changed ? t.goldSoft : t.surface2, marginTop: 0 }]}><Text style={[type.caption, { color: x.changed ? t.gold : t.ink2 }]}>{x.changed ? L('想法变了', 'Changed your mind') : L('还是这么想', 'Same view')}</Text></View>
                        {x.at != null ? <T v="caption" color={t.ink3}>{L(`这期 · ${pod.clock(x.at)}`, `This episode · ${pod.clock(x.at)}`)}</T> : null}
                      </View>
                      <T v="body" style={{ fontSize: 15, fontWeight: '600' }}>{x.now}</T>
                    </View>
                  ))}
                </Block>
              ) : null}
              {form.open.length ? (
                <Block label={L('还没想清的', 'Still open')}>
                  {form.open.map((o, i) => (
                    <View key={i} style={styles.li}>
                      <View style={[styles.dot, { backgroundColor: t.ink3 }]} />
                      <GrowInput value={o} onChangeText={(v) => set({ open: form.open.map((x, k) => (k === i ? v : x)) })} multiline style={[type.body, { flex: 1, color: t.ink, fontSize: 15 }]} />
                    </View>
                  ))}
                </Block>
              ) : null}
              {e.result?.minutes ? (
                <Block label={L('每人一份纪要', 'MINUTES, ONE EACH')}>
                  {Object.entries(e.result.minutes).map(([who, pts]) => {
                    const person = e.result?.people?.find((x) => x.name === who);
                    return (
                      <View key={who} style={{ gap: 2 }}>
                        <T v="callout" style={{ fontWeight: '700' }}>{who}</T>
                        {/* 记了画像的人：纪要最后一句是「会记进画像」（服务器加的），淡一点 */}
                        {pts.map((p, i) => (person && i === pts.length - 1 && p.startsWith('（') || p.startsWith('(This goes')
                          ? <T key={i} v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{p}</T> : <T key={i} v="callout" color={t.ink2}>· {p}</T>))}
                        {person ? (
                          <Pressable onPress={() => nav.navigate('Person', { id: person.person })} accessibilityRole="button" hitSlop={6} style={{ alignSelf: 'flex-start', paddingTop: 2 }}>
                            <T v="caption" color={t.gold} style={{ fontWeight: '600' }}>{L(`看${who}的画像 ›`, `Notes about ${who} ›`)}</T>
                          </Pressable>
                        ) : null}
                      </View>
                    );
                  })}
                </Block>
              ) : null}
              <Block label={L('关键词', 'Keywords')}>
                <View style={styles.chips}>
                  {form.keywords.map((k) => (
                    <Pressable key={k} onPress={() => set({ keywords: form.keywords.filter((x) => x !== k) })} accessibilityRole="button" accessibilityLabel={L(`去掉 ${k}`, `Remove ${k}`)}>
                      <KeywordChip k={k} big />
                    </Pressable>
                  ))}
                  {form.suggest.map((k) => (
                    <Pressable key={k} onPress={() => set({ keywords: [...form.keywords, k], suggest: form.suggest.filter((x) => x !== k) })} accessibilityRole="button" style={[styles.sug, { borderColor: t.cyan }]}>
                      <Plus size={12} color={t.tints.cyan.fg} /><Text style={[type.callout, { color: t.tints.cyan.fg, fontWeight: '600' }]}>#{k}</Text>
                    </Pressable>
                  ))}
                </View>
                {form.suggest.length ? <T v="caption" color={t.ink3}>{L('虚线的是它建议的，点了才加。点已有的去掉。', 'Dashed ones are suggestions, added only if you tap. Tap one to remove it.')}</T> : null}
              </Block>
            </View>

            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
              <T v="label" color={t.ink3} style={{ textTransform: 'uppercase' }}>{L('存到库里', 'Save to the vault')}</T>
              <View style={[styles.seg, { backgroundColor: t.surface2 }]}>
                {folders.map((f) => (
                  <Pressable key={f} onPress={() => setFolder(f)} accessibilityRole="button" accessibilityState={{ selected: folder === f }} style={[styles.segItem, folder === f && { backgroundColor: t.surface }]}>
                    <Text style={[type.callout, { fontWeight: '600', color: folder === f ? t.ink : t.ink2 }]}>{folderName(f)}</Text>
                  </Pressable>
                ))}
              </View>
            </View>
            <T v="caption" color={t.ink3} style={{ marginTop: -8 }}>{L(`库 › ${folderName(folder ?? 'notes')}${folder === 'study' && e.source.course ? ` › ${e.source.course}` : ''} › ${form.title || '…'}.md`, `Vault › ${folderName(folder ?? 'notes')}${folder === 'study' && e.source.course ? ` › ${e.source.course}` : ''} › ${form.title || '…'}.md`)}</T>

            {form.tree ? (
              <View style={[styles.card, { backgroundColor: t.surface, borderColor: t.line, gap: 8, paddingVertical: space.md }]}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                  <View style={[styles.cb, { backgroundColor: t.goodSoft }]}><TreeDeciduous size={15} color={t.good} /></View>
                  <T v="headline" style={{ flex: 1, fontSize: 15 }}>{fy ? L('学完这个，想法变了吗？', 'Did this change how you think?') : L('要不要记进世界树？', 'Add to your memory tree?')}</T>
                </View>
                <GrowInput value={form.tree} onChangeText={(v) => set({ tree: v })} multiline accessibilityLabel={L('记进世界树的那一句', 'The memory to add')} style={[type.body, { color: t.ink, fontSize: 15 }]} />
                <T v="caption" color={t.ink3}>{L(`挂在「${form.branch || '主干'}」 · 你所有的 AI 以后都知道你这么想`, `On "${form.branch || 'the trunk'}" · every AI you use will know you think this`)}</T>
                {tree === 'ask' ? (
                  <View style={{ flexDirection: 'row', gap: space.sm }}>
                    <Btn label={L('不记', 'No')} kind="quiet" flex onPress={() => setTree('no')} />
                    <Btn label={L('记一条', 'Add it')} flex onPress={() => setTree('yes')} />
                  </View>
                ) : (
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                    {tree === 'yes' ? <Check size={15} color={t.good} strokeWidth={2.5} /> : <X size={15} color={t.ink3} />}
                    <T v="callout" color={tree === 'yes' ? t.good : t.ink2} style={{ flex: 1, fontWeight: '600' }}>{tree === 'yes' ? L('存进库时一起记', 'Added when you save') : L('不记，只存笔记', 'Just the note')}</T>
                    <Pressable onPress={() => setTree('ask')} hitSlop={8} accessibilityRole="button"><T v="callout" color={t.gold} style={{ fontWeight: '600' }}>{L('改主意', 'Change')}</T></Pressable>
                  </View>
                )}
              </View>
            ) : null}
            <T v="caption" color={t.ink3}>{L('原声和逐字稿留在你的服务器上，不进库。', 'The audio and transcript stay on your server, not in the vault.')}</T>
            <Btn label={busy === 'save' ? L('正在存…', 'Saving…') : saved ? L('改过了，再存一次', 'Save the changes') : L('存进库', 'Save to the vault')} icon={<NotebookText size={16} color={t.onGold} />} onPress={save} />
            {!saved ? <Btn label={L('接着录一段', 'Record more')} kind="quiet" icon={<Mic size={16} color={t.ink} />} onPress={() => nav.replace('PodRec', { id })} /> : null}
            <Pressable onPress={retry} accessibilityRole="button" style={{ alignSelf: 'center', padding: 6 }}><T v="caption" color={t.ink3}>{L('不满意？让它重新整理一遍', 'Not right? Organize it again')}</T></Pressable>
          </>
        ) : null}
        {e && !working ? <Pressable onPress={del} accessibilityRole="button" style={{ alignSelf: 'center', padding: 6 }}><T v="caption" color={t.bad}>{L('删掉这一期', 'Delete this episode')}</T></Pressable> : null}
      </ScrollView>
    </Screen>
  );
}

function Block({ label, children }: { label: string; children: React.ReactNode }) {
  const t = useTheme();
  return (
    <View style={[styles.block, { borderTopColor: t.line }]}>
      <T v="label" color={t.ink3} style={{ textTransform: 'uppercase' }}>{label}</T>
      {children}
    </View>
  );
}

function EditSentence({ initial, flag, close, onSave }: { initial: string; flag?: string; close: () => void; onSave: (v: string) => Promise<void> }) {
  const t = useTheme();
  const [v, setV] = useState(initial);
  const [busy, setBusy] = useState(false);
  const go = async () => {
    if (busy || !v.trim()) return;
    setBusy(true);
    try { await onSave(v.trim()); close(); } catch (err) { showError(L('没改成', "Couldn't change it"), err); } finally { setBusy(false); }
  };
  return (
    <View style={{ gap: space.md }}>
      {flag ? <T v="caption" color={t.warn}>{L(`听不准的是：${flag}`, `Unclear: ${flag}`)}</T> : null}
      <GrowInput value={v} onChangeText={setV} multiline autoFocus accessibilityLabel={L('这一句', 'This sentence')}
        style={[type.body, { backgroundColor: t.surface, color: t.ink, borderRadius: radius.md, paddingHorizontal: space.lg, paddingVertical: 12, minHeight: 80 }]} />
      <Btn label={busy ? L('正在改…', 'Saving…') : L('改好了', 'Save')} onPress={go} />
      <T v="caption" color={t.ink3}>{L('改掉的词进词表，下次转文字就认得。', 'Changed words go into the vocabulary, so next time it gets them right.')}</T>
    </View>
  );
}

/** 坐一起录的：第一次让你认一下谁是谁（每个声音放两句，点一句能听）。别的声音选是谁：开录前选的「谁在」、以前记过画像的人、朋友，或者新名字；
 * 对上人的，整理完会给他记几条画像（只有你看得到）；「不记画像」的只留名字。 */
type Who = { k: 'me' } | { k: 'person'; id: string; name: string } | { k: 'friend'; friend: string; name: string } | { k: 'new'; name: string } | { k: 'none' };

function Naming({ e, onDone, play, playing }: { e: Episode; onDone: (e: Episode) => void; play: (seg: PodSegment, s: PodSentence) => void; playing: string | null }) {
  const t = useTheme();
  const labels: string[] = [];
  const samples: Record<string, { seg: PodSegment; s: PodSentence }[]> = {};
  for (const seg of e.segments) {
    for (const s of seg.sentences) {
      if (!s.speaker) continue;
      if (!samples[s.speaker]) { samples[s.speaker] = []; labels.push(s.speaker); }
      if (samples[s.speaker].length < 2) samples[s.speaker].push({ seg, s });
    }
  }
  const hasPeople = e.speakerPeople !== undefined;  // 老服务器没有朋友画像：只写名字
  const [who, setWho] = useState<Record<string, Who>>(() => Object.fromEntries(labels.map((k) => {
    const v = e.speakers[k];
    const pid = e.speakerPeople?.[k];
    if (v === '@me') return [k, { k: 'me' }];
    if (pid) return [k, { k: 'person', id: pid, name: v ?? '' }];
    return [k, v ? { k: 'new', name: v } : { k: 'none' }];
  })));
  const [skip, setSkip] = useState<Record<string, boolean>>({});
  const [known, setKnown] = useState<{ people: ppl.Person[]; friends: { id: string; name: string }[] }>({ people: [], friends: [] });
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (hasPeople) ppl.list().then((r) => setKnown({ people: r.people, friends: r.friends })).catch(() => {}); }, [hasPeople]);
  // 选的顺序：这期开录前选的谁在 → 以前记过的人 → 还没对上人的朋友
  const present = e.people ?? [];
  const chips: Who[] = [
    ...present.map((p) => ({ k: 'person' as const, id: p.id, name: p.name })),
    ...known.people.filter((p) => !present.some((x) => x.id === p.id)).slice(0, 8).map((p) => ({ k: 'person' as const, id: p.id, name: p.name })),
    ...known.friends.slice(0, 6).map((f) => ({ k: 'friend' as const, friend: f.id, name: f.name })),
  ];
  const me = labels.find((k) => who[k]?.k === 'me');
  const same = (a: Who | undefined, b: Who) => !!a && a.k === b.k && (a.k === 'person' ? a.id === (b as { id: string }).id : a.k === 'friend' ? a.friend === (b as { friend: string }).friend : true);
  const markMe = (k: string) => setWho((w) => {
    const next: Record<string, Who> = Object.fromEntries(Object.entries(w).map(([a, b]) => [a, b.k === 'me' ? { k: 'none' } : b]));
    next[k] = { k: 'me' };
    // 只有两个声音、开录前只选了一个人：另一个声音就是他
    const rest = labels.filter((x) => x !== k);
    if (rest.length === 1 && present.length === 1 && next[rest[0]].k === 'none') next[rest[0]] = { k: 'person', id: present[0].id, name: present[0].name };
    return next;
  });
  const nameOf = (w: Who | undefined) => (w && (w.k === 'person' || w.k === 'friend' || w.k === 'new') ? w.name.trim() : '');
  const named = (w: Who | undefined) => !!w && (w.k === 'me' || !!nameOf(w));
  const ready = !!me && labels.every((k) => named(who[k]));
  const go = async () => {
    if (busy || !ready) return;
    setBusy(true);
    try {
      const speakers: Record<string, string> = {};
      const people: Record<string, PersonPick> = {};
      for (const k of labels) {
        const w = who[k];
        if (w.k === 'me') { speakers[k] = '@me'; continue; }
        if (w.k !== 'person' && w.k !== 'friend' && w.k !== 'new') continue;
        speakers[k] = w.name.trim();
        if (!hasPeople) continue;
        people[k] = skip[k] ? { name: w.name.trim(), skip: true } : w.k === 'person' ? { id: w.id } : w.k === 'friend' ? { friend: w.friend, name: w.name } : { name: w.name.trim() };
      }
      onDone(await pod.patch(e.id, hasPeople ? { speakers, people } : { speakers }));
    } catch (err) { showError(L('没存上', "Couldn't save"), err); } finally { setBusy(false); }
  };
  return (
    <View style={[styles.card, { backgroundColor: t.surface, borderColor: t.line, gap: 12, paddingVertical: space.md }]}>
      <T v="headline">{L('认一下谁是谁', "Who's who?")}</T>
      <T v="callout" color={t.ink2}>{hasPeople
        ? L(`按声音分出了 ${labels.length} 个人。点一句听听，标出哪个是你，别的选一下是谁：认好了会给每个人记几条画像（只有你看得到）。朋友说的只留在这一期里，不进你的库和世界树。`,
          `It found ${labels.length} voices. Tap a line to listen, mark which one is you and pick who the others are: each gets a few private notes (only you see them). What friends said stays in this episode.`)
        : L(`按声音分出了 ${labels.length} 个人。点一句听听，标出哪个是你；朋友说的只留在这一期里，不进你的库和世界树。`, `It found ${labels.length} voices. Tap a line to listen and mark which one is you; what friends said stays in this episode only.`)}</T>
      {labels.map((k) => {
        const w = who[k];
        const isMe = w?.k === 'me';
        return (
          <View key={k} style={{ gap: 8, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line, paddingTop: 10 }}>
            {samples[k].map(({ seg, s }) => (
              <Pressable key={`${seg.idx}.${s.i}`} onPress={() => play(seg, s)} accessibilityRole="button" style={{ flexDirection: 'row', gap: 6, alignItems: 'flex-start' }}>
                {playing === `${seg.idx}.${s.i}` ? <Square size={12} color={t.ink2} fill={t.ink2} /> : <Play size={12} color={t.ink2} fill={t.ink2} />}
                <T v="callout" style={{ flex: 1 }}>{s.text}</T>
              </Pressable>
            ))}
            <View style={styles.chips}>
              <Pressable onPress={() => markMe(k)} accessibilityRole="button" accessibilityLabel={L('这是我', "That's me")} accessibilityState={{ selected: isMe }}
                style={[styles.pchip, { borderColor: isMe ? t.goldFill : t.line, backgroundColor: isMe ? t.goldSoft : t.bg }]}>
                {isMe ? <Check size={14} color={t.gold} /> : null}<Text style={[type.callout, { color: isMe ? t.gold : t.ink, fontWeight: '600' }]}>{L('这是我', "That's me")}</Text>
              </Pressable>
              {!isMe && hasPeople ? chips.map((c) => {
                const on = same(w, c);
                const key = c.k === 'person' ? c.id : c.k === 'friend' ? c.friend : '';
                return (
                  <Pressable key={key} onPress={() => setWho((x) => ({ ...x, [k]: c }))} accessibilityRole="button" accessibilityState={{ selected: on }}
                    style={[styles.pchip, { borderColor: on ? t.tints.pink.fg : t.line, backgroundColor: on ? t.tints.pink.soft : t.bg }]}>
                    {on ? <Check size={14} color={t.tints.pink.fg} /> : null}
                    <Text style={[type.callout, { color: on ? t.tints.pink.fg : t.ink, fontWeight: '600' }]}>{c.k === 'person' || c.k === 'friend' ? c.name : ''}</Text>
                    {c.k === 'friend' ? <Text style={[type.caption, { color: t.ink3 }]}>{L('朋友', 'friend')}</Text> : null}
                  </Pressable>
                );
              }) : null}
            </View>
            {!isMe ? (
              <TextInput value={w?.k === 'new' ? w.name : ''} onChangeText={(v) => setWho((x) => ({ ...x, [k]: v ? { k: 'new', name: v } : { k: 'none' } }))}
                placeholder={hasPeople && chips.length ? L('不在上面？写个名字', 'Not listed? Type a name') : L('朋友叫什么', "Friend's name")} placeholderTextColor={t.ink3}
                style={[type.callout, { backgroundColor: t.bg, color: t.ink, borderRadius: radius.sm, paddingHorizontal: 10, height: 36 }]} />
            ) : null}
            {!isMe && hasPeople && named(w) ? (
              <Pressable onPress={() => setSkip((x) => ({ ...x, [k]: !x[k] }))} accessibilityRole="checkbox" accessibilityState={{ checked: !skip[k] }} hitSlop={6}
                style={{ flexDirection: 'row', alignItems: 'center', gap: 6, alignSelf: 'flex-start' }}>
                <View style={[styles.box, { borderColor: skip[k] ? t.ink3 : t.goldFill, backgroundColor: skip[k] ? 'transparent' : t.goldFill }]}>
                  {!skip[k] ? <Check size={11} color={t.onGold} strokeWidth={3} /> : null}
                </View>
                <T v="caption" color={t.ink2} style={{ fontWeight: '400' }}>{skip[k]
                  ? L('不记画像，只写名字', 'No notes, just the name')
                  : L(`给${nameOf(w)}记画像（只有你看得到）`, `Keep notes about ${nameOf(w)} (only you)`)}</T>
              </Pressable>
            ) : null}
          </View>
        );
      })}
      <Btn label={busy ? L('正在整理…', 'Organizing…') : !me ? L('先标出哪个是你', 'Mark which one is you') : ready ? L('认好了，接着整理', 'Done, organize it') : L('每个声音都选一下是谁', 'Say who each voice is')} onPress={go} />
    </View>
  );
}

// —— 约朋友 ——————————————————————————————————————————————————————————

export function PodFriendsScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const route = useRoute<any>();
  // 「今天聊点什么」里的「约小林聊…」：带着题目和人进来
  const [title, setTitle] = useState<string>(route.params?.title ?? '');
  const [host, setHost] = useState(true);
  const [busy, setBusy] = useState(false);
  const [hasPeople, setHasPeople] = useState(false);
  const [known, setKnown] = useState<{ people: ppl.Person[]; friends: { id: string; name: string }[] }>({ people: [], friends: [] });
  const [chosen, setChosen] = useState<Who[]>(() => (route.params?.person ? [{ k: 'person', id: route.params.person, name: route.params.name ?? '' }] : []));
  const [typed, setTyped] = useState('');
  const [remember, setRemember] = useState<Record<string, ppl.PersonNote[]>>({});
  useEffect(() => {
    pod.podFeatures().then((f) => {
      setHasPeople(f.people);
      if (f.people) ppl.list().then((r) => setKnown({ people: r.people, friends: r.friends })).catch(() => {});
    });
  }, []);
  // 选了以前记过的人：拿他的画像，给你看主持人会记得什么
  const ids = chosen.flatMap((c) => (c.k === 'person' ? [c.id] : [])).join(',');
  useEffect(() => {
    for (const pid of ids ? ids.split(',') : []) {
      ppl.get(pid).then((r) => setRemember((m) => ({ ...m, [pid]: r.notes.filter((n) => n.status === 'active') }))).catch(() => {});
    }
  }, [ids]);
  const key = (c: Who) => (c.k === 'person' ? c.id : c.k === 'friend' ? `f:${c.friend}` : c.k === 'new' ? `n:${c.name}` : '');
  const toggle = (c: Who) => setChosen((xs) => (xs.some((x) => key(x) === key(c)) ? xs.filter((x) => key(x) !== key(c)) : [...xs, c]));
  const addTyped = () => {
    const v = typed.trim();
    if (!v) return;
    const hit = known.people.find((p) => p.name.toLowerCase() === v.toLowerCase());
    toggle(hit ? { k: 'person', id: hit.id, name: hit.name } : { k: 'new', name: v });
    setTyped('');
  };
  const go = async () => {
    if (busy || !title.trim()) return;
    setBusy(true);
    try {
      const people = chosen.flatMap((c): PersonPick[] => (c.k === 'person' ? [{ id: c.id }] : c.k === 'friend' ? [{ friend: c.friend, name: c.name }] : c.k === 'new' ? [{ name: c.name }] : []));
      const src = route.params?.person ? { kind: 'person' as const, person: route.params.person, name: route.params.name } : { kind: 'own' as const };
      const e = await pod.create({ title: title.trim(), mode: 'friends', source: src, ...(hasPeople ? { people } : {}) });
      nav.replace('PodRec', { id: e.id, host });
    } catch (err) { showError(L('没开成', "Couldn't start"), err); } finally { setBusy(false); }
  };
  const lk = modeLook(t, 'friends');
  const options: Who[] = [
    ...known.people.map((p) => ({ k: 'person' as const, id: p.id, name: p.name })),
    ...known.friends.map((f) => ({ k: 'friend' as const, friend: f.id, name: f.name })),
  ];
  const extra = chosen.filter((c) => !options.some((o) => key(o) === key(c)));  // 新写的名字、带进来但列表里还没有的
  const recall = chosen.flatMap((c) => (c.k === 'person' && remember[c.id]?.length ? [{ name: c.name || known.people.find((p) => p.id === c.id)?.name || '', notes: remember[c.id] }] : []));
  return (
    <Screen>
      <Header title={L('约朋友一起录', 'Record with friends')} sub={L('一台手机放中间', 'One phone in the middle')} onBack={() => nav.goBack()}
        icon={<View style={[styles.icon, { backgroundColor: lk.soft }]}><Users size={17} color={lk.fg} /></View>} />
      <ScrollView contentContainerStyle={{ padding: space.md, gap: space.md }} automaticallyAdjustKeyboardInsets keyboardShouldPersistTaps="handled">
        <TextInput value={title} onChangeText={setTitle} placeholder={L('这期聊什么？', 'What will you talk about?')} placeholderTextColor={t.ink3} accessibilityLabel={L('这期聊什么', 'Topic')}
          style={[type.body, { backgroundColor: t.surface, color: t.ink, borderRadius: radius.md, paddingHorizontal: space.lg, paddingVertical: 12 }]} />
        {hasPeople ? (
          <View style={[styles.card, { backgroundColor: t.surface, borderColor: t.line, gap: 10, paddingVertical: space.md }]}>
            <T v="headline" style={{ fontSize: 15 }}>{L('谁在', "Who's here")}</T>
            <View style={styles.chips}>
              {[...options, ...extra].map((c) => {
                const on = chosen.some((x) => key(x) === key(c));
                return (
                  <Pressable key={key(c)} onPress={() => toggle(c)} accessibilityRole="checkbox" accessibilityState={{ checked: on }}
                    style={[styles.pchip, { borderColor: on ? t.tints.pink.fg : t.line, backgroundColor: on ? t.tints.pink.soft : t.bg }]}>
                    {on ? <Check size={14} color={t.tints.pink.fg} /> : null}
                    <Text style={[type.callout, { color: on ? t.tints.pink.fg : t.ink, fontWeight: '600' }]}>{c.k === 'person' ? (c.name || known.people.find((p) => p.id === c.id)?.name || '…') : c.k === 'friend' || c.k === 'new' ? c.name : ''}</Text>
                    {c.k === 'friend' ? <Text style={[type.caption, { color: t.ink3 }]}>{L('朋友', 'friend')}</Text> : null}
                  </Pressable>
                );
              })}
            </View>
            <TextInput value={typed} onChangeText={setTyped} onSubmitEditing={addTyped} returnKeyType="done" placeholder={L('写个名字，回车加上', 'Type a name, press return')} placeholderTextColor={t.ink3}
              accessibilityLabel={L('加一个人', 'Add someone')} style={[type.callout, { backgroundColor: t.bg, color: t.ink, borderRadius: radius.sm, paddingHorizontal: 10, height: 36 }]} />
            <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L('选了的人，AI 主持人会接上你们上次聊的；录完认人时也排在最前面。', 'The AI host picks up where you left off with them; they also come first when you mark who is who.')}</T>
          </View>
        ) : null}
        {recall.length ? (
          <View style={[styles.card, { backgroundColor: t.tints.pink.soft, borderColor: t.tints.pink.soft, gap: 6, paddingVertical: space.md }]}>
            <T v="label" color={t.tints.pink.fg}>{L('主持人记得', 'THE HOST REMEMBERS')}</T>
            {recall.map((r) => (
              <View key={r.name} style={{ gap: 2 }}>
                {[...r.notes.filter((n) => n.kind === 'ask'), ...r.notes.filter((n) => n.kind !== 'ask')].slice(0, 3).map((n) => (
                  <T key={n.id} v="callout" color={t.ink}>{`${r.name} · ${n.text}`}</T>
                ))}
              </View>
            ))}
          </View>
        ) : null}
        <View style={[styles.card, { backgroundColor: t.surface, borderColor: t.line, gap: 6, paddingVertical: space.md }]}>
          <T v="headline" style={{ fontSize: 15 }}>{L('一台手机放中间', 'One phone in the middle')}</T>
          <T v="callout" color={t.ink2}>{L('录完按声音分人，第一次让你认一下谁是谁。', "Afterwards it tells voices apart; the first time you mark who's who.")}</T>
        </View>
        <View style={[styles.card, { backgroundColor: t.surface, borderColor: t.line, flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: space.md }]}>
          <Lens />
          <View style={{ flex: 1 }}>
            <T v="headline" style={{ fontSize: 15 }}>{L('AI 主持人一起', 'With the AI host')}</T>
            <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L('大家都停下时才问', 'Asks only when everyone pauses')}</T>
          </View>
          <Switch value={host} onValueChange={setHost} trackColor={{ true: t.goldFill, false: t.surface2 }} />
        </View>
        <T v="callout" color={t.ink2}>{hasPeople
          ? L('开录前跟大家说一声：屏幕上一直亮着「在录」，录完会给参与的人记画像（只有你看得到）。', 'Tell everyone before you start: the screen shows “Recording” the whole time, and afterwards each person gets a few private notes (only you see them).')
          : L('开录前跟大家说一声，屏幕上一直亮着「在录」。', 'Tell everyone before you start; the screen shows “Recording” the whole time.')}</T>
        <View style={[styles.card, { backgroundColor: t.surface, borderColor: t.line, gap: 6, paddingVertical: space.md }]}>
          <T v="label" color={t.ink3}>{L('录完', 'AFTERWARDS')}</T>
          <T v="callout">{L('· 每人一份纪要，按各自说的整理', '· Minutes for each person, from what they said')}</T>
          <T v="callout">{L('· 只有你说的，进你的库和世界树', '· Only what you said goes into your vault and memory tree')}</T>
          <T v="callout">{L('· 朋友说的只留在这一期里', "· What friends said stays in this episode")}</T>
          {hasPeople ? <T v="callout">{L('· 给每个朋友记几条画像：在做的事、在意的、下次问问。只有你看得到，能改能删', '· A few notes about each friend: what they are up to, what they care about, what to ask next time. Only you see them; edit or delete any')}</T> : null}
        </View>
        <Btn label={busy ? L('正在开…', 'Starting…') : L('开始录', 'Start recording')} icon={<Mic size={16} color={t.onGold} />} onPress={go} />
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  head: { flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingHorizontal: space.sm, paddingVertical: space.sm, borderBottomWidth: StyleSheet.hairlineWidth },
  back: { width: 36, height: 36, alignItems: 'center', justifyContent: 'center' },
  headBtn: { paddingHorizontal: 8, paddingVertical: 6 },
  icon: { width: 34, height: 34, borderRadius: 17, alignItems: 'center', justifyContent: 'center' },
  me: { alignSelf: 'flex-end', maxWidth: '82%', borderRadius: 18, borderBottomRightRadius: 6, paddingHorizontal: 14, paddingVertical: 10, gap: 4 },
  outline: { borderRadius: 16, borderWidth: 2, padding: 14, gap: 10 },
  mi: { width: 30, height: 30, borderRadius: 9, alignItems: 'center', justifyContent: 'center' },
  num: { width: 22, height: 22, borderRadius: 11, alignItems: 'center', justifyContent: 'center', marginTop: 1 },
  composer: { flexDirection: 'row', alignItems: 'center', gap: 4, paddingHorizontal: 10, paddingTop: 10, paddingBottom: 10, borderTopWidth: StyleSheet.hairlineWidth },
  micBtn: { width: 44, height: 44, borderRadius: 22, alignItems: 'center', justifyContent: 'center' },
  field: { flex: 1, minWidth: 0, height: 44, borderRadius: 22, borderWidth: StyleSheet.hairlineWidth, paddingHorizontal: 16, marginHorizontal: 4 },
  send: { width: 44, height: 44, borderRadius: 22, alignItems: 'center', justifyContent: 'center' },
  recHead: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 12, paddingTop: 14, paddingBottom: 6 },
  recPill: { height: 28, paddingHorizontal: 10, borderRadius: 14, backgroundColor: D.btn, flexDirection: 'row', alignItems: 'center', gap: 6 },
  wave: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 3, height: 48, paddingHorizontal: 16 },
  qBox: { borderRadius: 16, borderWidth: 1, padding: 14 },
  dq: { height: 34, paddingHorizontal: 12, borderRadius: 17, borderWidth: 1, borderColor: D.line2, justifyContent: 'center' },
  dqText: { color: D.ink, fontSize: 13, fontWeight: '600' },
  controls: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 24, paddingTop: 10 },
  side: { width: 72, alignItems: 'center', gap: 6 },
  sideCircle: { width: 56, height: 56, borderRadius: 28, backgroundColor: D.btn, alignItems: 'center', justifyContent: 'center' },
  sideText: { color: D.ink, fontSize: 12, fontWeight: '600' },
  bigBtn: { width: 80, height: 80, borderRadius: 40, borderWidth: 3, borderColor: D.gold, backgroundColor: '#1A1E24', alignItems: 'center', justifyContent: 'center' },
  ok: { borderRadius: radius.md + 2, padding: space.md, gap: 4 },
  okRow: { flexDirection: 'row', alignItems: 'center', gap: 8, borderRadius: radius.md, padding: space.md },
  wait: { flexDirection: 'row', alignItems: 'center', gap: space.md, borderRadius: radius.md + 2, padding: space.lg },
  card: { borderRadius: radius.lg - 2, borderWidth: StyleSheet.hairlineWidth, paddingHorizontal: space.md, paddingTop: space.sm },
  titleIn: { fontSize: 19, fontWeight: '800', paddingVertical: 8, borderBottomWidth: 1, borderStyle: 'dashed' },
  block: { gap: 6, paddingVertical: 12, borderTopWidth: StyleSheet.hairlineWidth },
  li: { flexDirection: 'row', alignItems: 'flex-start', gap: 8 },
  dot: { width: 5, height: 5, borderRadius: 3, marginTop: 10 },
  src: { alignSelf: 'flex-start', borderRadius: 11, paddingHorizontal: 8, paddingVertical: 3, marginTop: 2 },
  mini: { flexDirection: 'row', alignItems: 'center', gap: 4, height: 28, paddingHorizontal: 10, borderRadius: 14 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, alignItems: 'center' },
  sug: { flexDirection: 'row', alignItems: 'center', gap: 3, height: 28, paddingHorizontal: 10, borderRadius: 14, borderWidth: 1, borderStyle: 'dashed' },
  seg: { flexDirection: 'row', borderRadius: 11, padding: 3, gap: 2 },
  segItem: { height: 32, paddingHorizontal: 14, borderRadius: 9, alignItems: 'center', justifyContent: 'center' },
  stat: { flex: 1, flexDirection: 'row', alignItems: 'baseline', gap: 6, borderRadius: 14, paddingHorizontal: 12, paddingVertical: 10 },
  ch: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  cb: { width: 28, height: 28, borderRadius: 9, alignItems: 'center', justifyContent: 'center' },
  say: { borderRadius: 12, padding: 10, gap: 4 },
  sent: { flexDirection: 'row', alignItems: 'flex-start', gap: 4, borderRadius: 8, paddingVertical: 6, paddingHorizontal: 4 },
  quote: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 4 },
  rel: { borderRadius: 12, padding: 10, gap: 4 },
  matLine: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 4, marginTop: -4 },
  pchip: { flexDirection: 'row', alignItems: 'center', gap: 4, height: 32, paddingHorizontal: 12, borderRadius: 16, borderWidth: 1 },
  box: { width: 16, height: 16, borderRadius: 4, borderWidth: 1.5, alignItems: 'center', justifyContent: 'center' },
});
