// 项目（server/projects.py）：持续几天到几周、有目标和截止的事，做完归档。
// - 项目卡：项目对话顶上的一张卡。默认收成一行（最近的截止 + 还剩几件下一步），点开看全部：目标、截止、下一步、已定的、进度、在跑的任务。
//   截止就是日程层的截止（「今天」页「要记得的」里也有，哪边打勾都一样）；挂上来的作业、邮件里的事只能打勾，源头改不了。
//   每天第一句话之前服务器把这张卡带给模型，所以 04:00 重置以后它照样知道做到哪了。
// - 点一条：打勾、改、删；右上「加」加一条。归档了：顶上换成结论卡（在写结论时转圈）。
// - 开一个项目、归档的弹层也在这里（侧栏和「…」菜单用）。
import React, { useEffect, useState } from 'react';
import { Linking, Pressable, ScrollView, StyleSheet, TextInput, View, useWindowDimensions } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import type { ProjectCard, ProjectDeadline, ProjectItem } from '../data/types';
import { L } from '../i18n';
import { useStore } from '../store';
import { radius, space, type, useTheme } from '../theme';
import { Spinner } from './ChatCards';
import { ArchiveRestore, Check, ChevronDown, ChevronLeft, ChevronRight, ChevronUp, ClipboardList, Flag, NotebookPen, Plus, Trash2 } from './icons';
import { ModelField } from './ModelPicker';
import { Tick, addDays, dayWord, isoDate, longDay, normTime } from './Schedule';
import { useSheet } from './Sheet';
import { Btn, T, showError } from './ui';

/** 每个项目卡展开没有：切走再回来还是那样（这次打开 app 里记着）。 */
const opened = new Map<string, boolean>();
const todayIso = () => isoDate(new Date());

/** 「周五 09:00 交小组视频」：日子用 dayWord（今天 / 明天 / 周五 / 10/12），有钟点带上。 */
export function deadlineWords(d: { date: string | null; start: string; title: string }, today = todayIso()): string {
  if (!d.date) return d.title;
  return `${dayWord(d.date, today)}${d.start ? ` ${d.start}` : ''} ${d.title}`;
}
/** 今天 / 还剩 5 天 / 过了 2 天 */
export function leftWords(n: number | null | undefined): string {
  if (n == null) return '';
  if (n < 0) return L(`已逾期 ${-n} 天`, `${-n} days ago`);
  if (n === 0) return L('今天', 'today');
  return L(`剩余 ${n} 天`, `${n} days left`);
}
/** 侧栏那一行、对话顶上那一行的小字：最近的截止，没有就写目标。 */
export function projectLine(p: { next?: { title: string; date: string; start: string; left: number | null } | null; goal?: string; stepsLeft?: number }): string {
  if (p.next) return `${deadlineWords({ ...p.next })} · ${leftWords(p.next.left)}`;
  if (p.goal) return p.goal;
  return p.stepsLeft ? L(`下一步 ${p.stepsLeft} 项`, `${p.stepsLeft} next steps`) : L('暂无截止日期', 'No deadline yet');
}

// —— 对话顶上那张卡 ————————————————————————————————————————————————————

