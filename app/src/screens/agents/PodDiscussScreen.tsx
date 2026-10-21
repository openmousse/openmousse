// 播客的「先讨论」（主线第三步第二段，设计稿：录前讨论群）：路由 PodDiscuss（params id = 这一期）。Zen 播客一边点「先讨论」、今日话题里
// 要先聊的那几条进来（服务器开着群聊和赛博时；没开就照旧进 PodPrep 那个小对话）。
// 页上：这一期的题目、录法（四种，换了主持人跟着换：独白不设主持 / Iris 默认主持 / 阿哲外行 / 你的管家兼主持）、再拉几位赛博当嘉宾（最多 3 位）、
// 素材（MaterialsRow，和录前页同一个），「开始讨论」= 建这一期的录前讨论群（服务器 podprep.py），主持人先问一句第一反应，进群聊页。
// 多人对谈（第三段）：开着好友群时多一块「和谁一起录」：拉了好友 = 录前讨论是一个好友群，你的管家兼主持（先开口、约录制时间），
// 赛博不进（嘉宾那一块收起来），提纲要帮忙时去问 Iris；没拉好友（坐一起用一台手机）照旧由 Iris 在群聊里帮你理。
import React, { useEffect, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, View } from 'react-native';
import { useNavigation, useRoute } from '@react-navigation/native';
import { useButlerAvatar } from '../../api/butler';
import { cybersApi, type Cyber } from '../../api/cybers';
import { friendGroupsApi, useFriendGroupsOn, type FGCandidate } from '../../api/friendgroups';
import { groupsApi } from '../../api/groups';
import * as pod from '../../api/podcast';
import type { Episode, PodMode } from '../../api/podcast';
import { ButlerAvatar, CyberAvatar, PersonFace } from '../../components/agents/CyberAvatar';
import { openPrepGroup } from '../../components/agents/PrepBits';
import { Check } from '../../components/icons';
import { Btn, NavHeader, Screen, T, showError } from '../../components/ui';
import { L } from '../../i18n';
import { radius, space, useTheme } from '../../theme';
import { MaterialsRow } from '../../think/Materials';
import { modeLook, modeName } from '../../think/Podcast';

const GUESTS_MAX = 3;
/** 这种录法录前由哪位默认的赛博问你（服务器 podprep.HOSTS），和「先讨论」页上主持人那一行（podprep.rec_host_label） */
const HOST_OF: Record<PodMode, 'iris' | 'zhe'> = { solo: 'iris', host: 'iris', feynman: 'zhe', friends: 'iris' };
const hostLine = (m: PodMode) => ({ solo: L('独白不设主持 · Iris 帮你理提纲', 'No host for solo · Iris helps with the outline'), host: L('Iris · 默认主持', 'Iris · the default host'),
  feynman: L('阿哲 · 外行，听不懂就问', 'Zhe · the layman, asks when lost'), friends: L('你的管家 · 兼主持', 'Your butler · also hosts') }[m]);

