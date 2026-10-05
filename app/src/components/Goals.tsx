// 目标页的零件（server/goals.py）：目标卡、点开的弹层（编辑 / 标记完成 / 不做了 / 去看板）、加和改的表单、
// Agent 刚改过的那条（能撤销）、完成了的和不做了的（折起来）、体重的读数折线（训记为主，Apple 健康是空心圈）。
import React, { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Switch, TextInput, View } from 'react-native';
import type { GoalFields } from '../api/goals';
import type { Goal, GoalCategory, GoalChange, GoalTrend } from '../data/types';
import { L } from '../i18n';
import { openTarget } from '../navigation';
import { useStore } from '../store';
import { agentTint, radius, space, type, useTheme } from '../theme';
import { dayNum, Ring, TrendLine } from './charts';
import { GroupBadge } from './GroupIcon';
import { ArchiveRestore, Ban, Check, LayoutDashboard, Pencil, Plus } from './icons';
import { Caption } from './LiveBoards';
import { addDays, isoDate } from './Schedule';
import { SourcePill } from './SourceBadge';
import { Btn, Card, Disclosure, Pill, Segmented, T, showError } from './ui';

// —— 文字 ————————————————————————————————————————————————————————————

export const CATEGORIES: GoalCategory[] = ['健康', '学业', '职业', '财务'];
// 分类是服务器数据库里的值，拿来比较，不翻译；只换显示的文字。
export const categoryLabel = (c: GoalCategory) =>
  ({ 健康: L('健康', 'Health'), 学业: L('学业', 'Study'), 职业: L('职业', 'Career'), 财务: L('财务', 'Finance') })[c] ?? c;
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const thisYear = () => new Date().getFullYear();
/** 80.4 / 5,000（最多一位小数）。 */
export const fmt = (v: number) => v.toLocaleString('en-GB', { maximumFractionDigits: 1 });
const withUnit = (v: number, unit?: string | null) => {
  const u = (unit ?? '').trim();
  return !u || u === '%' || u === '‰' || u === '°' ? `${fmt(v)}${u}` : `${fmt(v)} ${u}`;
};
const signed = (v: number, unit?: string | null) => `${v > 0 ? '+' : v < 0 ? '−' : '±'}${withUnit(Math.abs(v), unit)}`;
/** 9/23；不是今年的带上年份：2022/7/8。 */
export const shortDate = (iso: string) => {
  const y = Number(iso.slice(0, 4)), m = Number(iso.slice(5, 7)), d = Number(iso.slice(8, 10));
  return y === thisYear() ? `${m}/${d}` : `${y}/${m}/${d}`;
};

/** 72–75 kg / 18% 以下 / 5,000 GBP 以上；没数字是空的。 */
export function targetText(g: Pick<Goal, 'targetLow' | 'targetHigh' | 'unit'>): string {
  const { targetLow: lo, targetHigh: hi, unit } = g;
  if (lo != null && hi != null) return lo === hi ? withUnit(lo, unit) : `${fmt(lo)}–${withUnit(hi, unit)}`;
  if (hi != null) return L(`${withUnit(hi, unit)} 以下`, `${withUnit(hi, unit)} or less`);
  if (lo != null) return L(`${withUnit(lo, unit)} 以上`, `${withUnit(lo, unit)} or more`);
  return '';
}

/** 截止怎么说：12月31日 / 2027年3月 / 原样（「2027 秋」）。 */
export function dueText(due: string): string {
  const m = /^(\d{4})-(\d{2})(?:-(\d{2}))?$/.exec(due.trim());
  if (!m) return due;
  const y = Number(m[1]), mo = Number(m[2]);
  if (!m[3]) return L(`${y}年${mo}月`, `${MON[mo - 1]} ${y}`);
  const d = Number(m[3]);
  return y === thisYear() ? L(`${mo}月${d}日`, `${MON[mo - 1]} ${d}`) : L(`${y}年${mo}月${d}日`, `${MON[mo - 1]} ${d}, ${y}`);
}

export const isNumeric = (g: Goal) => g.targetLow != null || g.targetHigh != null || !!g.metric;

/** 进度：服务器算好的（往下、往上都行）；老服务器没给就按往下走的算（体脂）。 */
function progressOf(g: Goal): number {
  if (g.progress != null) return g.progress;
  if (g.current == null || g.targetHigh == null) return 0;
  if (g.current <= g.targetHigh) return 1;
  if (g.start == null || g.start <= g.current) return 0;
  return Math.max(0, Math.min(1, (g.start - g.current) / (g.start - g.targetHigh)));
}

