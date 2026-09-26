import React from 'react';
import { StatusBar } from 'expo-status-bar';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { BannerHost, BannerProvider } from './src/components/Banner';
import { SheetProvider } from './src/components/Sheet';
import { UpdateBanner } from './src/components/UpdateBanner';
import { LangProvider } from './src/i18n';
import { RootNavigator } from './src/navigation';
import { StoreProvider } from './src/store';
import { ThemeProvider, useTheme } from './src/theme';

function Shell() {
  const t = useTheme();
  return (
    <SheetProvider>
      <StatusBar style={t.mode === 'dark' ? 'light' : 'dark'} />
      <RootNavigator />
      <UpdateBanner />
      <BannerHost />
    </SheetProvider>
  );
}

// 小窗（Banner）的队列在 store 外面：store 收到推送、轮询到新消息时要往里放；画出来的 BannerHost 在里面，要用 store 的数据。
export default function App() {
  return (
    <SafeAreaProvider>
      <ThemeProvider>
        <LangProvider>
          <BannerProvider>
            <StoreProvider>
              <Shell />
            </StoreProvider>
          </BannerProvider>
        </LangProvider>
      </ThemeProvider>
    </SafeAreaProvider>
  );
}
