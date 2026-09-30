import React, { useCallback, useEffect, useRef, useState } from 'react';
import { agentName } from '../brand';
import { Alert, Animated, AppState, Platform, Pressable, ScrollView, StyleSheet, Switch, Text, View, type LayoutChangeEvent } from 'react-native';
import { useIsFocused, useNavigation, useRoute } from '@react-navigation/native';
import { ChevronLeft, ChevronRight, LoaderCircle, X } from '../components/icons';
import { InboxCard } from '../components/InboxCard';
import { SourcePill } from '../components/SourceBadge';
import { modelName, originName } from '../components/TaskCard';
import { Card, LargeHeader, ListRow, Pill, PullRefresh, Screen, SectionLabel, T, useScaledWidth } from '../components/ui';
import type { FeedItem, InboxItem, JournalEntry, ScheduleEntry, UpcomingTask } from '../data/types';
import { dataApi } from '../api/data';
import * as sched from '../api/schedule';
import { AddScheduleButton, RememberCard, ScheduleCard } from '../components/Schedule';
import { StudyTodayRow } from '../components/StudyBoard';
import { L } from '../i18n';
import { receiptItems, useStore, type DataKey } from '../store';
import { radius, space, useTheme } from '../theme';
import { MealPlanCard } from '../components/LiveBoards';
import { Markdown } from '../components/Markdown';
import { TrainingPlanCard, isTrainingPlan } from '../components/Records';
import { WakeCard } from '../components/WakeCard';

function UpcomingRow({ u, last }: { u: UpcomingTask; last: boolean }) {
  const t = useTheme();
  const { toggleUpcoming } = useStore();
  const meta = [u.enabled ? u.when : L('已停用', 'Disabled'), u.repeat, u.agent, u.modelId ? modelName(u.modelId) : null, u.last?.status && u.last.status !== 'ok' ? L(`上次 ${u.last.status}`, `Last run: ${u.last.status}`) : null].filter(Boolean).join(' · ');
  return (
    <View style={[styles.up, !last && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }]}>
      <View style={{ flex: 1, gap: 3 }}>
        <T v="body" color={u.enabled ? t.ink : t.ink3}>{u.title}</T>
        <T v="caption" color={t.ink3}>{meta}</T>
      </View>
      {u.toggleable ? (
        <Switch value={u.enabled} trackColor={{ true: t.goldFill, false: t.track }} thumbColor="#FFFFFF"
          accessibilityLabel={L(`${u.enabled ? '停用' : '启用'}：${u.title}`, `${u.enabled ? 'Disable' : 'Enable'}: ${u.title}`)}
          onValueChange={(v) => toggleUpcoming(u.id, v).catch((e) => Alert.alert(L('没改成', "Couldn't update"), e instanceof Error ? e.message : String(e)))} />
      ) : <Pill label={L('系统', 'System')} />}
    </View>
  );
}

const kindLabel = (k: JournalEntry['kind']) =>
  ({ feeling: L('感受', 'Feeling'), thought: L('想法', 'Thought'), decision: L('决定', 'Decision'), note: L('记录', 'Note') })[k];

function isoOf(offset: number): string {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  return d.toLocaleDateString('en-CA');
}
function titleOf(offset: number): string {
  if (offset === 0) return L('今天', 'Today');
  if (offset === 1) return L('明天', 'Tomorrow');
  if (offset === -1) return L('昨天', 'Yesterday');
  const d = new Date();
  d.setDate(d.getDate() + offset);
  // zh-CN 的 short 和 long 一样是「9月30日」；英文用 short（Sep 30），大标题放得下
  return d.toLocaleDateString(L('zh-CN', 'en'), { month: 'short', day: 'numeric' });
}
function subOf(offset: number): string {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  const s = d.toLocaleDateString(L('zh-CN', 'en'), { month: 'long', day: 'numeric', weekday: 'long' });
  if (offset === 0 || Math.abs(offset) === 1) return s;
  // |offset| ≥ 2，英文总是复数
  return `${s} · ${offset > 0 ? L(`${offset} 天后`, `in ${offset} days`) : L(`${-offset} 天前`, `${-offset} days ago`)}`;
}

