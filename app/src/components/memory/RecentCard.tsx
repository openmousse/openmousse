// 记忆页第一张卡「记忆」（2026-10-20，设计第 20 张第三版）：共几条、最近 7 天新增几条、年轮（viz 的 MemoryRings），
// 环下面是「待确认」：你说的待确认（世界树里 status pending 的叶子，开了 require_confirm 才有）和别人说的待定（server/memhold.py）
// 合成一处（第三版的提议，主人批了这一批设计），一条一行，「记下」「不记」。收件箱里跟记忆有关的卡（每周修剪、改做法的提议）
// 照旧在今天页处理，这里不列。世界树没接上、太老：照世界树页的说法。
import React, { useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import type { HeldItem, InboxItem, TreeLeaf, TreeState } from '../../data/types';
import { L } from '../../i18n';
import { ForgetSheet, leafTitle, shortDate, spaced, useLeafSheet } from '../../screens/TreeScreen';
import { useStore } from '../../store';
import { radius, space, useTheme } from '../../theme';
import { needsUpdate } from '../../api/version';
import { useSheet } from '../Sheet';
import { Card, Pill, T, showError } from '../ui';
import { MemoryRings, type RingDot } from '../viz';
import { heldOnCard, weeklyPrune } from '../PruneCard';

export const DAYS = 7;

/** 这一条是今天往前第几天（本机的日历日，0 = 今天）；不在最近 DAYS 天里 → -1。 */
export function dayIndex(iso: string): number {
  if (!iso) return -1;
  const d = /^\d{4}-\d{2}-\d{2}$/.test(iso) ? new Date(`${iso}T12:00:00`) : new Date(iso);
  if (Number.isNaN(d.getTime())) return -1;
  const now = new Date();
  const start = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const n = Math.round((start(now) - start(d)) / 86400000);
  return n >= 0 && n < DAYS ? n : -1;
}

/** 底栏「记忆」的角标：待确认的条数（你说的待确认 + 别人说的待定），这周的修剪卡在的话加上卡上的条数（也在这一页定）。 */
export function memoryBadge(tree: TreeState | null, held: HeldItem[] | null, inbox: InboxItem[]): number {
  const card = weeklyPrune(inbox);
  return pendingOf(tree, held, heldOnCard(card)).length + (card?.items?.length ?? 0);
}

/** 别人说的那一条是不是对方要求记下的（memhold 把它写成 flags 的第一句）。 */
const asked = (h: HeldItem) => h.flags.some((f) => /要求记下|asked for this/i.test(f));
/** 别的标记（内容检查挡下的毛病：叫 AI 绕过关卡、往外发数据……）：琥珀一行写出来，你自己判断。 */
const warnings = (h: HeldItem) => h.flags.filter((f) => !/要求记下|asked for this/i.test(f));

type Pending = { kind: 'mine'; key: string; at: string; leaf: TreeLeaf } | { kind: 'theirs'; key: string; at: string; item: HeldItem };

/** 待确认的全部：你说的 + 别人说的，新的在前。skip：接在这周修剪卡上的待定（在修剪卡上定，这里不再列）。 */
export function pendingOf(tree: TreeState | null, held: HeldItem[] | null, skip?: Set<string>): Pending[] {
  const mine: Pending[] = tree?.kind === 'ok' ? tree.data.leaves.filter((l) => l.status === 'pending').map((leaf) => ({ kind: 'mine', key: leaf.id, at: leaf.createdAt || leaf.observedAt, leaf })) : [];
  const theirs: Pending[] = (held ?? []).filter((item) => !skip?.has(item.id)).map((item) => ({ kind: 'theirs', key: `held:${item.id}`, at: item.createdAt, item }));
  return [...mine, ...theirs].sort((a, b) => b.at.localeCompare(a.at));
}

function SmallBtn({ label, onPress, primary, disabled }: { label: string; onPress: () => void; primary?: boolean; disabled?: boolean }) {
  const t = useTheme();
  return (
    <Pressable onPress={onPress} disabled={disabled} accessibilityRole="button" accessibilityLabel={label} hitSlop={4}
      style={({ pressed }) => [styles.small, { backgroundColor: primary ? t.goldFill : t.surface2, opacity: pressed || disabled ? 0.6 : 1 }]}>
      <T v="callout" color={primary ? t.onGold : t.ink2} style={{ fontWeight: '600' }}>{label}</T>
    </Pressable>
  );
}

/** 待确认的叶子里也有别人说的：待定里记下的长成叶子，世界树要确认的（require_confirm）照样先是待确认（tier inferred，出处是好友、群聊、别人的 Agent）。 */
const OUTSIDE = new Set(['friend', 'a2a', 'group', 'card']);
const fromOthers = (l: TreeLeaf) => l.tier === 'inferred' && !!l.said && OUTSIDE.has(l.said.platform);

/** 待确认的一条：一句话、「你说的 / 别人说的」、从哪来（别人说的写谁、经哪里），「记下」「不记」。
 *  onKeep / onGrown / onFail：年轮上那个点落到今天的环上 / 别人说的那一条长成了哪片叶子 / 没记成退回去。 */
function PendingRow({ p, first, onOpen, onKeep, onGrown, onFail }: {
  p: Pending; first: boolean; onOpen: (leaf: TreeLeaf) => void; onKeep: (p: Pending) => void; onGrown: (p: Pending, leaf?: string) => void; onFail: (p: Pending) => void;
}) {
  const t = useTheme();
  const { treeAction, heldAction } = useStore();
  const sheet = useSheet();
  const [busy, setBusy] = useState<'keep' | 'drop' | null>(null);
  const run = (what: 'keep' | 'drop') => {
    if (busy) return;
    if (what === 'drop' && p.kind === 'mine') {  // 世界树里的叶子：不记 = 忘记（正文删掉、找不回），先确认，同世界树页
      sheet.open({ title: L('忘记此条？', 'Forget this?'), content: (c) => <ForgetSheet leaf={p.leaf} close={c} /> });
      return;
    }
    setBusy(what);
    if (what === 'keep') onKeep(p);
    const job: Promise<string | undefined | void> = p.kind === 'mine'
      ? treeAction(p.leaf.id, what === 'keep' ? 'confirm' : 'forget') : heldAction(p.item.id, what === 'keep' ? 'approve' : 'reject');
    job.then((leaf) => { if (what === 'keep' && p.kind === 'theirs') onGrown(p, leaf || undefined); }).catch((e) => {
      if (what === 'keep') onFail(p);
      showError(what === 'keep' ? L('记下失败', "Couldn't keep it") : L('操作失败', "Couldn't complete the action"), e);
    }).finally(() => setBusy(null));
  };
  const text = p.kind === 'mine' ? leafTitle(p.leaf.text) : p.item.text;
  const others = p.kind === 'theirs' || fromOthers(p.leaf);
  const from = p.kind === 'theirs'
    ? [p.item.whoLine || p.item.who, asked(p.item) ? L('对方让记下', 'they asked to keep it') : '', L('记下算推断', 'kept as inferred')].filter(Boolean).join(' · ')
    : [others ? p.leaf.said?.platformName : L(`${spaced(p.leaf.originName)}记下`, `Saved by ${p.leaf.originName}`), shortDate(p.leaf.observedAt || p.leaf.createdAt.slice(0, 10)),
      others ? L('记下算推断', 'kept as inferred') : ''].filter(Boolean).join(' · ');
  const warn = p.kind === 'theirs' ? warnings(p.item) : [];
  return (
    <View style={[styles.row, !first && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }]}>
      <Pressable onPress={p.kind === 'mine' ? () => onOpen(p.leaf) : undefined} disabled={p.kind !== 'mine'} accessibilityRole={p.kind === 'mine' ? 'button' : undefined}
        style={({ pressed }) => ({ opacity: pressed ? 0.6 : 1 })}>
        <T v="body" numberOfLines={3} style={{ fontSize: 15, lineHeight: 21 }}>{text}</T>
      </Pressable>
      <View style={styles.meta}>
        <Pill label={others ? L('别人说的', 'Someone else said') : L('你说的', 'You said')} />
        <T v="caption" color={t.ink3} numberOfLines={2} style={{ flex: 1, fontWeight: '400' }}>{from}</T>
      </View>
      {warn.length ? <T v="caption" color={t.warn} numberOfLines={2} style={{ fontWeight: '400' }}>{warn[0]}</T> : null}
      <View style={styles.btns}>
        <SmallBtn primary label={busy === 'keep' ? L('正在记下…', 'Keeping…') : L('记下', 'Keep')} disabled={!!busy} onPress={() => run('keep')} />
        <SmallBtn label={busy === 'drop' ? L('正在处理…', 'Working…') : L('不记', "Don't keep")} disabled={!!busy} onPress={() => run('drop')} />
      </View>
    </View>
  );
}

