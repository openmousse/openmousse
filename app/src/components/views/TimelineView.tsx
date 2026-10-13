// 时间线视图：素材库的 Timeline（条 = 从开始到日期还剩的时间，菱形 = 那一天，金线 = 今天，顶上按周写日期）。
// 窗口、每一条的起止都是服务器给的；点一行打开那一行。全屏页「加载更多」接着往下取。
import React, { useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import type { TimelineData, TimelineItem, ViewData } from '../../api/boards';
import { L } from '../../i18n';
import { radius, space, useTheme } from '../../theme';
import { showError, T } from '../ui';
import { Timeline } from '../viz';
import type { ViewProps } from './env';

const INLINE_ROWS = 6;

export function TimelineView({ env, data }: ViewProps<TimelineData>) {
  const t = useTheme();
  const [extra, setExtra] = useState<TimelineItem[]>([]);
  const [seen, setSeen] = useState(data);
  const [busy, setBusy] = useState(false);
  if (seen !== data) {
    setSeen(data);
    setExtra([]);
  }
  const all = [...data.items, ...extra];
  const items = env.full ? all : all.slice(0, INLINE_ROWS);
  const left = data.total - (env.full ? all.length : items.length);
  const byKey = new Map(items.map((it) => [it.id, it]));
  const span = (Date.parse(data.to) - Date.parse(data.from)) / 86400000;
  const loadMore = () => {
    if (!env.query) return;
    setBusy(true);
    env.query({ full: true, offset: all.length }).then((v: ViewData | null) => {
      if (v && v.type === 'timeline') setExtra((x) => [...x, ...(v as TimelineData).items]);
    }).catch((e) => showError(L('无法加载', "Couldn't load"), e)).finally(() => setBusy(false));
  };
  return (
    <View style={{ gap: space.sm }}>
      <View style={[styles.card, { backgroundColor: t.surface }]}>
        <Timeline from={data.from} to={data.to} today={data.todayDate} tickEvery={span > 100 ? 30 : 7}
          rows={items.map((it) => ({ key: it.id, label: it.title, start: it.startDate, end: it.endDate ?? data.to }))}
          onPressRow={env.readOnly ? undefined : (r) => { const it = byKey.get(r.key); if (it) env.openCard(it); }}
          emptyText={L(`接下来没有${data.dateLabel}。`, `Nothing coming up by ${data.dateLabel}.`)} />
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
  card: { borderRadius: radius.lg, padding: space.md },
  more: { alignItems: 'center', paddingVertical: space.sm },
});
