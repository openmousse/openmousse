// 图表卡：标题 + 右边一行小字（单位、来源）或控件 + 图；给了 table 就在右上角多一个「表格 / 图表」切换（每张图的表格视图）。
import React, { useState } from 'react';
import { Pressable, StyleSheet, View, type StyleProp, type ViewStyle } from 'react-native';
import { L } from '../../i18n';
import { radius, space, useTheme } from '../../theme';
import { ChartColumn, Table } from '../icons';
import { T } from '../ui';

export function ChartCard({ title, caption, right, table, children, style }: {
  title?: string;
  /** 标题右边的小字（「单位：个」「近 7 天」） */
  caption?: string;
  /** 标题右边的控件（时间范围切换等），有它就不写 caption */
  right?: React.ReactNode;
  /** 同一份数据的表格（DataTable）：右上角切换 */
  table?: React.ReactNode;
  children: React.ReactNode;
  style?: StyleProp<ViewStyle>;
}) {
  const t = useTheme();
  const [asTable, setAsTable] = useState(false);
  const Icon = asTable ? ChartColumn : Table;
  return (
    <View style={[styles.card, { backgroundColor: t.surface }, style]}>
      {title || caption || right || table ? (
        <View style={styles.head}>
          {title ? <T v="headline" numberOfLines={1} style={{ fontSize: 15, fontWeight: '700', flexShrink: 1 }}>{title}</T> : <View />}
          <View style={styles.right}>
            {right ?? (caption ? <T v="caption" color={t.ink3} numberOfLines={1} style={{ fontWeight: '400', flexShrink: 1 }}>{caption}</T> : null)}
            {table ? (
              <Pressable onPress={() => setAsTable((v) => !v)} hitSlop={10} accessibilityRole="button"
                accessibilityLabel={asTable ? L('显示图表', 'Show chart') : L('显示表格', 'Show table')}
                style={({ pressed }) => [styles.toggle, { backgroundColor: t.surface2, opacity: pressed ? 0.6 : 1 }]}>
                <Icon size={15} color={t.ink2} />
              </Pressable>
            ) : null}
          </View>
        </View>
      ) : null}
      {asTable && table ? table : children}
    </View>
  );
}

const styles = StyleSheet.create({
  card: { borderRadius: radius.lg - 2, padding: 14, gap: space.md },
  head: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: space.sm, minHeight: 24 },
  right: { flexDirection: 'row', alignItems: 'center', gap: space.sm, flexShrink: 1 },
  toggle: { width: 28, height: 28, borderRadius: 8, alignItems: 'center', justifyContent: 'center' },
});
