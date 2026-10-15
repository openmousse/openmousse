// 「今天」页 · 待处理（要动手的）：等你点头的、安全提醒、7 天内到期的待办和截止，右边一枚截止标签（顺序服务器排好）。
// 点圆圈完成（这次在页面上勾的留着，再点撤销，下次进来才收走）；左滑推迟一天 / 一周（底下一行能撤销）；点一行看详情。
// 没日期的收在末尾「无日期 · N」，点开展开。别的日子只列那天到期的，不推迟。
// 开着类别（服务器 today.categories，2026-10-15）：圆圈颜色 = 类别，灰字前面写类别，顶上的筛选只留那一类；
// 左滑多一个「不用管」（和做完分开记）；过期超过 7 天的收成一行「过期没处理 · N」；点开一条能改类别。
import React, { useRef, useState } from 'react';
import { Linking, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { agentName } from '../../brand';
import * as todayApi from '../../api/today';
import type { PendingRow } from '../../api/today';
import type { TodoCat } from '../../data/types';
import { L } from '../../i18n';
import { openTarget, openThread } from '../../navigation';
import { useStore } from '../../store';
import { radius, space, type, useTheme } from '../../theme';
import { Check, ChevronRight, Clock, GraduationCap, TriangleAlert, X } from '../icons';
import { InboxCard } from '../InboxCard';
import { useSheet } from '../Sheet';
import { SourceBadge } from '../SourceBadge';
import { reviewLine, useStudyHome } from '../StudyBoard';
import { Btn, Disclosure, T, showError } from '../ui';
import { CatDots, CatPills, type CatFilterValue, catColor, catLabel } from './Cats';
import { BlockHeader, Chip, addDays, monthDay } from './parts';

/** 底下那一行能撤销的：推迟到哪天 / 标了不用管。 */
type Undo = { row: PendingRow; kind: 'snooze'; until: string } | { row: PendingRow; kind: 'dismiss' };

export function PendingBlock({ rows, nodate, stale = [], day, today, cats = false, filter = 'all', onChanged }: {
  rows: PendingRow[]; nodate: PendingRow[]; stale?: PendingRow[]; day: string; today: string;
  /** 服务器开着类别 */
  cats?: boolean;
  /** 顶上选的类别（只在 cats 时有用） */
  filter?: CatFilterValue;
  /** 改了类别以后重读这一页 */
  onChanged?: () => void;
}) {
  const t = useTheme();
  const sheet = useSheet();
  const nav = useNavigation<any>();
  const { receipts } = useStore();
  const isToday = day === today;
  const [ticked, setTicked] = useState<Record<string, boolean>>({});
  const [hidden, setHidden] = useState<Record<string, true>>({});
  const [undo, setUndo] = useState<Undo | null>(null);
  const [showNodate, setShowNodate] = useState(false);
  const [showStale, setShowStale] = useState(false);
  const study = useStudyHome().data;
  const review = isToday && study ? reviewLine(study) : null;
  // 刚在这台设备上点过头的收件箱卡：服务器那份还没重读，先藏起来
  const decided = new Set(receipts.map((r) => r.item.id));
  const keep = (r: PendingRow) => !cats || filter === 'all' || r.category === filter;
  const visible = rows.filter((r) => !hidden[r.id] && !(r.kind === 'inbox' && r.inboxId && decided.has(r.inboxId)));
  const shown = visible.filter(keep);
  const later = nodate.filter((r) => !hidden[r.id] && keep(r));
  const old = cats ? stale.filter((r) => !hidden[r.id] && keep(r)) : [];
  const withReview = !!review && (!cats || filter === 'all' || filter === 'study');
  const count = shown.length + (withReview ? 1 : 0) + (cats ? later.length : 0);
  const anything = visible.length + nodate.length + stale.length + (review ? 1 : 0) > 0;
  if (!count && !later.length && !old.length && !undo && !(cats && filter !== 'all' && anything)) return null;

  const tick = async (r: PendingRow) => {
    const on = !(ticked[r.id] ?? r.done);
    setTicked((m) => ({ ...m, [r.id]: on }));
    try { await todayApi.tick(r, on); } catch (e) {
      setTicked((m) => { const n = { ...m }; delete n[r.id]; return n; });
      showError(L('标记失败', "Couldn't tick off"), e);
    }
  };
  const hide = (r: PendingRow) => setHidden((m) => ({ ...m, [r.id]: true }));
  const unhide = (r: PendingRow) => setHidden((m) => { const n = { ...m }; delete n[r.id]; return n; });
  const postpone = async (r: PendingRow, days: number) => {
    if (!r.ref) return;
    const until = addDays(today, days);
    hide(r);
    setUndo({ row: r, kind: 'snooze', until });
    try { await todayApi.snooze(r.ref, until); } catch (e) {
      unhide(r);
      setUndo(null);
      showError(L('推迟失败', "Couldn't postpone"), e);
    }
  };
  const dismiss = async (r: PendingRow) => {
    if (!r.ref) return;
    hide(r);
    setUndo({ row: r, kind: 'dismiss' });
    try { await todayApi.tick(r, true, 'dismissed'); } catch (e) {
      unhide(r);
      setUndo(null);
      showError(L('标记失败', "Couldn't update"), e);
    }
  };
  const restore = async (u: Undo) => {
    setUndo(null);
    unhide(u.row);
    try {
      if (!u.row.ref) return;
      if (u.kind === 'snooze') await todayApi.snooze(u.row.ref, null);
      else await todayApi.tick(u.row, false);
    } catch (e) { showError(L('撤销失败', "Couldn't undo"), e); }
  };
  const open = (r: PendingRow) => {
    if (r.kind === 'inbox' && r.inboxId) {
      const id = r.inboxId;
      sheet.open({ title: L('待确认', 'Needs your approval'), content: () => <InboxLive id={id} /> });
      return;
    }
    sheet.open({ title: r.title, content: (close) => (
      <PendingDetail r={r} done={ticked[r.id] ?? r.done} snooze={isToday && r.canSnooze} cats={cats} onChanged={onChanged}
        onTick={() => { close(); tick(r); }} onPostpone={(n) => { close(); postpone(r, n); }}
        onDismiss={isToday && cats && r.canDismiss ? () => { close(); dismiss(r); } : undefined} close={close} />
    ) });
  };
  const line = (r: PendingRow, last: boolean) => (
    <PendingLine key={r.id} r={r} last={last} done={ticked[r.id] ?? r.done} swipe={isToday && r.canSnooze} cats={cats}
      onTick={() => tick(r)} onOpen={() => open(r)} onPostpone={(n) => postpone(r, n)}
      onDismiss={isToday && cats && r.canDismiss ? () => dismiss(r) : undefined} />
  );
  const sep = { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line };
  const tail = (after: 'review' | 'nodate' | 'stale') => {
    const order = ['review', 'nodate', 'stale', 'undo'] as const;
    const has = { review: withReview, nodate: later.length > 0, stale: old.length > 0, undo: !!undo };
    return order.slice(order.indexOf(after) + 1).some((k) => has[k]);
  };
  const hint = isToday && (shown.some((r) => r.canSnooze) || later.length > 0)
    ? (cats ? L('左滑推迟或不用管，点圆圈完成', 'Swipe left to postpone or dismiss') : L('左滑推迟，点圆圈完成', 'Swipe left to postpone')) : '';
  return (
    <View>
      <BlockHeader title={isToday ? L(`待处理 · ${count}`, `To do · ${count}`) : L(`当日到期 · ${count}`, `Due that day · ${count}`)} accent={isToday}
        right={hint ? <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{hint}</T> : null} />
      <View style={[styles.card, { backgroundColor: t.surface }]}>
        {shown.map((r, i) => line(r, i === shown.length - 1 && !tail('review') && !withReview))}
        {cats && !count && !later.length && !old.length && filter !== 'all' ? (
          <View style={[styles.row, (tail('review') || withReview) && sep]}>
            <T v="callout" color={t.ink3}>{L(`${catLabel(filter)}暂无待处理事项`, `Nothing to do in ${catLabel(filter).toLowerCase()}`)}</T>
          </View>
        ) : null}
        {withReview && review ? (
          <Pressable onPress={() => (study?.review?.page ? nav.navigate('StudySession', { course: study.review.course, page: study.review.page, tab: 'path' }) : nav.navigate('StudyHome', { course: study?.review?.course }))}
            accessibilityRole="button" style={({ pressed }) => [styles.row, tail('review') && sep, { opacity: pressed ? 0.6 : 1 }]}>
            <View style={[styles.lead, { backgroundColor: t.cyanSoft }]}><GraduationCap size={14} color={t.cyan} /></View>
            <View style={styles.mid}>
              <Text style={[styles.title, { color: t.ink }]} numberOfLines={1}>{review.title}</Text>
              {review.sub ? <Text style={[styles.sub, { color: t.ink3 }]} numberOfLines={1}>{cats ? `${catLabel('study')} · ${review.sub}` : review.sub}</Text> : null}
            </View>
            <Chip label={L('学习台', 'Study')} tone="neutral" />
          </Pressable>
        ) : null}
        {later.length ? (
          <>
            <Pressable onPress={() => setShowNodate((v) => !v)} accessibilityRole="button" accessibilityState={{ expanded: showNodate }}
              style={({ pressed }) => [styles.fold, { opacity: pressed ? 0.6 : 1 }, (showNodate || tail('nodate')) && sep]}>
              <T v="callout" color={t.ink2} style={{ flex: 1 }}>{L(`无日期 · ${later.length}`, `No date · ${later.length}`)}</T>
              {cats ? <CatDots cats={later.map((r) => r.category)} /> : null}
              <Disclosure open={showNodate} />
            </Pressable>
            {showNodate ? later.map((r, i) => line(r, i === later.length - 1 && !tail('nodate'))) : null}
          </>
        ) : null}
        {old.length ? (
          <>
            <Pressable onPress={() => setShowStale((v) => !v)} accessibilityRole="button" accessibilityState={{ expanded: showStale }}
              accessibilityLabel={L(`过期没处理 ${old.length} 条`, `${old.length} overdue for over a week`)}
              style={({ pressed }) => [styles.fold, { opacity: pressed ? 0.6 : 1 }, (showStale || tail('stale')) && sep]}>
              <T v="callout" color={t.ink2} style={{ flex: 1 }}>{L(`过期没处理 · ${old.length}`, `Overdue, not handled · ${old.length}`)}</T>
              <CatDots cats={old.map((r) => r.category)} />
              <Disclosure open={showStale} />
            </Pressable>
            {showStale ? old.map((r, i) => line(r, i === old.length - 1 && !tail('stale'))) : null}
          </>
        ) : null}
        {undo ? (
          <View style={styles.undo}>
            <T v="callout" color={t.ink2} style={{ flex: 1 }} numberOfLines={1}>{undo.kind === 'snooze'
              ? L(`已推迟到 ${monthDay(undo.until)}：${undo.row.title}`, `Postponed to ${monthDay(undo.until)}: ${undo.row.title}`)
              : L(`已标为不用管：${undo.row.title}`, `Marked as not needed: ${undo.row.title}`)}</T>
            <Pressable onPress={() => restore(undo)} hitSlop={8} accessibilityRole="button">
              <T v="callout" color={t.gold} style={{ fontWeight: '600' }}>{L('撤销', 'Undo')}</T>
            </Pressable>
          </View>
        ) : null}
      </View>
    </View>
  );
}

/** 一行：左边是圆圈（待办、截止；开着类别时是类别色）/ 盾牌（安全）/ 提的 Agent（待确认），中间标题和来源，右边截止标签。 */
function PendingLine({ r, last, done, swipe, cats, onTick, onOpen, onPostpone, onDismiss }: {
  r: PendingRow; last: boolean; done: boolean; swipe: boolean; cats: boolean;
  onTick: () => void; onOpen: () => void; onPostpone: (days: number) => void; onDismiss?: () => void;
}) {
  const t = useTheme();
  const ring = cats && r.category ? catColor(t, r.category) : null;
  const sub = cats && r.category ? [catLabel(r.category), r.sub].filter(Boolean).join(' · ') : r.sub;
  // 「不用管」的（别的日子里看得到）：不画成做完，标签照服务器写的「不用管」
  const ticked = done && !r.dismissed;
  const body = (
    <View style={[styles.row, { backgroundColor: t.surface }, !last && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }]}>
      {r.kind === 'security' ? (
        <View style={[styles.lead, { backgroundColor: t.badSoft }]}><TriangleAlert size={14} color={t.bad} /></View>
      ) : r.kind === 'inbox' ? (
        <View style={styles.leadBox}><SourceBadge source={r.agent ?? 'main'} size={24} /></View>
      ) : (
        <Pressable onPress={onTick} disabled={!r.canTick} hitSlop={10} accessibilityRole="checkbox" accessibilityState={{ checked: done }}
          accessibilityLabel={L(`标记完成：${r.title}`, `Tick off: ${r.title}`)}
          style={[styles.tick, ring ? { borderWidth: 2 } : null, {
            borderColor: ticked ? (ring ?? t.cyan) : (ring ?? t.ink3), backgroundColor: ticked ? (ring ?? t.cyan) : 'transparent',
          }]}>
          {ticked ? <Check size={14} color={t.surface} strokeWidth={3} /> : null}
        </Pressable>
      )}
      <Pressable onPress={onOpen} accessibilityRole="button" accessibilityLabel={`${r.title}${r.chip ? `，${r.chip}` : ''}`}
        style={({ pressed }) => [styles.mid, { opacity: pressed ? 0.6 : 1 }]}>
        <Text style={[styles.title, { color: done ? t.ink3 : t.ink }, ticked && { textDecorationLine: 'line-through' }]} numberOfLines={1}>{r.title}</Text>
        {sub ? <Text style={[styles.sub, { color: t.ink3 }]} numberOfLines={1}>{sub}</Text> : null}
      </Pressable>
      <Chip label={ticked ? L('已完成', 'Done') : r.chip} tone={ticked ? 'good' : r.tone} />
    </View>
  );
  const actions = [
    ...(swipe ? [
      { label: L('推迟一天', '1 day'), icon: 'clock' as const, onPress: () => onPostpone(1) },
      { label: L('推迟一周', '1 week'), icon: 'clock' as const, onPress: () => onPostpone(7) },
    ] : []),
    ...(onDismiss ? [{ label: L('不用管', 'Dismiss'), icon: 'x' as const, onPress: onDismiss }] : []),
  ];
  return actions.length ? <SwipeActions actions={actions}>{body}</SwipeActions> : body;
}

