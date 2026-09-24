// 历史：按逻辑日（04:00 为界）翻某个线程的旧对话，顶部关键词搜索（默认搜全部线程：对话、建议卡、日志）。
// 只读。Group 会话按天重置后，当天之外的记录都从这里看（蓝图 4.3）。
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { agentName } from '../brand';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, TextInput, View } from 'react-native';
import { useNavigation, useRoute } from '@react-navigation/native';
import { dataApi } from '../api/data';
import { HttpApi } from '../api/client';
import { Bubble } from '../components/ChatView';
import { Search, X } from '../components/icons';
import { Card, NavHeader, Pill, Screen, SectionLabel, T } from '../components/ui';
import type { DayInfo, Message, SearchHit } from '../data/types';
import { L } from '../i18n';
import { useStore } from '../store';
import { radius, space, useTheme } from '../theme';

const WEEK = ['日', '一', '二', '三', '四', '五', '六'];
const WEEK_EN = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTH_EN = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function dayLabel(day: string): string {
  const [y, m, d] = day.split('-').map(Number);
  const dt = new Date(y, m - 1, d);
  const today = new Date();
  today.setHours(today.getHours() - 4); // 逻辑日
  const iso = today.toLocaleDateString('en-CA');
  const yest = new Date(today); yest.setDate(yest.getDate() - 1);
  const rel = day === iso ? L('今天', 'Today') : day === yest.toLocaleDateString('en-CA') ? L('昨天', 'Yesterday') : '';
  const base = L(`${m} 月 ${d} 日 周${WEEK[dt.getDay()]}`, `${WEEK_EN[dt.getDay()]}, ${MONTH_EN[m - 1]} ${d}`);
  return rel ? `${rel} · ${base}` : y === today.getFullYear() ? base : L(`${y} 年 ${base}`, `${base}, ${y}`);
}

const msgCount = (n: number) => L(`${n} 条`, n === 1 ? '1 message' : `${n} messages`);

function useThreadName() {
  const { groups, sideChats } = useStore();
  return (id: string | null) => (id === 'main' || !id ? `${agentName()}` : groups.find((g) => g.id === id)?.name ?? sideChats.find((c) => c.id === id)?.title ?? id);
}

export function HistoryScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { thread } = useRoute<any>().params as { thread: string };
  const name = useThreadName();
  const { connected } = useStore();
  const [days, setDays] = useState<DayInfo[] | null>(null);
  const [q, setQ] = useState('');
  const [scope, setScope] = useState<'all' | 'thread'>('all');
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState('');
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!connected) return;
    dataApi.days(thread).then(setDays).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [thread, connected]);

  // 输入停 350ms 再搜（不放在 effect 里：改 q 或范围时直接排一次）
  const runSearch = (query: string, sc: 'all' | 'thread') => {
    if (timer.current) clearTimeout(timer.current);
    const trimmed = query.trim();
    if (!trimmed) { setHits(null); setSearching(false); return; }
    setSearching(true);
    timer.current = setTimeout(() => {
      dataApi.search(trimmed, sc === 'thread' ? thread : undefined)
        .then(setHits).catch((e) => setError(e instanceof Error ? e.message : String(e))).finally(() => setSearching(false));
    }, 350);
  };
  const changeQ = (v: string) => { setQ(v); runSearch(v, scope); };
  const changeScope = (sc: 'all' | 'thread') => { setScope(sc); runSearch(q, sc); };

  const grouped = useMemo(() => {
    const m = new Map<string, SearchHit[]>();
    for (const h of hits ?? []) m.set(h.day, [...(m.get(h.day) ?? []), h]);
    return [...m.entries()];
  }, [hits]);

  const openHit = (h: SearchHit) => {
    if (h.kind === 'journal') { nav.navigate('Journal'); return; }
    nav.navigate('HistoryDay', { thread: h.thread ?? 'main', day: h.day, focus: h.kind === 'message' ? h.id : undefined });
  };

  return (
    <Screen>
      <NavHeader title={L(`${name(thread)} · 历史`, `${name(thread)} · History`)} onBack={() => nav.goBack()} />
      <View style={{ paddingHorizontal: space.lg, paddingTop: space.sm, gap: space.sm }}>
        <View style={[styles.search, { backgroundColor: t.surface2 }]}>
          <Search size={18} color={t.ink3} />
          <TextInput value={q} onChangeText={changeQ} placeholder={L('搜对话、建议卡、日志', 'Search chats, suggestion cards, journal')} placeholderTextColor={t.ink3} autoCorrect={false} returnKeyType="search"
            style={[styles.input, { color: t.ink }]} accessibilityLabel={L('关键词搜索', 'Search')} />
          {q ? <Pressable onPress={() => changeQ('')} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('清空', 'Clear')}><X size={16} color={t.ink3} /></Pressable> : null}
        </View>
        {q.trim() ? (
          <View style={{ flexDirection: 'row', gap: space.sm }}>
            {(['all', 'thread'] as const).map((s) => (
              <Pressable key={s} onPress={() => changeScope(s)} accessibilityRole="button" style={[styles.chip, { backgroundColor: scope === s ? t.goldSoft : t.surface2 }]}>
                <T v="caption" color={scope === s ? t.gold : t.ink2} style={{ fontWeight: '600' }}>{s === 'all' ? L('全部线程', 'All threads') : L(`只看${name(thread)}`, `Only ${name(thread)}`)}</T>
              </Pressable>
            ))}
          </View>
        ) : null}
      </View>
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl }} keyboardShouldPersistTaps="handled">
        {!connected ? <Card><T v="callout" color={t.ink2}>{L('没连上服务器。', 'Not connected to the server.')}</T></Card> : null}
        {error ? <Card><T v="callout" color={t.bad}>{error}</T></Card> : null}
        {q.trim() ? (
          searching && !hits ? <ActivityIndicator color={t.gold} style={{ marginTop: space.lg }} /> : !hits?.length ? (
            <Card><T v="callout" color={t.ink2}>{L(`没搜到「${q.trim()}」。`, `No results for "${q.trim()}".`)}</T></Card>
          ) : grouped.map(([day, list]) => (
            <View key={day}>
              <SectionLabel>{dayLabel(day)}</SectionLabel>
              <Card style={{ paddingVertical: space.xs }}>
                {list.map((h, i) => (
                  <Pressable key={`${h.kind}-${h.id}`} onPress={() => openHit(h)} accessibilityRole="button"
                    style={({ pressed }) => [styles.hit, i < list.length - 1 && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }, { opacity: pressed ? 0.6 : 1 }]}>
                    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                      <Pill label={name(h.thread)} tone={h.thread && h.thread !== 'main' ? 'cyan' : 'gold'} />
                      <Pill label={h.kind === 'card' ? L('建议卡', 'Suggestion card') : h.kind === 'journal' ? L('日志', 'Journal') : h.role === 'user' ? L('你', 'You') : h.role === 'auto' ? L('自动', 'Auto') : `${agentName()}`} />
                      <T v="caption" color={t.ink3}>{h.time}</T>
                    </View>
                    <T v="callout" numberOfLines={3}>{h.snippet}</T>
                  </Pressable>
                ))}
              </Card>
            </View>
          ))
        ) : !days ? (
          connected ? <ActivityIndicator color={t.gold} style={{ marginTop: space.lg }} /> : null
        ) : !days.length ? (
          <Card><T v="callout" color={t.ink2}>{L('这个对话还没有记录。', 'No history for this chat yet.')}</T></Card>
        ) : (
          <>
            <SectionLabel>{L('按天翻（04:00 为一天的边界）', 'By day (a day starts at 04:00)')}</SectionLabel>
            <Card style={{ paddingVertical: space.xs }}>
              {days.map((d, i) => (
                <Pressable key={d.day} onPress={() => nav.navigate('HistoryDay', { thread, day: d.day })} accessibilityRole="button"
                  style={({ pressed }) => [styles.hit, i < days.length - 1 && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }, { opacity: pressed ? 0.6 : 1 }]}>
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
                    <T v="body" style={{ fontWeight: '600', flex: 1 }}>{dayLabel(d.day)}</T>
                    <T v="caption" color={t.ink3}>{msgCount(d.count)}</T>
                  </View>
                  {d.first ? <T v="callout" color={t.ink2} numberOfLines={1} style={{ marginTop: 3 }}>{d.first}</T> : null}
                </Pressable>
              ))}
            </Card>
          </>
        )}
      </ScrollView>
    </Screen>
  );
}

