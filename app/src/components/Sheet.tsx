import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { Animated, BackHandler, Easing, Keyboard, Platform, Pressable, ScrollView, StyleSheet, View, useWindowDimensions } from 'react-native';
import { L } from '../i18n';
import { LiftSurface, radius, space, useTheme } from '../theme';
import Reanimated from 'react-native-reanimated';
import { useBottomInset } from './keyboard';
import { T } from './ui';

// 自己画的底部弹层，不用 RN 的 Modal：Modal 在网页上会挂到 body，跑出预览里的手机框。
// header：换掉标题那一行（小板子的头像 + 名字）；footer：钉在底下、不跟着内容滚的按钮；tall：最高到屏幕的九成（默认八成多）。
interface SheetSpec { title: string; /** 标题右边的灰字（对话页「新建」写在哪一边） */ sub?: string; content: (close: () => void) => React.ReactNode; header?: React.ReactNode; footer?: (close: () => void) => React.ReactNode; tall?: boolean }
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
  // 安卓的返回键 / 返回手势：弹层开着时只关弹层（后注册的先收到，导航那边就不会退一页）
  useEffect(() => {
    if (!shown || Platform.OS !== 'android') return;
    const sub = BackHandler.addEventListener('hardwareBackPress', () => { close(); return true; });
    return () => sub.remove();
  }, [shown, close]);
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
      {/* 深色浮层用 surface，和后面页面的 bg 分开。浅色仍用 bg：白卡片落在灰底上，样子不动。
          这里的 t 在 LiftSurface 外面，只有面板底色用它。里面的字和线在 SheetBody 里再取一次。 */}
      <Animated.View style={[styles.panel, { backgroundColor: t.mode === 'dark' ? t.surface : t.bg, paddingBottom: space.lg, transform: [{ translateY: lift }] }, spec.tall && { maxHeight: '90%' }]}>
        <LiftSurface>
          <SheetBody spec={spec} close={close} />
          {/* 垫片：键盘开着时和键盘一样高（逐帧跟着），收起时让出 Home 指示条 */}
          <Reanimated.View style={bottom.spacer} />
        </LiftSurface>
      </Animated.View>
    </View>
  );
}

/** 弹层里的拖动条、标题和内容。必须在 LiftSurface 里面取色：Panel 那份 t 是没抬的。 */
function SheetBody({ spec, close }: { spec: SheetSpec; close: () => void }) {
  const t = useTheme();
  return (
    <>
      <View style={[styles.grabber, { backgroundColor: t.line }]} />
      {spec.header ?? (spec.sub ? (
        <View style={{ flexDirection: 'row', alignItems: 'baseline', gap: space.sm, marginBottom: space.md }}>
          <T v="title">{spec.title}</T>
          <T v="callout" color={t.ink3}>{spec.sub}</T>
        </View>
      ) : <T v="title" style={{ marginBottom: space.md }}>{spec.title}</T>)}
      {/* handled：键盘开着时点弹层里的按钮（比如「保存」）一次就生效，点空白处收键盘。 */}
      <ScrollView style={{ flexGrow: 0 }} showsVerticalScrollIndicator={false} keyboardShouldPersistTaps="handled">{spec.content(close)}</ScrollView>
      {spec.footer ? spec.footer(close) : null}
    </>
  );
}

const styles = StyleSheet.create({
  panel: { position: 'absolute', left: 0, right: 0, bottom: 0, maxHeight: '82%', borderTopLeftRadius: radius.lg + 4, borderTopRightRadius: radius.lg + 4, paddingHorizontal: space.lg, paddingTop: space.sm },
  grabber: { alignSelf: 'center', width: 36, height: 5, borderRadius: 3, marginBottom: space.md },
});
