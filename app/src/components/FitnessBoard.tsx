// 健身看板，按你想知道的顺序排：现在该做什么 → 身体状态 → 这周 → 长期（90 天）。
// 数据来源写在每块小标题的右边（「Apple 健康 · 16:36」「训记 · 17:35」）；长列表一行一条，点开看细节。
import React, { useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import { agentName } from '../brand';
import type { LiveTrain, LiveTrend, LiveWeek } from '../api/live';
import type { FeedItem, TrainingPlan } from '../data/types';
import { L, lang } from '../i18n';
import { useStore } from '../store';
import { space, useTheme } from '../theme';
import { DayBars, Ring, weekdayName } from './charts';
import { Moon, Sparkles } from './icons';
import { bandColor, bandTone, Caption, HealthCaption, hhmm, localDate, NoRecoveryCard, NoSourceCard, RecoveryDetails, ScoreStrip, useRecovery } from './LiveBoards';
import { Markdown } from './Markdown';
import { decisionTone, isTrainingPlan } from './Records';
import { Btn, Card, Disclosure, Pill, SectionLabel, T } from './ui';
import { SectionedBoard } from './blocks/Sections';

const newest = (a: FeedItem, b: FeedItem) => (b.createdAt ?? '').localeCompare(a.createdAt ?? '');
/** 卡片是几点出的（createdAt 是服务器的本地时间）。 */
const clock = (f: FeedItem) => f.createdAt?.slice(11, 16) || f.time;
/** 去掉 Markdown 记号。 */
const plain = (s: string) => s.replace(/\*\*|__|`/g, '').replace(/^#+\s*/, '').trim();

// —— 练后卡（kind = training_review）拆成紧凑版要的几样 ——
// 两种写法都认：data 里有 session / minutes / vs_last / recovery_24h / tomorrow；或者只有正文（Markdown，
// 分「这次怎么样」「接下来 24 小时」「明天」三段，格式见 workspace skills/training/SKILL.md）。

interface ReviewData { session?: unknown; minutes?: unknown; vs_last?: unknown; recovery_24h?: unknown; tomorrow?: unknown }
type Section = { head: string; lines: string[] };

/** 正文按粗体小标题分段，每段里的要点一行一条。 */
function sections(body: string): Section[] {
  const out: Section[] = [{ head: '', lines: [] }];
  for (const raw of body.split('\n')) {
    const line = raw.trim();
    if (!line) continue;
    const bullet = /^(?:[-*•]|\d+[.、)])\s+(.*)$/.exec(line);
    const head = bullet ? null : /^(?:#{1,6}\s*)?\*\*(.+?)\*\*\s*[：:]?\s*(.*)$/.exec(line) ?? /^#{1,6}\s+(.+)$/.exec(line);
    if (head) {
      const rest = (head[2] ?? '').trim();
      // 「**这次怎么样**（对比 9/17 Leg B）」：括号里的是小标题的补充，不是一条要点
      out.push({ head: plain(head[1]).replace(/^\d+[.、)]\s*/, ''), lines: rest && !/^[（(]/.test(rest) ? [plain(rest)] : [] });
      continue;
    }
    out[out.length - 1].lines.push(plain(bullet ? bullet[1] : line));
  }
  return out.filter((s) => s.head || s.lines.length);
}

const strings = (v: unknown): string[] => (Array.isArray(v) ? v.map((x) => String(x)).filter(Boolean) : []);
const PROGRESS = /进步|加重|提高|多了|\+\s?\d|PR\b|improv|progress|heavier/i;

function parseReview(f: FeedItem) {
  const d = (f.data && typeof f.data === 'object' ? f.data : {}) as ReviewData;
  const secs = sections(f.body || '');
  const find = (re: RegExp) => secs.find((s) => re.test(s.head));
  const how = find(/怎么样|这次|how|session/i) ?? (secs[0] && !secs[0].head ? secs[0] : undefined);
  const next = find(/接下来|24|next|recover/i);
  const tom = find(/明天|tomorrow/i);
  // 标题：「练后：Leg B，踮脚进步，其余持平」「练后（9/24）：Pull B，…」「Push B 练完：…」「Post-workout: Push B, …」
  let session = typeof d.session === 'string' ? d.session : '';
  let headline = f.title;
  const m = /^(?:练后|练完了?)\s*(?:（[^）]*）|\([^)]*\))?\s*[：:]\s*(.+?)[，,]\s*(.+)$/.exec(f.title)
    ?? /^post[- ]?workout\s*(?:\([^)]*\))?\s*[：:]\s*(.+?)[，,]\s*(.+)$/i.exec(f.title)
    ?? /^(.+?)\s*(?:练完|done)\s*[：:]\s*()(.+)$/i.exec(f.title);
  if (m) {
    session = session || m[1].trim();
    headline = (m[3] ?? m[2]).trim();
  }
  const vs = strings(d.vs_last);
  const howLines = how?.lines ?? [];
  const conclusion = howLines.find((x) => /^结论[：:]/.test(x))?.replace(/^结论[：:]\s*/, '');
  const oneLine = vs.find((x) => PROGRESS.test(x)) ?? howLines.find((x) => PROGRESS.test(x)) ?? conclusion ?? vs[0]
    ?? howLines.find((x) => !/^(?:时长|\d+\s*分钟)/.test(x)) ?? howLines[0] ?? '';
  const nextLines = (strings(d.recovery_24h).length ? strings(d.recovery_24h) : next?.lines ?? []).slice(0, 3);
  // 明天那一行只要第一句（「练 Push A，后天休息。」后面的细节在全文里）
  const tomorrow = ((typeof d.tomorrow === 'string' ? d.tomorrow : tom?.lines[0] ?? '').replace(/^(?:明天|tomorrow)\s*[：:]?\s*/i, '').split(/[。；;]|\.\s/)[0] ?? '').trim();
  return { session, headline, oneLine, nextLines, tomorrow, minutes: typeof d.minutes === 'number' ? d.minutes : null };
}

/** 今天训记里的那一次（名字对得上的；Leg / Legs 算一样），对不上就用今天最后一次。 */
function todayTrain(week: LiveWeek | null, session: string): LiveTrain | null {
  const trains = week?.days[week.today_index]?.trains ?? [];
  const norm = (s: string) => s.toLowerCase().replace(/legs/g, 'leg').replace(/\s+/g, '');
  return trains.find((x) => !!session && norm(x.title) === norm(session)) ?? trains[trains.length - 1] ?? null;
}

/** 练完之后：练后卡的紧凑版。状态、这次怎么样一句、接下来要做的、明天练什么；点最后一行看全文。 */
function ReviewCard({ f, week }: { f: FeedItem; week: LiveWeek | null }) {
  const t = useTheme();
  const [open, setOpen] = useState(false);
  const r = parseReview(f);
  const tr = todayTrain(week, r.session);
  const minutes = r.minutes ?? tr?.minutes ?? null;
  const meta = [r.session || tr?.title, minutes != null ? L(`${minutes} 分钟`, `${minutes} min`) : null, tr ? L(`${tr.sets_done} 组`, `${tr.sets_done} sets`) : null].filter(Boolean).join(' · ');
  return (
    <Card style={{ gap: 10 }}>
      <View style={styles.rowC}>
        <Pill label={L('已完成', 'Done')} tone="good" />
        {meta ? <T v="caption" color={t.ink3} numberOfLines={1} style={{ flex: 1 }}>{meta}</T> : null}
      </View>
      <T v="headline" style={styles.big}>{r.headline}</T>
      {r.oneLine ? <T v="callout" color={t.ink2} numberOfLines={3}>{r.oneLine}</T> : null}
      {r.nextLines.length ? (
        <View style={[styles.box, { backgroundColor: t.bg, borderColor: t.line }]}>
          <T v="caption" color={t.ink3} style={{ fontWeight: '600' }}>{L('下一步', 'Next')}</T>
          {r.nextLines.map((x, i) => <T key={`${i}-${x}`} v="callout" numberOfLines={2}>{x}</T>)}
        </View>
      ) : null}
      <Pressable onPress={() => setOpen((v) => !v)} accessibilityRole="button" accessibilityState={{ expanded: open }} accessibilityHint={L('展开练后卡全文', 'Shows the full post-workout card')}
        style={({ pressed }) => [styles.rowC, { paddingTop: 2, opacity: pressed ? 0.6 : 1 }]}>
        <T v="headline" numberOfLines={2} style={{ flex: 1, fontSize: 15 }}>{r.tomorrow ? `${L('明天', 'Tomorrow')} · ${r.tomorrow}` : L('查看全文', 'Read more')}</T>
        <Disclosure open={open} />
      </Pressable>
      {open ? <View style={[styles.more, { borderTopColor: t.line }]}><Markdown text={f.body} color={t.ink2} compact /></View> : null}
    </Card>
  );
}

/** 练之前：今天的训练建议，紧凑版。动作收成一行，注意事项收成一行，点开看全部。 */
function PlanCard({ plan, busy, onAsk }: { plan: TrainingPlan; busy: boolean; onAsk: () => void }) {
  const t = useTheme();
  const [moves, setMoves] = useState(false);
  const [warn, setWarn] = useState(false);
  const focus = plan.focus ?? [];
  const cautions = plan.cautions ?? [];
  const when = [plan.time, plan.duration_min ? L(`${plan.duration_min} 分钟`, `${plan.duration_min} min`) : null].filter(Boolean).join(' · ');
  return (
    <Card style={{ gap: 10 }}>
      <View style={[styles.rowC, { flexWrap: 'wrap' }]}>
        <Pill label={plan.decision} tone={decisionTone(plan.decision)} />
        {plan.session ? <T v="headline" style={{ fontSize: 17, fontWeight: '700' }}>{plan.session}</T> : null}
        {when ? <T v="caption" color={t.ink3}>{when}</T> : null}
      </View>
      {plan.intensity ? <T v="callout" color={t.ink2}>{plan.intensity}</T> : null}
      {plan.why ? <T v="caption" color={t.ink3} numberOfLines={2}>{plan.why}</T> : null}
      {focus.length ? (
        <View style={[styles.line, { borderTopColor: t.line }]}>
          <Pressable onPress={() => setMoves((v) => !v)} accessibilityRole="button" accessibilityState={{ expanded: moves }} style={({ pressed }) => [styles.rowC, { opacity: pressed ? 0.6 : 1 }]}>
            <T v="callout" style={{ flex: 1, fontWeight: '600' }}>{L(`动作 · ${focus.length} 个`, `${focus.length} exercise${focus.length === 1 ? '' : 's'}`)}</T>
            <Disclosure open={moves} />
          </Pressable>
          {moves ? (
            <View style={{ gap: 4, marginTop: 8 }}>
              {focus.map((x, i) => <View key={`${i}-${x}`} style={styles.li}><T v="callout" color={t.ink3}>{i + 1}.</T><T v="callout" style={{ flex: 1 }}>{x}</T></View>)}
            </View>
          ) : null}
        </View>
      ) : null}
      {cautions.length ? (
        <Pressable onPress={() => setWarn((v) => !v)} accessibilityRole="button" accessibilityState={{ expanded: warn }}
          style={({ pressed }) => [styles.line, { borderTopColor: t.line, opacity: pressed ? 0.6 : 1 }]}>
          <View style={[styles.rowC, { alignItems: 'flex-start' }]}>
            <View style={{ flex: 1, gap: 4 }}>
              {(warn ? cautions : cautions.slice(0, 1)).map((c, i) => (
                <View key={`${i}-${c}`} style={styles.li}>
                  <T v="callout" color={t.warn} style={{ fontWeight: '700' }}>!</T>
                  <T v="callout" color={t.ink2} numberOfLines={warn ? undefined : 1} style={{ flex: 1 }}>{c}</T>
                </View>
              ))}
            </View>
            {!warn && cautions.length > 1 ? <T v="caption" color={t.ink3} style={{ marginTop: 2 }}>+{cautions.length - 1}</T> : null}
            <View style={{ marginTop: 2 }}><Disclosure open={warn} /></View>
          </View>
        </Pressable>
      ) : null}
      <Btn label={busy ? L(`${agentName()} 正在生成…`, `${agentName()} is generating…`) : L('重新生成', 'Regenerate')} kind="quiet" onPress={onAsk} icon={<Sparkles size={14} color={t.ink} />} />
    </Card>
  );
}

/** 现在：练完了就是练后卡，没练就是今天的训练建议，都没有就请它出一份。 */
function NowSection({ groupId, onAsk }: { groupId: string; onAsk: () => void }) {
  const t = useTheme();
  const { feed, send, typing, live } = useStore();
  const today = localDate();
  // 今天最新的一张练后卡或训练建议，哪张新用哪张：练完了是练后卡；凌晨补出的昨天的练后卡，会被早上的新建议盖过
  const card = feed.filter((f) => f.groupId === groupId && (f.createdAt ?? '').slice(0, 10) === today && (f.kind === 'training_review' || isTrainingPlan(f))).sort(newest)[0];
  const busy = !!typing[groupId];
  const ask = () => { send(groupId, L('出今天的训练建议', "Plan today's workout")); onAsk(); };
  return (
    <View>
      <SectionLabel right={card ? <Caption>{clock(card)}</Caption> : undefined}>{L('当前', 'Now')}</SectionLabel>
      {card?.kind === 'training_review' ? <ReviewCard f={card} week={live?.week ?? null} />
        : card && isTrainingPlan(card) ? <PlanCard plan={card.data} busy={busy} onAsk={ask} />
          : (
            <Card style={{ gap: space.sm }}>
              <T v="callout" color={t.ink2}>{L(`今日尚无训练建议。${agentName()} 将根据昨晚睡眠、恢复分、PPL 轮次、今日课程和你近期的状态制定。`, `No workout plan for today yet. ${agentName()} plans it around last night's sleep, your recovery score, your place in the PPL split, today's classes and how you've been feeling.`)}</T>
              <Btn label={busy ? L(`${agentName()} 正在生成…`, `${agentName()} is generating…`) : L(`请 ${agentName()} 生成今日建议`, `Ask ${agentName()} for today's plan`)} kind="primary" onPress={ask} icon={<Sparkles size={14} color={t.onGold} />} />
            </Card>
          )}
    </View>
  );
}

