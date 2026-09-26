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
import { BoardHistoryScreen } from './screens/BoardHistoryScreen';
import { EditGroupScreen } from './screens/EditGroupScreen';
import { TaskScreen, TasksScreen } from './screens/TasksScreen';
import { ScheduleFeedScreen } from './screens/ScheduleFeedScreen';
import { TodayScreen } from './screens/TodayScreen';
import { HistoryDayScreen, HistoryScreen } from './screens/HistoryScreen';
import { ConnectScreen } from './screens/ConnectScreen';
import { InboxScreen } from './screens/InboxScreen';
import type { PushTarget } from './data/types';
import { L } from './i18n';
import { useStore } from './store';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useTheme } from './theme';

const Tab = createBottomTabNavigator();
const Stack = createNativeStackNavigator();

function Tabs() {
  const t = useTheme();
  const { inbox, unread, groups, sideChats } = useStore();
  const insets = useSafeAreaInsets();
  // 未读（青色）：「对话」= 主对话 + 独立空间，「Agents」= 各个 Agent。「今天」（金色）= 等你点头的。
  const n = (id: string) => unread.threads[id]?.n ?? 0;
  const chatUnread = n('main') + sideChats.reduce((sum, c) => sum + n(c.id), 0);
  const agentUnread = groups.reduce((sum, g) => sum + n(g.id), 0);
  const cyanBadge = { backgroundColor: t.cyan, color: t.surface };
  // Tab 的 name 是路由标识（深链 ?screen=今天、navigate 都用它），不翻译；界面上显示的是 tabBarLabel。
  return (
    <Tab.Navigator
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: t.gold,
        tabBarInactiveTintColor: t.ink3,
        tabBarStyle: { backgroundColor: t.surface, borderTopColor: t.line, height: 72 + insets.bottom, paddingTop: 8, paddingBottom: insets.bottom + 10 },
        tabBarLabelStyle: { fontSize: 11, fontWeight: '600', lineHeight: 14 },
      }}>
      <Tab.Screen name="对话" component={ChatScreen}
        options={{ tabBarLabel: L('对话', 'Chat'), tabBarIcon: ({ color, size }) => <MessageCircle color={color} size={size} />, tabBarBadge: chatUnread || undefined, tabBarBadgeStyle: cyanBadge }} />
      <Tab.Screen name="Agents" component={GroupsScreen}
        options={{ tabBarIcon: ({ color, size }) => <LayoutGrid color={color} size={size} />, tabBarBadge: agentUnread || undefined, tabBarBadgeStyle: cyanBadge }} />
      <Tab.Screen name="今天" component={TodayScreen}
        options={{ tabBarLabel: L('今天', 'Today'), tabBarIcon: ({ color, size }) => <Sparkles color={color} size={size} />, tabBarBadge: inbox.length || undefined, tabBarBadgeStyle: { backgroundColor: t.goldFill, color: t.onGold } }} />
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

/** 给推送通知和小窗跳转用（src/store.tsx、components/Banner.tsx）。 */
export const navigationRef = createNavigationContainerRef<any>();

/**
 * 输入框上面的引用：从收件箱「去对话里说」带过来的（inboxId：显示「回复：标题」，发出去时带上），
 * 或者任务卡上点了「改一下」（taskId：显示「改：标题」，发出去的话直接交给做这件事的子会话，不进这个对话）。
 */
export interface ChatQuote { inboxId?: string; taskId?: string; title: string; model?: string | null; /** 日程或「要记得的」里的一条（schedule.py 的 id）：发出去时带上，模型知道说的是哪一条 */ ref?: string }

// 冷启动时点通知，那一下可能比导航器准备好还早（RootNavigator 要等本机配置读完才渲染）：先记下来，onReady 时补上。
let queued: { target: PushTarget; isGroup: boolean; quote?: ChatQuote; focus?: string } | null = null;

