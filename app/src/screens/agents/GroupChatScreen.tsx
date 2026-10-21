// 群聊页（主线第三步第二段，设计稿第 44 张；服务器 ../../../server/groupchat.py，API ../../api/groups.ts）。路由 GroupChat（params id）。
// - 页头：返回 / 群头像（最多四位）/ 群名 +「你、Victor、苏格拉底 · Mousse 主持」/ ⓘ → 群资料（GroupInfo）。顶上钉着最近一次小结的一句话（点开群记忆）。
// - 成员的发言：头像（赛博 = 半身像 + 细金环，功能 Agent = 底色 + 图标）+「名字 · 赛博 · 早期投资人」+「回复 X」；@名字 用那位的颜色标出。
//   你的话在右边，附件照对话页画（点开 FilePreview）。主持人的小结卡（金色细边）下面三个动作：写回想法 / 发起会议 / 做成播客。
// - 成员想替你做的事（要你确认的）是「仅你可见」的卡，挂在那条发言下面，用 InboxCard 画；只在这里和收件箱，成员看不到。
// - 输入框是对话页的 Composer（只引用）：附件照 chatInput.ts 选，发之前先传成这个群的附件；上面一行「@ 提到」打开选人：
//   「@所有 Agent」（每位各说一次）和每位成员。有人在说、输入框空着时发送键是「停」。
// 群的状态记在本页，不进 store：进来读一次，没人在说时每 15 秒看一眼；有人在说就接 SSE（followGroup），和会议页一样按发言人分段出字。
// 播客的录前讨论群（来源是一期播客，podprep.py）：顶上钉着这一期（PrepPanel：提纲、排提纲 / 放进素材 / 开始录制、「放进」的卡）。
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, AppState, Image, Platform, Pressable, ScrollView, StyleSheet, View } from 'react-native';
import { useFocusEffect, useNavigation, useRoute } from '@react-navigation/native';
import { agentName } from '../../brand';
import { dataApi, inboxOf } from '../../api/data';
import { followGroup, groupsApi, splitAt, uploadGroupFiles, type Group, type GroupCard, type GroupLive, type GroupMember, type GroupMsg, type PrepEpisode, type PrepOffer } from '../../api/groups';
import * as pod from '../../api/podcast';
import type { Attachment, InboxItem, PendingFile } from '../../data/types';
import { GroupFaces, MemberFace, SummaryCard, groupSubtitle, roleLine } from '../../components/agents/GroupBits';
import { BookSheet, PrepPanel } from '../../components/agents/PrepBits';
import { AgentAvatar } from '../../components/MeetingBits';
import { Dots } from '../../components/ChatCards';
import { Composer, type ComposerHandle } from '../../components/ChatComposer';
import { ChatHeader, StateFace } from '../../components/ChatParts';
import { Action } from '../../components/ChatView';
import { MAX_FILES, pickDocuments, pickMedia } from '../../components/chatInput';
import { AtSign, Camera, ChevronRight, FileText, ImageIcon, Paperclip, Pin, UsersRound, X } from '../../components/icons';
import { InboxCard } from '../../components/InboxCard';
import { ChatScroll, dismissMode, KeyboardSticky, useBottomInset } from '../../components/keyboard';
import { Markdown } from '../../components/Markdown';
import { useSheet } from '../../components/Sheet';
import { NavHeader, PullRefresh, Screen, T, showError } from '../../components/ui';
import { loadDraft } from '../../drafts';
import { L } from '../../i18n';
import { useStore, useThreadOnScreen } from '../../store';
import { agentTint, radius, space, useTheme } from '../../theme';
import { openEpisode } from '../../think/Podcast';

const MAX_BYTES = 30 * 1024 * 1024;
type LiveView = { speaker: string | null; replyTo: string | null; text: string; phase: 'speaking' | 'summary' | 'done'; queue: string[] };
const liveOf = (l: GroupLive | null): LiveView | null => (l ? { speaker: l.speaker, replyTo: l.replyTo, text: l.text, phase: l.phase, queue: l.queue } : null);
const memberOf = (g: Group | null, id: string | null | undefined): GroupMember | undefined => g?.members.find((m) => m.id === id);
const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

