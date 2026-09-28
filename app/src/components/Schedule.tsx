// 「今天」页的日程和「要记得的」（server/schedule.py）。
// - 日程：课表 + 你加的 + Agent 排的 + 到期那天的截止，一条时间线。点一节课：不去（可以每周都不去）、改地点、备注；
//   点你的 / Agent 排的：改时间、删；截止和邮件里的事带勾。过去的日子记实际发生的：去没去、做没做、实际几点。
// - 要记得的：作业、邮件里的事、求职和申请的截止。一件事只出现一次：到期那天挪进那天的日程。按 明天 / 一周内 / 以后 分组，
//   以后的、没定日子的、邮件动态默认折起来。打勾 = 做完了或不用管（推送和起床报告里也不提了），能撤销；点开看详情和原文。
import React, { useState } from 'react';
import { Linking, Pressable, StyleSheet, Switch, TextInput, View } from 'react-native';
import { agentName } from '../brand';
import type { RememberGroup, ScheduleEntry } from '../data/types';
import * as sched from '../api/schedule';
import { L } from '../i18n';
import { openThread } from '../navigation';
import { useStore } from '../store';
import { agentTint, radius, space, type, useTheme } from '../theme';
import { Check, ChevronLeft, ChevronRight, CircleAlert, FolderKanban, MapPin, Plus } from './icons';
import { useSheet } from './Sheet';
import { Btn, Disclosure, Pill, Segmented, T, showError, useScaledWidth } from './ui';

// —— 日子 ————————————————————————————————————————————————————————————

const pad = (n: number) => String(n).padStart(2, '0');
export const isoDate = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const parseIso = (iso: string) => { const [y, m, d] = iso.split('-').map(Number); return new Date(y, m - 1, d); };
export const addDays = (iso: string, n: number) => { const d = parseIso(iso); d.setDate(d.getDate() + n); return isoDate(d); };
const diffDays = (a: string, b: string) => Math.round((parseIso(a).getTime() - parseIso(b).getTime()) / 86400000);
const WD_ZH = '日一二三四五六';
const WD_EN = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const mondayOf = (iso: string) => { const d = parseIso(iso); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return isoDate(d); };

/** 一个日子怎么说：今天 / 明天 / 周三 / 下周一 / 10/12。 */
export function dayWord(iso: string, today: string): string {
  const n = diffDays(iso, today);
  if (n === 0) return L('今天', 'Today');
  if (n === 1) return L('明天', 'Tomorrow');
  if (n === -1) return L('昨天', 'Yesterday');
  const d = parseIso(iso);
  if (n > 1 && n <= 7) {
    const same = mondayOf(iso) === mondayOf(today);
    return same ? L(`周${WD_ZH[d.getDay()]}`, WD_EN[d.getDay()]) : L(`下周${WD_ZH[d.getDay()]}`, `Next ${WD_EN[d.getDay()]}`);
  }
  return `${d.getMonth() + 1}/${d.getDate()}`;
}
/** 表单里的日子：9月28日 周一。 */
export const longDay = (iso: string) => {
  const d = parseIso(iso);
  return L(`${d.getMonth() + 1}月${d.getDate()}日 周${WD_ZH[d.getDay()]}`, `${WD_EN[d.getDay()]} ${d.getDate()}/${d.getMonth() + 1}`);
};

const TIME_RE = /^([01]?\d|2[0-3]):([0-5]\d)$/;
export const toMin = (s: string) => { const m = TIME_RE.exec(s.trim()); return m ? Number(m[1]) * 60 + Number(m[2]) : null; };
const fromMin = (n: number) => { const x = Math.max(0, Math.min(23 * 60 + 59, n)); return `${pad(Math.floor(x / 60))}:${pad(x % 60)}`; };
export const normTime = (s: string) => { const n = toMin(s); return n == null ? null : fromMin(n); };

const isDeadline = (e: ScheduleEntry) => e.kind === 'deadline' || e.origin === 'canvas' || e.origin === 'apply';
/** 能打勾的：截止、邮件里的事。课和一段安排不打勾（过去的记去没去）。 */
const tickable = (e: ScheduleEntry) => e.kind !== 'class' && e.kind !== 'event';

// —— 小零件 ——————————————————————————————————————————————————————————

/** 勾：圆圈，勾上是青色实心（数据和进度用青色）。 */
export function Tick({ on, onPress, label, disabled }: { on: boolean; onPress: () => void; label: string; disabled?: boolean }) {
  const t = useTheme();
  return (
    <Pressable onPress={onPress} disabled={disabled} hitSlop={10} accessibilityRole="checkbox" accessibilityState={{ checked: on, disabled }} accessibilityLabel={label}
      style={({ pressed }) => [styles.tick, { borderColor: on ? t.cyan : t.ink3, backgroundColor: on ? t.cyan : 'transparent', opacity: pressed || disabled ? 0.6 : 1 }]}>
      {on ? <Check size={14} color={t.surface} strokeWidth={3} /> : null}
    </Pressable>
  );
}

/** 谁排的：Agent 用它自己的颜色，主对话用金色，你自己加的不写。 */
function WhoPill({ e }: { e: ScheduleEntry }) {
  const t = useTheme();
  const { groups } = useStore();
  if (!e.badge) return null;
  if (e.origin === 'own' || e.origin === 'apply') {
    const g = groups.find((x) => x.id === e.by);
    const tint = e.by === 'main' ? { soft: t.goldSoft, fg: t.gold } : agentTint(t, g?.color);
    return <Pill label={e.origin === 'own' ? L(`${e.badge}排的`, `by ${e.badge}`) : e.badge} colors={[tint.soft, tint.fg]} />;
  }
  if (e.origin === 'canvas') return <Pill label={e.badge} tone="cyan" />;
  return <Pill label={e.badge} />;
}

