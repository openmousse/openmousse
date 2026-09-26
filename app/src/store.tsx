import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { AppState, LayoutAnimation, Platform, type AppStateStatus } from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import { loadAgentName, loadServerConfig, persistAgentName, serverConfigured } from './api/base';
import { agentName, setAgentName } from './brand';
import { HttpApi, OfflineApi, timeNow, type GravaApi } from './api/client';
import { dataApi, resetServerSupport, serverSupport } from './api/data';
import { healthSupported, syncHealth } from './api/health';
import { HEALTH_KEYS, loadHealthParts, loadLive, probe, type LiveData } from './api/live';
import { onPushReceived, onPushResponse, registerCategories, registerPush, setAppBadge, type PushAction, type PushInfo } from './api/push';
import { useBanner, type BannerSpec } from './components/Banner';
import { kindLabel } from './components/InboxCard';
import { L } from './i18n';
import { openTarget } from './navigation';
import type {
  Application, JournalEntry, PendingFile,
  ActivityEntry, AgentColor, AvatarConfig, FeedItem, Goal, Group, GroupIcon, GroupPatch, InboxAction, InboxItem, MemoryItem, Message, ModelsInfo, ProfileItem, PushTarget, Receipt,
  SecurityInfo, SideChat, Task, UnreadSummary, UpcomingTask,
} from './data/types';

// 2026-09-23 起：界面上的每一项都来自服务器上的真实来源，没有示例数据。连不上服务器时各页显示"未连接"，不冒充。

interface State {
  connected: boolean;
  /** 第一次探测服务器还没结束 */
  booting: boolean;
  /** 本机的服务器配置读完了没（读完才知道要不要先进连接页） */
  configLoaded: boolean;
  /** 原生 app 还没填过服务器地址 */
  needsServer: boolean;
  /** 连上了但令牌不对 */
  authFailed: boolean;
  /** 服务器告诉我们的助手名字 */
  appName: string;
  /** 主会话还接着哪些聊天渠道（比如 Telegram），撤回时要提醒 */
  sharedChannels: string[];
  /** 训记、日历、Apple 健康。null 表示没连上。 */
  live: LiveData | null;
  liveLoading: boolean;
  liveErrors: Record<string, string>;
  groups: Group[];
  sideChats: SideChat[];
  threads: Record<string, Message[]>;
  threadModel: Record<string, string>;
  typing: Record<string, boolean>;
  /** 流式输出中的半截回复 */
  streaming: Record<string, string>;
  tasks: Task[];
  /** 收件箱：等你点头的 */
  inbox: InboxItem[];
  /** 最近 7 天点过头的（「已处理」页；「今天」页的回执也拿它看做完没有） */
  inboxRecent: InboxItem[];
  /** 这次打开 app 以来点过头的，「今天」页上显示成一行回执，换天就不显示 */
  receipts: Receipt[];
  /** 每个对话里的收件箱（等你点头的 + 最近 7 天处理过的）：对话里显示在提它的那条消息下面 */
  inboxByThread: Record<string, InboxItem[]>;
  /** 未读：各线程没看的消息、新卡片、等你点头的数量、图标角标 */
  unread: UnreadSummary;
  /** 正显示在屏幕上的对话（app 在前台时）。不为它弹小窗，新消息直接算已读。 */
  activeThread: string | null;
  feed: FeedItem[];
  upcoming: UpcomingTask[];
  goals: Goal[];
  journal: JournalEntry[];
  applications: Application[];
  memories: MemoryItem[];
  memoriesUpdated: string;
  activity: ActivityEntry[];
  profile: ProfileItem[];
  models: ModelsInfo | null;
  security: SecurityInfo | null;
  avatar: AvatarConfig;
  /** 哪几块正在读 */
  loading: Partial<Record<DataKey, boolean>>;
  /** 哪几块读失败了，原因是什么 */
  dataErrors: Partial<Record<DataKey, string>>;
}

export type DataKey = 'groups' | 'sideChats' | 'tasks' | 'inbox' | 'inboxRecent' | 'unread' | 'feed' | 'upcoming' | 'goals' | 'journal' | 'applications' | 'memories' | 'activity' | 'profile' | 'models' | 'security' | 'avatar';

interface Actions {
  /** 重新探测服务器，读回全部数据和对话记录。 */
  refreshLive(): Promise<void>;
  /** 重新读某几块数据；不传就全读。 */
  reload(...keys: DataKey[]): Promise<Partial<State>>;
  /** 读 HealthKit 推到服务器，再刷新看板（只在 iPhone 原生 app 里有效）。 */
  syncHealthNow(): Promise<void>;
  /** inboxId：这条是对收件箱里某件事的修改意见（从「今天」的「去对话里说」带过来），那件事在本地先标成「改一下」。 */
  send(threadId: string, text: string, files?: PendingFile[], opts?: { inboxId?: string }): void;
  deleteJournal(id: string): Promise<void>;
  /** 下拉刷新看板：只重读看板数据（训记 / 健康 / 派生指标）和建议、日志、申请，不重连、不重读全部。 */
  refreshBoards(): Promise<void>;
  /** 下拉刷新一个对话：按服务器记录重读，进行中的回复接上。 */
  refreshThread(threadId: string): Promise<void>;
  transcribe(file: PendingFile): Promise<string>;
  /** 长按删除：只从对话记录里去掉。 */
  deleteMessage(threadId: string, msgId: string): Promise<void>;
  /** 撤回 / 重新编辑：这条和之后的都去掉，Grava 也忘掉，返回原文。 */
  rewindMessage(threadId: string, msgId: string): Promise<string>;
  setThreadModel(threadId: string, modelId: string): void;
  /** 收件箱里的一条：同意 / 不要 / 改一下（note 是意见）。成功后这一条在「今天」页收成回执。 */
  decide(id: string, action: InboxAction, note?: string): Promise<InboxItem>;
  dismissFeed(id: string): Promise<void>;
  /** 这几张建议卡看过了（去掉「新」）。 */
  markFeedSeen(ids: string[]): void;
  /** 这个对话看到最新一条了。一般不用手动调：对话在屏幕上时 store 自己会调（见 useThreadOnScreen）。 */
  markRead(thread: string): void;
  /** 哪个对话正显示在屏幕上。thread 为 null 时，传 onlyIf 就只在当前正是它时才清（页面切换时先后顺序不定）。 */
  setActiveThread(thread: string | null, onlyIf?: string): void;
  toggleUpcoming(id: string, enabled: boolean): Promise<void>;
  /** 忘记一条长期记忆（L1）。 */
  forget(id: string): Promise<void>;
  /** 改档案（L0）的一条；text 为 null 就删掉这条。 */
  editProfile(id: string, text: string | null): Promise<void>;
  addGroup(g: { name: string; purpose: string; icon: GroupIcon; color: AgentColor; modelId: string }): Promise<string>;
  /** 改 Agent 的名字 / 图标 / 颜色 / 职责 / 默认模型（只传改了的）。改名字或职责，服务器顺带改它自己的说明。 */
  updateGroup(id: string, patch: GroupPatch): Promise<void>;
  setAvatar(a: Partial<AvatarConfig>): void;
  createSideChat(c: { title: string; purpose: string; modelId: string }): Promise<string>;
  renameSideChat(id: string, title: string): Promise<void>;
  archiveSideChat(id: string, archived?: boolean): Promise<void>;
  deleteSideChat(id: string): Promise<void>;
  /** 删 Agent：服务器上的 OpenClaw agent 去掉、工作区归档；这里的记录留着当历史。 */
  removeGroup(id: string): Promise<void>;
  cancelTask(id: string): Promise<void>;
  /** 修改意见发给做这件事的同一个子会话。 */
  reviseTask(id: string, note: string): Promise<void>;
}

