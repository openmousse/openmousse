// 「今天」页（2026-10-13 重做，画布第 5 排）：顶上 日 / 周 / 月。
// 日 = 三块，各按一条标准，一件事只在一块里：待处理（要动手）、日程（要到场）、报告（要阅读）；没有内容的块整块不显示。
// 周 = 按天列表，月 = 日历。数据由服务器分好组（server/today.py），app 只画。定时任务、后台任务不在这一页。
// 服务器没有这套（supports('today') 为 false）时用老页面 TodayLegacy。
// 2026-10-15 类别（服务器开着 today.categories 时）：日视图顶上一排筛选（全部 / 学习 / 生活 / 工作），待处理和日程按类别上色；
// 报告里多一张工程看板卡（只读，点进 EngBoard）。
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AppState, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useIsFocused, useRoute } from '@react-navigation/native';
import { cachedDay, loadDay, type DayEvent, type TodayDay } from '../api/today';
import { supports } from '../api/version';
import { ChevronLeft, ChevronRight } from '../components/icons';
import { useSheet } from '../components/Sheet';
import { Btn, Card, LargeHeader, PullRefresh, Screen, T } from '../components/ui';
import { WakeCard } from '../components/WakeCard';
import { EventDetail, ScheduleBlock } from '../components/today/DaySchedule';
import { InboxLive, PendingBlock } from '../components/today/Pending';
import { MonthView, WeekView } from '../components/today/Range';
import { ReportsBlock, useOpenReport } from '../components/today/Reports';
import { CATS, CatFilter, type CatFilterValue } from '../components/today/Cats';
import { nowMinute } from '../components/today/ring';
import { addDays, dayTitle, daySub, toMin } from '../components/today/parts';
import { L } from '../i18n';
import { todayIso, useStore } from '../store';
import { radius, space, useTheme } from '../theme';
import { TodayLegacy } from './TodayLegacy';

type Mode = 'day' | 'week' | 'month';
const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

export function TodayScreen() {
  const { connected } = useStore();
  return connected && !supports('today') ? <TodayLegacy /> : <TodayPage />;
}

/** 一天的三块（按日子缓存，先画上次的）；iso 为 null = 今天（服务器按用户的时区算）。bump 变了就重读。
 * at = 读回来的时刻（现在线按它对准）；画的是别处留下的缓存（还没读到）就是 0，现在线改用设备的钟。 */
function useDay(iso: string | null, connected: boolean, bump: number) {
  const [got, setGot] = useState<{ iso: string | null; data: TodayDay | null; error: string; at: number } | null>(null);
  useEffect(() => {
    if (!connected) return undefined;
    let alive = true;
    loadDay(iso).then((d) => { if (alive) setGot({ iso, data: d, error: '', at: Date.now() }); })
      .catch((e) => {
        if (!alive) return;
        // 没读到，画缓存：缓存还是上次读到的那一份，就留着那次的 at（服务器的时刻和 at 是一对），不然是 0
        const data = cachedDay(iso);
        setGot((g) => ({ iso, data, error: errText(e), at: g && g.iso === iso && g.data === data ? g.at : 0 }));
      });
    return () => { alive = false; };
  }, [iso, connected, bump]);
  const mine = got && got.iso === iso ? got : null;
  return { data: mine?.data ?? cachedDay(iso), error: mine?.error ?? '', at: mine?.at ?? 0 };
}

