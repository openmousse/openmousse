// 设置（从对话页右上角进来；2026-09-29 照 Claude 的设置页改版，2026-10-19 第 9 批照设计指挥部第 17 张改排法，原来的东西一样都不删）：
// 顶上一张状态卡（这台 claw 在不在线、东西都存在哪）→ 连接（授权登录、MCP 服务、技能、记忆、手）→ 密钥（模型、按用途选模型、向量、搜索、
// 语音、用量）→ 通用（安全、外观）→ 其他（SettingsOtherScreen：账号、我的 claw、名字、形象、活动、任务、分享、语言、通知、
// 硬件、日程订阅、关于……）。数字从 /api/setup 总览和 /api/setup/usage 读（只认主人的设备）。
// 「连接」里的「记忆」点了去底栏的记忆页（2026-10-20）；基础档案、记忆条目、日志、朋友画像也挪进了记忆页的「全部记忆」，路由照旧。
// 老服务器（features 里没有 setup）不放新的那几页：只有授权登录、记忆、按用途选模型、安全、外观和其他。
// MCP 服务、技能（第 5 批）、语音（第 3 批）是别的批的页：navigation 里有那个路由才能点进去，没有就只显示数、不带箭头；语音要服务器有语音设置才显示。
import React, { useCallback, useEffect, useState, useSyncExternalStore } from 'react';
import { Alert, Platform, Pressable, ScrollView, View } from 'react-native';
import Constants from 'expo-constants';
import { useFocusEffect, useNavigation } from '@react-navigation/native';
import { agentName } from '../brand';
import { Activity, Bell, Bluetooth, CalendarDays, ChartColumn, Check, ClipboardList, Globe, Info, LogOut, Palette, Plus, Server, ShareIcon, Sparkles, User } from '../components/icons';
import { LensAvatar } from '../components/LensAvatar';
import { Mark } from '../components/Mark';
import { Chevron, Dot, Group, GroupLabel, GroupNote, Row, SettingsHeader, Tile } from '../components/settings';
import { GroupHead, KitCard, KitRow } from '../components/setupKit';
import { useSheet } from '../components/Sheet';
import { PullRefresh, Screen, T, showError } from '../components/ui';
import { accountsEnabled, currentAccount, initialsOf, loadAccount, onAccountChange, signOut, syncClaw, type AccountUser } from '../api/account';
import { appsApi, noApps, type AppSummary } from '../api/apps';
import { activeClawId, listClaws, pingClaw, switchToClaw, touchClaw, type Claw } from '../api/claws';
import { defaultBase, getBase } from '../api/base';
import { setupApi, type Overview, type Purposes, type SearchView, type Usage } from '../api/setup';
import { flipperState, onFlipper } from '../hardware/flipper';
import { serverOutdated, supports } from '../api/version';
import { L, useLang, type LangPref } from '../i18n';
import { hasRoute } from '../navigation';
import { useStore } from '../store';
import { space, useAppearance, useTheme } from '../theme';
import { money, plural } from './SettingsCenter';
import { embedName } from './SetupScreens';

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

const looksOf = () => [{ value: 'system' as const, label: L('跟随系统', 'System') }, { value: 'light' as const, label: L('浅色', 'Light') }, { value: 'dark' as const, label: L('深色', 'Dark') }];
const bare = (ref: string | null | undefined) => (ref ? ref.split('/').slice(1).join('/') || ref : '');

