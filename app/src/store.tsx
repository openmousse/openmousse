import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { AppState, Platform } from 'react-native';
import { loadAgentName, loadServerConfig, persistAgentName, serverConfigured } from './api/base';
import { agentName, setAgentName } from './brand';
import { HttpApi, OfflineApi, timeNow, type GravaApi } from './api/client';
import { dataApi } from './api/data';
import { healthSupported, syncHealth } from './api/health';
import { HEALTH_KEYS, loadHealthParts, loadLive, probe, type LiveData } from './api/live';
import { onNotificationOpen, registerPush } from './api/push';
import { L } from './i18n';
import { openThread } from './navigation';
import type {
  Application, JournalEntry, PendingFile,
  ActivityEntry, Approval, AvatarConfig, FeedItem, Goal, Group, GroupIcon, MemoryItem, Message, ModelsInfo, ProfileItem,
  SecurityInfo, SideChat, Task, UpcomingTask,
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
  approvals: Approval[];
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

export type DataKey = 'groups' | 'sideChats' | 'tasks' | 'approvals' | 'feed' | 'upcoming' | 'goals' | 'journal' | 'applications' | 'memories' | 'activity' | 'profile' | 'models' | 'security' | 'avatar';

interface Actions {
  /** 重新探测服务器，读回全部数据和对话记录。 */
  refreshLive(): void;
  /** 重新读某几块数据；不传就全读。 */
  reload(...keys: DataKey[]): Promise<Partial<State>>;
  /** 读 HealthKit 推到服务器，再刷新看板（只在 iPhone 原生 app 里有效）。 */
  syncHealthNow(): Promise<void>;
  send(threadId: string, text: string, files?: PendingFile[]): void;
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
  decide(approvalId: string, allow: boolean): Promise<void>;
  dismissFeed(id: string): Promise<void>;
  toggleUpcoming(id: string, enabled: boolean): Promise<void>;
  /** 忘记一条长期记忆（L1）。 */
  forget(id: string): Promise<void>;
  /** 改档案（L0）的一条；text 为 null 就删掉这条。 */
  editProfile(id: string, text: string | null): Promise<void>;
  addGroup(g: { name: string; purpose: string; icon: GroupIcon; modelId: string }): Promise<string>;
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

function appendMsg(st: State, threadId: string, m: Message): State {
  return { ...st, threads: { ...st.threads, [threadId]: [...(st.threads[threadId] ?? []), m] } };
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

/** 每块数据怎么读、读回来放进 state 的哪里。 */
const LOADERS: Record<DataKey, () => Promise<Partial<State>>> = {
  groups: async () => ({ groups: await dataApi.groups() }),
  sideChats: async () => ({ sideChats: await dataApi.sideChats() }),
  tasks: async () => ({ tasks: await dataApi.tasks() }),
  approvals: async () => ({ approvals: await dataApi.approvals() }),
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

export function StoreProvider({ children }: { children: React.ReactNode }) {
  const api = useRef<GravaApi>(new OfflineApi());
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
    approvals: [],
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

  /** 读回来的数据除了写进 state，也原样返回：setS 之后 latest.current 要等下一次渲染才更新，接着要用的地方直接拿返回值。 */
  const reload = useCallback(async (...keys: DataKey[]): Promise<Partial<State>> => {
    const list = keys.length ? keys : ALL_KEYS;
    const got: Partial<State> = {};
    setS((st) => ({ ...st, loading: { ...st.loading, ...Object.fromEntries(list.map((k) => [k, true])) } }));
    await Promise.all(list.map(async (k) => {
      try {
        const patch = await LOADERS[k]();
        Object.assign(got, patch);
        setS((st) => {
          const dataErrors = { ...st.dataErrors }; delete dataErrors[k];
          return { ...st, ...patch, dataErrors, loading: { ...st.loading, [k]: false } };
        });
      } catch (e) {
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
    const hist = await Promise.all(ids.map(async (tid) => [tid, await api.current.history(tid).catch(() => null)] as const));
    const inFlight = hist.filter(([, h]) => h?.inFlight).map(([tid]) => tid);
    setS((st) => {
      const threads = { ...st.threads };
      const threadModel = { ...st.threadModel };
      const typing = { ...st.typing };
      const streaming = { ...st.streaming };
      for (const tid of inFlight) { typing[tid] = true; streaming[tid] = hist.find(([x]) => x === tid)?.[1]?.inFlight?.text ?? ''; }
      for (const [tid, h] of hist) {
        if (!h) continue;
        threads[tid] = h.messages;
        if (h.modelId) threadModel[tid] = h.modelId;
      }
      return { ...st, threads, threadModel, typing, streaming };
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

  const refreshLive = useCallback(() => {
    setS((st) => ({ ...st, liveLoading: true }));
    loadServerConfig().then(async () => {
      const remembered = await loadAgentName();
      if (remembered) { setAgentName(remembered); setS((st) => ({ ...st, appName: agentName() })); }
      if (!serverConfigured()) {
        api.current = new OfflineApi();
        setS((st) => ({ ...st, configLoaded: true, needsServer: true, connected: false, booting: false, live: null, liveLoading: false }));
        return;
      }
      const p = await probe();
      if (p.status !== 'ok') {
        api.current = new OfflineApi();
        setS((st) => ({ ...st, configLoaded: true, needsServer: false, authFailed: p.status === 'auth', connected: false, booting: false, live: null, liveLoading: false }));
        return;
      }
      api.current = new HttpApi();
      setAgentName(p.appName);
      persistAgentName(p.appName).catch(() => {});
      setS((st) => ({ ...st, configLoaded: true, needsServer: false, authFailed: false, appName: agentName(), sharedChannels: p.sharedChannels, connected: true, booting: false }));
      // 线程列表先到，对话记录才知道要读哪些。其余各块并行读，谁先回来先显示。
      const lists = reload('groups', 'sideChats').then((got) => loadThreads(got));
      const rest = reload(...ALL_KEYS.filter((k) => k !== 'groups' && k !== 'sideChats'));
      const live = loadLive().then(({ data, errors }) => setS((st) => ({ ...st, live: data, liveErrors: errors, liveLoading: false })));
      await Promise.all([lists, rest, live]);
      // Apple 健康：每次连上都把最近两周重推一遍（服务端按天覆盖），推完刷新看板。
      if (healthSupported()) syncHealthNow().catch(() => {});
      registerPush().catch(() => {});  // 推送 token 交给服务器（只在真机上）
    });
  }, [reload, loadThreads, syncHealthNow]);
  useEffect(() => { refreshLive(); }, [refreshLive]);

  // 点了推送通知：先把对话记录重读一遍，再跳到那个对话。
  useEffect(() => onNotificationOpen((thread) => {
    loadThreads().catch(() => {});
    openThread(thread, latest.current.groups.some((g) => g.id === thread));
  }), [loadThreads]);

  // 回到前台：对话记录按服务器的为准重读一遍（切后台时断掉的回复会补回来），进行中的接上。
  const hidden = useRef<number | null>(null);
  useEffect(() => {
    const sub = AppState.addEventListener('change', (next) => {
      if (next !== 'active') { if (hidden.current == null) hidden.current = Date.now(); return; }
      const away = hidden.current ? Date.now() - hidden.current : 0;
      hidden.current = null;
      if (away > 3000 && latest.current.connected) { loadThreads().catch(() => {}); reload('feed', 'journal').catch(() => {}); }
    });
    return () => sub.remove();
  }, [loadThreads, reload]);

  const send = useCallback((threadId: string, text: string, files?: PendingFile[]) => {
    const pending = files?.map((f, i) => ({ id: `local${i}`, name: f.name, mime: f.mime, size: f.size, kind: kindOf(f.name, f.mime), url: f.uri }));
    // '（见附件）' 是占位标记，和服务端 chat.py 一致，ChatView 按原文比较后隐藏：不翻译。
    const mine: Message = { id: id('u'), role: 'user', time: timeNow(), body: { type: 'text', text: text || '（见附件）', attachments: pending } };
    const modelId = latest.current.threadModel[threadId] ?? latest.current.threadModel.main;
    setS((st) => ({ ...appendMsg(st, threadId, mine), typing: { ...st.typing, [threadId]: true }, sideChats: st.sideChats.map((c) => (c.id === threadId ? { ...c, updatedAt: Date.now() } : c)) }));
    const swapId = (userId: string) => setS((st) => ({ ...st, threads: { ...st.threads, [threadId]: (st.threads[threadId] ?? []).map((m) => (m.id === mine.id ? { ...m, id: userId } : m)) } }));
    api.current.send(threadId, text, modelId, (partial) => setS((st) => ({ ...st, streaming: { ...st.streaming, [threadId]: partial } })), swapId, files)
      .catch((e: unknown): Message => ({ id: id('r'), role: 'grava', time: timeNow(), modelId, body: { type: 'text', text: L('（这条没发出去。）', "(This message wasn't sent.)") }, error: errText(e) }))
      .then((reply) => { reload('feed'); return reply; })  // Grava 可能刚写了一张建议卡
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
  }, [reload]);

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
    refreshThread: async (threadId) => {
      const h = await api.current.history(threadId);
      if (!h) return;
      setS((st) => ({ ...st, threads: { ...st.threads, [threadId]: h.messages }, threadModel: h.modelId ? { ...st.threadModel, [threadId]: h.modelId } : st.threadModel }));
      if (h.inFlight && !latest.current.typing[threadId]) {
        setS((st) => ({ ...st, typing: { ...st.typing, [threadId]: true }, streaming: { ...st.streaming, [threadId]: h.inFlight?.text ?? '' } }));
        api.current.attach(threadId, (partial) => setS((st) => ({ ...st, streaming: { ...st.streaming, [threadId]: partial } })))
          .then((reply) => setS((st) => { const streaming = { ...st.streaming }; delete streaming[threadId]; return reply ? { ...appendMsg(st, threadId, reply), typing: { ...st.typing, [threadId]: false }, streaming } : { ...st, typing: { ...st.typing, [threadId]: false }, streaming }; }))
          .catch(() => setS((st) => ({ ...st, typing: { ...st.typing, [threadId]: false } })));
      }
    },
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
    decide: async (approvalId, allow) => {
      await dataApi.decide(approvalId, allow);
      await reload('approvals', 'activity');
    },
    dismissFeed: async (fid) => {
      setS((st) => ({ ...st, feed: st.feed.filter((f) => f.id !== fid) }));
      await dataApi.dismissFeed(fid);
    },
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
      const gid = await dataApi.createGroup({ name: g.name, purpose: g.purpose, icon: g.icon, model: g.modelId });
      setS((st) => ({ ...st, threads: { ...st.threads, [gid]: [] }, threadModel: { ...st.threadModel, [gid]: g.modelId } }));
      await reload('groups', 'activity');
      return gid;
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
  }), [refreshLive, reload, syncHealthNow, send]);

  const value = useMemo(() => ({ ...s, ...actions }), [s, actions]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useStore() {
  const v = useContext(Ctx);
  if (!v) throw new Error('StoreProvider missing');
  return v;
}
