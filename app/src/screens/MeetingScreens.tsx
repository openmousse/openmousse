// Agent 会议（server/meetings.py，2026-10-13，画布第 2 排）：
// - Meeting：会议页。每条 Agent 的发言带头像和名字（回复谁、@ 了谁），系统行「由 Grava 发起」「Grava 请 X、Y 回答」，Grava 的纪要卡
//   （待办 + 确认 / 继续讨论）；一轮进行中接 SSE，按发言人分段出字。输入框：补充一句（待确认时开下一轮），@ 指定 Agent，进行中空着时是「停」。
// - NewMeeting：发起会议（议题 + 勾选 Agent，Grava 先按议题推荐；会后保留为群聊；写明用量）。
// - MeetingInfo：会议资料（议题、成员、主持、规则和用量、保留为群聊、结束、删除）。
// - Meetings：已结束的会议。对话列表里的会议行由对话页做（GET /api/meetings 的字段见 server/meetings.py）。
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AppState, Platform, Pressable, ScrollView, StyleSheet, Switch, TextInput, View, type NativeSyntheticEvent, type TextInputKeyPressEventData } from 'react-native';
import { useFocusEffect, useNavigation, useRoute } from '@react-navigation/native';
import { agentName } from '../brand';
import { dataApi } from '../api/data';
import { followMeeting, meetingsApi, splitMentions, type Meeting, type MeetingLive, type MeetingMember, type MeetingMsg } from '../api/meetings';
import { Dots } from '../components/ChatCards';
import { ArrowUp, AtSign, Check, ChevronLeft, ChevronRight, CircleStop, Info, Square, Trash2 } from '../components/icons';
import { ChatScroll, KeyboardSticky, dismissMode, useBottomInset } from '../components/keyboard';
import { LensAvatar } from '../components/LensAvatar';
import { Markdown } from '../components/Markdown';
import { AgentAvatar, MeetingAvatar, StatusPill, TodoRow, confirmedLine } from '../components/MeetingBits';
import { SheetProvider, useSheet } from '../components/Sheet';
import { Btn, Card, NavHeader, PullRefresh, Screen, SectionLabel, T, showError } from '../components/ui';
import { loadDraft, saveDraft } from '../drafts';
import { L } from '../i18n';
import { useStore, useThreadOnScreen } from '../store';
import { agentTint, radius, space, type, useTheme } from '../theme';

type LiveView = { speaker: string | null; replyTo: string | null; text: string; phase: 'speaking' | 'minutes' | 'done'; queue: string[] };
const liveOf = (l: MeetingLive | null): LiveView | null => (l ? { speaker: l.speaker, replyTo: l.replyTo, text: l.text, phase: l.phase, queue: l.queue } : null);
const memberOf = (meeting: Meeting | null, id: string | null | undefined): MeetingMember | undefined => meeting?.members.find((m) => m.id === id);
const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** 页头副标题：会议 · 第 1 轮 / 会议 · 待确认 / Agent 群 · 4 个成员 / 会议 · 已结束。 */
function subtitle(m: Meeting): string {
  if (m.status === 'group') return L(`Agent 群 · ${m.members.length} 个成员`, `Agent group · ${m.members.length} members`);
  if (m.status === 'ended') return L('会议 · 已结束', 'Meeting · Ended');
  if (m.status === 'pending') return L(`会议 · 第 ${m.round} 轮 · 待确认`, `Meeting · Round ${m.round} · To confirm`);
  return L(`会议 · 第 ${m.round} 轮`, `Meeting · Round ${m.round}`);
}

// —— 会议页 ——————————————————————————————————————————————————————————