export function GroupChatScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const sheet = useSheet();
  const { id } = useRoute<any>().params as { id: string; at?: number };
  const { reload, transcribe, inbox, receipts, groups } = useStore();
  const [group, setGroup] = useState<Group | null>(null);
  const [msgs, setMsgs] = useState<GroupMsg[]>([]);
  const [cards, setCards] = useState<GroupCard[]>([]);
  const [episode, setEpisode] = useState<PrepEpisode | null>(null);  // 录前讨论群：这一期
  const [offers, setOffers] = useState<PrepOffer[]>([]);
  const [prepBusy, setPrepBusy] = useState<string | null>(null);
  const [live, setLive] = useState<LiveView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [acting, setActing] = useState<string | null>(null);  // 「第几轮:哪个动作」正在处理
  const [pending, setPending] = useState<PendingFile[]>([]);
  const [sending, setSending] = useState(false);
  const follow = useRef<{ stop: () => void } | null>(null);
  const loadRef = useRef<() => Promise<void>>(async () => {});
  const composer = useRef<ComposerHandle>(null);
  const scroller = useRef<ScrollView>(null);
  const root = useRef<View>(null);
  const bottom = useBottomInset(root);
  useThreadOnScreen(id);  // 在屏幕上：不为它弹小窗

  const upsert = useCallback((m: GroupMsg) => setMsgs((cur) => {
    const i = cur.findIndex((x) => x.id === m.id);
    if (i < 0) return [...cur, m];
    const next = cur.slice();
    next[i] = m;
    return next;
  }), []);

  /** 接上正在说的：按发言人分段出字；说完了重读一次（小结、卡、状态以服务器为准）。 */
  const attach = useCallback(() => {
    if (follow.current) return;
    let got = false;
    const f = followGroup(id, (e) => {
      got = true;
      switch (e.event) {
        case 'state': setLive(liveOf(e.data.live)); break;
        case 'round': setGroup((g) => (g ? { ...g, round: e.data.round } : g)); break;
        case 'speaker': setLive((l) => ({ speaker: e.data.agent, replyTo: e.data.replyTo, text: '', phase: 'speaking', queue: (l?.queue ?? []).filter((a) => a !== e.data.agent) })); break;
        case 'delta': setLive((l) => (l && l.speaker === e.data.agent ? { ...l, text: l.text + e.data.text } : l)); break;
        case 'said': upsert(e.data); setLive((l) => (l && l.speaker === e.data.speaker ? { ...l, speaker: null, replyTo: null, text: '' } : l)); break;
        case 'system': case 'message': case 'summary': upsert(e.data); break;
        case 'phase': setLive((l) => ({ speaker: null, replyTo: null, text: '', phase: 'summary', queue: l?.queue ?? [] })); break;
        case 'done': setLive(null); break;
      }
    });
    follow.current = f;
    f.done.finally(() => {
      if (follow.current === f) follow.current = null;
      // 一个事件都没收到就断了（刚好说完 / 网络断了）：等两秒再读，免得来回空转
      if (got) loadRef.current().catch(() => {}); else setTimeout(() => { loadRef.current().catch(() => {}); }, 2000);
    });
  }, [id, upsert]);

  const load = useCallback(async () => {
    try {
      const r = await groupsApi.get(id);
      setGroup(r.group);
      setMsgs(r.messages);
      setCards(r.cards);
      setEpisode(r.episode ?? null);
      setOffers(r.offers ?? []);
      setError(null);
      if (r.group.live) { setLive((l) => l ?? liveOf(r.group.live)); attach(); } else if (!follow.current) setLive(null);
    } catch (e) {
      setError(errText(e));
    }
  }, [id, attach]);
  useEffect(() => { loadRef.current = load; }, [load]);

  useFocusEffect(useCallback(() => {
    loadRef.current().catch(() => {});
    const h = setInterval(() => { if (!follow.current && AppState.currentState === 'active') loadRef.current().catch(() => {}); }, 15000);
    return () => { clearInterval(h); follow.current?.stop(); follow.current = null; };
  }, []));

  // 看到最后一条了：标已读（store 里没有群的消息，自己报）
  const lastDb = [...msgs].reverse().find((m) => m.id.startsWith('db'))?.id;
  useEffect(() => {
    if (!lastDb || AppState.currentState !== 'active') return;
    dataApi.markRead(id, Number(lastDb.slice(2))).then(() => reload('unread', 'chats')).catch(() => {});
  }, [id, lastDb, reload]);

  const liveLen = live?.text.length ?? 0;
  useEffect(() => {
    const h = setTimeout(() => scroller.current?.scrollToEnd({ animated: true }), 60);
    return () => clearTimeout(h);
  }, [msgs.length, liveLen, live?.speaker, live?.phase, cards.length]);

  const fail = (e: unknown) => showError(L('操作失败', "Couldn't complete the action"), e);

  // —— 输入 ——
  const addFiles = (files: PendingFile[]) => {
    if (!files.length) return;
    const tooBig = files.filter((f) => f.size > MAX_BYTES);
    if (tooBig.length) Alert.alert(L('文件太大', 'File too large'), L(`${tooBig.map((f) => f.name).join('、')} 超过 ${MAX_BYTES / 1024 / 1024} MB，未添加。`, `Over ${MAX_BYTES / 1024 / 1024} MB, not added: ${tooBig.map((f) => f.name).join(', ')}`));
    const ok = files.filter((f) => f.size <= MAX_BYTES);
    setPending((cur) => {
      const next = [...cur, ...ok];
      if (next.length > MAX_FILES) Alert.alert(L('附件过多', 'Too many files'), L(`每条消息最多 ${MAX_FILES} 个附件，超出部分未添加。`, `Up to ${MAX_FILES} attachments per message. The extra ones weren't added.`));
      return next.slice(0, MAX_FILES);
    });
  };
  const openAttach = () => {
    if (Platform.OS === 'web') { pickDocuments().then(addFiles).catch(fail); return; }
    sheet.open({
      title: L('添加附件', 'Add attachment'),
      content: (close) => (
        <View style={{ gap: space.sm }}>
          <Action icon={Camera} label={L('拍照或录像', 'Take photo or video')} onPress={() => { close(); pickMedia(true).then(addFiles).catch(fail); }} />
          <Action icon={ImageIcon} label={L('相册', 'Photo library')} note={L('照片和视频，成员看得到图片', 'Photos and videos. Members see the images.')} onPress={() => { close(); pickMedia(false).then(addFiles).catch(fail); }} />
          <Action icon={FileText} label={L('文件', 'Files')} note={L(`文档抽出的文字成员都读得到。单个文件不超过 ${MAX_BYTES / 1024 / 1024} MB，每条最多 ${MAX_FILES} 个。`, `Members read the text taken from documents. Up to ${MAX_BYTES / 1024 / 1024} MB each, ${MAX_FILES} per message.`)}
            onPress={() => { close(); pickDocuments().then(addFiles).catch(fail); }} />
        </View>
      ),
    });
  };
  const submit = async (text: string): Promise<boolean> => {
    if (!text && !pending.length) return false;
    if (sending) return false;
    setSending(true);
    try {
      const ids = await uploadGroupFiles(id, pending);
      const r = await groupsApi.send(id, text, ids);
      upsert(r.message);
      setGroup(r.group);
      setPending([]);
      if (r.group.live) attach();
      else if (r.how === 'arrange') setTimeout(() => { loadRef.current().catch(() => {}); }, 1500);  // 主持人想人选要几秒
      return true;
    } catch (e) {
      showError(L('发送失败', "Couldn't send"), e);
      return false;
    } finally {
      setSending(false);
    }
  };
  const stop = () => { groupsApi.stop(id).catch(fail); };
  /** 「@ 提到」：@所有 Agent（每位各说一次）或一位成员，补进输入框（草稿在 Composer 里，存在本机，从 drafts 读回来接着写）。 */
  const mention = () => {
    const members = (group?.members ?? []).filter((m) => !m.gone);
    const put = (name: string) => {
      const d = loadDraft(id);
      composer.current?.set(`${d}${d && !/\s$/.test(d) ? ' ' : ''}@${name} `);
      setTimeout(() => composer.current?.focus(), 250);
    };
    sheet.open({
      title: L('提到', 'Mention'),
      content: (close) => (
        <View style={{ gap: 2 }}>
          <Pressable accessibilityRole="button" onPress={() => { close(); put(L('所有 Agent', 'All Agents')); }} style={({ pressed }) => [styles.pickRow, { opacity: pressed ? 0.6 : 1 }]}>
            <View style={[styles.allFace, { backgroundColor: t.goldSoft }]}><UsersRound size={17} color={t.gold} /></View>
            <View style={{ flex: 1, gap: 1 }}>
              <T v="body" style={{ fontWeight: '600' }}>{L('@所有 Agent', '@All Agents')}</T>
              <T v="caption" color={t.ink3}>{L(`每位成员各说一次${group?.host ? `，${group.host.name} 写小结` : ''}`, `Each member speaks once${group?.host ? `; ${group.host.name} writes the summary` : ''}`)}</T>
            </View>
          </Pressable>
          {members.map((m) => (
            <Pressable key={m.id} accessibilityRole="button" onPress={() => { close(); put(m.name); }} style={({ pressed }) => [styles.pickRow, { opacity: pressed ? 0.6 : 1 }]}>
              <MemberFace m={m} size={32} />
              <View style={{ flex: 1, gap: 1 }}>
                <T v="body">{m.name}</T>
                <T v="caption" color={t.ink3} numberOfLines={1}>{roleLine(m)}</T>
              </View>
            </Pressable>
          ))}
        </View>
      ),
    });
  };

  // —— 录前讨论群：提纲、放进素材、开始录制 ——
  const draftOutline = async () => {
    if (!episode) return;
    setPrepBusy('outline');
    try { await groupsApi.outline(episode.id); await load(); } catch (e) { showError(L('提纲没排出来', "Couldn't draft the outline"), e); } finally { setPrepBusy(null); }
  };
  const offerFrom = () => {
    if (!episode) return;
    sheet.open({
      title: L('放进素材', 'Add material'),
      sub: L('拿一个功能 Agent 最近的一份报告', "An Agent's latest report"),
      content: (close) => (
        <View style={{ gap: 2 }}>
          <T v="caption" color={t.ink3} style={{ paddingBottom: 6 }}>{L('出一张只有你看得到的卡，点「放进」才算。健康、饮食、睡眠的只给主持、只用一次，不进素材。', "You get a card only you can see; it counts once you tap Put in. Health, diet and sleep reports go to the host only, once, and aren't kept.")}</T>
          {groups.length ? groups.map((a) => (
            <Pressable key={a.id} accessibilityRole="button" onPress={() => {
              close();
              setPrepBusy('offer');
              groupsApi.offer(episode.id, a.id).then((r) => setOffers((cur) => [...cur, r.offer])).catch((e) => showError(L('无法放进素材', "Couldn't add it"), e)).finally(() => setPrepBusy(null));
            }} style={({ pressed }) => [styles.pickRow, { opacity: pressed ? 0.6 : 1 }]}>
              <AgentAvatar icon={a.icon} color={a.color} size={32} />
              <T v="body" style={{ flex: 1 }} numberOfLines={1}>{a.name}</T>
            </Pressable>
          )) : <T v="callout" color={t.ink3}>{L('还没有功能 Agent。', 'No Agents yet.')}</T>}
        </View>
      ),
    });
  };
  const book = () => episode && sheet.open({ title: L('约录制时间', 'Book the recording'), content: (close) => (
    <BookSheet booked={episode.booked?.at ?? null} close={close} onBook={(at) => groupsApi.book(episode.id, at).then(() => load())} />
  ) });
  const decideOffer = (o: PrepOffer, action: 'put' | 'skip') => {
    setPrepBusy(o.id);
    groupsApi.decideOffer(o.id, action).then((r) => setOffers((cur) => cur.map((x) => (x.id === o.id ? r.offer : x))))
      .catch((e) => showError(L('操作失败', "Couldn't complete the action"), e)).finally(() => setPrepBusy(null));
  };

  // —— 小结下的三个动作 ——
  const act = async (round: number, key: 'writeback' | 'meeting' | 'podcast') => {
    const m = msgs.find((x) => x.kind === 'summary' && x.round === round);
    const done = m?.actions?.[key];
    if (key === 'meeting' && m?.actions?.meeting) { nav.navigate('Meeting', { id: m.actions.meeting.id, at: Date.now() }); return; }
    if (key === 'podcast' && m?.actions?.podcast) { pod.get(m.actions.podcast.id).then((e) => openEpisode(nav, e)).catch(fail); return; }
    if (key === 'writeback' && done && !('error' in done && done.error)) return;
    setActing(`${round}:${key}`);
    try {
      if (key === 'writeback') {
        const r = await groupsApi.writeback(id, round);
        upsert(r.message);
        await load();
      } else if (key === 'meeting') {
        const r = await groupsApi.meeting(id, round);
        if (r.message) upsert(r.message);
        reload('chats').catch(() => {});
        nav.navigate('Meeting', { id: r.meeting.id, at: Date.now() });
      } else {
        const r = await groupsApi.podcast(id, round);
        if (r.message) upsert(r.message);
        nav.navigate('PodPrep', { id: r.episode.id });
      }
    } catch (e) {
      showError(key === 'writeback' ? L('写回失败', "Couldn't write it back") : key === 'meeting' ? L('无法发起会议', "Couldn't start the meeting") : L('无法新建播客', "Couldn't start the episode"), e);
    } finally {
      setActing(null);
    }
  };

  // 列表只在消息、群、卡变了时重排：回调走 ref（流式出字时 live 每秒变几十次，不能带着整个列表重画）
  const handlers = useRef({ act, submit, stop, openAttach, transcribe });
  useEffect(() => { handlers.current = { act, submit, stop, openAttach, transcribe }; });
  const onAct = useCallback((round: number, key: 'writeback' | 'meeting' | 'podcast') => { handlers.current.act(round, key); }, []);
  const onMemory = useCallback(() => nav.navigate('GroupMemory', { id }), [nav, id]);
  const onSubmit = useCallback((text: string) => handlers.current.submit(text), []);
  const onStop = useCallback(() => handlers.current.stop(), []);
  const onAttach = useCallback(() => handlers.current.openAttach(), []);
  const onTranscribe = useCallback((f: PendingFile) => handlers.current.transcribe(f), []);

  // 仅你可见的卡：store 里有它（刚点过、收件箱读过）就用 store 的，点完立刻变回执；没有就用群接口给的
  const known = useMemo(() => {
    const m = new Map<string, InboxItem>();
    for (const r of receipts) m.set(r.item.id, r.item);
    for (const i of inbox) m.set(i.id, i);
    return m;
  }, [inbox, receipts]);
  const cardsBy = useMemo(() => {
    const out = new Map<string, InboxItem[]>();
    for (const c of cards) {
      if (!c.groupMessage) continue;
      const it = known.get(c.id) ?? inboxOf(c as unknown as InboxItem);
      out.set(c.groupMessage, [...(out.get(c.groupMessage) ?? []), it]);
    }
    return out;
  }, [cards, known]);

  const list = useMemo(() => (group ? msgs.map((m) => (
    <View key={m.id} style={{ gap: space.sm }}>
      <GroupRow m={m} group={group} acting={acting} onAct={onAct} onMemory={onMemory} />
      <OwnerCards items={cardsBy.get(m.id)} />
    </View>
  )) : null), [msgs, group, acting, onAct, onMemory, cardsBy]);

  if (!group) {
    return (
      <Screen>
        <NavHeader title={L('群聊', 'Group chat')} onBack={() => nav.goBack()} />
        <View style={{ padding: space.xl, alignItems: 'center' }}>
          <T v="callout" color={error ? t.bad : t.ink3}>{error ? L(`无法加载：${error}`, `Couldn't load: ${error}`) : L('正在加载…', 'Loading…')}</T>
        </View>
      </Screen>
    );
  }
  const running = !!live || !!group.live;
  const ended = group.status === 'ended';
  const speaker = memberOf(group, live?.speaker);
  const waiting = cards.some((c) => (known.get(c.id)?.status ?? c.status) === 'pending');
  return (
    <Screen>
      <ChatHeader title={group.title} sub={groupSubtitle(group.members, group.host)}
        face={<StateFace size={36} state={running ? 'working' : waiting ? 'waiting' : null}><GroupFaces members={group.members} size={36} /></StateFace>}
        onBack={() => nav.goBack()} onInfo={() => nav.navigate('GroupInfo', { id })} infoLabel={L('群资料', 'Group info')} />
      {episode ? (
        <PrepPanel episode={episode} offers={offers} busy={prepBusy} onOutline={draftOutline} onOffer={offerFrom} onBook={book}
          onRecord={() => nav.navigate('PodRec', { id: episode.id })} onDecide={decideOffer} />
      ) : group.pinned ? (
        <Pressable onPress={onMemory} accessibilityRole="button" accessibilityHint={L('打开群记忆', "Opens the group's memory")}
          style={({ pressed }) => [styles.pinned, { backgroundColor: t.surface, borderBottomColor: t.line, opacity: pressed ? 0.7 : 1 }]}>
          <Pin size={13} color={t.gold} />
          <T v="caption" color={t.ink2} numberOfLines={1} style={{ flex: 1 }}>{L(`第 ${group.pinned.round} 轮小结：${group.pinned.line}`, `Round ${group.pinned.round}: ${group.pinned.line}`)}</T>
          <ChevronRight size={14} color={t.ink3} />
        </Pressable>
      ) : null}
      <View ref={root} onLayout={bottom.onLayout} style={{ flex: 1, paddingBottom: bottom.home }}>
        <ChatScroll ref={scroller} offset={bottom.offset} style={{ flex: 1 }} contentContainerStyle={{ padding: space.md, gap: space.md }}
          keyboardShouldPersistTaps="never" keyboardDismissMode={dismissMode} refreshControl={<PullRefresh onRefresh={load} />}>
          {group.topic ? (
            <View style={[styles.topic, { backgroundColor: t.surface }]}>
              <T v="callout" color={t.ink2} style={{ textAlign: 'center', fontSize: 13, lineHeight: 18 }}>{L(`议题：${group.topic}`, `Topic: ${group.topic}`)}</T>
              {group.source?.title ? <T v="caption" color={t.ink3} style={{ fontSize: 11 }}>{group.source.kind === 'podcast' ? L(`来自播客「${group.source.title}」`, `From the episode "${group.source.title}"`) : L(`来自想法「${group.source.title}」`, `From the thought "${group.source.title}"`)}</T> : null}
            </View>
          ) : null}
          {list}
          {live?.phase === 'summary' ? (
            <View style={styles.row}>
              <MemberFace m={group.host?.kind === 'cyber' ? { kind: 'cyber', avatar: group.host.avatar ?? null, icon: null, color: null } : null} main={group.host?.kind !== 'cyber'} size={30} />
              <View style={{ gap: 3, flexShrink: 1 }}>
                <T v="caption" color={t.gold} style={{ fontWeight: '600' }}>{L(`${group.host?.name ?? agentName()} · 主持`, `${group.host?.name ?? agentName()} · host`)}</T>
                <View style={[styles.bubble, { backgroundColor: t.surface, flexDirection: 'row', alignItems: 'center', gap: 8 }]}>
                  <T v="callout" color={t.ink2}>{L('正在写小结', 'Writing the summary')}</T>
                  <Dots color={t.gold} />
                </View>
              </View>
            </View>
          ) : live?.speaker ? (
            <MemberLine member={speaker} name={speaker?.name ?? live.speaker} replyTo={memberOf(group, live.replyTo)?.name} members={group.members} text={live.text} streaming />
          ) : null}
          {live && live.queue.length ? (
            <T v="caption" color={t.ink3} style={{ textAlign: 'center' }}>
              {L(`接下来：${live.queue.map((a) => memberOf(group, a)?.name ?? a).join('、')}`, `Next: ${live.queue.map((a) => memberOf(group, a)?.name ?? a).join(', ')}`)}
            </T>
          ) : null}
        </ChatScroll>
        <KeyboardSticky offset={bottom.offset} style={[styles.composerWrap, { borderTopColor: t.line, backgroundColor: t.bg }]}>
          {ended ? (
            <View style={styles.endedBar}>
              <T v="caption" color={t.ink3}>{L('群聊已结束，记录和群记忆保留在这里', 'This group chat has ended; the record and its memory stay here')}</T>
            </View>
          ) : (
            <>
              <View style={styles.tools}>
                <Pressable onPress={mention} accessibilityRole="button" accessibilityLabel={L('提到成员', 'Mention a member')}
                  style={({ pressed }) => [styles.toolBtn, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
                  <AtSign size={14} color={t.ink2} />
                  <T v="caption" color={t.ink2} style={{ fontWeight: '600' }}>{L('提到', 'Mention')}</T>
                </Pressable>
                <T v="caption" color={t.ink3} numberOfLines={1} style={{ flex: 1 }}>
                  {sending ? L('正在发送…', 'Sending…') : group.mode === 'host' && group.host ? L(`没 @ 时由 ${group.host.name} 安排谁接着说`, `Without an @, ${group.host.name} picks who speaks next`) : L('@ 谁谁说；@所有 Agent 每位各说一次', 'Whoever you @ speaks; @All Agents for one round')}
                </T>
              </View>
              {pending.length ? (
                <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: space.sm, paddingHorizontal: space.md, paddingTop: space.sm }} keyboardShouldPersistTaps="handled">
                  {pending.map((f, i) => (
                    <View key={`${f.uri}-${i}`} style={[styles.fileChip, { backgroundColor: t.surface, borderColor: t.line }]}>
                      {f.mime.startsWith('image/') ? <Image source={{ uri: f.uri }} style={{ width: 28, height: 28, borderRadius: 6 }} accessibilityLabel={f.name} /> : <Paperclip size={14} color={t.cyan} />}
                      <T v="caption" numberOfLines={1} style={{ maxWidth: 160 }}>{f.name}</T>
                      <Pressable onPress={() => setPending((cur) => cur.filter((_, k) => k !== i))} hitSlop={8} accessibilityRole="button" accessibilityLabel={L(`移除 ${f.name}`, `Remove ${f.name}`)}>
                        <X size={14} color={t.ink3} />
                      </Pressable>
                    </View>
                  ))}
                </ScrollView>
              ) : null}
              <Composer handle={composer} threadId={id} placeholder={L('回复；@ 一位成员让它说', 'Reply; @ a member to have it speak')} quote={null}
                busy={running} hasFiles={pending.length > 0} onSubmit={onSubmit} onStop={onStop} onAttach={onAttach} transcribe={onTranscribe} />
            </>
          )}
        </KeyboardSticky>
      </View>
    </Screen>
  );
}