/** 世界树读不出来时卡里那一句（照世界树页的说法）。 */
function TreeStateLine() {
  const t = useTheme();
  const { tree, connected, booting, dataErrors, loading } = useStore();
  if (tree?.kind === 'ok') return null;
  const line = booting ? L('正在连接服务器…', 'Connecting to the server…')
    : !connected ? L('未连接服务器。', 'Not connected to the server.')
      : dataErrors.tree ? L(`无法加载世界树：${dataErrors.tree}`, `Couldn't load the memory tree: ${dataErrors.tree}`)
        : tree?.kind === 'unsupported' ? needsUpdate('当前服务器不支持此页面。', "This server doesn't have this page yet.")
          : loading.tree || !tree ? L('正在加载…', 'Loading…') : '';
  if (tree?.kind === 'missing') {
    return (
      <View style={{ gap: 4 }}>
        <T v="headline" style={{ fontSize: 15 }}>{L('服务器尚未接入世界树', "The memory tree isn't connected on the server")}</T>
        <T v="callout" color={t.ink2}>{tree.hint || L('接入后将显示在这里。', 'Once it is, it shows up here.')}</T>
      </View>
    );
  }
  return line ? <T v="callout" color={dataErrors.tree ? t.bad : t.ink2}>{line}</T> : null;
}