export function MeetingScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { id } = useRoute<any>().params as { id: string; at?: number };
  const { avatar, reload } = useStore();
  const [meeting, setMeeting] = useState<Meeting | null>(null);
  const [msgs, setMsgs] = useState<MeetingMsg[]>([]);
  const [live, setLive] = useState<LiveView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [acting, setActing] = useState<'confirm' | 'next' | null>(null);
  const follow = useRef<{ stop: () => void } | null>(null);
  const loadRef = useRef<() => Promise<void>>(async () => {});
  const scroller = useRef<ScrollView>(null);
  const root = useRef<View>(null);
  const bottom = useBottomInset(root);
  useThreadOnScreen(id);  // 在屏幕上：不为它弹小窗

  const upsert = useCallback((m: MeetingMsg) => setMsgs((cur) => {
    const i = cur.findIndex((x) => x.id === m.id);
    if (i < 0) return [...cur, m];
    const next = cur.slice();
    next[i] = m;
    return next;
  }), []);

  /** 接上正在进行的一轮：按发言人分段出字；这一轮完了重读一次（纪要、状态以服务器为准）。 */
  const attach = useCallback(() => {
    if (follow.current) return;
    let got = false;
    const f = followMeeting(id, (e) => {
      got = true;
      switch (e.event) {
        case 'state': setLive(liveOf(e.data.live)); break;
        case 'round': setMeeting((m) => (m ? { ...m, round: e.data.round, status: e.data.minutes ? 'running' : m.status } : m)); break;
        case 'speaker': setLive((l) => ({ speaker: e.data.agent, replyTo: e.data.replyTo, text: '', phase: 'speaking', queue: (l?.queue ?? []).filter((a) => a !== e.data.agent) })); break;
        case 'delta': setLive((l) => (l && l.speaker === e.data.agent ? { ...l, text: l.text + e.data.text } : l)); break;
        case 'said': upsert(e.data); setLive((l) => (l && l.speaker === e.data.speaker ? { ...l, speaker: null, replyTo: null, text: '' } : l)); break;
        case 'system': case 'message': case 'minutes': upsert(e.data); break;
        case 'phase': setLive((l) => ({ speaker: null, replyTo: null, text: '', phase: 'minutes', queue: l?.queue ?? [] })); break;
        case 'status': setMeeting((m) => (m ? { ...m, status: e.data.status } : m)); break;
        case 'done': setLive(null); break;
      }
    });
    follow.current = f;
    f.done.finally(() => {
      if (follow.current === f) follow.current = null;
      // 一个事件都没收到就断了（刚好结束 / 网络断了）：等两秒再读，免得来回空转
      if (got) loadRef.current().catch(() => {}); else setTimeout(() => { loadRef.current().catch(() => {}); }, 2000);
    });
  }, [id, upsert]);

  const load = useCallback(async () => {
    try {
      const r = await meetingsApi.get(id);
      setMeeting(r.meeting);
      setMsgs(r.messages);
      setError(null);
      if (r.meeting.live) { setLive((l) => l ?? liveOf(r.meeting.live)); attach(); } else if (!follow.current) setLive(null);
    } catch (e) {
      setError(errText(e));
    }
  }, [id, attach]);
  useEffect(() => { loadRef.current = load; }, [load]);

  // 在屏幕上：读一次，没在进行时每 15 秒看一眼（别的设备上发了话、主对话那边点了继续）；离开就断开
  useFocusEffect(useCallback(() => {
    loadRef.current().catch(() => {});
    const h = setInterval(() => { if (!follow.current && AppState.currentState === 'active') loadRef.current().catch(() => {}); }, 15000);
    return () => { clearInterval(h); follow.current?.stop(); follow.current = null; };
  }, []));

  // 看到最后一条了：标已读（store 里没有会议的消息，自己报）
  const lastDb = [...msgs].reverse().find((m) => m.id.startsWith('db'))?.id;
  useEffect(() => {
    if (!lastDb || AppState.currentState !== 'active') return;
    dataApi.markRead(id, Number(lastDb.slice(2))).then(() => reload('unread')).catch(() => {});
  }, [id, lastDb, reload]);

  // 新消息、正在出的字：滚到底
  const liveLen = live?.text.length ?? 0;
  useEffect(() => {
    const h = setTimeout(() => scroller.current?.scrollToEnd({ animated: true }), 60);
    return () => clearTimeout(h);
  }, [msgs.length, liveLen, live?.speaker, live?.phase]);

  const confirm = async () => {
    setActing('confirm');
    try {
      const r = await meetingsApi.confirm(id);
      setMeeting(r.meeting);
      await load();
      reload('schedule', 'remember').catch(() => {});  // 待办进了日程：「今天」跟着变
    } catch (e) { showError(L('确认失败', "Couldn't confirm"), e); } finally { setActing(null); }
  };
  const next = async () => {
    setActing('next');
    try {
      setMeeting(await meetingsApi.next(id));
      attach();
    } catch (e) { showError(L('无法继续讨论', "Couldn't continue"), e); } finally { setActing(null); }
  };
  const send = async (text: string): Promise<boolean> => {
    try {
      const r = await meetingsApi.send(id, text);
      upsert(r.message);
      setMeeting(r.meeting);
      if (r.meeting.live) attach();
      return true;
    } catch (e) { showError(L('发送失败', "Couldn't send"), e); return false; }
  };
  const stop = () => { meetingsApi.stop(id).catch((e) => showError(L('操作失败', "Couldn't complete the action"), e)); };

  // 列表只在消息、会议变了时重排：回调走 ref（流式出字时 live 每秒变几十次，不能带着整个列表重画）
  const handlers = useRef({ confirm, next });
  useEffect(() => { handlers.current = { confirm, next }; });
  const onConfirm = useCallback(() => { handlers.current.confirm(); }, []);
  const onNext = useCallback(() => { handlers.current.next(); }, []);
  const list = useMemo(() => (meeting ? msgs.map((m) => (
    <MsgRow key={m.id} m={m} meeting={meeting} avatar={avatar} acting={acting} onConfirm={onConfirm} onNext={onNext} />
  )) : null), [msgs, meeting, avatar, acting, onConfirm, onNext]);

  if (!meeting) {
    return (
      <Screen>
        <NavHeader title={L('会议', 'Meeting')} onBack={() => nav.goBack()} />
        <View style={{ padding: space.xl, alignItems: 'center' }}>
          <T v="callout" color={error ? t.bad : t.ink3}>{error ? L(`无法加载：${error}`, `Couldn't load: ${error}`) : L('正在加载…', 'Loading…')}</T>
        </View>
      </Screen>
    );
  }
  const running = meeting.status === 'running' || !!live;
  const placeholder = meeting.status === 'group' ? L('发送消息，@ 指定 Agent', 'Message the group; @ an Agent')
    : running ? L('补充说明，未发言的 Agent 可见', 'Add a note for this round')
      : L('补充说明，下一轮讨论', 'Add a note for the next round');
  const speaker = memberOf(meeting, live?.speaker);
  return (
    <Screen>
      <View style={[styles.head, { borderBottomColor: t.line }]}>
        <Pressable onPress={() => nav.goBack()} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('返回', 'Back')} style={styles.headBtn}>
          <ChevronLeft size={24} color={t.ink} />
        </Pressable>
        <MeetingAvatar members={meeting.members} size={36} />
        <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
          <T v="headline" numberOfLines={1}>{meeting.title}</T>
          <T v="caption" color={t.ink3} numberOfLines={1}>{subtitle(meeting)}</T>
        </View>
        <Pressable onPress={() => nav.navigate('MeetingInfo', { id })} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('会议资料', 'Meeting info')} style={styles.headBtn}>
          <Info size={22} color={t.ink2} />
        </Pressable>
      </View>
      <View ref={root} onLayout={bottom.onLayout} style={{ flex: 1, paddingBottom: bottom.home }}>
        <ChatScroll ref={scroller} offset={bottom.offset} style={{ flex: 1 }} contentContainerStyle={{ padding: space.md, gap: space.md }}
          keyboardShouldPersistTaps="never" keyboardDismissMode={dismissMode} refreshControl={<PullRefresh onRefresh={load} />}>
          {list}
          {live?.phase === 'minutes' ? (
            <View style={styles.row}>
              <LensAvatar size={30} config={avatar} />
              <View style={{ gap: 3, flexShrink: 1 }}>
                <T v="caption" color={t.gold} style={{ fontWeight: '600' }}>{L(`${meeting.hostName} · 纪要`, `${meeting.hostName} · Minutes`)}</T>
                <View style={[styles.bubble, { backgroundColor: t.surface, flexDirection: 'row', alignItems: 'center', gap: 8 }]}>
                  <T v="callout" color={t.ink2}>{L('正在整理纪要', 'Writing the minutes')}</T>
                  <Dots color={t.gold} />
                </View>
              </View>
            </View>
          ) : live?.speaker ? (
            <AgentLine member={speaker} name={speaker?.name ?? live.speaker} replyTo={memberOf(meeting, live.replyTo)?.name} members={meeting.members}
              text={live.text} streaming />
          ) : null}
          {live && live.queue.length ? (
            <T v="caption" color={t.ink3} style={{ textAlign: 'center' }}>
              {L(`接下来：${live.queue.map((a) => memberOf(meeting, a)?.name ?? a).join('、')}`, `Next: ${live.queue.map((a) => memberOf(meeting, a)?.name ?? a).join(', ')}`)}
            </T>
          ) : null}
        </ChatScroll>
        <KeyboardSticky offset={bottom.offset} style={[styles.composerWrap, { borderTopColor: t.line, backgroundColor: t.bg }]}>
          {meeting.status === 'ended' ? (
            <View style={styles.endedBar}>
              <T v="caption" color={t.ink3}>{L('会议已结束，记录保留在这里', 'This meeting has ended; the record stays here')}</T>
            </View>
          ) : (
            <MeetingComposer id={id} members={meeting.members} placeholder={placeholder} running={running} onSend={send} onStop={stop} />
          )}
        </KeyboardSticky>
      </View>
    </Screen>
  );
}