/** 属于哪个项目：一行小字「□ CS 小组作业」，点一下进那个项目。 */
function ProjectTag({ p }: { p: { id: string; title: string } }) {
  const t = useTheme();
  return (
    <Pressable onPress={() => openThread(p.id, false)} hitSlop={6} accessibilityRole="button" accessibilityLabel={L(`打开项目：${p.title}`, `Open project: ${p.title}`)}
      style={({ pressed }) => [{ flexDirection: 'row', alignItems: 'center', gap: 4, alignSelf: 'flex-start', opacity: pressed ? 0.6 : 1 }]}>
      <FolderKanban size={12} color={t.cyan} />
      <T v="caption" color={t.cyan} numberOfLines={1} style={{ fontSize: 13, fontWeight: '600' }}>{p.title}</T>
    </Pressable>
  );
}

function Chip({ label, tone, onPress, a11y }: { label: string; tone: 'good' | 'neutral'; onPress?: () => void; a11y?: string }) {
  const t = useTheme();
  const [bg, fg] = tone === 'good' ? [t.goodSoft, t.good] : [t.surface2, t.ink2];
  return (
    <Pressable onPress={onPress} disabled={!onPress} hitSlop={6} accessibilityRole={onPress ? 'button' : undefined} accessibilityLabel={a11y}
      style={[styles.chip, { backgroundColor: bg }]}>
      {tone === 'good' ? <Check size={13} color={fg} strokeWidth={3} /> : null}
      <T v="caption" color={fg} style={{ fontSize: 13, fontWeight: '600' }}>{label}</T>
    </Pressable>
  );
}

function SmallBtn({ label, onPress }: { label: string; onPress: () => void }) {
  const t = useTheme();
  return (
    <Pressable onPress={onPress} accessibilityRole="button" hitSlop={4}
      style={({ pressed }) => [styles.small, { borderColor: t.line, backgroundColor: t.surface, opacity: pressed ? 0.6 : 1 }]}>
      <T v="callout" style={{ fontWeight: '600', fontSize: 14 }}>{label}</T>
    </Pressable>
  );
}

// —— 时间线 ——————————————————————————————————————————————————————————

/** 一天的日程（「今天」和翻到的别的日子共用）。day < today 是过去的日子：记实际发生的。 */
export function ScheduleCard({ events, day, today, editable, empty, onChanged }: {
  events: ScheduleEntry[]; day: string; today: string; editable: boolean; empty: string; onChanged: () => void;
}) {
  const t = useTheme();
  const past = day < today;
  const [ticked, setTicked] = useState<Record<string, boolean>>({});
  const rows = events.filter((e) => e.date === day);
  const tick = async (e: ScheduleEntry) => {
    const on = !(ticked[e.id] ?? e.done);
    setTicked((m) => ({ ...m, [e.id]: on }));
    try { await sched.tick(e, on); onChanged(); } catch (err) { setTicked((m) => { const n = { ...m }; delete n[e.id]; return n; }); showError(L('没勾上', "Couldn't tick it"), err); }
  };
  return (
    <View style={[styles.card, { backgroundColor: t.surface }]}>
      {rows.length ? rows.map((e, i) => (
        <EntryRow key={e.id} e={{ ...e, done: ticked[e.id] ?? e.done }} first={i === 0} past={past} editable={editable} onTick={() => tick(e)} onChanged={onChanged} />
      )) : <View style={styles.row}><T v="callout" color={t.ink2}>{empty}</T></View>}
    </View>
  );
}

