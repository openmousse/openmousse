// 时间线：每件事一行，条 = 从今天（或开始那天）到截止还剩的时间，菱形 = 截止，金线 = 今天；顶上按周写日期。
// 点按一行在下面写出它的截止和还剩几天。
import React, { useState } from 'react';
import { Pressable, StyleSheet, Text, View, type StyleProp, type ViewStyle } from 'react-native';
import { L } from '../../i18n';
import { space, useTheme } from '../../theme';
import { useScaledWidth } from '../ui';
import { seriesColor, useFontScale, useWidth, VizFrame, vizText, type VizStateProps } from './core';
import { dayNum } from './LineChart';
import { isoOfDay } from './Calendars';

export interface TimelineRow {
  key: string;
  label: string;
  /** YYYY-MM-DD；不给就从今天（或范围的起点）画起 */
  start?: string;
  /** 截止 YYYY-MM-DD */
  end: string;
  /** 截止那一项的说明（点按后显示），不给就写「截止 10/16」 */
  text?: string;
}

const md = (iso: string) => `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))}`;

export interface TimelineProps extends VizStateProps {
  rows: TimelineRow[];
  /** 显示的范围 YYYY-MM-DD */
  from: string;
  to: string;
  today?: string;
  /** 顶上隔几天写一个日期 */
  tickEvery?: number;
  onPressRow?: (row: TimelineRow) => void;
  label?: string;
  style?: StyleProp<ViewStyle>;
}

export function Timeline({ rows, from, to, today, tickEvery = 7, onPressRow, label, style, ...state }: TimelineProps) {
  const t = useTheme();
  const fs = useFontScale();
  const lw = useScaledWidth(76);
  const [w, onLayout] = useWidth();
  const [sel, setSel] = useState<string | null>(null);
  const d0 = dayNum(from);
  const span = Math.max(1, dayNum(to) - d0);
  const trackW = Math.max(0, w - lw);
  const pad = 6;  // 菱形不出右边
  const xOf = (iso: string) => Math.max(0, Math.min(trackW - pad, ((dayNum(iso) - d0) / span) * (trackW - pad)));
  const ticks: string[] = [];
  for (let n = d0; n <= d0 + span; n += tickEvery) ticks.push(isoOfDay(n));
  const tw = Math.round(40 * fs);
  const todayN = today ? dayNum(today) : null;
  const left = (r: TimelineRow) => (todayN != null ? Math.max(0, dayNum(r.end) - todayN) : null);
  const desc = (r: TimelineRow) => {
    const n = left(r);
    return r.text ?? `${L(`截止 ${md(r.end)}`, `Due ${md(r.end)}`)}${n != null ? L(`，还有 ${n} 天`, `, ${n} day${n === 1 ? '' : 's'} left`) : ''}`;
  };
  const picked = rows.find((r) => r.key === sel);
  const auto = rows.map((r) => `${r.label} ${desc(r)}`).join(L('；', '; '));
  const barColor = t.viz.seq[2];
  const dl = seriesColor(t, 1);
  return (
    <VizFrame empty={!rows.length} state={state} label={label ?? auto} group={false} style={[{ gap: 4 }, style]}>
      <View onLayout={onLayout}>
        <View style={{ height: Math.round(16 * fs), marginLeft: lw }}>
          {w > 0 ? ticks.filter((iso, i) => i === 0 || xOf(iso) + tw <= trackW + 4).map((iso) => (
            <Text key={iso} numberOfLines={1} style={[vizText.axis, { position: 'absolute', left: xOf(iso), width: tw, color: iso === today ? t.ink : t.ink3, fontWeight: iso === today ? '700' : '400' }]}>{md(iso)}</Text>
          )) : null}
        </View>
        <View style={{ paddingVertical: 4, gap: 10 }}>
          {w > 0 && todayN != null && todayN >= d0 && todayN <= d0 + span ? (
            <View pointerEvents="none" style={{ position: 'absolute', top: 0, bottom: 0, left: lw + xOf(today as string), width: 1.5, backgroundColor: t.gold }} />
          ) : null}
          {rows.map((r) => {
            const a = xOf(r.start ?? (today && dayNum(today) > d0 ? today : from));
            const b = xOf(r.end);
            const on = sel === r.key;
            return (
              <Pressable key={r.key} onPress={() => { setSel((k) => (k === r.key ? null : r.key)); onPressRow?.(r); }}
                accessibilityRole="button" accessibilityLabel={`${r.label} ${desc(r)}`} style={styles.row}>
                <Text numberOfLines={1} style={[vizText.axis, { width: lw, paddingRight: 6, fontSize: 12, color: on ? t.ink : t.ink2, fontWeight: on ? '600' : '400' }]}>{r.label}</Text>
                <View style={{ flex: 1, height: 18 }}>
                  {w > 0 ? (
                    <>
                      {b > a ? <View style={{ position: 'absolute', left: a, width: b - a, top: 6, height: 6, borderRadius: 3, backgroundColor: barColor, opacity: sel && !on ? 0.5 : 1 }} /> : null}
                      <View style={{ position: 'absolute', left: b - 5, top: 4, width: 10, height: 10, borderRadius: 2, backgroundColor: dl, transform: [{ rotate: '45deg' }], opacity: sel && !on ? 0.5 : 1 }} />
                    </>
                  ) : null}
                </View>
              </Pressable>
            );
          })}
        </View>
      </View>
      <View style={[styles.row, { gap: space.md, flexWrap: 'wrap', marginTop: 2 }]}>
        <View style={[styles.row, { gap: 5 }]}><View style={{ width: 14, height: 6, borderRadius: 3, backgroundColor: barColor }} /><Text style={[vizText.axis, { fontSize: 12, color: t.ink2 }]}>{L('剩余时间', 'Time left')}</Text></View>
        <View style={[styles.row, { gap: 5 }]}><View style={{ width: 8, height: 8, borderRadius: 1.5, backgroundColor: dl, transform: [{ rotate: '45deg' }] }} /><Text style={[vizText.axis, { fontSize: 12, color: t.ink2 }]}>{L('截止', 'Deadline')}</Text></View>
        {today ? <View style={[styles.row, { gap: 5 }]}><View style={{ width: 1.5, height: 12, backgroundColor: t.gold }} /><Text style={[vizText.axis, { fontSize: 12, color: t.ink2 }]}>{L('今天', 'Today')}</Text></View> : null}
      </View>
      {picked ? <Text style={[vizText.axis, { fontSize: 12, color: t.ink }]}>{`${picked.label} · ${desc(picked)}`}</Text> : null}
    </VizFrame>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center' },
});
