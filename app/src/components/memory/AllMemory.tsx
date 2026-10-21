// 记忆页第三张卡「全部记忆」（2026-10-20）：只有一套分法。世界树有枝的（/api/tree 的 branchable，主人那份就是）按枝看：
// 主干「档案」和各枝一行一行（TreeScreen 的 ByBranch，点开看叶子），后面接日志、朋友画像、来源；没有枝的：关于你（基础档案）、
// 朋友（朋友画像）、日志、来源各一行。设置 → 其他里原来的基础档案、记忆条目、日志、朋友画像四行挪到这里（路由 Identity / Memory /
// Journal / People 不变）。来源一行写「主对话 34 · 健身 18 · Zen 12 · ChatGPT 6 …」，点进去是世界树页按来源看。
import { useNavigation } from '@react-navigation/native';
import React, { useEffect, useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import { podFeatures } from '../../api/podcast';
import * as peopleApi from '../../api/people';
import { L } from '../../i18n';
import { ByBranch } from '../../screens/TreeScreen';
import { useStore } from '../../store';
import { space, useTheme } from '../../theme';
import { BookOpen, Brain, ChevronRight, IdCard, Layers, Users } from '../icons';
import { Card, T } from '../ui';

function Row({ icon, title, sub, value, onPress, first }: { icon: React.ReactNode; title: string; sub?: string; value?: string; onPress: () => void; first?: boolean }) {
  const t = useTheme();
  return (
    <Pressable onPress={onPress} accessibilityRole="button" accessibilityLabel={[title, sub, value].filter(Boolean).join(L('，', ', '))}
      style={({ pressed }) => [styles.row, !first && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }, { opacity: pressed ? 0.6 : 1 }]}>
      {icon}
      <View style={{ flex: 1, minWidth: 0, gap: 1 }}>
        <T v="body" style={{ fontWeight: '600' }}>{title}</T>
        {sub ? <T v="caption" color={t.ink3} numberOfLines={1} style={{ fontWeight: '400' }}>{sub}</T> : null}
      </View>
      {value ? <T v="callout" color={t.ink2} style={styles.tnum}>{value}</T> : null}
      <ChevronRight size={16} color={t.ink3} />
    </Pressable>
  );
}

/** 「主对话 34 · 健身 18 · Zen 12 · ChatGPT 6」：各来源照服务器的顺序（多的在前）一路写下去，一行放不下的由那一行截成「…」。 */
const sourcesLine = (bySource: { name: string; count: number }[]) => bySource.map((s) => `${s.name} ${s.count}`).join(' · ');

export function AllMemoryCard() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { tree, profile, journal, memories, connected } = useStore();
  const data = tree?.kind === 'ok' ? tree.data : null;
  // 朋友画像（播客记的，只有你看得到）：服务器有才显示，人数现读
  const [people, setPeople] = useState<number | null>(null);
  useEffect(() => {
    if (!connected) return undefined;
    let live = true;
    podFeatures().then((f) => (f.people ? peopleApi.list().then((r) => { if (live) setPeople(r.people.length); }) : undefined)).catch(() => {});
    return () => { live = false; };
  }, [connected]);
  const total = data?.counts.total ?? null;
  const ic = (I: typeof IdCard) => <I size={20} color={t.cyan} />;
  const trunk = data?.trunk.count ?? profile.length;
  return (
    <Card style={{ gap: space.xs }}>
      <T v="headline" style={styles.title}>{total != null ? L(`全部记忆 · ${total} 条`, `All memories · ${total}`) : L('全部记忆', 'All memories')}</T>
      {data?.branchable ? <ByBranch data={data} flat /> : (
        <Row first icon={ic(IdCard)} title={L('关于你', 'About you')} sub={L(`基础档案 · ${trunk} 条`, `Profile · ${trunk}`)} onPress={() => nav.navigate('Identity')} />
      )}
      {people != null ? (
        <Row icon={ic(Users)} title={L('朋友', 'Friends')} sub={L(`朋友画像 · ${people} 人`, `Friend notes · ${people} ${people === 1 ? 'person' : 'people'}`)} onPress={() => nav.navigate('People')} />
      ) : null}
      <Row first={false} icon={ic(BookOpen)} title={L('日志', 'Journal')} value={journal.length ? L(`${journal.length} 篇`, `${journal.length}`) : undefined} onPress={() => nav.navigate('Journal')} />
      {memories.length ? (
        <Row icon={ic(Brain)} title={L('记忆条目', 'Memory notes')} value={L(`${memories.length} 条`, `${memories.length}`)} onPress={() => nav.navigate('Memory')} />
      ) : null}
      {data && data.counts.bySource.length ? (
        <Row icon={ic(Layers)} title={L('来源', 'Sources')} sub={sourcesLine(data.counts.bySource)} onPress={() => nav.navigate('Tree', { view: 'source' })} />
      ) : null}
    </Card>
  );
}

const styles = StyleSheet.create({
  title: { fontSize: 18, fontWeight: '700', marginBottom: 2 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 12, minHeight: 50 },
  tnum: { fontVariant: ['tabular-nums'] },
});
