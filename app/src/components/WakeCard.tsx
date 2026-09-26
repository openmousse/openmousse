// 起床判断（A6）：早上醒了、服务器还没确认起床时，「今天」页顶上一行：几点醒的（睡了回笼觉就说到几点），和「我起来了」。
// 起床报告要等确认起床才发（关了闹钟接着睡回笼觉很常见，手表要等这一觉睡完才写进健康）；点一下就不用等了。
import React, { useState } from 'react';
import { ActivityIndicator, Alert, Pressable, StyleSheet, Text, View } from 'react-native';
import { Sunrise } from './icons';
import { Card, T } from './ui';
import { L } from '../i18n';
import { useStore } from '../store';
import { radius, space, type, useTheme } from '../theme';

const MORNING = ['05:30', '13:00'];  // 和服务器一样：13:00 以后起床报告照发，不再等

export function WakeCard() {
  const t = useTheme();
  const { wake, feed, imUp } = useStore();
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState<string | null>(null);  // 点过「我起来了」的那天
  if (!wake) return null;
  const done = sent === wake.date;  // 点过以后留一句回执，直到睡眠报告出来
  const reported = feed.some((f) => f.kind === 'sleep_report' && (f.createdAt ?? '').startsWith(wake.date));
  if (wake.now < MORNING[0] || wake.now >= MORNING[1] || reported) return null;
  if (!done && (wake.state !== 'maybe_awake' || !wake.woke)) return null;
  const back = wake.ref_from === 'sleep' && wake.night?.back_sleeps.length ? wake.night : null;
  const sub = back
    ? L(`${back.first_wake} 醒过，又睡到 ${wake.woke}`, `Woke at ${back.first_wake}, slept again until ${wake.woke}`)
    : wake.ref_from === 'sleep'
      ? L(`手表记到你 ${wake.woke} 醒`, `Your watch says you woke at ${wake.woke}`)
      : L('还没有昨晚的睡眠数据', 'No sleep data from last night yet');
  const up = () => {
    if (busy) return;
    setBusy(true);
    imUp().then(() => setSent(wake.date))
      .catch((e) => Alert.alert(L('没发出去', "Couldn't send"), e instanceof Error ? e.message : String(e)))
      .finally(() => setBusy(false));
  };
  return (
    <Card style={styles.card}>
      <Sunrise size={20} color={t.gold} />
      <View style={{ flex: 1, gap: 2 }}>
        <T v="headline" style={{ fontSize: 15 }}>{done ? L('好，这就出起床报告', 'OK, your morning report is on its way') : L('起床报告等你起来再发', 'Morning report waits until you’re up')}</T>
        {done ? null : <T v="caption" color={t.ink3}>{sub}</T>}
      </View>
      {done ? null : (
        <Pressable onPress={up} disabled={busy} accessibilityRole="button" accessibilityLabel={L('我起来了', 'I’m up')} accessibilityState={{ busy }}
          style={({ pressed }) => [styles.btn, { backgroundColor: t.goldFill, opacity: pressed ? 0.75 : 1 }]}>
          {busy ? <ActivityIndicator size="small" color={t.onGold} /> : <Text style={[type.headline, { fontSize: 15, color: t.onGold }]}>{L('我起来了', 'I’m up')}</Text>}
        </Pressable>
      )}
    </Card>
  );
}

const styles = StyleSheet.create({
  card: { marginTop: space.md, flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: space.md },
  btn: { borderRadius: radius.md, paddingHorizontal: space.md, height: 36, minWidth: 84, alignItems: 'center', justifyContent: 'center' },
});
