import React, { useEffect, useState } from 'react';
import { Linking, Platform } from 'react-native';
import { createNavigationContainerRef, DarkTheme, DefaultTheme, NavigationContainer } from '@react-navigation/native';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { LayoutGrid, Lightbulb, MessageCircle, Sparkles, Target } from './components/icons';
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
import { ClawScreen } from './screens/ClawScreen';
import { AccountScreen, LoginScreen, loginSkipped } from './screens/AccountScreens';
import { AppDetailScreen, AppGalleryScreen, CustomAppScreen, StarterScreen } from './screens/AppsScreens';
import { loadServerConfig, parsePairing } from './api/base';
import { accountsEnabled, loadAccount } from './api/account';
import { appsApi, parseOAuthCallback } from './api/apps';
import { showError } from './components/ui';
import { InboxScreen } from './screens/InboxScreen';
import { TreeScreen } from './screens/TreeScreen';
import { ConnectorsScreen } from './screens/ConnectorsScreen';
import { ThinkScreen } from './screens/ThinkScreen';
import { ThinkDoneScreen, ThinkTalkScreen } from './screens/ThinkTalkScreen';
import { ThinkWriteScreen, ZenEndScreen } from './screens/ThinkWriteScreen';
import { ThinkHistoryScreen, ThinkKeywordScreen, ThinkSearchScreen } from './screens/ThinkFindScreens';
import { PodDoneScreen, PodFriendsScreen, PodPrepScreen, PodRecScreen } from './screens/PodcastScreens';
import { PeopleScreen, PersonScreen } from './screens/PeopleScreens';
import { SaveScreen } from './screens/SaveScreen';
import { FilePreviewScreen } from './screens/FilePreviewScreen';
import { ShareScreen } from './screens/ShareScreen';
import { SharesScreen } from './screens/SharesScreen';
import { AddFriendScreen, CardAgentScreen, FriendChatScreen } from './screens/FriendsScreens';
import { FriendAgentsScreen } from './screens/FriendAgentsScreen';
import { findCode } from './api/friends';
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
  // 未读（青色）：「对话」= 主对话 + 项目 + 朋友，「Agents」= 各个 Agent，「Zen」（路由名「思考」）= 聊聊里的回复。「今天」（金色）= 等你点头的。
  const n = (id: string) => unread.threads[id]?.n ?? 0;
  const friendsUnread = Object.values(unread.friends ?? {}).reduce((sum, u) => sum + u.n, 0);  // 朋友发来还没看的（「对话」页的「朋友」）
  const chatUnread = n('main') + sideChats.reduce((sum, c) => sum + n(c.id), 0) + friendsUnread;
  const agentUnread = groups.reduce((sum, g) => sum + n(g.id), 0);
  const thinkUnread = Object.entries(unread.threads).reduce((sum, [tid, u]) => sum + (tid.startsWith('tp-') ? u.n : 0), 0);
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
      {/* 路由名还叫「思考」（跳转、截图参数 ?screen=思考 都认它）；显示的名字 2026-09-28 起叫 Zen */}
      <Tab.Screen name="思考" component={ThinkScreen}
        options={{ tabBarLabel: 'Zen', tabBarIcon: ({ color, size }) => <Lightbulb color={color} size={size} />, tabBarBadge: thinkUnread || undefined, tabBarBadgeStyle: cyanBadge }} />
      <Tab.Screen name="今天" component={TodayScreen}
        options={{ tabBarLabel: L('今天', 'Today'), tabBarIcon: ({ color, size }) => <Sparkles color={color} size={size} />, tabBarBadge: inbox.length || undefined, tabBarBadgeStyle: { backgroundColor: t.goldFill, color: t.onGold } }} />
      <Tab.Screen name="目标" component={GoalsScreen} options={{ tabBarLabel: L('目标', 'Goals'), tabBarIcon: ({ color, size }) => <Target color={color} size={size} /> }} />
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
  const tabs = ['对话', 'Agents', '思考', '今天', '目标'];
  if (tabs.includes(screen)) return { routes: [{ name: 'Tabs', state: { routes: tabs.map((name) => ({ name })), index: tabs.indexOf(screen) } }] };
  return { routes: [{ name: 'Tabs' }, { name: screen, params }], index: 1 };
}

