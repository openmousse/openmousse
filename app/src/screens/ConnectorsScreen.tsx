// 设置 → 连接器（2026-09-29 改版，照 Claude 的 Connectors 页）：
// 上面是经 MCP 接进来的应用（server/apps.py）：已连接的（右边是能用的工具数，要重新授权的标黄）、推荐的（点「连接」跳授权页）、
// 查看全部 / 自定义；下面是这台 claw 本来就接着的东西现在怎么样（server/connectors.py：数据来源、聊天渠道、推送……），
// 点一行看几条事实、它用来做什么、不对的时候怎么修。服务器老、没有 /api/apps 的，上面那块不出现。
import React, { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, ScrollView, StyleSheet, View } from 'react-native';
import { useFocusEffect, useNavigation } from '@react-navigation/native';
import { agentName } from '../brand';
import {
  Bell, CalendarDays, CalendarSync, Dumbbell, FileText, GraduationCap, HardDrive, HeartPulse, Mail, MessagesSquare, Notebook, Plug, Plus, Send, Server, TreeDeciduous,
} from '../components/icons';
import { Dot, Group, GroupLabel, GroupNote, RoundButton, Row, SettingsHeader, Tile } from '../components/settings';
import { useSheet } from '../components/Sheet';
import { Btn, Card, PullRefresh, Screen, T } from '../components/ui';
import { appsApi, descOf, noApps, type AppsList, type CatalogEntry } from '../api/apps';
import type { Connector } from '../data/types';
import { L } from '../i18n';
import { useStore } from '../store';
import { radius, space, useTheme, type Theme } from '../theme';
import { AppTile, ConnectHint, connectCatalog, statusChip } from './AppsScreens';

const ICONS: Record<string, typeof Plug> = {
  dumbbell: Dumbbell, 'heart-pulse': HeartPulse, graduation: GraduationCap, calendar: CalendarDays, mail: Mail, 'calendar-sync': CalendarSync,
  'hard-drive': HardDrive, notebook: Notebook, 'file-text': FileText, tree: TreeDeciduous, send: Send, messages: MessagesSquare, bell: Bell,
  server: Server,
};

const statusWord = (s: Connector['status']) => ({ ok: L('在用', 'Working'), warn: L('要注意', 'Needs a look'), off: L('没接', 'Not connected') })[s];
const dotTone = (s: Connector['status']) => (s === 'ok' ? 'good' : s === 'warn' ? 'warn' : 'off') as 'good' | 'warn' | 'off';
const zh = () => L('zh', 'en') === 'zh';

/** 弹层画在导航外面（SheetProvider 包着整个导航），所以跳页的函数由行传进来，这里不能 useNavigation。 */
function ConnectorSheet({ c, close, go }: { c: Connector; close: () => void; go: (screen: string) => void }) {
  const t = useTheme();
  const open = c.open;
  return (
    <View style={{ gap: space.md }}>
      <View style={styles.statusLine}>
        <Dot tone={dotTone(c.status)} />
        <T v="callout" color={t.ink2} style={{ flex: 1 }}>{`${statusWord(c.status)} · ${c.line}`}</T>
      </View>
      {c.facts.length ? (
        <Card style={{ paddingVertical: space.xs }}>
          {c.facts.map((f, i) => (
            <View key={`${f.label}${i}`} style={[styles.fact, i > 0 && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }]}>
              <T v="callout" color={t.ink2} style={styles.factLabel}>{f.label}</T>
              <T v="callout" style={styles.factValue}>{f.value}</T>
            </View>
          ))}
        </Card>
      ) : null}
      {c.uses ? (
        <View style={{ gap: 4 }}>
          <T v="label" color={t.ink3} style={{ textTransform: 'uppercase' }}>{L('它用来做什么', 'What it does')}</T>
          <T v="callout">{c.uses}</T>
        </View>
      ) : null}
      {c.fix ? (
        <View style={[styles.fix, { backgroundColor: c.status === 'warn' ? t.warnSoft : t.surface }]}>
          <T v="label" color={c.status === 'warn' ? t.warn : t.ink3} style={{ textTransform: 'uppercase' }}>{L('怎么修', 'How to fix it')}</T>
          <T v="callout" selectable>{c.fix}</T>
        </View>
      ) : null}
      {open ? <Btn kind={c.status === 'ok' ? 'quiet' : 'primary'} label={open.label || L('打开', 'Open')} onPress={() => { close(); go(open.screen); }} /> : null}
    </View>
  );
}

function StatusIcon({ c, t }: { c: Connector; t: Theme }) {
  const Icon = ICONS[c.icon] ?? Plug;
  const off = c.status === 'off';
  return <Tile size={34} bg={off ? t.surface2 : t.cyanSoft}><Icon size={18} color={off ? t.ink3 : t.cyan} /></Tile>;
}