function TodayPage() {
  const t = useTheme();
  const route = useRoute<any>();
  const focused = useIsFocused();
  const sheet = useSheet();
  const openReport = useOpenReport();
  const { connected, booting, inbox, feed, schedule, remember, reload, refreshLive } = useStore();
  const [mode, setMode] = useState<Mode>('day');
  const [filter, setFilter] = useState<CatFilterValue>('all');  // 顶上选的类别（开着类别时）
  const [picked, setPicked] = useState<string | null>(null);  // 翻到的日子；null = 今天
  const [bump, setBump] = useState(0);
  const [visit, setVisit] = useState(0);
  const [clock, setClock] = useState(0);
  const scroller = useRef<ScrollView>(null);

  // 收件箱、卡片、日程有变（推送来了、Agent 改了、别的页改了）就重读；回到这一页也重读，这次勾过的收起来
  const sig = [inbox, feed, schedule, remember];
  const [prevSig, setPrevSig] = useState(sig);
  if (sig.some((x, i) => x !== prevSig[i])) { setPrevSig(sig); setBump((n) => n + 1); }
  const [wasFocused, setWasFocused] = useState(focused);
  if (focused !== wasFocused) {
    setWasFocused(focused);
    if (focused) { setBump((n) => n + 1); setVisit((n) => n + 1); }
  }
  useEffect(() => {
    const sub = AppState.addEventListener('change', (st) => { if (st === 'active') { setClock(Date.now()); setBump((n) => n + 1); } });
    return () => sub.remove();
  }, []);
  // 现在线每 30 秒往后走；在这一页上每 5 分钟重读一次（「进行中」「已结束」跟着变）
  useEffect(() => {
    if (!focused) return undefined;
    let n = 0;
    const id = setInterval(() => { setClock(Date.now()); if (++n % 10 === 0) setBump((b) => b + 1); }, 30_000);
    return () => clearInterval(id);
  }, [focused]);

  const { data, error, at } = useDay(picked, connected, bump);
  // 「今天」以服务器为准（手机和服务器的时区可能不同），还没读到时先用手机的日子
  const today = data?.today || cachedDay(null)?.today || todayIso();
  const day = picked ?? today;
  const setDay = (iso: string) => setPicked(iso === today ? null : iso);
  const changed = useCallback(() => { setBump((n) => n + 1); reload('schedule', 'remember').catch(() => {}); }, [reload]);
  const serverNow = data?.now ? toMin(data.now) : null;
  const nowMin = serverNow != null ? nowMinute(serverNow, at, Math.max(clock, at)) : null;  // 跟手机的钟，按服务器时区对齐

  const openEvent = (e: DayEvent) => sheet.open({ title: e.title, content: (close) => <EventDetail e={e} past={e.past || day < today} close={close} onChanged={changed} /> });
  // 从推送 / 小窗点进来：回到今天的日视图，打开那张卡（收件箱的卡、报告，或归在日程里的计划和复盘）
  const hl = route.params?.highlight as { kind: 'card' | 'inbox'; id: string } | undefined;
  const hlAt = (route.params?.at as number | undefined) ?? 0;
  const [seenAt, setSeenAt] = useState(hlAt);
  if (hlAt !== seenAt) {
    setSeenAt(hlAt);
    if (hlAt) { setMode('day'); setPicked(null); }
  }
  const handled = useRef(0);
  useEffect(() => {
    if (!hlAt || handled.current === hlAt || !data || picked !== null) return;
    handled.current = hlAt;
    scroller.current?.scrollTo({ y: 0, animated: true });
    if (!hl) return;
    if (hl.kind === 'inbox') { sheet.open({ title: L('待确认', 'Needs your approval'), content: () => <InboxLive id={hl.id} /> }); return; }
    const rep = data.reports.find((r) => r.type === 'card' && r.id === hl.id);
    if (rep) { openReport(rep); return; }
    const ev = [...data.schedule.items, ...data.schedule.allDay].find((e) => e.cards?.some((c) => c.id === hl.id));
    if (ev) openEvent(ev);
  });

  const isDay = mode === 'day';
  const pickDay = (iso: string) => { setDay(iso); setMode('day'); scroller.current?.scrollTo({ y: 0, animated: false }); };
  const refresh = () => (connected ? reload('inbox', 'feed', 'schedule', 'remember', 'wake').then(() => setBump((n) => n + 1)) : refreshLive());
  const empty = !!data && !data.pending.items.length && !data.pending.nodate.length && !data.pending.stale.length && !data.schedule.items.length && !data.schedule.allDay.length && !data.reports.length;
  const issues = data ? Object.values(data.errors).filter(Boolean) : [];
  // 类别：服务器开着才有。筛选的条数 = 待处理（含无日期）里每类几件；「全部」连等你点头的一起数
  const cats = !!data?.categories.on;
  const counted = data ? [...data.pending.items, ...data.pending.nodate] : [];
  const counts = { all: counted.length, ...Object.fromEntries(CATS.map((c) => [c, counted.filter((r) => r.category === c).length])) } as Record<CatFilterValue, number>;
  return (
    <Screen>
      <ScrollView ref={scroller} contentContainerStyle={{ paddingBottom: space.xxl }} refreshControl={<PullRefresh onRefresh={refresh} />}>
        <LargeHeader title={isDay ? dayTitle(day, today) : L('今天', 'Today')} sub={daySub(isDay ? day : today)} right={isDay ? (
          <View style={styles.nav}>
            <Pressable onPress={() => setDay(addDays(day, -1))} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('前一天', 'Previous day')}
              style={({ pressed }) => [styles.navBtn, { backgroundColor: t.surface2, opacity: pressed ? 0.6 : 1 }]}><ChevronLeft size={20} color={t.ink} /></Pressable>
            <Pressable onPress={() => setDay(addDays(day, 1))} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('后一天', 'Next day')}
              style={({ pressed }) => [styles.navBtn, { backgroundColor: t.surface2, opacity: pressed ? 0.6 : 1 }]}><ChevronRight size={20} color={t.ink} /></Pressable>
          </View>
        ) : undefined} />
        <View style={{ paddingHorizontal: space.lg }}>
          <ModeSwitch value={mode} onChange={setMode} />
          {cats && isDay && data ? <CatFilter value={filter} onChange={setFilter} counts={counts} /> : null}
          {isDay && day !== today ? (
            <Pressable onPress={() => setPicked(null)} accessibilityRole="button" hitSlop={6} style={styles.back}>
              <T v="callout" color={t.gold} style={{ fontWeight: '600' }}>{L('返回今天', 'Back to today')}</T>
            </Pressable>
          ) : null}
          {!connected ? (
            <Card style={{ marginTop: space.lg }}><T v="callout" color={t.ink2}>{booting
              ? L('正在连接服务器…', 'Connecting to the server…')
              : L('未连接服务器。请检查「设置 → 我的 claw」，然后下拉刷新。', 'Not connected to the server. Check Settings → My claws, then pull down to refresh.')}</T></Card>
          ) : mode === 'week' ? <WeekView anchor={day} today={today} bump={bump} onAnchor={setDay} onPickDay={pickDay} cats={cats} />
            : mode === 'month' ? <MonthView anchor={day} today={today} bump={bump} onAnchor={setDay} onPickDay={pickDay} cats={cats} />
              : !data ? (
                error ? (
                  <Card style={{ marginTop: space.lg, gap: space.md }}>
                    <T v="callout" color={t.bad}>{L(`无法加载：${error}`, `Couldn't load: ${error}`)}</T>
                    <Btn label={L('重试', 'Retry')} kind="quiet" onPress={() => setBump((n) => n + 1)} />
                  </Card>
                ) : <T v="callout" color={t.ink2} style={styles.note}>{L('正在加载…', 'Loading…')}</T>
              ) : (
                <>
                  {day === today ? <WakeCard /> : null}
                  <PendingBlock key={`p:${day}:${visit}`} rows={data.pending.items} nodate={data.pending.nodate} stale={data.pending.stale} day={data.day} today={data.today}
                    cats={cats} filter={filter} onChanged={changed} />
                  <ScheduleBlock events={data.schedule.items} allDay={data.schedule.allDay} day={data.day} today={data.today} now={nowMin} onChanged={changed} onOpen={openEvent}
                    cats={cats} filter={filter} />
                  <ReportsBlock key={`r:${day}:${visit}`} reports={data.reports} />
                  {empty ? <T v="callout" color={t.ink3} style={styles.note}>{day === today ? L('今日暂无待处理事项、日程和报告。', 'Nothing to do, attend or read today.') : L('当日暂无事项。', 'Nothing on this day.')}</T> : null}
                  {error ? <T v="caption" color={t.bad} style={styles.note}>{L(`未能刷新：${error}`, `Couldn't refresh: ${error}`)}</T> : null}
                  {issues.length ? <T v="caption" color={t.ink3} style={styles.note}>{L(`部分内容未能加载：${issues.join('；')}`, `Some items couldn't load: ${issues.join('; ')}`)}</T> : null}
                </>
              )}
        </View>
      </ScrollView>
    </Screen>
  );
}

