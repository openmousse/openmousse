import React, { useEffect, useRef, useState } from 'react';
import { agentName } from '../brand';
import { useNavigation } from '@react-navigation/native';
import { Alert, Image, Linking, Platform, Pressable, ScrollView, StyleSheet, TextInput, View, type LayoutChangeEvent, type NativeScrollEvent, type NativeSyntheticEvent, type TextInputKeyPressEventData } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import * as Haptics from 'expo-haptics';
import { AudioModule, RecordingPresets, setAudioModeAsync, useAudioRecorder, useAudioRecorderState } from 'expo-audio';
import { ArrowUp, Camera, Copy, FileAudio, FileText, Film, ImageIcon, Inbox, ListChecks, Mic, Paperclip, Pencil, Square, Trash2, Undo2, X } from './icons';
import type { Attachment, ChatCard, HandoffCard, InboxItem, Message, PendingFile, TaskCardInfo } from '../data/types';
import { L } from '../i18n';
import type { ChatQuote } from '../navigation';
import { useStore } from '../store';
import { radius, space, type, useTheme } from '../theme';
import { LensAvatar } from './LensAvatar';
import { useBottomInset } from './keyboard';
import { modelOf } from './ModelPicker';
import { useSheet } from './Sheet';
import { PullRefresh, T } from './ui';
import { Markdown } from './Markdown';
import { InboxCard } from './InboxCard';
import { HandoffChip, HandoffFrom, ScheduleChip, TaskCardView, modelLabel } from './ChatCards';
import { drafts, MAX_FILES, pickDocuments, pickMedia } from './chatInput';

// 没发出去的草稿按线程记着（drafts 在 chatInput.ts）：切到看板、换线程、离开页面再回来还在（只在内存里，退出 app 就没了）。
// 从「今天」的「去对话里说」带过来、还没发出去的引用，也按线程记着。
const quotes = new Map<string, ChatQuote>();

/** 逻辑日（04:00 为界）里的第几分钟：00:30 排在 23:30 后面。 */
const dayMinute = (hhmm: string) => { const [h, m] = hhmm.split(':').map(Number); return (h * 60 + m - 240 + 1440) % 1440; };
function logicalDayStart(): number {
  const d = new Date();
  if (d.getHours() < 4) d.setDate(d.getDate() - 1);
  d.setHours(4, 0, 0, 0);
  return d.getTime();
}
/**
 * 对话里的收件箱卡片放在哪：跟在提它的那条消息（db<messageId>）下面；
 * 没有 messageId（或者那条不在今天的记录里）的，放在比它早的最后一条消息后面，比今天的都早就放最上面（-1）。
 */
function placeInbox(items: InboxItem[], msgs: Message[]): Map<number, InboxItem[]> {
  const index = new Map(msgs.map((m, i) => [m.id, i]));
  const start = logicalDayStart();
  const slots = new Map<number, InboxItem[]>();
  for (const it of items) {
    let at = it.messageId != null ? index.get(`db${it.messageId}`) : undefined;
    if (at === undefined) {
      at = -1;
      const ms = Date.parse(it.createdAt);
      if (!Number.isNaN(ms) && ms >= start) {
        const minute = dayMinute(new Date(ms).toTimeString().slice(0, 5));
        msgs.forEach((m, i) => { if (/^\d{1,2}:\d{2}$/.test(m.time) && dayMinute(m.time) <= minute) at = i; });
      }
    }
    slots.set(at, [...(slots.get(at) ?? []), it]);
  }
  return slots;
}

/** 对话里的一张收件箱卡：和助手的消息对齐（让出头像那一列）。 */
function ChatInbox({ items }: { items?: InboxItem[] }) {
  if (!items?.length) return null;
  return <>{items.map((it) => <View key={it.id} style={{ paddingLeft: 36 }}><InboxCard item={it} variant="chat" /></View>)}</>;
}

/**
 * 转交卡、任务卡放在哪：挂在 db<messageId> 那条回复上（转交卡在回复文字上面，是先问的；任务卡在下面，是回复里说的那件事）。
 * 还没挂上的（那条回复还在进行）：正在回复就放进进行中的那一块（live），否则按时间排进去（放在比它早的最后一条消息下面）。
 */
function placeCards(cards: ChatCard[], msgs: Message[], busy: boolean) {
  const index = new Map(msgs.map((m, i) => [m.id, i]));
  const above = new Map<number, HandoffCard[]>();
  const below = new Map<number, ChatCard[]>();  // 任务卡、日程卡（和按时间排进来的转交卡）
  const live: ChatCard[] = [];
  const start = logicalDayStart();
  const lastUser = [...msgs].reverse().find((m) => m.role === 'user');
  const add = <V,>(map: Map<number, V[]>, at: number, v: V) => map.set(at, [...(map.get(at) ?? []), v]);
  for (const c of cards) {
    const at = c.messageId != null ? index.get(`db${c.messageId}`) : undefined;
    if (at !== undefined) { if (c.kind === 'handoff') add(above, at, c); else add(below, at, c); continue; }
    const ms = Date.parse(c.createdAt ?? '');
    const minute = !Number.isNaN(ms) && ms >= start ? dayMinute(new Date(ms).toTimeString().slice(0, 5)) : -1;
    if (busy && (!lastUser || !/^\d{1,2}:\d{2}$/.test(lastUser.time) || minute >= dayMinute(lastUser.time))) { live.push(c); continue; }
    let slot = -1;
    msgs.forEach((m, i) => { if (/^\d{1,2}:\d{2}$/.test(m.time) && minute >= 0 && dayMinute(m.time) <= minute) slot = i; });
    add(below, slot, c);
  }
  return { above, below, live };
}