export function RecentCard() {
  const t = useTheme();
  const sheet = useSheet();
  const open = useLeafSheet();
  const { tree, held, inbox } = useStore();
  const data = tree?.kind === 'ok' ? tree.data : null;
  const pending = pendingOf(tree, held, heldOnCard(weeklyPrune(inbox)));
  // 这次点了「记下」的：点的 key → 那片叶子（你说的 = 它自己；别人说的 = 服务器回的新叶子 id，回来之前先空着，点留在环外）和那一句。
  // 它们的点落到今天的环上（同一个 key，点滑过去）。没记成就退回去；别人说的和已有的叶子重复（没长新的）、老服务器不回 id：点收掉。
  const [kept, setKept] = useState<Record<string, { leaf?: string; label: string }>>({});
  const drop = (m: typeof kept, key: string) => { const { [key]: _, ...rest } = m; return rest; };
  const onKeep = (p: Pending) => setKept((m) => ({ ...m, [p.key]: p.kind === 'mine' ? { leaf: p.leaf.id, label: leafTitle(p.leaf.text) } : { label: p.item.text } }));
  const onGrown = (p: Pending, leaf?: string) => setKept((m) => (leaf && m[p.key] ? { ...m, [p.key]: { ...m[p.key], leaf } } : drop(m, p.key)));
  const onFail = (p: Pending) => setKept((m) => drop(m, p.key));
  const leaves = data?.leaves ?? [];
  const keyOf = new Map<string, string>();  // 叶子 id → 年轮上的点的 key
  for (const [key, k] of Object.entries(kept)) if (k.leaf && !keyOf.has(k.leaf) && leaves.some((l) => l.id === k.leaf)) keyOf.set(k.leaf, key);
  // 年轮上的点：最近 7 天长出来的（按 createdAt，本机的日子）；待确认的画在外面
  const dots: RingDot[] = [];
  for (const l of leaves) {
    if (l.status === 'pending') continue;
    const key = keyOf.get(l.id);
    const day = key ? 0 : dayIndex(l.createdAt || l.observedAt);
    if (day >= 0) dots.push({ key: key ?? l.id, day, label: leafTitle(l.text) });
  }
  for (const p of pending) dots.push({ key: p.key, day: null, label: p.kind === 'mine' ? leafTitle(p.leaf.text) : p.item.text });
  const placed = new Set(dots.map((d) => d.key));
  for (const [key, k] of Object.entries(kept)) if (!placed.has(key) && !k.leaf) dots.push({ key, day: null, label: k.label });  // 别人说的：正在长成叶子
  const fresh = dots.filter((d) => d.day != null).length;
  const pick = (key: string) => {
    const leaf = leaves.find((l) => (keyOf.get(l.id) ?? l.id) === key);
    if (leaf) { open(leaf); return; }
    const p = pending.find((x) => x.key === key);
    if (p?.kind === 'theirs') sheet.open({ title: L('别人说的', 'Someone else said'), content: () => <HeldDetail item={p.item} /> });
  };
  return (
    <Card style={{ gap: space.md }}>
      <View style={styles.head}>
        <T v="headline" style={styles.title}>{L('记忆', 'Memory')}</T>
        {data ? <T v="callout" color={t.ink3} style={styles.tnum}>{L(`共 ${data.counts.total} 条`, `${data.counts.total} in all`)}</T> : null}
      </View>
      <TreeStateLine />
      {data ? (
        <>
          <T v="callout" color={t.ink2} style={[styles.tnum, { marginTop: -8 }]}>{L(`最近 ${DAYS} 天 新增 ${fresh} 条`, `${fresh} new in the last ${DAYS} days`)}</T>
          <MemoryRings dots={dots} days={DAYS} onPick={pick} emptyText={L('最近 7 天没有新记忆', 'Nothing new in the last 7 days')} />
          <T v="caption" color={t.ink3} style={{ fontWeight: '400', lineHeight: 17, textAlign: 'center' }}>
            {L('外圈为今天，向内依次为前 6 天；每个点是一条新记忆', 'The outer ring is today, then the 6 days before it inward; each dot is a new memory')}
          </T>
        </>
      ) : null}
      <View style={[styles.split, { borderTopColor: t.line }]}>
        <T v="label" color={t.ink3}>{L(`待确认 · ${pending.length}`, `TO CONFIRM · ${pending.length}`)}</T>
        {pending.length ? (
          <>
            <View>{pending.map((p, i) => <PendingRow key={p.key} p={p} first={i === 0} onOpen={open} onKeep={onKeep} onGrown={onGrown} onFail={onFail} />)}</View>
            <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L('点「记下」才进记忆', 'Only what you keep goes into memory')}</T>
          </>
        ) : <T v="callout" color={t.ink2}>{L('没有待确认的记忆。', 'Nothing waiting to be confirmed.')}</T>}
      </View>
    </Card>
  );
}

