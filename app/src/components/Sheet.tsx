import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { Animated, Easing, Keyboard, Platform, Pressable, ScrollView, StyleSheet, View, useWindowDimensions } from 'react-native';
import { L } from '../i18n';
import { radius, space, useTheme } from '../theme';
import Reanimated from 'react-native-reanimated';
import { useBottomInset } from './keyboard';
import { T } from './ui';

// 自己画的底部弹层，不用 RN 的 Modal：Modal 在网页上会挂到 body，跑出预览里的手机框。
interface SheetSpec { title: string; content: (close: () => void) => React.ReactNode }
const Ctx = createContext<{ open: (s: SheetSpec) => void; close: () => void }>({ open: () => {}, close: () => {} });
export const useSheet = () => useContext(Ctx);

export function SheetProvider({ children }: { children: React.ReactNode }) {
  const [spec, setSpec] = useState<SheetSpec | null>(null);
  const [shown, setShown] = useState(false);
  const want = useRef(false);  // 落下动画刚走完、同一刻又被打开时，别把新打开的卸掉
  // 弹层和键盘不同时出现：打开时先收键盘，弹层同时从底下升上来，接住键盘让出的位置；关的时候一起落下。
  const open = useCallback((s: SheetSpec) => { Keyboard.dismiss(); want.current = true; setSpec(s); setShown(true); }, []);
  const close = useCallback(() => { Keyboard.dismiss(); want.current = false; setShown(false); }, []);
  const hidden = useCallback(() => { if (!want.current) setSpec(null); }, []);
  const api = useMemo(() => ({ open, close }), [open, close]);
  return (
    <Ctx.Provider value={api}>
      <View style={{ flex: 1 }}>
        {children}
        {spec ? <Panel spec={spec} shown={shown} close={close} onHidden={hidden} /> : null}
      </View>
    </Ctx.Provider>
  );
}

/** 升起 / 落下都有动画；落下走完才卸掉（onHidden），中途又被打开就接着升。 */
function Panel({ spec, shown, close, onHidden }: { spec: SheetSpec; shown: boolean; close: () => void; onHidden: () => void }) {
  const t = useTheme();
  const bottom = useBottomInset();  // 弹层里的输入框弹出键盘时，内容升到键盘上面（底下的垫片）；键盘收起时让出 Home 指示条
  const screen = useWindowDimensions().height;
  const [p] = useState(() => new Animated.Value(0));  // 0 = 藏在屏幕下面，1 = 升起来
  // 位移按屏幕高度算、不按弹层自己的高度：键盘把弹层撑高时，正在走的动画不会跳。
  const lift = useMemo(() => p.interpolate({ inputRange: [0, 1], outputRange: [screen, 0] }), [p, screen]);
  useEffect(() => {
    Animated.timing(p, {
      toValue: shown ? 1 : 0, duration: shown ? 300 : 220, easing: shown ? Easing.out(Easing.cubic) : Easing.in(Easing.quad),
      useNativeDriver: Platform.OS !== 'web',
    }).start(({ finished }) => { if (finished && !shown) onHidden(); });
  }, [shown, p, onHidden]);
  return (
    <View style={StyleSheet.absoluteFill} pointerEvents={shown ? 'auto' : 'none'} accessibilityViewIsModal>
      <Animated.View style={[StyleSheet.absoluteFill, { backgroundColor: 'rgba(0,0,0,0.45)', opacity: p }]}>
        <Pressable style={StyleSheet.absoluteFill} onPress={close} accessibilityLabel={L('关闭', 'Close')} />
      </Animated.View>
      <Animated.View style={[styles.panel, { backgroundColor: t.bg, paddingBottom: space.lg, transform: [{ translateY: lift }] }]}>
        <View style={[styles.grabber, { backgroundColor: t.line }]} />
        <T v="title" style={{ marginBottom: space.md }}>{spec.title}</T>
        {/* handled：键盘开着时点弹层里的按钮（比如「保存」）一次就生效，点空白处收键盘。 */}
        <ScrollView style={{ flexGrow: 0 }} showsVerticalScrollIndicator={false} keyboardShouldPersistTaps="handled">{spec.content(close)}</ScrollView>
        {/* 垫片：键盘开着时和键盘一样高（逐帧跟着），收起时让出 Home 指示条 */}
        <Reanimated.View style={bottom.spacer} />
      </Animated.View>
    </View>
  );
}

const styles = StyleSheet.create({
  panel: { position: 'absolute', left: 0, right: 0, bottom: 0, maxHeight: '82%', borderTopLeftRadius: radius.lg + 4, borderTopRightRadius: radius.lg + 4, paddingHorizontal: space.lg, paddingTop: space.sm },
  grabber: { alignSelf: 'center', width: 36, height: 5, borderRadius: 3, marginBottom: space.md },
});
