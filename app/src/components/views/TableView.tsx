// 表格视图：素材库的 DataTable。表头点一下按这一列排（再点倒过来），筛选按选项字段和日期；选项字段画成分类色的标签
// （和看板列头同一种颜色）；点一行看详情。排序、筛选只在这台设备上临时用（不改视图配置），由服务器重算（POST /api/boards/{agent}/view）。
import React, { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import type { FieldDef, TableColumn as ViewColumn, TableData, TableRowData, ViewData } from '../../api/boards';
import { L } from '../../i18n';
import { radius, space, type, useTheme } from '../../theme';
import { ListFilter, X } from '../icons';
import { useSheet } from '../Sheet';
import { showError, T } from '../ui';
import { DataTable, type TableColumn } from '../viz';
import type { ViewProps } from './env';

const INLINE_ROWS = 6;
type Filter = { key: string; label: string; clause: unknown[]; text: string };

/** 一列多宽（flex）：标题这类长文字宽一些，日期、数字窄一些。列多时整张表横着滚。 */
const weight = (c: ViewColumn, i: number) => (c.type === 'date' || c.type === 'datetime' ? 0.9 : c.type === 'number' || c.type === 'money' ? 0.75 : c.type === 'choice' || c.type === 'bool' ? 0.9 : i === 1 ? 1.5 : 1.2);

export function TableView({ env, data: initial }: ViewProps<TableData>) {
  const t = useTheme();
  const sheet = useSheet();
  const [data, setData] = useState(initial);
  const [seen, setSeen] = useState(initial);
  const [sort, setSort] = useState<string[] | null>(null);  // null = 视图自己的排法
  const [filters, setFilters] = useState<Filter[]>([]);
  const [busy, setBusy] = useState(false);
  if (initial !== seen) {  // 看板重读了：用新数据（你点过的排序、筛选要再点一次才按新数据重算）
    setSeen(initial);
    if (!sort && !filters.length) setData(initial);
  }
  const fields = env.block.data.fields ?? [];
  const run = (nextSort: string[] | null, nextFilters: Filter[], more = false) => {
    if (!env.query) return;
    setBusy(true);
    env.query({ sort: nextSort ?? undefined, where: nextFilters.map((f) => f.clause), full: env.full, ...(more ? { offset: data.rows.length } : {}) })
      .then((v: ViewData | null) => {
        if (!v || v.type !== 'table') return;
        const d = v as TableData & { label: string };
        setData(more ? { ...d, rows: [...data.rows, ...d.rows] } : d);
      })
      .catch((e) => showError(L('无法加载', "Couldn't load"), e))
      .finally(() => setBusy(false));
  };
  const applyFilters = (next: Filter[]) => {
    setFilters(next);
    run(sort, next);
  };
  const openFilter = () => sheet.open({
    title: L('筛选', 'Filter'),
    content: () => <FilterSheet fields={fields} initial={filters} onChange={applyFilters} />,
  });
  const rows = env.full ? data.rows : data.rows.slice(0, INLINE_ROWS);
  const hidden = data.total - rows.length;
  const first = (sort ?? data.sort)[0] ?? '';
  const current = first ? { key: first.replace(/^-/, ''), dir: (first.startsWith('-') ? 'desc' : 'asc') as 'asc' | 'desc' } : null;
  // 表头点一下按这一列排，再点倒过来（服务器排好、要翻页，所以排序交给服务器）
  const columns: TableColumn<TableRowData>[] = data.columns.map((c, i) => ({
    key: c.key, title: c.label, flex: weight(c, i), numeric: c.align === 'right', primary: i === 0, sortable: c.sortable && !!env.query,
    text: (r) => r.cells[c.key] ?? '—',
    tag: c.type === 'choice' ? (r) => (r.cells[c.key] && r.cells[c.key] !== '—' ? { text: r.cells[c.key], slot: r.colors?.[c.key] ?? null } : null) : undefined,
  }));
  const many = data.columns.length > 4;
  const table = (
    <DataTable columns={columns} rows={rows} rowKey={(r) => r.id} onPressRow={env.readOnly ? undefined : (r) => env.openCard(r)} sort={current}
      onSort={env.query ? (s) => { const next = [s.dir === 'desc' ? `-${s.key}` : s.key]; setSort(next); run(next, filters); } : undefined}
      loading={busy} emptyText={filters.length ? L('没有符合筛选条件的行。', 'No rows match the filter.') : undefined}
      style={many ? { width: data.columns.length * 110 } : undefined} />
  );
  return (
    <View style={{ gap: space.sm }}>
      {!env.readOnly && env.query && fields.some(filterable) ? (
        <View style={styles.chips}>
          <Pressable onPress={openFilter} accessibilityRole="button" style={({ pressed }) => [styles.chip, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
            <ListFilter size={14} color={t.ink2} />
            <T v="caption" color={t.ink2} style={{ fontSize: 13 }}>{L('筛选', 'Filter')}</T>
          </Pressable>
          {filters.map((f) => (
            <Pressable key={f.key} onPress={() => applyFilters(filters.filter((x) => x.key !== f.key))} accessibilityRole="button" accessibilityLabel={L(`移除筛选：${f.label} ${f.text}`, `Remove filter: ${f.label} ${f.text}`)}
              style={({ pressed }) => [styles.chip, { backgroundColor: t.cyanSoft, opacity: pressed ? 0.7 : 1 }]}>
              <T v="caption" color={t.cyan} style={{ fontSize: 13, fontWeight: '600' }} numberOfLines={1}>{`${f.label}：${f.text}`}</T>
              <X size={13} color={t.cyan} />
            </Pressable>
          ))}
        </View>
      ) : null}
      <View style={[styles.card, { backgroundColor: t.surface }]}>
        {many ? <ScrollView horizontal showsHorizontalScrollIndicator={false}>{table}</ScrollView> : table}
      </View>
      {hidden > 0 ? (
        env.full ? (
          data.more > 0 ? (
            <Pressable onPress={() => run(sort, filters, true)} disabled={busy} accessibilityRole="button" style={({ pressed }) => [styles.more, { opacity: pressed ? 0.6 : 1 }]}>
              <T v="callout" color={t.gold} style={{ fontWeight: '700' }}>{L(`加载更多（另有 ${data.more} 行）`, `Load more (${data.more} more)`)}</T>
            </Pressable>
          ) : null
        ) : (
          <Pressable onPress={env.openFull} disabled={!env.openFull} accessibilityRole="button" style={({ pressed }) => [styles.more, { opacity: pressed ? 0.6 : 1 }]}>
            <T v="callout" color={t.ink2}>{L(`查看全部 ${data.total} 行`, `See all ${data.total} rows`)}</T>
          </Pressable>
        )
      ) : null}
    </View>
  );
}

const filterable = (f: FieldDef) => f.type === 'choice' || f.type === 'bool' || f.type === 'date' || f.type === 'datetime';

/** 筛选：选项字段挑几个值，日期按范围。一个字段一条，和视图自己的筛选叠加；每点一下就重算，点空白处关掉。 */
function FilterSheet({ fields, initial, onChange }: { fields: FieldDef[]; initial: Filter[]; onChange: (next: Filter[]) => void }) {
  const t = useTheme();
  const [active, setActive] = useState(initial);
  const onPick = (f: Filter | null, key: string) => {
    const next = [...active.filter((x) => x.key !== key), ...(f ? [f] : [])];
    setActive(next);
    onChange(next);
  };
  const dates = (f: FieldDef): [string, unknown[]][] => [
    [L('今天以后', 'From today'), [f.key, '>=', 'today']],
    [L('7 天内', 'Next 7 days'), [f.key, '<=', '+7d']],
    [L('30 天内', 'Next 30 days'), [f.key, '<=', '+30d']],
    [L('已过', 'Past'), [f.key, '<', 'today']],
    [L('没有日期', 'No date'), [f.key, 'empty']],
  ];
  return (
    <View style={{ gap: space.lg }}>
      {fields.filter(filterable).map((f) => {
        const cur = active.find((x) => x.key === f.key);
        if (f.type === 'date' || f.type === 'datetime') {
          return (
            <View key={f.key} style={{ gap: 6 }}>
              <T v="caption" color={t.ink2} style={{ fontSize: 13, fontWeight: '600' }}>{f.label}</T>
              <View style={styles.opts}>
                {dates(f).map(([label, clause]) => {
                  const on = cur?.text === label;
                  return (
                    <Pressable key={label} onPress={() => onPick(on ? null : { key: f.key, label: f.label, clause, text: label }, f.key)} accessibilityRole="button" accessibilityState={{ selected: on }}
                      style={[styles.opt, { backgroundColor: on ? t.ink : t.surface, borderColor: on ? t.ink : t.line }]}>
                      <Text style={[type.caption, { fontSize: 14, fontWeight: '600', color: on ? t.surface : t.ink2 }]}>{label}</Text>
                    </Pressable>
                  );
                })}
              </View>
            </View>
          );
        }
        const values: unknown[] = f.type === 'bool' ? [true, false] : f.options ?? [];
        const label = (v: unknown) => (v === true ? L('是', 'Yes') : v === false ? L('否', 'No') : String(v));
        const sel = cur && cur.clause[1] === 'in' ? (cur.clause[2] as unknown[]) : [];
        const apply = (next: unknown[]) =>
          onPick(next.length ? { key: f.key, label: f.label, clause: [f.key, 'in', next], text: next.map(label).join(L('、', ', ')) } : null, f.key);
        return (
          <View key={f.key} style={{ gap: 6 }}>
            <T v="caption" color={t.ink2} style={{ fontSize: 13, fontWeight: '600' }}>{f.label}</T>
            <View style={styles.opts}>
              {values.map((v) => {
                const on = sel.includes(v);
                return (
                  <Pressable key={label(v)} onPress={() => apply(on ? sel.filter((x) => x !== v) : [...sel, v])} accessibilityRole="button" accessibilityState={{ selected: on }}
                    style={[styles.opt, { backgroundColor: on ? t.ink : t.surface, borderColor: on ? t.ink : t.line }]}>
                    <Text style={[type.caption, { fontSize: 14, fontWeight: '600', color: on ? t.surface : t.ink2 }]}>{label(v)}</Text>
                  </Pressable>
                );
              })}
            </View>
          </View>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  card: { borderRadius: radius.lg, paddingHorizontal: space.md, paddingVertical: 2 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: space.sm, alignItems: 'center' },
  chip: { flexDirection: 'row', alignItems: 'center', gap: 5, height: 30, borderRadius: 15, paddingHorizontal: 12, maxWidth: '100%' },
  more: { alignItems: 'center', paddingVertical: space.sm },
  opts: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  opt: { height: 34, paddingHorizontal: 14, borderRadius: 17, borderWidth: StyleSheet.hairlineWidth, alignItems: 'center', justifyContent: 'center' },
});