export function ProjectPanel({ id }: { id: string }) {
  const t = useTheme();
  const nav = useNavigation<any>();
  const sheet = useSheet();
  const { projects, loadProject, connected, updateProjectItem, patchProject } = useStore();
  const { height } = useWindowDimensions();
  const card = projects[id];
  const [open, setOpenState] = useState(() => opened.get(id) ?? false);
  const setOpen = (v: boolean) => { opened.set(id, v); setOpenState(v); };
  // 打勾先在本地变，服务器回来重读卡片（版本变了）后以服务器为准
  const [ticks, setTicks] = useState<Record<string, boolean>>({});
  const [seenRev, setSeenRev] = useState(card?.rev);
  if (card?.rev !== seenRev) { setSeenRev(card?.rev); setTicks({}); }
  useEffect(() => { if (connected) loadProject(id).catch(() => {}); }, [id, connected, loadProject]);
  if (!card) return null;
  if (card.archived && (card.summary || card.closing)) return <SummaryCard card={card} />;

  const tick = (itemId: string, done: boolean) => {
    setTicks((m) => ({ ...m, [itemId]: !done }));
    updateProjectItem(id, { id: itemId, done: !done }).catch((e) => { setTicks((m) => { const n = { ...m }; delete n[itemId]; return n; }); showError(L('勾选失败', "Couldn't tick it"), e); });
  };
  const isDone = (x: { id: string; done: boolean }) => ticks[x.id] ?? x.done;
  const next = card.next;
  const soon = next && next.left != null && next.left <= 3;
  const headline = next ? deadlineWords(next) : card.goal || L('添加目标和截止日期', 'Add a goal and deadlines');
  const sub = [next ? leftWords(next.left) : !card.goal ? L('轻点添加', 'Tap to add') : L('暂无截止日期', 'No deadline yet'),
    card.stepsLeft ? L(`下一步 ${card.stepsLeft} 项`, `${card.stepsLeft} next step${card.stepsLeft > 1 ? 's' : ''}`) : ''].filter(Boolean).join(' · ');
  const openSheet = (title: string, content: (close: () => void) => React.ReactNode) => sheet.open({ title, content });
  const tasks = card.tasks;
  return (
    <View style={[styles.panel, { backgroundColor: t.surface, borderColor: t.line }]}>
      <Pressable onPress={() => setOpen(!open)} accessibilityRole="button" accessibilityState={{ expanded: open }}
        accessibilityLabel={open ? L('收起项目卡', 'Collapse the project card') : L('展开项目卡', 'Expand the project card')}
        style={({ pressed }) => [styles.head, { opacity: pressed ? 0.7 : 1 }]}>
        <View style={[styles.flag, { backgroundColor: soon ? t.warnSoft : t.surface2 }]}><Flag size={16} color={soon ? t.warn : t.ink2} /></View>
        <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
          <T v="headline" numberOfLines={1} style={{ fontSize: 15 }}>{headline}</T>
          <T v="caption" color={soon ? t.warn : t.ink3} numberOfLines={1} style={{ fontSize: 13 }}>{sub}</T>
        </View>
        {open ? <ChevronUp size={18} color={t.ink3} /> : <ChevronDown size={18} color={t.ink3} />}
      </Pressable>
      {open ? (
        // 展开的卡片最多占半屏，里面自己滚：对话还得看得见
        <ScrollView style={{ maxHeight: Math.max(260, height * 0.5) }} contentContainerStyle={styles.body} nestedScrollEnabled keyboardShouldPersistTaps="handled">
          <Section label={L('目标', 'Goal')} onAdd={card.goal ? undefined : () => openSheet(L('目标', 'Goal'), (close) => (
            <TextSheet close={close} placeholder={L('项目要达成的目标', 'What this project should achieve')} multiline onSave={(v) => patchProject(id, { goal: v })} />))}
            addLabel={L('添加', 'Add')} />
          {card.goal ? (
            <Pressable onPress={() => openSheet(L('目标', 'Goal'), (close) => <TextSheet close={close} initial={card.goal} multiline onSave={(v) => patchProject(id, { goal: v })} />)}
              accessibilityRole="button" style={({ pressed }) => [styles.text, { opacity: pressed ? 0.6 : 1 }]}>
              <T v="body" style={{ fontSize: 15 }}>{card.goal}</T>
            </Pressable>
          ) : null}

          <Section label={L('截止', 'Deadlines')} caption={card.deadlines.length ? L('同步显示在「今天」', 'Also on Today') : undefined}
            onAdd={() => openSheet(L('添加截止日期', 'Add a deadline'), (close) => <DeadlineSheet pid={id} close={close} />)} addLabel={L('添加', 'Add')} />
          {card.deadlines.length ? card.deadlines.map((d) => (
            <DeadlineRow key={d.id} d={{ ...d, done: isDone(d) }} onTick={() => tick(d.id, isDone(d))}
              onPress={() => openSheet(d.own ? L('截止', 'Deadline') : d.title, (close) => <DeadlineSheet pid={id} d={{ ...d, done: isDone(d) }} close={close} />)} />
          )) : <T v="caption" color={t.ink3} style={styles.empty}>{L('暂无。在此添加截止日期，将同步到日程和提醒。', 'None yet. Due dates go here and into your schedule and reminders.')}</T>}

          <Section label={L('下一步', 'Next steps')} onAdd={() => openSheet(L('添加下一步', 'Add a step'), (close) => <AddTextSheet pid={id} kind="step" close={close} />)} addLabel={L('添加', 'Add')} />
          {card.steps.length ? card.steps.map((s) => (
            <ItemRow key={s.id} item={{ ...s, done: isDone(s) }} tickable onTick={() => tick(s.id, isDone(s))}
              onPress={() => openSheet(L('下一步', 'Next step'), (close) => <ItemSheet pid={id} item={{ ...s, done: isDone(s) }} close={close} />)} />
          )) : <T v="caption" color={t.ink3} style={styles.empty}>{L('暂无。', 'None yet.')}</T>}

          <Section label={L('已决定', 'Decided')} onAdd={() => openSheet(L('添加决定', 'Note a decision'), (close) => <AddTextSheet pid={id} kind="decision" close={close} />)} addLabel={L('添加', 'Add')} />
          {card.decisions.length ? card.decisions.map((s) => (
            <ItemRow key={s.id} item={s} onPress={() => openSheet(L('已决定', 'Decided'), (close) => <ItemSheet pid={id} item={s} close={close} />)} />
          )) : <T v="caption" color={t.ink3} style={styles.empty}>{L('暂无。已确定的事项记录在此，之后无需再次确认。', "None yet. Settled things go here so they don't get asked again.")}</T>}

          <Section label={L('进度', 'Progress')} caption={card.progressAt ? L(`${shortDate(card.progressAt)} 更新`, `Updated ${shortDate(card.progressAt)}`) : L('每日日结时更新', 'Updated at the daily wrap-up')} />
          <Pressable onPress={() => openSheet(L('进度', 'Progress'), (close) => <TextSheet close={close} initial={card.progress} multiline placeholder={L('当前进展，一两句话', 'Where things are, in a sentence or two')} onSave={(v) => patchProject(id, { progress: v })} />)}
            accessibilityRole="button" style={({ pressed }) => [styles.text, { opacity: pressed ? 0.6 : 1 }]}>
            <T v="body" color={card.progress ? t.ink : t.ink3} style={{ fontSize: 15 }}>{card.progress || L('暂无。', 'Nothing yet.')}</T>
          </Pressable>

          {tasks && tasks.items.length ? (
            <Pressable onPress={() => nav.navigate('Tasks')} accessibilityRole="button" style={({ pressed }) => [styles.tasks, { borderTopColor: t.line, opacity: pressed ? 0.6 : 1 }]}>
              <View style={[styles.round, { backgroundColor: t.cyanSoft }]}><ClipboardList size={14} color={t.cyan} /></View>
              <View style={{ flex: 1, minWidth: 0, gap: 1 }}>
                <T v="callout" style={{ fontWeight: '600' }}>{tasks.running ? L(`${tasks.running} 个任务进行中`, `${tasks.running} task${tasks.running > 1 ? 's' : ''} running`) : L(`${tasks.done} 个任务已完成`, `${tasks.done} task${tasks.done > 1 ? 's' : ''} done`)}</T>
                <T v="caption" color={t.ink3} numberOfLines={1}>{(tasks.items.find((x) => x.status === '进行中') ?? tasks.items[0]).title}</T>
              </View>
              <ChevronRight size={16} color={t.ink3} />
            </Pressable>
          ) : null}
        </ScrollView>
      ) : null}
    </View>
  );
}

function shortDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : `${d.getMonth() + 1}/${d.getDate()}`;
}

function Section({ label, caption, onAdd, addLabel }: { label: string; caption?: string; onAdd?: () => void; addLabel?: string }) {
  const t = useTheme();
  return (
    <View style={[styles.section, { borderTopColor: t.line }]}>
      <T v="label" color={t.ink3} style={{ flex: 1 }}>{label}</T>
      {caption ? <T v="caption" color={t.ink3}>{caption}</T> : null}
      {onAdd ? (
        <Pressable onPress={onAdd} hitSlop={8} accessibilityRole="button" accessibilityLabel={`${addLabel ?? L('添加', 'Add')} · ${label}`} style={styles.add}>
          <Plus size={14} color={t.gold} />
          <T v="caption" color={t.gold} style={{ fontWeight: '600', fontSize: 13 }}>{addLabel ?? L('添加', 'Add')}</T>
        </Pressable>
      ) : null}
    </View>
  );
}

function DeadlineRow({ d, onTick, onPress }: { d: ProjectDeadline; onTick: () => void; onPress: () => void }) {
  const t = useTheme();
  const when = d.date ? `${dayWord(d.date, todayIso())}${d.start ? ` ${d.start}` : ''}` : L('未定日期', 'No date');
  const tail = d.done ? (d.gone ? L('已提交', 'Submitted') : L('已勾选', 'Ticked')) : leftWords(d.left);
  const soon = !d.done && d.left != null && d.left <= 1;
  return (
    <View style={styles.row}>
      <Tick on={d.done} onPress={onTick} disabled={d.gone} label={d.done ? L(`取消勾选：${d.title}`, `Untick: ${d.title}`) : L(`勾选：${d.title}`, `Tick off: ${d.title}`)} />
      <Pressable onPress={onPress} accessibilityRole="button" style={({ pressed }) => [{ flex: 1, minWidth: 0, gap: 1, opacity: pressed ? 0.6 : 1 }]}>
        <T v="body" numberOfLines={2} color={d.done ? t.ink3 : t.ink} style={[{ fontSize: 15 }, d.done && { textDecorationLine: 'line-through' }]}>{d.title}</T>
        <T v="caption" color={soon ? t.warn : t.ink3} numberOfLines={1} style={{ fontSize: 12 }}>{[when, tail, !d.own ? d.badge : ''].filter(Boolean).join(' · ')}</T>
      </Pressable>
    </View>
  );
}

