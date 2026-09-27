// 我 → 世界树（server/memtree.py）：你在 Claude、ChatGPT、Gemini 和各个 Agent 那里说过的关于你的事，一条一片叶子，挂在枝上。
// 顶上是等你确认的（逐条确认 / 忘记）；下面按枝看（主干 = 档案 → 大枝 → 小枝，点开看叶子）或按来源看。
// 点一片叶子：弹层标题是「类型 · 挂在哪根枝」，里面是全文、来源 · 日期、标签，确认 / 挪到别的枝 / 忘记。
// 真身是 Obsidian 库里的「世界树」文件夹，这里只是看和整理（server/memtree.py 经 workspace 的 memory_tree.py 改笔记）。
import React, { useEffect, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, View } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { agentName } from '../brand';
import { Check, ChevronRight, GitBranch, IdCard, Trash2 } from '../components/icons';
import { clean, memoryTitle } from '../components/memoryText';
import { useSheet } from '../components/Sheet';
import { SourcePill } from '../components/SourceBadge';
import { Btn, Card, Disclosure, NavHeader, Pill, PullRefresh, Screen, SectionLabel, Segmented, T, showError } from '../components/ui';
import type { TreeBranch, TreeInfo, TreeLeaf } from '../data/types';
import { L, lang } from '../i18n';
import { useStore } from '../store';
import { radius, space, useTheme } from '../theme';

// —— 文字 ————————————————————————————————————————————————————————————

const kindLabel = (k: TreeLeaf['kind']) => ({ fact: L('事实', 'Fact'), preference: L('偏好', 'Preference'), decision: L('决定', 'Decision'), event: L('近况', 'Update') })[k] ?? k;
const parts = (iso: string) => iso.split('-').map(Number);
/** 列表里的日子：9/26。 */
const shortDate = (iso: string) => { const [, m, d] = parts(iso); return m && d ? `${m}/${d}` : ''; };
/** 弹层里的日子：9月26日（不是今年的带年份）。 */
function longDate(iso: string): string {
  const [y, m, d] = parts(iso);
  if (!y || !m || !d) return '';
  const same = y === new Date().getFullYear();
  return lang() === 'zh' ? `${same ? '' : `${y}年`}${m}月${d}日` : new Date(y, m - 1, d).toLocaleDateString('en', same ? { month: 'short', day: 'numeric' } : { year: 'numeric', month: 'short', day: 'numeric' });
}
/** 主干（档案）的叫法。 */
const trunkLabel = (name: string) => L(`${name}（主干）`, `${name} (trunk)`);
/** 列表里一片叶子的标题：memoryTitle 取得出完整的一句（冒号前那段、第一句）就用它；要截断时用去掉括号的原文，最多两行。 */
const leafTitle = (text: string) => { const t = memoryTitle(text); return t.endsWith('…') ? clean(text) || t : t; };
/** 展开一根枝时上面那行小字：它管什么，再加上哪个 Agent 记下的默认挂在这里。 */
function branchNote(b: TreeBranch): string {
  const who = b.agents.map((a) => a.name);
  const agents = who.length ? L(`Agent「${who.join('」「')}」记下的默认挂在这里。`, `What the ${who.join(', ')} agent saves lands here.`) : '';
  if (!b.about) return agents;
  const about = lang() === 'zh' && !/[。！？.!?]$/.test(b.about) ? `${b.about}。` : b.about;
  return [about, agents].filter(Boolean).join(lang() === 'zh' ? '' : ' ');
}
/** 中文里夹英文名字时前后空一格（「ChatGPT 记下的」「健身规划记下的」）。 */
const spaced = (name: string) => (/[A-Za-z0-9]$/.test(name) ? `${name} ` : name);

// —— 小零件 ——————————————————————————————————————————————————————————

/** 最早是谁记下的：Agent 用它自己的颜色，AI 平台和你自己是中性色。 */
function OriginPill({ leaf }: { leaf: TreeLeaf }) {
  return leaf.agent ? <SourcePill source={leaf.agent} label={leaf.originName} /> : <Pill label={leaf.originName} />;
}

function SmallBtn({ label, onPress, primary, disabled }: { label: string; onPress: () => void; primary?: boolean; disabled?: boolean }) {
  const t = useTheme();
  return (
    <Pressable onPress={onPress} disabled={disabled} accessibilityRole="button" hitSlop={4}
      style={({ pressed }) => [styles.small, { backgroundColor: primary ? t.goldFill : t.surface2, opacity: pressed || disabled ? 0.6 : 1 }]}>
      <T v="callout" color={primary ? t.onGold : t.ink2} style={{ fontWeight: '600' }}>{label}</T>
    </Pressable>
  );
}

