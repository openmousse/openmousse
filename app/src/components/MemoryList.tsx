import React, { useState } from 'react';
import { agentName } from '../brand';
import { Alert, Pressable, StyleSheet, View } from 'react-native';
import { Trash2 } from './icons';
import type { MemoryItem } from '../data/types';
import { L } from '../i18n';
import { useStore } from '../store';
import { space, useTheme } from '../theme';
import { useSheet } from './Sheet';
import { Btn, Card, SectionLabel, T } from './ui';

function ForgetSheet({ m, close }: { m: MemoryItem; close: () => void }) {
  const t = useTheme();
  const { forget } = useStore();
  const [busy, setBusy] = useState(false);
  return (
    <View style={{ gap: space.md }}>
      <T v="body" color={t.ink2}>{L(`「${m.text}」`, `"${m.text}"`)}</T>
      <T v="callout" color={t.ink3}>{L('会从 MEMORY.md 里删掉这一条，检索索引自动更新。忘记后不能恢复；活动记录里只留「遗忘了 1 条」，不保留内容。', "This removes the item from MEMORY.md and the search index updates on its own. It can't be undone; the activity log only notes that 1 item was forgotten, not what it said.")}</T>
      <View style={{ flexDirection: 'row', gap: space.sm }}>
        <Btn flex kind="quiet" label={L('留着', 'Keep')} onPress={close} />
        <Btn flex kind="danger" label={busy ? L('正在忘…', 'Forgetting…') : L('忘记', 'Forget')} onPress={() => {
          if (busy) return;
          setBusy(true);
          forget(m.id).then(close).catch((e) => Alert.alert(L('没忘掉', "Couldn't forget it"), e instanceof Error ? e.message : String(e))).finally(() => setBusy(false));
        }} />
      </View>
    </View>
  );
}

/** 一组记忆，按 MEMORY.md 的小节分开显示，每条可以忘记。scope = agent id：main 或某个 Group。 */
export function MemoryList({ scope }: { scope: string }) {
  const t = useTheme();
  const sheet = useSheet();
  const { memories, loading, dataErrors, connected } = useStore();
  const list = memories.filter((m) => m.scope === scope);
  if (dataErrors.memories) return <Card><T v="callout" color={t.bad}>{L(`读不到记忆：${dataErrors.memories}`, `Couldn't read memory: ${dataErrors.memories}`)}</T></Card>;
  if (!list.length) return <Card><T v="callout" color={t.ink2}>{!connected ? L('没连上服务器。', 'Not connected to the server.') : loading.memories ? L('正在读…', 'Loading…') : L('这里还没有记忆。', 'No memories here yet.')}</T></Card>;
  const sections = [...new Set(list.map((m) => m.section))];
  return (
    <View>
      {sections.map((sec) => {
        const rows = list.filter((m) => m.section === sec);
        return (
          <View key={sec}>
            <SectionLabel>{sec}</SectionLabel>
            <Card style={{ paddingVertical: space.xs }}>
              {rows.map((m, i) => (
                <View key={m.id} style={[styles.row, i < rows.length - 1 && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }]}>
                  <T v="body" style={{ flex: 1 }}>{m.text}</T>
                  <Pressable hitSlop={10} accessibilityRole="button" accessibilityLabel={L(`让 ${agentName()} 忘记这条`, `Have ${agentName()} forget this`)}
                    onPress={() => sheet.open({ title: L(`让 ${agentName()} 忘记这条？`, `Have ${agentName()} forget this?`), content: (close) => <ForgetSheet m={m} close={close} /> })}>
                    <Trash2 size={18} color={t.ink3} />
                  </Pressable>
                </View>
              ))}
            </Card>
          </View>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({ row: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: 12 } });
