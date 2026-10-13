// 小板子（2026-10-13）：一份产出点开后的半屏板子，按 kind 套模板（练后报告、饮食建议、睡眠报告、训练计划；别的只有正文）。
// 浮动标签、对话里的产出卡片、今天页的报告点开都是它（useOutputBoard）。要画的数服务器都算好了（server/outputs.py），这里只画。
// 用 Sheet（自己画的底部弹层）：内容在 NavigationContainer 外面，跳转走 navigationRef / openThread。
import React, { useCallback, useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import { dietLog, type MealBoard, type Output, type PlanBoard, type ReviewBoard, type SleepBoard, type Trend, outputsApi, dropOutputs } from '../../api/outputs';
import { supports } from '../../api/version';
import { L } from '../../i18n';
import { navigationRef, openThread } from '../../navigation';
import { useStore } from '../../store';
import { radius, space, useTheme } from '../../theme';
import { CircleAlert, CircleCheck } from '../icons';
import { mealLabel } from '../LiveBoards';
import { Markdown } from '../Markdown';
import { useSheet } from '../Sheet';
import { SourceBadge, useSourceName } from '../SourceBadge';
import { Btn, Disclosure, Pill, showError, T } from '../ui';
import { BeforeAfter, ChangeTag, CompareList, NutritionRing, Sparkline, TargetBar, type Direction } from '../viz';

const dirOf = (tr: Trend | null | undefined): Direction => (tr === 'up' ? 'up' : tr === 'down' ? 'down' : 'flat');
/** 变化标签的颜色：变好绿、变差红、持平和新动作灰（辅助减少也是变好，服务器给的 trend 已经是 up）。 */
export const goodOf = (tr: Trend | null | undefined) => (tr === 'up' ? true : tr === 'down' ? false : null);

/** Agent 的圆头像（Agent 的图标和颜色；主对话是助手自己的头像）。 */
export function OutputAvatar({ agent, size }: { agent: string | null; size: number }) {
  return (
    <View style={{ width: size, height: size, borderRadius: size / 2, overflow: 'hidden' }}>
      <SourceBadge source={agent ?? 'main'} size={size} />
    </View>
  );
}

/** 小板子打开时：标看过（任何地方打开都算）、从浮动标签里拿掉，再升起弹层。传 id 时先读那一份。 */
export function useOutputBoard() {
  const sheet = useSheet();
  const { markFeedSeen } = useStore();
  return useCallback((o: Output | string) => {
    const show = (x: Output) => {
      if (!x.seen) markFeedSeen([x.id]);
      dropOutputs([x.id]);
      sheet.open({
        title: x.label,
        header: <BoardHeader o={x} />,
        content: () => <BoardBody o={x} />,
        footer: (close) => <BoardActions o={x} close={close} />,
        tall: true,
      });
    };
    if (typeof o !== 'string') { show(o); return; }
    outputsApi.get(o).then(show).catch((e) => showError(L('无法加载', "Couldn't load"), e));
  }, [sheet, markFeedSeen]);
}

function BoardHeader({ o }: { o: Output }) {
  const t = useTheme();
  const name = useSourceName();
  const b = o.board;
  const pill = b.template === 'training_review' && b.recovery != null ? <Pill label={L(`恢复分 ${b.recovery}`, `Recovery ${b.recovery}`)} />
    : b.template === 'meal_plan' && b.tag ? <Pill label={b.tag} tone="warn" lines={1} />
    : b.template === 'training_plan' && b.decision ? <Pill label={b.decision} tone={b.tone === 'neutral' ? 'neutral' : b.tone} />
    : o.replaced ? <Pill label={L('已更新', 'Updated')} /> : null;
  return (
    <View style={styles.header}>
      <OutputAvatar agent={o.agent} size={34} />
      <View style={{ flex: 1, minWidth: 0, gap: 1 }}>
        <T v="headline" style={{ fontSize: 17, fontWeight: '700' }} numberOfLines={1}>{o.label}</T>
        <T v="caption" color={t.ink3} style={{ fontWeight: '400' }} numberOfLines={1}>{`${name(o.agent)} · ${o.time}`}</T>
      </View>
      {pill ? <View style={{ flexShrink: 1, maxWidth: '48%' }}>{pill}</View> : null}
    </View>
  );
}

function BoardBody({ o }: { o: Output }) {
  const t = useTheme();
  const b = o.board;
  const [full, setFull] = useState(false);
  return (
    <View style={{ gap: space.md, paddingBottom: space.sm }}>
      {o.replaced ? <Note tone="neutral" text={L('同一天已有更新的版本，这里是当时的内容。', 'A newer version came later the same day; this is the earlier one.')} /> : null}
      {b.template === 'training_review' ? <ReviewView b={b} />
        : b.template === 'meal_plan' ? <MealView b={b} />
        : b.template === 'sleep_report' ? <SleepView b={b} />
        : b.template === 'training_plan' ? <PlanView b={b} />
        : (
          <View style={[styles.block, { backgroundColor: t.surface, gap: space.sm }]}>
            <T v="headline">{o.title}</T>
            <Markdown text={o.body} color={t.ink2} compact />
          </View>
        )}
      {b.template !== 'text' && o.body.trim() ? (
        <>
          <Pressable onPress={() => setFull((v) => !v)} accessibilityRole="button" accessibilityState={{ expanded: full }}
            style={({ pressed }) => [styles.fullRow, { opacity: pressed ? 0.6 : 1 }]}>
            <T v="callout" color={t.ink2} style={{ flex: 1 }}>{L('Agent 原文', "The Agent's full text")}</T>
            <Disclosure open={full} />
          </Pressable>
          {full ? <View style={[styles.block, { backgroundColor: t.surface }]}><Markdown text={o.body} color={t.ink2} compact /></View> : null}
        </>
      ) : null}
    </View>
  );
}

function Note({ tone, text }: { tone: 'warn' | 'good' | 'neutral'; text: string }) {
  const t = useTheme();
  const [bg, fg] = tone === 'warn' ? [t.warnSoft, t.warn] : tone === 'good' ? [t.goodSoft, t.good] : [t.surface2, t.ink2];
  const Icon = tone === 'good' ? CircleCheck : CircleAlert;
  return (
    <View style={[styles.note, { backgroundColor: bg }]}>
      <Icon size={15} color={fg} />
      <T v="callout" color={tone === 'neutral' ? t.ink2 : t.ink} style={{ flex: 1, fontSize: 13, lineHeight: 18 }}>{text}</T>
    </View>
  );
}

function Tiles({ items }: { items: { value: string; label: string }[] }) {
  const t = useTheme();
  return (
    <View style={{ flexDirection: 'row', gap: space.sm }}>
      {items.map((s, i) => (
        <View key={`${i}-${s.label}`} style={[styles.tile, { backgroundColor: t.surface }]}>
          <T style={styles.tileValue} numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.6}>{s.value}</T>
          <T v="caption" color={t.ink2} style={{ fontSize: 11, fontWeight: '400' }} numberOfLines={1}>{s.label}</T>
        </View>
      ))}
    </View>
  );
}

