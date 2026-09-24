import React, { useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import { Check, ChevronDown } from './icons';
import { MODELS } from '../data/models';
import { useStore } from '../store';
import type { ModelOption } from '../data/types';
import { radius, space, useTheme } from '../theme';
import { useSheet } from './Sheet';
import { Pill, T } from './ui';

export const modelOf = (id?: string): ModelOption | undefined => MODELS.find((m) => m.id === id);

/** 目录里写的是"订阅"，但这家的订阅登录过期了，实际会退到 API key 按量计费。 */
export function useBilling() {
  const { models } = useStore();
  const expired = new Set((models?.providers ?? []).filter((p) => p.subscription && p.status !== 'ok').map((p) => p.provider));
  return (m: ModelOption): ModelOption['billing'] => (m.billing === '订阅' && expired.has(m.id.split('/')[0]) ? 'API' : m.billing);
}

function Option({ m, on, onPress }: { m: ModelOption; on: boolean; onPress: () => void }) {
  const t = useTheme();
  const billing = useBilling()(m);
  return (
    <Pressable onPress={onPress} accessibilityRole="radio" accessibilityState={{ selected: on }}
      style={({ pressed }) => [styles.opt, { backgroundColor: t.surface, borderColor: on ? t.goldFill : 'transparent', opacity: pressed ? 0.7 : 1 }]}>
      <View style={{ flex: 1, gap: 4 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          <T v="headline">{m.name}</T>
          <Pill label={billing} tone={billing === '订阅' ? 'gold' : billing === '免费' ? 'good' : 'cyan'} />
          {m.cost === '贵' ? <Pill label="贵" tone="warn" /> : m.cost === '省' ? <Pill label="省" tone="neutral" /> : null}
        </View>
        <T v="callout" color={t.ink2}>{m.note}</T>
      </View>
      {on ? <Check size={20} color={t.gold} /> : null}
    </Pressable>
  );
}

/** 只列 Gateway 允许列表里的模型（openclaw.json 的 modelPolicy.allow）；还没读到就先按目录全列。 */
function useAllowedModels() {
  const { models } = useStore();
  return models ? MODELS.filter((m) => models.allowed.includes(m.id)) : MODELS;
}

function ModelList({ value, onPick }: { value: string; onPick: (id: string) => void }) {
  const t = useTheme();
  const allowed = useAllowedModels();
  const current = modelOf(value);
  const [more, setMore] = useState(current ? !current.featured : false);
  const list = allowed.filter((m) => m.featured || more);
  return (
    <View style={{ gap: space.sm }} accessibilityRole="radiogroup">
      {list.map((m) => <Option key={m.id} m={m} on={m.id === value} onPress={() => onPick(m.id)} />)}
      {!more ? (
        <Pressable onPress={() => setMore(true)} style={{ paddingVertical: space.md, alignItems: 'center' }} accessibilityRole="button">
          <T v="callout" color={t.gold}>更多模型（{allowed.filter((m) => !m.featured).length}）</T>
        </Pressable>
      ) : null}
      <T v="callout" color={t.ink3} style={{ marginTop: space.xs }}>
        切换只影响当前对话，记忆和历史不变。订阅额度用尽时会按回退链自动换下一个，回复上标的是实际回答的模型。
      </T>
    </View>
  );
}

/** 聊天顶部的模型切换按钮。 */
export function ModelSwitch({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const t = useTheme();
  const sheet = useSheet();
  const m = modelOf(value);
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={`当前模型 ${m?.name}，点击切换`}
      onPress={() => sheet.open({ title: '这段对话用哪个模型', content: (close) => <ModelList value={value} onPick={(id) => { onChange(id); close(); }} /> })}
      style={({ pressed }) => [styles.switch, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
      <View style={{ width: 7, height: 7, borderRadius: 4, backgroundColor: m?.billing === '订阅' ? t.goldFill : t.chartA }} />
      <T v="caption" style={{ fontSize: 13 }}>{m?.short ?? '模型'}</T>
      <ChevronDown size={14} color={t.ink2} />
    </Pressable>
  );
}

/** 表单里用的模型选择行（新建 Agent）。 */
export function ModelField({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const t = useTheme();
  const sheet = useSheet();
  const m = modelOf(value);
  return (
    <Pressable accessibilityRole="button"
      onPress={() => sheet.open({ title: '这个 Agent 默认用哪个模型', content: (close) => <ModelList value={value} onPick={(id) => { onChange(id); close(); }} /> })}
      style={[styles.field, { backgroundColor: t.surface }]}>
      <T v="body">{m?.name}</T>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
        <T v="callout" color={t.ink2}>{m?.billing}</T>
        <ChevronDown size={16} color={t.ink3} />
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  opt: { flexDirection: 'row', alignItems: 'center', gap: space.md, borderRadius: radius.md, padding: space.lg, borderWidth: 1.5 },
  switch: { flexDirection: 'row', alignItems: 'center', gap: 6, borderRadius: radius.pill, paddingHorizontal: 12, paddingVertical: 7 },
  field: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', borderRadius: radius.md, paddingHorizontal: space.lg, paddingVertical: 14 },
});
