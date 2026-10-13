// 表格：表头点按排序（再点一次反过来），状态用标签，行点开看详情。数字列右对齐、等宽字形（要上下对齐）。
// 也是每张图的「表格视图」：读屏和不方便点按的人从这里读到每个数。
import React, { useState } from 'react';
import { Pressable, StyleSheet, Text, View, type StyleProp, type ViewStyle } from 'react-native';
import { L } from '../../i18n';
import { useTheme } from '../../theme';
import { slotColors, toneColors, VizFrame, vizText, type Tone, type VizStateProps } from './core';

export interface TableColumn<R> {
  key: string;
  title: string;
  /** 宽度比例（默认 1） */
  flex?: number;
  /** 数字列：右对齐、等宽字形 */
  numeric?: boolean;
  /** 这一格的字（不给就取 row[key]） */
  text?: (row: R) => string;
  /** 这一格画成标签：{ text, tone }，或按分类色 { text, slot }（选项字段：和看板列头同一种颜色） */
  tag?: (row: R) => { text: string; tone?: Tone | string | null; slot?: number | null } | null;
  /** 排序用的值（不给就按 text 排）；sortable = false 不能排 */
  sortValue?: (row: R) => number | string | null;
  sortable?: boolean;
  /** 主列（第一列）：主字色、加粗一点；其余列次要字色 */
  primary?: boolean;
}

export interface DataTableProps<R> extends VizStateProps {
  columns: TableColumn<R>[];
  rows: R[];
  rowKey: (row: R) => string;
  onPressRow?: (row: R) => void;
  /** 一开始按哪列排；给了 onSort 就是受控的：表头只显示这个，行照给的顺序（服务器排好的） */
  sort?: { key: string; dir: 'asc' | 'desc' } | null;
  /** 排序交给调用方（服务器排、要翻页的表）：点表头时调用，不在本地排 */
  onSort?: (sort: { key: string; dir: 'asc' | 'desc' }) => void;
  /** 最多显示几行（其余点「另有 N 条」展开） */
  limit?: number;
  label?: string;
  style?: StyleProp<ViewStyle>;
}

const cellText = <R,>(c: TableColumn<R>, r: R): string => {
  if (c.text) return c.text(r);
  const v = (r as Record<string, unknown>)[c.key];
  return v == null ? '' : String(v);
};

export function DataTable<R>({ columns, rows, rowKey, onPressRow, sort: initial = null, onSort, limit, label, style, ...state }: DataTableProps<R>) {
  const t = useTheme();
  const [own, setSort] = useState(initial);
  const sort = onSort ? initial : own;
  const [open, setOpen] = useState(false);
  const col = sort ? columns.find((c) => c.key === sort.key) : undefined;
  const sorted = col && !onSort ? [...rows].sort((a, b) => {
    const va = col.sortValue ? col.sortValue(a) : cellText(col, a);
    const vb = col.sortValue ? col.sortValue(b) : cellText(col, b);
    if (va == null && vb == null) return 0;
    if (va == null) return 1;  // 空的永远排在最后
    if (vb == null) return -1;
    const d = typeof va === 'number' && typeof vb === 'number' ? va - vb : String(va).localeCompare(String(vb), 'zh-Hans-CN', { numeric: true });
    return sort?.dir === 'desc' ? -d : d;
  }) : rows;
  const shown = limit && !open ? sorted.slice(0, limit) : sorted;
  const hidden = sorted.length - shown.length;
  const tap = (c: TableColumn<R>) => {
    if (c.sortable === false) return;
    if (onSort) { onSort(sort?.key === c.key ? { key: c.key, dir: sort.dir === 'asc' ? 'desc' : 'asc' } : { key: c.key, dir: 'asc' }); return; }
    setSort((s) => (s?.key === c.key ? { key: c.key, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key: c.key, dir: 'asc' }));
  };
  const line = { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line };
  return (
    <VizFrame empty={!rows.length} state={state} label={label} group={false} style={style}>
      <View style={[styles.row, line]} accessibilityRole="header">
        {columns.map((c) => {
          const on = sort?.key === c.key;
          const arrow = on ? (sort?.dir === 'asc' ? ' ↑' : ' ↓') : '';
          return (
            <Pressable key={c.key} onPress={() => tap(c)} disabled={c.sortable === false} hitSlop={6}
              accessibilityRole="button" accessibilityLabel={`${c.title}${on ? (sort?.dir === 'asc' ? L('，升序', ', ascending') : L('，降序', ', descending')) : ''}`}
              style={[styles.cell, { flex: c.flex ?? 1, paddingVertical: 6 }]}>
              <Text numberOfLines={1} style={[vizText.axis, { fontSize: 12, color: on ? t.ink2 : t.ink3, fontWeight: on ? '600' : '400', textAlign: c.numeric ? 'right' : 'left' }]}>{`${c.title}${arrow}`}</Text>
            </Pressable>
          );
        })}
      </View>
      {shown.map((r, i) => {
        const cells = columns.map((c) => {
          const tag = c.tag?.(r);
          if (tag) {
            const [bg, fg] = tag.slot !== undefined ? slotColors(t, tag.slot) : toneColors(t, tag.tone);
            return (
              <View key={c.key} style={[styles.cell, { flex: c.flex ?? 1, alignItems: c.numeric ? 'flex-end' : 'flex-start' }]}>
                <View style={[styles.tag, { backgroundColor: bg }]}><Text numberOfLines={1} style={[vizText.value, { fontSize: 11, fontWeight: '600', color: fg }]}>{tag.text}</Text></View>
              </View>
            );
          }
          return (
            <View key={c.key} style={[styles.cell, { flex: c.flex ?? 1 }]}>
              <Text numberOfLines={1} style={{ fontSize: 13, color: c.primary ? t.ink : t.ink2, fontWeight: c.primary ? '600' : '400', textAlign: c.numeric ? 'right' : 'left', fontVariant: c.numeric ? ['tabular-nums'] : undefined }}>{cellText(c, r)}</Text>
            </View>
          );
        });
        const a11y = columns.map((c) => `${c.title} ${c.tag?.(r)?.text ?? cellText(c, r)}`).join(L('，', ', '));
        const rowStyle = [styles.row, i < shown.length - 1 || hidden > 0 ? line : null, { minHeight: 40 }];
        return onPressRow ? (
          <Pressable key={rowKey(r)} onPress={() => onPressRow(r)} accessibilityRole="button" accessibilityLabel={a11y}
            style={({ pressed }) => [...rowStyle, { opacity: pressed ? 0.6 : 1 }]}>{cells}</Pressable>
        ) : <View key={rowKey(r)} style={rowStyle} accessible accessibilityLabel={a11y}>{cells}</View>;
      })}
      {hidden > 0 || (open && limit && sorted.length > limit) ? (
        <Pressable onPress={() => setOpen((v) => !v)} accessibilityRole="button" accessibilityState={{ expanded: open }} style={[styles.row, { minHeight: 40 }]}>
          <Text style={{ fontSize: 13, fontWeight: '600', color: t.ink }}>{open ? L('收起', 'Show less') : L(`另有 ${hidden} 条`, `${hidden} more`)}</Text>
        </Pressable>
      ) : null}
    </VizFrame>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center' },
  cell: { paddingHorizontal: 4, paddingVertical: 8, justifyContent: 'center', minWidth: 0 },
  tag: { borderRadius: 6, paddingHorizontal: 6, paddingVertical: 2, maxWidth: '100%' },
});
