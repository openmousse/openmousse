// 对话里的产出卡片（2026-10-13）：Agent 在这条回复里出的报告 / 建议，永久留在对话里，点开是同一块小板子。
// 练后报告画成一张小表（主项和各动作对上次），别的一行标题 + 一行说明。卡片挂在哪条回复下由服务器给（messageId，见 server/outputs.py）。
import React from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import type { OutputChatCard } from '../../data/types';
import type { Output } from '../../api/outputs';
import { L } from '../../i18n';
import { agentTint, useTheme } from '../../theme';
import { useStore } from '../../store';
import { ChevronRight } from '../icons';
import { mealLabel } from '../LiveBoards';
import { T } from '../ui';
import { useOutputBoard } from './Board';
import { ChangeTag } from './vizlite';

/** 卡片第二行：各模板最有用的那一句。 */
function subline(o: Output): string {
  const b = o.board;
  if (b.template === 'training_plan') return b.intensity ?? b.why ?? '';
  if (b.template === 'meal_plan') return b.meals.map((m) => [m.time, mealLabel(m.label)].filter(Boolean).join(' ')).join(' · ');
  if (b.template === 'sleep_report') return b.updated?.sleep ? L(`睡眠 ${b.updated.sleep}`, `Sleep ${b.updated.sleep}`) : b.notice ?? (b.sleep.text ? L(`睡眠 ${b.sleep.text}`, `Sleep ${b.sleep.text}`) : '');
  if (b.template === 'training_review') return b.footer ?? b.next ?? '';
  return o.line !== o.title ? o.line : '';
}

function headline(o: Output): string {
  const b = o.board;
  if (b.template === 'training_plan') return [b.session, b.decision].filter(Boolean).join(' · ') || o.line;
  if (b.template === 'text') return o.title;
  return o.line;
}

export function OutputCard({ card }: { card: OutputChatCard }) {
  const t = useTheme();
  const open = useOutputBoard();
  const { groups } = useStore();
  const o = card.output;
  const b = o.board;
  const tint = agentTint(t, groups.find((g) => g.id === o.agent)?.color);
  const rich = b.template === 'training_review' && b.moves.length > 0;
  const fade = o.replaced ? 0.65 : 1;
  const a11y = `${o.label}${L('：', ': ')}${headline(o)}${o.replaced ? L('（已更新）', ' (updated)') : ''}`;
  if (!rich) {
    const sub = subline(o);
    return (
      <Pressable onPress={() => open(o)} accessibilityRole="button" accessibilityLabel={a11y} accessibilityHint={L('打开小板子', 'Opens the report')}
        style={({ pressed }) => [styles.card, styles.row, { backgroundColor: t.surface, borderColor: t.line, opacity: pressed ? 0.7 : fade }]}>
        <View style={{ flex: 1, minWidth: 0, gap: 3 }}>
          <T v="caption" color={t.ink3} style={{ fontWeight: '700' }} numberOfLines={1}>{o.replaced ? `${o.label} · ${L('已更新', 'Updated')}` : o.label}</T>
          <T v="headline" style={{ fontSize: 15 }} numberOfLines={2}>{headline(o)}</T>
          {sub ? <T v="caption" color={t.ink2} style={{ fontWeight: '400' }} numberOfLines={2}>{sub}</T> : null}
        </View>
        <ChevronRight size={16} color={t.ink3} />
      </Pressable>
    );
  }
  if (b.template !== 'training_review') return null;
  const meta = b.stats.slice(1).map((s) => `${s.value} ${s.label}`).join(' · ');
  return (
    <Pressable onPress={() => open(o)} accessibilityRole="button" accessibilityLabel={a11y} accessibilityHint={L('打开小板子', 'Opens the report')}
      style={({ pressed }) => [styles.card, { backgroundColor: t.surface, borderColor: tint.soft, borderWidth: 1, gap: 10, opacity: pressed ? 0.7 : fade }]}>
      <View style={[styles.row, { gap: 8 }]}>
        <T v="caption" color={t.ink3} style={{ flex: 1, fontWeight: '700' }} numberOfLines={1}>{o.replaced ? `${o.label} · ${L('已更新', 'Updated')}` : o.label}</T>
        {b.verdict ? <ChangeTag small dir={b.verdictTrend === 'up' ? 'up' : b.verdictTrend === 'down' ? 'down' : 'flat'} text={b.verdict} /> : null}
      </View>
      <View style={[styles.row, { alignItems: 'baseline', gap: 8 }]}>
        <T v="headline" style={{ fontSize: 17, fontWeight: '700' }} numberOfLines={1}>{b.session}</T>
        {meta ? <T v="callout" color={t.ink2} style={{ fontSize: 13 }} numberOfLines={1}>{meta}</T> : null}
      </View>
      <View style={{ gap: 6 }}>
        {b.moves.slice(0, 3).map((m) => {
          const c = m.trend === 'up' ? t.good : m.trend === 'down' ? t.bad : t.ink2;
          return (
            <View key={m.name} style={[styles.row, { gap: 8 }]}>
              <T v="callout" color={t.ink2} style={{ flex: 1, fontSize: 13 }} numberOfLines={1}>{m.name}</T>
              <T v="callout" style={{ fontSize: 13, fontVariant: ['tabular-nums'] }} numberOfLines={1}>{m.now}</T>
              <T v="caption" color={c} style={{ minWidth: 48, textAlign: 'right', fontWeight: '700' }} numberOfLines={1}>{m.delta ?? m.verdict}</T>
            </View>
          );
        })}
      </View>
      <View style={[styles.row, styles.foot, { borderTopColor: t.line }]}>
        <T v="caption" color={t.ink3} style={{ flex: 1, fontWeight: '400' }} numberOfLines={1}>{b.footer ?? b.next ?? ''}</T>
        <T v="callout" color={t.gold} style={{ fontSize: 13, fontWeight: '600' }}>{L('查看', 'View')}</T>
        <ChevronRight size={14} color={t.gold} />
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: { borderRadius: 16, borderWidth: StyleSheet.hairlineWidth, paddingVertical: 12, paddingHorizontal: 14 },
  row: { flexDirection: 'row', alignItems: 'center' },
  foot: { gap: 2, paddingTop: 8, borderTopWidth: StyleSheet.hairlineWidth },
});
