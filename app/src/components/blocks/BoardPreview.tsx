// 收件箱里「看板」提案卡的预览：提案那一版里新加或改过的块，用现在的真实数据画（只看，不能点）。
import React, { useEffect, useState } from 'react';
import { ActivityIndicator, StyleSheet, View } from 'react-native';
import { boardsApi, type Board } from '../../api/boards';
import { L } from '../../i18n';
import { radius, space, useTheme } from '../../theme';
import { T } from '../ui';
import { BoardProvider } from './BoardContext';
import { BlockView } from './Blocks';

const noop = async () => {};

export function BoardPreview({ inboxId }: { inboxId: string }) {
  const t = useTheme();
  const [board, setBoard] = useState<Board | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    boardsApi.proposal(inboxId).then((b) => { if (live) setBoard(b); }, () => { if (live) setFailed(true); });
    return () => { live = false; };
  }, [inboxId]);
  if (failed) return null;  // 老服务器、或者这张卡没有预览：照常显示文字
  if (!board) return <View style={[styles.box, { backgroundColor: t.bg, alignItems: 'center' }]}><ActivityIndicator color={t.ink3} /></View>;
  const changed = new Set(board.changed ?? board.blocks.map((b) => b.id));
  const blocks = board.blocks.filter((b) => changed.has(b.id) && !b.hidden);
  const removed = board.removed ?? [];
  if (!blocks.length && !removed.length) return null;
  return (
    <View style={[styles.box, { backgroundColor: t.bg }]}>
      <T v="caption" color={t.ink3} style={{ fontWeight: '600' }}>{L('预览 · 用你现在的数据画的', 'Preview · drawn with your current data')}</T>
      <BoardProvider agent={board.agent} board={{ ...board, blocks, strip: null }} reload={noop} onChat={() => {}} readOnly>
        {blocks.map((b) => <BlockView key={b.id} block={b} />)}
      </BoardProvider>
      {removed.length ? <T v="caption" color={t.ink2} style={{ fontWeight: '400' }}>{L(`去掉 ${removed.length} 块`, `Removes ${removed.length} block${removed.length === 1 ? '' : 's'}`)}</T> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  box: { borderRadius: radius.md + 2, padding: space.md, gap: 2 },
});
