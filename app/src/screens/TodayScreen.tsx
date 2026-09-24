import React, { useEffect, useState } from 'react';
import { agentName } from '../brand';
import { Alert, Pressable, RefreshControl, ScrollView, StyleSheet, Switch, View } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { ChevronLeft, ChevronRight, LoaderCircle, MapPin, X } from '../components/icons';
import { ApprovalCard } from '../components/ApprovalCard';
import { modelName, originName } from '../components/TaskCard';
import { Card, LargeHeader, ListRow, Pill, Screen, SectionLabel, T } from '../components/ui';
import type { FeedItem, JournalEntry, UpcomingTask } from '../data/types';
import { dataApi } from '../api/data';
import { loadEventsOn, type LiveEvent } from '../api/live';
import { useStore } from '../store';
import { radius, space, useTheme } from '../theme';
import { MealPlanCard } from '../components/LiveBoards';
import { Markdown } from '../components/Markdown';
import { TrainingPlanCard, isTrainingPlan } from '../components/Records';

function UpcomingRow({ u, last }: { u: UpcomingTask; last: boolean }) {
  const t = useTheme();
  const { toggleUpcoming } = useStore();
  const meta = [u.enabled ? u.when : '已停用', u.repeat, u.agent, u.modelId ? modelName(u.modelId) : null, u.last?.status && u.last.status !== 'ok' ? `上次 ${u.last.status}` : null].filter(Boolean).join(' · ');
  return (
    <View style={[styles.up, !last && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }]}>
      <View style={{ flex: 1, gap: 3 }}>
        <T v="body" color={u.enabled ? t.ink : t.ink3}>{u.title}</T>
        <T v="caption" color={t.ink3}>{meta}</T>
      </View>
      {u.toggleable ? (
        <Switch value={u.enabled} trackColor={{ true: t.goldFill, false: t.track }} thumbColor="#FFFFFF" accessibilityLabel={`${u.enabled ? '停用' : '启用'}：${u.title}`}
          onValueChange={(v) => toggleUpcoming(u.id, v).catch((e) => Alert.alert('没改成', e instanceof Error ? e.message : String(e)))} />
      ) : <Pill label="系统" />}
    </View>
  );
}

const KIND_LABEL: Record<JournalEntry['kind'], string> = { feeling: '感受', thought: '想法', decision: '决定', note: '记录' };

function isoOf(offset: number): string {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  return d.toLocaleDateString('en-CA');
}
function titleOf(offset: number): string {
  if (offset === 0) return '今天';
  if (offset === 1) return '明天';
  if (offset === -1) return '昨天';
  const d = new Date();
  d.setDate(d.getDate() + offset);
  return d.toLocaleDateString('zh-CN', { month: 'long', day: 'numeric' });
}
function subOf(offset: number): string {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  const s = d.toLocaleDateString('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' });
  if (offset === 0 || Math.abs(offset) === 1) return s;
  return `${s} · ${offset > 0 ? `${offset} 天后` : `${-offset} 天前`}`;
}

/** 日程列表（今天和别的日子共用）。 */
function EventList({ events, empty }: { events: LiveEvent[]; empty: string }) {
  const t = useTheme();
  return (
    <Card style={{ paddingVertical: space.xs }}>
      {events.length ? events.map((e, i) => (
        <View key={`${e.start}-${e.title}-${i}`} style={[styles.ev, i < events.length - 1 && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }, e.past && { opacity: 0.45 }]}>
          <View style={{ width: 52 }}>
            <T v="callout" style={{ fontVariant: ['tabular-nums'], fontWeight: '600' }}>{e.start}</T>
            {e.end ? <T v="caption" color={t.ink3} style={{ fontVariant: ['tabular-nums'] }}>{e.end}</T> : null}
          </View>
          <View style={{ flex: 1, gap: 3 }}>
            <T v="body" numberOfLines={2}>{e.title}{e.tentative ? '（暂定）' : ''}</T>
            {e.location ? (
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
                <MapPin size={12} color={t.ink3} />
                <T v="caption" color={t.ink3} numberOfLines={1} style={{ flex: 1 }}>{e.location}</T>
              </View>
            ) : null}
          </View>
        </View>
      )) : <View style={styles.ev}><T v="callout" color={t.ink2}>{empty}</T></View>}
    </Card>
  );
}