function EntryRow({ e, first, past, editable, onTick, onChanged }: {
  e: ScheduleEntry; first: boolean; past: boolean; editable: boolean; onTick: () => void; onChanged: () => void;
}) {
  const t = useTheme();
  const sheet = useSheet();
  const timeW = useScaledWidth(48);
  const [open, setOpen] = useState(false);
  const dim = e.skip || e.done || (!past && e.past);
  const openEditor = () => {
    if (!editable) return;
    if (e.kind === 'class') sheet.open({ title: e.title, content: (close) => <ClassEditor e={e} past={past} close={close} onChanged={onChanged} /> });
    else if (e.origin === 'own') sheet.open({ title: L('改一条日程', 'Edit'), content: (close) => <ItemEditor e={e} day={e.date ?? ''} past={past} close={close} onChanged={onChanged} /> });
    else setOpen((v) => !v);
  };
  const attend = async (yes: boolean) => {
    try {
      if (e.origin === 'own') await sched.patchItem(e.id, { attended: yes });
      else await sched.mark({ ref: e.id, attended: yes });
      onChanged();
    } catch (err) { showError(L('没记上', "Couldn't save"), err); }
  };
  const deadline = isDeadline(e);
  const timeTop = e.allDay || !e.start ? L('全天', 'All day') : e.start;
  const timeSub = deadline && e.start ? L('截止', 'due') : e.end;
  const sub: string[] = [];
  if (tickable(e) && e.badge && e.origin !== 'own' && e.badge !== e.project?.title) sub.push(e.badge);
  if (e.kind === 'class' && e.skip) sub.push(e.series ? L('每周这节都不去', 'Skipping every week') : L('你标了不去', 'Skipping this one'));
  else if (e.location) sub.push(e.location);
  // 已经过去的（翻到过去的日子，或者今天已经结束的）：有实际时间就写实际的
  const done = past || e.past;
  const showActual = done && e.actualStart && (e.kind === 'event' || e.kind === 'class');
  const attendLabel = e.kind === 'class' ? [L('去了', 'Went'), L('没去', "Didn't go")] : [L('做了', 'Done'), L('没做', "Didn't")];
  const trainingDone = e.actualFrom === 'workouts';
  const top = showActual ? e.actualStart : timeTop;
  // 钟点只占一行（字号调大时宁可缩一点字，也不折成「11:0」「0」）；「全天」「All day」照常可以折
  const clock = { numberOfLines: 1, adjustsFontSizeToFit: true, minimumFontScale: 0.6 } as const;
  return (
    <View style={[styles.row, !first && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }]}>
      <Pressable onPress={openEditor} disabled={!editable} accessibilityRole={editable ? 'button' : undefined}
        accessibilityLabel={`${timeTop} ${e.title}`} style={({ pressed }) => [styles.rowMain, { opacity: pressed ? 0.6 : 1 }]}>
        <View style={[styles.time, { width: timeW }]}>
          <T v="callout" {...(toMin(top) != null ? clock : null)} style={[styles.tnum, { fontWeight: '600', fontSize: 15 }, dim && { color: t.ink3 }]}>{top}</T>
          {showActual ? <T v="caption" color={t.ink3} {...clock} style={styles.tnum}>{e.actualEnd}</T>
            : timeSub ? <T v="caption" color={t.ink3} {...clock} style={styles.tnum}>{timeSub}</T> : null}
        </View>
        <View style={{ flex: 1, minWidth: 0, gap: 3 }}>
          <T v="body" numberOfLines={2} color={dim ? t.ink3 : t.ink} style={(e.skip || e.done) ? { textDecorationLine: 'line-through' } : undefined}>
            {e.title}{e.tentative ? L('（暂定）', ' (tentative)') : ''}
          </T>
          {sub.filter(Boolean).length ? (
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
              {e.location && !(e.kind === 'class' && e.skip) && !(tickable(e) && e.badge) ? <MapPin size={12} color={t.ink3} /> : null}
              <T v="caption" color={t.ink3} numberOfLines={1} style={{ flex: 1, fontSize: 13 }}>{sub.filter(Boolean).join(' · ')}</T>
            </View>
          ) : null}
          {showActual ? <T v="caption" color={t.ink3} style={{ fontSize: 13 }}>{L(`原定 ${e.start}${e.end ? `–${e.end}` : ''}${trainingDone ? ' · 实际时间来自训练记录' : ''}`, `Planned ${e.start}${e.end ? `–${e.end}` : ''}`)}</T> : null}
          {e.note ? <T v="caption" color={t.ink2} numberOfLines={2} style={{ fontSize: 13 }}>{e.note}</T> : null}
          {e.project ? <ProjectTag p={e.project} /> : null}
          {e.clash.length && !dim ? <T v="caption" color={t.warn} numberOfLines={1} style={{ fontSize: 13, fontWeight: '600' }}>{L(`和 ${e.clash[0]} 撞了`, `Clashes with ${e.clash[0]}`)}</T> : null}
          {past && editable && (e.kind === 'class' || e.kind === 'event') && e.attended === null && !e.skip ? (
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 4 }}>
              <T v="callout" color={t.ink2}>{e.kind === 'class' ? L('去了吗？', 'Did you go?') : L('做了吗？', 'Did it happen?')}</T>
              <SmallBtn label={attendLabel[0]} onPress={() => attend(true)} />
              <SmallBtn label={attendLabel[1]} onPress={() => attend(false)} />
            </View>
          ) : null}
          {open ? <Detail e={e} /> : null}
        </View>
      </Pressable>
      {tickable(e) ? <Tick on={e.done} onPress={onTick} disabled={!editable} label={L(`勾掉：${e.title}`, `Tick off: ${e.title}`)} />
        : done && (e.attended !== null || (past && e.skip)) ? (
          <Chip label={e.attended === true ? (trainingDone ? L('练了', 'Done') : attendLabel[0]) : attendLabel[1]} tone={e.attended === true ? 'good' : 'neutral'}
            onPress={editable && !e.skip ? () => attend(!e.attended) : undefined} a11y={L('改成另一个', 'Switch')} />
        ) : e.skip ? <Pill label={L('不去', 'Skipping')} />
          : e.origin === 'own' && !e.project ? <WhoPill e={e} /> : null}
    </View>
  );
}

/** 点开的一行：详情、原文、说错了去对话里讲。 */
function Detail({ e }: { e: ScheduleEntry }) {
  const t = useTheme();
  const talk = () => openThread('main', false, { title: e.title, ref: e.id });
  return (
    <View style={[styles.detail, { backgroundColor: t.bg }]}>
      {e.detail ? <T v="callout" color={t.ink2}>{e.detail}</T> : null}
      {e.clash.length ? <T v="callout" color={t.warn}>{L(`和 ${e.clash.join('、')} 撞了`, `Clashes with ${e.clash.join(', ')}`)}</T> : null}
      <View style={{ flexDirection: 'row', gap: space.lg, flexWrap: 'wrap' }}>
        {e.link ? (
          <Pressable onPress={() => { Linking.openURL(e.link as string).catch((err) => showError(L('打不开', "Couldn't open it"), err)); }} accessibilityRole="link" hitSlop={6}>
            <T v="callout" color={t.gold} style={{ fontWeight: '600' }}>{e.origin === 'mail' ? L('看原文', 'Open the email') : L('看原文', 'Open the original')}</T>
          </Pressable>
        ) : null}
        {e.origin === 'mail' || e.origin === 'own' ? (
          <Pressable onPress={talk} accessibilityRole="button" hitSlop={6}>
            <T v="callout" color={t.gold} style={{ fontWeight: '600' }}>{L(`不对？跟 ${agentName()} 说`, `Wrong? Tell ${agentName()}`)}</T>
          </Pressable>
        ) : null}
      </View>
      {!e.link && e.origin === 'mail' ? <T v="caption" color={t.ink3}>{L('手动加的邮件条目，没有原文链接', 'Added by hand, no link to the email')}</T> : null}
    </View>
  );
}

