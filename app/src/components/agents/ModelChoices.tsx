// 赛博选模型的一行一行（主线第三步，设计稿 D）：名字、厂商 · 一句用途、按量 / 本机 / 套餐、成本档（低 / 中 / 高）。
// 套餐照样列出来，但标灰、写明「赛博暂时不能用套餐」，点不了。新建草稿的弹层、赛博的模型页都用它。
import React from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import type { ModelRow } from '../../api/cybers';
import { L } from '../../i18n';
import { space, useTheme } from '../../theme';
import { Check } from '../icons';
import { Pill, T } from '../ui';

export const costLabel = (c: ModelRow['cost']) => (c === 'low' ? L('成本低', 'Low cost') : c === 'mid' ? L('成本中', 'Mid cost') : c === 'high' ? L('成本高', 'High cost') : '');
export const billingLabel = (b: ModelRow['billing']) => (b === 'local' ? L('本机', 'Local') : b === 'plan' ? L('订阅', 'Plan') : L('按量', 'Pay as you go'));

export function ModelChoice({ row, selected, onPress, reason }: { row: ModelRow; selected: boolean; onPress: () => void; reason?: string }) {
  const t = useTheme();
  const off = !row.allowed || !row.usable;
  return (
    <Pressable onPress={onPress} disabled={off} accessibilityRole="button" accessibilityState={{ selected, disabled: off }}
      style={({ pressed }) => [styles.row, { opacity: off ? 0.45 : pressed ? 0.7 : 1 }]}>
      <View style={{ flex: 1, minWidth: 0, gap: 3 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm, flexWrap: 'wrap' }}>
          <T v="headline" numberOfLines={1} style={{ flexShrink: 1 }}>{row.name}</T>
          {row.open ? <Pill label={L('开源', 'Open')} tone="cyan" /> : null}
          <Pill label={billingLabel(row.billing)} />
          {row.cost ? <Pill label={costLabel(row.cost)} tone={row.cost === 'high' ? 'warn' : 'neutral'} /> : null}
        </View>
        <T v="callout" color={t.ink2} numberOfLines={2} style={{ fontSize: 13 }}>{`${row.providerName || row.vendor} · ${reason ?? row.use}`}</T>
        {row.note ? <T v="caption" color={t.ink3}>{row.note}</T> : !row.usable && row.allowed ? <T v="caption" color={t.ink3}>{L('还没连上', 'Not connected yet')}</T> : null}
      </View>
      {selected ? <Check size={18} color={t.gold} /> : null}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: space.sm },
});