/** 一条 Agent 的发言：圆头像 + 名字（颜色跟着它）+「回复 X」，气泡里 @名字 用被 @ 的那位的颜色。 */
const AgentLine = React.memo(function AgentLine({ member, name, replyTo, members, text, streaming, at, failed }: {
  member?: MeetingMember; name: string; replyTo?: string; members: MeetingMember[]; text: string; streaming?: boolean;
  at?: MeetingMsg['at']; failed?: boolean;
}) {
  const t = useTheme();
  const tint = agentTint(t, member?.color);
  return (
    <View style={styles.row}>
      <AgentAvatar icon={member?.icon} color={member?.color} size={30} />
      <View style={{ flex: 1, minWidth: 0, gap: 3, alignItems: 'flex-start' }}>
        <T v="caption" color={tint.fg} style={{ fontWeight: '600' }}>
          {name}{replyTo ? <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L(` · 回复 ${replyTo}`, ` · replying to ${replyTo}`)}</T> : null}
        </T>
        <View style={[styles.bubble, { backgroundColor: t.surface, maxWidth: '100%' }]}>
          {text ? <SpeechText text={text} members={members} color={failed ? t.ink3 : undefined} /> : null}
          {streaming ? <View style={{ paddingTop: text ? 6 : 2 }}><Dots color={tint.fg} /></View> : null}
        </View>
        {at?.deferred ? <T v="caption" color={t.ink3}>{L(`${at.name} 本轮已发言，问题留到下一轮`, `${at.name} already spoke this round; the question carries over`)}</T> : null}
      </View>
    </View>
  );
});

/** 发言正文：带 @名字 的那几行自己画（@ 的部分用那位 Agent 的颜色），其余照常 Markdown。 */
function SpeechText({ text, members, color }: { text: string; members: MeetingMember[]; color?: string }) {
  const t = useTheme();
  const out: ({ md: string } | { line: string })[] = [];
  let buf: string[] = [];
  for (const ln of text.split('\n')) {
    if (splitMentions(ln, members).some((p) => p.agent)) {
      if (buf.length) out.push({ md: buf.join('\n') });
      buf = [];
      out.push({ line: ln });
    } else buf.push(ln);
  }
  if (buf.length) out.push({ md: buf.join('\n') });
  return (
    // 紧凑的 Markdown 最后一段还带 4 的下边距：整块往回收 4，气泡上下一样宽
    <View style={{ gap: 4, marginBottom: out.length && 'md' in out[out.length - 1] ? -4 : 0 }}>
      {out.map((b, i) => ('md' in b ? (b.md.trim() ? <Markdown key={i} text={b.md} color={color} compact /> : null) : (
        <T key={i} v="body" color={color}>
          {splitMentions(b.line, members).map((p, j) => (p.agent
            ? <T key={j} v="body" style={{ fontWeight: '600' }} color={agentTint(t, members.find((x) => x.id === p.agent)?.color).fg}>{p.text}</T>
            : p.text))}
        </T>
      )))}
    </View>
  );
}

