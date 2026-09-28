// 思考空间的数据和动作（想法、主题、收藏、冥想时间）。对话本身（聊聊的那个线程）还是走 store 的 send / refreshThread。
import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { AppState } from 'react-native';
import * as api from '../api/think';
import { syncLive } from '../api/native';
import type { PendingFile } from '../data/types';
import { useStore } from '../store';
import { setMeditating } from './focus';

interface State {
  stream: api.Stream | null;
  streamError: string | null;
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

export function ThinkProvider({ children }: { children: React.ReactNode }) {
  const { connected, reload } = useStore();
  const [s, setS] = useState<State>({ stream: null, streamError: null, saves: [], savesNew: 0, savesError: null, keywords: [], focus: null, unseenFocus: null });
  const filterRef = useRef('all');

  const refresh = useCallback(async () => {
    try {
      const st = await api.stream();
      setS((cur) => ({ ...cur, stream: st, streamError: null, savesNew: st.savesNew }));
    } catch (e) {
      setS((cur) => ({ ...cur, streamError: errText(e) }));
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
    refreshSaves();
    refreshKeywords();
    loadFocus();
  }, [connected, refresh, refreshSaves, refreshKeywords, loadFocus]);

  // 回到前台：冥想可能到点结束了、手机上别处（Obsidian）记了新的
  useEffect(() => {
    const sub = AppState.addEventListener('change', (next) => {
      if (next === 'active' && connected) { loadFocus(); refresh(); }
    });
    return () => sub.remove();
  }, [connected, loadFocus, refresh]);

  // 冥想中：到点那一刻自己收起（服务器也按到点算，这里只是让界面及时知道）
  const endsAt = s.focus?.endsAt;
  useEffect(() => {
    if (!endsAt) return undefined;
    const ms = Date.parse(endsAt) - Date.now();
    const h = setTimeout(() => { loadFocus(); }, Math.max(1000, ms + 1500));
    return () => clearTimeout(h);
  }, [endsAt, loadFocus]);

  const actions: Actions = useMemo(() => ({
    refresh, refreshSaves, refreshKeywords, loadFocus,
    capture: async (b) => {
      const f = await api.addFragment(b);
      if (!b.topic) setS((cur) => (cur.stream ? { ...cur, stream: { ...cur.stream, fragments: [f, ...cur.stream.fragments] } } : cur));
      if (f.keywords.length) refreshKeywords();
      return f;
    },
    captureFiles: async (files, opts) => {
      const f = await api.uploadFragment(files, opts);
      setS((cur) => (cur.stream ? { ...cur, stream: { ...cur.stream, fragments: [f, ...cur.stream.fragments] } } : cur));
      return f;
    },
    editFragment: async (id, b) => {
      const f = await api.patchFragment(id, b);
      setS((cur) => (cur.stream ? { ...cur, stream: { ...cur.stream, fragments: cur.stream.fragments.map((x) => (x.id === id ? f : x)) } } : cur));
      refreshKeywords();
      return f;
    },
    removeFragment: async (id) => {
      await api.deleteFragment(id);
      setS((cur) => (cur.stream ? { ...cur, stream: { ...cur.stream, fragments: cur.stream.fragments.filter((x) => x.id !== id) } } : cur));
    },
    openTopic: async (ids) => {
      const t = await api.createTopic(ids);
      refresh();
      return t;
    },
    addSave: async (b) => {
      const sv = await api.addSave(b);
      setS((cur) => ({ ...cur, saves: [sv, ...cur.saves], savesNew: cur.savesNew + 1 }));
      return sv;
    },
    addSaveFiles: async (files, opts) => {
      const list = await api.uploadSaves(files, opts);
      setS((cur) => ({ ...cur, saves: [...list, ...cur.saves], savesNew: cur.savesNew + list.length }));
      return list;
    },
    saveMessage: async (thread, msgId) => {
      const sv = await api.saveMessage(thread, msgId);
      setS((cur) => ({ ...cur, saves: [sv, ...cur.saves], savesNew: cur.savesNew + 1 }));
      return sv;
    },
    updateSave: async (id, b) => {
      const sv = await api.patchSave(id, b);
      setS((cur) => ({ ...cur, saves: cur.saves.map((x) => (x.id === id ? sv : x)), savesNew: b.seen && !cur.saves.find((x) => x.id === id)?.seen ? Math.max(0, cur.savesNew - 1) : cur.savesNew }));
      if (b.keywords || b.note) refreshKeywords();
      return sv;
    },
    removeSave: async (id) => {
      await api.deleteSave(id);
      setS((cur) => ({ ...cur, saves: cur.saves.filter((x) => x.id !== id) }));
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
  }), [refresh, refreshSaves, refreshKeywords, loadFocus, reload]);

  const value = useMemo(() => ({ ...s, ...actions }), [s, actions]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useThink() {
  const v = useContext(Ctx);
  if (!v) throw new Error('ThinkProvider missing');
  return v;
}