/** 一片叶子：一句标题（最多两行），下面一行小字 = 来源 · 日子（按来源看时换成挂在哪根枝）；待确认的前面标着「待确认」。点一下看全文和操作。 */
function LeafRow({ leaf, where, first }: { leaf: TreeLeaf; where?: string; first?: boolean }) {
  const t = useTheme();
  const open = useLeafSheet();
  return (
    <Pressable onPress={() => open(leaf)} accessibilityRole="button" accessibilityHint={L('看全文和操作', 'Shows the full text and actions')}
      style={({ pressed }) => [styles.leaf, { opacity: pressed ? 0.6 : 1 }, !first && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }]}>
      <T v="body" numberOfLines={2} style={styles.leafText}>{leafTitle(leaf.text)}</T>
      <View style={styles.leafMeta}>
        {leaf.status === 'pending' ? <T v="caption" color={t.warn}>{L('待确认', 'Unconfirmed')}</T> : null}
        {where === undefined ? <OriginPill leaf={leaf} /> : <T v="caption" color={t.ink3} numberOfLines={1} style={{ fontWeight: '400', flexShrink: 1 }}>{where}</T>}
        <T v="caption" color={t.ink3} style={styles.date}>{shortDate(leaf.observedAt)}</T>
      </View>
    </Pressable>
  );
}

// —— 弹层：一片叶子、忘记、挪枝 ——————————————————————————————————————————

/** 打开某片叶子的弹层（叶子列表、等你确认的卡都用它）。 */
function useLeafSheet() {
  const sheet = useSheet();
  const { tree } = useStore();
  const trunk = tree?.kind === 'ok' ? tree.data.trunk.name : '';
  // 标题是「类型 · 挂在哪」，全文在弹层里：短的一句话不会上下重复两遍
  return (leaf: TreeLeaf) => sheet.open({ title: `${kindLabel(leaf.kind)} · ${leaf.branch === trunk ? trunkLabel(trunk) : leaf.branch}`, content: (close) => <LeafSheet leaf={leaf} close={close} /> });
}

function LeafSheet({ leaf, close }: { leaf: TreeLeaf; close: () => void }) {
  const t = useTheme();
  const sheet = useSheet();
  const { treeAction } = useStore();
  const [busy, setBusy] = useState(false);
  const meta = [leaf.originName, longDate(leaf.observedAt)].filter(Boolean).join(' · ');
  const confirm = () => {
    setBusy(true);
    treeAction(leaf.id, 'confirm').then(close).catch((e) => { showError(L('没确认上', "Couldn't confirm it"), e); setBusy(false); });
  };
  return (
    <View style={{ gap: space.md }}>
      <View style={[styles.quote, { backgroundColor: t.surface }]}>
        <T v="body" selectable style={{ lineHeight: 24 }}>{leaf.text}</T>
      </View>
      <View style={{ gap: 4 }}>
        <T v="callout" color={t.ink2}>{meta}</T>
        {leaf.source === 'prune' && leaf.origin !== 'prune' ? <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L('每周修剪时改写过', 'Rewritten during the weekly pruning')}</T> : null}
        {leaf.tags.length ? <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{leaf.tags.map((x) => `#${x}`).join(' ')}</T> : null}
      </View>
      {leaf.status === 'pending' ? (
        <>
          <T v="caption" color={t.warn} style={{ fontWeight: '400' }}>{L(`${spaced(leaf.originName)}记下的，还等你确认。确认前各平台读到它都会标着「待确认」。`, `${leaf.originName} saved this and it's waiting for you. Until you confirm, every app sees it marked as unconfirmed.`)}</T>
          <Btn label={busy ? L('正在确认…', 'Confirming…') : L('确认', 'Confirm')} icon={<Check size={16} color={t.onGold} />} onPress={() => { if (!busy) confirm(); }} />
        </>
      ) : null}
      <View style={{ flexDirection: 'row', gap: space.sm }}>
        <Btn flex kind="quiet" label={L('挪到别的枝', 'Move')} icon={<GitBranch size={16} color={t.ink} />}
          onPress={() => sheet.open({ title: L('挪到哪根枝？', 'Move it to which branch?'), content: (c) => <MoveSheet leaf={leaf} close={c} /> })} />
        <Btn flex kind="danger" label={L('忘记', 'Forget')} icon={<Trash2 size={16} color={t.bad} />}
          onPress={() => sheet.open({ title: L('忘记这一条？', 'Forget this?'), content: (c) => <ForgetSheet leaf={leaf} close={c} /> })} />
      </View>
    </View>
  );
}

