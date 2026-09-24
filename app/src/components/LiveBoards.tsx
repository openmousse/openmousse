import React from 'react';
import { agentName } from '../brand';
import { Platform, Pressable, StyleSheet, View } from 'react-native';
import type { LiveDiet, LiveEnergy, LiveEnergyDay, LiveRecoveryDay, LiveTrend, LiveTrendStat, LiveWeek } from '../api/live';
import { useStore } from '../store';
import { space, useTheme } from '../theme';
import { Meter, Ring, WeekBars } from './charts';
import { Activity, Flame, HeartPulse, Moon, RefreshCw, Sparkles, Utensils } from './icons';
import { Btn, Card, Pill, SectionLabel, T } from './ui';
import type { MealPlan } from '../data/types';

/** 某类数据还没接来源时看板上放的说明卡：不是错误，是空状态。 */
export function NoSourceCard({ kind, hint }: { kind: string; hint?: string }) {
  const t = useTheme();
  return (
    <Card style={{ gap: space.xs }}>
      <T v="headline">还没接{kind}数据</T>
      <T v="callout" color={t.ink2}>{hint ?? `直接在对话里告诉 ${agentName()}，它会记下来；也可以在服务器上接一个提供${kind}数据的软件或脚本。`}</T>
    </Card>
  );
}

export function SourceBar({ source }: { source: string }) {
  const t = useTheme();
  const { live, liveLoading, refreshLive } = useStore();
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm, marginBottom: space.md }}>
      <Pill label={`${source} · 真实数据`} tone="good" />
      <T v="caption" color={t.ink3} style={{ flex: 1 }}>{liveLoading ? '正在更新…' : `更新于 ${live?.loadedAt ?? ''}`}</T>
      <Pressable onPress={refreshLive} disabled={liveLoading} hitSlop={10} accessibilityRole="button" accessibilityLabel="刷新">
        <RefreshCw size={16} color={liveLoading ? t.ink3 : t.cyan} />
      </Pressable>
    </View>
  );
}

function Stat({ value, label }: { value: string; label: string }) {
  const t = useTheme();
  return (
    <View style={{ flex: 1, gap: 2 }}>
      <T v="title" style={{ fontVariant: ['tabular-nums'] }}>{value}</T>
      <T v="caption" color={t.ink3}>{label}</T>
    </View>
  );
}

export function LiveFitnessBoard({ week }: { week: LiveWeek }) {
  const t = useTheme();
  const trained = week.days.filter((d) => d.trains.length);
  return (
    <View>
      <SourceBar source={week.source || '训练记录'} />
      <Card style={{ flexDirection: 'row', gap: space.md }}>
        <Stat value={`${week.sessions}`} label="本周训练次数" />
        <Stat value={`${week.total_minutes}`} label="总分钟" />
        <Stat value={`${week.total_sets}`} label="完成组数" />
      </Card>
      <SectionLabel>每日训练时长</SectionLabel>
      <Card><WeekBars days={week.days} unit="分钟" todayIndex={week.today_index} /></Card>
      <SectionLabel>本周练了什么</SectionLabel>
      {trained.length ? (
        <View style={{ gap: space.md }}>
          {trained.flatMap((d) => d.trains.map((tr, i) => (
            <Card key={`${d.date}-${i}`} style={{ gap: space.sm }}>
              <View style={{ flexDirection: 'row', alignItems: 'baseline', gap: space.sm }}>
                <T v="headline" style={{ flex: 1 }}>{tr.title}</T>
                <T v="caption" color={t.ink3} style={{ fontVariant: ['tabular-nums'] }}>
                  周{d.d} {tr.start} · {tr.minutes} 分钟{tr.kcal ? ` · ${tr.kcal} kcal` : ''}
                </T>
              </View>
              {tr.movements.map((m, k) => (
                <View key={`${m.name}-${k}`} style={[styles.row, k > 0 && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }]}>
                  <T v="callout" style={{ flex: 1 }} numberOfLines={1}>{m.name}</T>
                  <T v="callout" color={t.ink2} style={{ fontVariant: ['tabular-nums'] }}>{m.top_set}</T>
                  <T v="caption" color={t.ink3} style={{ width: 38, textAlign: 'right', fontVariant: ['tabular-nums'] }}>{m.sets_done}/{m.sets_total} 组</T>
                </View>
              ))}
            </Card>
          )))}
        </View>
      ) : <Card><T v="callout" color={t.ink2}>这周还没有训练记录。</T></Card>}
    </View>
  );
}

