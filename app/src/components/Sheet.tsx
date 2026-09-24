import React, { createContext, useCallback, useContext, useMemo, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { L } from '../i18n';
import { radius, space, useTheme } from '../theme';
import { T } from './ui';

// 自己画的底部弹层，不用 RN 的 Modal：Modal 在网页上会挂到 body，跑出预览里的手机框。
interface SheetSpec { title: string; content: (close: () => void) => React.ReactNode }
const Ctx = createContext<{ open: (s: SheetSpec) => void; close: () => void }>({ open: () => {}, close: () => {} });
export const useSheet = () => useContext(Ctx);

export function SheetProvider({ children }: { children: React.ReactNode }) {
  const t = useTheme();
  const insets = useSafeAreaInsets();
  const [spec, setSpec] = useState<SheetSpec | null>(null);
  const close = useCallback(() => setSpec(null), []);
  const api = useMemo(() => ({ open: setSpec, close }), [close]);
  return (
    <Ctx.Provider value={api}>
      <View style={{ flex: 1 }}>
        {children}
        {spec ? (
          <View style={StyleSheet.absoluteFill} accessibilityViewIsModal>
            <Pressable style={[StyleSheet.absoluteFill, { backgroundColor: 'rgba(0,0,0,0.45)' }]} onPress={close} accessibilityLabel={L('关闭', 'Close')} />
            <View style={[styles.panel, { backgroundColor: t.bg, paddingBottom: insets.bottom + space.lg }]}>
              <View style={[styles.grabber, { backgroundColor: t.line }]} />
              <T v="title" style={{ marginBottom: space.md }}>{spec.title}</T>
              <ScrollView style={{ flexGrow: 0 }} showsVerticalScrollIndicator={false}>{spec.content(close)}</ScrollView>
            </View>
          </View>
        ) : null}
      </View>
    </Ctx.Provider>
  );
}

const styles = StyleSheet.create({
  panel: { position: 'absolute', left: 0, right: 0, bottom: 0, maxHeight: '82%', borderTopLeftRadius: radius.lg + 4, borderTopRightRadius: radius.lg + 4, paddingHorizontal: space.lg, paddingTop: space.sm },
  grabber: { alignSelf: 'center', width: 36, height: 5, borderRadius: 3, marginBottom: space.md },
});