// —— 要记得的 ————————————————————————————————————————————————————————

const FOLDED: RememberGroup[] = ['later', 'nodate', 'news'];
const groupName = (g: RememberGroup) => ({
  security: L('可疑的安全提醒', 'Security alerts'), overdue: L('过了的', 'Overdue'), tomorrow: L('明天', 'Tomorrow'), week: L('一周内', 'This week'),
  later: L('以后', 'Later'), nodate: L('没定日子的', 'No date yet'), news: L('邮件动态', 'Email updates'),
})[g];

/** 一行的时间：组名已经是「明天」就只写几点；截止写「截止」。 */
function whenOf(e: ScheduleEntry, today: string): string {
  if (!e.date) return '';
  const time = e.allDay ? '' : e.start;
  const day = e.group === 'tomorrow' ? '' : dayWord(e.date, today);
  const s = [day, time].filter(Boolean).join(' ');
  return isDeadline(e) ? (s ? L(`${s} 截止`, `due ${s}`) : L('截止', 'due')) : s;
}

export function RememberCard({ items, errors, today }: { items: ScheduleEntry[]; errors: Record<string, string>; today: string }) {
  const t = useTheme();
  const [open, setOpen] = useState<string | null>(null);
  const [folds, setFolds] = useState<Record<string, boolean>>({});
  /** 这次打的勾：id → 改动号（撤销用；0 = 正在提交） */
  const [ticked, setTicked] = useState<Record<string, number>>({});
  const tick = async (e: ScheduleEntry) => {
    const log = ticked[e.id];
    if (log !== undefined) {  // 勾过了：再点一下 = 撤销
      if (!log) return;
      setTicked((m) => { const n = { ...m }; delete n[e.id]; return n; });
      try { await sched.undo(log); } catch (err) { setTicked((m) => ({ ...m, [e.id]: log })); showError(L('没撤销成', "Couldn't undo"), err); }
      return;
    }
    setTicked((m) => ({ ...m, [e.id]: 0 }));
    try {
      const r = await sched.tick(e, true);
      setTicked((m) => ({ ...m, [e.id]: sched.cardOf(r)?.logId ?? -1 }));
    } catch (err) {
      setTicked((m) => { const n = { ...m }; delete n[e.id]; return n; });
      showError(L('没勾上', "Couldn't tick it"), err);
    }
  };
  const groups: RememberGroup[] = ['security', 'overdue', 'tomorrow', 'week', 'later', 'nodate', 'news'];
  const by = (g: RememberGroup) => items.filter((e) => e.group === g);
  const canvasErr = errors.canvas;
  if (!items.length && !canvasErr) {
    return <View style={[styles.card, { backgroundColor: t.surface }]}><View style={styles.row}><T v="callout" color={t.ink2}>{L('没有要记得的。作业、邮件里的事、求职和申请的截止会出现在这里。', 'Nothing to remember. Coursework, things from email and application deadlines show up here.')}</T></View></View>;
  }
  const line = { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line };
  // 先排出要显示的几块，再按顺序画：第一块上面不画线
  const blocks: { key: string; draw: (top: typeof line | null) => React.ReactNode }[] = [];
  if (canvasErr) {
    blocks.push({ key: 'canvas', draw: (top) => (
      <View style={[styles.row, top, { alignItems: 'center' }]}>
        <CircleAlert size={18} color={t.warn} />
        <T v="callout" color={t.warn} style={{ flex: 1 }}>{/登录|log ?in/i.test(canvasErr) ? L('课程平台登录失效了，作业的截止暂时读不到。', 'The course site login expired, so coursework deadlines are missing.') : L(`作业的截止读不到：${canvasErr}`, `Couldn't read coursework deadlines: ${canvasErr}`)}</T>
      </View>
    ) });
  }
  for (const g of groups) {
    const rows = by(g);
    if (!rows.length) continue;
    if (FOLDED.includes(g)) {
      const folded = !folds[g];
      const money = rows.filter((e) => e.kind === 'money').length;
      const meta = g === 'news' ? L(`${rows.length} 条 · 钱 ${money} · 状态 ${rows.length - money}`, `${rows.length} · money ${money} · status ${rows.length - money}`)
        : g === 'later' && rows[0].date ? L(`${rows.length} 件 · 最近的 ${dayWord(rows[0].date, today)}`, `${rows.length} · next ${dayWord(rows[0].date, today)}`)
          : L(`${rows.length} 件`, `${rows.length}`);
      blocks.push({ key: g, draw: (top) => (
        <View style={top}>
          <Pressable onPress={() => setFolds((m) => ({ ...m, [g]: !m[g] }))} accessibilityRole="button" accessibilityState={{ expanded: !folded }}
            style={({ pressed }) => [styles.fold, { opacity: pressed ? 0.6 : 1 }]}>
            <T v="headline" style={{ fontSize: 15 }}>{groupName(g)}</T>
            <T v="callout" color={t.ink3} style={{ flex: 1 }} numberOfLines={1}>{meta}</T>
            <Disclosure open={!folded} />
          </Pressable>
          {folded ? null : rows.map((e) => (
            <RememberRow key={e.id} e={e} today={today} open={open === e.id} compact onToggle={() => setOpen((x) => (x === e.id ? null : e.id))}
              ticked={ticked[e.id]} onTick={() => tick(e)} />
          ))}
          {!folded && g === 'news' ? <T v="caption" color={t.ink3} style={{ paddingBottom: space.md }}>{L('勾掉 = 看过了', 'Tick = seen')}</T> : null}
        </View>
      ) });
      continue;
    }
    blocks.push({ key: g, draw: (top) => (
      <View style={top}>
        <T v="label" color={g === 'security' || g === 'overdue' ? t.warn : t.ink3} style={styles.group}>{groupName(g)}</T>
        {rows.map((e, i) => (
          <RememberRow key={e.id} e={e} today={today} open={open === e.id} first={i === 0} onToggle={() => setOpen((x) => (x === e.id ? null : e.id))}
            ticked={ticked[e.id]} onTick={() => tick(e)} />
        ))}
      </View>
    ) });
  }
  return (
    <View style={[styles.card, { backgroundColor: t.surface, paddingBottom: 2 }]}>
      {blocks.map((b, i) => <React.Fragment key={b.key}>{b.draw(i ? line : null)}</React.Fragment>)}
    </View>
  );
}

