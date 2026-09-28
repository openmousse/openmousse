import React, { useCallback, useState } from 'react';
import { Dimensions, Platform, ScrollView, type ScrollViewProps, type View, type ViewProps } from 'react-native';
import { KeyboardChatScrollView, useReanimatedKeyboardAnimation } from 'react-native-keyboard-controller';
import Reanimated, { useAnimatedStyle } from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

/**
 * 贴着底部的那块（输入栏、底部弹层）底下要让出多少：键盘开着时正好让到键盘上沿，收起时让出 Home 指示条
 * （这块的底边不贴屏幕底，比如在 tab 栏上面，就是 0）。
 *
 * 键盘的位置来自 react-native-keyboard-controller（1.0.5 起的原生模块），在 UI 线程上一帧一帧地给，
 * 下拉收键盘（ScrollView 的 keyboardDismissMode="interactive"）时也跟着手指走；网页上没有键盘，一直是 0。
 *
 * 返回：
 * - style：动画的 paddingBottom，给 Reanimated.View 用（输入栏跟着键盘升降，内容区一起变矮）
 * - spacer：同样的高度做成一块垫片，给没法改 padding 的地方（弹层）
 * - home / offset：数字。home = 键盘收起时让出的 Home 指示条；offset = 这块底边到屏幕底的距离 + home，
 *   也就是键盘升起时不用再让的那一截（对话页的 KeyboardSticky / ChatScroll 用）
 * - onLayout：接到传进来的 ref 那个 View 上，量它在窗口里的位置
 *
 * 传 ref 就量这个 View；不传就当它的底边是屏幕底边（弹层）。原生 modal 页里别传 ref：那里量出来的坐标是相对 modal 的。
 * 不用 RN 的 KeyboardAvoidingView：它拿相对坐标去比键盘的屏幕坐标，得手填 keyboardVerticalOffset，填错了输入栏就悬在键盘上面一截。
 */
export function useBottomInset(ref?: React.RefObject<View | null>) {
  const safeBottom = useSafeAreaInsets().bottom;
  const [gap, setGap] = useState(0);
  const { height } = useReanimatedKeyboardAnimation();  // 键盘开着时是负的键盘高度

  const onLayout = useCallback(() => {
    if (!ref) return;
    ref.current?.measureInWindow((_x, y, _w, h) => {
      setGap(Math.max(0, Math.round(Dimensions.get('window').height - (y + h))));
    });
  }, [ref]);

  const home = Math.max(safeBottom - gap, 0);
  const style = useAnimatedStyle(() => ({ paddingBottom: Math.max(-height.value - gap, home, 0) }), [gap, home]);
  const spacer = useAnimatedStyle(() => ({ height: Math.max(-height.value - gap, home, 0) }), [gap, home]);
  return { style, spacer, home, offset: gap + home, onLayout };
}

/**
 * 跟着键盘走的输入栏：键盘升起时整条往上移（transform，不重新排版），最多移到键盘上沿，下拉收键盘时逐帧跟着。
 * offset = useBottomInset(...).offset：输入栏原本离屏幕底有多远（tab 栏 + Home 指示条），键盘要先盖过这一截才开始推它。
 */
export function KeyboardSticky({ offset, style, children, ...rest }: ViewProps & { offset: number }) {
  const { height } = useReanimatedKeyboardAnimation();
  const moved = useAnimatedStyle(() => ({ transform: [{ translateY: Math.min(0, height.value + offset) }] }), [offset]);
  return React.createElement(Reanimated.View, { ...rest, style: [style, moved] }, children);
}

/**
 * 对话的消息列表：iOS 上是 KeyboardChatScrollView，键盘升起时内容和输入栏一起往上走（像信息 App），
 * 下拉收键盘时一起落下；offset 同上。别的平台就是普通 ScrollView。
 */
type ChatScrollProps = ScrollViewProps & {
  offset: number;
  /** iOS：键盘在列表底下垫出的高度变了（contentInset），滚到底的时候要算上它 */
  onInsetChange?: (bottom: number) => void;
};

export const ChatScroll = React.forwardRef<ScrollView, ChatScrollProps>(function ChatScroll({ offset, onInsetChange, ...props }, ref) {
  if (Platform.OS === 'ios') {
    const onContentInsetChange = onInsetChange ? (i: { bottom?: number }) => onInsetChange(i.bottom ?? 0) : undefined;
    return React.createElement(KeyboardChatScrollView, { ...props, offset, onContentInsetChange, ref: ref as never });
  }
  return React.createElement(ScrollView, { ...props, ref });
});

/** 列表的收键盘方式：iOS 下拉时键盘跟着手指走；Android 一拖就收；网页不收（react-native-web 滚动时会让输入框失焦）。 */
export const dismissMode = Platform.OS === 'ios' ? 'interactive' : Platform.OS === 'web' ? 'none' : 'on-drag';