/** 左滑露出按钮（横向 ScrollView，安装包里没有手势库）。 */
function SwipeActions({ children, actions }: { children: React.ReactNode; actions: { label: string; icon: 'clock' | 'x'; onPress: () => void }[] }) {
  const t = useTheme();
  const [w, setW] = useState(0);
  const ref = useRef<ScrollView>(null);
  const each = 76;
  return (
    <View onLayout={(e) => setW(Math.round(e.nativeEvent.layout.width))}>
      {w ? (
        <ScrollView ref={ref} horizontal showsHorizontalScrollIndicator={false} bounces={false} decelerationRate="fast"
          snapToOffsets={[0, each * actions.length]} disableIntervalMomentum directionalLockEnabled>
          <View style={{ width: w }}>{children}</View>
          {actions.map((a, i) => {
            const first = i === 0 && a.icon === 'clock';
            const color = first ? t.warn : t.ink2;
            const Icon = a.icon === 'x' ? X : Clock;
            return (
              <Pressable key={a.label} onPress={() => { ref.current?.scrollTo({ x: 0, animated: true }); a.onPress(); }} accessibilityRole="button" accessibilityLabel={a.label}
                style={({ pressed }) => [styles.swipeBtn, { width: each, backgroundColor: first ? t.warnSoft : t.surface2, opacity: pressed ? 0.8 : 1 }]}>
                <Icon size={16} color={color} />
                <Text style={[type.caption, { color, fontWeight: '700' }]}>{a.label}</Text>
              </Pressable>
            );
          })}
        </ScrollView>
      ) : children}
    </View>
  );
}

