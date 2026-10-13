// 「今天」页 · 报告（要阅读的）：Agent 的报告卡和邮件摘要，横向滑动的小卡片，每张一个小图。
// 点开 = useOpenReport：现在是弹层里的全文；产出小板子（output-boards 分支）合进来后换成同一块小板子。
import React, { useState } from 'react';
import { Linking, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import * as todayApi from '../../api/today';
import type { CardReport, MailReport, Report } from '../../api/today';
import type { MealPlan } from '../../data/types';
import { L } from '../../i18n';
import { openThread } from '../../navigation';
import { useStore } from '../../store';
import { radius, space, useTheme, vizMark } from '../../theme';
import { Check, ChevronRight } from '../icons';
import { GroupBadge } from '../GroupIcon';
import { MealPlanCard } from '../LiveBoards';
import { Markdown } from '../Markdown';
import { useSheet } from '../Sheet';
import { useSourceName } from '../SourceBadge';
import { T, showError } from '../ui';
import { Legend, Sparkline, seriesColor } from '../viz';
import { BlockHeader } from './parts';

const CARD_W = 250;
const CARD_H = 188;  // 饮食卡的三项图例要占两行，几张卡一样高

/** 536 → 8 小时 56 分 */
const duration = (min: number) => {
  const h = Math.floor(min / 60), m = Math.round(min % 60);
  return L(`${h} 小时${m ? ` ${m} 分` : ''}`, `${h}h${m ? ` ${m}m` : ''}`);
};
const md = (iso: string) => `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))}`;

export function ReportsBlock({ reports }: { reports: Report[] }) {
  const open = useOpenReport();
  const [seen, setSeen] = useState<Record<string, true>>({});
  if (!reports.length) return null;
  return (
    <View>
      <BlockHeader title={L(`报告 · ${reports.length}`, `Reports · ${reports.length}`)} />
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.strip} contentContainerStyle={styles.stripIn}>
        {reports.map((r) => {
          const fresh = r.type === 'card' && r.unseen && !seen[r.id];
          const onPress = () => { open(r); if (r.type === 'card') setSeen((m) => ({ ...m, [r.id]: true })); };
          return r.type === 'mail' ? <MailCard key={r.id} r={r} onPress={onPress} />
            : r.sleep ? <SleepCard key={r.id} r={r} fresh={fresh} onPress={onPress} />
              : r.meal ? <MealCard key={r.id} r={r} fresh={fresh} onPress={onPress} />
                : <PlainCard key={r.id} r={r} fresh={fresh} onPress={onPress} />;
        })}
      </ScrollView>
    </View>
  );
}

function Shell({ children, onPress, label }: { children: React.ReactNode; onPress: () => void; label: string }) {
  const t = useTheme();
  return (
    <Pressable onPress={onPress} accessibilityRole="button" accessibilityLabel={label}
      style={({ pressed }) => [styles.card, { backgroundColor: t.surface, opacity: pressed ? 0.8 : 1 }]}>
      {children}
    </Pressable>
  );
}

/** 卡片顶上一行：哪个 Agent、几点；没看过的时间前面一个青点。 */
function Head({ r, fresh, label }: { r: CardReport; fresh: boolean; label?: string }) {
  const t = useTheme();
  const { groups } = useStore();
  const name = useSourceName();
  const g = groups.find((x) => x.id === r.agent);
  return (
    <View style={styles.head}>
      {g ? <GroupBadge icon={g.icon} color={g.color} size={22} /> : null}
      <T v="caption" color={t.ink2} style={{ flex: 1, fontWeight: '400' }} numberOfLines={1}>{label ?? name(r.agent)}</T>
      <View style={styles.when}>
        {fresh ? <View style={[styles.newDot, { backgroundColor: t.cyan }]} accessible accessibilityLabel={L('未读', 'New')} /> : null}
        <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{r.time}</T>
      </View>
    </View>
  );
}

