// 饮食看板，按你想知道的顺序排：今天到哪了 → 下一餐 → 今天吃了 → 这周 → 要买。
// 「下一餐」只放建议卡（meal_plan）里还没吃的；吃过的只在「今天吃了」出现一次（来自饮食记录）。
// 「吃了，记上」是你自己点的，发一句明确的话给饮食 Agent，它直接记，不再问。
import React, { useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';
import { agentName } from '../brand';
import type { LiveDiet, LiveEnergy, LiveMeal } from '../api/live';
import type { MealPlan, MealPlanMeal } from '../data/types';
import { L, lang } from '../i18n';
import { useStore } from '../store';
import { space, type, useTheme } from '../theme';
import { Bar } from './charts';
import { Check, Flame, Sparkles } from './icons';
import { Caption, DeficitBars, kcal, localDate, mealLabel } from './LiveBoards';
import { Btn, Card, Disclosure, SectionLabel, T } from './ui';

const MINUS = '−';
const n0 = (x: number | string | null | undefined) => (x == null || x === '' ? null : Math.round(Number(x)));
const amountOf = (amount: number | string | null | undefined, unit?: string | null) => (amount != null && amount !== '' ? `${amount} ${unit ?? 'g'}` : '');

/** 看板上的按钮：金色的是主要动作，灰的是次要的；Agent 正在回的时候都点不了。 */
function ActionBtn({ label, kind, icon, disabled, busy, onPress, grow }: { label: string; kind: 'primary' | 'quiet'; icon?: React.ReactNode; disabled?: boolean; busy?: boolean; onPress: () => void; grow?: boolean }) {
  const t = useTheme();
  const primary = kind === 'primary';
  const fg = primary ? t.onGold : t.ink;
  return (
    <Pressable onPress={onPress} disabled={disabled} accessibilityRole="button" accessibilityLabel={label} accessibilityState={{ disabled: !!disabled, busy: !!busy }}
      style={({ pressed }) => [styles.btn, { backgroundColor: primary ? t.goldFill : t.surface2, opacity: disabled && !busy ? 0.5 : pressed ? 0.75 : 1 }, grow && { flexGrow: 1, flexShrink: 1 }]}>
      {busy ? <ActivityIndicator size="small" color={fg} /> : icon}
      <Text numberOfLines={1} style={[type.headline, { fontSize: 15, color: fg, flexShrink: 1 }]}>{label}</Text>
    </Pressable>
  );
}

/** 一样食物：名字、份量、热量。 */
function FoodRow({ name, amount, kcalText }: { name: string; amount: string; kcalText: string }) {
  const t = useTheme();
  return (
    <View style={[styles.food, { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }]}>
      <T v="callout" numberOfLines={1} style={{ flex: 1, fontSize: 15 }}>{name}</T>
      <T v="caption" color={t.ink3} style={{ minWidth: 56, textAlign: 'right', fontVariant: ['tabular-nums'], fontWeight: '400' }}>{amount}</T>
      <T v="callout" color={t.ink2} style={{ width: 70, textAlign: 'right', fontVariant: ['tabular-nums'] }}>{kcalText}</T>
    </View>
  );
}

/** 一餐收成一行（餐名、时间、热量 · 蛋白质），点开看每样吃了什么。 */
function MealRow({ label, time, kcalN, protein, items, first }: { label: string; time?: string | null; kcalN: number | null; protein: number | null; items: { name: string; amount: string; kcal: number | null }[]; first?: boolean }) {
  const t = useTheme();
  const [open, setOpen] = useState(false);
  return (
    <View style={[styles.mealRow, !first && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }]}>
      <Pressable onPress={() => setOpen((v) => !v)} accessibilityRole="button" accessibilityState={{ expanded: open }} style={({ pressed }) => [styles.rowC, { opacity: pressed ? 0.6 : 1 }]}>
        <T v="body" style={{ fontWeight: '600' }}>{mealLabel(label)}</T>
        {time ? <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{time}</T> : null}
        <View style={{ flex: 1 }} />
        <T v="callout" color={t.ink2} style={{ fontVariant: ['tabular-nums'] }}>{L(`${kcal(kcalN)} kcal · 蛋白 ${protein ?? '–'} g`, `${kcal(kcalN)} kcal · ${protein ?? '–'} g protein`)}</T>
        <Disclosure open={open} />
      </Pressable>
      {open ? (
        <View style={{ marginTop: 4 }}>
          {items.map((it, k) => <FoodRow key={`${it.name}-${k}`} name={it.name} amount={it.amount} kcalText={`${it.kcal ?? '–'} kcal`} />)}
        </View>
      ) : null}
    </View>
  );
}

