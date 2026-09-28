// agent 之间（社交第三层，../../server/cardagent.py + a2a.py，设计稿 SocAgents）：和一个朋友之间，两边 agent 替你们说过的话。
// 从朋友聊天右上角「…」进来。一轮 = 一段对话：对方的 agent 来问（card_log 里 channel a2a 的，按 ref 分），
// 或者你的名片 agent 去问对方（a2a_out，按 contextId 分）。出给你的卡挂在出卡的那句下面（收件箱里还在就能直接点），
// 最后一轮对方来问的紧下面是「这次它说出去的」。聊天里对着分享的追问，代答在聊天里（能看、能改、能收回），这里不重复。
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Platform, Pressable, ScrollView, StyleSheet, TextInput, View,
  type NativeSyntheticEvent, type TextInputKeyPressEventData } from 'react-native';
import { useFocusEffect, useNavigation, useRoute } from '@react-navigation/native';
import * as fr from '../api/friends';
import type { A2AOut, CardAsk, CardLogItem, CardSettings, Friend, ScopeKey } from '../api/friends';
import { InboxCard } from '../components/InboxCard';
import { ArrowUp, Ban, ChevronRight, IdCard, X } from '../components/icons';
import { ChatScroll, KeyboardSticky, dismissMode, useBottomInset } from '../components/keyboard';
import { Card, NavHeader, Pill, PullRefresh, Screen, T, showError } from '../components/ui';
import { L } from '../i18n';
import { useStore } from '../store';
import { radius, space, type, useTheme } from '../theme';
import { AgentLens, timeLabel } from '../components/FriendBits';

type Round =
  | { kind: 'in'; key: string; at: number; items: CardLogItem[] }
  | { kind: 'out'; key: string; at: number; items: A2AOut[] };

const ms = (iso: string) => {
  const n = new Date(iso).getTime();
  return Number.isNaN(n) ? 0 : n;
};

/** 按对话分成一轮一轮，早的在前（服务器给的是新的在前）。 */
function toRounds(log: CardLogItem[], out: A2AOut[]): Round[] {
  const ins = new Map<string, CardLogItem[]>();
  for (const it of [...log].reverse()) ins.set(it.ref || it.id, [...(ins.get(it.ref || it.id) ?? []), it]);
  const outs = new Map<string, A2AOut[]>();
  for (const o of [...out].reverse()) outs.set(o.contextId || o.id, [...(outs.get(o.contextId || o.id) ?? []), o]);
  const rounds: Round[] = [
    ...[...ins].map(([key, items]): Round => ({ kind: 'in', key: `in:${key}`, at: ms(items[0].ts), items })),
    ...[...outs].map(([key, items]): Round => ({ kind: 'out', key: `out:${key}`, at: ms(items[0].createdAt), items })),
  ];
  return rounds.sort((a, b) => a.at - b.at);
}

/** 「只给了忙闲」这类小标签；老记录没有，就按资料 id 写「用了：日程」。 */
function usedLine(it: CardLogItem): string {
  if (it.usedLabel) return it.usedLabel;
  const names = it.used.map((u) => (u === 'calendar' ? L('日程', 'calendar') : u === 'status' ? L('近况', "what's new") : u === 'address' ? L('住址', 'address')
    : u.startsWith('share:') ? L('分享过的东西', 'a share') : u));
  return names.length ? L(`用了：${names.join('、')}`, `Used: ${names.join(', ')}`) : '';
}

/** 服务端拦下它原本要说的一句时，为什么。 */
function blockedLine(b: string[]): string {
  if (b.some((x) => x.startsWith('leak'))) return L('原话里有这一档没放出来的东西，没发出去，换成了「得问本人」', "The original had something this tier doesn't get; it was replaced with \"ask them directly\"");
  if (b.includes('commit')) return L('原话像是替你答应了，改成了「我去问一下」，并出了卡给你', 'The original sounded like a yes on your behalf; it became "I\'ll ask" and a card for you');
  if (b.includes('empty')) return L('它没给出回答，换成了一句固定的话', 'It gave no answer; a fixed line went instead');
  return '';
}

