// 思考空间的数据和动作（想法、主题、收藏、冥想时间）。对话本身（聊聊的那个线程）还是走 store 的 send / refreshThread。
import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { AppState } from 'react-native';
import * as api from '../api/think';
import { syncLive } from '../api/native';
import { supports } from '../api/version';
import type { PendingFile } from '../data/types';
import { useStore } from '../store';
import { setMeditating } from './focus';

interface State {
  stream: api.Stream | null;
  streamError: string | null;
  /** Zen 首页（想法一边，supports('think.home')）：在想 + 记下的 + 已想完几条 */
  home: api.ThinkHome | null;
  homeError: string | null;
  /** 播客里等你处理的有几期（「播客」旁的琥珀点） */
  podWaiting: number;
  saves: api.SaveItem[];
  savesNew: number;
  savesError: string | null;
  /** 用过的关键词（打关键词时补全、搜索页的关键词云） */
  keywords: api.KeywordStat[];
  /** 正在冥想（服务器记的，到点自己结束） */
  focus: api.Focus | null;
  /** 刚结束、还没看过小结的那一次 */
  unseenFocus: api.Focus | null;
}

interface Actions {
  refresh(): Promise<void>;
  refreshHome(): Promise<void>;
  setPodWaiting(n: number): void;
  /** 完成几条（undo = 恢复）：先从「记下的」里拿掉，失败了再读一次 */
  finish(ids: string[], undo?: boolean): Promise<void>;
  refreshSaves(filter?: string): Promise<void>;
  refreshKeywords(): Promise<void>;
  capture(b: { kind?: api.FragmentKind; text?: string; title?: string; keywords?: string[]; url?: string; topic?: string }): Promise<api.Fragment>;
  captureFiles(files: PendingFile[], opts: { text?: string; kind?: api.FragmentKind; title?: string; keywords?: string[]; duration?: number }): Promise<api.Fragment>;
  editFragment(id: string, b: { text?: string; title?: string; keywords?: string[] }): Promise<api.Fragment>;
  removeFragment(id: string): Promise<void>;
  /** 勾的几条开一个主题（聊聊 / 想完了都先开主题） */
  openTopic(ids: string[]): Promise<api.Topic>;
  addSave(b: { url?: string; text?: string; note?: string; keywords?: string[]; source?: string }): Promise<api.SaveItem>;
  addSaveFiles(files: PendingFile[], opts: { note?: string; source?: string; keywords?: string[] }): Promise<api.SaveItem[]>;
  saveMessage(thread: string, msgId: string): Promise<api.SaveItem>;
  updateSave(id: string, b: { title?: string; note?: string; keywords?: string[]; seen?: boolean }): Promise<api.SaveItem>;
  removeSave(id: string): Promise<void>;
  startFocus(minutes: number): Promise<api.Focus>;
  endFocus(b: { words?: number; notes?: number }): Promise<api.FocusSummary>;
  loadFocus(): Promise<void>;
}

const Ctx = createContext<(State & Actions) | null>(null);
const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

// Zen 首页「记下的」本地跟着改（服务器下次读的时候再对齐）
export const itemId = (x: api.DayItem) => (x.type === 'idea' ? x.idea.id : x.save.id);
const withHome = (cur: State, f: (h: api.ThinkHome) => api.ThinkHome): State => (cur.home ? { ...cur, home: f(cur.home) } : cur);
const addIdea = (h: api.ThinkHome, f: api.Fragment): api.ThinkHome => ({ ...h, items: [{ type: 'idea', at: f.createdAt, idea: f }, ...h.items], count: h.count + 1 });
const addSaves = (h: api.ThinkHome, list: api.SaveItem[]): api.ThinkHome =>
  ({ ...h, items: [...list.map((sv) => ({ type: 'save' as const, at: sv.createdAt, save: sv })), ...h.items], count: h.count + list.length });