/** 离目标还差多少：在区间里 / 比目标高 5.4 kg / 比目标低 2 kg。 */
function gapText(g: Goal): string {
  if (g.current == null || g.state == null) return '';
  if (g.state === 'in') return L('已达标', 'On target');
  const d = g.state === 'above' ? g.current - (g.targetHigh ?? g.current) : (g.targetLow ?? g.current) - g.current;
  return g.state === 'above' ? L(`高于目标 ${withUnit(d, g.unit)}`, `${withUnit(d, g.unit)} above target`) : L(`低于目标 ${withUnit(d, g.unit)}`, `${withUnit(d, g.unit)} below target`);
}

// —— 目标卡 ——————————————————————————————————————————————————————————

/** 卡片下面一行小字：挂在哪个 Agent（带它的颜色）、读数的来源和日子、截止。 */
function Meta({ g }: { g: Goal }) {
  const t = useTheme();
  const bits = [
    g.currentSource && g.currentDate ? `${g.currentSource} ${shortDate(g.currentDate)}` : null,
    g.due ? L(`截止 ${dueText(g.due)}`, `Due ${dueText(g.due)}`) : null,
  ].filter(Boolean) as string[];
  if (!g.groupId && !bits.length) return null;
  return (
    <View style={styles.meta}>
      {g.groupId ? <SourcePill source={g.groupId} /> : null}
      {bits.length ? <T v="caption" color={t.ink3} numberOfLines={1} style={{ flexShrink: 1, fontWeight: '400' }}>{bits.join(' · ')}</T> : null}
    </View>
  );
}

function NumericHead({ g }: { g: Goal }) {
  const t = useTheme();
  const target = targetText(g);
  const gap = gapText(g);
  const now = g.current != null ? withUnit(g.current, g.unit) : null;
  const line = now
    ? [L(`当前 ${now}`, `Current ${now}`), target ? L(`目标 ${target}`, `Target ${target}`) : null].filter(Boolean).join(' · ')
    : target ? L(`目标 ${target} · 暂无读数`, `Target ${target} · no reading yet`) : L('暂无读数', 'No reading yet');
  const ring = g.stale || g.current == null ? t.ink3 : g.state === 'in' ? t.good : undefined;
  return (
    <View style={{ gap: space.sm }}>
      <View style={styles.row}>
        <Ring size={60} stroke={6} value={g.stale ? 0 : progressOf(g)} target={1} color={ring}>
          <T v="headline" style={[styles.tnum, { fontSize: 15 }]}>{g.current != null ? fmt(g.current) : '—'}</T>
        </Ring>
        <View style={{ flex: 1, minWidth: 0, gap: 4 }}>
          <T v="headline">{g.title}</T>
          <T v="callout" color={t.ink2} style={styles.tnum}>{line}</T>
          {gap && !g.stale ? <T v="caption" color={g.state === 'in' ? t.good : t.ink2} style={{ fontSize: 13, fontWeight: '600' }}>{gap}</T> : null}
        </View>
      </View>
      <Meta g={g} />
      {g.stale ? (
        <T v="caption" color={t.warn} style={styles.note}>{L(
          `最近一次读数为 ${g.currentDate}，已过期。重新测量并记录到${g.currentSource ?? '你的记录'}或 Apple 健康后，此处将自动更新。`,
          `The latest reading is from ${g.currentDate} and is out of date. Log a new measurement in ${g.currentSource ?? 'your app'} or Apple Health and this will update automatically.`,
        )}</T>
      ) : null}
    </View>
  );
}

/** 一个目标的卡片：数字目标带进度环；体重目标下面接着读数折线和目标区间。点开 = 详情和操作。 */
export function GoalCard({ g, trend, onPress }: { g: Goal; trend?: GoalTrend | null; onPress: () => void }) {
  const t = useTheme();
  return (
    <Pressable onPress={onPress} accessibilityRole="button" accessibilityHint={L('打开详情：编辑、标记完成、放弃', 'Opens details: edit, mark done, drop')}
      style={({ pressed }) => ({ opacity: pressed ? 0.8 : 1 })}>
      <Card style={{ gap: space.md }}>
        {isNumeric(g) ? <NumericHead g={g} /> : (
          <View style={{ gap: 6 }}>
            <T v="headline">{g.title}</T>
            <Meta g={g} />
          </View>
        )}
        {trend ? <View style={[styles.split, { borderTopColor: t.line }]}><WeightBody trend={trend} band={g} /></View> : null}
      </Card>
    </Pressable>
  );
}

// —— 体重的读数 ——————————————————————————————————————————————————————

function Dot({ hollow }: { hollow?: boolean }) {
  const t = useTheme();
  return <View style={[styles.dot, hollow ? { borderWidth: 1.2, borderColor: t.ink3 } : { backgroundColor: t.chartA }]} />;
}

