// 「今天」页 · 待处理（要动手的）：等你点头的、安全提醒、7 天内到期的待办和截止，右边一枚截止标签（顺序服务器排好）。
// 点圆圈完成（这次在页面上勾的留着，再点撤销，下次进来才收走）；左滑推迟一天 / 一周（底下一行能撤销）；点一行看详情。
// 没日期的收在末尾「无日期 · N」，点开展开。别的日子只列那天到期的，不推迟。
import React, { useRef, useState } from 'react';
import { Linking, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { agentName } from '../../brand';
import * as todayApi from '../../api/today';
import type { PendingRow } from '../../api/today';
import { L } from '../../i18n';
import { openTarget, openThread } from '../../navigation';
import { useStore } from '../../store';
import { radius, space, type, useTheme } from '../../theme';
import { Check, ChevronRight, Clock, GraduationCap, TriangleAlert } from '../icons';
import { InboxCard } from '../InboxCard';
import { useSheet } from '../Sheet';
import { SourceBadge } from '../SourceBadge';
import { reviewLine, useStudyHome } from '../StudyBoard';
import { Btn, Disclosure, T, showError } from '../ui';
import { BlockHeader, Chip, addDays, monthDay } from './parts';

type Snoozed = { row: PendingRow; until: string };

export function PendingBlock({ rows, nodate, day, today }: { rows: PendingRow[]; nodate: PendingRow[]; day: string; today: string }) {
  const t = useTheme();
  const sheet = useSheet();
  const nav = useNavigation<any>();
  const { receipts } = useStore();
  const isToday = day === today;
  const [ticked, setTicked] = useState<Record<string, boolean>>({});
  const [hidden, setHidden] = useState<Record<string, true>>({});
  const [undo, setUndo] = useState<Snoozed | null>(null);
  const [showNodate, setShowNodate] = useState(false);
  const study = useStudyHome().data;
  const review = isToday && study ? reviewLine(study) : null;
  // 刚在这台设备上点过头的收件箱卡：服务器那份还没重读，先藏起来
  const decided = new Set(receipts.map((r) => r.item.id));
  const shown = rows.filter((r) => !hidden[r.id] && !(r.kind === 'inbox' && r.inboxId && decided.has(r.inboxId)));
  const later = nodate.filter((r) => !hidden[r.id]);
  const count = shown.length + (review ? 1 : 0);
  if (!count && !later.length && !undo) return null;

  const tick = async (r: PendingRow) => {
    const on = !(ticked[r.id] ?? r.done);
    setTicked((m) => ({ ...m, [r.id]: on }));
    try { await todayApi.tick(r, on); } catch (e) {
      setTicked((m) => { const n = { ...m }; delete n[r.id]; return n; });
      showError(L('标记失败', "Couldn't tick off"), e);
    }
  };
  const postpone = async (r: PendingRow, days: number) => {
    if (!r.ref) return;
    const until = addDays(today, days);
    setHidden((m) => ({ ...m, [r.id]: true }));
    setUndo({ row: r, until });
    try { await todayApi.snooze(r.ref, until); } catch (e) {
      setHidden((m) => { const n = { ...m }; delete n[r.id]; return n; });
      setUndo(null);
      showError(L('推迟失败', "Couldn't postpone"), e);
    }
  };
  const restore = async (s: Snoozed) => {
    setUndo(null);
    setHidden((m) => { const n = { ...m }; delete n[s.row.id]; return n; });
    try { if (s.row.ref) await todayApi.snooze(s.row.ref, null); } catch (e) { showError(L('撤销失败', "Couldn't undo"), e); }
  };
  const open = (r: PendingRow) => {
    if (r.kind === 'inbox' && r.inboxId) {
      const id = r.inboxId;
      sheet.open({ title: L('待确认', 'Needs your approval'), content: () => <InboxLive id={id} /> });
      return;
    }
    sheet.open({ title: r.title, content: (close) => (
      <PendingDetail r={r} done={ticked[r.id] ?? r.done} snooze={isToday && r.canSnooze}
        onTick={() => { close(); tick(r); }} onPostpone={(n) => { close(); postpone(r, n); }} close={close} />
    ) });
  };
  const line = (r: PendingRow, last: boolean) => (
    <PendingLine key={r.id} r={r} last={last} done={ticked[r.id] ?? r.done} swipe={isToday && r.canSnooze}
      onTick={() => tick(r)} onOpen={() => open(r)} onPostpone={(n) => postpone(r, n)} />
  );
  return (
    <View>
      <BlockHeader title={isToday ? L(`待处理 · ${count}`, `To do · ${count}`) : L(`当日到期 · ${count}`, `Due that day · ${count}`)} accent={isToday}
        right={isToday && shown.some((r) => r.canSnooze) ? <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L('左滑推迟，点圆圈完成', 'Swipe left to postpone')}</T> : null} />
      <View style={[styles.card, { backgroundColor: t.surface }]}>
        {shown.map((r, i) => line(r, i === shown.length - 1 && !review && !later.length && !undo))}
        {review ? (
          <Pressable onPress={() => (study?.review?.page ? nav.navigate('StudySession', { course: study.review.course, page: study.review.page, tab: 'path' }) : nav.navigate('StudyHome', { course: study?.review?.course }))}
            accessibilityRole="button" style={({ pressed }) => [styles.row, (later.length > 0 || !!undo) && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }, { opacity: pressed ? 0.6 : 1 }]}>
            <View style={[styles.lead, { backgroundColor: t.cyanSoft }]}><GraduationCap size={14} color={t.cyan} /></View>
            <View style={styles.mid}>
              <Text style={[styles.title, { color: t.ink }]} numberOfLines={1}>{review.title}</Text>
              {review.sub ? <Text style={[styles.sub, { color: t.ink3 }]} numberOfLines={1}>{review.sub}</Text> : null}
            </View>
            <Chip label={L('学习台', 'Study')} tone="neutral" />
          </Pressable>
        ) : null}
        {later.length ? (
          <>
            <Pressable onPress={() => setShowNodate((v) => !v)} accessibilityRole="button" accessibilityState={{ expanded: showNodate }}
              style={({ pressed }) => [styles.fold, { opacity: pressed ? 0.6 : 1 }, (showNodate || undo) && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }]}>
              <T v="callout" color={t.ink2} style={{ flex: 1 }}>{L(`无日期 · ${later.length}`, `No date · ${later.length}`)}</T>
              <Disclosure open={showNodate} />
            </Pressable>
            {showNodate ? later.map((r, i) => line(r, i === later.length - 1 && !undo)) : null}
          </>
        ) : null}
        {undo ? (
          <View style={styles.undo}>
            <T v="callout" color={t.ink2} style={{ flex: 1 }} numberOfLines={1}>{L(`已推迟到 ${monthDay(undo.until)}：${undo.row.title}`, `Postponed to ${monthDay(undo.until)}: ${undo.row.title}`)}</T>
            <Pressable onPress={() => restore(undo)} hitSlop={8} accessibilityRole="button">
              <T v="callout" color={t.gold} style={{ fontWeight: '600' }}>{L('撤销', 'Undo')}</T>
            </Pressable>
          </View>
        ) : null}
      </View>
    </View>
  );
}