/**
 * 打开推送 / 小窗指向的地方：
 * 对话 → main / 独立空间在「对话」tab 里，Agent 有自己的页；卡片、收件箱 → 「今天」页，滚到那一张闪一下金边。
 * 去 tab 用 pop：从「已处理」「任务」这类叠在上面的页过去时退回到 tab，不再叠一层新的。
 */
export function openTarget(target: PushTarget, isGroup = false, quote?: ChatQuote, focus?: string) {
  if (!navigationRef.isReady()) { queued = { target, isGroup, quote, focus }; return; }
  const at = Date.now();
  const tab = (screen: string, params: object) => navigationRef.navigate('Tabs', { screen, params }, { pop: true });
  switch (target.type) {
    case 'thread':
      if (target.thread === 'today') tab('今天', { at });
      else if (isGroup) navigationRef.navigate('Group', { id: target.thread, tab: 'chat', at, quote, focus });
      else tab('对话', { thread: target.thread, at, quote, focus });
      return;
    case 'card':
    case 'inbox':
      tab('今天', { highlight: { kind: target.type, id: target.id }, at });
      return;
    default:
      tab('今天', { at });
  }
}

/** 打开某个对话（quote：顺带一条收件箱引用；focus：滚到这条消息（"db<id>"）闪一下，比如点转交卡去看 Agent 那边的那个问题）。 */
export const openThread = (thread: string, isGroup: boolean, quote?: ChatQuote, focus?: string) => openTarget({ type: 'thread', thread }, isGroup, quote, focus);

function flushQueued() {
  if (!queued || !navigationRef.isReady()) return;
  const q = queued;
  queued = null;
  openTarget(q.target, q.isGroup, q.quote, q.focus);
}

export function RootNavigator() {
  const t = useTheme();
  const { configLoaded, needsServer } = useStore();
  const base = t.mode === 'dark' ? DarkTheme : DefaultTheme;
  if (!configLoaded) return null;  // 先读本机的服务器配置，决定首页是连接页还是 Tabs
  // 不配置 linking：导航状态只存在内存里，不读写浏览器地址栏，网页预览放在任何路径下都能跑。
  return (
    <NavigationContainer ref={navigationRef} theme={{ ...base, colors: { ...base.colors, background: t.bg, card: t.surface, text: t.ink, border: t.line, primary: t.gold } }} documentTitle={{ enabled: false }} initialState={initialFromQuery()} onReady={flushQueued}>
      <Stack.Navigator initialRouteName={needsServer ? 'Connect' : 'Tabs'} screenOptions={{ headerShown: false, contentStyle: { backgroundColor: t.bg } }}>
        <Stack.Screen name="Tabs" component={Tabs} />
        <Stack.Screen name="Connect" component={ConnectScreen} />
        <Stack.Screen name="Group" component={GroupScreen} />
        <Stack.Screen name="History" component={HistoryScreen} />
        <Stack.Screen name="BoardHistory" component={BoardHistoryScreen} />
        <Stack.Screen name="HistoryDay" component={HistoryDayScreen} />
        <Stack.Screen name="Inbox" component={InboxScreen} />
        <Stack.Screen name="NewGroup" component={NewGroupScreen} options={{ presentation: 'modal' }} />
        <Stack.Screen name="EditGroup" component={EditGroupScreen} options={{ presentation: 'modal' }} />
        <Stack.Screen name="Identity" component={IdentityScreen} />
        <Stack.Screen name="Memory" component={MemoryScreen} />
        <Stack.Screen name="Journal" component={JournalScreen} />
        <Stack.Screen name="Activity" component={ActivityScreen} />
        <Stack.Screen name="Security" component={SecurityScreen} />
        <Stack.Screen name="Models" component={ModelsScreen} />
        <Stack.Screen name="Avatar" component={AvatarScreen} />
        <Stack.Screen name="Tasks" component={TasksScreen} />
        <Stack.Screen name="ScheduleFeed" component={ScheduleFeedScreen} />
        <Stack.Screen name="Task" component={TaskScreen} />
      </Stack.Navigator>
    </NavigationContainer>
  );
}