/** 今天到哪了：摄入对目标（有目标才画进度条，没目标只写摄入），加一行热量缺口。 */
function TodayCard({ diet, energy, energyError }: { diet: LiveDiet; energy: LiveEnergy | null; energyError?: string }) {
  const t = useTheme();
  const { totals, targets } = diet;
  const today = energy?.days[energy.days.length - 1];
  const hm = energy?.synced_at ? energy.synced_at.slice(11, 16) : '';
  const macros = [
    { label: L('蛋白', 'Protein'), v: totals.protein, target: targets?.protein },
    { label: L('碳水', 'Carbs'), v: totals.carb, target: targets?.carb },
    { label: L('脂肪', 'Fat'), v: totals.fat, target: targets?.fat },
  ];
  const left = targets ? targets.kcal - totals.kcal : null;
  const burned = today?.partial && hm ? L(`手表算到 ${hm} 消耗 ${kcal(today?.burned)}`, `watch: ${kcal(today?.burned)} burned by ${hm}`) : L(`手表算的消耗 ${kcal(today?.burned)}`, `watch: ${kcal(today?.burned)} burned`);
  return (
    <Card style={{ gap: space.md }}>
      <View style={[styles.rowC, { alignItems: 'baseline', gap: 6 }]}>
        <T v="largeTitle" style={styles.big}>{kcal(totals.kcal)}</T>
        <T v="callout" color={t.ink2} style={{ fontSize: 15 }}>{targets ? `/ ${kcal(targets.kcal)} kcal` : 'kcal'}</T>
        <View style={{ flex: 1 }} />
        {left != null ? <T v="caption" color={left >= 0 ? t.ink3 : t.warn} style={{ fontWeight: left >= 0 ? '400' : '600' }}>{left >= 0 ? L(`还剩 ${kcal(left)}`, `${kcal(left)} left`) : L(`超了 ${kcal(-left)}`, `${kcal(-left)} over`)}</T> : null}
      </View>
      {targets ? <Bar value={totals.kcal} target={targets.kcal} height={10} /> : null}
      {targets ? (
        <View style={{ gap: 9 }}>
          {macros.map((m) => (
            <View key={m.label} style={styles.rowC} accessible accessibilityLabel={`${m.label} ${m.v} / ${m.target ?? '—'} g`}>
              <T v="callout" color={t.ink2} style={{ width: lang() === 'zh' ? 36 : 58 }}>{m.label}</T>
              <View style={{ flex: 1 }}>{m.target ? <Bar value={m.v} target={m.target} /> : null}</View>
              <T v="callout" style={{ minWidth: 84, textAlign: 'right', fontVariant: ['tabular-nums'] }}><T v="callout" style={{ fontWeight: '700' }}>{m.v}</T>{m.target ? ` / ${m.target} g` : ' g'}</T>
            </View>
          ))}
        </View>
      ) : (
        <View style={{ flexDirection: 'row', gap: space.sm }}>
          {macros.map((m) => (
            <View key={m.label} style={{ flex: 1, gap: 2 }}>
              <T v="headline" style={{ fontVariant: ['tabular-nums'] }}>{m.v}<T v="caption" color={t.ink3}> g</T></T>
              <T v="caption" color={t.ink2} style={{ fontWeight: '400' }}>{m.label}</T>
            </View>
          ))}
        </View>
      )}
      <View style={[styles.rowC, { alignItems: 'flex-start' }]}>
        <View style={{ marginTop: 2 }}><Flame size={16} color={today?.deficit != null && today.deficit < 0 ? t.warn : t.good} /></View>
        <T v="callout" color={t.ink2} style={{ flex: 1 }}>
          {today?.deficit != null ? (
            <>
              {today.deficit >= 0 ? L('热量缺口 ', 'Deficit ') : L('热量超出 ', 'Over by ')}
              <T v="callout" color={today.deficit >= 0 ? t.good : t.warn} style={{ fontWeight: '700', fontVariant: ['tabular-nums'] }}>{today.deficit >= 0 ? MINUS : '+'}{kcal(Math.abs(today.deficit))}</T>
              {` kcal · ${burned}`}
            </>
          ) : today?.burned != null ? `${burned} kcal${L('，还算不了缺口', ", can't work out the deficit yet")}`
            : energyError ? L(`消耗数据没读到：${energyError}`, `Couldn't read calories burned: ${energyError}`)
              : L('Apple 健康还没同步，算不了消耗。在 iPhone 上打开健身看板同步一次。', "Apple Health hasn't synced yet, so calories burned can't be worked out. Open the fitness dashboard on your iPhone once to sync.")}
        </T>
      </View>
      {targets?.kcal_derived ? <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L(`热量目标是按三大营养素目标换算的（${diet.source || '饮食记录'}不存热量目标）。`, `The calorie target is worked out from the macro targets (${diet.source || 'the meal log'} doesn't store one).`)}</T> : null}
      {!targets ? <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L(`还没设每日目标，所以只显示摄入量。告诉 ${agentName()} 你的热量和三大营养素目标，这里就会变成进度条。`, `No daily targets yet, so only intake is shown. Tell ${agentName()} your calorie and macro targets and this turns into progress bars.`)}</T> : null}
    </Card>
  );
}

