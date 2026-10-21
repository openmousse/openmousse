// 记忆页顶上的「每周修剪」（2026-10-20，设计第 22 张）：收件箱里那一周的修剪卡（itemsKind tree-prune，server/treeprune.py；
// 周日晚上出，一周后过期），在这里一条一条定。每条一行字，下面一行回原话核对的结果（服务器写好：与原对话相符 / 与原对话不符… /
// 找不到原对话的记录…；对不上、找不到的服务器默认「拒绝」），「同意 / 修改 / 拒绝」三选一（选中的是白底），选「修改」写一句怎么改；
// 底下金色「提交」，提交完收成一行回执「已修剪 3 条：同意 1 · 修改 1 · 拒绝 1」。
// 「修改」照服务器的规矩（inbox.py：按你写的那句重出那一条，换掉以后回到没定、等你再定）：有「修改」的那次「提交」先把要改的重出，
// 卡留着，改写过的标「已按说明改写」、等你再看一眼；没有要改的了才真的提交（POST /api/inbox/{id} {action: submit, decisions}）。
// 卡上接着的待定（别人说的，id held-…，默认「拒绝」）也在这里定；这一周它们不再列在「待确认」里（RecentCard），免得两处各点一次。
// 对话里、今天页里那张照旧是整张点的卡（InboxCard.tsx 不动）；以后接进 InboxCard：逐条的卡（caps 有 items）换成 <PruneItems card={item} /> 一行。
import { useNavigation } from '@react-navigation/native';
import React, { useState } from 'react';
import { Pressable, StyleSheet, TextInput, View } from 'react-native';
import type { InboxCardItem, InboxItem } from '../data/types';
import { L } from '../i18n';
import { todayIso, useStore } from '../store';
import { radius, space, type, useTheme } from '../theme';
import { Check, ChevronRight, TriangleAlert } from './icons';
import { Btn, Card, Disclosure, Segmented, T, showError } from './ui';

type Choice = 'approve' | 'revise' | 'reject';

/** 那一周的修剪卡：还在等你定、能逐条点的（服务器 inbox.items 关着时 caps 是空的，照旧在今天页整张点）。 */
export const weeklyPrune = (inbox: InboxItem[]) => inbox.find((i) => i.itemsKind === 'tree-prune' && !!i.items?.length && !!i.caps?.includes('items')) ?? null;

/** 修剪卡上接着的待定（data.held = 待定的 id）。 */
export const heldOnCard = (card: InboxItem | null) => new Set((card?.items ?? []).map((x) => x.data?.held).filter((v): v is string => typeof v === 'string'));

/** 核对的结果怎么画：相符 = 勾；对不上、找不到 = 三角（这两种服务器默认拒绝）；别的（查不了、没有出处、别人说的）只写字。 */
function CheckLine({ x, onOpen }: { x: InboxCardItem; onOpen?: () => void }) {
  const t = useTheme();
  const c = x.check;
  if (!c?.text) return null;
  const bad = c.state === 'mismatch' || c.state === 'missing' || c.state === 'flagged';
  const color = c.state === 'ok' ? t.good : bad ? t.warn : t.ink3;
  const body = (
    <View style={styles.check}>
      {c.state === 'ok' ? <Check size={13} color={t.good} strokeWidth={3} /> : bad ? <TriangleAlert size={13} color={t.warn} /> : null}
      <T v="caption" color={color} numberOfLines={2} style={{ flex: 1, fontWeight: '500' }}>{c.text}</T>
      {onOpen ? <ChevronRight size={13} color={t.ink3} /> : null}
    </View>
  );
  return onOpen ? (
    <Pressable onPress={onOpen} accessibilityRole="link" accessibilityHint={L('打开原对话', 'Opens the original chat')} style={({ pressed }) => ({ opacity: pressed ? 0.6 : 1 })}>{body}</Pressable>
  ) : body;
}

