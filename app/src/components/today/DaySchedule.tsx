// 「今天」页 · 日程（要到场的）：按时长比例的时间格，空闲收成一行，金线是现在；过去的放在实际时间，旁边写原定。
// 同一活动服务器已经并成一条（状态成了标签「已购票」），训练计划、练后记录归在训练那一条里：点开看，跳过 / 改时间 / 备注。
import React, { useState } from 'react';
import { Linking, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { agentName } from '../../brand';
import * as sched from '../../api/schedule';
import type { DayEvent, FeedRef } from '../../api/today';
import type { FeedItem, MealPlan, TrainingPlan } from '../../data/types';
import { L } from '../../i18n';
import { openThread } from '../../navigation';
import { useStore } from '../../store';
import { agentTint, radius, space, type, useTheme } from '../../theme';
import { CalendarDays, ChevronRight, Mail } from '../icons';
import { GroupBadge } from '../GroupIcon';
import { MealPlanCard } from '../LiveBoards';
import { Markdown } from '../Markdown';
import { TrainingPlanCard, isTrainingPlan } from '../Records';
import { AddScheduleButton, ClassEditor, ItemEditor } from '../Schedule';
import { useSheet } from '../Sheet';
import { Btn, T, showError } from '../ui';
import { BlockHeader, toMin } from './parts';
import { DayTimeGrid, seriesOf, type GridBlock } from './vizLite';

/** 这一条是哪个 Agent 排的（课表、邮件、你自己加的、主对话加的都不算）。 */
const agentOf = (e: DayEvent) => (e.origin === 'own' && e.by && e.by !== 'leo' && e.by !== 'main' ? e.by : null);
const skipped = (e: DayEvent) => e.state === 'skipped';
const span = (a: string, b: string) => (b ? `${a}–${b}` : a);

function useTint() {
  const t = useTheme();
  const { groups } = useStore();
  return (e: DayEvent) => {
    const g = groups.find((x) => x.id === agentOf(e));
    return g ? agentTint(t, g.color) : { soft: t.cyanSoft, fg: seriesOf(t)[0] };
  };
}

export function ScheduleBlock({ events, allDay, day, today, now, onChanged, onOpen: open }: {
  events: DayEvent[]; allDay: DayEvent[]; day: string; today: string; now: number | null; onChanged: () => void; onOpen: (e: DayEvent) => void;
}) {
  const t = useTheme();
  const tint = useTint();
  const past = day < today;
  const n = events.length + allDay.length;
  const add = <AddScheduleButton day={day} past={past} onChanged={onChanged} />;
  if (!n) {
    // 没有安排：不画空卡，只留小标题和「添加」（过去的日子连小标题也不留）
    return past ? null : <BlockHeader title={L('日程 · 无安排', 'Schedule · Nothing yet')} right={add} />;
  }
  const blocks: GridBlock[] = events.flatMap((e) => {
    const s = toMin(e.slotStart), en = e.slotEnd === '24:00' ? 24 * 60 : toMin(e.slotEnd);
    if (s == null || en == null) return [];
    const time = [e.moved ? span(e.actualStart, e.actualEnd) : span(e.start, e.end), ...e.tags, skipped(e) ? L('已跳过', 'Skipped') : ''].filter(Boolean).join(' · ');
    return [{ id: e.id, start: s, end: Math.max(en, s + 15), title: e.title, note: e.moved ? L(`原定 ${e.start}`, `planned ${e.start}`) : undefined, time,
      color: tint(e), dim: skipped(e), strike: skipped(e), a11y: `${e.title}，${time}` }];
  });
  return (
    <View>
      <BlockHeader title={L(`日程 · ${n}`, `Schedule · ${n}`)} right={add} />
      <View style={[styles.card, { backgroundColor: t.surface }]}>
        {allDay.map((e) => (
          <Pressable key={e.id} onPress={() => open(e)} accessibilityRole="button" style={({ pressed }) => [styles.allDay, { backgroundColor: tint(e).soft, opacity: (skipped(e) ? 0.55 : 1) * (pressed ? 0.75 : 1) }]}>
            <View style={[styles.bar, { backgroundColor: tint(e).fg }]} />
            <Text style={[styles.allDayText, { color: t.ink }, skipped(e) && { textDecorationLine: 'line-through' }]} numberOfLines={1}>{e.title}</Text>
            <Text style={[styles.allDayTag, { color: t.ink2 }]}>{[L('全天', 'All day'), ...e.tags].join(' · ')}</Text>
          </Pressable>
        ))}
        {blocks.length ? <DayTimeGrid blocks={blocks} now={day === today ? now : null} onPress={(id) => { const e = events.find((x) => x.id === id); if (e) open(e); }} /> : null}
      </View>
    </View>
  );
}

/** 状态词：已出席 / 已完成 / 已跳过 / 进行中 / 已结束。 */
function stateWord(e: DayEvent): string {
  switch (e.state) {
    case 'attended': return e.kind === 'class' ? L('已出席', 'Attended') : L('已完成', 'Done');
    case 'skipped': return L('已跳过', 'Skipped');
    case 'now': return L('进行中', 'Happening now');
    case 'past': return L('已结束', 'Over');
    default: return '';
  }
}

/** 点开日程的一条：计划和复盘（归进来的卡）、地点和备注，跳过 / 改时间 / 备注，去对应的对话里说。 */
export function EventDetail({ e, past, close, onChanged }: { e: DayEvent; past: boolean; close: () => void; onChanged: () => void }) {
  const t = useTheme();
  const sheet = useSheet();
  const { groups } = useStore();
  const [busy, setBusy] = useState(false);
  const agent = agentOf(e);
  const g = groups.find((x) => x.id === agent);
  const isMail = e.origin === 'mail';
  const isSkipped = skipped(e);
  const changed = () => { close(); onChanged(); };
  const run = async (fn: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(true);
    try { await fn(); changed(); } catch (err) { showError(L('保存失败', "Couldn't save"), err); } finally { setBusy(false); }
  };
  // 跳过：自己的 = 记成没做；课和邮件里的活动 = 还没到的标「不去」，过去的记成没去。恢复 = 反过来。
  const skip = () => run(async () => {
    if (e.origin === 'own') await sched.patchItem(e.id, { attended: isSkipped ? null : false });
    else if (past) await sched.mark({ ref: e.id, attended: isSkipped ? null : false });
    else await sched.mark({ ref: e.id, skip: !isSkipped });
  });
  // 弹层里换一页（同一个弹层，不先落下）
  const editTime = () => sheet.open(e.kind === 'class'
    ? { title: e.title, content: (c) => <ClassEditor e={e} past={past} close={c} onChanged={onChanged} /> }
    : { title: L('改时间', 'Change time'), content: (c) => <ItemEditor e={e} day={e.date ?? ''} past={past} close={c} onChanged={onChanged} /> });
  const editNote = () => sheet.open({ title: L('备注', 'Note'), content: (c) => <NoteEditor e={e} close={c} onChanged={onChanged} /> });
  const planned = e.allDay || !e.start ? L('全天', 'All day') : span(e.start, e.end);
  const word = stateWord(e);
  const loc = /\w/.test(e.location) ? e.location : '';
  const link = (label: string, onPress: () => void) => (
    <Pressable onPress={onPress} accessibilityRole="button" hitSlop={6} style={styles.linkRow}>
      <T v="callout" color={t.gold} style={{ fontWeight: '600', flex: 1 }}>{label}</T>
      <ChevronRight size={16} color={t.gold} />
    </Pressable>
  );
  return (
    <View style={{ gap: space.md }}>
      <View style={styles.head}>
        {g ? <GroupBadge icon={g.icon} color={g.color} size={40} /> : (
          <View style={[styles.icon, { backgroundColor: t.cyanSoft }]}>{isMail ? <Mail size={20} color={t.cyan} /> : <CalendarDays size={20} color={t.cyan} />}</View>
        )}
        <View style={{ flex: 1, gap: 2 }}>
          <T v="callout" color={t.ink2}>{[planned, ...e.tags, word].filter(Boolean).join(' · ')}</T>
          {e.moved ? <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L(`实际 ${span(e.actualStart, e.actualEnd)}${e.actualFrom === 'workouts' ? ' · 来自训练记录' : ''}`, `Actually ${span(e.actualStart, e.actualEnd)}`)}</T> : null}
          {loc ? <T v="caption" color={t.ink3} style={{ fontWeight: '400' }} numberOfLines={2}>{loc}</T> : null}
        </View>
      </View>
      {(e.cards ?? []).map((c) => <AttachedCard key={c.id} c={c} />)}
      {e.note ? <View style={[styles.box, { backgroundColor: t.surface }]}><T v="caption" color={t.ink3}>{L('备注', 'Note')}</T><T v="callout">{e.note}</T></View> : null}
      {isMail && e.detail ? <View style={[styles.box, { backgroundColor: t.surface }]}><T v="callout" color={t.ink2}>{e.detail}</T></View> : null}
      {e.editable ? (
        <View style={styles.actions}>
          <Btn label={isSkipped ? L('恢复', 'Restore') : L('跳过', 'Skip')} kind="quiet" onPress={skip} flex />
          {isMail ? null : <Btn label={e.kind === 'class' ? L('编辑', 'Edit') : L('改时间', 'Time')} kind="quiet" onPress={editTime} flex />}
          <Btn label={L('备注', 'Note')} kind="quiet" onPress={editNote} flex />
        </View>
      ) : null}
      <View>
        {e.link ? link(isMail ? L('查看邮件原文', 'Open the email') : L('查看原文', 'Open the original'),
          () => { Linking.openURL(e.link as string).catch((err) => showError(L('无法打开', "Couldn't open"), err)); }) : null}
        {g ? link(L(`在${g.name}中讨论`, `Discuss in ${g.name}`), () => { close(); openThread(g.id, true, { title: e.title, ref: e.id }); })
          : isMail || e.origin === 'own' ? link(L(`信息有误？告诉 ${agentName()}`, `Incorrect? Tell ${agentName()}`), () => { close(); openThread('main', false, { title: e.title, ref: e.id }); }) : null}
      </View>
    </View>
  );
}

