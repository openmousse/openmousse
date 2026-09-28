// 我 → 连接（server/connectors.py）：助手接着的每一样东西现在怎么样。
// 按组一张卡，每行 = 图标、名字、一句现状、右边一个小圆点（绿 = 在用，琥珀 = 要注意，灰 = 没接）。
// 点一行：几条事实、它用来做什么、不对的时候怎么修；能在 app 里改的（日历订阅、世界树）带一个跳过去的按钮。
import React, { useEffect } from 'react';
import { Pressable, ScrollView, StyleSheet, View } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { agentName } from '../brand';
import {
  Bell, CalendarDays, CalendarSync, ChevronRight, Dumbbell, FileText, GraduationCap, HardDrive, HeartPulse, Mail, MessagesSquare, Notebook, Plug, Send, Server, TreeDeciduous,
} from '../components/icons';
import { useSheet } from '../components/Sheet';
import { Btn, Card, NavHeader, PullRefresh, Screen, SectionLabel, T } from '../components/ui';
import type { Connector } from '../data/types';
import { L } from '../i18n';
import { useStore } from '../store';
import { radius, space, useTheme, type Theme } from '../theme';

const ICONS: Record<string, typeof Plug> = {
  dumbbell: Dumbbell, 'heart-pulse': HeartPulse, graduation: GraduationCap, calendar: CalendarDays, mail: Mail, 'calendar-sync': CalendarSync,
  'hard-drive': HardDrive, notebook: Notebook, 'file-text': FileText, tree: TreeDeciduous, send: Send, messages: MessagesSquare, bell: Bell,
  server: Server,
};

const statusWord = (s: Connector['status']) => ({ ok: L('在用', 'Working'), warn: L('要注意', 'Needs a look'), off: L('没接', 'Not connected') })[s];
const dotColor = (t: Theme, s: Connector['status']) => (s === 'ok' ? t.good : s === 'warn' ? t.warn : t.ink3);

function Dot({ status, size = 9 }: { status: Connector['status']; size?: number }) {
  const t = useTheme();
  return <View style={{ width: size, height: size, borderRadius: size / 2, backgroundColor: dotColor(t, status), opacity: status === 'off' ? 0.6 : 1 }} />;
}

function ConnectorIcon({ c }: { c: Connector }) {
  const t = useTheme();
  const Icon = ICONS[c.icon] ?? Plug;
  const off = c.status === 'off';
  return (
    <View style={[styles.icon, { backgroundColor: off ? t.surface2 : t.cyanSoft }]}>
      <Icon size={18} color={off ? t.ink3 : t.cyan} />
    </View>
  );
}

