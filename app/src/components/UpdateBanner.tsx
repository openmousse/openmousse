// 热更新提示：启动和回到前台时查一次 EAS Update，下载好了就在顶上放一条"点一下重载"。
// 之前靠 expo-updates 默认行为要冷启动两次才生效，用户看不到新东西会以为没推上。
import React, { useEffect, useRef, useState } from 'react';
import { AppState, Platform, Pressable, StyleSheet, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import * as Updates from 'expo-updates';
import { T } from './ui';
import { L } from '../i18n';
import { navigationRef } from '../navigation';
import { space, useTheme } from '../theme';

const CHECK_EVERY_MS = 60_000;
/** 这两页不放：重载会丢掉填到一半的东西（10/22 朋友收到验证码后点了重载，回到填邮箱，再发就被限流）。下载好的新版本下次冷启动照样生效。 */
const QUIET = new Set(['Login', 'Connect']);
const routeName = () => (navigationRef.isReady() ? navigationRef.getCurrentRoute()?.name : undefined);

export function UpdateBanner() {
  const t = useTheme();
  const insets = useSafeAreaInsets();
  const [ready, setReady] = useState(false);
  const [route, setRoute] = useState<string | undefined>(undefined);
  const last = useRef(0);

  useEffect(() => navigationRef.addListener('state', () => setRoute(routeName())), []);

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
        if (alive) { setRoute(routeName()); setReady(true); }
      } catch { /* 没网或服务不可达：下次再查 */ }
    };
    check();
    const sub = AppState.addEventListener('change', (s) => { if (s === 'active') check(); });
    return () => { alive = false; sub.remove(); };
  }, []);

  if (!ready || (route && QUIET.has(route))) return null;
  return (
    <View pointerEvents="box-none" style={[styles.wrap, { top: insets.top + 6 }]}>
      <Pressable onPress={() => Updates.reloadAsync().catch(() => setReady(false))} accessibilityRole="button"
        style={({ pressed }) => [styles.pill, { backgroundColor: t.gold, opacity: pressed ? 0.8 : 1 }]}>
        <T v="callout" color="#FFFFFF" style={{ fontWeight: '600' }}>{L('新版本已下载 · 轻点重新加载', 'New version downloaded · Tap to reload')}</T>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { position: 'absolute', left: 0, right: 0, alignItems: 'center', zIndex: 50 },
  pill: { paddingHorizontal: space.lg, paddingVertical: 10, borderRadius: 999, shadowColor: '#000', shadowOpacity: 0.18, shadowRadius: 8, shadowOffset: { width: 0, height: 3 }, elevation: 4 },
});
