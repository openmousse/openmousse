import React from 'react';
import { StatusBar } from 'expo-status-bar';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { SheetProvider } from './src/components/Sheet';
import { UpdateBanner } from './src/components/UpdateBanner';
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
    </SheetProvider>
  );
}

export default function App() {
  return (
    <SafeAreaProvider>
      <ThemeProvider>
        <StoreProvider>
          <Shell />
        </StoreProvider>
      </ThemeProvider>
    </SafeAreaProvider>
  );
}