function RememberRow({ e, today, open, first, compact, onToggle, ticked, onTick }: {
  e: ScheduleEntry; today: string; open: boolean; first?: boolean; compact?: boolean; onToggle: () => void; ticked: number | undefined; onTick: () => void;
}) {
  const t = useTheme();
  const done = ticked !== undefined;
  const when = whenOf(e, today);
  const warn = e.group === 'overdue' || e.group === 'security';
  return (
    <View style={[styles.rem, compact ? { paddingVertical: 9 } : null, !first && !compact && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }]}>
      <Tick on={done} onPress={onTick} disabled={ticked === 0} label={done ? L(`撤销：${e.title}`, `Undo: ${e.title}`) : L(`勾掉：${e.title}`, `Tick off: ${e.title}`)} />
      <View style={{ flex: 1, minWidth: 0 }}>
        <Pressable onPress={onToggle} accessibilityRole="button" accessibilityState={{ expanded: open }} style={({ pressed }) => ({ opacity: pressed ? 0.6 : 1, gap: 2 })}>
          <T v="body" numberOfLines={open ? 3 : 1} color={done ? t.ink3 : t.ink} style={[compact && { fontSize: 15 }, done && { textDecorationLine: 'line-through' }]}>{e.title}</T>
          {when || (e.clash.length && !done) ? (
            <View style={{ flexDirection: 'row', gap: 6, flexWrap: 'wrap' }}>
              {when ? <T v="caption" color={warn ? t.warn : t.ink3} style={[{ fontSize: 13 }, styles.tnum]}>{e.group === 'overdue' ? L(`${when} · 过了`, `${when} · past`) : when}</T> : null}
              {e.clash.length && !done ? <T v="caption" color={t.warn} style={{ fontSize: 13, fontWeight: '600' }}>{L(`和 ${short(e.clash[0])} 撞了`, `clashes with ${short(e.clash[0])}`)}</T> : null}
            </View>
          ) : null}
        </Pressable>
        {e.project && !done ? <View style={{ marginTop: 3 }}><ProjectTag p={e.project} /></View> : null}
        {done ? <T v="caption" color={t.ink3} style={{ fontSize: 13, marginTop: 3 }}>{L('勾掉了，推送和起床报告里也不提了。再点一下勾就撤销。', 'Ticked off. No more reminders about it. Tap the tick again to undo.')}</T> : null}
        {open && !done ? <Detail e={e} /> : null}
      </View>
      {e.badge && e.origin !== 'own' ? <WhoPill e={e} /> : null}
    </View>
  );
}

/** 课名缩写：Machine Learning Systems → MLS。 */
function short(name: string): string {
  const words = name.match(/[A-Za-z]+/g) ?? [];
  if (name.length <= 12 || words.length < 2) return name;
  const caps = words.filter((w) => /^[A-Z]/.test(w)).map((w) => w[0]).join('');
  return caps || name;
}

// —— 弹层：一节课 ————————————————————————————————————————————————————————

