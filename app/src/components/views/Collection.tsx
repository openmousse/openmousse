// 集合块（积木 type collection，server/views.py）：一张表、几种视图（表格 · 看板 · 日历 · 时间线 · 图表），顶上一条切换条。
// 看板页上是缩略的样子（数据随看板 GET 一起来），「查看全部」和右上角的展开进全屏页（CollectionScreen，看板能拖卡片）。
// 你点的排序、筛选、翻月、翻页由服务器重算（POST /api/boards/{agent}/view，feature boards.views）；老服务器上只看默认的样子。
// 不认识的视图种类（服务器比 app 新）显示「需要更新 app」。
import React, { useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, View } from 'react-native';
import {
  boardsApi, type Block, type BoardColumn, type BoardRow, type BoardViewData, type CalendarData, type ChartsData, type TableData,
  type TimelineData, type ViewCard, type ViewData, type ViewQuery, type ViewSpec,
} from '../../api/boards';
import { supports } from '../../api/version';
import { L } from '../../i18n';
import { navigationRef } from '../../navigation';
import { radius, space, useTheme } from '../../theme';
import { useBoard } from '../blocks/ctx';
import { openRow } from '../blocks/RowSheet';
import { CalendarIcon, ChartColumn, ChartGantt, Columns3, Maximize2, Table as TableIcon } from '../icons';
import { useSheet } from '../Sheet';
import { PullRefresh, showError, T } from '../ui';
import { CalendarView } from './CalendarView';
import { CardItem } from './Card';
import { ChartsView } from './ChartsView';
import type { ViewEnv } from './env';
import { KanbanView } from './KanbanView';
import { pickedView, pickView } from './palette';
import { TableView } from './TableView';
import { TimelineView } from './TimelineView';

const KNOWN = ['table', 'board', 'calendar', 'timeline', 'chart'];
const TYPE_LABEL: Record<string, [string, string]> = { table: ['表格', 'Table'], board: ['看板', 'Board'], calendar: ['日历', 'Calendar'], timeline: ['时间线', 'Timeline'], chart: ['图表', 'Charts'] };
const ICON = { table: TableIcon, board: Columns3, calendar: CalendarIcon, timeline: ChartGantt, chart: ChartColumn } as Record<string, typeof TableIcon>;

export const viewLabel = (spec: ViewSpec, data?: ViewData) => data?.label || spec.title || (TYPE_LABEL[spec.type] ? L(...TYPE_LABEL[spec.type]) : spec.type);

/** 进全屏页时先把这一块记下：页面一打开就有东西画，服务器的数据回来再换。 */
const opened = new Map<string, Block>();
export const openedBlock = (agent: string, block: string) => opened.get(`${agent}:${block}`);

export function openCollection(agent: string, block: Block, view?: string) {
  opened.set(`${agent}:${block.id}`, block);
  navigationRef.navigate('Collection', { agent, block: block.id, ...(view ? { view } : {}) });
}

/** 视图切换条：图标 + 名字，选中的金色下划线（画布第 6 排）。 */
export function ViewTabs({ views, value, onChange, labels, right }: {
  views: ViewSpec[]; value: string; onChange: (id: string) => void; labels: Record<string, string>; right?: React.ReactNode;
}) {
  const t = useTheme();
  return (
    <View style={[styles.tabsRow, { borderBottomColor: t.line }]}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.tabs} style={{ flex: 1 }} accessibilityRole="tablist">
        {views.map((v) => {
          const on = v.id === value;
          const Icon = ICON[v.type] ?? TableIcon;
          const color = on ? t.ink : t.ink3;
          return (
            <Pressable key={v.id} onPress={() => onChange(v.id)} accessibilityRole="tab" accessibilityState={{ selected: on }}
              style={[styles.tab, { borderBottomColor: on ? t.gold : 'transparent' }]}>
              <Icon size={16} color={color} strokeWidth={2} />
              <T v="callout" style={{ fontSize: 14, fontWeight: '600', color }}>{labels[v.id]}</T>
            </Pressable>
          );
        })}
      </ScrollView>
      {right}
    </View>
  );
}

/**
 * 一个集合块：切换条 + 当前视图。full = 全屏页（每个视图向服务器要完整的样子；看板能拖）。
 * 看板页上用 CollectionBody；全屏页在 BoardProvider 里用它，好让点开一行、改、删照常重读看板。
 */