function Bullets({ items, color }: { items: string[]; color?: string }) {
  const t = useTheme();
  return (
    <View style={{ gap: 6 }}>
      {items.map((x, i) => (
        <View key={`${i}-${x}`} style={styles.li}>
          <View style={[styles.dot, { backgroundColor: color ?? t.ink3 }]} />
          <T v="callout" color={t.ink2} style={{ flex: 1, fontSize: 14, lineHeight: 20 }}>{x}</T>
        </View>
      ))}
    </View>
  );
}

// —— 练后报告 ——

function ReviewView({ b }: { b: ReviewBoard }) {
  const t = useTheme();
  const p = b.main?.points ?? [];
  return (
    <>
      <Tiles items={b.stats} />
      {b.main ? (
        <View style={[styles.block, styles.row, { backgroundColor: t.surface, gap: space.md }]}>
          <View style={{ flex: 1, minWidth: 0, gap: 4 }}>
            <T v="caption" color={t.ink2} style={{ fontWeight: '400' }}>{b.main.name}</T>
            <View style={[styles.row, { gap: space.sm, flexWrap: 'wrap' }]}>
              <T style={{ fontSize: 26, fontWeight: '700', fontVariant: ['tabular-nums'], color: t.ink }}>{b.main.value}</T>
              {b.main.delta ? <ChangeTag dir={dirOf(b.main.trend)} text={b.main.delta.replace(/^[+−-]/, '')} good={goodOf(b.main.trend)} /> : null}
            </View>
            {b.main.note ? <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{b.main.note}</T> : null}
          </View>
          {p.length === 2 ? <BeforeAfter before={{ label: p[0].label, value: p[0].value }} after={{ label: p[1].label, value: p[1].value }} width={120} height={72} label={b.main.a11y} /> : null}
        </View>
      ) : null}
      {b.moves.length ? (
        <View style={[styles.block, { backgroundColor: t.surface, paddingVertical: 10 }]}>
          <CompareList title={b.compare} items={b.moves.map((m) => ({ key: m.name, name: m.name, now: m.now, last: m.last || undefined, dir: dirOf(m.trend), verdict: m.verdict, good: goodOf(m.trend) }))} />
        </View>
      ) : null}
      {b.notes.length ? <View style={[styles.block, { backgroundColor: t.surface }]}><Bullets items={b.notes} /></View> : null}
      {b.skipped ? <T v="caption" color={t.ink3} style={{ fontWeight: '400', paddingHorizontal: space.xs }}>{b.skipped}</T> : null}
      {b.next24h.length ? (
        <View style={[styles.block, { backgroundColor: t.surface, gap: space.sm }]}>
          <T v="caption" color={t.ink3} style={{ fontWeight: '700' }}>{L('接下来 24 小时', 'Next 24 hours')}</T>
          {b.next24h.map((x, i) => (
            <View key={`${i}-${x}`} style={[styles.row, { gap: 10, alignItems: 'flex-start' }]}>
              <View style={[styles.circle, { borderColor: t.ink3 }]} />
              <T v="callout" style={{ flex: 1, fontSize: 14, lineHeight: 20 }}>{x}</T>
            </View>
          ))}
        </View>
      ) : null}
      {b.next ? (
        <View style={[styles.row, { gap: 10, paddingHorizontal: space.xs }]}>
          <T v="callout" color={t.ink3} style={{ fontSize: 13 }}>{L('下次训练', 'Next session')}</T>
          <T v="callout" style={{ flex: 1, fontWeight: '600' }}>{b.next}</T>
        </View>
      ) : null}
    </>
  );
}

