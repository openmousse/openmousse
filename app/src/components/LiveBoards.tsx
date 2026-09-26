// 看板共用的几块：没接数据源的说明、Apple 健康的来源小字、恢复与睡眠、热量缺口柱、三餐建议卡。
// 健身看板在 FitnessBoard.tsx，饮食看板在 DietBoard.tsx。数据都来自 store 的 live（src/api/live.ts）。
import React, { useState } from 'react';
import { agentName } from '../brand';
import { Platform, Pressable, StyleSheet, View } from 'react-native';
import type { LiveEnergyDay, LiveRecoveryDay } from '../api/live';
import { useStore } from '../store';
import { space, useTheme, type Theme } from '../theme';
import { Ring, weekdayName, weekdayShort } from './charts';
import { HeartPulse, Moon, RefreshCw } from './icons';
import { Card, Pill, SectionLabel, T } from './ui';
import type { MealPlan } from '../data/types';
import { L } from '../i18n';

/** 餐次是服务器给的枚举值（早餐 / 午餐 / 晚餐 / 练前 / 练后 / 加餐 / 其他），不翻译；显示时按当前语言换。别的写法原样显示。 */
const MEAL_EN: Record<string, string> = { 早餐: 'Breakfast', 午餐: 'Lunch', 晚餐: 'Dinner', 练前: 'Pre-workout', 练后: 'Post-workout', 加餐: 'Snack', 其他: 'Other' };
export const mealLabel = (label: string) => L(label, MEAL_EN[label] ?? label);

/** 本地日期 YYYY-MM-DD（建议卡按 createdAt 的日期算「今天的」）。 */
export const localDate = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
/** 1,269 这种写法；没有值是 —。 */
export const kcal = (n: number | null | undefined) => (n == null ? '—' : Math.round(n).toLocaleString('en-GB'));
export const hhmm = (min: number | null | undefined) => (min == null ? '—' : `${Math.floor(min / 60)}h${String(Math.round(min % 60)).padStart(2, '0')}`);
const avg = (xs: (number | null)[]) => { const v = xs.filter((x): x is number => x != null); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; };

/** 某类数据还没接来源时看板上放的说明卡：不是错误，是空状态。 */
export function NoSourceCard({ kind, hint }: { kind: string; hint?: string }) {
  const t = useTheme();
  return (
    <Card style={{ gap: space.xs }}>
      <T v="headline">{L(`还没接${kind}数据`, `No ${kind} data connected yet`)}</T>
      <T v="callout" color={t.ink2}>{hint ?? L(`直接在对话里告诉 ${agentName()}，它会记下来；也可以在服务器上接一个提供${kind}数据的软件或脚本。`, `Just tell ${agentName()} in chat and it'll keep track, or connect an app or script on the server that provides ${kind} data.`)}</T>
    </Card>
  );
}

/** 小标题右边的一行小字（数据来源、时间）。 */
export function Caption({ children, color }: { children: React.ReactNode; color?: string }) {
  const t = useTheme();
  return <T v="caption" color={color ?? t.ink3} numberOfLines={1} style={{ fontWeight: '400', flexShrink: 1, textAlign: 'right' }}>{children}</T>;
}

/** 同步时间：今天的只写钟点，别的日子带上月-日。 */
const syncedLabel = (iso: string) => (iso.slice(0, 10) === localDate() ? iso.slice(11, 16) : `${iso.slice(5, 10)} ${iso.slice(11, 16)}`);

/** 「Apple 健康 · 16:36」。iPhone 上点它就重新同步一次。 */
export function HealthCaption() {
  const t = useTheme();
  const { live, liveErrors, syncHealthNow } = useStore();
  const [busy, setBusy] = useState(false);
  const canSync = Platform.OS === 'ios';
  const err = liveErrors.health;
  const at = live?.health?.synced_at;
  const text = busy ? L('正在同步…', 'Syncing…')
    : err ? (canSync ? L('同步失败，点一下重试', 'Sync failed, tap to retry') : L("Apple 健康 · 没读到", "Apple Health · couldn't load"))
      : at ? `${L('Apple 健康', 'Apple Health')} · ${syncedLabel(at)}` : L('Apple 健康 · 还没同步', 'Apple Health · not synced yet');
  const color = err && !busy ? t.bad : t.ink3;
  if (!canSync) return <Caption color={color}>{text}</Caption>;
  const sync = () => { if (busy) return; setBusy(true); syncHealthNow().catch(() => {}).finally(() => setBusy(false)); };
  return (
    <Pressable onPress={sync} disabled={busy} hitSlop={10} accessibilityRole="button" accessibilityLabel={L(`${text}。点一下同步 Apple 健康`, `${text}. Tap to sync Apple Health`)}
      style={{ flexDirection: 'row', alignItems: 'center', gap: 4, flexShrink: 1 }}>
      <Caption color={color}>{text}</Caption>
      <RefreshCw size={11} color={busy ? t.ink3 : t.cyan} />
    </Pressable>
  );
}