/** 归到这一条里的卡（训练计划、练后记录）：默认一行结论，点开看全文。 */
function AttachedCard({ c }: { c: FeedRef }) {
  const t = useTheme();
  const { groups } = useStore();
  const [open, setOpen] = useState(false);
  const g = groups.find((x) => x.id === c.agent);
  const tint = agentTint(t, g?.color);
  const asFeed = { id: c.id, groupId: c.agent, title: c.title, body: c.body, cta: c.cta, time: c.time, kind: c.kind, data: c.data as FeedItem['data'] } as FeedItem;
  return (
    <Pressable onPress={() => setOpen((v) => !v)} accessibilityRole="button" accessibilityState={{ expanded: open }}
      style={[styles.box, { backgroundColor: t.surface }]}>
      <View style={styles.cardHead}>
        <T v="caption" color={tint.fg} style={{ fontWeight: '700' }}>{c.label ?? c.title}</T>
        <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{[g?.name, c.time].filter(Boolean).join(' · ')}</T>
      </View>
      <T v="callout">{c.title}</T>
      {open ? (
        isTrainingPlan(asFeed) ? <TrainingPlanCard plan={asFeed.data as TrainingPlan} />
          : c.kind === 'meal_plan' && c.data && typeof c.data === 'object' && 'meals' in c.data ? <MealPlanCard plan={c.data as MealPlan} />
            : <Markdown text={c.body} color={t.ink2} compact />
      ) : <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L('点按查看全文', 'Tap for the full text')}</T>}
    </Pressable>
  );
}