function ItemRow({ item, tickable, onTick, onPress }: { item: ProjectItem; tickable?: boolean; onTick?: () => void; onPress: () => void }) {
  const t = useTheme();
  return (
    <View style={styles.row}>
      {tickable ? <Tick on={item.done} onPress={() => onTick?.()} label={item.done ? L(`取消勾选：${item.text}`, `Untick: ${item.text}`) : L(`勾选：${item.text}`, `Tick off: ${item.text}`)} />
        : <View style={[styles.dot, { backgroundColor: t.ink3 }]} />}
      <Pressable onPress={onPress} accessibilityRole="button" style={({ pressed }) => [{ flex: 1, minWidth: 0, opacity: pressed ? 0.6 : 1 }]}>
        <T v="body" color={item.done ? t.ink3 : t.ink} style={[{ fontSize: 15 }, item.done && { textDecorationLine: 'line-through' }]}>{item.text}</T>
      </Pressable>
    </View>
  );
}

// —— 弹层 ————————————————————————————————————————————————————————————

/** 改一段文字（目标、进度）。 */
function TextSheet({ close, initial = '', placeholder, multiline, onSave }: {
  close: () => void; initial?: string; placeholder?: string; multiline?: boolean; onSave: (v: string) => Promise<void>;
}) {
  const t = useTheme();
  const [v, setV] = useState(initial);
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try { await onSave(v.trim()); close(); } catch (e) { showError(L('保存失败', "Couldn't save"), e); } finally { setBusy(false); }
  };
  return (
    <View style={{ gap: space.md }}>
      <TextInput value={v} onChangeText={setV} placeholder={placeholder} placeholderTextColor={t.ink3} multiline={multiline} autoFocus
        accessibilityLabel={placeholder ?? L('内容', 'Text')} style={[type.body, styles.input, { backgroundColor: t.bg, color: t.ink }, multiline && { minHeight: 88, textAlignVertical: 'top' }]} />
      <Btn label={busy ? L('正在保存…', 'Saving…') : L('保存', 'Save')} onPress={() => { if (!busy) save(); }} />
    </View>
  );
}

/** 加一条下一步 / 已定的。 */
function AddTextSheet({ pid, kind, close }: { pid: string; kind: 'step' | 'decision'; close: () => void }) {
  const t = useTheme();
  const { addProjectItem } = useStore();
  const [v, setV] = useState('');
  const [busy, setBusy] = useState(false);
  const add = async () => {
    if (!v.trim()) return;
    setBusy(true);
    try { await addProjectItem(pid, { kind, text: v.trim() }); close(); } catch (e) { showError(L('添加失败', "Couldn't add it"), e); } finally { setBusy(false); }
  };
  const ph = kind === 'step' ? L('下一步，例如「周日前确定分工」', 'What\'s next, e.g. "Split the work by Sunday"') : L('已确定的事项，例如「视频不超过 8 分钟」', 'What was decided, e.g. "Video under 8 minutes"');
  return (
    <View style={{ gap: space.md }}>
      <TextInput value={v} onChangeText={setV} placeholder={ph} placeholderTextColor={t.ink3} autoFocus multiline accessibilityLabel={ph}
        style={[type.body, styles.input, { backgroundColor: t.bg, color: t.ink, minHeight: 64, textAlignVertical: 'top' }]} />
      <Btn label={busy ? L('正在添加…', 'Adding…') : L('添加', 'Add')} icon={<Plus size={18} color={t.onGold} />} onPress={() => { if (!busy) add(); }} />
    </View>
  );
}

/** 一条下一步 / 已定的：改文字、打勾、删。 */
function ItemSheet({ pid, item, close }: { pid: string; item: ProjectItem; close: () => void }) {
  const t = useTheme();
  const { updateProjectItem, deleteProjectItem } = useStore();
  const [v, setV] = useState(item.text);
  const [busy, setBusy] = useState(false);
  const run = async (p: Promise<void>) => {
    setBusy(true);
    try { await p; close(); } catch (e) { showError(L('操作失败', "Couldn't do that"), e); } finally { setBusy(false); }
  };
  return (
    <View style={{ gap: space.md }}>
      <TextInput value={v} onChangeText={setV} multiline accessibilityLabel={L('内容', 'Text')}
        style={[type.body, styles.input, { backgroundColor: t.bg, color: t.ink, minHeight: 64, textAlignVertical: 'top' }]} />
      {item.kind === 'step' ? (
        <Btn label={item.done ? L('标为未完成', 'Not done yet') : L('标为完成', 'Done')} icon={item.done ? undefined : <Check size={18} color={t.onGold} />}
          onPress={() => { if (!busy) run(updateProjectItem(pid, { id: item.id, done: !item.done })); }} />
      ) : null}
      <View style={{ flexDirection: 'row', gap: space.sm }}>
        <Btn label={L('删除', 'Delete')} kind="danger" icon={<Trash2 size={16} color={t.bad} />} onPress={() => { if (!busy) run(deleteProjectItem(pid, item.id)); }} />
        <Btn label={L('保存', 'Save')} kind="quiet" flex onPress={() => { if (!busy && v.trim() && v.trim() !== item.text) run(updateProjectItem(pid, { id: item.id, text: v.trim() })); else close(); }} />
      </View>
    </View>
  );
}