export function LiveDietBoard({ diet, energy, energyError, groupId, onAsk }: { diet: LiveDiet; energy: LiveEnergy | null; energyError?: string; groupId: string; onAsk: () => void }) {
  const t = useTheme();
  const { totals, targets } = diet;
  const { feed, send, typing } = useStore();
  const today = localDate();
  const plan = feed.find((f) => f.groupId === groupId && f.kind === 'meal_plan' && !!f.data && 'meals' in f.data && (f.createdAt ?? '').slice(0, 10) === today);
  const busy = !!typing[groupId];
  const ask = () => { send(groupId, '出今天的三餐建议'); onAsk(); };
  return (
    <View>
      <SourceBar source={diet.source || '饮食记录'} />
      <SectionLabel right={plan ? <T v="caption" color={t.ink3}>{plan.time}</T> : undefined}>{`${agentName()} 的建议`}</SectionLabel>
      {plan?.data && 'meals' in plan.data ? (
        <Card style={{ gap: space.sm }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
            <Utensils size={16} color={t.ink2} />
            <T v="headline" style={{ flex: 1 }}>{plan.title}</T>
          </View>
          <MealPlanCard plan={plan.data} />
          <Btn label={busy ? `${agentName()} 正在出…` : '重新出一份'} kind="quiet" onPress={ask} icon={<Sparkles size={14} color={t.ink} />} />
        </Card>
      ) : (
        <Card style={{ gap: space.sm }}>
          <T v="callout" color={t.ink2}>{`今天还没有三餐建议。${agentName()} 会按你的固定早餐、常买清单、今天练不练和还差的热量来配。`}</T>
          <Btn label={busy ? `${agentName()} 正在出…` : `让 ${agentName()} 出今天的建议`} kind="primary" onPress={ask} icon={<Sparkles size={14} color={t.onGold} />} />
        </Card>
      )}
      <Card style={{ flexDirection: 'row', alignItems: 'center', gap: space.lg }}>
        {targets ? (
          <Ring size={88} stroke={8} value={totals.kcal} target={targets.kcal}>
            <T v="title" style={{ fontVariant: ['tabular-nums'] }}>{totals.kcal}</T>
            <T v="caption" color={t.ink3}>/ {targets.kcal} kcal</T>
          </Ring>
        ) : (
          <View style={{ width: 88, alignItems: 'center' }}>
            <T v="largeTitle" style={{ fontSize: 28, fontVariant: ['tabular-nums'] }}>{totals.kcal}</T>
            <T v="caption" color={t.ink3}>kcal 今天</T>
          </View>
        )}
        <View style={{ flex: 1, gap: space.md }}>
          <Meter label="蛋白质" value={totals.protein} target={targets?.protein} unit="g" />
          <Meter label="碳水" value={totals.carb} target={targets?.carb} unit="g" />
          <Meter label="脂肪" value={totals.fat} target={targets?.fat} unit="g" />
        </View>
      </Card>
      {targets?.kcal_derived ? <T v="caption" color={t.ink3} style={{ marginTop: space.sm, paddingHorizontal: space.xs }}>目标来自训记：蛋白质 {targets.protein} g、碳水 {targets.carb} g、脂肪 {targets.fat} g。训记不存热量目标，{targets.kcal} kcal 是按这三项换算的。</T> : null}
      {!targets ? <T v="callout" color={t.ink3} style={{ marginTop: space.sm, paddingHorizontal: space.xs }}>{`还没设每日目标，所以只显示摄入量。告诉 ${agentName()} 你的热量和三大营养素目标，这里就会变成进度环。`}</T> : null}
      <SectionLabel>热量缺口</SectionLabel>
      {energy ? <EnergyCard energy={energy} /> : <Card><T v="callout" color={t.ink2}>{energyError ? `消耗数据没读到：${energyError}` : 'Apple 健康还没同步，算不了消耗。在 iPhone 上打开健身看板同步一次。'}</T></Card>}
      <SectionLabel>今天吃了什么</SectionLabel>
      {diet.meals.length ? (
        <View style={{ gap: space.md }}>
          {diet.meals.map((m) => (
            <Card key={m.label} style={{ gap: space.sm }}>
              <View style={{ flexDirection: 'row', alignItems: 'baseline' }}>
                <T v="headline" style={{ flex: 1 }}>{m.label}</T>
                <T v="caption" color={t.ink3} style={{ fontVariant: ['tabular-nums'] }}>{m.kcal} kcal · 蛋白质 {m.protein} g</T>
              </View>
              {m.items.map((it, k) => (
                <View key={`${it.name}-${k}`} style={[styles.row, k > 0 && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }]}>
                  <T v="callout" style={{ flex: 1 }} numberOfLines={1}>{it.name}</T>
                  <T v="callout" color={t.ink3} style={{ fontVariant: ['tabular-nums'] }}>{it.amount} {it.unit}</T>
                  <T v="callout" color={t.ink2} style={{ width: 64, textAlign: 'right', fontVariant: ['tabular-nums'] }}>{it.kcal ?? '–'} kcal</T>
                </View>
              ))}
            </Card>
          ))}
        </View>
      ) : <Card><T v="callout" color={t.ink2}>今天还没有饮食记录。</T></Card>}
    </View>
  );
}