/** 折线 + 下面一行（从哪天起、图例、今天）+ 近 7 天平均和变化。band：目标区间（有体重目标时）。 */
function WeightBody({ trend, band }: { trend: GoalTrend; band?: { targetLow: number | null; targetHigh: number | null } | null }) {
  const t = useTheme();
  const body = trend.series.filter((p) => p.source === 'body');
  const health = trend.series.filter((p) => p.source === 'health');
  const main = body.length ? body : health;   // 没接训练软件：Apple 健康就画成线
  const others = body.length ? health : [];
  const s = trend.summary;
  if (!main.length || !s) return <T v="callout" color={t.ink3}>{L('近六个月暂无读数。', 'No readings in the last six months.')}</T>;
  const name = (k: 'body' | 'health') => trend.sources.find((x) => x.key === k)?.name ?? (k === 'body' ? L('训记', 'Xunji') : L('Apple 健康', 'Apple Health'));
  // 横轴从第一次读数前几天画到今天，至少两周：才记了十来天就别拿半年的宽度把线挤到右边
  const first = trend.series.reduce((a, p) => (p.date < a ? p.date : a), trend.to);
  let from = addDays(first, -3);
  if (from < trend.from) from = trend.from;
  if (dayNum(trend.to) - dayNum(from) < 14) from = addDays(trend.to, -14);
  const stats = [
    s.avg7 ? L(`近 7 天平均 ${withUnit(s.avg7.value, trend.unit)}`, `7-day avg ${withUnit(s.avg7.value, trend.unit)}`) : null,
    s.change30 ? L(`较 ${shortDate(s.change30.since)} ${signed(s.change30.value, trend.unit)}`, `${signed(s.change30.value, trend.unit)} since ${shortDate(s.change30.since)}`) : null,
  ].filter(Boolean) as string[];
  const label = L(`${trend.label}：${shortDate(from)} 至今 ${main.length} 次读数，最新 ${withUnit(s.latest.value, trend.unit)}`,
    `${trend.label}: ${main.length} readings since ${shortDate(from)}, latest ${withUnit(s.latest.value, trend.unit)}`);
  const hasBand = band && (band.targetLow != null || band.targetHigh != null);
  return (
    <View style={{ gap: 6 }}>
      <TrendLine points={main} secondary={others} from={from} to={trend.to} label={label}
        band={hasBand ? { low: band.targetLow, high: band.targetHigh } : null} />
      <View style={styles.axis}>
        <T v="caption" color={t.ink3} style={styles.axisText}>{shortDate(from)}</T>
        <View style={styles.legend}>
          {others.length ? (
            <>
              <Dot /><T v="caption" color={t.ink3} style={styles.axisText}>{name(s.source)}</T>
              <Dot hollow /><T v="caption" color={t.ink3} style={styles.axisText}>{name('health')}</T>
            </>
          ) : hasBand ? (
            <><View style={[styles.swatch, { backgroundColor: t.goodSoft }]} /><T v="caption" color={t.ink3} style={styles.axisText}>{L('目标', 'Target')}</T></>
          ) : null}
        </View>
        <T v="caption" color={t.ink3} style={styles.axisText}>{L('今天', 'Today')}</T>
      </View>
      {stats.length ? <T v="callout" color={t.ink2} style={styles.tnum}>{stats.join(' · ')}</T> : null}
      {s.check ? (
        <T v="caption" color={t.warn} style={styles.note}>{L(`${s.check.sourceName}同日读数为 ${withUnit(s.check.value, trend.unit)}，数据不一致`,
          `${s.check.sourceName} reports ${withUnit(s.check.value, trend.unit)} for the same day`)}</T>
      ) : null}
    </View>
  );
}

/** 没有体重目标时，健康那一节里单独一张体重卡：最新一次在上面，下面是折线。 */
export function WeightCard({ trend }: { trend: GoalTrend }) {
  const s = trend.summary;
  if (!s) return null;
  return (
    <Card style={{ gap: space.md }}>
      <View style={[styles.row, { alignItems: 'baseline' }]}>
        <T v="headline" style={{ flex: 1 }}>{trend.label || L('体重', 'Weight')}</T>
        <Caption>{`${s.sourceName} · ${shortDate(s.latest.date)}`}</Caption>
      </View>
      <T v="title" style={[styles.tnum, { fontSize: 26, fontWeight: '800', marginTop: -6 }]}>{withUnit(s.latest.value, trend.unit)}</T>
      <WeightBody trend={trend} />
    </Card>
  );
}

