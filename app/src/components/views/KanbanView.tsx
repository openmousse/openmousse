// 看板视图：按一个字段分列。看板页上是缩略的样子（每列前几张，横着滑），点卡片看详情、在详情里「移到」别的列；
// 全屏页里每列竖着滚，长按卡片拖到别的列 = 改这一行的 group 字段（PATCH /api/rows/{id}），松手先挪过去，写失败再挪回来。
// 拖动用 RN 自带的 PanResponder（安装包里没有 gesture-handler）：长按后关掉横竖两层滚动，外层接管手指，按手指的横坐标算落在哪一列，
// 靠近左右边缘自动横滚。
import * as Haptics from 'expo-haptics';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Animated, PanResponder, Platform, Pressable, ScrollView, StyleSheet, View, useWindowDimensions } from 'react-native';
import type { BoardColumn, BoardViewData, ViewCard } from '../../api/boards';
import { L } from '../../i18n';
import { radius, space, useTheme } from '../../theme';
import { T } from '../ui';
import { CardFace, CardItem } from './Card';
import type { ViewProps } from './env';
import { columnBg, seriesColor } from './palette';

const INLINE_CARDS = 3;
const GAP = 10;

/** 松手以后、服务器回来以前：卡片先放到目标列的最前面，计数跟着变。 */
function withPending(cols: BoardColumn[], pending: Record<string, string>): BoardColumn[] {
  const ids = Object.keys(pending);
  if (!ids.length) return cols;
  const moving = new Map<string, ViewCard>();
  for (const c of cols) for (const k of c.cards) if (pending[k.id] && pending[k.id] !== c.id) moving.set(k.id, k);
  return cols.map((c) => {
    const out = c.cards.filter((k) => !moving.has(k.id));
    const inc = [...moving.values()].filter((k) => pending[k.id] === c.id);
    return { ...c, cards: [...inc, ...out], count: c.count - (c.cards.length - out.length) + inc.length };
  });
}

function ColumnHead({ col, hot }: { col: BoardColumn; hot?: boolean }) {
  const t = useTheme();
  return (
    <View style={styles.head} accessibilityRole="header" accessibilityLabel={L(`${col.label}，${col.count} 条`, `${col.label}, ${col.count}`)}>
      <View style={[styles.dot, { backgroundColor: seriesColor(t, col.color) }]} />
      <T v="callout" style={{ flex: 1, fontSize: 14, fontWeight: '700', color: hot ? t.gold : t.ink }} numberOfLines={1}>{col.label}</T>
      <T v="callout" color={t.ink3} style={{ fontSize: 13, fontVariant: ['tabular-nums'] }}>{String(col.count)}</T>
    </View>
  );
}

export function KanbanView({ env, data }: ViewProps<BoardViewData>) {
  const [pending, setPending] = useState<Record<string, string>>({});
  const [seen, setSeen] = useState(data);
  if (seen !== data) {  // 新数据回来了：服务器那边已经是挪过以后的样子
    setSeen(data);
    setPending({});
  }
  const cols = useMemo(() => withPending(data.columns, pending), [data, pending]);
  const move = (card: ViewCard, from: string, to: BoardColumn) => {
    if (to.id === from || !to.drop) return;
    setPending((m) => ({ ...m, [card.id]: to.id }));
    env.moveCard(card, to).catch(() => setPending((m) => { const n = { ...m }; delete n[card.id]; return n; }));
  };
  const empty = !cols.some((c) => c.count > 0);
  return (
    <View style={{ gap: space.sm }}>
      {env.full ? <Full env={env} cols={cols} move={move} /> : <Inline env={env} cols={cols} />}
      {empty ? <EmptyNote /> : null}
      {data.hidden > 0 ? <HiddenNote n={data.hidden} /> : null}
    </View>
  );
}

function EmptyNote() {
  const t = useTheme();
  return <T v="callout" color={t.ink2} style={{ paddingHorizontal: 4 }}>{L('暂无数据', 'No data yet')}</T>;
}

function HiddenNote({ n }: { n: number }) {
  const t = useTheme();
  return <T v="caption" color={t.ink3} style={{ fontWeight: '400', paddingHorizontal: 4 }}>{L(`另有 ${n} 条不在以上各列`, `${n} more not in these columns`)}</T>;
}