function ClassEditor({ e, past, close, onChanged }: { e: ScheduleEntry; past: boolean; close: () => void; onChanged: () => void }) {
  const t = useTheme();
  const d = e.date ? parseIso(e.date) : null;
  const [skip, setSkip] = useState(e.skip);
  const [series, setSeries] = useState(e.series);
  const [attended, setAttended] = useState<'yes' | 'no' | ''>(e.attended === true ? 'yes' : e.attended === false ? 'no' : '');
  const [location, setLocation] = useState(e.location);
  const [note, setNote] = useState(e.note);
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try {
      if (!past && (skip !== e.skip || series !== e.series)) {
        if (skip && series) await sched.mark({ ref: e.id, skip: true, series: true });          // 每周都不去
        else if (skip) {
          if (e.series) await sched.mark({ ref: e.id, skip: false, series: true });           // 关掉「每周」，只这一次不去
          await sched.mark({ ref: e.id, skip: true });
        } else await sched.mark({ ref: e.id, skip: false });                                 // 这一次去（每周的设置别的周照旧）
      }
      const body: Parameters<typeof sched.mark>[0] = { ref: e.id };
      if (location.trim() !== e.location) body.location = location.trim() === (e.sourceLocation ?? '') ? '' : location.trim();
      if (note.trim() !== e.note) body.note = note.trim();
      if (past && attended !== (e.attended === true ? 'yes' : e.attended === false ? 'no' : '')) body.attended = attended === '' ? null : attended === 'yes';
      if (Object.keys(body).length > 1) await sched.mark(body);
      close();
      onChanged();
    } catch (err) { showError(L('没改成', "Couldn't save"), err); } finally { setBusy(false); }
  };
  return (
    <View style={{ gap: space.md }}>
      <T v="callout" color={t.ink2}>{e.date ? `${longDay(e.date)} · ${e.allDay ? L('全天', 'All day') : `${e.start}–${e.end}`}` : ''}</T>
      <View style={{ flexDirection: 'row', gap: 8, alignItems: 'flex-start' }}>
        <Pill label={e.badge || L('课表', 'Calendar')} />
        <T v="caption" color={t.ink3} style={{ flex: 1, fontSize: 13, lineHeight: 18 }}>{L('课表本身改不了。这里改的只记在这一层，Agent 排时间和 iPhone 日历都按这一层来。', "The timetable itself can't change. What you set here lives in this layer; Agents and your iPhone calendar follow it.")}</T>
      </View>
      {past ? (
        <Segmented value={attended || 'none'} onChange={(v) => setAttended(v === 'none' ? '' : v)}
          options={[{ value: 'yes', label: L('去了', 'Went') }, { value: 'no', label: L('没去', "Didn't go") }, { value: 'none', label: L('不记', 'Not set') }]} />
      ) : (
        <Segmented value={skip ? 'skip' : 'go'} onChange={(v) => setSkip(v === 'skip')}
          options={[{ value: 'go', label: L('去', 'Going') }, { value: 'skip', label: L('不去', 'Skipping') }]} />
      )}
      {!past && skip ? (
        <View style={[styles.box, { backgroundColor: t.surface }]}>
          <T v="callout" color={t.ink2}>{L('这节会变灰。Agent 排时间时当你这段有空，iPhone 日历里也不显示。', "This one turns grey. Agents treat the slot as free, and it's hidden from your iPhone calendar.")}</T>
          {e.start && d ? (
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.md }}>
              <T v="body" style={{ flex: 1 }}>{L(`以后每周${WD_ZH[d.getDay()]}这节都不去`, `Skip this every ${WD_EN[d.getDay()]}`)}</T>
              <Switch value={series} onValueChange={setSeries} accessibilityLabel={L('以后每周都不去', 'Skip every week')} trackColor={{ true: t.cyan, false: t.track }} thumbColor="#FFFFFF" />
            </View>
          ) : null}
        </View>
      ) : null}
      <View style={[styles.box, { backgroundColor: t.surface, paddingVertical: 0 }]}>
        <Field label={L('地点', 'Place')} value={location} onChange={setLocation} placeholder={e.sourceLocation || L('课表里没写', 'Not in the timetable')} />
        <Field label={L('备注', 'Note')} value={note} onChange={setNote} placeholder={L('加一句，比如「带电脑」', 'Add a note, e.g. "bring laptop"')} last />
      </View>
      <View style={{ flexDirection: 'row', gap: space.sm }}>
        <Btn label={L('取消', 'Cancel')} kind="quiet" onPress={close} />
        <Btn label={busy ? L('正在存…', 'Saving…') : L('保存', 'Save')} onPress={() => { if (!busy) save(); }} flex />
      </View>
    </View>
  );
}

function Field({ label, value, onChange, placeholder, last, keyboard, width }: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string; last?: boolean; keyboard?: 'numbers-and-punctuation'; width?: number;
}) {
  const t = useTheme();
  return (
    <View style={[styles.field, !last && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }]}>
      <T v="body" color={t.ink2} numberOfLines={1} style={{ width: 52, flexShrink: 0 }}>{label}</T>
      <TextInput value={value} onChangeText={onChange} placeholder={placeholder} placeholderTextColor={t.ink3} accessibilityLabel={label}
        keyboardType={keyboard} maxLength={keyboard ? 5 : 200} style={[type.body, { flex: width ? undefined : 1, minWidth: 0, width, color: t.ink, paddingVertical: 12 }]} />
    </View>
  );
}

// —— 弹层：你的 / Agent 排的，或者加一条 ————————————————————————————————————

