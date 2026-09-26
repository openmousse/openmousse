// Agent 页的「记忆」tab：顶上按类筛（规则 / 现状 / 日志），每条记忆一行标题 + 日期，点开看全文、让它忘记；
// 日志是时间线，颜色点分感受 / 决定 / 想法。删除 Agent 挪到了编辑页。
// 记忆的「标题」是 app 自己从原文里取的（以后 Agent 写记忆时会顺手写一句给人看的标题）。
import React, { useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import { agentName } from '../brand';
import type { JournalEntry, MemoryItem } from '../data/types';
import { L, lang } from '../i18n';
import { useStore } from '../store';
import { space, useTheme } from '../theme';
import { ForgetSheet } from './MemoryList';
import { clean, memoryDate, memoryTitle, memoryWho } from './memoryText';
import { useSheet } from './Sheet';
import { Btn, Card, Disclosure, SectionLabel, T } from './ui';

const RULES = /规则|偏好|原则|习惯|rule|prefer|principle|habit/i;
type Bucket = 'rules' | 'state';
const bucketOf = (section: string): Bucket => (RULES.test(section) ? 'rules' : 'state');
/** 小节名去掉括号里的说明：「规则与偏好（本块）」→「规则与偏好」。 */
const sectionTitle = (s: string) => clean(s) || s;

// —— 界面 ——

type Filter = 'all' | Bucket | 'journal';

function Chip({ label, on, onPress }: { label: string; on: boolean; onPress: () => void }) {
  const t = useTheme();
  return (
    <Pressable onPress={onPress} accessibilityRole="tab" accessibilityState={{ selected: on }}
      style={({ pressed }) => [styles.chip, { backgroundColor: on ? t.ink : t.surface, borderColor: on ? t.ink : t.line, opacity: pressed ? 0.7 : 1 }]}>
      <T v="callout" color={on ? t.surface : t.ink2} style={{ fontWeight: '600' }}>{label}</T>
    </Pressable>
  );
}

/** 一条记忆：标题 + 日期（+ 你定的），点开看全文和「让它忘记这条」。 */
function MemoryRow({ m, first }: { m: MemoryItem; first: boolean }) {
  const t = useTheme();
  const sheet = useSheet();
  const [open, setOpen] = useState(false);
  const title = memoryTitle(m.text);
  const date = memoryDate(m.text);
  const who = memoryWho(m.text);
  const meta = [date, who === 'decided' ? L('你定的', 'your call') : who === 'asked' ? L('你要求的', 'you asked') : null].filter(Boolean).join(' · ');
  return (
    <View style={[styles.mem, !first && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }]}>
      <Pressable onPress={() => setOpen((v) => !v)} accessibilityRole="button" accessibilityState={{ expanded: open }} accessibilityHint={L('点开看全文', 'Shows the full text')}
        style={({ pressed }) => [styles.row, { opacity: pressed ? 0.6 : 1 }]}>
        <View style={{ flex: 1, gap: 2 }}>
          <T v="body" style={styles.title}>{title}</T>
          {meta ? <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{meta}</T> : null}
        </View>
        <Disclosure open={open} />
      </Pressable>
      {open ? (
        <View style={{ gap: space.sm, marginTop: space.sm }}>
          <View style={[styles.full, { backgroundColor: t.bg }]}>
            <T v="callout" color={t.ink2} selectable style={{ lineHeight: 22 }}>{m.text.replace(/`/g, '')}</T>
          </View>
          <Pressable onPress={() => sheet.open({ title: L('让它忘记这条？', 'Make it forget this?'), content: (close) => <ForgetSheet m={m} close={close} /> })}
            hitSlop={8} accessibilityRole="button" style={{ alignSelf: 'flex-start', paddingVertical: 2 }}>
            <T v="callout" color={t.bad} style={{ fontSize: 13, fontWeight: '600' }}>{L('让它忘记这条', 'Make it forget this')}</T>
          </Pressable>
        </View>
      ) : null}
    </View>
  );
}

function DeleteJournalSheet({ e, close }: { e: JournalEntry; close: () => void }) {
  const t = useTheme();
  const { deleteJournal } = useStore();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  return (
    <View style={{ gap: space.md }}>
      <T v="body" color={t.ink2}>{L(`「${e.text}」`, `"${e.text}"`)}</T>
      <T v="callout" color={t.ink3}>{L(`只删正文，${agentName()} 也不会再引用它。`, `This erases the text, and ${agentName()} won't refer to it again.`)}</T>
      {err ? <T v="callout" color={t.bad}>{err}</T> : null}
      <View style={{ flexDirection: 'row', gap: space.sm }}>
        <Btn flex kind="quiet" label={L('留着', 'Keep')} onPress={close} />
        <Btn flex kind="danger" label={busy ? L('正在删…', 'Deleting…') : L('删除', 'Delete')} onPress={() => {
          if (busy) return;
          setBusy(true);
          deleteJournal(e.id).then(close).catch((x) => { setErr(x instanceof Error ? x.message : String(x)); setBusy(false); });
        }} />
      </View>
    </View>
  );
}

const KIND = (t: ReturnType<typeof useTheme>): Record<JournalEntry['kind'], { label: string; dot: string; fg: string }> => ({
  feeling: { label: L('感受', 'Feeling'), dot: t.cyan, fg: t.cyan },
  decision: { label: L('决定', 'Decision'), dot: t.chartB, fg: t.gold },
  thought: { label: L('想法', 'Thought'), dot: t.ink3, fg: t.ink2 },
  note: { label: L('记录', 'Note'), dot: t.ink3, fg: t.ink2 },
});

/** 日志的一行：时间 · 颜色点 · 类型 + 正文。点开看上下文、标签，可以删。 */
function JournalRow({ e }: { e: JournalEntry }) {
  const t = useTheme();
  const sheet = useSheet();
  const [open, setOpen] = useState(false);
  const k = KIND(t)[e.kind] ?? KIND(t).note;
  return (
    <View>
      <Pressable onPress={() => setOpen((v) => !v)} accessibilityRole="button" accessibilityState={{ expanded: open }}
        style={({ pressed }) => [styles.jr, { opacity: pressed ? 0.6 : 1 }]}>
        <T v="caption" color={t.ink3} style={styles.jt}>{e.time}</T>
        <View style={[styles.jd, { backgroundColor: k.dot }]} />
        <T v="body" style={{ flex: 1, fontSize: 15, lineHeight: 22 }}>
          <T v="caption" color={k.fg} style={{ fontWeight: '600' }}>{k.label}{'  '}</T>{e.text}
        </T>
      </Pressable>
      {open ? (
        <View style={styles.jmore}>
          {e.context ? <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{e.context}</T> : null}
          {e.tags.length ? <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{e.tags.map((x) => `#${x}`).join(' ')}</T> : null}
          <Pressable onPress={() => sheet.open({ title: L('删掉这条日志？', 'Delete this journal entry?'), content: (close) => <DeleteJournalSheet e={e} close={close} /> })}
            hitSlop={8} accessibilityRole="button" style={{ alignSelf: 'flex-start', paddingVertical: 2 }}>
            <T v="callout" color={t.bad} style={{ fontSize: 13, fontWeight: '600' }}>{L('删掉这条', 'Delete this entry')}</T>
          </Pressable>
        </View>
      ) : null}
    </View>
  );
}

const ymd = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
/** 日志的日期小标题：今天 / 昨天 / 9月23日（不是今年的带年份）。 */
function dayTitle(iso: string): string {
  const now = new Date();
  if (iso === ymd(now)) return L('今天', 'Today');
  const y = new Date(now); y.setDate(y.getDate() - 1);
  if (iso === ymd(y)) return L('昨天', 'Yesterday');
  const [yy, mm, dd] = iso.split('-').map(Number);
  if (!yy || !mm || !dd) return iso;
  const sameYear = yy === now.getFullYear();
  return lang() === 'zh' ? `${sameYear ? '' : `${yy}年`}${mm}月${dd}日` : new Date(yy, mm - 1, dd).toLocaleDateString('en', sameYear ? { month: 'short', day: 'numeric' } : { year: 'numeric', month: 'short', day: 'numeric' });
}

function Timeline({ entries }: { entries: JournalEntry[] }) {
  const t = useTheme();
  return (
    <Card style={{ paddingVertical: space.sm }}>
      {entries.map((e, i) => {
        const head = i === 0 || entries[i - 1].date !== e.date;
        return (
          <View key={e.id}>
            {head ? <T v="caption" color={t.ink3} style={{ fontWeight: '700', paddingTop: i === 0 ? 4 : 10, paddingBottom: 4 }}>{dayTitle(e.date)}</T> : null}
            <JournalRow e={e} />
          </View>
        );
      })}
    </Card>
  );
}

/** 一个 Agent 的记忆 tab。 */
export function AgentMemory({ groupId }: { groupId: string }) {
  const t = useTheme();
  const { memories, journal, loading, dataErrors, connected } = useStore();
  const [filter, setFilter] = useState<Filter>('all');
  const items = memories.filter((m) => m.scope === groupId);
  const entries = journal.filter((e) => e.groupId === groupId);
  const sections = [...new Set(items.map((m) => m.section))];
  // 规则类的小节排前面，现状类的排后面，各自保持 MEMORY.md 里的顺序
  const ordered = [...sections.filter((s) => bucketOf(s) === 'rules'), ...sections.filter((s) => bucketOf(s) === 'state')];
  const count = (b: Bucket) => items.filter((m) => bucketOf(m.section) === b).length;
  const chips: { key: Filter; label: string; n: number }[] = [
    { key: 'rules', label: L(`规则 ${count('rules')}`, `Rules ${count('rules')}`), n: count('rules') },
    { key: 'state', label: L(`现状 ${count('state')}`, `Current ${count('state')}`), n: count('state') },
    { key: 'journal', label: L(`日志 ${entries.length}`, `Journal ${entries.length}`), n: entries.length },
  ];
  // 筛到的那一类后来空了（比如刚忘掉最后一条）：回到「全部」
  const active: Filter = filter !== 'all' && !chips.find((c) => c.key === filter)?.n ? 'all' : filter;
  const showMem = (b: Bucket) => active === 'all' || active === b;
  const memNote = dataErrors.memories ? L(`读不到记忆：${dataErrors.memories}`, `Couldn't read memory: ${dataErrors.memories}`)
    : !connected ? L('没连上服务器。', 'Not connected to the server.')
      : loading.memories && !items.length ? L('正在读…', 'Loading…') : !items.length ? L('这里还没有长期记忆。', 'No long-term memories here yet.') : '';
  return (
    <View>
      <View style={styles.chips} accessibilityRole="tablist">
        <Chip label={L('全部', 'All')} on={active === 'all'} onPress={() => setFilter('all')} />
        {chips.filter((c) => c.n > 0).map((c) => <Chip key={c.key} label={c.label} on={active === c.key} onPress={() => setFilter(c.key)} />)}
      </View>

      {active === 'all' && memNote ? <Card style={{ marginTop: space.md }}><T v="callout" color={dataErrors.memories ? t.bad : t.ink2}>{memNote}</T></Card> : null}
      {ordered.filter((s) => showMem(bucketOf(s))).map((sec) => {
        const rows = items.filter((m) => m.section === sec);
        return (
          <View key={sec}>
            <SectionLabel caps={false}>{sectionTitle(sec)}</SectionLabel>
            <Card style={{ paddingVertical: space.xs }}>
              {rows.map((m, i) => <MemoryRow key={m.id} m={m} first={i === 0} />)}
            </Card>
          </View>
        );
      })}

      {active === 'all' || active === 'journal' ? (
        <>
          <SectionLabel>{L('日志', 'Journal')}</SectionLabel>
          {entries.length ? <Timeline entries={entries} />
            : <Card><T v="callout" color={t.ink2}>{L(`还没有记录。在对话里说感受、想法或决定，${agentName()} 会记在这里。`, `Nothing yet. Share feelings, thoughts or decisions in chat and ${agentName()} will log them here.`)}</T></Card>}
        </>
      ) : null}

      <T v="caption" color={t.ink3} style={styles.foot}>{L('每天 04:00 前它写一次日结，结论会存进这里。点一条看全文，也可以让它忘记。', 'Before 04:00 each day it writes a daily digest and saves the conclusions here. Tap an item to read all of it, or to make it forget.')}</T>
    </View>
  );
}

const styles = StyleSheet.create({
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: space.sm, marginTop: space.xs },
  chip: { height: 32, borderRadius: 16, borderWidth: 1, paddingHorizontal: 13, alignItems: 'center', justifyContent: 'center' },
  mem: { paddingVertical: 13 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  title: { fontSize: 15, fontWeight: '600', lineHeight: 20 },
  full: { borderRadius: 12, paddingVertical: 10, paddingHorizontal: space.md },
  jr: { flexDirection: 'row', alignItems: 'flex-start', gap: 8, paddingVertical: 9 },
  jt: { width: 42, fontWeight: '400', marginTop: 3, fontVariant: ['tabular-nums'] },
  jd: { width: 8, height: 8, borderRadius: 4, marginTop: 7 },
  jmore: { marginLeft: 58, gap: 6, paddingBottom: 8 },
  foot: { marginTop: space.lg, paddingHorizontal: space.xs, lineHeight: 18, fontWeight: '400' },
});
