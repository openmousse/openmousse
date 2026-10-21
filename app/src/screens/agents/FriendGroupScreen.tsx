// 好友群（主线第三步第三段，设计稿第 46 张；服务器 ../../../server/friendgroup.py，API ../../api/friendgroups.ts）。路由 FriendGroup（params id）。
// - 页头：返回 / 群头像 / 群名 +「你、Mia、Mia 的管家、你的管家」/ ⓘ（成员、你的管家进不进、这个群的规矩）。顶上一行
//   「好友的话只给管家看要点，不驱动你的工具；说出去之前经 Doorman 复查。」
// - 人的话：好友在左（首字母，没有环），你在右。管家的气泡写「管家 · 你的管家」/「管家 · Mia 的管家」（你的金环，好友的灰环）。
// - 只在你这边的：管家请教了哪位赛博（「仅你可见：问了 Victor，只带问题要点」，点开看带去的要点）、管家出给你的卡（InboxCard：约时间的
//   「同意 / 换时间 / 不去」、Doorman 扣下的一句），挂在管家那一句下面；提纲那种灰字也只在你这边。
// - 输入框是对话页的 Composer（只引用）：「回复；@管家 让它去问」；好友群只收文字，没有附件。
// - 有好友的录前讨论（来源是一期播客，podprep.py）：顶上钉着这一期（PrepPanel：提纲、排提纲 / 放进素材 / 约时间 / 开始录制）；
//   你自己约的时间直接进日程、管家在群里说一声，好友提的时间先出卡给你。
// 状态记在本页，不进 store：进来读一次，在屏幕上时每 4 秒问一次新的（after），看到了报已读（只在你这边，不发回执）。
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, AppState, Pressable, ScrollView, StyleSheet, Switch, View } from 'react-native';
import { useFocusEffect, useNavigation, useRoute } from '@react-navigation/native';
import { inboxOf } from '../../api/data';
import { useButlerAvatar } from '../../api/butler';
import { friendGroupsApi, type FGConsult, type FGMessage, type FriendGroup } from '../../api/friendgroups';
import { groupsApi, type PrepOffer } from '../../api/groups';
import type { InboxItem, PendingFile } from '../../data/types';
import { ButlerAvatar, ButlerFace, PersonFace } from '../../components/agents/CyberAvatar';
import { FriendGroupFace, friendGroupLine } from '../../components/agents/GroupBits';
import { BookSheet, PrepPanel } from '../../components/agents/PrepBits';
import { AgentAvatar } from '../../components/MeetingBits';
import { Composer, type ComposerHandle } from '../../components/ChatComposer';
import { ChatHeader } from '../../components/ChatParts';
import { AtSign, ChevronDown, ChevronUp, ShieldCheck } from '../../components/icons';
import { InboxCard } from '../../components/InboxCard';
import { ChatScroll, dismissMode, KeyboardSticky, useBottomInset } from '../../components/keyboard';
import { Markdown } from '../../components/Markdown';
import { useSheet } from '../../components/Sheet';
import { NavHeader, Pill, PullRefresh, Screen, T, showError } from '../../components/ui';
import { loadDraft } from '../../drafts';
import { L } from '../../i18n';
import { useStore, useThreadOnScreen } from '../../store';
import { radius, space, useTheme } from '../../theme';
import { clockLabel } from '../../components/FriendBits';

const POLL_MS = 4000;
const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