/** 看板页上：横着滑的几列，每列前三张。 */
function Inline({ env, cols }: { env: ViewProps<BoardViewData>['env']; cols: BoardColumn[] }) {
  const t = useTheme();
  const W = 232;
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: GAP, paddingRight: space.lg }} style={{ marginHorizontal: -space.lg }}>
      <View style={{ width: space.lg - GAP }} />
      {cols.map((c) => (
        <View key={c.id || 'empty'} style={[styles.col, { width: W, backgroundColor: columnBg(t) }]}>
          <ColumnHead col={c} />
          {c.cards.slice(0, INLINE_CARDS).map((k) => (
            <CardItem key={k.id} card={k} onPress={env.readOnly ? undefined : () => env.openCard(k, c)} />
          ))}
          {c.count > INLINE_CARDS ? (
            <Pressable onPress={env.openFull} disabled={!env.openFull} accessibilityRole="button" style={({ pressed }) => [styles.moreRow, { opacity: pressed ? 0.6 : 1 }]}>
              <T v="callout" color={t.ink3} style={{ fontSize: 13 }}>{L(`另有 ${c.count - Math.min(INLINE_CARDS, c.cards.length)} 条`, `${c.count - Math.min(INLINE_CARDS, c.cards.length)} more`)}</T>
            </Pressable>
          ) : null}
        </View>
      ))}
    </ScrollView>
  );
}

type Drag = { card: ViewCard; from: string; dot: number | null };
type Bound = { cols: BoardColumn[]; W: number; move: (card: ViewCard, from: string, to: BoardColumn) => void; env: ViewProps<BoardViewData>['env'];
  setDrag: (d: Drag | null) => void; setHover: (id: string | null) => void; scrollTo: (x: number) => void };

/**
 * 拖卡片的过程（不放在 React 状态里：手指每动一下都会触发，只在「拖起 / 换列 / 松手」时通知界面）。
 * 组件每次渲染后用 bind 交给它最新的列、列宽、move、env；PanResponder 只建一次。
 */
class Dragger {
  x = 0; y = 0; w = 0; grabX = 0; grabY = 0; scrollX = 0; speed = 0; lastX = 0; contentW = 0;
  drag: Drag | null = null;
  hover: string | null = null;
  timer: ReturnType<typeof setInterval> | null = null;
  b: Bound | null = null;
  ghost = new Animated.ValueXY();
  pan = PanResponder.create({
    onStartShouldSetPanResponderCapture: () => false,
    onMoveShouldSetPanResponderCapture: () => this.drag != null,
    onMoveShouldSetPanResponder: () => this.drag != null,
    onPanResponderTerminationRequest: () => false,
    onPanResponderMove: (_e, g) => this.moveTo(g.moveX, g.moveY),
    onPanResponderRelease: (_e, g) => this.finish(g.moveX),
    onPanResponderTerminate: () => this.finish(null),
  });

  bind(b: Bound) { this.b = b; }
  onScroll(x: number) { this.scrollX = x; }
  onContent(w: number) { this.contentW = w; }

  /** 手指下面是哪一列：换了就高亮新的那一列（自动横滚时手指没动，列也会换）。 */
  updateHover(pageX: number) {
    const c = this.colAt(pageX);
    const id = c && c.drop ? c.id : null;
    if (id !== this.hover) {
      this.hover = id;
      this.b?.setHover(id);
      if (id != null && Platform.OS !== 'web') Haptics.selectionAsync().catch(() => {});
    }
  }

  colAt(pageX: number): BoardColumn | null {
    if (!this.b) return null;
    const { cols, W } = this.b;
    const x = pageX - this.x + this.scrollX - space.lg;
    const i = Math.floor(x / (W + GAP));
    if (i < 0 || x - i * (W + GAP) > W) return null;
    return cols[i] ?? null;
  }

  begin(box: { x: number; y: number; w: number }, grab: { x: number; y: number }, page: { x: number; y: number }, d: Drag) {
    Object.assign(this, { x: box.x, y: box.y, w: box.w, grabX: grab.x, grabY: grab.y, hover: null, drag: d });
    this.ghost.setValue({ x: page.x - box.x - grab.x, y: page.y - box.y - grab.y });
    this.b?.setDrag(d);
    this.b?.env.setDragging?.(true);
    if (Platform.OS !== 'web') Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});
  }

  moveTo(pageX: number, pageY: number) {
    if (!this.drag) return;
    this.ghost.setValue({ x: pageX - this.x - this.grabX, y: pageY - this.y - this.grabY });
    this.lastX = pageX;
    this.updateHover(pageX);
    // 靠近左右边缘：自动横滚（滚到头就停）
    const edge = 44;
    this.speed = pageX < this.x + edge ? -14 : pageX > this.x + this.w - edge ? 14 : 0;
    if (this.speed && !this.timer) {
      this.timer = setInterval(() => {
        if (!this.speed) return;
        const max = Math.max(0, this.contentW - (this.w + space.lg * 2));  // 横滚的那层左右各伸出 space.lg
        const next = Math.min(max, Math.max(0, this.scrollX + this.speed));
        if (next === this.scrollX) return;
        this.scrollX = next;
        this.b?.scrollTo(next);
        this.updateHover(this.lastX);
      }, 16);
    } else if (!this.speed) this.stopAuto();
  }

  stopAuto() {
    this.speed = 0;
    if (this.timer) { clearInterval(this.timer); this.timer = null; }
  }

  finish(pageX: number | null) {
    this.stopAuto();
    const d = this.drag;
    this.drag = null;
    this.hover = null;
    this.b?.setDrag(null);
    this.b?.setHover(null);
    this.b?.env.setDragging?.(false);
    if (!d || pageX == null) return;
    const to = this.colAt(pageX);
    if (to && to.id !== d.from && to.drop) this.b?.move(d.card, d.from, to);
  }
}