function ForgetSheet({ leaf, close }: { leaf: TreeLeaf; close: () => void }) {
  const t = useTheme();
  const { treeAction } = useStore();
  const [busy, setBusy] = useState(false);
  return (
    <View style={{ gap: space.md }}>
      <T v="body" color={t.ink2}>{L(`「${leaf.text}」`, `"${leaf.text}"`)}</T>
      <T v="callout" color={t.ink3}>{L(
        '内容会从库里删掉，只在归档里留一个空壳，哪个平台都不会再读到它。Obsidian 同步的版本历史里最长还留 1 个月。',
        "The text is deleted from your vault, leaving only an empty shell in the archive, and no app will read it again. Obsidian Sync's version history keeps it for up to a month.",
      )}</T>
      <View style={{ flexDirection: 'row', gap: space.sm }}>
        <Btn flex kind="quiet" label={L('留着', 'Keep')} onPress={close} />
        <Btn flex kind="danger" label={busy ? L('正在忘…', 'Forgetting…') : L('忘记', 'Forget')} onPress={() => {
          if (busy) return;
          setBusy(true);
          treeAction(leaf.id, 'forget').then(close).catch((e) => { showError(L('没忘掉', "Couldn't forget it"), e); setBusy(false); });
        }} />
      </View>
    </View>
  );
}

/** 挪到哪根枝：一组一行，大枝在前（加粗）、它的小枝跟在后面；现在挂的那根标着勾，点别的就挪过去。 */
function MoveSheet({ leaf, close }: { leaf: TreeLeaf; close: () => void }) {
  const t = useTheme();
  const { tree, treeAction } = useStore();
  const [busy, setBusy] = useState<string | null>(null);
  if (tree?.kind !== 'ok') return null;
  const { trunk, branches } = tree.data;
  const move = (name: string) => {
    if (busy || name === leaf.branch) return;
    setBusy(name);
    treeAction(leaf.id, 'move', name).then(close).catch((e) => { showError(L('没挪成', "Couldn't move it"), e); setBusy(null); });
  };
  const groups: { name: string; label: string; big: boolean }[][] = [[{ name: trunk.name, label: L(`直接挂在主干上（${trunk.name}）`, `Straight on the trunk (${trunk.name})`), big: false }]];
  for (const b of branches) {
    if (b.depth === 1 || groups.length === 1) groups.push([]);
    groups[groups.length - 1].push({ name: b.name, label: b.name, big: b.depth === 1 });
  }
  const chip = (c: { name: string; label: string; big: boolean }) => {
    const here = c.name === leaf.branch;
    return (
      <Pressable key={c.name} onPress={() => move(c.name)} disabled={here || !!busy} accessibilityRole="button" accessibilityState={{ selected: here, disabled: here || !!busy }}
        style={({ pressed }) => [styles.chip, { backgroundColor: here ? t.cyanSoft : c.big ? t.surface2 : t.surface, borderColor: here ? t.cyan : t.line, opacity: pressed || (busy && busy !== c.name) ? 0.6 : 1 }]}>
        {here ? <Check size={14} color={t.cyan} strokeWidth={3} /> : null}
        <T v="callout" color={here ? t.cyan : t.ink} style={c.big ? { fontWeight: '700' } : undefined}>{busy === c.name ? L('正在挪…', 'Moving…') : c.label}</T>
      </Pressable>
    );
  };
  return (
    <View style={{ gap: space.md }}>
      <T v="callout" color={t.ink2}>{leaf.branch === trunk.name
        ? L('现在直接挂在主干上。点一根枝就挪过去（每行第一个是大枝）。', 'It hangs straight on the trunk now. Tap a branch to move it (each row starts with a big branch).')
        : L(`现在挂在「${leaf.branch}」。点别的枝就挪过去（每行第一个是大枝）。`, `It hangs on "${leaf.branch}" now. Tap another branch to move it (each row starts with a big branch).`)}</T>
      {groups.map((g) => <View key={g[0]?.name ?? 'trunk'} style={styles.chips}>{g.map(chip)}</View>)}
    </View>
  );
}

// —— 等你确认 ————————————————————————————————————————————————————————