/** 日 / 周 / 月。 */
function ModeSwitch({ value, onChange }: { value: Mode; onChange: (m: Mode) => void }) {
  const t = useTheme();
  const opts: { v: Mode; label: string }[] = [{ v: 'day', label: L('日', 'Day') }, { v: 'week', label: L('周', 'Week') }, { v: 'month', label: L('月', 'Month') }];
  return (
    <View style={[styles.seg, { backgroundColor: t.surface }]} accessibilityRole="tablist" accessibilityLabel={L('视图', 'View')}>
      {opts.map((o) => {
        const on = o.v === value;
        return (
          <Pressable key={o.v} onPress={() => onChange(o.v)} accessibilityRole="tab" accessibilityState={{ selected: on }}
            style={[styles.segItem, on && { backgroundColor: t.mode === 'dark' ? t.line : t.surface2 }]}>
            <Text style={[styles.segText, { color: on ? t.ink : t.ink2 }]}>{o.label}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  nav: { flexDirection: 'row', gap: 8, marginBottom: 2 },
  navBtn: { width: 36, height: 36, borderRadius: 18, alignItems: 'center', justifyContent: 'center' },
  seg: { flexDirection: 'row', borderRadius: radius.md, padding: 3, gap: 3 },
  segItem: { flex: 1, height: 32, borderRadius: 9, alignItems: 'center', justifyContent: 'center' },
  segText: { fontSize: 14, fontWeight: '600' },
  back: { alignSelf: 'flex-start', paddingTop: space.sm, paddingHorizontal: space.xs },
  note: { marginTop: space.lg, paddingHorizontal: space.xs },
});