function ItemRow({ x, first, choice, note, redone, disabled, onChoice, onNote }: {
  x: InboxCardItem; first: boolean; choice: Choice; note: string; redone: boolean; disabled: boolean;
  onChoice: (c: Choice) => void; onNote: (s: string) => void;
}) {
  const t = useTheme();
  const nav = useNavigation<any>();
  const [open, setOpen] = useState(false);
  const c = x.check;
  const go = c?.thread && c.day ? () => nav.navigate('HistoryDay', { thread: c.thread, day: c.day, focus: c.message && /^\d+$/.test(c.message) ? `db${c.message}` : undefined }) : undefined;
  return (
    <View style={[styles.item, !first && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }]}>
      <Pressable onPress={x.detail ? () => setOpen((v) => !v) : undefined} disabled={!x.detail} accessibilityRole={x.detail ? 'button' : undefined}
        accessibilityState={x.detail ? { expanded: open } : undefined} style={({ pressed }) => [styles.itemHead, { opacity: pressed ? 0.6 : 1 }]}>
        <T v="body" style={{ flex: 1, fontSize: 15, lineHeight: 21 }}>{x.text}</T>
        {x.detail ? <Disclosure open={open} /> : null}
      </Pressable>
      {redone ? <T v="caption" color={t.ink2} style={{ fontWeight: '600' }}>{L('已按说明改写，再看一眼', 'Revised as you said; have another look')}</T> : null}
      <CheckLine x={x} onOpen={go} />
      {open && x.detail ? <T v="caption" color={t.ink3} style={styles.detail}>{x.detail}</T> : null}
      <View pointerEvents={disabled ? 'none' : 'auto'} style={{ opacity: disabled ? 0.6 : 1 }}>
        <Segmented<Choice> value={choice} onChange={onChoice}
          options={[{ value: 'approve', label: L('同意', 'Approve') }, { value: 'revise', label: L('修改', 'Change') }, { value: 'reject', label: L('拒绝', 'Decline') }]} />
      </View>
      {choice === 'revise' ? (
        <TextInput value={note} onChangeText={onNote} editable={!disabled} maxLength={500} multiline placeholder={L('写一句怎么改', 'Say how to change it')} placeholderTextColor={t.ink3}
          accessibilityLabel={L('怎么改', 'How to change it')} style={[type.callout, styles.note, { color: t.ink, backgroundColor: t.surface2 }]} />
      ) : null}
    </View>
  );
}

/** 卡本身：逐条选，最后提交。 */
export function PruneItems({ card }: { card: InboxItem }) {
  const t = useTheme();
  const { reviseInboxItem, submitInboxItems } = useStore();
  const items = card.items ?? [];
  // 这次在卡上选的、「修改」写的那句；没选过的照服务器的（逐条点过的 > 默认）
  const [choice, setChoice] = useState<Record<string, Choice>>({});
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [redone, setRedone] = useState<string[]>([]);
  const [busy, setBusy] = useState<'revise' | 'submit' | null>(null);
  const [hint, setHint] = useState('');
  const [warned, setWarned] = useState(false);
  const pick = (x: InboxCardItem): Choice => choice[x.id] ?? x.decision ?? x.default;
  const changing = items.filter((x) => pick(x) === 'revise');
  const submit = async () => {
    if (busy) return;
    if (changing.some((x) => !(notes[x.id] ?? '').trim())) { setHint(L('选了「修改」的，写一句怎么改。', 'For each one set to Change, say how to change it.')); return; }
    setHint('');
    if (changing.length) {
      setBusy('revise');
      const done: string[] = [];
      try {
        for (const x of changing) { await reviseInboxItem(card.id, x.id, notes[x.id].trim()); done.push(x.id); }
      } catch (e) {
        showError(L('修改失败', "Couldn't change it"), e);
      } finally {
        // 改写过的回到没定（服务器的规矩），照它新的默认；没改成的留着你写的那句
        setRedone((r) => [...r.filter((id) => !done.includes(id)), ...done]);
        setChoice((m) => Object.fromEntries(Object.entries(m).filter(([id]) => !done.includes(id))));
        setNotes((m) => Object.fromEntries(Object.entries(m).filter(([id]) => !done.includes(id))));
        setBusy(null);
      }
      return;
    }
    // 刚改写过、你还没再点过的，照服务器的默认可能是「拒绝」（核对不上的、别人说的）：先提醒一次，免得改好了一提交就被拒
    const dropping = items.filter((x) => redone.includes(x.id) && !choice[x.id] && pick(x) === 'reject');
    if (dropping.length && !warned) {
      setWarned(true);
      setHint(L(`刚改写的 ${dropping.length} 条现在是「拒绝」：要留下就点「同意」，不要就再点一次提交。`,
        `The ${dropping.length} just revised are set to Decline: tap Approve to keep them, or Submit again to let them go.`));
      return;
    }
    setBusy('submit');
    try {
      await submitInboxItems(card.id, Object.fromEntries(items.map((x) => [x.id, pick(x) === 'approve' ? 'approve' : 'reject'])));
    } catch (e) {
      showError(L('提交失败', "Couldn't submit"), e);
      setBusy(null);
    }
  };
  const label = busy === 'revise' ? L('正在按说明改写…', 'Revising…') : busy === 'submit' ? L('正在提交…', 'Submitting…') : L('提交', 'Submit');
  return (
    <Card style={{ gap: space.sm }}>
      <T v="headline" style={styles.title}>{L(`每周修剪 · ${items.length} 条`, `Weekly pruning · ${items.length}`)}</T>
      <T v="callout" color={t.ink2}>{L('写入档案前，先与原对话核对；不符的暂不合并。', "Checked against the original chats before anything goes into your profile; what doesn't match isn't merged for now.")}</T>
      <View>
        {items.map((x, i) => (
          <ItemRow key={x.id} x={x} first={i === 0} choice={pick(x)} note={notes[x.id] ?? ''} redone={redone.includes(x.id)} disabled={!!busy}
            onChoice={(c) => { setHint(''); setWarned(false); setChoice((m) => ({ ...m, [x.id]: c })); }} onNote={(s) => setNotes((m) => ({ ...m, [x.id]: s }))} />
        ))}
      </View>
      {changing.length ? (
        <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L(`选了「修改」的 ${changing.length} 条先按说明改写，改好后再提交。`, `The ${changing.length} set to Change are revised first; submit again once they're done.`)}</T>
      ) : null}
      {hint ? <T v="caption" color={t.warn} style={{ fontWeight: '500' }}>{hint}</T> : null}
      <Btn label={label} onPress={() => { submit().catch(() => {}); }} />
    </Card>
  );
}