/** 一条成员的发言：头像 + 名字（功能 Agent 用它的颜色）· 身份 +「回复 X」；气泡里 @名字 用那位的颜色。 */
const MemberLine = React.memo(function MemberLine({ member, name, replyTo, members, text, streaming, at, failed }: {
  member?: GroupMember; name: string; replyTo?: string; members: GroupMember[]; text: string; streaming?: boolean;
  at?: GroupMsg['at']; failed?: boolean;
}) {
  const t = useTheme();
  const color = member?.kind === 'cyber' ? t.ink : agentTint(t, member?.color).fg;
  const role = roleLine(member);
  return (
    <View style={styles.row}>
      <MemberFace m={member} size={30} />
      <View style={{ flex: 1, minWidth: 0, gap: 3, alignItems: 'flex-start' }}>
        <T v="caption" color={color} style={{ fontWeight: '600' }} numberOfLines={1}>
          {name}
          {role ? <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{` · ${role}`}</T> : null}
          {replyTo ? <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L(` · 回复 ${replyTo}`, ` · replying to ${replyTo}`)}</T> : null}
        </T>
        <View style={[styles.bubble, { backgroundColor: t.surface, maxWidth: '100%' }]}>
          {text ? <SpeechText text={text} members={members} color={failed ? t.ink3 : undefined} /> : null}
          {streaming ? <View style={{ paddingTop: text ? 6 : 2 }}><Dots color={member?.kind === 'cyber' ? t.gold : color} /></View> : null}
        </View>
        {at?.deferred ? <T v="caption" color={t.ink3}>{L(`${at.name} 已经说过，问题留到下一轮`, `${at.name} already spoke; the question carries over`)}</T> : null}
      </View>
    </View>
  );
});