function askOutcome(a: CardAsk): { label: string; tone: 'good' | 'gold' | 'neutral' } {
  switch (a.outcome) {
    case 'accepted': return { label: L('你同意了', 'You said yes'), tone: 'good' };
    case 'declined': return { label: L('你没去', 'You said no'), tone: 'neutral' };
    case 'counter': return { label: L('你想换个时间', 'You asked for another time'), tone: 'neutral' };
    case 'ack': return { label: L('你看到了，会自己回', "You saw it and will reply yourself"), tone: 'neutral' };
    case 'private_declined': return { label: L('你没说', "You didn't share it"), tone: 'neutral' };
    default: break;
  }
  if (a.status === 'withdrawn') return { label: L('对方撤回了', 'They withdrew it'), tone: 'neutral' };
  const day = a.proposal?.date ? ms(`${a.proposal.date}T23:59:59`) : 0;
  return day && day < new Date().getTime() ? { label: L('过期了，没点', 'Expired'), tone: 'neutral' } : { label: L('等你点头', 'Waiting for you'), tone: 'gold' };
}

/** 你的名片 agent 去问以后，对方那边到哪一步了。 */
export function outState(o: A2AOut, name: string): { label: string; tone: 'good' | 'gold' | 'warn' | 'neutral' } | null {
  switch (o.outcome) {
    case 'accepted': return { label: L(`${name} 同意了`, `${name} said yes`), tone: 'good' };
    case 'declined': return { label: L(`${name} 这次去不了`, `${name} can't make it`), tone: 'neutral' };
    case 'counter': return { label: L(`${name} 想换个时间`, `${name} wants another time`), tone: 'warn' };
    case 'ack': return { label: L(`${name} 看到了，会自己回你`, `${name} saw it and will reply`), tone: 'neutral' };
    case 'private_declined': return { label: L(`${name} 不方便说`, `${name} would rather not say`), tone: 'neutral' };
    case 'expired': return { label: L(`${name} 没来得及回`, `${name} didn't get to it`), tone: 'neutral' };
    default: break;
  }
  switch (o.state) {
    case 'TASK_STATE_AUTH_REQUIRED': return { label: L(`等 ${name} 本人点头`, `Waiting for ${name}`), tone: 'gold' };
    case 'TASK_STATE_INPUT_REQUIRED': return { label: L('轮到你这边再提', 'Your turn to suggest'), tone: 'warn' };
    case 'TASK_STATE_REJECTED': return { label: L(`${name} 那边没接`, `${name}'s side declined`), tone: 'neutral' };
    case 'TASK_STATE_CANCELED': return { label: L('取消了', 'Canceled'), tone: 'neutral' };
    case 'TASK_STATE_FAILED': return { label: L('没办成', "Didn't work"), tone: 'neutral' };
    default: return null;
  }
}

/** 这一档名片 agent 看不到的（「这次它说出去的」最后一行）：按「我的名片 agent」里现在的设置，健康和世界树哪一档都没有。 */
function hiddenFor(card: CardSettings | null, tier: string): string[] {
  const s = card?.tiers[tier as keyof CardSettings['tiers']] as Record<ScopeKey, string> | undefined;
  const out: string[] = [];
  if (s?.calendar === 'none') out.push(L('日程', 'your calendar'));
  else if (s?.calendar === 'busy') out.push(L('日程的具体内容', "what's in your calendar"));
  if (s?.status === 'none') out.push(L('近况', "what you're up to"));
  if (s?.address === 'none') out.push(L('住址', 'your address'));
  return [...out, L('健康和身体', 'health and body'), L('世界树', 'your memory tree')];
}

// —— 一句一句 ——

function TheirLine({ name, text, chip }: { name: string; text: string; chip?: string }) {
  const t = useTheme();
  return (
    <View style={styles.line}>
      <AgentLens mine={false} />
      <View style={[styles.bubble, { backgroundColor: t.surface, borderColor: t.line }]}>
        <T v="caption" color={t.tints.pink.fg} style={styles.who}>{L(`${name} 的 agent`, `${name}'s agent`)}</T>
        <T v="body" selectable>{text}</T>
        {chip ? <View style={styles.chips}><Pill label={chip} tone="good" /></View> : null}
      </View>
    </View>
  );
}