/** 会议里的一条：议题（第一行系统消息）、系统行、你说的、Agent 的发言、Grava 的纪要。 */
const MsgRow = React.memo(function MsgRow({ m, meeting, avatar, acting, onConfirm, onNext }: {
  m: MeetingMsg; meeting: Meeting; avatar: React.ComponentProps<typeof LensAvatar>['config']; acting: 'confirm' | 'next' | null;
  onConfirm: () => void; onNext: () => void;
}) {
  const t = useTheme();
  if (m.kind === 'system' && m.event === 'start') {
    return (
      <View style={[styles.topic, { backgroundColor: t.surface }]}>
        <T v="callout" color={t.ink2} style={{ textAlign: 'center', fontSize: 13, lineHeight: 18 }}>{L(`议题：${meeting.topic}`, `Topic: ${meeting.topic}`)}</T>
        <T v="caption" color={t.ink3} style={{ fontSize: 11, fontWeight: '400' }}>{`${m.text} · ${m.time}`}</T>
      </View>
    );
  }
  if (m.kind === 'system') {
    return <T v="caption" color={t.ink3} style={{ textAlign: 'center', fontWeight: '400', paddingHorizontal: space.lg }}>{`${m.text} · ${m.time}`}</T>;
  }
  if (m.kind === 'user') {
    return (
      <View style={{ alignItems: 'flex-end' }}>
        <View style={[styles.userBubble, { backgroundColor: t.surface2 }]}>
          <SpeechText text={m.text} members={meeting.members} />
        </View>
      </View>
    );
  }
  if (m.kind === 'minutes' && m.minutes) {
    const mn = m.minutes;
    const confirmed = meeting.confirmed?.round === mn.round;
    const latest = mn.round === meeting.round && meeting.minutes?.round === mn.round;
    const sections = [mn.decisions.length, mn.todos.length, mn.open.length].filter(Boolean).length;
    const label = (s: string) => (sections > 1 ? <T v="caption" color={t.ink3}>{s}</T> : null);
    return (
      <View style={[styles.row, !latest && !confirmed && { opacity: 0.6 }]}>
        <LensAvatar size={30} config={avatar} />
        <View style={{ flex: 1, minWidth: 0, gap: 3 }}>
          <T v="caption" color={t.gold} style={{ fontWeight: '600' }}>
            {mn.round > 1 ? L(`${meeting.hostName} · 第 ${mn.round} 轮纪要`, `${meeting.hostName} · Minutes, round ${mn.round}`) : L(`${meeting.hostName} · 纪要`, `${meeting.hostName} · Minutes`)}
          </T>
          <View style={[styles.minutes, { backgroundColor: t.surface, borderColor: t.goldSoft }]}>
            {mn.decisions.length ? <View style={{ gap: 4 }}>{label(L('决定', 'Decided'))}{mn.decisions.map((d, i) => <T key={i} v="callout" style={{ fontSize: 14 }}>{`· ${d}`}</T>)}</View> : null}
            {mn.todos.length ? <View style={{ gap: 10 }}>{label(L('待办', 'To-dos'))}{mn.todos.map((td, i) => <TodoRow key={`${td.agent}-${i}`} todo={td} />)}</View> : null}
            {mn.open.length ? <View style={{ gap: 4 }}>{label(L('未定', 'Open'))}{mn.open.map((q, i) => <T key={i} v="callout" color={t.ink2} style={{ fontSize: 14 }}>{`· ${q}`}</T>)}</View> : null}
            {!sections ? <T v="callout" color={t.ink2}>{L('本轮没有定下决定或待办。', 'Nothing was decided this round.')}</T> : null}
            {confirmed ? (
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                <Check size={14} color={t.good} />
                <T v="caption" color={t.good} style={{ flex: 1 }}>{confirmedLine(meeting.confirmed)}</T>
              </View>
            ) : !latest ? (
              <T v="caption" color={t.ink3}>{L('已被下一轮的纪要取代', 'Replaced by the next round’s minutes')}</T>
            ) : meeting.status === 'pending' ? (
              <View style={{ flexDirection: 'row', gap: space.sm, marginTop: 2 }}>
                <Pressable onPress={onConfirm} disabled={!!acting} accessibilityRole="button" accessibilityLabel={L('确认纪要', 'Confirm the minutes')}
                  accessibilityHint={L('待办写入日程和对应 Agent 的看板', "Adds the to-dos to the schedule and the Agents' dashboards")}
                  style={({ pressed }) => [styles.pillBtn, { backgroundColor: t.goldFill, opacity: acting ? 0.5 : pressed ? 0.8 : 1 }]}>
                  <T v="caption" color={t.onGold} style={{ fontSize: 13, fontWeight: '700' }}>{acting === 'confirm' ? L('正在写入…', 'Adding…') : L('确认', 'Confirm')}</T>
                </Pressable>
                <Pressable onPress={onNext} disabled={!!acting} accessibilityRole="button" accessibilityLabel={L('继续讨论', 'Keep discussing')}
                  accessibilityHint={L('开始下一轮讨论', 'Starts the next round')} style={({ pressed }) => [styles.pillBtn, { backgroundColor: t.surface2, opacity: acting ? 0.5 : pressed ? 0.8 : 1 }]}>
                  <T v="caption" style={{ fontSize: 13, fontWeight: '600' }}>{acting === 'next' ? L('正在开始…', 'Starting…') : L('继续讨论', 'Keep discussing')}</T>
                </Pressable>
              </View>
            ) : meeting.status === 'ended' ? <T v="caption" color={t.ink3}>{L('会议已结束，纪要未确认', 'The meeting ended without confirming these')}</T> : null}
          </View>
        </View>
      </View>
    );
  }
  const member = memberOf(meeting, m.speaker);
  return <AgentLine member={member} name={member?.name ?? m.name} replyTo={m.replyTo?.name} members={meeting.members} text={m.text} at={m.at} failed={m.status === 'error'} />;
});