/** 发言正文：带 @名字 的那几行自己画（@ 的部分用那位的颜色，「@所有 Agent」金色），其余照常 Markdown。 */
function SpeechText({ text, members, color }: { text: string; members: GroupMember[]; color?: string }) {
  const t = useTheme();
  const out: ({ md: string } | { line: string })[] = [];
  let buf: string[] = [];
  for (const ln of text.split('\n')) {
    if (splitAt(ln, members).some((p) => p.who)) {
      if (buf.length) out.push({ md: buf.join('\n') });
      buf = [];
      out.push({ line: ln });
    } else buf.push(ln);
  }
  if (buf.length) out.push({ md: buf.join('\n') });
  const tint = (who: string) => {
    if (who === '*') return t.gold;
    const m = members.find((x) => x.id === who);
    return m?.kind === 'cyber' ? t.ink : agentTint(t, m?.color).fg;
  };
  return (
    <View style={{ gap: 4, marginBottom: out.length && 'md' in out[out.length - 1] ? -4 : 0 }}>
      {out.map((b, i) => ('md' in b ? (b.md.trim() ? <Markdown key={i} text={b.md} color={color} compact /> : null) : (
        <T key={i} v="body" color={color}>
          {splitAt(b.line, members).map((p, j) => (p.who ? <T key={j} v="body" style={{ fontWeight: '600' }} color={tint(p.who)}>{p.text}</T> : p.text))}
        </T>
      )))}
    </View>
  );
}

