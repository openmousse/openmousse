// 记忆页顶上的搜索（2026-10-20，设计第 21 张）：在 /api/tree 的叶子里按正文、原话、「用得上的时候」（cues）本地找，不加接口。
// 几个词用空格隔开 = 都要找到（中文不分词，按字串找；全角半角、大小写当一样）。结果一条一张小卡：标题，下面一行
// 「来源 · 日期 · 匹配：「…」」：匹配到的是 cues 里的哪一句；只在原话里找到的写原话那一段；正文里就找得到的，标题就是匹配，不另写。
import React, { useMemo } from 'react';
import { Pressable, StyleSheet, TextInput, View } from 'react-native';
import type { TreeLeaf } from '../../data/types';
import { L } from '../../i18n';
import { leafTitle, shortDate, useLeafSheet } from '../../screens/TreeScreen';
import { radius, space, type, useTheme } from '../../theme';
import { Search, X } from '../icons';
import { Card, T } from '../ui';

export interface LeafHit { leaf: TreeLeaf; cue?: string; quote?: string }

const norm = (s: string) => s.normalize('NFKC').toLowerCase();
const MAX = 50;

/** 原话里匹配到的那一段（前后各留几个字），太长的两头写「…」。 */
function around(text: string, term: string): string {
  const i = norm(text).indexOf(term);
  if (i < 0 || text.length <= 28) return text;
  const from = Math.max(0, i - 10);
  const to = Math.min(text.length, i + term.length + 14);
  return `${from > 0 ? '…' : ''}${text.slice(from, to)}${to < text.length ? '…' : ''}`;
}

/** 找叶子（顺序照服务器给的：新的在前）。 */
export function searchLeaves(leaves: TreeLeaf[], q: string): LeafHit[] {
  const terms = norm(q).split(/\s+/).filter(Boolean);
  if (!terms.length) return [];
  const out: LeafHit[] = [];
  for (const leaf of leaves) {
    const text = norm(leaf.text);
    const quote = norm(leaf.quote ?? '');
    const cues = (leaf.cues ?? []).map(norm);
    if (!terms.every((x) => text.includes(x) || quote.includes(x) || cues.some((c) => c.includes(x)))) continue;
    const missing = terms.filter((x) => !text.includes(x));  // 正文里没有的词：看它们是在哪句 cue、还是原话里找到的
    if (!missing.length) { out.push({ leaf }); continue; }
    const ci = cues.findIndex((c) => missing.some((x) => c.includes(x)));
    if (ci >= 0) out.push({ leaf, cue: leaf.cues![ci] });
    else out.push({ leaf, quote: around(leaf.quote ?? '', missing[0]) });
  }
  return out;
}

export function SearchBox({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const t = useTheme();
  return (
    <View style={[styles.box, { backgroundColor: t.surface2 }]}>
      <Search size={17} color={t.ink3} />
      <TextInput value={value} onChangeText={onChange} placeholder={L('搜索记忆', 'Search memories')} placeholderTextColor={t.ink3} autoCorrect={false} returnKeyType="search"
        accessibilityLabel={L('搜索记忆', 'Search memories')} style={[type.body, { flex: 1, color: t.ink, paddingVertical: 0, outlineWidth: 0 }]} />
      {value ? <Pressable onPress={() => onChange('')} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('清空', 'Clear')}><X size={16} color={t.ink3} /></Pressable> : null}
    </View>
  );
}

function HitCard({ hit, onOpen }: { hit: LeafHit; onOpen: (leaf: TreeLeaf) => void }) {
  const t = useTheme();
  const { leaf } = hit;
  const match = hit.cue ? L(`匹配：「${hit.cue}」`, `matches "${hit.cue}"`) : hit.quote ? L(`匹配：原话「${hit.quote}」`, `matches the original words "${hit.quote}"`) : '';
  const line = [leaf.originName, shortDate(leaf.observedAt), match].filter(Boolean).join(' · ');
  return (
    <Pressable onPress={() => onOpen(leaf)} accessibilityRole="button" accessibilityHint={L('查看全文和操作', 'Shows the full text and actions')}
      style={({ pressed }) => ({ opacity: pressed ? 0.7 : 1 })}>
      <Card style={styles.hit}>
        <T v="body" numberOfLines={2} style={{ fontSize: 15, lineHeight: 21 }}>{leafTitle(leaf.text)}</T>
        <View style={styles.meta}>
          {leaf.status === 'pending' ? <T v="caption" color={t.warn}>{L('待确认', 'Unconfirmed')}</T> : null}
          <T v="caption" color={t.ink3} numberOfLines={2} style={{ flex: 1, fontWeight: '400' }}>{line}</T>
        </View>
      </Card>
    </Pressable>
  );
}

/** 搜索结果（框里有字时代替记忆页的三张卡）。 */
export function SearchResults({ leaves, q }: { leaves: TreeLeaf[]; q: string }) {
  const t = useTheme();
  const open = useLeafSheet();
  const hits = useMemo(() => searchLeaves(leaves, q), [leaves, q]);  // 记忆页随 store 的任何变化重画：只在叶子、搜的词变了时再找
  if (!hits.length) return <Card><T v="callout" color={t.ink2}>{L(`未找到「${q.trim()}」的相关结果。`, `No results for "${q.trim()}".`)}</T></Card>;
  return (
    <View style={{ gap: space.sm }}>
      <T v="caption" color={t.ink3} style={styles.count}>{hits.length > MAX ? L(`共 ${hits.length} 条，显示前 ${MAX} 条`, `${hits.length} found, showing the first ${MAX}`) : L(`共 ${hits.length} 条`, `${hits.length} found`)}</T>
      {hits.slice(0, MAX).map((h) => <HitCard key={h.leaf.id} hit={h} onOpen={open} />)}
    </View>
  );
}

const styles = StyleSheet.create({
  box: { flexDirection: 'row', alignItems: 'center', gap: space.sm, borderRadius: radius.md, paddingHorizontal: space.md, height: 40 },
  hit: { gap: 5, paddingVertical: 12 },
  meta: { flexDirection: 'row', alignItems: 'flex-start', gap: 6 },
  count: { fontWeight: '400', fontVariant: ['tabular-nums'], paddingHorizontal: space.xs },
});