/** 和前 14 天均值比：HRV 高于均值是好事，静息心率低于均值是好事。 */
function Versus({ value, base, unit, higherIsBetter }: { value: number | null; base: number | null; unit: string; higherIsBetter: boolean }) {
  const t = useTheme();
  if (value == null || base == null) return <T v="caption" color={t.ink3}>{L('14 天均值', '14-day avg')} {base == null ? '—' : `${Math.round(base)} ${unit}`}</T>;
  const d = value - base;
  const good = higherIsBetter ? d >= 0 : d <= 0;
  const flat = Math.abs(d) / base < 0.05;
  return <T v="caption" color={flat ? t.ink3 : good ? t.good : t.warn}>{L('均值', 'Avg')} {Math.round(base)} · {d >= 0 ? '+' : ''}{Math.round(d)}</T>;
}

export const bandColor = (t: Theme, band: LiveRecoveryDay['band']) => (band === 'good' ? t.good : band === 'ok' ? t.warn : band === 'low' ? t.bad : t.track);
export const bandTone = (band: LiveRecoveryDay['band']) => (band === 'good' ? 'good' : band === 'low' ? 'bad' : 'warn') as 'good' | 'bad' | 'warn';

/** 恢复与睡眠用到的数：最近一晚、之前几晚、最新恢复分、近 14 天恢复分。 */
export function useRecovery() {
  const { live } = useStore();
  const days = live?.health?.days ?? [];
  const last = [...days].reverse().find((d) => d.sleep_min != null || d.hrv_ms != null || d.rhr_bpm != null);
  const prior = days.filter((d) => d !== last);
  return { days, last, prior, rec: live?.recovery?.latest ?? null, recDays: live?.recovery?.days ?? [] };
}

/** 近 14 天恢复分的小条：高度是分数，颜色是档位。 */
export function ScoreStrip({ days, height = 28 }: { days: LiveRecoveryDay[]; height?: number }) {
  const t = useTheme();
  return (
    <View style={{ flexDirection: 'row', alignItems: 'flex-end', gap: 3, height }} accessibilityLabel={L(`近 ${days.length} 天恢复分：${days.map((d) => d.score ?? '无').join('、')}`, `Recovery score, last ${days.length} days: ${days.map((d) => d.score ?? 'none').join(', ')}`)}>
      {days.map((d) => (
        <View key={d.date} style={{ flex: 1, height: d.score == null ? 3 : Math.max(3, Math.round((d.score / 100) * height)), backgroundColor: bandColor(t, d.band), borderRadius: 2, opacity: d.score == null ? 0.6 : 1 }} />
      ))}
    </View>
  );
}

/**
 * 恢复与睡眠的细节：恢复分（score）、昨晚睡了多久和分期、HRV / 静息心率 / 14 天平均睡眠、近 14 天的小条（strip）和算法说明。
 * 健康看板整块显示；健身看板上方是紧凑的一行，点开才显示这些（那时分数和小条已经在上面了）。
 */