/** 日子一行：‹ 10月2日 周五 ›，再加几个快捷的。 */
function DayRow({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const t = useTheme();
  return (
    <View style={[styles.field, { borderColor: t.line }]}>
      <T v="body" color={t.ink2} numberOfLines={1} style={{ width: 56 }}>{L('日期', 'Day')}</T>
      <T v="body" style={{ flex: 1 }}>{longDay(value)}</T>
      <Pressable onPress={() => onChange(addDays(value, -1))} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('前一天', 'Day before')} style={[styles.step, { backgroundColor: t.surface2 }]}><ChevronLeft size={18} color={t.ink} /></Pressable>
      <Pressable onPress={() => onChange(addDays(value, 1))} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('后一天', 'Day after')} style={[styles.step, { backgroundColor: t.surface2 }]}><ChevronRight size={18} color={t.ink} /></Pressable>
      <Pressable onPress={() => onChange(addDays(value, 7))} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('推后一周', 'A week later')} style={[styles.step, { backgroundColor: t.surface2, width: 44 }]}><T v="caption" style={{ fontWeight: '700' }}>+7</T></Pressable>
    </View>
  );
}

function TimeField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const t = useTheme();
  return (
    <View style={[styles.field, { borderColor: t.line }]}>
      <T v="body" color={t.ink2} numberOfLines={1} style={{ width: 56 }}>{L('时间', 'Time')}</T>
      <TextInput value={value} onChangeText={onChange} placeholder={L('可选，例如 09:00', 'Optional, e.g. 09:00')} placeholderTextColor={t.ink3}
        keyboardType="numbers-and-punctuation" maxLength={5} accessibilityLabel={L('时间', 'Time')} style={[type.body, { flex: 1, color: t.ink, paddingVertical: 10, fontWeight: '600' }]} />
    </View>
  );
}

