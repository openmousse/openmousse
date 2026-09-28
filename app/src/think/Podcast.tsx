// 「思考」的第三块：播客。今天聊点什么（服务器从 Zen、库里「还没想清的」、学习台、截止、世界树里挑，写明从哪来）、四种录法、
// 录前先聊聊、节目列表（点播放听整期）、底下「开始录」。录、整理、存库在 PodcastScreens.tsx。
import React, { useCallback, useState } from 'react';
import { ActivityIndicator, Alert, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { useFocusEffect, useNavigation } from '@react-navigation/native';
import { CalendarDays, ChevronRight, GraduationCap, Lightbulb, MessageCircle, Mic, Play, RefreshCw, Square, TextAlignStart, TreeDeciduous, Users } from '../components/icons';
import { useSheet } from '../components/Sheet';
import { Btn, PullRefresh, T, showError } from '../components/ui';
import * as pod from '../api/podcast';
import type { PodBrief, PodHome, PodMode, PodSource, PodSuggestion } from '../api/podcast';
import { L } from '../i18n';
import { radius, space, type, useTheme, type Theme } from '../theme';
import { usePodPlayer } from './podAudio';

export const modeName = (m: PodMode) => ({ solo: L('自己讲', 'Solo'), host: L('有主持人', 'With a host'), friends: L('约朋友', 'With friends'), feynman: L('费曼', 'Feynman') }[m]);

export function modeLook(t: Theme, m: PodMode) {
  switch (m) {
    case 'solo': return { Icon: Mic, ...t.tints.purple, sub: L('纯输出，它只听', 'You talk, it listens') };
    case 'host': return { Icon: MessageCircle, ...t.tints.gold, sub: L('你停下，它问一个', 'Pause and it asks one') };
    case 'friends': return { Icon: Users, ...t.tints.pink, sub: L('一台手机，一起录', 'One phone, together') };
    default: return { Icon: GraduationCap, ...t.tints.cyan, sub: L('讲给外行听，对照课件', 'Teach a layperson, check the slides') };
  }
}

function sourceLook(t: Theme, s: PodSource) {
  switch (s.kind) {
    case 'study': return { Icon: GraduationCap, ...t.tints.cyan };
    case 'deadline': return { Icon: CalendarDays, ...t.tints.orange };
    case 'tree': return { Icon: TreeDeciduous, ...t.tints.green };
    case 'open': return { Icon: Lightbulb, ...t.tints.gold };
    default: return { Icon: Mic, ...t.tints.purple };
  }
}

/** 一期从哪一步接着：还在聊 → 录前，录到一半 → 接着录，其余 → 录完那页。 */
export function openEpisode(nav: { navigate: (name: string, params?: object) => void }, e: Pick<PodBrief, 'id' | 'status' | 'mode'>) {
  if (e.status === 'prep') nav.navigate('PodPrep', { id: e.id });
  else if (e.status === 'recording') nav.navigate('PodRec', { id: e.id });
  else nav.navigate('PodDone', { id: e.id });
}

function dayLabel(iso: string) {
  const d = new Date(iso);
  const now = new Date();
  const days = Math.round((new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() - new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()) / 86400000);
  if (days <= 0) return L('今天', 'Today');
  if (days === 1) return L('昨天', 'Yesterday');
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

export function Podcast() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const sheet = useSheet();
  const [data, setData] = useState<PodHome | null>(null);
  const [picking, setPicking] = useState(false);
  const [pickError, setPickError] = useState<string | null>(null);
  const [mode, setMode] = useState<PodMode>('host');
  const [busy, setBusy] = useState<string | null>(null);
  const player = usePodPlayer([]);

  const pick = useCallback(async (exclude: string[] = []) => {
    setPicking(true);
    setPickError(null);
    try {
      const s = await pod.suggest(exclude);
      setData((d) => (d ? { ...d, suggestions: s } : d));
    } catch (e) { setPickError(e instanceof Error ? e.message : String(e)); } finally { setPicking(false); }
  }, []);
  const load = useCallback(async () => {
    try {
      const h = await pod.home();
      setData(h);
      if (h.suggestions === null) pick();
    } catch (e) { showError(L('播客没打开', "Couldn't load the podcast"), e); }
  }, [pick]);
  useFocusEffect(useCallback(() => { load(); }, [load]));

  const start = async (title: string, m: PodMode, source: PodSource | undefined, then: 'prep' | 'rec') => {
    if (busy) return;
    setBusy(title);
    try {
      const e = await pod.create({ title, mode: m, source });
      nav.navigate(then === 'prep' ? 'PodPrep' : 'PodRec', { id: e.id });
    } catch (e) { showError(L('没开成', "Couldn't start"), e); } finally { setBusy(null); }
  };
  const fromSuggestion = (s: PodSuggestion) => start(s.title, s.mode, s.source, s.mode === 'host' ? 'prep' : 'rec');
  const askTopic = (then: 'prep' | 'rec') => {
    if (mode === 'friends' && then === 'rec') { nav.navigate('PodFriends'); return; }
    sheet.open({ title: then === 'prep' ? L('录前先聊聊', 'Talk it through first') : L(`开始录 · ${modeName(mode)}`, `Record · ${modeName(mode)}`),
      content: (close) => <TopicSheet mode={mode} then={then} close={close} onGo={(title) => start(title, mode, { kind: 'own' }, then)} /> });
  };
  const playAll = async (e: PodBrief) => {
    try {
      if (player.playing === e.id) { player.stop(); return; }
      const full = await pod.get(e.id);
      if (!full.segments.length) return;
      player.play(e.id, full.segments[0].idx, 0, null, true, full.segments);
    } catch (err) { showError(L('放不了', "Couldn't play"), err); }
  };
  const del = (e: PodBrief) => {
    const go = () => pod.remove(e.id).then(load).catch((err) => showError(L('没删掉', "Couldn't delete"), err));
    const msg = L('原声和逐字稿一起删；存进库的笔记还在。', 'Deletes the audio and transcript; a note you saved stays in the vault.');
    if (Platform.OS === 'web') { if (window.confirm(`${e.title}\n${msg}`)) go(); return; }
    Alert.alert(L(`删掉「${e.title}」？`, `Delete “${e.title}”?`), msg, [{ text: L('取消', 'Cancel'), style: 'cancel' }, { text: L('删掉', 'Delete'), style: 'destructive', onPress: go }]);
  };

  const sugg = data?.suggestions ?? [];
  const eps = data?.episodes ?? [];
  const ml = modeLook(t, mode);
  return (
    <View style={{ flex: 1 }}>
      <ScrollView contentContainerStyle={{ paddingHorizontal: space.lg, paddingBottom: 110, gap: 10 }} refreshControl={<PullRefresh onRefresh={load} />}>
        <View style={styles.labelRow}>
          <T v="label" color={t.ink3} style={styles.caps}>{L('今天聊点什么', 'Talk about today')}</T>
          <Pressable onPress={() => pick(sugg.map((s) => s.title))} disabled={picking} hitSlop={8} accessibilityRole="button" style={{ flexDirection: 'row', alignItems: 'center', gap: 4, opacity: picking ? 0.5 : 1 }}>
            <RefreshCw size={13} color={t.gold} />
            <T v="callout" color={t.gold} style={{ fontWeight: '600', fontSize: 13 }}>{L('换一批', 'Others')}</T>
          </Pressable>
        </View>
        <View style={[styles.card, { backgroundColor: t.surface, borderColor: t.line }]}>
          {picking && !sugg.length ? (
            <View style={styles.wait}><ActivityIndicator color={t.gold} /><T v="callout" color={t.ink2}>{L('在从你的笔记、学习台和截止日期里挑…', 'Picking from your notes, study desk and deadlines…')}</T></View>
          ) : pickError && !sugg.length ? (
            <View style={[styles.wait, { flexDirection: 'column', alignItems: 'stretch' }]}>
              <T v="callout" color={t.warn}>{L(`没挑出来：${pickError}`, `Couldn't pick: ${pickError}`)}</T>
              <Btn label={L('再挑一次', 'Try again')} kind="quiet" onPress={() => pick()} />
            </View>
          ) : !sugg.length ? (
            <View style={styles.wait}><T v="callout" color={t.ink2}>{L('今天没挑出来。自己起个题，点下面「开始录」。', 'Nothing picked today. Pick your own topic with Record below.')}</T></View>
          ) : sugg.map((s, i) => {
            const lk = sourceLook(t, s.source);
            const act = s.mode === 'host' ? L('先聊聊', 'Talk first') : s.mode === 'feynman' ? L('费曼', 'Feynman') : L('录', 'Record');
            return (
              <Pressable key={`${s.title}${i}`} onPress={() => fromSuggestion(s)} disabled={!!busy} accessibilityRole="button" accessibilityLabel={`${s.title}，${s.source.label ?? ''}，${act}`}
                style={({ pressed }) => [styles.row, i ? { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line } : null, { opacity: pressed ? 0.7 : 1 }]}>
                <View style={[styles.tile, { backgroundColor: lk.soft }]}><lk.Icon size={17} color={lk.fg} /></View>
                <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
                  <T v="headline" style={{ fontSize: 15, fontWeight: '700' }}>{s.title}</T>
                  {s.source.label ? <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{s.source.label}</T> : null}
                </View>
                <View style={[styles.go, { backgroundColor: t.bg }]}>
                  {busy === s.title ? <ActivityIndicator size="small" color={t.ink2} /> : <Text style={[type.callout, { color: t.ink, fontWeight: '700', fontSize: 13 }]}>{act}</Text>}
                </View>
              </Pressable>
            );
          })}
        </View>

        <T v="label" color={t.ink3} style={[styles.caps, { paddingTop: 8, paddingHorizontal: 4 }]}>{L('怎么录', 'How')}</T>
        <View style={styles.grid}>
          {(['solo', 'host', 'friends', 'feynman'] as const).map((m) => {
            const lk = modeLook(t, m);
            const on = m === mode;
            return (
              <Pressable key={m} onPress={() => setMode(m)} accessibilityRole="button" accessibilityState={{ selected: on }}
                style={[styles.mode, { backgroundColor: t.surface, borderColor: on ? t.goldFill : t.line, borderWidth: on ? 2 : StyleSheet.hairlineWidth, padding: on ? 11 : 12 }]}>
                <View style={[styles.mi, { backgroundColor: lk.soft }]}><lk.Icon size={16} color={lk.fg} /></View>
                <View style={{ gap: 2 }}>
                  <T v="headline" style={{ fontSize: 15, fontWeight: '700' }}>{modeName(m)}</T>
                  <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{lk.sub}</T>
                </View>
              </Pressable>
            );
          })}
        </View>
        {mode !== 'friends' ? (
          <Pressable onPress={() => askTopic('prep')} accessibilityRole="button" style={({ pressed }) => [styles.prepRow, { backgroundColor: t.surface, borderColor: t.line, opacity: pressed ? 0.7 : 1 }]}>
            <View style={[styles.mi, { backgroundColor: t.surface2 }]}><TextAlignStart size={16} color={t.ink2} /></View>
            <T v="body" style={{ flex: 1, fontSize: 15, fontWeight: '600' }}>{L('录前先聊聊，出一张提纲卡', 'Talk it through first, get an outline card')}</T>
            <ChevronRight size={16} color={t.ink3} />
          </Pressable>
        ) : null}

        {eps.length ? <T v="label" color={t.ink3} style={[styles.caps, { paddingTop: 8, paddingHorizontal: 4 }]}>{L(`节目 · ${eps.length} 期`, `Episodes · ${eps.length}`)}</T> : null}
        {eps.length ? (
          <View style={[styles.card, { backgroundColor: t.surface, borderColor: t.line }]}>
            {eps.map((e, i) => {
              const lk = modeLook(t, e.mode);
              const playing = player.playing === e.id;
              const chip = e.chip ? chipColors(t, e.chip.tone) : null;
              return (
                <Pressable key={e.id} onPress={() => openEpisode(nav, e)} onLongPress={() => del(e)} accessibilityRole="button"
                  style={({ pressed }) => [styles.row, i ? { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line } : null, { opacity: pressed ? 0.7 : 1 }]}>
                  <Pressable onPress={() => playAll(e)} disabled={!e.duration} hitSlop={6} accessibilityRole="button"
                    accessibilityLabel={playing ? L('停', 'Stop') : L(`播放：${e.title}`, `Play: ${e.title}`)}
                    style={[styles.play, { backgroundColor: lk.soft, opacity: e.duration ? 1 : 0.4 }]}>
                    {playing ? <Square size={12} color={lk.fg} fill={lk.fg} /> : <Play size={13} color={lk.fg} fill={lk.fg} />}
                  </Pressable>
                  <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
                    <T v="headline" numberOfLines={2} style={{ fontSize: 15, fontWeight: '700' }}>{e.title}</T>
                    <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{[e.duration ? pod.clock(e.duration) : null, modeName(e.mode), dayLabel(e.createdAt)].filter(Boolean).join(' · ')}</T>
                  </View>
                  {e.chip && chip ? <View style={[styles.chip, { backgroundColor: chip[0] }]}><Text style={[type.caption, { color: chip[1] }]}>{e.chip.text}</Text></View> : null}
                </Pressable>
              );
            })}
          </View>
        ) : null}
      </ScrollView>
      <View style={styles.bottom} pointerEvents="box-none">
        <Pressable onPress={() => askTopic('rec')} accessibilityRole="button" style={({ pressed }) => [styles.startBtn, { backgroundColor: t.goldFill, opacity: pressed ? 0.85 : 1 }]}>
          <ml.Icon size={20} color={t.onGold} />
          <Text style={[type.headline, { color: t.onGold, fontWeight: '700' }]}>{L(`开始录 · ${modeName(mode)}`, `Record · ${modeName(mode)}`)}</Text>
        </Pressable>
      </View>
    </View>
  );
}

export function chipColors(t: Theme, tone: 'gold' | 'cyan' | 'red' | 'gray'): [string, string] {
  if (tone === 'gold') return [t.warnSoft, t.warn];
  if (tone === 'cyan') return [t.cyanSoft, t.cyan];
  if (tone === 'red') return [t.badSoft, t.bad];
  return [t.surface2, t.ink2];
}

function TopicSheet({ mode, then, close, onGo }: { mode: PodMode; then: 'prep' | 'rec'; close: () => void; onGo: (title: string) => void }) {
  const t = useTheme();
  const [title, setTitle] = useState('');
  const go = () => {
    const v = title.trim();
    if (!v) return;
    close();
    onGo(v);
  };
  return (
    <View style={{ gap: space.md }}>
      <TextInput value={title} onChangeText={setTitle} autoFocus placeholder={mode === 'feynman' ? L('讲哪个概念？比如：边际成本', 'Which concept? e.g. marginal cost') : L('这期聊什么？一句话就行', 'What is this episode about? One line')}
        placeholderTextColor={t.ink3} returnKeyType="go" onSubmitEditing={go} accessibilityLabel={L('这期聊什么', 'Topic')}
        style={[type.body, { backgroundColor: t.surface, color: t.ink, borderRadius: radius.md, paddingHorizontal: space.lg, paddingVertical: 12 }]} />
      <Btn label={then === 'prep' ? L('先聊几句', 'Talk first') : L('开录', 'Start recording')} onPress={go} />
      <T v="caption" color={t.ink3}>{then === 'prep'
        ? L('它先问你一句第一反应，按你的话排一张提纲卡；不进主对话。', "It asks for your first reaction and drafts an outline card from your words. Not in the main chat.")
        : mode === 'feynman' ? L('讲完它会对照学习台的课件；没有课件就按公认的讲法对照。', "Afterwards it checks against your study desk materials, or the standard view if there are none.")
          : L('原声留在你的服务器上，录完帮你理成笔记。', 'The audio stays on your server; afterwards it turns it into a note.')}</T>
    </View>
  );
}

const styles = StyleSheet.create({
  labelRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingTop: 8, paddingHorizontal: 4 },
  caps: { textTransform: 'uppercase' },
  card: { borderRadius: 16, borderWidth: StyleSheet.hairlineWidth, overflow: 'hidden' },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, padding: 12 },
  tile: { width: 34, height: 34, borderRadius: 10, alignItems: 'center', justifyContent: 'center' },
  go: { height: 32, minWidth: 48, paddingHorizontal: 12, borderRadius: 16, alignItems: 'center', justifyContent: 'center' },
  wait: { flexDirection: 'row', alignItems: 'center', gap: space.md, padding: space.lg },
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  mode: { flexBasis: '48%', flexGrow: 1, gap: 8, borderRadius: 14 },
  mi: { width: 30, height: 30, borderRadius: 9, alignItems: 'center', justifyContent: 'center' },
  prepRow: { flexDirection: 'row', alignItems: 'center', gap: 10, borderRadius: 14, borderWidth: StyleSheet.hairlineWidth, padding: 12 },
  play: { width: 34, height: 34, borderRadius: 17, alignItems: 'center', justifyContent: 'center' },
  chip: { borderRadius: 11, paddingHorizontal: 8, height: 22, justifyContent: 'center' },
  bottom: { position: 'absolute', left: space.lg, right: space.lg, bottom: space.md },
  startBtn: { height: 50, borderRadius: 14, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8 },
});
