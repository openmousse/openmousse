import React, { useState } from 'react';
import { agentName } from '../brand';
import { Alert, StyleSheet, View } from 'react-native';
import { Check, ShieldCheck, Terminal, X } from './icons';
import type { Approval } from '../data/types';
import { useStore } from '../store';
import { radius, space, useTheme } from '../theme';
import { Btn, Pill, T } from './ui';

/** OpenClaw 审批队列里的一项。同意 = 这一次放行（allow-once），不会变成长期授权。 */
export function ApprovalCard({ approval }: { approval: Approval }) {
  const t = useTheme();
  const { decide, groups } = useStore();
  const [busy, setBusy] = useState(false);
  const from = groups.find((g) => g.id === approval.groupId)?.name ?? `${agentName()}`;
  const Icon = approval.kind === 'exec' ? Terminal : ShieldCheck;
  const act = (allow: boolean) => {
    setBusy(true);
    decide(approval.id, allow).catch((e) => Alert.alert('没做成', e instanceof Error ? e.message : String(e))).finally(() => setBusy(false));
  };
  return (
    <View style={[styles.card, { backgroundColor: t.surface, borderColor: t.goldFill, opacity: busy ? 0.6 : 1 }]}>
      <View style={styles.head}>
        <Icon size={16} color={t.gold} />
        <T v="caption" color={t.ink2} style={{ flex: 1 }}>{from}{approval.requestedAt ? ` · ${approval.requestedAt}` : ''}</T>
        <Pill label={approval.kind === 'exec' ? '执行命令' : approval.kind} tone="warn" />
      </View>
      <T v="headline" style={{ marginTop: space.sm }}>{approval.action}</T>
      {approval.detail ? <T v="callout" color={t.ink2} style={{ marginTop: 4 }}>{approval.detail}</T> : null}
      {approval.fields.length ? (
        <View style={[styles.fields, { backgroundColor: t.bg }]}>
          {approval.fields.map((f) => (
            <View key={f.k} style={{ flexDirection: 'row', gap: space.md }}>
              <T v="callout" color={t.ink3} style={{ width: 52 }}>{f.k}</T>
              <T v="callout" style={{ flex: 1 }}>{f.v}</T>
            </View>
          ))}
        </View>
      ) : null}
      <View style={{ flexDirection: 'row', gap: space.sm, marginTop: space.md }}>
        <Btn flex kind="quiet" label="拒绝" icon={<X size={16} color={t.ink} />} onPress={() => !busy && act(false)} />
        <Btn flex label="这一次同意" icon={<Check size={16} color={t.onGold} />} onPress={() => !busy && act(true)} />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: { borderRadius: radius.lg, padding: space.lg, borderWidth: 1 },
  head: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  fields: { borderRadius: radius.sm, padding: space.md, gap: 6, marginTop: space.md },
});