/** 从一组选项里挑一个（外观、语言）：弹层里一行一个，选中的打勾。 */
function usePicker() {
  const t = useTheme();
  const sheet = useSheet();
  return <V extends string>(title: string, options: { value: V; label: string }[], value: V, onChange: (v: V) => void) => {
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
}

export function MeScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const pick = usePicker();
  const { connected, booting, authFailed, security, models, tree, reload, claw, server } = useStore();
  const { appearance, setAppearance } = useAppearance();
  const warn = security?.facts.filter((f) => f.tone === 'warn') ?? [];
  const expired = models?.providers.filter((p) => p.subscription && p.status !== 'ok') ?? [];
  const leaves = tree?.kind === 'ok' ? tree.data.counts : null;
  const setup = connected && supports('setup');
  const builtin = claw.kind === 'builtin';

  // 世界树不跟启动那一批读（见 store 的 STARTUP_KEYS，那一批读完才读）：这一页再读一次，「记忆」那行的数是新的
  useEffect(() => { if (connected) reload('tree').catch(() => {}); }, [connected, reload]);

  // 数字：/api/setup 总览、用量、按用途（连上了几家、主对话用哪个）、搜索（用的那家叫什么）。老服务器没有：授权登录照旧读 /api/apps
  const [ov, setOv] = useState<Overview | null>(null);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [pv, setPv] = useState<Purposes | null>(null);
  const [search, setSearch] = useState<SearchView | null>(null);
  const [setupErr, setSetupErr] = useState<string | null>(null);
  const [apps, setApps] = useState<AppSummary[] | null>(null);
  const load = useCallback(() => {
    if (!connected) return Promise.resolve();
    if (!supports('setup')) return appsApi.list().then((r) => setApps(r.apps)).catch((e) => { if (noApps(e)) setApps(null); });
    return Promise.all([setupApi.overview(), setupApi.usage(30).catch(() => null), setupApi.purposes(false).catch(() => null), setupApi.search().catch(() => null)])
      .then(([o, u, p, s]) => { setOv(o); setUsage(u); setPv(p); setSearch(s); setSetupErr(null); })
      .catch((e) => setSetupErr(e instanceof Error ? e.message : String(e)));
  }, [connected]);
  useFocusEffect(useCallback(() => { load(); }, [load]));

  const outdated = connected && serverOutdated(server);  // 服务器比这版 app 旧：点进去有更新方法
  const clawStatus = connected ? (outdated ? L('在线 · 需要更新', 'Online · update needed') : L('在线', 'Online'))
    : booting ? L('正在连接…', 'Connecting…') : authFailed ? L('令牌无效', 'Invalid token') : L('无法连接', 'Unreachable');
  const clawLine = builtin ? L(`${agentName()} 自带内核`, `${agentName()} built-in engine`) : claw.name;

  const conn = ov?.layers.connections;
  const appsOn = conn?.apps ?? (apps ? apps.filter((a) => a.status === 'connected').length : null);
  const appsNeedAuth = (apps ?? []).some((a) => a.status === 'needs_auth');
  const handsN = ov?.layers.hands.agents?.filter((a) => a.files || a.webFetch || a.webSearch || a.sandbox || a.hostExec).length;
  const memories = ov?.layers.memory.tree?.memories ?? leaves?.total ?? null;
  const external = (layer: { status?: string } | undefined) => !builtin && (layer?.status === 'external' || !layer);
  const by = L(`由 ${claw.name} 管`, `Managed by ${claw.name}`);
  const providersN = pv ? Object.keys(pv.providers).length : null;
  const chatRef = ov?.layers.models.chat ?? null;
  const emb = ov?.layers.embeddings;
  const embText = emb?.current ? embedName(emb.current, pv?.choices.embeddings ?? []) : emb?.status === 'warn' ? L('要处理', 'Needs attention') : L('没开', 'Off');
  const searchName = search?.provider ? search.providers.find((x) => x.id === search.provider)?.name ?? search.provider : null;
  const searchText = searchName ?? (search?.off ? L('关着', 'Off') : L('没设', 'Not set'));
  const spent = usage ? usage.total.cost + usage.embeddings.total.cost : null;
  const usageText = usage ? [money(spent ?? 0), usage.total.planTurns ? L(`套餐 ${usage.total.planTurns} 轮`, plural(usage.total.planTurns, 'plan turn')) : null].filter(Boolean).join(' · ') : null;
  const go = (route: string) => (hasRoute(route) ? () => nav.navigate(route) : undefined);  // 别的批的页：路由在才能点

  return (
    <Screen>
      <SettingsHeader title={L('设置', 'Settings')} onBack={nav.canGoBack() ? () => nav.goBack() : undefined} close />
      <ScrollView contentContainerStyle={{ paddingBottom: space.xxl, paddingTop: 4, paddingHorizontal: space.lg }}
        refreshControl={<PullRefresh onRefresh={() => Promise.all([load(), reload('profile', 'tree', 'memories', 'journal', 'activity', 'tasks', 'security', 'models', 'connectors')])} />}>

        <Pressable onPress={() => nav.navigate(connected ? 'Claw' : 'Connect')} accessibilityRole="button"
          accessibilityLabel={`${clawLine}${L('，', ', ')}${clawStatus}`} style={({ pressed }) => ({ opacity: pressed ? 0.7 : 1 })}>
          <KitCard style={{ padding: space.lg, gap: 6 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
              <Dot tone={connected && !outdated ? 'good' : 'warn'} />
              <T v="headline" numberOfLines={1} style={{ flex: 1 }}>{`${clawLine} · ${clawStatus}`}</T>
              <Chevron />
            </View>
            <T v="caption" color={t.ink3} style={{ fontWeight: '400', lineHeight: 17 }}>{L('对话、记忆和密钥都存在这台 claw 上。', 'Chats, memory and keys are all stored on this claw.')}</T>
          </KitCard>
        </Pressable>
        {setupErr ? <T v="caption" color={t.warn} style={{ marginTop: space.sm, paddingHorizontal: space.xs, fontWeight: '400' }}>{setupErr}</T> : null}

        <GroupHead title={L('连接', 'Connections')} note={L('接上外面的服务、工具和你的资料', 'Outside services, tools and your own data')} />
        <KitCard>
          <KitRow first title={L('授权登录', 'Sign-ins')} value={appsOn != null ? L(`已连 ${appsOn} 个`, `${appsOn} connected`) : null} tone={appsNeedAuth ? 'warn' : undefined}
            onPress={() => nav.navigate('Connectors')} />
          {setup && conn ? (
            <KitRow title={L('MCP 服务', 'MCP servers')} onPress={go('McpServers')} tone={conn.mcpErrors ? 'warn' : undefined}
              value={conn.mcpErrors ? L(`${conn.mcp ?? 0} 个 / ${conn.mcpErrors} 个出错`, `${conn.mcp ?? 0} / ${conn.mcpErrors} failing`) : L(`${conn.mcp ?? 0} 个`, `${conn.mcp ?? 0}`)} />
          ) : null}
          {setup && conn ? <KitRow title={L('技能', 'Skills')} value={L(`${conn.skills ?? 0} 个`, `${conn.skills ?? 0}`)} onPress={go('Skills')} /> : null}
          <KitRow title={L('记忆', 'Memory')} tone={leaves?.pending ? 'warn' : undefined} onPress={() => nav.navigate('Tabs', { screen: '记忆' }, { pop: true })}
            value={leaves?.pending ? L(`${leaves.pending} 条待确认`, `${leaves.pending} waiting`) : memories != null ? L(`${memories} 条`, `${memories}`) : null} />
          {setup ? (
            <KitRow title={L('手', 'Hands')} disabled={!builtin} value={!builtin ? by : handsN != null ? L(`${handsN} 个 Agent`, `${handsN} Agent${handsN === 1 ? '' : 's'}`) : null}
              onPress={builtin ? () => nav.navigate('SettingsHands') : undefined} />
          ) : null}
        </KitCard>

        <GroupHead title={L('密钥', 'Keys')} note={L('模型，和其他要用密钥的服务', 'Models, and other services that need a key')} />
        <KitCard>
          {setup ? (
            <KitRow first title={L('模型', 'Models')} disabled={external(ov?.layers.models)}
              value={external(ov?.layers.models) ? by : providersN != null ? L(`${providersN} 家`, `${providersN}`) : null}
              tone={ov?.layers.models.status === 'missing' ? 'warn' : undefined}
              onPress={external(ov?.layers.models) ? undefined : () => nav.navigate('SettingsProviders')} />
          ) : null}
          <KitRow first={!setup} title={L('按用途选模型', 'Models by purpose')} onPress={() => nav.navigate('Models')}
            value={expired.length ? L('订阅登录已过期', 'Sign-in expired') : builtin && chatRef ? L(`主对话 ${bare(chatRef)}`, `Main chat ${bare(chatRef)}`) : !builtin ? claw.name : null}
            tone={expired.length ? 'warn' : undefined} />
          {setup ? (
            <KitRow title={L('向量', 'Embeddings')} disabled={!builtin} value={!builtin ? by : embText} tone={emb?.status === 'warn' ? 'warn' : undefined}
              onPress={builtin ? () => nav.navigate('SettingsMemory') : undefined} />
          ) : null}
          {setup ? (
            <KitRow title={L('搜索', 'Search')} disabled={!builtin} value={!builtin ? by : searchText}
              onPress={builtin ? () => nav.navigate('SettingsSearch') : undefined} />
          ) : null}
          {setup && supports('voice') ? <KitRow title={L('语音', 'Voice')} onPress={go('Voice')} /> : null}
          {setup ? <KitRow title={L('用量', 'Usage')} value={usageText} onPress={() => nav.navigate('SettingsUsage')} /> : null}
        </KitCard>

        <GroupHead title={L('通用', 'General')} />
        <KitCard>
          <KitRow first title={L('安全', 'Security')} value={security ? (warn.length ? L(`${warn.length} 条提醒`, `${warn.length} to check`) : L('正常', 'All good')) : null}
            tone={warn.length ? 'warn' : undefined} onPress={() => nav.navigate('Security')} />
          <KitRow title={L('外观', 'Appearance')} value={looksOf().find((o) => o.value === appearance)?.label}
            onPress={() => pick(L('外观', 'Appearance'), looksOf(), appearance, setAppearance)} />
        </KitCard>

        <GroupHead title={L('其他', 'More')} />
        <KitCard>
          <KitRow first title={L('账号、我的 claw 和更多', 'Account, claws and more')}
            sub={L('形象、活动、任务、分享、语言、通知、硬件、日程订阅、关于', 'Look, activity, tasks, sharing, language, notifications, hardware, calendar feed, about')}
            onPress={() => nav.navigate('SettingsOther')} />
        </KitCard>

        <T v="caption" color={t.ink3} style={{ textAlign: 'center', marginTop: 20, fontWeight: '400' }}>
          {L(`${Constants.expoConfig?.name ?? 'OpenMousse'} ${Constants.expoConfig?.version ?? ''} · 开源（AGPL-3.0）`, `${Constants.expoConfig?.name ?? 'OpenMousse'} ${Constants.expoConfig?.version ?? ''} · Open source (AGPL-3.0)`)}
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

/** 设置 → 其他：账号卡、我的 claw（连过的几台，点「换到这台」切换）、这个助手的（名字和性格、形象）、活动、应用（语言、通知、硬件……）。
 *  原来设置首页上的这些一样都没删，只是收到这一页；基础档案、记忆条目、日志、朋友画像 2026-10-20 挪进了记忆页（底栏第四格）。 */
export function SettingsOtherScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const sheet = useSheet();
  const pick = usePicker();
  const { avatar, connected, booting, authFailed, appName, tasks, claw, server, refreshLive } = useStore();
  const { pref, setPref } = useLang();
  const acct = useAccount();
  const flipper = useSyncExternalStore(onFlipper, flipperState).device;  // 硬件页记住的那台 Flipper
  const running = tasks.filter((x) => x.status === '进行中').length;

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
  useFocusEffect(useCallback(() => { loadClaws(); }, [loadClaws]));

  const activeId = activeClawId(claws);
  const web = Platform.OS === 'web';
  const naming = supports('naming') && claw.kind === 'builtin' && hasRoute('SetupName');

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

  const langs: { value: LangPref; label: string }[] = [{ value: 'system', label: L('跟随系统', 'System') }, { value: 'zh', label: '中文' }, { value: 'en', label: 'English' }];
  const version = Constants.expoConfig?.version ?? '';

  const about = () => sheet.open({
    title: L('关于', 'About'),
    content: (close) => (
      <View style={{ gap: space.sm }}>
        <Mark size={64} decorative />
        <T v="body">{`${Constants.expoConfig?.name ?? 'OpenMousse'} ${version}`}</T>
        <T v="callout" color={t.ink2}>{L('开源（AGPL-3.0）：github.com/openmousse/openmousse。你的对话、记忆和数据均保存在你自己的 claw 上。',
          'Open source (AGPL-3.0): github.com/openmousse/openmousse. Your chats, memory and data are stored on your own claw.')}</T>
        <Group style={{ marginHorizontal: 0, marginTop: space.sm }}>
          <Row first icon={<ChartColumn size={22} color={t.cyan} />} title={L('可视化素材库', 'Chart kit')} onPress={() => { close(); nav.navigate('VizKit'); }} />
        </Group>
      </View>
    ),
  });

  const logout = () => {
    Alert.alert(L('退出登录？', 'Sign out?'), L('仅退出账号，此设备已连接的 claw 仍可正常使用。', 'This only signs out of your account; the claws on this device keep working.'), [
      { text: L('取消', 'Cancel'), style: 'cancel' },
      { text: L('退出', 'Sign out'), style: 'destructive', onPress: () => { signOut().then(() => nav.reset({ index: 0, routes: [{ name: 'Login' }] })).catch((e) => showError(L('退出失败', "Couldn't sign out"), e)); } },
    ]);
  };

  const outdated = connected && serverOutdated(server);
  const clawStatus = connected ? (outdated ? L('在线 · 需要更新', 'Online · update needed') : L('在线', 'Online'))
    : booting ? L('正在连接…', 'Connecting…') : authFailed ? L('令牌无效', 'Invalid token') : L('无法连接', 'Unreachable');

  return (
    <Screen>
      <SettingsHeader title={L('其他', 'More')} onBack={() => nav.goBack()} />
      <ScrollView contentContainerStyle={{ paddingBottom: space.xxl, paddingTop: 4 }} refreshControl={<PullRefresh onRefresh={() => { loadClaws(); }} />}>
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
          {naming ? (
            <Row first icon={<Sparkles size={22} color={t.cyan} />} title={L('名字和性格', 'Name and character')} value={agentName()} onPress={() => nav.navigate('SetupName', { from: 'settings' })} />
          ) : null}
          <Row first={!naming} icon={<Palette size={22} color={t.cyan} />} title={L('形象', 'Look')} right={<LensAvatar size={24} config={avatar} />} onPress={() => nav.navigate('Avatar')} />
        </Group>
        <GroupNote>{L('基础档案、记忆条目、日志和朋友画像在底栏的「记忆」里。', 'Profile, memory notes, journal and friend notes are on the Memory tab.')}</GroupNote>

        <GroupLabel>{L('活动', 'Activity')}</GroupLabel>
        <Group>
          <Row first icon={<Activity size={22} color={t.cyan} />} title={L('活动记录', 'Activity')} onPress={() => nav.navigate('Activity')} />
          {claw.caps.tasks ? <Row icon={<ClipboardList size={22} color={t.cyan} />} title={L('任务', 'Tasks')} value={running ? L(`${running} 个运行中`, `${running} running`) : undefined} tone="accent" onPress={() => nav.navigate('Tasks')} /> : null}
          <Row icon={<ShareIcon size={22} color={t.cyan} />} title={L('已分享的链接', 'Shared links')} onPress={() => nav.navigate('Shares')} />
        </Group>

        <GroupLabel>{L('应用', 'App')}</GroupLabel>
        <Group>
          <Row first icon={<Globe size={22} color={t.cyan} />} title={L('语言', 'Language')} value={langs.find((o) => o.value === pref)?.label}
            onPress={() => pick(L('语言', 'Language'), langs, pref, setPref)} />
          {supports('friends.prefs') ? <Row icon={<Bell size={22} color={t.cyan} />} title={L('通知', 'Notifications')} onPress={() => nav.navigate('Notify')} /> : null}
          <Row icon={<Bluetooth size={22} color={t.cyan} />} title={L('硬件', 'Hardware')} value={flipper?.name} onPress={() => nav.navigate('Hardware')} />
          <Row icon={<CalendarDays size={22} color={t.cyan} />} title={L('日程订阅', 'Calendar feed')} value={L('iPhone 日历', 'iPhone Calendar')} onPress={() => nav.navigate('ScheduleFeed')} />
          <Row icon={<Info size={22} color={t.cyan} />} title={L('关于', 'About')} value={version} onPress={about} />
        </Group>

        {acct.enabled && acct.user ? (
          <Group style={{ marginTop: 26 }}>
            <Row first icon={<LogOut size={22} color={t.bad} />} title={L('退出登录', 'Sign out')} danger onPress={logout} />
          </Group>
        ) : null}
      </ScrollView>
    </Screen>
  );
}