// —— 点开：详情和操作 ——————————————————————————————————————————————————

function Field({ label, children, last, top }: { label: string; children: React.ReactNode; last?: boolean; top?: boolean }) {
  const t = useTheme();
  return (
    <View style={[styles.field, top && { alignItems: 'flex-start' }, !last && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }]}>
      <T v="body" color={t.ink2} numberOfLines={1} style={[{ width: 56, flexShrink: 0 }, top && { paddingTop: 12 }]}>{label}</T>
      <View style={{ flex: 1, minWidth: 0 }}>{children}</View>
    </View>
  );
}

/** 点开一个目标：分类、谁负责、数字（现在 / 目标 / 起点）、截止、说明，下面是操作。 */
export function GoalSheet({ g, close, onEdit }: { g: Goal; close: () => void; onEdit: () => void }) {
  const t = useTheme();
  const { groups, goalsEditable, setGoalStatus } = useStore();
  const [busy, setBusy] = useState(false);
  const group = groups.find((x) => x.id === g.groupId);
  const run = (fn: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    fn().then(close).catch((e) => showError(L('修改失败', "Couldn't update"), e)).finally(() => setBusy(false));
  };
  const target = targetText(g);
  const rows: { label: string; value: string; sub?: string; color?: string }[] = [];
  if (isNumeric(g)) {
    if (g.current != null) rows.push({ label: L('当前', 'Current'), value: withUnit(g.current, g.unit), sub: [g.currentSource, g.currentDate ? shortDate(g.currentDate) : null].filter(Boolean).join(' · '), color: g.stale ? t.warn : undefined });
    if (target) rows.push({ label: L('目标', 'Target'), value: target, sub: g.stale ? '' : gapText(g) });
    if (g.start != null && g.startDate && g.startDate !== g.currentDate) rows.push({ label: L('起点', 'Start'), value: withUnit(g.start, g.unit), sub: shortDate(g.startDate) });
  }
  if (g.due) rows.push({ label: L('截止', 'Due'), value: dueText(g.due), sub: g.daysLeft != null && g.daysLeft >= 0 ? L(`剩余 ${g.daysLeft} 天`, `${g.daysLeft} days left`) : '' });
  const active = g.status === 'active';
  return (
    <View style={{ gap: space.md }}>
      <View style={styles.meta}>
        <Pill label={categoryLabel(g.category)} />
        {group ? <SourcePill source={group.id} /> : null}
        {!active ? <Pill label={g.status === 'done' ? L('已完成', 'Done') : L('已放弃', 'Dropped')} tone={g.status === 'done' ? 'good' : 'neutral'} /> : null}
      </View>
      {rows.length ? (
        <View style={[styles.box, { backgroundColor: t.surface }]}>
          {rows.map((r, i) => (
            <Field key={r.label} label={r.label} last={i === rows.length - 1}>
              <View style={{ flexDirection: 'row', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
                <T v="body" color={r.color} style={[styles.tnum, { fontWeight: '600' }]}>{r.value}</T>
                {r.sub ? <T v="caption" color={t.ink3} style={{ fontSize: 13, fontWeight: '400' }}>{r.sub}</T> : null}
              </View>
            </Field>
          ))}
        </View>
      ) : null}
      {g.detail ? <T v="body" color={t.ink2}>{g.detail}</T> : null}
      {g.metric ? (
        <T v="caption" color={t.ink3} style={styles.note}>{L('当前数值自动读取，无需手动填写。', 'The current value is read automatically; no manual entry needed.')}</T>
      ) : null}
      {goalsEditable ? (
        <View style={{ gap: space.sm }}>
          {active ? (
            <>
              <View style={styles.btns}>
                <Btn label={L('编辑', 'Edit')} kind="quiet" icon={<Pencil size={16} color={t.ink} />} onPress={onEdit} flex />
                <Btn label={L('标记完成', 'Mark done')} icon={<Check size={17} color={t.onGold} />} onPress={() => run(() => setGoalStatus(g.id, 'done'))} flex />
              </View>
              <View style={styles.btns}>
                <Btn label={L('放弃', 'Drop')} kind="quiet" icon={<Ban size={16} color={t.ink} />} onPress={() => run(() => setGoalStatus(g.id, 'dropped'))} flex />
                {group ? <Btn label={L('打开看板', 'Open board')} kind="quiet" icon={<LayoutDashboard size={16} color={t.ink} />}
                  onPress={() => { close(); openTarget({ type: 'board', agent: group.id }); }} flex /> : null}
              </View>
            </>
          ) : (
            <View style={styles.btns}>
              <Btn label={L('编辑', 'Edit')} kind="quiet" icon={<Pencil size={16} color={t.ink} />} onPress={onEdit} flex />
              <Btn label={L('设为进行中', 'Mark active')} icon={<ArchiveRestore size={16} color={t.onGold} />} onPress={() => run(() => setGoalStatus(g.id, 'active'))} flex />
            </View>
          )}
        </View>
      ) : group ? (
        <Btn label={L('打开看板', 'Open board')} kind="quiet" icon={<LayoutDashboard size={16} color={t.ink} />} onPress={() => { close(); openTarget({ type: 'board', agent: group.id }); }} />
      ) : null}
    </View>
  );
}

// —— 加 / 改 ——————————————————————————————————————————————————————————

/** 表单里的一个小选项（截止的快捷、挂哪个 Agent）。 */
function Choice({ label, on, onPress, icon, tint }: { label: string; on: boolean; onPress: () => void; icon?: React.ReactNode; tint?: { soft: string; fg: string } }) {
  const t = useTheme();
  const [bg, fg] = on ? [tint?.soft ?? t.cyanSoft, tint?.fg ?? t.cyan] : [t.surface, t.ink2];
  return (
    <Pressable onPress={onPress} accessibilityRole="radio" accessibilityState={{ selected: on }} accessibilityLabel={label} hitSlop={4}
      style={({ pressed }) => [styles.choice, { backgroundColor: bg, borderColor: on ? fg : t.line, opacity: pressed ? 0.7 : 1 }]}>
      {icon}
      <T v="callout" color={on ? fg : t.ink} style={{ fontWeight: on ? '600' : '400' }} numberOfLines={1}>{label}</T>
    </Pressable>
  );
}

/** 表单里的数：空 = null，写错了 = NaN（逗号当小数点）。 */
const parseNum = (s: string): number | null => {
  const x = s.trim().replace(/,/g, '.').replace(/\s/g, '');
  if (!x) return null;
  const n = Number(x);
  return Number.isFinite(n) ? n : NaN;
};

/** 加一个目标（g 为空）或改一个。只把改了的字段发给服务器。 */
export function GoalEditor({ g, close }: { g?: Goal; close: () => void }) {
  const t = useTheme();
  const { groups, goalMetrics, saveGoal } = useStore();
  const [title, setTitle] = useState(g?.title ?? '');
  const [category, setCategory] = useState<GoalCategory>(g?.category ?? '健康');
  const [numeric, setNumeric] = useState(g ? isNumeric(g) : false);
  const [metric, setMetric] = useState<string>(g?.metric ?? '');
  const [low, setLow] = useState(g?.targetLow != null ? String(g.targetLow) : '');
  const [high, setHigh] = useState(g?.targetHigh != null ? String(g.targetHigh) : '');
  const [unit, setUnit] = useState(g?.unit ?? '');
  const [due, setDue] = useState(g?.due ?? '');
  const [detail, setDetail] = useState(g?.detail ?? '');
  const [groupId, setGroupId] = useState<string | null>(g?.groupId ?? null);
  const [busy, setBusy] = useState(false);
  const metricUnit = goalMetrics.find((m) => m.key === metric)?.unit ?? '';
  const shownUnit = metric ? metricUnit : unit;
  // 截止的两个快捷：打开表单时算一次
  const [{ endOfYear, in3m }] = useState(() => {
    const d = new Date();
    d.setMonth(d.getMonth() + 3);
    return { endOfYear: `${thisYear()}-12-31`, in3m: isoDate(d) };
  });

  const save = async () => {
    const tt = title.trim();
    if (!tt) { showError(L('请填写目标', 'Enter a goal'), ''); return; }
    const lo = numeric ? parseNum(low) : null;
    const hi = numeric ? parseNum(high) : null;
    if (Number.isNaN(lo) || Number.isNaN(hi)) { showError(L('目标须为数字，例如 72', 'The target must be a number, e.g. 72'), ''); return; }
    if (lo != null && hi != null && lo > hi) { showError(L('左侧数值须小于右侧', 'The first number must be the smaller one'), ''); return; }
    const want: Required<Pick<GoalFields, 'title' | 'category' | 'detail' | 'due' | 'unit' | 'targetLow' | 'targetHigh' | 'metric' | 'groupId'>> = {
      title: tt, category, detail: detail.trim() || null, due: due.trim() || null,
      unit: numeric ? (metric ? metricUnit || null : unit.trim() || null) : null,
      targetLow: lo, targetHigh: hi, metric: numeric && metric ? metric : null, groupId,
    };
    const fields: GoalFields = {};
    if (g) {
      const before: typeof want = { title: g.title, category: g.category, detail: g.detail || null, due: g.due || null, unit: g.unit, targetLow: g.targetLow,
        targetHigh: g.targetHigh, metric: g.metric, groupId: g.groupId };
      for (const k of Object.keys(want) as (keyof typeof want)[]) if (want[k] !== before[k]) (fields as Record<string, unknown>)[k] = want[k];
      if (!Object.keys(fields).length) { close(); return; }
    } else {
      for (const k of Object.keys(want) as (keyof typeof want)[]) if (want[k] != null) (fields as Record<string, unknown>)[k] = want[k];
    }
    setBusy(true);
    try { await saveGoal(g?.id ?? null, fields); close(); } catch (e) { showError(L('保存失败', "Couldn't save"), e); } finally { setBusy(false); }
  };

  return (
    <View style={{ gap: space.md }}>
      <TextInput value={title} onChangeText={setTitle} placeholder={L('要达成的目标', 'What do you want to achieve?')} placeholderTextColor={t.ink3}
        maxLength={80} accessibilityLabel={L('目标', 'Goal')} style={[type.title, { color: t.ink, paddingVertical: 4 }]} />
      <Segmented value={category} onChange={setCategory} options={CATEGORIES.map((c) => ({ value: c, label: categoryLabel(c) }))} />

      <View style={[styles.box, { backgroundColor: t.surface }]}>
        <View style={styles.field}>
          <View style={{ flex: 1, gap: 2 }}>
            <T v="body">{L('按数值追踪', 'Track a number')}</T>
            <T v="caption" color={t.ink3} style={{ fontSize: 13, fontWeight: '400' }}>{L('例如体重 72–75 kg、存款 5,000', 'e.g. weight 72–75 kg, savings 5,000')}</T>
          </View>
          <Switch value={numeric} onValueChange={setNumeric} accessibilityLabel={L('按数值追踪', 'Track a number')} trackColor={{ true: t.cyan, false: t.track }} thumbColor="#FFFFFF" />
        </View>
        {numeric ? (
          <>
            <View style={[styles.sub, { borderTopColor: t.line }]}>
              <T v="caption" color={t.ink2} style={{ fontSize: 13 }}>{L('当前数值来源', 'Source of the current value')}</T>
              <Segmented value={metric || 'none'} onChange={(v) => setMetric(v === 'none' ? '' : v)}
                options={[...goalMetrics.map((m) => ({ value: m.key, label: m.label })), { value: 'none', label: L('不自动读取', 'Not tracked') }]} />
            </View>
            <View style={[styles.field, { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }]}>
              <T v="body" color={t.ink2} style={{ width: 56, flexShrink: 0 }}>{L('目标', 'Target')}</T>
              <TextInput value={low} onChangeText={setLow} placeholder={L('从', 'From')} placeholderTextColor={t.ink3} keyboardType="decimal-pad" maxLength={9}
                accessibilityLabel={L('目标下限', 'Low end')} style={[type.body, styles.num, { color: t.ink, backgroundColor: t.bg }]} />
              <T v="body" color={t.ink3}>–</T>
              <TextInput value={high} onChangeText={setHigh} placeholder={L('到', 'To')} placeholderTextColor={t.ink3} keyboardType="decimal-pad" maxLength={9}
                accessibilityLabel={L('目标上限', 'High end')} style={[type.body, styles.num, { color: t.ink, backgroundColor: t.bg }]} />
              {metric ? <T v="body" color={t.ink2} style={{ minWidth: 28 }}>{shownUnit}</T> : (
                <TextInput value={unit} onChangeText={setUnit} placeholder={L('单位', 'Unit')} placeholderTextColor={t.ink3} maxLength={12}
                  accessibilityLabel={L('单位', 'Unit')} style={[type.body, styles.unit, { color: t.ink, backgroundColor: t.bg }]} />
              )}
            </View>
            <T v="caption" color={t.ink3} style={[styles.note, { paddingBottom: space.sm }]}>{L('可只填一侧：仅填右侧表示降至该值以下，仅填左侧表示达到该值以上。',
              'You can fill in one side only: right only means below that value, left only means above it.')}</T>
          </>
        ) : null}
      </View>

      <View style={[styles.box, { backgroundColor: t.surface }]}>
        <Field label={L('截止', 'Due')}>
          <TextInput value={due} onChangeText={setDue} placeholder={L('选填：2026-12-31 或「2027 秋」', 'Optional: 2026-12-31 or "fall 2027"')} placeholderTextColor={t.ink3}
            maxLength={24} accessibilityLabel={L('截止', 'Due')} style={[type.body, { color: t.ink, paddingVertical: 12 }]} />
        </Field>
        <View style={[styles.quick, { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }]}>
          <Choice label={L('年底', 'End of year')} on={due === endOfYear} onPress={() => setDue(endOfYear)} />
          <Choice label={L('三个月后', 'In 3 months')} on={due === in3m} onPress={() => setDue(in3m)} />
          {due ? <Choice label={L('不设', 'None')} on={false} onPress={() => setDue('')} /> : null}
        </View>
        <Field label={L('说明', 'Note')} last top>
          <TextInput value={detail} onChangeText={setDetail} placeholder={L('选填：目标缘由与完成标准', 'Optional: why, and what counts as done')} placeholderTextColor={t.ink3}
            multiline maxLength={500} accessibilityLabel={L('说明', 'Note')} style={[type.body, { color: t.ink, paddingVertical: 12, minHeight: 48, textAlignVertical: 'top' }]} />
        </Field>
      </View>

      {groups.length ? (
        <View style={{ gap: space.sm }}>
          <T v="caption" color={t.ink2} style={{ fontSize: 13, paddingHorizontal: space.xs }}>{L('负责跟进的 Agent', 'Assigned Agent')}</T>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: space.sm, paddingHorizontal: 2 }} keyboardShouldPersistTaps="handled">
            <Choice label={L('不指定', 'None')} on={groupId == null} onPress={() => setGroupId(null)} />
            {groups.map((x) => (
              <Choice key={x.id} label={x.name} on={groupId === x.id} onPress={() => setGroupId(x.id)} tint={agentTint(t, x.color)}
                icon={<GroupBadge icon={x.icon} color={x.color} size={20} />} />
            ))}
          </ScrollView>
        </View>
      ) : null}

      <View style={styles.btns}>
        <Btn label={L('取消', 'Cancel')} kind="quiet" onPress={close} />
        <Btn label={busy ? L('正在保存…', 'Saving…') : g ? L('保存', 'Save') : L('添加', 'Add')} icon={g ? undefined : <Plus size={18} color={t.onGold} />}
          onPress={() => { if (!busy) save(); }} flex />
      </View>
    </View>
  );
}