/** 会议的输入栏：@ 选一位 Agent、补充一句；进行中空着时发送键是「停」。草稿按会议存在本机。 */
const MeetingComposer = React.memo(function MeetingComposer({ id, members, placeholder, running, onSend, onStop }: {
  id: string; members: MeetingMember[]; placeholder: string; running: boolean; onSend: (text: string) => Promise<boolean>; onStop: () => void;
}) {
  const t = useTheme();
  const sheet = useSheet();
  const input = useRef<TextInput>(null);
  const key = `meeting:${id}`;
  const [draft, setDraft] = useState(() => loadDraft(key));
  const [sending, setSending] = useState(false);
  useEffect(() => { saveDraft(key, draft); }, [key, draft]);
  const typed = !!draft.trim();
  const submit = () => {
    if (!typed) { if (running) onStop(); return; }
    if (sending) return;
    setSending(true);
    onSend(draft.trim()).then((ok) => { if (ok) setDraft(''); }).finally(() => setSending(false));
  };
  const mention = () => sheet.open({
    title: L('提到 Agent', 'Mention an Agent'),
    content: (close) => (
      <View style={{ gap: 2 }}>
        {members.filter((m) => !m.gone).map((m) => (
          <Pressable key={m.id} accessibilityRole="button" onPress={() => {
            close();
            setDraft((d) => `${d}${d && !/\s$/.test(d) ? ' ' : ''}@${m.name} `);
            setTimeout(() => input.current?.focus(), 250);
          }} style={({ pressed }) => [styles.pickRow, { opacity: pressed ? 0.6 : 1 }]}>
            <AgentAvatar icon={m.icon} color={m.color} size={32} />
            <T v="body" style={{ flex: 1 }}>{m.name}</T>
          </Pressable>
        ))}
      </View>
    ),
  });
  const webEnter = Platform.OS === 'web' ? (e: NativeSyntheticEvent<TextInputKeyPressEventData>) => {
    const k = e.nativeEvent as unknown as KeyboardEvent;
    if (k.key !== 'Enter' || k.shiftKey || k.isComposing || k.keyCode === 229) return;
    e.preventDefault();
    submit();
  } : undefined;
  const stop = running && !typed;
  return (
    <View style={styles.composer}>
      <Pressable onPress={mention} accessibilityRole="button" accessibilityLabel={L('提到 Agent', 'Mention an Agent')} style={[styles.round, { backgroundColor: t.surface2 }]}>
        <AtSign size={19} color={t.ink} />
      </Pressable>
      <TextInput ref={input} value={draft} onChangeText={setDraft} placeholder={placeholder} placeholderTextColor={t.ink3} multiline numberOfLines={1}
        onSubmitEditing={submit} submitBehavior="submit" returnKeyType="send" onKeyPress={webEnter} accessibilityLabel={L('补充说明', 'Add a note')}
        style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink }]} />
      <Pressable onPress={submit} disabled={!stop && (!typed || sending)} accessibilityRole="button" accessibilityLabel={stop ? L('停止本轮', 'Stop this round') : L('发送', 'Send')}
        style={[styles.round, { backgroundColor: stop ? t.surface2 : typed ? t.goldFill : t.surface2 }]}>
        {stop ? <Square size={14} color={t.ink} fill={t.ink} /> : <ArrowUp size={19} color={typed ? t.onGold : t.ink3} />}
      </Pressable>
    </View>
  );
});

// —— 发起会议 ——————————————————————————————————————————————————————————

// 原生 modal 页：弹层要在页内自己挂一个（见 NewGroupScreen）
export function NewMeetingScreen() {
  return <SheetProvider><NewMeetingForm /></SheetProvider>;
}