/** 身体状态：恢复分的环、一句话、近 14 天的小条；点开看睡眠和心率的细节。 */
function BodySection() {
  const t = useTheme();
  const { last, rec, recDays } = useRecovery();
  const [open, setOpen] = useState(false);
  const scored = rec != null && rec.score != null;
  const summary = scored
    ? (rec.notes.length ? rec.notes.join(' · ') : L('各项指标接近基线', 'All metrics are near baseline'))
    : last ? L(`睡眠 ${hhmm(last.sleep_min)}${last.hrv_ms != null ? ` · HRV ${Math.round(last.hrv_ms)} ms` : ''}`, `Slept ${hhmm(last.sleep_min)}${last.hrv_ms != null ? ` · HRV ${Math.round(last.hrv_ms)} ms` : ''}`) : '';
  return (
    <View>
      <SectionLabel right={<HealthCaption />}>{L('身体状态', 'Body')}</SectionLabel>
      {!last ? <NoRecoveryCard /> : (
        <Card style={{ gap: space.md }}>
          <Pressable onPress={() => setOpen((v) => !v)} accessibilityRole="button" accessibilityState={{ expanded: open }} accessibilityHint={L('展开睡眠与心率详情', 'Shows sleep and heart rate details')}
            style={({ pressed }) => [styles.rowC, { gap: 14, opacity: pressed ? 0.7 : 1 }]}>
            {scored ? (
              <Ring size={64} stroke={7} value={rec.score ?? 0} target={100} color={bandColor(t, rec.band)}>
                <T v="title" style={{ fontWeight: '800', fontVariant: ['tabular-nums'] }}>{rec.score}</T>
              </Ring>
            ) : <View style={[styles.moon, { backgroundColor: t.surface2 }]}><Moon size={26} color={t.ink2} /></View>}
            <View style={{ flex: 1, minWidth: 0, gap: 6 }}>
              <View style={styles.rowC}>
                <T v="headline">{scored ? `${L('恢复分', 'Recovery')}${rec.date !== last.date ? ` · ${rec.date.slice(5)}` : ''}` : L('昨晚', 'Last night')}</T>
                {scored ? <Pill label={rec.label} tone={bandTone(rec.band)} /> : null}
              </View>
              {summary ? <T v="caption" color={t.ink2} numberOfLines={2} style={{ fontSize: 13, lineHeight: 18, fontWeight: '400' }}>{summary}</T> : null}
              {recDays.length ? <ScoreStrip days={recDays} height={22} /> : null}
            </View>
            <Disclosure open={open} />
          </Pressable>
          {open ? <View style={[styles.more, { borderTopColor: t.line, paddingTop: space.md }]}><RecoveryDetails score={false} strip={false} /></View> : null}
        </Card>
      )}
    </View>
  );
}

