// 朋友（社交第二层，../../server/friends.py）：「对话」页切到「朋友」时的列表（FriendsHome）、和一个朋友的聊天（FriendChatScreen）、
// 加朋友（AddFriendScreen：给对方一张邀请码 / 用对方给的）、我的名片 agent（CardAgentScreen：谁能问到什么、近况）。
// 两边 agent 之间的来往（第三层）在 FriendAgentsScreen.tsx，从聊天页右上角「…」进。
// 服务器之间的事（签名、投递、重试、名片 agent 代答）都在服务器上；这里只画、只调自己的服务器。
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Alert, Platform, Pressable, ScrollView, Share as NativeShare, StyleSheet, Text, TextInput, View,
  type NativeSyntheticEvent, type TextInputKeyPressEventData } from 'react-native';
import { useNavigation, useRoute } from '@react-navigation/native';
import * as Clipboard from 'expo-clipboard';
import Svg, { Path, Rect } from 'react-native-svg';
import * as fr from '../api/friends';
import type { AnyTier, CardSettings, Friend, FriendMsg, FriendsHome as Home, Invite, ScopeKey, Tier } from '../api/friends';
import { ArrowUp, Ban, Check, ChevronRight, ClipboardPaste, Copy, Ellipsis, IdCard, Link2, QrCode, RotateCw, ShareIcon, ShieldCheck,
  TriangleAlert, Undo2, UserPlus, UserX, X } from '../components/icons';
import { LensAvatar } from '../components/LensAvatar';
import { Markdown } from '../components/Markdown';
import { useSheet } from '../components/Sheet';
import { ChatScroll, KeyboardSticky, dismissMode, useBottomInset } from '../components/keyboard';
import { Btn, Card, CountPill, NavHeader, Pill, PullRefresh, Screen, SectionLabel, Segmented, T, showError } from '../components/ui';
import type { AgentColor } from '../data/types';
import { L } from '../i18n';
import { useStore } from '../store';
import { radius, space, type, useTheme, type Theme } from '../theme';

// —— 小零件 ——

const TINT_ORDER: AgentColor[] = ['pink', 'cyan', 'purple', 'green', 'orange', 'gold'];

function tintOf(t: Theme, id: string) {
  let h = 0;
  for (const ch of id) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return t.tints[TINT_ORDER[h % TINT_ORDER.length]];
}

/** 头像上的字：中文取第一个字，英文取前两个词的首字母。 */
function initials(name: string): string {
  const s = (name || '?').trim();
  if (/^[㐀-鿿]/.test(s)) return s[0];
  const words = s.split(/\s+/).filter(Boolean);
  const second = words[1]?.[0] ?? '';
  // 两个词都是拉丁字母才取两个首字母（「Imperial 同学」只取 I）
  return ((words[0]?.[0] ?? '?') + (/[A-Za-z]/.test(second) ? second : '')).toUpperCase();
}

export function FriendAvatar({ id, name, size = 44 }: { id: string; name: string; size?: number }) {
  const t = useTheme();
  const tint = tintOf(t, id);
  return (
    <View style={{ width: size, height: size, borderRadius: size / 2, backgroundColor: tint.soft, alignItems: 'center', justifyContent: 'center' }}>
      <Text style={{ color: tint.fg, fontWeight: '800', fontSize: Math.round(size * 0.36) }}>{initials(name)}</Text>
    </View>
  );
}

/** 名片 agent 的小透镜：自己的用自己的形象，朋友的是粉紫色的环。 */
export function AgentLens({ mine, size = 28 }: { mine: boolean; size?: number }) {
  const t = useTheme();
  const { avatar } = useStore();
  if (mine) return <LensAvatar size={size} config={avatar} />;
  return (
    <View style={{ width: size, height: size, borderRadius: size / 2, backgroundColor: t.lensField, alignItems: 'center', justifyContent: 'center' }}>
      <View style={{ width: size / 2, height: size / 2, borderRadius: size / 4, borderWidth: 2, borderColor: '#F291BC', borderTopColor: '#B9A4F4' }} />
    </View>
  );
}

function ymd(d: Date) { return `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`; }

/** 列表和气泡上的时间：今天 = 钟点，昨天，今年 = 月/日，更早带年。 */
export function timeLabel(ts: string | null | undefined): string {
  if (!ts) return '';
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return '';
  const now = new Date();
  const yest = new Date(now.getTime() - 86400000);
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  if (ymd(d) === ymd(now)) return hm;
  if (ymd(d) === ymd(yest)) return L(`昨天 ${hm}`, `Yesterday ${hm}`);
  if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}/${d.getDate()}`;
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`;
}

function lastLine(f: Friend): string {
  const x = f.last;
  if (!x) return f.note ? L(`邀请时写的：${f.note}`, `Invite note: ${f.note}`) : L('还没说过话', 'No messages yet');
  if (x.kind === 'system') return x.text;
  const who = x.dir === 'out' ? (x.by === 'agent' ? L('你的名片 agent：', 'Your card agent: ') : L('你：', 'You: ')) : x.by === 'agent' ? L('对方的名片 agent：', 'Their card agent: ') : '';
  const what = x.kind === 'ask' ? (x.dir === 'in' ? L('问了你的名片 agent：', 'Asked your card agent: ') : L('你追问：', 'You asked: ')) : '';
  return `${who}${what}${x.text}`;
}

const confirm = (title: string, body: string, ok: string, go: () => void) => {
  if (Platform.OS === 'web') {
    if (typeof window === 'undefined' || typeof window.confirm !== 'function' || window.confirm(`${title}\n${body}`)) go();
    return;
  }
  Alert.alert(title, body, [{ text: L('取消', 'Cancel'), style: 'cancel' }, { text: ok, style: 'destructive', onPress: go }]);
};

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

const TIER_OPTS = (): { value: Tier; label: string }[] => [
  { value: 'close', label: L('亲近', 'Close') }, { value: 'friend', label: L('朋友', 'Friend') }, { value: 'mate', label: L('同学', 'Classmate') },
];

function NotReady({ why }: { why: Home['why'] }) {
  const t = useTheme();
  return (
    <View style={[styles.warn, { backgroundColor: t.warnSoft }]}>
      <TriangleAlert size={17} color={t.warn} />
      <T v="callout" style={{ flex: 1 }}>
        {why === 'no_name'
          ? L('先在「我 → 身份」设一个称呼：朋友那边显示它。', 'Set the name to call you first (Me → Identity): friends see it.')
          : L('要先有一个外面打得进来的地址（服务器的 share.public_url，安装时那一问）：朋友的服务器要能把消息送回来。', "Your server needs an address people can reach first (share.public_url, the installer's question): friends' servers have to reach you.")}
      </T>
    </View>
  );
}

// —— 「对话」页的「朋友」 ——

