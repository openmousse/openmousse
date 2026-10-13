// 全屏看板（集合块的看板视图，2026-10-13）：每列竖着滚，长按卡片拖到别的列 = 改这张卡的分列字段（onMove）。
// 缩略的看板（看板页上、素材库里）用 Kanban：没有拖拽，长按「移到…」。这里是给整页用的：列宽 220–280、字大一号，
// 拖动用 RN 自带的 PanResponder（安装包里没有 gesture-handler）：长按以后关掉横竖两层滚动，外层接管手指，
// 按手指的横坐标和横向滚动的位置算落在哪一列；靠近左右边缘自动横滚（滚到头就停），高亮跟着变；松手落在别的列才调 onMove。
// 放不进去的列（drop false，比如「其他」）不能落。拖动中 onDragging(true)：调用方关掉返回手势这类会抢手指的东西。
import * as Haptics from 'expo-haptics';
import React, { useEffect, useRef, useState } from 'react';
import { Animated, PanResponder, Platform, Pressable, ScrollView, StyleSheet, Text, View, useWindowDimensions, type StyleProp, type ViewStyle } from 'react-native';
import { L } from '../../i18n';
import { radius, space, useTheme } from '../../theme';
import { cardToneColors, seriesColor, VizFrame, vizText, type VizStateProps } from './core';
import type { KanbanCard, KanbanCol } from './Kanban';

export interface BoardColumn extends KanbanCol {
  /** 能不能把卡片放进这一列（「其他」这种合并出来的列不能） */
  drop?: boolean;
  /** 还有几张没给（翻页没取到的） */
  more?: number;
}

export interface KanbanBoardProps extends VizStateProps {
  columns: BoardColumn[];
  onPressCard?: (card: KanbanCard, column: BoardColumn) => void;
  /** 卡片拖到了另一列（不给就不能拖） */
  onMove?: (card: KanbanCard, from: BoardColumn, to: BoardColumn) => void;
  onDragging?: (on: boolean) => void;
  style?: StyleProp<ViewStyle>;
}

const GAP = 10;

type Drag = { card: KanbanCard; from: BoardColumn };
type Bound = { cols: BoardColumn[]; W: number; onMove?: KanbanBoardProps['onMove']; onDragging?: (on: boolean) => void;
  setDrag: (d: Drag | null) => void; setHover: (key: string | null) => void; scrollTo: (x: number) => void };

/** 拖动的过程放在 React 状态外面（手指每动一下都会触发），只在拖起、换列、松手时通知界面。组件每次渲染后用 bind 交给它最新的东西。 */
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

  colAt(pageX: number): BoardColumn | null {
    if (!this.b) return null;
    const { cols, W } = this.b;
    const x = pageX - this.x + this.scrollX - space.lg;
    const i = Math.floor(x / (W + GAP));
    if (i < 0 || x - i * (W + GAP) > W) return null;
    return cols[i] ?? null;
  }

  begin(box: { x: number; y: number; w: number }, grab: { x: number; y: number }, page: { x: number; y: number }, d: Drag) {
    Object.assign(this, { x: box.x, y: box.y, w: box.w, grabX: grab.x, grabY: grab.y, lastX: page.x, hover: null, drag: d });
    this.ghost.setValue({ x: page.x - box.x - grab.x, y: page.y - box.y - grab.y });
    this.b?.setDrag(d);
    this.b?.onDragging?.(true);
    if (Platform.OS !== 'web') Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});
  }

  /** 手指下面是哪一列：换了就高亮新的那一列（自动横滚时手指没动，列也会换）。 */
  updateHover(pageX: number) {
    const c = this.colAt(pageX);
    const key = c && c.drop !== false ? c.key : null;
    if (key === this.hover) return;
    this.hover = key;
    this.b?.setHover(key);
    if (key != null && Platform.OS !== 'web') Haptics.selectionAsync().catch(() => {});
  }

  moveTo(pageX: number, pageY: number) {
    if (!this.drag) return;
    this.ghost.setValue({ x: pageX - this.x - this.grabX, y: pageY - this.y - this.grabY });
    this.lastX = pageX;
    this.updateHover(pageX);
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
    this.b?.onDragging?.(false);
    if (!d || pageX == null) return;
    const to = this.colAt(pageX);
    if (to && to.key !== d.from.key && to.drop !== false) this.b?.onMove?.(d.card, d.from, to);
  }
}

/** 一张卡：上面一行小字（sub）、标题、标签。 */
function CardFace({ card }: { card: KanbanCard }) {
  const t = useTheme();
  return (
    <View style={[styles.card, { backgroundColor: t.mode === 'dark' ? t.surface2 : t.surface }]}>
      {card.sub ? <Text numberOfLines={1} style={[vizText.axis, { fontSize: 12, color: t.ink2 }]}>{card.sub}</Text> : null}
      <Text numberOfLines={2} style={{ fontSize: 14, lineHeight: 19, fontWeight: '600', color: t.ink }}>{card.title}</Text>
      {card.tags?.length ? (
        <View style={styles.tags}>
          {card.tags.map((x, k) => {
            const [bg, fg] = cardToneColors(t, x.tone);
            return (
              <View key={k} style={[styles.tag, { backgroundColor: bg }]}>
                <Text numberOfLines={1} style={{ fontSize: 11, fontWeight: '600', color: fg }}>{x.text}</Text>
              </View>
            );
          })}
        </View>
      ) : null}
    </View>
  );
}

