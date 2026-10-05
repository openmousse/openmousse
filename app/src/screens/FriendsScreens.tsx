// 朋友（社交第二层，../../server/friends.py）：「对话」页切到「朋友」时的列表（FriendsHome）、和一个朋友的聊天（FriendChatScreen）、
// 加朋友（AddFriendScreen：给对方一张邀请码 / 用对方给的）、我的名片 agent（CardAgentScreen：谁能问到什么、近况）。
// 两边 agent 之间的来往（第三层）在 FriendAgentsScreen.tsx，从聊天页右上角「…」进。
// 服务器之间的事（签名、投递、重试、名片 agent 代答）都在服务器上；这里只画、只调自己的服务器。
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, Alert, AppState, Platform, Pressable, ScrollView, Share as NativeShare, StyleSheet, Switch, Text, TextInput, View,
  type LayoutChangeEvent, type NativeScrollEvent, type NativeSyntheticEvent, type TextInputKeyPressEventData } from 'react-native';
import { useIsFocused, useNavigation, useRoute } from '@react-navigation/native';
import * as Clipboard from 'expo-clipboard';
import * as Haptics from 'expo-haptics';
import Svg, { Path, Rect } from 'react-native-svg';
import * as fr from '../api/friends';
import type { A2AOut, AnyTier, CardLogItem, CardSettings, Friend, FriendMsg, FriendsHome as Home, Invite, ScopeKey, Tier } from '../api/friends';
import { ArrowDown, ArrowUp, Ban, Bell, BellOff, Check, CheckCheck, ChevronDown, ChevronRight, ClipboardPaste, Clock, Copy, Ellipsis, Eye, IdCard, Link2, Pencil, Pin, PinOff, QrCode,
  Reply, RotateCw, Search, ShareIcon, ShieldCheck, Mic, Trash2, TriangleAlert, Undo2, UserPlus, UserX, X } from '../components/icons';
import { AgentLens, clockLabel, dayKey, dayLabel, timeLabel } from '../components/FriendBits';
import { Markdown } from '../components/Markdown';
import { useSheet } from '../components/Sheet';
import { ChatScroll, KeyboardSticky, dismissMode, useBottomInset } from '../components/keyboard';
import { Btn, Card, CountPill, NavHeader, Pill, PullRefresh, Screen, SectionLabel, Segmented, T, showError } from '../components/ui';
import { Group, GroupLabel, GroupNote, Row, SettingsHeader } from '../components/settings';
import type { AgentColor } from '../data/types';
import { L } from '../i18n';
import { useStore, useThreadOnScreen } from '../store';
import { radius, space, type, useTheme, type Theme } from '../theme';
import { loadDraft, saveDraft } from '../drafts';
import { supports } from '../api/version';
import { AskCard, AskOutCard, InRound, PairLens, askOutcome, isLocalOut, toRounds, type Round } from './FriendAgentsScreen';
import { Action } from '../components/ChatView';
import { podFeatures } from '../api/podcast';
import { PutInPodcast } from '../think/Materials';
import { openEpisode } from '../think/Podcast';
import * as fw from '../api/forward';
import { BasketPill, BundleCard, SelectBar, SelectMark, Transcript, forwardActions, setBasketCount, useForwardSupported, useSelect } from '../components/Forward';

export { AgentLens, timeLabel };

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
  // 两个词都是拉丁字母才取两个首字母（「Acme 同事」只取 A）
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


