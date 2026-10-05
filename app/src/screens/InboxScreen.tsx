// 已处理：最近 7 天你点过头的事（收件箱 status=recent），按天分组。做完的打勾，在做的转圈，让它改的是笔，没要的是叉，没做成的是感叹号。
// 点一行看详情（为什么、结果、时间），没做完的在详情里「跟进」：带着这件事回到那个对话接着说。
import React, { useEffect, useMemo } from 'react';
import { Pressable, ScrollView, StyleSheet, View } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { serverSupport } from '../api/data';
import { StatusCircle, clock, handledText, useInboxDetail } from '../components/InboxCard';
import { ChevronRight } from '../components/icons';
import { useSourceName } from '../components/SourceBadge';
import { Card, NavHeader, PullRefresh, Screen, SectionLabel, T } from '../components/ui';
import type { InboxItem } from '../data/types';
import { L } from '../i18n';
import { useStore } from '../store';
import { space, useTheme } from '../theme';
import { needsUpdate } from '../api/version';

/** 按最近一次你动它的时间排：跟进过的跟着跟进那天走 */
const whenOf = (i: InboxItem) => i.followedAt || i.decidedAt || i.createdAt;
const dayOf = (iso: string) => { const ms = Date.parse(iso); return Number.isNaN(ms) ? '' : new Date(ms).toLocaleDateString('en-CA'); };

function dayTitle(day: string): string {
  if (!day) return L('更早', 'Earlier');
  const today = new Date();
  const yest = new Date(); yest.setDate(yest.getDate() - 1);
  if (day === today.toLocaleDateString('en-CA')) return L('今天', 'Today');
  if (day === yest.toLocaleDateString('en-CA')) return L('昨天', 'Yesterday');
  const [y, m, d] = day.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(L('zh-CN', 'en'), { month: 'short', day: 'numeric', weekday: 'short' });
}

function Row({ item, last, onPress }: { item: InboxItem; last: boolean; onPress: () => void }) {
  const t = useTheme();
  const nameOf = useSourceName();
  const name = item.sourceName || nameOf(item.source);
  return (
    <Pressable onPress={onPress} accessibilityRole="button" accessibilityHint={L('查看详情', 'Shows the details')}
      style={({ pressed }) => [styles.row, !last && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }, { opacity: pressed ? 0.6 : 1 }]}>
      <View style={{ marginTop: 1 }}><StatusCircle status={item.status} exec={item.kind === 'exec'} size={30} /></View>
      <View style={{ flex: 1, gap: 2 }}>
        <T v="headline" style={{ fontSize: 15, lineHeight: 20 }}>{item.title}</T>
        <T v="callout" color={t.ink2} style={{ fontSize: 13, lineHeight: 19 }}>{handledText(item, name)}</T>
      </View>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 2, marginTop: 2 }}>
        <T v="caption" color={t.ink3}>{clock(whenOf(item))}</T>
        <ChevronRight size={14} color={t.ink3} />
      </View>
    </Pressable>
  );
}

export function InboxScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { inboxRecent, reload, connected, booting, loading, dataErrors } = useStore();
  const openDetail = useInboxDetail();
  useEffect(() => { if (connected) reload('inboxRecent').catch(() => {}); }, [connected, reload]);
  const days = useMemo(() => {
    const sorted = [...inboxRecent].sort((a, b) => (Date.parse(whenOf(b)) || 0) - (Date.parse(whenOf(a)) || 0));
    const m = new Map<string, InboxItem[]>();
    for (const it of sorted) { const d = dayOf(whenOf(it)); m.set(d, [...(m.get(d) ?? []), it]); }
    return [...m.entries()];
  }, [inboxRecent]);
  const status = !connected
    ? (booting ? L('正在连接服务器…', 'Connecting to the server…') : L('未连接服务器。请检查「设置 → 我的 claw」后下拉刷新。', 'Not connected to the server. Check Settings → My claws, then pull down to refresh.'))
    : dataErrors.inboxRecent ? L(`无法加载：${dataErrors.inboxRecent}`, `Couldn't load: ${dataErrors.inboxRecent}`)
      : serverSupport.inbox === false ? needsUpdate('当前服务器不支持收件箱。', "This server doesn't support the inbox yet.")
        : !inboxRecent.length ? (loading.inboxRecent ? L('正在加载…', 'Loading…') : L('最近 7 天暂无已处理事项。', 'Nothing decided in the last 7 days.'))
          : '';
  return (
    <Screen>
      <NavHeader title={L('已处理', 'Handled')} sub={L('最近 7 天你处理过的事项', 'What you decided in the last 7 days')} onBack={() => nav.goBack()} />
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingTop: space.xs, paddingBottom: space.xxl }}
        refreshControl={<PullRefresh onRefresh={() => reload('inboxRecent')} />}>
        {status ? <Card style={{ marginTop: space.lg }}><T v="callout" color={dataErrors.inboxRecent ? t.bad : t.ink2}>{status}</T></Card> : null}
        {days.map(([day, items]) => (
          <View key={day || 'earlier'}>
            <SectionLabel>{dayTitle(day)}</SectionLabel>
            <Card style={{ paddingVertical: 0, paddingHorizontal: 0 }}>
              {items.map((it, i) => <Row key={it.id} item={it} last={i === items.length - 1} onPress={() => openDetail(it)} />)}
            </Card>
          </View>
        ))}
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', gap: space.md, alignItems: 'flex-start', paddingVertical: 14, paddingHorizontal: space.lg },
});