/** 页头右边的「+ 加一个目标」。 */
export function AddGoalButton({ onPress }: { onPress: () => void }) {
  const t = useTheme();
  return (
    <Pressable onPress={onPress} accessibilityRole="button" accessibilityLabel={L('添加目标', 'Add goal')} hitSlop={8}
      style={({ pressed }) => [styles.add, { opacity: pressed ? 0.6 : 1 }]}>
      <Plus size={17} color={t.gold} />
      <T v="callout" color={t.gold} style={{ fontWeight: '600' }}>{L('添加目标', 'Add goal')}</T>
    </Pressable>
  );
}

// —— Agent 刚改过的：能撤销 ————————————————————————————————————————————————

const clock = (iso: string) => {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  return isoDate(d) === isoDate(new Date()) ? hm : L(`昨天 ${hm}`, `Yesterday ${hm}`);
};

function StripBtn({ label, color, onPress, disabled }: { label: string; color: string; onPress: () => void; disabled?: boolean }) {
  return (
    <Pressable onPress={onPress} disabled={disabled} hitSlop={8} accessibilityRole="button"
      style={({ pressed }) => ({ paddingVertical: 4, paddingHorizontal: 2, opacity: disabled ? 0.5 : pressed ? 0.6 : 1 })}>
      <T v="callout" color={color} style={{ fontWeight: '700' }}>{label}</T>
    </Pressable>
  );
}

