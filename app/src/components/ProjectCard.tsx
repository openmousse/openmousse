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
  if (n < 0) return L(`过了 ${-n} 天`, `${-n} days ago`);
  if (n === 0) return L('今天', 'today');
  return L(`还剩 ${n} 天`, `${n} days left`);
}
/** 侧栏那一行、对话顶上那一行的小字：最近的截止，没有就写目标。 */
export function projectLine(p: { next?: { title: string; date: string; start: string; left: number | null } | null; goal?: string; stepsLeft?: number }): string {
  if (p.next) return `${deadlineWords({ ...p.next })} · ${leftWords(p.next.left)}`;
  if (p.goal) return p.goal;
  return p.stepsLeft ? L(`下一步 ${p.stepsLeft} 件`, `${p.stepsLeft} next steps`) : L('还没有截止', 'No deadline yet');
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
    updateProjectItem(id, { id: itemId, done: !done }).catch((e) => { setTicks((m) => { const n = { ...m }; delete n[itemId]; return n; }); showError(L('没勾上', "Couldn't tick it"), e); });
  };
  const isDone = (x: { id: string; done: boolean }) => ticks[x.id] ?? x.done;
  const next = card.next;
  const soon = next && next.left != null && next.left <= 3;
  const headline = next ? deadlineWords(next) : card.goal || L('写上目标和截止', 'Add a goal and deadlines');
  const sub = [next ? leftWords(next.left) : !card.goal ? L('点开加', 'Tap to add') : L('还没有截止', 'No deadline yet'),
    card.stepsLeft ? L(`下一步 ${card.stepsLeft} 件`, `${card.stepsLeft} next step${card.stepsLeft > 1 ? 's' : ''}`) : ''].filter(Boolean).join(' · ');
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
            <TextSheet close={close} placeholder={L('这个项目要做成什么', 'What this project should achieve')} multiline onSave={(v) => patchProject(id, { goal: v })} />))}
            addLabel={L('写目标', 'Add')} />
          {card.goal ? (
            <Pressable onPress={() => openSheet(L('目标', 'Goal'), (close) => <TextSheet close={close} initial={card.goal} multiline onSave={(v) => patchProject(id, { goal: v })} />)}
              accessibilityRole="button" style={({ pressed }) => [styles.text, { opacity: pressed ? 0.6 : 1 }]}>
              <T v="body" style={{ fontSize: 15 }}>{card.goal}</T>
            </Pressable>
          ) : null}

          <Section label={L('截止', 'Deadlines')} caption={card.deadlines.length ? L('也在「今天」里', 'Also on Today') : undefined}
            onAdd={() => openSheet(L('加一个截止', 'Add a deadline'), (close) => <DeadlineSheet pid={id} close={close} />)} addLabel={L('加', 'Add')} />
          {card.deadlines.length ? card.deadlines.map((d) => (
            <DeadlineRow key={d.id} d={{ ...d, done: isDone(d) }} onTick={() => tick(d.id, isDone(d))}
              onPress={() => openSheet(d.own ? L('截止', 'Deadline') : d.title, (close) => <DeadlineSheet pid={id} d={{ ...d, done: isDone(d) }} close={close} />)} />
          )) : <T v="caption" color={t.ink3} style={styles.empty}>{L('还没有。交东西的日子写在这里，会进日程和提醒。', 'None yet. Due dates go here and into your schedule and reminders.')}</T>}

          <Section label={L('下一步', 'Next steps')} onAdd={() => openSheet(L('加一步', 'Add a step'), (close) => <AddTextSheet pid={id} kind="step" close={close} />)} addLabel={L('加一步', 'Add')} />
          {card.steps.length ? card.steps.map((s) => (
            <ItemRow key={s.id} item={{ ...s, done: isDone(s) }} tickable onTick={() => tick(s.id, isDone(s))}
              onPress={() => openSheet(L('下一步', 'Next step'), (close) => <ItemSheet pid={id} item={{ ...s, done: isDone(s) }} close={close} />)} />
          )) : <T v="caption" color={t.ink3} style={styles.empty}>{L('还没有。', 'None yet.')}</T>}

          <Section label={L('已定的', 'Decided')} onAdd={() => openSheet(L('记一条已定的', 'Note a decision'), (close) => <AddTextSheet pid={id} kind="decision" close={close} />)} addLabel={L('记一条', 'Add')} />
          {card.decisions.length ? card.decisions.map((s) => (
            <ItemRow key={s.id} item={s} onPress={() => openSheet(L('已定的', 'Decided'), (close) => <ItemSheet pid={id} item={s} close={close} />)} />
          )) : <T v="caption" color={t.ink3} style={styles.empty}>{L('还没有。定下来的事记在这里，以后不用再问。', "None yet. Settled things go here so they don't get asked again.")}</T>}

          <Section label={L('进度', 'Progress')} caption={card.progressAt ? L(`${shortDate(card.progressAt)} 更新`, `Updated ${shortDate(card.progressAt)}`) : L('日结时它会更新', 'Updated at the daily wrap-up')} />
          <Pressable onPress={() => openSheet(L('进度', 'Progress'), (close) => <TextSheet close={close} initial={card.progress} multiline placeholder={L('做到哪了，一两句', 'Where things are, in a sentence or two')} onSave={(v) => patchProject(id, { progress: v })} />)}
            accessibilityRole="button" style={({ pressed }) => [styles.text, { opacity: pressed ? 0.6 : 1 }]}>
            <T v="body" color={card.progress ? t.ink : t.ink3} style={{ fontSize: 15 }}>{card.progress || L('还没写。', 'Nothing yet.')}</T>
          </Pressable>

          {tasks && tasks.items.length ? (
            <Pressable onPress={() => nav.navigate('Tasks')} accessibilityRole="button" style={({ pressed }) => [styles.tasks, { borderTopColor: t.line, opacity: pressed ? 0.6 : 1 }]}>
              <View style={[styles.round, { backgroundColor: t.cyanSoft }]}><ClipboardList size={14} color={t.cyan} /></View>
              <View style={{ flex: 1, minWidth: 0, gap: 1 }}>
                <T v="callout" style={{ fontWeight: '600' }}>{tasks.running ? L(`${tasks.running} 个任务在跑`, `${tasks.running} task${tasks.running > 1 ? 's' : ''} running`) : L(`${tasks.done} 个任务做完了`, `${tasks.done} task${tasks.done > 1 ? 's' : ''} done`)}</T>
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
        <Pressable onPress={onAdd} hitSlop={8} accessibilityRole="button" accessibilityLabel={`${addLabel ?? L('加', 'Add')} · ${label}`} style={styles.add}>
          <Plus size={14} color={t.gold} />
          <T v="caption" color={t.gold} style={{ fontWeight: '600', fontSize: 13 }}>{addLabel ?? L('加', 'Add')}</T>
        </Pressable>
      ) : null}
    </View>
  );
}

