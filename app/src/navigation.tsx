import React from 'react';
import { Platform } from 'react-native';
import { createNavigationContainerRef, DarkTheme, DefaultTheme, NavigationContainer } from '@react-navigation/native';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { LayoutGrid, MessageCircle, Sparkles, Target, User } from './components/icons';
import { ChatScreen } from './screens/ChatScreen';
import { GoalsScreen } from './screens/GoalsScreen';
import { GroupScreen } from './screens/GroupScreen';
import { GroupsScreen } from './screens/GroupsScreen';
import { MeScreen } from './screens/MeScreen';
import { ActivityScreen, AvatarScreen, IdentityScreen, JournalScreen, MemoryScreen, ModelsScreen, SecurityScreen } from './screens/MoreScreens';
import { NewGroupScreen } from './screens/NewGroupScreen';
import { TaskScreen, TasksScreen } from './screens/TasksScreen';
import { TodayScreen } from './screens/TodayScreen';
import { HistoryDayScreen, HistoryScreen } from './screens/HistoryScreen';
import { ConnectScreen } from './screens/ConnectScreen';
import { L } from './i18n';
import { useStore } from './store';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useTheme } from './theme';

const Tab = createBottomTabNavigator();
const Stack = createNativeStackNavigator();

function Tabs() {
  const t = useTheme();
  const { approvals } = useStore();
  const insets = useSafeAreaInsets();
  // Tab 的 name 是路由标识（深链 ?screen=今天、navigate 都用它），不翻译；界面上显示的是 tabBarLabel。
  return (
    <Tab.Navigator
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: t.gold,
        tabBarInactiveTintColor: t.ink3,
        tabBarStyle: { backgroundColor: t.surface, borderTopColor: t.line, height: 68 + insets.bottom, paddingTop: 8, paddingBottom: insets.bottom + 10 },
        tabBarLabelStyle: { fontSize: 11, fontWeight: '600', lineHeight: 14 },
      }}>
      <Tab.Screen name="对话" component={ChatScreen} options={{ tabBarLabel: L('对话', 'Chat'), tabBarIcon: ({ color, size }) => <MessageCircle color={color} size={size} /> }} />
      <Tab.Screen name="Agents" component={GroupsScreen} options={{ tabBarIcon: ({ color, size }) => <LayoutGrid color={color} size={size} /> }} />
      <Tab.Screen name="今天" component={TodayScreen}
        options={{ tabBarLabel: L('今天', 'Today'), tabBarIcon: ({ color, size }) => <Sparkles color={color} size={size} />, tabBarBadge: approvals.length || undefined, tabBarBadgeStyle: { backgroundColor: t.goldFill, color: t.onGold } }} />
      <Tab.Screen name="目标" component={GoalsScreen} options={{ tabBarLabel: L('目标', 'Goals'), tabBarIcon: ({ color, size }) => <Target color={color} size={size} /> }} />
      <Tab.Screen name="我" component={MeScreen} options={{ tabBarLabel: L('我', 'Me'), tabBarIcon: ({ color, size }) => <User color={color} size={size} /> }} />
    </Tab.Navigator>
  );
}

// 调试用：网页版可以用 ?screen=Group&id=fitness&tab=board 直接打开某一页，方便截图检查。
function initialFromQuery() {
  if (Platform.OS !== 'web' || typeof window === 'undefined') return undefined;
  const q = new URLSearchParams(window.location.search);
  const screen = q.get('screen');
  if (!screen) return undefined;
  const params = Object.fromEntries([...q.entries()].filter(([k]) => k !== 'screen'));
  const tabs = ['对话', 'Agents', '今天', '目标', '我'];
  if (tabs.includes(screen)) return { routes: [{ name: 'Tabs', state: { routes: tabs.map((name) => ({ name })), index: tabs.indexOf(screen) } }] };
  return { routes: [{ name: 'Tabs' }, { name: screen, params }], index: 1 };
}

/** 给推送通知跳转用（src/store.tsx）。 */
export const navigationRef = createNavigationContainerRef<any>();

/** 打开某个对话：main / 独立空间在「对话」tab 里，Group 有自己的页。 */
export function openThread(thread: string, isGroup: boolean) {
  if (!navigationRef.isReady()) return;
  if (thread === 'today') { navigationRef.navigate('Tabs', { screen: '今天' }); return; }  // 起床报告、提醒
  if (isGroup) navigationRef.navigate('Group', { id: thread });
  else navigationRef.navigate('Tabs', { screen: '对话', params: { thread, at: Date.now() } });
}

export function RootNavigator() {
  const t = useTheme();
  const { configLoaded, needsServer } = useStore();
  const base = t.mode === 'dark' ? DarkTheme : DefaultTheme;
  if (!configLoaded) return null;  // 先读本机的服务器配置，决定首页是连接页还是 Tabs
  // 不配置 linking：导航状态只存在内存里，不读写浏览器地址栏，网页预览放在任何路径下都能跑。
  return (
    <NavigationContainer ref={navigationRef} theme={{ ...base, colors: { ...base.colors, background: t.bg, card: t.surface, text: t.ink, border: t.line, primary: t.gold } }} documentTitle={{ enabled: false }} initialState={initialFromQuery()}>
      <Stack.Navigator initialRouteName={needsServer ? 'Connect' : 'Tabs'} screenOptions={{ headerShown: false, contentStyle: { backgroundColor: t.bg } }}>
        <Stack.Screen name="Tabs" component={Tabs} />
        <Stack.Screen name="Connect" component={ConnectScreen} />
        <Stack.Screen name="Group" component={GroupScreen} />
        <Stack.Screen name="History" component={HistoryScreen} />
        <Stack.Screen name="HistoryDay" component={HistoryDayScreen} />
        <Stack.Screen name="NewGroup" component={NewGroupScreen} options={{ presentation: 'modal' }} />
        <Stack.Screen name="Identity" component={IdentityScreen} />
        <Stack.Screen name="Memory" component={MemoryScreen} />
        <Stack.Screen name="Journal" component={JournalScreen} />
        <Stack.Screen name="Activity" component={ActivityScreen} />
        <Stack.Screen name="Security" component={SecurityScreen} />
        <Stack.Screen name="Models" component={ModelsScreen} />
        <Stack.Screen name="Avatar" component={AvatarScreen} />
        <Stack.Screen name="Tasks" component={TasksScreen} />
        <Stack.Screen name="Task" component={TaskScreen} />
      </Stack.Navigator>
    </NavigationContainer>
  );
}