export function RecoveryDetails({ score = true, strip = true }: { score?: boolean; strip?: boolean }) {
  const t = useTheme();
  const { days, last, prior, rec, recDays } = useRecovery();
  if (!last) return null;
  return (
    <View style={{ gap: space.md }}>
      {score && rec && rec.score != null ? (
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.md }}>
          <Ring size={64} stroke={7} value={rec.score} target={100} color={bandColor(t, rec.band)}>
            <T v="title" style={{ fontVariant: ['tabular-nums'] }}>{rec.score}</T>
          </Ring>
          <View style={{ flex: 1, gap: 3 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
              <T v="headline">{L('恢复分', 'Recovery score')}{rec.date !== last.date ? ` · ${rec.date.slice(5)}` : ''}</T>
              <Pill label={rec.label} tone={bandTone(rec.band)} />
            </View>
            <T v="caption" color={t.ink2}>{rec.notes.length ? rec.notes.join(' · ') : L('各项都在基线附近', 'Everything is near baseline')}</T>
          </View>
        </View>
      ) : null}
      <View style={{ flexDirection: 'row', alignItems: 'baseline', gap: space.sm }}>
        <Moon size={16} color={t.ink2} />
        <T v="headline" style={{ flex: 1 }}>{last.date === days[days.length - 1]?.date
          ? L(`昨晚 睡了 ${hhmm(last.sleep_min)}`, `Slept ${hhmm(last.sleep_min)} last night`)
          : L(`${last.date.slice(5)} 睡了 ${hhmm(last.sleep_min)}`, `Slept ${hhmm(last.sleep_min)} on ${last.date.slice(5)}`)}</T>
        {last.bed_start ? <T v="caption" color={t.ink3} style={{ fontVariant: ['tabular-nums'] }}>{last.bed_start}–{last.bed_end}</T> : null}
      </View>
      {last.deep_min != null ? (
        <T v="callout" color={t.ink2} style={{ fontVariant: ['tabular-nums'] }}>
          {L(`深睡 ${hhmm(last.deep_min)} · REM ${hhmm(last.rem_min)} · 核心 ${hhmm(last.core_min)} · 醒着 ${Math.round(last.awake_min ?? 0)} 分钟`,
            `Deep ${hhmm(last.deep_min)} · REM ${hhmm(last.rem_min)} · Core ${hhmm(last.core_min)} · Awake ${Math.round(last.awake_min ?? 0)} min`)}
        </T>
      ) : null}
      <View style={{ flexDirection: 'row', gap: space.md }}>
        <View style={{ flex: 1, gap: 2 }}>
          <T v="title" style={{ fontVariant: ['tabular-nums'] }}>{last.hrv_ms == null ? '—' : Math.round(last.hrv_ms)}<T v="caption" color={t.ink3}> ms</T></T>
          <T v="caption" color={t.ink3}>{L('夜间 HRV', 'Overnight HRV')}</T>
          <Versus value={last.hrv_ms} base={avg(prior.map((d) => d.hrv_ms))} unit="ms" higherIsBetter />
        </View>
        <View style={{ flex: 1, gap: 2 }}>
          <T v="title" style={{ fontVariant: ['tabular-nums'] }}>{last.rhr_bpm == null ? '—' : Math.round(last.rhr_bpm)}<T v="caption" color={t.ink3}> bpm</T></T>
          <T v="caption" color={t.ink3}>{L('静息心率', 'Resting HR')}</T>
          <Versus value={last.rhr_bpm} base={avg(prior.map((d) => d.rhr_bpm))} unit="bpm" higherIsBetter={false} />
        </View>
        <View style={{ flex: 1, gap: 2 }}>
          <T v="title" style={{ fontVariant: ['tabular-nums'] }}>{hhmm(avg(days.map((d) => d.sleep_min)))}</T>
          <T v="caption" color={t.ink3}>{L('14 天平均睡眠', '14-day avg sleep')}</T>
        </View>
      </View>
      {recDays.length ? (
        <View style={{ gap: 6 }}>
          {strip ? <ScoreStrip days={recDays} /> : null}
          <T v="caption" color={t.ink3}>{L(`近 ${recDays.length} 天恢复分。${agentName()} 自己算的：HRV 40% · 静息心率 25% · 睡眠 25% · 手腕温度 10%，各和前 14 天中位数比；昨天练得重扣 5。`, `Recovery score, last ${recDays.length} days, worked out by ${agentName()}: HRV 40% · resting HR 25% · sleep 25% · wrist temperature 10%, each against the prior 14-day median; minus 5 after a hard workout yesterday.`)}</T>
        </View>
      ) : null}
    </View>
  );
}