function Num({ value, label }: { value: string | number; label: string }) {
  const t = useTheme();
  return (
    <View style={{ flex: 1, gap: 2 }}>
      <T v="title" style={styles.num}>{value}</T>
      <T v="caption" color={t.ink2} style={{ fontWeight: '400' }}>{label}</T>
    </View>
  );
}

/** 这周的一次训练：一行，点开看每个动作的最重一组。 */
function WorkoutRow({ day, tr }: { day: string; tr: LiveTrain }) {
  const t = useTheme();
  const [open, setOpen] = useState(false);
  const n = tr.movements.length;
  return (
    <View style={[styles.wrow, { borderTopColor: t.line }]}>
      <Pressable onPress={() => setOpen((v) => !v)} accessibilityRole="button" accessibilityState={{ expanded: open }} style={({ pressed }) => [styles.rowC, { gap: 10, opacity: pressed ? 0.6 : 1 }]}>
        <T v="callout" color={t.ink3} style={{ width: lang() === 'zh' ? 36 : 44, fontSize: 13 }}>{day}</T>
        <T v="body" numberOfLines={1} style={{ fontWeight: '600', flexShrink: 1 }}>{tr.title}</T>
        <View style={{ flex: 1 }} />
        <T v="caption" color={t.ink3} style={{ fontVariant: ['tabular-nums'], fontWeight: '400' }}>{L(`${tr.minutes} 分钟 · ${n} 个动作`, `${tr.minutes} min · ${n} exercise${n === 1 ? '' : 's'}`)}</T>
        <Disclosure open={open} />
      </Pressable>
      {open ? (
        <View style={{ marginTop: 6 }}>
          <T v="caption" color={t.ink3} style={{ fontWeight: '400', marginBottom: 2 }}>{L(`${tr.start} 开始${tr.kcal ? ` · ${tr.kcal} kcal` : ''} · 完成 ${tr.sets_done} 组`, `Started ${tr.start}${tr.kcal ? ` · ${tr.kcal} kcal` : ''} · ${tr.sets_done} sets done`)}</T>
          {tr.movements.map((m, k) => (
            <View key={`${m.name}-${k}`} style={[styles.move, k > 0 && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }]}>
              <T v="callout" style={{ flex: 1 }} numberOfLines={1}>{m.name}</T>
              <T v="callout" color={t.ink2} style={{ fontVariant: ['tabular-nums'] }}>{m.top_set}</T>
              <T v="caption" color={t.ink3} style={{ width: lang() === 'zh' ? 38 : 56, textAlign: 'right', fontVariant: ['tabular-nums'] }}>{m.sets_done}/{m.sets_total} {L('组', 'sets')}</T>
            </View>
          ))}
        </View>
      ) : null}
    </View>
  );
}

