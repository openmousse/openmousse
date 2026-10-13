// 新产出的浮动标签（2026-10-13）：标签栏上方，像迷你播放条。Agent 头像 + 产出名 + 一句结论 + 小图；
// 多份时右边是 +N（点一下换下一份），左右滑也换，下滑关掉（全部标成看过）。点开是小板子（Board.tsx）。
// 放在标签栏的位置里（OutputsTabBar），不盖在页面上：对话页的输入栏、Zen 的记录栏都在它上面，不会被挡住。
import React, { useEffect, useState } from 'react';
import { Animated, AppState, PanResponder, Platform, Pressable, StyleSheet, View, type AccessibilityActionEvent } from 'react-native';
import { BottomTabBar, type BottomTabBarProps } from '@react-navigation/bottom-tabs';
import { currentOutputs, dropOutputs, refreshOutputs, resetOutputs, useFreshOutputs, type Output } from '../../api/outputs';
import { L } from '../../i18n';
import { useStore } from '../../store';
import { useTheme } from '../../theme';
import { T } from '../ui';
import { OutputAvatar, useOutputBoard } from './Board';
import { Donut, PairChart, Sparkline } from './vizlite';

const HEIGHT = 66;
const SPACE = HEIGHT + 10;  // 标签加上它和标签栏之间的空
const SLIDE = 480;  // 左右换一份时滑出去的距离（比手机屏宽一点就行）
// 网页上用鼠标拖：按下和松开都在标签里，浏览器还会补一个 click，拖完别再当成点开（手机上手势接管后不会有这一下）
let pannedAt = 0;

/** 标签栏：上面一条浮动标签（有新产出时），下面是原来的标签栏。 */
export function OutputsTabBar(props: BottomTabBarProps) {
  return (
    <View>
      <FloatTab />
      <BottomTabBar {...props} />
    </View>
  );
}

function Spark({ o, surface }: { o: Output; surface: string }) {
  const s = o.spark;
  if (!s) return null;
  if (s.type === 'pair' && s.values.length === 2) return <PairChart compact values={[s.values[0], s.values[1]]} width={48} height={30} surface={surface} label={s.a11y} />;
  if (s.type === 'ring' && s.values.length) return <Donut size={30} thickness={6} surface={surface} label={s.a11y} data={s.values.map((v, i) => ({ key: String(i), label: '', value: v, slot: i }))} />;
  if (s.type === 'trend' && s.values.length >= 2) return <Sparkline values={s.values} width={48} height={30} surface={surface} label={s.a11y} />;
  return null;
}

