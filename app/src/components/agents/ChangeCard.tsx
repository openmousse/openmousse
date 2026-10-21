// 改法的卡（主线第三步，设计稿第 43 张）：「改好以后 · 等你点头」：旧 → 新，「改 / 不改」；改完一行「刚改 · 撤销」。
// 资料页上点一行改、底下「跟 X 说要怎么改」出来的都是这一张；对话里说的、它自己提的是收件箱卡（对话里本来就画），资料页也列出来，
// 在这里点了一样算数（那张收件箱卡跟着撤回）。
import React from 'react';
import { View } from 'react-native';
import type { Change, Version } from '../../api/cybers';
import { L } from '../../i18n';
import { radius, space, useTheme } from '../../theme';
import { Undo2 } from '../icons';
import { Btn, Card, T } from '../ui';

const viaLabel = (via: string) => ({ chat: L('对话里说的', 'From the chat'), review: L('它自己提的', 'It suggested this'), daily: L('读完日报提的', 'After its daily read'),
  profile: L('资料页', 'Profile') }[via] ?? via);

export function ChangeCard({ change: c, onApply, onDrop, busy }: { change: Change; onApply: () => void; onDrop: () => void; busy?: boolean }) {
  const t = useTheme();
  const note = c.field === 'note';
  return (
    <Card style={{ gap: space.md, borderWidth: 1, borderColor: t.goldFill }}>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', gap: space.sm }}>
        <T v="label" color={t.gold}>{note ? L('想记下一条 · 等你点头', 'WANTS TO KEEP A NOTE · NEEDS YOUR OK') : L(`改好以后 · ${c.label} · 等你点头`, `AFTER THE CHANGE · ${c.label.toUpperCase()} · NEEDS YOUR OK`)}</T>
        <T v="caption" color={t.ink3}>{viaLabel(c.via)}</T>
      </View>
      {note ? <T v="callout">{c.new}</T> : (
        <View style={{ gap: space.sm }}>
          <View style={{ backgroundColor: t.surface2, borderRadius: radius.sm, padding: space.sm }}>
            <T v="caption" color={t.ink3}>{L('旧', 'Before')}</T>
            <T v="callout" color={t.ink2}>{c.old || L('（空）', '(empty)')}</T>
          </View>
          <View style={{ backgroundColor: t.goldSoft, borderRadius: radius.sm, padding: space.sm }}>
            <T v="caption" color={t.gold}>{L('新', 'After')}</T>
            <T v="callout">{c.new || L('（空）', '(empty)')}</T>
          </View>
        </View>
      )}
      {c.why ? <T v="caption" color={t.ink3}>{c.why}</T> : null}
      <View style={{ flexDirection: 'row', gap: space.sm }}>
        <Btn flex label={busy ? L('正在改…', 'Changing…') : note ? L('记下', 'Keep') : L('采用', 'Apply')} onPress={onApply} />
        <Btn flex kind="quiet" label={note ? L('不记', 'Skip') : L('不改', 'Keep as is')} onPress={onDrop} />
      </View>
    </Card>
  );
}

/** 「刚改 · 撤销」那一行（一天以内、没撤过的最近一次改动）。 */
export function RecentLine({ version: v, onUndo }: { version: Version; onUndo: () => void }) {
  const t = useTheme();
  const what = v.field === 'note' ? L(`刚记下：${String(v.new ?? '')}`, `Just kept: ${String(v.new ?? '')}`) : L(`刚改 · ${v.label}`, `Just changed · ${v.label}`);
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingVertical: space.xs }}>
      <T v="callout" color={t.ink2} style={{ flex: 1 }} numberOfLines={1}>{what}</T>
      <Btn kind="quiet" label={L('撤销', 'Undo')} icon={<Undo2 size={14} color={t.ink} />} onPress={onUndo} />
    </View>
  );
}