/** 给推送通知和小窗跳转用（src/store.tsx、components/Banner.tsx）。 */
export const navigationRef = createNavigationContainerRef<any>();

/**
 * 输入框上面的引用：从收件箱「去对话里说」带过来的（inboxId：显示「回复：标题」，发出去时带上；follow：「已处理」里点「跟进」，显示「跟进：标题」），
 * 或者任务卡上点了「改一下」（taskId：显示「改：标题」，发出去的话直接交给做这件事的子会话，不进这个对话）。
 */
export interface ChatQuote { inboxId?: string; /** 和 inboxId 一起：跟进一件已经处理过的事（「已处理」里点的），不是「改一下」 */ follow?: boolean; /** 长按「引用」的那条消息（"db<id>"）：发出去时带上，模型看到原话 */ replyTo?: string; taskId?: string; title: string; model?: string | null; /** 日程或「要记得的」里的一条（schedule.py 的 id）：发出去时带上，模型知道说的是哪一条 */ ref?: string; /** 收藏里的一条（问问、翻译）：发出去时带上，模型看到它的正文 */ saveId?: string }

// 冷启动时点通知，那一下可能比导航器准备好还早（RootNavigator 要等本机配置读完才渲染）：先记下来，onReady 时补上。
let queued: { target: PushTarget; isGroup: boolean; quote?: ChatQuote; focus?: string } | null = null;

/**
 * 打开推送 / 小窗指向的地方：
 * 对话 → main / 项目在「对话」tab 里，Agent 有自己的页；卡片、收件箱 → 「今天」页，滚到那一张闪一下金边。
 * 去 tab 用 pop：从「已处理」「任务」这类叠在上面的页过去时退回到 tab，不再叠一层新的。
 */
export function openTarget(target: PushTarget, isGroup = false, quote?: ChatQuote, focus?: string) {
  if (!navigationRef.isReady()) { queued = { target, isGroup, quote, focus }; return; }
  const at = Date.now();
  const tab = (screen: string, params: object) => navigationRef.navigate('Tabs', { screen, params }, { pop: true });
  switch (target.type) {
    case 'thread':
      if (target.thread === 'today') tab('今天', { at });
      else if (target.thread.startsWith('tp-')) navigationRef.navigate('ThinkTalk', { id: target.thread, at });  // 思考主题
      else if (isGroup) navigationRef.navigate('Group', { id: target.thread, tab: 'chat', at, quote, focus });
      else tab('对话', { thread: target.thread, at, quote, focus });
      return;
    case 'card':
    case 'inbox':
      tab('今天', { highlight: { kind: target.type, id: target.id }, at });
      return;
    case 'board':
      navigationRef.navigate('Group', { id: target.agent, tab: 'board', at });
      return;
    case 'friend':
      navigationRef.navigate('FriendChat', { id: target.id, at });
      return;
    default:
      tab('今天', { at });
  }
}

/** 打开某个对话（quote：顺带一条收件箱引用；focus：滚到这条消息（"db<id>"）闪一下，比如点转交卡去看 Agent 那边的那个问题）。 */
export const openThread = (thread: string, isGroup: boolean, quote?: ChatQuote, focus?: string) => openTarget({ type: 'thread', thread }, isGroup, quote, focus);

// 邀请码的深链（落地页上「在 app 里打开」：<scheme>://friends/add?code=<邀请码>）：打开「加朋友」并直接看看是谁
let queuedCode: string | null = null;

export function openAddFriend(code: string) {
  if (!navigationRef.isReady()) { queuedCode = code; return; }
  navigationRef.navigate('AddFriend', { code, at: Date.now() });
}

// 配对链接（服务器上 tokens.py pair 出的 <scheme>://pair?s=<服务器>&c=<码>）：打开连接页、填好地址和配对码，用户看过地址再点「连接」
// （不自动连：别人发来的链接可能想把 app 接到他的服务器上）
let queuedPair: { server: string; code: string } | null = null;

export function openPair(server: string, code: string) {
  if (!navigationRef.isReady()) { queuedPair = { server, code }; return; }
  navigationRef.navigate('Connect', { pairServer: server, pairCode: code, at: Date.now() });
}