export function KanbanBoard({ columns, onPressCard, onMove, onDragging, style, ...state }: KanbanBoardProps) {
  const t = useTheme();
  const { width: screenW } = useWindowDimensions();
  const W = Math.min(280, Math.max(220, screenW - 110));  // 一屏露出下一列的一角，知道还能往右滑
  const wrap = useRef<View>(null);
  const scroller = useRef<ScrollView>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [dg] = useState(() => new Dragger());
  useEffect(() => {
    dg.bind({ cols: columns, W, onMove, onDragging, setDrag, setHover, scrollTo: (x) => scroller.current?.scrollTo({ x, animated: false }) });
  }, [dg, columns, W, onMove, onDragging]);
  useEffect(() => () => dg.stopAuto(), [dg]);
  const movable = !!onMove;
  const start = (card: KanbanCard, col: BoardColumn, e: { nativeEvent: { pageX: number; pageY: number; locationX: number; locationY: number } }) => {
    if (!movable) return;
    const { pageX, pageY, locationX, locationY } = e.nativeEvent;
    wrap.current?.measureInWindow((x, y, w) => dg.begin({ x, y, w }, { x: locationX, y: locationY }, { x: pageX, y: pageY }, { card, from: col }));
  };
  const colBg = t.mode === 'dark' ? t.surface : t.surface2;
  return (
    <VizFrame empty={!columns.length} state={state} group={false} style={[{ flex: 1 }, style]}>
      <View ref={wrap} style={{ flex: 1 }} {...dg.pan.panHandlers}>
        <ScrollView ref={scroller} horizontal scrollEnabled={!drag} showsHorizontalScrollIndicator={false} scrollEventThrottle={16}
          onScroll={(e) => dg.onScroll(e.nativeEvent.contentOffset.x)} onContentSizeChange={(w) => dg.onContent(w)}
          contentContainerStyle={{ paddingHorizontal: space.lg, gap: GAP, alignItems: 'flex-start' }} style={{ marginHorizontal: -space.lg }}>
          {columns.map((c, ci) => {
            const hot = !!drag && hover === c.key && c.key !== drag.from.key;
            const n = c.total ?? c.cards.length;
            return (
              <View key={c.key} style={[styles.col, { width: W, backgroundColor: colBg, borderColor: hot ? t.gold : 'transparent' }]}>
                <View style={styles.head} accessibilityRole="header" accessibilityLabel={`${c.title} ${n}`}>
                  <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: seriesColor(t, c.slot ?? ci) }} />
                  <Text numberOfLines={1} style={{ flex: 1, fontSize: 14, fontWeight: '700', color: hot ? t.gold : t.ink }}>{c.title}</Text>
                  <Text style={[vizText.axis, { fontSize: 13, color: t.ink3 }]}>{String(n)}</Text>
                </View>
                <ScrollView scrollEnabled={!drag} showsVerticalScrollIndicator={false} contentContainerStyle={{ gap: 8, paddingBottom: 4 }}>
                  {c.cards.map((k) => (
                    <Pressable key={k.key} onPress={onPressCard ? () => onPressCard(k, c) : undefined} onLongPress={movable ? (e) => start(k, c, e) : undefined}
                      delayLongPress={250} disabled={!onPressCard && !movable} accessibilityRole={onPressCard ? 'button' : 'text'}
                      accessibilityLabel={[k.sub, k.title, ...(k.tags ?? []).map((x) => x.text)].filter(Boolean).join(L('，', ', '))}
                      accessibilityHint={movable ? L('长按拖到其他列', 'Long-press and drag to another column') : undefined}
                      style={({ pressed }) => ({ opacity: drag?.card.key === k.key ? 0.35 : pressed ? 0.7 : 1 })}>
                      <CardFace card={k} />
                    </Pressable>
                  ))}
                  {!c.cards.length ? (
                    <Text style={[vizText.axis, { fontSize: 12, color: t.ink3, paddingHorizontal: 4, paddingVertical: space.md }]}>{movable && c.drop !== false ? L('拖到这里', 'Drop here') : L('暂无', 'None')}</Text>
                  ) : null}
                  {c.more ? <Text style={[vizText.axis, { fontSize: 12, color: t.ink3, paddingHorizontal: 4 }]}>{L(`另有 ${c.more} 张未显示`, `${c.more} more not shown`)}</Text> : null}
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
      </View>
    </VizFrame>
  );
}

const styles = StyleSheet.create({
  col: { borderRadius: radius.lg - 2, padding: 10, gap: 8, maxHeight: '100%', borderWidth: 1.5 },
  head: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 4, paddingTop: 2, paddingBottom: 4 },
  card: { borderRadius: radius.md, paddingHorizontal: 12, paddingVertical: 10, gap: 6 },
  tags: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  tag: { borderRadius: 6, paddingHorizontal: 6, paddingVertical: 2, maxWidth: '100%' },
  ghost: { position: 'absolute', left: 0, top: 0, shadowColor: '#000', shadowOpacity: 0.35, shadowRadius: 12, shadowOffset: { width: 0, height: 6 }, elevation: 8 },
});