function NewMeetingForm() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { groups } = useStore();
  const params = (useRoute<any>().params ?? {}) as { topic?: string; members?: string[] };
  const [topic, setTopic] = useState(params.topic ?? '');
  const [picked, setPicked] = useState<string[]>(params.members ?? []);
  const [touched, setTouched] = useState(!!params.members?.length);  // 自己勾过：不再拿推荐覆盖
  const [hint, setHint] = useState<{ topic: string; agents: string[]; title: string } | null>(null);
  const [asking, setAsking] = useState(false);
  const [keep, setKeep] = useState(false);
  const [starting, setStarting] = useState(false);
  const q = topic.trim();
  // 议题停下来 0.9 秒：Grava 按议题推荐人选（不带工具的模型）
  useEffect(() => {
    if (q.length < 4 || touched || hint?.topic === q) return undefined;
    const h = setTimeout(() => {
      setAsking(true);
      meetingsApi.suggest(q)
        .then((s) => { setHint({ topic: q, ...s }); if (s.agents.length) setPicked(s.agents); })
        .catch(() => {})
        .finally(() => setAsking(false));
    }, 900);
    return () => clearTimeout(h);
  }, [q, touched, hint?.topic]);
  const toggle = (gid: string) => { setTouched(true); setPicked((cur) => (cur.includes(gid) ? cur.filter((x) => x !== gid) : [...cur, gid])); };
  const can = !!q && picked.length >= 2 && !starting;
  const start = async () => {
    if (!can) return;
    setStarting(true);
    try {
      const m = await meetingsApi.create({ topic: q, members: picked, keep, title: hint?.topic === q && hint.title ? hint.title : undefined });
      nav.replace('Meeting', { id: m.id, at: Date.now() });
    } catch (e) {
      showError(L('无法发起会议', "Couldn't start the meeting"), e);
      setStarting(false);
    }
  };
  const recommended = !touched && hint?.topic === q && hint.agents.length;
  const label = asking ? L('参会 Agent · 正在按议题推荐…', 'Agents · Picking for this topic…')
    : recommended ? L(`参会 Agent · 已按议题推荐 ${hint!.agents.length} 个`, `Agents · ${hint!.agents.length} suggested for this topic`)
      : L(`参会 Agent · 已选 ${picked.length} 个`, `Agents · ${picked.length} selected`);
  return (
    <Screen>
      <View style={styles.modalHead}>
        <Pressable onPress={() => nav.goBack()} hitSlop={8} accessibilityRole="button" style={{ width: 64 }}>
          <T v="body" color={t.ink2}>{L('取消', 'Cancel')}</T>
        </Pressable>
        <T v="headline" style={{ fontSize: 17, fontWeight: '700' }}>{L('发起会议', 'New meeting')}</T>
        <Pressable onPress={start} disabled={!can} hitSlop={8} accessibilityRole="button" accessibilityState={{ disabled: !can }} style={{ width: 64, alignItems: 'flex-end' }}>
          <T v="body" color={can ? t.gold : t.ink3} style={{ fontWeight: '700' }}>{starting ? L('正在发起', 'Starting') : L('开始', 'Start')}</T>
        </Pressable>
      </View>
      <ScrollView contentContainerStyle={{ paddingHorizontal: space.lg, paddingBottom: space.xxl }} automaticallyAdjustKeyboardInsets keyboardShouldPersistTaps="handled">
        <T v="caption" color={t.ink3} style={styles.formLabel}>{L('议题', 'Topic')}</T>
        <TextInput value={topic} onChangeText={setTopic} multiline placeholder={L('例如：下周三模拟面试前的训练、饮食与睡眠安排', 'e.g. Training, meals and sleep before next Wednesday’s mock interview')}
          placeholderTextColor={t.ink3} accessibilityLabel={L('议题', 'Topic')} style={[type.body, styles.topicInput, { backgroundColor: t.surface, color: t.ink }]} />
        <T v="caption" color={t.ink3} style={[styles.formLabel, { marginTop: space.lg }]}>{label}</T>
        <View style={[styles.group, { backgroundColor: t.surface }]}>
          {groups.length ? groups.map((g, i) => {
            const on = picked.includes(g.id);
            return (
              <Pressable key={g.id} onPress={() => toggle(g.id)} accessibilityRole="checkbox" accessibilityState={{ checked: on }} accessibilityLabel={g.name}
                style={({ pressed }) => [styles.agentRow, { opacity: pressed ? 0.7 : 1 }]}>
                <AgentAvatar icon={g.icon} color={g.color} size={34} />
                <View style={[styles.agentRowBody, i < groups.length - 1 && { borderBottomColor: t.surface2, borderBottomWidth: StyleSheet.hairlineWidth }]}>
                  <T v="body" style={{ flex: 1, fontWeight: '600' }} numberOfLines={1}>{g.name}</T>
                  {on ? <View style={[styles.check, { backgroundColor: t.cyan }]}><Check size={15} color={t.bg} strokeWidth={3} /></View>
                    : <View style={[styles.uncheck, { borderColor: t.ink3 }]} />}
                </View>
              </Pressable>
            );
          }) : <T v="callout" color={t.ink3} style={{ padding: space.lg }}>{L('还没有 Agent。先新建 Agent，再发起会议。', 'No Agents yet. Create an Agent first, then start a meeting.')}</T>}
        </View>
        <View style={[styles.group, styles.keepRow, { backgroundColor: t.surface, marginTop: space.lg }]}>
          <View style={{ flex: 1, gap: 3 }}>
            <T v="body" style={{ fontWeight: '600' }}>{L('会后保留为群聊', 'Keep as a group chat')}</T>
            <T v="caption" color={t.ink3} style={{ fontWeight: '400', lineHeight: 17 }}>
              {L('关闭：会议结束后归入「已结束的会议」；开启：保留为群聊，可继续讨论', 'Off: the meeting moves to Ended meetings when it’s over. On: it stays as a group chat you can keep using.')}
            </T>
          </View>
          <Switch value={keep} onValueChange={setKeep} accessibilityLabel={L('会后保留为群聊', 'Keep as a group chat')} trackColor={{ true: t.cyan, false: t.surface2 }} />
        </View>
        <T v="caption" color={t.ink3} style={styles.rules}>
          {L(`每轮每个 Agent 发言一次，可 @ 其他 Agent 一次；由 ${agentName()} 主持并生成纪要。`, `Each Agent speaks once per round and may @ one other Agent; ${agentName()} hosts and writes the minutes.`)}
        </T>
        <T v="caption" color={t.ink3} style={[styles.rules, { paddingTop: 4 }]}>
          {picked.length >= 2
            ? L(`用量：每轮 = 成员数次回复 + 1 次纪要，当前 ${picked.length} + 1 = ${picked.length + 1} 次模型调用。`, `Usage: each round = one reply per member + 1 for the minutes; now ${picked.length} + 1 = ${picked.length + 1} model calls.`)
            : L('用量：每轮 = 成员数次回复 + 1 次纪要。至少选择 2 个 Agent。', 'Usage: each round = one reply per member + 1 for the minutes. Pick at least 2 Agents.')}
        </T>
      </ScrollView>
    </Screen>
  );
}

// —— 会议资料 ——————————————————————————————————————————————————————————