/** 弹层画在导航外面（SheetProvider 包着整个导航），所以跳页的函数由行传进来，这里不能 useNavigation。 */
function ConnectorSheet({ c, close, go }: { c: Connector; close: () => void; go: (screen: string) => void }) {
  const t = useTheme();
  const open = c.open;
  return (
    <View style={{ gap: space.md }}>
      <View style={styles.statusLine}>
        <Dot status={c.status} />
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

function ConnectorRow({ c, first }: { c: Connector; first: boolean }) {
  const t = useTheme();
  const sheet = useSheet();
  const nav = useNavigation<any>();
  return (
    <Pressable onPress={() => sheet.open({ title: c.name, content: (close) => <ConnectorSheet c={c} close={close} go={(screen) => nav.navigate(screen)} /> })}
      accessibilityRole="button" accessibilityLabel={`${c.name}，${statusWord(c.status)}，${c.line}`}
      style={({ pressed }) => [styles.row, !first && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }, { opacity: pressed ? 0.6 : 1 }]}>
      <ConnectorIcon c={c} />
      <View style={{ flex: 1, gap: 2 }}>
        <T v="body" numberOfLines={1} color={c.status === 'off' ? t.ink2 : t.ink}>{c.name}</T>
        <T v="callout" color={c.status === 'warn' ? t.warn : t.ink2} numberOfLines={2}>{c.line}</T>
      </View>
      <Dot status={c.status} />
      <ChevronRight size={16} color={t.ink3} />
    </Pressable>
  );
}

function Note({ text, tone }: { text: string; tone?: 'bad' }) {
  const t = useTheme();
  return <Card style={{ marginTop: space.md }}><T v="callout" color={tone === 'bad' ? t.bad : t.ink2}>{text}</T></Card>;
}

export function ConnectorsScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { connectors, connected, booting, loading, dataErrors, reload, refreshConnectors } = useStore();
  useEffect(() => { if (connected) reload('connectors').catch(() => {}); }, [connected, reload]);  // 服务器缓存 60 秒，打开页面读一次
  const data = connectors?.kind === 'ok' ? connectors.data : null;
  let state: React.ReactNode = null;
  if (!data) {
    if (booting) state = <Note text={L('正在连服务器…', 'Connecting to the server…')} />;
    else if (!connected) state = <Note text={L('没连上服务器。检查「我 → 服务器」后下拉刷新。', 'Not connected to the server. Check Me → Server, then pull down to refresh.')} />;
    else if (dataErrors.connectors) state = <Note tone="bad" text={L(`读不到：${dataErrors.connectors}`, `Couldn't load: ${dataErrors.connectors}`)} />;
    else if (connectors?.kind === 'unsupported') state = <Note text={L('服务器的版本还没有这一页，更新服务器以后再来看。', "The server's version doesn't have this page yet. Update the server and check back.")} />;
    else if (loading.connectors || !connectors) state = <Note text={L('正在查…', 'Checking…')} />;
  }
  const c = data?.counts;
  const summary = c ? [
    c.ok ? L(`${c.ok} 个在用`, `${c.ok} working`) : '', c.warn ? L(`${c.warn} 个要注意`, `${c.warn} need a look`) : '', c.off ? L(`${c.off} 个没接`, `${c.off} not connected`) : '',
  ].filter(Boolean).join(L('，', ', ')) : '';
  return (
    <Screen>
      <NavHeader title={L('连接', 'Connections')} onBack={() => nav.goBack()} />
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl }} refreshControl={<PullRefresh onRefresh={refreshConnectors} />}>
        <T v="callout" color={t.ink2}>{L(
          `${agentName()} 接着的每一样东西现在怎么样。点一项看它用来做什么；要注意的，里面写着怎么修。`,
          `Everything ${agentName()} is connected to, and how it's doing. Tap one to see what it's for; anything that needs a look says how to fix it.`,
        )}</T>
        {state}
        {data?.groups.map((g) => (
          <View key={g.id}>
            <SectionLabel caps={g.id !== 'claw'}>{g.title}</SectionLabel>
            <Card style={{ paddingVertical: space.xs }}>
              {g.items.map((x, i) => <ConnectorRow key={x.id} c={x} first={i === 0} />)}
            </Card>
          </View>
        ))}
        {data ? (
          <T v="caption" color={t.ink3} style={styles.foot}>
            {[summary, data.checkedAt ? L(`查于 ${data.checkedAt}，下拉重新查`, `Checked ${data.checkedAt}; pull down to check again`) : ''].filter(Boolean).join(L('。', '. '))}
          </T>
        ) : null}
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: 12 },
  icon: { width: 34, height: 34, borderRadius: radius.sm + 2, alignItems: 'center', justifyContent: 'center' },
  statusLine: { flexDirection: 'row', alignItems: 'center', gap: space.sm },
  fact: { flexDirection: 'row', alignItems: 'flex-start', gap: space.md, paddingVertical: 10 },
  factLabel: { flexShrink: 0, maxWidth: '50%' },
  factValue: { flex: 1, textAlign: 'right' },
  fix: { borderRadius: radius.md, padding: space.md, gap: 4 },
  foot: { marginTop: space.lg, paddingHorizontal: space.xs, lineHeight: 18, fontWeight: '400' },
});