/** 目标页顶上：Agent（或主对话）24 小时内改了哪个目标，一条一行，能撤销；撤销了能恢复。「知道了」= 收起来。 */
export function GoalChangesStrip() {
  const t = useTheme();
  const { goalChanges, undoGoalChange, ackGoalChanges } = useStore();
  const [busy, setBusy] = useState<number | null>(null);
  if (!goalChanges.length) return null;
  const toggle = (c: GoalChange) => {
    if (busy != null) return;
    setBusy(c.logId);
    undoGoalChange(c.logId, c.status === 'undone').catch((e) => showError(L('撤销失败', "Couldn't undo"), e)).finally(() => setBusy(null));
  };
  return (
    <View style={[styles.strip, { backgroundColor: t.goldSoft }]}>
      {goalChanges.map((c, i) => (
        <View key={c.logId} style={[styles.stripRow, i > 0 && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }]}>
          <View style={{ flex: 1, minWidth: 0, gap: 4 }}>
            <View style={styles.meta}>
              <SourcePill source={c.actor} label={c.actorName || undefined} />
              <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{[clock(c.at), c.status === 'undone' ? L('已撤销', 'Undone') : ''].filter(Boolean).join(' · ')}</T>
            </View>
            <T v="callout" color={c.status === 'undone' ? t.ink3 : t.ink} style={c.status === 'undone' ? { textDecorationLine: 'line-through' } : undefined}>{c.summary}</T>
          </View>
          <StripBtn label={c.status === 'undone' ? L('恢复', 'Redo') : L('撤销', 'Undo')} color={t.gold} disabled={busy != null} onPress={() => toggle(c)} />
        </View>
      ))}
      <View style={{ alignItems: 'flex-end' }}>
        <StripBtn label={L('关闭', 'Dismiss')} color={t.ink2} disabled={busy != null} onPress={() => { ackGoalChanges(goalChanges.map((c) => c.logId)).catch(() => {}); }} />
      </View>
    </View>
  );
}