/** 点开一行：详情、完成、推迟、不用管、改类别、原文、去对话里说。 */
function PendingDetail({ r, done, snooze, cats, onTick, onPostpone, onDismiss, onChanged, close }: {
  r: PendingRow; done: boolean; snooze: boolean; cats: boolean; onTick: () => void; onPostpone: (days: number) => void;
  onDismiss?: () => void; onChanged?: () => void; close: () => void;
}) {
  const t = useTheme();
  const [cat, setCat] = useState<TodoCat | null>(r.category ?? null);
  const [busy, setBusy] = useState(false);
  const pick = async (c: TodoCat) => {
    if (!r.ref || busy) return;
    const was = cat;
    setCat(c);
    setBusy(true);
    try { await todayApi.setCategory(r.ref, c); onChanged?.(); } catch (e) {
      setCat(was);
      showError(L('修改失败', "Couldn't update"), e);
    } finally { setBusy(false); }
  };
  const link = (label: string, onPress: () => void) => (
    <Pressable onPress={onPress} accessibilityRole="button" hitSlop={6} style={styles.linkRow}>
      <T v="callout" color={t.gold} style={{ fontWeight: '600', flex: 1 }}>{label}</T>
      <ChevronRight size={16} color={t.gold} />
    </Pressable>
  );
  const study = r.study?.course ? r.study : null;
  const links = (
    <View>
      {r.link ? link(r.origin === 'mail' ? L('查看邮件原文', 'Open the email') : L('查看原文', 'Open the original'),
        () => { Linking.openURL(r.link as string).catch((e) => showError(L('无法打开', "Couldn't open"), e)); }) : null}
      {study ? link(L('打开学习台', 'Open the study desk'), () => { close(); openTarget({ type: 'study', course: study.course as string, session: study.session ?? null }); }) : null}
      {r.origin === 'mail' || r.origin === 'own' ? link(cats ? L(`在对话里告诉 ${agentName()}`, `Tell ${agentName()} in chat`) : L(`信息有误？告诉 ${agentName()}`, `Incorrect? Tell ${agentName()}`),
        () => { close(); openThread('main', false, { title: r.title, ref: r.ref ?? undefined }); }) : null}
    </View>
  );
  if (!cats) {
    return (
      <View style={{ gap: space.md }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <Chip label={done && !r.dismissed ? L('已完成', 'Done') : r.chip} tone={done && !r.dismissed ? 'good' : r.tone} />
          <T v="callout" color={t.ink2} style={{ flex: 1 }} numberOfLines={2}>{r.sub}</T>
        </View>
        {r.detail ? <View style={[styles.box, { backgroundColor: t.surface }]}><T v="callout" color={t.ink}>{r.detail}</T></View> : null}
        {r.canTick || snooze ? (
          <View style={{ flexDirection: 'row', gap: space.sm }}>
            {snooze ? <Btn label={L('推迟一天', 'Postpone a day')} kind="quiet" onPress={() => onPostpone(1)} flex /> : null}
            {snooze ? <Btn label={L('推迟一周', 'Postpone a week')} kind="quiet" onPress={() => onPostpone(7)} flex /> : null}
            {r.canTick ? <Btn label={done ? L('撤销完成', 'Not done') : L('完成', 'Done')} kind={done ? 'quiet' : 'primary'} onPress={onTick} flex /> : null}
          </View>
        ) : null}
        {links}
      </View>
    );
  }
  const when = r.date ? `${monthDay(r.date)}${r.time ? ` ${r.time}` : L(' · 全天', ' · all day')}` : L('无日期', 'No date');
  const facts: [string, string][] = [
    [L('日子', 'Date'), r.overdueDays ? `${when} · ${r.chip}` : when],
    [L('来源', 'From'), [r.sub, r.kept ? L('原条目已过期，按快照显示', 'the original has expired; kept as a snapshot') : ''].filter(Boolean).join(' · ') || L('自己添加', 'Added by you')],
    ...(r.detail ? [[L('说明', 'Details'), r.detail] as [string, string]] : []),
  ];
  return (
    <View style={{ gap: space.md }}>
      <CatPills value={cat} onChange={pick} disabled={busy || !r.ref} />
      <View style={[styles.facts, { backgroundColor: t.surface }]}>
        {facts.map(([k, v], i) => (
          <View key={k} style={[styles.fact, i < facts.length - 1 && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }]}>
            <T v="caption" color={t.ink3} style={styles.factKey}>{k}</T>
            <T v="callout" color={t.ink} style={{ flex: 1 }}>{v}</T>
          </View>
        ))}
      </View>
      {r.canTick ? <Btn label={done ? L('撤销完成', 'Not done') : L('做完', 'Done')} kind={done ? 'quiet' : 'primary'} onPress={onTick} /> : null}
      {snooze || onDismiss ? (
        <View style={{ flexDirection: 'row', gap: space.sm }}>
          {snooze ? <Btn label={L('推迟一天', 'A day')} kind="quiet" onPress={() => onPostpone(1)} flex /> : null}
          {snooze ? <Btn label={L('推迟一周', 'A week')} kind="quiet" onPress={() => onPostpone(7)} flex /> : null}
          {onDismiss ? <Btn label={L('不用管', 'Dismiss')} kind="quiet" onPress={onDismiss} flex /> : null}
        </View>
      ) : null}
      {links}
    </View>
  );
}