/** 你发的附件：图片给缩略图，其它给文件条；点开在 app 里预览，这一条的几个附件能左右滑。 */
function Files({ items }: { items: Attachment[] }) {
  const t = useTheme();
  const nav = useNavigation<any>();
  const images = items.filter((a) => a.kind === 'image');
  const others = items.filter((a) => a.kind !== 'image');
  const all = [...images, ...others];
  const open = (a: Attachment) => nav.navigate('FilePreview', { items: all, index: Math.max(0, all.indexOf(a)) });
  return (
    <View style={{ gap: 6, alignItems: 'flex-end', maxWidth: '82%' }}>
      {images.length ? (
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6, justifyContent: 'flex-end' }}>
          {images.map((a) => (
            <Pressable key={a.id} onPress={() => open(a)} accessibilityRole="imagebutton" accessibilityLabel={a.name}>
              <Image source={{ uri: `${a.url}${a.url.includes('?') ? '&' : '?'}thumb=1` }} resizeMode="cover"
                style={{ width: images.length === 1 ? 200 : 96, height: images.length === 1 ? 200 : 96, borderRadius: radius.md, backgroundColor: t.surface2 }} />
            </Pressable>
          ))}
        </View>
      ) : null}
      {others.map((a) => (
        <Pressable key={a.id} onPress={() => open(a)} accessibilityRole="button" accessibilityLabel={L(`附件 ${a.name}`, `Attachment ${a.name}`)}
          style={[styles.fileChip, { backgroundColor: t.surface, borderColor: t.line }]}>
          <Paperclip size={14} color={t.cyan} />
          <T v="caption" numberOfLines={1} style={{ maxWidth: 200 }}>{a.name}</T>
        </Pressable>
      ))}
    </View>
  );
}