function SleepCard({ r, fresh, onPress }: { r: CardReport; fresh: boolean; onPress: () => void }) {
  const t = useTheme();
  const s = r.sleep;
  const vals = (s?.series ?? []).map((x) => x.score);
  const has = s?.score != null;
  const label = has ? L(`恢复分 ${s?.score}`, `Recovery ${s?.score}`) : r.title;
  return (
    <Shell onPress={onPress} label={label}>
      <Head r={r} fresh={fresh} />
      {has ? (
        <View style={styles.big}>
          <Text style={[styles.bigNum, { color: t.ink }]}>{s?.score}</Text>
          <T v="callout" color={t.ink2} style={{ flex: 1, fontSize: 13 }} numberOfLines={1}>
            {[L('恢复分', 'recovery'), s?.sleepMin ? L(`睡眠 ${duration(s.sleepMin)}`, `slept ${duration(s.sleepMin)}`) : ''].filter(Boolean).join(' · ')}
          </T>
        </View>
      ) : <T v="headline" numberOfLines={2} style={{ fontSize: 15 }}>{r.title}</T>}
      {vals.filter((v) => v != null).length >= 2 ? (
        <Sparkline values={vals} height={44} min label={L(`近 ${vals.length} 天恢复分：${vals.map((v) => v ?? '无').join('、')}`, `Recovery, last ${vals.length} days: ${vals.map((v) => v ?? 'none').join(', ')}`)} />
      ) : null}
      {s?.low ? <T v="caption" color={t.ink3} style={{ fontWeight: '400', fontSize: 11 }} numberOfLines={1}>
        {L(`近 ${vals.length} 天恢复分，最低 ${s.low.score}（${md(s.low.date)}）`, `Last ${vals.length} days, lowest ${s.low.score} (${md(s.low.date)})`)}
      </T> : null}
    </Shell>
  );
}

function MealCard({ r, fresh, onPress }: { r: CardReport; fresh: boolean; onPress: () => void }) {
  const t = useTheme();
  const m = r.meal;
  const tot = m?.totals;
  const kcal = { p: (tot?.protein ?? 0) * 4, c: (tot?.carb ?? 0) * 4, f: (tot?.fat ?? 0) * 9 };
  const sum = kcal.p + kcal.c + kcal.f;
  const parts = sum > 0 ? [
    { label: L('蛋白质', 'Protein'), v: kcal.p, slot: 0 }, { label: L('碳水', 'Carbs'), v: kcal.c, slot: 1 }, { label: L('脂肪', 'Fat'), v: kcal.f, slot: 2 },
  ].map((x) => ({ ...x, pct: Math.round((x.v / sum) * 100) })) : [];
  const logged = m?.logged;
  const foot = [tot?.kcal ? `${tot.kcal} kcal` : '', logged ? (logged.count ? L(`今日已记录 ${logged.count} 项`, `${logged.count} logged today`) : L('今日尚未记录饮食', 'Nothing logged today')) : ''].filter(Boolean).join(' · ');
  return (
    <Shell onPress={onPress} label={`${m?.heading ?? r.title}，${m?.dishes ?? ''}`}>
      <Head r={r} fresh={fresh} />
      <T v="headline">{m?.heading ?? r.title}</T>
      <T v="callout" numberOfLines={2}>{m?.dishes || r.title}</T>
      <View style={{ flex: 1 }} />
      {parts.length ? (
        <View style={{ gap: 5 }} accessible accessibilityLabel={L(`按热量：${parts.map((p) => `${p.label} ${p.pct}%`).join('、')}`, `By calories: ${parts.map((p) => `${p.label} ${p.pct}%`).join(', ')}`)}>
          {/* 按热量的三大营养素：一条百分比条（段间 2px，两头圆角）+ 图例 */}
          <View style={styles.split}>
            {parts.map((p, i) => (
              <View key={p.label} style={{ flex: Math.max(p.v, 1), height: 6, backgroundColor: seriesColor(t, p.slot),
                borderTopLeftRadius: i === 0 ? vizMark.radius : 0, borderBottomLeftRadius: i === 0 ? vizMark.radius : 0,
                borderTopRightRadius: i === parts.length - 1 ? vizMark.radius : 0, borderBottomRightRadius: i === parts.length - 1 ? vizMark.radius : 0 }} />
            ))}
          </View>
          <Legend items={parts.map((p) => ({ label: p.label, slot: p.slot, value: `${p.pct}%` }))} style={{ columnGap: 10, rowGap: 0 }} />
        </View>
      ) : null}
      {foot ? <T v="caption" color={t.ink3} style={{ fontWeight: '400' }} numberOfLines={1}>{foot}</T> : null}
    </Shell>
  );
}

