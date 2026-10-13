// 表格视图：表头点一下按这一列排（再点倒过来，第三下回到视图自己的排法），筛选按选项字段和日期；选项字段画成标签；点一行看详情。
// 排序、筛选只在这台设备上临时用（不改视图配置），由服务器重算（POST /api/boards/{agent}/view）。
import React, { useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import type { FieldDef, TableColumn, TableData, TableRowData, ViewData } from '../../api/boards';
import { L } from '../../i18n';
import { radius, space, type, useTheme } from '../../theme';
import { ArrowDown, ArrowUp, ListFilter, X } from '../icons';
import { useSheet } from '../Sheet';
import { showError, T } from '../ui';
import type { ViewProps } from './env';
import { seriesTag } from './palette';

const INLINE_ROWS = 6;
type Filter = { key: string; label: string; clause: unknown[]; text: string };

/** 一列多宽（flex）：标题这类长文字宽一些，日期、数字窄一些。列多时整张表横着滚。 */
const weight = (c: TableColumn, i: number) => (c.type === 'date' || c.type === 'datetime' ? 0.9 : c.type === 'number' || c.type === 'money' ? 0.75 : c.type === 'choice' || c.type === 'bool' ? 0.9 : i === 1 ? 1.5 : 1.2);

export function TableView({ env, data: initial }: ViewProps<TableData>) {
  const t = useTheme();
  const sheet = useSheet();
  const [data, setData] = useState(initial);
  const [seen, setSeen] = useState(initial);
  const [sort, setSort] = useState<string[] | null>(null);  // null = 视图自己的排法
  const [filters, setFilters] = useState<Filter[]>([]);
  const [busy, setBusy] = useState(false);
  if (initial !== seen) {  // 看板重读了：用新数据（你点过的排序、筛选由 rerun 重新算）
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
  const tapHeader = (c: TableColumn) => {
    if (!c.sortable || !env.query) return;
    const cur = (sort ?? data.sort)[0] ?? '';
    const next = cur === c.key ? [`-${c.key}`] : cur === `-${c.key}` && sort ? null : [c.key];
    setSort(next);
    run(next, filters);
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
  const many = data.columns.length > 4;
  const sorted = (sort ?? data.sort)[0] ?? '';
  const table = (
    <View style={many ? { width: data.columns.length * 120 } : { flex: 1 }}>
      <View style={[styles.row, styles.headRow, { borderBottomColor: t.line }]}>
        {data.columns.map((c, i) => {
          const dir = sorted === c.key ? 'asc' : sorted === `-${c.key}` ? 'desc' : null;
          return (
            <Pressable key={c.key} onPress={() => tapHeader(c)} disabled={!c.sortable || !env.query} accessibilityRole="button"
              accessibilityLabel={L(`按「${c.label}」排序`, `Sort by ${c.label}`)} accessibilityState={{ selected: !!dir }}
              style={[styles.cell, { flex: weight(c, i), flexDirection: 'row', gap: 2, justifyContent: c.align === 'right' ? 'flex-end' : 'flex-start' }]}>
              <Text numberOfLines={1} style={[type.caption, { color: dir ? t.ink : t.ink3, fontWeight: dir ? '700' : '500' }]}>{c.label}</Text>
              {dir === 'asc' ? <ArrowUp size={12} color={t.ink} /> : dir === 'desc' ? <ArrowDown size={12} color={t.ink} /> : null}
            </Pressable>
          );
        })}
      </View>
      {rows.map((r, ri) => <Row key={r.id} r={r} cols={data.columns} last={ri === rows.length - 1} onPress={env.readOnly ? undefined : () => env.openCard(r)} />)}
      {!rows.length ? <T v="callout" color={t.ink2} style={{ paddingVertical: space.lg, paddingHorizontal: 4 }}>{filters.length ? L('没有符合筛选条件的行。', 'No rows match the filter.') : L('暂无数据', 'No data yet')}</T> : null}
    </View>
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
          {busy ? <ActivityIndicator size="small" color={t.ink3} /> : null}
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

function Row({ r, cols, last, onPress }: { r: TableRowData; cols: TableColumn[]; last: boolean; onPress?: () => void }) {
  const t = useTheme();
  return (
    <Pressable onPress={onPress} disabled={!onPress} accessibilityRole={onPress ? 'button' : undefined} accessibilityLabel={cols.map((c) => r.cells[c.key]).join('，')}
      style={({ pressed }) => [styles.row, !last && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }, { opacity: pressed ? 0.6 : 1 }]}>
      {cols.map((c, i) => {
        const v = r.cells[c.key] ?? '—';
        const color = r.colors?.[c.key];
        if (c.type === 'choice' && v !== '—') {
          const [bg, fg] = seriesTag(t, color);
          return (
            <View key={c.key} style={[styles.cell, { flex: weight(c, i), alignItems: c.align === 'right' ? 'flex-end' : 'flex-start' }]}>
              <View style={[styles.tag, { backgroundColor: bg }]}><Text numberOfLines={1} style={[type.caption, { fontSize: 11, fontWeight: '600', color: fg }]}>{v}</Text></View>
            </View>
          );
        }
        return (
          <Text key={c.key} numberOfLines={1} style={[type.callout, styles.cell, { flex: weight(c, i), fontSize: 13, textAlign: c.align, color: i === 0 ? t.ink : t.ink2,
            fontVariant: c.align === 'right' ? ['tabular-nums'] : undefined }]}>{v}</Text>
        );
      })}
    </Pressable>
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
  row: { flexDirection: 'row', alignItems: 'center', minHeight: 40 },
  headRow: { borderBottomWidth: 1, minHeight: 34 },
  cell: { paddingHorizontal: 4, paddingVertical: 8, minWidth: 0 },
  tag: { borderRadius: 6, paddingHorizontal: 6, paddingVertical: 2, maxWidth: '100%' },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: space.sm, alignItems: 'center' },
  chip: { flexDirection: 'row', alignItems: 'center', gap: 5, height: 30, borderRadius: 15, paddingHorizontal: 12, maxWidth: '100%' },
  more: { alignItems: 'center', paddingVertical: space.sm },
  opts: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  opt: { height: 34, paddingHorizontal: 14, borderRadius: 17, borderWidth: StyleSheet.hairlineWidth, alignItems: 'center', justifyContent: 'center' },
});