const Ctx = createContext<(State & Actions) | null>(null);

/** 加一条消息。同一个 id 已经在了（比如刚按服务器重读过）就替换，不重复。 */
function appendMsg(st: State, threadId: string, m: Message): State {
  const cur = st.threads[threadId] ?? [];
  const list = cur.some((x) => x.id === m.id) ? cur.map((x) => (x.id === m.id ? m : x)) : [...cur, m];
  return { ...st, threads: { ...st.threads, [threadId]: list } };
}
let uid = 1;
const id = (p: string) => `${p}${Date.now().toString(36)}${uid++}`;
/** 和服务端 files.py 的 kind_of 一致，只用来在上传前先画对图标。 */
const kindOf = (name: string, mime: string): 'image' | 'doc' | 'audio' | 'video' | 'file' => {
  const ext = (name.split('.').pop() ?? '').toLowerCase();
  if (mime.startsWith('image/') || ['jpg', 'jpeg', 'png', 'gif', 'webp', 'heic', 'heif'].includes(ext)) return 'image';
  if (mime.startsWith('audio/') || ['m4a', 'mp3', 'wav', 'aac', 'ogg', 'flac', 'caf'].includes(ext)) return 'audio';
  if (mime.startsWith('video/') || ['mov', 'mp4', 'm4v'].includes(ext)) return 'video';
  if (mime.startsWith('text/') || ['pdf', 'docx', 'xlsx', 'pptx', 'csv', 'txt', 'md', 'json'].includes(ext)) return 'doc';
  return 'file';
};
const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));
const sleep = (ms: number) => new Promise<null>((r) => setTimeout(() => r(null), ms));
/** 本地日期 YYYY-MM-DD（回执留到换天）。 */
export const todayIso = () => new Date().toLocaleDateString('en-CA');
/** 小窗上的一段预览：去掉 Markdown 记号、压成一行。 */
const preview = (text: string) => {
  const s = (text || '').replace(/[*_`#>]+/g, '').replace(/\s+/g, ' ').trim();
  return s.length > 140 ? `${s.slice(0, 140)}…` : s;
};
const EMPTY_UNREAD: UnreadSummary = { threads: {}, feedNew: [], inbox: 0, badge: 0 };
/** 处理过的一条：从待处理里拿掉、今天的回执里加上、对话里那张卡换成回执（三处一起改，界面上同时收起）。 */
function settle(st: State, item: InboxItem): State {
  const day = todayIso();
  const wasPending = st.inbox.some((i) => i.id === item.id);
  const inboxByThread: Record<string, InboxItem[]> = {};
  for (const [tid, list] of Object.entries(st.inboxByThread)) inboxByThread[tid] = list.map((i) => (i.id === item.id ? item : i));
  return {
    ...st,
    inbox: st.inbox.filter((i) => i.id !== item.id),
    receipts: [{ item, day }, ...st.receipts.filter((r) => r.item.id !== item.id && r.day === day)],
    inboxByThread,
    unread: wasPending ? { ...st.unread, inbox: Math.max(0, st.unread.inbox - 1), badge: Math.max(0, st.unread.badge - 1) } : st.unread,
  };
}
const FINAL = new Set(['done', 'failed', 'rejected', 'withdrawn', 'expired']);

/**
 * 「今天」页要显示的回执：今天点过头、现在不在待处理里的；「已处理」列表里有更新的状态（比如做完了）就用新的。
 */
export function receiptItems(receipts: Receipt[], recent: InboxItem[], pending: InboxItem[], day = todayIso()): InboxItem[] {
  const waiting = new Set(pending.map((i) => i.id));
  const at = (i: InboxItem) => Date.parse(i.decidedAt ?? '') || 0;
  return receipts.filter((r) => r.day === day && !waiting.has(r.item.id)).map((r) => {
    const fresh = recent.find((x) => x.id === r.item.id);
    return fresh && fresh.status !== 'pending' && at(fresh) >= at(r.item) ? fresh : r.item;
  });
}

/** 每块数据怎么读、读回来放进 state 的哪里。 */
const LOADERS: Record<DataKey, () => Promise<Partial<State>>> = {
  groups: async () => ({ groups: await dataApi.groups() }),
  sideChats: async () => ({ sideChats: await dataApi.sideChats() }),
  tasks: async () => ({ tasks: await dataApi.tasks() }),
  inbox: async () => ({ inbox: await dataApi.inbox('pending') }),
  inboxRecent: async () => ({ inboxRecent: await dataApi.inbox('recent') }),
  unread: async () => { const u = await dataApi.unread(); return u ? { unread: u } : {}; },
  feed: async () => ({ feed: await dataApi.feed() }),
  upcoming: async () => ({ upcoming: await dataApi.upcoming() }),
  goals: async () => ({ goals: await dataApi.goals() }),
  journal: async () => ({ journal: await dataApi.journal() }),
  applications: async () => ({ applications: await dataApi.applications() }),
  memories: async () => { const m = await dataApi.memories(); return { memories: m.items, memoriesUpdated: m.updated }; },
  activity: async () => ({ activity: await dataApi.activity() }),
  profile: async () => ({ profile: (await dataApi.profile()).items }),
  models: async () => ({ models: await dataApi.models() }),
  security: async () => ({ security: await dataApi.security() }),
  avatar: async () => { const a = await dataApi.avatar(); return a ? { avatar: a } : {}; },
};
const ALL_KEYS = Object.keys(LOADERS) as DataKey[];
/** 连上时不跟大家一起读的：线程列表先读（对话记录要等它），未读等对话记录读完再读，「已处理」进那一页才读。 */
const STARTUP_KEYS = ALL_KEYS.filter((k) => !['groups', 'sideChats', 'unread', 'inboxRecent'].includes(k));
/** 后台轮询的：不亮「正在读」、读失败也不报错，没变化就不更新（不让整棵树白白重画）。 */
const QUIET = new Set<DataKey>(['unread']);
const POLL_MS = 45_000;

export function StoreProvider({ children }: { children: React.ReactNode }) {
  const api = useRef<GravaApi>(new OfflineApi());
  const banner = useBanner();
  const [s, setS] = useState<State>(() => ({
    connected: false,
    booting: true,
    configLoaded: false,
    needsServer: false,
    authFailed: false,
    appName: agentName(),
    sharedChannels: [],
    live: null,
    liveLoading: true,
    liveErrors: {},
    groups: [],
    sideChats: [],
    threads: {},
    threadModel: { main: 'anthropic/claude-opus-5-5' },
    typing: {},
    streaming: {},
    tasks: [],
    inbox: [],
    inboxRecent: [],
    receipts: [],
    inboxByThread: {},
    unread: EMPTY_UNREAD,
    activeThread: null,
    feed: [],
    upcoming: [],
    goals: [],
    journal: [],
    applications: [],
    memories: [],
    memoriesUpdated: '',
    activity: [],
    profile: [],
    models: null,
    security: null,
    avatar: { style: 'lens', ring: '#D9AE62', stream: '#5CCFE6' },
    loading: {},
    dataErrors: {},
  }));

  const latest = useRef(s);
  latest.current = s;
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
  useEffect(() => () => timers.current.forEach(clearTimeout), []);

  // —— 未读的簿记（都在 ref 里：只给比较用，不上界面） ——
  /** 连上以后第一次读到未读摘要之前是 false：第一次只记下现状，不重读、不弹小窗 */
  const unreadPrimed = useRef(false);
  /** 每个线程处理过的最新一条消息 id：比它新的才算「来了新消息」 */
  const seenLast = useRef<Record<string, number>>({});
  /** 处理过的新卡片 id */
  const seenFeedNew = useRef(new Set<string>());
  /** 已经知道的待处理 id（网页版只为新出现的弹小窗） */
  const knownInbox = useRef(new Set<string>());
  /** 每个线程上次告诉服务器「看到这里」的 upto（成功之后才记） */
  const lastMarked = useRef<Record<string, number | null>>({});
  const readTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  // 下面几个函数互相调用，又要给只注册一次的回调用：放进 ref，调用时取最新的
  const applyUnreadRef = useRef<(prev: UnreadSummary, next: UnreadSummary) => void>(() => {});

  /** 读回来的数据除了写进 state，也原样返回：setS 之后 latest.current 要等下一次渲染才更新，接着要用的地方直接拿返回值。 */
  const reload = useCallback(async (...keys: DataKey[]): Promise<Partial<State>> => {
    const list = keys.length ? keys : ALL_KEYS;
    const loud = list.filter((k) => !QUIET.has(k));
    const got: Partial<State> = {};
    const prevUnread = latest.current.unread;
    if (loud.length) setS((st) => ({ ...st, loading: { ...st.loading, ...Object.fromEntries(loud.map((k) => [k, true])) } }));
    await Promise.all(list.map(async (k) => {
      try {
        const patch = await LOADERS[k]();
        Object.assign(got, patch);
        if (k === 'unread') { if (patch.unread) applyUnreadRef.current(prevUnread, patch.unread); return; }
        setS((st) => {
          const dataErrors = { ...st.dataErrors }; delete dataErrors[k];
          return { ...st, ...patch, dataErrors, loading: { ...st.loading, [k]: false } };
        });
      } catch (e) {
        if (QUIET.has(k)) return;
        setS((st) => ({ ...st, dataErrors: { ...st.dataErrors, [k]: errText(e) }, loading: { ...st.loading, [k]: false } }));
      }
    }));
    return got;
  }, []);

  const syncHealthNow = useCallback(async () => {
    try {
      await syncHealth(14);
      const { patch, errors } = await loadHealthParts();
      setS((st) => {
        const liveErrors = { ...st.liveErrors, ...errors };
        for (const k of HEALTH_KEYS) if (!(k in errors)) delete liveErrors[k];
        return st.live ? { ...st, live: { ...st.live, ...patch }, liveErrors } : st;
      });
      reload('goals');  // 体脂目标可能有了新读数
    } catch (e) {
      setS((st) => ({ ...st, liveErrors: { ...st.liveErrors, health: errText(e) } }));
      throw e;
    }
  }, [reload]);

  /** 读回所有线程的对话记录；服务端还在回的线程接回去。 */
  const loadThreads = useCallback(async (fresh?: Partial<State>) => {
    const st0 = { ...latest.current, ...fresh };
    const ids = ['main', ...st0.groups.map((g) => g.id), ...st0.sideChats.map((c) => c.id)];
    const withInbox = api.current.connected && serverSupport.inbox !== false;
    const [hist, boxes] = await Promise.all([
      Promise.all(ids.map(async (tid) => [tid, await api.current.history(tid).catch(() => null)] as const)),
      Promise.all(ids.map(async (tid) => [tid, withInbox ? await dataApi.inboxForThread(tid).catch(() => null) : null] as const)),
    ]);
    const inFlight = hist.filter(([, h]) => h?.inFlight).map(([tid]) => tid);
    setS((st) => {
      const threads = { ...st.threads };
      const threadModel = { ...st.threadModel };
      const typing = { ...st.typing };
      const streaming = { ...st.streaming };
      const inboxByThread = { ...st.inboxByThread };
      for (const tid of inFlight) { typing[tid] = true; streaming[tid] = hist.find(([x]) => x === tid)?.[1]?.inFlight?.text ?? ''; }
      for (const [tid, h] of hist) {
        if (!h) continue;
        threads[tid] = h.messages;
        if (h.modelId) threadModel[tid] = h.modelId;
      }
      for (const [tid, items] of boxes) if (items) inboxByThread[tid] = items;
      return { ...st, threads, threadModel, typing, streaming, inboxByThread };
    });
    for (const tid of inFlight) {
      api.current.attach(tid, (partial) => setS((st) => ({ ...st, streaming: { ...st.streaming, [tid]: partial } })))
        .then((reply) => setS((st) => {
          const streaming = { ...st.streaming }; delete streaming[tid];
          return reply ? { ...appendMsg(st, tid, reply), typing: { ...st.typing, [tid]: false }, streaming } : { ...st, typing: { ...st.typing, [tid]: false }, streaming };
        }))
        .catch(() => setS((st) => ({ ...st, typing: { ...st.typing, [tid]: false } })));
    }
  }, []);

  /** 重读一个对话里的收件箱（对话里跟着消息显示的那些卡）。 */
  const loadThreadInbox = useCallback(async (threadId: string) => {
    if (!api.current.connected || serverSupport.inbox === false) return;
    const items = await dataApi.inboxForThread(threadId).catch(() => null);
    if (items) setS((st) => ({ ...st, inboxByThread: { ...st.inboxByThread, [threadId]: items } }));
  }, []);

  /** 按服务器记录重读一个线程，进行中的回复接上（下拉刷新、来了新消息、点通知进来都走这里）。 */
  const refreshThread = useCallback(async (threadId: string) => {
    loadThreadInbox(threadId).catch(() => {});
    const h = await api.current.history(threadId);
    if (!h) return;
    setS((st) => ({ ...st, threads: { ...st.threads, [threadId]: h.messages }, threadModel: h.modelId ? { ...st.threadModel, [threadId]: h.modelId } : st.threadModel }));
    if (h.inFlight && !latest.current.typing[threadId]) {
      setS((st) => ({ ...st, typing: { ...st.typing, [threadId]: true }, streaming: { ...st.streaming, [threadId]: h.inFlight?.text ?? '' } }));
      api.current.attach(threadId, (partial) => setS((st) => ({ ...st, streaming: { ...st.streaming, [threadId]: partial } })))
        .then((reply) => setS((st) => { const streaming = { ...st.streaming }; delete streaming[threadId]; return reply ? { ...appendMsg(st, threadId, reply), typing: { ...st.typing, [threadId]: false }, streaming } : { ...st, typing: { ...st.typing, [threadId]: false }, streaming }; }))
        .catch(() => setS((st) => ({ ...st, typing: { ...st.typing, [threadId]: false } })));
    }
  }, [loadThreadInbox]);

  /** 收件箱变了：对话里的卡也重读。新出现的待处理在哪些对话、哪些对话里还有没办完的、正看着的那个。 */
  const refreshThreadInboxes = useCallback((pending?: InboxItem[]) => {
    const st = latest.current;
    const tids = new Set<string>((pending ?? st.inbox).map((i) => i.thread));
    for (const [tid, list] of Object.entries(st.inboxByThread)) if (list.some((i) => !FINAL.has(i.status))) tids.add(tid);
    if (st.activeThread) tids.add(st.activeThread);
    tids.forEach((tid) => { loadThreadInbox(tid).catch(() => {}); });
  }, [loadThreadInbox]);

  /** 「今天」页有回执还在等结果（同意了在做、让它改了）：顺带读一下「已处理」，看做完没有。 */
  const withRecent = useCallback((): DataKey[] => {
    const st = latest.current;
    return receiptItems(st.receipts, st.inboxRecent, st.inbox).some((i) => !FINAL.has(i.status)) ? ['inboxRecent'] : [];
  }, []);

  // —— 小窗 ——
  /** 正看着的那个对话不弹。 */
  const bannerAllowed = (tg: PushTarget | null | undefined) => {
    const onScreen = latest.current.activeThread;
    const th = !tg ? undefined : tg.type === 'thread' ? tg.thread : tg.type === 'card' || tg.type === 'inbox' ? tg.thread : undefined;
    return !(onScreen && th && th === onScreen);
  };
  const showBanner = (b: BannerSpec) => { if (bannerAllowed(b.target)) banner.show(b); };
  // 小窗的 key 由 Banner 按「来源 + 去处」算：同一件事再来一条就替换，不重复排队
  const inboxBanner = (it: InboxItem, subtitle?: string): BannerSpec => ({
    kind: 'inbox', title: it.title, subtitle: subtitle || `${L('要你点头', 'Needs your OK')} · ${kindLabel(it.kind)}`,
    body: it.why || it.changes[0] || '', source: it.source, target: { type: 'inbox', id: it.id, thread: it.thread },
  });
  const cardBanner = (f: FeedItem, subtitle?: string, fallback?: string, kind = 'card'): BannerSpec => ({
    kind, title: f.title, subtitle: subtitle || L('新卡片', 'New card'), body: preview(f.body) || fallback || '',
    source: f.groupId ?? 'main', target: { type: 'card', id: f.id, thread: f.groupId ?? undefined },
  });

  /**
   * 读到一份新的未读摘要（轮询、标已读、推送之后）：
   * 线程有了更新的一条 → 重读那个线程；新卡片变了 → 重读建议卡；等你点头的数量变了 → 重读收件箱。
   * 正在看的线程直接算已读。网页版没有推送，在这里补小窗：只为你发的消息有了回复、新的要你点头；别的只更新角标和「新」。
   */
  applyUnreadRef.current = (prev, raw) => {
    const st = latest.current;
    const active = st.activeThread;
    let next = raw;
    const onScreen = active ? raw.threads[active] : undefined;
    if (active && onScreen) {
      const threads = { ...raw.threads };
      delete threads[active];
      next = { ...raw, threads, badge: Math.max(0, raw.badge - onScreen.mine) };
    }
    setS((cur) => (JSON.stringify(cur.unread) === JSON.stringify(next) ? cur : { ...cur, unread: next }));
    setAppBadge(next.badge);
    if (active && onScreen) markRead(active);
    if (!unreadPrimed.current) {
      // 第一次：只记下现状（对话记录、建议卡、收件箱刚刚都读过）
      unreadPrimed.current = true;
      for (const [tid, u] of Object.entries(raw.threads)) if (u.last) seenLast.current[tid] = Math.max(seenLast.current[tid] ?? 0, u.last.id);
      raw.feedNew.forEach((fid) => seenFeedNew.current.add(fid));
      return;
    }
    const web = Platform.OS === 'web';
    // 1. 线程
    const known = new Set(['main', ...st.groups.map((g) => g.id), ...st.sideChats.map((c) => c.id)]);
    let unknown = false;
    let changed = false;
    for (const [tid, u] of Object.entries(raw.threads)) {
      const lastId = u.last?.id ?? 0;
      if (!lastId || lastId <= (seenLast.current[tid] ?? 0)) continue;
      seenLast.current[tid] = lastId;
      changed = true;
      if (!known.has(tid)) unknown = true;
      if (!st.typing[tid]) refreshThread(tid).catch(() => {});
      // 网页版没有推送：只为「你发的消息有了回复」弹小窗（mine 变多了），后台自己跑出来的只标未读
      if (web && tid !== active && u.last && u.mine > (prev.threads[tid]?.mine ?? 0)) {
        showBanner({ kind: 'reply', title: '', subtitle: L('回复了', 'Replied'), body: preview(u.last.text), source: tid, target: { type: 'thread', thread: tid } });
      }
    }
    if (unknown) reload('groups', 'sideChats').catch(() => {});  // 别的设备上刚建的 Agent / 空间
    if (changed) { const r = withRecent(); if (r.length) reload(...r).catch(() => {}); }
    // 2. 新卡片：多了就重读（卡片上标「新」，不弹小窗）；少了（别的设备上看过了）也重读，把「新」去掉
    const added = raw.feedNew.filter((fid) => !seenFeedNew.current.has(fid));
    added.forEach((fid) => seenFeedNew.current.add(fid));
    const gone = prev.feedNew.some((fid) => !raw.feedNew.includes(fid));
    if (added.length || gone) reload('feed').catch(() => {});
    // 3. 等你点头的数量变了：重读；网页版为新出现的弹小窗
    if (raw.inbox !== prev.inbox) {
      const before = new Set(knownInbox.current);
      reload('inbox', ...withRecent()).then((got) => {
        refreshThreadInboxes(got.inbox);
        if (!web || !got.inbox) return;
        got.inbox.filter((i) => !before.has(i.id)).slice(0, 3).reverse().forEach((i) => showBanner(inboxBanner(i)));
      }).catch(() => {});
    }
  };
  useEffect(() => { s.inbox.forEach((i) => knownInbox.current.add(i.id)); }, [s.inbox]);
  // 图标角标跟着未读走（第一次读到之前不动它，免得把推送带来的数字先清成 0）
  useEffect(() => { if (unreadPrimed.current) setAppBadge(s.unread.badge); }, [s.unread.badge]);

  /** 告诉服务器这个线程看到最后一条了（等 0.8 秒，连着来的几条只发一次）。 */
  const markRead = useCallback((thread: string) => {
    clearTimeout(readTimers.current[thread]);
    readTimers.current[thread] = setTimeout(() => {
      const st = latest.current;
      if (!st.connected || serverSupport.unread === false || AppState.currentState !== 'active' || st.activeThread !== thread) return;
      const msgs = st.threads[thread];
      if (!msgs) return;
      let upto: number | null = null;
      for (let i = msgs.length - 1; i >= 0; i--) {
        const m = /^db(\d+)$/.exec(msgs[i].id);
        if (m) { upto = Number(m[1]); break; }
      }
      setS((cur) => {
        const u = cur.unread.threads[thread];
        if (!u) return cur;
        const threads = { ...cur.unread.threads }; delete threads[thread];
        return { ...cur, unread: { ...cur.unread, threads, badge: Math.max(0, cur.unread.badge - u.mine) } };
      });
      // 同一个位置已经报过：服务器还说有未读，是它有这里还没读回来的新消息，等对话记录重读完 upto 变了再报
      if (thread in lastMarked.current && lastMarked.current[thread] === upto) return;
      dataApi.markRead(thread, upto ?? undefined)
        .then((res) => { lastMarked.current[thread] = upto; if (res) applyUnreadRef.current(latest.current.unread, res); })
        .catch(() => {});
    }, 800);
  }, []);

  const setActiveThread = useCallback((thread: string | null, onlyIf?: string) => {
    setS((st) => {
      if (thread === null && onlyIf !== undefined && st.activeThread !== onlyIf) return st;
      return st.activeThread === thread ? st : { ...st, activeThread: thread };
    });
  }, []);

  // 对话在屏幕上、消息读回来了、又来了新消息：都标已读
  const activeMsgs = s.activeThread ? s.threads[s.activeThread] : undefined;
  const activeLast = activeMsgs ? activeMsgs[activeMsgs.length - 1]?.id ?? '' : null;
  const activeUnread = s.activeThread ? s.unread.threads[s.activeThread]?.n ?? 0 : 0;
  useEffect(() => {
    if (!s.activeThread || activeLast === null || !s.connected) return;
    markRead(s.activeThread);
  }, [s.activeThread, activeLast, activeUnread, s.connected, markRead]);

  // —— 收件箱 ——
  const decide = useCallback(async (itemId: string, action: InboxAction, note?: string): Promise<InboxItem> => {
    const st = latest.current;
    const current = st.inbox.find((i) => i.id === itemId) ?? Object.values(st.inboxByThread).flat().find((i) => i.id === itemId) ?? st.receipts.find((r) => r.item.id === itemId)?.item;
    const item = await dataApi.decideInbox(itemId, action, note, current);
    const done = { ...item, decidedAt: item.decidedAt ?? new Date().toISOString() };
    // 卡片收成一行回执（「今天」和对话里同时）：用布局动画过渡（网页上没有动画，直接换）
    LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut);
    setS((cur) => settle(cur, done));
    reload('unread', 'activity').catch(() => {});
    if (done.thread) loadThreadInbox(done.thread).catch(() => {});
    return done;
  }, [reload, loadThreadInbox]);

  const markFeedSeen = useCallback((ids: string[]) => {
    if (!ids.length) return;
    const set = new Set(ids);
    ids.forEach((fid) => seenFeedNew.current.add(fid));
    setS((st) => ({
      ...st,
      feed: st.feed.map((f) => (set.has(f.id) ? { ...f, seen: true } : f)),
      unread: { ...st.unread, feedNew: st.unread.feedNew.filter((fid) => !set.has(fid)) },
    }));
    dataApi.feedSeen(ids).catch(() => {});  // 老服务器没有这个接口：本地去掉「新」就行
  }, []);

  // —— 推送 ——
  /** 冷启动时点通知 / 点「同意」：等连上、线程列表读回来（才知道是不是 Agent）再处理。 */
  const respReady = useRef(false);
  const respQueue = useRef<[PushAction, PushInfo][]>([]);

  const handleResponse = async (action: PushAction, p: PushInfo) => {
    const st = latest.current;
    const tg: PushTarget = p.target ?? { type: 'today' };
    if (action === 'approve' && tg.type === 'inbox') {
      const fail = (msg: string) => banner.show({ kind: 'inbox', title: L('没同意成', "Couldn't approve"), subtitle: '', body: msg, source: tg.thread ?? 'main', target: tg });
      if (!st.connected) { fail(L('没连上服务器。', 'Not connected to the server.')); return; }
      try {
        const item = await decide(tg.id, 'approve');
        banner.show({ kind: 'inbox', title: L(`已同意：${item.title || p.body}`, `Approved: ${item.title || p.body}`), subtitle: '', body: '', source: item.source, target: tg });
      } catch (e) {
        fail(errText(e));
      }
      return;
    }
    // 点通知 / 「看一下」「看卡片」：先把它指的那块重读，再跳过去
    if (st.connected) {
      if (tg.type === 'thread' && tg.thread !== 'today') { if (!st.typing[tg.thread]) refreshThread(tg.thread).catch(() => {}); }
      else if (tg.type === 'card') reload('feed').catch(() => {});
      else if (tg.type === 'inbox') reload('inbox', ...withRecent()).then((got) => refreshThreadInboxes(got.inbox)).catch(() => {});
      else reload('feed', 'inbox').catch(() => {});
      reload('unread').catch(() => {});
    }
    const thread = tg.type === 'thread' ? tg.thread : undefined;
    openTarget(tg, !!thread && st.groups.some((g) => g.id === thread));
  };

  /**
   * app 开着时收到推送：系统不显示（见 api/push.ts）。先重读它指的那块和未读；
   * 值得打断的（level = ring，老服务器没有 level 也算）再滑下小窗，正看着那个对话就不弹。quiet 的只更新数据、角标和「新」。
   */
  const handleReceived = async (p: PushInfo) => {
    const st = latest.current;
    if (!st.connected) return;
    const tg = p.target;
    const none = (): Partial<State> => ({});
    let job: Promise<Partial<State>>;
    if (tg?.type === 'thread' && tg.thread !== 'today') job = st.typing[tg.thread] ? Promise.resolve(none()) : refreshThread(tg.thread).then(none, none);
    else if (tg?.type === 'card') job = reload('feed');
    else if (tg?.type === 'inbox') job = reload('inbox', ...withRecent()).then((got) => { refreshThreadInboxes(got.inbox); return got; });
    else job = reload('feed', 'inbox');
    reload('unread').catch(() => {});
    if (!tg || tg.type !== 'inbox') { const r = withRecent(); if (r.length) reload(...r).catch(() => {}); }
    const ring = !p.level || p.level === 'ring';
    if (!ring || AppState.currentState !== 'active' || !bannerAllowed(tg)) return;
    // 等它指的那条读回来（最多 1.5 秒），小窗上好写卡片 / 事情的标题
    const got: Partial<State> = (await Promise.race([job.catch(none), sleep(1500)])) ?? {};
    const now = latest.current;
    const kind = p.kind ?? undefined;
    if (tg?.type === 'inbox') {
      const it = (got.inbox ?? now.inbox).find((i) => i.id === tg.id) ?? Object.values(now.inboxByThread).flat().find((i) => i.id === tg.id);
      showBanner(it ? { ...inboxBanner(it, p.subtitle), kind: kind ?? 'inbox' } : { kind: kind ?? 'inbox', title: p.body, subtitle: p.subtitle || L('要你点头', 'Needs your OK'), body: '', source: tg.thread ?? 'main', target: tg });
    } else if (tg?.type === 'card') {
      const f = (got.feed ?? now.feed).find((x) => x.id === tg.id);
      showBanner(f ? cardBanner(f, p.subtitle, p.body, kind) : { kind: kind ?? 'card', title: p.body, subtitle: p.subtitle, body: '', source: tg.thread ?? 'main', target: tg });
    } else if (tg?.type === 'thread' && tg.thread !== 'today') {
      showBanner({ kind: kind ?? 'reply', title: '', subtitle: p.subtitle || L('回复了', 'Replied'), body: p.body, source: tg.thread, target: tg });
    } else {
      const title = p.title && p.title !== agentName() ? p.title : '';
      showBanner({ kind: kind ?? 'report', title: title || p.body, subtitle: p.subtitle, body: title ? p.body : '', source: 'main', target: tg ?? { type: 'today' } });
    }
  };

  const pushHandlers = useRef({ received: handleReceived, response: handleResponse });
  pushHandlers.current = { received: handleReceived, response: handleResponse };
  const flushResponses = () => {
    respReady.current = true;
    const q = respQueue.current;
    respQueue.current = [];
    q.forEach(([a, p]) => { pushHandlers.current.response(a, p).catch(() => {}); });
  };
  const flushRef = useRef(flushResponses);
  flushRef.current = flushResponses;

  useEffect(() => {
    registerCategories();  // 按钮文字跟语言走：切换语言时 store 整个重挂，这里会再注册一遍
    const offReceived = onPushReceived((p) => { pushHandlers.current.received(p).catch(() => {}); });
    const offResponse = onPushResponse((action, p) => {
      if (!respReady.current) { respQueue.current.push([action, p]); return; }
      pushHandlers.current.response(action, p).catch(() => {});
    });
    return () => { offReceived(); offResponse(); };
  }, []);

  const refreshLive = useCallback(() => {
    setS((st) => ({ ...st, liveLoading: true }));
    return loadServerConfig().then(async () => {
      const remembered = await loadAgentName();
      if (remembered) { setAgentName(remembered); setS((st) => ({ ...st, appName: agentName() })); }
      if (!serverConfigured()) {
        api.current = new OfflineApi();
        setS((st) => ({ ...st, configLoaded: true, needsServer: true, connected: false, booting: false, live: null, liveLoading: false }));
        flushRef.current();
        return;
      }
      const p = await probe();
      if (p.status !== 'ok') {
        api.current = new OfflineApi();
        setS((st) => ({ ...st, configLoaded: true, needsServer: false, authFailed: p.status === 'auth', connected: false, booting: false, live: null, liveLoading: false }));
        flushRef.current();
        return;
      }
      api.current = new HttpApi();
      // 可能换了服务器：接口支不支持、未读的簿记都从头来
      resetServerSupport();
      unreadPrimed.current = false;
      seenLast.current = {};
      seenFeedNew.current = new Set();
      lastMarked.current = {};
      setAgentName(p.appName);
      persistAgentName(p.appName).catch(() => {});
      setS((st) => ({ ...st, configLoaded: true, needsServer: false, authFailed: false, appName: agentName(), sharedChannels: p.sharedChannels, connected: true, booting: false }));
      // 线程列表先到，对话记录才知道要读哪些；对话记录读完再读未读（第一次只记下现状）。其余各块并行读，谁先回来先显示。
      const lists = reload('groups', 'sideChats').then((got) => loadThreads(got)).then(() => reload('unread')).finally(() => flushRef.current());
      const rest = reload(...STARTUP_KEYS);
      const live = loadLive().then(({ data, errors }) => setS((st) => ({ ...st, live: data, liveErrors: errors, liveLoading: false })));
      await Promise.all([lists, rest, live]);
      // Apple 健康：每次连上都把最近两周重推一遍（服务端按天覆盖），推完刷新看板。
      if (healthSupported()) syncHealthNow().catch(() => {});
      registerPush().catch(() => {});  // 推送 token 交给服务器（只在真机上）
    });
  }, [reload, loadThreads, syncHealthNow]);
  useEffect(() => { refreshLive(); }, [refreshLive]);

  // 回到前台：对话记录按服务器的为准重读一遍（切后台时断掉的回复会补回来），进行中的接上；收件箱、未读一起重读。
  const hidden = useRef<number | null>(null);
  useEffect(() => {
    const sub = AppState.addEventListener('change', (next) => {
      if (next !== 'active') { if (hidden.current == null) hidden.current = Date.now(); return; }
      const away = hidden.current ? Date.now() - hidden.current : 0;
      hidden.current = null;
      if (away > 3000 && latest.current.connected) {
        loadThreads().catch(() => {});
        reload('feed', 'journal', 'inbox', 'unread', ...withRecent()).catch(() => {});
      }
    });
    return () => sub.remove();
  }, [loadThreads, reload, withRecent]);

  // 未读轮询：app 在前台（网页：页面可见）时每 45 秒一次。服务器没有这个接口就不轮询。
  useEffect(() => {
    if (!s.connected) return undefined;
    const h = setInterval(() => {
      if (serverSupport.unread === false || AppState.currentState !== 'active') return;
      reload('unread').catch(() => {});
    }, POLL_MS);
    return () => clearInterval(h);
  }, [s.connected, reload]);

  const send = useCallback((threadId: string, text: string, files?: PendingFile[], opts?: { inboxId?: string }) => {
    const pending = files?.map((f, i) => ({ id: `local${i}`, name: f.name, mime: f.mime, size: f.size, kind: kindOf(f.name, f.mime), url: f.uri }));
    // '（见附件）' 是占位标记，和服务端 chat.py 一致，ChatView 按原文比较后隐藏：不翻译。
    const mine: Message = { id: id('u'), role: 'user', time: timeNow(), body: { type: 'text', text: text || '（见附件）', attachments: pending } };
    const modelId = latest.current.threadModel[threadId] ?? latest.current.threadModel.main;
    if (opts?.inboxId) LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut);
    setS((st) => {
      const next = { ...appendMsg(st, threadId, mine), typing: { ...st.typing, [threadId]: true }, sideChats: st.sideChats.map((c) => (c.id === threadId ? { ...c, updatedAt: Date.now() } : c)) };
      // 对收件箱里某件事的修改意见：那件事先在本地标成「改一下」（服务器收到 inboxId 会把它退回去改）
      const item = opts?.inboxId ? st.inbox.find((i) => i.id === opts.inboxId) ?? Object.values(st.inboxByThread).flat().find((i) => i.id === opts.inboxId) : undefined;
      return item ? settle(next, { ...item, status: 'revising', note: text, decidedAt: new Date().toISOString() }) : next;
    });
    const swapId = (userId: string) => setS((st) => ({ ...st, threads: { ...st.threads, [threadId]: (st.threads[threadId] ?? []).map((m) => (m.id === mine.id ? { ...m, id: userId } : m)) } }));
    api.current.send(threadId, text, modelId, (partial) => setS((st) => ({ ...st, streaming: { ...st.streaming, [threadId]: partial } })), swapId, files, opts?.inboxId ? { inboxId: opts.inboxId } : undefined)
      .catch((e: unknown): Message => ({ id: id('r'), role: 'grava', time: timeNow(), modelId, body: { type: 'text', text: L('（这条没发出去。）', "(This message wasn't sent.)") }, error: errText(e) }))
      .then((reply) => { reload('feed'); loadThreadInbox(threadId).catch(() => {}); return reply; })  // 可能刚写了一张建议卡、提了一件要你点头的事
      .then((reply) => setS((st) => {
        const streaming = { ...st.streaming }; delete streaming[threadId];
        return {
          ...appendMsg(st, threadId, reply),
          typing: { ...st.typing, [threadId]: false },
          streaming,
          groups: st.groups.map((g) => (g.id === threadId ? { ...g, lastLine: reply.body.text } : g)),
          sideChats: st.sideChats.map((c) => (c.id === threadId ? { ...c, lastLine: reply.body.text, updatedAt: Date.now() } : c)),
        };
      }));
  }, [reload, loadThreadInbox]);

  // 调试用：开发服务器（npm run web）里 ?say=你好 会在连上后自动往主对话发一条（真的发给 agent），方便截图流式输出。
  // 生产构建不认它：否则别人发一个带 ?say= 的链接，点开就等于替你给 agent 下指令。
  const said = useRef(false);
  useEffect(() => {
    if (!__DEV__ || said.current || !s.connected || Platform.OS !== 'web' || typeof window === 'undefined') return;
    const text = new URLSearchParams(window.location.search).get('say');
    if (text) { said.current = true; timers.current.push(setTimeout(() => send('main', text), 300)); }
  }, [s.connected, send]);

  const actions: Actions = useMemo(() => ({
    refreshLive,
    reload,
    syncHealthNow,
    send,
    transcribe: (file) => api.current.transcribe(file),
    refreshBoards: async () => {
      setS((st) => ({ ...st, liveLoading: true }));
      const [{ data, errors }] = await Promise.all([loadLive(), reload('feed', 'journal', 'applications', 'goals')]);
      setS((st) => ({ ...st, live: data, liveErrors: errors, liveLoading: false }));
    },
    refreshThread,
    deleteJournal: async (jid) => { await dataApi.deleteJournal(jid); setS((st) => ({ ...st, journal: st.journal.filter((e) => e.id !== jid) })); reload('activity'); },
    deleteMessage: async (threadId, msgId) => {
      await api.current.deleteMessage(threadId, msgId);
      setS((st) => ({ ...st, threads: { ...st.threads, [threadId]: (st.threads[threadId] ?? []).filter((m) => m.id !== msgId) } }));
      reload('activity');
    },
    rewindMessage: async (threadId, msgId) => {
      const m = (latest.current.threads[threadId] ?? []).find((x) => x.id === msgId);
      if (!m || m.role !== 'user') return '';
      const text = await api.current.rewind(threadId, msgId);
      setS((st) => {
        const cur = st.threads[threadId] ?? [];
        const at = cur.findIndex((x) => x.id === msgId);
        return at < 0 ? st : { ...st, threads: { ...st.threads, [threadId]: cur.slice(0, at) } };
      });
      reload('activity');
      return text;
    },
    setThreadModel: (threadId, modelId) => {
      if (latest.current.connected) api.current.setModel(threadId, modelId);
      setS((st) => ({ ...st, threadModel: { ...st.threadModel, [threadId]: modelId } }));
    },
    decide,
    dismissFeed: async (fid) => {
      setS((st) => ({ ...st, feed: st.feed.filter((f) => f.id !== fid) }));
      await dataApi.dismissFeed(fid);
    },
    markFeedSeen,
    markRead,
    setActiveThread,
    toggleUpcoming: async (jid, enabled) => {
      setS((st) => ({ ...st, upcoming: st.upcoming.map((u) => (u.id === jid ? { ...u, enabled } : u)) }));
      try { await dataApi.toggleUpcoming(jid, enabled); } finally { await reload('upcoming', 'activity'); }
    },
    forget: async (mid) => {
      await dataApi.forget(mid);
      await reload('memories', 'activity');
    },
    editProfile: async (pid, text) => {
      await dataApi.editProfile(pid, text);
      await reload('profile', 'activity');
    },
    addGroup: async (g) => {
      const gid = await dataApi.createGroup({ name: g.name, purpose: g.purpose, icon: g.icon, color: g.color, model: g.modelId });
      setS((st) => ({ ...st, threads: { ...st.threads, [gid]: [] }, threadModel: { ...st.threadModel, [gid]: g.modelId } }));
      await reload('groups', 'activity');
      return gid;
    },
    updateGroup: async (gid, p) => {
      const body = {
        ...(p.name !== undefined ? { name: p.name } : {}), ...(p.icon !== undefined ? { icon: p.icon } : {}), ...(p.color !== undefined ? { color: p.color } : {}),
        ...(p.purpose !== undefined ? { purpose: p.purpose } : {}), ...(p.modelId !== undefined ? { model: p.modelId } : {}),
      };
      if (!Object.keys(body).length) return;
      const fresh = await dataApi.patchGroup(gid, body);
      // 服务器回了改完的那一条就用它；没回就按改的内容先改本地（列表里的 lastLine 这些留着）
      const local: Partial<Group> = { ...(p.name !== undefined ? { name: p.name } : {}), ...(p.icon !== undefined ? { icon: p.icon } : {}), ...(p.color !== undefined ? { color: p.color } : {}), ...(p.purpose !== undefined ? { purpose: p.purpose } : {}), ...(p.modelId !== undefined ? { modelId: p.modelId } : {}) };
      setS((st) => ({
        ...st,
        groups: st.groups.map((g) => (g.id === gid ? { ...g, ...local, ...(fresh ?? {}), lastLine: fresh?.lastLine ?? g.lastLine } : g)),
        threadModel: p.modelId ? { ...st.threadModel, [gid]: p.modelId } : st.threadModel,
      }));
      reload('activity').catch(() => {});
    },
    setAvatar: (a) => {
      const next = { ...latest.current.avatar, ...a };
      setS((st) => ({ ...st, avatar: next }));
      if (latest.current.connected) dataApi.setAvatar(next).catch(() => {});
    },
    createSideChat: async (c) => {
      const cid = await dataApi.createSideChat({ title: c.title, purpose: c.purpose, model: c.modelId });
      setS((st) => ({ ...st, threads: { ...st.threads, [cid]: [] }, threadModel: { ...st.threadModel, [cid]: c.modelId } }));
      await reload('sideChats', 'activity');
      return cid;
    },
    renameSideChat: async (cid, title) => {
      await dataApi.patchSideChat(cid, { title });
      await reload('sideChats');
    },
    archiveSideChat: async (cid, archived = true) => {
      await dataApi.patchSideChat(cid, { archived });
      await reload('sideChats', 'activity');
    },
    removeGroup: async (gid) => {
      await dataApi.deleteGroup(gid);
      setS((st) => ({ ...st, groups: st.groups.filter((g) => g.id !== gid) }));
      reload('activity').catch(() => {});
    },
    deleteSideChat: async (cid) => {
      await dataApi.deleteSideChat(cid);
      setS((st) => { const threads = { ...st.threads }; delete threads[cid]; return { ...st, threads }; });
      await reload('sideChats', 'activity');
    },
    cancelTask: async (tid) => {
      await dataApi.cancelTask(tid);
      await reload('tasks', 'activity');
    },
    reviseTask: async (tid, note) => {
      await dataApi.reviseTask(tid, note);
      await reload('tasks', 'activity');
    },
  }), [refreshLive, reload, syncHealthNow, send, refreshThread, decide, markFeedSeen, markRead, setActiveThread]);

  const value = useMemo(() => ({ ...s, ...actions }), [s, actions]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useStore() {
  const v = useContext(Ctx);
  if (!v) throw new Error('StoreProvider missing');
  return v;
}

/**
 * 这个对话正显示在屏幕上：页面在前台（useFocusEffect）并且 app 在前台。
 * 这期间不为它弹小窗，来了新消息直接标已读；离开页面或 app 进后台就清掉。
 */
export function useThreadOnScreen(thread: string | null) {
  const { setActiveThread } = useStore();
  useFocusEffect(useCallback(() => {
    if (!thread) return undefined;
    const apply = (st: AppStateStatus) => setActiveThread(st === 'active' ? thread : null, thread);
    apply(AppState.currentState);
    const sub = AppState.addEventListener('change', apply);
    return () => { sub.remove(); setActiveThread(null, thread); };
  }, [thread, setActiveThread]));
}