export function ConnectorsScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const sheet = useSheet();
  const { connectors, connected, booting, loading, dataErrors, reload, refreshConnectors, appName } = useStore();
  const [apps, setApps] = useState<AppsList | null>(null);
  const [appsErr, setAppsErr] = useState<string | null>(null);
  const [appsOld, setAppsOld] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  const loadApps = useCallback(() => {
    if (!connected) return Promise.resolve();
    return appsApi.list().then((r) => { setApps(r); setAppsErr(null); setAppsOld(false); })
      .catch((e) => { if (noApps(e)) setAppsOld(true); else setAppsErr(e instanceof Error ? e.message : String(e)); });
  }, [connected]);
  useFocusEffect(useCallback(() => { loadApps(); }, [loadApps]));
  useEffect(() => { if (connected) reload('connectors').catch(() => {}); }, [connected, reload]);  // 服务器缓存 60 秒，打开页面读一次

  const data = connectors?.kind === 'ok' ? connectors.data : null;
  let state: string | null = null;
  if (!data) {
    if (booting) state = L('正在连服务器…', 'Connecting to the server…');
    else if (!connected) state = L('没连上服务器。检查「设置 → 我的 claw」后下拉刷新。', 'Not connected to the server. Check Settings → My claws, then pull down to refresh.');
    else if (dataErrors.connectors) state = L(`读不到：${dataErrors.connectors}`, `Couldn't load: ${dataErrors.connectors}`);
    else if (connectors?.kind === 'unsupported') state = null;
    else if (loading.connectors || !connectors) state = L('正在查…', 'Checking…');
  }

  const installed = apps?.apps ?? [];
  const recommended = (apps?.catalog ?? []).filter((c) => !c.installed).slice(0, 3);
  const connect = async (c: CatalogEntry) => {
    setBusy(c.id);
    try { await connectCatalog(c, nav); } finally { setBusy(null); loadApps(); }
  };

  return (
    <Screen>
      <SettingsHeader title={L('连接器', 'Connectors')} onBack={() => nav.goBack()}
        right={apps ? <RoundButton onPress={() => nav.navigate('AppGallery')} label={L('添加连接器', 'Add a connector')}><Plus size={20} color={t.ink} /></RoundButton> : undefined} />
      <ScrollView contentContainerStyle={{ paddingBottom: space.xxl }} refreshControl={<PullRefresh onRefresh={async () => { await Promise.all([loadApps(), refreshConnectors()]); }} />}>
        {apps ? (
          <>
            {installed.length ? (
              <>
                <GroupLabel>{L('已连接', 'Connected')}</GroupLabel>
                <Group>
                  {installed.map((a, i) => (
                    <Row key={a.id} first={i === 0} icon={<AppTile a={a} />} title={a.name} right={statusChip(a)} chevron
                      onPress={() => nav.navigate('AppDetail', { id: a.id })} />
                  ))}
                </Group>
              </>
            ) : null}
            {recommended.length ? (
              <>
                <GroupLabel>{installed.length ? L('推荐', 'Suggested') : L('连一个试试', 'Try connecting one')}</GroupLabel>
                <Group>
                  {recommended.map((c, i) => (
                    <Row key={c.id} first={i === 0} icon={<AppTile a={c} />} title={c.name} sub={descOf(c.desc, zh()) || undefined} chevron={false}
                      right={busy === c.id ? <ActivityIndicator color={t.ink3} /> : <ConnectHint />} onPress={() => connect(c)} label={L(`连接 ${c.name}`, `Connect ${c.name}`)} />
                  ))}
                </Group>
              </>
            ) : null}
            <Group style={{ marginTop: space.lg }}>
              <Row first title={L('查看全部连接器', 'Browse all connectors')} onPress={() => nav.navigate('AppGallery')} />
              <Row title={L('自定义连接器', 'Custom connector')} value={L('MCP 地址', 'MCP address')} onPress={() => nav.navigate('CustomApp')} />
            </Group>
            <GroupNote>{L(`数字是能用的工具数。令牌存在 ${appName} 上；每个工具可以设成自动、先问我或关。`,
              `The number is how many tools it offers. Tokens are kept on ${appName}; each tool can be Auto, Ask me or Off.`)}</GroupNote>
          </>
        ) : appsErr ? <T v="callout" color={t.bad} style={{ margin: space.lg }}>{appsErr}</T> : !appsOld && connected ? <ActivityIndicator style={{ marginTop: space.lg }} color={t.ink3} /> : null}

        {state ? <T v="callout" color={t.ink2} style={{ marginHorizontal: space.lg + 4, marginTop: space.lg }}>{state}</T> : null}
        {data ? (
          <>
            {apps ? (
              <View style={{ marginTop: 34, marginHorizontal: space.lg + 4, paddingTop: 18, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line, gap: 4 }}>
                <T v="headline">{L(`${agentName()} 本来就接着的`, `What ${agentName()} is already hooked up to`)}</T>
                <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L('数据来源、渠道和通知现在怎么样；点一项看怎么修。', "Data sources, channels and notifications and how they're doing; tap one to see how to fix it.")}</T>
              </View>
            ) : null}
            {data.groups.map((g) => (
              <View key={g.id}>
                <GroupLabel>{g.title}</GroupLabel>
                <Group>
                  {g.items.map((x, i) => (
                    <Row key={x.id} first={i === 0} icon={<StatusIcon c={x} t={t} />} title={x.name} sub={x.line}
                      right={<Dot tone={dotTone(x.status)} />}
                      label={`${x.name}${L('，', ', ')}${statusWord(x.status)}${L('，', ', ')}${x.line}`}
                      onPress={() => sheet.open({ title: x.name, content: (close) => <ConnectorSheet c={x} close={close} go={(screen) => nav.navigate(screen)} /> })} />
                  ))}
                </Group>
              </View>
            ))}
            {data.checkedAt ? <GroupNote>{L(`查于 ${data.checkedAt}，下拉重新查`, `Checked ${data.checkedAt}; pull down to check again`)}</GroupNote> : null}
          </>
        ) : null}
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  statusLine: { flexDirection: 'row', alignItems: 'center', gap: space.sm },
  fact: { flexDirection: 'row', alignItems: 'flex-start', gap: space.md, paddingVertical: 10 },
  factLabel: { flexShrink: 0, maxWidth: '50%' },
  factValue: { flex: 1, textAlign: 'right' },
  fix: { borderRadius: radius.md, padding: space.md, gap: 4 },
});