export function MeetingInfoScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const sheet = useSheet();
  const { id } = useRoute<any>().params as { id: string };
  const { reload, avatar } = useStore();
  const [m, setM] = useState<Meeting | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => meetingsApi.get(id).then((r) => { setM(r.meeting); setError(null); }).catch((e) => setError(errText(e))), [id]);
  useFocusEffect(useCallback(() => { load(); }, [load]));
  if (!m) {
    return (
      <Screen>
        <NavHeader title={L('会议资料', 'Meeting info')} onBack={() => nav.goBack()} />
        <T v="callout" color={error ? t.bad : t.ink3} style={{ padding: space.xl, textAlign: 'center' }}>{error ? L(`无法加载：${error}`, `Couldn't load: ${error}`) : L('正在加载…', 'Loading…')}</T>
      </Screen>
    );
  }
  const keep = async (v: boolean) => {
    try { setM(await meetingsApi.patch(id, { keep: v })); } catch (e) { showError(L('保存失败', "Couldn't save"), e); }
  };
  const end = () => sheet.open({
    title: m.status === 'group' ? L('解散群聊', 'Close the group') : L('结束会议', 'End the meeting'),
    content: (close) => (
      <View style={{ gap: space.md }}>
        <T v="callout" color={t.ink2}>
          {m.status === 'group' ? L('群聊归入「已结束的会议」，记录保留。', 'The group moves to Ended meetings; the record stays.')
            : L('会议归入「已结束的会议」，记录保留；没有确认的纪要不写入日程和看板。', 'The meeting moves to Ended meetings and the record stays. Minutes that weren’t confirmed are not added to the schedule or dashboards.')}
        </T>
        <Btn label={m.status === 'group' ? L('解散', 'Close') : L('结束', 'End')} kind="danger" icon={<CircleStop size={16} color={t.bad} />}
          onPress={() => { close(); meetingsApi.end(id).then((x) => { setM(x); reload('unread').catch(() => {}); }).catch((e) => showError(L('操作失败', "Couldn't complete the action"), e)); }} />
      </View>
    ),
  });
  const remove = () => sheet.open({
    title: L('删除会议', 'Delete the meeting'),
    content: (close) => (
      <View style={{ gap: space.md }}>
        <T v="callout" color={t.ink2}>{L('删除会议记录和纪要；已确认的待办保留在日程和看板中。', 'Deletes the meeting’s messages and minutes. Confirmed to-dos stay in your schedule and dashboards.')}</T>
        <Btn label={L('删除', 'Delete')} kind="danger" icon={<Trash2 size={16} color={t.bad} />} onPress={() => {
          close();
          meetingsApi.remove(id).then(() => { reload('unread').catch(() => {}); nav.pop(2); }).catch((e) => showError(L('删除失败', "Couldn't delete"), e));
        }} />
      </View>
    ),
  });
  return (
    <Screen>
      <NavHeader title={L('会议资料', 'Meeting info')} onBack={() => nav.goBack()} />
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl }}>
        <View style={{ alignItems: 'center', gap: space.sm, paddingVertical: space.md }}>
          <MeetingAvatar members={m.members} size={64} />
          <T v="title" style={{ textAlign: 'center' }}>{m.title}</T>
          <StatusPill status={m.status} label={m.statusLabel} />
        </View>
        <SectionLabel>{L('议题', 'Topic')}</SectionLabel>
        <Card><T v="body">{m.topic}</T></Card>
        <SectionLabel>{L(`成员 · ${m.members.length}`, `Members · ${m.members.length}`)}</SectionLabel>
        <Card style={{ paddingVertical: space.xs }}>
          {m.members.map((a) => (
            <Pressable key={a.id} disabled={a.gone} onPress={() => nav.navigate('Group', { id: a.id })} accessibilityRole="button" style={({ pressed }) => [styles.memberRow, { opacity: pressed ? 0.6 : 1 }]}>
              <AgentAvatar icon={a.icon} color={a.color} size={32} />
              <T v="body" style={{ flex: 1 }}>{a.name}</T>
              {a.gone ? <T v="caption" color={t.ink3}>{L('已删除', 'Deleted')}</T> : <ChevronRight size={16} color={t.ink3} />}
            </Pressable>
          ))}
          <View style={styles.memberRow}>
            <LensAvatar size={32} config={avatar} />
            <T v="body" style={{ flex: 1 }}>{L(`${m.hostName} 主持`, `Hosted by ${m.hostName}`)}</T>
          </View>
        </Card>
        <T v="caption" color={t.ink3} style={[styles.rules, { paddingHorizontal: space.xs }]}>
          {m.status === 'group'
            ? L(`@ 谁谁回答；未 @ 时由 ${m.hostName} 选择回答的 Agent。群聊不生成纪要。`, `Whoever you @ answers; otherwise ${m.hostName} picks who answers. Group chats have no minutes.`)
            : L(`每轮每个 Agent 发言一次，可 @ 其他 Agent 一次；由 ${m.hostName} 主持并生成纪要。用量：每轮 = 成员数次回复 + 1 次纪要。`, `Each Agent speaks once per round and may @ one other Agent; ${m.hostName} hosts and writes the minutes. Usage: each round = one reply per member + 1 for the minutes.`)}
        </T>
        <View style={[styles.group, styles.keepRow, { backgroundColor: t.surface, marginTop: space.lg }]}>
          <View style={{ flex: 1, gap: 3 }}>
            <T v="body" style={{ fontWeight: '600' }}>{m.status === 'ended' ? L('重新开启为群聊', 'Reopen as a group chat') : L('会后保留为群聊', 'Keep as a group chat')}</T>
            <T v="caption" color={t.ink3} style={{ fontWeight: '400', lineHeight: 17 }}>
              {m.status === 'group' ? L('关闭后群聊结束，归入「已结束的会议」', 'Turning it off ends the group and moves it to Ended meetings')
                : m.status === 'ended' ? L('开启后可继续 @ 成员讨论', 'Turn on to keep talking with the members')
                  : L('确认纪要后保留为群聊，可继续讨论', 'After the minutes are confirmed it stays as a group chat')}
            </T>
          </View>
          <Switch value={m.status === 'group' || (m.status !== 'ended' && m.keep)} onValueChange={keep} accessibilityLabel={L('会后保留为群聊', 'Keep as a group chat')} trackColor={{ true: t.cyan, false: t.surface2 }} />
        </View>
        <View style={{ gap: space.sm, marginTop: space.xl }}>
          {m.status !== 'ended' ? <Btn label={m.status === 'group' ? L('解散群聊', 'Close the group') : L('结束会议', 'End the meeting')} kind="quiet" icon={<CircleStop size={16} color={t.ink} />} onPress={end} /> : null}
          <Btn label={L('删除会议', 'Delete the meeting')} kind="danger" icon={<Trash2 size={16} color={t.bad} />} onPress={remove} />
        </View>
      </ScrollView>
    </Screen>
  );
}