export function FriendsHome() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { unread } = useStore();
  const [data, setData] = useState<Home | null>(null);
  const [err, setErr] = useState('');
  const load = useCallback(() => fr.home().then((d) => { setData(d); setErr(''); }).catch((e) => setErr(errText(e))), []);
  useEffect(() => nav.addListener('focus', () => { load(); }), [nav, load]);
  // 未读变了（轮询每 45 秒一次）就重读一遍：谁发来了新的、最后一句
  const sig = JSON.stringify(unread.friends ?? {});
  useEffect(() => { load(); }, [sig, load]);

  const live = (data?.friends ?? []).filter((f) => f.status === 'active');
  const off = (data?.friends ?? []).filter((f) => f.status !== 'active');
  const open = data?.invites.length ?? 0;
  return (
    <ScrollView contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl, gap: space.md }} refreshControl={<PullRefresh onRefresh={load} />}>
      <View style={styles.homeHead}>
        <T v="title" style={{ flex: 1 }}>{L('朋友', 'Friends')}</T>
        <Pressable onPress={() => nav.navigate('AddFriend')} accessibilityRole="button" accessibilityLabel={L('加朋友', 'Add a friend')}
          style={({ pressed }) => [styles.roundBtn, { backgroundColor: t.surface2, opacity: pressed ? 0.7 : 1 }]}>
          <UserPlus size={20} color={t.ink} />
        </Pressable>
      </View>
      {err ? <Card><T v="callout" color={t.bad}>{L(`读不到：${err}`, `Couldn't load: ${err}`)}</T></Card> : null}
      {!data && !err ? <ActivityIndicator color={t.gold} style={{ marginTop: space.xl }} /> : null}
      {data && !data.ready ? <NotReady why={data.why} /> : null}
      {data && !data.friends.length ? (
        <Card style={{ gap: space.sm }}>
          <T v="headline">{L('还没有朋友', 'No friends yet')}</T>
          <T v="callout" color={t.ink2}>{L('加朋友 = 给对方一张邀请码（二维码或链接），或者粘贴对方给你的。之后你们的服务器直接说话，每条消息都带签名。', "Adding a friend = giving them an invite (QR code or link), or pasting theirs. After that your servers talk directly, every message signed.")}</T>
          <T v="callout" color={t.ink2}>{L('没装 OpenMousse 的人，用分享的链接就行。', "For people without OpenMousse, a share link is enough.")}</T>
          {data.ready ? <Btn label={L('加朋友', 'Add a friend')} icon={<UserPlus size={17} color={t.onGold} />} onPress={() => nav.navigate('AddFriend')} /> : null}
        </Card>
      ) : null}
      {live.length ? (
        <Card style={{ paddingVertical: 0, paddingHorizontal: space.md }}>
          {live.map((f, i) => (
            <Pressable key={f.id} onPress={() => nav.navigate('FriendChat', { id: f.id })} accessibilityRole="button"
              style={({ pressed }) => [styles.friendRow, i ? { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line } : null, { opacity: pressed ? 0.7 : 1 }]}>
              <FriendAvatar id={f.id} name={f.name} />
              <View style={{ flex: 1, minWidth: 0, gap: 3 }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                  <T v="headline" numberOfLines={1} style={{ flexShrink: 1 }}>{f.name}</T>
                  {f.agent ? <Pill label={L('有 agent', 'Has an agent')} tone="cyan" /> : null}
                </View>
                <T v="callout" color={f.unread ? t.ink : t.ink2} numberOfLines={1}>{lastLine(f)}</T>
              </View>
              <View style={{ alignItems: 'flex-end', gap: 5, alignSelf: 'flex-start', paddingTop: 2 }}>
                <T v="caption" color={t.ink3}>{timeLabel(f.last?.ts ?? f.createdAt)}</T>
                <CountPill n={f.unread} small />
              </View>
            </Pressable>
          ))}
        </Card>
      ) : null}
      {open ? (
        <Pressable onPress={() => nav.navigate('AddFriend')} accessibilityRole="button" style={({ pressed }) => [styles.linkRow, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
          <QrCode size={18} color={t.ink2} />
          <T v="callout" style={{ flex: 1 }}>{L(`${open} 张邀请码还没人用`, `${open} invite${open === 1 ? '' : 's'} not used yet`)}</T>
          <ChevronRight size={16} color={t.ink3} />
        </Pressable>
      ) : null}
      {data?.friends.length || data?.ready ? (
        <Pressable onPress={() => nav.navigate('CardAgent')} accessibilityRole="button" style={({ pressed }) => [styles.linkRow, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
          <View style={[styles.cardIcon, { backgroundColor: t.goldSoft }]}><IdCard size={17} color={t.gold} /></View>
          <View style={{ flex: 1, gap: 2 }}>
            <T v="headline" style={{ fontSize: 15 }}>{L('我的名片 agent', 'My card agent')}</T>
            <T v="caption" color={t.ink3}>{data?.agent ? L('朋友能问到什么，按人分档', 'What friends can ask about, by tier') : L('还没开：朋友的追问你自己回', "Not on yet: you answer friends' questions yourself")}</T>
          </View>
          <ChevronRight size={16} color={t.ink3} />
        </Pressable>
      ) : null}
      {off.length ? (
        <>
          <SectionLabel>{L('不在朋友里的', 'No longer friends')}</SectionLabel>
          <Card style={{ paddingVertical: 0, paddingHorizontal: space.md }}>
            {off.map((f, i) => (
              <Pressable key={f.id} onPress={() => nav.navigate('FriendChat', { id: f.id })} accessibilityRole="button"
                style={({ pressed }) => [styles.friendRow, i ? { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line } : null, { opacity: pressed ? 0.7 : 1 }]}>
                <FriendAvatar id={f.id} name={f.name} size={36} />
                <View style={{ flex: 1, gap: 2 }}>
                  <T v="body" numberOfLines={1}>{f.name}</T>
                  <T v="caption" color={t.ink3}>{f.status === 'blocked' ? L('拉黑了：他发的你收不到', "Blocked: you won't get their messages") : L('对方把你删了', 'They removed you')}</T>
                </View>
              </Pressable>
            ))}
          </Card>
        </>
      ) : null}
    </ScrollView>
  );
}

// —— 聊天 ——

function mergeMsgs(prev: FriendMsg[], more: FriendMsg[]): FriendMsg[] {
  if (!more.length) return prev;
  const byId = new Map(prev.map((m) => [m.id, m]));
  for (const m of more) byId.set(m.id, m);
  return [...byId.values()].sort((a, b) => a.id - b.id);
}

function StatusLine({ m, onRetry }: { m: FriendMsg; onRetry: () => void }) {
  const t = useTheme();
  if (m.dir !== 'out') return null;
  if (m.status === 'queued') return <T v="caption" color={t.ink3} style={{ alignSelf: 'flex-end' }}>{L('发送中…', 'Sending…')}</T>;
  if (m.status === 'failed') {
    return (
      <Pressable onPress={onRetry} accessibilityRole="button" style={{ alignSelf: 'flex-end', flexDirection: 'row', alignItems: 'center', gap: 4 }}>
        <RotateCw size={12} color={t.bad} />
        <T v="caption" color={t.bad}>{L('没送到 · 点这里重发', 'Not delivered · tap to retry')}</T>
      </Pressable>
    );
  }
  return null;
}

function SharedCard({ m, friend, mine, onAsk }: { m: FriendMsg; friend: Friend; mine: boolean; onAsk?: () => void }) {
  const t = useTheme();
  const [open, setOpen] = useState(false);
  const s = m.share;
  if (!s) return null;
  const gone = m.status === 'revoked';
  return (
    <View style={[styles.shareCard, { backgroundColor: t.surface, borderColor: t.line, alignSelf: mine ? 'flex-end' : 'flex-start' }]}>
      <T v="caption" color={t.cyan} style={{ fontWeight: '600' }}>
        {`${s.kind === 'note' ? L('笔记', 'Note') : s.kind === 'message' ? L('对话里的一段', 'From a chat') : L('分享', 'Shared')}${s.when ? ` · ${s.when}` : ''}`}
      </T>
      {gone ? (
        <T v="callout" color={t.ink3}>{mine ? L('你收回了这条分享', 'You withdrew this share') : L(`${friend.name} 收回了这条分享`, `${friend.name} withdrew this share`)}</T>
      ) : (
        <>
          <T v="headline" style={{ fontSize: 16 }}>{s.title || L('（没有标题）', '(untitled)')}</T>
          {s.quote ? <T v="callout" color={t.ink2}>{`「${s.quote}」`}</T> : null}
          {m.text ? <T v="callout">{m.text}</T> : null}
          {!mine && s.text ? (
            <>
              <Pressable onPress={() => setOpen(!open)} accessibilityRole="button" style={{ alignSelf: 'flex-start' }}>
                <T v="callout" color={t.gold} style={{ fontWeight: '600' }}>{open ? L('收起全文', 'Hide the full text') : L('看全文', 'Read the full text')}</T>
              </Pressable>
              {open ? <Markdown text={s.text} /> : null}
            </>
          ) : null}
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm, alignItems: 'center' }}>
            {s.can_ask ? <Pill label={mine ? L('可以追问', 'Takes questions') : L(`能追问 ${friend.name} 的名片 agent`, `You can ask ${friend.name}'s card agent`)} tone="gold" /> : null}
            {s.link ? <Pill label={L('有链接的人都能看', 'Anyone with the link')} /> : null}
            {!mine && s.can_ask && onAsk ? (
              <Pressable onPress={onAsk} accessibilityRole="button" style={({ pressed }) => [styles.mini, { backgroundColor: t.goldSoft, opacity: pressed ? 0.7 : 1 }]}>
                <T v="callout" color={t.gold} style={{ fontWeight: '700' }}>{L('追问', 'Ask')}</T>
              </Pressable>
            ) : null}
          </View>
        </>
      )}
    </View>
  );
}

function ReviewBox({ m, onReview, onEdit }: { m: FriendMsg; onReview: (a: 'ok' | 'revoke') => void; onEdit: () => void }) {
  const t = useTheme();
  if (m.review === 'pending') {
    return (
      <View style={[styles.review, { borderColor: t.gold, backgroundColor: t.goldSoft }]}>
        <T v="caption" color={t.ink2}>{L('只有你看得到 · 它替你答了一条', 'Only you see this · it answered for you')}</T>
        <View style={{ flexDirection: 'row', gap: space.sm, flexWrap: 'wrap' }}>
          <Pressable onPress={() => onReview('ok')} accessibilityRole="button" style={[styles.mini, { backgroundColor: t.surface }]}>
            <Check size={14} color={t.ink} /><T v="callout" style={{ fontWeight: '600' }}>{L('没问题', 'Fine')}</T>
          </Pressable>
          <Pressable onPress={onEdit} accessibilityRole="button" style={[styles.mini, { backgroundColor: t.surface }]}>
            <T v="callout" style={{ fontWeight: '600' }}>{L('我来改', "I'll rewrite it")}</T>
          </Pressable>
          <Pressable onPress={() => onReview('revoke')} accessibilityRole="button" style={[styles.mini, { backgroundColor: t.surface }]}>
            <Undo2 size={14} color={t.ink} /><T v="callout" style={{ fontWeight: '600' }}>{L('收回', 'Withdraw')}</T>
          </Pressable>
        </View>
      </View>
    );
  }
  const line = m.review === 'ok' ? L('你看过了，这条就这样', "You checked it; it stays") : m.review === 'edited' ? L('你改过这条', 'You rewrote this')
    : m.review === 'revoked' ? L('你收回了，他那边显示「收回了这条」', 'Withdrawn; they see "withdrew this"') : '';
  return line ? <T v="caption" color={m.review === 'ok' ? t.good : t.ink3}>{line}</T> : null;
}

function MsgView({ m, friend, onAsk, onReview, onEdit, onRetry, onLong }: {
  m: FriendMsg; friend: Friend; onAsk: (m: FriendMsg) => void; onReview: (m: FriendMsg, a: 'ok' | 'revoke') => void; onEdit: (m: FriendMsg) => void;
  onRetry: (m: FriendMsg) => void; onLong: (m: FriendMsg) => void;
}) {
  const t = useTheme();
  const mine = m.dir === 'out';
  if (m.kind === 'system') return <T v="caption" color={t.ink3} style={{ textAlign: 'center' }}>{m.text}</T>;
  if (m.kind === 'share') {
    return (
      <Pressable onLongPress={mine ? () => onLong(m) : undefined} delayLongPress={350} style={{ gap: 4 }}>
        <SharedCard m={m} friend={friend} mine={mine} onAsk={friend.status === 'active' ? () => onAsk(m) : undefined} />
        <T v="caption" color={t.ink3} style={{ alignSelf: mine ? 'flex-end' : 'flex-start' }}>{timeLabel(m.ts)}</T>
        <StatusLine m={m} onRetry={() => onRetry(m)} />
      </Pressable>
    );
  }
  if (m.kind === 'answer') {
    // 名片 agent 的代答：我的（替我答朋友）和朋友的（替他答我）；被主人改过的算主人说的
    const byPerson = m.by === 'person';
    const who = mine ? (byPerson ? L('你改过的代答', 'Your rewrite') : L('你的名片 agent · 代答', 'Your card agent · answered for you'))
      : (byPerson ? L(`${friend.name} 改过`, `Edited by ${friend.name}`) : L(`${friend.name} 的名片 agent · 代答`, `${friend.name}'s card agent · answered`));
    const gone = m.status === 'revoked';
    const used = m.usedLabel || (m.used?.length ? L(`只用了：${m.used.join('、')}`, `Used only: ${m.used.join(', ')}`) : '');
    return (
      <View style={{ flexDirection: 'row', gap: space.sm, alignItems: 'flex-start' }}>
        <AgentLens mine={mine} />
        <View style={{ flex: 1, minWidth: 0, gap: 6 }}>
          <T v="caption" color={t.gold} style={{ fontWeight: '700' }}>{who}</T>
          {gone && !mine ? <T v="callout" color={t.ink3}>{L(`${friend.name} 收回了这条`, `${friend.name} withdrew this`)}</T>
            : <T v="body" color={gone ? t.ink3 : t.ink} style={gone ? { textDecorationLine: 'line-through' } : undefined}>{m.text}</T>}
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm, alignItems: 'center' }}>
            {used && !gone ? <T v="caption" color={t.ink3}>{used}</T> : null}
            {m.defer && !gone ? <Pill label={mine ? L('要你本人回', 'Needs you') : L(`得问 ${friend.name} 本人`, `Ask ${friend.name} directly`)} tone="warn" /> : null}
            <T v="caption" color={t.ink3}>{timeLabel(m.ts)}</T>
          </View>
          {mine ? <ReviewBox m={m} onReview={(a) => onReview(m, a)} onEdit={() => onEdit(m)} /> : null}
          <StatusLine m={m} onRetry={() => onRetry(m)} />
        </View>
      </View>
    );
  }
  // text / ask：气泡
  const isAsk = m.kind === 'ask';
  const gone = m.status === 'revoked';
  const bubble = (
    <View style={[styles.bubble, mine ? { alignSelf: 'flex-end', backgroundColor: t.surface2, borderBottomRightRadius: 6 }
      : { alignSelf: 'flex-start', backgroundColor: t.surface, borderBottomLeftRadius: 6 }]}>
      {isAsk ? (
        <View style={[styles.askChip, { backgroundColor: t.goldSoft }]}>
          <AgentLens mine={!mine} size={16} />
          <T v="caption" color={t.gold} style={{ fontWeight: '700' }}>{mine ? L(`问 ${friend.name} 的 agent`, `Asked ${friend.name}'s agent`) : L('问你的名片 agent', 'Asked your card agent')}</T>
        </View>
      ) : null}
      {gone ? <T v="callout" color={t.ink3}>{mine ? L('你收回了这条', 'You withdrew this') : L(`${friend.name} 收回了这条`, `${friend.name} withdrew this`)}</T>
        : <T v="body" selectable>{m.text}</T>}
      {m.edited && !gone ? <T v="caption" color={t.ink3}>{L('改过', 'edited')}</T> : null}
    </View>
  );
  return (
    <View style={{ gap: 3 }}>
      {mine && !gone && !isAsk ? <Pressable onLongPress={() => onLong(m)} delayLongPress={350}>{bubble}</Pressable> : bubble}
      <T v="caption" color={t.ink3} style={{ alignSelf: mine ? 'flex-end' : 'flex-start' }}>{timeLabel(m.ts)}</T>
      <StatusLine m={m} onRetry={() => onRetry(m)} />
    </View>
  );
}

