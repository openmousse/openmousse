// Zen 的「播客」一边（2026-10-13 重排；老服务器的三栏里也是它）：
//   制作中：还没存入的几期，横排卡片（进度四格 讨论 → 录制 → 整理 → 存入，等你处理的带琥珀点），按最近一次推进排列
//   今日话题：服务器从 Zen、库里「还没想清的」、学习台、截止、世界树里挑，写明从哪来；点一条直接开始
//   节目：存入库的几期（点播放听整期）
//   底部：四种录法 + 一句说明 +「先讨论」（录前先聊，出提纲卡）/「开始录制」。录、整理、存库在 PodcastScreens.tsx。
import React, { useCallback, useState } from 'react';
import { ActivityIndicator, Alert, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { useFocusEffect, useNavigation } from '@react-navigation/native';
import { CalendarDays, GraduationCap, Lightbulb, MessageCircle, Mic, Play, RefreshCw, Square, TextAlignStart, TreeDeciduous, Users } from '../components/icons';
import { rowTime } from '../components/ChatParts';
import { useSheet } from '../components/Sheet';
import { Btn, PullRefresh, T, showError } from '../components/ui';
import * as pod from '../api/podcast';
import type { PodBrief, PodHome, PodMode, PodSegment, PodSource, PodSuggestion } from '../api/podcast';
import { L } from '../i18n';
import { radius, space, type, useTheme, type Theme } from '../theme';
import { usePodPlayer } from './podAudio';
import { useThink } from './ThinkStore';
import { ShelfCard, ZenHead } from './parts';

const NO_SEGMENTS: PodSegment[] = [];  // 播放器的初始段落用同一个数组：每次渲染给新数组，播整期时它会把 play 给的段落冲掉，只放得完第一段

export const modeName = (m: PodMode) => ({ solo: L('独白', 'Solo'), host: L('主持人', 'With a host'), friends: L('多人对谈', 'With friends'), feynman: L('费曼', 'Feynman') }[m]);

export function modeLook(t: Theme, m: PodMode) {
  switch (m) {
    case 'solo': return { Icon: Mic, ...t.tints.purple, sub: L('只录制，不提问', 'Just you, no questions') };
    case 'host': return { Icon: MessageCircle, ...t.tints.gold, sub: L('每次停顿后提一个问题', 'One question after each pause') };
    case 'friends': return { Icon: Users, ...t.tints.pink, sub: L('一台手机共同录制', 'Record together on one phone') };
    default: return { Icon: GraduationCap, ...t.tints.cyan, sub: L('向外行讲解，对照课件核查', 'Explain to a layperson, checked against slides') };
  }
}

function sourceLook(t: Theme, s: PodSource) {
  switch (s.kind) {
    case 'study': return { Icon: GraduationCap, ...t.tints.cyan };
    case 'deadline': return { Icon: CalendarDays, ...t.tints.orange };
    case 'tree': return { Icon: TreeDeciduous, ...t.tints.green };
    case 'open': return { Icon: Lightbulb, ...t.tints.gold };
    case 'person': return { Icon: Users, ...t.tints.pink };
    default: return { Icon: Mic, ...t.tints.purple };
  }
}

/** 制作中的卡：进度四格走到第几格、一句状态、等不等你。服务器给的（supports('podcast.stage')）就用，老服务器按状态估一个。 */
export function stageOf(e: PodBrief): { stage: number; state: string; waiting: boolean } {
  if (e.stage != null && e.state != null) return { stage: e.stage, state: e.state, waiting: !!e.waiting };
  const st = e.status;
  const stage = st === 'saved' ? 4 : st === 'prep' ? 1 : st === 'recording' ? 2 : 3;
  const waiting = st === 'naming' || st === 'ready' || st === 'failed';
  return { stage, state: e.chip?.text ?? (st === 'prep' ? L(`${modeName(e.mode)} · 未开始`, `${modeName(e.mode)} · not started`) : ''), waiting };
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
  const { setPodWaiting } = useThink();
  const [data, setData] = useState<PodHome | null>(null);
  const [picking, setPicking] = useState(false);
  const [pickError, setPickError] = useState<string | null>(null);
  const [mode, setMode] = useState<PodMode>('host');
  const [busy, setBusy] = useState<string | null>(null);
  const player = usePodPlayer(NO_SEGMENTS);

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
      setPodWaiting(h.episodes.filter((e) => stageOf(e).waiting).length);
      if (h.suggestions === null) pick();
    } catch (e) { showError(L('无法加载播客', "Couldn't load the podcast"), e); }
  }, [pick, setPodWaiting]);
  useFocusEffect(useCallback(() => { load(); }, [load]));

  const start = async (title: string, m: PodMode, source: PodSource | undefined, then: 'prep' | 'rec') => {
    if (busy) return;
    setBusy(title);
    try {
      const e = await pod.create({ title, mode: m, source });
      nav.navigate(then === 'prep' ? 'PodPrep' : 'PodRec', { id: e.id });
    } catch (e) { showError(L('无法开始', "Couldn't start"), e); } finally { setBusy(null); }
  };
  const fromSuggestion = (s: PodSuggestion) => (s.mode === 'friends'  // 朋友画像来的「约小林聊…」：去约朋友那页（人和题目带过去，等他在了再开录）
    ? nav.navigate('PodFriends', { title: s.title, person: s.source.person, name: s.source.name })
    : start(s.title, s.mode, s.source, s.mode === 'host' ? 'prep' : 'rec'));
  const askTopic = (then: 'prep' | 'rec') => {
    if (mode === 'friends' && then === 'rec') { nav.navigate('PodFriends'); return; }
    sheet.open({ title: then === 'prep' ? L('录前讨论', 'Talk it through first') : L(`开始录制 · ${modeName(mode)}`, `Record · ${modeName(mode)}`),
      content: (close) => <TopicSheet mode={mode} then={then} close={close} onGo={(title) => start(title, mode, { kind: 'own' }, then)} /> });
  };
  const playAll = async (e: PodBrief) => {
    try {
      if (player.playing === e.id) { player.stop(); return; }
      const full = await pod.get(e.id);
      if (!full.segments.length) return;
      player.play(e.id, full.segments[0].idx, 0, null, true, full.segments);
    } catch (err) { showError(L('无法播放', "Couldn't play"), err); }
  };
  const del = (e: PodBrief) => {
    const go = () => pod.remove(e.id).then(load).catch((err) => showError(L('删除失败', "Couldn't delete"), err));
    const msg = L('原始录音和逐字稿将一并删除；已存入库中的笔记会保留。', 'Deletes the audio and transcript. A note you saved stays in the vault.');
    if (Platform.OS === 'web') { if (window.confirm(`${e.title}\n${msg}`)) go(); return; }
    Alert.alert(L(`删除「${e.title}」？`, `Delete “${e.title}”?`), msg, [{ text: L('取消', 'Cancel'), style: 'cancel' }, { text: L('删除', 'Delete'), style: 'destructive', onPress: go }]);
  };

  const sugg = data?.suggestions ?? [];
  const eps = data?.episodes ?? [];
  const byLatest = (a: PodBrief, b: PodBrief) => (a.updatedAt < b.updatedAt ? 1 : -1);
  const making = eps.filter((e) => e.status !== 'saved').sort(byLatest);
  const shows = eps.filter((e) => e.status === 'saved').sort(byLatest);
  const ml = modeLook(t, mode);
  return (
    <View style={{ flex: 1 }}>
      <ScrollView contentContainerStyle={{ paddingHorizontal: space.lg, paddingBottom: space.xl }} refreshControl={<PullRefresh onRefresh={load} />}>
        {making.length ? (
          <>
            <ZenHead title={L(`制作中 · ${making.length}`, `In progress · ${making.length}`)} right={L('按最近一次推进排列', 'Latest progress first')} />
            <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginHorizontal: -space.lg }} contentContainerStyle={{ paddingHorizontal: space.lg, gap: 10 }}>
              {making.map((e) => {
                const lk = modeLook(t, e.mode);
                const sg = stageOf(e);
                return (
                  <ShelfCard key={e.id} title={e.title} date={rowTime(e.updatedAt)} Icon={lk.Icon} tint={lk} stage={sg.stage} state={sg.state} waiting={sg.waiting}
                    steps={L('讨论、录制、整理、存入', 'prep, record, organize, save')} onPress={() => openEpisode(nav, e)} onLongPress={() => del(e)} />
                );
              })}
            </ScrollView>
          </>
        ) : null}

        <ZenHead title={L('今日话题', 'Talk about today')} right={(
          <Pressable onPress={() => pick(sugg.map((s) => s.title))} disabled={picking} hitSlop={8} accessibilityRole="button" style={{ flexDirection: 'row', alignItems: 'center', gap: 4, opacity: picking ? 0.5 : 1 }}>
            <RefreshCw size={13} color={t.gold} />
            <T v="callout" color={t.gold} style={{ fontWeight: '600', fontSize: 13 }}>{L('换一批', 'Others')}</T>
          </Pressable>
        )} />
        <View style={[styles.card, { backgroundColor: t.surface }]}>
          {picking && !sugg.length ? (
            <View style={styles.wait}><ActivityIndicator color={t.gold} /><T v="callout" color={t.ink2}>{L('正在从你的笔记、学习台和截止日期中挑选…', 'Picking from your notes, study desk and deadlines…')}</T></View>
          ) : pickError && !sugg.length ? (
            <View style={[styles.wait, { flexDirection: 'column', alignItems: 'stretch' }]}>
              <T v="callout" color={t.warn}>{L(`未能生成话题：${pickError}`, `Couldn't get topics: ${pickError}`)}</T>
              <Btn label={L('重试', 'Try again')} kind="quiet" onPress={() => pick()} />
            </View>
          ) : !sugg.length ? (
            <View style={styles.wait}><T v="callout" color={t.ink2}>{L('今日暂无推荐话题。可自拟题目，点按下方「开始录制」。', 'No topics today. Set your own with Record below.')}</T></View>
          ) : sugg.map((s, i) => {
            const lk = sourceLook(t, s.source);
            const act = s.mode === 'host' ? L('先讨论', 'Talk first') : s.mode === 'feynman' ? L('费曼', 'Feynman') : s.mode === 'friends' ? L('邀请', 'Invite') : L('录制', 'Record');
            return (
              <Pressable key={`${s.title}${i}`} onPress={() => fromSuggestion(s)} disabled={!!busy} accessibilityRole="button" accessibilityLabel={`${s.title}，${s.source.label ?? ''}，${act}`}
                style={({ pressed }) => [styles.row, i ? { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line } : null, { opacity: pressed ? 0.7 : 1 }]}>
                <View style={[styles.tile, { backgroundColor: lk.soft }]}><lk.Icon size={17} color={lk.fg} /></View>
                <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
                  <T v="headline" numberOfLines={2} style={{ fontSize: 15, fontWeight: '600' }}>{s.title}</T>
                  {s.source.label ? <T v="caption" color={t.ink3} numberOfLines={1} style={{ fontWeight: '400' }}>{s.source.label}</T> : null}
                </View>
                <View style={[styles.go, { backgroundColor: t.bg }]}>
                  {busy === s.title ? <ActivityIndicator size="small" color={t.ink2} /> : <Text style={[type.callout, { color: t.ink, fontWeight: '700', fontSize: 13 }]}>{act}</Text>}
                </View>
              </Pressable>
            );
          })}
        </View>

        {shows.length ? (
          <>
            <ZenHead title={L(`节目 · ${shows.length}`, `Episodes · ${shows.length}`)} right={L('已存入库', 'Saved to the vault')} />
            <View style={[styles.card, { backgroundColor: t.surface }]}>
              {shows.map((e, i) => {
                const lk = modeLook(t, e.mode);
                const playing = player.playing === e.id;
                const chip = e.chip ? chipColors(t, e.chip.tone) : null;
                return (
                  <Pressable key={e.id} onPress={() => openEpisode(nav, e)} onLongPress={() => del(e)} accessibilityRole="button"
                    style={({ pressed }) => [styles.row, i ? { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line } : null, { opacity: pressed ? 0.7 : 1 }]}>
                    <Pressable onPress={() => playAll(e)} disabled={!e.duration} hitSlop={6} accessibilityRole="button"
                      accessibilityLabel={playing ? L('停止', 'Stop') : L(`播放：${e.title}`, `Play: ${e.title}`)}
                      style={[styles.play, { backgroundColor: lk.soft, opacity: e.duration ? 1 : 0.4 }]}>
                      {playing ? <Square size={12} color={lk.fg} fill={lk.fg} /> : <Play size={13} color={lk.fg} fill={lk.fg} />}
                    </Pressable>
                    <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
                      <T v="headline" numberOfLines={2} style={{ fontSize: 15, fontWeight: '600' }}>{e.title}</T>
                      <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{[e.duration ? pod.clock(e.duration) : null, modeName(e.mode), dayLabel(e.createdAt)].filter(Boolean).join(' · ')}</T>
                    </View>
                    {e.chip && chip ? <View style={[styles.chip, { backgroundColor: chip[0] }]}><Text style={[type.caption, { color: chip[1] }]}>{e.chip.text}</Text></View> : null}
                  </Pressable>
                );
              })}
            </View>
          </>
        ) : null}
      </ScrollView>

      <View style={[styles.dock, { borderTopColor: t.line, backgroundColor: t.bg }]}>
        <View style={styles.modes} accessibilityRole="radiogroup" accessibilityLabel={L('录制方式', 'Recording mode')}>
          {(['solo', 'host', 'friends', 'feynman'] as const).map((m) => {
            const lk = modeLook(t, m);
            const on = m === mode;
            return (
              <Pressable key={m} onPress={() => setMode(m)} accessibilityRole="radio" accessibilityState={{ checked: on }} accessibilityLabel={`${modeName(m)}，${lk.sub}`}
                style={[styles.mode, { backgroundColor: t.surface, borderColor: on ? t.goldFill : t.line, borderWidth: on ? 1.5 : StyleSheet.hairlineWidth }]}>
                <lk.Icon size={15} color={lk.fg} />
                <Text numberOfLines={1} style={[type.callout, { fontSize: 13, fontWeight: '600', color: on ? t.ink : t.ink2 }]}>{modeName(m)}</Text>
              </Pressable>
            );
          })}
        </View>
        <T v="caption" color={t.ink3} style={{ textAlign: 'center', fontWeight: '400' }}>{L(`${modeName(mode)}：${ml.sub}`, `${modeName(mode)}: ${ml.sub}`)}</T>
        <View style={{ flexDirection: 'row', gap: 8 }}>
          {mode !== 'friends' ? (
            <Pressable onPress={() => askTopic('prep')} accessibilityRole="button" accessibilityHint={L('录前讨论，生成提纲卡', 'Talk it through first and get an outline card')}
              style={({ pressed }) => [styles.prep, { backgroundColor: t.surface, borderColor: t.line, opacity: pressed ? 0.7 : 1 }]}>
              <TextAlignStart size={16} color={t.ink2} />
              <Text style={[type.headline, { color: t.ink, fontSize: 15 }]}>{L('先讨论', 'Talk first')}</Text>
            </Pressable>
          ) : null}
          <Pressable onPress={() => askTopic('rec')} accessibilityRole="button" style={({ pressed }) => [styles.startBtn, { backgroundColor: t.goldFill, opacity: pressed ? 0.85 : 1 }]}>
            <ml.Icon size={18} color={t.onGold} />
            <Text numberOfLines={1} style={[type.headline, { color: t.onGold, fontWeight: '700' }]}>{L(`开始录制 · ${modeName(mode)}`, `Record · ${modeName(mode)}`)}</Text>
          </Pressable>
        </View>
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
      <TextInput value={title} onChangeText={setTitle} autoFocus placeholder={mode === 'feynman' ? L('讲解哪个概念？例如：边际成本', 'Which concept? e.g. marginal cost') : L('本期主题（一句话）', 'Episode topic, in one line')}
        placeholderTextColor={t.ink3} returnKeyType="go" onSubmitEditing={go} accessibilityLabel={L('本期主题', 'Topic')}
        style={[type.body, { backgroundColor: t.surface, color: t.ink, borderRadius: radius.md, paddingHorizontal: space.lg, paddingVertical: 12 }]} />
      <Btn label={then === 'prep' ? L('开始讨论', 'Talk first') : L('开始录制', 'Start recording')} onPress={go} />
      <T v="caption" color={t.ink3}>{then === 'prep'
        ? L('主持人先询问你的第一反应，再根据你的回答整理提纲卡；内容不进入主对话。', "The host asks for your first reaction and drafts an outline card from your answers. It stays out of the main chat.")
        : mode === 'feynman' ? L('讲解结束后将对照学习台的课件核查；无课件时按通行解释核查。', "Afterwards your explanation is checked against your study desk materials, or the standard view if there are none.")
          : L('原始录音保存在你的服务器上，录制结束后整理为笔记。', 'The audio stays on your server and is turned into a note afterwards.')}</T>
    </View>
  );
}