function PendingCard({ leaves }: { leaves: TreeLeaf[] }) {
  const t = useTheme();
  const open = useLeafSheet();
  const sheet = useSheet();
  const { treeAction } = useStore();
  const [busy, setBusy] = useState<string | null>(null);
  const confirm = (leaf: TreeLeaf) => {
    setBusy(leaf.id);
    treeAction(leaf.id, 'confirm').catch((e) => showError(L('没确认上', "Couldn't confirm it"), e)).finally(() => setBusy(null));
  };
  return (
    <>
      <SectionLabel>{L(`等你确认 · ${leaves.length}`, `Waiting for you · ${leaves.length}`)}</SectionLabel>
      <Card style={{ paddingVertical: space.xs }}>
        {leaves.map((leaf, i) => (
          <View key={leaf.id} style={[styles.pending, i > 0 && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }]}>
            <Pressable onPress={() => open(leaf)} accessibilityRole="button" accessibilityHint={L('看全文', 'Shows the full text')} style={({ pressed }) => ({ opacity: pressed ? 0.6 : 1 })}>
              <T v="body" numberOfLines={2}>{leafTitle(leaf.text)}</T>
            </Pressable>
            <View style={styles.pendingFoot}>
              <View style={styles.pendingMeta}>
                <OriginPill leaf={leaf} />
                <T v="caption" color={t.ink3}>{shortDate(leaf.observedAt)}</T>
              </View>
              <SmallBtn primary label={busy === leaf.id ? L('确认中…', 'Confirming…') : L('确认', 'Confirm')} disabled={!!busy} onPress={() => confirm(leaf)} />
              <SmallBtn label={L('忘记', 'Forget')} disabled={!!busy} onPress={() => sheet.open({ title: L('忘记这一条？', 'Forget this?'), content: (c) => <ForgetSheet leaf={leaf} close={c} /> })} />
            </View>
          </View>
        ))}
      </Card>
    </>
  );
}

// —— 按枝 ——————————————————————————————————————————————————————————

/** 一根枝一行：名字 + 叶子数（连它的小枝一起）；直接挂着叶子的点开在下面列出来（先写一句它管什么）。 */
function BranchRow({ branch, leaves, open, onToggle }: { branch: TreeBranch; leaves: TreeLeaf[]; open: boolean; onToggle: () => void }) {
  const t = useTheme();
  const big = branch.depth === 1;
  const indent = (Math.max(1, branch.depth) - 1) * 16;
  const canOpen = leaves.length > 0;
  return (
    <View>
      <Pressable onPress={canOpen ? onToggle : undefined} disabled={!canOpen} accessibilityRole={canOpen ? 'button' : undefined} accessibilityState={canOpen ? { expanded: open } : undefined}
        style={({ pressed }) => [styles.branch, { paddingLeft: indent, opacity: pressed ? 0.6 : 1 }]}>
        <T v="body" numberOfLines={1} style={[{ flex: 1 }, big ? styles.bigName : null]}>{branch.name}</T>
        <T v="callout" color={t.ink2} style={styles.count}>{branch.total}</T>
        <View style={{ width: 18, alignItems: 'flex-end' }}>{canOpen ? <Disclosure open={open} /> : null}</View>
      </Pressable>
      {open ? (
        <View style={{ paddingLeft: indent + 12, paddingBottom: space.xs }}>
          {branch.about || branch.agents.length ? <T v="caption" color={t.ink3} style={styles.about}>{branchNote(branch)}</T> : null}
          {leaves.map((leaf, i) => <LeafRow key={leaf.id} leaf={leaf} first={i === 0} />)}
        </View>
      ) : null}
    </View>
  );
}

