// 连接器的几页（2026-09-29）：添加（目录，照 Manus 的「插件」页：能帮你做的 + 分类）、一个连接器的详情（工具权限、哪些 Agent 能用、
// 重新授权、断开）、自定义（填一个 MCP 地址）。OAuth 的连接：点「连接」→ 服务器给授权页地址 → 跳浏览器 → 授权完跳回
// <scheme>://oauth/callback（navigation.tsx 的 finishOAuth 接住，交给服务器换令牌，再打开这里的详情页）。
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, Alert, Linking, Pressable, ScrollView, StyleSheet, Switch, TextInput, View } from 'react-native';
import { useFocusEffect, useNavigation, useRoute } from '@react-navigation/native';
import { ArrowUpRight, Check, Lock, Plus, RefreshCw, Search } from '../components/icons';
import { Chip, Count, Group, GroupLabel, GroupNote, Row, SettingsHeader, Tile } from '../components/settings';
import { useSheet } from '../components/Sheet';
import { PullRefresh, Screen, Segmented, T, showError } from '../components/ui';
import {
  appsApi, descOf, oauthHere, type AppDetail, type AppLevel, type AppsList, type AppSummary, type CatalogEntry,
} from '../api/apps';
import { httpStatus } from '../api/base';
import { L } from '../i18n';
import { useStore } from '../store';
import { radius, space, type, useTheme } from '../theme';

const zh = () => L('zh', 'en') === 'zh';
const levels = (): { value: AppLevel; label: string }[] => [
  { value: 'auto', label: L('自动', 'Auto') }, { value: 'ask', label: L('需确认', 'Ask me') }, { value: 'off', label: L('关闭', 'Off') },
];
const levelName = (v: AppLevel) => levels().find((x) => x.value === v)?.label ?? v;

/** 应用的图标块：目录给的字和颜色；自定义的用名字的头一个字。 */
export function AppTile({ a, size = 36 }: { a: { name: string; mono?: string | null; bg?: string | null; fg?: string | null; border?: string | boolean | null }; size?: number }) {
  return <Tile size={size} mono={a.mono || a.name.slice(0, 1).toUpperCase()} bg={a.bg} fg={a.fg} border={a.border} />;
}

export function statusChip(a: AppSummary) {
  if (a.status === 'needs_auth') return <Chip label={L('需重新授权', 'Reconnect')} tone="warn" />;
  if (a.status === 'error') return <Chip label={L('错误', 'Error')} tone="bad" />;
  return a.toolCount ? <Count n={a.toolCount} label={L(`${a.toolCount} 个工具`, `${a.toolCount} tools`)} /> : <Chip label={L('已连接', 'Connected')} tone="good" />;
}

/**
 * 连目录里的一个：要令牌的去自定义页填；OAuth 的跳浏览器授权（回来由 navigation.tsx 接）；不用授权的当场连上、打开详情。
 * 网页版接不住授权页跳回来，OAuth 的请在手机 app 里连。
 */
export async function connectCatalog(c: CatalogEntry, nav: { navigate: (s: string, p?: object) => void }): Promise<void> {
  if (c.auth === 'token') { nav.navigate('CustomApp', { name: c.name, url: c.url, auth: 'token', hint: c.hint ?? '' }); return; }
  if (c.auth === 'oauth' && !oauthHere()) {
    showError(L('请在手机 app 中连接', 'Connect it in the phone app'), L('授权页无法返回网页版：请在手机上打开「设置 → 连接器」，点击「连接」。', "The sign-in page can't return to the web version: on your phone open Settings → Connectors and tap Connect."));
    return;
  }
  try {
    const r = await appsApi.addCatalog(c.id);
    if (r.authorizeUrl) { await Linking.openURL(r.authorizeUrl); return; }
    nav.navigate('AppDetail', { id: r.app.id, fresh: true });
  } catch (e) {
    if (httpStatus(e) === 409) { nav.navigate('AppDetail', { id: c.id }); return; }
    showError(L(`连接 ${c.name} 失败`, `Couldn't connect ${c.name}`), e);
  }
}

