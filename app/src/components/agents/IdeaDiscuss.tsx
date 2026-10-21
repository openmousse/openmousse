// Zen 里一条想法「拿去讨论」（主线第三步第二段，设计稿第 29–31 张；服务器 ../../../server/ideatalk.py，supports('think.discuss')）。
// 一张弹层，去哪五处：和一位赛博一对一（原文只当前情给它）/ 新建群聊（小结写回这条想法，群结束它就算想完了）/ 放进播客 / 交给功能 Agent（要动手的事）/
// 发给好友（先理成要点，你看过、改过才发，原话不发），最后一行是原来的「和助手讨论」（Zen 的主题）。一样东西只有一个家：已经在哪讨论了，顶上一行直接去那里。
// 弹层渲染在导航外面：跳页走 navigationRef。
import React, { useEffect, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, TextInput, View } from 'react-native';
import { groupsApi, type IdeaDiscussion, type IdeaOptions } from '../../api/groups';
import * as fr from '../../api/friends';
import * as pod from '../../api/podcast';
import { agentName } from '../../brand';
import { L } from '../../i18n';
import { navigationRef } from '../../navigation';
import { useStore } from '../../store';
import { radius, space, type, useTheme } from '../../theme';
import { openEpisode } from '../../think/Podcast';
import { MenuItem } from '../ChatParts';
import { Check, ChevronLeft, ChevronRight, LayoutGrid, Lightbulb, MessageCircle, Mic, Send, UserRound, UsersRound } from '../icons';
import { AgentAvatar } from '../MeetingBits';
import { Btn, T, showError } from '../ui';
import { CyberAvatar } from './CyberAvatar';

/** 去那一处：赛博 / 功能 Agent 的对话、群聊、那一期播客。 */
export function openDiscussion(d: Pick<IdeaDiscussion, 'kind' | 'id'>) {
  if (d.kind === 'cyber') navigationRef.navigate('CyberChat', { id: d.id, at: Date.now() });
  else if (d.kind === 'group') navigationRef.navigate('GroupChat', { id: d.id });
  else if (d.kind === 'agent') navigationRef.navigate('Group', { id: d.id, at: Date.now() });
  else pod.get(d.id).then((e) => openEpisode(navigationRef as unknown as { navigate: (n: string, p?: object) => void }, e)).catch((e) => showError(L('无法打开', "Couldn't open"), e));
}

type View_ = 'main' | 'cyber' | 'group' | 'podcast' | 'agent' | 'friend';