export function FriendGroupScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const sheet = useSheet();
  const { id } = useRoute<any>().params as { id: string };
  const { inbox, receipts, transcribe, groups } = useStore();
  const [group, setGroup] = useState<FriendGroup | null>(null);
  const [msgs, setMsgs] = useState<FGMessage[]>([]);
  const [cards, setCards] = useState<(InboxItem & { groupMessage: number })[]>([]);
  const [consults, setConsults] = useState<FGConsult[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [offers, setOffers] = useState<PrepOffer[]>([]);
  const [prepBusy, setPrepBusy] = useState<string | null>(null);
  const composer = useRef<ComposerHandle>(null);
  const scroller = useRef<ScrollView>(null);
  const root = useRef<View>(null);
  const bottom = useBottomInset(root);
  const lastRef = useRef(0);
  const threadKey = `fg:${id}`;
  useThreadOnScreen(threadKey);

  const lastId = msgs.length ? msgs[msgs.length - 1].id : 0;
  useEffect(() => { lastRef.current = lastId; }, [lastId]);

  /** after = 0：整个读一遍；否则只接这之后的（轮询）。 */
  const load = useCallback(async (after = 0) => {
    try {
      const r = await friendGroupsApi.get(id, after);
      setGroup(r.group);
      setCards(r.cards);
      setConsults(r.consults);
      setOffers(r.offers ?? []);
      setMsgs((cur) => {
        if (!after) return r.messages;
        const have = new Set(cur.map((m) => m.id));
        const more = r.messages.filter((m) => !have.has(m.id));
        return more.length ? [...cur, ...more] : cur;
      });
      setError(null);
    } catch (e) {
      setError(errText(e));
    }
  }, [id]);

  useFocusEffect(useCallback(() => {
    load().catch(() => {});
    const h = setInterval(() => { if (AppState.currentState === 'active') load(lastRef.current).catch(() => {}); }, POLL_MS);
    return () => clearInterval(h);
  }, [load]));

  // 看到最后一条了：报已读（只清你这边的未读，不发回执）
  useEffect(() => {
    if (!lastId || AppState.currentState !== 'active') return;
    friendGroupsApi.read(id, lastId).catch(() => {});
  }, [id, lastId]);

  useEffect(() => {
    const h = setTimeout(() => scroller.current?.scrollToEnd({ animated: true }), 60);
    return () => clearTimeout(h);
  }, [msgs.length, cards.length]);

  const submit = async (text: string): Promise<boolean> => {
    if (!text.trim() || sending) return false;
    setSending(true);
    try {
      const m = await friendGroupsApi.send(id, text.trim());
      setMsgs((cur) => (cur.some((x) => x.id === m.id) ? cur : [...cur, m]));
      return true;
    } catch (e) {
      showError(L('发送失败', "Couldn't send"), e);
      return false;
    } finally {
      setSending(false);
    }
  };
  /** 「@管家」：叫管家去问（群主的管家「@管家」就叫得到；别人的群 @ 它的名字）。 */
  const mention = () => {
    if (!group) return;
    const butlers = group.members.filter((m) => m.butler);
    const put = (name: string) => {
      const d = loadDraft(threadKey);
      composer.current?.set(`${d}${d && !/\s$/.test(d) ? ' ' : ''}@${name} `);
      setTimeout(() => composer.current?.focus(), 250);
    };
    sheet.open({ title: L('提到', 'Mention'), content: (close) => (
      <View style={{ gap: 2 }}>
        {butlers.map((m) => (
          <Pressable key={m.kid} accessibilityRole="button" onPress={() => { close(); put(m.me && group.mine ? L('管家', 'butler') : m.butlerName ?? m.name); }}
            style={({ pressed }) => [styles.pickRow, { opacity: pressed ? 0.6 : 1 }]}>
            {m.me ? <MyButlerFace size={32} /> : <ButlerFace size={32} tone="grey" />}
            <View style={{ flex: 1, gap: 1 }}>
              <T v="body">{m.me ? L('你的管家', 'Your butler') : m.butlerName ?? m.name}</T>
              <T v="caption" color={t.ink3}>{m.me ? L('替你答；要你定的先出卡给你', 'Answers for you; anything you need to decide comes to you as a card first') : L('替对方答', 'Answers for them')}</T>
            </View>
          </Pressable>
        ))}
        {!butlers.length ? <T v="callout" color={t.ink3}>{L('这个群里没有管家。', 'No butler in this group.')}</T> : null}
      </View>
    ) });
  };
  const info = () => group && sheet.open({ title: group.title, content: (close) => <InfoSheet g={group} close={close} onChanged={setGroup} /> });

  // —— 录前讨论：提纲（Iris 帮忙排，只给她你说的）、放进素材、约时间、开始录制 ——
  const episode = group?.episode ?? null;
  const draftOutline = async () => {
    if (!episode) return;
    setPrepBusy('outline');
    try { await groupsApi.outline(episode.id); await load(); } catch (e) { showError(L('提纲没排出来', "Couldn't draft the outline"), e); } finally { setPrepBusy(null); }
  };
  const offerFrom = () => {
    if (!episode) return;
    sheet.open({ title: L('放进素材', 'Add material'), sub: L('拿一个功能 Agent 最近的一份报告', "An Agent's latest report"), content: (close) => (
      <View style={{ gap: 2 }}>
        <T v="caption" color={t.ink3} style={{ paddingBottom: 6 }}>{L('出一张只有你看得到的卡，点「放进」才算。有好友在：健康、饮食、睡眠的报告不放进来。', "You get a card only you can see; it counts once you tap Put in. With friends here, health, diet and sleep reports stay out.")}</T>
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
    ) });
  };
  const decideOffer = (o: PrepOffer, action: 'put' | 'skip') => {
    setPrepBusy(o.id);
    groupsApi.decideOffer(o.id, action).then((r) => setOffers((cur) => cur.map((x) => (x.id === o.id ? r.offer : x))))
      .catch((e) => showError(L('操作失败', "Couldn't complete the action"), e)).finally(() => setPrepBusy(null));
  };
  const book = () => episode && sheet.open({ title: L('约录制时间', 'Book the recording'), content: (close) => (
    <BookSheet booked={episode.booked?.at ?? null} friends close={close} onBook={(at) => groupsApi.book(episode.id, at).then(() => load())} />
  ) });
  // 输入框是 memo 的：回调走 ref，保持不变
  const handlers = useRef({ submit, transcribe });
  useEffect(() => { handlers.current = { submit, transcribe }; });
  const onSubmit = useCallback((text: string) => handlers.current.submit(text), []);
  const onTranscribe = useCallback((f: PendingFile) => handlers.current.transcribe(f), []);
  const noop = useCallback(() => {}, []);
  const onAttach = useCallback(() => Alert.alert(L('只发文字', 'Text only'), L('好友群暂时只发文字；图片和文件请在一对一里发。', 'Friend groups take text only for now; send photos and files one-to-one.')), []);

  // 只给你的卡：store 里有它（刚点过、收件箱读过）就用 store 的，点完立刻变回执；没有就用群接口给的
  const known = useMemo(() => {
    const m = new Map<string, InboxItem>();
    for (const r of receipts) m.set(r.item.id, r.item);
    for (const i of inbox) m.set(i.id, i);
    return m;
  }, [inbox, receipts]);
  const cardsBy = useMemo(() => {
    const out = new Map<number, InboxItem[]>();
    for (const c of cards) out.set(c.groupMessage, [...(out.get(c.groupMessage) ?? []), known.get(c.id) ?? inboxOf(c)]);
    return out;
  }, [cards, known]);
  const consultBy = useMemo(() => new Map(consults.map((c) => [c.id, c])), [consults]);

  if (!group) {
    return (
      <Screen>
        <NavHeader title={L('好友群', 'Friend group')} onBack={() => nav.goBack()} />
        <View style={{ padding: space.xl, alignItems: 'center' }}>
          <T v="callout" color={error ? t.bad : t.ink3}>{error ? L(`无法加载：${error}`, `Couldn't load: ${error}`) : L('正在加载…', 'Loading…')}</T>
        </View>
      </Screen>
    );
  }
  const waiting = cards.some((c) => (known.get(c.id)?.status ?? c.status) === 'pending');
  return (
    <Screen>
      <ChatHeader title={group.title} sub={friendGroupLine(group)} face={<FriendGroupFace g={group} size={36} />}
        onBack={() => nav.goBack()} onInfo={info} infoLabel={L('群资料', 'Group info')} />
      {episode ? (
        <PrepPanel episode={episode} offers={offers} busy={prepBusy} onOutline={draftOutline} onOffer={offerFrom} onBook={book}
          onRecord={() => nav.navigate('PodRec', { id: episode.id })} onDecide={decideOffer} />
      ) : null}
      <View style={[styles.note, { backgroundColor: t.surface, borderBottomColor: t.line }]}>
        <ShieldCheck size={13} color={t.good} />
        <T v="caption" color={t.ink2} style={{ flex: 1 }} numberOfLines={2}>{group.note}</T>
        {waiting ? <Pill label={L('有卡等你', 'Card waiting')} tone="warn" /> : null}
      </View>
      <View ref={root} onLayout={bottom.onLayout} style={{ flex: 1, paddingBottom: bottom.home }}>
        <ChatScroll ref={scroller} offset={bottom.offset} style={{ flex: 1 }} contentContainerStyle={{ padding: space.md, gap: space.md }}
          keyboardShouldPersistTaps="never" keyboardDismissMode={dismissMode} refreshControl={<PullRefresh onRefresh={() => load()} />}>
          {!msgs.length ? <T v="callout" color={t.ink3} style={{ textAlign: 'center', paddingTop: space.xl }}>{L('还没有人说话。', 'Nobody has said anything yet.')}</T> : null}
          {msgs.map((m) => (
            <View key={m.id} style={{ gap: space.sm }}>
              <Line m={m} consult={m.view ? consultBy.get(m.view.consult) : undefined} />
              <OwnerCards items={cardsBy.get(m.id)} />
            </View>
          ))}
        </ChatScroll>
        <KeyboardSticky offset={bottom.offset} style={[styles.composerWrap, { borderTopColor: t.line, backgroundColor: t.bg }]}>
          <View style={styles.tools}>
            <Pressable onPress={mention} accessibilityRole="button" accessibilityLabel={L('提到管家', 'Mention a butler')}
              style={({ pressed }) => [styles.toolBtn, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
              <AtSign size={14} color={t.ink2} />
              <T v="caption" color={t.ink2} style={{ fontWeight: '600' }}>{L('提到', 'Mention')}</T>
            </Pressable>
            <T v="caption" color={t.ink3} numberOfLines={1} style={{ flex: 1 }}>
              {sending ? L('正在发送…', 'Sending…') : L('端到端加密 · 逐个送到每位成员', 'End-to-end encrypted · delivered to each member')}
            </T>
          </View>
          <Composer handle={composer} threadId={threadKey} placeholder={L('回复；@管家 让它去问', 'Reply; @butler to have it ask')} quote={null}
            busy={false} hasFiles={false} onSubmit={onSubmit} onStop={noop} onAttach={onAttach} transcribe={onTranscribe} />
        </KeyboardSticky>
      </View>
    </Screen>
  );
}

/** 你的管家的头像（资料页上换过的形象也跟着）。 */
function MyButlerFace({ size }: { size: number }) {
  const avatar = useButlerAvatar();
  return <ButlerAvatar avatar={avatar} size={size} />;
}

/** 群里的一条：只在你这边的灰字、你说的（右边）、好友说的（左边，首字母）、管家说的（「管家 · 你的管家」）。 */
const Line = React.memo(function Line({ m, consult }: { m: FGMessage; consult?: FGConsult }) {
  const t = useTheme();
  const time = clockLabel(m.at);
  if (m.system) return <T v="caption" color={t.ink3} style={{ textAlign: 'center', paddingHorizontal: space.lg }}>{`${m.text} · ${time}`}</T>;
  if (m.mine && m.who === 'person') {
    return (
      <View style={{ alignItems: 'flex-end', gap: 4 }}>
        <View style={[styles.mineBubble, { backgroundColor: t.surface2 }]}><Markdown text={m.text} compact /></View>
        <T v="caption" color={t.ink3} style={{ fontSize: 11 }}>{time}</T>
      </View>
    );
  }
  const butler = m.who === 'butler';
  const label = butler ? (m.mine ? L('管家 · 你的管家', 'Butler · yours') : L(`管家 · ${m.name}`, `Butler · ${m.name}`)) : m.name;
  return (
    <View style={styles.row}>
      {butler ? (m.mine ? <MyButlerFace size={30} /> : <ButlerFace size={30} tone="grey" />) : <PersonFace name={m.name} size={30} />}
      <View style={{ flex: 1, minWidth: 0, gap: 3, alignItems: 'flex-start' }}>
        <T v="caption" color={butler && m.mine ? t.gold : t.ink2} style={{ fontWeight: '600' }} numberOfLines={1}>
          {label}<T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{` · ${time}`}</T>
        </T>
        <View style={[styles.bubble, { backgroundColor: t.surface, borderColor: butler && m.mine ? t.goldFill : 'transparent' }]}><Markdown text={m.text} compact /></View>
        {consult ? <ConsultLine c={consult} /> : null}
      </View>
    </View>
  );
});

/** 「仅你可见：问了 Victor，只带问题要点」：点开看带去的要点和它怎么说。 */
function ConsultLine({ c }: { c: FGConsult }) {
  const t = useTheme();
  const [open, setOpen] = useState(false);
  return (
    <Pressable onPress={() => setOpen((v) => !v)} accessibilityRole="button" accessibilityState={{ expanded: open }}
      style={[styles.consult, { backgroundColor: t.goldSoft }]}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
        <T v="caption" color={t.gold} style={{ flex: 1 }}>{c.line}</T>
        {open ? <ChevronUp size={13} color={t.gold} /> : <ChevronDown size={13} color={t.gold} />}
      </View>
      {open ? (
        <View style={{ gap: 2, paddingTop: 4 }}>
          <T v="caption" color={t.ink2}>{L(`带去的要点：${c.gist}`, `The gist it took: ${c.gist}`)}</T>
          {c.answer ? <T v="caption" color={t.ink2}>{L(`${c.name} 的看法：${c.answer}`, `${c.name}'s view: ${c.answer}`)}</T> : null}
        </View>
      ) : null}
    </Pressable>
  );
}

/** 挂在管家那一句下面、只有你看得到的卡：「仅你可见 · 管家在问你」+ 收件箱的卡。 */
const OwnerCards = React.memo(function OwnerCards({ items }: { items?: InboxItem[] }) {
  const t = useTheme();
  if (!items?.length) return null;
  const open = items.some((i) => i.status === 'pending');
  return (
    <View style={{ paddingLeft: 38, gap: 6 }}>
      <T v="caption" color={t.ink3}>{open ? L('仅你可见 · 管家在问你', 'Only you can see this · your butler is asking you') : L('仅你可见', 'Only you can see this')}</T>
      {items.map((it) => <InboxCard key={it.id} item={it} variant="chat" />)}
    </View>
  );
});

/** ⓘ：成员（谁带了管家）、你的管家进不进（别人的群；群主的管家一直在）、这个群的规矩。 */
function InfoSheet({ g, close, onChanged }: { g: FriendGroup; close: () => void; onChanged: (g: FriendGroup) => void }) {
  const t = useTheme();
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState(false);
  const me = g.members.find((m) => m.me);
  const toggle = (v: boolean) => {
    setBusy(true);
    friendGroupsApi.setButler(g.id, v).then((r) => { onChanged(r.group); setPending(r.pending); }).catch((e) => showError(L('修改失败', "Couldn't update"), e))
      .finally(() => setBusy(false));
  };
  return (
    <View style={{ gap: space.md }}>
      <View style={{ gap: 2 }}>
        {g.members.map((m) => (
          <View key={m.kid} style={styles.pickRow}>
            <PersonFace name={m.me ? L('你', 'You') : m.name} size={32} />
            <View style={{ flex: 1, gap: 1 }}>
              <T v="body">{m.me ? L('你', 'You') : m.name}{m.owner ? <T v="caption" color={t.ink3}>{L(' · 群主', ' · owner')}</T> : null}</T>
              <T v="caption" color={t.ink3}>{m.butler ? (m.me ? L('带着你的管家', 'With your butler') : L(`带着 ${m.butlerName ?? '管家'}`, `With ${m.butlerName ?? 'a butler'}`)) : L('没带管家', 'No butler')}</T>
            </View>
          </View>
        ))}
      </View>
      {g.mine ? (
        <T v="caption" color={t.ink3}>{L('你是群主：你的管家主持这个群，一直在。', 'You own this group: your butler hosts it and stays in.')}</T>
      ) : (
        <View style={[styles.switchRow, { backgroundColor: t.surface }]}>
          <View style={{ flex: 1, gap: 2 }}>
            <T v="body">{L('你的管家进这个群', 'Your butler joins')}</T>
            <T v="caption" color={t.ink3}>{pending ? L('已告诉群主，等群主更新成员表。', 'Sent to the owner; waiting for the updated member list.')
              : L('进来以后有人 @ 它才答；要你定的先出卡给你。', 'Once in, it answers only when named; anything you need to decide comes to you first.')}</T>
          </View>
          <Switch value={!!me?.butler} disabled={busy} onValueChange={toggle} trackColor={{ true: t.goldFill, false: t.line }} thumbColor={t.surface}
            accessibilityLabel={L('你的管家进这个群', 'Your butler joins')} />
        </View>
      )}
      <T v="caption" color={t.ink3}>{g.note}</T>
      <Pressable onPress={close} accessibilityRole="button" style={{ alignSelf: 'center', padding: space.sm }}><T v="callout" color={t.ink2}>{L('关闭', 'Close')}</T></Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  note: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: space.lg, paddingVertical: 8, borderBottomWidth: StyleSheet.hairlineWidth },
  row: { flexDirection: 'row', gap: space.sm, alignItems: 'flex-start' },
  bubble: { borderRadius: 16, borderTopLeftRadius: 4, paddingHorizontal: 12, paddingVertical: 9, maxWidth: '100%', borderWidth: 1 },
  mineBubble: { maxWidth: '82%', borderRadius: radius.lg, borderBottomRightRadius: 6, paddingHorizontal: 14, paddingVertical: 9 },
  consult: { borderRadius: radius.md, paddingHorizontal: 10, paddingVertical: 6, maxWidth: '100%' },
  composerWrap: { borderTopWidth: StyleSheet.hairlineWidth },
  tools: { flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingHorizontal: space.md, paddingTop: space.sm },
  toolBtn: { flexDirection: 'row', alignItems: 'center', gap: 4, height: 28, paddingHorizontal: 10, borderRadius: 14 },
  pickRow: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: 8 },
  switchRow: { flexDirection: 'row', alignItems: 'center', gap: space.md, borderRadius: radius.md, padding: space.md },
});