function MailCard({ r, onPress }: { r: MailReport; onPress: () => void }) {
  const t = useTheme();
  return (
    <Shell onPress={onPress} label={L(`邮件摘要，${r.count} 条状态更新`, `Email digest, ${r.count} updates`)}>
      <T v="caption" color={t.ink2} style={{ fontWeight: '400' }}>{r.title}</T>
      <View style={styles.big}>
        <Text style={[styles.bigNum, { color: t.ink }]}>{r.count}</Text>
        <T v="callout" color={t.ink2} style={{ fontSize: 13 }}>{L('条状态更新', r.count === 1 ? 'update' : 'updates')}</T>
      </View>
      <View style={styles.chips}>
        {r.categories.map((c) => (
          <View key={c.key} style={[styles.cat, { backgroundColor: t.surface2 }]}>
            <Text style={[styles.catText, { color: t.ink2 }]}>{`${c.label} ${c.count}`}</Text>
          </View>
        ))}
      </View>
    </Shell>
  );
}

function PlainCard({ r, fresh, onPress }: { r: CardReport; fresh: boolean; onPress: () => void }) {
  const t = useTheme();
  const line = r.body.replace(/[#*>`_-]/g, '').split('\n').map((x) => x.trim()).find(Boolean) ?? '';
  return (
    <Shell onPress={onPress} label={r.title}>
      <Head r={r} fresh={fresh} />
      <T v="headline" numberOfLines={2}>{r.title}</T>
      {line ? <T v="callout" color={t.ink2} numberOfLines={3}>{line}</T> : null}
    </Shell>
  );
}

// —— 点开 ————————————————————————————————————————————————————————————

/** 点开一份报告。产出小板子（output-boards 分支）合进来后，卡片类的换成它的小板子；邮件摘要留在这里。 */
export function useOpenReport() {
  const sheet = useSheet();
  const { markFeedSeen } = useStore();
  const name = useSourceName();
  return (r: Report) => {
    if (r.type === 'mail') {
      sheet.open({ title: r.title, content: () => <MailDigest r={r} /> });
      return;
    }
    if (r.unseen) markFeedSeen([r.id]);
    sheet.open({ title: r.title, content: (close) => <ReportBody r={r} agent={name(r.agent)} close={close} /> });
  };
}

function ReportBody({ r, agent, close }: { r: CardReport; agent: string; close: () => void }) {
  const t = useTheme();
  const plan = r.kind === 'meal_plan' && r.data && typeof r.data === 'object' && 'meals' in r.data ? (r.data as MealPlan) : null;
  return (
    <View style={{ gap: space.md }}>
      <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{[agent, r.time].filter(Boolean).join(' · ')}</T>
      {r.sleep?.score != null ? (
        <View style={[styles.box, { backgroundColor: t.surface }]}>
          <View style={styles.big}>
            <Text style={[styles.bigNum, { color: t.ink }]}>{r.sleep.score}</Text>
            <T v="callout" color={t.ink2}>{[L('恢复分', 'recovery'), r.sleep.label, r.sleep.sleepMin ? L(`睡眠 ${duration(r.sleep.sleepMin)}`, `slept ${duration(r.sleep.sleepMin)}`) : ''].filter(Boolean).join(' · ')}</T>
          </View>
        </View>
      ) : null}
      {plan ? <MealPlanCard plan={plan} /> : <Markdown text={r.body} color={t.ink} compact />}
      {r.agent ? (
        <Pressable onPress={() => { close(); openThread(r.agent as string, true, { title: r.title }); }} accessibilityRole="button" hitSlop={6} style={styles.linkRow}>
          <T v="callout" color={t.gold} style={{ fontWeight: '600', flex: 1 }}>{L(`在${agent}中讨论`, `Discuss in ${agent}`)}</T>
          <ChevronRight size={16} color={t.gold} />
        </Pressable>
      ) : null}
    </View>
  );
}

/** 邮件摘要：按类别列出，点一条看详情和原文；勾掉 = 看过了，不再出现在摘要里。 */
function MailDigest({ r }: { r: MailReport }) {
  const t = useTheme();
  const [open, setOpen] = useState<string | null>(null);
  const [done, setDone] = useState<Record<string, boolean>>({});
  const toggle = async (ref: string, title: string, date: string | null, time: string) => {
    const on = !done[ref];
    setDone((m) => ({ ...m, [ref]: on }));
    try { await todayApi.tick({ ref, title, date, time }, on); } catch (e) {
      setDone((m) => ({ ...m, [ref]: !on }));
      showError(L('标记失败', "Couldn't mark it"), e);
    }
  };
  return (
    <View style={{ gap: space.md }}>
      {r.categories.map((c) => (
        <View key={c.key} style={{ gap: 6 }}>
          <T v="caption" color={t.ink3}>{`${c.label} · ${c.count}`}</T>
          <View style={[styles.list, { backgroundColor: t.surface }]}>
            {r.items.filter((x) => x.category === c.key).map((x, i, all) => (
              <View key={x.ref} style={[styles.mailRow, i < all.length - 1 && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }]}>
                <Pressable onPress={() => toggle(x.ref, x.title, x.date, x.time)} hitSlop={10} accessibilityRole="checkbox" accessibilityState={{ checked: !!done[x.ref] }}
                  accessibilityLabel={L(`标记已读：${x.title}`, `Mark as read: ${x.title}`)}
                  style={[styles.tick, { borderColor: done[x.ref] ? t.cyan : t.ink3, backgroundColor: done[x.ref] ? t.cyan : 'transparent' }]}>
                  {done[x.ref] ? <Check size={13} color={t.surface} strokeWidth={3} /> : null}
                </Pressable>
                <Pressable onPress={() => setOpen((v) => (v === x.ref ? null : x.ref))} accessibilityRole="button" accessibilityState={{ expanded: open === x.ref }} style={{ flex: 1, gap: 3 }}>
                  <T v="callout" color={done[x.ref] ? t.ink3 : t.ink} style={done[x.ref] ? { textDecorationLine: 'line-through' } : undefined}>{x.title}</T>
                  {x.date ? <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{[md(x.date), x.time].filter(Boolean).join(' ')}</T> : null}
                  {open === x.ref ? (
                    <View style={{ gap: 6, marginTop: 4 }}>
                      {x.detail ? <T v="callout" color={t.ink2}>{x.detail}</T> : null}
                      {x.link ? (
                        <Pressable onPress={() => { Linking.openURL(x.link as string).catch((e) => showError(L('无法打开', "Couldn't open"), e)); }} accessibilityRole="link" hitSlop={6}>
                          <T v="callout" color={t.gold} style={{ fontWeight: '600' }}>{L('查看邮件原文', 'Open the email')}</T>
                        </Pressable>
                      ) : null}
                    </View>
                  ) : null}
                </Pressable>
              </View>
            ))}
          </View>
        </View>
      ))}
      <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L('勾选的条目不再出现在邮件摘要中。', 'Ticked items leave the digest.')}</T>
    </View>
  );
}


const styles = StyleSheet.create({
  strip: { marginHorizontal: -space.lg },
  stripIn: { paddingHorizontal: space.lg, gap: 10 },
  card: { width: CARD_W, height: CARD_H, borderRadius: 16, paddingHorizontal: 14, paddingVertical: 12, gap: 6 },
  head: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  when: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  newDot: { width: 7, height: 7, borderRadius: 4 },
  big: { flexDirection: 'row', alignItems: 'baseline', gap: 6 },
  bigNum: { fontSize: 28, fontWeight: '700', fontVariant: ['tabular-nums'] },
  split: { flexDirection: 'row', gap: vizMark.gap },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  cat: { borderRadius: 7, paddingHorizontal: 8, paddingVertical: 3 },
  catText: { fontSize: 12 },
  box: { borderRadius: radius.md, padding: 12 },
  list: { borderRadius: radius.md, paddingHorizontal: 12 },
  mailRow: { flexDirection: 'row', gap: 10, paddingVertical: 11, alignItems: 'flex-start' },
  tick: { width: 20, height: 20, borderRadius: 10, borderWidth: 1.5, alignItems: 'center', justifyContent: 'center', marginTop: 1 },
  linkRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 10, gap: 6 },
});