/** 朋友的设置（从聊天页右上角「…」打开）：agent 之间（第三层，FriendAgentsScreen）、档位、备注、指纹、拉黑、删掉。
 * 弹层画在导航器外面，用不了 useNavigation：跳转由聊天页传进来（onAgents）。 */
function FriendSheet({ f, close, onChanged, onGone, onAgents }: {
  f: Friend; close: () => void; onChanged: (f: Friend) => void; onGone: () => void; onAgents: () => void;
}) {
  const t = useTheme();
  const [alias, setAlias] = useState(f.alias ?? '');
  const [tier, setTier] = useState<Tier>(f.tier);
  const run = (p: Promise<Friend>) => p.then((x) => { onChanged(x); }).catch((e) => showError(L('没改成', "Couldn't change it"), e));
  return (
    <View style={{ gap: space.md }}>
      <Pressable onPress={() => { close(); onAgents(); }} accessibilityRole="button"
        style={({ pressed }) => [styles.linkRow, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
        <View style={{ width: 46, height: 30 }}>
          <View style={{ position: 'absolute', left: 0, top: 1 }}><AgentLens mine /></View>
          <View style={[styles.lensRing, { backgroundColor: t.surface }]}><AgentLens mine={false} /></View>
        </View>
        <View style={{ flex: 1, gap: 2 }}>
          <T v="headline" style={{ fontSize: 15 }}>{L('agent 之间', 'Agent to agent')}</T>
          <T v="caption" color={t.ink3}>{L(`两边 agent 替你和 ${f.name} 说过的话、要你点头的卡`, `What the agents said for you and ${f.name}, and cards for you`)}</T>
        </View>
        <ChevronRight size={16} color={t.ink3} />
      </Pressable>
      <View style={{ gap: space.xs }}>
        <T v="label" color={t.ink3}>{L('在哪一档（名片 agent 按这档替你说话）', 'Tier (your card agent answers them by it)')}</T>
        <Segmented<Tier> value={tier} options={TIER_OPTS()} onChange={(v) => { setTier(v); run(fr.patchFriend(f.id, { tier: v })); }} />
        <T v="caption" color={t.ink3}>{L('对方看不到自己在哪一档。每档能问到什么在「我的名片 agent」里定。', "They never see their tier. What each tier gets is set in My card agent.")}</T>
      </View>
      <View style={{ gap: space.xs }}>
        <T v="label" color={t.ink3}>{L('备注（只你自己看）', 'Your name for them (only you see it)')}</T>
        <View style={{ flexDirection: 'row', gap: space.sm, alignItems: 'center' }}>
          <TextInput value={alias} onChangeText={setAlias} placeholder={f.cardName} placeholderTextColor={t.ink3}
            style={[type.body, styles.field, { flex: 1, backgroundColor: t.surface, color: t.ink, borderColor: t.line }]} accessibilityLabel={L('备注', 'Name')} />
          <Btn label={L('存', 'Save')} kind="quiet" onPress={() => run(fr.patchFriend(f.id, { alias: alias.trim() }))} />
        </View>
      </View>
      <View style={{ flexDirection: 'row', gap: space.sm, alignItems: 'center' }}>
        <ShieldCheck size={16} color={t.good} />
        <T v="callout" color={t.ink2} style={{ flex: 1 }}>{L(`指纹 ${f.fingerprint}：和对方 app 里看到的一样，就是本人`, `Fingerprint ${f.fingerprint}: if it matches what they see, it's really them`)}</T>
      </View>
      {f.status === 'active' || f.status === 'blocked' ? (
        <View style={{ gap: space.sm }}>
          <Btn label={f.status === 'blocked' ? L('解开拉黑', 'Unblock') : L('拉黑', 'Block')} kind="quiet" icon={<Ban size={16} color={t.ink} />}
            onPress={() => run(fr.blockFriend(f.id, f.status !== 'blocked'))} />
          <Btn label={L('删掉这个朋友', 'Remove this friend')} kind="danger" icon={<UserX size={16} color={t.bad} />}
            onPress={() => confirm(L(`删掉 ${f.name}？`, `Remove ${f.name}?`), L('会告诉对方一声，之后互相发不了消息。聊天记录留在你这边。要再加回来得重新给邀请码。', "They're told, and neither of you can message the other after that. The chat stays on your side. To add them again you need a new invite."),
              L('删掉', 'Remove'), () => { fr.removeFriend(f.id).then(() => { close(); onGone(); }).catch((e) => showError(L('没删成', "Couldn't remove"), e)); })} />
        </View>
      ) : null}
    </View>
  );
}

export function FriendChatScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const route = useRoute<any>();
  const id = route.params?.id as string;
  const sheet = useSheet();
  const { reload } = useStore();
  const [friend, setFriend] = useState<Friend | null>(null);
  const [msgs, setMsgs] = useState<FriendMsg[]>([]);
  const [agentOn, setAgentOn] = useState(false);
  const [err, setErr] = useState('');
  const [draft, setDraft] = useState('');
  const [askOf, setAskOf] = useState<{ mid: string; title: string } | null>(null);
  const [editOf, setEditOf] = useState<FriendMsg | null>(null);
  const [sending, setSending] = useState(false);
  const scroller = useRef<ScrollView>(null);
  const root = useRef<View>(null);
  const bottom = useBottomInset(root);
  const lastId = useRef(0);
  const lastRead = useRef(0);

  const apply = useCallback((th: fr.FriendThread, full: boolean) => {
    setFriend(th.friend);
    setAgentOn(th.agent);
    setMsgs((cur) => mergeMsgs(full ? [] : cur, [...th.recent, ...th.messages]));
    for (const m of th.messages) lastId.current = Math.max(lastId.current, m.id);
    const newest = th.messages.filter((m) => m.dir === 'in').reduce((a, m) => Math.max(a, m.id), 0);
    if (newest > lastRead.current) {  // 屏幕上有了新的朋友发来的：标成已读，角标跟着变
      lastRead.current = newest;
      fr.markRead(th.friend.id).then(() => reload('unread')).catch(() => {});
    }
  }, [reload]);
  const load = useCallback(() => fr.thread(id).then((th) => { apply(th, true); setErr(''); }).catch((e) => setErr(errText(e))), [id, apply]);
  useEffect(() => { load(); }, [load]);
  // 开着的时候每 4 秒看一眼有没有新的（和发出去的送到了没有）
  useEffect(() => {
    const h = setInterval(() => { fr.thread(id, lastId.current).then((th) => apply(th, false)).catch(() => {}); }, 4000);
    return () => clearInterval(h);
  }, [id, apply]);
  useEffect(() => {
    const h = setTimeout(() => scroller.current?.scrollToEnd({ animated: true }), 80);
    return () => clearTimeout(h);
  }, [msgs.length]);

  const put = (m: FriendMsg) => { lastId.current = Math.max(lastId.current, m.id); setMsgs((cur) => mergeMsgs(cur, [m])); };
  const submit = async () => {
    const text = draft.trim();
    if (!text || sending || !friend) return;
    setSending(true);
    try {
      if (editOf) put(await fr.review(editOf.id, 'edit', text));
      else if (askOf) put(await fr.ask(friend.id, askOf.mid, text));
      else put(await fr.sendText(friend.id, text));
      setDraft('');
      setAskOf(null);
      setEditOf(null);
    } catch (e) { showError(L('没发出去', "Couldn't send"), e); } finally { setSending(false); }
  };
  const webEnter = Platform.OS === 'web' ? (e: NativeSyntheticEvent<TextInputKeyPressEventData>) => {
    const k = e.nativeEvent as unknown as KeyboardEvent;
    if (k.key !== 'Enter' || k.shiftKey || k.isComposing || k.keyCode === 229) return;
    e.preventDefault();
    submit();
  } : undefined;
  const onReview = (m: FriendMsg, a: 'ok' | 'revoke') => {
    const go = () => fr.review(m.id, a).then(put).catch((e) => showError(L('没做成', "Couldn't do that"), e));
    if (a === 'revoke') confirm(L('收回这条代答？', 'Withdraw this answer?'), L('对方那边清空，显示「收回了这条」。', 'It disappears on their side and shows "withdrew this".'), L('收回', 'Withdraw'), go);
    else go();
  };
  const onEdit = (m: FriendMsg) => { setEditOf(m); setAskOf(null); setDraft(m.text); };
  const onAsk = (m: FriendMsg) => { if (m.share) { setAskOf({ mid: m.mid, title: m.share.title }); setEditOf(null); } };
  const onRetry = (m: FriendMsg) => { fr.retry(m.id).then(put).catch((e) => showError(L('没重发成', "Couldn't retry"), e)); };
  const onLong = (m: FriendMsg) => confirm(L('收回这条？', 'Withdraw this?'), L('对方那边清空，显示「收回了这条」。', 'It disappears on their side and shows "withdrew this".'), L('收回', 'Withdraw'),
    () => { fr.revokeMsg(m.id).then(put).catch((e) => showError(L('没收回', "Couldn't withdraw"), e)); });
  const openSheet = () => {
    if (!friend) return;
    sheet.open({ title: friend.name, content: (close) => (
      <FriendSheet f={friend} close={close} onChanged={setFriend} onGone={() => nav.goBack()} onAgents={() => nav.navigate('FriendAgents', { id: friend.id })} />
    ) });
  };

  const active = friend?.status === 'active';
  const sub = friend ? [friend.tierName, friend.agent ? L(`有 agent · 能追问 ${friend.name} 分享的东西`, `Has an agent · you can ask about ${friend.name}'s shares`) : ''].filter(Boolean).join(' · ') : undefined;
  return (
    <Screen>
      <NavHeader title={friend?.name ?? L('朋友', 'Friend')} sub={sub} onBack={() => nav.goBack()}
        right={friend ? <Pressable onPress={openSheet} hitSlop={10} accessibilityRole="button" accessibilityLabel={L('这个朋友的设置', 'Settings for this friend')} style={{ padding: 6 }}><Ellipsis size={22} color={t.ink} /></Pressable> : undefined} />
      <View ref={root} style={{ flex: 1 }} onLayout={bottom.onLayout}>
        <ChatScroll ref={scroller} offset={bottom.offset} style={{ flex: 1 }} contentContainerStyle={{ padding: space.lg, gap: space.lg }}
          keyboardShouldPersistTaps="handled" keyboardDismissMode={dismissMode} refreshControl={<PullRefresh onRefresh={load} />}>
          {err ? <Card><T v="callout" color={t.bad}>{L(`读不到：${err}`, `Couldn't load: ${err}`)}</T></Card> : null}
          {!friend && !err ? <ActivityIndicator color={t.gold} style={{ marginTop: space.xl }} /> : null}
          {friend && !msgs.some((m) => m.kind !== 'system') ? (
            <T v="callout" color={t.ink3} style={{ textAlign: 'center' }}>{L('说点什么，或者从对话里长按一条 →「分享」发给他。', 'Say something, or long-press a message in a chat → Share to send it here.')}</T>
          ) : null}
          {friend ? msgs.map((m) => <MsgView key={m.id} m={m} friend={friend} onAsk={onAsk} onReview={onReview} onEdit={onEdit} onRetry={onRetry} onLong={onLong} />) : null}
          {friend && !agentOn && msgs.some((m) => m.kind === 'ask' && m.dir === 'in') ? (
            <T v="caption" color={t.ink3} style={{ textAlign: 'center' }}>{L('你的名片 agent 还没开：他的追问要你自己回。', "Your card agent isn't on: answer their questions yourself.")}</T>
          ) : null}
        </ChatScroll>
        {friend ? (
          <KeyboardSticky offset={bottom.offset} style={[styles.composerWrap, { borderTopColor: t.line, backgroundColor: t.bg }]}>
            {!active ? (
              <T v="callout" color={t.ink3} style={{ padding: space.md, textAlign: 'center' }}>
                {friend.status === 'blocked' ? L('拉黑了。在右上角「…」里解开。', 'Blocked. Unblock from … at the top right.') : L('你们现在不是朋友，发不了消息。', "You aren't friends now; messages can't be sent.")}
              </T>
            ) : (
              <>
                {askOf || editOf ? (
                  <View style={{ paddingHorizontal: space.md, paddingTop: space.sm }}>
                    <View style={[styles.quote, { backgroundColor: t.goldSoft }]}>
                      <T v="callout" numberOfLines={1} style={{ flex: 1, fontSize: 13 }}>
                        {editOf ? L('改名片 agent 的这条代答（发出去替换它那条）', "Rewriting your card agent's answer (replaces it)") : L(`追问：${askOf?.title}（${friend.name} 的名片 agent 答）`, `Ask about: ${askOf?.title} (${friend.name}'s card agent answers)`)}
                      </T>
                      <Pressable onPress={() => { setAskOf(null); if (editOf) { setEditOf(null); setDraft(''); } }} hitSlop={10} accessibilityRole="button" accessibilityLabel={L('不追问了', 'Cancel')}>
                        <X size={14} color={t.ink3} />
                      </Pressable>
                    </View>
                  </View>
                ) : null}
                <View style={styles.composer}>
                  <TextInput value={draft} onChangeText={setDraft} multiline numberOfLines={1} onKeyPress={webEnter}
                    placeholder={askOf ? L('问点什么…', 'Ask something…') : L(`发给 ${friend.name}`, `Message ${friend.name}`)} placeholderTextColor={t.ink3}
                    accessibilityLabel={L('消息输入框', 'Message')} style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink }]} />
                  <Pressable onPress={submit} disabled={!draft.trim() || sending} accessibilityRole="button" accessibilityLabel={L('发出去', 'Send')}
                    style={[styles.send, { backgroundColor: draft.trim() && !sending ? t.goldFill : t.surface2 }]}>
                    <ArrowUp size={20} color={draft.trim() && !sending ? t.onGold : t.ink3} />
                  </Pressable>
                </View>
              </>
            )}
          </KeyboardSticky>
        ) : null}
      </View>
    </Screen>
  );
}

