import React, { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, TextInput, View } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { GROUP_ICONS, GroupBadge } from '../components/GroupIcon';
import { ModelField } from '../components/ModelPicker';
import { SheetProvider } from '../components/Sheet';
import { Btn, NavHeader, Screen, SectionLabel, T } from '../components/ui';
import type { GroupIcon } from '../data/types';
import { L } from '../i18n';
import { useStore } from '../store';
import { radius, space, type, useTheme } from '../theme';

// 这一页是原生 modal，盖在根部 SheetProvider 之上；弹层要在页内自己挂一个，不然模型列表会被压在这一页后面。
export function NewGroupScreen() {
  return <SheetProvider><NewGroupForm /></SheetProvider>;
}

function NewGroupForm() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { addGroup, connected, threadModel } = useStore();
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState('');
  const [purpose, setPurpose] = useState('');
  const [icon, setIcon] = useState<GroupIcon>('moon');
  const [modelId, setModelId] = useState(threadModel.main);
  const [err, setErr] = useState('');

  const create = () => {
    if (!name.trim()) { setErr(L('先给这个 Agent 起个名字', 'Give this agent a name first')); return; }
    if (!connected) { setErr(L('没连上服务器，建不了', "Not connected to the server, can't create it")); return; }
    setBusy(true);
    addGroup({ name: name.trim(), purpose: purpose.trim(), icon, modelId })
      .then((id) => nav.replace('Group', { id }))  // 服务器已建好 OpenClaw agent（独立工作区、记忆、skills）
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setBusy(false));
  };

  return (
    <Screen>
      <NavHeader title={L('新建 Agent', 'New agent')} onBack={() => nav.goBack()} />
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl }} keyboardShouldPersistTaps="handled">
        <SectionLabel>{L('名字', 'Name')}</SectionLabel>
        <TextInput value={name} onChangeText={(v) => { setName(v); setErr(''); }} placeholder={L('比如：睡眠', 'e.g. Sleep')} placeholderTextColor={t.ink3}
          accessibilityLabel={L('Agent 名字', 'Agent name')} style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink }]} />
        {err ? <T v="callout" color={t.bad} style={{ marginTop: 6 }}>{err}</T> : null}

        <SectionLabel>{L('它负责什么', 'What it does')}</SectionLabel>
        <TextInput value={purpose} onChangeText={setPurpose} multiline
          placeholder={L('一两句话说清职责。比如：记录每晚入睡和起床时间，发现规律，提醒我别熬夜。', 'Its job in a sentence or two. E.g. Log when I fall asleep and wake up, spot patterns, remind me not to stay up late.')}
          placeholderTextColor={t.ink3} accessibilityLabel={L('Agent 职责', 'Agent purpose')}
          style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink, minHeight: 96, textAlignVertical: 'top' }]} />

        <SectionLabel>{L('图标', 'Icon')}</SectionLabel>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.md }}>
          {GROUP_ICONS.map((g) => (
            <Pressable key={g.key} onPress={() => setIcon(g.key)} accessibilityRole="radio" accessibilityState={{ selected: icon === g.key }} accessibilityLabel={g.label}
              style={{ alignItems: 'center', gap: 4 }}>
              <GroupBadge icon={g.key} size={52} active={icon === g.key} />
              <T v="caption" color={icon === g.key ? t.ink : t.ink3}>{g.label}</T>
            </Pressable>
          ))}
        </View>

        <SectionLabel>{L('默认模型', 'Default model')}</SectionLabel>
        <ModelField value={modelId} onChange={setModelId} />
        <T v="callout" color={t.ink3} style={{ marginTop: 6, paddingHorizontal: space.xs }}>{L(
          '默认跟主对话一样。记录类的 Agent 用省钱的就够；需要规划和判断的用贵的。进对话后随时能换。',
          'Same as the main chat by default. A cheap model is enough for logging agents; use a pricier one for planning and judgment. You can switch anytime in the chat.',
        )}</T>

        <View style={{ marginTop: space.xl }}><Btn label={busy ? L('创建中…', 'Creating…') : L('创建', 'Create')} onPress={() => !busy && create()} /></View>
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({ input: { borderRadius: radius.md, paddingHorizontal: space.lg, paddingVertical: 13 } });