/** 下一餐：建议卡里第一顿还没吃的，整张摊开；再往后的收成一行。 */
function NextMeal({ meal, later, plan, busy, onSwap, onAte }: { meal: MealPlanMeal; later: MealPlanMeal[]; plan: MealPlan; busy: boolean; onSwap: () => void; onAte: () => void }) {
  const t = useTheme();
  const notes = [meal.note, plan.why].filter((x): x is string => !!x);
  return (
    <Card style={{ gap: 10 }}>
      <View style={[styles.rowC, { alignItems: 'baseline' }]}>
        <T v="headline" style={{ fontSize: 18, fontWeight: '700' }}>{mealLabel(meal.label)}{meal.time ? ` · ${meal.time}` : ''}</T>
        <View style={{ flex: 1 }} />
        <T v="caption" color={t.ink3} style={{ fontVariant: ['tabular-nums'], fontWeight: '400' }}>{L(`${n0(meal.kcal) ?? '–'} kcal · 蛋白 ${n0(meal.protein) ?? '–'} g`, `${n0(meal.kcal) ?? '–'} kcal · ${n0(meal.protein) ?? '–'} g protein`)}</T>
      </View>
      {meal.items.length ? (
        <View>
          {meal.items.map((it, k) => <FoodRow key={`${it.name}-${k}`} name={it.name} amount={amountOf(it.amount, it.unit)} kcalText={`${n0(it.kcal) ?? '–'} kcal`} />)}
        </View>
      ) : null}
      {notes.map((x, i) => <T key={`${i}-${x}`} v="callout" color={t.ink2}>{x}</T>)}
      <View style={[styles.rowC, { marginTop: 2 }]}>
        <ActionBtn kind="quiet" label={L('换一个', 'Swap it')} disabled={busy} onPress={onSwap} />
        <ActionBtn kind="primary" grow label={busy ? L(`${agentName()} 正在回…`, `${agentName()} is replying…`) : L('吃了，记上', 'Ate it, log it')} icon={<Check size={18} color={t.onGold} />} disabled={busy} busy={busy} onPress={onAte} />
      </View>
      {later.length ? (
        <View style={{ marginTop: 4 }}>
          <T v="caption" color={t.ink3} style={{ fontWeight: '600', marginBottom: 2 }}>{L('之后', 'Later')}</T>
          {later.map((m, i) => (
            <MealRow key={`${m.label}-${i}`} first={i === 0} label={m.label} time={m.time} kcalN={n0(m.kcal)} protein={n0(m.protein)}
              items={m.items.map((it) => ({ name: it.name, amount: amountOf(it.amount, it.unit), kcal: n0(it.kcal) }))} />
          ))}
        </View>
      ) : null}
    </Card>
  );
}