export function CollectionPane({ agent, block, full, viewId, onView, onOpenFull, setDragging }: {
  agent: string; block: Block; full: boolean; viewId: string; onView: (id: string) => void; onOpenFull?: (view: string) => void; setDragging?: (on: boolean) => void;
}) {
  const t = useTheme();
  const ctx = useBoard();
  const sheet = useSheet();
  const readOnly = !!ctx?.readOnly;
  const views = block.views ?? [];
  const spec = views.find((v) => v.id === viewId) ?? views[0];
  const [fetched, setFetched] = useState<Record<string, ViewData>>({});
  const [failed, setFailed] = useState<string | null>(null);
  const live = supports('boards.views') && !readOnly;
  // 全屏页：每个视图第一次显示、看板重读以后都向服务器要完整的样子；回来之前先画看板 GET 里带的
  useEffect(() => {
    if (!full || !spec || !live) return;
    let on = true;
    boardsApi.view(agent, { block: block.id, view: spec.id, full: true })
      .then((r) => { if (on) { setFetched((m) => ({ ...m, [spec.id]: r.view })); setFailed(null); } })
      .catch((e) => { if (on) setFailed(e instanceof Error ? e.message : String(e)); });
    return () => { on = false; };
  }, [full, live, agent, block, spec]);
  if (!spec) return <T v="callout" color={t.ink2}>{L('此集合块没有视图。', 'This collection has no views.')}</T>;
  const views0 = block.data.views ?? {};
  const data: ViewData | undefined = (full ? fetched[spec.id] : undefined) ?? views0[spec.id];
  const labels = Object.fromEntries(views.map((v) => [v.id, viewLabel(v, views0[v.id])]));
  const group = spec.group ?? (data && data.type === 'board' ? (data as BoardViewData).group : undefined);

  const query = live ? (q: Partial<ViewQuery>) => boardsApi.view(agent, { block: block.id, view: spec.id, full, ...q }).then((r) => r.view) : null;
  const reload = async () => {
    await ctx?.reload();
  };
  const moveCard = async (card: ViewCard, to: BoardColumn) => {
    if (!group) return;
    try {
      await boardsApi.patchRow(card.id, { [group]: to.key });
    } catch (e) {
      showError(L('修改失败', "Couldn't update"), e);
      throw e;
    }
    await reload();
  };
  const openCard = (card: ViewCard, col?: BoardColumn) => {
    if (!ctx || readOnly) return;
    const row: BoardRow = { id: card.id, title: card.title, sub: card.meta, data: card.data, display: card.display };
    const cols = data && data.type === 'board' ? (data as BoardViewData).columns : [];
    const movable = !!col && !!group && !block.data.readonly && cols.some((c) => c.drop && c.id !== col.id);
    openRow(sheet, ctx, block, row, movable ? {
      label: (data as BoardViewData).groupLabel,
      options: cols.filter((c) => c.drop).map((c) => ({ key: c.id, label: c.label, on: c.id === col?.id })),
      pick: (id: string) => {
        const to = cols.find((c) => c.id === id);
        return to ? boardsApi.patchRow(card.id, { [group as string]: to.key }) : Promise.resolve();
      },
    } : undefined);
  };
  const drill = (chart: number, key: string | null, label: string) => {
    sheet.open({ title: label, content: () => <BucketList agent={agent} block={block} view={spec.id} chart={chart} bucket={key} onOpen={(c) => openCard(c)} /> });
  };
  const env: ViewEnv = {
    agent, block, viewId: spec.id, full, readOnly, query, openCard, drill, moveCard,
    openFull: onOpenFull ? () => onOpenFull(spec.id) : undefined, setDragging,
  };
  const body = !data ? (
    <View style={[styles.note, { backgroundColor: t.surface }]}>
      {failed ? <T v="callout" color={t.bad}>{L(`无法加载：${failed}`, `Couldn't load: ${failed}`)}</T> : <ActivityIndicator color={t.ink3} />}
    </View>
  ) : <ViewBody env={env} data={data} />;
  const expand = onOpenFull && !readOnly ? (
    <Pressable onPress={() => onOpenFull(spec.id)} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('全屏查看', 'Open full screen')} style={styles.expand}>
      <T v="caption" color={t.ink3} style={{ fontWeight: '400', fontVariant: ['tabular-nums'] }}>{L(`${block.data.total ?? 0} 条`, `${block.data.total ?? 0}`)}</T>
      <Maximize2 size={15} color={t.ink3} />
    </Pressable>
  ) : null;
  const tabs = views.length > 1 || expand ? <ViewTabs views={views} value={spec.id} onChange={onView} labels={labels} right={expand} /> : null;
  if (!full) {
    return (
      <View style={{ gap: space.md }}>
        {tabs}
        {body}
      </View>
    );
  }
  // 全屏：看板自己横竖滚（要拖），别的视图整页竖着滚
  return (
    <View style={{ flex: 1 }}>
      <View style={{ paddingHorizontal: space.lg }}>{tabs}</View>
      {data?.type === 'board' && !data.error ? (
        <View style={{ flex: 1, paddingHorizontal: space.lg, paddingTop: 14, paddingBottom: space.lg }}>{body}</View>
      ) : (
        <ScrollView contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl }} refreshControl={<PullRefresh onRefresh={reload} />}>{body}</ScrollView>
      )}
    </View>
  );
}

