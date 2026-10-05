// 小窗：app 开着的时候来了值得打断你的事（要你点头、你发的消息回复好了……），不出系统通知，从顶上滑下来一张卡。
// 5 秒后自己收起（按住时暂停），点一下打开它指的地方，往上划收起。
// 同一个来源、同一个去处再来一条：替换原来那条，不重复排队。4 秒内来了两条以上不同的：合成一张「N 条新消息」，
// 点一下原地展开成列表（最多 4 行，每行点了去各自的地方），往上划全部收起。列表里的顺序：要你点头 > 回复 > 卡片。
// 正看着的那个对话不弹（store 发之前判断；弹出来之后你自己点进了那个对话，也会收起）。
// 只用 RN 自带的 Animated 和触摸响应（onResponder*）：reanimated、gesture-handler 这类原生模块不在已装的包里，热更新带不过去。
import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { Animated, Easing, Platform, Pressable, StyleSheet, Text, View, type GestureResponderEvent } from 'react-native';
import * as Haptics from 'expo-haptics';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import type { PushTarget } from '../data/types';
import { L } from '../i18n';
import { openTarget } from '../navigation';
import { useStore } from '../store';
import { useTheme } from '../theme';
import { SourceBadge, useSourceName, useSourceTint } from './SourceBadge';

export interface BannerSpec {
  /** 不给就按「来源 + 去处」算：同一个来源、同一个去处的新一条替换旧的 */
  key?: string;
  /** inbox / reply / card / done / report：合成列表时排序用（要你点头 > 回复 > 卡片 > 其他） */
  kind?: string;
  /** 第二行，粗体。可以空（回复只有正文） */
  title: string;
  /** 第一行名字后面的「· 说明」 */
  subtitle?: string;
  /** 第三行，最多两行 */
  body?: string;
  /** 'main'、Agent id 或项目 id：左边的图标、第一行的名字 */
  source?: string;
  /** 点开去哪；没有就只收起 */
  target?: PushTarget | null;
}
interface Entry extends BannerSpec { key: string; at: number }
interface BannerState { items: Entry[]; expanded: boolean; leaving: boolean }

const targetKey = (tg?: PushTarget | null) => (!tg ? '' : tg.type === 'thread' ? `thread:${tg.thread}` : tg.type === 'today' ? 'today'
  : tg.type === 'board' ? `board:${tg.agent}` : tg.type === 'study' ? `study:${tg.course}:${tg.page ?? tg.session ?? ''}` : `${tg.type}:${tg.id}`);
const kindOf = (e: BannerSpec) => e.kind || (e.target?.type === 'inbox' ? 'inbox' : e.target?.type === 'thread' ? 'reply' : e.target?.type === 'card' ? 'card' : 'other');
const RANK: Record<string, number> = { inbox: 0, reply: 1, card: 2 };
const byRank = (a: Entry, b: Entry) => (RANK[kindOf(a)] ?? 3) - (RANK[kindOf(b)] ?? 3) || b.at - a.at;

const MERGE_MS = 4000;
const SHOW_MS = 5000;
const EXPANDED_MS = 12000;
const MAX_ROWS = 4;
const native = Platform.OS !== 'web';

const Api = createContext<{ show: (b: BannerSpec) => void; hide: () => void }>({ show: () => {}, hide: () => {} });
const StateCtx = createContext<{ state: BannerState; expand: () => void; clear: () => void }>({ state: { items: [], expanded: false, leaving: false }, expand: () => {}, clear: () => {} });
export const useBanner = () => useContext(Api);