function MineLine({ it }: { it: CardLogItem }) {
  const t = useTheme();
  const [open, setOpen] = useState(false);
  const owner = it.by === 'owner';
  const gone = it.status === 'retracted' || it.status === 'replaced';
  const used = owner ? '' : usedLine(it);
  const why = blockedLine(it.blocked);
  return (
    <View style={styles.line}>
      <AgentLens mine />
      <View style={[styles.bubble, { backgroundColor: t.goldSoft, borderColor: t.goldSoft }]}>
        <T v="caption" color={t.gold} style={styles.who}>{owner ? L('你点的 · 名片 agent 替你转告', 'Your call · passed on by your card agent') : L('你的名片 agent', 'Your card agent')}</T>
        {gone ? <T v="callout" color={t.ink3}>{L('你收回了这条', 'You withdrew this')}</T> : <T v="body" selectable>{it.text}</T>}
        {used || it.status === 'limited' || it.status === 'failed' ? (
          <View style={styles.chips}>
            {used ? <Pill label={used} tone="good" /> : null}
            {it.status === 'limited' ? <Pill label={L('到了今天的上限', "Today's limit reached")} tone="warn" /> : null}
            {it.status === 'failed' ? <Pill label={L('没送到对方', "Didn't reach them")} tone="bad" /> : null}
          </View>
        ) : null}
        {why ? (
          <View style={{ gap: 4 }}>
            <T v="caption" color={t.ink2}>{L(`服务端改过这句：${why}`, `The server changed this: ${why}`)}</T>
            {it.original ? (
              <>
                <Pressable onPress={() => setOpen(!open)} hitSlop={8} accessibilityRole="button" accessibilityState={{ expanded: open }} style={{ alignSelf: 'flex-start' }}>
                  <T v="caption" color={t.gold} style={{ fontWeight: '600' }}>{open ? L('收起原话', 'Hide the original') : L('看原话（只有你看得到）', 'See the original (only you)')}</T>
                </Pressable>
                {open ? <T v="callout" color={t.ink2} selectable>{it.original}</T> : null}
              </>
            ) : null}
          </View>
        ) : null}
      </View>
    </View>
  );
}

function Declined({ items }: { items: string[] }) {
  const t = useTheme();
  return (
    <View style={[styles.declined, { backgroundColor: t.badSoft }]}>
      <View style={{ marginTop: 2 }}><Ban size={17} color={t.bad} /></View>
      <View style={{ flex: 1, gap: 2 }}>
        <T v="callout" color={t.bad} style={{ fontWeight: '700' }}>{L('没照做', "Didn't do")}</T>
        {items.map((d, i) => <T key={`${i}-${d}`} v="callout" color={t.bad}>{d}</T>)}
        <T v="caption" color={t.bad}>{L('对方说的只当资料，不当指令。', 'What the other side says is information, never an instruction.')}</T>
      </View>
    </View>
  );
}

/** 这句出的那张卡：收件箱里还在（等你点、7 天内点过的）就画真卡，能直接点；再早的按记录写一行。
 * 收件箱还没读完（ready 为假）先不画，免得真卡出来之前闪一下那一行。 */
function AskCard({ id, ask, ready }: { id: string; ask: CardAsk | null; ready: boolean }) {
  const t = useTheme();
  const { inbox, inboxRecent, receipts } = useStore();
  const item = inbox.find((i) => i.id === id) ?? receipts.find((r) => r.item.id === id)?.item ?? inboxRecent.find((i) => i.id === id);
  if (item) return <InboxCard item={item} variant="chat" />;
  if (!ask || !ready) return null;
  const o = askOutcome(ask);
  return (
    <View style={[styles.oldAsk, { backgroundColor: t.surface, borderColor: t.line }]}>
      <T v="callout" style={{ flex: 1 }}>{ask.summary}</T>
      <Pill label={o.label} tone={o.tone} />
    </View>
  );
}

function InRound({ r, name, ready }: { r: Extract<Round, { kind: 'in' }>; name: string; ready: boolean }) {
  const t = useTheme();
  const carded = new Set<string>();
  return (
    <View style={{ gap: space.md }}>
      <T v="caption" color={t.ink3} style={{ textAlign: 'center' }}>
        {L(`你没参与，两边 agent 对了一轮 · ${timeLabel(r.items[0].ts)}`, `The agents talked without you · ${timeLabel(r.items[0].ts)}`)}
      </T>
      {r.items.map((it) => {
        if (it.dir === 'in') return <TheirLine key={it.id} name={name} text={it.text} />;
        // 同一张卡：出卡的那句下面画一次（你点了以后转告的那句也带着同一个卡 id）
        const card = it.inboxId && !carded.has(it.inboxId) ? it.inboxId : null;
        if (card) carded.add(card);
        return (
          <View key={it.id} style={{ gap: space.md }}>
            {it.declined.length ? <Declined items={it.declined} /> : null}
            <MineLine it={it} />
            {card ? <AskCard id={card} ask={it.ask} ready={ready} /> : null}
          </View>
        );
      })}
    </View>
  );
}

