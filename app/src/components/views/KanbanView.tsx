// 看板视图：按一个字段分列。看板页上用素材库的 Kanban（缩略：每列前三张，点卡片看详情，长按「移到…」）；
// 全屏页用 KanbanBoard（每列竖着滚，长按卡片拖到别的列）。两种都是改这一行的 group 字段（PATCH /api/rows/{id}），
// 松手 / 点了先挪过去，写失败再挪回来。
import React, { useMemo, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import type { BoardColumn, BoardViewData, ViewCard } from '../../api/boards';
import { L } from '../../i18n';
import { radius, space, useTheme } from '../../theme';
import { T } from '../ui';
import { Kanban, KanbanBoard, type KanbanBoardColumn, type KanbanCard } from '../viz';
import type { ViewProps } from './env';

const INLINE_CARDS = 3;
const EMPTY = '__empty';  // 没填的那一列：列的 id 是空字符串，素材库的 key 不能空

/** 松手以后、服务器回来以前：卡片先放到目标列的最前面，计数跟着变。 */
function withPending(cols: BoardColumn[], pending: Record<string, string>): BoardColumn[] {
  const ids = Object.keys(pending);
  if (!ids.length) return cols;
  const moving = new Map<string, ViewCard>();
  for (const c of cols) for (const k of c.cards) if (pending[k.id] !== undefined && pending[k.id] !== c.id) moving.set(k.id, k);
  return cols.map((c) => {
    const out = c.cards.filter((k) => !moving.has(k.id));
    const inc = [...moving.values()].filter((k) => pending[k.id] === c.id);
    return { ...c, cards: [...inc, ...out], count: c.count - (c.cards.length - out.length) + inc.length };
  });
}

const keyOf = (c: BoardColumn) => c.id || EMPTY;
const kitCard = (k: ViewCard): KanbanCard => ({ key: k.id, title: k.title, sub: k.meta, tags: k.tags });
const kitCol = (c: BoardColumn): KanbanBoardColumn => ({
  key: keyOf(c), title: c.label, slot: c.color ?? -1, cards: c.cards.map(kitCard), total: c.count, drop: c.drop, more: c.more,
});

export function KanbanView({ env, data }: ViewProps<BoardViewData>) {
  const t = useTheme();
  const [pending, setPending] = useState<Record<string, string>>({});
  const [seen, setSeen] = useState(data);
  if (seen !== data) {  // 新数据回来了：服务器那边已经是挪过以后的样子
    setSeen(data);
    setPending({});
  }
  const cols = useMemo(() => withPending(data.columns, pending), [data, pending]);
  const kit = useMemo(() => cols.map(kitCol), [cols]);
  const cardById = new Map(cols.flatMap((c) => c.cards.map((k) => [k.id, k] as const)));
  const colByKey = new Map(cols.map((c) => [keyOf(c), c]));
  const movable = data.movable && !env.readOnly;
  const move = (card: KanbanCard, from: KanbanBoardColumn, to: KanbanBoardColumn) => {
    const k = cardById.get(card.key);
    const target = colByKey.get(to.key);
    if (!k || !target || to.key === from.key || !target.drop) return;
    setPending((m) => ({ ...m, [k.id]: target.id }));
    env.moveCard(k, target).catch(() => setPending((m) => { const n = { ...m }; delete n[k.id]; return n; }));
  };
  const press = env.readOnly ? undefined : (card: KanbanCard, col: KanbanBoardColumn) => {
    const k = cardById.get(card.key);
    if (k) env.openCard(k, colByKey.get(col.key));
  };
  const empty = !cols.some((c) => c.count > 0);
  return (
    <View style={[{ gap: space.sm }, env.full && { flex: 1 }]}>
      {env.full ? (
        <KanbanBoard columns={kit} onPressCard={press} onMove={movable ? move : undefined} onDragging={env.setDragging} />
      ) : (
        <View style={[styles.card, { backgroundColor: t.surface }]}>
          <Kanban columns={kit} limit={INLINE_CARDS} onPressCard={press} onMove={movable ? move : undefined} />
        </View>
      )}
      {empty ? <T v="callout" color={t.ink2} style={{ paddingHorizontal: 4 }}>{L('暂无数据', 'No data yet')}</T> : null}
      {data.hidden > 0 ? <T v="caption" color={t.ink3} style={{ fontWeight: '400', paddingHorizontal: 4 }}>{L(`另有 ${data.hidden} 条不在以上各列`, `${data.hidden} more not in these columns`)}</T> : null}
      {env.full && movable ? <T v="caption" color={t.ink3} style={{ fontWeight: '400', textAlign: 'center' }}>{L('长按卡片拖到其他列，即更新该字段', 'Long-press a card and drag it to another column to change that field')}</T> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  card: { borderRadius: radius.lg, padding: space.sm },
});