export function IdeaDiscussSheet({ idea, close, onTalk, onDone }: {
  idea: string; close: () => void;
  /** 原来的「和助手讨论」（Zen 的主题）：调用方去开 */
  onTalk: () => void;
  /** 去了哪一处之后（刷新 Zen 的列表） */
  onDone: () => void;
}) {
  const t = useTheme();
  const { reload } = useStore();
  const [o, setO] = useState<IdeaOptions | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<View_>('main');
  const [busy, setBusy] = useState(false);
  const [members, setMembers] = useState<string[]>([]);
  const [note, setNote] = useState('');
  const [agent, setAgent] = useState<string | null>(null);
  const [friend, setFriend] = useState<string | null>(null);
  const [points, setPoints] = useState<string | null>(null);
  useEffect(() => { groupsApi.ideaOptions(idea).then(setO).catch((e) => setError(e instanceof Error ? e.message : String(e))); }, [idea]);
  if (!o) return <T v="callout" color={error ? t.bad : t.ink3}>{error ? L(`无法加载：${error}`, `Couldn't load: ${error}`) : L('正在加载…', 'Loading…')}</T>;

  const run = async (what: () => Promise<void>, fail: string) => {
    if (busy) return;
    setBusy(true);
    try { await what(); onDone(); } catch (e) { showError(fail, e); } finally { setBusy(false); }
  };
  const back = (
    <Pressable onPress={() => setView('main')} accessibilityRole="button" hitSlop={6} style={styles.back}>
      <ChevronLeft size={16} color={t.ink2} />
      <T v="callout" color={t.ink2}>{L('返回', 'Back')}</T>
    </Pressable>
  );
  const check = (on: boolean) => (on ? <View style={[styles.check, { backgroundColor: t.cyan }]}><Check size={13} color={t.bg} strokeWidth={3} /></View> : <View style={[styles.uncheck, { borderColor: t.ink3 }]} />);

  if (view === 'cyber') {
    return (
      <View style={{ gap: space.sm }}>
        {back}
        {o.cybers.map((c) => (
          <Pressable key={c.id} disabled={busy} accessibilityRole="button" onPress={() => run(async () => {
            const r = await groupsApi.ideaToCyber(idea, c.id);
            close();
            navigationRef.navigate('CyberChat', { id: r.thread, at: Date.now() });
          }, L('无法开始', "Couldn't start"))} style={({ pressed }) => [styles.pick, { opacity: busy ? 0.5 : pressed ? 0.6 : 1 }]}>
            <CyberAvatar avatar={c.avatar} size={34} />
            <View style={{ flex: 1, minWidth: 0 }}>
              <T v="body" numberOfLines={1}>{c.name}</T>
              {c.tag ? <T v="caption" color={t.ink3} numberOfLines={1}>{c.tag}</T> : null}
            </View>
            <ChevronRight size={16} color={t.ink3} />
          </Pressable>
        ))}
        <T v="caption" color={t.ink3}>{L('它读到的只有这一条想法（不进对话里显示）；健康、饮食、睡眠照旧不给它。', "It reads only this one thought (not shown in the chat); health, diet and sleep stay out as always.")}</T>
      </View>
    );
  }
  if (view === 'group') {
    const all = [...o.cybers.map((c) => ({ id: c.id, name: c.name, sub: c.tag || L('赛博', 'Cyber'), face: <CyberAvatar avatar={c.avatar} size={32} />, health: false, cyber: true })),
      ...o.agents.map((a) => ({ id: a.id, name: a.name, sub: a.purpose || L('功能 Agent', 'Agent'), face: <AgentAvatar icon={a.icon} color={a.color} size={32} />, health: a.health, cyber: false }))];
    const withCyber = members.some((id) => all.find((x) => x.id === id)?.cyber);
    const withHealth = members.some((id) => all.find((x) => x.id === id)?.health);
    return (
      <View style={{ gap: space.sm }}>
        {back}
        <ScrollView style={{ maxHeight: 340 }}>
          {all.map((m) => {
            const on = members.includes(m.id);
            const off = !on && ((m.cyber && withHealth) || (m.health && withCyber));
            return (
              <Pressable key={m.id} disabled={off} accessibilityRole="checkbox" accessibilityState={{ checked: on, disabled: off }}
                onPress={() => setMembers((cur) => (cur.includes(m.id) ? cur.filter((x) => x !== m.id) : [...cur, m.id]))}
                style={({ pressed }) => [styles.pick, { opacity: off ? 0.45 : pressed ? 0.6 : 1 }]}>
                {m.face}
                <View style={{ flex: 1, minWidth: 0 }}>
                  <T v="body" numberOfLines={1}>{m.name}</T>
                  <T v="caption" color={t.ink3} numberOfLines={1}>{off ? L('健康类 Agent 不和赛博同群', 'Health Agents and Cybers stay apart') : m.sub}</T>
                </View>
                {check(on)}
              </Pressable>
            );
          })}
        </ScrollView>
        <Btn label={busy ? L('正在新建…', 'Starting…') : L(`新建群聊 · ${members.length} 位`, `New group chat · ${members.length}`)} onPress={() => {
          if (!members.length) return;
          run(async () => {
            const r = await groupsApi.ideaToGroup(idea, members);
            reload('chats').catch(() => {});
            close();
            navigationRef.navigate('GroupChat', { id: r.group.id });
          }, L('无法新建群聊', "Couldn't start the group chat"));
        }} />
        <T v="caption" color={t.ink3}>{L(`议题就是这条想法，成员先各说一轮；${agentName()} 主持，小结写回这条想法，群结束时它算想完了。`, `The thought is the topic and each member speaks once first; ${agentName()} hosts, summaries go back into the thought, and it's done when the group ends.`)}</T>
      </View>
    );
  }
  if (view === 'podcast') {
    const put = (episode?: string) => run(async () => {
      const r = await groupsApi.ideaToPodcast(idea, episode);
      close();
      const e = await pod.get(r.episode.id);
      openEpisode(navigationRef as unknown as { navigate: (n: string, p?: object) => void }, e);
    }, L('无法放进播客', "Couldn't add it"));
    return (
      <View style={{ gap: space.sm }}>
        {back}
        <MenuItem icon={Mic} title={L('新开一期', 'New episode')} desc={L('标题是这条想法的开头，主持人录前先问你', 'Titled after the thought; the host asks you first')} onPress={() => put()} />
        {o.episodes.map((e) => <MenuItem key={e.id} icon={Mic} title={e.title} desc={L('放进这一期的素材', "Into this episode's materials")} onPress={() => put(e.id)} />)}
      </View>
    );
  }
  if (view === 'agent') {
    return (
      <View style={{ gap: space.sm }}>
        {back}
        {o.agents.map((a) => (
          <Pressable key={a.id} accessibilityRole="radio" accessibilityState={{ checked: agent === a.id }} onPress={() => setAgent(a.id)}
            style={({ pressed }) => [styles.pick, { opacity: pressed ? 0.6 : 1 }]}>
            <AgentAvatar icon={a.icon} color={a.color} size={32} />
            <View style={{ flex: 1, minWidth: 0 }}>
              <T v="body" numberOfLines={1}>{a.name}</T>
              {a.purpose ? <T v="caption" color={t.ink3} numberOfLines={1}>{a.purpose}</T> : null}
            </View>
            {check(agent === a.id)}
          </Pressable>
        ))}
        <TextInput value={note} onChangeText={setNote} multiline placeholder={L('要它做什么（可选）', 'What should it do (optional)')} placeholderTextColor={t.ink3}
          accessibilityLabel={L('要它做什么', 'What should it do')} style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink }]} />
        <Btn label={busy ? L('正在交给它…', 'Handing over…') : L('交给它', 'Hand it over')} onPress={() => {
          if (!agent) return;
          run(async () => {
            const r = await groupsApi.ideaToAgent(idea, agent, note.trim());
            close();
            navigationRef.navigate('Group', { id: r.thread, at: Date.now() });
          }, L('交给它失败', "Couldn't hand it over"));
        }} />
        <T v="caption" color={t.ink3}>{L('它照平时的规矩做：要改你的东西，照样先出卡等你点头。', 'It works as usual: anything that changes your things still asks you first.')}</T>
      </View>
    );
  }
  if (view === 'friend') {
    return (
      <View style={{ gap: space.sm }}>
        {back}
        {o.friends.map((f) => (
          <Pressable key={f.id} accessibilityRole="radio" accessibilityState={{ checked: friend === f.id }} onPress={() => setFriend(f.id)}
            style={({ pressed }) => [styles.pick, { opacity: pressed ? 0.6 : 1 }]}>
            <View style={[styles.face, { backgroundColor: t.surface2 }]}><UserRound size={16} color={t.ink2} /></View>
            <T v="body" style={{ flex: 1 }} numberOfLines={1}>{f.name}</T>
            {check(friend === f.id)}
          </Pressable>
        ))}
        {points === null ? (
          <Btn label={busy ? L('正在理要点…', 'Making key points…') : L('先理成要点', 'Make key points first')} kind="quiet" onPress={() => run(async () => {
            const r = await groupsApi.ideaPoints(idea);
            setPoints(r.text);
          }, L('没理出要点', "Couldn't make the key points"))} />
        ) : (
          <>
            <TextInput value={points} onChangeText={setPoints} multiline accessibilityLabel={L('要发的要点', 'Key points to send')}
              style={[type.body, styles.input, { minHeight: 96, backgroundColor: t.surface, color: t.ink }]} />
            <Btn label={busy ? L('正在发送…', 'Sending…') : L('发送', 'Send')} icon={<Send size={16} color={t.onGold} />} onPress={() => {
              if (!friend || !points.trim()) return;
              run(async () => {
                await fr.sendText(friend, points.trim());
                close();
                navigationRef.navigate('FriendChat', { id: friend });
              }, L('发送失败', "Couldn't send"));
            }} />
          </>
        )}
        <T v="caption" color={t.ink3}>{L('只发要点，不发原话；你看过、改过才发出去。', 'Only key points go out, never the original; nothing is sent until you check it.')}</T>
      </View>
    );
  }
  const d = o.discussion;
  return (
    <View>
      {d ? (
        <Pressable onPress={() => { close(); openDiscussion(d); }} accessibilityRole="button" style={({ pressed }) => [styles.current, { backgroundColor: t.goldSoft, opacity: pressed ? 0.75 : 1 }]}>
          <T v="callout" color={t.gold} style={{ flex: 1, fontWeight: '600' }} numberOfLines={1}>
            {d.open === false ? L(`讨论过 · ${d.title || ''}`, `Discussed · ${d.title || ''}`) : L(`讨论中 · ${d.title || ''}`, `Being discussed · ${d.title || ''}`)}
          </T>
          <ChevronRight size={16} color={t.gold} />
        </Pressable>
      ) : null}
      {o.can.cyber && o.cybers.length ? <MenuItem icon={MessageCircle} title={L('和一位赛博聊', 'With one Cyber')} desc={L('一对一，原文只给它这一条', 'One to one; it reads only this thought')} chevron onPress={() => setView('cyber')} /> : null}
      {o.can.group ? <MenuItem icon={UsersRound} title={L('新建群聊', 'New group chat')} desc={L('先讨论一轮，小结写回这条想法', 'One round first; the summary comes back here')} chevron onPress={() => setView('group')} /> : null}
      <MenuItem icon={Mic} title={L('放进播客', 'Into a podcast')} desc={L('进一期的素材，或者新开一期', "Into an episode's materials, or a new one")} chevron onPress={() => setView('podcast')} />
      {o.can.agent ? <MenuItem icon={LayoutGrid} title={L('交给功能 Agent', 'To an Agent')} desc={L('要动手的事', 'For something to get done')} chevron onPress={() => setView('agent')} /> : null}
      {o.can.friend ? <MenuItem icon={Send} title={L('发给好友', 'To a friend')} desc={L('先理成要点，原话不发', 'Key points first; the original stays here')} chevron onPress={() => setView('friend')} /> : null}
      <MenuItem icon={Lightbulb} title={L(`和 ${agentName()} 讨论`, `With ${agentName()}`)} desc={L('Zen 里的主题，聊完写成笔记', 'A Zen topic, written up afterwards')} onPress={() => { close(); onTalk(); }} />
    </View>
  );
}

const styles = StyleSheet.create({
  back: { flexDirection: 'row', alignItems: 'center', gap: 2, alignSelf: 'flex-start', paddingVertical: 4 },
  pick: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: 8 },
  check: { width: 22, height: 22, borderRadius: 11, alignItems: 'center', justifyContent: 'center' },
  uncheck: { width: 20, height: 20, borderRadius: 10, borderWidth: 1.5 },
  input: { minHeight: 60, borderRadius: radius.md, paddingHorizontal: space.lg, paddingVertical: 10, textAlignVertical: 'top' },
  face: { width: 32, height: 32, borderRadius: 16, alignItems: 'center', justifyContent: 'center' },
  current: { flexDirection: 'row', alignItems: 'center', gap: 6, borderRadius: radius.md, paddingHorizontal: 12, paddingVertical: 10, marginBottom: space.sm },
});