const eatenMealItems = (m: LiveMeal) => m.items.map((it) => ({ name: it.name, amount: amountOf(it.amount, it.unit), kcal: it.kcal == null ? null : Math.round(it.kcal) }));

/** 饮食看板。 */
export function DietBoard({ diet, energy, energyError, groupId, onAsk }: { diet: LiveDiet; energy: LiveEnergy | null; energyError?: string; groupId: string; onAsk: () => void }) {
  const t = useTheme();
  const { feed, send, typing, live } = useStore();
  const today = localDate();
  const card = feed.filter((f) => f.groupId === groupId && f.kind === 'meal_plan' && !!f.data && 'meals' in f.data && (f.createdAt ?? '').slice(0, 10) === today)
    .sort((a, b) => (b.createdAt ?? '').localeCompare(a.createdAt ?? ''))[0];
  const plan = card?.data && 'meals' in card.data ? card.data : null;
  // 吃过的：饮食记录里已经有这一餐，或者建议卡自己写了「已记」。只是「已吃、等确认」的还留在下一餐，点「吃了，记上」就记。
  const logged = new Set(diet.meals.map((m) => m.label));
  const eaten = (m: MealPlanMeal) => logged.has(m.label) || /已记|已经记|logged/i.test(m.note ?? '');
  const upcoming = plan ? plan.meals.filter((m) => !eaten(m)) : [];
  const busy = !!typing[groupId];
  const say = (text: string) => { send(groupId, text); onAsk(); };
  const ask = () => say(L('出今天的三餐建议', "Plan today's meals"));
  const source = diet.source || L('饮食记录', 'Meal log');
  const s = energy?.summary;
  const shopping = (plan?.shopping ?? []).filter(Boolean);
  return (
    <View>
      <SectionLabel right={<Caption>{`${source} · ${live?.loadedAt ?? ''}`}</Caption>}>{L('今天到哪了', 'Today so far')}</SectionLabel>
      <TodayCard diet={diet} energy={energy} energyError={energyError} />

      <SectionLabel right={card ? <Caption>{L(`${card.createdAt?.slice(11, 16) ?? ''} 排的`, `Planned ${card.createdAt?.slice(11, 16) ?? ''}`)}</Caption> : undefined}>{L('下一餐', 'Next meal')}</SectionLabel>
      {plan && upcoming.length ? (
        <NextMeal meal={upcoming[0]} later={upcoming.slice(1)} plan={plan} busy={busy}
          onSwap={() => say(L(`换一个${mealLabel(upcoming[0].label)}方案`, `Suggest a different ${mealLabel(upcoming[0].label).toLowerCase()}`))}
          onAte={() => say(L(`吃了，按建议记上：${mealLabel(upcoming[0].label)}`, `Ate it. Log it as suggested: ${mealLabel(upcoming[0].label)}`))} />
      ) : (
        <Card style={{ gap: space.sm }}>
          <T v="callout" color={t.ink2}>{plan
            ? L('今天建议的几餐都吃过了。', "You've had all the meals in today's plan.")
            : L(`今天还没有三餐建议。${agentName()} 会按你的固定早餐、常买清单、今天练不练和还差的热量来配。`, `No meal plan for today yet. ${agentName()} builds it from your usual breakfast, your regular shopping list, whether you train today and the calories you still need.`)}</T>
          <Btn label={busy ? L(`${agentName()} 正在出…`, `${agentName()} is on it…`) : plan ? L('再出一份', 'Make a new one') : L(`让 ${agentName()} 出今天的建议`, `Ask ${agentName()} for today's plan`)}
            kind={plan ? 'quiet' : 'primary'} onPress={ask} icon={<Sparkles size={14} color={plan ? t.ink : t.onGold} />} />
        </Card>
      )}

      <SectionLabel right={diet.meals.length ? <Caption>{L(`${diet.meals.length} 餐`, `${diet.meals.length} meal${diet.meals.length === 1 ? '' : 's'}`)}</Caption> : undefined}>{L('今天吃了', 'Eaten today')}</SectionLabel>
      {diet.meals.length ? (
        <Card style={{ paddingVertical: space.xs }}>
          {diet.meals.map((m, i) => <MealRow key={`${m.label}-${i}`} first={i === 0} label={m.label} kcalN={m.kcal} protein={m.protein} items={eatenMealItems(m)} />)}
        </Card>
      ) : <Card><T v="callout" color={t.ink2}>{L('今天还没有饮食记录。', 'No meals logged today.')}</T></Card>}

      <SectionLabel right={<Caption>{`${L('Apple 健康', 'Apple Health')} + ${source}`}</Caption>}>{L('这周', 'This week')}</SectionLabel>
      {energy && s ? (
        <Card style={{ gap: space.md }}>
          <DeficitBars days={energy.days} />
          <T v="callout" color={t.ink2}>{s.days_counted
            ? L(`记了 ${s.days_counted} 天：平均每天${(s.avg_deficit ?? 0) >= 0 ? '缺口' : '超出'} ${kcal(Math.abs(s.avg_deficit ?? 0))} kcal${s.est_fat_kg != null ? `，约 ${s.est_fat_kg >= 0 ? MINUS : '+'}${Math.abs(s.est_fat_kg).toFixed(2)} kg 脂肪` : ''}。没记的日子不算。`,
              `${s.days_counted} day${s.days_counted === 1 ? '' : 's'} logged: an average ${(s.avg_deficit ?? 0) >= 0 ? 'deficit' : 'surplus'} of ${kcal(Math.abs(s.avg_deficit ?? 0))} kcal a day${s.est_fat_kg != null ? `, about ${s.est_fat_kg >= 0 ? MINUS : '+'}${Math.abs(s.est_fat_kg).toFixed(2)} kg of fat` : ''}. Days without a log don't count.`)
            : L('近 7 天还没有完整的饮食记录，算不了缺口。', "No complete meal logs in the last 7 days, so the deficit can't be worked out.")}</T>
          {energy.intake_error ? <T v="caption" color={t.bad} style={{ fontWeight: '400' }}>{L(`摄入数据读取失败：${energy.intake_error}`, `Couldn't read intake data: ${energy.intake_error}`)}</T> : null}
        </Card>
      ) : (
        <Card><T v="callout" color={t.ink2}>{energyError ? L(`消耗数据没读到：${energyError}`, `Couldn't read calories burned: ${energyError}`) : L('Apple 健康还没同步，算不了消耗。在 iPhone 上打开健身看板同步一次。', "Apple Health hasn't synced yet, so calories burned can't be worked out. Open the fitness dashboard on your iPhone once to sync.")}</T></Card>
      )}

      {shopping.length ? (
        <>
          <SectionLabel>{L('要买', 'To buy')}</SectionLabel>
          <View style={styles.chips}>
            {shopping.map((x, i) => (
              <View key={`${i}-${x}`} style={[styles.chip, { backgroundColor: t.surface, borderColor: t.line }]}>
                <T v="caption" style={{ fontSize: 13, fontWeight: '600' }}>{x}</T>
              </View>
            ))}
          </View>
        </>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  rowC: { flexDirection: 'row', alignItems: 'center', gap: space.sm },
  big: { fontSize: 32, fontWeight: '800', letterSpacing: -0.3, fontVariant: ['tabular-nums'] },
  food: { flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingVertical: 9 },
  mealRow: { paddingVertical: 12 },
  btn: { flexDirection: 'row', gap: 6, alignItems: 'center', justifyContent: 'center', borderRadius: 12, paddingHorizontal: 18, height: 44 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: space.sm },
  chip: { minHeight: 28, borderRadius: 14, borderWidth: StyleSheet.hairlineWidth, paddingHorizontal: 11, paddingVertical: 4, justifyContent: 'center' },
});