/** 回复下面的任务卡（和转交卡一样让出头像那一列；转交卡如果按时间排到这里，也走这里）。 */
function ChatTasks({ cards, onRevise }: { cards?: ChatCard[]; onRevise: (c: TaskCardInfo) => void }) {
  if (!cards?.length) return null;
  return <>{cards.map((c) => <View key={c.id} style={{ paddingLeft: 36 }}>{c.kind === 'task' ? <TaskCardView card={c} onRevise={onRevise} /> : c.kind === 'schedule' ? <ScheduleChip card={c} /> : <HandoffChip card={c} />}</View>)}</>;
}

// 上限对齐主流 LLM 产品（服务端 files.py 同样的数）：一条消息 10 个附件，每个 30 MB，类型不限。
const MAX_BYTES = 30 * 1024 * 1024;
const PLACEHOLDER_TEXT = '（见附件）';  // 只发附件时服务端（chat.py）和 store 记的占位文字；按原文比较后隐藏，不翻译
// 语音输入只要听清人声：16 kHz 单声道 32 kbps 的 AAC，比默认的 44.1 kHz 立体声 128 kbps 小 6 倍，上传快得多，转写质量不受影响。
const SPEECH_PRESET = { ...RecordingPresets.HIGH_QUALITY, sampleRate: 16000, numberOfChannels: 1, bitRate: 32000 };

const human = (n: number) => (n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);
const KIND_ICON = { doc: FileText, audio: FileAudio, video: Film, file: Paperclip, image: ImageIcon } as const;

function ModelTag({ m }: { m: Message }) {
  const t = useTheme();
  const model = modelOf(m.modelId);
  const err = m.error ? L(` · 出错：${m.error}`, ` · Error: ${m.error}`) : '';
  if (!model) {
    return m.modelId || m.error ? <T v="caption" color={m.error ? t.bad : t.ink3} style={{ marginTop: 4 }}>{m.modelId ?? ''}{err} · {m.time}</T> : null;
  }
  const from = modelOf(m.fallbackFrom);
  // billing 是目录里的枚举值（'订阅' / 'API' / '免费'），程序拿它比较；这里只换显示文字。
  const billing = model.billing === '订阅' ? L('订阅', 'Subscription') : model.billing === '免费' ? L('免费', 'Free') : model.billing;
  return (
    <T v="caption" color={m.error ? t.bad : from ? t.warn : t.ink3} style={{ marginTop: 4 }}>
      {model.short} · {billing}{from ? L(` · 请求的是 ${from.short}，回退链换成了它`, ` · requested ${from.short}, fell back to this`) : ''}{err} · {m.time}
    </T>
  );
}

function FileChip({ a, onPress, onRemove }: { a: Attachment; onPress?: () => void; onRemove?: () => void }) {
  const t = useTheme();
  const Icon = KIND_ICON[a.kind] ?? Paperclip;
  return (
    <Pressable onPress={onPress} accessibilityRole={onPress ? 'button' : undefined} accessibilityLabel={L(`附件 ${a.name}`, `Attachment ${a.name}`)}
      style={[styles.chip, { backgroundColor: t.surface, borderColor: t.line }]}>
      <Icon size={16} color={t.cyan} />
      <View style={{ flexShrink: 1 }}>
        <T v="callout" numberOfLines={1}>{a.name}</T>
        <T v="caption" color={a.status === 'warn' ? t.warn : t.ink3} numberOfLines={1}>{human(a.size)}{a.note ? ` · ${a.note}` : ''}</T>
      </View>
      {onRemove ? <Pressable onPress={onRemove} hitSlop={8} accessibilityRole="button" accessibilityLabel={L(`去掉 ${a.name}`, `Remove ${a.name}`)}><X size={14} color={t.ink3} /></Pressable> : null}
    </Pressable>
  );
}