/** 加一条（e 为空）或改一条自己的日程。day：加的时候默认哪天。 */
export function ItemEditor({ e, day, past, close, onChanged }: { e?: ScheduleEntry; day: string; past: boolean; close: () => void; onChanged: () => void }) {
  const t = useTheme();
  const { groups } = useStore();
  const [title, setTitle] = useState(e?.title ?? '');
  const [date, setDate] = useState(e?.date ?? day);
  const [allDay, setAllDay] = useState(e ? !e.start : false);
  const [start, setStart] = useState(e?.start || '');
  const [end, setEnd] = useState(e?.end || '');
  const [location, setLocation] = useState(e?.location ?? '');
  const [note, setNote] = useState(e?.note ?? '');
  const [deadline, setDeadline] = useState(e?.kind === 'deadline');
  const [attended, setAttended] = useState<'yes' | 'no' | ''>(e?.attended === true ? 'yes' : e?.attended === false ? 'no' : '');
  const [aStart, setAStart] = useState(e?.actualStart ?? '');
  const [aEnd, setAEnd] = useState(e?.actualEnd ?? '');
  const [busy, setBusy] = useState(false);
  const who = e?.by && e.by !== 'leo' ? (groups.find((g) => g.id === e.by)?.name ?? (e.by === 'main' ? agentName() : e.by)) : '';
  const step = (v: string, set: (x: string) => void, n: number, fallback: string) => {
    const m = toMin(v) ?? toMin(fallback) ?? 9 * 60;
    set(fromMin(m + n));
    if (set === setStart && toMin(end) != null) setEnd(fromMin((toMin(end) as number) + n));  // 挪开始时间，结束一起挪（时长不变）
  };
  const save = async () => {
    const tt = title.trim();
    if (!tt) { showError(L('写个标题', 'Add a title'), ''); return; }
    const s = allDay ? null : normTime(start);
    const en = allDay || !end.trim() ? null : normTime(end);
    if (!allDay && start.trim() && s == null) { showError(L('开始时间写成 17:30 这样', 'Start time looks like 17:30'), ''); return; }
    if (!allDay && end.trim() && en == null) { showError(L('结束时间写成 18:30 这样', 'End time looks like 18:30'), ''); return; }
    if (s && en && (toMin(en) as number) <= (toMin(s) as number)) { showError(L('结束要晚于开始', 'End must be after start'), ''); return; }
    setBusy(true);
    try {
      const fields = { title: tt, date, start: s, end: s ? en : null, location: location.trim(), note: note.trim(), kind: (deadline ? 'deadline' : 'event') as 'deadline' | 'event' };
      if (e) {
        const patch: sched.ItemFields & { attended?: boolean | null; actualStart?: string | null; actualEnd?: string | null } = {};
        if (fields.title !== e.title) patch.title = fields.title;
        if (fields.date !== e.date) patch.date = fields.date;
        if ((fields.start ?? '') !== e.start || (fields.end ?? '') !== e.end) { patch.start = fields.start ?? ''; patch.end = fields.end; }
        if (fields.location !== e.location) patch.location = fields.location;
        if (fields.note !== e.note) patch.note = fields.note;
        if (fields.kind !== (e.kind === 'deadline' ? 'deadline' : 'event')) patch.kind = fields.kind;
        if (past) {
          const a = attended === '' ? null : attended === 'yes';
          if (a !== e.attended) patch.attended = a;
          const as = normTime(aStart), ae = normTime(aEnd);
          if ((as ?? '') !== e.actualStart) patch.actualStart = as;
          if ((ae ?? '') !== e.actualEnd) patch.actualEnd = ae;
        }
        if (Object.keys(patch).length) await sched.patchItem(e.id, patch);
      } else {
        await sched.addItem(fields);
      }
      close();
      onChanged();
    } catch (err) { showError(L('没存上', "Couldn't save"), err); } finally { setBusy(false); }
  };
  const remove = async () => {
    if (!e) return;
    setBusy(true);
    try { await sched.deleteItem(e.id); close(); onChanged(); } catch (err) { showError(L('没删掉', "Couldn't delete"), err); } finally { setBusy(false); }
  };
  return (
    <View style={{ gap: space.md }}>
      {who ? (
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          {e ? <WhoPill e={e} /> : null}
          <T v="caption" color={t.ink3} style={{ flex: 1, fontSize: 13 }}>{L(`${who}会知道你改了什么。`, `${who} will see what you changed.`)}</T>
        </View>
      ) : null}
      <TextInput value={title} onChangeText={setTitle} placeholder={L('做什么，比如「自习 · 统计」', 'What, e.g. "Study · Stats"')} placeholderTextColor={t.ink3}
        accessibilityLabel={L('标题', 'Title')} style={[type.title, { color: t.ink, paddingVertical: 4 }]} />
      <View style={[styles.box, { backgroundColor: t.surface, paddingVertical: 0 }]}>
        <View style={[styles.field, { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }]}>
          <T v="body" color={t.ink2} numberOfLines={1} style={{ width: 52, flexShrink: 0 }}>{L('日子', 'Day')}</T>
          <T v="body" style={{ flex: 1 }}>{longDay(date)}</T>
          <Pressable onPress={() => setDate((x) => addDays(x, -1))} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('前一天', 'Day before')} style={[styles.step, { backgroundColor: t.surface2 }]}><ChevronLeft size={18} color={t.ink} /></Pressable>
          <Pressable onPress={() => setDate((x) => addDays(x, 1))} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('后一天', 'Day after')} style={[styles.step, { backgroundColor: t.surface2 }]}><ChevronRight size={18} color={t.ink} /></Pressable>
        </View>
        <View style={[styles.field, { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }]}>
          <T v="body" color={t.ink2} style={{ flex: 1 }}>{L('全天', 'All day')}</T>
          <Switch value={allDay} onValueChange={setAllDay} accessibilityLabel={L('全天', 'All day')} trackColor={{ true: t.cyan, false: t.track }} thumbColor="#FFFFFF" />
        </View>
        {allDay ? null : (
          <>
            <TimeRow label={L('开始', 'Start')} value={start} onChange={setStart} onStep={(n) => step(start, setStart, n, '09:00')} placeholder="17:30" />
            <TimeRow label={L('结束', 'End')} value={end} onChange={setEnd} onStep={(n) => step(end, setEnd, n, start || '10:00')} placeholder={L('可以不写', 'Optional')} />
          </>
        )}
        <Field label={L('地点', 'Place')} value={location} onChange={setLocation} placeholder={L('可以不写', 'Optional')} />
        <Field label={L('备注', 'Note')} value={note} onChange={setNote} placeholder={L('可以不写', 'Optional')} last />
      </View>
      <View style={[styles.box, { backgroundColor: t.surface, flexDirection: 'row', alignItems: 'center' }]}>
        <View style={{ flex: 1, gap: 2 }}>
          <T v="body">{L('这是个截止', "It's a deadline")}</T>
          <T v="caption" color={t.ink3} style={{ fontSize: 13 }}>{L('之前进「要记得的」，到那天带勾', 'Shows in "To remember" until the day, then with a tick')}</T>
        </View>
        <Switch value={deadline} onValueChange={setDeadline} accessibilityLabel={L('这是个截止', "It's a deadline")} trackColor={{ true: t.cyan, false: t.track }} thumbColor="#FFFFFF" />
      </View>
      {past && e ? (
        <View style={{ gap: space.sm }}>
          <Segmented value={attended || 'none'} onChange={(v) => setAttended(v === 'none' ? '' : v)}
            options={[{ value: 'yes', label: L('做了', 'Done') }, { value: 'no', label: L('没做', "Didn't") }, { value: 'none', label: L('不记', 'Not set') }]} />
          {attended === 'yes' ? (
            <View style={[styles.box, { backgroundColor: t.surface, paddingVertical: 0 }]}>
              <TimeRow label={L('实际开始', 'Began')} value={aStart} onChange={setAStart} onStep={(n) => step(aStart, setAStart, n, start || '09:00')} wide />
              <TimeRow label={L('实际结束', 'Ended')} value={aEnd} onChange={setAEnd} onStep={(n) => step(aEnd, setAEnd, n, end || start || '10:00')} wide last />
            </View>
          ) : null}
        </View>
      ) : null}
      <View style={{ flexDirection: 'row', gap: space.sm }}>
        {e ? <Btn label={L('删除', 'Delete')} kind="danger" onPress={() => { if (!busy) remove(); }} /> : <Btn label={L('取消', 'Cancel')} kind="quiet" onPress={close} />}
        <Btn label={busy ? L('正在存…', 'Saving…') : e ? L('保存', 'Save') : L('加上', 'Add')} icon={e ? undefined : <Plus size={18} color={t.onGold} />} onPress={() => { if (!busy) save(); }} flex />
      </View>
    </View>
  );
}