function NoteEditor({ e, close, onChanged }: { e: DayEvent; close: () => void; onChanged: () => void }) {
  const t = useTheme();
  const [note, setNote] = useState(e.note);
  const [busy, setBusy] = useState(false);
  const save = async () => {
    if (busy) return;
    setBusy(true);
    try {
      if (e.origin === 'own') await sched.patchItem(e.id, { note: note.trim() });
      else await sched.mark({ ref: e.id, note: note.trim() });
      close();
      onChanged();
    } catch (err) { showError(L('保存失败', "Couldn't save"), err); } finally { setBusy(false); }
  };
  return (
    <View style={{ gap: space.md }}>
      <T v="callout" color={t.ink2}>{e.title}</T>
      <TextInput value={note} onChangeText={setNote} multiline autoFocus placeholder={L('添加备注，例如「带电脑」', 'Add a note, e.g. "bring laptop"')} placeholderTextColor={t.ink3}
        accessibilityLabel={L('备注', 'Note')} style={[type.body, styles.input, { color: t.ink, backgroundColor: t.surface }]} maxLength={500} />
      <View style={{ flexDirection: 'row', gap: space.sm }}>
        <Btn label={L('取消', 'Cancel')} kind="quiet" onPress={close} />
        <Btn label={busy ? L('正在保存…', 'Saving…') : L('保存', 'Save')} onPress={save} flex />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: { borderRadius: radius.lg, paddingHorizontal: 14, paddingTop: 10, paddingBottom: 12, gap: 8 },
  allDay: { flexDirection: 'row', alignItems: 'center', borderRadius: 8, overflow: 'hidden', height: 34, gap: 10 },
  bar: { width: 3, alignSelf: 'stretch' },
  allDayText: { flex: 1, fontSize: 14, fontWeight: '600' },
  allDayTag: { fontSize: 12, paddingRight: 10 },
  head: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  icon: { width: 40, height: 40, borderRadius: 12, alignItems: 'center', justifyContent: 'center' },
  box: { borderRadius: radius.md, padding: 12, gap: 6 },
  cardHead: { flexDirection: 'row', justifyContent: 'space-between', gap: 8 },
  actions: { flexDirection: 'row', gap: space.sm },
  linkRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 10, gap: 6 },
  input: { minHeight: 96, borderRadius: radius.md, padding: 12, textAlignVertical: 'top' },
});
