import React, { useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import { Check, ChevronDown } from './icons';
import { MODELS, billingLabel, costLabel } from '../data/models';
import { L } from '../i18n';
import { useStore } from '../store';
import type { ModelOption } from '../data/types';
import { radius, space, useTheme } from '../theme';
import { useSheet } from './Sheet';
import { Pill, T } from './ui';

export const modelOf = (id?: string): ModelOption | undefined => MODELS.find((m) => m.id === id);
/** 目录里没有的模型（比如 OpenClaw 里配的 DeepSeek）：名字取 id 里 / 后面那段，计费和用途不知道就不标。 */
const plainModel = (id: string): ModelOption => {
  const short = id.split('/').pop() || id;
  return { id, name: short, short, billing: 'API', cost: '中', note: '', featured: true, plain: true };
};

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
          {m.plain ? null : <Pill label={billingLabel(billing)} tone={billing === '订阅' ? 'gold' : billing === '免费' ? 'good' : 'cyan'} />}
          {m.plain ? null : m.cost === '贵' ? <Pill label={costLabel(m.cost)} tone="warn" /> : m.cost === '省' ? <Pill label={costLabel(m.cost)} tone="neutral" /> : null}
        </View>
        <T v="callout" color={t.ink2}>{m.plain ? m.id : m.note}</T>
      </View>
      {on ? <Check size={20} color={t.gold} /> : null}
    </Pressable>
  );
}

/** 只列 Gateway 允许列表里的模型（openclaw.json 的 modelPolicy.allow）；还没读到就先按目录全列。 */
function useAllowedModels(): ModelOption[] {
  const { models } = useStore();
  if (!models) return MODELS;
  // 允许列表空 = OpenClaw 没配（新装的就是这样，都能用）：列它配好的主模型和回退，不列目录里这台服务器多半没登录的
  const ids = models.allowed.length ? models.allowed : [models.primary, ...(models.fallbacks ?? [])].filter((x): x is string => !!x);
  return ids.map((id) => modelOf(id) ?? plainModel(id));
}

/** 别的 claw（不是 OpenClaw）：模型就是服务器 claw 段写的那几个（/api/models 的 allowed），app 的模型目录里没有它们，原样列出。OpenClaw = null。 */
function useClawModels(): { ids: string[]; name: string } | null {
  const { claw, models } = useStore();
  if (claw.kind === 'openclaw') return null;
  return { ids: models?.allowed ?? [], name: claw.name };
}

function PlainList({ ids, value, onPick }: { ids: string[]; value: string; onPick: (id: string) => void }) {
  const t = useTheme();
  return (
    <View style={{ gap: space.sm }} accessibilityRole="radiogroup">
      {ids.map((id) => (
        <Pressable key={id} onPress={() => onPick(id)} accessibilityRole="radio" accessibilityState={{ selected: id === value }}
          style={({ pressed }) => [styles.opt, { backgroundColor: t.surface, borderColor: id === value ? t.goldFill : 'transparent', opacity: pressed ? 0.7 : 1 }]}>
          <T v="headline" style={{ flex: 1 }}>{id}</T>
          {id === value ? <Check size={20} color={t.gold} /> : null}
        </Pressable>
      ))}
      <T v="callout" color={t.ink3} style={{ marginTop: space.xs }}>{L('切换只影响当前对话。能选哪些模型在服务器的 server.json（claw 段）里配。',
        "Switching only affects this chat. The models to choose from are set on the server, in server.json's claw section.")}</T>
    </View>
  );
}

function ModelList({ value, onPick }: { value: string; onPick: (id: string) => void }) {
  const t = useTheme();
  const other = useClawModels();
  const allowed = useAllowedModels();
  const current = modelOf(value);
  const [more, setMore] = useState(current ? !current.featured : false);
  const list = allowed.filter((m) => m.featured || more);
  if (other) return <PlainList ids={other.ids} value={value} onPick={onPick} />;
  return (
    <View style={{ gap: space.sm }} accessibilityRole="radiogroup">
      {list.map((m) => <Option key={m.id} m={m} on={m.id === value} onPress={() => onPick(m.id)} />)}
      {!more && allowed.some((m) => !m.featured) ? (
        <Pressable onPress={() => setMore(true)} style={{ paddingVertical: space.md, alignItems: 'center' }} accessibilityRole="button">
          <T v="callout" color={t.gold}>{L(`更多模型（${allowed.filter((m) => !m.featured).length}）`, `More models (${allowed.filter((m) => !m.featured).length})`)}</T>
        </Pressable>
      ) : null}
      <T v="callout" color={t.ink3} style={{ marginTop: space.xs }}>
        {L('切换只影响当前对话，记忆和历史不变。订阅额度用尽时会按回退链自动换下一个，回复上标的是实际回答的模型。', 'Switching only affects this chat; memory and history stay the same. When subscription quota runs out, the fallback chain moves to the next model, and each reply shows the model that actually answered.')}
      </T>
    </View>
  );
}

/** 聊天顶部的模型切换按钮。 */
export function ModelSwitch({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const t = useTheme();
  const sheet = useSheet();
  const other = useClawModels();
  const m = modelOf(value);
  if (other && other.ids.length <= 1) {  // 别的 claw、只有一个模型：不能换，只写是谁在答
    return (
      <View style={[styles.switch, { backgroundColor: t.surface }]} accessibilityLabel={L(`由 ${other.name} 回答`, `Answered by ${other.name}`)}>
        <View style={{ width: 7, height: 7, borderRadius: 4, backgroundColor: t.chartA }} />
        <T v="caption" style={{ fontSize: 13 }}>{other.name}</T>
      </View>
    );
  }
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={L(`当前模型 ${m?.name}，点击切换`, `Current model ${m?.name}, tap to switch`)}
      onPress={() => sheet.open({ title: L('这段对话用哪个模型', 'Model for this chat'), content: (close) => <ModelList value={value} onPick={(id) => { onChange(id); close(); }} /> })}
      style={({ pressed }) => [styles.switch, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
      <View style={{ width: 7, height: 7, borderRadius: 4, backgroundColor: m?.billing === '订阅' ? t.goldFill : t.chartA }} />
      <T v="caption" style={{ fontSize: 13 }} numberOfLines={1}>{other ? value : m?.short ?? (value ? plainModel(value).short : L('模型', 'Model'))}</T>
      <ChevronDown size={14} color={t.ink2} />
    </Pressable>
  );
}

/** 表单里用的模型选择行（新建 Agent）。 */
export function ModelField({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const t = useTheme();
  const sheet = useSheet();
  const other = useClawModels();
  const m = modelOf(value);
  if (other && other.ids.length <= 1) {  // 别的 claw、只有一个模型：没得选
    return (
      <View style={[styles.field, { backgroundColor: t.surface }]}>
        <T v="body">{other.name}</T>
        <T v="callout" color={t.ink2}>{other.ids[0] ?? ''}</T>
      </View>
    );
  }
  return (
    <Pressable accessibilityRole="button"
      onPress={() => sheet.open({ title: L('这个 Agent 默认用哪个模型', 'Default model for this agent'), content: (close) => <ModelList value={value} onPick={(id) => { onChange(id); close(); }} /> })}
      style={[styles.field, { backgroundColor: t.surface }]}>
      <T v="body">{other ? value : m?.name}</T>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
        <T v="callout" color={t.ink2}>{m ? billingLabel(m.billing) : null}</T>
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