const dropItem = (h: api.ThinkHome, id: string): api.ThinkHome => {
  const items = h.items.filter((x) => itemId(x) !== id);
  return { ...h, items, count: Math.max(0, h.count - (h.items.length - items.length)) };
};

export function ThinkProvider({ children }: { children: React.ReactNode }) {
  const { connected, reload } = useStore();
  const [s, setS] = useState<State>({ stream: null, streamError: null, home: null, homeError: null, podWaiting: 0, saves: [], savesNew: 0, savesError: null, keywords: [], focus: null, unseenFocus: null });
  const filterRef = useRef('all');

  const refresh = useCallback(async () => {
    try {
      const st = await api.stream();
      setS((cur) => ({ ...cur, stream: st, streamError: null, savesNew: st.savesNew }));
    } catch (e) {
      setS((cur) => ({ ...cur, streamError: errText(e) }));
    }
  }, []);
  const refreshHome = useCallback(async () => {
    if (!supports('think.home')) return;  // 老服务器：Zen 用原来的三栏（ThinkLegacy）
    try {
      const h = await api.home();
      setS((cur) => ({ ...cur, home: h, homeError: null, savesNew: h.savesNew }));
    } catch (e) {
      setS((cur) => ({ ...cur, homeError: errText(e) }));
    }
  }, []);
  const refreshSaves = useCallback(async (filter?: string) => {
    if (filter) filterRef.current = filter;
    try {
      const r = await api.saves(filterRef.current);
      setS((cur) => ({ ...cur, saves: r.saves, savesNew: r.new, savesError: null }));
    } catch (e) {
      setS((cur) => ({ ...cur, savesError: errText(e) }));
    }
  }, []);
  const refreshKeywords = useCallback(async () => {
    const k = await api.keywords().catch(() => null);
    if (k) setS((cur) => ({ ...cur, keywords: k }));
  }, []);
  const loadFocus = useCallback(async () => {
    const f = await api.focus().catch(() => null);
    if (!f) return;
    setMeditating(!!f.active);
    setS((cur) => ({ ...cur, focus: f.active, unseenFocus: f.unseen }));
  }, []);

  useEffect(() => {
    if (!connected) return;
    refresh();
    refreshHome();
    refreshSaves();
    refreshKeywords();
    loadFocus();
  }, [connected, refresh, refreshHome, refreshSaves, refreshKeywords, loadFocus]);

  // 回到前台：冥想可能到点结束了、手机上别处（Obsidian）记了新的
  useEffect(() => {
    const sub = AppState.addEventListener('change', (next) => {
      if (next === 'active' && connected) { loadFocus(); refresh(); refreshHome(); }
    });
    return () => sub.remove();
  }, [connected, loadFocus, refresh, refreshHome]);

  // 冥想中：到点那一刻自己收起（服务器也按到点算，这里只是让界面及时知道）
  const endsAt = s.focus?.endsAt;
  useEffect(() => {
    if (!endsAt) return undefined;
    const ms = Date.parse(endsAt) - Date.now();
    const h = setTimeout(() => { loadFocus(); }, Math.max(1000, ms + 1500));
    return () => clearTimeout(h);
  }, [endsAt, loadFocus]);

  const actions: Actions = useMemo(() => ({
    refresh, refreshHome, refreshSaves, refreshKeywords, loadFocus,
    setPodWaiting: (n) => setS((cur) => (cur.podWaiting === n ? cur : { ...cur, podWaiting: n })),
    finish: async (ids, undo) => {
      if (!undo) setS((cur) => withHome(cur, (h) => ({ ...h, items: h.items.filter((x) => !ids.includes(itemId(x))), count: Math.max(0, h.count - ids.length), finished: h.finished + ids.length })));
      try { await api.markDone(ids, !!undo); } finally { refreshHome(); }
    },
    capture: async (b) => {
      const f = await api.addFragment(b);
      if (!b.topic) setS((cur) => withHome(cur.stream ? { ...cur, stream: { ...cur.stream, fragments: [f, ...cur.stream.fragments] } } : cur, (h) => addIdea(h, f)));
      if (f.keywords.length) refreshKeywords();
      return f;
    },
    captureFiles: async (files, opts) => {
      const f = await api.uploadFragment(files, opts);
      setS((cur) => withHome(cur.stream ? { ...cur, stream: { ...cur.stream, fragments: [f, ...cur.stream.fragments] } } : cur, (h) => addIdea(h, f)));
      return f;
    },
    editFragment: async (id, b) => {
      const f = await api.patchFragment(id, b);
      setS((cur) => withHome(cur.stream ? { ...cur, stream: { ...cur.stream, fragments: cur.stream.fragments.map((x) => (x.id === id ? f : x)) } } : cur,
        (h) => ({ ...h, items: h.items.map((x) => (x.type === 'idea' && x.idea.id === id ? { ...x, idea: f } : x)) })));
      refreshKeywords();
      return f;
    },
    removeFragment: async (id) => {
      await api.deleteFragment(id);
      setS((cur) => withHome(cur.stream ? { ...cur, stream: { ...cur.stream, fragments: cur.stream.fragments.filter((x) => x.id !== id) } } : cur, (h) => dropItem(h, id)));
    },
    openTopic: async (ids) => {
      const t = await api.createTopic(ids);
      refresh();
      return t;
    },
    addSave: async (b) => {
      const sv = await api.addSave(b);
      setS((cur) => withHome({ ...cur, saves: [sv, ...cur.saves], savesNew: cur.savesNew + 1 }, (h) => addSaves(h, [sv])));
      return sv;
    },
    addSaveFiles: async (files, opts) => {
      const list = await api.uploadSaves(files, opts);
      setS((cur) => withHome({ ...cur, saves: [...list, ...cur.saves], savesNew: cur.savesNew + list.length }, (h) => addSaves(h, list)));
      return list;
    },
    saveMessage: async (thread, msgId) => {
      const sv = await api.saveMessage(thread, msgId);
      setS((cur) => withHome({ ...cur, saves: [sv, ...cur.saves], savesNew: cur.savesNew + 1 }, (h) => addSaves(h, [sv])));
      return sv;
    },
    updateSave: async (id, b) => {
      const sv = await api.patchSave(id, b);
      setS((cur) => withHome({ ...cur, saves: cur.saves.map((x) => (x.id === id ? sv : x)), savesNew: b.seen && !cur.saves.find((x) => x.id === id)?.seen ? Math.max(0, cur.savesNew - 1) : cur.savesNew },
        (h) => ({ ...h, items: h.items.map((x) => (x.type === 'save' && x.save.id === id ? { ...x, save: sv } : x)) })));
      if (b.keywords || b.note) refreshKeywords();
      return sv;
    },
    removeSave: async (id) => {
      await api.deleteSave(id);
      setS((cur) => withHome({ ...cur, saves: cur.saves.filter((x) => x.id !== id) }, (h) => dropItem(h, id)));
    },
    startFocus: async (minutes) => {
      const f = await api.focusStart(minutes);
      setMeditating(true);
      setS((cur) => ({ ...cur, focus: f, unseenFocus: null }));
      syncLive().catch(() => {});  // 锁屏和灵动岛上的倒计时（服务器的 /api/live 里有这次冥想）
      return f;
    },
    endFocus: async (b) => {
      const sum = await api.focusEnd(b);
      setMeditating(false);
      setS((cur) => ({ ...cur, focus: null, unseenFocus: null }));
      syncLive().catch(() => {});
      reload('unread', 'inbox', 'feed').catch(() => {});
      return sum;
    },
  }), [refresh, refreshHome, refreshSaves, refreshKeywords, loadFocus, reload]);

  const value = useMemo(() => ({ ...s, ...actions }), [s, actions]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useThink() {
  const v = useContext(Ctx);
  if (!v) throw new Error('ThinkProvider missing');
  return v;
}