function ViewBody({ env, data }: { env: ViewEnv; data: ViewData }) {
  const t = useTheme();
  if (data.error) {
    return <View style={[styles.note, { backgroundColor: t.surface }]}><T v="callout" color={t.bad}>{L(`此视图出错：${data.error}`, `This view has an error: ${data.error}`)}</T></View>;
  }
  if (!KNOWN.includes(data.type)) {
    return <View style={[styles.note, { backgroundColor: t.surface }]}><T v="callout" color={t.ink2}>{L('此视图需要更新 app 后才能显示。', 'This view needs a newer version of the app.')}</T></View>;
  }
  if (data.type === 'table') return <TableView env={env} data={data as TableData} />;
  if (data.type === 'board') return <KanbanView env={env} data={data as BoardViewData} />;
  if (data.type === 'calendar') return <CalendarView env={env} data={data as CalendarData} />;
  if (data.type === 'timeline') return <TimelineView env={env} data={data as TimelineData} />;
  return <ChartsView env={env} data={data as ChartsData} />;
}

/** 图表上点开的一段：有哪些行（点一行看详情）。 */
function BucketList({ agent, block, view, chart, bucket, onOpen }: { agent: string; block: Block; view: string; chart: number; bucket: string | null; onOpen: (c: ViewCard) => void }) {
  const t = useTheme();
  const [res, setRes] = useState<{ title: string; total: number; rows: ViewCard[] } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let on = true;
    boardsApi.bucket(agent, { block: block.id, view, chart, bucket }).then((r) => { if (on) setRes(r); }, (e) => { if (on) setErr(e instanceof Error ? e.message : String(e)); });
    return () => { on = false; };
  }, [agent, block.id, view, chart, bucket]);
  if (err) return <T v="callout" color={t.bad}>{L(`无法加载：${err}`, `Couldn't load: ${err}`)}</T>;
  if (!res) return <ActivityIndicator color={t.ink3} style={{ marginVertical: space.lg }} />;
  return (
    <View style={{ gap: 8 }}>
      <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L(`共 ${res.total} 条`, `${res.total} in total`)}</T>
      {res.rows.map((c) => <CardItem key={c.id} card={c} onPress={() => onOpen(c)} />)}
      {res.total > res.rows.length ? <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L(`仅显示前 ${res.rows.length} 条`, `Showing the first ${res.rows.length}`)}</T> : null}
    </View>
  );
}

/** 看板页上的集合块：切换条（记住上次看的视图）+ 缩略的视图；右上角进全屏页。 */
export function CollectionBody({ block }: { block: Block }) {
  const ctx = useBoard();
  const agent = ctx?.agent ?? '';
  const views = block.views ?? [];
  const [picked, setPicked] = useState(() => pickedView(agent, block.id));
  const viewId = views.some((v) => v.id === picked) ? (picked as string) : block.view && views.some((v) => v.id === block.view) ? block.view : views[0]?.id ?? '';
  const onView = (id: string) => {
    setPicked(id);
    pickView(agent, block.id, id);
  };
  return (
    <CollectionPane agent={agent} block={block} full={false} viewId={viewId} onView={onView}
      onOpenFull={ctx && !ctx.readOnly ? (v) => openCollection(agent, block, v) : undefined} />
  );
}

const styles = StyleSheet.create({
  tabsRow: { flexDirection: 'row', alignItems: 'center', borderBottomWidth: StyleSheet.hairlineWidth },
  tabs: { gap: 18, paddingRight: space.sm },
  tab: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingTop: 8, paddingBottom: 10, borderBottomWidth: 2 },
  expand: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingLeft: space.sm, paddingVertical: 6 },
  note: { borderRadius: radius.lg, padding: space.lg, alignItems: 'center' },
});