/** 全屏页：每列竖着滚；长按拖到别的列。 */
function Full({ env, cols, move }: { env: ViewProps<BoardViewData>['env']; cols: BoardColumn[]; move: (card: ViewCard, from: string, to: BoardColumn) => void }) {
  const t = useTheme();
  const { width: screenW } = useWindowDimensions();
  const W = Math.min(280, Math.max(220, screenW - 110));  // 一屏露出下一列的一角，知道还能往右滑
  const wrap = useRef<View>(null);
  const scroller = useRef<ScrollView>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [dg] = useState(() => new Dragger());
  useEffect(() => {
    dg.bind({ cols, W, move, env, setDrag, setHover, scrollTo: (x) => scroller.current?.scrollTo({ x, animated: false }) });
  }, [dg, cols, W, move, env]);
  useEffect(() => () => dg.stopAuto(), [dg]);
  const movable = env.block.data.readonly !== true && !env.readOnly;
  const start = (card: ViewCard, col: BoardColumn, e: { nativeEvent: { pageX: number; pageY: number; locationX: number; locationY: number } }) => {
    if (!movable) return;
    const { pageX, pageY, locationX, locationY } = e.nativeEvent;
    wrap.current?.measureInWindow((x, y, w) => dg.begin({ x, y, w }, { x: locationX, y: locationY }, { x: pageX, y: pageY }, { card, from: col.id, dot: col.color }));
  };
  return (
    <View ref={wrap} style={{ flex: 1 }} {...dg.pan.panHandlers}>
      <ScrollView ref={scroller} horizontal scrollEnabled={!drag} showsHorizontalScrollIndicator={false} scrollEventThrottle={16}
        onScroll={(e) => dg.onScroll(e.nativeEvent.contentOffset.x)} onContentSizeChange={(w) => dg.onContent(w)}
        contentContainerStyle={{ paddingHorizontal: space.lg, gap: GAP, alignItems: 'flex-start' }} style={{ marginHorizontal: -space.lg }}>
        {cols.map((c) => {
          const hot = !!drag && hover === c.id && c.id !== drag.from;
          return (
            <View key={c.id || 'empty'} style={[styles.col, styles.fullCol, { width: W, backgroundColor: columnBg(t), borderColor: hot ? t.gold : 'transparent' }]}>
              <ColumnHead col={c} hot={hot} />
              <ScrollView scrollEnabled={!drag} showsVerticalScrollIndicator={false} contentContainerStyle={{ gap: 8, paddingBottom: 4 }}>
                {c.cards.map((k) => (
                  <CardItem key={k.id} card={k} dim={drag?.card.id === k.id} onPress={env.readOnly ? undefined : () => env.openCard(k, c)}
                    onLongPress={movable ? (e) => start(k, c, e) : undefined} />
                ))}
                {!c.cards.length ? <T v="caption" color={t.ink3} style={{ fontWeight: '400', paddingHorizontal: 4, paddingVertical: space.md }}>{movable ? L('拖到这里', 'Drop here') : L('暂无', 'None')}</T> : null}
                {c.more > 0 ? <T v="caption" color={t.ink3} style={{ fontWeight: '400', paddingHorizontal: 4 }}>{L(`另有 ${c.more} 条未显示`, `${c.more} more not shown`)}</T> : null}
              </ScrollView>
            </View>
          );
        })}
      </ScrollView>
      {drag ? (
        <Animated.View pointerEvents="none" style={[styles.ghost, { width: W - 20, transform: [...dg.ghost.getTranslateTransform(), { rotate: '2deg' }] }]}>
          <CardFace card={drag.card} />
        </Animated.View>
      ) : null}
      {movable ? <T v="caption" color={t.ink3} style={{ fontWeight: '400', textAlign: 'center', marginTop: space.sm }}>{L('长按卡片拖到其他列，即更新该字段', 'Long-press a card and drag it to another column to change that field')}</T> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  col: { borderRadius: radius.lg - 2, padding: 10, gap: 8, alignSelf: 'flex-start' },
  fullCol: { maxHeight: '100%', borderWidth: 1.5 },
  head: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 4, paddingTop: 2, paddingBottom: 4 },
  dot: { width: 8, height: 8, borderRadius: 4 },
  moreRow: { paddingHorizontal: 4, paddingVertical: 4 },
  ghost: { position: 'absolute', left: 0, top: 0, shadowColor: '#000', shadowOpacity: 0.35, shadowRadius: 12, shadowOffset: { width: 0, height: 6 }, elevation: 8 },
});