// —— 饮食建议 ——

function MealView({ b }: { b: MealBoard }) {
  const t = useTheme();
  return (
    <>
      {b.ring ? (
        <View style={[styles.block, { backgroundColor: t.surface }]}>
          <NutritionRing protein={b.ring.protein} carbs={b.ring.carb} fat={b.ring.fat} kcal={b.ring.kcal ?? undefined} centerLabel={b.ring.caption} size={112} />
        </View>
      ) : null}
      {b.protein?.targetGrams ? (
        <View style={[styles.block, { backgroundColor: t.surface }]}>
          <TargetBar label={L('蛋白质', 'Protein')} value={b.protein.grams} target={b.protein.targetGrams} text={b.protein.value} targetText={`${b.protein.target} g`} note={b.note ?? undefined} />
        </View>
      ) : b.note ? <T v="caption" color={t.ink3} style={{ fontWeight: '400', paddingHorizontal: space.xs, lineHeight: 17 }}>{b.note}</T> : null}
      {b.meals.length ? (
        <View style={[styles.list, { backgroundColor: t.surface }]}>
          {b.meals.map((m, i) => (
            <View key={`${i}-${m.label}`} style={[styles.listRow, { alignItems: 'flex-start', gap: space.md }, i > 0 && { borderTopColor: t.line, borderTopWidth: StyleSheet.hairlineWidth }, i === 0 && { borderTopWidth: 0 }]}>
              <T v="callout" style={{ width: 44, fontWeight: '600', fontVariant: ['tabular-nums'] }} numberOfLines={1} adjustsFontSizeToFit>{m.time}</T>
              <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
                <T v="callout" style={{ fontWeight: '600' }}>{mealLabel(m.label)}</T>
                <T v="caption" color={t.ink2} style={{ fontWeight: '400', lineHeight: 17 }}>{m.items}</T>
              </View>
              {m.kcal ? <T v="callout" color={t.ink2} style={{ fontSize: 13, fontVariant: ['tabular-nums'] }}>{m.kcal}</T> : null}
            </View>
          ))}
        </View>
      ) : null}
      {b.why ? <T v="caption" color={t.ink2} style={{ fontWeight: '400', paddingHorizontal: space.xs, lineHeight: 17 }}>{b.why}</T> : null}
      {b.shopping.length ? <T v="caption" color={t.gold} style={{ paddingHorizontal: space.xs }}>{L(`采购清单：${b.shopping.join('、')}`, `Shopping list: ${b.shopping.join(', ')}`)}</T> : null}
    </>
  );
}