// —— 添加（目录） ————————————————————————————————————————————————————————————

/** 目录里认得的几个，给「能帮你做的」那一排配一句用途（没装这个连接器的服务器上就不出现）。 */
const USES: Record<string, () => string> = {
  notion: () => L('从 Notion 笔记中查找答案', 'Answers from your Notion notes'),
  linear: () => L('将待办事项记录到 Linear', 'Turn to-dos into Linear issues'),
  github: () => L('查看代码仓库的近期变更', "Catch up on a repo's changes"),
  atlassian: () => L('搜索 Jira 和 Confluence', 'Search Jira and Confluence'),
  deepwiki: () => L('理解开源项目', 'Understand an open-source project'),
  context7: () => L('查阅最新开发文档', 'Look up current developer docs'),
};

export function AppGalleryScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const [data, setData] = useState<AppsList | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [q, setQ] = useState('');
  const [cat, setCat] = useState('all');
  const [busy, setBusy] = useState<string | null>(null);
  const load = useCallback(() => appsApi.list().then((r) => { setData(r); setErr(null); }).catch((e) => setErr(e instanceof Error ? e.message : String(e))), []);
  useFocusEffect(useCallback(() => { load(); }, [load]));

  const cats = useMemo(() => {
    const names: Record<string, string> = { common: L('常用', 'Popular'), dev: L('开发', 'Developer'), data: L('资料', 'Knowledge') };
    const seen = [...new Set((data?.catalog ?? []).map((c) => c.category))];
    return [{ value: 'all', label: L('全部', 'All') }, ...seen.map((c) => ({ value: c, label: names[c] ?? c }))];
  }, [data]);
  const list = (data?.catalog ?? []).filter((c) => (cat === 'all' || c.category === cat)
    && (!q.trim() || `${c.name} ${descOf(c.desc, zh())}`.toLowerCase().includes(q.trim().toLowerCase())));
  const featured = (data?.catalog ?? []).filter((c) => USES[c.id] && !c.installed).slice(0, 3);

  const connect = async (c: CatalogEntry) => {
    setBusy(c.id);
    try { await connectCatalog(c, nav); } finally { setBusy(null); load(); }
  };

  return (
    <Screen>
      <SettingsHeader title={L('添加连接器', 'Add a connector')} onBack={() => nav.goBack()} />
      <ScrollView contentContainerStyle={{ paddingBottom: space.xxl }} keyboardShouldPersistTaps="handled" refreshControl={<PullRefresh onRefresh={load} />}>
        <View style={[styles.search, { backgroundColor: t.surface }]}>
          <Search size={18} color={t.ink3} />
          <TextInput value={q} onChangeText={setQ} placeholder={L('搜索：笔记、代码、文档…', 'Search: notes, code, docs…')} placeholderTextColor={t.ink3}
            autoCorrect={false} style={[type.body, { flex: 1, color: t.ink, paddingVertical: 10 }]} accessibilityLabel={L('搜索连接器', 'Search connectors')} />
        </View>
        {err ? <T v="callout" color={t.bad} style={{ margin: space.lg }}>{err}</T> : null}
        {!data && !err ? <ActivityIndicator style={{ marginTop: space.xl }} color={t.ink3} /> : null}

        {featured.length && !q ? (
          <>
            <GroupLabel>{L('推荐用途', 'Suggested uses')}</GroupLabel>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ paddingHorizontal: space.lg, gap: 10 }}>
              {featured.map((c) => (
                <Pressable key={c.id} onPress={() => connect(c)} accessibilityRole="button" accessibilityLabel={`${USES[c.id]()}${L('，连接 ', ', connect ')}${c.name}`}
                  style={({ pressed }) => [styles.feature, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
                  <AppTile a={c} size={32} />
                  <T v="headline" numberOfLines={2}>{USES[c.id]()}</T>
                  <T v="callout" color={t.ink2}>{L(`使用 ${c.name}`, `With ${c.name}`)}</T>
                </Pressable>
              ))}
            </ScrollView>
          </>
        ) : null}

        {cats.length > 2 ? (
          <View style={{ paddingHorizontal: space.lg, marginTop: space.lg }}>
            <Segmented value={cat} onChange={setCat} options={cats} />
          </View>
        ) : null}

        {data ? (
          <Group style={{ marginTop: space.md }}>
            {list.map((c, i) => (
              <Row key={c.id} first={i === 0} icon={<AppTile a={c} />} title={c.name} sub={descOf(c.desc, zh()) || undefined} chevron={false}
                onPress={c.installed ? () => nav.navigate('AppDetail', { id: c.id }) : () => connect(c)}
                label={c.installed ? L(`${c.name}，已连接`, `${c.name}, connected`) : L(`连接 ${c.name}`, `Connect ${c.name}`)}
                right={busy === c.id ? <ActivityIndicator color={t.ink3} /> : c.installed ? <Check size={20} color={t.good} /> : (
                  <View style={[styles.plus, { borderColor: t.line }]}><Plus size={18} color={t.ink} /></View>
                )} />
            ))}
            {!list.length ? <Row first title={L('未找到', 'Nothing found')} sub={L('可在「自定义连接器」中填写其 MCP 地址。', 'You can add its MCP address under Custom connector.')} /> : null}
          </Group>
        ) : null}

        <Group style={{ marginTop: space.lg }}>
          <Row first icon={<Plus size={22} color={t.cyan} />} title={L('自定义连接器', 'Custom connector')} sub={L('填写 MCP 服务器地址', 'Add an MCP server address')} onPress={() => nav.navigate('CustomApp')} />
        </Group>
        <GroupNote>{L('点击「连接」将跳转到服务方的授权页，授权完成后自动返回。令牌保存在你的 claw 上。', "Connect opens the service's sign-in page and returns automatically. Tokens are stored on your claw.")}</GroupNote>
      </ScrollView>
    </Screen>
  );
}