const styles = StyleSheet.create({ row: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingTop: 8 } });

const hhmm = (min: number | null) => (min == null ? '—' : `${Math.floor(min / 60)}h${String(Math.round(min % 60)).padStart(2, '0')}`);
const avg = (xs: (number | null)[]) => { const v = xs.filter((x): x is number => x != null); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; };

/** 和前 14 天均值比：HRV 高于均值是好事，静息心率低于均值是好事。 */
function Versus({ value, base, unit, higherIsBetter }: { value: number | null; base: number | null; unit: string; higherIsBetter: boolean }) {
  const t = useTheme();
  if (value == null || base == null) return <T v="caption" color={t.ink3}>14 天均值 {base == null ? '—' : `${Math.round(base)} ${unit}`}</T>;
  const d = value - base;
  const good = higherIsBetter ? d >= 0 : d <= 0;
  const flat = Math.abs(d) / base < 0.05;
  return <T v="caption" color={flat ? t.ink3 : good ? t.good : t.warn}>均值 {Math.round(base)} · {d >= 0 ? '+' : ''}{Math.round(d)}</T>;
}

/** 恢复：昨晚睡眠 + HRV + 静息心率，数据来自 Apple 健康（iPhone app 同步）。 */
export function LiveRecoveryCard() {
  const t = useTheme();
  const { live, liveErrors, syncHealthNow } = useStore();
  const [busy, setBusy] = React.useState(false);
  const days = live?.health?.days ?? [];
  const last = [...days].reverse().find((d) => d.sleep_min != null || d.hrv_ms != null || d.rhr_bpm != null);
  const prior = days.filter((d) => d !== last);
  const canSync = Platform.OS === 'ios';
  const sync = () => { setBusy(true); syncHealthNow().catch(() => {}).finally(() => setBusy(false)); };
  const err = liveErrors.health;
  const rec = live?.recovery?.latest ?? null;
  const recDays = live?.recovery?.days ?? [];
  return (
    <View style={{ marginBottom: space.lg }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm, marginBottom: space.md }}>
        <Pill label="Apple 健康 · 真实数据" tone="good" />
        <T v="caption" color={err ? t.bad : t.ink3} style={{ flex: 1 }} numberOfLines={1}>
          {busy ? '正在同步…' : err ? `同步失败：${err}` : live?.health?.synced_at ? `同步于 ${live.health.synced_at.slice(5, 16).replace('T', ' ')}` : '还没同步过'}
        </T>
        {canSync ? (
          <Pressable onPress={sync} disabled={busy} hitSlop={10} accessibilityRole="button" accessibilityLabel="同步 Apple 健康">
            <RefreshCw size={16} color={busy ? t.ink3 : t.cyan} />
          </Pressable>
        ) : null}
      </View>
      {last ? (
        <Card style={{ gap: space.md }}>
          {rec && rec.score != null ? (
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.md }}>
              <Ring size={64} stroke={7} value={rec.score} target={100} color={bandColor(t, rec.band)}>
                <T v="title" style={{ fontVariant: ['tabular-nums'] }}>{rec.score}</T>
              </Ring>
              <View style={{ flex: 1, gap: 3 }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
                  <T v="headline">恢复分{rec.date !== last.date ? ` · ${rec.date.slice(5)}` : ''}</T>
                  <Pill label={rec.label} tone={rec.band === 'good' ? 'good' : rec.band === 'low' ? 'bad' : 'warn'} />
                </View>
                <T v="caption" color={t.ink2}>{rec.notes.length ? rec.notes.join(' · ') : '各项都在基线附近'}</T>
              </View>
            </View>
          ) : null}
          <View style={{ flexDirection: 'row', alignItems: 'baseline', gap: space.sm }}>
            <Moon size={16} color={t.ink2} />
            <T v="headline" style={{ flex: 1 }}>{last.date === days[days.length - 1]?.date ? '昨晚' : last.date.slice(5)} 睡了 {hhmm(last.sleep_min)}</T>
            {last.bed_start ? <T v="caption" color={t.ink3} style={{ fontVariant: ['tabular-nums'] }}>{last.bed_start}–{last.bed_end}</T> : null}
          </View>
          {last.deep_min != null ? (
            <T v="callout" color={t.ink2} style={{ fontVariant: ['tabular-nums'] }}>
              深睡 {hhmm(last.deep_min)} · REM {hhmm(last.rem_min)} · 核心 {hhmm(last.core_min)} · 醒着 {Math.round(last.awake_min ?? 0)} 分钟
            </T>
          ) : null}
          <View style={{ flexDirection: 'row', gap: space.md }}>
            <View style={{ flex: 1, gap: 2 }}>
              <T v="title" style={{ fontVariant: ['tabular-nums'] }}>{last.hrv_ms == null ? '—' : Math.round(last.hrv_ms)}<T v="caption" color={t.ink3}> ms</T></T>
              <T v="caption" color={t.ink3}>夜间 HRV</T>
              <Versus value={last.hrv_ms} base={avg(prior.map((d) => d.hrv_ms))} unit="ms" higherIsBetter />
            </View>
            <View style={{ flex: 1, gap: 2 }}>
              <T v="title" style={{ fontVariant: ['tabular-nums'] }}>{last.rhr_bpm == null ? '—' : Math.round(last.rhr_bpm)}<T v="caption" color={t.ink3}> bpm</T></T>
              <T v="caption" color={t.ink3}>静息心率</T>
              <Versus value={last.rhr_bpm} base={avg(prior.map((d) => d.rhr_bpm))} unit="bpm" higherIsBetter={false} />
            </View>
            <View style={{ flex: 1, gap: 2 }}>
              <T v="title" style={{ fontVariant: ['tabular-nums'] }}>{hhmm(avg(days.map((d) => d.sleep_min)))}</T>
              <T v="caption" color={t.ink3}>14 天平均睡眠</T>
            </View>
          </View>
          {recDays.length ? (
            <View style={{ gap: 6 }}>
              <ScoreStrip days={recDays} />
              <T v="caption" color={t.ink3}>近 {recDays.length} 天恢复分。{agentName()} 自己算的：HRV 40% · 静息心率 25% · 睡眠 25% · 手腕温度 10%，各和前 14 天中位数比；昨天练得重扣 5。</T>
            </View>
          ) : null}
        </Card>
      ) : (
        <Card style={{ gap: space.sm }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
            <HeartPulse size={18} color={t.ink2} />
            <T v="headline">还没有恢复数据</T>
          </View>
          <T v="callout" color={t.ink2}>
            {canSync ? '点右上角同步，第一次会弹出 Apple 健康的授权页，勾上睡眠、心率变异性、静息心率。' : `在 iPhone 上的 ${agentName()} app 里打开这一页，会自动同步 Apple 健康。`}
          </T>
        </Card>
      )}
    </View>
  );
}