/** 截止：加一个（d 为空）/ 看一个。自己的能改标题、日子、钟点、删；挂上来的（作业、邮件）只能打勾、拿掉、看原文。 */
function DeadlineSheet({ pid, d, close }: { pid: string; d?: ProjectDeadline; close: () => void }) {
  const t = useTheme();
  const { addProjectItem, updateProjectItem, deleteProjectItem } = useStore();
  const [title, setTitle] = useState(d?.title ?? '');
  const [date, setDate] = useState(d?.date ?? addDays(todayIso(), 7));
  const [time, setTime] = useState(d?.start ?? '');
  const [busy, setBusy] = useState(false);
  const run = async (p: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    try { await p(); close(); } catch (e) { showError(L('操作失败', "Couldn't do that"), e); } finally { setBusy(false); }
  };
  const due = () => {
    const tm = time.trim() ? normTime(time) : '';
    if (tm == null) throw new Error(L('时间格式应为 09:00', 'Use the format 09:00'));
    return tm ? `${date} ${tm}` : date;
  };
  if (d && !d.own) {  // 挂上来的
    return (
      <View style={{ gap: space.md }}>
        <View style={[styles.infoBox, { backgroundColor: t.bg }]}>
          <T v="callout" color={t.ink2}>{[d.date ? `${longDay(d.date)}${d.start ? ` ${d.start}` : ''}` : L('未定日期', 'No date'), d.badge].filter(Boolean).join(' · ')}</T>
          <T v="caption" color={t.ink3} style={{ fontSize: 13 }}>{d.gone ? L('来源中已移除（已提交或已过期清除），此处按关联时的内容显示。', "It's gone at the source (submitted or expired); shown as it was when linked.")
            : L('关联自课程、邮件或求职，无法在此编辑；在此勾选与在「今天」中勾选效果相同。', "Linked from coursework, mail or applications; it can't be edited here. Ticking it here or on Today is the same.")}</T>
        </View>
        {d.gone ? null : <Btn label={d.done ? L('取消勾选', 'Untick') : L('标为完成', 'Done')} icon={d.done ? undefined : <Check size={18} color={t.onGold} />}
          onPress={() => run(() => updateProjectItem(pid, { id: d.id, done: !d.done }))} />}
        <View style={{ flexDirection: 'row', gap: space.sm }}>
          <Btn label={L('从项目卡移除', 'Remove from card')} kind="danger" onPress={() => run(() => deleteProjectItem(pid, d.id))} />
          {d.link ? <Btn label={L('查看原文', 'Open')} kind="quiet" flex onPress={() => Linking.openURL(d.link as string).catch(() => {})} /> : null}
        </View>
      </View>
    );
  }
  return (
    <View style={{ gap: space.md }}>
      <TextInput value={title} onChangeText={setTitle} placeholder={L('截止事项，例如「提交小组视频」', 'What\'s due, e.g. "Submit the video"')} placeholderTextColor={t.ink3}
        autoFocus={!d} accessibilityLabel={L('截止事项', "What's due")} style={[type.title, { color: t.ink, paddingVertical: 4 }]} />
      <View style={[styles.infoBox, { backgroundColor: t.bg, paddingVertical: 0 }]}>
        <DayRow value={date} onChange={setDate} />
        <TimeField value={time} onChange={setTime} />
      </View>
      <T v="caption" color={t.ink3} style={{ fontSize: 13 }}>{L('将同步到「今天」页的日程和「要记得的」，并在前一天晚上和截止前 3 小时提醒；在任一处勾选效果相同。', 'It also shows on Today and in To remember, with reminders the evening before and 3 hours before. Tick it in either place.')}</T>
      {d ? (
        <>
          <Btn label={d.done ? L('取消勾选', 'Untick') : L('标为完成', 'Done')} icon={d.done ? undefined : <Check size={18} color={t.onGold} />}
            onPress={() => run(() => updateProjectItem(pid, { id: d.id, done: !d.done }))} />
          <View style={{ flexDirection: 'row', gap: space.sm }}>
            <Btn label={L('删除', 'Delete')} kind="danger" icon={<Trash2 size={16} color={t.bad} />} onPress={() => run(() => deleteProjectItem(pid, d.id))} />
            <Btn label={busy ? L('正在保存…', 'Saving…') : L('保存', 'Save')} kind="quiet" flex onPress={() => run(async () => {
              const change: { id: string; text?: string; due?: string } = { id: d.id };
              if (title.trim() && title.trim() !== d.title) change.text = title.trim();
              const nd = due();
              if (nd !== (d.start ? `${d.date} ${d.start}` : d.date)) change.due = nd;
              if (change.text || change.due) await updateProjectItem(pid, change);
            })} />
          </View>
        </>
      ) : (
        <Btn label={busy ? L('正在添加…', 'Adding…') : L('添加', 'Add')} icon={<Plus size={18} color={t.onGold} />} onPress={() => run(async () => {
          if (!title.trim()) throw new Error(L('请填写截止事项', "Enter what's due"));
          await addProjectItem(pid, { kind: 'deadline', text: title.trim(), due: due() });
        })} />
      )}
    </View>
  );
}

// —— 归档了：结论卡 ————————————————————————————————————————————————————

function SummaryCard({ card }: { card: ProjectCard }) {
  const t = useTheme();
  const { restoreProject } = useStore();
  const s = card.summary;
  const [busy, setBusy] = useState(false);
  const restore = () => {
    if (busy) return;
    setBusy(true);
    restoreProject(card.id).catch((e) => showError(L('恢复失败', "Couldn't restore it"), e)).finally(() => setBusy(false));
  };
  return (
    <View style={[styles.panel, { backgroundColor: t.surface, borderColor: t.line, padding: space.md, gap: space.sm }]}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        <View style={[styles.pill, { backgroundColor: s ? t.goodSoft : t.cyanSoft }]}>
          {s ? <Check size={13} color={t.good} strokeWidth={3} /> : <Spinner size={13} color={t.cyan} />}
          <T v="caption" color={s ? t.good : t.cyan} style={{ fontWeight: '700', fontSize: 12 }}>{s ? L('结论', 'Summary') : L('正在生成结论', 'Writing the summary')}</T>
        </View>
        <T v="caption" color={t.ink3} style={{ flex: 1 }}>{card.archivedAt ? L(`${shortDate(card.archivedAt)} 归档`, `Archived ${shortDate(card.archivedAt)}`) : ''}</T>
      </View>
      {s ? (
        <>
          {s.done ? <Block label={L('成果', 'Done')}><T v="body" style={{ fontSize: 15 }}>{s.done}</T></Block> : null}
          {s.decided.length ? (
            <Block label={L('已决定', 'Decided')}>
              {s.decided.map((x) => <View key={x} style={styles.row}><View style={[styles.dot, { backgroundColor: t.ink3 }]} /><T v="body" style={{ flex: 1, fontSize: 15 }}>{x}</T></View>)}
            </Block>
          ) : null}
          {s.learned ? <Block label={L('经验教训', 'Next time')}><T v="body" style={{ fontSize: 15 }}>{s.learned}</T></Block> : null}
          {s.saved ? (
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, paddingTop: 2 }}>
              <NotebookPen size={14} color={t.ink3} />
              <T v="caption" color={t.ink3} style={{ flex: 1, fontSize: 13 }}>{L(`已保存至：${s.saved}`, `Saved to: ${s.saved}`)}</T>
            </View>
          ) : null}
        </>
      ) : <T v="callout" color={t.ink2}>{L('正在生成结论：成果、已决定事项和经验教训。完成后存入记忆，此处保留一份。', "Writing what got done, what was decided and what to remember. It goes into memory, and a copy stays here.")}</T>}
      <Btn label={busy ? L('正在恢复…', 'Restoring…') : L('恢复到侧栏', 'Restore to sidebar')} kind="quiet" icon={<ArchiveRestore size={16} color={t.ink} />} onPress={restore} />
    </View>
  );
}