/** 年轮上点了别人说的那一条：原话和是谁、经哪里（记下 / 不记在卡里那一行）。 */
function HeldDetail({ item }: { item: HeldItem }) {
  const t = useTheme();
  return (
    <View style={{ gap: space.md }}>
      <View style={[styles.box, { backgroundColor: t.surface }]}><T v="body" style={{ lineHeight: 24 }}>{item.text}</T></View>
      {item.quote ? <T v="callout" color={t.ink2}>{L(`原话：「${item.quote}」`, `In their words: "${item.quote}"`)}</T> : null}
      <T v="callout" color={t.ink2}>{item.whoLine || item.who}</T>
      {warnings(item).map((w) => <T key={w} v="caption" color={t.warn} style={{ fontWeight: '400' }}>{w}</T>)}
      <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L('别人说的记下了算推断，不当成你自己的话。', "Kept from someone else, it counts as inferred, not as your own words.")}</T>
    </View>
  );
}

const styles = StyleSheet.create({
  head: { flexDirection: 'row', alignItems: 'baseline', gap: space.sm },
  title: { flex: 1, fontSize: 18, fontWeight: '700' },
  tnum: { fontVariant: ['tabular-nums'] },
  split: { borderTopWidth: StyleSheet.hairlineWidth, paddingTop: space.md, gap: space.sm },
  row: { paddingVertical: 12, gap: 8 },
  meta: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  btns: { flexDirection: 'row', gap: space.sm },
  small: { borderRadius: radius.pill, paddingHorizontal: 16, paddingVertical: 7, minWidth: 64, alignItems: 'center' },
  box: { borderRadius: radius.md, paddingVertical: 12, paddingHorizontal: space.md },
});