// —— 睡眠报告 ——

function SleepView({ b }: { b: SleepBoard }) {
  const t = useTheme();
  const u = b.updated;
  const score = u ? u.score : b.score.value ?? '—';
  const scoreLabel = u ? L('恢复分', 'Recovery') : b.score.provisional && b.score.value ? L('恢复分（暂估）', 'Recovery (estimate)') : L('恢复分', 'Recovery');
  const sleepText = u?.sleep ?? b.sleep.text ?? L('待同步', 'Not synced');
  const minutes = u?.minutes ?? b.sleep.minutes;
  const pts = b.trend?.points ?? [];
  return (
    <>
      {u ? <Note tone="good" text={L(`健康数据已同步：恢复分 ${u.score}${u.label ? `（${u.label}）` : ''}${u.sleep ? `，睡眠 ${u.sleep}` : ''}。报告写于同步之前。`, `Health data has synced: recovery ${u.score}${u.sleep ? `, sleep ${u.sleep}` : ''}. The report was written before the sync.`)} />
        : b.notice ? <Note tone="warn" text={b.notice} /> : null}
      <Tiles items={[{ value: score, label: scoreLabel }, { value: sleepText, label: L('睡眠', 'Sleep') }]} />
      {minutes != null ? (
        <View style={[styles.block, { backgroundColor: t.surface }]}>
          <TargetBar label={L('睡眠时长', 'Sleep')} value={minutes} target={b.sleep.low} text={sleepText} targetText={b.sleep.range} />
        </View>
      ) : null}
      {pts.length >= 2 ? (
        <View style={[styles.block, { backgroundColor: t.surface, gap: 6 }]}>
          <View style={[styles.row, { justifyContent: 'space-between' }]}>
            <T v="callout" color={t.ink2} style={{ fontSize: 13 }}>{b.trend?.caption}</T>
            <T v="callout" style={{ fontSize: 13, fontWeight: '700', fontVariant: ['tabular-nums'] }}>{pts[pts.length - 1].value}</T>
          </View>
          <Sparkline values={pts.map((x) => x.value)} height={44} min label={`${b.trend?.caption}${L('：', ': ')}${pts.map((x) => x.value).join(L('、', ', '))}`} />
          <View style={[styles.row, { justifyContent: 'space-between' }]}>
            <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{pts[0].label}</T>
            <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{pts[pts.length - 1].label}</T>
          </View>
        </View>
      ) : null}
      {b.stages.length || b.vitals.length ? (
        <View style={[styles.row, { flexWrap: 'wrap', gap: space.sm }]}>
          {[...b.stages.map((s) => `${s.label} ${s.text}`), ...b.vitals.map((v) => `${v.label} ${v.value}`)].map((x) => <Pill key={x} label={x} />)}
        </View>
      ) : null}
      {b.notes.length ? <View style={[styles.block, { backgroundColor: t.surface }]}><Bullets items={b.notes} /></View> : null}
    </>
  );
}

// —— 训练计划 ——