// —— 加朋友 ——

function QrView({ qr, label }: { qr: { size: number; path: string }; label: string }) {
  // 永远是白底黑码（相机扫深色底的反色码常常扫不出来），深色模式也一样
  return (
    <View style={{ alignSelf: 'center', padding: 6, backgroundColor: '#FFFFFF', borderRadius: radius.md }} accessible accessibilityRole="image" accessibilityLabel={label}>
      <Svg width={216} height={216} viewBox={`0 0 ${qr.size} ${qr.size}`}>
        <Rect width={qr.size} height={qr.size} fill="#FFFFFF" />
        <Path d={qr.path} fill="#000000" />
      </Svg>
    </View>
  );
}

function daysLeft(iso: string): number {
  return Math.max(0, Math.ceil((new Date(iso).getTime() - new Date().getTime()) / 86400000));
}

export function AddFriendScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const route = useRoute<any>();
  const given = route.params?.code as string | undefined;
  const [home, setHome] = useState<Home | null>(null);
  const [note, setNote] = useState('');
  const [tier, setTier] = useState<Tier>('friend');
  const [made, setMade] = useState<Invite | null>(null);
  const [copied, setCopied] = useState('');
  const [busy, setBusy] = useState(false);
  const [paste, setPaste] = useState(given ?? '');
  const [who, setWho] = useState<{ code: string; name: string; fingerprint: string; agent: boolean; already: string | null } | null>(null);
  const [theirTier, setTheirTier] = useState<Tier>('friend');
  const [alias, setAlias] = useState('');
  const [err, setErr] = useState('');
  const loadHome = useCallback(() => fr.home().then(setHome).catch((e) => setErr(errText(e))), []);
  useEffect(() => { loadHome(); }, [loadHome]);

  const lookUp = useCallback(async (text: string) => {
    const code = fr.findCode(text);
    setWho(null);
    if (!code) { setErr(L('没认出邀请码：应该是一个 …/f/i/… 的链接。', "That isn't an invite: it should be a …/f/i/… link.")); return; }
    setErr('');
    setBusy(true);
    try { const p = await fr.preview(code); setWho({ code, ...p }); } catch (e) { setErr(errText(e)); } finally { setBusy(false); }
  }, []);
  // 从落地页「在 app 里打开」或别处带着邀请码进来：直接看看是谁
  const [autoOf, setAutoOf] = useState<string | undefined>(undefined);
  if (given && autoOf !== given) { setAutoOf(given); setPaste(given); setWho(null); setErr(''); }
  useEffect(() => {
    const code = autoOf ? fr.findCode(autoOf) : null;
    if (!code) return undefined;
    let live = true;
    fr.preview(code).then((p) => { if (live) setWho({ code, ...p }); }).catch((e) => { if (live) setErr(errText(e)); });
    return () => { live = false; };
  }, [autoOf]);

  const make = async () => {
    if (busy) return;
    setBusy(true);
    setCopied('');
    try { setMade(await fr.newInvite({ note: note.trim() || undefined, tier })); loadHome(); } catch (e) { showError(L('没生成', "Couldn't make one"), e); } finally { setBusy(false); }
  };
  const copy = async (text: string) => { await Clipboard.setStringAsync(text); setCopied(L('复制好了，发给对方就行', 'Copied. Send it to them.')); };
  const shareCode = async (code: string) => {
    const msg = L(`加我为 OpenMousse 的朋友（这个链接只能用一次）：${code}`, `Add me as a friend on OpenMousse (this link works once): ${code}`);
    try { await NativeShare.share(Platform.OS === 'ios' ? { message: msg } : { message: msg, title: L('邀请码', 'Invite') }); } catch { await copy(code); }
  };
  const fromClipboard = async () => { const s = await Clipboard.getStringAsync(); setPaste(s); if (s) lookUp(s); };
  const addThem = async () => {
    if (!who || busy) return;
    setBusy(true);
    try {
      const f = await fr.accept({ code: who.code, tier: theirTier, alias: alias.trim() || undefined });
      nav.replace('FriendChat', { id: f.id });
    } catch (e) { showError(L('没加上', "Couldn't add"), e); } finally { setBusy(false); }
  };

  const openInvites = home?.invites ?? [];
  return (
    <Screen>
      <NavHeader title={L('加朋友', 'Add a friend')} onBack={() => nav.goBack()} />
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl, gap: space.md }} automaticallyAdjustKeyboardInsets keyboardShouldPersistTaps="handled">
        {home && !home.ready ? <NotReady why={home.why} /> : null}

        <SectionLabel>{L('用对方给你的邀请码', "Use someone's invite")}</SectionLabel>
        <Card style={{ gap: space.sm }}>
          <TextInput value={paste} onChangeText={setPaste} placeholder={L('粘贴对方发来的链接', 'Paste the link they sent')} placeholderTextColor={t.ink3}
            autoCapitalize="none" autoCorrect={false} multiline style={[type.callout, styles.field, { color: t.ink, borderColor: t.line, minHeight: 64 }]}
            accessibilityLabel={L('邀请码', 'Invite link')} />
          <View style={{ flexDirection: 'row', gap: space.sm }}>
            <Btn label={L('粘贴', 'Paste')} kind="quiet" icon={<ClipboardPaste size={16} color={t.ink} />} onPress={fromClipboard} flex />
            <Btn label={busy && !who ? L('在看…', 'Checking…') : L('看看是谁', 'Who is it?')} kind="quiet" onPress={() => lookUp(paste)} flex />
          </View>
          <T v="caption" color={t.ink3}>{L('用手机相机扫对方的二维码，会打开一页，点「在 app 里打开」就到这里。', 'Scanning their QR code with the phone camera opens a page; tap "Open in the app" to come here.')}</T>
          {err ? <T v="callout" color={t.bad}>{err}</T> : null}
          {who ? (
            <View style={{ gap: space.sm, marginTop: space.xs }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.md }}>
                <FriendAvatar id={who.fingerprint} name={who.name} />
                <View style={{ flex: 1, gap: 2 }}>
                  <T v="headline">{who.name}</T>
                  <T v="caption" color={t.ink3}>{L(`指纹 ${who.fingerprint}`, `Fingerprint ${who.fingerprint}`)}{who.agent ? L(' · 有 agent', ' · has an agent') : ''}</T>
                </View>
              </View>
              {who.already ? (
                <Btn label={L('已经是朋友了，去聊天', 'Already friends — open the chat')} kind="quiet" onPress={() => nav.replace('FriendChat', { id: who.already })} />
              ) : (
                <>
                  <T v="caption" color={t.ink3}>{L('放在哪一档（你的名片 agent 按这档回他）', 'Which tier (your card agent answers them by it)')}</T>
                  <Segmented<Tier> value={theirTier} options={TIER_OPTS()} onChange={setTheirTier} />
                  <TextInput value={alias} onChangeText={setAlias} placeholder={L(`备注（可以不写，默认叫 ${who.name}）`, `Your name for them (optional, default ${who.name})`)} placeholderTextColor={t.ink3}
                    style={[type.body, styles.field, { color: t.ink, borderColor: t.line }]} accessibilityLabel={L('备注', 'Name')} />
                  <Btn label={busy ? L('正在加…', 'Adding…') : L(`加 ${who.name} 为朋友`, `Add ${who.name}`)} icon={<UserPlus size={17} color={t.onGold} />} onPress={addThem} />
                </>
              )}
            </View>
          ) : null}
        </Card>

        <SectionLabel>{L('给对方一张邀请码', 'Give someone an invite')}</SectionLabel>
        {made?.code ? (
          <Card style={{ gap: space.md }}>
            {made.qr ? <QrView qr={made.qr} label={L('邀请码二维码', 'Invite QR code')} /> : null}
            <T v="callout" color={t.ink2} style={{ textAlign: 'center' }}>{L(`对方用手机相机扫，或者把链接发给对方。${daysLeft(made.expiresAt)} 天内有效，只能用一次。`, `They scan it with the phone camera, or you send them the link. Valid for ${daysLeft(made.expiresAt)} days, works once.`)}</T>
            <Text selectable style={[type.caption, { color: t.ink2, fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace' }]}>{made.code}</Text>
            <View style={{ flexDirection: 'row', gap: space.sm }}>
              <Btn label={L('发给对方', 'Send it')} icon={<ShareIcon size={16} color={t.onGold} />} onPress={() => shareCode(made.code!)} flex />
              <Btn label={L('复制', 'Copy')} kind="quiet" icon={<Copy size={16} color={t.ink} />} onPress={() => copy(made.code!)} flex />
            </View>
            {copied ? <T v="callout" color={t.good}>{copied}</T> : null}
            {home?.me.fingerprint ? <T v="caption" color={t.ink3}>{L(`你的指纹 ${home.me.fingerprint}：对方加你时看到的也是它`, `Your fingerprint ${home.me.fingerprint}: they see the same when adding you`)}</T> : null}
            <Btn label={L('再生成一张', 'Make another')} kind="quiet" onPress={() => { setMade(null); setNote(''); }} />
          </Card>
        ) : (
          <Card style={{ gap: space.sm }}>
            <TextInput value={note} onChangeText={setNote} placeholder={L('给谁的（只你自己看，比如「NYU 老同学」）', 'Who is it for (only you see this)')} placeholderTextColor={t.ink3}
              maxLength={80} style={[type.body, styles.field, { color: t.ink, borderColor: t.line }]} accessibilityLabel={L('给谁的', 'Who is it for')} />
            <T v="caption" color={t.ink3}>{L('他加进来以后在哪一档', 'Which tier they land in')}</T>
            <Segmented<Tier> value={tier} options={TIER_OPTS()} onChange={setTier} />
            <Btn label={busy ? L('正在生成…', 'Making…') : L('生成邀请码', 'Make an invite')} icon={<QrCode size={17} color={t.onGold} />} onPress={make} />
          </Card>
        )}

        {openInvites.length ? (
          <>
            <SectionLabel>{L('还没人用的邀请码', 'Unused invites')}</SectionLabel>
            <Card style={{ paddingVertical: space.xs }}>
              {openInvites.map((iv, i) => (
                <View key={iv.id} style={[styles.inviteRow, i ? { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line } : null]}>
                  <Link2 size={17} color={t.ink3} />
                  <View style={{ flex: 1, gap: 2 }}>
                    <T v="body" numberOfLines={1}>{iv.note || L('（没写给谁）', '(no note)')}</T>
                    <T v="caption" color={t.ink3}>{L(`${TIER_OPTS().find((x) => x.value === iv.tier)?.label} · 还有 ${daysLeft(iv.expiresAt)} 天`, `${TIER_OPTS().find((x) => x.value === iv.tier)?.label} · ${daysLeft(iv.expiresAt)} days left`)}</T>
                  </View>
                  <Pressable onPress={() => fr.withdrawInvite(iv.id).then(loadHome).catch((e) => showError(L('没收回', "Couldn't withdraw"), e))}
                    accessibilityRole="button" style={[styles.mini, { backgroundColor: t.surface2 }]}>
                    <T v="callout" style={{ fontWeight: '600' }}>{L('收回', 'Withdraw')}</T>
                  </Pressable>
                </View>
              ))}
            </Card>
            <T v="caption" color={t.ink3} style={{ paddingHorizontal: space.xs }}>{L('生成时的二维码只显示那一次；忘了发就收回，再生成一张。', "A QR code shows only when made; if you forgot to send it, withdraw it and make another.")}</T>
          </>
        ) : null}
      </ScrollView>
    </Screen>
  );
}

// —— 我的名片 agent ——

const SCOPE_ROWS = (): { key: ScopeKey; label: string; values: Record<string, string> }[] => [
  { key: 'calendar', label: L('日程', 'Calendar'), values: { detail: L('看详情', 'Details'), busy: L('只给忙闲', 'Free/busy'), none: L('不给', 'Nothing') } },
  { key: 'status', label: L('近况', 'What you are up to'), values: { some: L('几句话', 'A few lines'), line: L('一句话', 'One line'), none: L('不给', 'Nothing') } },
  { key: 'shares', label: L('你分享过的东西', 'What you shared'), values: { ask: L('能追问', 'Can ask'), view: L('只能看', 'Read only'), public: L('只看公开的', 'Public only') } },
  { key: 'notes', label: L('学习笔记', 'Study notes'), values: { view: L('能看', 'Can read'), none: L('不给', 'Nothing') } },
  { key: 'address', label: L('住址', 'Address'), values: { view: L('能看', 'Can see'), none: L('不给', 'Nothing') } },
];
const LEVEL_TONE: Record<string, 'good' | 'gold' | 'neutral'> = { detail: 'good', some: 'good', ask: 'good', view: 'good', busy: 'gold', line: 'gold', public: 'gold', none: 'neutral' };

export function CardAgentScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const [data, setData] = useState<CardSettings | null>(null);
  const [tier, setTier] = useState<AnyTier>('friend');
  const [status, setStatus] = useState<string | null>(null);
  const [err, setErr] = useState('');
  const [saved, setSaved] = useState(false);
  const load = useCallback(() => fr.card().then((d) => { setData(d); setErr(''); }).catch((e) => setErr(errText(e))), []);
  useEffect(() => { load(); }, [load]);

  const cycle = async (key: ScopeKey) => {
    if (!data) return;
    const opts = data.scopes[key];
    const cur = data.tiers[tier][key];
    const next = opts[(opts.indexOf(cur) + 1) % opts.length];
    try { setData(await fr.patchCard({ tiers: { [tier]: { [key]: next } } })); } catch (e) { showError(L('没改成', "Couldn't change it"), e); }
  };
  const saveStatus = async () => {
    if (status == null) return;
    try { setData(await fr.patchCard({ status })); setStatus(null); setSaved(true); } catch (e) { showError(L('没存上', "Couldn't save"), e); }
  };
  const people = tier === 'stranger' ? null : data?.people[tier] ?? [];
  return (
    <Screen>
      <NavHeader title={L('我的名片 agent', 'My card agent')} sub={L('替你对外说话 · 看不到整棵世界树', 'Speaks for you · never sees your memory tree')} onBack={() => nav.goBack()} />
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl, gap: space.md }} automaticallyAdjustKeyboardInsets keyboardShouldPersistTaps="handled"
        refreshControl={<PullRefresh onRefresh={load} />}>
        {err ? <Card><T v="callout" color={t.bad}>{L(`读不到：${err}`, `Couldn't load: ${err}`)}</T></Card> : null}
        {!data && !err ? <ActivityIndicator color={t.gold} style={{ marginTop: space.xl }} /> : null}
        {data && !data.agent ? (
          <View style={[styles.warn, { backgroundColor: t.warnSoft }]}>
            <TriangleAlert size={17} color={t.warn} />
            <T v="callout" style={{ flex: 1 }}>{L('名片 agent 还没开：朋友对着你的分享追问时，不会自动回答，你自己看着回。档位先定好，开了就按这个来。', "The card agent isn't on yet: when friends ask about your shares, nothing answers automatically. Set the tiers now; it follows them once on.")}</T>
          </View>
        ) : null}
        {data ? (
          <>
            <Segmented<AnyTier> value={tier} onChange={setTier}
              options={(['close', 'friend', 'mate', 'stranger'] as AnyTier[]).map((k) => ({ value: k, label: data.tierNames[k] }))} />
            <T v="callout" color={t.ink2} style={{ paddingHorizontal: space.xs }}>
              {tier === 'stranger' ? L('这一档：没加过的人、别家的 agent', 'This tier: people you never added, other agents')
                : people && people.length ? L(`这一档：${people.map((p) => p.name).join('、')}`, `This tier: ${people.map((p) => p.name).join(', ')}`) : L('这一档还没有人', 'Nobody in this tier yet')}
            </T>
            <Card style={{ paddingVertical: 0 }}>
              {SCOPE_ROWS().map((row, i) => {
                const v = data.tiers[tier][row.key];
                return (
                  <Pressable key={row.key} onPress={() => cycle(row.key)} accessibilityRole="button" accessibilityHint={L('点一下换一档', 'Tap to change')}
                    style={({ pressed }) => [styles.scopeRow, i ? { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line } : null, { opacity: pressed ? 0.7 : 1 }]}>
                    <T v="body" style={{ flex: 1 }}>{row.label}</T>
                    <Pill label={row.values[v] ?? v} tone={LEVEL_TONE[v] ?? 'neutral'} />
                  </Pressable>
                );
              })}
              <View style={[styles.scopeRow, { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }]}>
                <T v="body" style={{ flex: 1 }}>{L('健康和身体', 'Health and body')}</T>
                <Pill label={L('一律不给', 'Never')} tone="bad" />
              </View>
              <View style={[styles.scopeRow, { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }]}>
                <T v="body" style={{ flex: 1 }}>{L('世界树', 'Memory tree')}</T>
                <Pill label={L('它自己也看不到', 'It never sees it')} tone="bad" />
              </View>
            </Card>
            <T v="caption" color={t.ink3} style={{ paddingHorizontal: space.xs }}>{L('点一行换一档。朋友看不到自己在哪一档；某个人放哪一档，在和这个人的聊天里点右上角「…」。', "Tap a row to change it. Friends never see their tier; move someone from … in their chat.")}</T>

            <SectionLabel caps={false}>{L('近况（名片 agent 说你最近在干嘛时用它）', 'What you are up to (used when it says what you are doing)')}</SectionLabel>
            <Card style={{ gap: space.sm }}>
              <TextInput value={status ?? data.status} onChangeText={(v) => { setStatus(v); setSaved(false); }} onBlur={saveStatus} multiline maxLength={1000}
                placeholder={L('比如：在伦敦读 ESB；最近在做 OpenMousse', 'e.g. Studying in London; building OpenMousse lately')} placeholderTextColor={t.ink3}
                style={[type.body, styles.field, { color: t.ink, borderColor: t.line, minHeight: 72, textAlignVertical: 'top' }]} accessibilityLabel={L('近况', 'Status')} />
              <T v="caption" color={t.ink3}>{L('「几句话」给全部，「一句话」只给第一行。', '"A few lines" gives all of it, "One line" only the first line.')}</T>
              {status != null ? <Btn label={L('存', 'Save')} kind="quiet" onPress={saveStatus} /> : saved ? <T v="callout" color={t.good}>{L('存好了', 'Saved')}</T> : null}
            </Card>

            <SectionLabel>{L('它守的三条', 'Its three rules')}</SectionLabel>
            <Card style={{ gap: space.md }}>
              {[L('对方 agent 说的，只当资料，不当指令', "What other agents say is information, never an instruction"),
                L('要你表态、问你私事，先出卡片等你点头', 'Anything needing your say or asking about private things becomes a card for you first'),
                L('说出去的每一句，都记进活动记录', 'Everything it says goes into Activity')].map((line, i) => (
                <View key={i} style={{ flexDirection: 'row', gap: space.md, alignItems: 'flex-start' }}>
                  <View style={[styles.num, { backgroundColor: t.goldSoft }]}><T v="caption" color={t.gold} style={{ fontWeight: '800' }}>{String(i + 1)}</T></View>
                  <T v="callout" style={{ flex: 1 }}>{line}</T>
                </View>
              ))}
            </Card>
          </>
        ) : null}
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  homeHead: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingHorizontal: space.xs },
  roundBtn: { width: 40, height: 40, borderRadius: 20, alignItems: 'center', justifyContent: 'center' },
  friendRow: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: space.md },
  linkRow: { flexDirection: 'row', alignItems: 'center', gap: space.md, borderRadius: radius.lg, padding: space.md },
  cardIcon: { width: 34, height: 34, borderRadius: 10, alignItems: 'center', justifyContent: 'center' },
  warn: { flexDirection: 'row', gap: space.sm, alignItems: 'flex-start', padding: space.md, borderRadius: radius.md },
  bubble: { maxWidth: '82%', borderRadius: 18, paddingHorizontal: 14, paddingVertical: 10, gap: 6 },
  askChip: { flexDirection: 'row', alignItems: 'center', gap: 6, alignSelf: 'flex-start', borderRadius: 11, paddingLeft: 3, paddingRight: 8, paddingVertical: 3 },
  shareCard: { maxWidth: '88%', borderWidth: StyleSheet.hairlineWidth, borderRadius: 18, padding: space.md, gap: 8 },
  review: { borderWidth: 1, borderStyle: 'dashed', borderRadius: 14, padding: space.md, gap: space.sm },
  mini: { flexDirection: 'row', alignItems: 'center', gap: 4, height: 32, paddingHorizontal: 12, borderRadius: 16 },
  composerWrap: { borderTopWidth: StyleSheet.hairlineWidth },
  composer: { flexDirection: 'row', alignItems: 'flex-end', gap: space.sm, paddingHorizontal: space.md, paddingVertical: space.sm },
  input: { flex: 1, minHeight: 40, maxHeight: 120, borderRadius: 20, paddingHorizontal: 16, paddingTop: 9, paddingBottom: 9 },
  send: { width: 40, height: 40, borderRadius: 20, alignItems: 'center', justifyContent: 'center' },
  quote: { flexDirection: 'row', alignItems: 'center', gap: 6, borderRadius: radius.md, paddingHorizontal: 10, paddingVertical: 7 },
  field: { borderWidth: StyleSheet.hairlineWidth, borderRadius: radius.sm, paddingHorizontal: space.md, paddingVertical: space.sm },
  inviteRow: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: space.sm },
  scopeRow: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: 13 },
  num: { width: 22, height: 22, borderRadius: 11, alignItems: 'center', justifyContent: 'center', marginTop: 1 },
  lensRing: { position: 'absolute', left: 16, top: 0, width: 30, height: 30, borderRadius: 15, alignItems: 'center', justifyContent: 'center' },
});