export function HistoryDayScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const api = useMemo(() => new HttpApi(), []);
  const { thread, day, focus } = useRoute<any>().params as { thread: string; day: string; focus?: string };
  const name = useThreadName();
  const [msgs, setMsgs] = useState<Message[] | null>(null);
  const [error, setError] = useState('');
  const scroll = useRef<ScrollView>(null);
  const ys = useRef<Record<string, number>>({});

  useEffect(() => {
    api.history(thread, day).then((h) => setMsgs(h?.messages ?? [])).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [api, thread, day]);

  const jump = () => { const y = focus ? ys.current[focus] : undefined; if (y !== undefined) scroll.current?.scrollTo({ y: Math.max(0, y - 80), animated: true }); };

  return (
    <Screen>
      <NavHeader title={`${name(thread)} · ${dayLabel(day)}`} onBack={() => nav.goBack()} />
      {error ? <Card style={{ margin: space.lg }}><T v="callout" color={t.bad}>{error}</T></Card> : null}
      {!msgs ? <ActivityIndicator color={t.gold} style={{ marginTop: space.lg }} /> : !msgs.length ? (
        <Card style={{ margin: space.lg }}><T v="callout" color={t.ink2}>{L('这一天没有记录。', 'Nothing on this day.')}</T></Card>
      ) : (
        <ScrollView ref={scroll} contentContainerStyle={{ padding: space.lg, gap: 14, paddingBottom: space.xxl }} onContentSizeChange={jump}>
          <T v="caption" color={t.ink3} style={{ textAlign: 'center' }}>{L('只读', 'Read-only')} · {msgCount(msgs.length)}</T>
          {msgs.map((m, i) => (
            <View key={m.id} onLayout={(e) => { ys.current[m.id] = e.nativeEvent.layout.y; }}
              style={focus === m.id ? [styles.focus, { borderLeftColor: t.gold }] : undefined}>
              <Bubble m={m} showAvatar={m.role === 'grava' && msgs[i - 1]?.role !== 'grava'} />
            </View>
          ))}
        </ScrollView>
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  search: { flexDirection: 'row', alignItems: 'center', gap: space.sm, borderRadius: radius.md, paddingHorizontal: space.md, paddingVertical: 8 },
  input: { flex: 1, fontSize: 16, paddingVertical: 4 },
  chip: { borderRadius: radius.pill, paddingHorizontal: 12, paddingVertical: 6 },
  hit: { paddingVertical: 10 },
  focus: { borderLeftWidth: 3, paddingLeft: 8, marginLeft: -11 },
});
