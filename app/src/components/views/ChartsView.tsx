// 图表视图：一个视图里 1–6 个图，按配置的顺序；挨着的几个数字排成一行。点按显示数值，「查看」列出那一段的行。
import React from 'react';
import { View } from 'react-native';
import type { ChartData, ChartsData } from '../../api/boards';
import { space } from '../../theme';
import { BarChart, ChartError, ChartUnknown, DonutChart, HBarChart, LineChart, NumberTiles } from './charts';
import type { ViewProps } from './env';

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
  const drill = (i: number) => (env.readOnly || !env.query ? undefined : (key: string | null, label: string) => env.drill(i, key, label));
  return (
    <View style={{ gap: space.md }}>
      {parts.map((p) => {
        if (p.kind === 'numbers') {
          return <NumberTiles key={`n${p.items[0].i}`} charts={p.items} onDrill={env.readOnly || !env.query ? undefined : (i, key, label) => env.drill(i, key, label)} />;
        }
        const { c, i } = p;
        if (c.error) return <ChartError key={i} c={c} />;
        if (c.kind === 'bar') return <BarChart key={i} c={c} onDrill={drill(i)} />;
        if (c.kind === 'hbar') return <HBarChart key={i} c={c} onDrill={drill(i)} />;
        if (c.kind === 'line') return <LineChart key={i} c={c} onDrill={drill(i)} />;
        if (c.kind === 'donut') return <DonutChart key={i} c={c} onDrill={drill(i)} />;
        return <ChartUnknown key={i} c={c} />;
      })}
    </View>
  );
}