/** 这周：三个数、每天的柱子、每次训练一行。 */
function WeekSection({ week, loadedAt }: { week: LiveWeek; loadedAt: string }) {
  const t = useTheme();
  const rows = week.days.flatMap((d, i) => d.trains.map((tr, k) => ({ key: `${d.date}-${k}`, day: i === week.today_index ? L('今天', 'Today') : weekdayName(d.d), tr })));
  return (
    <View>
      <SectionLabel right={<Caption>{`${week.source || L('训练记录', 'Workout log')} · ${loadedAt}`}</Caption>}>{L('本周', 'This week')}</SectionLabel>
      <Card style={{ gap: space.md }}>
        <View style={{ flexDirection: 'row', gap: space.sm }}>
          <Num value={week.sessions} label={L('次训练', week.sessions === 1 ? 'workout' : 'workouts')} />
          <Num value={week.total_minutes} label={L('分钟', 'minutes')} />
          <Num value={week.total_sets} label={L('组', 'sets')} />
        </View>
        <DayBars days={week.days} todayIndex={week.today_index} unit={L('分钟', 'min')} />
        {rows.length ? <View>{rows.map((r) => <WorkoutRow key={r.key} day={r.day} tr={r.tr} />)}</View>
          : <T v="callout" color={t.ink2}>{L('本周尚无训练记录。', 'No workouts logged this week.')}</T>}
      </Card>
    </View>
  );
}