function DeadlineRow({ d, onTick, onPress }: { d: ProjectDeadline; onTick: () => void; onPress: () => void }) {
  const t = useTheme();
  const when = d.date ? `${dayWord(d.date, todayIso())}${d.start ? ` ${d.start}` : ''}` : L('没定日子', 'No date');
  const tail = d.done ? (d.gone ? L('交了', 'Submitted') : L('勾掉了', 'Ticked')) : leftWords(d.left);
  const soon = !d.done && d.left != null && d.left <= 1;
  return (
    <View style={styles.row}>
      <Tick on={d.done} onPress={onTick} disabled={d.gone} label={d.done ? L(`放回去：${d.title}`, `Untick: ${d.title}`) : L(`勾掉：${d.title}`, `Tick off: ${d.title}`)} />
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
      {tickable ? <Tick on={item.done} onPress={() => onTick?.()} label={item.done ? L(`放回去：${item.text}`, `Untick: ${item.text}`) : L(`勾掉：${item.text}`, `Tick off: ${item.text}`)} />
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
    try { await onSave(v.trim()); close(); } catch (e) { showError(L('没存上', "Couldn't save"), e); } finally { setBusy(false); }
  };
  return (
    <View style={{ gap: space.md }}>
      <TextInput value={v} onChangeText={setV} placeholder={placeholder} placeholderTextColor={t.ink3} multiline={multiline} autoFocus
        accessibilityLabel={placeholder ?? L('内容', 'Text')} style={[type.body, styles.input, { backgroundColor: t.bg, color: t.ink }, multiline && { minHeight: 88, textAlignVertical: 'top' }]} />
      <Btn label={busy ? L('正在存…', 'Saving…') : L('保存', 'Save')} onPress={() => { if (!busy) save(); }} />
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
    try { await addProjectItem(pid, { kind, text: v.trim() }); close(); } catch (e) { showError(L('没加上', "Couldn't add it"), e); } finally { setBusy(false); }
  };
  const ph = kind === 'step' ? L('下一步做什么，比如「周日前定分工」', 'What\'s next, e.g. "Split the work by Sunday"') : L('定下来的事，比如「视频 8 分钟以内」', 'What was decided, e.g. "Video under 8 minutes"');
  return (
    <View style={{ gap: space.md }}>
      <TextInput value={v} onChangeText={setV} placeholder={ph} placeholderTextColor={t.ink3} autoFocus multiline accessibilityLabel={ph}
        style={[type.body, styles.input, { backgroundColor: t.bg, color: t.ink, minHeight: 64, textAlignVertical: 'top' }]} />
      <Btn label={busy ? L('正在加…', 'Adding…') : L('加上', 'Add')} icon={<Plus size={18} color={t.onGold} />} onPress={() => { if (!busy) add(); }} />
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
    try { await p; close(); } catch (e) { showError(L('没做成', "Couldn't do that"), e); } finally { setBusy(false); }
  };
  return (
    <View style={{ gap: space.md }}>
      <TextInput value={v} onChangeText={setV} multiline accessibilityLabel={L('内容', 'Text')}
        style={[type.body, styles.input, { backgroundColor: t.bg, color: t.ink, minHeight: 64, textAlignVertical: 'top' }]} />
      {item.kind === 'step' ? (
        <Btn label={item.done ? L('放回去', 'Not done yet') : L('做完了', 'Done')} icon={item.done ? undefined : <Check size={18} color={t.onGold} />}
          onPress={() => { if (!busy) run(updateProjectItem(pid, { id: item.id, done: !item.done })); }} />
      ) : null}
      <View style={{ flexDirection: 'row', gap: space.sm }}>
        <Btn label={L('删掉', 'Delete')} kind="danger" icon={<Trash2 size={16} color={t.bad} />} onPress={() => { if (!busy) run(deleteProjectItem(pid, item.id)); }} />
        <Btn label={L('保存修改', 'Save')} kind="quiet" flex onPress={() => { if (!busy && v.trim() && v.trim() !== item.text) run(updateProjectItem(pid, { id: item.id, text: v.trim() })); else close(); }} />
      </View>
    </View>
  );
}

/** 日子一行：‹ 10月2日 周五 ›，再加几个快捷的。 */
function DayRow({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const t = useTheme();
  return (
    <View style={[styles.field, { borderColor: t.line }]}>
      <T v="body" color={t.ink2} numberOfLines={1} style={{ width: 56 }}>{L('哪天', 'Day')}</T>
      <T v="body" style={{ flex: 1 }}>{longDay(value)}</T>
      <Pressable onPress={() => onChange(addDays(value, -1))} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('前一天', 'Day before')} style={[styles.step, { backgroundColor: t.surface2 }]}><ChevronLeft size={18} color={t.ink} /></Pressable>
      <Pressable onPress={() => onChange(addDays(value, 1))} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('后一天', 'Day after')} style={[styles.step, { backgroundColor: t.surface2 }]}><ChevronRight size={18} color={t.ink} /></Pressable>
      <Pressable onPress={() => onChange(addDays(value, 7))} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('往后一周', 'A week later')} style={[styles.step, { backgroundColor: t.surface2, width: 44 }]}><T v="caption" style={{ fontWeight: '700' }}>+7</T></Pressable>
    </View>
  );
}

