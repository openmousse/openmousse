// 管家「说过的话」（主线第三步第三段，设计稿第 46 张）：这周对外说过的每句，新的在前，按天分开。每行「回 Mia 的管家 · 读书会群 11:48」+ 原话；
// Doorman 扣下的写原因（「带了住址」）和扣下的原话，还在等你的那句「放行 / 不放」（走原来那张 review 卡：服务器 butler.py → inbox → cardagent.release）。
// 路由 ButlerSaid（params held：只看扣下的）。
import React, { useCallback, useState } from 'react';
import { ActivityIndicator, ScrollView, StyleSheet, View } from 'react-native';
import { useFocusEffect, useNavigation, useRoute } from '@react-navigation/native';
import { errText } from '../../components/agents/NewFlow';
import { dayKey, dayLabel } from '../../components/FriendBits';
import { ShieldCheck } from '../../components/icons';
import { Btn, Card, NavHeader, Pill, PullRefresh, Screen, SectionLabel, Segmented, T, showError } from '../../components/ui';
import { butlerApi, type SaidItem } from '../../api/butler';
import { L } from '../../i18n';
import { radius, space, useTheme } from '../../theme';

const reasonText = (rs: SaidItem['reasons']) => rs.map((r) => (typeof r === 'string' ? r : r.detail || r.kind || '')).filter(Boolean).join(L('；', '; '));

const outcomeLabel = (o: string) => ({ released: L('已放行', 'Released'), withheld: L('没放', 'Kept back'), rewritten: L('你改写后发出', 'You rewrote it'),
  accepted: L('你点了同意', 'You said yes'), declined: L('你点了不去', 'You said no'), counter: L('你换了时间', 'You asked for another time') }[o] ?? '');

function SaidRow({ i, busy, onAct }: { i: SaidItem; busy: boolean; onAct: (a: 'release' | 'keep') => void }) {
  const t = useTheme();
  const why = reasonText(i.reasons ?? []);
  const done = outcomeLabel(i.outcome);
  return (
    <Card style={{ gap: space.sm, ...(i.inboxId ? { borderWidth: 1, borderColor: t.warn } : null) }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
        <T v="caption" color={t.ink3} style={{ flex: 1 }} numberOfLines={1}>{`${i.where} · ${i.time}`}</T>
        {i.held ? <Pill label={L('Doorman 扣下', 'Held by Doorman')} tone="warn" /> : i.by === 'owner' ? <Pill label={L('你点的', 'Your call')} /> : null}
      </View>
      {i.held && i.original ? (
        <>
          <View style={[styles.box, { backgroundColor: t.warnSoft }]}>
            <T v="caption" color={t.warn}>{why ? L(`扣下的原话 · ${why}`, `Held · ${why}`) : L('扣下的原话', 'What it held')}</T>
            <T v="callout">{i.original}</T>
          </View>
          {i.text ? <T v="callout" color={t.ink2}>{L(`对方收到：${i.text}`, `They got: ${i.text}`)}</T> : null}
        </>
      ) : <T v="callout">{i.text || L('（已收回）', '(withdrawn)')}</T>}
      {i.inboxId ? (
        <View style={{ flexDirection: 'row', gap: space.sm }}>
          <Btn flex label={busy ? L('正在发送…', 'Sending…') : L('放行', 'Release')} onPress={() => onAct('release')} />
          <Btn flex kind="quiet" label={L('不放', 'Keep back')} onPress={() => onAct('keep')} />
        </View>
      ) : done ? <T v="caption" color={t.ink3}>{done}</T> : null}
    </Card>
  );
}

export function ButlerSaidScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const params = (useRoute<any>().params ?? {}) as { held?: boolean };
  const [only, setOnly] = useState<'all' | 'held'>(params.held ? 'held' : 'all');
  const [data, setData] = useState<{ items: SaidItem[]; week: { said: number; held: number } } | null>(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const load = useCallback(() => butlerApi.said(7).then((d) => { setData(d); setErr(''); }).catch((e) => setErr(errText(e))), []);
  useFocusEffect(useCallback(() => { load(); }, [load]));
  const act = (i: SaidItem, a: 'release' | 'keep') => {
    if (busy) return;
    setBusy(i.id);
    butlerApi.actSaid(i.id, a).then(() => load()).catch((e) => showError(a === 'release' ? L('发送失败', "Couldn't send") : L('操作失败', "Couldn't do that"), e))
      .finally(() => setBusy(null));
  };

  const items = (data?.items ?? []).filter((i) => only === 'all' || i.held);
  const days: { key: string; label: string; items: SaidItem[] }[] = [];
  for (const i of items) {
    const k = dayKey(i.ts);
    const last = days[days.length - 1];
    if (last && last.key === k) last.items.push(i);
    else days.push({ key: k, label: dayLabel(i.ts), items: [i] });
  }
  return (
    <Screen>
      <NavHeader title={L('说过的话', 'What it said')} onBack={() => nav.goBack()}
        sub={data ? L(`最近 7 天 · 说了 ${data.week.said} 句 · 扣下 ${data.week.held} 句`, `Last 7 days · ${data.week.said} said · ${data.week.held} held`) : undefined} />
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl, gap: space.sm }} refreshControl={<PullRefresh onRefresh={load} />}>
        <Segmented value={only} onChange={setOnly} options={[{ value: 'all', label: L('全部', 'All') }, { value: 'held', label: L('扣下的', 'Held') }]} />
        <View style={[styles.note, { backgroundColor: t.surface }]}>
          <ShieldCheck size={16} color={t.good} />
          <T v="caption" color={t.ink2} style={{ flex: 1 }}>{L('说出去之前每句都经 Doorman 复查；扣下的，对方先收到一句「需要你本人回答」，你放行了才发原话。',
            'Doorman checks every line before it goes out. When it holds one, they first get a line saying you have to answer; the original goes only if you release it.')}</T>
        </View>
        {err ? <Card><T v="callout" color={t.bad}>{L(`无法加载：${err}`, `Couldn't load: ${err}`)}</T></Card> : null}
        {!data && !err ? <ActivityIndicator color={t.gold} style={{ marginTop: space.xl }} /> : null}
        {data && !items.length ? <T v="callout" color={t.ink3} style={{ marginTop: space.lg }}>{only === 'held' ? L('这周没有扣下的。', 'Nothing held this week.') : L('这周还没说过话。', 'Nothing said this week.')}</T> : null}
        {days.map((d) => (
          <View key={d.key} style={{ gap: space.sm }}>
            <SectionLabel caps={false}>{d.label}</SectionLabel>
            {d.items.map((i) => <SaidRow key={i.id} i={i} busy={busy === i.id} onAct={(a) => act(i, a)} />)}
          </View>
        ))}
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  box: { borderRadius: radius.sm, padding: space.sm, gap: 2 },
  note: { flexDirection: 'row', alignItems: 'flex-start', gap: space.sm, borderRadius: radius.md, padding: space.md },
});