/** 一行：左边是圆圈（待办、截止）/ 盾牌（安全）/ 提的 Agent（待确认），中间标题和来源，右边截止标签。 */
function PendingLine({ r, last, done, swipe, onTick, onOpen, onPostpone }: {
  r: PendingRow; last: boolean; done: boolean; swipe: boolean; onTick: () => void; onOpen: () => void; onPostpone: (days: number) => void;
}) {
  const t = useTheme();
  const body = (
    <View style={[styles.row, { backgroundColor: t.surface }, !last && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }]}>
      {r.kind === 'security' ? (
        <View style={[styles.lead, { backgroundColor: t.badSoft }]}><TriangleAlert size={14} color={t.bad} /></View>
      ) : r.kind === 'inbox' ? (
        <View style={styles.leadBox}><SourceBadge source={r.agent ?? 'main'} size={24} /></View>
      ) : (
        <Pressable onPress={onTick} disabled={!r.canTick} hitSlop={10} accessibilityRole="checkbox" accessibilityState={{ checked: done }}
          accessibilityLabel={L(`标记完成：${r.title}`, `Tick off: ${r.title}`)}
          style={[styles.tick, { borderColor: done ? t.cyan : t.ink3, backgroundColor: done ? t.cyan : 'transparent' }]}>
          {done ? <Check size={14} color={t.surface} strokeWidth={3} /> : null}
        </Pressable>
      )}
      <Pressable onPress={onOpen} accessibilityRole="button" accessibilityLabel={`${r.title}${r.chip ? `，${r.chip}` : ''}`}
        style={({ pressed }) => [styles.mid, { opacity: pressed ? 0.6 : 1 }]}>
        <Text style={[styles.title, { color: done ? t.ink3 : t.ink }, done && { textDecorationLine: 'line-through' }]} numberOfLines={1}>{r.title}</Text>
        {r.sub ? <Text style={[styles.sub, { color: t.ink3 }]} numberOfLines={1}>{r.sub}</Text> : null}
      </Pressable>
      <Chip label={done ? L('已完成', 'Done') : r.chip} tone={done ? 'good' : r.tone} />
    </View>
  );
  return swipe ? <SwipeActions actions={[
    { label: L('推迟一天', '1 day'), onPress: () => onPostpone(1) },
    { label: L('推迟一周', '1 week'), onPress: () => onPostpone(7) },
  ]}>{body}</SwipeActions> : body;
}