/** 提交以后：一行回执（这次打开 app 以来定的，留到换天，和今天页的回执一样）。 */
function PruneReceipt({ item }: { item: InboxItem }) {
  const t = useTheme();
  const items = item.items ?? [];
  const changed = items.filter((x) => x.decision === 'approve' && x.was.length).length;
  const yes = items.filter((x) => x.decision === 'approve').length - changed;
  const no = items.length - yes - changed;
  const line = yes + changed
    ? L(`已修剪 ${items.length} 条：同意 ${yes} · 修改 ${changed} · 拒绝 ${no}`, `Pruned ${items.length}: ${yes} approved · ${changed} changed · ${no} declined`)
    : L(`这周的 ${items.length} 条修剪都没同意，世界树不变`, `None of this week's ${items.length} pruning steps were approved; the tree stays as it is`);
  return (
    <Card style={styles.receipt}>
      <Check size={16} color={t.good} strokeWidth={3} />
      <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
        <T v="callout" style={{ fontWeight: '600' }}>{line}</T>
        {item.result ? <T v="caption" color={t.ink3} numberOfLines={2} style={{ fontWeight: '400' }}>{item.result}</T> : null}
      </View>
    </Card>
  );
}

/** 记忆页顶上那一块：有等你定的修剪卡就是它，今天刚定过的是回执，都没有不占地方。 */
export function PruneCard() {
  const { inbox, receipts } = useStore();
  const card = weeklyPrune(inbox);
  if (card) return <PruneItems key={card.id} card={card} />;
  const day = todayIso();
  const done = receipts.find((r) => r.day === day && r.item.itemsKind === 'tree-prune')?.item;
  return done ? <PruneReceipt item={done} /> : null;
}

const styles = StyleSheet.create({
  title: { fontSize: 18, fontWeight: '700' },
  item: { paddingVertical: 12, gap: 8 },
  itemHead: { flexDirection: 'row', alignItems: 'flex-start', gap: space.sm },
  check: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  detail: { fontWeight: '400', lineHeight: 17 },
  note: { borderRadius: radius.md, paddingHorizontal: 12, paddingVertical: 9, minHeight: 40, textAlignVertical: 'top' },
  receipt: { flexDirection: 'row', alignItems: 'center', gap: space.sm },
});
