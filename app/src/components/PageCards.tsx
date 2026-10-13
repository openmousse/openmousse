// 页面（2026-10-13，服务器 ../server/pages.py）：对话里的页面卡片（PageChip，挂在发布它的那条回复下面）和看板上的页面积木（PageBlockBody）。
// 点开都是 Page 页（src/screens/PageScreen.tsx）。积木可能画在 sheet 里（收件箱卡的看板预览在导航器外面），跳转一律走 navigationRef。
import React from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import type { Block } from '../api/boards';
import type { PageChatCard } from '../data/types';
import { L } from '../i18n';
import { navigationRef } from '../navigation';
import { useTheme } from '../theme';
import { ChevronRight, PanelsTopLeft } from './icons';
import { Card, T } from './ui';

export function openPage(id: string) {
  if (navigationRef.isReady()) navigationRef.navigate('Page', { id, at: Date.now() });
}

/** 「03:20」（今天）/「10/12 03:20」。 */
export function pageTime(iso?: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  const now = new Date();
  return d.toDateString() === now.toDateString() ? hm : `${d.getMonth() + 1}/${d.getDate()} ${hm}`;
}

export function readsLine(reads: string[]): string {
  return reads.length ? L(`读取：${reads.join('、')}（只读）`, `Reads: ${reads.join(', ')} (read-only)`) : L('不读取数据', 'Reads no data');
}

export function PageTag() {
  const t = useTheme();
  const tint = t.tints.purple;
  return (
    <View style={[styles.tag, { backgroundColor: tint.soft }]}>
      <PanelsTopLeft size={11} color={tint.fg} />
      <T v="caption" color={tint.fg} style={{ fontSize: 11, fontWeight: '700' }}>{L('页面', 'Page')}</T>
    </View>
  );
}

export const PageChip = React.memo(function PageChip({ card }: { card: PageChatCard }) {
  const t = useTheme();
  const old = card.latest > card.version;
  const label = card.deleted ? L('已删除', 'Deleted')
    : old ? L(`第 ${card.version} 版 · 已更新到第 ${card.latest} 版`, `Version ${card.version} · now version ${card.latest}`)
      : L(`第 ${card.version} 版`, `Version ${card.version}`);
  return (
    <Pressable disabled={card.deleted} onPress={() => openPage(card.page)} accessibilityRole="button"
      accessibilityLabel={L(`页面：${card.title}，${label}`, `Page: ${card.title}, ${label}`)} accessibilityHint={L('打开页面', 'Opens the page')}
      style={({ pressed }) => [styles.card, { backgroundColor: t.surface, borderColor: t.line, opacity: card.deleted ? 0.55 : pressed ? 0.7 : 1 }]}>
      <View style={styles.row}>
        <PageTag />
        <T v="caption" color={t.ink3} style={{ flex: 1, fontWeight: '400' }} numberOfLines={1}>{label}</T>
      </View>
      <View style={{ gap: 3 }}>
        <T v="headline" style={{ fontSize: 16, fontWeight: '700' }} numberOfLines={2}>{card.title}</T>
        {card.summary ? <T v="caption" color={t.ink2} style={{ fontWeight: '400' }} numberOfLines={2}>{card.summary}</T> : null}
      </View>
      <View style={[styles.row, styles.foot, { borderTopColor: t.line }]}>
        <T v="caption" color={t.ink3} style={{ flex: 1, fontWeight: '400' }} numberOfLines={1}>{readsLine(card.reads)}</T>
        {card.deleted ? null : (
          <View style={[styles.row, { gap: 2 }]}>
            <T v="caption" color={t.gold} style={{ fontSize: 13, fontWeight: '600' }}>{L('打开', 'Open')}</T>
            <ChevronRight size={14} color={t.gold} />
          </View>
        )}
      </View>
    </Pressable>
  );
});

interface PageBlockData { page?: string; title?: string; summary?: string; version?: number; updatedAt?: string; reads?: string[]; deleted?: boolean }

/** 看板上的页面积木：标题在积木头上（BlockHeader），这里是说明、读哪些表、更新时间和「打开页面」。 */
export function PageBlockBody({ block }: { block: Block }) {
  const t = useTheme();
  const d = block.data as unknown as PageBlockData;
  if (d.deleted || !d.page) return <Card><T v="callout" color={t.ink2}>{L('这个页面已删除。', 'This page has been deleted.')}</T></Card>;
  const page = d.page;
  const meta = [L(`第 ${d.version ?? 1} 版`, `Version ${d.version ?? 1}`), pageTime(d.updatedAt) ? L(`${pageTime(d.updatedAt)} 更新`, `updated ${pageTime(d.updatedAt)}`) : '']
    .filter(Boolean).join(' · ');
  return (
    <Pressable onPress={() => openPage(page)} accessibilityRole="button" accessibilityLabel={L(`打开页面：${d.title ?? block.title}`, `Open the page: ${d.title ?? block.title}`)}
      style={({ pressed }) => [styles.card, { backgroundColor: t.surface, borderColor: t.tints.purple.fg, opacity: pressed ? 0.7 : 1 }]}>
      <View style={styles.row}>
        <PageTag />
        <T v="caption" color={t.ink3} style={{ flex: 1, fontWeight: '400' }} numberOfLines={1}>{meta}</T>
      </View>
      {d.summary ? <T v="callout" color={t.ink2} numberOfLines={2}>{d.summary}</T> : null}
      <View style={[styles.row, styles.foot, { borderTopColor: t.line }]}>
        <T v="caption" color={t.ink3} style={{ flex: 1, fontWeight: '400' }} numberOfLines={1}>{readsLine(d.reads ?? [])}</T>
        <View style={[styles.row, { gap: 2 }]}>
          <T v="caption" color={t.gold} style={{ fontSize: 13, fontWeight: '600' }}>{L('打开页面', 'Open')}</T>
          <ChevronRight size={14} color={t.gold} />
        </View>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: { borderRadius: 16, borderWidth: StyleSheet.hairlineWidth, paddingVertical: 12, paddingHorizontal: 14, gap: 10 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  tag: { flexDirection: 'row', alignItems: 'center', gap: 4, borderRadius: 7, paddingHorizontal: 7, paddingVertical: 2 },
  foot: { paddingTop: 8, borderTopWidth: StyleSheet.hairlineWidth },
});