/** 群里的一条：系统行、你说的、成员的发言、主持人的小结。 */
const GroupRow = React.memo(function GroupRow({ m, group, acting, onAct, onMemory }: {
  m: GroupMsg; group: Group; acting: string | null; onAct: (round: number, key: 'writeback' | 'meeting' | 'podcast') => void; onMemory: () => void;
}) {
  const t = useTheme();
  if (m.kind === 'system') {
    return <T v="caption" color={t.ink3} style={{ textAlign: 'center', fontWeight: '400', paddingHorizontal: space.lg }}>{`${m.text} · ${m.time}`}</T>;
  }
  if (m.kind === 'user') {
    const files = m.attachments ?? [];
    const onlyFiles = files.length > 0 && (m.text === '（见附件）' || m.text === '(see attachments)');
    return (
      <View style={{ alignItems: 'flex-end', gap: 6 }}>
        {files.length ? <Files items={files} /> : null}
        {onlyFiles ? null : (
          <View style={[styles.userBubble, { backgroundColor: t.surface2 }]}>
            <SpeechText text={m.text} members={group.members} />
          </View>
        )}
        <T v="caption" color={t.ink3} style={{ fontSize: 11 }}>{m.time}</T>
      </View>
    );
  }
  if (m.kind === 'summary' && m.summary) {
    const round = m.round ?? 1;
    const busy = acting?.startsWith(`${round}:`) ? acting.slice(String(round).length + 1) : acting ? 'other' : null;
    const host = m.host?.kind === 'cyber' ? group.host : null;
    return (
      <View style={styles.row}>
        <MemberFace m={host ? { kind: 'cyber', avatar: host.avatar ?? null, icon: null, color: null } : null} main={!host} size={30} />
        <SummaryCard m={m} busy={busy} sourceKind={group.source?.kind ?? null} onMemory={onMemory}
          onWriteback={() => onAct(round, 'writeback')} onMeeting={() => onAct(round, 'meeting')} onPodcast={() => onAct(round, 'podcast')} />
      </View>
    );
  }
  const member = m.who ?? memberOf(group, m.speaker);
  return <MemberLine member={member} name={member?.name ?? m.name} replyTo={m.replyTo?.name} members={group.members} text={m.text} at={m.at} failed={m.status === 'error'} />;
});

