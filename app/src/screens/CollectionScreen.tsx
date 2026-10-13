// 集合块全屏页（画布第 6 排「求职看板」）：返回 / 名字 + 「Agent · N 条」/ 新增；下面是视图切换条和完整的视图。
// 看板视图在这里能长按拖卡片换列（拖的时候关掉返回手势）。参数 {agent, block, view?}；网页版 ?screen=Collection&agent=apply&block=radar&view=board。
import { useNavigation, useRoute } from '@react-navigation/native';
import React, { useCallback, useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import { BoardProvider, useAgentBoard, useBoard } from '../components/blocks/BoardContext';
import { openForm } from '../components/blocks/RowSheet';
import { ChevronLeft, Plus } from '../components/icons';
import { useSheet } from '../components/Sheet';
import { Screen, T } from '../components/ui';
import { CollectionPane, openedBlock } from '../components/views/Collection';
import { pickedView, pickView } from '../components/views/palette';
import { L } from '../i18n';
import { useStore } from '../store';
import { space, useTheme } from '../theme';

export function CollectionScreen() {
  const nav = useNavigation<any>();
  const { agent, block: blockId, view: wanted } = useRoute<any>().params as { agent: string; block: string; view?: string };
  const b = useAgentBoard(agent, true);
  const onChat = useCallback(() => nav.navigate('Group', { id: agent, tab: 'chat' }), [nav, agent]);
  return (
    <Screen>
      <BoardProvider agent={agent} board={b.board} error={b.error} reload={b.reload} onChat={onChat}>
        <Page agent={agent} blockId={blockId} wanted={wanted} />
      </BoardProvider>
    </Screen>
  );
}

function Page({ agent, blockId, wanted }: { agent: string; blockId: string; wanted?: string }) {
  const t = useTheme();
  const nav = useNavigation<any>();
  const ctx = useBoard();
  const sheet = useSheet();
  const { groups } = useStore();
  const g = groups.find((x) => x.id === agent);
  const block = ctx?.board?.blocks.find((x) => x.id === blockId && x.type === 'collection') ?? openedBlock(agent, blockId);
  const [view, setView] = useState(() => wanted ?? pickedView(agent, blockId));
  const setDragging = useCallback((on: boolean) => nav.setOptions({ gestureEnabled: !on }), [nav]);
  const title = block?.title || block?.data.collectionTitle || L('集合', 'Collection');
  const total = block?.data.total;
  const canAdd = !!block && !!ctx && block.edit !== false && !block.data.readonly && !!block.data.collection && !!block.data.fields?.length;
  const viewId = block?.views?.some((v) => v.id === view) ? (view as string) : block?.view ?? block?.views?.[0]?.id ?? '';
  return (
    <View style={{ flex: 1 }}>
      <View style={styles.header}>
        <Pressable onPress={() => nav.goBack()} hitSlop={12} accessibilityRole="button" accessibilityLabel={L('返回', 'Back')} style={styles.back}>
          <ChevronLeft size={26} color={t.gold} />
        </Pressable>
        <View style={{ flex: 1, gap: 1 }}>
          <T v="headline" style={{ fontSize: 17, fontWeight: '700' }} numberOfLines={1}>{title}</T>
          <T v="caption" color={t.ink3} style={{ fontWeight: '400' }} numberOfLines={1}>
            {[g?.name, total != null ? L(`${total} 条`, `${total} rows`) : ''].filter(Boolean).join(' · ')}
          </T>
        </View>
        {canAdd ? (
          <Pressable onPress={() => ctx && block && openForm(sheet, ctx, block.data.collection as string, L('新增', 'Add'), block.data.fields ?? [])}
            hitSlop={8} accessibilityRole="button" accessibilityLabel={L('新增', 'Add')} style={({ pressed }) => [styles.add, { backgroundColor: t.surface2, opacity: pressed ? 0.7 : 1 }]}>
            <Plus size={18} color={t.ink} />
          </Pressable>
        ) : null}
      </View>
      {block ? (
        <CollectionPane agent={agent} block={block} full viewId={viewId} setDragging={setDragging}
          onView={(id) => { setView(id); pickView(agent, blockId, id); }} />
      ) : (
        <View style={{ padding: space.lg }}>
          <T v="callout" color={ctx?.error ? t.bad : t.ink2}>{ctx?.error ? L(`无法加载：${ctx.error}`, `Couldn't load: ${ctx.error}`) : ctx?.board ? L('看板上已没有这一块。', 'This block is no longer on the board.') : L('正在加载…', 'Loading…')}</T>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingRight: space.md, paddingLeft: 4, paddingTop: 6, paddingBottom: 6 },
  back: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center' },
  add: { width: 34, height: 34, borderRadius: 17, alignItems: 'center', justifyContent: 'center' },
});
