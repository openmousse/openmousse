// 我 → 日程：在 iPhone 自带日历里看（只读订阅链接，server/schedule.py 的 /cal/<令牌>.ics）。
// 四类各自开关；在自带日历里改不会同步回来。链接就是密码，能换一个（旧的马上失效）。
import React, { useCallback, useEffect, useState } from 'react';
import { Alert, Linking, Platform, ScrollView, StyleSheet, Switch, View } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import * as Clipboard from 'expo-clipboard';
import { agentName } from '../brand';
import { getBase } from '../api/base';
import * as sched from '../api/schedule';
import type { ScheduleFeed } from '../data/types';
import { CalendarDays, Check, Copy, Plus } from '../components/icons';
import { Btn, Card, NavHeader, Screen, SectionLabel, T, showError } from '../components/ui';
import { L } from '../i18n';
import { radius, space, useTheme } from '../theme';

type Key = keyof ScheduleFeed['include'];
const ROWS: { key: Key; title: () => string; sub: () => string }[] = [
  { key: 'classes', title: () => L('课表', 'Timetable'), sub: () => L('iPhone 上已经有学校的课表就别开，免得重复；标了不去的不显示', "Leave off if your iPhone already has the school timetable; skipped classes are hidden") },
  { key: 'mine', title: () => L('你加的、Agent 排的', 'Yours and your Agents’'), sub: () => L('训练、自习、吃饭、办事', 'Workouts, study, meals, errands') },
  { key: 'deadlines', title: () => L('截止', 'Deadlines'), sub: () => L('作业、求职和申请、你加的截止', 'Coursework, applications, your own deadlines') },
  { key: 'mail', title: () => L('邮件里的事', 'From email'), sub: () => L('面试、约好的咨询、买了票的活动、要办的事', 'Interviews, appointments, events, to-dos') },
];

/** 订阅链接：服务器地址 + 路径；网页版地址留空时是同源。 */
function urls(path: string): { http: string; webcal: string } {
  const base = getBase() || (Platform.OS === 'web' && typeof window !== 'undefined' ? window.location.origin : '');
  const http = `${base}${path}`;
  return { http, webcal: http.replace(/^https?:\/\//, 'webcal://') };
}

export function ScheduleFeedScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const [feed, setFeed] = useState<ScheduleFeed | null>(null);
  const [err, setErr] = useState('');
  const [copied, setCopied] = useState(false);
  const load = useCallback(() => {
    sched.feed().then((f) => { setFeed(f); setErr(''); }).catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, []);
  useEffect(() => { load(); }, [load]);
  const toggle = async (k: Key, v: boolean) => {
    if (!feed) return;
    setFeed({ ...feed, include: { ...feed.include, [k]: v } });
    try { setFeed(await sched.setFeed({ include: { [k]: v } })); } catch (e) { showError(L('没改成', "Couldn't change it"), e); load(); }
  };
  const rotate = () => {
    const go = async () => { try { setFeed(await sched.setFeed({ rotate: true })); setCopied(false); } catch (e) { showError(L('没换成', "Couldn't change it"), e); } };
    if (Platform.OS === 'web') { go(); return; }
    Alert.alert(L('换一个链接？', 'New link?'), L('旧链接马上失效，iPhone 日历里要重新订阅一次。', 'The old link stops working right away; subscribe again on your iPhone.'),
      [{ text: L('取消', 'Cancel'), style: 'cancel' }, { text: L('换', 'Change'), style: 'destructive', onPress: go }]);
  };
  const u = feed ? urls(feed.path) : null;
  return (
    <Screen>
      <NavHeader title={L('日程', 'Schedule')} onBack={() => nav.goBack()} />
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl }}>
        <Card style={{ gap: space.md }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.md }}>
            <View style={[styles.icon, { backgroundColor: t.cyanSoft }]}><CalendarDays size={22} color={t.cyan} /></View>
            <View style={{ flex: 1, gap: 2 }}>
              <T v="headline" style={{ fontSize: 17 }}>{L('在 iPhone 日历里看', 'See it in your iPhone calendar')}</T>
              <T v="callout" color={t.ink2}>{L('加进自带的日历，只读', 'Adds it to the built-in Calendar, read-only')}</T>
            </View>
          </View>
          {err ? <T v="callout" color={t.bad}>{L(`读不到：${err}`, `Couldn't load: ${err}`)}</T> : null}
          {u ? (
            <>
              <Btn label={L('添加到 iPhone 日历', 'Add to iPhone Calendar')} icon={<Plus size={18} color={t.onGold} />}
                onPress={() => { Linking.openURL(u.webcal).catch((e) => showError(L('打不开日历', "Couldn't open Calendar"), e)); }} />
              <Btn label={copied ? L('复制好了', 'Copied') : L('复制订阅链接', 'Copy the link')} kind="quiet"
                icon={copied ? <Check size={18} color={t.ink} /> : <Copy size={18} color={t.ink} />}
                onPress={() => { Clipboard.setStringAsync(u.http).then(() => setCopied(true)).catch((e) => showError(L('没复制上', "Couldn't copy"), e)); }} />
            </>
          ) : null}
        </Card>

        <SectionLabel>{L('里面有什么', "What's in it")}</SectionLabel>
        <Card style={{ paddingVertical: space.xs }}>
          {ROWS.map((r, i) => (
            <View key={r.key} style={[styles.row, i < ROWS.length - 1 && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }]}>
              <View style={{ flex: 1, gap: 2 }}>
                <T v="body">{r.title()}</T>
                <T v="caption" color={t.ink3} style={{ fontSize: 13, lineHeight: 18 }}>{r.sub()}</T>
              </View>
              <Switch value={!!feed?.include[r.key]} disabled={!feed} onValueChange={(v) => toggle(r.key, v)} accessibilityLabel={r.title()}
                trackColor={{ true: t.cyan, false: t.track }} thumbColor="#FFFFFF" />
            </View>
          ))}
        </Card>
        <T v="caption" color={t.ink3} style={styles.help}>
          {L(`在自带日历里改不会同步回来：要改在这里改，或者跟 ${agentName()} 说。iPhone 自己定时刷新；手机要连得上服务器（和 app 一样）才刷得到。`,
            `Changes made in the Calendar app don't come back here: change things here or tell ${agentName()}. Your iPhone refreshes it on its own schedule, as long as it can reach the server.`)}
        </T>
        {feed ? <Btn label={L('换一个链接（旧的马上失效）', 'New link (the old one stops working)')} kind="danger" onPress={rotate} /> : null}
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  icon: { width: 44, height: 44, borderRadius: radius.md, alignItems: 'center', justifyContent: 'center' },
  row: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: 12 },
  help: { fontSize: 13, lineHeight: 19, marginTop: space.sm, marginBottom: space.lg, paddingHorizontal: space.xs },
});
