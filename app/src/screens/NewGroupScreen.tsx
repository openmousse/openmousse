import React, { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, TextInput, View } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { agentName } from '../brand';
import { GroupBadge } from '../components/GroupIcon';
import { AGENT_COLORS, IconColorPicker } from '../components/IconColorPicker';
import { ChevronRight, MessageCircle } from '../components/icons';
import { ModelField } from '../components/ModelPicker';
import { SheetProvider } from '../components/Sheet';
import { Btn, NavHeader, Screen, SectionLabel, T } from '../components/ui';
import type { AgentColor, GroupIcon } from '../data/types';
import { L } from '../i18n';
import { openThread } from '../navigation';
import { useStore } from '../store';
import { radius, space, type, useTheme } from '../theme';

// 这一页是原生 modal，盖在根部 SheetProvider 之上；弹层要在页内自己挂一个，不然模型列表会被压在这一页后面。
export function NewGroupScreen() {
  return <SheetProvider><NewGroupForm /></SheetProvider>;
}

/** 「先聊聊」发进新空间的第一句：让助手帮着想清楚，想好了按 agent-builder 出方案、用收件箱交一张新 Agent 的卡。 */
function openingMessage(name: string, purpose: string): string {
  return L(
    `我想新建一个 Agent${name ? `「${name}」` : ''}，还没想好它具体负责什么。帮我一起想清楚：它管什么、要记哪些数据、从哪里来、看板放什么、和哪些 Agent 联动。想清楚了按 agent-builder 出一份方案，用收件箱给我一张新 Agent 的卡（带上名字、图标和颜色），我点同意再建。${purpose ? `我现在的想法：${purpose}` : ''}`,
    `I want to create a new agent${name ? ` called "${name}"` : ''}, but I haven't worked out exactly what it should handle. Help me think it through: what it looks after, what data it should keep and where that comes from, what goes on its dashboard, and which agents it works with. Once it's clear, draw up a plan with agent-builder and send me a new-agent card in the inbox (with its name, icon and color); I'll create it when I approve.${purpose ? ` What I have in mind so far: ${purpose}` : ''}`,
  );
}