/** 放在 store 外面（store 要用 show）；画出来的部分是 BannerHost，放在 store 里面、和 UpdateBanner 挨着。 */
export function BannerProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<BannerState>({ items: [], expanded: false, leaving: false });
  const show = useCallback((b: BannerSpec) => setState((cur) => {
    const key = b.key || `${b.source ?? 'main'}|${targetKey(b.target)}`;
    const now = Date.now();
    const lastAt = cur.items.reduce((m, i) => Math.max(m, i.at), 0);
    // 4 秒内接着来的（或者你正展开看着列表）合在一起；隔得久了，新的一条单独显示
    const merge = !cur.leaving && cur.items.length > 0 && (cur.expanded || now - lastAt < MERGE_MS);
    const keep = merge ? cur.items.filter((i) => i.key !== key) : [];
    return { items: [{ ...b, key, at: now }, ...keep].slice(0, 12), expanded: merge && cur.expanded, leaving: false };
  }), []);
  const hide = useCallback(() => setState((cur) => (cur.items.length && !cur.leaving ? { ...cur, leaving: true } : cur)), []);
  const clear = useCallback(() => setState((cur) => (cur.leaving ? { items: [], expanded: false, leaving: false } : cur)), []);
  const expand = useCallback(() => setState((cur) => (cur.items.length > 1 && !cur.leaving ? { ...cur, expanded: true } : cur)), []);
  const api = useMemo(() => ({ show, hide }), [show, hide]);
  const ctx = useMemo(() => ({ state, expand, clear }), [state, expand, clear]);
  return <Api.Provider value={api}><StateCtx.Provider value={ctx}>{children}</StateCtx.Provider></Api.Provider>;
}

function Row({ e, onPress }: { e: Entry; onPress: () => void }) {
  const t = useTheme();
  const nameOf = useSourceName();
  const time = new Date(e.at).toTimeString().slice(0, 5);
  return (
    <Pressable onPress={onPress} accessibilityRole="button" accessibilityLabel={`${nameOf(e.source)}${L('，', ', ')}${e.title || e.body || ''}`}
      style={({ pressed }) => [styles.listRow, { opacity: pressed ? 0.6 : 1 }]}>
      <SourceBadge source={e.source} size={24} />
      <Text numberOfLines={1} style={[styles.rowTitle, { color: t.ink }]}>{e.title || e.body || nameOf(e.source)}</Text>
      <Text style={[styles.meta, { color: t.ink3 }]}>{time}</Text>
    </Pressable>
  );
}