/** 消息里的附件：图片给缩略图（点开原图），其它给文件条。 */
function AttachmentList({ items, mine }: { items: Attachment[]; mine: boolean }) {
  const t = useTheme();
  const images = items.filter((a) => a.kind === 'image');
  const others = items.filter((a) => a.kind !== 'image');
  const open = (a: Attachment) => { if (a.url.startsWith('http')) Linking.openURL(a.url).catch(() => {}); };
  const side = images.length === 1 ? 200 : 96;
  return (
    <View style={{ gap: 6, alignItems: mine ? 'flex-end' : 'flex-start', maxWidth: '82%' }}>
      {images.length ? (
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6, justifyContent: mine ? 'flex-end' : 'flex-start' }}>
          {images.map((a) => (
            <Pressable key={a.id} onPress={() => open(a)} accessibilityRole="imagebutton" accessibilityLabel={a.name}>
              <Image source={{ uri: a.url.includes('/api/files/') ? `${a.url}${a.url.includes('?') ? '&' : '?'}thumb=1` : a.url }} resizeMode="cover"
                style={{ width: side, height: side, borderRadius: radius.md, backgroundColor: t.surface2 }} />
            </Pressable>
          ))}
        </View>
      ) : null}
      {others.map((a) => <FileChip key={a.id} a={a} onPress={() => open(a)} />)}
    </View>
  );
}

export function Bubble({ m, showAvatar, onLongPress, before, from, highlight }: {
  m: Message; showAvatar: boolean; onLongPress?: () => void;
  /** 回复文字上面的东西（这条回复里转出去的转交卡） */
  before?: React.ReactNode;
  /** 「主对话转来」这一条对应的转交记录（点一下回去） */
  from?: HandoffCard;
  /** 从别处点过来定位到这一条：闪一下金边 */
  highlight?: boolean;
}) {
  const t = useTheme();
  const { avatar } = useStore();
  const att = m.body.attachments ?? [];
  const text = att.length && m.body.text === PLACEHOLDER_TEXT ? '' : m.body.text;
  if (m.role === 'auto') {
    // 【收件箱】：你在收件箱里点了同意 / 不要 / 改一下，或者同意的事做完了。前面换成收件箱图标。
    if (text.startsWith('【收件箱】')) {
      return (
        <View style={{ flexDirection: 'row', justifyContent: 'center', alignItems: 'flex-start', gap: 4, paddingVertical: 2, paddingHorizontal: space.lg }}>
          <View style={{ paddingTop: 1 }}><Inbox size={13} color={t.ink3} /></View>
          <T v="caption" color={t.ink3} style={{ textAlign: 'center', flexShrink: 1 }}>{text.replace(/^【收件箱】\s*/, '')} · {m.time}</T>
        </View>
      );
    }
    // 【主对话转来】：别的对话（一般是主对话）把问题转给了这个 Agent。点一下回到转交它的那条回复。
    if (text.startsWith('【主对话转来】')) return <HandoffFrom question={text.replace(/^【主对话转来】\s*/, '')} time={m.time} card={from} highlight={highlight} />;
    return (
      <View style={{ alignItems: 'center', paddingVertical: 2 }}>
        <T v="caption" color={t.ink3} style={{ textAlign: 'center' }}>⚙ {text.replace(/^【自动触发】/, '')} · {m.time}</T>
      </View>
    );
  }
  if (m.role === 'user') {
    return (
      <View style={{ alignItems: 'flex-end', gap: 6 }}>
        {att.length ? <AttachmentList items={att} mine /> : null}
        {text ? (
          <Pressable onLongPress={onLongPress} delayLongPress={350} accessibilityHint={L('长按可以复制、撤回、重新编辑或删除', 'Long-press to copy, unsend, edit or delete')}
            style={({ pressed }) => [styles.userBubble, { backgroundColor: t.surface2, opacity: pressed ? 0.75 : 1 }]}>
            <T v="body">{text}</T>
          </Pressable>
        ) : null}
      </View>
    );
  }
  return (
    <Pressable onLongPress={onLongPress} delayLongPress={350} style={{ flexDirection: 'row', gap: space.sm }}>
      <View style={{ width: 28 }}>{showAvatar || before ? <LensAvatar size={28} config={avatar} /> : null}</View>
      <View style={{ flex: 1 }}>
        {before ? <View style={{ gap: 8, marginBottom: 8 }}>{before}</View> : null}
        <Markdown text={m.body.text} />
        {att.length ? <View style={{ marginTop: 6 }}><AttachmentList items={att} mine={false} /></View> : null}
        <ModelTag m={m} />
      </View>
    </Pressable>
  );
}

