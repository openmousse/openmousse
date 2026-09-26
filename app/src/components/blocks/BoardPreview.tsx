// 收件箱卡片里的预览（只看，不能点）：
// - 「看板」提案（kind block）：提案那一版里新加或改过的块，用现在的真实数据画；
// - 建 Agent 的方案（kind agent）：Agent 还没建，按方案里的表和示例行在服务器内存里算出来的整页看板；
// - 提醒（kind push）：到点推出来的那条通知长什么样（按现在的数据）。
import React, { useEffect, useState } from 'react';
import { ActivityIndicator, StyleSheet, View } from 'react-native';
import { boardsApi, type Board, type BoardAlert } from '../../api/boards';
import { agentName } from '../../brand';
import { L } from '../../i18n';
import { radius, space, useTheme } from '../../theme';
import { Bell } from '../icons';
import { T } from '../ui';
import { BoardProvider } from './BoardContext';
import { BlockView } from './Blocks';

const noop = async () => {};

export function BoardPreview({ inboxId, plan = false }: { inboxId: string; plan?: boolean }) {
  const t = useTheme();
  const [board, setBoard] = useState<Board | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    (plan ? boardsApi.plan(inboxId) : boardsApi.proposal(inboxId)).then((b) => { if (live) setBoard(b); }, () => { if (live) setFailed(true); });
    return () => { live = false; };
  }, [inboxId, plan]);
  if (failed) return null;  // 老服务器、或者这张卡没有预览：照常显示文字
  if (!board) return <View style={[styles.box, { backgroundColor: t.bg, alignItems: 'center' }]}><ActivityIndicator color={t.ink3} /></View>;
  const changed = new Set(board.changed ?? board.blocks.map((b) => b.id));
  const blocks = board.blocks.filter((b) => changed.has(b.id) && !b.hidden);
  const removed = board.removed ?? [];
  if (!blocks.length && !removed.length) return null;
  const head = plan
    ? (board.sample ? L('看板预览 · 按示例数据画的', 'Board preview · drawn from sample data') : L('看板预览 · 还没有数据，先是空的样子', 'Board preview · no data yet, so it starts empty'))
    : L('预览 · 用你现在的数据画的', 'Preview · drawn with your current data');
  return (
    <View style={[styles.box, { backgroundColor: t.bg }]}>
      <T v="caption" color={t.ink3} style={{ fontWeight: '600' }}>{head}</T>
      <BoardProvider agent={board.agent} board={{ ...board, blocks, strip: null }} reload={noop} onChat={() => {}} readOnly>
        {blocks.map((b) => <BlockView key={b.id} block={b} />)}
      </BoardProvider>
      {removed.length ? <T v="caption" color={t.ink2} style={{ fontWeight: '400' }}>{L(`去掉 ${removed.length} 块`, `Removes ${removed.length} block${removed.length === 1 ? '' : 's'}`)}</T> : null}
    </View>
  );
}

/** 提醒的卡片：画一条「到点推出来的通知」，按现在的数据；查出来是空的就说到点不会推。 */
export function AlertPreview({ inboxId, source }: { inboxId: string; source: string }) {
  const t = useTheme();
  const [a, setA] = useState<BoardAlert | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    boardsApi.alertProposal(inboxId).then((x) => { if (live) setA(x); }, () => { if (live) setFailed(true); });
    return () => { live = false; };
  }, [inboxId]);
  if (failed || !a) return null;
  return (
    <View style={[styles.box, { backgroundColor: t.bg, gap: 6 }]}>
      <T v="caption" color={t.ink3} style={{ fontWeight: '600' }}>{a.preview ? L('到点会推成这样 · 按现在的数据', 'What it would send · with today\'s data') : L('按现在的数据查出来是空的，到点不会推', "Nothing matches today, so it wouldn't send anything")}</T>
      {a.preview ? (
        <View style={[styles.notif, { backgroundColor: t.surface, borderColor: t.line }]}>
          <View style={[styles.icon, { backgroundColor: t.goldSoft }]}><Bell size={16} color={t.gold} /></View>
          <View style={{ flex: 1, gap: 1 }}>
            <View style={{ flexDirection: 'row', gap: 6 }}>
              <T v="caption" style={{ fontWeight: '700', flex: 1 }} numberOfLines={1}>{`${agentName()} · ${source}`}</T>
              <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{a.when.replace(/^.* /, '')}</T>
            </View>
            <T v="caption" style={{ fontWeight: '600' }}>{a.title}</T>
            <T v="callout" style={{ fontSize: 14 }}>{a.preview}</T>
          </View>
        </View>
      ) : null}
      <T v="caption" color={t.ink2} style={{ fontWeight: '400' }}>{`${a.when} · ${a.levelText}`}</T>
    </View>
  );
}

const styles = StyleSheet.create({
  box: { borderRadius: radius.md + 2, padding: space.md, gap: 2 },
  notif: { flexDirection: 'row', gap: 10, borderRadius: 14, borderWidth: StyleSheet.hairlineWidth, padding: 10 },
  icon: { width: 28, height: 28, borderRadius: 7, alignItems: 'center', justifyContent: 'center' },
});
