import React, { useEffect } from 'react';
import { agentName } from '../brand';
import { Pressable, ScrollView, View } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { Activity, BookOpen, Brain, CalendarDays, ClipboardList, Cpu, IdCard, Palette, Plug, Server, ShieldCheck, TreeDeciduous } from '../components/icons';
import { LensAvatar } from '../components/LensAvatar';
import { Card, LargeHeader, ListRow, NavHeader, Pill, PullRefresh, Screen, SectionLabel, Segmented, T } from '../components/ui';
import { L, useLang, type LangPref } from '../i18n';
import { useStore } from '../store';
import { space, useAppearance, useTheme } from '../theme';

export function MeScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { avatar, profile, memories, activity, connected, booting, authFailed, appName, tasks, security, models, journal, tree, connectors, reload } = useStore();
  const { appearance, setAppearance } = useAppearance();
  const { pref, setPref } = useLang();
  const warn = security?.facts.filter((f) => f.tone === 'warn') ?? [];
  const expired = models?.providers.filter((p) => p.subscription && p.status !== 'ok') ?? [];
  const running = tasks.filter((x) => x.status === '进行中').length;
  const leaves = tree?.kind === 'ok' ? tree.data.counts : null;
  const conn = connectors?.kind === 'ok' ? connectors.data.counts : null;
  // 世界树和连接不在启动时读（见 store 的 STARTUP_KEYS）：第一次来「我」这一页时读，下面两行的小字要用
  useEffect(() => { if (connected) reload('tree', 'connectors').catch(() => {}); }, [connected, reload]);
  return (
    <Screen>
      {/* 从侧栏底部进来（2026-09-27 起「我」不是 tab 了）：顶上一个返回 */}
      {nav.canGoBack() ? <NavHeader title={L('我', 'Me')} onBack={() => nav.goBack()} /> : null}
      <ScrollView contentContainerStyle={{ paddingTop: nav.canGoBack() ? space.lg : 0, paddingBottom: space.xxl }} refreshControl={<PullRefresh onRefresh={() => reload('profile', 'tree', 'memories', 'journal', 'activity', 'tasks', 'security', 'models', 'connectors')} />}>
        {nav.canGoBack() ? null : <LargeHeader title={L('我', 'Me')} />}
        <View style={{ paddingHorizontal: space.lg }}>
          <Pressable onPress={() => nav.navigate('Avatar')} accessibilityRole="button" accessibilityLabel={L(`定制 ${agentName()} 的形象`, `Customize ${agentName()}'s look`)}>
            <Card style={{ flexDirection: 'row', alignItems: 'center', gap: space.lg }}>
              <LensAvatar size={64} config={avatar} />
              <View style={{ flex: 1, gap: 4 }}>
                <T v="title">{agentName()}</T>
                <T v="callout" color={t.ink2}>{L('不是工具，是搭档。点这里换它的样子。', 'Not a tool, a partner. Tap to change how it looks.')}</T>
                <View style={{ flexDirection: 'row' }}>{connected ? <Pill label={L('已连接服务器', 'Connected')} tone="good" /> : <Pill label={booting ? L('正在连接…', 'Connecting…') : authFailed ? L('令牌不对', 'Wrong token') : L('未连接服务器', 'Not connected')} tone="warn" />}</View>
              </View>
            </Card>
          </Pressable>

          <SectionLabel>{L(`${agentName()} 知道的`, `What ${agentName()} knows`)}</SectionLabel>
          <Card style={{ paddingVertical: space.xs }}>
            <ListRow icon={<IdCard size={20} color={t.cyan} />} title={L('基础档案', 'Profile')} sub={profile.length ? L(`${profile.length} 条，所有 agent 共用，可以直接改`, `${profile.length} items, shared by all agents, editable`) : L('所有 agent 共用', 'Shared by all agents')} onPress={() => nav.navigate('Identity')} />
            <ListRow icon={<TreeDeciduous size={20} color={t.cyan} />} title={L('世界树', 'Memory tree')}
              sub={leaves ? (leaves.pending ? L(`${leaves.total} 片叶子，${leaves.pending} 条等你确认`, `${leaves.total} leaves, ${leaves.pending} waiting for you`) : L(`${leaves.total} 片叶子，所有 AI 平台共用`, `${leaves.total} leaves, shared by all your AI apps`)) : L('所有 AI 平台共用的记忆', 'Memory shared by all your AI apps')}
              onPress={() => nav.navigate('Tree')} />
            <ListRow icon={<Brain size={20} color={t.cyan} />} title={L('记忆', 'Memory')} sub={memories.length ? L(`长期记忆 ${memories.length} 条，可以逐条忘记`, `${memories.length} long-term memories, forget any of them`) : L('长期记忆，可以逐条忘记', 'Long-term memory, forget any item')} onPress={() => nav.navigate('Memory')} />
            <ListRow icon={<BookOpen size={20} color={t.cyan} />} title={L('日志', 'Journal')} sub={journal.length ? L(`${journal.length} 条感受、想法和决定`, `${journal.length} feelings, thoughts and decisions`) : L('感受、想法、决定，在对话里说就会记', 'Feelings, thoughts, decisions: say them in chat and they get logged')} onPress={() => nav.navigate('Journal')} last />
          </Card>

          <SectionLabel>{L(`${agentName()} 做过的`, `What ${agentName()} did`)}</SectionLabel>
          <Card style={{ paddingVertical: space.xs }}>
            <ListRow icon={<Activity size={20} color={t.cyan} />} title={L('活动记录', 'Activity')} sub={activity[0] ? `${activity[0].time} · ${activity[0].text}` : L('每一次回复、定时任务和你的操作', 'Every reply, scheduled job and action you took')} onPress={() => nav.navigate('Activity')} />
            <ListRow icon={<ClipboardList size={20} color={t.cyan} />} title={L('任务', 'Tasks')} sub={tasks.length ? L(`${tasks.length} 个子会话${running ? `，${running} 个在跑` : ''}，能看过程`, `${tasks.length} sub-sessions${running ? `, ${running} running` : ''}, steps included`) : L('派出去的子会话', 'Sub-sessions sent out')} onPress={() => nav.navigate('Tasks')} />
            <ListRow icon={<ShieldCheck size={20} color={warn.length ? t.warn : t.cyan} />} title={L('安全', 'Security')} sub={security ? (warn.length ? L(`${warn.length} 项要注意：${warn.map((f) => f.title).join('、')}`, `${warn.length} to check: ${warn.map((f) => f.title).join(', ')}`) : L('都正常', 'All good')) : L('服务器上的实测状态', 'Live status from the server')} onPress={() => nav.navigate('Security')} last />
          </Card>

          <SectionLabel>{L('设置', 'Settings')}</SectionLabel>
          <Card style={{ paddingVertical: space.xs }}>
            <ListRow icon={<Server size={20} color={connected ? t.cyan : t.warn} />} title={L('服务器', 'Server')} sub={connected ? L(`已连接 · ${appName}`, `Connected · ${appName}`) : authFailed ? L('令牌不对，点这里改', 'Wrong token, tap to fix') : L('地址和接入令牌', 'Address and access token')} onPress={() => nav.navigate('Connect')} />
            <ListRow icon={<Plug size={20} color={conn?.warn ? t.warn : t.cyan} />} title={L('连接', 'Connections')}
              sub={conn ? [L(`${conn.ok} 个在用`, `${conn.ok} working`), conn.warn ? L(`${conn.warn} 个要注意`, `${conn.warn} need a look`) : ''].filter(Boolean).join(L('，', ', ')) : L(`${agentName()} 接着的各项服务，现在怎么样`, `What ${agentName()} is connected to, and how it's doing`)}
              onPress={() => nav.navigate('Connectors')} />
            <ListRow icon={<CalendarDays size={20} color={t.cyan} />} title={L('日程', 'Schedule')} sub={L('在 iPhone 日历里看（订阅链接）', 'See it in your iPhone calendar (subscription)')} onPress={() => nav.navigate('ScheduleFeed')} />
            <ListRow icon={<Cpu size={20} color={expired.length ? t.warn : t.cyan} />} title={L('模型与计费', 'Models & billing')} sub={expired.length ? L(`${expired.map((p) => p.name).join('、')} 订阅登录已过期`, `Subscription login expired: ${expired.map((p) => p.name).join(', ')}`) : L('订阅、API、回退顺序', 'Subscriptions, API, fallback order')} onPress={() => nav.navigate('Models')} />
            <ListRow icon={<Palette size={20} color={t.cyan} />} title={L('形象', 'Look')} sub={L('光环样式和颜色', 'Halo style and color')} onPress={() => nav.navigate('Avatar')} last />
          </Card>

          <SectionLabel>{L('外观', 'Appearance')}</SectionLabel>
          <Segmented value={appearance} onChange={setAppearance} options={[{ value: 'system', label: L('跟随系统', 'System') }, { value: 'light', label: L('浅色', 'Light') }, { value: 'dark', label: L('深色', 'Dark') }]} />

          <SectionLabel>{L('语言', 'Language')}</SectionLabel>
          <Segmented<LangPref> value={pref} onChange={setPref} options={[{ value: 'system', label: L('跟随系统', 'System') }, { value: 'zh', label: '中文' }, { value: 'en', label: 'English' }]} />
        </View>
      </ScrollView>
    </Screen>
  );
}