function lastLine(f: Friend): string {
  const x = f.last;
  if (!x) return f.note ? L(`邀请备注：${f.note}`, `Invite note: ${f.note}`) : L('暂无消息', 'No messages yet');
  if (x.kind === 'system') return x.text;
  const who = x.dir === 'out' ? (x.by === 'agent' ? L('你的名片 Agent：', 'Your card agent: ') : L('你：', 'You: ')) : x.by === 'agent' ? L('对方的名片 Agent：', 'Their card agent: ') : '';
  const what = x.kind === 'ask' ? (x.dir === 'in' ? L('向你的名片 Agent 提问：', 'Asked your card agent: ') : L('你追问：', 'You asked: ')) : '';
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

function NotReady() {
  const t = useTheme();
  return (
    <View style={[styles.warn, { backgroundColor: t.warnSoft }]}>
      <TriangleAlert size={17} color={t.warn} />
      <T v="callout" style={{ flex: 1 }}>
        {L('服务器尚未开启公网访问，好友的服务器无法向你发送消息。请在服务器上重新运行安装命令，并在「开公网」步骤中选择 y。',
          "Your server has no public address yet, so friends' servers can't deliver to you. Run the installer again on the server and answer y to going public.")}
      </T>
    </View>
  );
}

/** 有对外地址，但外面连不进来（Funnel 没开 /f，或者朋友的服务器试过、连不上）：说清楚，给出在服务器上要跑的那一句。 */
export function PublicWarn({ fix, relay }: { fix?: string; relay?: boolean }) {
  const t = useTheme();
  const [copied, setCopied] = useState(false);
  const cmd = relay ? undefined : fix;  // 用的是中继：没有要在服务器上跑的命令，等它重新连上
  return (
    <View style={[styles.warn, { backgroundColor: t.warnSoft, flexDirection: 'column', alignItems: 'stretch' }]}>
      <View style={{ flexDirection: 'row', gap: space.sm, alignItems: 'flex-start' }}>
        <TriangleAlert size={17} color={t.warn} />
        <T v="callout" style={{ flex: 1 }}>
          {relay ? L('服务器暂时未连接到 OpenMousse 中继，好友发来的消息会在恢复连接后送达。服务器会自动重连。',
            "Your server isn't connected to the OpenMousse relay right now; friends' messages arrive once it reconnects, which it does automatically.")
            : L('外网暂时无法连接你的服务器，好友发来的消息无法送达。请在服务器上开启公网访问（Tailscale Funnel）：',
              "Your server can't be reached from the internet, so friends' messages can't get through. Turn on public access (Tailscale Funnel) on the server:")}
        </T>
      </View>
      {cmd ? (
        <Pressable onPress={() => { Clipboard.setStringAsync(cmd).then(() => setCopied(true)).catch(() => {}); }} accessibilityRole="button"
          accessibilityLabel={L('复制命令', 'Copy the command')} style={({ pressed }) => [styles.codeRow, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
          <T v="caption" selectable style={{ flex: 1, fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace', fontSize: 12 }}>{cmd}</T>
          {copied ? <Check size={15} color={t.good} /> : <Copy size={15} color={t.ink2} />}
        </Pressable>
      ) : null}
    </View>
  );
}

/** 朋友看到的名字（服务器的 user_name：名片上、对方的好友列表里，agent 也用它称呼你）。加朋友之前必须有。 */
export function NameCard({ suggest, onSaved }: { suggest?: string; onSaved: (name: string) => void }) {
  const t = useTheme();
  const [v, setV] = useState(suggest ?? '');
  const [busy, setBusy] = useState(false);
  const save = async () => {
    const name = v.trim();
    if (!name || busy) return;
    setBusy(true);
    try { await fr.setMyName(name); onSaved(name); } catch (e) { showError(L('保存失败', "Couldn't save"), e); } finally { setBusy(false); }
  };
  return (
    <Card style={{ gap: space.sm }}>
      <T v="headline">{L('设置对外名称', 'Set the name friends see')}</T>
      <T v="callout" color={t.ink2}>{L('添加好友前需要先设置名称。该名称显示在你的名片和对方的好友列表中，你的 Agent 也会以此称呼你。',
        'You need a name before adding friends. It appears on your card and in their friends list, and your Agent calls you by it.')}</T>
      <View style={{ flexDirection: 'row', gap: space.sm, alignItems: 'center' }}>
        <TextInput value={v} onChangeText={setV} onSubmitEditing={save} returnKeyType="done" maxLength={40} autoCorrect={false}
          placeholder={L('名字或昵称', 'Your name or nickname')} placeholderTextColor={t.ink3} accessibilityLabel={L('对外名称', 'Name friends see')}
          style={[type.body, styles.field, { flex: 1, backgroundColor: t.surface, color: t.ink, borderColor: t.line }]} />
        {busy ? <ActivityIndicator color={t.gold} /> : <Btn label={L('保存', 'Save')} onPress={save} />}
      </View>
    </Card>
  );
}

// —— 「对话」页的「朋友」 ——

/** 左滑露出的几个按钮（横向 ScrollView，不用新的原生模块）。宽度没量出来之前就是普通的一行。 */
function SwipeRow({ children, actions }: { children: React.ReactNode; actions: { label: string; icon: React.ReactNode; bg: string; fg: string; onPress: () => void }[] }) {
  const [w, setW] = useState(0);
  const ref = useRef<ScrollView>(null);
  const each = 78;
  const total = each * actions.length;
  if (!actions.length) return <>{children}</>;
  return (
    <View onLayout={(e) => setW(Math.round(e.nativeEvent.layout.width))}>
      {w ? (
        <ScrollView ref={ref} horizontal showsHorizontalScrollIndicator={false} bounces={false} decelerationRate="fast" snapToOffsets={[0, total]}
          disableIntervalMomentum directionalLockEnabled>
          <View style={{ width: w }}>{children}</View>
          {actions.map((a) => (
            <Pressable key={a.label} onPress={() => { ref.current?.scrollTo({ x: 0, animated: true }); a.onPress(); }} accessibilityRole="button" accessibilityLabel={a.label}
              style={({ pressed }) => [styles.swipeBtn, { width: each, backgroundColor: a.bg, opacity: pressed ? 0.8 : 1 }]}>
              {a.icon}
              <T v="caption" color={a.fg} style={{ fontWeight: '700' }}>{a.label}</T>
            </Pressable>
          ))}
        </ScrollView>
      ) : children}
    </View>
  );
}

export function FriendsHome() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const sheet = useSheet();
  const { unread } = useStore();
  const [data, setData] = useState<Home | null>(null);
  const [err, setErr] = useState('');
  const [q, setQ] = useState('');
  const [hits, setHits] = useState<fr.FriendHit[] | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const load = useCallback(() => fr.home().then((d) => { setData(d); setErr(''); }).catch((e) => setErr(errText(e))), []);
  useEffect(() => nav.addListener('focus', () => { load(); }), [nav, load]);
  // 未读变了（轮询每 45 秒一次）就重读一遍：谁发来了新的、最后一句
  const sig = JSON.stringify(unread.friends ?? {});
  useEffect(() => { load(); }, [sig, load]);
  // 搜索：名字在本机筛；聊天内容问服务器（停 300ms 再问）
  const onQ = (v: string) => {
    setQ(v);
    if (timer.current) clearTimeout(timer.current);
    const term = v.trim();
    if (!term || !supports('friends.search')) { setHits(null); return; }
    timer.current = setTimeout(() => { fr.search(term).then((r) => setHits(r.hits)).catch(() => setHits([])); }, 300);
  };
  const words = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const named = (f: Friend) => words.every((w) => `${f.name} ${f.cardName}`.toLowerCase().includes(w));
  const prefsOk = supports('friends.prefs');
  const patch = (f: Friend, b: { pinned?: boolean; muted?: boolean }) => { fr.patchFriend(f.id, b).then(load).catch((e) => showError(L('修改失败', "Couldn't update"), e)); };
  const rowActions = (f: Friend) => (prefsOk ? [
    { label: f.pinned ? L('取消置顶', 'Unpin') : L('置顶', 'Pin'), icon: f.pinned ? <PinOff size={18} color={t.ink} /> : <Pin size={18} color={t.ink} />, bg: t.surface2, fg: t.ink,
      onPress: () => patch(f, { pinned: !f.pinned }) },
    { label: f.muted ? L('取消免打扰', 'Unmute') : L('免打扰', 'Mute'), icon: f.muted ? <Bell size={18} color={t.onGold} /> : <BellOff size={18} color={t.onGold} />, bg: t.goldFill, fg: t.onGold,
      onPress: () => patch(f, { muted: !f.muted }) },
  ] : []);
  const rowMenu = (f: Friend) => {
    const acts = rowActions(f);
    if (!acts.length) return;
    sheet.open({ title: f.name, content: (close) => (
      <View style={{ gap: space.sm }}>
        <Action icon={f.pinned ? PinOff : Pin} label={acts[0].label} onPress={() => { close(); acts[0].onPress(); }} />
        <Action icon={f.muted ? Bell : BellOff} label={acts[1].label} note={f.muted ? undefined : L('不推送通知，未读不计入角标', "No notifications; unread doesn't count in the badge")}
          onPress={() => { close(); acts[1].onPress(); }} />
      </View>
    ) });
  };

  const live = (data?.friends ?? []).filter((f) => f.status === 'active' && named(f));
  const off = (data?.friends ?? []).filter((f) => f.status !== 'active' && named(f));
  const open = data?.invites.length ?? 0;
  const searching = !!words.length;
  return (
    <ScrollView contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl, gap: space.md }} refreshControl={<PullRefresh onRefresh={load} />}
      keyboardShouldPersistTaps="handled" keyboardDismissMode={Platform.OS === 'web' ? 'none' : 'on-drag'}>
      <View style={styles.homeHead}>
        <T v="title" style={{ flex: 1 }}>{L('好友', 'Friends')}</T>
        <Pressable onPress={() => nav.navigate('AddFriend')} accessibilityRole="button" accessibilityLabel={L('添加好友', 'Add a friend')}
          style={({ pressed }) => [styles.roundBtn, { backgroundColor: t.surface2, opacity: pressed ? 0.7 : 1 }]}>
          <UserPlus size={20} color={t.ink} />
        </Pressable>
      </View>
      {data?.friends.length ? (
        <View style={[styles.searchBox, { backgroundColor: t.surface2 }]}>
          <Search size={17} color={t.ink3} />
          <TextInput value={q} onChangeText={onQ} placeholder={supports('friends.search') ? L('搜索好友和聊天记录', 'Search friends and messages') : L('搜索好友', 'Search friends')}
            placeholderTextColor={t.ink3} autoCorrect={false} returnKeyType="search" accessibilityLabel={L('搜索', 'Search')} style={[type.body, { flex: 1, color: t.ink, paddingVertical: 0 }]} />
          {q ? <Pressable onPress={() => onQ('')} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('清空', 'Clear')}><X size={16} color={t.ink3} /></Pressable> : null}
        </View>
      ) : null}
      {err ? <Card><T v="callout" color={t.bad}>{L(`无法加载：${err}`, `Couldn't load: ${err}`)}</T></Card> : null}
      {!data && !err ? <ActivityIndicator color={t.gold} style={{ marginTop: space.xl }} /> : null}
      {!searching && data?.why === 'no_name' ? <NameCard suggest={data.me.suggest} onSaved={() => { load(); }} /> : !searching && data?.why === 'no_url' ? <NotReady /> : null}
      {!searching && data?.unreachable ? <PublicWarn fix={data.publicFix} relay={!!data.relay?.base && data.me.url === data.relay.base} /> : null}
      {data && !data.friends.length ? (
        <Card style={{ gap: space.sm }}>
          <T v="headline">{L('暂无好友', 'No friends yet')}</T>
          <T v="callout" color={t.ink2}>{L('添加好友：向对方发送邀请码（二维码或链接），或粘贴对方发来的邀请码。添加后双方服务器直接通信，每条消息均带签名。', "To add a friend, give them an invite (QR code or link) or paste theirs. Your servers then communicate directly, and every message is signed.")}</T>
          <T v="callout" color={t.ink2}>{L('对方未安装 OpenMousse 时，可使用分享链接。', "For people without OpenMousse, use a share link.")}</T>
          {data.ready ? <Btn label={L('添加好友', 'Add a friend')} icon={<UserPlus size={17} color={t.onGold} />} onPress={() => nav.navigate('AddFriend')} /> : null}
        </Card>
      ) : null}
      {searching && live.length ? <SectionLabel>{L('好友', 'Friends')}</SectionLabel> : null}
      {live.length ? (
        <Card style={{ paddingVertical: 0, paddingHorizontal: 0, overflow: 'hidden' }}>
          {live.map((f, i) => (
            <View key={f.id} style={i ? { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line } : null}>
              <SwipeRow actions={rowActions(f)}>
                <Pressable onPress={() => nav.navigate('FriendChat', { id: f.id })} onLongPress={prefsOk ? () => rowMenu(f) : undefined} delayLongPress={400} accessibilityRole="button"
                  accessibilityHint={prefsOk ? L('左滑或长按可置顶、免打扰', 'Swipe left or long-press to pin or mute') : undefined}
                  style={({ pressed }) => [styles.friendRow, { paddingHorizontal: space.md, backgroundColor: f.pinned ? t.surface2 : t.surface, opacity: pressed ? 0.7 : 1 }]}>
                  <FriendAvatar id={f.id} name={f.name} />
                  <View style={{ flex: 1, minWidth: 0, gap: 3 }}>
                    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                      <T v="headline" numberOfLines={1} style={{ flexShrink: 1 }}>{f.name}</T>
                      {f.muted ? <BellOff size={14} color={t.ink3} /> : null}
                      {f.agent ? <Pill label={L('有 Agent', 'Has an Agent')} tone="cyan" /> : null}
                    </View>
                    <T v="callout" color={f.unread && !f.muted ? t.ink : t.ink2} numberOfLines={1}>{lastLine(f)}</T>
                  </View>
                  <View style={{ alignItems: 'flex-end', gap: 5, alignSelf: 'flex-start', paddingTop: 2 }}>
                    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
                      {f.pinned ? <Pin size={12} color={t.ink3} /> : null}
                      <T v="caption" color={t.ink3}>{timeLabel(f.last?.ts ?? f.createdAt)}</T>
                    </View>
                    <CountPill n={f.unread} small muted={f.muted} />
                  </View>
                </Pressable>
              </SwipeRow>
            </View>
          ))}
        </Card>
      ) : null}
      {searching && hits?.length ? (
        <>
          <SectionLabel>{L('聊天记录', 'Messages')}</SectionLabel>
          <Card style={{ paddingVertical: 0, paddingHorizontal: space.md }}>
            {hits.map((h, i) => (
              <Pressable key={h.id} onPress={() => nav.navigate('FriendChat', { id: h.friend, focus: h.id })} accessibilityRole="button"
                style={({ pressed }) => [styles.friendRow, i ? { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line } : null, { opacity: pressed ? 0.7 : 1 }]}>
                <FriendAvatar id={h.friend} name={h.name} size={36} />
                <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                    <T v="headline" numberOfLines={1} style={{ flexShrink: 1, fontSize: 15 }}>{h.name}</T>
                    <T v="caption" color={t.ink3}>{timeLabel(h.ts)}</T>
                  </View>
                  <T v="callout" color={t.ink2} numberOfLines={2}>{h.dir === 'out' ? L(`你：${h.snippet}`, `You: ${h.snippet}`) : h.snippet}</T>
                </View>
              </Pressable>
            ))}
          </Card>
        </>
      ) : null}
      {searching && hits && !hits.length && !live.length && !off.length ? (
        <Card><T v="callout" color={t.ink2}>{L(`未找到「${q.trim()}」的相关结果。`, `No results for "${q.trim()}".`)}</T></Card>
      ) : null}
      {!searching && open ? (
        <Pressable onPress={() => nav.navigate('AddFriend')} accessibilityRole="button" style={({ pressed }) => [styles.linkRow, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
          <QrCode size={18} color={t.ink2} />
          <T v="callout" style={{ flex: 1 }}>{L(`${open} 张邀请码尚未使用`, `${open} invite${open === 1 ? '' : 's'} not used yet`)}</T>
          <ChevronRight size={16} color={t.ink3} />
        </Pressable>
      ) : null}
      {!searching && (data?.friends.length || data?.ready) ? (
        <Pressable onPress={() => nav.navigate('CardAgent')} accessibilityRole="button" style={({ pressed }) => [styles.linkRow, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
          <View style={[styles.cardIcon, { backgroundColor: t.goldSoft }]}><IdCard size={17} color={t.gold} /></View>
          <View style={{ flex: 1, gap: 2 }}>
            <T v="headline" style={{ fontSize: 15 }}>{L('我的名片 Agent', 'My card agent')}</T>
            <T v="caption" color={t.ink3}>{data?.agent ? L('按档位设置好友可查询的内容', 'What friends can ask about, by tier') : L('尚未开启：好友的提问需要你亲自回复', "Not enabled: you answer friends' questions yourself")}</T>
          </View>
          <ChevronRight size={16} color={t.ink3} />
        </Pressable>
      ) : null}
      {off.length ? (
        <>
          <SectionLabel>{L('已不是好友', 'No longer friends')}</SectionLabel>
          <Card style={{ paddingVertical: 0, paddingHorizontal: space.md }}>
            {off.map((f, i) => (
              <Pressable key={f.id} onPress={() => nav.navigate('FriendChat', { id: f.id })} accessibilityRole="button"
                style={({ pressed }) => [styles.friendRow, i ? { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line } : null, { opacity: pressed ? 0.7 : 1 }]}>
                <FriendAvatar id={f.id} name={f.name} size={36} />
                <View style={{ flex: 1, gap: 2 }}>
                  <T v="body" numberOfLines={1}>{f.name}</T>
                  <T v="caption" color={t.ink3}>{f.status === 'blocked' ? L('已屏蔽：你不会收到对方的消息', "Blocked: you won't get their messages") : L('对方已将你删除', 'They removed you')}</T>
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

/** 问出去的（a2a_out）按 id 合并，早的在前；还在等的（local-…）排在最后。 */
function mergeOuts(prev: A2AOut[], more: A2AOut[]): A2AOut[] {
  const byId = new Map(prev.map((o) => [o.id, o]));
  for (const o of more) byId.set(o.id, o);
  const at = (o: A2AOut) => (isLocalOut(o) ? Infinity : new Date(o.createdAt).getTime() || 0);
  return [...byId.values()].sort((a, b) => at(a) - at(b));
}

type InRoundT = Extract<Round, { kind: 'in' }>;
type Line =
  | { kind: 'msg'; m: FriendMsg; at: number }
  | { kind: 'out'; o: A2AOut; at: number }
  | { kind: 'round'; r: InRoundT; at: number }
  | { kind: 'day'; key: string; label: string };

const msOf = (iso: string) => { const n = new Date(iso).getTime(); return Number.isNaN(n) ? NaN : n; };

/** 聊天里的一行行：朋友的消息按服务器的顺序；名片 agent 问出去的、对方 Agent 来问的（折成一行）按时间插进去；换天的地方插日期。 */
function timeline(msgs: FriendMsg[], outs: A2AOut[], rounds: InRoundT[]): Line[] {
  type Extra = Extract<Line, { kind: 'out' | 'round' }>;
  const extras: Extra[] = [
    ...outs.filter((o) => !isLocalOut(o)).map((o): Extra => ({ kind: 'out', o, at: msOf(o.createdAt) || 0 })),
    ...rounds.map((r): Extra => ({ kind: 'round', r, at: r.at })),
  ].sort((a, b) => a.at - b.at);
  const body: Line[] = [];
  let i = 0;
  for (const m of msgs) {
    const t = msOf(m.ts);
    while (i < extras.length && !Number.isNaN(t) && extras[i].at <= t) body.push(extras[i++]);
    body.push({ kind: 'msg', m, at: t });
  }
  while (i < extras.length) body.push(extras[i++]);
  body.push(...outs.filter(isLocalOut).map((o): Line => ({ kind: 'out', o, at: Infinity })));  // 还在等对方 agent 回的，排在最后
  // 换天的地方插一行日期（系统灰字也算在那一天里；还在等的那条不算）
  const out: Line[] = [];
  let day = '';
  for (const x of body) {
    const ts = x.kind === 'msg' ? x.m.ts : x.kind === 'out' ? (isLocalOut(x.o) ? '' : x.o.createdAt) : x.kind === 'round' ? x.r.items[0].ts : '';
    const k = dayKey(ts);
    if (k && k !== day) { day = k; out.push({ kind: 'day', key: `day:${k}`, label: dayLabel(ts) }); }
    out.push(x);
  }
  return out;
}

/** 合并新读到的一批（recent 里带着改过的、删掉的）：按 id 排，hidden 的拿掉。 */
function mergeMsgs(prev: FriendMsg[], more: FriendMsg[]): FriendMsg[] {
  if (!more.length) return prev;
  const byId = new Map(prev.map((m) => [m.id, m]));
  for (const m of more) {
    if (m.hidden) byId.delete(m.id);
    else byId.set(m.id, m);
  }
  return [...byId.values()].sort((a, b) => a.id - b.id);
}

/** 试过、没送到、还在自动重试的那几条（服务器给了上一次的原因） */
const retrying = (m: FriendMsg) => m.dir === 'out' && m.status === 'queued' && !!m.error;

/** 没送到的说明（正常的排队、送到、看过画在气泡角上，见 Meta）。 */
function StatusLine({ m, onRetry }: { m: FriendMsg; onRetry: () => void }) {
  const t = useTheme();
  if (m.dir !== 'out') return null;
  if (retrying(m)) {
    return (
      <Pressable onPress={onRetry} accessibilityRole="button" style={{ alignSelf: 'flex-end', flexDirection: 'row', alignItems: 'center', gap: 4 }}>
        <RotateCw size={12} color={t.warn} />
        <T v="caption" color={t.warn}>{L('未送达，将自动重试 · 立即重试', 'Not delivered yet; retrying automatically · Retry now')}</T>
      </Pressable>
    );
  }
  if (m.status === 'failed') {
    return (
      <Pressable onPress={onRetry} accessibilityRole="button" style={{ alignSelf: 'flex-end', flexDirection: 'row', alignItems: 'center', gap: 4 }}>
        <RotateCw size={12} color={t.bad} />
        <T v="caption" color={t.bad}>{L('未送达 · 点按重试', 'Not delivered · Tap to retry')}</T>
      </Pressable>
    );
  }
  return null;
}

/** 气泡角上：已编辑、几点几分；我发的再加状态：排队中（钟）→ ✓ 已送到对方服务器 → ✓✓ 对方看过（已读回执两边都开着才有）。 */
function Meta({ m, onDark }: { m: FriendMsg; onDark?: boolean }) {
  const t = useTheme();
  const c = onDark ? t.ink2 : t.ink3;
  const mine = m.dir === 'out' && m.kind !== 'system';
  const gone = m.status === 'revoked';
  let tick: React.ReactNode = null;
  let label = '';
  if (mine && !gone) {
    if (m.status === 'queued' && !m.error) { tick = <Clock size={12} color={c} />; label = L('排队中', 'Queued'); }
    else if (m.status === 'sent' && m.read) { tick = <CheckCheck size={14} color={t.cyan} />; label = L('对方已看过', 'Seen'); }
    else if (m.status === 'sent') { tick = <Check size={14} color={c} />; label = L('已送到对方服务器', "Delivered to their server"); }
  }
  return (
    <View style={styles.meta} accessible accessibilityLabel={[m.edited && !gone ? L('已编辑', 'edited') : '', clockLabel(m.ts), label].filter(Boolean).join(' ')}>
      {m.edited && !gone ? <T v="caption" color={c} style={styles.metaText}>{L('已编辑', 'edited')}</T> : null}
      <T v="caption" color={c} style={styles.metaText}>{clockLabel(m.ts)}</T>
      {tick}
    </View>
  );
}

/** 回复的那条原话（点一下跳过去）。dir 是这台服务器上的方向：out = 我说的。 */
function QuoteBox({ q, friend, onJump }: { q: NonNullable<FriendMsg['quote']>; friend: Friend; onJump?: (mid: string) => void }) {
  const t = useTheme();
  return (
    <Pressable onPress={onJump && !q.gone ? () => onJump(q.mid) : undefined} disabled={!onJump || q.gone} accessibilityRole={onJump && !q.gone ? 'button' : undefined}
      accessibilityLabel={L(`回复 ${q.dir === 'out' ? '你' : friend.name}：${q.text}`, `Reply to ${q.dir === 'out' ? 'you' : friend.name}: ${q.text}`)}
      style={({ pressed }) => [styles.quoteBox, { borderLeftColor: t.gold, backgroundColor: t.bg, opacity: pressed ? 0.6 : 1 }]}>
      <T v="caption" color={t.gold} style={{ fontWeight: '700' }}>{q.dir === 'out' ? L('你', 'You') : friend.name}</T>
      <T v="callout" color={t.ink2} numberOfLines={2} style={{ fontSize: 13, lineHeight: 18 }}>{q.gone ? L('原消息已收回或删除', 'The original was withdrawn or deleted') : q.text}</T>
    </Pressable>
  );
}

/** 气泡下的表情回应：两个人点的一样就写「👍 2」。点一下打开长按菜单（换一个、取消）。 */
function Reactions({ m, mine, onPress }: { m: FriendMsg; mine: boolean; onPress: () => void }) {
  const t = useTheme();
  const r = m.reactions;
  if (!r || (!r.me && !r.them) || m.status === 'revoked') return null;
  const same = r.me && r.them && r.me === r.them;
  const text = same ? `${r.me} 2` : [r.them, r.me].filter(Boolean).join(' ');
  return (
    <Pressable onPress={onPress} accessibilityRole="button" hitSlop={6}
      accessibilityLabel={L(`表情回应：${[r.them ? `对方 ${r.them}` : '', r.me ? `你 ${r.me}` : ''].filter(Boolean).join('，')}`,
        `Reactions: ${[r.them ? `them ${r.them}` : '', r.me ? `you ${r.me}` : ''].filter(Boolean).join(', ')}`)}
      style={({ pressed }) => [styles.reactPill, { alignSelf: mine ? 'flex-end' : 'flex-start', backgroundColor: t.surface, borderColor: r.me ? t.gold : t.line, opacity: pressed ? 0.7 : 1 }]}>
      <Text style={{ fontSize: 14 }}>{text}</Text>
    </Pressable>
  );
}

/** 日期分隔。 */
function DaySep({ label }: { label: string }) {
  const t = useTheme();
  return (
    <View style={{ alignItems: 'center' }} accessibilityRole="header">
      <View style={[styles.dayPill, { backgroundColor: t.surface2 }]}><T v="caption" color={t.ink2} style={{ fontWeight: '600' }}>{label}</T></View>
    </View>
  );
}

/** 对方 Agent 来问的一段（card_log 的一轮）：折成一行「Bea 的 Agent 问了 2 句 · 周四 19:00 一起吃饭 · 待你确认」，点开看全过程；
 * 要你定的卡挂在这一行下面，能直接点。 */
function AgentRound({ r, friend, ready }: { r: InRoundT; friend: Friend; ready: boolean }) {
  const t = useTheme();
  const { inbox } = useStore();
  const [open, setOpen] = useState(false);
  const asked = r.items.filter((i) => i.dir === 'in').length;
  const ask = r.items.find((i) => i.ask)?.ask ?? null;
  const cards = [...new Set(r.items.map((i) => i.inboxId).filter((x): x is string => !!x))];
  const waiting = cards.filter((id) => inbox.some((x) => x.id === id));
  const firstIn = r.items.find((i) => i.dir === 'in')?.text ?? '';
  const gist = ask?.summary || (firstIn.length > 36 ? `${firstIn.slice(0, 36)}…` : firstIn);
  const state = waiting.length ? { label: L('待你确认', 'Waiting for you'), tone: 'gold' as const } : ask && ready ? askOutcome(ask) : null;
  const head = L(`${friend.name} 的 Agent 问了 ${asked} 句`, `${friend.name}'s Agent asked ${asked} question${asked === 1 ? '' : 's'}`);
  return (
    <View style={{ gap: space.sm }}>
      <Pressable onPress={() => setOpen(!open)} accessibilityRole="button" accessibilityState={{ expanded: open }}
        accessibilityHint={open ? L('收起', 'Collapse') : L('查看全过程', 'Shows the whole exchange')}
        style={({ pressed }) => [styles.roundRow, { backgroundColor: t.surface, borderColor: waiting.length ? t.gold : t.line, opacity: pressed ? 0.8 : 1 }]}>
        <PairLens bg={t.surface} size={20} />
        <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
          <T v="callout" numberOfLines={open ? undefined : 2} style={{ fontSize: 14 }}>
            <Text style={{ fontWeight: '700' }}>{head}</Text>{gist ? ` · ${gist}` : ''}
          </T>
          <T v="caption" color={t.ink3}>{clockLabel(r.items[0].ts)}</T>
        </View>
        {state ? <Pill label={state.label} tone={state.tone} /> : null}
        <View style={{ transform: [{ rotate: open ? '180deg' : '0deg' }] }}><ChevronDown size={16} color={t.ink3} /></View>
      </Pressable>
      {open ? (
        <View style={[styles.roundBody, { borderLeftColor: t.line }]}><InRound r={r} name={friend.name} ready={ready} /></View>
      ) : waiting.map((id) => <AskCard key={id} id={id} ask={null} ready={ready} />)}
    </View>
  );
}

function SharedCard({ m, friend, mine, onAsk }: { m: FriendMsg; friend: Friend; mine: boolean; onAsk?: () => void }) {
  const t = useTheme();
  const sheet = useSheet();
  const [open, setOpen] = useState(false);
  const s = m.share;
  if (!s) return null;
  const gone = m.status === 'revoked';
  // 合并转发的聊天记录：一张聊天记录卡，点开看全部（收回了、老服务器没切好的走下面的普通卡）
  const items = s.kind === 'thread' && !gone ? (s.items ?? []) : [];
  if (items.length) {
    const rows = items.map((i) => ({ ...i, when: timeLabel(i.at) }));
    return (
      <View style={{ alignSelf: mine ? 'flex-end' : 'flex-start', maxWidth: '88%', gap: 6, alignItems: mine ? 'flex-end' : 'flex-start' }}>
        <BundleCard mine={mine} title={s.title} n={items.length} convs={s.convs} lines={items}
          onOpen={() => sheet.open({ title: s.title || L('聊天记录', 'Messages'), content: () => <Transcript items={rows} /> })} />
        {m.text ? <T v="callout">{m.text}</T> : null}
        {s.can_ask || (!mine && s.can_ask && onAsk) ? (
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm, alignItems: 'center' }}>
            <Pill label={mine ? L('可追问', 'Takes questions') : L(`可向 ${friend.name} 的名片 Agent 追问`, `You can ask ${friend.name}'s card agent`)} tone="gold" />
            {!mine && onAsk ? (
              <Pressable onPress={onAsk} accessibilityRole="button" style={({ pressed }) => [styles.mini, { backgroundColor: t.goldSoft, opacity: pressed ? 0.7 : 1 }]}>
                <T v="callout" color={t.gold} style={{ fontWeight: '700' }}>{L('追问', 'Ask')}</T>
              </Pressable>
            ) : null}
          </View>
        ) : null}
      </View>
    );
  }
  return (
    <View style={[styles.shareCard, { backgroundColor: t.surface, borderColor: t.line, alignSelf: mine ? 'flex-end' : 'flex-start' }]}>
      <T v="caption" color={t.cyan} style={{ fontWeight: '600' }}>
        {`${s.kind === 'note' ? L('笔记', 'Note') : s.kind === 'message' ? L('对话摘录', 'From a chat') : s.kind === 'thread' ? L('聊天记录', 'Messages') : L('分享', 'Shared')}${s.when ? ` · ${s.when}` : ''}`}
      </T>
      {gone ? (
        <T v="callout" color={t.ink3}>{mine ? L('你已收回此分享', 'You withdrew this share') : L(`${friend.name} 已收回此分享`, `${friend.name} withdrew this share`)}</T>
      ) : (
        <>
          <T v="headline" style={{ fontSize: 16 }}>{s.title || L('（无标题）', '(untitled)')}</T>
          {s.quote ? <T v="callout" color={t.ink2}>{L(`「${s.quote}」`, `“${s.quote}”`)}</T> : null}
          {m.text ? <T v="callout">{m.text}</T> : null}
          {!mine && s.text ? (
            <>
              <Pressable onPress={() => setOpen(!open)} accessibilityRole="button" style={{ alignSelf: 'flex-start' }}>
                <T v="callout" color={t.gold} style={{ fontWeight: '600' }}>{open ? L('收起全文', 'Hide the full text') : L('查看全文', 'Read the full text')}</T>
              </Pressable>
              {open ? <Markdown text={s.text} /> : null}
            </>
          ) : null}
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm, alignItems: 'center' }}>
            {s.can_ask ? <Pill label={mine ? L('可追问', 'Takes questions') : L(`可向 ${friend.name} 的名片 Agent 追问`, `You can ask ${friend.name}'s card agent`)} tone="gold" /> : null}
            {s.link ? <Pill label={L('知道链接的人可查看', 'Anyone with the link')} /> : null}
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
        <T v="caption" color={t.ink2}>{L('仅你可见 · 名片 Agent 已代你回答', 'Only you see this · Your card agent answered for you')}</T>
        <View style={{ flexDirection: 'row', gap: space.sm, flexWrap: 'wrap' }}>
          <Pressable onPress={() => onReview('ok')} accessibilityRole="button" style={[styles.mini, { backgroundColor: t.surface }]}>
            <Check size={14} color={t.ink} /><T v="callout" style={{ fontWeight: '600' }}>{L('确认', 'Approve')}</T>
          </Pressable>
          <Pressable onPress={onEdit} accessibilityRole="button" style={[styles.mini, { backgroundColor: t.surface }]}>
            <T v="callout" style={{ fontWeight: '600' }}>{L('修改', 'Rewrite')}</T>
          </Pressable>
          <Pressable onPress={() => onReview('revoke')} accessibilityRole="button" style={[styles.mini, { backgroundColor: t.surface }]}>
            <Undo2 size={14} color={t.ink} /><T v="callout" style={{ fontWeight: '600' }}>{L('收回', 'Withdraw')}</T>
          </Pressable>
        </View>
      </View>
    );
  }
  const line = m.review === 'ok' ? L('已确认，保留此回答', 'Approved; kept as is') : m.review === 'edited' ? L('你已修改此回答', 'You rewrote this')
    : m.review === 'revoked' ? L('已收回，对方那边显示为已收回', 'Withdrawn; it shows as withdrawn on their side') : '';
  return line ? <T v="caption" color={m.review === 'ok' ? t.good : t.ink3}>{line}</T> : null;
}

function MsgView({ m, friend, onAsk, onReview, onEdit, onRetry, onLong, onJump, lit }: {
  m: FriendMsg; friend: Friend; onAsk: (m: FriendMsg) => void; onReview: (m: FriendMsg, a: 'ok' | 'revoke') => void; onEdit: (m: FriendMsg) => void;
  onRetry: (m: FriendMsg) => void; onLong: (m: FriendMsg) => void; onJump: (mid: string) => void;
  /** 从搜索、引用跳过来的那条：闪一下 */
  lit?: boolean;
}) {
  const t = useTheme();
  const mine = m.dir === 'out';
  if (m.kind === 'system') return <T v="caption" color={t.ink3} style={{ textAlign: 'center' }}>{m.text}</T>;
  if (m.kind === 'share') {
    return (
      <Pressable onLongPress={m.status !== 'revoked' ? () => onLong(m) : undefined} delayLongPress={350} style={{ gap: 4 }}>
        <SharedCard m={m} friend={friend} mine={mine} onAsk={friend.status === 'active' ? () => onAsk(m) : undefined} />
        <View style={{ alignSelf: mine ? 'flex-end' : 'flex-start' }}><Meta m={m} /></View>
        <Reactions m={m} mine={mine} onPress={() => onLong(m)} />
        <StatusLine m={m} onRetry={() => onRetry(m)} />
      </Pressable>
    );
  }
  if (m.kind === 'answer') {
    // 名片 agent 的代答：我的（替我答朋友）和朋友的（替他答我）；被主人改过的算主人说的
    const byPerson = m.by === 'person';
    const who = mine ? (byPerson ? L('你修改后的代答', 'Your rewrite') : L('你的名片 Agent · 代答', 'Your card agent · answered for you'))
      : (byPerson ? L(`${friend.name} 已修改`, `Edited by ${friend.name}`) : L(`${friend.name} 的名片 Agent · 代答`, `${friend.name}'s card agent · answered`));
    const gone = m.status === 'revoked';
    const used = m.usedLabel || (m.used?.length ? L(`仅使用：${m.used.join('、')}`, `Used only: ${m.used.join(', ')}`) : '');
    return (
      <Pressable onLongPress={!gone ? () => onLong(m) : undefined} delayLongPress={350} style={{ flexDirection: 'row', gap: space.sm, alignItems: 'flex-start' }}>
        <AgentLens mine={mine} />
        <View style={{ flex: 1, minWidth: 0, gap: 6 }}>
          <T v="caption" color={t.gold} style={{ fontWeight: '700' }}>{who}</T>
          {gone && !mine ? <T v="callout" color={t.ink3}>{L(`${friend.name} 已收回此消息`, `${friend.name} withdrew this`)}</T>
            : <T v="body" color={gone ? t.ink3 : t.ink} style={gone ? { textDecorationLine: 'line-through' } : undefined}>{m.text}</T>}
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm, alignItems: 'center' }}>
            {used && !gone ? <T v="caption" color={t.ink3}>{used}</T> : null}
            {m.defer && !gone ? <Pill label={mine ? L('需你本人回复', 'Needs you') : L(`需询问 ${friend.name} 本人`, `Ask ${friend.name} directly`)} tone="warn" /> : null}
            {mine && !gone && m.sentinel?.verdict === 'pass' && m.sentinel.via && m.sentinel.via !== 'rules' ? <Pill label={L('已通过 Doorman', 'Doorman checked')} tone="good" /> : null}
            <Meta m={m} />
          </View>
          {mine && !gone && (m.sentinel?.verdict === 'hold' || m.sentinel?.verdict === 'fail') ? (
            <T v="caption" color={m.sentinel.verdict === 'hold' ? t.gold : t.bad}>
              {m.sentinel.verdict === 'hold'
                ? L(`Doorman 已拦截原回答${m.sentinel.reasons.length ? `（${m.sentinel.reasons.map((r) => r.detail).join('；')}）` : ''}，请在收件箱的卡片中选择照发、修改或不发送`,
                  `Doorman held the original answer${m.sentinel.reasons.length ? ` (${m.sentinel.reasons.map((r) => r.detail).join('; ')})` : ''}: send, rewrite or drop it from the card in your inbox`)
                : L('Doorman 未能复查原回答，已替换为此固定回复', "Doorman couldn't review the original answer; this fixed reply was sent instead")}
            </T>
          ) : null}
          <Reactions m={m} mine={false} onPress={() => onLong(m)} />
          {mine ? <ReviewBox m={m} onReview={(a) => onReview(m, a)} onEdit={() => onEdit(m)} /> : null}
          <StatusLine m={m} onRetry={() => onRetry(m)} />
        </View>
      </Pressable>
    );
  }
  // text / ask：气泡，时间和送达状态在气泡右下角
  const isAsk = m.kind === 'ask';
  const gone = m.status === 'revoked';
  const bubble = (
    <View style={[styles.bubble, mine ? { alignSelf: 'flex-end', backgroundColor: t.surface2, borderBottomRightRadius: 6 }
      : { alignSelf: 'flex-start', backgroundColor: t.surface, borderBottomLeftRadius: 6 }, lit ? { borderWidth: 1.5, borderColor: t.gold } : null]}>
      {isAsk ? (
        <View style={[styles.askChip, { backgroundColor: t.goldSoft }]}>
          <AgentLens mine={!mine} size={16} />
          <T v="caption" color={t.gold} style={{ fontWeight: '700' }}>{mine ? L(`询问 ${friend.name} 的 Agent`, `Asked ${friend.name}'s Agent`) : L('向你的名片 Agent 提问', 'Asked your card agent')}</T>
        </View>
      ) : null}
      {m.quote && !gone ? <QuoteBox q={m.quote} friend={friend} onJump={onJump} /> : null}
      {gone ? <T v="callout" color={t.ink3}>{mine ? L('你已收回此消息', 'You withdrew this') : L(`${friend.name} 已收回此消息`, `${friend.name} withdrew this`)}</T>
        : <T v="body" selectable>{m.text}</T>}
      <Meta m={m} onDark={mine} />
    </View>
  );
  return (
    <View style={{ gap: 3 }}>
      {!gone ? <Pressable onLongPress={() => onLong(m)} delayLongPress={350}>{bubble}</Pressable> : bubble}
      <Reactions m={m} mine={mine} onPress={() => onLong(m)} />
      <StatusLine m={m} onRetry={() => onRetry(m)} />
    </View>
  );
}

/** 一行开关（好友设置、通知设置里用）。 */
export function SwitchRow({ icon, label, note, value, onChange, disabled }: {
  icon?: React.ReactNode; label: string; note?: string; value: boolean; onChange: (v: boolean) => void; disabled?: boolean;
}) {
  const t = useTheme();
  return (
    <View style={styles.switchRow}>
      {icon ? <View style={{ width: 28, alignItems: 'center' }}>{icon}</View> : null}
      <View style={{ flex: 1, gap: 2 }}>
        <T v="body">{label}</T>
        {note ? <T v="caption" color={t.ink3}>{note}</T> : null}
      </View>
      <Switch value={value} onValueChange={onChange} disabled={disabled} accessibilityLabel={label}
        trackColor={{ true: t.cyan, false: t.track }} thumbColor="#FFFFFF" />
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
  const run = (p: Promise<Friend>) => p.then((x) => { onChanged(x); }).catch((e) => showError(L('修改失败', "Couldn't update"), e));
  return (
    <View style={{ gap: space.md }}>
      <Pressable onPress={() => { close(); onAgents(); }} accessibilityRole="button"
        style={({ pressed }) => [styles.linkRow, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
        <View style={{ width: 46, height: 30 }}>
          <View style={{ position: 'absolute', left: 0, top: 1 }}><AgentLens mine /></View>
          <View style={[styles.lensRing, { backgroundColor: t.surface }]}><AgentLens mine={false} /></View>
        </View>
        <View style={{ flex: 1, gap: 2 }}>
          <T v="headline" style={{ fontSize: 15 }}>{L('Agent 之间', 'Agent to Agent')}</T>
          <T v="caption" color={t.ink3}>{L(`双方 Agent 代你与 ${f.name} 的对话，以及待你确认的卡片`, `What the Agents said for you and ${f.name}, and cards awaiting you`)}</T>
        </View>
        <ChevronRight size={16} color={t.ink3} />
      </Pressable>
      <View style={{ gap: space.xs }}>
        <T v="label" color={t.ink3}>{L('档位（名片 Agent 按此档位代你回答）', 'Tier (your card agent answers them by it)')}</T>
        <Segmented<Tier> value={tier} options={TIER_OPTS()} onChange={(v) => { setTier(v); run(fr.patchFriend(f.id, { tier: v })); }} />
        <T v="caption" color={t.ink3}>{L('对方无法看到自己所在的档位。各档位可查询的内容在「我的名片 Agent」中设置。', "They never see their tier. What each tier gets is set in My card agent.")}</T>
      </View>
      <View style={{ gap: space.xs }}>
        <T v="label" color={t.ink3}>{L('备注（仅你可见）', 'Your name for them (only you see it)')}</T>
        <View style={{ flexDirection: 'row', gap: space.sm, alignItems: 'center' }}>
          <TextInput value={alias} onChangeText={setAlias} placeholder={f.cardName} placeholderTextColor={t.ink3}
            style={[type.body, styles.field, { flex: 1, backgroundColor: t.surface, color: t.ink, borderColor: t.line }]} accessibilityLabel={L('备注', 'Name')} />
          <Btn label={L('保存', 'Save')} kind="quiet" onPress={() => run(fr.patchFriend(f.id, { alias: alias.trim() }))} />
        </View>
      </View>
      {supports('friends.prefs') && f.status === 'active' ? (
        <View style={[styles.switchCard, { backgroundColor: t.surface }]}>
          <SwitchRow icon={<Pin size={18} color={t.ink2} />} label={L('置顶', 'Pin')} value={!!f.pinned}
            onChange={(v) => run(fr.patchFriend(f.id, { pinned: v }))} />
          <View style={{ height: StyleSheet.hairlineWidth, backgroundColor: t.line, marginLeft: 40 }} />
          <SwitchRow icon={<BellOff size={18} color={t.ink2} />} label={L('免打扰', 'Mute')} note={L('不推送通知，未读不计入角标', "No notifications; unread doesn't count in the badge")}
            value={!!f.muted} onChange={(v) => run(fr.patchFriend(f.id, { muted: v }))} />
        </View>
      ) : null}
      <View style={{ flexDirection: 'row', gap: space.sm, alignItems: 'center' }}>
        <ShieldCheck size={16} color={t.good} />
        <T v="callout" color={t.ink2} style={{ flex: 1 }}>{L(`指纹 ${f.fingerprint}：与对方 app 中显示的一致即为本人`, `Fingerprint ${f.fingerprint}: if it matches what they see, it's really them`)}</T>
      </View>
      {f.status === 'active' || f.status === 'blocked' ? (
        <View style={{ gap: space.sm }}>
          <Btn label={f.status === 'blocked' ? L('解除屏蔽', 'Unblock') : L('屏蔽', 'Block')} kind="quiet" icon={<Ban size={16} color={t.ink} />}
            onPress={() => run(fr.blockFriend(f.id, f.status !== 'blocked'))} />
          <Btn label={L('删除好友', 'Remove friend')} kind="danger" icon={<UserX size={16} color={t.bad} />}
            onPress={() => confirm(L(`删除 ${f.name}？`, `Remove ${f.name}?`), L('系统将通知对方，此后双方无法互发消息。聊天记录保留在你这边。如需重新添加，需要新的邀请码。', "They're told, and neither of you can message the other after that. The chat stays on your side. To add them again you need a new invite."),
              L('删除', 'Remove'), () => { fr.removeFriend(f.id).then(() => { close(); onGone(); }).catch((e) => showError(L('删除失败', "Couldn't remove"), e)); })} />
        </View>
      ) : null}
    </View>
  );
}

/** 输入框现在在做什么：追问分享 / 改名片 Agent 的代答 / 让名片 Agent 去问对方的 / 回复一条 / 改自己发的一条。null = 普通发消息。 */
type Mode =
  | { k: 'ask'; mid: string; title: string }
  | { k: 'review'; m: FriendMsg }
  | { k: 'agent'; prev: A2AOut | null }
  | { k: 'reply'; m: FriendMsg }
  | { k: 'edit'; m: FriendMsg }
  | null;

const MSG_KINDS = ['text', 'share', 'ask', 'answer'];
/** 先画上的那条「在问」的 id 和时间（放在组件外：渲染里不调 Date.now()） */
const localStamp = () => ({ id: `local-${Date.now()}`, createdAt: new Date().toISOString() });
const snip = (s: string, n = 40) => { const x = s.replace(/\s+/g, ' ').trim(); return x.length > n ? `${x.slice(0, n)}…` : x; };

export function FriendChatScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const route = useRoute<any>();
  const id = route.params?.id as string;
  const focusId = typeof route.params?.focus === 'number' ? (route.params.focus as number) : Number(route.params?.focus) || null;
  const draftKey = `friend:${id}`;
  const sheet = useSheet();
  const [canPod, setCanPod] = useState(false);
  useEffect(() => { podFeatures().then((f) => setCanPod(f.materials)).catch(() => {}); }, []);
  const { reload } = useStore();
  // 正看着这段聊天：不为它弹小窗（store 的 bannerAllowed 认 friend:<id>）；看着的时候才标已读（已读回执会告诉对方）
  useThreadOnScreen(draftKey);
  const focused = useIsFocused();
  const [appActive, setAppActive] = useState(AppState.currentState === 'active');
  useEffect(() => { const sub = AppState.addEventListener('change', (st) => setAppActive(st === 'active')); return () => sub.remove(); }, []);
  const visible = focused && appActive;
  const actions = supports('friends.actions');
  const [friend, setFriend] = useState<Friend | null>(null);
  const [msgs, setMsgs] = useState<FriendMsg[]>([]);
  const [more, setMore] = useState(false);
  const [older, setOlder] = useState(false);
  const [agentOn, setAgentOn] = useState(false);
  const [err, setErr] = useState('');
  const [draft, setDraftRaw] = useState(() => loadDraft(draftKey));
  const [mode, setMode] = useState<Mode>(null);
  const [outs, setOuts] = useState<A2AOut[]>([]);
  const [log, setLog] = useState<CardLogItem[]>([]);
  const [inboxReady, setInboxReady] = useState(false);
  const [sending, setSending] = useState(false);
  const [newCount, setNewCount] = useState(0);
  const [lit, setLit] = useState<number | null>(null);
  // 合并转发：多选 / 「选这一段」时消息左边出勾，输入栏换成「加入转发篮 · 转发」
  const sel = useSelect();
  const canForward = useForwardSupported();
  const scroller = useRef<ScrollView>(null);
  const input = useRef<TextInput>(null);
  const root = useRef<View>(null);
  const bottom = useBottomInset(root);
  const lastId = useRef(0);
  const lastRead = useRef(0);
  const newest = useRef(0);            // 屏幕上最新一条朋友发来的（算「↓ N 条新消息」）
  const atBottom = useRef(true);
  const wantEnd = useRef(true);         // 内容高度变了以后滚到底（第一次打开、自己发了、在底部时来了新的）
  const scrolled = useRef(false);       // 第一次滚到底以后才认「滑到顶加载更早的」
  const prepend = useRef<{ h: number; y: number } | null>(null);
  const contentH = useRef(0);
  const offsetY = useRef(0);
  const ys = useRef<Record<number, number>>({});
  const stash = useRef('');             // 改一条之前输入框里的草稿，改完放回去
  const sought = useRef(false);

  const setDraft = useCallback((v: string | ((d: string) => string)) => setDraftRaw(v), []);
  // 草稿按好友存（普通消息和回复时；改一条、改代答时输入框里是原文，不存）
  useEffect(() => { if (!mode || mode.k === 'reply' || mode.k === 'ask') saveDraft(draftKey, draft); }, [draft, mode, draftKey]);

  const apply = useCallback((th: fr.FriendThread, full: boolean) => {
    setFriend(th.friend);
    setAgentOn(th.agent);
    setMsgs((cur) => mergeMsgs(full ? [] : cur, [...th.recent, ...th.messages]));
    for (const m of th.messages) lastId.current = Math.max(lastId.current, m.id);
    if (full) setMore(th.more ?? th.messages.length >= 60);  // 老服务器不报 more：满一页就当前面还有
    const incoming = th.messages.filter((m) => m.dir === 'in' && m.kind !== 'system' && m.id > newest.current);
    if (incoming.length) {
      if (full || !newest.current || atBottom.current) wantEnd.current = true;
      else setNewCount((n) => n + incoming.length);
      newest.current = Math.max(newest.current, ...incoming.map((m) => m.id));
    }
    if (!full && th.messages.some((m) => m.dir === 'out') && atBottom.current) wantEnd.current = true;
  }, []);
  const load = useCallback(() => fr.thread(id).then((th) => { apply(th, true); setErr(''); }).catch((e) => setErr(errText(e))), [id, apply]);
  useEffect(() => { load(); }, [load]);
  // 开着的时候每 4 秒看一眼有没有新的（和发出去的送到了没有、对方看了没有、有没有表情回应）
  useEffect(() => {
    const h = setInterval(() => { fr.thread(id, lastId.current).then((th) => apply(th, false)).catch(() => {}); }, 4000);
    return () => clearInterval(h);
  }, [id, apply]);
  // 看着的时候才标已读：屏幕上有了新的朋友发来的 → 标已读、角标跟着变（对方开着已读回执的话，它那边变 ✓✓）
  useEffect(() => {
    if (!visible || !friend) return;
    const top = msgs.reduce((a, m) => (m.dir === 'in' ? Math.max(a, m.id) : a), 0);
    if (top > lastRead.current) {
      lastRead.current = top;
      fr.markRead(friend.id).then(() => reload('unread')).catch(() => {});
    }
  }, [visible, msgs, friend, reload]);
  const hasA2A = !!friend?.caps.includes('a2a');
  const loadOuts = useCallback(() => fr.a2aOut(id, true).then((o) => setOuts((cur) => mergeOuts(cur.filter(isLocalOut), o))).catch(() => {}), [id]);
  useEffect(() => { if (hasA2A) loadOuts(); }, [hasA2A, loadOuts]);
  const waiting = outs.some((o) => !isLocalOut(o) && ['TASK_STATE_AUTH_REQUIRED', 'TASK_STATE_WORKING', 'TASK_STATE_SUBMITTED'].includes(o.state ?? ''));
  useEffect(() => {
    if (!waiting) return undefined;
    const h = setInterval(loadOuts, 8000);
    return () => clearInterval(h);
  }, [waiting, loadOuts]);
  // 对方 Agent 来问的（card_log 里 channel a2a 的）：折成一行进时间线；出了卡的先把收件箱读一遍，卡才画得出来
  const loadLog = useCallback(() => fr.cardLog(id).then((l) => {
    setLog(l);
    if (l.some((x) => x.inboxId)) reload('inbox', 'inboxRecent').catch(() => {}).finally(() => setInboxReady(true));
    else setInboxReady(true);
  }).catch(() => {}), [id, reload]);
  useEffect(() => { loadLog(); }, [loadLog]);
  useEffect(() => {
    if (!visible) return undefined;
    const h = setInterval(loadLog, 12000);
    return () => clearInterval(h);
  }, [visible, loadLog]);
  const rounds = useMemo(() => toRounds(log, []).filter((r): r is InRoundT => r.kind === 'in'), [log]);

  /** 往上翻：读更早的一页，插在前面，滚动位置不跳。 */
  const loadOlder = useCallback(async () => {
    const first = msgs[0]?.id;
    if (!first || older) return false;
    setOlder(true);
    try {
      const th = await fr.thread(id, undefined, first);
      prepend.current = { h: contentH.current, y: offsetY.current };
      setMsgs((cur) => mergeMsgs(cur, th.messages));
      setMore(th.more ?? th.messages.length >= 60);
      return th.messages.length > 0;
    } catch { return false; } finally { setOlder(false); }
  }, [id, msgs, older]);

  /** 跳到某一条（搜索、引用点过来）：没读到就往前翻，找到了滚过去、闪一下。 */
  const jumpTo = useCallback((rowId: number) => {
    const y = ys.current[rowId];
    if (y !== undefined) {
      scroller.current?.scrollTo({ y: Math.max(0, y - 120), animated: true });
      setLit(rowId);
      setTimeout(() => setLit((x) => (x === rowId ? null : x)), 2500);
      return true;
    }
    return false;
  }, []);
  const onJump = (mid: string) => {
    const m = msgs.find((x) => x.mid === mid);
    if (m) jumpTo(m.id);
  };
  // 从搜索点进来的：读到那一条为止（最多往前翻 15 页），再滚过去
  useEffect(() => {
    if (!focusId || sought.current || !msgs.length) return;
    if (msgs.some((m) => m.id === focusId)) {
      sought.current = true;
      wantEnd.current = false;
      const h = setTimeout(() => jumpTo(focusId), 250);
      return () => clearTimeout(h);
    }
    if (!more || older || msgs[0].id < focusId) return undefined;
    const h = setTimeout(() => { loadOlder(); }, 0);
    return () => clearTimeout(h);
  }, [focusId, msgs, more, older, loadOlder, jumpTo]);

  const onContentSize = (_w: number, h: number) => {
    const before = contentH.current;
    contentH.current = h;
    if (prepend.current) {  // 前面插了更早的：往下挪同样的高度，看着的那条不动
      const p = prepend.current;
      prepend.current = null;
      scroller.current?.scrollTo({ y: p.y + (h - p.h), animated: false });
      return;
    }
    if (wantEnd.current && !(focusId && !sought.current)) {
      wantEnd.current = false;
      scroller.current?.scrollToEnd({ animated: scrolled.current && before > 0 });
      setTimeout(() => { scrolled.current = true; }, 400);
    }
  };
  const onScroll = (e: NativeSyntheticEvent<NativeScrollEvent>) => {
    const { contentOffset, layoutMeasurement, contentSize } = e.nativeEvent;
    offsetY.current = contentOffset.y;
    atBottom.current = contentOffset.y + layoutMeasurement.height >= contentSize.height - 80;
    if (atBottom.current && newCount) setNewCount(0);
    if (contentOffset.y < 60 && scrolled.current && more && !older && !prepend.current) loadOlder();
  };
  const toEnd = () => { setNewCount(0); scroller.current?.scrollToEnd({ animated: true }); };

  const put = (m: FriendMsg) => { lastId.current = Math.max(lastId.current, m.id); setMsgs((cur) => mergeMsgs(cur, [m])); };
  const askAgent = (text: string, prev: A2AOut | null) => {
    if (!friend) return;
    // 先画上「在问」，对方的名片 agent 要调模型，回来可能要十几秒；这期间照样能发别的
    const tmp: A2AOut = { ...localStamp(), friend: friend.id, contextId: prev?.contextId ?? null, taskId: prev?.taskId ?? null, state: 'local',
      text, reply: null, outcome: '', usedLabel: '', updatedAt: '', later: false };
    wantEnd.current = true;
    setOuts((cur) => mergeOuts(cur, [tmp]));
    setDraft('');
    setMode(null);
    fr.a2aSend(friend.id, text, prev)
      // 同一个任务里前面那几条：算「后来又接着说了」（进度和按钮只画在最新那条上）
      .then((o) => setOuts((cur) => mergeOuts(cur.filter((x) => x.id !== tmp.id).map((x) => (o.taskId && x.taskId === o.taskId ? { ...x, later: true } : x)), [o])))
      .catch((e) => {
        setOuts((cur) => cur.filter((x) => x.id !== tmp.id));
        setDraft((d) => d || text);
        setMode({ k: 'agent', prev });
        showError(L('提问失败', "Couldn't ask"), e);
      });
  };
  /** 进入「改一条」：先把输入框里没发的收起来，改完放回去。 */
  const startEditing = (next: Mode & { m: FriendMsg }) => {
    if (!mode || mode.k === 'reply' || mode.k === 'ask') stash.current = draft;
    setMode(next);
    setDraft(next.m.text);
    setTimeout(() => input.current?.focus(), 250);
  };
  const leaveMode = () => {
    if (mode && (mode.k === 'edit' || mode.k === 'review')) setDraft(stash.current);
    setMode(null);
  };
  const submit = async () => {
    const text = draft.trim();
    if (!text || sending || !friend) return;
    if (mode?.k === 'agent') { askAgent(text, mode.prev); return; }
    setSending(true);
    try {
      if (mode?.k === 'review') put(await fr.review(mode.m.id, 'edit', text));
      else if (mode?.k === 'edit') put(await fr.editMsg(mode.m.id, text));
      else {
        wantEnd.current = true;
        if (mode?.k === 'ask') put(await fr.ask(friend.id, mode.mid, text));
        else put(await fr.sendText(friend.id, text, mode?.k === 'reply' ? mode.m.mid : undefined));
      }
      setDraft(mode?.k === 'edit' || mode?.k === 'review' ? stash.current : '');
      setMode(null);
      setNewCount(0);
    } catch (e) { showError(L('发送失败', "Couldn't send"), e); } finally { setSending(false); }
  };
  const webEnter = Platform.OS === 'web' ? (e: NativeSyntheticEvent<TextInputKeyPressEventData>) => {
    const k = e.nativeEvent as unknown as KeyboardEvent;
    if (k.key !== 'Enter' || k.shiftKey || k.isComposing || k.keyCode === 229) return;
    e.preventDefault();
    submit();
  } : undefined;
  const onReview = (m: FriendMsg, a: 'ok' | 'revoke') => {
    const go = () => fr.review(m.id, a).then(put).catch((e) => showError(L('操作失败', "Couldn't do that"), e));
    if (a === 'revoke') confirm(L('收回此代答？', 'Withdraw this answer?'), L('对方那边的内容将被清除，并显示为已收回。', 'It is removed on their side and shows as withdrawn.'), L('收回', 'Withdraw'), go);
    else go();
  };
  const onEdit = (m: FriendMsg) => startEditing({ k: 'review', m });
  const onAsk = (m: FriendMsg) => { if (m.share) setMode({ k: 'ask', mid: m.mid, title: m.share.title }); };
  const onMore = (o: A2AOut) => { setMode({ k: 'agent', prev: o }); setTimeout(() => input.current?.focus(), 50); };
  const toggleAgent = () => { if (mode?.k === 'agent') setMode(null); else { if (mode?.k === 'edit' || mode?.k === 'review') setDraft(stash.current); setMode({ k: 'agent', prev: null }); } };
  const onRetry = (m: FriendMsg) => { fr.retry(m.id).then(put).catch((e) => showError(L('重试失败', "Couldn't retry"), e)); };
  const withdraw = (m: FriendMsg) => confirm(L('收回此消息？', 'Withdraw this?'), L('对方那边的内容将被清除，并显示为已收回。', 'It is removed on their side and shows as withdrawn.'), L('收回', 'Withdraw'),
    () => { fr.revokeMsg(m.id).then(put).catch((e) => showError(L('收回失败', "Couldn't withdraw"), e)); });
  const hide = (m: FriendMsg) => confirm(L('删除此消息？', 'Delete this message?'), L('仅从你的聊天记录中删除，对方那边不受影响。', 'Only removes it from your chat; it stays on their side.'), L('删除', 'Delete'),
    () => { fr.hideMsg(m.id).then(() => setMsgs((cur) => cur.filter((x) => x.id !== m.id))).catch((e) => showError(L('删除失败', "Couldn't delete"), e)); });
  const react = (m: FriendMsg, e: string) => {
    Haptics.selectionAsync().catch(() => {});
    fr.react(m.id, m.reactions?.me === e ? null : e).then(put).catch((x) => showError(L('操作失败', "Couldn't do that"), x));
  };
  // 长按一条：表情回应、复制、回复、编辑（自己发的文字）、转发（转发 / 选这一段 / 多选 / 加入转发篮）、放进播客、收回（你发的）、只在我这删除
  const onLong = (m: FriendMsg) => {
    const gone = m.status === 'revoked';
    if (gone || !friend) return;
    const active = friend.status === 'active';
    const isMsg = MSG_KINDS.includes(m.kind);
    const canReact = actions && active && isMsg && friend.caps.includes('react');
    const canReply = active && isMsg;
    const canEditMine = actions && active && m.dir === 'out' && m.kind === 'text' && m.by === 'person';
    const canWithdraw = m.dir === 'out' && (m.kind === 'text' || m.kind === 'share');
    const podOk = canPod && (m.kind === 'text' || m.kind === 'ask' || m.kind === 'answer') && !!m.text.trim();
    const copyText = m.kind === 'share' ? (m.share?.text || m.share?.quote || m.share?.title || '') : m.text;
    const canFwd = canForward && m.kind !== 'system' && (!!m.text.trim() || m.kind === 'share');
    const ref = fw.friendRef(id, m.id);
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});
    sheet.open({ title: m.dir === 'out' ? L('此消息', 'This message') : L(`${friend.name} 的消息`, `From ${friend.name}`), content: (close) => (
      <View style={{ gap: space.sm }}>
        {canReact ? (
          <View style={[styles.reactBar, { backgroundColor: t.surface }]} accessibilityRole="toolbar" accessibilityLabel={L('表情回应', 'Reactions')}>
            {fr.REACTIONS.map((e) => {
              const on = m.reactions?.me === e;
              return (
                <Pressable key={e} onPress={() => { close(); react(m, e); }} accessibilityRole="button" accessibilityState={{ selected: on }}
                  accessibilityLabel={on ? L(`取消 ${e}`, `Remove ${e}`) : e}
                  style={({ pressed }) => [styles.reactBtn, { backgroundColor: on ? t.goldSoft : 'transparent', opacity: pressed ? 0.6 : 1 }]}>
                  <Text style={{ fontSize: 26 }}>{e}</Text>
                </Pressable>
              );
            })}
          </View>
        ) : null}
        {copyText.trim() ? <Action icon={Copy} label={L('复制', 'Copy')} onPress={() => { close(); Clipboard.setStringAsync(copyText).catch(() => {}); }} /> : null}
        {canReply ? <Action icon={Reply} label={L('回复', 'Reply')} note={L('引用这条回复，对方会看到原话', 'Reply quoting this; they see the original line')}
          onPress={() => { close(); setMode({ k: 'reply', m }); setTimeout(() => input.current?.focus(), 250); }} /> : null}
        {canEditMine ? <Action icon={Pencil} label={L('编辑', 'Edit')} note={L('修改后对方那边显示「已编辑」', 'They see it marked as edited')}
          onPress={() => { close(); startEditing({ k: 'edit', m }); }} /> : null}
        {canFwd ? forwardActions({
          forward: () => { close(); nav.navigate('Forward', { refs: [ref] }); },
          around: () => { close(); sel.around(ref); },
          pick: () => { close(); sel.start(ref); },
          basket: () => { close(); fw.addToBasket([ref]).then(setBasketCount).catch((e) => showError(L('添加失败', "Couldn't add it"), e)); },
        }).map((a) => <Action key={a.label} {...a} />) : null}
        {podOk ? (
          <Action icon={Mic} label={L('添加到播客', 'Add to a podcast')} note={m.dir === 'in'
            ? L(`用作一期播客的素材；${friend.name}的发言仅用于该期，不会原文写入库或世界树`, `Use it in an episode; ${friend.name}'s words stay in that episode, never quoted into the vault or memory tree`)
            : L('用作一期播客的素材：录制前准备、主持人提问和整理都会参考', "Use it in an episode: prep, the host's questions and the note draw on it")}
            onPress={() => sheet.open({ title: L('选择一期', 'Which episode'), content: (c) => (
              <PutInPodcast kind="friend" target={`${id}:${m.id}`} close={c} onOpen={(e) => openEpisode(nav, e)} />
            ) })} />
        ) : null}
        {canWithdraw ? <Action icon={Undo2} label={L('收回', 'Withdraw')} danger note={L('对方那边的内容将被清除，并显示为已收回', 'It disappears on their side')} onPress={() => { close(); withdraw(m); }} /> : null}
        {actions ? <Action icon={Trash2} label={L('删除', 'Delete')} danger note={L('仅从你的聊天记录中删除，对方那边不受影响', 'Only removes it from your chat; it stays on their side')}
          onPress={() => { close(); hide(m); }} /> : null}
      </View>
    ) });
  };
  const openSheet = () => {
    if (!friend) return;
    sheet.open({ title: friend.name, content: (close) => (
      <FriendSheet f={friend} close={close} onChanged={setFriend} onGone={() => nav.goBack()} onAgents={() => nav.navigate('FriendAgents', { id: friend.id })} />
    ) });
  };

  const active = friend?.status === 'active';
  const canAgent = !!friend && active && hasA2A;
  // 连不上对方服务器（网络错误，不是对方拒收）：聊天顶上说一句原因，别让人以为一直在「发送中」
  const unreachable = active && msgs.some((m) => retrying(m) && /^(network\b|50[234]\b)/.test(m.error ?? ''));
  const lines = timeline(msgs, outs, rounds);
  const openAgents = () => { if (friend) nav.navigate('FriendAgents', { id: friend.id }); };
  const sub = friend ? [friend.tierName, friend.agent ? L(`有 Agent · 可追问 ${friend.name} 的分享`, `Has an Agent · you can ask about ${friend.name}'s shares`) : ''].filter(Boolean).join(' · ') : undefined;
  const agentAsk = mode?.k === 'agent' ? mode : null;
  const bar = mode ? (
    mode.k === 'agent' ? (mode.prev ? L(`继续与 ${friend?.name} 的 Agent 沟通：「${mode.prev.text}」`, `Continuing with ${friend?.name}'s Agent: "${mode.prev.text}"`)
      : L(`由你的名片 Agent 询问 ${friend?.name} 的 Agent；需要 ${friend?.name} 本人决定的事项，将转交 ${friend?.name} 确认`, `Your card agent asks ${friend?.name}'s Agent; anything ${friend?.name} has to decide goes to ${friend?.name}`))
      : mode.k === 'review' ? L('修改名片 Agent 的代答（发送后替换原回答）', "Rewriting your card agent's answer (replaces it)")
        : mode.k === 'edit' ? L(`编辑：${snip(mode.m.text)}`, `Editing: ${snip(mode.m.text)}`)
          : mode.k === 'reply' ? L(`回复 ${mode.m.dir === 'out' ? '你' : friend?.name}：${snip(mode.m.kind === 'share' ? mode.m.share?.title ?? '' : mode.m.text)}`,
            `Replying to ${mode.m.dir === 'out' ? 'you' : friend?.name}: ${snip(mode.m.kind === 'share' ? mode.m.share?.title ?? '' : mode.m.text)}`)
            : L(`追问：${mode.title}（由 ${friend?.name} 的名片 Agent 回答）`, `Ask about: ${mode.title} (${friend?.name}'s card agent answers)`)
  ) : '';
  const BarIcon = mode?.k === 'reply' ? Reply : mode?.k === 'edit' || mode?.k === 'review' ? Pencil : null;
  return (
    <Screen>
      <NavHeader title={friend?.name ?? L('好友', 'Friend')} sub={sub} onBack={() => nav.goBack()}
        right={friend ? <Pressable onPress={openSheet} hitSlop={10} accessibilityRole="button" accessibilityLabel={L('好友设置', 'Friend settings')} style={{ padding: 6 }}><Ellipsis size={22} color={t.ink} /></Pressable> : undefined} />
      <View ref={root} style={{ flex: 1, paddingBottom: bottom.home }} onLayout={bottom.onLayout}>
        <ChatScroll ref={scroller} offset={bottom.offset} style={{ flex: 1 }} contentContainerStyle={{ padding: space.lg, gap: space.lg }}
          onScroll={onScroll} scrollEventThrottle={64} onContentSizeChange={onContentSize}
          keyboardShouldPersistTaps="handled" keyboardDismissMode={dismissMode} refreshControl={<PullRefresh onRefresh={load} />}>
          {err ? <Card><T v="callout" color={t.bad}>{L(`无法加载：${err}`, `Couldn't load: ${err}`)}</T></Card> : null}
          {older ? <ActivityIndicator color={t.ink3} /> : null}
          {route.params?.unreachable ? <PublicWarn fix={route.params?.fix as string | undefined} /> : null}
          {friend && unreachable ? (
            <View style={[styles.notice, { backgroundColor: t.surface, borderColor: t.line }]}>
              <TriangleAlert size={16} color={t.warn} />
              <T v="callout" color={t.ink2} style={{ flex: 1 }}>
                {L(`暂时无法连接 ${friend.name} 的服务器，消息尚未送达。对方的服务器可能离线，或尚未开启公网访问；连接恢复后将自动送达，消息最多保留 3 天。`,
                  `${friend.name}'s server can't be reached right now, so your messages haven't been delivered. Their server may be offline or not open to the internet yet; messages go through automatically once it's reachable, and are kept for up to 3 days.`)}
              </T>
            </View>
          ) : null}
          {!friend && !err ? <ActivityIndicator color={t.gold} style={{ marginTop: space.xl }} /> : null}
          {friend && !msgs.some((m) => m.kind !== 'system') && !outs.length && !rounds.length ? (
            <T v="callout" color={t.ink3} style={{ textAlign: 'center' }}>
              {canAgent ? L(`发送消息，或点按输入框左侧的圆形按钮，让你的名片 Agent 询问 ${friend.name} 的 Agent（例如约定时间）。`, `Send a message, or tap the round button left of the text field to have your card agent ask ${friend.name}'s Agent (for example, to find a time to meet).`)
                : L('发送消息，或在对话中长按一条消息 →「分享」发送给对方。', 'Send a message, or long-press a message in a chat → Share to send it here.')}
            </T>
          ) : null}
          {friend ? lines.map((x) => {
            if (x.kind === 'day') return <DaySep key={x.key} label={x.label} />;
            if (x.kind === 'out') return <AskOutCard key={x.o.id} o={x.o} name={friend.name} onOpen={isLocalOut(x.o) ? undefined : openAgents} onMore={active ? () => onMore(x.o) : undefined} />;
            if (x.kind === 'round') return <AgentRound key={x.r.key} r={x.r} friend={friend} ready={inboxReady} />;
            const m = x.m;
            const view = <MsgView m={m} friend={friend} onAsk={onAsk} onReview={onReview} onEdit={onEdit} onRetry={onRetry} onLong={onLong} onJump={onJump} lit={lit === m.id} />;
            const at = (e: LayoutChangeEvent) => { ys.current[m.id] = e.nativeEvent.layout.y; };
            if (!sel.on) return <View key={m.id} onLayout={at}>{view}</View>;
            // 多选时：能转的消息左边一个勾，点整行勾上 / 去掉
            const ok = m.kind !== 'system' && m.status !== 'revoked' && (!!m.text.trim() || m.kind === 'share');
            const ref = ok ? fw.friendRef(id, m.id) : null;
            return (
              <Pressable key={m.id} onLayout={at} onPress={ref ? () => sel.toggle(ref) : undefined} disabled={!ref} accessibilityRole="checkbox" accessibilityState={{ checked: !!ref && sel.has(ref) }}
                style={{ flexDirection: 'row', gap: space.sm, alignItems: 'flex-start' }}>
                {ref ? <SelectMark on={sel.has(ref)} /> : <View style={{ width: 22 }} />}
                <View style={{ flex: 1, minWidth: 0 }} pointerEvents="none">{view}</View>
              </Pressable>
            );
          }) : null}
          {friend && !agentOn && msgs.some((m) => m.kind === 'ask' && m.dir === 'in') ? (
            <T v="caption" color={t.ink3} style={{ textAlign: 'center' }}>{L('你的名片 Agent 尚未开启：对方的提问需要你亲自回复。', "Your card agent is off: answer their questions yourself.")}</T>
          ) : null}
        </ChatScroll>
        {newCount > 0 ? (
          <Pressable onPress={toEnd} accessibilityRole="button" accessibilityLabel={L(`${newCount} 条新消息，滚动到底部`, `${newCount} new, scroll to the bottom`)}
            style={({ pressed }) => [styles.newPill, { backgroundColor: t.surface, borderColor: t.line, bottom: 92 + bottom.home, opacity: pressed ? 0.8 : 1 }]}>
            <ArrowDown size={15} color={t.cyan} />
            <T v="callout" color={t.cyan} style={{ fontWeight: '700' }}>{L(`${newCount} 条新消息`, `${newCount} new`)}</T>
          </Pressable>
        ) : null}
        {friend ? (
          <KeyboardSticky offset={bottom.offset} style={[styles.composerWrap, { borderTopColor: t.line, backgroundColor: t.bg }]}>
            {!active ? (
              <T v="callout" color={t.ink3} style={{ padding: space.md, textAlign: 'center' }}>
                {friend.status === 'blocked' ? L('已屏蔽。可在右上角「…」中解除。', 'Blocked. Unblock from … at the top right.') : L('你们已不是好友，无法发送消息。', "You're no longer friends; messages can't be sent.")}
              </T>
            ) : sel.on ? (
              <SelectBar n={sel.refs.length} busy={sel.busy} onCancel={sel.stop}
                onBasket={() => { fw.addToBasket(sel.refs).then((n) => { setBasketCount(n); sel.stop(); }).catch((e) => showError(L('添加失败', "Couldn't add it"), e)); }}
                onForward={() => { nav.navigate('Forward', { refs: sel.refs, gist: sel.gist || undefined }); sel.stop(); }} />
            ) : (
              <>
                <BasketPill onPress={() => nav.navigate('Forward', { basket: true })} />
                {mode ? (
                  <View style={{ paddingHorizontal: space.md, paddingTop: space.sm }}>
                    <View style={[styles.quote, { backgroundColor: t.goldSoft }]}>
                      {agentAsk ? <AgentLens mine size={16} /> : BarIcon ? <BarIcon size={14} color={t.gold} /> : null}
                      <T v="callout" numberOfLines={agentAsk ? 2 : 1} style={{ flex: 1, fontSize: 13 }}>{bar}</T>
                      <Pressable onPress={leaveMode} hitSlop={10} accessibilityRole="button"
                        accessibilityLabel={agentAsk ? L('取消询问', "Don't ask their Agent") : L('取消', 'Cancel')}>
                        <X size={14} color={t.ink3} />
                      </Pressable>
                    </View>
                  </View>
                ) : null}
                <View style={styles.composer}>
                  {canAgent ? (
                    <Pressable onPress={toggleAgent} accessibilityRole="button" accessibilityState={{ selected: !!agentAsk }}
                      accessibilityLabel={L(`让名片 Agent 询问 ${friend.name} 的 Agent`, `Have your card agent ask ${friend.name}'s Agent`)}
                      style={({ pressed }) => [styles.agentBtn, { borderColor: agentAsk ? t.gold : t.line, backgroundColor: agentAsk ? t.goldSoft : 'transparent', opacity: pressed ? 0.7 : 1 }]}>
                      <AgentLens mine size={26} />
                    </Pressable>
                  ) : null}
                  <TextInput ref={input} value={draft} onChangeText={setDraft} multiline numberOfLines={1} onKeyPress={webEnter}
                    onSubmitEditing={submit} submitBehavior="submit" returnKeyType="send" enablesReturnKeyAutomatically
                    placeholder={agentAsk ? (agentAsk.prev ? L('例如：周五晚上可以吗？', 'e.g. How about Friday evening?') : L(`例如：${friend.name} 本周哪天晚上有空？`, `e.g. Which evenings is ${friend.name} free this week?`))
                      : mode?.k === 'ask' ? L('输入问题…', 'Ask a question…') : L(`发送给 ${friend.name}`, `Message ${friend.name}`)} placeholderTextColor={t.ink3}
                    accessibilityLabel={agentAsk ? L('要名片 Agent 询问的内容', 'What your card agent should ask') : L('消息输入框', 'Message')}
                    style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink }, agentAsk ? { borderWidth: 1, borderColor: t.gold } : null]} />
                  <Pressable onPress={submit} disabled={!draft.trim() || (sending && !agentAsk)} accessibilityRole="button"
                    accessibilityLabel={agentAsk ? L('询问', 'Ask') : mode?.k === 'edit' || mode?.k === 'review' ? L('保存', 'Save') : L('发送', 'Send')}
                    style={[styles.send, { backgroundColor: draft.trim() && (!sending || agentAsk) ? t.goldFill : t.surface2 }]}>
                    {mode?.k === 'edit' || mode?.k === 'review' ? <Check size={20} color={draft.trim() && !sending ? t.onGold : t.ink3} />
                      : <ArrowUp size={20} color={draft.trim() && (!sending || agentAsk) ? t.onGold : t.ink3} />}
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

// —— 设置 → 通知（好友消息的推送、通知里显示内容、已读回执，按好友开关；2026-10-05）——

export function NotifySettingsScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const [p, setP] = useState<fr.FriendPrefs | null>(null);
  const [err, setErr] = useState('');
  const load = useCallback(() => fr.prefs().then((x) => { setP(x); setErr(''); }).catch((e) => setErr(errText(e))), []);
  useEffect(() => { load(); }, [load]);
  const set = (b: Partial<Pick<fr.FriendPrefs, 'notify' | 'preview' | 'receipts'>>) => {
    if (p) setP({ ...p, ...b });  // 先画上，失败了再读回来
    fr.patchPrefs(b).then(setP).catch((e) => { showError(L('保存失败', "Couldn't save"), e); load(); });
  };
  const mute = (id: string, muted: boolean) => {
    if (p) setP({ ...p, friends: p.friends.map((f) => (f.id === id ? { ...f, muted } : f)) });
    fr.patchFriend(id, { muted }).catch((e) => { showError(L('保存失败', "Couldn't save"), e); load(); });
  };
  const sw = (value: boolean, onChange: (v: boolean) => void, label: string, disabled?: boolean) => (
    <Switch value={value} onValueChange={onChange} disabled={disabled} accessibilityLabel={label} trackColor={{ true: t.cyan, false: t.track }} thumbColor="#FFFFFF" />
  );
  return (
    <Screen>
      <SettingsHeader title={L('通知', 'Notifications')} onBack={() => nav.goBack()} />
      <ScrollView contentContainerStyle={{ paddingBottom: space.xxl, paddingTop: 4 }} refreshControl={<PullRefresh onRefresh={load} />}>
        {err ? <Card style={{ margin: space.lg }}><T v="callout" color={t.bad}>{L(`无法加载：${err}`, `Couldn't load: ${err}`)}</T></Card> : null}
        {!p && !err ? <ActivityIndicator color={t.gold} style={{ marginTop: space.xl }} /> : null}
        {p ? (
          <>
            <GroupLabel>{L('好友消息', 'Friend messages')}</GroupLabel>
            <Group>
              <Row first icon={<Bell size={22} color={t.cyan} />} title={L('好友消息通知', 'Notify me')} right={sw(p.notify, (v) => set({ notify: v }), L('好友消息通知', 'Notify me'))} />
              <Row icon={<Eye size={22} color={p.notify ? t.cyan : t.ink3} />} title={L('通知显示消息内容', 'Show message text')} disabled={!p.notify}
                right={sw(p.preview, (v) => set({ preview: v }), L('通知显示消息内容', 'Show message text'), !p.notify)} />
            </Group>
            <GroupNote>{L('关闭「显示消息内容」后，通知只显示「对方名字 · 发来一条消息」，消息原文不经过 Apple 或 Google 的推送服务。',
              "With message text off, a notification only says who sent something; the text never goes through Apple's or Google's push service.")}</GroupNote>
            <GroupLabel>{L('隐私', 'Privacy')}</GroupLabel>
            <Group>
              <Row first icon={<CheckCheck size={22} color={t.cyan} />} title={L('已读回执', 'Read receipts')} right={sw(p.receipts, (v) => set({ receipts: v }), L('已读回执', 'Read receipts'))} />
            </Group>
            <GroupNote>{L('开启时，好友看过你的消息后显示两个勾。关闭后，对方看不到你是否已读，你也看不到对方是否已读。',
              "When on, two ticks show once a friend has seen your message. Turn it off and they can't see whether you've read theirs, and you can't see theirs either.")}</GroupNote>
            {p.friends.length ? (
              <>
                <GroupLabel>{L('按好友', 'By friend')}</GroupLabel>
                <Group>
                  {p.friends.map((f, i) => (
                    <Row key={f.id} first={i === 0} icon={<FriendAvatar id={f.id} name={f.name} size={30} />} title={f.name}
                      sub={f.muted ? L('免打扰：不推送，未读不计入角标', "Muted: no notifications, not in the badge") : undefined}
                      right={sw(!f.muted, (v) => mute(f.id, !v), L(`${f.name} 的通知`, `Notifications from ${f.name}`), !p.notify)} />
                  ))}
                </Group>
              </>
            ) : null}
          </>
        ) : null}
      </ScrollView>
    </Screen>
  );
}

// —— 第一次连上服务器：朋友怎么称呼你（在「接上常用的」之前） ——

/** 连接页第一次连上以后先到这里：服务器还没有名字（user_name）就请他设一个，加朋友、名片、agent 称呼都用它；
 * 已经有了、老服务器（/api/card 没有 name）或者读不到，直接去下一步。 */
export function MyNameScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { appName } = useStore();
  const [suggest, setSuggest] = useState<string | null>(null);
  const [v, setV] = useState('');
  const [busy, setBusy] = useState(false);
  const next = useCallback(() => nav.reset({ index: 0, routes: [{ name: 'Starter' }] }), [nav]);
  useEffect(() => {
    fr.card().then((c) => {
      if (c.name === undefined || c.name) next();
      else { setSuggest(c.suggest ?? ''); setV(c.suggest ?? ''); }
    }).catch(() => next());
  }, [next]);
  const save = async () => {
    const name = v.trim();
    if (!name || busy) return;
    setBusy(true);
    try { await fr.setMyName(name); next(); } catch (e) { showError(L('保存失败', "Couldn't save"), e); setBusy(false); }
  };
  if (suggest === null) return <Screen><ActivityIndicator style={{ marginTop: space.xxl }} color={t.ink3} /></Screen>;
  return (
    <Screen>
      <ScrollView contentContainerStyle={{ flexGrow: 1, paddingBottom: space.xl }} automaticallyAdjustKeyboardInsets keyboardShouldPersistTaps="handled">
        <View style={{ paddingHorizontal: space.xl, paddingTop: space.xl, gap: 8 }}>
          <T v="callout" color={t.cyan} style={{ fontWeight: '600' }}>{L('开始之前', 'Before you start')}</T>
          <T v="largeTitle" style={{ fontSize: 28 }}>{L('好友如何称呼你', 'What should friends call you?')}</T>
          <T v="body" color={t.ink2}>{L(`此名称显示在你的名片上，添加好友时对方可以看到；${appName} 也会以此称呼你。之后可在「好友 → 我的名片 Agent」中修改。`,
            `This name is on your card, and friends see it when you add each other; ${appName} calls you by it too. Change it later in Friends → My card agent.`)}</T>
        </View>
        <TextInput value={v} onChangeText={setV} onSubmitEditing={save} returnKeyType="done" maxLength={40} autoCorrect={false} autoFocus
          placeholder={L('名字或昵称', 'Your name or nickname')} placeholderTextColor={t.ink3} accessibilityLabel={L('对外名称', 'Name friends see')}
          style={[type.title, styles.bigField, { backgroundColor: t.surface, color: t.ink }]} />
        <View style={{ flex: 1 }} />
        <Pressable onPress={save} disabled={!v.trim() || busy} accessibilityRole="button"
          style={({ pressed }) => [styles.primary, { backgroundColor: v.trim() ? t.cyan : t.surface2, opacity: pressed || busy ? 0.7 : 1 }]}>
          {busy ? <ActivityIndicator color="#FFFFFF" /> : <T v="headline" color={v.trim() ? '#FFFFFF' : t.ink3}>{L('继续', 'Continue')}</T>}
        </Pressable>
        <Pressable onPress={next} accessibilityRole="button" style={{ height: 44, alignItems: 'center', justifyContent: 'center', marginTop: 6 }}>
          <T v="callout" color={t.ink2}>{L('跳过', 'Skip for now')}</T>
        </Pressable>
      </ScrollView>
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
    if (!code) { setErr(L('无法识别邀请码：邀请码应为 …/f/i/… 格式的链接。', "Not a valid invite: it should be a …/f/i/… link.")); return; }
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
    try { setMade(await fr.newInvite({ note: note.trim() || undefined, tier })); loadHome(); } catch (e) { showError(L('生成失败', "Couldn't create invite"), e); } finally { setBusy(false); }
  };
  const copy = async (text: string) => { await Clipboard.setStringAsync(text); setCopied(L('已复制，发送给对方即可', 'Copied. Send it to them.')); };
  const shareCode = async (code: string) => {
    const msg = L(`添加我为 OpenMousse 好友（此链接仅可使用一次）：${code}`, `Add me as a friend on OpenMousse (this link works once): ${code}`);
    try { await NativeShare.share(Platform.OS === 'ios' ? { message: msg } : { message: msg, title: L('邀请码', 'Invite') }); } catch { await copy(code); }
  };
  const fromClipboard = async () => { const s = await Clipboard.getStringAsync(); setPaste(s); if (s) lookUp(s); };
  const addThem = async () => {
    if (!who || busy) return;
    setBusy(true);
    try {
      const r = await fr.accept({ code: who.code, tier: theirTier, alias: alias.trim() || undefined });
      // 对方试着连回来、没连上：到了聊天页顶上说一句（你的公网访问没开好，对方的消息送不到你这里）
      nav.replace('FriendChat', { id: r.friend.id, ...(r.unreachable ? { unreachable: 1, fix: r.publicFix } : {}) });
    } catch (e) { showError(L('添加失败', "Couldn't add"), e); } finally { setBusy(false); }
  };

  const openInvites = home?.invites ?? [];
  return (
    <Screen>
      <NavHeader title={L('添加好友', 'Add a friend')} onBack={() => nav.goBack()} />
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl, gap: space.md }} automaticallyAdjustKeyboardInsets keyboardShouldPersistTaps="handled">
        {home?.why === 'no_name' ? <NameCard suggest={home.me.suggest} onSaved={() => { loadHome(); }} /> : home?.why === 'no_url' ? <NotReady /> : null}
        {home?.unreachable ? <PublicWarn fix={home.publicFix} relay={!!home.relay?.base && home.me.url === home.relay.base} /> : null}

        <SectionLabel>{L('使用对方的邀请码', "Use someone's invite")}</SectionLabel>
        <Card style={{ gap: space.sm }}>
          <TextInput value={paste} onChangeText={setPaste} placeholder={L('粘贴对方发来的链接', 'Paste the link they sent')} placeholderTextColor={t.ink3}
            autoCapitalize="none" autoCorrect={false} multiline style={[type.callout, styles.field, { color: t.ink, borderColor: t.line, minHeight: 64 }]}
            accessibilityLabel={L('邀请码', 'Invite link')} />
          <View style={{ flexDirection: 'row', gap: space.sm }}>
            <Btn label={L('粘贴', 'Paste')} kind="quiet" icon={<ClipboardPaste size={16} color={t.ink} />} onPress={fromClipboard} flex />
            <Btn label={busy && !who ? L('正在查询…', 'Checking…') : L('查看对方', 'Look up')} kind="quiet" onPress={() => lookUp(paste)} flex />
          </View>
          <T v="caption" color={t.ink3}>{L('用手机相机扫描对方的二维码会打开一个网页，点按「在 app 里打开」即可回到此处。', 'Scanning their QR code with the phone camera opens a page; tap "Open in the app" to come here.')}</T>
          {err ? <T v="callout" color={t.bad}>{err}</T> : null}
          {who ? (
            <View style={{ gap: space.sm, marginTop: space.xs }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.md }}>
                <FriendAvatar id={who.fingerprint} name={who.name} />
                <View style={{ flex: 1, gap: 2 }}>
                  <T v="headline">{who.name}</T>
                  <T v="caption" color={t.ink3}>{L(`指纹 ${who.fingerprint}`, `Fingerprint ${who.fingerprint}`)}{who.agent ? L(' · 有 Agent', ' · has an Agent') : ''}</T>
                </View>
              </View>
              {who.already ? (
                <Btn label={L('已是好友，打开对话', 'Already friends — open the chat')} kind="quiet" onPress={() => nav.replace('FriendChat', { id: who.already })} />
              ) : (
                <>
                  <T v="caption" color={t.ink3}>{L('选择档位（你的名片 Agent 按此档位回答对方）', 'Which tier (your card agent answers them by it)')}</T>
                  <Segmented<Tier> value={theirTier} options={TIER_OPTS()} onChange={setTheirTier} />
                  <TextInput value={alias} onChangeText={setAlias} placeholder={L(`备注（选填，默认为 ${who.name}）`, `Your name for them (optional, default ${who.name})`)} placeholderTextColor={t.ink3}
                    style={[type.body, styles.field, { color: t.ink, borderColor: t.line }]} accessibilityLabel={L('备注', 'Name')} />
                  <Btn label={busy ? L('正在添加…', 'Adding…') : L(`添加 ${who.name} 为好友`, `Add ${who.name}`)} icon={<UserPlus size={17} color={t.onGold} />} onPress={addThem} />
                </>
              )}
            </View>
          ) : null}
        </Card>

        <SectionLabel>{L('邀请好友', 'Invite someone')}</SectionLabel>
        {made?.code ? (
          <Card style={{ gap: space.md }}>
            {made.qr ? <QrView qr={made.qr} label={L('邀请码二维码', 'Invite QR code')} /> : null}
            <T v="callout" color={t.ink2} style={{ textAlign: 'center' }}>{L(`请对方用手机相机扫描，或将链接发送给对方。${daysLeft(made.expiresAt)} 天内有效，仅可使用一次。`, `They scan it with the phone camera, or you send them the link. Valid for ${daysLeft(made.expiresAt)} days, works once.`)}</T>
            <Text selectable style={[type.caption, { color: t.ink2, fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace' }]}>{made.code}</Text>
            <View style={{ flexDirection: 'row', gap: space.sm }}>
              <Btn label={L('发送', 'Send')} icon={<ShareIcon size={16} color={t.onGold} />} onPress={() => shareCode(made.code!)} flex />
              <Btn label={L('复制', 'Copy')} kind="quiet" icon={<Copy size={16} color={t.ink} />} onPress={() => copy(made.code!)} flex />
            </View>
            {copied ? <T v="callout" color={t.good}>{copied}</T> : null}
            {home?.me.fingerprint ? <T v="caption" color={t.ink3}>{L(`你的指纹 ${home.me.fingerprint}：对方添加你时看到的指纹应与此一致`, `Your fingerprint ${home.me.fingerprint}: they see the same when adding you`)}</T> : null}
            <Btn label={L('再生成一张', 'Create another')} kind="quiet" onPress={() => { setMade(null); setNote(''); }} />
          </Card>
        ) : (
          <Card style={{ gap: space.sm }}>
            <TextInput value={note} onChangeText={setNote} placeholder={L('邀请对象（仅你可见，例如「大学同学」）', 'Who is it for (only you see this)')} placeholderTextColor={t.ink3}
              maxLength={80} style={[type.body, styles.field, { color: t.ink, borderColor: t.line }]} accessibilityLabel={L('邀请对象', 'Who is it for')} />
            <T v="caption" color={t.ink3}>{L('对方加入后的档位', 'Which tier they land in')}</T>
            <Segmented<Tier> value={tier} options={TIER_OPTS()} onChange={setTier} />
            <Btn label={busy ? L('正在生成…', 'Creating…') : L('生成邀请码', 'Create invite')} icon={<QrCode size={17} color={t.onGold} />} onPress={make} />
          </Card>
        )}

        {openInvites.length ? (
          <>
            <SectionLabel>{L('未使用的邀请码', 'Unused invites')}</SectionLabel>
            <Card style={{ paddingVertical: space.xs }}>
              {openInvites.map((iv, i) => (
                <View key={iv.id} style={[styles.inviteRow, i ? { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line } : null]}>
                  <Link2 size={17} color={t.ink3} />
                  <View style={{ flex: 1, gap: 2 }}>
                    <T v="body" numberOfLines={1}>{iv.note || L('（无备注）', '(no note)')}</T>
                    <T v="caption" color={t.ink3}>{L(`${TIER_OPTS().find((x) => x.value === iv.tier)?.label} · 剩余 ${daysLeft(iv.expiresAt)} 天`, `${TIER_OPTS().find((x) => x.value === iv.tier)?.label} · ${daysLeft(iv.expiresAt)} days left`)}</T>
                  </View>
                  <Pressable onPress={() => fr.withdrawInvite(iv.id).then(loadHome).catch((e) => showError(L('收回失败', "Couldn't withdraw"), e))}
                    accessibilityRole="button" style={[styles.mini, { backgroundColor: t.surface2 }]}>
                    <T v="callout" style={{ fontWeight: '600' }}>{L('收回', 'Withdraw')}</T>
                  </Pressable>
                </View>
              ))}
            </Card>
            <T v="caption" color={t.ink3} style={{ paddingHorizontal: space.xs }}>{L('二维码仅在生成时显示一次。如未发送，请收回后重新生成。', "A QR code shows only when created; if you didn't send it, withdraw it and create another.")}</T>
          </>
        ) : null}
      </ScrollView>
    </Screen>
  );
}

// —— 我的名片 agent ——

const SCOPE_ROWS = (): { key: ScopeKey; label: string; values: Record<string, string> }[] => [
  { key: 'calendar', label: L('日程', 'Calendar'), values: { detail: L('详情', 'Details'), busy: L('仅忙闲', 'Free/busy'), none: L('不提供', 'Nothing') } },
  { key: 'status', label: L('近况', 'What you are up to'), values: { some: L('全部', 'All'), line: L('仅首行', 'First line'), none: L('不提供', 'Nothing') } },
  { key: 'shares', label: L('你的分享', 'What you shared'), values: { ask: L('可追问', 'Can ask'), view: L('仅查看', 'Read only'), public: L('仅公开内容', 'Public only') } },
  { key: 'notes', label: L('学习笔记', 'Study notes'), values: { view: L('可查看', 'Can read'), none: L('不提供', 'Nothing') } },
  { key: 'address', label: L('住址', 'Address'), values: { view: L('可查看', 'Can see'), none: L('不提供', 'Nothing') } },
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
  const [health, setHealth] = useState<fr.CardHealth | null>(null);
  const load = useCallback(() => Promise.all([
    fr.card().then((d) => { setData(d); setErr(''); }).catch((e) => setErr(errText(e))),
    fr.cardHealth().then(setHealth).catch(() => {}),
  ]), []);
  useEffect(() => { load(); }, [load]);

  const cycle = async (key: ScopeKey) => {
    if (!data) return;
    const opts = data.scopes[key];
    const cur = data.tiers[tier][key];
    const next = opts[(opts.indexOf(cur) + 1) % opts.length];
    try { setData(await fr.patchCard({ tiers: { [tier]: { [key]: next } } })); } catch (e) { showError(L('修改失败', "Couldn't update"), e); }
  };
  const [name, setName] = useState<string | null>(null);
  const [nameSaved, setNameSaved] = useState(false);
  const saveName = async () => {
    if (name == null || !name.trim() || name.trim() === data?.name) return;
    try { setData(await fr.setMyName(name.trim())); setName(null); setNameSaved(true); } catch (e) { showError(L('保存失败', "Couldn't save"), e); }
  };
  const saveStatus = async () => {
    if (status == null) return;
    try { setData(await fr.patchCard({ status })); setStatus(null); setSaved(true); } catch (e) { showError(L('保存失败', "Couldn't save"), e); }
  };
  const people = tier === 'stranger' ? null : data?.people[tier] ?? [];
  return (
    <Screen>
      <NavHeader title={L('我的名片 Agent', 'My card agent')} sub={L('代你对外回答 · 无法访问世界树', 'Speaks for you · never sees your memory tree')} onBack={() => nav.goBack()} />
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl, gap: space.md }} automaticallyAdjustKeyboardInsets keyboardShouldPersistTaps="handled"
        refreshControl={<PullRefresh onRefresh={load} />}>
        {err ? <Card><T v="callout" color={t.bad}>{L(`无法加载：${err}`, `Couldn't load: ${err}`)}</T></Card> : null}
        {!data && !err ? <ActivityIndicator color={t.gold} style={{ marginTop: space.xl }} /> : null}
        {data && !data.agent ? (
          <View style={[styles.warn, { backgroundColor: t.warnSoft }]}>
            <TriangleAlert size={17} color={t.warn} />
            <T v="callout" style={{ flex: 1 }}>{L('名片 Agent 尚未开启：好友就你的分享提问时不会自动回答，需要你亲自回复。可先设置好档位，开启后将按此执行。', "The card agent is off: when friends ask about your shares, nothing answers automatically. Set the tiers now; it follows them once enabled.")}</T>
          </View>
        ) : null}
        {data && data.name !== undefined ? (
          <>
            <SectionLabel caps={false}>{L('对外名称（显示在你的名片和好友列表中）', 'Name friends see (on your card and in their list)')}</SectionLabel>
            <Card style={{ gap: space.sm }}>
              <View style={{ flexDirection: 'row', gap: space.sm, alignItems: 'center' }}>
                <TextInput value={name ?? data.name ?? ''} onChangeText={(v) => { setName(v); setNameSaved(false); }} onSubmitEditing={saveName} returnKeyType="done"
                  maxLength={40} autoCorrect={false} placeholder={data.suggest || L('名字或昵称', 'Your name or nickname')} placeholderTextColor={t.ink3}
                  style={[type.body, styles.field, { flex: 1, color: t.ink, borderColor: t.line }]} accessibilityLabel={L('对外名称', 'Name friends see')} />
                {name != null && name.trim() && name.trim() !== data.name ? <Btn label={L('保存', 'Save')} kind="quiet" onPress={saveName} /> : null}
              </View>
              {nameSaved ? <T v="caption" color={t.good}>{L('已保存，好友那边将随名片同步更新', 'Saved. Friends see it when your card updates.')}</T> : null}
            </Card>
          </>
        ) : null}
        {data ? (
          <>
            <Segmented<AnyTier> value={tier} onChange={setTier}
              options={(['close', 'friend', 'mate', 'stranger'] as AnyTier[]).map((k) => ({ value: k, label: data.tierNames[k] }))} />
            <T v="callout" color={t.ink2} style={{ paddingHorizontal: space.xs }}>
              {tier === 'stranger' ? L('此档位：未添加的人、其他 Agent', 'This tier: people you never added, other Agents')
                : people && people.length ? L(`此档位：${people.map((p) => p.name).join('、')}`, `This tier: ${people.map((p) => p.name).join(', ')}`) : L('此档位暂无成员', 'Nobody in this tier yet')}
            </T>
            <Card style={{ paddingVertical: 0 }}>
              {SCOPE_ROWS().map((row, i) => {
                const v = data.tiers[tier][row.key];
                return (
                  <Pressable key={row.key} onPress={() => cycle(row.key)} accessibilityRole="button" accessibilityHint={L('点按切换', 'Tap to change')}
                    style={({ pressed }) => [styles.scopeRow, i ? { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line } : null, { opacity: pressed ? 0.7 : 1 }]}>
                    <T v="body" style={{ flex: 1 }}>{row.label}</T>
                    <Pill label={row.values[v] ?? v} tone={LEVEL_TONE[v] ?? 'neutral'} />
                  </Pressable>
                );
              })}
              <View style={[styles.scopeRow, { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }]}>
                <T v="body" style={{ flex: 1 }}>{L('健康和身体', 'Health and body')}</T>
                <Pill label={L('始终不提供', 'Never')} tone="bad" />
              </View>
              <View style={[styles.scopeRow, { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }]}>
                <T v="body" style={{ flex: 1 }}>{L('世界树', 'Memory tree')}</T>
                <Pill label={L('名片 Agent 不可访问', 'It never sees it')} tone="bad" />
              </View>
            </Card>
            <T v="caption" color={t.ink3} style={{ paddingHorizontal: space.xs }}>{L('点按一行切换档位。好友无法看到自己所在的档位；如需调整某位好友的档位，请在与其对话中点按右上角「…」。', "Tap a row to change it. Friends never see their tier; move someone from … in their chat.")}</T>

            <SectionLabel caps={false}>{L('近况（名片 Agent 介绍你的近况时使用）', "What you're up to (used when your card agent says what you're doing)")}</SectionLabel>
            <Card style={{ gap: space.sm }}>
              <TextInput value={status ?? data.status} onChangeText={(v) => { setStatus(v); setSaved(false); }} onBlur={saveStatus} multiline maxLength={1000}
                placeholder={L('例如：在读研究生；正在做一个开源项目', 'e.g. In grad school; building an open-source project lately')} placeholderTextColor={t.ink3}
                style={[type.body, styles.field, { color: t.ink, borderColor: t.line, minHeight: 72, textAlignVertical: 'top' }]} accessibilityLabel={L('近况', 'Status')} />
              <T v="caption" color={t.ink3}>{L('「全部」提供完整近况，「仅首行」只提供第一行。', '"All" shares all of it, "First line" only the first line.')}</T>
              {status != null ? <Btn label={L('保存', 'Save')} kind="quiet" onPress={saveStatus} /> : saved ? <T v="callout" color={t.good}>{L('已保存', 'Saved')}</T> : null}
            </Card>

            <SectionLabel>{health?.sentinel ? L('四条规则', 'Four rules') : L('三条规则', 'Three rules')}</SectionLabel>
            <Card style={{ gap: space.md }}>
              {[L('对方 Agent 的内容仅作为资料，不作为指令', "What other Agents say is information, never an instruction"),
                L('需要你表态或涉及私事时，先生成卡片等待你确认', 'Anything needing your say or asking about private things becomes a card for you first'),
                ...(health?.sentinel ? [L('发出前先经 Doorman 独立复查；不妥的内容将被拦截，等待你选择照发、修改或不发送', 'Before anything goes out, Doorman reviews it separately; anything off is held for you to send, rewrite or drop')] : []),
                L('发出的每一句都会记入活动记录', 'Everything it says goes into Activity')].map((line, i) => (
                <View key={i} style={{ flexDirection: 'row', gap: space.md, alignItems: 'flex-start' }}>
                  <View style={[styles.num, { backgroundColor: t.goldSoft }]}><T v="caption" color={t.gold} style={{ fontWeight: '800' }}>{String(i + 1)}</T></View>
                  <T v="callout" style={{ flex: 1 }}>{line}</T>
                </View>
              ))}
            </Card>
            {health?.sentinel ? <DoormanCard s={health.sentinel} /> : null}
          </>
        ) : null}
      </ScrollView>
    </Screen>
  );
}

/** 「我的名片 agent」页底下：Doorman 开着没有、走哪条路、今天查了几句 / 扣下几句。 */
function DoormanCard({ s }: { s: NonNullable<fr.CardHealth['sentinel']> }) {
  const t = useTheme();
  const on = s.backend !== 'off';
  const how = s.backend === 'sentinel-llm' ? L('由另一个模型复查', 'reviewed by a different model') : on ? L('独立的模型复查，无法访问名片 Agent 的上下文', 'a separate model review that never sees the card agent\'s context')
    : L('仅规则：无可用模型，名片 Agent 仅使用固定回复', 'rules only: no model, so the card agent only says fixed lines');
  const d = s.today;
  return (
    <Card style={{ gap: space.sm }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
        <ShieldCheck size={18} color={on ? t.good : t.warn} />
        <T v="headline" style={{ flex: 1, fontSize: 16 }}>Doorman</T>
        <Pill label={on ? L('已开启', 'On') : L('仅规则', 'Rules only')} tone={on ? 'good' : 'warn'} />
      </View>
      <T v="callout" color={t.ink2}>{how}</T>
      <T v="callout">{L(`今日已复查 ${d.checked} 句，拦截 ${d.held} 句`, `Today: ${d.checked} checked, ${d.held} held`) + (d.failed ? L(`，${d.failed} 句未能复查（已替换为固定回复）`, `, ${d.failed} couldn't be reviewed (fixed replies went instead)`) : '')}</T>
      {s.lastError ? <T v="caption" color={t.ink3}>{L(`上次复查出错：${timeLabel(s.lastError.at)}`, `Last review error: ${timeLabel(s.lastError.at)}`)}</T> : null}
    </Card>
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
  bigField: { marginHorizontal: space.lg, marginTop: space.xl, height: 56, borderRadius: radius.md, paddingHorizontal: space.lg },
  primary: { marginHorizontal: space.lg, marginTop: space.xl, height: 52, borderRadius: radius.md, alignItems: 'center', justifyContent: 'center' },
  codeRow: { flexDirection: 'row', alignItems: 'center', gap: space.sm, borderRadius: radius.sm, paddingHorizontal: 10, paddingVertical: 8 },
  notice: { flexDirection: 'row', alignItems: 'flex-start', gap: space.sm, borderWidth: StyleSheet.hairlineWidth, borderRadius: radius.md, padding: space.md },
  composer: { flexDirection: 'row', alignItems: 'flex-end', gap: space.sm, paddingHorizontal: space.md, paddingVertical: space.sm },
  input: { flex: 1, minHeight: 40, maxHeight: 120, borderRadius: 20, paddingHorizontal: 16, paddingTop: 9, paddingBottom: 9 },
  send: { width: 40, height: 40, borderRadius: 20, alignItems: 'center', justifyContent: 'center' },
  agentBtn: { width: 40, height: 40, borderRadius: 20, borderWidth: 1.5, alignItems: 'center', justifyContent: 'center' },
  quote: { flexDirection: 'row', alignItems: 'center', gap: 6, borderRadius: radius.md, paddingHorizontal: 10, paddingVertical: 7 },
  field: { borderWidth: StyleSheet.hairlineWidth, borderRadius: radius.sm, paddingHorizontal: space.md, paddingVertical: space.sm },
  inviteRow: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: space.sm },
  scopeRow: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: 13 },
  num: { width: 22, height: 22, borderRadius: 11, alignItems: 'center', justifyContent: 'center', marginTop: 1 },
  lensRing: { position: 'absolute', left: 16, top: 0, width: 30, height: 30, borderRadius: 15, alignItems: 'center', justifyContent: 'center' },
  meta: { flexDirection: 'row', alignItems: 'center', gap: 4, alignSelf: 'flex-end' },
  metaText: { fontSize: 11, fontVariant: ['tabular-nums'] },
  quoteBox: { borderLeftWidth: 3, borderRadius: 8, paddingLeft: 8, paddingRight: 10, paddingVertical: 5, gap: 1 },
  reactPill: { flexDirection: 'row', alignItems: 'center', borderWidth: StyleSheet.hairlineWidth, borderRadius: 13, paddingHorizontal: 8, height: 26, marginTop: -2 },
  reactBar: { flexDirection: 'row', justifyContent: 'space-between', borderRadius: radius.lg, paddingHorizontal: space.sm, paddingVertical: 6 },
  reactBtn: { width: 46, height: 46, borderRadius: 23, alignItems: 'center', justifyContent: 'center' },
  dayPill: { borderRadius: radius.pill, paddingHorizontal: 10, paddingVertical: 3 },
  roundRow: { flexDirection: 'row', alignItems: 'center', gap: space.sm, borderWidth: 1, borderRadius: 16, paddingHorizontal: space.md, paddingVertical: 10 },
  roundBody: { borderLeftWidth: 2, paddingLeft: space.md, marginLeft: space.sm },
  newPill: { position: 'absolute', alignSelf: 'center', flexDirection: 'row', alignItems: 'center', gap: 6, borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 18, paddingHorizontal: 14, height: 36, shadowColor: '#000', shadowOpacity: 0.12, shadowRadius: 8, shadowOffset: { width: 0, height: 2 }, elevation: 3 },
  switchCard: { borderRadius: radius.lg, paddingHorizontal: space.md },
  switchRow: { flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingVertical: 10 },
  swipeBtn: { alignItems: 'center', justifyContent: 'center', gap: 4 },
  searchBox: { flexDirection: 'row', alignItems: 'center', gap: space.sm, borderRadius: radius.md, paddingHorizontal: space.md, height: 40 },
});