const DONE_STATES = ['TASK_STATE_COMPLETED', 'TASK_STATE_FAILED', 'TASK_STATE_CANCELED', 'TASK_STATE_REJECTED'];
/** 还没发出去（app 里先画上）的一条：id 以 local- 开头。 */
export const isLocalOut = (o: A2AOut) => o.state === 'local';
/** 这一问要不要你再说一句：对方本人想换个时间（任务在等我们这边再提）。 */
export const needsMore = (o: A2AOut) => o.state === 'TASK_STATE_INPUT_REQUIRED' || (o.outcome === 'counter' && !DONE_STATES.includes(o.state ?? ''));

/** 走到哪一步：问了 → 对方的 agent 回了 → 对方本人定了（只有要对方本人表态、建了任务的才有第三步）。 */
function Steps({ o, name }: { o: A2AOut; name: string }) {
  const t = useTheme();
  const decided = !!o.outcome || DONE_STATES.includes(o.state ?? '');
  const steps = [
    { key: 'ask', label: L('问了', 'Asked'), done: true, now: false },
    { key: 'reply', label: L('对方 agent 回了', 'Their agent replied'), done: true, now: false },
    { key: 'owner', label: L(`${name} 本人定`, `${name} decides`), done: decided, now: !decided },
  ];
  return (
    <View style={styles.steps} accessible accessibilityLabel={steps.map((x) => `${x.label}${x.done ? L('（到了）', ' (done)') : L('（还没）', ' (not yet)')}`).join('，')}>
      {steps.map((x, i) => (
        <React.Fragment key={x.key}>
          {i ? <View style={[styles.stepLine, { backgroundColor: x.done ? t.good : t.line }]} /> : null}
          <View style={styles.step}>
            <View style={[styles.stepDot, x.done ? { backgroundColor: t.good, borderColor: t.good } : { borderColor: x.now ? t.gold : t.line }]} />
            <T v="caption" color={x.done ? t.ink2 : x.now ? t.gold : t.ink3} style={x.now ? { fontWeight: '700' } : undefined}>{x.label}</T>
          </View>
        </React.Fragment>
      ))}
    </View>
  );
}

/** 两个透镜叠在一起（你的名片 agent 和对方的）。 */
function PairLens({ bg, size = 22 }: { bg: string; size?: number }) {
  return (
    <View style={{ width: size * 1.6, height: size + 2 }}>
      <View style={{ position: 'absolute', left: 0, top: 1 }}><AgentLens mine size={size} /></View>
      <View style={{ position: 'absolute', left: size * 0.6 - 1, top: 0, width: size + 2, height: size + 2, borderRadius: size / 2 + 1, backgroundColor: bg,
        alignItems: 'center', justifyContent: 'center' }}><AgentLens mine={false} size={size} /></View>
    </View>
  );
}

/** 你的名片 agent 去问朋友的 agent 的一条：你问的、对方 agent 回的（带它说用了什么）、走到哪一步、对方本人的决定。
 * 聊天里（onOpen 进「agent 之间」）和「agent 之间」页都用它。对方想换时间时 onMore =「再提一个时间」。 */