export function FloatTab() {
  const t = useTheme();
  const items = useFreshOutputs();
  const { connected, unread, markFeedSeen } = useStore();
  const open = useOutputBoard();
  const feedKey = unread.feedNew.join(',');
  // 连上了、新卡片变了（未读轮询、推送之后 store 会重读未读）：重读新产出；断开或换了服务器：清空
  useEffect(() => {
    if (!connected) { resetOutputs(); return; }
    refreshOutputs().catch(() => {});
  }, [connected, feedKey]);
  // 回到前台也读一次：过了 04:00（新的一天）昨天的就该收起来，这时未读不一定变
  useEffect(() => {
    if (!connected) return undefined;
    const sub = AppState.addEventListener('change', (st) => { if (st === 'active') refreshOutputs().catch(() => {}); });
    return () => sub.remove();
  }, [connected]);

  // 新的一份到了就显示最新那份（列表变了才换，左右滑过的不被轮询打断）
  const key = items.map((o) => o.id).join(',');
  const [seenKey, setSeenKey] = useState(key);
  const [index, setIndex] = useState(0);
  if (key !== seenKey) { setSeenKey(key); setIndex(0); }
  const at = Math.min(index, Math.max(items.length - 1, 0));
  const cur = items[at];

  const [show] = useState(() => new Animated.Value(0));   // 0 = 收起（高度 0），1 = 出来
  const [drag] = useState(() => new Animated.ValueXY({ x: 0, y: 0 }));
  const visible = items.length > 0;
  useEffect(() => {
    Animated.timing(show, { toValue: visible ? 1 : 0, duration: visible ? 260 : 200, useNativeDriver: false }).start();
  }, [visible, show]);

  // 手势回调不在渲染里跑：列表从模块里现读（currentOutputs），位置用 setIndex 的函数形式，markFeedSeen 本身不变
  const step = (dir: 1 | -1) => {
    const n = currentOutputs().length;
    if (n < 2) { Animated.spring(drag, { toValue: { x: 0, y: 0 }, useNativeDriver: false }).start(); return; }
    Animated.timing(drag.x, { toValue: -dir * SLIDE, duration: 140, useNativeDriver: false }).start(() => {
      setIndex((i) => (Math.min(i, n - 1) + dir + n) % n);
      drag.x.setValue(dir * SLIDE);
      Animated.spring(drag.x, { toValue: 0, useNativeDriver: false, bounciness: 4 }).start();
    });
  };
  const dismiss = () => {
    const ids = currentOutputs().map((o) => o.id);
    Animated.parallel([
      Animated.timing(drag.y, { toValue: SPACE, duration: 180, useNativeDriver: false }),
      Animated.timing(show, { toValue: 0, duration: 220, useNativeDriver: false }),
    ]).start(() => {
      markFeedSeen(ids);
      dropOutputs(ids);
      drag.setValue({ x: 0, y: 0 });
    });
  };
  const [pan] = useState(() => PanResponder.create({
    onMoveShouldSetPanResponder: (_e, g) => Math.abs(g.dx) > 10 || g.dy > 10,
    onPanResponderGrant: () => { pannedAt = Date.now(); },
    onPanResponderMove: (_e, g) => {
      if (Math.abs(g.dx) > Math.abs(g.dy)) drag.setValue({ x: g.dx, y: 0 });
      else drag.setValue({ x: 0, y: Math.max(0, g.dy) });
    },
    onPanResponderRelease: (_e, g) => {
      pannedAt = Date.now();
      if (g.dy > 36 && g.dy > Math.abs(g.dx)) dismiss();
      else if (Math.abs(g.dx) > 56 && Math.abs(g.dx) > g.dy) step(g.dx < 0 ? 1 : -1);
      else Animated.spring(drag, { toValue: { x: 0, y: 0 }, useNativeDriver: false, bounciness: 6 }).start();
    },
    onPanResponderTerminate: () => Animated.spring(drag, { toValue: { x: 0, y: 0 }, useNativeDriver: false }).start(),
  }));

  if (!cur) return <Animated.View style={{ height: show.interpolate({ inputRange: [0, 1], outputRange: [0, SPACE] }) }} />;
  const bg = t.mode === 'dark' ? t.surface2 : t.surface;
  const more = items.length - 1;
  const onAction = (e: AccessibilityActionEvent) => {
    if (e.nativeEvent.actionName === 'activate') open(cur);
    else if (e.nativeEvent.actionName === 'dismiss') dismiss();
    else if (e.nativeEvent.actionName === 'next') step(1);
  };
  return (
    <Animated.View style={{ height: show.interpolate({ inputRange: [0, 1], outputRange: [0, SPACE] }), overflow: 'visible' }}>
      <Animated.View {...pan.panHandlers}
        style={[styles.wrap, { opacity: Animated.multiply(show, drag.y.interpolate({ inputRange: [0, SPACE], outputRange: [1, 0], extrapolate: 'clamp' })),
          transform: [{ translateX: drag.x }, { translateY: Animated.add(drag.y, show.interpolate({ inputRange: [0, 1], outputRange: [24, 0] })) }] }]}>
        <Pressable onPress={() => { if (Date.now() - pannedAt > 350) open(cur); }} accessibilityRole="button" accessibilityLabel={`${cur.label}${L('：', ': ')}${cur.line}`}
          accessibilityHint={L('打开小板子；下滑关闭', 'Opens the report; swipe down to dismiss')}
          accessibilityActions={[{ name: 'activate' }, { name: 'dismiss', label: L('关闭', 'Dismiss') }, ...(more ? [{ name: 'next', label: L('下一份', 'Next') }] : [])]}
          onAccessibilityAction={onAction}
          style={({ pressed }) => [styles.card, { backgroundColor: bg, borderColor: t.line, shadowOpacity: t.mode === 'dark' ? 0.55 : 0.14, opacity: pressed ? 0.85 : 1 }]}>
          <View style={styles.avatar}>
            <OutputAvatar agent={cur.agent} size={42} />
            <View style={[styles.newDot, { backgroundColor: t.cyan, borderColor: bg }]} />
          </View>
          <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
            <T v="headline" style={{ fontSize: 15, fontWeight: '700' }} numberOfLines={1}>{cur.label}</T>
            <T v="caption" color={t.ink2} style={{ fontWeight: '400' }} numberOfLines={1}>{cur.line}</T>
          </View>
          <Spark o={cur} surface={bg} />
          {more ? (
            <Pressable onPress={() => step(1)} hitSlop={6} accessibilityRole="button" accessibilityLabel={L(`另有 ${more} 份新产出，查看下一份`, `${more} more; show the next one`)}
              style={({ pressed }) => [styles.more, { backgroundColor: t.mode === 'dark' ? '#2A3038' : t.surface2, opacity: pressed ? 0.7 : 1 }]}>
              <T v="caption" style={{ fontSize: 12, fontWeight: '700' }}>{`+${more}`}</T>
            </Pressable>
          ) : null}
        </Pressable>
      </Animated.View>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  wrap: { paddingHorizontal: 12, paddingBottom: 10 },
  card: {
    height: HEIGHT, borderRadius: 20, borderWidth: 1, flexDirection: 'row', alignItems: 'center', gap: 12, paddingLeft: 10, paddingRight: 12,
    shadowColor: '#000', shadowRadius: 16, shadowOffset: { width: 0, height: 8 }, elevation: 8,
    ...(Platform.OS === 'web' ? { cursor: 'pointer' } : null),
  },
  avatar: { width: 42, height: 42 },
  newDot: { position: 'absolute', right: -1, top: -1, width: 11, height: 11, borderRadius: 6, borderWidth: 2 },
  more: { width: 30, height: 30, borderRadius: 15, alignItems: 'center', justifyContent: 'center' },
});