export function BannerHost() {
  const t = useTheme();
  const insets = useSafeAreaInsets();
  const { state, expand, clear } = useContext(StateCtx);
  const { hide } = useBanner();
  const { groups, activeThread } = useStore();
  const nameOf = useSourceName();
  const tintOf = useSourceTint();
  const { items, expanded, leaving } = state;
  const hiddenY = -(insets.top + 260);
  const [y] = useState(() => new Animated.Value(-500));
  const onScreen = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const deadline = useRef(0);

  const stop = useCallback(() => { if (timer.current) { clearTimeout(timer.current); timer.current = null; } }, []);
  const arm = useCallback((ms: number) => { stop(); deadline.current = Date.now() + ms; timer.current = setTimeout(hide, ms); }, [stop, hide]);
  useEffect(() => stop, [stop]);

  // 来了一条（或者替换了一条）：没在屏幕上就从上面弹下来；轻震一下；重新计时
  const newestAt = items[0]?.at ?? 0;
  useEffect(() => {
    if (!newestAt) return;
    if (!onScreen.current) { onScreen.current = true; y.setValue(hiddenY); }
    Animated.spring(y, { toValue: 0, friction: 8, tension: 70, useNativeDriver: native }).start();
    if (native) Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => {});
    arm(SHOW_MS);
  }, [newestAt, y, hiddenY, arm]);
  // 展开了：多给一会儿
  useEffect(() => { if (expanded) arm(EXPANDED_MS); }, [expanded, arm]);
  // 收起：往上滑走，走完再清空（中途又来了新的，动画被打断，不清）
  useEffect(() => {
    if (!leaving) return;
    stop();
    Animated.timing(y, { toValue: hiddenY, duration: 220, easing: Easing.in(Easing.quad), useNativeDriver: native })
      .start(({ finished }) => { if (finished) { onScreen.current = false; clear(); } });
  }, [leaving, y, hiddenY, stop, clear]);

  // 小窗在的时候你自己点进了那个对话：那一条对你没用了，只有它一条就收起
  const only = items.length === 1 ? items[0] : undefined;
  const onlyThread = only?.target && only.target.type !== 'today' ? only.target.thread : undefined;
  useEffect(() => { if (activeThread && onlyThread === activeThread) hide(); }, [activeThread, onlyThread, hide]);

  const go = (e: Entry) => {
    hide();
    const tg = e.target;
    if (!tg) return;
    const thread = tg.type === 'thread' ? tg.thread : undefined;
    openTarget(tg, !!thread && groups.some((g) => g.id === thread));
  };
  const tap = () => {
    if (!items.length) return;
    if (items.length === 1) go(items[0]);
    else if (!expanded) expand();
  };
  // 手势：按下暂停自动收起；几乎没动 = 点一下；往上划 = 全部收起；别的方向松手弹回去。
  // 用 View 自己的触摸响应（onResponder*）而不是 PanResponder：回调每次渲染重建，直接拿到最新的状态。
  const drag = useRef({ x0: 0, y0: 0, t0: 0 });
  const begin = (e: GestureResponderEvent) => { drag.current = { x0: e.nativeEvent.pageX, y0: e.nativeEvent.pageY, t0: e.nativeEvent.timestamp || Date.now() }; };
  const delta = (e: GestureResponderEvent) => ({ dx: e.nativeEvent.pageX - drag.current.x0, dy: e.nativeEvent.pageY - drag.current.y0 });
  const resume = () => arm(Math.max(deadline.current - Date.now(), 2000));
  const settle = () => { Animated.spring(y, { toValue: 0, friction: 8, useNativeDriver: native }).start(); resume(); };
  const gestures = {
    onStartShouldSetResponderCapture: (e: GestureResponderEvent) => { begin(e); return false; },
    onStartShouldSetResponder: () => true,
    // 往上下拖：从里面的行手里接过来（捕获阶段先问外层）
    onMoveShouldSetResponderCapture: (e: GestureResponderEvent) => { const { dx, dy } = delta(e); return Math.abs(dy) > 6 && Math.abs(dy) > Math.abs(dx); },
    onResponderGrant: () => stop(),
    onResponderMove: (e: GestureResponderEvent) => { const { dy } = delta(e); y.setValue(dy < 0 ? dy : dy * 0.25); },
    onResponderRelease: (e: GestureResponderEvent) => {
      const { dx, dy } = delta(e);
      const ms = Math.max(1, (e.nativeEvent.timestamp || Date.now()) - drag.current.t0);
      if (Math.abs(dx) < 8 && Math.abs(dy) < 8) { y.setValue(0); tap(); resume(); return; }
      if (dy < -24 || dy / ms < -0.3) { hide(); return; }
      settle();
    },
    onResponderTerminate: settle,
  };

  if (!items.length) return null;
  const n = items.length;
  const newest = items[0];
  const sorted = [...items].sort(byRank);
  const names = [...new Set(sorted.map((e) => nameOf(e.source)))].join(' · ');
  const single = n === 1;
  const cta = !single ? (expanded ? '' : L('展开', 'Show all'))
    : !newest.target ? '' : newest.target.type === 'card' ? L('查看卡片', 'View card') : newest.target.type === 'thread' ? L('查看回复', 'View reply') : newest.target.type === 'inbox' ? L('查看', 'View') : L('打开', 'Open');
  const opacity = y.interpolate({ inputRange: [hiddenY, -60, 0], outputRange: [0, 1, 1], extrapolate: 'clamp' });
  const label = single
    ? [nameOf(newest.source), newest.subtitle, newest.title, newest.body].filter(Boolean).join(L('，', ', '))
    : L(`${n} 条新消息，${names}`, `${n} new, ${names}`);
  return (
    <View pointerEvents="box-none" style={[styles.wrap, { top: insets.top + 8 }]}>
      <Animated.View {...gestures} style={[styles.slot, { opacity, transform: [{ translateY: y }] }]}
        accessible={!expanded} accessibilityRole="button" accessibilityLabel={label}
        accessibilityHint={single ? L('轻点打开，上滑关闭', 'Tap to open, swipe up to dismiss') : L('轻点展开，上滑全部关闭', 'Tap to show all, swipe up to dismiss')}
        accessibilityActions={[{ name: 'activate' }, { name: 'escape' }]}
        onAccessibilityAction={(e) => (e.nativeEvent.actionName === 'escape' ? hide() : tap())}>
        {!single && !expanded ? <View style={[styles.peek, { backgroundColor: t.surface, borderColor: t.line }]} /> : null}
        <View style={[styles.card, { backgroundColor: t.surface, borderColor: t.line }]}>
          <View style={styles.row}>
            <SourceBadge source={newest.source} size={42} />
            <View style={{ flex: 1, minWidth: 0, gap: 3 }}>
              <View style={styles.line1}>
                <Text numberOfLines={1} style={[styles.name, { color: single ? tintOf(newest.source).fg : t.cyan }]}>{single ? nameOf(newest.source) : names}</Text>
                {single && newest.subtitle ? <Text numberOfLines={1} style={[styles.meta, { color: t.ink3, flexShrink: 1 }]}>· {newest.subtitle}</Text> : null}
                <View style={{ flex: 1 }} />
                <Text style={[styles.meta, { color: t.ink3 }]}>{L('刚刚', 'now')}</Text>
              </View>
              {single ? (
                <>
                  {newest.title ? <Text numberOfLines={2} style={[styles.title, { color: t.ink }]}>{newest.title}</Text> : null}
                  {newest.body ? <Text numberOfLines={2} style={newest.title ? [styles.body, { color: t.ink2 }] : [styles.bodyOnly, { color: t.ink }]}>{newest.body}</Text> : null}
                </>
              ) : (
                <>
                  <Text numberOfLines={1} style={[styles.title, { color: t.ink }]}>{L(`${n} 条新消息`, `${n} new`)}</Text>
                  {!expanded ? <Text numberOfLines={2} style={[styles.body, { color: t.ink2 }]}>{newest.title || newest.body}</Text> : null}
                </>
              )}
            </View>
          </View>
          {!single && expanded ? (
            <View style={[styles.list, { borderTopColor: t.line }]}>
              {sorted.slice(0, MAX_ROWS).map((e) => <Row key={e.key} e={e} onPress={() => go(e)} />)}
              {n > MAX_ROWS ? <Text style={[styles.small, { color: t.ink3, paddingLeft: 34 }]}>{L(`另有 ${n - MAX_ROWS} 条`, `${n - MAX_ROWS} more`)}</Text> : null}
            </View>
          ) : null}
          <View style={styles.foot}>
            <View style={{ minWidth: 72 }} />
            <View style={[styles.grabber, { backgroundColor: t.line }]} />
            <Text style={[styles.small, { color: t.gold, fontWeight: '600', textAlign: 'right' }]}>{cta}</Text>
          </View>
        </View>
      </Animated.View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { position: 'absolute', left: 0, right: 0, alignItems: 'center', zIndex: 60, elevation: 60 },
  slot: { width: '100%', maxWidth: 540, paddingHorizontal: 10 },
  peek: { position: 'absolute', left: 22, right: 22, top: 10, bottom: -8, borderRadius: 20, borderWidth: StyleSheet.hairlineWidth, opacity: 0.85, shadowColor: '#000', shadowOpacity: 0.08, shadowRadius: 8, shadowOffset: { width: 0, height: 4 }, elevation: 6 },
  card: {
    borderRadius: 20, borderWidth: StyleSheet.hairlineWidth, paddingTop: 12, paddingHorizontal: 14, paddingBottom: 8, gap: 8,
    shadowColor: '#000', shadowOpacity: 0.2, shadowRadius: 16, shadowOffset: { width: 0, height: 8 }, elevation: 10,
  },
  row: { flexDirection: 'row', gap: 12, alignItems: 'flex-start' },
  line1: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  name: { fontSize: 13, fontWeight: '700', flexShrink: 1 },
  meta: { fontSize: 12, fontWeight: '500' },
  title: { fontSize: 15, fontWeight: '700', lineHeight: 20 },
  body: { fontSize: 13, lineHeight: 19 },
  bodyOnly: { fontSize: 14, lineHeight: 20 },
  list: { borderTopWidth: StyleSheet.hairlineWidth, paddingTop: 4, gap: 2 },
  listRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 7 },
  rowTitle: { flex: 1, fontSize: 14, fontWeight: '600' },
  foot: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  small: { fontSize: 11, minWidth: 72 },
  grabber: { width: 36, height: 4, borderRadius: 2 },
});