const bandColor = (t: ReturnType<typeof useTheme>, band: LiveRecoveryDay['band']) => (band === 'good' ? t.good : band === 'ok' ? t.warn : band === 'low' ? t.bad : t.track);

/** 近 14 天恢复分的小条：高度是分数，颜色是档位。 */
function ScoreStrip({ days }: { days: LiveRecoveryDay[] }) {
  const t = useTheme();
  const H = 28;
  return (
    <View style={{ flexDirection: 'row', alignItems: 'flex-end', gap: 3, height: H }} accessibilityLabel={`近 ${days.length} 天恢复分：${days.map((d) => d.score ?? '无').join('、')}`}>
      {days.map((d) => (
        <View key={d.date} style={{ flex: 1, height: d.score == null ? 3 : Math.max(3, Math.round((d.score / 100) * H)), backgroundColor: bandColor(t, d.band), borderRadius: 2, opacity: d.score == null ? 0.6 : 1 }} />
      ))}
    </View>
  );
}

const kcal = (n: number | null | undefined) => (n == null ? '—' : Math.round(n).toLocaleString('en-GB'));

/** 热量缺口：消耗（Apple Watch 估算）对摄入（饮食记录）。正数是缺口，负数是超出。 */
function EnergyCard({ energy }: { energy: LiveEnergy }) {
  const t = useTheme();
  const today = energy.days[energy.days.length - 1];
  const s = energy.summary;
  const syncedHm = energy.synced_at ? energy.synced_at.slice(11, 16) : '';
  const gap = (v: number | null) => (v == null ? <T v="title" color={t.ink3}>—</T>
    : <T v="title" color={v >= 0 ? t.good : t.warn} style={{ fontVariant: ['tabular-nums'] }}>{v >= 0 ? '−' : '+'}{kcal(Math.abs(v))}</T>);
  return (
    <Card style={{ gap: space.md }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
        <Flame size={16} color={t.ink2} />
        <T v="headline" style={{ flex: 1 }}>今天{today?.partial ? `到 ${syncedHm} 为止` : ''}</T>
        {today?.intake == null ? <Pill label="还没记摄入" tone="neutral" /> : null}
      </View>
      <View style={{ flexDirection: 'row', gap: space.md }}>
        <View style={{ flex: 1, gap: 2 }}>
          <T v="title" style={{ fontVariant: ['tabular-nums'] }}>{kcal(today?.burned)}</T>
          <T v="caption" color={t.ink3}>消耗 kcal</T>
          <T v="caption" color={t.ink3} style={{ fontVariant: ['tabular-nums'] }}>活动 {kcal(today?.active)} + 静息 {kcal(today?.basal)}</T>
        </View>
        <View style={{ flex: 1, gap: 2 }}>
          <T v="title" style={{ fontVariant: ['tabular-nums'] }}>{kcal(today?.intake)}</T>
          <T v="caption" color={t.ink3}>摄入 kcal</T>
          <T v="caption" color={t.ink3}>{today?.intake_items ? `摄入 ${today.intake_items} 条` : '摄入记录'}</T>
        </View>
        <View style={{ flex: 1, gap: 2 }}>
          {gap(today?.deficit ?? null)}
          <T v="caption" color={t.ink3}>{today?.deficit != null && today.deficit < 0 ? '超出' : '缺口'} kcal</T>
        </View>
      </View>
      <DeficitBars days={energy.days} />
      {s.days_counted ? (
        <T v="callout" color={t.ink2} style={{ fontVariant: ['tabular-nums'] }}>
          近 7 天有记录的 {s.days_counted} 天：平均消耗 {kcal(s.avg_burned)}、摄入 {kcal(s.avg_intake)}，{(s.avg_deficit ?? 0) >= 0 ? '平均缺口' : '平均超出'} {kcal(Math.abs(s.avg_deficit ?? 0))} kcal/天{s.est_fat_kg != null ? `，累计约 ${s.est_fat_kg >= 0 ? '−' : '+'}${Math.abs(s.est_fat_kg).toFixed(2)} kg 脂肪` : ''}。
        </T>
      ) : <T v="callout" color={t.ink2}>近 7 天还没有完整的饮食记录，算不了缺口。</T>}
      <T v="caption" color={t.ink3}>{energy.intake_error ? `摄入数据读取失败：${energy.intake_error}` : energy.note}</T>
    </Card>
  );
}

/** 一周的缺口柱：向上是缺口（绿），向下是超出（黄），没记录画一小段灰。点不动，看趋势用。 */
function DeficitBars({ days }: { days: LiveEnergyDay[] }) {
  const t = useTheme();
  const H = 44;
  const max = Math.max(300, ...days.map((d) => Math.abs(d.deficit ?? 0)));
  return (
    <View>
      <View style={{ flexDirection: 'row', height: H * 2 + 1 }}>
        {days.map((d) => {
          const v = d.deficit;
          const h = v == null ? 0 : Math.max(3, Math.round((Math.abs(v) / max) * (H - 2)));
          const label = v == null ? '没有记录' : v >= 0 ? `缺口 ${Math.round(v)} kcal` : `超出 ${Math.round(-v)} kcal`;
          return (
            <View key={d.date} style={{ flex: 1, alignItems: 'center' }} accessibilityLabel={`周${d.weekday} ${label}`}>
              <View style={{ height: H, justifyContent: 'flex-end' }}>
                {v != null && v >= 0 ? <View style={{ width: 14, height: h, backgroundColor: t.good, opacity: d.partial ? 0.45 : 1, borderTopLeftRadius: 4, borderTopRightRadius: 4 }} /> : null}
              </View>
              <View style={{ alignSelf: 'stretch', height: 1, backgroundColor: t.line }} />
              <View style={{ height: H, justifyContent: 'flex-start' }}>
                {v != null && v < 0 ? <View style={{ width: 14, height: h, backgroundColor: t.warn, opacity: d.partial ? 0.45 : 1, borderBottomLeftRadius: 4, borderBottomRightRadius: 4 }} /> : null}
                {v == null ? <View style={{ width: 14, height: 3, backgroundColor: t.track, marginTop: 2, borderRadius: 2 }} /> : null}
              </View>
            </View>
          );
        })}
      </View>
      <View style={{ flexDirection: 'row', marginTop: 4 }}>
        {days.map((d) => <T key={d.date} v="caption" color={d.partial ? t.ink : t.ink3} style={{ flex: 1, textAlign: 'center', fontWeight: d.partial ? '700' : '500' }}>{d.weekday}</T>)}
      </View>
    </View>
  );
}

function TrendStat({ value, label, stat, unit, higherIsBetter }: { value: string; label: string; stat: LiveTrendStat; unit: string; higherIsBetter: boolean }) {
  const t = useTheme();
  return (
    <View style={{ width: '47%', gap: 2 }}>
      <T v="title" style={{ fontVariant: ['tabular-nums'] }}>{value}<T v="caption" color={t.ink3}> {unit}</T></T>
      <T v="caption" color={t.ink3}>{label}</T>
      <Versus value={stat.last7} base={stat.window} unit={unit} higherIsBetter={higherIsBetter} />
    </View>
  );
}

/** 体能趋势：VO2max、步行心率、静息心率、步数，近 7 天对近 90 天。数据来自 Apple 健康。 */
export function LiveFitnessTrendCard({ trend }: { trend: LiveTrend }) {
  const t = useTheme();
  const v = trend.vo2max;
  const fmt = (n: number | null) => (n == null ? '—' : `${Math.round(n)}`);
  return (
    <View>
      <SectionLabel>{`体能趋势 · 近 ${trend.days} 天`}</SectionLabel>
      <Card style={{ gap: space.md }}>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.md, justifyContent: 'space-between' }}>
          <View style={{ width: '47%', gap: 2 }}>
            <T v="title" style={{ fontVariant: ['tabular-nums'] }}>{v.latest ? v.latest.value : '—'}<T v="caption" color={t.ink3}> ml/kg/min</T></T>
            <T v="caption" color={t.ink3}>VO2max{v.latest ? ` · ${v.latest.date.slice(5)}` : ''}</T>
            <T v="caption" color={v.change == null ? t.ink3 : v.change >= 0 ? t.good : t.warn}>{v.change == null ? (v.latest ? '这段时间只有一次' : '户外走跑后才有') : `${v.change >= 0 ? '+' : ''}${v.change} 对这段最早一次`}</T>
          </View>
          <TrendStat value={fmt(trend.walking_hr.last7)} label="步行心率 · 近 7 天" stat={trend.walking_hr} unit="bpm" higherIsBetter={false} />
          <TrendStat value={fmt(trend.resting_hr.last7)} label="静息心率 · 近 7 天" stat={trend.resting_hr} unit="bpm" higherIsBetter={false} />
          <TrendStat value={trend.steps.last7 == null ? '—' : Math.round(trend.steps.last7).toLocaleString('en-GB')} label="步数 · 近 7 天日均" stat={trend.steps} unit="步" higherIsBetter />
        </View>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.xs }}>
          <Activity size={12} color={t.ink3} />
          <T v="caption" color={t.ink3}>{`Apple 健康。「均值」是这 ${trend.days} 天的平均；同样走路心率越低、静息心率越低越好。`}</T>
        </View>
      </Card>
    </View>
  );
}

