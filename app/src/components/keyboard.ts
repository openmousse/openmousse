import { useCallback, useEffect, useRef, useState } from 'react';
import { Dimensions, Keyboard, LayoutAnimation, Platform, type KeyboardEvent, type View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

/**
 * 贴着底部的那块（对话输入栏、底部弹层）底下要让出多少，当它的 paddingBottom 用：
 * 键盘弹起时正好让到键盘上沿；键盘收起时让出 Home 指示条，底边不贴屏幕底（比如在 tab 栏上面）就是 0。
 *
 * 传 ref 就量这个 View 在窗口里的位置（onLayout 也要接到它上面）；不传就当它的底边是屏幕底边（弹层）。
 * 原生 modal 页里别传 ref：那里量出来的坐标是相对 modal 的，不是屏幕的。
 *
 * 不用 RN 的 KeyboardAvoidingView：它拿 onLayout 的相对坐标去比键盘的屏幕坐标，得手填 keyboardVerticalOffset，
 * 填错了输入栏就悬在键盘上面一截（之前写死 90，iPhone 上空出 90pt）。
 * 键盘只处理 iOS：Android 由系统顶起窗口，网页没有键盘事件。
 */
export function useBottomInset(ref?: React.RefObject<View | null>) {
  const safeBottom = useSafeAreaInsets().bottom;
  const [inset, setInset] = useState(0);
  const s = useRef({ inset: 0, keyboardTop: null as number | null, safeBottom });

  const measure = useCallback((e?: KeyboardEvent) => {
    const apply = (bottom: number) => {
      const cur = s.current;
      const covered = cur.keyboardTop == null ? 0 : bottom - cur.keyboardTop;
      const home = bottom - (Dimensions.get('window').height - cur.safeBottom);
      const next = Math.round(Math.max(covered, home, 0));
      if (next === cur.inset) return;
      cur.inset = next;
      // 跟键盘用同一个时长。iOS 报的曲线是 'keyboard'，新架构的 LayoutAnimation 把它当匀速，
      // 输入栏会落后键盘一大截（最多 150pt 左右）；ease-out 最接近系统键盘先快后慢的走法。
      const curve = e?.easing && e.easing !== 'keyboard' ? LayoutAnimation.Types[e.easing] : LayoutAnimation.Types.easeOut;
      if (e?.duration) LayoutAnimation.configureNext({ duration: e.duration, update: { duration: e.duration, type: curve } });
      setInset(next);
    };
    if (ref) ref.current?.measureInWindow((_x, y, _w, h) => apply(y + h));
    else apply(Dimensions.get('window').height);
  }, [ref]);

  useEffect(() => { s.current.safeBottom = safeBottom; measure(); }, [measure, safeBottom]);

  useEffect(() => {
    if (Platform.OS !== 'ios') return;
    const show = (e: KeyboardEvent) => {
      const { screenY, height } = e.endCoordinates;
      // 打开「首选淡入淡出过渡」时 iOS 报的 screenY 是 0，只能拿高度倒推上沿。
      s.current.keyboardTop = height > 0 ? (screenY > 0 ? screenY : Dimensions.get('window').height - height) : null;
      measure(e);
    };
    const hide = (e: KeyboardEvent) => { s.current.keyboardTop = null; measure(e); };
    const subs = [
      Keyboard.addListener('keyboardWillChangeFrame', show),  // 也管切输入法、中文候选栏、表情键盘带来的高度变化
      Keyboard.addListener('keyboardWillShow', show),
      Keyboard.addListener('keyboardWillHide', hide),
      // did 事件兜底：快速连点时 will 事件可能错过或乱序，以键盘最后停下的状态为准。
      Keyboard.addListener('keyboardDidShow', show),
      Keyboard.addListener('keyboardDidHide', hide),
    ];
    return () => subs.forEach((sub) => sub.remove());
  }, [measure]);

  return { inset, onLayout: () => measure() };
}