export function AskOutCard({ o, name, onOpen, onMore }: { o: A2AOut; name: string; onOpen?: () => void; onMore?: () => void }) {
  const t = useTheme();
  const local = isLocalOut(o);
  const st = local ? null : outState(o, name);
  const more = !local && !!onMore && needsMore(o);
  return (
    <Pressable onPress={onOpen} disabled={!onOpen} accessibilityRole={onOpen ? 'button' : undefined}
      accessibilityHint={onOpen ? L('打开「agent 之间」', 'Opens Agent to agent') : undefined}
      style={({ pressed }) => [styles.askCard, { backgroundColor: t.surface, borderColor: t.goldSoft, opacity: pressed && onOpen ? 0.8 : 1 }]}>
      <View style={styles.askHead}>
        <PairLens bg={t.surface} />
        <T v="caption" color={t.gold} style={{ flex: 1, fontWeight: '700' }} numberOfLines={1}>{L(`你的名片 agent 去问 ${name} 的 agent`, `Your card agent asked ${name}'s agent`)}</T>
        {o.createdAt && !local ? <T v="caption" color={t.ink3}>{timeLabel(o.createdAt)}</T> : null}
        {onOpen ? <ChevronRight size={14} color={t.ink3} /> : null}
      </View>
      <T v="body" selectable>{o.text}</T>
      {local ? (
        <View style={styles.waitRow}>
          <ActivityIndicator size="small" color={t.gold} />
          <T v="caption" color={t.ink3}>{L(`等 ${name} 的 agent 回…（它要想一下）`, `Waiting for ${name}'s agent… (it takes a moment)`)}</T>
        </View>
      ) : o.reply ? (
        <View style={styles.replyRow}>
          <AgentLens mine={false} size={22} />
          <View style={[styles.replyBubble, { backgroundColor: t.bg, borderColor: t.line }]}>
            <T v="caption" color={t.tints.pink.fg} style={{ fontWeight: '700' }}>{L(`${name} 的 agent`, `${name}'s agent`)}</T>
            <T v="callout" selectable>{o.reply}</T>
            {o.usedLabel ? <View style={{ flexDirection: 'row' }}><Pill label={o.usedLabel} tone="good" /></View> : null}
          </View>
        </View>
      ) : null}
      {o.taskId && !local ? <Steps o={o} name={name} /> : null}
      {st || more ? (
        <View style={styles.askFoot}>
          {st ? <Pill label={st.label} tone={st.tone} /> : null}
          <View style={{ flex: 1 }} />
          {more ? (
            <Pressable onPress={onMore} accessibilityRole="button" hitSlop={6} style={({ pressed }) => [styles.moreBtn, { backgroundColor: t.goldSoft, opacity: pressed ? 0.7 : 1 }]}>
              <T v="callout" color={t.gold} style={{ fontWeight: '700' }}>{L('再提一个时间', 'Suggest another time')}</T>
            </Pressable>
          ) : null}
        </View>
      ) : null}
    </Pressable>
  );
}

function OutRound({ r, name, onMore }: { r: Extract<Round, { kind: 'out' }>; name: string; onMore: (o: A2AOut) => void }) {
  const t = useTheme();
  return (
    <View style={{ gap: space.md }}>
      <T v="caption" color={t.ink3} style={{ textAlign: 'center' }}>
        {L(`你的名片 agent 去问了 ${name} 的 agent · ${timeLabel(r.items[0].createdAt)}`, `Your card agent asked ${name}'s agent · ${timeLabel(r.items[0].createdAt)}`)}
      </T>
      {r.items.map((o, i) => (
        // 同一件事里只有最后一条能「再提一个时间」
        <AskOutCard key={o.id} o={o} name={name} onMore={i === r.items.length - 1 ? () => onMore(o) : undefined} />
      ))}
    </View>
  );
}

/** 最后一轮对方来问的下面：这次它说出去了什么、没照做什么、这一档它看不到什么。 */
function SaidCard({ r, friend, card }: { r: Extract<Round, { kind: 'in' }>; friend: Friend; card: CardSettings | null }) {
  const t = useTheme();
  const said = r.items.filter((i) => i.dir === 'out' && i.by === 'agent' && i.status === 'sent');
  const used = [...new Set(said.map(usedLine).filter(Boolean))];
  const asks = new Set(said.map((i) => i.inboxId).filter(Boolean)).size;
  const declined = [...new Set(r.items.flatMap((i) => i.declined))];
  const rows: { tone: string; text: string }[] = [
    ...(used.length ? used.map((u) => ({ tone: t.good, text: u })) : [{ tone: t.good, text: L('没用你的任何资料', 'None of your information') }]),
    ...(asks ? [{ tone: t.gold, text: L(`要你定的出了 ${asks} 张卡，没替你答应`, `${asks} card${asks === 1 ? '' : 's'} for you; it agreed to nothing`) }] : []),
    ...(declined.length ? [{ tone: t.bad, text: L(`没照做：${declined.join('；')}`, `Didn't do: ${declined.join('; ')}`) }] : []),
    { tone: t.bad, text: L(`「${friend.tierName}」这一档它看不到：${hiddenFor(card, friend.tier).join('、')}`, `At "${friend.tierName}" it can't see: ${hiddenFor(card, friend.tier).join(', ')}`) },
  ];
  return (
    <Card style={{ gap: space.sm, padding: space.md }}>
      <T v="label" color={t.ink3}>{L('这次它说出去的', 'What it said this time')}</T>
      {rows.map((x, i) => (
        <View key={i} style={styles.said}>
          <View style={[styles.dot, { backgroundColor: x.tone }]} />
          <T v="callout" style={{ flex: 1 }}>{x.text}</T>
        </View>
      ))}
      <T v="caption" color={t.ink3}>{L('说出去的每一句都记进了活动记录', 'Every line it said is in Activity')}</T>
    </Card>
  );
}