/** 待确认的卡：从 store 里拿最新的（点过同意之后变成回执）。 */
export function InboxLive({ id }: { id: string }) {
  const t = useTheme();
  const { inbox, inboxRecent, receipts } = useStore();
  const item = inbox.find((i) => i.id === id) ?? receipts.find((r) => r.item.id === id)?.item ?? inboxRecent.find((i) => i.id === id);
  if (!item) return <T v="callout" color={t.ink2}>{L('这一项已处理。', 'This item has been handled.')}</T>;
  return <InboxCard item={item} />;
}

const styles = StyleSheet.create({
  card: { borderRadius: radius.lg, overflow: 'hidden' },
  row: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 14, paddingVertical: 11, minHeight: 56 },
  lead: { width: 24, height: 24, borderRadius: 12, alignItems: 'center', justifyContent: 'center', flexShrink: 0 },
  leadBox: { width: 24, alignItems: 'center', flexShrink: 0 },
  tick: { width: 22, height: 22, marginHorizontal: 1, borderRadius: 11, borderWidth: 1.5, alignItems: 'center', justifyContent: 'center', flexShrink: 0 },
  mid: { flex: 1, minWidth: 0, gap: 2 },
  title: { fontSize: 15, lineHeight: 20 },
  sub: { fontSize: 12, lineHeight: 16 },
  fold: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 14, height: 46 },
  undo: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingHorizontal: 14, paddingVertical: 12 },
  swipeBtn: { alignItems: 'center', justifyContent: 'center', gap: 3 },
  box: { borderRadius: radius.md, padding: space.md },
  facts: { borderRadius: radius.md, paddingHorizontal: space.md },
  fact: { flexDirection: 'row', gap: space.md, paddingVertical: 11, alignItems: 'flex-start' },
  factKey: { width: 40, fontWeight: '400', paddingTop: 2 },
  linkRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 10, gap: 6 },
});