const styles = StyleSheet.create({
  card: { borderRadius: 14, overflow: 'hidden' },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, padding: 12 },
  tile: { width: 34, height: 34, borderRadius: 10, alignItems: 'center', justifyContent: 'center' },
  go: { height: 32, minWidth: 48, paddingHorizontal: 12, borderRadius: 16, alignItems: 'center', justifyContent: 'center' },
  wait: { flexDirection: 'row', alignItems: 'center', gap: space.md, padding: space.lg },
  play: { width: 34, height: 34, borderRadius: 17, alignItems: 'center', justifyContent: 'center' },
  chip: { borderRadius: 11, paddingHorizontal: 8, height: 22, justifyContent: 'center' },
  dock: { borderTopWidth: StyleSheet.hairlineWidth, paddingHorizontal: 12, paddingTop: 10, paddingBottom: 8, gap: 8 },
  modes: { flexDirection: 'row', gap: 6 },
  mode: { flex: 1, height: 36, borderRadius: 10, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 5, paddingHorizontal: 4 },
  prep: { height: 44, borderRadius: 14, borderWidth: StyleSheet.hairlineWidth, flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 14 },
  startBtn: { flex: 1, minWidth: 0, height: 44, borderRadius: 14, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, paddingHorizontal: 12 },
});