function Action({ icon: Icon, label, note, danger, onPress }: { icon: typeof Copy; label: string; note?: string; danger?: boolean; onPress: () => void }) {
  const t = useTheme();
  const color = danger ? t.bad : t.ink;
  return (
    <Pressable onPress={onPress} accessibilityRole="button" accessibilityLabel={label}
      style={({ pressed }) => [styles.action, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
      <Icon size={20} color={color} />
      <View style={{ flex: 1, gap: 2 }}>
        <T v="headline" color={color}>{label}</T>
        {note ? <T v="callout" color={t.ink2}>{note}</T> : null}
      </View>
    </Pressable>
  );
}

export function ChatView({ threadId, placeholder, empty, quote: quoteProp, quoteAt = 0, focus, focusAt = 0 }: {
  threadId: string; placeholder: string; empty?: string;
  /** 从收件箱「去对话里说」带过来的那件事；quoteAt 是那次跳转的时间（同一个对话再带一次也认得出） */
  quote?: ChatQuote; quoteAt?: number;
  /** 从转交卡点过来：滚到这条消息（"db<id>"）闪一下；focusAt 是那次跳转的时间 */
  focus?: string; focusAt?: number;
}) {
  const t = useTheme();
  const { threads, typing, send, avatar, streaming, connected, booting, deleteMessage, rewindMessage, transcribe, refreshThread, sharedChannels, inboxByThread, cardsByThread, liveCards, reviseTask } = useStore();
  const sheet = useSheet();
  const msgs = threads[threadId] ?? [];
  // 这个对话里的收件箱卡片：跟在提它的那条消息下面（处理过的显示成回执）
  const slots = placeInbox(inboxByThread[threadId] ?? [], msgs);
  const inboxCount = inboxByThread[threadId]?.length ?? 0;
  // 转交卡、任务卡：服务器记的，加上进行中的回复里刚出的（同一张卡以新的为准，挂在哪条回复下面以服务器的为准）
  const liveHere = liveCards[threadId] ?? {};
  const serverCards = cardsByThread[threadId]?.cards ?? [];
  const allCards: ChatCard[] = [
    ...serverCards.map((c) => (liveHere[c.id] ? ({ ...c, ...liveHere[c.id], messageId: c.messageId ?? liveHere[c.id].messageId } as ChatCard) : c)),
    ...Object.values(liveHere).filter((c) => !serverCards.some((x) => x.id === c.id)),
  ];
  const incoming = cardsByThread[threadId]?.incoming ?? [];
  const cardCount = allCards.length;
  const cardState = allCards.map((c) => `${c.id}:${c.status}:${c.kind === 'task' ? `${c.round}${c.roundStatus}` : ''}`).join(',');
  // 引用：跳转带来的新引用替换旧的；发出去或点 × 就没了
  const [quote, setQuote] = useState<ChatQuote | null>(() => quoteProp ?? quotes.get(threadId) ?? null);
  const [quoteSeen, setQuoteSeen] = useState(quoteAt);
  if (quoteAt !== quoteSeen) {
    setQuoteSeen(quoteAt);
    if (quoteProp) setQuote(quoteProp);
  }
  const input = useRef<TextInput>(null);
  useEffect(() => {
    if (quote) quotes.set(threadId, quote); else quotes.delete(threadId);
  }, [threadId, quote]);
  // 带着引用进来：直接把光标放进输入框
  useEffect(() => {
    if (!quoteAt || !quoteProp) return undefined;
    const h = setTimeout(() => input.current?.focus(), 350);
    return () => clearTimeout(h);
  }, [quoteAt, quoteProp]);
  const partial = streaming[threadId];
  const [draft, setDraft] = useState(() => drafts.get(threadId) ?? '');
  const [pending, setPending] = useState<PendingFile[]>([]);
  const [transcribing, setTranscribing] = useState(false);
  const scroller = useRef<ScrollView>(null);
  const root = useRef<View>(null);
  const bottom = useBottomInset(root);
  const list = useRef({ y: 0, content: 0, height: 0 });
  const nav = useNavigation<any>();
  const busy = !!typing[threadId];
  const placed = placeCards(allCards, msgs, busy);
  const liveHandoffs = placed.live.filter((c): c is HandoffCard => c.kind === 'handoff');
  const liveTasks = placed.live.filter((c) => c.kind !== 'handoff');  // 任务卡、日程卡
  // 每条消息在列表里的位置（定位到某一条用）；从转交卡点过来的那一条闪一下
  const ys = useRef<Record<string, number>>({});
  const [flash, setFlash] = useState<string | null>(null);
  const recorder = useAudioRecorder(SPEECH_PRESET);
  const rec = useAudioRecorderState(recorder, 250);
  const canRecord = Platform.OS !== 'web';

  useEffect(() => {
    const h = setTimeout(() => {
      scroller.current?.scrollToEnd({ animated: true });
      list.current.y = Math.max(0, list.current.content - list.current.height);  // 滚动事件回来之前先按目标位置算
    }, 60);
    return () => clearTimeout(h);
  }, [msgs.length, busy, partial?.length, inboxCount, cardCount, cardState]);

  // 从转交卡 / 「主对话转来」点过来：等列表排好（上面那个滚到底之后）再滚到那一条，闪一下金边
  useEffect(() => {
    if (!focus || !focusAt) return undefined;
    const go = setTimeout(() => {
      const y = ys.current[focus];
      if (y == null) return;
      scroller.current?.scrollTo({ y: Math.max(0, y - 24), animated: true });
      list.current.y = Math.max(0, y - 24);
      setFlash(focus);
    }, 450);
    const off = setTimeout(() => setFlash(null), 2600);
    return () => { clearTimeout(go); clearTimeout(off); };
  }, [focus, focusAt, msgs.length]);

  useEffect(() => {
    if (draft) drafts.set(threadId, draft);
    else drafts.delete(threadId);
  }, [threadId, draft]);

  const track = (e: NativeSyntheticEvent<NativeScrollEvent>) => {
    list.current.y = e.nativeEvent.contentOffset.y;
    list.current.content = e.nativeEvent.contentSize.height;
  };
  // 消息区变矮（键盘升起、输入框变高）时内容跟着往上推，原来贴着输入栏的那条还贴着，不会被挡住；
  // 变高（键盘收起）时位置不动，只在滚过了头时收回来，下面不留空白。
  const keepBottom = (e: LayoutChangeEvent) => {
    const l = list.current;
    const h = e.nativeEvent.layout.height;
    const shrink = l.height ? l.height - h : 0;
    l.height = h;
    if (!shrink || l.y < 0) return;
    const y = Math.min(shrink > 0 ? l.y + shrink : l.y, Math.max(0, l.content - h));
    if (Math.abs(y - l.y) > 1) { scroller.current?.scrollTo({ y, animated: true }); l.y = y; }
  };

  const fail = (e: unknown) => Alert.alert(L('没做成', "Couldn't do that"), e instanceof Error ? e.message : String(e));

  const addFiles = (files: PendingFile[]) => {
    if (!files.length) return;
    const tooBig = files.filter((f) => f.size > MAX_BYTES);
    if (tooBig.length) Alert.alert(L('文件太大', 'File too large'), L(`${tooBig.map((f) => f.name).join('、')} 超过 ${MAX_BYTES / 1024 / 1024} MB，没有加进去。`, `Over ${MAX_BYTES / 1024 / 1024} MB, not added: ${tooBig.map((f) => f.name).join(', ')}`));
    const ok = files.filter((f) => f.size <= MAX_BYTES);
    setPending((cur) => {
      const next = [...cur, ...ok];
      if (next.length > MAX_FILES) Alert.alert(L('太多了', 'Too many files'), L(`一条消息最多 ${MAX_FILES} 个附件，多出来的没加。`, `Up to ${MAX_FILES} attachments per message. The extra ones weren't added.`));
      return next.slice(0, MAX_FILES);
    });
  };

  const openAttach = () => {
    if (Platform.OS === 'web') { pickDocuments().then(addFiles).catch(fail); return; }
    const close = sheet.close;
    sheet.open({
      title: L('添加附件', 'Add attachment'),
      content: () => (
        <View style={{ gap: space.sm }}>
          <Action icon={Camera} label={L('拍照或录像', 'Take photo or video')} onPress={() => { close(); pickMedia(true).then(addFiles).catch(fail); }} />
          <Action icon={ImageIcon} label={L('相册', 'Photo library')} note={L(`照片和视频，${agentName()} 直接看图`, `Photos and videos. ${agentName()} sees images directly.`)} onPress={() => { close(); pickMedia(false).then(addFiles).catch(fail); }} />
          <Action icon={FileText} label={L('文件', 'Files')} note={L(`PDF、Word、表格、PPT、代码、录音，什么类型都行。每个 ${MAX_BYTES / 1024 / 1024} MB 以内，一条最多 ${MAX_FILES} 个。`, `PDF, Word, spreadsheets, slides, code, recordings: any type works. Up to ${MAX_BYTES / 1024 / 1024} MB each, ${MAX_FILES} per message.`)} onPress={() => { close(); pickDocuments().then(addFiles).catch(fail); }} />
        </View>
      ),
    });
  };

  const submit = () => {
    const text = draft.trim();
    // 改一下任务：意见直接交给做这件事的子会话（不进这个对话），任务卡变成下一轮
    if (quote?.taskId) {
      if (!text || transcribing) return;
      if (pending.length) { Alert.alert(L('改一下不能带附件', "Revisions can't carry attachments"), L('把要改的写成文字发给它。', 'Say what to change in words.')); return; }
      reviseTask(quote.taskId, text).catch(fail);
      setDraft('');
      setQuote(null);
      return;
    }
    if ((!text && !pending.length) || busy || transcribing) return;
    // 带着引用：这条是对收件箱里那件事的修改意见（服务器收到 inboxId 会把它退回去改）
    send(threadId, text, pending.length ? pending : undefined, quote?.inboxId ? { inboxId: quote.inboxId } : quote?.ref ? { ref: quote.ref } : undefined);
    setDraft('');
    setPending([]);
    setQuote(null);
  };

  // 任务卡上点「改一下」/「接着做」：输入框上面带「改：标题」，光标放进去；接着做的先填一句
  const reviseCard = (c: TaskCardInfo) => {
    setQuote({ taskId: c.id, title: c.title, model: c.modelId });
    if (c.timedOut && !draft.trim()) setDraft(L('接着做完，从停下的地方继续', 'Keep going from where you stopped'));
    setTimeout(() => input.current?.focus(), 250);
  };

  const startRecording = async () => {
    try {
      const perm = await AudioModule.requestRecordingPermissionsAsync();
      if (!perm.granted) { Alert.alert(L('没有麦克风权限', 'No microphone access'), L(`去系统设置里给 ${agentName()} 打开麦克风。`, `Turn on the microphone for ${agentName()} in Settings.`)); return; }
      await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true });
      await recorder.prepareToRecordAsync();
      recorder.record();
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => {});
    } catch (e) { fail(e); }
  };

  const stopRecording = async () => {
    try {
      await recorder.stop();
      await setAudioModeAsync({ allowsRecording: false, playsInSilentMode: true });
      const uri = recorder.uri;
      if (!uri) return;
      setTranscribing(true);
      const text = await transcribe({ uri, name: 'voice.m4a', mime: 'audio/m4a', size: 0 });
      if (text) setDraft((d) => (d.trim() ? `${d.trim()} ${text}` : text));
      else Alert.alert(L('没听清', "Didn't catch that"), L('录音里没有识别出文字。', 'No speech was recognized in the recording.'));
    } catch (e) { fail(e); } finally { setTranscribing(false); }
  };

  const openActions = (m: Message) => {
    const text = m.body.text;
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});
    const after = msgs.length - msgs.findIndex((x) => x.id === m.id) - 1;
    const shared = threadId === 'main' && sharedChannels.length ? L(`主对话和 ${sharedChannels.join('、')} 共用，那边在这之后的消息也会一起退掉。`, ` The main chat is shared with ${sharedChannels.join(', ')}, so later messages there are rolled back too.`) : '';
    const tail = after > 0 ? L(`之后的 ${after} 条也会一起去掉，`, after === 1 ? 'The message after it is also removed. ' : `The ${after} messages after it are also removed. `) : '';
    const close = sheet.close;
    const rewind = (edit: boolean) => { close(); rewindMessage(threadId, m.id).then((txt) => { if (edit) setDraft(txt || text); }).catch(fail); };
    sheet.open({
      title: m.role === 'user' ? L('这条消息', 'This message') : L(`${agentName()} 的回复`, `${agentName()}'s reply`),
      content: () => (
        <View style={{ gap: space.sm }}>
          <Action icon={Copy} label={L('复制', 'Copy')} onPress={() => { close(); Clipboard.setStringAsync(text).catch(() => {}); }} />
          {m.role === 'user' && !busy ? <>
            <Action icon={Pencil} label={L('重新编辑', 'Edit')} note={L(`放回输入框改完再发。${tail}${agentName()} 也会忘掉这段。${shared}${m.body.attachments?.length ? '附件要重新加。' : ''}`, `Puts it back in the input box to edit and resend. ${tail}${agentName()} will forget it too.${shared}${m.body.attachments?.length ? ' Attachments need to be added again.' : ''}`)} onPress={() => rewind(true)} />
            <Action icon={Undo2} label={L('撤回', 'Unsend')} note={L(`${tail}${agentName()} 也会忘掉这段。${shared}`, `${tail}${agentName()} will forget it too.${shared}`)} onPress={() => rewind(false)} />
          </> : null}
          {m.role === 'user' && busy ? <T v="callout" color={t.ink3}>{L(`${agentName()} 回完之后才能撤回或重新编辑。`, `You can unsend or edit once ${agentName()} has replied.`)}</T> : null}
          <Action icon={Trash2} label={L('删除', 'Delete')} danger note={L(`只从这里的记录删掉，${agentName()} 仍然记得。`, `Only removes it from the history here. ${agentName()} still remembers it.`)} onPress={() => { close(); deleteMessage(threadId, m.id).catch(fail); }} />
        </View>
      ),
    });
  };

  // 改一下任务不经过这个对话：这边正在回复也能发
  const canSend = quote?.taskId ? !!draft.trim() && !transcribing : (!!draft.trim() || pending.length > 0) && !busy && !transcribing;
  // 手机上回车键是「发送」，发完键盘留着接着打（submitBehavior）。网页的多行输入框不认 submitBehavior，
  // 自己接 Enter：发送、不丢焦点；Shift+Enter 换行；输入法选字时按的 Enter 不算。
  const webEnter = Platform.OS === 'web' ? (e: NativeSyntheticEvent<TextInputKeyPressEventData>) => {
    const k = e.nativeEvent as unknown as KeyboardEvent;
    if (k.key !== 'Enter' || k.shiftKey || k.isComposing || k.keyCode === 229) return;
    e.preventDefault();
    submit();
  } : undefined;
  const secs = Math.floor((rec.durationMillis ?? 0) / 1000);

  return (
    <View ref={root} onLayout={bottom.onLayout} style={{ flex: 1, paddingBottom: bottom.inset }}>
      {/* 键盘开着时点消息区任何地方、或者往上划，都先收键盘（点到的消息不响应，再点一次才算）。
          网页不开 on-drag：react-native-web 在任何滚动（包括新回复自动滚到底）时都会让输入框失焦。 */}
      <ScrollView ref={scroller} style={{ flex: 1 }} contentContainerStyle={{ padding: space.lg, gap: space.lg }} keyboardShouldPersistTaps="never" keyboardDismissMode={Platform.OS === 'web' ? 'none' : 'on-drag'}
        scrollToOverflowEnabled onLayout={keepBottom} onScroll={track} onScrollEndDrag={track} onMomentumScrollEnd={track} scrollEventThrottle={32}
        onContentSizeChange={(_w, h) => { list.current.content = h; }}
        refreshControl={<PullRefresh onRefresh={() => refreshThread(threadId)} />}>
        {connected ? (
          <Pressable onPress={() => nav.navigate('History', { thread: threadId })} accessibilityRole="button" style={{ alignSelf: 'center', paddingVertical: 2 }}>
            <T v="caption" color={t.ink3}>{L('这里只有今天的（04:00 起）· ', 'Today only (from 04:00) · ')}<T v="caption" color={t.gold}>{L('之前的在历史里', 'Earlier in History')}</T></T>
          </Pressable>
        ) : null}
        {!msgs.length && !busy ? (
          <View style={{ alignItems: 'center', gap: space.sm, paddingVertical: space.xxl, paddingHorizontal: space.lg }}>
            <LensAvatar size={40} config={avatar} />
            <T v="callout" color={t.ink3} style={{ textAlign: 'center' }}>
              {booting ? L('正在连服务器…', 'Connecting to the server…') : !connected ? L('没连上服务器。检查「我 → 服务器」，再回到这一页。', 'Not connected to the server. Check Me → Server, then come back here.') : empty ?? L('还没有对话。', 'No messages yet.')}
            </T>
          </View>
        ) : null}
        <ChatInbox items={slots.get(-1)} />
        <ChatTasks cards={placed.below.get(-1)} onRevise={reviseCard} />
        {msgs.map((m, i) => {
          const above = placed.above.get(i);
          return (
            <React.Fragment key={m.id}>
              <View onLayout={(e) => { ys.current[m.id] = e.nativeEvent.layout.y; }}>
                <Bubble m={m} showAvatar={m.role === 'grava' && msgs[i - 1]?.role !== 'grava'} onLongPress={() => openActions(m)}
                  before={above?.length ? above.map((c) => <HandoffChip key={c.id} card={c} />) : undefined}
                  from={m.role === 'auto' ? incoming.find((h) => h.relayId === m.id) : undefined} highlight={flash === m.id} />
              </View>
              <ChatTasks cards={placed.below.get(i)} onRevise={reviseCard} />
              <ChatInbox items={slots.get(i)} />
            </React.Fragment>
          );
        })}
        {busy ? (
          <View style={{ flexDirection: 'row', gap: space.sm, alignItems: partial || liveHandoffs.length ? 'flex-start' : 'center' }}>
            <LensAvatar size={28} config={avatar} />
            <View style={{ flex: 1, gap: 8 }}>
              {/* 这次回复里正在转给 Agent 的：先问，问完它再接着写 */}
              {liveHandoffs.map((c) => <HandoffChip key={c.id} card={c} />)}
              {partial ? <Markdown text={partial} />
                : liveHandoffs.length ? null : <T v="callout" color={t.ink3}>{L(`发给 ${agentName()} 了，等回复…`, `Sent to ${agentName()}, waiting for a reply…`)}</T>}
            </View>
          </View>
        ) : null}
        {busy ? <ChatTasks cards={liveTasks} onRevise={reviseCard} /> : null}
      </ScrollView>
      <View style={[styles.composerWrap, { borderTopColor: t.line, backgroundColor: t.bg }]}>
        {quote ? (
          <View style={{ paddingHorizontal: space.md, paddingTop: space.sm }}>
            <View style={[styles.quote, { backgroundColor: t.goldSoft }]}>
              {quote.taskId ? <Pencil size={14} color={t.gold} /> : quote.ref ? <ListChecks size={14} color={t.gold} /> : <Inbox size={14} color={t.gold} />}
              <T v="callout" numberOfLines={1} style={{ flex: 1, fontSize: 13 }}>
                {quote.taskId
                  ? L(`改「${quote.title}」· 直接发给做它的 ${modelLabel(quote.model)}`, `Revise "${quote.title}" · goes straight to ${modelLabel(quote.model)}`)
                  : quote.ref ? L(`说的是：${quote.title}`, `About: ${quote.title}`) : L(`回复：${quote.title}`, `Re: ${quote.title}`)}
              </T>
              <Pressable onPress={() => setQuote(null)} hitSlop={10} accessibilityRole="button" accessibilityLabel={L('不带这条引用', 'Remove the quote')}>
                <X size={14} color={t.ink3} />
              </Pressable>
            </View>
          </View>
        ) : null}
        {pending.length ? (
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: space.sm, paddingHorizontal: space.md, paddingTop: space.sm }} keyboardShouldPersistTaps="handled">
            {pending.map((f, i) => (
              f.mime.startsWith('image/') ? (
                <View key={`${f.uri}-${i}`} style={{ position: 'relative' }}>
                  <Image source={{ uri: f.uri }} style={{ width: 56, height: 56, borderRadius: radius.md, backgroundColor: t.surface2 }} accessibilityLabel={f.name} />
                  <Pressable onPress={() => setPending((cur) => cur.filter((_, k) => k !== i))} hitSlop={8} accessibilityRole="button" accessibilityLabel={L(`去掉 ${f.name}`, `Remove ${f.name}`)}
                    style={[styles.removeDot, { backgroundColor: t.ink }]}><X size={12} color={t.bg} /></Pressable>
                </View>
              ) : (
                <FileChip key={`${f.uri}-${i}`} a={{ id: `p${i}`, name: f.name, mime: f.mime, size: f.size, kind: f.mime.startsWith('audio/') ? 'audio' : f.mime.startsWith('video/') ? 'video' : 'doc', url: f.uri }}
                  onRemove={() => setPending((cur) => cur.filter((_, k) => k !== i))} />
              )
            ))}
          </ScrollView>
        ) : null}
        {rec.isRecording ? (
          <View style={styles.composer}>
            <View style={[styles.recording, { backgroundColor: t.surface }]}>
              <View style={{ width: 10, height: 10, borderRadius: 5, backgroundColor: t.bad }} />
              <T v="body" style={{ flex: 1, fontVariant: ['tabular-nums'] }}>{L('正在录音', 'Recording')} {Math.floor(secs / 60)}:{String(secs % 60).padStart(2, '0')}</T>
              <T v="caption" color={t.ink3}>{L('说完点停止，转成文字后可以改', 'Tap stop when done, then edit the text')}</T>
            </View>
            <Pressable onPress={stopRecording} accessibilityRole="button" accessibilityLabel={L('停止录音', 'Stop recording')} style={[styles.send, { backgroundColor: t.bad }]}>
              <Square size={16} color="#fff" fill="#fff" />
            </Pressable>
          </View>
        ) : (
          <View style={styles.composer}>
            <Pressable onPress={openAttach} disabled={busy} hitSlop={6} accessibilityRole="button" accessibilityLabel={L('添加附件', 'Add attachment')} style={styles.iconBtn}>
              <Paperclip size={22} color={busy ? t.ink3 : t.ink2} />
            </Pressable>
            <TextInput
              ref={input}
              value={draft} onChangeText={setDraft} placeholder={transcribing ? L('正在转文字…', 'Transcribing…') : quote ? L('说说要改什么', 'Say what should change') : placeholder} placeholderTextColor={t.ink3}
              multiline numberOfLines={1} onSubmitEditing={submit} submitBehavior="submit" returnKeyType="send" enablesReturnKeyAutomatically onKeyPress={webEnter}
              accessibilityLabel={L('消息输入框', 'Message')} editable={!transcribing}
              style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink }]}
            />
            {canRecord && !draft.trim() && !pending.length && !transcribing ? (
              <Pressable onPress={startRecording} disabled={busy} accessibilityRole="button" accessibilityLabel={L('录音输入', 'Voice input')}
                style={[styles.send, { backgroundColor: t.surface2 }]}>
                <Mic size={20} color={busy ? t.ink3 : t.ink} />
              </Pressable>
            ) : (
              <Pressable onPress={submit} disabled={!canSend} accessibilityRole="button" accessibilityLabel={L('发送', 'Send')}
                style={[styles.send, { backgroundColor: canSend ? t.goldFill : t.surface2 }]}>
                <ArrowUp size={20} color={canSend ? t.onGold : t.ink3} />
              </Pressable>
            )}
          </View>
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  userBubble: { maxWidth: '82%', borderRadius: radius.lg, borderBottomRightRadius: 6, paddingHorizontal: 14, paddingVertical: 9 },
  composerWrap: { borderTopWidth: StyleSheet.hairlineWidth },
  composer: { flexDirection: 'row', alignItems: 'flex-end', gap: space.sm, paddingHorizontal: space.md, paddingVertical: space.sm },
  input: { flex: 1, minHeight: 40, maxHeight: 120, borderRadius: 20, paddingHorizontal: 16, paddingTop: 9, paddingBottom: 9 },
  action: { flexDirection: 'row', alignItems: 'center', gap: space.md, borderRadius: radius.md, padding: space.lg },
  send: { width: 40, height: 40, borderRadius: 20, alignItems: 'center', justifyContent: 'center' },
  iconBtn: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center' },
  chip: { flexDirection: 'row', alignItems: 'center', gap: 8, borderWidth: StyleSheet.hairlineWidth, borderRadius: radius.md, paddingHorizontal: 10, paddingVertical: 6, maxWidth: 260 },
  recording: { flex: 1, flexDirection: 'row', alignItems: 'center', gap: 8, minHeight: 40, borderRadius: 20, paddingHorizontal: 14 },
  removeDot: { position: 'absolute', top: -6, right: -6, width: 20, height: 20, borderRadius: 10, alignItems: 'center', justifyContent: 'center' },
  quote: { flexDirection: 'row', alignItems: 'center', gap: 6, borderRadius: radius.md, paddingHorizontal: 10, paddingVertical: 7 },
});