function ByBranch({ data }: { data: TreeInfo }) {
  const t = useTheme();
  const nav = useNavigation<any>();
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const toggle = (k: string) => setOpen((m) => ({ ...m, [k]: !m[k] }));
  const on = (b: string) => data.leaves.filter((l) => l.branch === b);
  const trunkLeaves = on(data.trunk.name);
  // 有叶子的大枝，连同它下面有叶子的小枝（branches 是先序：大枝后面跟着它的小枝）；一片叶子都没有的枝收进最后一行
  const groups: { big: TreeBranch; small: TreeBranch[] }[] = [];
  for (const b of data.branches) {
    if (b.total === 0) continue;
    if (b.depth === 1 || !groups.length) groups.push({ big: b, small: [] });
    else groups[groups.length - 1].small.push(b);
  }
  const bare = data.branches.filter((b) => b.total === 0);
  const sep = { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line };
  return (
    <Card style={{ paddingVertical: space.xs }}>
      <Pressable onPress={() => nav.navigate('Identity')} accessibilityRole="button" accessibilityLabel={L(`${trunkLabel(data.trunk.name)}，${data.trunk.count} 条，打开基础档案`, `${trunkLabel(data.trunk.name)}, ${data.trunk.count} items, opens your profile`)}
        style={({ pressed }) => [styles.branch, { opacity: pressed ? 0.6 : 1 }]}>
        <IdCard size={18} color={t.cyan} />
        <T v="body" style={[{ flex: 1 }, styles.bigName]}>{trunkLabel(data.trunk.name)}</T>
        <T v="callout" color={t.ink2} style={styles.count}>{L(`${data.trunk.count} 条`, `${data.trunk.count}`)}</T>
        <View style={{ width: 18, alignItems: 'flex-end' }}><ChevronRight size={16} color={t.ink3} /></View>
      </Pressable>
      {trunkLeaves.length ? (
        <View style={sep}>
          <BranchRow branch={{ name: L('直接挂在主干上', 'Straight on the trunk'), parent: '', about: '', depth: 2, leaves: trunkLeaves.length, total: trunkLeaves.length, agents: [] }}
            leaves={trunkLeaves} open={!!open['#trunk']} onToggle={() => toggle('#trunk')} />
        </View>
      ) : null}
      {groups.map(({ big, small }) => (
        <View key={big.name} style={sep}>
          <BranchRow branch={big} leaves={on(big.name)} open={!!open[big.name]} onToggle={() => toggle(big.name)} />
          {small.map((b) => <BranchRow key={b.name} branch={b} leaves={on(b.name)} open={!!open[b.name]} onToggle={() => toggle(b.name)} />)}
        </View>
      ))}
      {bare.length ? (
        <View style={sep}>
          <Pressable onPress={() => toggle('#bare')} accessibilityRole="button" accessibilityState={{ expanded: !!open['#bare'] }}
            style={({ pressed }) => [styles.branch, { opacity: pressed ? 0.6 : 1 }]}>
            <T v="body" color={t.ink2} style={{ flex: 1 }}>{L('还没长叶子的枝', 'Branches with no leaves yet')}</T>
            <T v="callout" color={t.ink3} style={styles.count}>{bare.length}</T>
            <View style={{ width: 18, alignItems: 'flex-end' }}><Disclosure open={!!open['#bare']} /></View>
          </Pressable>
          {open['#bare'] ? (
            <View style={{ paddingBottom: space.sm, gap: 10 }}>
              {bare.map((b) => (
                <View key={b.name} style={{ gap: 1 }}>
                  <T v="callout" color={t.ink2}>{b.name}</T>
                  <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{[b.depth > 1 ? b.parent : '', b.about].filter(Boolean).join(' · ')}</T>
                </View>
              ))}
            </View>
          ) : null}
        </View>
      ) : null}
    </Card>
  );
}

// —— 按来源 ——————————————————————————————————————————————————————————

function BySource({ data }: { data: TreeInfo }) {
  const t = useTheme();
  return (
    <>
      {data.counts.bySource.map((s) => {
        const rows = data.leaves.filter((l) => l.origin === s.source);
        if (!rows.length) return null;
        return (
          <View key={s.source}>
            <SectionLabel caps={false} right={<T v="caption" color={t.ink3}>{rows.length}</T>}>{s.name}</SectionLabel>
            <Card style={{ paddingVertical: space.xs }}>
              {rows.map((leaf, i) => <LeafRow key={leaf.id} leaf={leaf} where={leaf.branch === data.trunk.name ? trunkLabel(data.trunk.name) : leaf.branch} first={i === 0} />)}
            </Card>
          </View>
        );
      })}
    </>
  );
}

// —— 页面 ————————————————————————————————————————————————————————————

function Note({ children, tone }: { children: React.ReactNode; tone?: 'bad' }) {
  const t = useTheme();
  return <Card style={{ marginTop: space.md, gap: 4 }}>{typeof children === 'string' ? <T v="callout" color={tone === 'bad' ? t.bad : t.ink2}>{children}</T> : children}</Card>;
}

