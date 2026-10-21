// 群记忆（主线第三步第二段）：路由 GroupMemory（params id）。主持人每轮小结写进来的三样：定了的（第几轮、哪天，「你同意了」）、
// 还在争的（每一方一句）、各自记下的（谁记了什么）。下一轮从这里接着聊（钉在群顶的就是最近一次小结的那一句）。
// 点一条：定了的能标「你同意了」/ 取消；哪条都能「不要这一条」（之后的轮次不再读到它）。
import React, { useCallback, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, View } from 'react-native';
import { useFocusEffect, useNavigation, useRoute } from '@react-navigation/native';
import { groupsApi, type GroupMemory } from '../../api/groups';
import { MenuItem } from '../../components/ChatParts';
import { Check, Trash2, X } from '../../components/icons';
import { useSheet } from '../../components/Sheet';
import { Card, NavHeader, PullRefresh, Screen, SectionLabel, T, showError } from '../../components/ui';
import { L } from '../../i18n';
import { space, useTheme } from '../../theme';

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

export function GroupMemoryScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const sheet = useSheet();
  const { id } = useRoute<any>().params as { id: string };
  const [m, setM] = useState<GroupMemory | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => groupsApi.memory(id).then((x) => { setM(x); setError(null); }).catch((e) => setError(errText(e))), [id]);
  useFocusEffect(useCallback(() => { load(); }, [load]));
  const act = (item: string, action: 'agree' | 'unagree' | 'remove') => {
    groupsApi.memoryAct(id, item, action).then(setM).catch((e) => showError(L('操作失败', "Couldn't complete the action"), e));
  };
  const menu = (item: string, text: string, decided?: { agreed: boolean }) => sheet.open({
    title: text.length > 40 ? `${text.slice(0, 40)}…` : text,
    content: (close) => (
      <View>
        {decided ? (
          <MenuItem icon={decided.agreed ? X : Check} title={decided.agreed ? L('取消「你同意了」', 'Unmark “you agreed”') : L('标为你同意了', 'Mark as agreed')}
            desc={decided.agreed ? undefined : L('之后的轮次照这一条来', 'Later rounds go by it')} onPress={() => { close(); act(item, decided.agreed ? 'unagree' : 'agree'); }} />
        ) : null}
        <MenuItem icon={Trash2} danger title={L('不要这一条', 'Remove it')} desc={L('之后的轮次不再读到它', 'Later rounds no longer read it')} onPress={() => { close(); act(item, 'remove'); }} />
      </View>
    ),
  });
  if (!m) {
    return (
      <Screen>
        <NavHeader title={L('群记忆', "Group's memory")} onBack={() => nav.goBack()} />
        <T v="callout" color={error ? t.bad : t.ink3} style={{ padding: space.xl, textAlign: 'center' }}>{error ? L(`无法加载：${error}`, `Couldn't load: ${error}`) : L('正在加载…', 'Loading…')}</T>
      </Screen>
    );
  }
  const empty = !m.decided.length && !m.disputed.length && !m.notes.length;
  return (
    <Screen>
      <NavHeader title={L('群记忆', "Group's memory")} sub={m.title} onBack={() => nav.goBack()} />
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl }} refreshControl={<PullRefresh onRefresh={load} />}>
        {m.line ? (
          <Card style={{ gap: 4 }}>
            <T v="caption" color={t.gold} style={{ fontWeight: '600' }}>{L(`第 ${m.round} 轮小结`, `Summary of round ${m.round}`)}</T>
            <T v="body">{m.line}</T>
          </Card>
        ) : null}
        <T v="caption" color={t.ink3} style={styles.intro}>{m.intro}</T>
        {empty ? <T v="callout" color={t.ink3} style={{ paddingVertical: space.lg, textAlign: 'center' }}>{L('还没有小结。@所有 Agent 讨论一轮，主持人会把定了的、还在争的写进来。', "No summaries yet. After a round with @All Agents, the host writes what was decided and what's still open here.")}</T> : null}

        {m.decided.length ? <SectionLabel>{L(`定了 · ${m.decided.length}`, `Decided · ${m.decided.length}`)}</SectionLabel> : null}
        {m.decided.length ? (
          <Card style={{ paddingVertical: space.xs }}>
            {m.decided.map((d, i) => (
              <Pressable key={d.id} onPress={() => menu(d.id, d.text, { agreed: d.agreed })} accessibilityRole="button"
                style={({ pressed }) => [styles.item, i > 0 && { borderTopColor: t.line, borderTopWidth: StyleSheet.hairlineWidth }, { opacity: pressed ? 0.6 : 1 }]}>
                <T v="body">{d.text}</T>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                  {d.agreed ? <Check size={13} color={t.good} /> : null}
                  <T v="caption" color={d.agreed ? t.good : t.ink3}>{d.tag || `${L(`第 ${d.round} 轮`, `Round ${d.round}`)} · ${d.dayLabel}`}</T>
                </View>
              </Pressable>
            ))}
          </Card>
        ) : null}

        {m.disputed.length ? <SectionLabel>{L(`还在争 · ${m.disputed.length}`, `Still disputed · ${m.disputed.length}`)}</SectionLabel> : null}
        {m.disputed.length ? (
          <Card style={{ paddingVertical: space.xs }}>
            {m.disputed.map((d, i) => (
              <Pressable key={d.id} onPress={() => menu(d.id, d.point)} accessibilityRole="button"
                style={({ pressed }) => [styles.item, i > 0 && { borderTopColor: t.line, borderTopWidth: StyleSheet.hairlineWidth }, { opacity: pressed ? 0.6 : 1 }]}>
                <T v="body">{d.point}</T>
                {d.sides.map((s, j) => <T key={j} v="callout" color={t.ink2} style={{ paddingLeft: 10 }}>{`${s.name}${L('：', ': ')}${s.says}`}</T>)}
                <T v="caption" color={t.ink3}>{`${L(`第 ${d.round} 轮`, `Round ${d.round}`)} · ${d.dayLabel}`}</T>
              </Pressable>
            ))}
          </Card>
        ) : null}

        {m.notes.length ? <SectionLabel>{L(`各自记下的 · ${m.notes.length}`, `What each kept · ${m.notes.length}`)}</SectionLabel> : null}
        {m.notes.length ? (
          <Card style={{ paddingVertical: space.xs }}>
            {m.notes.map((n, i) => (
              <Pressable key={n.id} onPress={() => menu(n.id, n.text)} accessibilityRole="button"
                style={({ pressed }) => [styles.item, i > 0 && { borderTopColor: t.line, borderTopWidth: StyleSheet.hairlineWidth }, { opacity: pressed ? 0.6 : 1 }]}>
                <T v="callout"><T v="callout" style={{ fontWeight: '600' }}>{n.name}</T>{`${L('：', ': ')}${n.text}`}</T>
                <T v="caption" color={t.ink3}>{`${L(`第 ${n.round} 轮`, `Round ${n.round}`)} · ${n.dayLabel}`}</T>
              </Pressable>
            ))}
          </Card>
        ) : null}
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  intro: { fontSize: 13, lineHeight: 19, paddingHorizontal: space.xs, paddingTop: space.md },
  item: { gap: 4, paddingVertical: 12 },
});