export function PodDiscussScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { id } = useRoute<any>().params as { id: string };
  const [ep, setEp] = useState<Episode | null>(null);
  const [mode, setMode] = useState<PodMode>('host');
  const [cybers, setCybers] = useState<Cyber[]>([]);
  const [guests, setGuests] = useState<string[]>([]);
  const [count, setCount] = useState(0);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fgOn = useFriendGroupsOn();
  const butlerAvatar = useButlerAvatar();
  const [fc, setFc] = useState<FGCandidate[] | null>(null);
  const [friends, setFriends] = useState<string[]>([]);
  useEffect(() => {
    if (!fgOn) return;
    let live = true;
    friendGroupsApi.candidates().then((x) => { if (live) setFc(x.friends); }).catch(() => {});
    return () => { live = false; };
  }, [fgOn]);
  const withFriends = mode === 'friends' && friends.length > 0;
  const toggleFriend = (fid: string) => setFriends((cur) => (cur.includes(fid) ? cur.filter((x) => x !== fid) : cur.length >= 8 ? cur : [...cur, fid]));
  useEffect(() => {
    pod.get(id).then((e) => { setEp(e); setMode(e.mode); }).catch((e) => setError(e instanceof Error ? e.message : String(e)));
    pod.materials(id).then((items) => setCount(items.length)).catch(() => {});
    cybersApi.list().then(setCybers).catch(() => {});
  }, [id]);
  const host = cybers.find((c) => c.builtin === HOST_OF[mode]);
  const others = cybers.filter((c) => c.id !== host?.id);
  const toggle = (cid: string) => setGuests((cur) => (cur.includes(cid) ? cur.filter((x) => x !== cid) : cur.length >= GUESTS_MAX ? cur : [...cur, cid]));
  const start = async () => {
    if (starting) return;
    setStarting(true);
    try {
      const r = await groupsApi.discuss(id, withFriends ? { mode, friends } : { mode, guests: guests.filter((g) => g !== host?.id) });
      openPrepGroup(nav, r.group, true);
    } catch (e) {
      showError(L('无法开始讨论', "Couldn't start the discussion"), e);
      setStarting(false);
    }
  };
  if (!ep) {
    return (
      <Screen>
        <NavHeader title={L('先讨论', 'Talk first')} onBack={() => nav.goBack()} />
        <T v="callout" color={error ? t.bad : t.ink3} style={{ padding: space.xl, textAlign: 'center' }}>{error ? L(`无法加载：${error}`, `Couldn't load: ${error}`) : L('正在加载…', 'Loading…')}</T>
      </Screen>
    );
  }
  return (
    <Screen>
      <NavHeader title={L('先讨论', 'Talk first')} onBack={() => nav.goBack()} />
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl, gap: space.md }}>
        <T v="title">{ep.title}</T>
        <T v="caption" color={t.ink3} style={styles.label}>{L('录法', 'Mode')}</T>
        <View style={styles.modes} accessibilityRole="radiogroup" accessibilityLabel={L('录制方式', 'Recording mode')}>
          {(['solo', 'host', 'friends', 'feynman'] as const).map((m) => {
            const lk = modeLook(t, m);
            const on = m === mode;
            return (
              <Pressable key={m} onPress={() => setMode(m)} accessibilityRole="radio" accessibilityState={{ checked: on }} accessibilityLabel={`${modeName(m)}，${lk.sub}`}
                style={[styles.mode, { backgroundColor: t.surface, borderColor: on ? t.goldFill : t.line, borderWidth: on ? 1.5 : StyleSheet.hairlineWidth }]}>
                <lk.Icon size={15} color={lk.fg} />
                <T v="callout" numberOfLines={1} color={on ? t.ink : t.ink2} style={{ fontSize: 13, fontWeight: '600' }}>{modeName(m)}</T>
              </Pressable>
            );
          })}
        </View>
        <View style={[styles.hostRow, { backgroundColor: t.surface }]}>
          {withFriends ? <ButlerAvatar avatar={butlerAvatar} size={36} /> : host ? <CyberAvatar avatar={host.avatar} size={36} /> : null}
          <View style={{ flex: 1, gap: 2 }}>
            <T v="body" style={{ fontWeight: '600' }}>{withFriends || mode !== 'friends' ? hostLine(mode) : L('Iris · 帮你理提纲', 'Iris · helps with the outline')}</T>
            <T v="caption" color={t.ink3}>{withFriends ? L('录前讨论是一个好友群：你的管家先开口、约录制时间；提纲要帮忙时去问 Iris，Iris 只看得到你自己说的话。', 'The prep group is a friend group: your butler opens and books the recording time; Iris helps with the outline and only sees what you say.')
              : mode === 'friends' ? (fgOn ? L('没拉好友（坐一起用一台手机）：Iris 在群里帮你理提纲。拉了好友，就由你的管家兼主持。', 'No friends picked (one phone for everyone): Iris helps with the outline. Pick friends and your butler hosts.')
                : L('好友加入前，先由助手主持、Iris 帮你理提纲。', 'Until friends join, the assistant hosts and Iris helps with the outline.'))
                : L('录前在群里问你、帮你排提纲；聊的内容不进主对话。', 'Asks you in the group and drafts the outline; none of it goes to the main chat.')}</T>
          </View>
        </View>
        {!host && cybers.length ? <T v="caption" color={t.warn}>{L('默认的主持赛博删了：先在通讯录里建一位，或者用原来的录前聊天。', 'The default host Cyber was deleted: create one in Contacts first, or use the old prep chat.')}</T> : null}

        {mode === 'friends' && fgOn && fc ? (
          <>
            <T v="caption" color={t.ink3} style={styles.label}>{L('和谁一起录 · 好友', 'Recording with · friends')}</T>
            <View style={[styles.list, { backgroundColor: t.surface }]}>
              {fc.length ? fc.map((f, i) => {
                const on = friends.includes(f.id);
                return (
                  <Pressable key={f.id} onPress={() => toggleFriend(f.id)} disabled={!f.can} accessibilityRole="checkbox" accessibilityState={{ checked: on, disabled: !f.can }}
                    accessibilityLabel={f.name} accessibilityHint={f.why ?? undefined}
                    style={({ pressed }) => [styles.guest, i > 0 && { borderTopColor: t.line, borderTopWidth: StyleSheet.hairlineWidth }, { opacity: !f.can ? 0.45 : pressed ? 0.7 : 1 }]}>
                    <PersonFace name={f.name} size={32} />
                    <View style={{ flex: 1, minWidth: 0 }}>
                      <T v="body" numberOfLines={1}>{f.name}</T>
                      <T v="caption" color={t.ink3} numberOfLines={2}>{f.why ?? L('好友 · 消息端到端加密', 'Friend · end-to-end encrypted')}</T>
                    </View>
                    {on ? <View style={[styles.check, { backgroundColor: t.cyan }]}><Check size={14} color={t.bg} strokeWidth={3} /></View> : <View style={[styles.uncheck, { borderColor: t.ink3 }]} />}
                  </Pressable>
                );
              }) : <T v="callout" color={t.ink3} style={{ padding: space.md }}>{L('还没有好友。', 'No friends yet.')}</T>}
            </View>
          </>
        ) : null}

        {withFriends ? null : <>
        <T v="caption" color={t.ink3} style={styles.label}>{L(`嘉宾 · 可选，最多 ${GUESTS_MAX} 位赛博`, `Guests · optional, up to ${GUESTS_MAX} Cybers`)}</T>
        <View style={[styles.list, { backgroundColor: t.surface }]}>
          {others.length ? others.map((c, i) => {
            const on = guests.includes(c.id);
            return (
              <Pressable key={c.id} onPress={() => toggle(c.id)} accessibilityRole="checkbox" accessibilityState={{ checked: on }} accessibilityLabel={c.name}
                style={({ pressed }) => [styles.guest, i > 0 && { borderTopColor: t.line, borderTopWidth: StyleSheet.hairlineWidth }, { opacity: pressed ? 0.7 : 1 }]}>
                <CyberAvatar avatar={c.avatar} size={32} />
                <View style={{ flex: 1, minWidth: 0 }}>
                  <T v="body" numberOfLines={1}>{c.name}</T>
                  {c.tag ? <T v="caption" color={t.ink3} numberOfLines={1}>{c.tag}</T> : null}
                </View>
                {on ? <View style={[styles.check, { backgroundColor: t.cyan }]}><Check size={14} color={t.bg} strokeWidth={3} /></View> : <View style={[styles.uncheck, { borderColor: t.ink3 }]} />}
              </Pressable>
            );
          }) : <T v="callout" color={t.ink3} style={{ padding: space.md }}>{L('没有别的赛博。', 'No other Cybers.')}</T>}
        </View>
        </>}

        <T v="caption" color={t.ink3} style={styles.label}>{L('素材', 'Materials')}</T>
        <MaterialsRow id={id} count={count} onCount={setCount} />

        <Btn label={starting ? L('正在开始…', 'Starting…') : L('开始讨论', 'Start the discussion')} onPress={start} />
        <T v="caption" color={t.ink3}>{withFriends ? L('建一个这一期的好友群：你的管家先开口，好友在群里提的录制时间先出卡给你，你点了才进日程；提纲钉在群顶，聊完点「开始录制」。',
          "Starts a friend group for this episode: your butler opens, a recording time a friend proposes comes to you as a card before it goes into your calendar, and the outline is pinned at the top; tap Start recording when you're ready.")
          : L('建一个这一期的录前讨论群：主持人先问你的第一反应，聊着聊着排出提纲，提纲钉在群顶；聊完在群里点「开始录制」。',
            "Starts this episode's prep group: the host asks for your first reaction and drafts an outline pinned at the top; when you're ready, tap Start recording there.")}</T>
        <Pressable onPress={() => nav.replace('PodPrep', { id })} accessibilityRole="button" hitSlop={6} style={{ alignSelf: 'center', paddingVertical: space.sm }}>
          <T v="caption" color={t.ink2} style={{ fontWeight: '600' }}>{L('用原来的录前聊天', 'Use the old prep chat')}</T>
        </Pressable>
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  label: { fontSize: 13, fontWeight: '600', paddingLeft: 4, marginTop: space.sm },
  modes: { flexDirection: 'row', gap: 6 },
  mode: { flex: 1, height: 36, borderRadius: 10, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 5, paddingHorizontal: 4 },
  hostRow: { flexDirection: 'row', alignItems: 'center', gap: space.md, borderRadius: radius.lg - 2, padding: space.md },
  list: { borderRadius: radius.lg - 2, overflow: 'hidden' },
  guest: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingHorizontal: space.md, paddingVertical: 10 },
  check: { width: 22, height: 22, borderRadius: 11, alignItems: 'center', justifyContent: 'center' },
  uncheck: { width: 20, height: 20, borderRadius: 10, borderWidth: 1.5 },
});
