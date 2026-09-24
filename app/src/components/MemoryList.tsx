import React, { useState } from 'react';
import { agentName } from '../brand';
import { Alert, Pressable, StyleSheet, View } from 'react-native';
import { Trash2 } from './icons';
import type { MemoryItem } from '../data/types';
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
      <T v="body" color={t.ink2}>「{m.text}」</T>
      <T v="callout" color={t.ink3}>会从 MEMORY.md 里删掉这一条，检索索引自动更新。忘记后不能恢复；活动记录里只留「遗忘了 1 条」，不保留内容。</T>
      <View style={{ flexDirection: 'row', gap: space.sm }}>
        <Btn flex kind="quiet" label="留着" onPress={close} />
        <Btn flex kind="danger" label={busy ? '正在忘…' : '忘记'} onPress={() => {
          if (busy) return;
          setBusy(true);
          forget(m.id).then(close).catch((e) => Alert.alert('没忘掉', e instanceof Error ? e.message : String(e))).finally(() => setBusy(false));
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
  if (dataErrors.memories) return <Card><T v="callout" color={t.bad}>读不到记忆：{dataErrors.memories}</T></Card>;
  if (!list.length) return <Card><T v="callout" color={t.ink2}>{!connected ? '没连上服务器。' : loading.memories ? '正在读…' : '这里还没有记忆。'}</T></Card>;
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
                  <Pressable hitSlop={10} accessibilityRole="button" accessibilityLabel={`让 ${agentName()} 忘记这条`}
                    onPress={() => sheet.open({ title: `让 ${agentName()} 忘记这条？`, content: (close) => <ForgetSheet m={m} close={close} /> })}>
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
