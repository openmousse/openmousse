// 图表视图：一个视图里 1–6 个图，按配置的顺序；挨着的几个数字排成一行（StatRow）。图都来自可视化素材库：
// 柱状 BarChart、横条 HBarChart、折线 LineChart、环形 Donut，外面是 ChartCard。点按一段显示它的数值（素材库自己的提示），
// 右上角出「查看明细」：列出这一段的行（POST /api/boards/{agent}/view {chart, bucket}）。
import React, { useState } from 'react';
import { Pressable, View } from 'react-native';
import type { ChartData, ChartsData } from '../../api/boards';
import { L } from '../../i18n';
import { space, useTheme } from '../../theme';
import { T } from '../ui';
import { BarChart, ChartCard, Donut, HBarChart, LineChart, StatRow } from '../viz';
import type { ViewEnv, ViewProps } from './env';

type Part = { kind: 'numbers'; items: { c: ChartData; i: number }[] } | { kind: 'one'; c: ChartData; i: number };

export function ChartsView({ env, data }: ViewProps<ChartsData>) {
  const parts: Part[] = [];
  data.charts.forEach((c, i) => {
    const last = parts[parts.length - 1];
    if (c.kind === 'number') {
      if (last?.kind === 'numbers' && last.items.length < 3) last.items.push({ c, i });
      else parts.push({ kind: 'numbers', items: [{ c, i }] });
    } else parts.push({ kind: 'one', c, i });
  });
  const canDrill = !env.readOnly && !!env.query;
  return (
    <View style={{ gap: space.md }}>
      {parts.map((p) => (p.kind === 'numbers' ? (
        <StatRow key={`n${p.items[0].i}`} boxed items={p.items.map(({ c, i }) => ({
          label: c.label || c.title, value: c.error ? '—' : c.text ?? '—', sub: c.error ? L('无法计算', "Couldn't work out") : undefined,
          onPress: canDrill && !c.error ? () => env.drill(i, null, c.label || c.title) : undefined,
        }))} />
      ) : <OneChart key={p.i} c={p.c} i={p.i} env={env} canDrill={canDrill} />))}
    </View>
  );
}

function OneChart({ c, i, env, canDrill }: { c: ChartData; i: number; env: ViewEnv; canDrill: boolean }) {
  const t = useTheme();
  const [picked, setPicked] = useState<{ key: string; label: string } | null>(null);
  const pick = (key: string, label: string) => setPicked((p) => (p?.key === key ? null : { key, label }));
  const right = picked && canDrill ? (
    <Pressable onPress={() => env.drill(i, picked.key, c.title ? `${c.title} · ${picked.label}` : picked.label)} hitSlop={8} accessibilityRole="button"
      accessibilityLabel={L(`查看「${picked.label}」的明细`, `See the rows for ${picked.label}`)}>
      <T v="caption" color={t.gold} style={{ fontWeight: '700' }}>{L('查看明细', 'View rows')}</T>
    </Pressable>
  ) : undefined;
  if (c.error) {
    return <ChartCard title={c.title || L('图表', 'Chart')}><T v="callout" color={t.bad}>{L(`无法显示：${c.error}`, `Can't show this: ${c.error}`)}</T></ChartCard>;
  }
  let body: React.ReactNode;
  if (c.kind === 'bar') {
    body = <BarChart data={(c.bars ?? []).map((b) => ({ key: b.key, label: b.label, value: b.value, text: b.text, current: b.current }))} emphasis onPress={(d) => pick(d.key, d.label)} />;
  } else if (c.kind === 'hbar') {
    body = <HBarChart data={(c.bars ?? []).map((b) => ({ key: b.key, label: b.label, value: b.value, text: b.text }))} labelWidth={64} onPress={(d) => pick(d.key, d.label)} />;
  } else if (c.kind === 'line') {
    const pts = c.points ?? [];
    body = (
      <LineChart series={[{ key: 'v', label: c.title, points: pts.map((x) => ({ key: x.key, value: x.value, label: x.label, text: x.text })) }]}
        current={pts.find((x) => x.current)?.key} zero={c.lo === 0} emphasis legend={false} />
    );
  } else if (c.kind === 'donut') {
    body = (
      <Donut data={(c.slices ?? []).map((x) => ({ key: x.key, label: x.label, value: x.value, text: x.text, slot: x.color ?? -1 }))}
        center={{ value: c.totalText ?? '', label: L('合计', 'Total') }} pct onPress={(d) => pick(d.key, d.label)} />
    );
  } else {
    body = <T v="callout" color={t.ink2}>{L('此图表需要更新 app 后才能显示。', 'This chart needs a newer version of the app.')}</T>;
  }
  return <ChartCard title={c.title} caption={right ? undefined : c.unitLabel ?? (c.kind === 'line' ? c.lastText : undefined)} right={right}>{body}</ChartCard>;
}