function Block({ label, children }: { label: string; children: React.ReactNode }) {
  const t = useTheme();
  return (
    <View style={[styles.block, { borderTopColor: t.line }]}>
      <T v="label" color={t.ink3}>{label}</T>
      {children}
    </View>
  );
}

// —— 开一个项目、归档 ——————————————————————————————————————————————————————

/** 开一个项目：名字、要做成什么、第一个截止（可以不填）、模型。 */
export function NewProjectSheet({ close, onCreated }: { close: () => void; onCreated: (id: string) => void }) {
  const t = useTheme();
  const { createProject, connected, threadModel } = useStore();
  const [title, setTitle] = useState('');
  const [goal, setGoal] = useState('');
  const [withDue, setWithDue] = useState(false);
  const [dueTitle, setDueTitle] = useState('');
  const [date, setDate] = useState(addDays(todayIso(), 7));
  const [time, setTime] = useState('');
  const [modelId, setModelId] = useState(threadModel.main ?? 'anthropic/claude-opus-5-5');
  const [busy, setBusy] = useState(false);
  const create = async () => {
    if (!title.trim() || busy) return;
    if (!connected) { showError(L('未连接服务器', 'Not connected to the server'), L('请在「我 → 服务器」中检查地址和令牌。', 'Check the address and token in Me → Server.')); return; }
    const tm = time.trim() ? normTime(time) : '';
    if (withDue && tm == null) { showError(L('时间格式应为 09:00', 'Use the format 09:00'), ''); return; }
    setBusy(true);
    try {
      const deadline = withDue && dueTitle.trim() ? { title: dueTitle.trim(), due: tm ? `${date} ${tm}` : date } : undefined;
      const id = await createProject({ title: title.trim(), goal: goal.trim(), modelId, deadline });
      onCreated(id);
      close();
    } catch (e) { showError(L('创建失败', "Couldn't create it"), e); } finally { setBusy(false); }
  };
  return (
    <View style={{ gap: space.md }}>
      <T v="callout" color={t.ink2}>{L('适用于持续数天到数周、有目标和截止日期的事项。项目卡固定在对话顶部，每天的对话都会延续卡上的进度，完成后归档。', 'For something that runs days or weeks, with a goal and deadlines. A project card sits on top; each day picks up from the card. Archive it when done.')}</T>
      <TextInput value={title} onChangeText={setTitle} placeholder={L('项目名称，例如：CS 小组作业', 'Name, e.g. Group project')} placeholderTextColor={t.ink3} accessibilityLabel={L('项目名称', 'Project name')}
        style={[type.body, styles.input, { backgroundColor: t.bg, color: t.ink }]} />
      <TextInput value={goal} onChangeText={setGoal} multiline placeholder={L('项目目标，一两句话', 'What it should achieve, in a sentence or two')} placeholderTextColor={t.ink3} accessibilityLabel={L('目标', 'Goal')}
        style={[type.body, styles.input, { backgroundColor: t.bg, color: t.ink, minHeight: 72, textAlignVertical: 'top' }]} />
      {withDue ? (
        <View style={[styles.infoBox, { backgroundColor: t.bg, paddingVertical: 0 }]}>
          <View style={[styles.field, { borderColor: t.line }]}>
            <T v="body" color={t.ink2} numberOfLines={1} style={{ width: 56 }}>{L('事项', 'Due')}</T>
            <TextInput value={dueTitle} onChangeText={setDueTitle} placeholder={L('例如「提交小组视频」', 'e.g. "Submit the video"')} placeholderTextColor={t.ink3} accessibilityLabel={L('截止事项', "What's due")}
              style={[type.body, { flex: 1, color: t.ink, paddingVertical: 10 }]} />
          </View>
          <DayRow value={date} onChange={setDate} />
          <TimeField value={time} onChange={setTime} />
        </View>
      ) : (
        <Pressable onPress={() => setWithDue(true)} accessibilityRole="button" hitSlop={6} style={[styles.add, { alignSelf: 'flex-start' }]}>
          <Plus size={16} color={t.gold} />
          <T v="callout" color={t.gold} style={{ fontWeight: '600' }}>{L('添加首个截止日期', 'Add the first deadline')}</T>
        </Pressable>
      )}
      <ModelField value={modelId} onChange={setModelId} />
      <Btn label={busy ? L('正在创建…', 'Creating…') : L('创建项目', 'Create project')} onPress={create} />
      <T v="caption" color={t.ink3} style={{ textAlign: 'center' }}>{L('下一步和已决定事项可稍后在项目卡上添加，或在对话中说明', 'Add next steps and decisions on the card later, or mention them in the chat')}</T>
    </View>
  );
}