// 连接器授权完跳回来（<scheme>://oauth/callback?code=…&state=…，见 api/apps.ts）：交给服务器换令牌，打开那个连接器的页面
let queuedOAuth: ReturnType<typeof parseOAuthCallback> = null;

async function finishOAuth(cb: NonNullable<ReturnType<typeof parseOAuthCallback>>) {
  if (!navigationRef.isReady()) { queuedOAuth = cb; return; }
  try {
    await loadServerConfig();  // 冷启动时点进来：先把服务器地址和令牌读出来
    const r = await appsApi.callback(cb);
    navigationRef.navigate('AppDetail', { id: r.app.id, fresh: true, at: Date.now() });
  } catch (e) {
    showError(L('没连上', "Couldn't connect"), e);
  }
}

function handleUrl(url: string | null) {
  const oauth = url ? parseOAuthCallback(url) : null;
  if (oauth) { finishOAuth(oauth).catch(() => {}); return; }
  if (url && /^[a-z]+:\/\/pair\?/i.test(url)) {
    const p = parsePairing(url.replace(/^[a-z]+:\/\//i, 'openmousse://'));
    if (p.server && p.code) openPair(p.server, p.code);
    return;
  }
  if (!url || !/friends\/add/.test(url)) return;
  const code = findCode(url);
  if (code) openAddFriend(code);
}

function flushQueued() {
  if (queuedOAuth && navigationRef.isReady()) { const o = queuedOAuth; queuedOAuth = null; finishOAuth(o).catch(() => {}); }
  if (queuedPair && navigationRef.isReady()) { const p = queuedPair; queuedPair = null; openPair(p.server, p.code); }
  if (queuedCode && navigationRef.isReady()) { const c = queuedCode; queuedCode = null; openAddFriend(c); }
  if (!queued || !navigationRef.isReady()) return;
  const q = queued;
  queued = null;
  openTarget(q.target, q.isGroup, q.quote, q.focus);
}

export function RootNavigator() {
  const t = useTheme();
  const { configLoaded, needsServer } = useStore();
  const base = t.mode === 'dark' ? DarkTheme : DefaultTheme;
  // 有账号的壳（OpenMousse）第一次打开先登录，登录完再连 claw；没配账号的（自己搭的、自用的）打开就用
  const [acct, setAcct] = useState<'loading' | 'in' | 'out'>(() => (accountsEnabled() ? 'loading' : 'in'));
  useEffect(() => {
    if (!accountsEnabled()) return;
    loadAccount().then((u) => setAcct(u || loginSkipped() ? 'in' : 'out')).catch(() => setAcct('in'));
  }, []);
  useEffect(() => {
    if (Platform.OS === 'web') return undefined;
    Linking.getInitialURL().then(handleUrl).catch(() => {});
    const sub = Linking.addEventListener('url', (e) => handleUrl(e.url));
    return () => sub.remove();
  }, []);
  if (!configLoaded || acct === 'loading') return null;  // 先读本机的服务器配置和账号，决定首页是登录页、连接页还是 Tabs
  // 不配置 linking：导航状态只存在内存里，不读写浏览器地址栏，网页预览放在任何路径下都能跑。
  return (
    <NavigationContainer ref={navigationRef} theme={{ ...base, colors: { ...base.colors, background: t.bg, card: t.surface, text: t.ink, border: t.line, primary: t.gold } }} documentTitle={{ enabled: false }} initialState={initialFromQuery()} onReady={flushQueued}>
      <Stack.Navigator initialRouteName={acct === 'out' ? 'Login' : needsServer ? 'Connect' : 'Tabs'} screenOptions={{ headerShown: false, contentStyle: { backgroundColor: t.bg } }}>
        <Stack.Screen name="Tabs" component={Tabs} />
        <Stack.Screen name="Connect" component={ConnectScreen} />
        {/* 设置页改版（2026-09-29）：账号、claw 详情、连接器（经 MCP 接进来的应用） */}
        <Stack.Screen name="Login" component={LoginScreen} />
        <Stack.Screen name="Account" component={AccountScreen} />
        <Stack.Screen name="Claw" component={ClawScreen} />
        <Stack.Screen name="AppGallery" component={AppGalleryScreen} />
        <Stack.Screen name="AppDetail" component={AppDetailScreen} />
        <Stack.Screen name="CustomApp" component={CustomAppScreen} />
        <Stack.Screen name="Starter" component={StarterScreen} options={{ gestureEnabled: false }} />
        <Stack.Screen name="Group" component={GroupScreen} />
        <Stack.Screen name="History" component={HistoryScreen} />
        <Stack.Screen name="BoardHistory" component={BoardHistoryScreen} />
        <Stack.Screen name="HistoryDay" component={HistoryDayScreen} />
        <Stack.Screen name="Inbox" component={InboxScreen} />
        <Stack.Screen name="NewGroup" component={NewGroupScreen} options={{ presentation: 'modal' }} />
        <Stack.Screen name="EditGroup" component={EditGroupScreen} options={{ presentation: 'modal' }} />
        <Stack.Screen name="Identity" component={IdentityScreen} />
        <Stack.Screen name="Tree" component={TreeScreen} />
        <Stack.Screen name="Connectors" component={ConnectorsScreen} />
        <Stack.Screen name="Memory" component={MemoryScreen} />
        <Stack.Screen name="Journal" component={JournalScreen} />
        <Stack.Screen name="Activity" component={ActivityScreen} />
        <Stack.Screen name="Security" component={SecurityScreen} />
        <Stack.Screen name="Models" component={ModelsScreen} />
        <Stack.Screen name="Avatar" component={AvatarScreen} />
        <Stack.Screen name="Tasks" component={TasksScreen} />
        <Stack.Screen name="ScheduleFeed" component={ScheduleFeedScreen} />
        <Stack.Screen name="Task" component={TaskScreen} />
        {/* 「我」从 tab 挪到了侧栏底部（2026-09-27） */}
        <Stack.Screen name="Me" component={MeScreen} />
        <Stack.Screen name="ThinkTalk" component={ThinkTalkScreen} />
        <Stack.Screen name="ThinkDone" component={ThinkDoneScreen} />
        <Stack.Screen name="ThinkWrite" component={ThinkWriteScreen} options={({ route }: { route: { params?: { zen?: boolean } } }) => ({ gestureEnabled: !route.params?.zen, animation: route.params?.zen ? 'fade' : 'slide_from_bottom' })} />
        <Stack.Screen name="ZenEnd" component={ZenEndScreen} options={{ animation: 'fade' }} />
        <Stack.Screen name="ThinkSearch" component={ThinkSearchScreen} options={{ animation: 'fade' }} />
        <Stack.Screen name="ThinkKeyword" component={ThinkKeywordScreen} />
        <Stack.Screen name="ThinkHistory" component={ThinkHistoryScreen} />
        <Stack.Screen name="PodPrep" component={PodPrepScreen} />
        <Stack.Screen name="PodRec" component={PodRecScreen} options={{ gestureEnabled: false, animation: 'slide_from_bottom' }} />
        <Stack.Screen name="PodDone" component={PodDoneScreen} />
        <Stack.Screen name="PodFriends" component={PodFriendsScreen} />
        {/* 朋友画像（播客记的，只有你看得到，2026-09-28）：我 → 朋友画像，录完页「小林的画像多了 3 条」 */}
        <Stack.Screen name="People" component={PeopleScreen} />
        <Stack.Screen name="Person" component={PersonScreen} />
        <Stack.Screen name="Save" component={SaveScreen} />
        <Stack.Screen name="FilePreview" component={FilePreviewScreen} options={{ presentation: 'fullScreenModal', animation: 'fade', gestureEnabled: false }} />
        {/* 分享（社交第一层，2026-09-28）：先挡私事，再发链接或干净版卡片 */}
        <Stack.Screen name="Share" component={ShareScreen} />
        <Stack.Screen name="Shares" component={SharesScreen} />
        {/* 朋友（社交第二层，2026-09-28）：朋友列表在「对话」页里切，这几页叠在上面 */}
        <Stack.Screen name="FriendChat" component={FriendChatScreen} />
        <Stack.Screen name="AddFriend" component={AddFriendScreen} />
        <Stack.Screen name="CardAgent" component={CardAgentScreen} />
        {/* agent 之间（社交第三层）：从朋友聊天右上角「…」进 */}
        <Stack.Screen name="FriendAgents" component={FriendAgentsScreen} />
      </Stack.Navigator>
    </NavigationContainer>
  );
}
