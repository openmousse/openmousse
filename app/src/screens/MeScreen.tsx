// 设置（从侧栏底部进来；2026-09-29 照 Claude 的设置页改版）：
// 账号卡（有账号的壳才有）→ 我的 claw（连过的几台，点「换到这台」切换）→ 这个助手的（连接器、档案、世界树、记忆……）→ 活动与安全 → 应用。
// 原来「我」页的东西都还在，只是分了组；服务器地址和令牌在「我的 claw」点进去。
import React, { useCallback, useEffect, useState } from 'react';
import { Alert, Platform, Pressable, ScrollView, View } from 'react-native';
import Constants from 'expo-constants';
import { useFocusEffect, useNavigation } from '@react-navigation/native';
import { agentName } from '../brand';
import {
  Activity, BookOpen, Brain, CalendarDays, Check, ClipboardList, Cpu, Globe, IdCard, Info, LogOut, Palette, Plug, Plus, Server, ShareIcon, ShieldCheck, SunMoon,
  TreeDeciduous, User,
} from '../components/icons';
import { LensAvatar } from '../components/LensAvatar';
import { Chevron, Dot, Group, GroupLabel, GroupNote, Row, SettingsHeader, Tile } from '../components/settings';
import { useSheet } from '../components/Sheet';
import { PullRefresh, Screen, T, showError } from '../components/ui';
import { accountsEnabled, currentAccount, initialsOf, loadAccount, onAccountChange, signOut, syncClaw, type AccountUser } from '../api/account';
import { appsApi, noApps, type AppSummary } from '../api/apps';
import { activeClawId, listClaws, pingClaw, switchToClaw, touchClaw, type Claw } from '../api/claws';
import { defaultBase, getBase } from '../api/base';
import { podFeatures } from '../api/podcast';
import { serverOutdated } from '../api/version';
import { L, useLang, type LangPref } from '../i18n';
import { useStore } from '../store';
import { space, useAppearance, useTheme } from '../theme';

/** 账号：登录着的用户（没有账号服务的壳一直是 null）。 */
export function useAccount(): { enabled: boolean; user: AccountUser | null } {
  const [user, setUser] = useState<AccountUser | null>(currentAccount());
  useEffect(() => {
    let live = true;
    loadAccount().then((u) => { if (live) setUser(u); }).catch(() => {});
    const off = onAccountChange((u) => setUser(u));
    return () => { live = false; off(); };
  }, []);
  return { enabled: accountsEnabled(), user };
}


