// 热更新提示：启动和回到前台时查一次 EAS Update，下载好了就在顶上放一条"点一下重载"。
// 之前靠 expo-updates 默认行为要冷启动两次才生效，用户看不到新东西会以为没推上。
import React, { useEffect, useRef, useState } from 'react';
import { AppState, Platform, Pressable, StyleSheet, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import * as Updates from 'expo-updates';
import { T } from './ui';
import { space, useTheme } from '../theme';

const CHECK_EVERY_MS = 60_000;

export function UpdateBanner() {
  const t = useTheme();
  const insets = useSafeAreaInsets();
  const [ready, setReady] = useState(false);
  const last = useRef(0);

  useEffect(() => {
    if (Platform.OS === 'web' || !Updates.isEnabled || __DEV__) return undefined;
    let alive = true;
    const check = async () => {
      if (Date.now() - last.current < CHECK_EVERY_MS) return;
      last.current = Date.now();
      try {
        const r = await Updates.checkForUpdateAsync();
        if (!r.isAvailable) return;
        await Updates.fetchUpdateAsync();
        if (alive) setReady(true);
      } catch { /* 没网或服务不可达：下次再查 */ }
    };
    check();
    const sub = AppState.addEventListener('change', (s) => { if (s === 'active') check(); });
    return () => { alive = false; sub.remove(); };
  }, []);

  if (!ready) return null;
  return (
    <View pointerEvents="box-none" style={[styles.wrap, { top: insets.top + 6 }]}>
      <Pressable onPress={() => Updates.reloadAsync().catch(() => setReady(false))} accessibilityRole="button"
        style={({ pressed }) => [styles.pill, { backgroundColor: t.gold, opacity: pressed ? 0.8 : 1 }]}>
        <T v="callout" color="#FFFFFF" style={{ fontWeight: '600' }}>新版本已下载 · 点一下重载</T>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { position: 'absolute', left: 0, right: 0, alignItems: 'center', zIndex: 50 },
  pill: { paddingHorizontal: space.lg, paddingVertical: 10, borderRadius: 999, shadowColor: '#000', shadowOpacity: 0.18, shadowRadius: 8, shadowOffset: { width: 0, height: 3 }, elevation: 4 },
});