function TimeField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const t = useTheme();
  return (
    <View style={[styles.field, { borderColor: t.line }]}>
      <T v="body" color={t.ink2} numberOfLines={1} style={{ width: 56 }}>{L('几点', 'Time')}</T>
      <TextInput value={value} onChangeText={onChange} placeholder={L('可以不写，比如 09:00', 'Optional, e.g. 09:00')} placeholderTextColor={t.ink3}
        keyboardType="numbers-and-punctuation" maxLength={5} accessibilityLabel={L('几点', 'Time')} style={[type.body, { flex: 1, color: t.ink, paddingVertical: 10, fontWeight: '600' }]} />
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
    try { await p(); close(); } catch (e) { showError(L('没做成', "Couldn't do that"), e); } finally { setBusy(false); }
  };
  const due = () => {
    const tm = time.trim() ? normTime(time) : '';
    if (tm == null) throw new Error(L('钟点写成 09:00 这样', 'Time looks like 09:00'));
    return tm ? `${date} ${tm}` : date;
  };
  if (d && !d.own) {  // 挂上来的
    return (
      <View style={{ gap: space.md }}>
        <View style={[styles.infoBox, { backgroundColor: t.bg }]}>
          <T v="callout" color={t.ink2}>{[d.date ? `${longDay(d.date)}${d.start ? ` ${d.start}` : ''}` : L('没定日子', 'No date'), d.badge].filter(Boolean).join(' · ')}</T>
          <T v="caption" color={t.ink3} style={{ fontSize: 13 }}>{d.gone ? L('源头已经没了（交了或过期清掉了），按挂上时的样子显示。', "It's gone at the source (submitted or expired); shown as it was when linked.")
            : L('从课程、邮件或求职里挂过来的，源头改不了；打勾和「今天」里是同一个。', "Linked from coursework, mail or applications; it can't be edited here. Ticking it here or on Today is the same.")}</T>
        </View>
        {d.gone ? null : <Btn label={d.done ? L('放回去', 'Untick') : L('交了 / 做完了', 'Done')} icon={d.done ? undefined : <Check size={18} color={t.onGold} />}
          onPress={() => run(() => updateProjectItem(pid, { id: d.id, done: !d.done }))} />}
        <View style={{ flexDirection: 'row', gap: space.sm }}>
          <Btn label={L('从项目卡拿掉', 'Remove from card')} kind="danger" onPress={() => run(() => deleteProjectItem(pid, d.id))} />
          {d.link ? <Btn label={L('看原文', 'Open')} kind="quiet" flex onPress={() => Linking.openURL(d.link as string).catch(() => {})} /> : null}
        </View>
      </View>
    );
  }
  return (
    <View style={{ gap: space.md }}>
      <TextInput value={title} onChangeText={setTitle} placeholder={L('交什么，比如「交小组视频」', 'What\'s due, e.g. "Submit the video"')} placeholderTextColor={t.ink3}
        autoFocus={!d} accessibilityLabel={L('交什么', "What's due")} style={[type.title, { color: t.ink, paddingVertical: 4 }]} />
      <View style={[styles.infoBox, { backgroundColor: t.bg, paddingVertical: 0 }]}>
        <DayRow value={date} onChange={setDate} />
        <TimeField value={time} onChange={setTime} />
      </View>
      <T v="caption" color={t.ink3} style={{ fontSize: 13 }}>{L('也会进「今天」页的日程和「要记得的」，前一天晚上和前 3 小时提醒；在哪边打勾都一样。', 'It also shows on Today and in To remember, with reminders the evening before and 3 hours before. Tick it in either place.')}</T>
      {d ? (
        <>
          <Btn label={d.done ? L('放回去', 'Untick') : L('交了 / 做完了', 'Done')} icon={d.done ? undefined : <Check size={18} color={t.onGold} />}
            onPress={() => run(() => updateProjectItem(pid, { id: d.id, done: !d.done }))} />
          <View style={{ flexDirection: 'row', gap: space.sm }}>
            <Btn label={L('删掉', 'Delete')} kind="danger" icon={<Trash2 size={16} color={t.bad} />} onPress={() => run(() => deleteProjectItem(pid, d.id))} />
            <Btn label={busy ? L('正在存…', 'Saving…') : L('保存修改', 'Save')} kind="quiet" flex onPress={() => run(async () => {
              const change: { id: string; text?: string; due?: string } = { id: d.id };
              if (title.trim() && title.trim() !== d.title) change.text = title.trim();
              const nd = due();
              if (nd !== (d.start ? `${d.date} ${d.start}` : d.date)) change.due = nd;
              if (change.text || change.due) await updateProjectItem(pid, change);
            })} />
          </View>
        </>
      ) : (
        <Btn label={busy ? L('正在加…', 'Adding…') : L('加上', 'Add')} icon={<Plus size={18} color={t.onGold} />} onPress={() => run(async () => {
          if (!title.trim()) throw new Error(L('写一下交什么', "Say what's due"));
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
    restoreProject(card.id).catch((e) => showError(L('没恢复成', "Couldn't restore it"), e)).finally(() => setBusy(false));
  };
  return (
    <View style={[styles.panel, { backgroundColor: t.surface, borderColor: t.line, padding: space.md, gap: space.sm }]}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        <View style={[styles.pill, { backgroundColor: s ? t.goodSoft : t.cyanSoft }]}>
          {s ? <Check size={13} color={t.good} strokeWidth={3} /> : <Spinner size={13} color={t.cyan} />}
          <T v="caption" color={s ? t.good : t.cyan} style={{ fontWeight: '700', fontSize: 12 }}>{s ? L('结论', 'Summary') : L('正在写结论', 'Writing the summary')}</T>
        </View>
        <T v="caption" color={t.ink3} style={{ flex: 1 }}>{card.archivedAt ? L(`${shortDate(card.archivedAt)} 归档`, `Archived ${shortDate(card.archivedAt)}`) : ''}</T>
      </View>
      {s ? (
        <>
          {s.done ? <Block label={L('做成了', 'Done')}><T v="body" style={{ fontSize: 15 }}>{s.done}</T></Block> : null}
          {s.decided.length ? (
            <Block label={L('定过的', 'Decided')}>
              {s.decided.map((x) => <View key={x} style={styles.row}><View style={[styles.dot, { backgroundColor: t.ink3 }]} /><T v="body" style={{ flex: 1, fontSize: 15 }}>{x}</T></View>)}
            </Block>
          ) : null}
          {s.learned ? <Block label={L('下次记得', 'Next time')}><T v="body" style={{ fontSize: 15 }}>{s.learned}</T></Block> : null}
          {s.saved ? (
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, paddingTop: 2 }}>
              <NotebookPen size={14} color={t.ink3} />
              <T v="caption" color={t.ink3} style={{ flex: 1, fontSize: 13 }}>{L(`存进了：${s.saved}`, `Saved to: ${s.saved}`)}</T>
            </View>
          ) : null}
        </>
      ) : <T v="callout" color={t.ink2}>{L('它在写：做成了什么、定过的事、下次记得的。写好了存进记忆，这里也留一份。', "It's writing what got done, what was decided and what to remember. It goes into memory, and a copy stays here.")}</T>}
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
    if (!connected) { showError(L('没连上服务器', 'Not connected to the server'), L('到「我 → 服务器」检查地址和令牌', 'Check the address and token in Me → Server.')); return; }
    const tm = time.trim() ? normTime(time) : '';
    if (withDue && tm == null) { showError(L('钟点写成 09:00 这样', 'Time looks like 09:00'), ''); return; }
    setBusy(true);
    try {
      const deadline = withDue && dueTitle.trim() ? { title: dueTitle.trim(), due: tm ? `${date} ${tm}` : date } : undefined;
      const id = await createProject({ title: title.trim(), goal: goal.trim(), modelId, deadline });
      onCreated(id);
      close();
    } catch (e) { showError(L('没开成', "Couldn't create it"), e); } finally { setBusy(false); }
  };
  return (
    <View style={{ gap: space.md }}>
      <T v="callout" color={t.ink2}>{L('持续几天到几周、有目标和截止的事。顶上一张项目卡，它每天带着这张卡接着聊，做完归档。', 'For something that runs days or weeks, with a goal and deadlines. A project card sits on top; it picks up from the card every day. Archive it when done.')}</T>
      <TextInput value={title} onChangeText={setTitle} placeholder={L('叫什么，比如：CS 小组作业', 'Name, e.g. Group project')} placeholderTextColor={t.ink3} accessibilityLabel={L('项目名字', 'Project name')}
        style={[type.body, styles.input, { backgroundColor: t.bg, color: t.ink }]} />
      <TextInput value={goal} onChangeText={setGoal} multiline placeholder={L('要做成什么，一两句', 'What it should achieve, in a sentence or two')} placeholderTextColor={t.ink3} accessibilityLabel={L('目标', 'Goal')}
        style={[type.body, styles.input, { backgroundColor: t.bg, color: t.ink, minHeight: 72, textAlignVertical: 'top' }]} />
      {withDue ? (
        <View style={[styles.infoBox, { backgroundColor: t.bg, paddingVertical: 0 }]}>
          <View style={[styles.field, { borderColor: t.line }]}>
            <T v="body" color={t.ink2} numberOfLines={1} style={{ width: 56 }}>{L('交什么', 'Due')}</T>
            <TextInput value={dueTitle} onChangeText={setDueTitle} placeholder={L('比如「交小组视频」', 'e.g. "Submit the video"')} placeholderTextColor={t.ink3} accessibilityLabel={L('交什么', "What's due")}
              style={[type.body, { flex: 1, color: t.ink, paddingVertical: 10 }]} />
          </View>
          <DayRow value={date} onChange={setDate} />
          <TimeField value={time} onChange={setTime} />
        </View>
      ) : (
        <Pressable onPress={() => setWithDue(true)} accessibilityRole="button" hitSlop={6} style={[styles.add, { alignSelf: 'flex-start' }]}>
          <Plus size={16} color={t.gold} />
          <T v="callout" color={t.gold} style={{ fontWeight: '600' }}>{L('加第一个截止', 'Add the first deadline')}</T>
        </Pressable>
      )}
      <ModelField value={modelId} onChange={setModelId} />
      <Btn label={busy ? L('正在开…', 'Creating…') : L('开这个项目', 'Create project')} onPress={create} />
      <T v="caption" color={t.ink3} style={{ textAlign: 'center' }}>{L('下一步、已定的事以后在项目卡上加，或者直接跟它说', 'Add next steps and decisions on the card later, or just tell it')}</T>
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
    archiveProject(id, summarize).then(close).catch((e) => showError(L('没归档成', "Couldn't archive it"), e)).finally(() => setBusy(null));
  };
  return (
    <View style={{ gap: space.md }}>
      <T v="callout" color={t.ink2}>{L(`归档前它写一份结论：做成了什么、定过的事、下次记得的。存进记忆，「${title}」的卡上也留一份。对话都在，可以恢复。`,
        `Before archiving it writes a summary: what got done, what was decided, what to remember. It goes into memory, and a copy stays on "${title}". The chat is kept and you can restore it.`)}</T>
      {open || running ? (
        <View style={[styles.infoBox, { backgroundColor: t.bg }]}>
          {open ? <T v="callout">{L(`还有 ${open} 个截止没勾`, `${open} deadline${open > 1 ? 's' : ''} not ticked yet`)}</T> : null}
          {running ? <T v="callout">{L(`${running} 个任务还在跑，归档后照样跑完`, `${running} task${running > 1 ? 's' : ''} still running; they'll finish`)}</T> : null}
        </View>
      ) : null}
      <Btn label={busy === 'sum' ? L('正在归档…', 'Archiving…') : L('写结论并归档', 'Summarize and archive')} icon={<NotebookPen size={18} color={t.onGold} />} onPress={() => go(true)} />
      <Btn label={busy === 'plain' ? L('正在归档…', 'Archiving…') : L('直接归档，不写结论', 'Just archive')} kind="quiet" onPress={() => go(false)} />
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