export function MeScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const sheet = useSheet();
  const {
    avatar, profile, memories, connected, booting, authFailed, appName, tasks, security, models, journal, tree, connectors, reload, claw, server, refreshLive,
  } = useStore();
  const { appearance, setAppearance } = useAppearance();
  const { pref, setPref } = useLang();
  const acct = useAccount();
  const warn = security?.facts.filter((f) => f.tone === 'warn') ?? [];
  const expired = models?.providers.filter((p) => p.subscription && p.status !== 'ok') ?? [];
  const running = tasks.filter((x) => x.status === '进行中').length;
  const leaves = tree?.kind === 'ok' ? tree.data.counts : null;
  const conn = connectors?.kind === 'ok' ? connectors.data.counts : null;

  // 世界树和连接状态不在启动时读（见 store 的 STARTUP_KEYS）：第一次来这一页时读
  useEffect(() => { if (connected) reload('tree', 'connectors').catch(() => {}); }, [connected, reload]);
  // 朋友画像（播客记的，只有你看得到）：服务器有才显示
  const [people, setPeople] = useState(false);
  useEffect(() => { if (connected) podFeatures().then((f) => setPeople(f.people)).catch(() => {}); }, [connected]);

  // 连接器：连着的应用（服务器老没有这个接口就用原来「连接」的计数）
  const [apps, setApps] = useState<AppSummary[] | null>(null);
  const loadApps = useCallback(() => {
    if (!connected) return;
    appsApi.list().then((r) => setApps(r.apps)).catch((e) => { if (noApps(e)) setApps(null); });
  }, [connected]);

  // 我的 claw：这台设备连过的几台；正在用的那台顺手更新名字，别的几台看一眼在不在
  const [claws, setClaws] = useState<Claw[]>([]);
  const [alive, setAlive] = useState<Record<string, 'ok' | 'auth' | 'down'>>({});
  const loadClaws = useCallback(() => {
    // 网页版和服务同源、没有列表：只把这一台记进账号
    if (Platform.OS === 'web' && connected) syncClaw({ base: defaultBase(), name: appName, clawKind: claw.kind, clawName: claw.name }).catch(() => {});
    listClaws(appName).then(async (list) => {
      setClaws(list);
      const active = activeClawId(list);
      if (connected && active) {
        const c = list.find((x) => x.id === active);
        if (c) {
          await touchClaw(c.base, { name: appName, clawKind: claw.kind, clawName: claw.name });
          setClaws(await listClaws(appName));
          syncClaw({ base: c.base, name: appName, clawKind: claw.kind, clawName: claw.name }).catch(() => {});
        }
      }
      for (const c of list) {
        if (c.id === active) continue;
        pingClaw(c).then((s) => setAlive((a) => ({ ...a, [c.id]: s }))).catch(() => {});
      }
    }).catch(() => {});
  }, [appName, connected, claw.kind, claw.name]);

  useFocusEffect(useCallback(() => { loadClaws(); loadApps(); }, [loadClaws, loadApps]));

  const activeId = activeClawId(claws);
  const web = Platform.OS === 'web';

  const switchTo = (c: Claw) => {
    Alert.alert(L(`切换到「${c.name}」？`, `Switch to ${c.name}?`), L('此设备将改为连接该 claw，对话和记忆将切换为其上的数据。', "This device will use that claw: chats and memory switch to its own."), [
      { text: L('取消', 'Cancel'), style: 'cancel' },
      {
        text: L('切换', 'Switch'), onPress: async () => {
          try {
            await switchToClaw(c.id);
            refreshLive();
            nav.reset({ index: 0, routes: [{ name: 'Tabs' }] });
          } catch (e) { showError(L('切换失败', "Couldn't switch"), e); }
        },
      },
    ]);
  };

  const pick = <V extends string>(title: string, options: { value: V; label: string }[], value: V, onChange: (v: V) => void) => {
    sheet.open({
      title,
      content: (close) => (
        <Group style={{ marginHorizontal: 0 }}>
          {options.map((o, i) => (
            <Row key={o.value} title={o.label} first={i === 0} chevron={false} onPress={() => { onChange(o.value); close(); }}
              right={o.value === value ? <Check size={20} color={t.cyan} /> : undefined} />
          ))}
        </Group>
      ),
    });
  };
  const looks = [{ value: 'system' as const, label: L('跟随系统', 'System') }, { value: 'light' as const, label: L('浅色', 'Light') }, { value: 'dark' as const, label: L('深色', 'Dark') }];
  const langs: { value: LangPref; label: string }[] = [{ value: 'system', label: L('跟随系统', 'System') }, { value: 'zh', label: '中文' }, { value: 'en', label: 'English' }];
  const version = Constants.expoConfig?.version ?? '';

  const about = () => sheet.open({
    title: L('关于', 'About'),
    content: () => (
      <View style={{ gap: space.sm }}>
        <T v="body">{`${Constants.expoConfig?.name ?? 'OpenMousse'} ${version}`}</T>
        <T v="callout" color={t.ink2}>{L('开源（AGPL-3.0）：github.com/openmousse/openmousse。你的对话、记忆和数据均保存在你自己的 claw 上。',
          'Open source (AGPL-3.0): github.com/openmousse/openmousse. Your chats, memory and data are stored on your own claw.')}</T>
      </View>
    ),
  });

  const logout = () => {
    Alert.alert(L('退出登录？', 'Sign out?'), L('仅退出账号，此设备已连接的 claw 仍可正常使用。', 'This only signs out of your account; the claws on this device keep working.'), [
      { text: L('取消', 'Cancel'), style: 'cancel' },
      { text: L('退出', 'Sign out'), style: 'destructive', onPress: () => { signOut().then(() => nav.reset({ index: 0, routes: [{ name: 'Login' }] })).catch((e) => showError(L('退出失败', "Couldn't sign out"), e)); } },
    ]);
  };

  const connectedApps = (apps ?? []).filter((a) => a.status !== 'error');
  const appsNeedAuth = (apps ?? []).filter((a) => a.status === 'needs_auth').length;
  const outdated = connected && serverOutdated(server);  // 服务器比这版 app 旧：点进去有更新方法
  const clawStatus = connected ? (outdated ? L('在线 · 需要更新', 'Online · update needed') : L('在线', 'Online'))
    : booting ? L('正在连接…', 'Connecting…') : authFailed ? L('令牌无效', 'Invalid token') : L('无法连接', 'Unreachable');

  return (
    <Screen>
      <SettingsHeader title={L('设置', 'Settings')} onBack={nav.canGoBack() ? () => nav.goBack() : undefined} close />
      <ScrollView contentContainerStyle={{ paddingBottom: space.xxl, paddingTop: 4 }}
        refreshControl={<PullRefresh onRefresh={() => { loadClaws(); loadApps(); return reload('profile', 'tree', 'memories', 'journal', 'activity', 'tasks', 'security', 'models', 'connectors'); }} />}>

        {acct.enabled ? (
          <Group>
            {acct.user ? (
              <Pressable onPress={() => nav.navigate('Account')} accessibilityRole="button" accessibilityLabel={L(`账号：${acct.user.email}`, `Account: ${acct.user.email}`)}
                style={({ pressed }) => ({ opacity: pressed ? 0.6 : 1 })}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 14, padding: 16, paddingLeft: 18 }}>
                  <View style={{ width: 52, height: 52, borderRadius: 26, backgroundColor: t.surface2, alignItems: 'center', justifyContent: 'center' }}>
                    <T v="headline" style={{ fontSize: 17 }}>{initialsOf(acct.user)}</T>
                  </View>
                  <View style={{ flex: 1, minWidth: 0, gap: 3 }}>
                    <T v="headline" numberOfLines={1} style={{ fontSize: 17 }}>{acct.user.name || acct.user.email.split('@')[0]}</T>
                    <T v="callout" color={t.ink2} numberOfLines={1}>{acct.user.email}</T>
                  </View>
                  <Chevron />
                </View>
              </Pressable>
            ) : (
              <Row first icon={<User size={22} color={t.cyan} />} title={L(`登录${Constants.expoConfig?.name ?? ''}账号`, `Sign in to ${Constants.expoConfig?.name ?? 'your account'}`)} sub={L('通过邮箱验证码登录', 'Sign in with a code sent to your email')}
                onPress={() => nav.navigate('Login', { from: 'settings' })} />
            )}
          </Group>
        ) : null}

        <GroupLabel>{L('我的 claw', 'My claws')}</GroupLabel>
        <Group>
          {(web || !claws.length) ? (
            <Row first icon={<Tile size={36} bg={t.cyanSoft}><Server size={20} color={t.cyan} /></Tile>} title={appName}
              sub={`${claw.name} · ${clawStatus}`} right={<Dot tone={connected && !outdated ? 'good' : 'warn'} />} onPress={() => nav.navigate(connected ? 'Claw' : 'Connect')} />
          ) : claws.map((c, i) => {
            const active = c.id === activeId;
            const st = active ? (connected && !outdated ? 'good' : 'warn') : alive[c.id] === 'ok' ? 'good' : alive[c.id] ? 'warn' : 'off';
            const line = active ? clawStatus : alive[c.id] === 'ok' ? L('在线', 'Online') : alive[c.id] === 'auth' ? L('令牌无效', 'Invalid token') : alive[c.id] === 'down' ? L('无法连接', 'Unreachable') : '…';
            return (
              <Row key={c.id} first={i === 0}
                icon={<Tile size={36} bg={active ? t.cyanSoft : t.surface2}><Server size={20} color={active ? t.cyan : t.ink3} /></Tile>}
                title={c.name} sub={`${c.clawName || claw.name}${L(' · ', ' · ')}${line}`}
                right={active ? <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}><Dot tone={st} /><Check size={20} color={t.cyan} /></View> : <Dot tone={st} />}
                chevron={active}
                onPress={() => (active ? nav.navigate(connected ? 'Claw' : 'Connect') : switchTo(c))}
                label={active ? L(`${c.name}，使用中，${line}`, `${c.name}, in use, ${line}`) : L(`${c.name}，${line}，点击切换`, `${c.name}, ${line}, tap to switch`)} />
            );
          })}
          {web ? null : (
            <Row icon={<Plus size={22} color={t.cyan} />} title={L('添加 claw', 'Add a claw')} accent onPress={() => nav.navigate('Connect', { add: true, at: Date.now() })} />
          )}
        </Group>
        <GroupNote>{acct.enabled
          ? L('对话、记忆和连接器令牌均保存在 claw 上，不存储在账号中。', 'Chats, memory and connector tokens are stored on your claws, not in your account.')
          : L('对话、记忆和连接器令牌均保存在 claw 上。', 'Chats, memory and connector tokens are stored on your claws.')}</GroupNote>

        <GroupLabel>{agentName()}</GroupLabel>
        <Group>
          <Row first icon={<Plug size={22} color={appsNeedAuth || conn?.warn ? t.warn : t.cyan} />} title={L('连接器', 'Connectors')} onPress={() => nav.navigate('Connectors')}
            right={apps ? (
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                <View style={{ flexDirection: 'row' }}>
                  {connectedApps.slice(0, 3).map((a, i) => (
                    <View key={a.id} style={{ marginLeft: i ? -6 : 0, borderRadius: 8, borderWidth: 2, borderColor: t.surface }}>
                      <Tile size={22} mono={a.mono || a.name.slice(0, 1)} bg={a.bg} fg={a.fg} border={a.border} />
                    </View>
                  ))}
                </View>
                {connectedApps.length ? <T v="body" color={appsNeedAuth ? t.warn : t.ink2} style={{ fontSize: 16 }}>{String(connectedApps.length)}</T> : null}
              </View>
            ) : undefined}
            value={apps ? (connectedApps.length ? undefined : L('立即连接', 'Connect one')) : conn ? L(`${conn.ok} 个正常`, `${conn.ok} working`) : undefined}
            tone={apps && !connectedApps.length ? 'accent' : undefined} />
          <Row icon={<IdCard size={22} color={t.cyan} />} title={L('基础档案', 'Profile')} value={profile.length ? L(`${profile.length} 条`, `${profile.length}`) : undefined} onPress={() => nav.navigate('Identity')} />
          <Row icon={<TreeDeciduous size={22} color={t.cyan} />} title={L('世界树', 'Memory tree')}
            value={leaves ? (leaves.pending ? L(`${leaves.pending} 条待确认`, `${leaves.pending} waiting`) : L(`${leaves.total} 片叶子`, `${leaves.total} leaves`)) : undefined}
            tone={leaves?.pending ? 'accent' : undefined} onPress={() => nav.navigate('Tree')} />
          <Row icon={<Brain size={22} color={t.cyan} />} title={L('记忆', 'Memory')} value={memories.length ? L(`${memories.length} 条`, `${memories.length}`) : undefined} onPress={() => nav.navigate('Memory')} />
          <Row icon={<BookOpen size={22} color={t.cyan} />} title={L('日志', 'Journal')} value={journal.length ? L(`${journal.length} 条`, `${journal.length}`) : undefined} onPress={() => nav.navigate('Journal')} />
          {people ? <Row icon={<User size={22} color={t.cyan} />} title={L('朋友画像', 'Friend notes')} onPress={() => nav.navigate('People')} /> : null}
          <Row icon={<Palette size={22} color={t.cyan} />} title={L('形象', 'Look')} right={<LensAvatar size={24} config={avatar} />} onPress={() => nav.navigate('Avatar')} />
          <Row icon={<Cpu size={22} color={expired.length ? t.warn : t.cyan} />} title={L('模型与用量', 'Models & usage')}
            value={expired.length ? L('订阅登录已过期', 'Sign-in expired') : claw.kind !== 'openclaw' ? claw.name : undefined} tone={expired.length ? 'warn' : undefined}
            onPress={() => nav.navigate('Models')} />
        </Group>

        <GroupLabel>{L('活动与安全', 'Activity & safety')}</GroupLabel>
        <Group>
          <Row first icon={<Activity size={22} color={t.cyan} />} title={L('活动记录', 'Activity')} onPress={() => nav.navigate('Activity')} />
          {claw.caps.tasks ? <Row icon={<ClipboardList size={22} color={t.cyan} />} title={L('任务', 'Tasks')} value={running ? L(`${running} 个运行中`, `${running} running`) : undefined} tone="accent" onPress={() => nav.navigate('Tasks')} /> : null}
          <Row icon={<ShieldCheck size={22} color={warn.length ? t.warn : t.cyan} />} title={L('安全', 'Security')}
            value={security ? (warn.length ? L(`${warn.length} 项需关注`, `${warn.length} to check`) : L('全部正常', 'All good')) : undefined} tone={warn.length ? 'warn' : 'good'}
            onPress={() => nav.navigate('Security')} />
          <Row icon={<ShareIcon size={22} color={t.cyan} />} title={L('已分享的链接', 'Shared links')} onPress={() => nav.navigate('Shares')} />
        </Group>

        <GroupLabel>{L('应用', 'App')}</GroupLabel>
        <Group>
          <Row first icon={<SunMoon size={22} color={t.cyan} />} title={L('外观', 'Appearance')} value={looks.find((o) => o.value === appearance)?.label}
            onPress={() => pick(L('外观', 'Appearance'), looks, appearance, setAppearance)} />
          <Row icon={<Globe size={22} color={t.cyan} />} title={L('语言', 'Language')} value={langs.find((o) => o.value === pref)?.label}
            onPress={() => pick(L('语言', 'Language'), langs, pref, setPref)} />
          <Row icon={<CalendarDays size={22} color={t.cyan} />} title={L('日程订阅', 'Calendar feed')} value={L('iPhone 日历', 'iPhone Calendar')} onPress={() => nav.navigate('ScheduleFeed')} />
          <Row icon={<Info size={22} color={t.cyan} />} title={L('关于', 'About')} value={version} onPress={about} />
        </Group>

        {acct.enabled && acct.user ? (
          <Group style={{ marginTop: 26 }}>
            <Row first icon={<LogOut size={22} color={t.bad} />} title={L('退出登录', 'Sign out')} danger onPress={logout} />
          </Group>
        ) : null}
        <T v="caption" color={t.ink3} style={{ textAlign: 'center', marginTop: 20, fontWeight: '400' }}>
          {L(`${Constants.expoConfig?.name ?? 'OpenMousse'} ${version} · 开源（AGPL-3.0）`, `${Constants.expoConfig?.name ?? 'OpenMousse'} ${version} · Open source (AGPL-3.0)`)}
        </T>
        {getBase() && !connected && !booting ? (
          <View style={{ marginTop: space.md, alignItems: 'center' }}>
            <Pressable onPress={() => nav.navigate('Connect')} accessibilityRole="button"><T v="callout" color={t.cyan}>{L('无法连接？检查地址和令牌 →', "Can't connect? Check the address and token →")}</T></Pressable>
          </View>
        ) : null}
      </ScrollView>
    </Screen>
  );
}