/** 挂在发言下面、只有你看得到的卡（成员要替你做的事）：一行「仅你可见 · N 项待确认」+ 收件箱的卡。 */
const OwnerCards = React.memo(function OwnerCards({ items }: { items?: InboxItem[] }) {
  const t = useTheme();
  if (!items?.length) return null;
  const open = items.filter((i) => i.status === 'pending').length;
  return (
    <View style={{ paddingLeft: 38, gap: 6 }}>
      <T v="caption" color={t.ink3}>{open ? L(`仅你可见 · ${open} 项待确认`, `Only you can see this · ${open} to confirm`) : L('仅你可见', 'Only you can see this')}</T>
      {items.map((it) => <InboxCard key={it.id} item={it} variant="chat" />)}
    </View>
  );
});

const styles = StyleSheet.create({
  row: { flexDirection: 'row', gap: space.sm, alignItems: 'flex-start' },
  bubble: { borderRadius: 16, borderTopLeftRadius: 4, paddingHorizontal: 12, paddingVertical: 9 },
  userBubble: { maxWidth: '82%', borderRadius: radius.lg, borderBottomRightRadius: 6, paddingHorizontal: 14, paddingVertical: 9 },
  topic: { alignSelf: 'center', maxWidth: 320, alignItems: 'center', gap: 3, paddingVertical: 8, paddingHorizontal: 14, borderRadius: 14 },
  pinned: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: space.lg, paddingVertical: 8, borderBottomWidth: StyleSheet.hairlineWidth },
  composerWrap: { borderTopWidth: StyleSheet.hairlineWidth },
  endedBar: { alignItems: 'center', paddingVertical: space.md, paddingHorizontal: space.lg },
  tools: { flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingHorizontal: space.md, paddingTop: space.sm },
  toolBtn: { flexDirection: 'row', alignItems: 'center', gap: 4, height: 28, paddingHorizontal: 10, borderRadius: 14 },
  fileChip: { flexDirection: 'row', alignItems: 'center', gap: 6, borderWidth: StyleSheet.hairlineWidth, borderRadius: radius.md, paddingHorizontal: 8, paddingVertical: 5 },
  pickRow: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: 10 },
  allFace: { width: 32, height: 32, borderRadius: 16, alignItems: 'center', justifyContent: 'center' },
});