// —— 长期：近 7 天对这段时间的平均 ——
const MINUS = '−';
const signed = (n: number, digits = 0) => `${n > 0 ? '+' : n < 0 ? MINUS : '±'}${Math.abs(n).toLocaleString('en-GB', { maximumFractionDigits: digits, minimumFractionDigits: digits })}`;
const md = (iso: string) => `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))}`;

function TrendCell({ value, label, delta, color }: { value: string; label: string; delta: string; color: string }) {
  const t = useTheme();
  return (
    <View style={{ width: '47%', gap: 2 }}>
      <T v="title" style={styles.num}>{value}</T>
      <T v="caption" color={t.ink2} style={{ fontWeight: '400' }}>{label}</T>
      <T v="caption" color={color} style={{ fontWeight: '600' }}>{delta}</T>
    </View>
  );
}

function TrendSection({ trend }: { trend: LiveTrend }) {
  const t = useTheme();
  const v = trend.vo2max;
  const first = v.points[0];
  // 近 7 天对这段时间的平均：差不到 5% 算持平（灰），变好绿，变差红
  const vsAvg = (s: { last7: number | null; window: number | null }, higherIsBetter: boolean, fmt: (n: number) => string) => {
    if (s.last7 == null || s.window == null) return { value: s.last7 == null ? '—' : fmt(s.last7), delta: s.window == null ? '' : L(`平均 ${fmt(s.window)}`, `Avg ${fmt(s.window)}`), color: t.ink3 };
    const d = s.last7 - s.window;
    const flat = s.window !== 0 && Math.abs(d) / Math.abs(s.window) < 0.05;
    const better = higherIsBetter ? d > 0 : d < 0;
    return { value: fmt(s.last7), delta: L(`较平均 ${signed(Math.round(d))}`, `${signed(Math.round(d))} vs avg`), color: flat ? t.ink3 : better ? t.good : t.bad };
  };
  const int = (n: number) => Math.round(n).toLocaleString('en-GB');
  const rhr = vsAvg(trend.resting_hr, false, int);
  const walk = vsAvg(trend.walking_hr, false, int);
  const steps = vsAvg(trend.steps, true, int);
  const vo2Delta = v.change == null
    ? { text: v.latest ? L('期间仅有一次读数', 'Only one reading so far') : L('户外步行或跑步后显示', 'Appears after an outdoor walk or run'), color: t.ink3 }
    : { text: first ? L(`较 ${md(first.date)} ${signed(v.change, 1)}`, `${signed(v.change, 1)} since ${md(first.date)}`) : signed(v.change, 1), color: Math.abs(v.change) < 0.5 ? t.ink3 : v.change > 0 ? t.good : t.bad };
  return (
    <View>
      <SectionLabel right={<Caption>{L('Apple 健康', 'Apple Health')}</Caption>}>{L(`长期 · ${trend.days} 天`, `Long term · ${trend.days} days`)}</SectionLabel>
      <Card style={{ flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'space-between', rowGap: 14 }}>
        <TrendCell value={v.latest ? String(v.latest.value) : '—'} label={L('最大摄氧量 · ml/kg/min', 'VO2 max · ml/kg/min')} delta={vo2Delta.text} color={vo2Delta.color} />
        <TrendCell value={rhr.value} label={L('静息心率 · 近 7 天', 'Resting HR · last 7 days')} delta={rhr.delta} color={rhr.color} />
        <TrendCell value={walk.value} label={L('步行心率 · 近 7 天', 'Walking HR · last 7 days')} delta={walk.delta} color={walk.color} />
        <TrendCell value={steps.value} label={L('日均步数 · 近 7 天', 'Daily steps · last 7 days')} delta={steps.delta} color={steps.color} />
      </Card>
    </View>
  );
}