/** 「今天要记得的」「邮件里要记得的」两张卡 9/26 起合进「要记得的」（Schedule.tsx）：旧卡不再显示。 */
const RETIRED = new Set(['reminder', 'mail_digest']);
const shownCards = (feed: FeedItem[]) => feed.filter((f) => !RETIRED.has(f.kind ?? ''));

/** 一张建议卡。别的日子不给划掉。isNew：还没看过，时间后面标一个青色的「新」。 */
function FeedCard({ f, onDismiss, isNew }: { f: FeedItem; onDismiss?: () => void; isNew?: boolean }) {
  const t = useTheme();
  const nav = useNavigation<any>();
  return (
    <Card style={{ gap: space.sm }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
        <SourcePill source={f.groupId} />
        <T v="caption" color={t.ink3}>{onDismiss ? f.time : f.createdAt?.slice(11, 16) || f.time}</T>
        {isNew ? (
          <View style={[styles.newPill, { backgroundColor: t.cyan }]} accessible accessibilityLabel={L('新的', 'New')}>
            <Text style={[styles.newText, { color: t.surface }]}>{L('新', 'New')}</Text>
          </View>
        ) : null}
        <View style={{ flex: 1 }} />
        {onDismiss ? <Pressable onPress={onDismiss} hitSlop={10} accessibilityRole="button" accessibilityLabel={L('不感兴趣', 'Not interested')}><X size={16} color={t.ink3} /></Pressable> : null}
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

/** 从通知 / 小窗点进来时闪一下金边，只闪一次。 */
function Flash({ token, round }: { token: number; round: number }) {
  const t = useTheme();
  const [o] = useState(() => new Animated.Value(0));
  useEffect(() => {
    if (!token) return;
    const nd = Platform.OS !== 'web';
    o.setValue(0);
    Animated.sequence([
      Animated.timing(o, { toValue: 1, duration: 180, useNativeDriver: nd }),
      Animated.delay(900),
      Animated.timing(o, { toValue: 0, duration: 700, useNativeDriver: nd }),
    ]).start();
  }, [token, o]);
  return <Animated.View pointerEvents="none" style={[StyleSheet.absoluteFill, { borderRadius: round, borderWidth: 2, borderColor: t.goldFill, opacity: o }]} />;
}

const byNewest = (a: InboxItem, b: InboxItem) => (Date.parse(b.createdAt) || 0) - (Date.parse(a.createdAt) || 0);

/** 「今天」页上的位置簿记（都是相对 ScrollView 内容的 y）：滚到某张卡、判断哪些卡在屏幕里。 */
type Layout = { pad: number; inbox: number; feed: number; items: Record<string, { y: number; h: number }>; scrollY: number; viewH: number };
function yOf(l: Layout, key: string): number | null {
  const it = l.items[key];
  return it ? l.pad + (key.startsWith('card:') ? l.feed : l.inbox) + it.y : null;
}

type DayData = { events: ScheduleEntry[]; editable: boolean; feed: FeedItem[]; errors: string[] };

/** 翻到别的日子：那天的日程（过去的记实际发生的）、Grava 的建议卡、日志。审批 / 后台任务 / 定时任务只跟"现在"有关，只在今天显示。 */
function DayView({ iso, offset, refreshKey }: { iso: string; offset: number; refreshKey: number }) {
  const t = useTheme();
  const timeW = useScaledWidth(52);
  const { journal, connected, reload } = useStore();
  // 按日期缓存，翻回来不用重读；下拉刷新（refreshKey 变）或在这一页改了日程（edits 变）时重读当前这天。
  const [days, setDays] = useState<Record<string, DayData>>({});
  const [edits, setEdits] = useState(0);
  const day = days[iso] ?? null;
  useEffect(() => {
    if (!connected) return undefined;
    let alive = true;
    const errors: string[] = [];
    Promise.all([
      sched.timeline(iso, 1).catch((e): sched.Timeline => { const m = e instanceof Error ? e.message : String(e); errors.push(L(`日程：${m}`, `Schedule: ${m}`)); return { events: [], errors: {}, editable: false }; }),
      dataApi.feedOn(iso).catch((e) => { const m = e instanceof Error ? e.message : String(e); errors.push(L(`建议：${m}`, `Suggestions: ${m}`)); return [] as FeedItem[]; }),
    ]).then(([tl, feed]) => { if (alive) setDays((m) => ({ ...m, [iso]: { events: tl.events, editable: tl.editable, feed: shownCards(feed), errors } })); });
    return () => { alive = false; };
  }, [iso, connected, refreshKey, edits]);
  // 改了别的日子：这一天重读；今天和明天的在「今天」页上，也跟着重读
  const changed = () => { setEdits((n) => n + 1); reload('schedule', 'remember').catch(() => {}); };
  const today = isoOf(0);
  const entries = journal.filter((e) => e.date === iso);
  if (!connected) return <Card style={{ marginTop: space.md }}><T v="callout" color={t.ink2}>{L('没连上服务器，翻不了别的日子。', "Not connected to the server, so other days can't be loaded.")}</T></Card>;
  if (!day) return <Card style={{ marginTop: space.md }}><T v="callout" color={t.ink2}>{L(`正在读${titleOf(offset)}的…`, 'Loading…')}</T></Card>;
  return (
    <>
      {day.errors.length ? <Card style={{ marginTop: space.md }}><T v="callout" color={t.bad}>{day.errors.join(L('；', '; '))}</T></Card> : null}
      <SectionLabel right={day.editable ? <AddScheduleButton day={iso} past={offset < 0} onChanged={changed} /> : <Pill label={L('日历', 'Calendar')} tone="good" />}>
        {offset < 0 ? L('那天的日程', "That day's schedule") : L('日程', 'Schedule')}
      </SectionLabel>
      <ScheduleCard events={day.events} day={iso} today={today} editable={day.editable} onChanged={changed}
        empty={offset < 0 ? L('那天没有安排。', 'Nothing scheduled that day.') : L(`${titleOf(offset)}还没有安排。`, 'Nothing scheduled yet.')} />
      {offset < 0 && day.editable ? <T v="caption" color={t.ink3} style={{ marginTop: space.sm, paddingHorizontal: space.xs }}>{L('过去的日子记实际发生的：去没去、做没做、几点。日结和复盘用这个。', 'Past days record what actually happened. The daily wrap-up and reviews use it.')}</T> : null}

      <SectionLabel>{L(`${agentName()} 的建议`, `Suggestions from ${agentName()}`)}</SectionLabel>
      {day.feed.length ? <View style={{ gap: space.md }}>{day.feed.map((f) => <FeedCard key={f.id} f={f} />)}</View>
        : <Card><T v="callout" color={t.ink2}>{offset < 0 ? L('那天没有建议卡。', 'No suggestion cards that day.') : L('还没有。建议卡是当天才出的。', 'None yet. Suggestion cards only appear on the day.')}</T></Card>}

      {entries.length ? (
        <>
          <SectionLabel>{L('日志', 'Journal')}</SectionLabel>
          <Card style={{ paddingVertical: space.xs }}>
            {entries.map((e, i) => (
              <View key={e.id} style={[styles.ev, i < entries.length - 1 && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }]}>
                <View style={{ width: timeW }}><T v="callout" color={t.ink3} numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.6} style={{ fontVariant: ['tabular-nums'] }}>{e.time}</T></View>
                <View style={{ flex: 1, gap: 4 }}>
                  <View style={{ flexDirection: 'row', gap: 6 }}><Pill label={kindLabel(e.kind) ?? e.kind} /><SourcePill source={e.groupId} /></View>
                  <T v="body">{e.text}</T>
                </View>
              </View>
            ))}
          </Card>
        </>
      ) : null}
      <T v="caption" color={t.ink3} style={{ marginTop: space.lg, paddingHorizontal: space.xs }}>{L('审批、后台任务和定时任务只在「今天」显示。', 'Approvals, background tasks and scheduled jobs only show on Today.')}</T>
    </>
  );
}

export function TodayScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const route = useRoute<any>();
  const focused = useIsFocused();
  const { inbox, inboxRecent, receipts, feed: allFeed, schedule, scheduleEditable, remember, rememberErrors, upcoming, groups, sideChats, dismissFeed, markFeedSeen, tasks, connected, booting, reload, refreshLive, loading, dataErrors } = useStore();
  const feed = shownCards(allFeed);
  const [showOff, setShowOff] = useState(false);
  const [offset, setOffset] = useState(0);
  const [dayRefresh, setDayRefresh] = useState(0);
  const bg = tasks.filter((x) => x.status === '进行中').slice(0, 3);
  const iso = isoOf(0);
  const dayIso = isoOf(offset);
  const tomorrows = schedule.filter((e) => e.date != null && e.date > iso && (e.kind === 'class' || e.kind === 'event') && !e.skip);
  const scheduleChanged = () => { reload('schedule', 'remember').catch(() => {}); };
  const on = upcoming.filter((u) => u.enabled);
  const off = upcoming.filter((u) => !u.enabled);
  // 等你点头的 + 这次点过头的回执（今天的），新的在上面。回执和原来的卡同一个位置，点完原地收成一行。
  const done = receiptItems(receipts, inboxRecent, inbox, iso);
  const asks = [...inbox, ...done].sort(byNewest);
  const waiting = done.some((i) => i.status === 'approved' || i.status === 'revising');

  // —— 位置簿记 ——
  const scroller = useRef<ScrollView>(null);
  const lay = useRef<Layout>({ pad: 0, inbox: 0, feed: 0, items: {}, scrollY: 0, viewH: 0 });
  const place = (key: string) => (e: LayoutChangeEvent) => { const { y, height } = e.nativeEvent.layout; lay.current.items[key] = { y, h: height }; };

  // —— 新卡片：在屏幕里停留 1.5 秒算看过（只在「今天」、页面在前台时） ——
  const live2 = useRef({ focused, offset, feed, markFeedSeen });
  useEffect(() => { live2.current = { focused, offset, feed, markFeedSeen }; });
  const seenTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const checkSeen = useCallback(() => {
    const { focused: f, offset: o, feed: items, markFeedSeen: mark } = live2.current;
    const l = lay.current;
    if (!f || o !== 0 || !l.viewH || AppState.currentState !== 'active') return;
    const top = l.scrollY;
    const bottom = l.scrollY + l.viewH;
    const ids = items.filter((x) => x.seen === false).filter((x) => {
      const it = l.items[`card:${x.id}`];
      if (!it) return false;
      const y0 = l.pad + l.feed + it.y;
      return Math.min(bottom, y0 + it.h) - Math.max(top, y0) >= Math.min(it.h * 0.5, 160);
    }).map((x) => x.id);
    if (ids.length) mark(ids);
  }, []);
  const scheduleSeen = useCallback(() => {
    if (seenTimer.current) clearTimeout(seenTimer.current);
    seenTimer.current = setTimeout(checkSeen, 1500);
  }, [checkSeen]);
  useEffect(() => {
    if (focused && offset === 0) scheduleSeen();
    return () => { if (seenTimer.current) clearTimeout(seenTimer.current); };
  }, [focused, offset, feed, scheduleSeen]);

  // 回到这一页时，有回执还在等结果：看看做完没有
  useEffect(() => { if (focused && waiting && connected) reload('inboxRecent').catch(() => {}); }, [focused, waiting, connected, reload]);

  // —— 从通知 / 小窗点进来：回到今天，滚到那张卡，闪一下金边 ——
  const [flash, setFlash] = useState<{ key: string; token: number } | null>(null);
  const hl = route.params?.highlight as { kind: 'card' | 'inbox'; id: string } | undefined;
  const hlAt = (route.params?.at as number | undefined) ?? 0;
  // 新的一次跳转：先回到今天（渲染时就调整，不在 effect 里 setState）
  const [seenAt, setSeenAt] = useState(hlAt);
  if (hlAt !== seenAt) {
    setSeenAt(hlAt);
    if (hlAt) setOffset(0);
  }
  const hlRef = useRef(hl);
  // 现在页面上有哪些卡（量过的位置可能是早就划掉的那张留下的）
  const shown = useRef(new Set<string>());
  useEffect(() => {
    hlRef.current = hl;
    shown.current = new Set([...asks.map((i) => `inbox:${i.id}`), ...feed.map((f) => `card:${f.id}`)]);
  });
  useEffect(() => {
    if (!hlAt) return undefined;
    const target = hlRef.current;
    if (!target) { scroller.current?.scrollTo({ y: 0, animated: true }); return undefined; }
    const key = `${target.kind}:${target.id}`;
    let tries = 0;
    let timer: ReturnType<typeof setTimeout>;
    const tick = () => {
      const y = shown.current.has(key) ? yOf(lay.current, key) : null;
      if (y != null) {
        scroller.current?.scrollTo({ y: Math.max(0, y - 96), animated: true });
        setFlash({ key, token: Date.now() });
        return;
      }
      // 卡片可能还在读（推送刚到）：等一会儿再找，最多 8 秒；找不到就停在「等你点头」那一段
      if (++tries < 40) { timer = setTimeout(tick, 200); return; }
      if (target.kind === 'inbox') scroller.current?.scrollTo({ y: Math.max(0, lay.current.pad + lay.current.inbox - 48), animated: true });
    };
    timer = setTimeout(tick, 150);
    return () => clearTimeout(timer);
  }, [hlAt]);
  const flashFor = (key: string) => (flash?.key === key ? flash.token : 0);

  const refreshKeys: DataKey[] = ['inbox', 'unread', 'upcoming', 'tasks', 'feed', 'schedule', 'remember', 'journal', 'wake', ...(done.length ? ['inboxRecent' as const] : [])];
  return (
    <Screen>
      <ScrollView ref={scroller} contentContainerStyle={{ paddingBottom: space.xxl }} scrollEventThrottle={100}
        onScroll={(e) => { lay.current.scrollY = e.nativeEvent.contentOffset.y; scheduleSeen(); }}
        onLayout={(e) => { lay.current.viewH = e.nativeEvent.layout.height; scheduleSeen(); }}
        refreshControl={<PullRefresh onRefresh={() => { if (offset !== 0) setDayRefresh((n) => n + 1); return connected ? reload(...refreshKeys) : refreshLive(); }} />}>
        <LargeHeader title={titleOf(offset)} sub={subOf(offset)} right={(
          <View style={styles.nav}>
            <Pressable onPress={() => setOffset((o) => o - 1)} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('前一天', 'Previous day')} style={({ pressed }) => [styles.navBtn, { backgroundColor: t.surface2, opacity: pressed ? 0.6 : 1 }]}><ChevronLeft size={20} color={t.ink} /></Pressable>
            <Pressable onPress={() => setOffset((o) => o + 1)} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('后一天', 'Next day')} style={({ pressed }) => [styles.navBtn, { backgroundColor: t.surface2, opacity: pressed ? 0.6 : 1 }]}><ChevronRight size={20} color={t.ink} /></Pressable>
          </View>
        )} />
        <View style={{ paddingHorizontal: space.lg }} onLayout={(e) => { lay.current.pad = e.nativeEvent.layout.y; }}>
          {offset !== 0 ? (
            <Pressable onPress={() => setOffset(0)} accessibilityRole="button" style={{ alignSelf: 'flex-start', paddingVertical: 2, paddingHorizontal: space.xs }}>
              <T v="callout" color={t.gold} style={{ fontWeight: '600' }}>{L('回到今天', 'Back to today')}</T>
            </Pressable>
          ) : null}
          {!connected ? (
            <Card style={{ marginTop: space.md }}><T v="callout" color={t.ink2}>{booting
              ? L('正在连服务器…', 'Connecting to the server…')
              : L('没连上服务器。检查「我 → 服务器」后下拉刷新。', 'Not connected to the server. Check Me → Server, then pull down to refresh.')}</T></Card>
          ) : null}
          {offset !== 0 ? <DayView iso={dayIso} offset={offset} refreshKey={dayRefresh} /> : (<>

          <WakeCard />
          <SectionLabel right={(
            <Pressable onPress={() => nav.navigate('Inbox')} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('已处理', 'Handled')} style={styles.more}>
              <T v="callout" color={t.gold} style={{ fontWeight: '600' }}>{L('已处理', 'Handled')}</T>
              <ChevronRight size={16} color={t.gold} />
            </Pressable>
          )}>{inbox.length ? L(`等你点头 · ${inbox.length}`, `Needs your OK · ${inbox.length}`) : L('等你点头', 'Needs your OK')}</SectionLabel>
          {asks.length ? (
            <View style={{ gap: space.md }} onLayout={(e) => { lay.current.inbox = e.nativeEvent.layout.y; }}>
              {asks.map((it) => (
                <View key={it.id} onLayout={place(`inbox:${it.id}`)}>
                  <InboxCard item={it} />
                  {flashFor(`inbox:${it.id}`) ? <Flash token={flashFor(`inbox:${it.id}`)} round={it.status === 'pending' ? radius.lg : 14} /> : null}
                </View>
              ))}
            </View>
          ) : (
            <Card>
              <T v="callout" color={dataErrors.inbox ? t.bad : t.ink2}>
                {dataErrors.inbox
                  ? L(`读不到收件箱：${dataErrors.inbox}`, `Couldn't load the inbox: ${dataErrors.inbox}`)
                  : L('没有等你点头的事。需要你决定的事会出现在这里。', 'Nothing waiting for your OK. Anything that needs your decision will show up here.')}
              </T>
            </Card>
          )}

          {bg.length ? (
            <>
              <SectionLabel right={<Pressable onPress={() => nav.navigate('Tasks')} accessibilityRole="button"><T v="caption" color={t.gold}>{L('全部', 'All')}</T></Pressable>}>{L('后台在做的', 'Running in the background')}</SectionLabel>
              <Card style={{ paddingVertical: space.xs }}>
                {bg.map((x, i) => (
                  <ListRow key={x.id} title={x.title} last={i === bg.length - 1} onPress={() => nav.navigate('Task', { id: x.id })}
                    icon={<LoaderCircle size={18} color={t.cyan} />}
                    sub={`${originName(x.origin, groups, sideChats)} → ${modelName(x.modelId)}${x.lastTool ? L(` · 正在用 ${x.lastTool}`, ` · using ${x.lastTool}`) : ''}`} />
                ))}
              </Card>
            </>
          ) : null}

          {connected ? (
            <>
              <SectionLabel right={scheduleEditable ? <AddScheduleButton day={iso} past={false} onChanged={scheduleChanged} /> : <Pill label={L('日历', 'Calendar')} tone="good" />}>{L('日程', 'Schedule')}</SectionLabel>
              {dataErrors.schedule ? <Card style={{ marginBottom: space.sm }}><T v="callout" color={t.bad}>{L(`读不到日程：${dataErrors.schedule}`, `Couldn't load the schedule: ${dataErrors.schedule}`)}</T></Card> : null}
              <ScheduleCard events={schedule} day={iso} today={iso} editable={scheduleEditable} onChanged={scheduleChanged}
                empty={loading.schedule && !schedule.length ? L('正在读…', 'Loading…') : L('今天还没有安排。', 'Nothing scheduled today.')} />
              {tomorrows.length ? (
                <Pressable onPress={() => setOffset(1)} accessibilityRole="button" style={{ marginTop: space.sm, paddingHorizontal: space.xs }}>
                  <T v="callout" color={t.ink3}>{L(
                    `明天 ${tomorrows.length} 个日程，第一个 ${tomorrows[0].start}。`,
                    `Tomorrow: ${tomorrows.length} event${tomorrows.length === 1 ? '' : 's'}, first at ${tomorrows[0].start}. `,
                  )}<T v="callout" color={t.gold}>{L('看明天', 'See tomorrow')}</T></T>
                </Pressable>
              ) : null}

              {scheduleEditable || remember.length ? (
                <>
                  <SectionLabel>{L('要记得的', 'To remember')}</SectionLabel>
                  {dataErrors.remember ? <Card style={{ marginBottom: space.sm }}><T v="callout" color={t.bad}>{L(`读不到：${dataErrors.remember}`, `Couldn't load: ${dataErrors.remember}`)}</T></Card> : null}
                  <RememberCard items={remember} errors={rememberErrors} today={iso} />
                </>
              ) : null}
              <StudyTodayRow />
            </>
          ) : null}

          <SectionLabel>{L(`${agentName()} 的建议`, `Suggestions from ${agentName()}`)}</SectionLabel>
          {feed.length ? (
            <View style={{ gap: space.md }} onLayout={(e) => { lay.current.feed = e.nativeEvent.layout.y; scheduleSeen(); }}>
              {feed.map((f) => (
                <View key={f.id} onLayout={place(`card:${f.id}`)}>
                  <FeedCard f={f} onDismiss={() => dismissFeed(f.id)} isNew={f.seen === false} />
                  {flashFor(`card:${f.id}`) ? <Flash token={flashFor(`card:${f.id}`)} round={radius.lg} /> : null}
                </View>
              ))}
            </View>
          ) : (
            <Card><T v="callout" color={t.ink2}>{L('还没有建议。起床报告和主动提醒会出现在这里。', 'No suggestions yet. Morning reports and proactive reminders will show up here.')}</T></Card>
          )}

          <SectionLabel>{L('接下来会自动做的事', 'Coming up automatically')}</SectionLabel>
          {upcoming.length ? (
            <>
              <Card style={{ paddingVertical: space.xs }}>
                {on.map((u, i) => <UpcomingRow key={u.id} u={u} last={i === on.length - 1} />)}
              </Card>
              {off.length ? (
                <>
                  <Pressable onPress={() => setShowOff((v) => !v)} accessibilityRole="button" style={{ paddingVertical: space.sm, paddingHorizontal: space.xs }}>
                    <T v="caption" color={t.gold}>{showOff
                      ? L('收起停用的', 'Hide disabled')
                      : L(`还有 ${off.length} 个停用的定时任务`, `${off.length} more disabled scheduled job${off.length === 1 ? '' : 's'}`)}</T>
                  </Pressable>
                  {showOff ? <Card style={{ paddingVertical: space.xs }}>{off.map((u, i) => <UpcomingRow key={u.id} u={u} last={i === off.length - 1} />)}</Card> : null}
                </>
              ) : null}
            </>
          ) : (
            <Card><T v="callout" color={dataErrors.upcoming ? t.bad : t.ink2}>{dataErrors.upcoming
              ? L(`读不到定时任务：${dataErrors.upcoming}`, `Couldn't load scheduled jobs: ${dataErrors.upcoming}`)
              : loading.upcoming ? L('正在读…', 'Loading…') : connected ? L('没有定时任务。', 'No scheduled jobs.') : L('没连上服务器。', 'Not connected to the server.')}</T></Card>
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
  more: { flexDirection: 'row', alignItems: 'center', gap: 2 },
  newPill: { height: 20, borderRadius: 10, paddingHorizontal: 8, alignItems: 'center', justifyContent: 'center' },
  newText: { fontSize: 11, fontWeight: '700' },
  navBtn: { width: 36, height: 36, borderRadius: 18, alignItems: 'center', justifyContent: 'center' },
});