/** 一张建议卡。别的日子不给划掉。 */
function FeedCard({ f, onDismiss }: { f: FeedItem; onDismiss?: () => void }) {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { groups } = useStore();
  const gname = groups.find((g) => g.id === f.groupId)?.name ?? `${agentName()}`;
  return (
    <Card style={{ gap: space.sm }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
        <Pill label={gname} tone={f.groupId ? 'cyan' : 'gold'} />
        <T v="caption" color={t.ink3} style={{ flex: 1 }}>{onDismiss ? f.time : f.createdAt?.slice(11, 16) || f.time}</T>
        {onDismiss ? <Pressable onPress={onDismiss} hitSlop={10} accessibilityRole="button" accessibilityLabel="不感兴趣"><X size={16} color={t.ink3} /></Pressable> : null}
      </View>
      <T v="headline">{f.title}</T>
      {f.kind === 'meal_plan' && f.data && 'meals' in f.data ? <MealPlanCard plan={f.data} /> : isTrainingPlan(f) ? <TrainingPlanCard plan={f.data} /> : <Markdown text={f.body} color={t.ink2} compact />}
      {f.cta && onDismiss ? (
        <Pressable onPress={() => (f.groupId ? nav.navigate('Group', { id: f.groupId }) : nav.navigate('Tabs', { screen: '对话' }))} accessibilityRole="button"
          style={({ pressed }) => [styles.cta, { backgroundColor: t.surface2, opacity: pressed ? 0.7 : 1 }]}>
          <T v="callout" style={{ fontWeight: '600' }}>{f.cta}</T>
        </Pressable>
      ) : null}
    </Card>
  );
}

type DayData = { events: LiveEvent[]; feed: FeedItem[]; errors: string[] };

/** 翻到别的日子：那天的日程、Grava 的建议卡、日志。审批 / 后台任务 / 定时任务只跟"现在"有关，只在今天显示。 */
function DayView({ iso, offset, refreshKey }: { iso: string; offset: number; refreshKey: number }) {
  const t = useTheme();
  const { journal, groups, connected } = useStore();
  // 按日期缓存，翻回来不用重读；下拉刷新（refreshKey 变）时重读当前这天。
  const [days, setDays] = useState<Record<string, DayData>>({});
  const day = days[iso] ?? null;
  useEffect(() => {
    if (!connected) return undefined;
    let alive = true;
    const errors: string[] = [];
    Promise.all([
      loadEventsOn(iso).catch((e) => { errors.push(`日程：${e instanceof Error ? e.message : String(e)}`); return [] as LiveEvent[]; }),
      dataApi.feedOn(iso).catch((e) => { errors.push(`建议：${e instanceof Error ? e.message : String(e)}`); return [] as FeedItem[]; }),
    ]).then(([events, feed]) => { if (alive) setDays((m) => ({ ...m, [iso]: { events, feed, errors } })); });
    return () => { alive = false; };
  }, [iso, connected, refreshKey]);
  const entries = journal.filter((e) => e.date === iso);
  const gname = (id: string | null) => groups.find((g) => g.id === id)?.name ?? `${agentName()}`;
  if (!connected) return <Card style={{ marginTop: space.md }}><T v="callout" color={t.ink2}>没连上服务器，翻不了别的日子。</T></Card>;
  if (!day) return <Card style={{ marginTop: space.md }}><T v="callout" color={t.ink2}>正在读{titleOf(offset)}的…</T></Card>;
  return (
    <>
      {day.errors.length ? <Card style={{ marginTop: space.md }}><T v="callout" color={t.bad}>{day.errors.join('；')}</T></Card> : null}
      <SectionLabel right={<Pill label="日历" tone="good" />}>{offset < 0 ? '那天的日程' : '日程'}</SectionLabel>
      <EventList events={day.events} empty={`${titleOf(offset)}日历上没有安排。`} />

      <SectionLabel>{`${agentName()} 的建议`}</SectionLabel>
      {day.feed.length ? <View style={{ gap: space.md }}>{day.feed.map((f) => <FeedCard key={f.id} f={f} />)}</View>
        : <Card><T v="callout" color={t.ink2}>{offset < 0 ? '那天没有建议卡。' : '还没有。建议卡是当天才出的。'}</T></Card>}

      {entries.length ? (
        <>
          <SectionLabel>日志</SectionLabel>
          <Card style={{ paddingVertical: space.xs }}>
            {entries.map((e, i) => (
              <View key={e.id} style={[styles.ev, i < entries.length - 1 && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }]}>
                <View style={{ width: 52 }}><T v="callout" color={t.ink3} style={{ fontVariant: ['tabular-nums'] }}>{e.time}</T></View>
                <View style={{ flex: 1, gap: 4 }}>
                  <View style={{ flexDirection: 'row', gap: 6 }}><Pill label={KIND_LABEL[e.kind] ?? e.kind} /><Pill label={gname(e.groupId)} tone="cyan" /></View>
                  <T v="body">{e.text}</T>
                </View>
              </View>
            ))}
          </Card>
        </>
      ) : null}
      <T v="caption" color={t.ink3} style={{ marginTop: space.lg, paddingHorizontal: space.xs }}>审批、后台任务和定时任务只在「今天」显示。</T>
    </>
  );
}