/** 健身看板。GroupScreen 保证 live 已经读回来了。 */
export function FitnessBoard({ groupId, onAsk }: { groupId: string; onAsk: () => void }) {
  const t = useTheme();
  const { live, liveErrors } = useStore();
  if (!live) return null;
  // 每一节一个元素（按默认顺序）；排在哪、藏没藏由看板配置定（SectionedBoard）
  return (
    <SectionedBoard els={{
      'fitness.now': <NowSection groupId={groupId} onAsk={onAsk} />,
      'fitness.body': <BodySection />,
      'fitness.week': live.week ? <WeekSection week={live.week} loadedAt={live.loadedAt} /> : (
        <View>
          <SectionLabel>{L('本周', 'This week')}</SectionLabel>
          {live.sources.workouts === false ? <NoSourceCard kind={L('训练', 'workout')} />
            : <Card><T v="callout" color={t.bad}>{L(`无法加载训练数据${liveErrors.week ? `：${liveErrors.week}` : ''}`, `Couldn't load workout data${liveErrors.week ? `: ${liveErrors.week}` : ''}`)}</T></Card>}
        </View>
      ),
      'fitness.long': live.trend ? <TrendSection trend={live.trend} /> : null,
    }} />
  );
}

const styles = StyleSheet.create({
  rowC: { flexDirection: 'row', alignItems: 'center', gap: space.sm },
  big: { fontSize: 18, fontWeight: '700', lineHeight: 24 },
  box: { borderWidth: 1, borderRadius: 12, paddingVertical: 10, paddingHorizontal: space.md, gap: 6 },
  more: { borderTopWidth: StyleSheet.hairlineWidth, paddingTop: 10 },
  line: { borderTopWidth: StyleSheet.hairlineWidth, paddingTop: 10 },
  wrow: { borderTopWidth: StyleSheet.hairlineWidth, paddingVertical: 11 },
  li: { flexDirection: 'row', gap: 6, alignItems: 'flex-start' },
  moon: { width: 64, height: 64, borderRadius: 32, alignItems: 'center', justifyContent: 'center' },
  num: { fontSize: 24, fontWeight: '800', letterSpacing: -0.2, fontVariant: ['tabular-nums'] },
  move: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: 7 },
});