// —— 一个连接器 ——————————————————————————————————————————————————————————————

export function AppDetailScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const route = useRoute<any>();
  const id: string = route.params?.id;
  const { appName } = useStore();
  const sheet = useSheet();
  const [app, setApp] = useState<AppDetail | null>(null);
  const [agents, setAgents] = useState<{ id: string; name: string }[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [each, setEach] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [d, l] = await Promise.all([appsApi.get(id), appsApi.list()]);
      setApp(d); setAgents(l.agents); setErr(null);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  }, [id]);
  useFocusEffect(useCallback(() => { load(); }, [load]));

  const patch = async (b: Parameters<typeof appsApi.patch>[1]) => {
    try { setApp(await appsApi.patch(id, b)); } catch (e) { showError(L('修改失败', "Couldn't change it"), e); load(); }
  };

  const reconnect = async () => {
    if (!app) return;
    if (app.auth === 'token') {
      // 换一把令牌：不对的话服务器还用旧的
      let draft = '';
      sheet.open({
        title: L(`${app.name} 的令牌`, `${app.name} token`),
        content: (close) => (
          <View style={{ gap: space.md }}>
            <TextInput onChangeText={(v) => { draft = v; }} autoFocus secureTextEntry autoCapitalize="none" autoCorrect={false}
              placeholder={L('新的 API 令牌', 'The new API token')} placeholderTextColor={t.ink3} accessibilityLabel={L('令牌', 'Token')}
              style={[type.body, styles.input, { backgroundColor: t.surface2, color: t.ink }]} />
            <Pressable onPress={() => {
              appsApi.connect(id, draft.trim()).then((r) => { close(); if (r.app) load(); }).catch((e) => showError(L('更换令牌失败', "Couldn't change the token"), e));
            }} accessibilityRole="button" style={({ pressed }) => [styles.primary, { marginHorizontal: 0, marginTop: 0, backgroundColor: t.cyan, opacity: pressed ? 0.7 : 1 }]}>
              <T v="headline" color="#FFFFFF">{L('更换', 'Replace')}</T>
            </Pressable>
          </View>
        ),
      });
      return;
    }
    if (app.auth === 'none') { refresh(); return; }
    if (!oauthHere()) { showError(L('请在手机 app 中连接', 'Connect it in the phone app'), ''); return; }
    try {
      const r = await appsApi.connect(id);
      if (r.authorizeUrl) await Linking.openURL(r.authorizeUrl);
    } catch (e) { showError(L('无法打开授权页', "Couldn't open the sign-in page"), e); }
  };

  const refresh = async () => {
    setBusy(true);
    try { setApp(await appsApi.refresh(id)); } catch (e) { showError(L('刷新失败', "Couldn't refresh"), e); } finally { setBusy(false); }
  };

  const remove = () => {
    if (!app) return;
    Alert.alert(L(`断开 ${app.name}？`, `Disconnect ${app.name}?`), L('令牌将从 claw 上删除，Agent 将无法再使用它的工具。如需使用，可重新连接。', 'Its tokens are deleted from your claw and Agents lose its tools. You can connect it again later.'), [
      { text: L('取消', 'Cancel'), style: 'cancel' },
      { text: L('断开', 'Disconnect'), style: 'destructive', onPress: () => { appsApi.remove(id).then(() => nav.goBack()).catch((e) => showError(L('断开失败', "Couldn't disconnect"), e)); } },
    ]);
  };

  if (!app) {
    return (
      <Screen>
        <SettingsHeader title={L('连接器', 'Connector')} onBack={() => nav.goBack()} />
        {err ? <T v="callout" color={t.bad} style={{ margin: space.lg }}>{err}</T> : <ActivityIndicator style={{ marginTop: space.xl }} color={t.ink3} />}
      </Screen>
    );
  }

  const reads = app.tools.filter((x) => x.kind === 'read');
  const writes = app.tools.filter((x) => x.kind === 'write');
  const toolNames = (xs: typeof app.tools) => xs.map((x) => x.title || x.name).slice(0, 6).join(L('、', ', ')) + (xs.length > 6 ? L(' 等', '…') : '');
  const allowed = new Set(app.agents);
  const toggleAgent = (aid: string, on: boolean) => patch({ agents: on ? [...new Set([...app.agents, aid])] : app.agents.filter((x) => x !== aid) });

  return (
    <Screen>
      <SettingsHeader title={app.name} onBack={() => nav.goBack()} />
      <ScrollView contentContainerStyle={{ paddingBottom: space.xxl, paddingTop: 4 }} refreshControl={<PullRefresh onRefresh={load} />}>
        <Group style={{ padding: 18, gap: 14 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 14 }}>
            <AppTile a={app} size={56} />
            <View style={{ flex: 1, minWidth: 0, gap: 4 }}>
              <T v="title" numberOfLines={1}>{app.name}</T>
              <T v="callout" color={t.ink2} numberOfLines={1}>{app.account || app.url.replace(/^https?:\/\//, '')}</T>
            </View>
            {app.status === 'connected' ? <Chip label={route.params?.fresh ? L('刚刚连接', 'Just connected') : L('已连接', 'Connected')} tone="good" /> : statusChip(app)}
          </View>
          {app.status !== 'connected' && app.error ? <T v="callout" color={t.warn}>{app.error}</T> : null}
          <View style={{ flexDirection: 'row', gap: 10 }}>
            <Pressable onPress={reconnect} accessibilityRole="button" style={({ pressed }) => [styles.btn, { borderColor: t.line, opacity: pressed ? 0.6 : 1 }, app.status === 'needs_auth' ? { backgroundColor: t.cyan, borderColor: t.cyan } : null]}>
              <T v="callout" color={app.status === 'needs_auth' ? '#FFFFFF' : t.ink} style={{ fontWeight: '600' }}>{app.status === 'needs_auth' ? L('重新连接', 'Reconnect') : app.auth === 'oauth' ? L('切换账号', 'Switch account') : app.auth === 'token' ? L('更换令牌', 'Change token') : L('重新加载', 'Reload')}</T>
            </Pressable>
            <Pressable onPress={remove} accessibilityRole="button" style={({ pressed }) => [styles.btn, { borderColor: t.line, opacity: pressed ? 0.6 : 1 }]}>
              <T v="callout" color={t.bad} style={{ fontWeight: '600' }}>{L('断开', 'Disconnect')}</T>
            </Pressable>
          </View>
        </Group>

        <GroupLabel right={busy ? <ActivityIndicator color={t.ink3} /> : (
          <Pressable onPress={refresh} accessibilityRole="button" accessibilityLabel={L('重新加载工具', 'Reload tools')} hitSlop={8}><RefreshCw size={16} color={t.ink3} /></Pressable>
        )}>{L(`工具权限 · ${app.toolCount}`, `Tool permissions · ${app.toolCount}`)}</GroupLabel>
        <Group style={{ paddingHorizontal: 18 }}>
          {reads.length ? (
            <View style={{ paddingVertical: 14, gap: 10 }}>
              <View style={{ gap: 2 }}>
                <T v="headline">{L(`读取 · ${reads.length} 个`, `Read · ${reads.length}`)}</T>
                <T v="callout" color={t.ink2} numberOfLines={2}>{toolNames(reads)}</T>
              </View>
              <Segmented value={app.policy.read} onChange={(v) => patch({ policy: { read: v } })} options={levels()} />
            </View>
          ) : null}
          {writes.length ? (
            <View style={{ paddingVertical: 14, gap: 10, borderTopWidth: reads.length ? StyleSheet.hairlineWidth : 0, borderTopColor: t.line }}>
              <View style={{ gap: 2 }}>
                <T v="headline">{L(`写入 · ${writes.length} 个`, `Write · ${writes.length}`)}</T>
                <T v="callout" color={t.ink2} numberOfLines={2}>{toolNames(writes)}</T>
              </View>
              <Segmented value={app.policy.write} onChange={(v) => patch({ policy: { write: v } })} options={levels()} />
            </View>
          ) : null}
          {app.tools.length ? (
            <Pressable onPress={() => setEach((v) => !v)} accessibilityRole="button" style={{ paddingVertical: 14, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }}>
              <T v="body" color={t.cyan}>{each ? L('收起', 'Hide') : L('逐个设置工具', 'Set tools one by one')}</T>
            </Pressable>
          ) : <T v="callout" color={t.ink2} style={{ paddingVertical: 14 }}>{L('暂无可用工具。', 'No tools available.')}</T>}
        </Group>
        {each ? (
          <Group style={{ marginTop: 10, paddingHorizontal: 18 }}>
            {app.tools.map((x, i) => (
              <View key={x.name} style={{ paddingVertical: 12, gap: 8, borderTopWidth: i ? StyleSheet.hairlineWidth : 0, borderTopColor: t.line }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                  <T v="body" style={{ flex: 1 }} numberOfLines={1}>{x.title || x.name}</T>
                  <Chip label={x.kind === 'read' ? L('读取', 'Read') : L('写入', 'Write')} tone={x.kind === 'read' ? 'muted' : 'warn'} />
                </View>
                {x.description ? <T v="caption" color={t.ink2} numberOfLines={2} style={{ fontWeight: '400', lineHeight: 17 }}>{x.description}</T> : null}
                <Segmented value={x.level} onChange={(v) => patch({ overrides: { [x.name]: v === (x.kind === 'read' ? app.policy.read : app.policy.write) ? null : v } })} options={levels()} />
              </View>
            ))}
          </Group>
        ) : null}
        <GroupNote>{L(`「需确认」：生成收件箱卡片并响铃提醒，你确认后 ${appName} 才会执行。`, `Ask me: a card arrives in your inbox with an alert; ${appName} acts only after you approve it.`)}</GroupNote>

        <GroupLabel>{L('可使用的 Agent', 'Agents with access')}</GroupLabel>
        <Group>
          {agents.map((a, i) => (
            <Row key={a.id} first={i === 0} title={a.name} sub={a.id === 'main' ? L('主对话', 'Main chat') : undefined}
              right={<Switch value={allowed.has(a.id)} onValueChange={(v) => toggleAgent(a.id, v)} trackColor={{ true: t.cyan, false: t.surface2 }} accessibilityLabel={L(`${a.name} 可使用 ${app.name}`, `${a.name} can use ${app.name}`)} />} />
          ))}
          <Row icon={<Lock size={20} color={t.ink3} />} title={L('名片 Agent', 'Card agent')} sub={L('代你回复好友，始终无法使用连接器', 'Answers friends on your behalf and never has access to connectors')} />
        </Group>

        <GroupLabel>{L('数据', 'Data')}</GroupLabel>
        <Group>
          <Row first title={L('令牌存储于', 'Tokens stored on')} value={appName} />
          <Row title={L('当前设置', 'Current setup')} value={`${levelName(app.policy.read)} / ${levelName(app.policy.write)}`} />
        </Group>
      </ScrollView>
    </Screen>
  );
}

// —— 自定义（MCP 地址） ——————————————————————————————————————————————————————

export function CustomAppScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const route = useRoute<any>();
  const p = route.params ?? {};
  const [name, setName] = useState<string>(p.name ?? '');
  const [url, setUrl] = useState<string>(p.url ?? '');
  const [auth, setAuth] = useState<'oauth' | 'token' | 'none'>(p.auth ?? 'oauth');
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const add = async () => {
    const u = url.trim();
    if (!/^https?:\/\/\S+$/i.test(u)) { setMsg(L('地址须以 https:// 开头', 'The address must start with https://')); return; }
    if (auth === 'token' && !token.trim()) { setMsg(L('请输入令牌', 'Enter the token')); return; }
    if (auth === 'oauth' && !oauthHere()) { setMsg(L('需授权的连接器请在手机 app 中添加', 'Add connectors that need sign-in from the phone app')); return; }
    setBusy(true); setMsg(null);
    try {
      const r = await appsApi.addCustom({ name: name.trim() || u.replace(/^https?:\/\//, '').split(/[/.]/)[0], url: u, auth, token: auth === 'token' ? token.trim() : undefined });
      if (r.authorizeUrl) { await Linking.openURL(r.authorizeUrl); nav.goBack(); return; }
      nav.replace('AppDetail', { id: r.app.id, fresh: true });
    } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };

  const field = (label: string, el: React.ReactNode, first = false) => (
    <View style={{ paddingVertical: 12, gap: 6, borderTopWidth: first ? 0 : StyleSheet.hairlineWidth, borderTopColor: t.line }}>
      <T v="caption" color={t.ink2} style={{ fontWeight: '500' }}>{label}</T>
      {el}
    </View>
  );
  const input = (value: string, set: (v: string) => void, ph: string, extra: object = {}) => (
    <TextInput value={value} onChangeText={(v) => { set(v); setMsg(null); }} placeholder={ph} placeholderTextColor={t.ink3} autoCapitalize="none" autoCorrect={false}
      style={[type.body, { color: t.ink, paddingVertical: 4 }]} {...extra} />
  );

  return (
    <Screen>
      <SettingsHeader title={L('自定义连接器', 'Custom connector')} onBack={() => nav.goBack()} />
      <ScrollView contentContainerStyle={{ paddingBottom: space.xxl }} keyboardShouldPersistTaps="handled" automaticallyAdjustKeyboardInsets>
        <Group style={{ paddingHorizontal: 18, marginTop: 8 }}>
          {field(L('名称', 'Name'), input(name, setName, L('例如 Linear', 'e.g. Linear'), { accessibilityLabel: L('名称', 'Name') }), true)}
          {field(L('MCP 地址', 'MCP address'), input(url, setUrl, 'https://mcp.example.com/mcp', { keyboardType: 'url', accessibilityLabel: L('MCP 地址', 'MCP address') }))}
          {field(L('登录', 'Sign-in'), <Segmented value={auth} onChange={(v) => { setAuth(v); setMsg(null); }} options={[
            { value: 'oauth', label: L('自动授权', 'Sign in') }, { value: 'token', label: L('令牌', 'Token') }, { value: 'none', label: L('无', 'None') },
          ]} />)}
          {auth === 'token' ? field(L('令牌', 'Token'), input(token, setToken, p.hint || L('服务方提供的 API 令牌', 'The API token the service gave you'), { secureTextEntry: true, accessibilityLabel: L('令牌', 'Token') })) : null}
        </Group>
        <GroupNote>{auth === 'oauth'
          ? L('若服务支持 MCP 授权，点击「添加并授权」将跳转到其授权页，无需在其网站注册应用。', "If it supports MCP sign-in, Add and sign in opens its sign-in page; you don't need to register anything on its site.")
          : auth === 'token' ? L('令牌仅保存在你的 claw 上，不会存入 app 或账号。', 'The token is stored on your claw only, never in the app or your account.')
            : L('无需登录的服务：任何人都可以连接，请勿通过它传输私密信息。', "A service without sign-in: anyone can use it, so don't send it anything private.")}</GroupNote>
        <View style={[styles.warn, { backgroundColor: t.warnSoft }]}>
          <T v="callout" color={t.warn} style={{ fontWeight: '500' }}>{L('仅添加你信任的服务：Agent 发送给它的所有内容均对其可见。', 'Only add services you trust: they can see everything an Agent sends them.')}</T>
        </View>
        {msg ? <T v="callout" color={t.bad} style={{ marginHorizontal: space.lg + 4, marginTop: space.md }}>{msg}</T> : null}
        <Pressable onPress={busy ? undefined : add} accessibilityRole="button"
          style={({ pressed }) => [styles.primary, { backgroundColor: t.cyan, opacity: pressed || busy ? 0.7 : 1 }]}>
          {busy ? <ActivityIndicator color="#FFFFFF" /> : <T v="headline" color="#FFFFFF">{auth === 'oauth' ? L('添加并授权', 'Add and sign in') : L('添加', 'Add')}</T>}
        </Pressable>
      </ScrollView>
    </Screen>
  );
}

// —— 第一次连上 claw 以后：接上常用的（设计稿第 3 步） ——————————————————————————

/** 连接页第一次连上以后来这里：目录里的头几个，点「连接」就去授权；也可以先跳过，以后在设置 → 连接器里连。服务器没有连接器就直接进去。 */
export function StarterScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { appName } = useStore();
  const [data, setData] = useState<AppsList | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const done = useCallback(() => nav.reset({ index: 0, routes: [{ name: 'Tabs' }] }), [nav]);
  const load = useCallback(() => appsApi.list().then(setData).catch(() => done()), [done]);
  useFocusEffect(useCallback(() => { load(); }, [load]));
  const list = (data?.catalog ?? []).filter((c) => c.auth !== 'token').slice(0, 4);
  const connect = async (c: CatalogEntry) => {
    setBusy(c.id);
    try { await connectCatalog(c, nav); } finally { setBusy(null); load(); }
  };
  const empty = !!data && !list.length;
  useEffect(() => { if (empty) done(); }, [empty, done]);  // 目录是空的：没什么可连，直接进去
  if (empty) return null;
  return (
    <Screen>
      <ScrollView contentContainerStyle={{ flexGrow: 1, paddingBottom: space.xl }}>
        <View style={{ paddingHorizontal: space.xl, paddingTop: space.xl, gap: 8 }}>
          <T v="callout" color={t.cyan} style={{ fontWeight: '600' }}>{L('最后一步', 'One last step')}</T>
          <T v="largeTitle" style={{ fontSize: 28 }}>{L('连接常用服务', 'Connect what you use')}</T>
          <T v="body" color={t.ink2}>{L(`连接后，${appName} 可以帮你查阅笔记、跟进项目。可以现在连接，也可以稍后在「设置 → 连接器」中连接。`,
            `Once connected, ${appName} can look things up for you. Connect now, or later in Settings → Connectors.`)}</T>
        </View>
        {!data ? <ActivityIndicator style={{ marginTop: space.xl }} color={t.ink3} /> : (
          <Group style={{ marginTop: space.xl }}>
            {list.map((c, i) => (
              <Row key={c.id} first={i === 0} icon={<AppTile a={c} size={40} />} title={c.name} sub={descOf(c.desc, zh()) || undefined} chevron={false}
                onPress={c.installed ? undefined : () => connect(c)} label={c.installed ? L(`${c.name}，已连接`, `${c.name}, connected`) : L(`连接 ${c.name}`, `Connect ${c.name}`)}
                right={busy === c.id ? <ActivityIndicator color={t.ink3} /> : c.installed ? <Check size={20} color={t.good} /> : <Chip label={L('连接', 'Connect')} tone="accent" />} />
            ))}
          </Group>
        )}
        <View style={{ flex: 1 }} />
        <Pressable onPress={done} accessibilityRole="button" style={({ pressed }) => [styles.primary, { backgroundColor: t.cyan, opacity: pressed ? 0.7 : 1 }]}>
          <T v="headline" color="#FFFFFF">{L('开始', 'Start')}</T>
        </Pressable>
        <Pressable onPress={done} accessibilityRole="button" style={{ height: 44, alignItems: 'center', justifyContent: 'center', marginTop: 6 }}>
          <T v="callout" color={t.ink2}>{L('暂时跳过', 'Skip for now')}</T>
        </Pressable>
      </ScrollView>
    </Screen>
  );
}

/** 连接器页用：「推荐」里一行右边的「连接 ↗」。 */
export function ConnectHint() {
  const t = useTheme();
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
      <T v="body" color={t.ink2} style={{ fontSize: 16 }}>{L('连接', 'Connect')}</T>
      <ArrowUpRight size={18} color={t.ink2} />
    </View>
  );
}


const styles = StyleSheet.create({
  input: { height: 52, borderRadius: radius.md, paddingHorizontal: space.lg },
  search: { flexDirection: 'row', alignItems: 'center', gap: 10, marginHorizontal: space.lg, marginTop: 8, paddingHorizontal: 14, borderRadius: radius.md },
  feature: { width: 200, padding: 14, gap: 10, borderRadius: 18 },
  plus: { width: 34, height: 34, borderRadius: 17, borderWidth: StyleSheet.hairlineWidth, alignItems: 'center', justifyContent: 'center' },
  btn: { flex: 1, height: 40, borderRadius: radius.md, borderWidth: StyleSheet.hairlineWidth, alignItems: 'center', justifyContent: 'center' },
  warn: { marginHorizontal: space.lg, marginTop: space.lg, padding: 14, borderRadius: radius.md },
  primary: { marginHorizontal: space.lg, marginTop: space.xl, height: 52, borderRadius: radius.md, alignItems: 'center', justifyContent: 'center' },
});