// —— 完成了的、不做了的：折起来 ————————————————————————————————————————————————

export function ClosedGoals({ onOpen }: { onOpen: (g: Goal) => void }) {
  const t = useTheme();
  const { goalsClosed } = useStore();
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const groups = (['done', 'dropped'] as const).map((status) => ({ status, rows: goalsClosed.filter((g) => g.status === status) })).filter((x) => x.rows.length);
  if (!groups.length) return null;
  return (
    <Card style={{ paddingVertical: space.xs, marginTop: space.xl }}>
      {groups.map(({ status, rows }, gi) => {
        const on = !!open[status];
        return (
          <View key={status} style={gi > 0 ? { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line } : undefined}>
            <Pressable onPress={() => setOpen((m) => ({ ...m, [status]: !m[status] }))} accessibilityRole="button" accessibilityState={{ expanded: on }}
              style={({ pressed }) => [styles.fold, { opacity: pressed ? 0.6 : 1 }]}>
              <T v="headline" style={{ fontSize: 15 }}>{status === 'done' ? L('已完成', 'Done') : L('已放弃', 'Dropped')}</T>
              <T v="callout" color={t.ink3} style={{ flex: 1 }}>{rows.length}</T>
              <Disclosure open={on} />
            </Pressable>
            {on ? rows.map((g) => (
              <Pressable key={g.id} onPress={() => onOpen(g)} accessibilityRole="button" style={({ pressed }) => [styles.closedRow, { opacity: pressed ? 0.6 : 1 }]}>
                <T v="body" color={t.ink2} numberOfLines={1} style={{ flex: 1 }}>{g.title}</T>
                {g.closedAt ? <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{shortDate(g.closedAt.slice(0, 10))}</T> : null}
              </Pressable>
            )) : null}
          </View>
        );
      })}
    </Card>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: space.lg },
  meta: { flexDirection: 'row', alignItems: 'center', gap: 6, flexWrap: 'wrap' },
  tnum: { fontVariant: ['tabular-nums'] },
  note: { fontSize: 13, lineHeight: 18, fontWeight: '400' },
  split: { borderTopWidth: StyleSheet.hairlineWidth, paddingTop: space.md },
  axis: { flexDirection: 'row', alignItems: 'center', gap: space.sm },
  axisText: { fontSize: 11, fontWeight: '500' },
  legend: { flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 5 },
  dot: { width: 8, height: 8, borderRadius: 4 },
  swatch: { width: 14, height: 8, borderRadius: 3 },
  box: { borderRadius: radius.md, paddingHorizontal: 14 },
  field: { flexDirection: 'row', alignItems: 'center', gap: space.sm, minHeight: 50 },
  sub: { borderTopWidth: StyleSheet.hairlineWidth, paddingVertical: space.md, gap: space.sm },
  num: { width: 72, borderRadius: radius.sm, paddingHorizontal: 10, paddingVertical: 8, textAlign: 'center', fontVariant: ['tabular-nums'] },
  unit: { flex: 1, minWidth: 48, borderRadius: radius.sm, paddingHorizontal: 10, paddingVertical: 8 },
  quick: { flexDirection: 'row', gap: space.sm, paddingBottom: 12, paddingLeft: 64, flexWrap: 'wrap' },
  choice: { flexDirection: 'row', alignItems: 'center', gap: 6, height: 34, borderRadius: 17, borderWidth: 1, paddingHorizontal: 12 },
  btns: { flexDirection: 'row', gap: space.sm },
  add: { flexDirection: 'row', alignItems: 'center', gap: 3, marginBottom: 6 },
  strip: { borderRadius: radius.md + 2, paddingTop: 4, paddingBottom: 8, paddingHorizontal: 14, marginTop: space.md },
  stripRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 10 },
  fold: { flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingVertical: 13 },
  closedRow: { flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingVertical: 10, paddingLeft: space.xs },
});
