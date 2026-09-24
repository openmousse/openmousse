import React from 'react';
import { agentName } from '../brand';
import { Pressable, RefreshControl, ScrollView, View } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { Activity, BookOpen, Brain, ClipboardList, Cpu, IdCard, Palette, Server, ShieldCheck } from '../components/icons';
import { LensAvatar } from '../components/LensAvatar';
import { Card, LargeHeader, ListRow, Pill, Screen, SectionLabel, Segmented, T } from '../components/ui';
import { useStore, type DataKey } from '../store';
import { space, useAppearance, useTheme } from '../theme';

export function MeScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { avatar, profile, memories, activity, connected, booting, authFailed, appName, tasks, security, models, journal, loading, reload } = useStore();
  const { appearance, setAppearance } = useAppearance();
  const warn = security?.facts.filter((f) => f.tone === 'warn') ?? [];
  const expired = models?.providers.filter((p) => p.subscription && p.status !== 'ok') ?? [];
  const running = tasks.filter((x) => x.status === '进行中').length;
  return (
    <Screen>
      <ScrollView contentContainerStyle={{ paddingBottom: space.xxl }} refreshControl={<RefreshControl refreshing={['profile', 'memories', 'journal', 'activity', 'tasks', 'security', 'models'].some((k) => loading[k as DataKey])} onRefresh={() => reload('profile', 'memories', 'journal', 'activity', 'tasks', 'security', 'models')} />}>
        <LargeHeader title="我" />
        <View style={{ paddingHorizontal: space.lg }}>
          <Pressable onPress={() => nav.navigate('Avatar')} accessibilityRole="button" accessibilityLabel={`定制 ${agentName()} 的形象`}>
            <Card style={{ flexDirection: 'row', alignItems: 'center', gap: space.lg }}>
              <LensAvatar size={64} config={avatar} />
              <View style={{ flex: 1, gap: 4 }}>
                <T v="title">{agentName()}</T>
                <T v="callout" color={t.ink2}>不是工具，是搭档。点这里换它的样子。</T>
                <View style={{ flexDirection: 'row' }}>{connected ? <Pill label="已连接服务器" tone="good" /> : <Pill label={booting ? '正在连接…' : authFailed ? '令牌不对' : '未连接服务器'} tone="warn" />}</View>
              </View>
            </Card>
          </Pressable>

          <SectionLabel>{`${agentName()} 知道的`}</SectionLabel>
          <Card style={{ paddingVertical: space.xs }}>
            <ListRow icon={<IdCard size={20} color={t.cyan} />} title="基础档案" sub={profile.length ? `${profile.length} 条，所有 agent 共用，可以直接改` : '所有 agent 共用'} onPress={() => nav.navigate('Identity')} />
            <ListRow icon={<Brain size={20} color={t.cyan} />} title="记忆" sub={memories.length ? `长期记忆 ${memories.length} 条，可以逐条忘记` : '长期记忆，可以逐条忘记'} onPress={() => nav.navigate('Memory')} />
            <ListRow icon={<BookOpen size={20} color={t.cyan} />} title="日志" sub={journal.length ? `${journal.length} 条感受、想法和决定` : '感受、想法、决定，在对话里说就会记'} onPress={() => nav.navigate('Journal')} last />
          </Card>

          <SectionLabel>{`${agentName()} 做过的`}</SectionLabel>
          <Card style={{ paddingVertical: space.xs }}>
            <ListRow icon={<Activity size={20} color={t.cyan} />} title="活动记录" sub={activity[0] ? `${activity[0].time} · ${activity[0].text}` : '每一次回复、定时任务和你的操作'} onPress={() => nav.navigate('Activity')} />
            <ListRow icon={<ClipboardList size={20} color={t.cyan} />} title="任务" sub={tasks.length ? `${tasks.length} 个子会话${running ? `，${running} 个在跑` : ''}，能看过程` : '派出去的子会话'} onPress={() => nav.navigate('Tasks')} />
            <ListRow icon={<ShieldCheck size={20} color={warn.length ? t.warn : t.cyan} />} title="安全" sub={security ? (warn.length ? `${warn.length} 项要注意：${warn.map((f) => f.title).join('、')}` : '都正常') : '服务器上的实测状态'} onPress={() => nav.navigate('Security')} last />
          </Card>

          <SectionLabel>设置</SectionLabel>
          <Card style={{ paddingVertical: space.xs }}>
            <ListRow icon={<Server size={20} color={connected ? t.cyan : t.warn} />} title="服务器" sub={connected ? `已连接 · ${appName}` : authFailed ? '令牌不对，点这里改' : '地址和接入令牌'} onPress={() => nav.navigate('Connect')} />
            <ListRow icon={<Cpu size={20} color={expired.length ? t.warn : t.cyan} />} title="模型与计费" sub={expired.length ? `${expired.map((p) => p.name).join('、')} 订阅登录已过期` : '订阅、API、回退顺序'} onPress={() => nav.navigate('Models')} />
            <ListRow icon={<Palette size={20} color={t.cyan} />} title="形象" sub="光环样式和颜色" onPress={() => nav.navigate('Avatar')} last />
          </Card>

          <SectionLabel>外观</SectionLabel>
          <Segmented value={appearance} onChange={setAppearance} options={[{ value: 'system', label: '跟随系统' }, { value: 'light', label: '浅色' }, { value: 'dark', label: '深色' }]} />
        </View>
      </ScrollView>
    </Screen>
  );
}