export function TodayScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { approvals, feed, upcoming, groups, sideChats, dismissFeed, live, tasks, connected, booting, reload, refreshLive, loading, liveLoading, dataErrors } = useStore();
  const [showOff, setShowOff] = useState(false);
  const [offset, setOffset] = useState(0);
  const [dayRefresh, setDayRefresh] = useState(0);
  const bg = tasks.filter((x) => x.status === '进行中').slice(0, 3);
  const iso = isoOf(0);
  const dayIso = isoOf(offset);
  const todays = (live?.events ?? []).filter((e) => e.date === iso);
  const tomorrows = (live?.events ?? []).filter((e) => e.date > iso);
  const on = upcoming.filter((u) => u.enabled);
  const off = upcoming.filter((u) => !u.enabled);
  const refreshing = liveLoading || !!loading.approvals || !!loading.upcoming;
  return (
    <Screen>
      <ScrollView contentContainerStyle={{ paddingBottom: space.xxl }}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { if (offset !== 0) setDayRefresh((n) => n + 1); return connected ? reload('approvals', 'upcoming', 'tasks', 'feed', 'journal') : refreshLive(); }} />}>
        <LargeHeader title={titleOf(offset)} sub={subOf(offset)} right={(
          <View style={styles.nav}>
            <Pressable onPress={() => setOffset((o) => o - 1)} hitSlop={8} accessibilityRole="button" accessibilityLabel="前一天" style={({ pressed }) => [styles.navBtn, { backgroundColor: t.surface2, opacity: pressed ? 0.6 : 1 }]}><ChevronLeft size={20} color={t.ink} /></Pressable>
            <Pressable onPress={() => setOffset((o) => o + 1)} hitSlop={8} accessibilityRole="button" accessibilityLabel="后一天" style={({ pressed }) => [styles.navBtn, { backgroundColor: t.surface2, opacity: pressed ? 0.6 : 1 }]}><ChevronRight size={20} color={t.ink} /></Pressable>
          </View>
        )} />
        <View style={{ paddingHorizontal: space.lg }}>
          {offset !== 0 ? (
            <Pressable onPress={() => setOffset(0)} accessibilityRole="button" style={{ alignSelf: 'flex-start', paddingVertical: 2, paddingHorizontal: space.xs }}>
              <T v="callout" color={t.gold} style={{ fontWeight: '600' }}>回到今天</T>
            </Pressable>
          ) : null}
          {!connected ? (
            <Card style={{ marginTop: space.md }}><T v="callout" color={t.ink2}>{booting ? '正在连服务器…' : '没连上服务器。检查「我 → 服务器」后下拉刷新。'}</T></Card>
          ) : null}
          {offset !== 0 ? <DayView iso={dayIso} offset={offset} refreshKey={dayRefresh} /> : (<>

          <SectionLabel>等你点头</SectionLabel>
          {approvals.length ? (
            <View style={{ gap: space.md }}>{approvals.map((a) => <ApprovalCard key={a.id} approval={a} />)}</View>
          ) : (
            <Card>
              <T v="callout" color={dataErrors.approvals ? t.bad : t.ink2}>
                {dataErrors.approvals ? `读不到审批队列：${dataErrors.approvals}` : `审批队列是空的。现在 ${agentName()} 跑命令不需要审批（见「我 → 安全」），安全底座上线后要你点头的动作会出现在这里。`}
              </T>
            </Card>
          )}

          {bg.length ? (
            <>
              <SectionLabel right={<Pressable onPress={() => nav.navigate('Tasks')} accessibilityRole="button"><T v="caption" color={t.gold}>全部</T></Pressable>}>后台在做的</SectionLabel>
              <Card style={{ paddingVertical: space.xs }}>
                {bg.map((x, i) => (
                  <ListRow key={x.id} title={x.title} last={i === bg.length - 1} onPress={() => nav.navigate('Task', { id: x.id })}
                    icon={<LoaderCircle size={18} color={t.cyan} />}
                    sub={`${originName(x.origin, groups, sideChats)} → ${modelName(x.modelId)}${x.lastTool ? ` · 正在用 ${x.lastTool}` : ''}`} />
                ))}
              </Card>
            </>
          ) : null}

          {live ? (
            <>
              <SectionLabel right={<Pill label="日历" tone="good" />}>今天的日程</SectionLabel>
              <EventList events={todays} empty={live?.sources.calendar === false ? '还没接日历。' : '今天日历上没有安排。'} />
              {tomorrows.length ? (
                <Pressable onPress={() => setOffset(1)} accessibilityRole="button" style={{ marginTop: space.sm, paddingHorizontal: space.xs }}>
                  <T v="callout" color={t.ink3}>明天 {tomorrows.length} 个日程，第一个 {tomorrows[0].start}。<T v="callout" color={t.gold}>看明天</T></T>
                </Pressable>
              ) : null}
            </>
          ) : null}

          <SectionLabel>{`${agentName()} 的建议`}</SectionLabel>
          {feed.length ? (
            <View style={{ gap: space.md }}>
              {feed.map((f) => <FeedCard key={f.id} f={f} onDismiss={() => dismissFeed(f.id)} />)}
            </View>
          ) : (
            <Card><T v="callout" color={t.ink2}>还没有建议。起床报告和主动提醒是第 8 步，上线后会出现在这里。</T></Card>
          )}

          <SectionLabel>接下来会自动做的事</SectionLabel>
          {upcoming.length ? (
            <>
              <Card style={{ paddingVertical: space.xs }}>
                {on.map((u, i) => <UpcomingRow key={u.id} u={u} last={i === on.length - 1} />)}
              </Card>
              {off.length ? (
                <>
                  <Pressable onPress={() => setShowOff((v) => !v)} accessibilityRole="button" style={{ paddingVertical: space.sm, paddingHorizontal: space.xs }}>
                    <T v="caption" color={t.gold}>{showOff ? '收起停用的' : `还有 ${off.length} 个停用的定时任务`}</T>
                  </Pressable>
                  {showOff ? <Card style={{ paddingVertical: space.xs }}>{off.map((u, i) => <UpcomingRow key={u.id} u={u} last={i === off.length - 1} />)}</Card> : null}
                </>
              ) : null}
            </>
          ) : (
            <Card><T v="callout" color={dataErrors.upcoming ? t.bad : t.ink2}>{dataErrors.upcoming ? `读不到定时任务：${dataErrors.upcoming}` : loading.upcoming ? '正在读…' : connected ? '没有定时任务。' : '没连上服务器。'}</T></Card>
          )}
          </>)}
        </View>
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  cta: { alignSelf: 'flex-start', borderRadius: radius.pill, paddingHorizontal: 14, paddingVertical: 8, marginTop: 2 },
  up: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: 12 },
  ev: { flexDirection: 'row', gap: space.md, paddingVertical: 12 },
  nav: { flexDirection: 'row', gap: 8 },
  navBtn: { width: 36, height: 36, borderRadius: 18, alignItems: 'center', justifyContent: 'center' },
});
