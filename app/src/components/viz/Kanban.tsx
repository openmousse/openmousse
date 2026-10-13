// 看板列：按某个字段分列（新岗位 / 撰写中 / 已投递），每列头一个色点 + 名字 + 数量，下面是卡片。
// 手机上没有拖拽：长按卡片选「移到…」（给了 onMove 才有）。三列放得下就并排，放不下横向滑。
import React from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View, type StyleProp, type ViewStyle } from 'react-native';
import { L } from '../../i18n';
import { radius, space, useTheme } from '../../theme';
import { Group, Row } from '../settings';
import { useSheet } from '../Sheet';
import { seriesColor, toneColors, useWidth, VizFrame, vizText, type Tone, type VizStateProps } from './core';

export interface KanbanCard { key: string; title: string; sub?: string; tags?: { text: string; tone?: Tone | string | null }[] }
export interface KanbanCol {
  key: string;
  title: string;
  /** 列头色点用第几个分类色（按列的身份，不按位置） */
  slot?: number;
  cards: KanbanCard[];
  /** 这一列一共多少张（卡片只给了前几张时） */
  total?: number;
}

export interface KanbanProps extends VizStateProps {
  columns: KanbanCol[];
  onPressCard?: (card: KanbanCard, column: KanbanCol) => void;
  /** 长按卡片「移到…」：改这张卡的分列字段 */
  onMove?: (card: KanbanCard, from: KanbanCol, to: KanbanCol) => void;
  /** 每列最多显示几张 */
  limit?: number;
  style?: StyleProp<ViewStyle>;
}

export function Kanban({ columns, onPressCard, onMove, limit = 5, style, ...state }: KanbanProps) {
  const t = useTheme();
  const sheet = useSheet();
  const [w, onLayout] = useWidth();
  const gap = space.sm;
  const fit = w > 0 && (w - gap * (columns.length - 1)) / columns.length >= 112;
  const colW = fit ? (w - gap * (columns.length - 1)) / Math.max(1, columns.length) : 160;
  const cardBg = t.mode === 'dark' ? t.surface2 : t.surface;
  const move = (card: KanbanCard, from: KanbanCol) => {
    if (!onMove) return;
    sheet.open({
      title: L('移到', 'Move to'),
      content: (close) => (
        <Group style={{ marginHorizontal: 0 }}>
          {columns.filter((c) => c.key !== from.key).map((c, i) => (
            <Row key={c.key} first={i === 0} title={c.title} chevron={false} onPress={() => { close(); onMove(card, from, c); }}
              icon={<View style={{ width: 10, height: 10, borderRadius: 5, backgroundColor: seriesColor(t, c.slot ?? columns.indexOf(c)) }} />} />
          ))}
        </Group>
      ),
    });
  };
  const body = columns.map((col, ci) => {
    const shown = col.cards.slice(0, limit);
    const more = (col.total ?? col.cards.length) - shown.length;
    return (
      <View key={col.key} style={[styles.col, { width: colW, backgroundColor: t.bg }]}>
        <View style={styles.head} accessible accessibilityRole="header" accessibilityLabel={`${col.title} ${col.total ?? col.cards.length}`}>
          <View style={{ width: 7, height: 7, borderRadius: 4, backgroundColor: seriesColor(t, col.slot ?? ci) }} />
          <Text numberOfLines={1} style={{ flexShrink: 1, fontSize: 12, fontWeight: '700', color: t.ink }}>{col.title}</Text>
          <Text style={[vizText.axis, { fontSize: 12, color: t.ink3 }]}>{String(col.total ?? col.cards.length)}</Text>
        </View>
        {shown.map((card) => (
          <Pressable key={card.key} onPress={onPressCard ? () => onPressCard(card, col) : undefined} onLongPress={onMove ? () => move(card, col) : undefined}
            delayLongPress={400} disabled={!onPressCard && !onMove} accessibilityRole={onPressCard ? 'button' : 'text'}
            accessibilityLabel={[card.sub, card.title, ...(card.tags ?? []).map((x) => x.text)].filter(Boolean).join(L('，', ', '))}
            accessibilityHint={onMove ? L('长按可移到其他列', 'Long-press to move it to another column') : undefined}
            style={({ pressed }) => [styles.card, { backgroundColor: cardBg, opacity: pressed ? 0.7 : 1 }]}>
            {card.sub ? <Text numberOfLines={1} style={{ fontSize: 10, color: t.ink2 }}>{card.sub}</Text> : null}
            <Text numberOfLines={2} style={{ fontSize: 12, lineHeight: 16, fontWeight: '600', color: t.ink }}>{card.title}</Text>
            {card.tags?.length ? (
              <View style={styles.tags}>
                {card.tags.map((x, k) => (
                  <View key={k} style={[styles.tag, { backgroundColor: toneColors(t, x.tone)[0] }]}>
                    <Text numberOfLines={1} style={{ fontSize: 10, fontWeight: '600', color: toneColors(t, x.tone)[1] }}>{x.text}</Text>
                  </View>
                ))}
              </View>
            ) : null}
          </Pressable>
        ))}
        {more > 0 ? <Text style={[vizText.axis, { fontSize: 11, color: t.ink3, paddingHorizontal: 2 }]}>{L(`另有 ${more} 张`, `${more} more`)}</Text> : null}
      </View>
    );
  });
  return (
    <VizFrame empty={!columns.length} state={state} group={false} style={style}>
      <View onLayout={onLayout}>
        {fit || w === 0 ? <View style={{ flexDirection: 'row', gap, alignItems: 'flex-start' }}>{body}</View> : (
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap, alignItems: 'flex-start' }}>{body}</ScrollView>
        )}
      </View>
    </VizFrame>
  );
}

const styles = StyleSheet.create({
  col: { borderRadius: radius.md, padding: 8, gap: 6 },
  head: { flexDirection: 'row', alignItems: 'center', gap: 5, paddingHorizontal: 2, paddingBottom: 2 },
  card: { borderRadius: 8, padding: 7, gap: 2 },
  tags: { flexDirection: 'row', flexWrap: 'wrap', gap: 4, marginTop: 3 },
  tag: { borderRadius: 5, paddingHorizontal: 5, paddingVertical: 1 },
});