/** 归档：先让它写结论（做成了什么、定过的、下次记得的，存进记忆），或者直接收起来。 */
export function ArchiveSheet({ id, title, close }: { id: string; title: string; close: () => void }) {
  const t = useTheme();
  const { archiveProject, projects, loadProject } = useStore();
  const card = projects[id];
  useEffect(() => { loadProject(id).catch(() => {}); }, [id, loadProject]);
  const open = card ? card.deadlines.filter((d) => !d.done).length : 0;
  const running = card?.tasks?.running ?? 0;
  const [busy, setBusy] = useState<'sum' | 'plain' | null>(null);
  const go = (summarize: boolean) => {
    if (busy) return;
    setBusy(summarize ? 'sum' : 'plain');
    archiveProject(id, summarize).then(close).catch((e) => showError(L('归档失败', "Couldn't archive it"), e)).finally(() => setBusy(null));
  };
  return (
    <View style={{ gap: space.md }}>
      <T v="callout" color={t.ink2}>{L(`归档前将生成一份结论：成果、已决定事项和经验教训。结论存入记忆，「${title}」的项目卡上也保留一份。对话会保留，可随时恢复。`,
        `Before archiving, a summary is written: what got done, what was decided, what to remember. It goes into memory, and a copy stays on "${title}". The chat is kept and you can restore it.`)}</T>
      {open || running ? (
        <View style={[styles.infoBox, { backgroundColor: t.bg }]}>
          {open ? <T v="callout">{L(`${open} 个截止日期尚未勾选`, `${open} deadline${open > 1 ? 's' : ''} not ticked yet`)}</T> : null}
          {running ? <T v="callout">{L(`${running} 个任务进行中，归档后将继续完成`, `${running} task${running > 1 ? 's' : ''} still running; they'll finish`)}</T> : null}
        </View>
      ) : null}
      <Btn label={busy === 'sum' ? L('正在归档…', 'Archiving…') : L('生成结论并归档', 'Summarize and archive')} icon={<NotebookPen size={18} color={t.onGold} />} onPress={() => go(true)} />
      <Btn label={busy === 'plain' ? L('正在归档…', 'Archiving…') : L('仅归档，不生成结论', 'Just archive')} kind="quiet" onPress={() => go(false)} />
    </View>
  );
}

const styles = StyleSheet.create({
  panel: { borderRadius: 16, borderWidth: StyleSheet.hairlineWidth, marginHorizontal: space.md, marginTop: space.sm },
  head: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 14, paddingVertical: 11 },
  flag: { width: 32, height: 32, borderRadius: 10, alignItems: 'center', justifyContent: 'center' },
  body: { paddingHorizontal: 14, paddingBottom: 10 },
  section: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingTop: 12, paddingBottom: 4, borderTopWidth: StyleSheet.hairlineWidth },
  add: { flexDirection: 'row', alignItems: 'center', gap: 3 },
  text: { paddingVertical: 4, paddingBottom: 8 },
  row: { flexDirection: 'row', alignItems: 'flex-start', gap: 10, paddingVertical: 6 },
  dot: { width: 5, height: 5, borderRadius: 3, marginTop: 9, marginHorizontal: 9 },
  empty: { paddingVertical: 4, paddingBottom: 8, fontSize: 13 },
  tasks: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingTop: 11, paddingBottom: 2, borderTopWidth: StyleSheet.hairlineWidth, marginTop: 4 },
  round: { width: 26, height: 26, borderRadius: 13, alignItems: 'center', justifyContent: 'center' },
  input: { borderRadius: radius.md, paddingHorizontal: space.lg, paddingVertical: 13 },
  field: { flexDirection: 'row', alignItems: 'center', gap: 8, minHeight: 46, borderBottomWidth: StyleSheet.hairlineWidth },
  step: { width: 34, height: 34, borderRadius: 17, alignItems: 'center', justifyContent: 'center' },
  infoBox: { borderRadius: radius.md, paddingHorizontal: space.md, paddingVertical: space.sm, gap: 4 },
  pill: { flexDirection: 'row', alignItems: 'center', gap: 4, height: 22, paddingHorizontal: 9, borderRadius: 11 },
  block: { gap: 4, paddingTop: 9, borderTopWidth: StyleSheet.hairlineWidth },
});