function PlanView({ b }: { b: PlanBoard }) {
  const t = useTheme();
  const meta = [b.time, b.duration].filter(Boolean).join(' · ');
  return (
    <>
      <View style={[styles.block, { backgroundColor: t.surface, gap: 4 }]}>
        <T style={{ fontSize: 22, fontWeight: '700', color: t.ink }}>{b.session ?? b.decision ?? ''}</T>
        {meta ? <T v="callout" color={t.ink2} style={{ fontVariant: ['tabular-nums'] }}>{meta}</T> : null}
        {b.intensity ? <T v="callout" color={t.ink2} style={{ marginTop: 4, fontSize: 14, lineHeight: 20 }}>{b.intensity}</T> : null}
      </View>
      {b.focus.length ? (
        <View style={[styles.block, { backgroundColor: t.surface, gap: space.sm }]}>
          <T v="caption" color={t.ink3} style={{ fontWeight: '700' }}>{L('练什么', 'Exercises')}</T>
          {b.focus.map((x, i) => (
            <View key={`${i}-${x}`} style={[styles.row, { gap: 8, alignItems: 'flex-start' }]}>
              <T v="callout" color={t.ink3} style={{ width: 16, fontVariant: ['tabular-nums'] }}>{`${i + 1}.`}</T>
              <T v="callout" style={{ flex: 1, fontSize: 14, lineHeight: 20 }}>{x}</T>
            </View>
          ))}
        </View>
      ) : null}
      {b.cautions.length ? (
        <View style={[styles.block, { backgroundColor: t.surface, gap: space.sm }]}>
          <T v="caption" color={t.ink3} style={{ fontWeight: '700' }}>{L('注意', 'Watch out')}</T>
          <Bullets items={b.cautions} color={t.warn} />
        </View>
      ) : null}
      {b.why ? <T v="caption" color={t.ink2} style={{ fontWeight: '400', paddingHorizontal: space.xs, lineHeight: 17 }}>{b.why}</T> : null}
    </>
  );
}

// —— 底下的按钮 ——

function BoardActions({ o, close }: { o: Output; close: () => void }) {
  const { groups } = useStore();
  const [busy, setBusy] = useState(false);
  const thread = o.thread ?? o.agent ?? 'main';
  const isGroup = groups.some((g) => g.id === thread);
  const discuss = () => {
    close();
    const quote = o.messageId != null ? { replyTo: `db${o.messageId}`, title: `${o.label} · ${o.line}`.slice(0, 60) } : undefined;
    openThread(thread, isGroup, quote, o.messageId != null ? `db${o.messageId}` : undefined);
  };
  const board = o.agent && groups.some((g) => g.id === o.agent)
    ? () => { close(); navigationRef.navigate('Group', { id: o.agent, tab: 'board', at: Date.now() }); }
    : null;
  const canLog = o.board.template === 'meal_plan' && o.board.loggable && supports('diet.log');
  const log = () => {
    setBusy(true);
    dietLog(o.id).then(() => close()).catch((e) => showError(L('记录失败', "Couldn't log it"), e)).finally(() => setBusy(false));
  };
  const meal = o.board.template === 'meal_plan';
  return (
    <View style={[styles.row, { gap: space.sm, paddingTop: space.md }]}>
      <Btn label={meal ? L('调整', 'Adjust') : L('在对话中讨论', 'Discuss in chat')} kind="quiet" onPress={discuss} flex />
      {canLog ? <Btn label={busy ? L('正在记录…', 'Logging…') : L('记录这一餐', 'Log this meal')} onPress={busy ? () => {} : log} flex />
        : board ? <Btn label={L('打开看板', 'Open board')} onPress={board} flex /> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: space.md },
  row: { flexDirection: 'row', alignItems: 'center' },
  block: { borderRadius: 14, padding: space.md },
  tile: { flex: 1, minWidth: 0, borderRadius: radius.md, padding: 10, gap: 2 },
  tileValue: { fontSize: 20, fontWeight: '700', fontVariant: ['tabular-nums'] },
  list: { borderRadius: 14, overflow: 'hidden' },
  listRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 9, paddingHorizontal: space.md, borderTopWidth: StyleSheet.hairlineWidth },
  circle: { width: 18, height: 18, borderRadius: 9, borderWidth: 1.5, marginTop: 1 },
  note: { flexDirection: 'row', alignItems: 'flex-start', gap: 8, borderRadius: radius.md, paddingVertical: 9, paddingHorizontal: space.md },
  fullRow: { flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingHorizontal: space.xs, paddingVertical: 6 },
  li: { flexDirection: 'row', gap: 8, alignItems: 'flex-start' },
  dot: { width: 5, height: 5, borderRadius: 3, marginTop: 8 },
});