/** 还没有恢复数据：iPhone 上说怎么同步，别的设备上说去 iPhone 上打开。 */
export function NoRecoveryCard() {
  const t = useTheme();
  const { liveErrors } = useStore();
  const canSync = Platform.OS === 'ios';
  return (
    <Card style={{ gap: space.sm }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
        <HeartPulse size={18} color={t.ink2} />
        <T v="headline">{L('还没有恢复数据', 'No recovery data yet')}</T>
      </View>
      <T v="callout" color={t.ink2}>
        {canSync
          ? L('点右上角的「Apple 健康」同步，第一次会弹出 Apple 健康的授权页，勾上睡眠、心率变异性、静息心率。', 'Tap "Apple Health" at the top right to sync. The first time, Apple Health asks for access: turn on Sleep, Heart Rate Variability and Resting Heart Rate.')
          : L(`在 iPhone 上的 ${agentName()} app 里打开这一页，会自动同步 Apple 健康。`, `Open this page in the ${agentName()} app on your iPhone and it syncs Apple Health automatically.`)}
      </T>
      {liveErrors.health ? <T v="caption" color={t.bad}>{liveErrors.health}</T> : null}
    </Card>
  );
}

/** 恢复与睡眠（健康看板）：来源和同步时间在小标题右边，下面整块细节。 */
export function LiveRecoveryCard() {
  const { last } = useRecovery();
  return (
    <View style={{ marginBottom: space.lg }}>
      <SectionLabel right={<HealthCaption />}>{L('恢复与睡眠', 'Recovery and sleep')}</SectionLabel>
      {last ? <Card><RecoveryDetails /></Card> : <NoRecoveryCard />}
    </View>
  );
}

/**
 * 一周的缺口柱：向上是缺口（绿），向下是超出（黄），没记录画一小段灰；今天（还没过完）淡一点。
 * 这一周没有超出的日子就只画上半截。点不动，看趋势用。
 */
export function DeficitBars({ days }: { days: LiveEnergyDay[] }) {
  const t = useTheme();
  const H = 44;
  const max = Math.max(300, ...days.map((d) => Math.abs(d.deficit ?? 0)));
  const over = days.some((d) => d.deficit != null && d.deficit < 0);
  return (
    <View>
      <View style={{ flexDirection: 'row', height: over ? H * 2 + 1 : H + 1 }}>
        {days.map((d) => {
          const v = d.deficit;
          const h = v == null ? 0 : Math.max(3, Math.round((Math.abs(v) / max) * (H - 2)));
          const label = v == null ? L('没有记录', 'No data') : v >= 0 ? L(`缺口 ${Math.round(v)} kcal`, `Deficit ${Math.round(v)} kcal`) : L(`超出 ${Math.round(-v)} kcal`, `Over by ${Math.round(-v)} kcal`);
          return (
            <View key={d.date} style={{ flex: 1, alignItems: 'center' }} accessibilityLabel={`${weekdayName(d.weekday)} ${label}`}>
              <View style={{ height: H, justifyContent: 'flex-end' }}>
                {v != null && v >= 0 ? <View style={{ width: 14, height: h, backgroundColor: t.good, opacity: d.partial ? 0.45 : 1, borderTopLeftRadius: 4, borderTopRightRadius: 4 }} /> : null}
                {v == null && !over ? <View style={{ width: 14, height: 3, backgroundColor: t.track, borderRadius: 2 }} /> : null}
              </View>
              <View style={{ alignSelf: 'stretch', height: 1, backgroundColor: over ? t.line : 'transparent' }} />
              {over ? (
                <View style={{ height: H, justifyContent: 'flex-start' }}>
                  {v != null && v < 0 ? <View style={{ width: 14, height: h, backgroundColor: t.warn, opacity: d.partial ? 0.45 : 1, borderBottomLeftRadius: 4, borderBottomRightRadius: 4 }} /> : null}
                  {v == null ? <View style={{ width: 14, height: 3, backgroundColor: t.track, marginTop: 2, borderRadius: 2 }} /> : null}
                </View>
              ) : null}
            </View>
          );
        })}
      </View>
      <View style={{ flexDirection: 'row', marginTop: 4 }}>
        {days.map((d) => <T key={d.date} v="caption" color={d.partial ? t.ink : t.ink3} style={{ flex: 1, textAlign: 'center', fontSize: 11, fontWeight: d.partial ? '700' : '500' }}>{weekdayShort(d.weekday)}</T>)}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({ row: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingTop: 8 } });
const n0 = (x: number | string | null | undefined) => (x == null || x === '' ? null : Math.round(Number(x)));

/** 三餐建议卡（Agent 写进 feed_items，kind = meal_plan）。「今天」页用；饮食看板按「下一餐」拆开显示。 */
export function MealPlanCard({ plan }: { plan: MealPlan }) {
  const t = useTheme();
  const tot = plan.totals;
  return (
    <View style={{ gap: space.md }}>
      {plan.meals.map((m, i) => (
        <View key={`${m.label}-${i}`} style={{ gap: 4 }}>
          <View style={{ flexDirection: 'row', alignItems: 'baseline', gap: space.sm }}>
            <T v="headline" style={{ flex: 1 }}>{mealLabel(m.label)}{m.time ? <T v="caption" color={t.ink3}>  {m.time}</T> : null}</T>
            <T v="caption" color={t.ink3} style={{ fontVariant: ['tabular-nums'] }}>{L(`${n0(m.kcal) ?? '–'} kcal · 蛋白质 ${n0(m.protein) ?? '–'} g`, `${n0(m.kcal) ?? '–'} kcal · ${n0(m.protein) ?? '–'} g protein`)}</T>
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
          {L(`建议合计 ${n0(tot.kcal) ?? '–'} kcal · 蛋白质 ${n0(tot.protein) ?? '–'} g · 碳水 ${n0(tot.carb) ?? '–'} g · 脂肪 ${n0(tot.fat) ?? '–'} g`,
            `Suggested total ${n0(tot.kcal) ?? '–'} kcal · ${n0(tot.protein) ?? '–'} g protein · ${n0(tot.carb) ?? '–'} g carbs · ${n0(tot.fat) ?? '–'} g fat`)}
        </T>
      ) : null}
      {plan.vs_target ? <T v="caption" color={t.ink3}>{plan.vs_target}</T> : null}
      {plan.why ? <T v="caption" color={t.ink2}>{plan.why}</T> : null}
      {plan.shopping?.length ? <T v="caption" color={t.gold}>{L(`要买：${plan.shopping.join('、')}`, `To buy: ${plan.shopping.join(', ')}`)}</T> : null}
    </View>
  );
}