function NewGroupForm() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { addGroup, createSideChat, send, connected, threadModel, groups } = useStore();
  const [busy, setBusy] = useState<'create' | 'talk' | null>(null);
  const [name, setName] = useState('');
  const [purpose, setPurpose] = useState('');
  const [icon, setIcon] = useState<GroupIcon>('moon');
  // 没挑过颜色：默认用一个还没有 Agent 用的（金色是助手自己的颜色，排最后），列表里一眼分得开
  const [picked, setColor] = useState<AgentColor | null>(null);
  const color: AgentColor = picked ?? [...AGENT_COLORS.filter((c) => c !== 'gold'), 'gold' as const].find((c) => !groups.some((g) => (g.color ?? 'cyan') === c)) ?? 'cyan';
  const [modelId, setModelId] = useState(threadModel.main);
  // 出错提示放在相关的地方：没起名在名字下面，创建没成功在「创建」下面，「先聊聊」的错在它下面
  type ErrAt = 'name' | 'create' | 'talk';
  const [err, setErrState] = useState<{ text: string; at: ErrAt }>({ text: '', at: 'name' });
  const setErr = (text: string, at: ErrAt = 'create') => setErrState({ text, at });
  const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

  const create = () => {
    if (busy) return;
    if (!name.trim()) { setErr(L('先给这个 Agent 起个名字', 'Give this agent a name first'), 'name'); return; }
    if (!connected) { setErr(L('没连上服务器，建不了', "Not connected to the server, can't create it")); return; }
    setBusy('create');
    addGroup({ name: name.trim(), purpose: purpose.trim(), icon, color, modelId })
      .then((id) => nav.replace('Group', { id }))  // 服务器已建好 OpenClaw agent（独立工作区、记忆、skills）
      .catch((e) => { setErr(errText(e)); setBusy(null); });
  };

  // 还没想好：开一个独立空间，把想法发过去，跳到那个空间里接着聊
  const talk = () => {
    if (busy) return;
    if (!connected) { setErr(L('没连上服务器，开不了', "Not connected to the server, can't open it"), 'talk'); return; }
    const n = name.trim();
    const p = purpose.trim();
    setBusy('talk');
    createSideChat({
      title: L(`新 Agent：${n || '还没起名'}`, `New agent: ${n || 'unnamed'}`),
      purpose: L('一起想清楚新 Agent 管什么，想好了出方案', 'Work out what the new agent should do, then draft a plan'),
      modelId,
    })
      .then((id) => { send(id, openingMessage(n, p)); openThread(id, false); })
      .catch((e) => { setErr(errText(e), 'talk'); setBusy(null); });
  };

  return (
    <Screen>
      <NavHeader title={L('新建 Agent', 'New agent')} onBack={() => nav.goBack()} />
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingTop: space.xl, paddingBottom: space.xxl }} keyboardShouldPersistTaps="handled" automaticallyAdjustKeyboardInsets keyboardDismissMode="interactive">
        {/* 预览：改名字、职责、颜色、图标，这里跟着变 */}
        <View style={styles.preview} accessible accessibilityLabel={L(`预览：${name.trim() || '还没起名'}`, `Preview: ${name.trim() || 'no name yet'}`)}>
          <GroupBadge icon={icon} color={color} size={64} />
          <View style={{ flex: 1, gap: 2 }}>
            <T v="title" numberOfLines={1} color={name.trim() ? t.ink : t.ink3} style={{ fontWeight: '700' }}>{name.trim() || L('还没起名', 'No name yet')}</T>
            <T v="callout" color={t.ink3} numberOfLines={2} style={{ fontSize: 13, lineHeight: 18 }}>{purpose.trim() || L('还没写它负责什么', "What it does isn't written yet")}</T>
          </View>
        </View>

        <SectionLabel>{L('名字', 'Name')}</SectionLabel>
        <TextInput value={name} onChangeText={(v) => { setName(v); setErr(''); }} placeholder={L('比如：睡眠', 'e.g. Sleep')} placeholderTextColor={t.ink3}
          accessibilityLabel={L('Agent 名字', 'Agent name')} style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink }]} />
        {err.text && err.at === 'name' ? <T v="callout" color={t.bad} style={{ marginTop: 6 }}>{err.text}</T> : null}

        <SectionLabel>{L('它负责什么', 'What it does')}</SectionLabel>
        <TextInput value={purpose} onChangeText={setPurpose} multiline
          placeholder={L('一两句话说清职责。比如：记录每晚几点睡、几点起，找规律，提醒我别熬夜。', 'Its job in a sentence or two. E.g. Log when I fall asleep and wake up, spot patterns, remind me not to stay up late.')}
          placeholderTextColor={t.ink3} accessibilityLabel={L('Agent 职责', 'Agent purpose')}
          style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink, minHeight: 96, textAlignVertical: 'top' }]} />
        <Pressable onPress={talk} disabled={!!busy} accessibilityRole="button" accessibilityState={{ busy: busy === 'talk' }}
          style={({ pressed }) => [styles.talk, { backgroundColor: t.goldSoft, opacity: pressed || busy === 'talk' ? 0.7 : 1 }]}>
          <View style={[styles.talkIcon, { backgroundColor: t.surface }]}><MessageCircle size={18} color={t.gold} /></View>
          <View style={{ flex: 1, gap: 2 }}>
            <T v="headline" style={{ fontSize: 15, fontWeight: '700' }}>{busy === 'talk' ? L('正在开一个空间…', 'Opening a side chat…') : L('还没想好？先聊聊', 'Not sure yet? Talk it through')}</T>
            <T v="callout" color={t.ink2} style={{ fontSize: 13, lineHeight: 19 }}>{L(
              `开一个独立空间，和 ${agentName()} 一起想清楚它管什么、记什么、看板放什么。想好了它出方案，你点头就建好。`,
              `Opens a side chat where you and ${agentName()} work out what it looks after, what it keeps track of and what goes on its dashboard. Then it drafts a plan, and the agent is built once you approve.`,
            )}</T>
          </View>
          <ChevronRight size={16} color={t.gold} />
        </Pressable>
        {err.text && err.at === 'talk' ? <T v="callout" color={t.bad} style={{ marginTop: 6 }}>{err.text}</T> : null}

        <IconColorPicker icon={icon} color={color} onIcon={setIcon} onColor={setColor} />

        <SectionLabel>{L('默认模型', 'Default model')}</SectionLabel>
        <ModelField value={modelId} onChange={setModelId} />
        <T v="callout" color={t.ink3} style={{ marginTop: 6, paddingHorizontal: space.xs }}>{L(
          '默认跟主对话一样。记录类的 Agent 用省钱的就够；需要规划和判断的用贵的。进对话后随时能换。',
          'Same as the main chat by default. A cheap model is enough for logging agents; use a pricier one for planning and judgment. You can switch anytime in the chat.',
        )}</T>

        <View style={{ marginTop: space.xl }}><Btn label={busy === 'create' ? L('创建中…', 'Creating…') : L('创建', 'Create')} onPress={create} /></View>
        {err.text && err.at === 'create' ? <T v="callout" color={t.bad} style={{ marginTop: 6 }}>{err.text}</T> : null}
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  input: { borderRadius: radius.md, paddingHorizontal: space.lg, paddingVertical: 13 },
  preview: { flexDirection: 'row', alignItems: 'center', gap: 14, paddingHorizontal: space.xs },
  talk: { flexDirection: 'row', alignItems: 'center', gap: space.md, borderRadius: 14, paddingVertical: space.md, paddingHorizontal: 14, marginTop: space.sm },
  talkIcon: { width: 36, height: 36, borderRadius: 18, alignItems: 'center', justifyContent: 'center' },
});