export function FriendAgentsScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const route = useRoute<any>();
  const id = route.params?.id as string;
  const { reload } = useStore();
  const [friend, setFriend] = useState<Friend | null>(null);
  const [log, setLog] = useState<CardLogItem[] | null>(null);
  const [out, setOut] = useState<A2AOut[]>([]);
  const [card, setCard] = useState<CardSettings | null>(null);
  const [inboxReady, setInboxReady] = useState(false);
  const [err, setErr] = useState('');
  const [draft, setDraft] = useState('');
  const [prev, setPrev] = useState<A2AOut | null>(null);  // 接着哪一件事说（对方想换个时间）
  const [pend, setPend] = useState<A2AOut[]>([]);          // 发出去、还在等对方 agent 回的
  const scroller = useRef<ScrollView>(null);
  const input = useRef<TextInput>(null);
  const root = useRef<View>(null);
  const bottom = useBottomInset(root);

  // refresh=true：还在等对方本人点头的，服务器顺手问一下对方到哪了（一分钟最多一次）
  const refresh = useCallback(() => Promise.all([fr.cardLog(id), fr.a2aOut(id, true).catch(() => [] as A2AOut[])])
    .then(([l, o]) => { setLog(l); setOut(o); setErr(''); }), [id]);
  const load = useCallback(() => Promise.all([
    fr.home().then((h) => setFriend(h.friends.find((f) => f.id === id) ?? null)),
    fr.card().then(setCard).catch(() => {}),
    refresh(),
    reload('inbox', 'inboxRecent').catch(() => {}).finally(() => setInboxReady(true)),  // 7 天内点过的卡画成回执
  ]).catch((e) => setErr(e instanceof Error ? e.message : String(e))), [id, refresh, reload]);
  useEffect(() => { load(); }, [load]);
  // 开着的时候每 8 秒看一眼：对方的 agent 又来问了、你在卡上点了以后转告的那句
  useFocusEffect(useCallback(() => {
    const h = setInterval(() => { refresh().catch(() => {}); }, 8000);
    return () => clearInterval(h);
  }, [refresh]));

  const rounds = toRounds(log ?? [], [...pend, ...out]);
  const lastIn = [...rounds].reverse().find((r): r is Extract<Round, { kind: 'in' }> => r.kind === 'in');
  const n = rounds.length + pend.length;
  useEffect(() => {
    if (!n) return undefined;
    const h = setTimeout(() => scroller.current?.scrollToEnd({ animated: false }), 80);
    return () => clearTimeout(h);
  }, [n]);

  const name = friend?.name ?? L('朋友', 'Friend');
  const canAsk = !!friend && friend.status === 'active' && friend.caps.includes('a2a');
  const ask = () => {
    const text = draft.trim();
    if (!text || !friend) return;
    const p = prev;
    const tmp: A2AOut = { id: `local-${Date.now()}`, friend: friend.id, contextId: p?.contextId ?? null, taskId: p?.taskId ?? null, state: 'local',
      text, reply: null, outcome: '', usedLabel: '', createdAt: new Date().toISOString(), updatedAt: '' };
    setPend((c) => [...c, tmp]);
    setDraft('');
    setPrev(null);
    fr.a2aSend(friend.id, text, p)
      .then((o) => setOut((c) => [o, ...c.filter((x) => x.id !== o.id)]))
      .catch((e) => { setDraft(text); setPrev(p); showError(L('没问成', "Couldn't ask"), e); })
      .finally(() => setPend((c) => c.filter((x) => x.id !== tmp.id)));
  };
  const more = (o: A2AOut) => { setPrev(o); setTimeout(() => input.current?.focus(), 50); };
  const webEnter = Platform.OS === 'web' ? (e: NativeSyntheticEvent<TextInputKeyPressEventData>) => {
    const k = e.nativeEvent as unknown as KeyboardEvent;
    if (k.key !== 'Enter' || k.shiftKey || k.isComposing || k.keyCode === 229) return;
    e.preventDefault();
    ask();
  } : undefined;
  return (
    <Screen>
      <NavHeader title={L(`${name} · agent 之间`, `${name} · Agents`)} sub={L('A2A · 对方身份已核对', 'A2A · identity verified')} onBack={() => nav.goBack()} />
      <View ref={root} style={{ flex: 1 }} onLayout={bottom.onLayout}>
      <ChatScroll ref={scroller} offset={bottom.offset} style={{ flex: 1 }} contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl, gap: space.lg }}
        keyboardShouldPersistTaps="handled" keyboardDismissMode={dismissMode} refreshControl={<PullRefresh onRefresh={load} />}>
        {err ? <Card><T v="callout" color={t.bad}>{L(`读不到：${err}`, `Couldn't load: ${err}`)}</T></Card> : null}
        {!log && !err ? <ActivityIndicator color={t.gold} style={{ marginTop: space.xl }} /> : null}
        {friend ? (
          <View style={styles.pair}>
            <View style={{ width: 46, height: 30 }}>
              <View style={{ position: 'absolute', left: 0, top: 1 }}><AgentLens mine /></View>
              <View style={[styles.lensRing, { left: 16, backgroundColor: t.bg }]}><AgentLens mine={false} /></View>
            </View>
            <View style={{ flex: 1, gap: 2 }}>
              <T v="callout" style={{ fontWeight: '600' }}>{L(`你的名片 agent 和 ${name} 的 agent`, `Your card agent and ${name}'s agent`)}</T>
              <T v="caption" color={t.ink3}>{L(`${name} 在「${friend.tierName}」这一档 · 指纹 ${friend.fingerprint}`, `${name} is at "${friend.tierName}" · fingerprint ${friend.fingerprint}`)}</T>
            </View>
          </View>
        ) : null}
        {log && !n ? (
          <Card style={{ gap: space.sm }}>
            <T v="headline">{L('还没有来往', 'Nothing yet')}</T>
            <T v="callout" color={t.ink2}>{L(`${name} 的 agent 来问你（比如约个时间），或者你的名片 agent 替你去问 ${name} 的，都记在这里；要你定的会出一张卡，等你点头。`,
              `When ${name}'s agent asks you something (say, a time to meet), or your card agent asks ${name}'s, it shows here; anything that needs your say becomes a card for you.`)}</T>
            <T v="callout" color={t.ink2}>{L(`${name} 在聊天里对着你的分享追问，代答在聊天里。`, `When ${name} asks about your shares in the chat, the answers stay in the chat.`)}</T>
          </Card>
        ) : null}
        {friend ? rounds.map((r) => (r.kind === 'out' ? <OutRound key={r.key} r={r} name={name} onMore={more} /> : (
          <View key={r.key} style={{ gap: space.lg }}>
            <InRound r={r} name={name} ready={inboxReady} />
            {r === lastIn ? <SaidCard r={r} friend={friend} card={card} /> : null}
          </View>
        ))) : null}
        {friend ? (
          <Pressable onPress={() => nav.navigate('CardAgent')} accessibilityRole="button" style={({ pressed }) => [styles.link, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
            <View style={[styles.linkIcon, { backgroundColor: t.goldSoft }]}><IdCard size={17} color={t.gold} /></View>
            <T v="callout" style={{ flex: 1, fontWeight: '600' }}>{L('改名片：谁能问到什么', 'Edit your card: who can ask what')}</T>
            <ChevronRight size={16} color={t.ink3} />
          </Pressable>
        ) : null}
      </ChatScroll>
      {canAsk ? (
        <KeyboardSticky offset={bottom.offset} style={[styles.composerWrap, { borderTopColor: t.line, backgroundColor: t.bg }]}>
          <View style={{ paddingHorizontal: space.md, paddingTop: space.sm }}>
            <View style={[styles.quote, { backgroundColor: t.goldSoft }]}>
              <AgentLens mine size={16} />
              <T v="callout" numberOfLines={2} style={{ flex: 1, fontSize: 13 }}>
                {prev ? L(`接着说那件事：「${prev.text}」`, `Continuing: "${prev.text}"`)
                  : L(`你的名片 agent 替你去问 ${name} 的 agent；要 ${name} 本人定的，对方会去问 ${name}`, `Your card agent asks ${name}'s agent for you; anything ${name} has to decide goes to ${name}`)}
              </T>
              {prev ? (
                <Pressable onPress={() => setPrev(null)} hitSlop={10} accessibilityRole="button" accessibilityLabel={L('不接着说了', 'Start a new question')}>
                  <X size={14} color={t.ink3} />
                </Pressable>
              ) : null}
            </View>
          </View>
          <View style={styles.composer}>
            <TextInput ref={input} value={draft} onChangeText={setDraft} multiline numberOfLines={1} onKeyPress={webEnter}
              placeholder={prev ? L('比如：那周五晚上呢？', 'e.g. How about Friday evening?') : L(`比如：${name} 这周哪天晚上有空？`, `e.g. Which evenings is ${name} free this week?`)}
              placeholderTextColor={t.ink3} accessibilityLabel={L('要名片 agent 去问的话', 'What your card agent should ask')}
              style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink }]} />
            <Pressable onPress={ask} disabled={!draft.trim()} accessibilityRole="button" accessibilityLabel={L('让它去问', 'Ask')}
              style={[styles.send, { backgroundColor: draft.trim() ? t.goldFill : t.surface2 }]}>
              <ArrowUp size={20} color={draft.trim() ? t.onGold : t.ink3} />
            </Pressable>
          </View>
        </KeyboardSticky>
      ) : null}
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  pair: { flexDirection: 'row', alignItems: 'center', gap: space.md },
  lensRing: { position: 'absolute', top: 0, width: 30, height: 30, borderRadius: 15, alignItems: 'center', justifyContent: 'center' },
  line: { flexDirection: 'row', gap: 10, alignItems: 'flex-start' },
  bubble: { flex: 1, minWidth: 0, gap: 5, borderWidth: StyleSheet.hairlineWidth, borderRadius: 16, borderTopLeftRadius: 6, paddingHorizontal: 12, paddingVertical: 10 },
  who: { fontWeight: '700' },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, alignItems: 'center' },
  declined: { flexDirection: 'row', gap: 10, alignItems: 'flex-start', borderRadius: radius.md, paddingHorizontal: 12, paddingVertical: 10 },
  oldAsk: { flexDirection: 'row', alignItems: 'center', gap: space.sm, borderWidth: StyleSheet.hairlineWidth, borderRadius: radius.md, padding: space.md },
  said: { flexDirection: 'row', gap: 8, alignItems: 'flex-start' },
  dot: { width: 6, height: 6, borderRadius: 3, marginTop: 8 },
  link: { flexDirection: 'row', alignItems: 'center', gap: space.md, borderRadius: radius.lg, padding: space.md },
  askCard: { borderWidth: 1, borderRadius: 16, padding: space.md, gap: 8 },
  askHead: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  waitRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  replyRow: { flexDirection: 'row', gap: 8, alignItems: 'flex-start' },
  replyBubble: { flex: 1, minWidth: 0, gap: 4, borderWidth: StyleSheet.hairlineWidth, borderRadius: 14, borderTopLeftRadius: 5, paddingHorizontal: 10, paddingVertical: 8 },
  steps: { flexDirection: 'row', alignItems: 'center', gap: 6, flexWrap: 'wrap' },
  step: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  stepDot: { width: 9, height: 9, borderRadius: 5, borderWidth: 1.5 },
  stepLine: { width: 14, height: 1.5, borderRadius: 1 },
  askFoot: { flexDirection: 'row', alignItems: 'center', gap: space.sm },
  moreBtn: { height: 32, paddingHorizontal: 12, borderRadius: 16, alignItems: 'center', justifyContent: 'center' },
  composerWrap: { borderTopWidth: StyleSheet.hairlineWidth },
  composer: { flexDirection: 'row', alignItems: 'flex-end', gap: space.sm, paddingHorizontal: space.md, paddingVertical: space.sm },
  input: { flex: 1, minHeight: 40, maxHeight: 120, borderRadius: 20, paddingHorizontal: 16, paddingTop: 9, paddingBottom: 9 },
  send: { width: 40, height: 40, borderRadius: 20, alignItems: 'center', justifyContent: 'center' },
  quote: { flexDirection: 'row', alignItems: 'center', gap: 6, borderRadius: radius.md, paddingHorizontal: 10, paddingVertical: 7 },
  linkIcon: { width: 32, height: 32, borderRadius: 10, alignItems: 'center', justifyContent: 'center' },
});