// —— 会议列表（已结束的会议；对话页没有会议行之前，侧栏也用它） ————————————————————

/** 读会议列表：on 的时候读一次（status 见 GET /api/meetings）。 */
export function useMeetings(on: boolean, status: 'active' | 'ended' = 'active') {
  const [rows, setRows] = useState<Meeting[]>([]);
  const [ended, setEnded] = useState(0);
  const load = useCallback(() => meetingsApi.list(status).then((r) => { setRows(r.meetings); setEnded(r.endedCount); }).catch(() => {}), [status]);
  useEffect(() => { if (on) load(); }, [on, load]);
  return { rows, ended, reload: load };
}

/** 一行会议：组合头像、名字、最后一句（谁说的）、时间；待确认的标出来。 */
export function MeetingRow({ m, unread = 0, onPress }: { m: Meeting; unread?: number; onPress: () => void }) {
  const t = useTheme();
  const last = m.last ? `${m.last.name ? `${m.last.name}${L('：', ': ')}` : ''}${m.last.text}` : m.topic;
  return (
    <Pressable onPress={onPress} accessibilityRole="button" style={({ pressed }) => [styles.listRow, { opacity: pressed ? 0.7 : 1 }]}>
      <MeetingAvatar members={m.members} size={40} />
      <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
          <T v="headline" numberOfLines={1} style={{ flexShrink: 1, fontSize: 15 }}>{m.title}</T>
          {m.needsYou ? <StatusPill status="pending" label={m.statusLabel} /> : m.status === 'running' ? <StatusPill status="running" label={m.statusLabel} /> : null}
        </View>
        <T v="caption" color={unread ? t.ink2 : t.ink3} numberOfLines={1} style={{ fontWeight: '400' }}>{last}</T>
      </View>
      {m.last ? <T v="caption" color={t.ink3}>{m.last.time}</T> : null}
    </Pressable>
  );
}

export function MeetingsScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { status = 'ended' } = (useRoute<any>().params ?? {}) as { status?: 'active' | 'ended' };
  const { rows, reload } = useMeetings(true, status);
  useFocusEffect(useCallback(() => { reload(); }, [reload]));
  return (
    <Screen>
      <NavHeader title={status === 'ended' ? L('已结束的会议', 'Ended meetings') : L('会议', 'Meetings')} onBack={() => nav.goBack()} />
      <ScrollView contentContainerStyle={{ padding: space.lg, gap: space.xs }} refreshControl={<PullRefresh onRefresh={reload} />}>
        {rows.length ? rows.map((m) => <MeetingRow key={m.id} m={m} onPress={() => nav.navigate('Meeting', { id: m.id })} />)
          : <T v="callout" color={t.ink3} style={{ textAlign: 'center', paddingVertical: space.xxl }}>{status === 'ended' ? L('暂无已结束的会议。', 'No ended meetings yet.') : L('暂无会议。', 'No meetings yet.')}</T>}
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  head: { flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingVertical: 10, paddingLeft: 4, paddingRight: space.sm, borderBottomWidth: StyleSheet.hairlineWidth },
  headBtn: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center' },
  row: { flexDirection: 'row', gap: space.sm, alignItems: 'flex-start' },
  bubble: { borderRadius: 16, borderTopLeftRadius: 4, paddingHorizontal: 12, paddingVertical: 9 },
  userBubble: { maxWidth: '82%', borderRadius: radius.lg, borderBottomRightRadius: 6, paddingHorizontal: 14, paddingVertical: 9 },
  topic: { alignSelf: 'center', maxWidth: 320, alignItems: 'center', gap: 3, paddingVertical: 8, paddingHorizontal: 14, borderRadius: 14 },
  minutes: { borderWidth: 1, borderRadius: 16, borderTopLeftRadius: 4, padding: space.md, gap: 10 },
  pillBtn: { height: 34, paddingHorizontal: 16, borderRadius: 17, alignItems: 'center', justifyContent: 'center' },
  composerWrap: { borderTopWidth: StyleSheet.hairlineWidth },
  composer: { flexDirection: 'row', alignItems: 'flex-end', gap: space.sm, paddingHorizontal: 10, paddingVertical: space.sm },
  round: { width: 38, height: 38, borderRadius: 19, alignItems: 'center', justifyContent: 'center' },
  input: { flex: 1, minHeight: 38, maxHeight: 120, borderRadius: 19, paddingHorizontal: 14, paddingTop: 8, paddingBottom: 8, fontSize: 15 },
  endedBar: { alignItems: 'center', paddingVertical: space.md },
  pickRow: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: 10 },
  modalHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: space.lg, paddingTop: space.lg, paddingBottom: space.md },
  formLabel: { fontSize: 13, fontWeight: '600', paddingLeft: 4, marginBottom: space.sm },
  topicInput: { minHeight: 70, borderRadius: 14, paddingHorizontal: 14, paddingTop: 12, paddingBottom: 12, fontSize: 15, lineHeight: 21, textAlignVertical: 'top' },
  group: { borderRadius: radius.lg - 2, overflow: 'hidden' },
  agentRow: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingLeft: space.md },
  agentRowBody: { flex: 1, minWidth: 0, height: 54, flexDirection: 'row', alignItems: 'center', gap: 10, paddingRight: 14 },
  check: { width: 24, height: 24, borderRadius: 12, alignItems: 'center', justifyContent: 'center' },
  uncheck: { width: 22, height: 22, borderRadius: 11, borderWidth: 1.5 },
  keepRow: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: space.md, paddingHorizontal: 14 },
  rules: { fontSize: 13, lineHeight: 19, fontWeight: '400', paddingHorizontal: space.xs, paddingTop: space.md },
  memberRow: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: 10 },
  listRow: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: 10 },
});