export function TreeScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { tree, connected, booting, loading, dataErrors, reload } = useStore();
  const [view, setView] = useState<'branch' | 'source'>('branch');
  useEffect(() => { if (connected) reload('tree').catch(() => {}); }, [connected, reload]);  // 每次打开都按库的最新状态读
  const data = tree?.kind === 'ok' ? tree.data : null;
  const pending = data?.leaves.filter((l) => l.status === 'pending') ?? [];
  let state: React.ReactNode = null;
  if (!data) {
    if (booting) state = <Note>{L('正在连服务器…', 'Connecting to the server…')}</Note>;
    else if (!connected) state = <Note>{L('没连上服务器。检查「我 → 服务器」后下拉刷新。', 'Not connected to the server. Check Me → Server, then pull down to refresh.')}</Note>;
    else if (dataErrors.tree) state = <Note tone="bad">{L(`读不到世界树：${dataErrors.tree}`, `Couldn't load the memory tree: ${dataErrors.tree}`)}</Note>;
    else if (tree?.kind === 'missing') {
      state = (
        <Note>
          <T v="headline">{L('服务器上还没接世界树', "The memory tree isn't connected on the server")}</T>
          <T v="callout" color={t.ink2}>{tree.hint || L('接上以后这里就有了。', 'Once it is, it shows up here.')}</T>
        </Note>
      );
    } else if (tree?.kind === 'unsupported') state = <Note>{L('服务器的版本还没有这一页，更新服务器以后再来看。', "The server's version doesn't have this page yet. Update the server and check back.")}</Note>;
    else if (loading.tree || !tree) state = <Note>{L('正在读…', 'Loading…')}</Note>;
  }
  return (
    <Screen>
      <NavHeader title={L('世界树', 'Memory tree')} onBack={() => nav.goBack()} />
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl }} refreshControl={<PullRefresh onRefresh={() => reload('tree')} />}>
        <T v="callout" color={t.ink2}>{L(
          `你在 Claude、ChatGPT、Gemini 和 ${agentName()} 里说过的关于你的事，都长在这棵树上。真身是 Obsidian 库里的「世界树」文件夹。`,
          `What you've told Claude, ChatGPT, Gemini and ${agentName()} about yourself grows on this tree. The real copy is the "世界树" folder in your Obsidian vault.`,
        )}</T>
        {state}
        {data ? (
          <>
            {pending.length ? <PendingCard leaves={pending} /> : null}
            <View style={{ marginTop: space.lg, marginBottom: view === 'branch' ? space.md : 0 }}>
              <Segmented value={view} onChange={setView} options={[{ value: 'branch', label: L('按枝', 'By branch') }, { value: 'source', label: L('按来源', 'By source') }]} />
            </View>
            {view === 'branch' ? <ByBranch data={data} /> : <BySource data={data} />}
            {view === 'source' && !data.leaves.length ? <Note>{L('还没有叶子。在哪个平台说起你自己的事，它记下来就会长在这里。', "No leaves yet. When an app saves something about you, it grows here.")}</Note> : null}
            <T v="caption" color={t.ink3} style={styles.foot}>{L(
              `一共 ${data.counts.total} 片叶子。在 Obsidian 里直接改、加笔记也行，这里跟着变。`,
              `${data.counts.total} leaves in all. You can also edit or add notes in Obsidian; this page follows.`,
            )}{data.issues ? L(` 有 ${data.issues} 篇笔记格式不对，先跳过了。`, ` ${data.issues} note(s) have a formatting problem and were skipped.`) : ''}</T>
          </>
        ) : null}
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  small: { borderRadius: radius.pill, paddingHorizontal: 14, paddingVertical: 6, minWidth: 56, alignItems: 'center' },
  leaf: { paddingVertical: 10, gap: 5 },
  leafText: { fontSize: 15, lineHeight: 21 },
  leafMeta: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: space.sm },
  chip: { flexDirection: 'row', alignItems: 'center', gap: 4, borderWidth: 1, borderRadius: radius.pill, paddingHorizontal: 12, paddingVertical: 7 },
  date: { fontWeight: '400', fontVariant: ['tabular-nums'] },
  quote: { borderRadius: radius.md, paddingVertical: 12, paddingHorizontal: space.md },
  pending: { paddingVertical: 12, gap: 8 },
  pendingFoot: { flexDirection: 'row', alignItems: 'center', gap: space.sm },
  pendingMeta: { flex: 1, flexDirection: 'row', alignItems: 'center', gap: 6 },
  branch: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 12, minHeight: 46 },
  bigName: { fontWeight: '600' },
  count: { fontVariant: ['tabular-nums'] },
  about: { fontWeight: '400', lineHeight: 17, marginBottom: 2 },
  foot: { marginTop: space.lg, paddingHorizontal: space.xs, lineHeight: 18, fontWeight: '400' },
});