const localDate = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
const n0 = (x: number | string | null | undefined) => (x == null || x === '' ? null : Math.round(Number(x)));

/** 三餐建议卡（Agent 写进 feed_items，kind = meal_plan）。今天页和饮食看板共用。 */
export function MealPlanCard({ plan }: { plan: MealPlan }) {
  const t = useTheme();
  const tot = plan.totals;
  return (
    <View style={{ gap: space.md }}>
      {plan.meals.map((m, i) => (
        <View key={`${m.label}-${i}`} style={{ gap: 4 }}>
          <View style={{ flexDirection: 'row', alignItems: 'baseline', gap: space.sm }}>
            <T v="headline" style={{ flex: 1 }}>{m.label}{m.time ? <T v="caption" color={t.ink3}>  {m.time}</T> : null}</T>
            <T v="caption" color={t.ink3} style={{ fontVariant: ['tabular-nums'] }}>{n0(m.kcal) ?? '–'} kcal · 蛋白质 {n0(m.protein) ?? '–'} g</T>
          </View>
          {m.items.map((it, k) => (
            <View key={`${it.name}-${k}`} style={[styles.row, k > 0 && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }]}>
              <T v="callout" style={{ flex: 1 }} numberOfLines={1}>{it.name}</T>
              <T v="callout" color={t.ink3} style={{ fontVariant: ['tabular-nums'] }}>{it.amount != null && it.amount !== '' ? `${it.amount} ${it.unit ?? 'g'}` : ''}</T>
              <T v="callout" color={t.ink2} style={{ width: 64, textAlign: 'right', fontVariant: ['tabular-nums'] }}>{n0(it.kcal) ?? '–'} kcal</T>
            </View>
          ))}
          {m.note ? <T v="caption" color={t.ink3}>{m.note}</T> : null}
        </View>
      ))}
      {tot ? (
        <T v="callout" color={t.ink2} style={{ fontVariant: ['tabular-nums'] }}>
          建议合计 {n0(tot.kcal) ?? '–'} kcal · 蛋白质 {n0(tot.protein) ?? '–'} g · 碳水 {n0(tot.carb) ?? '–'} g · 脂肪 {n0(tot.fat) ?? '–'} g
        </T>
      ) : null}
      {plan.vs_target ? <T v="caption" color={t.ink3}>{plan.vs_target}</T> : null}
      {plan.why ? <T v="caption" color={t.ink2}>{plan.why}</T> : null}
      {plan.shopping?.length ? <T v="caption" color={t.gold}>要买：{plan.shopping.join('、')}</T> : null}
    </View>
  );
}