function TimeRow({ label, value, onChange, onStep, wide, last, placeholder = '17:30' }: { label: string; value: string; onChange: (v: string) => void; onStep: (n: number) => void; wide?: boolean; last?: boolean; placeholder?: string }) {
  const t = useTheme();
  return (
    <View style={[styles.field, !last && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }]}>
      <T v="body" color={t.ink2} numberOfLines={1} style={{ width: wide ? 76 : 52, flexShrink: 0 }}>{label}</T>
      <TextInput value={value} onChangeText={onChange} placeholder={placeholder} placeholderTextColor={t.ink3} keyboardType="numbers-and-punctuation" maxLength={5}
        accessibilityLabel={label} style={[type.body, styles.tnum, { flex: 1, minWidth: 0, color: t.ink, paddingVertical: 12, fontWeight: '600' }]} />
      <Pressable onPress={() => onStep(-30)} accessibilityRole="button" accessibilityLabel={L(`${label}提前半小时`, `${label} 30 min earlier`)} style={[styles.stepTxt, { backgroundColor: t.surface2 }]}>
        <T v="callout" style={{ fontWeight: '600' }}>−30</T>
      </Pressable>
      <Pressable onPress={() => onStep(30)} accessibilityRole="button" accessibilityLabel={L(`${label}推后半小时`, `${label} 30 min later`)} style={[styles.stepTxt, { backgroundColor: t.surface2 }]}>
        <T v="callout" style={{ fontWeight: '600' }}>+30</T>
      </Pressable>
    </View>
  );
}

/** 「加一条」按钮（日程小标题右边）。 */
export function AddScheduleButton({ day, past, onChanged }: { day: string; past: boolean; onChanged: () => void }) {
  const t = useTheme();
  const sheet = useSheet();
  return (
    <Pressable onPress={() => sheet.open({ title: L('加一条', 'Add to schedule'), content: (close) => <ItemEditor day={day} past={past} close={close} onChanged={onChanged} /> })}
      accessibilityRole="button" accessibilityLabel={L('加一条日程', 'Add to schedule')} hitSlop={8} style={styles.add}>
      <Plus size={16} color={t.gold} />
      <T v="callout" color={t.gold} style={{ fontWeight: '600' }}>{L('加一条', 'Add')}</T>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: { borderRadius: radius.lg, paddingHorizontal: space.lg, paddingVertical: space.xs },
  row: { flexDirection: 'row', gap: space.md, paddingVertical: 12, alignItems: 'flex-start' },
  rowMain: { flex: 1, minWidth: 0, flexDirection: 'row', gap: space.md },
  time: { gap: 1, flexShrink: 0 },  // 宽度按系统字号算（useScaledWidth）
  tnum: { fontVariant: ['tabular-nums'] },
  tick: { width: 24, height: 24, borderRadius: 12, borderWidth: 2, alignItems: 'center', justifyContent: 'center', marginTop: 1 },
  chip: { flexDirection: 'row', alignItems: 'center', gap: 4, height: 28, borderRadius: 14, paddingHorizontal: 10 },
  small: { height: 32, paddingHorizontal: 12, borderRadius: 9, borderWidth: 1, alignItems: 'center', justifyContent: 'center' },
  detail: { borderRadius: radius.md, paddingVertical: 10, paddingHorizontal: space.md, gap: 8, marginTop: 8 },
  rem: { flexDirection: 'row', gap: space.md, paddingVertical: 11, alignItems: 'flex-start' },
  group: { textTransform: 'uppercase', paddingTop: space.md, paddingBottom: 2 },
  fold: { flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingVertical: 13 },
  box: { borderRadius: radius.md, paddingHorizontal: 14, paddingVertical: 12, gap: 10 },
  field: { flexDirection: 'row', alignItems: 'center', gap: space.sm, minHeight: 50 },
  step: { width: 34, height: 34, borderRadius: 17, alignItems: 'center', justifyContent: 'center' },
  stepTxt: { height: 32, minWidth: 48, borderRadius: 9, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 8, flexShrink: 0 },
  add: { flexDirection: 'row', alignItems: 'center', gap: 3 },
});
