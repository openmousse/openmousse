// 一行画成的卡片（看板列里、日历当天、图表点开的明细都用它）：上面一行小字、标题、标签。字都是服务器写好的。
import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import type { CardTag, ViewCard } from '../../api/boards';
import { radius, type, useTheme } from '../../theme';
import { T } from '../ui';
import { cardBg, seriesColor, toneTag } from './palette';

export function Tags({ tags }: { tags?: CardTag[] }) {
  const t = useTheme();
  if (!tags?.length) return null;
  return (
    <View style={styles.tags}>
      {tags.map((x, i) => {
        const [bg, fg] = toneTag(t, x.tone);
        return (
          <View key={`${i}-${x.text}`} style={[styles.tag, { backgroundColor: bg }]}>
            <Text style={[type.caption, { fontSize: 11, fontWeight: '600', color: fg }]} numberOfLines={1}>{x.text}</Text>
          </View>
        );
      })}
    </View>
  );
}

/** 卡片本身（不带手势）：看板拖动时浮在手指下的那张也用它。 */
export function CardFace({ card, dot, lines = 2 }: { card: ViewCard; dot?: number | null; lines?: number }) {
  const t = useTheme();
  return (
    <View style={[styles.card, { backgroundColor: cardBg(t) }]}>
      {card.meta ? (
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
          {dot !== undefined ? <View style={[styles.dot, { backgroundColor: seriesColor(t, dot) }]} /> : null}
          <T v="caption" color={t.ink2} style={{ fontWeight: '400', flexShrink: 1 }} numberOfLines={1}>{card.meta}</T>
        </View>
      ) : null}
      <T v="callout" style={{ fontSize: 14, lineHeight: 19, fontWeight: '600' }} numberOfLines={lines}>{card.title}</T>
      <Tags tags={card.tags} />
    </View>
  );
}

/** 能点的卡片：点开看全部字段、改、移到别的列。 */
export function CardItem({ card, onPress, onLongPress, dot, lines, dim }: {
  card: ViewCard; onPress?: () => void; onLongPress?: (e: { nativeEvent: { pageX: number; pageY: number; locationX: number; locationY: number } }) => void;
  dot?: number | null; lines?: number; dim?: boolean;
}) {
  return (
    <Pressable onPress={onPress} onLongPress={onLongPress} delayLongPress={250} disabled={!onPress && !onLongPress}
      accessibilityRole={onPress ? 'button' : undefined} accessibilityLabel={[card.meta, card.title, ...(card.tags ?? []).map((x) => x.text)].filter(Boolean).join('，')}
      style={({ pressed }) => ({ opacity: dim ? 0.35 : pressed ? 0.7 : 1 })}>
      <CardFace card={card} dot={dot} lines={lines} />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: { borderRadius: radius.md, paddingHorizontal: 12, paddingVertical: 10, gap: 6 },
  tags: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  tag: { borderRadius: 6, paddingHorizontal: 6, paddingVertical: 2, maxWidth: '100%' },
  dot: { width: 8, height: 8, borderRadius: 4 },
});
