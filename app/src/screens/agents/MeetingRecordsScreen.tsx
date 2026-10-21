// 会议记录（主线第三步第二段）：路由 MeetingRecords，从主对话的资料页进来（「会议记录」那一行）。全部会议，新的在前：
// 助手发起的和「定了告诉我」的开完不在对话列表里单列（server.json meetings.by_origin 开着时），都在这里；你发起的也存一份。
// 顶上按谁发起筛（全部 / 助手发起 / 定了告诉我 / 你发起），搜议题、纪要和发言（服务器字面搜，不调模型）。点一条进会议页。
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, TextInput, View } from 'react-native';
import { useFocusEffect, useNavigation } from '@react-navigation/native';
import { agentName } from '../../brand';
import { meetingsApi, type MeetingRecord, type RecordFilter } from '../../api/meetings';
import { Search, X } from '../../components/icons';
import { MeetingAvatar, StatusPill } from '../../components/MeetingBits';
import { Card, NavHeader, PullRefresh, Screen, T } from '../../components/ui';
import { L } from '../../i18n';
import { radius, space, type, useTheme } from '../../theme';

const FILTERS: { key: RecordFilter; label: () => string }[] = [
  { key: 'all', label: () => L('全部', 'All') },
  { key: 'main', label: () => L(`${agentName()} 发起`, `By ${agentName()}`) },
  { key: 'auto', label: () => L('定了告诉我', 'Told when settled') },
  { key: 'mine', label: () => L('你发起', 'By you') },
];

export function MeetingRecordsScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const [filter, setFilter] = useState<RecordFilter>('all');
  const [q, setQ] = useState('');
  const [rows, setRows] = useState<MeetingRecord[] | null>(null);
  const [intro, setIntro] = useState('');
  const [error, setError] = useState<string | null>(null);
  const query = q.trim();
  const load = useCallback(() => meetingsApi.records(filter, query)
    .then((r) => { setRows(r.records); setIntro(r.intro); setError(null); })
    .catch((e) => setError(e instanceof Error ? e.message : String(e))), [filter, query]);
  // 搜的字停下来 0.4 秒再问（服务器字面搜，不调模型）
  useEffect(() => {
    const h = setTimeout(() => { load(); }, query ? 400 : 0);
    return () => clearTimeout(h);
  }, [load, query]);
  // 从会议页回来：再读一次（第一次进来上面那个已经读了）
  const loadRef = useRef(load);
  useEffect(() => { loadRef.current = load; }, [load]);
  const seen = useRef(false);
  useFocusEffect(useCallback(() => { if (seen.current) loadRef.current(); seen.current = true; }, []));
  return (
    <Screen>
      <NavHeader title={L('会议记录', 'Meeting records')} onBack={() => nav.goBack()} />
      <View style={{ paddingHorizontal: space.lg, gap: space.sm, paddingBottom: space.sm }}>
        <View style={[styles.search, { backgroundColor: t.surface }]}>
          <Search size={16} color={t.ink3} />
          <TextInput value={q} onChangeText={setQ} placeholder={L('搜议题、纪要和发言', 'Search topics, minutes and messages')} placeholderTextColor={t.ink3}
            returnKeyType="search" accessibilityLabel={L('搜索会议记录', 'Search meeting records')} style={[type.body, styles.searchInput, { color: t.ink }]} />
          {q ? <Pressable onPress={() => setQ('')} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('清除', 'Clear')}><X size={15} color={t.ink3} /></Pressable> : null}
        </View>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: space.sm }}>
          {FILTERS.map((f) => {
            const on = f.key === filter;
            return (
              <Pressable key={f.key} onPress={() => setFilter(f.key)} accessibilityRole="button" accessibilityState={{ selected: on }}
                style={({ pressed }) => [styles.chip, { backgroundColor: on ? t.goldSoft : t.surface, borderColor: on ? t.goldFill : t.line, opacity: pressed ? 0.7 : 1 }]}>
                <T v="caption" color={on ? t.gold : t.ink2} style={{ fontWeight: on ? '700' : '500' }}>{f.label()}</T>
              </Pressable>
            );
          })}
        </ScrollView>
      </View>
      <ScrollView contentContainerStyle={{ paddingHorizontal: space.lg, paddingBottom: space.xxl, gap: space.sm }} refreshControl={<PullRefresh onRefresh={load} />}
        keyboardShouldPersistTaps="handled">
        {intro && !query ? <T v="caption" color={t.ink3} style={styles.intro}>{intro}</T> : null}
        {error ? <Card><T v="callout" color={t.bad}>{L(`无法加载：${error}`, `Couldn't load: ${error}`)}</T></Card> : null}
        {rows && !rows.length ? (
          <T v="callout" color={t.ink3} style={{ paddingVertical: space.lg, textAlign: 'center' }}>{query ? L(`没有找到「${query}」`, `Nothing found for "${query}"`) : L('暂无会议记录。', 'No meeting records yet.')}</T>
        ) : null}
        {(rows ?? []).map((r) => (
          <Pressable key={r.id} onPress={() => nav.navigate('Meeting', { id: r.id, at: Date.now() })} accessibilityRole="button"
            accessibilityLabel={`${r.title}${L('，', ', ')}${r.line}`} style={({ pressed }) => [styles.row, { backgroundColor: t.surface, opacity: pressed ? 0.75 : 1 }]}>
            <MeetingAvatar members={r.members} size={40} />
            <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                <T v="headline" numberOfLines={1} style={{ flex: 1, fontSize: 15 }}>{r.title}</T>
                <T v="caption" color={t.ink3}>{r.day}</T>
              </View>
              <T v="caption" color={t.ink2} numberOfLines={1}>{r.line}</T>
              {r.match ? <T v="caption" color={t.ink3} numberOfLines={2}>{r.match}</T>
                : r.todos || r.decisions ? <T v="caption" color={t.ink3}>{L(`${r.decisions} 项决定 · ${r.todos} 项待办`, `${r.decisions} decided · ${r.todos} to-dos`)}</T> : null}
            </View>
            {r.status === 'running' || r.status === 'pending' ? <StatusPill status={r.status} label={r.status === 'running' ? L('进行中', 'Running') : L('待确认', 'To confirm')} /> : null}
          </Pressable>
        ))}
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  search: { flexDirection: 'row', alignItems: 'center', gap: space.sm, height: 40, borderRadius: radius.md, paddingHorizontal: space.md },
  searchInput: { flex: 1, fontSize: 15, paddingVertical: 0 },
  chip: { height: 30, paddingHorizontal: 12, borderRadius: 15, borderWidth: StyleSheet.hairlineWidth, alignItems: 'center', justifyContent: 'center' },
  intro: { fontSize: 13, lineHeight: 19, paddingHorizontal: space.xs, paddingBottom: space.xs },
  row: { flexDirection: 'row', alignItems: 'center', gap: space.md, borderRadius: radius.lg - 2, padding: space.md },
});