/** 左滑露出按钮（横向 ScrollView，安装包里没有手势库）。 */
function SwipeActions({ children, actions }: { children: React.ReactNode; actions: { label: string; onPress: () => void }[] }) {
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
          {actions.map((a, i) => (
            <Pressable key={a.label} onPress={() => { ref.current?.scrollTo({ x: 0, animated: true }); a.onPress(); }} accessibilityRole="button" accessibilityLabel={a.label}
              style={({ pressed }) => [styles.swipeBtn, { width: each, backgroundColor: i === 0 ? t.warnSoft : t.surface2, opacity: pressed ? 0.8 : 1 }]}>
              <Clock size={16} color={i === 0 ? t.warn : t.ink2} />
              <Text style={[type.caption, { color: i === 0 ? t.warn : t.ink2, fontWeight: '700' }]}>{a.label}</Text>
            </Pressable>
          ))}
        </ScrollView>
      ) : children}
    </View>
  );
}

/** 点开一行：详情、完成、推迟、原文、去对话里说。 */
function PendingDetail({ r, done, snooze, onTick, onPostpone, close }: {
  r: PendingRow; done: boolean; snooze: boolean; onTick: () => void; onPostpone: (days: number) => void; close: () => void;
}) {
  const t = useTheme();
  const link = (label: string, onPress: () => void) => (
    <Pressable onPress={onPress} accessibilityRole="button" hitSlop={6} style={styles.linkRow}>
      <T v="callout" color={t.gold} style={{ fontWeight: '600', flex: 1 }}>{label}</T>
      <ChevronRight size={16} color={t.gold} />
    </Pressable>
  );
  const study = r.study?.course ? r.study : null;
  return (
    <View style={{ gap: space.md }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        <Chip label={done ? L('已完成', 'Done') : r.chip} tone={done ? 'good' : r.tone} />
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
      <View>
        {r.link ? link(r.origin === 'mail' ? L('查看邮件原文', 'Open the email') : L('查看原文', 'Open the original'),
          () => { Linking.openURL(r.link as string).catch((e) => showError(L('无法打开', "Couldn't open"), e)); }) : null}
        {study ? link(L('打开学习台', 'Open the study desk'), () => { close(); openTarget({ type: 'study', course: study.course as string, session: study.session ?? null }); }) : null}
        {r.origin === 'mail' || r.origin === 'own' ? link(L(`信息有误？告诉 ${agentName()}`, `Incorrect? Tell ${agentName()}`),
          () => { close(); openThread('main', false, { title: r.title, ref: r.ref ?? undefined }); }) : null}
      </View>
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
  linkRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 10, gap: 6 },
});
