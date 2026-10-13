// 时间线视图：每行一条，条 = 从开始（没写开始字段就是今天）到日期，菱形是那一天，金线是今天；窗口和刻度都是服务器算好的位置（0–1）。
import React, { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import type { TimelineData, ViewData } from '../../api/boards';
import { L } from '../../i18n';
import { radius, space, type, useTheme } from '../../theme';
import { showError, T, useScaledWidth } from '../ui';
import type { ViewProps } from './env';
import { seriesColor } from './palette';

const INLINE_ROWS = 6;

export function TimelineView({ env, data: initial }: ViewProps<TimelineData>) {
  const t = useTheme();
  const [extra, setExtra] = useState<TimelineData['items']>([]);
  const [seen, setSeen] = useState(initial);
  const [busy, setBusy] = useState(false);
  if (seen !== initial) {
    setSeen(initial);
    setExtra([]);
  }
  const data = initial;
  const lw = useScaledWidth(84);
  const ew = useScaledWidth(40);
  const all = [...data.items, ...extra];
  const items = env.full ? all : all.slice(0, INLINE_ROWS);
  const left = data.total - (env.full ? all.length : items.length);
  const loadMore = () => {
    if (!env.query) return;
    setBusy(true);
    env.query({ full: true, offset: all.length }).then((v: ViewData | null) => {
      if (v && v.type === 'timeline') setExtra((x) => [...x, ...(v as TimelineData).items]);
    }).catch((e) => showError(L('无法加载', "Couldn't load"), e)).finally(() => setBusy(false));
  };
  if (!items.length) {
    return (
      <View style={[styles.card, { backgroundColor: t.surface }]}>
        <T v="callout" color={t.ink2}>{L(`接下来没有${data.dateLabel}。`, `Nothing coming up by ${data.dateLabel}.`)}</T>
      </View>
    );
  }
  const bar = seriesColor(t, 0);
  const tip = seriesColor(t, 1);
  return (
    <View style={{ gap: space.sm }}>
      <View style={[styles.card, { backgroundColor: t.surface }]}>
        <View style={{ flexDirection: 'row', height: 18 }}>
          <View style={{ width: lw }} />
          <View style={{ flex: 1 }}>
            {data.ticks.map((x) => (
              <Text key={x.label} numberOfLines={1} style={[type.caption, styles.tick, { left: `${x.pos * 100}%`, color: t.ink3 }]}>{x.label}</Text>
            ))}
          </View>
          <View style={{ width: ew }} />
        </View>
        <View>
          {items.map((it) => (
            <Pressable key={it.id} onPress={env.readOnly ? undefined : () => env.openCard(it)} disabled={env.readOnly} accessibilityRole="button"
              accessibilityLabel={L(`${it.title}，${it.endText}${it.overdue ? '，已过' : ''}`, `${it.title}, ${it.endText}${it.overdue ? ', past' : ''}`)}
              style={({ pressed }) => [styles.row, { opacity: pressed ? 0.6 : 1 }]}>
              <T v="caption" color={t.ink2} numberOfLines={1} style={{ width: lw, paddingRight: 6, fontWeight: '400', fontSize: 12 }}>{it.title}</T>
              <View style={{ flex: 1, height: 18, justifyContent: 'center' }}>
                <View style={[styles.track, { backgroundColor: t.track }]} />
                {it.end > it.start ? (
                  <View style={[styles.bar, { left: `${it.start * 100}%`, width: `${(it.end - it.start) * 100}%`, backgroundColor: bar, opacity: it.overdue ? 0.35 : 0.6 }]} />
                ) : null}
                {it.clipped ? null : <View style={[styles.diamond, { left: `${it.end * 100}%`, backgroundColor: it.overdue ? t.ink3 : tip }]} />}
              </View>
              <Text numberOfLines={1} style={[type.caption, { width: ew, textAlign: 'right', fontSize: 11, color: it.clipped ? t.ink3 : t.ink, fontVariant: ['tabular-nums'] }]}>
                {it.clipped ? `${it.endText} ›` : it.endText}
              </Text>
            </Pressable>
          ))}
          {data.today != null ? (
            <View pointerEvents="none" style={[StyleSheet.absoluteFill, { flexDirection: 'row' }]}>
              <View style={{ width: lw }} />
              <View style={{ flex: 1 }}><View style={[styles.today, { left: `${data.today * 100}%`, backgroundColor: t.gold }]} /></View>
              <View style={{ width: ew }} />
            </View>
          ) : null}
        </View>
      </View>
      {left > 0 ? (
        <Pressable onPress={env.full ? loadMore : env.openFull} disabled={busy || (!env.full && !env.openFull)} accessibilityRole="button" style={({ pressed }) => [styles.more, { opacity: pressed ? 0.6 : 1 }]}>
          <T v="callout" color={env.full ? t.gold : t.ink2} style={{ fontWeight: env.full ? '700' : '400' }}>
            {env.full ? L(`加载更多（另有 ${left} 条）`, `Load more (${left} more)`) : L(`查看全部 ${data.total} 条`, `See all ${data.total}`)}
          </T>
        </Pressable>
      ) : null}
      {data.before ? <T v="caption" color={t.ink3} style={{ fontWeight: '400', paddingHorizontal: 4 }}>{L(`另有 ${data.before} 条在这之前`, `${data.before} earlier`)}</T> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  card: { borderRadius: radius.lg, padding: space.md, gap: 4 },
  tick: { position: 'absolute', top: 0, fontSize: 10, marginLeft: -2 },
  row: { flexDirection: 'row', alignItems: 'center', height: 30 },
  track: { position: 'absolute', left: 0, right: 0, height: 2, borderRadius: 1 },
  bar: { position: 'absolute', height: 6, borderRadius: 3 },
  diamond: { position: 'absolute', width: 10, height: 10, marginLeft: -5, borderRadius: 2, transform: [{ rotate: '45deg' }] },
  today: { position: 'absolute', top: 0, bottom: 0, width: 1.5, marginLeft: -0.75 },
  more: { alignItems: 'center', paddingVertical: space.sm },
});
