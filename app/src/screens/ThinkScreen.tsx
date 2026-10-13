// Zen tab（2026-10-13 重排）：顶上「想法 | 播客」两边，和对话页「Grava | 好友」同一个切换条。
// 想法 = 在想 / 记下的 / 已想完（../think/Home.tsx）；播客 = 制作中 / 今日话题 / 节目，方式和「开始录制」在底部（../think/Podcast.tsx）。
// 顶栏：搜索、冥想；想法一边多一个「+」（讨论一个话题 / 全屏写作），播客一边新建就是底部的「开始录制」。
// 长按「记下的」一条进多选：页头换成 取消 · 已选 N 条 · 全选。服务器没有 think.home 时还是老的三栏（ThinkLegacy）。
import React, { useCallback, useEffect, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { useFocusEffect, useNavigation, useRoute } from '@react-navigation/native';
import { MessageCircle, Moon, NotebookPen, Plus, Search, Timer } from '../components/icons';
import { MenuItem, PillTabs } from '../components/ChatParts';
import { useSheet } from '../components/Sheet';
import { Screen, T } from '../components/ui';
import * as pod from '../api/podcast';
import { supports } from '../api/version';
import { agentName } from '../brand';
import { L } from '../i18n';
import { useStore } from '../store';
import { space, type, useTheme } from '../theme';
import { NewTopicSheet, SelectHeader, ZenIdeas } from '../think/Home';
import { Podcast, stageOf } from '../think/Podcast';
import { itemId, useThink } from '../think/ThinkStore';
import { ZenStartSheet } from '../think/Zen';
import { ThinkLegacy } from './ThinkLegacy';

type Side = 'ideas' | 'podcast';

export function ThinkScreen() {
  const { connected } = useStore();
  return connected && !supports('think.home') ? <ThinkLegacy /> : <ZenScreen />;
}

/** 顶上一个圆按钮（和对话页一样）。 */
function RoundBtn({ label, onPress, gold, children }: { label: string; onPress: () => void; gold?: boolean; children: React.ReactNode }) {
  const t = useTheme();
  return (
    <Pressable onPress={onPress} hitSlop={4} accessibilityRole="button" accessibilityLabel={label}
      style={({ pressed }) => [styles.round, { backgroundColor: gold ? t.goldFill : t.surface2, opacity: pressed ? 0.7 : 1 }]}>
      {children}
    </Pressable>
  );
}

function ZenScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const route = useRoute<any>();
  const sheet = useSheet();
  const { focus, home, podWaiting, refreshHome, refreshKeywords, loadFocus, setPodWaiting } = useThink();
  const [hasPod, setHasPod] = useState(true);  // 老服务器没有播客：不显示切换条
  useEffect(() => { pod.podcastSupported().then(setHasPod); }, []);
  // 别处要求切到某一边（录完点「返回 Zen」回到播客）：带 tab + at；老链接的 saves 算想法一边
  const asked: Side = route.params?.tab === 'podcast' ? 'podcast' : 'ideas';
  const wantedAt = (route.params?.at as number | undefined) ?? 0;
  const [pick, setPick] = useState<{ side: Side; at: number }>({ side: asked, at: 0 });
  const picked = wantedAt > pick.at && route.params?.tab ? asked : pick.side;
  const side: Side = picked === 'podcast' && !hasPod ? 'ideas' : picked;
  const setSide = (v: Side) => setPick({ side: v, at: Math.max(pick.at, wantedAt) });
  const [sel, setSel] = useState<string[] | null>(null);

  useFocusEffect(useCallback(() => {
    refreshHome();
    refreshKeywords();
    loadFocus();
    // 「播客」旁的琥珀点：只读列表，不会去挑今日话题（那要调模型，切到播客那边才挑）
    if (hasPod && side !== 'podcast') pod.home().then((h) => setPodWaiting(h.episodes.filter((e) => stageOf(e).waiting).length)).catch(() => {});
  }, [refreshHome, refreshKeywords, loadFocus, hasPod, side, setPodWaiting]));

  const zen = () => {
    if (focus) { nav.navigate('ThinkWrite', { zen: true }); return; }
    sheet.open({ title: L('进入冥想时间', 'Focus time'), content: (close) => <ZenStartSheet close={close} /> });
  };
  const plus = () => sheet.open({
    title: L('新建', 'New'),
    content: (close) => (
      <View style={{ gap: 4 }}>
        <MenuItem icon={MessageCircle} title={L('讨论一个话题', 'Discuss a topic')} chevron
          desc={L(`${agentName()} 先提问，帮你把想法理清；结束后可写成笔记`, `${agentName()} asks first to help you think it through; afterwards you can write it up`)}
          onPress={() => sheet.open({ title: L('讨论一个话题', 'Discuss a topic'), content: (c) => <NewTopicSheet close={c} onOpen={(id) => nav.navigate('ThinkTalk', { id })} /> })} />
        <MenuItem icon={NotebookPen} title={L('全屏写作', 'Write full screen')} desc={L('直接写，存入库的「写作」', 'Write directly; saved to Writing in the vault')} chevron
          onPress={() => { close(); nav.navigate('ThinkWrite'); }} />
      </View>
    ),
  });

  const all = home?.items.map(itemId) ?? [];
  const header = sel ? (
    <SelectHeader n={sel.length} all={!!all.length && sel.length === all.length} onCancel={() => setSel(null)} onAll={() => setSel(sel.length === all.length ? [] : all)} />
  ) : (
    <>
      <View style={styles.head}>
        <T v="largeTitle" style={{ flex: 1, lineHeight: 38 }}>Zen</T>
        <RoundBtn label={L('搜索', 'Search')} onPress={() => nav.navigate('ThinkSearch')}><Search size={19} color={t.ink} /></RoundBtn>
        {focus ? (
          <Pressable onPress={zen} accessibilityRole="button" accessibilityLabel={L(`冥想中，至 ${focus.until}`, `In focus until ${focus.until}`)}
            style={({ pressed }) => [styles.focus, { backgroundColor: t.lensField, opacity: pressed ? 0.8 : 1 }]}>
            <Timer size={15} color={t.goldFill} />
            <Text style={[type.callout, { color: '#E8EAED', fontWeight: '600' }]}>{L(`至 ${focus.until}`, `Until ${focus.until}`)}</Text>
          </Pressable>
        ) : <RoundBtn label={L('进入冥想时间', 'Start focus time')} onPress={zen}><Moon size={19} color={t.gold} /></RoundBtn>}
        {side === 'ideas' ? <RoundBtn label={L('新建', 'New')} onPress={plus} gold><Plus size={20} color={t.onGold} /></RoundBtn> : null}
      </View>
      {hasPod ? (
        <PillTabs<Side> value={side} onChange={setSide} style={{ marginHorizontal: space.lg, marginTop: 14, marginBottom: 2 }}
          options={[{ value: 'ideas', label: L('想法', 'Thoughts') },
            { value: 'podcast', label: L('播客', 'Podcast'), dot: side !== 'podcast' && podWaiting > 0, dotLabel: L(`${podWaiting} 期等你处理`, `${podWaiting} waiting for you`) }]} />
      ) : null}
    </>
  );

  return (
    <Screen>
      {header}
      {side === 'podcast' ? <Podcast /> : <ZenIdeas sel={sel} setSel={setSel} />}
    </Screen>
  );
}

const styles = StyleSheet.create({
  head: { flexDirection: 'row', alignItems: 'flex-end', gap: space.sm, paddingHorizontal: space.lg, paddingTop: 14 },
  round: { width: 38, height: 38, borderRadius: 19, alignItems: 'center', justifyContent: 'center', marginBottom: 2 },
  focus: { flexDirection: 'row', alignItems: 'center', gap: 6, height: 38, paddingHorizontal: 14, borderRadius: 19, marginBottom: 2 },
});
