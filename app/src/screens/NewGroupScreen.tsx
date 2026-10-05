import React, { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, TextInput, View } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { agentName } from '../brand';
import { GroupBadge } from '../components/GroupIcon';
import { AGENT_COLORS, IconColorPicker } from '../components/IconColorPicker';
import { BookOpen, ChevronRight, MessageCircle } from '../components/icons';
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

/** 从例子开始：点一个，名字、职责、图标填好（都能改）。学习的那个建好就带学习台（学习看板 + 功能包 + 改课的 skill）。 */
const EXAMPLES = () => [
  { key: 'study', label: L('学习', 'Study'), icon: 'book' as GroupIcon, purpose: L('管我的课：课件、学习页、复习和截止。每一节出学习路线、闪卡和小测，考前帮我查漏。', 'Looks after my courses: slides, study notes, review and deadlines. A study path, flashcards and a quiz for every session, and gap-checking before exams.') },
  { key: 'fit', label: L('训练', 'Training'), icon: 'dumbbell' as GroupIcon, purpose: L('记训练、看恢复，按我的目标排下周计划。', 'Logs workouts, watches recovery and plans next week around my goals.') },
  { key: 'diet', label: L('饮食', 'Meals'), icon: 'utensils' as GroupIcon, purpose: L('记每顿吃了什么，算热量和蛋白质，给下一顿出主意。', 'Logs every meal, counts calories and protein, and suggests the next one.') },
  { key: 'sleep', label: L('睡眠', 'Sleep'), icon: 'moon' as GroupIcon, purpose: L('记录每晚几点睡、几点起，找规律，提醒我别熬夜。', 'Tracks when I sleep and wake, spots patterns and nudges me off late nights.') },
  { key: 'money', label: L('记账', 'Money'), icon: 'wallet' as GroupIcon, purpose: L('记开销、看预算，月底给我一份小结。', 'Logs spending, watches the budget and sums up the month.') },
  { key: 'job', label: L('求职', 'Job hunt'), icon: 'briefcase' as GroupIcon, purpose: L('管投递、面试和截止，帮我准备每一轮。', 'Tracks applications, interviews and deadlines, and preps me for each round.') },
  { key: 'trip', label: L('出行', 'Travel'), icon: 'plane' as GroupIcon, purpose: L('管行程、车票和酒店，出发前提醒我。', 'Keeps trips, tickets and hotels, and reminds me before I leave.') },
];

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
  const [example, setExample] = useState<string | null>(null);
  const pickExample = (k: string) => {
    if (example === k) { setExample(null); return; }
    const ex = EXAMPLES().find((x) => x.key === k);
    if (!ex) return;
    setExample(k);
    setName(ex.label);
    setPurpose(ex.purpose);
    setIcon(ex.icon);
    setErr('');
  };
  // 出错提示放在相关的地方：没起名在名字下面，创建没成功在「创建」下面，「先聊聊」的错在它下面
  type ErrAt = 'name' | 'create' | 'talk';
  const [err, setErrState] = useState<{ text: string; at: ErrAt }>({ text: '', at: 'name' });
  const setErr = (text: string, at: ErrAt = 'create') => setErrState({ text, at });
  const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

  const create = () => {
    if (busy) return;
    if (!name.trim()) { setErr(L('请先为此 Agent 命名', 'Give this Agent a name first'), 'name'); return; }
    if (!connected) { setErr(L('未连接服务器，无法创建', "Not connected to the server, can't create it")); return; }
    setBusy('create');
    const study = example === 'study';
    addGroup({ name: name.trim(), purpose: purpose.trim(), icon, color, modelId, ...(study ? { template: 'study' } : {}) })
      .then((id) => nav.replace('Group', study ? { id, tab: 'board' } : { id }))  // 服务器已建好 OpenClaw agent（独立工作区、记忆、skills）；学习的直接看学习台
      .catch((e) => { setErr(errText(e)); setBusy(null); });
  };

  // 还没想好：开一个项目，把想法发过去，跳到那个项目里接着聊
  const talk = () => {
    if (busy) return;
    if (!connected) { setErr(L('未连接服务器，无法创建项目', "Not connected to the server, can't open a project"), 'talk'); return; }
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
      <NavHeader title={L('新建 Agent', 'New Agent')} onBack={() => nav.goBack()} />
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingTop: space.xl, paddingBottom: space.xxl }} keyboardShouldPersistTaps="handled" automaticallyAdjustKeyboardInsets keyboardDismissMode="interactive">
        {/* 预览：改名字、职责、颜色、图标，这里跟着变 */}
        <View style={styles.preview} accessible accessibilityLabel={L(`预览：${name.trim() || '还没起名'}`, `Preview: ${name.trim() || 'no name yet'}`)}>
          <GroupBadge icon={icon} color={color} size={64} />
          <View style={{ flex: 1, gap: 2 }}>
            <T v="title" numberOfLines={1} color={name.trim() ? t.ink : t.ink3} style={{ fontWeight: '700' }}>{name.trim() || L('未命名', 'No name yet')}</T>
            <T v="callout" color={t.ink3} numberOfLines={2} style={{ fontSize: 13, lineHeight: 18 }}>{purpose.trim() || L('尚未填写职责', 'No description yet')}</T>
          </View>
        </View>

        <SectionLabel>{L('从示例开始', 'Start from an example')}</SectionLabel>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm }}>
          {EXAMPLES().map((ex) => {
            const on = example === ex.key;
            return (
              <Pressable key={ex.key} onPress={() => pickExample(ex.key)} accessibilityRole="button" accessibilityState={{ selected: on }}
                style={[styles.example, { borderColor: on ? t.cyan : t.line, backgroundColor: on ? t.cyanSoft : t.surface }]}>
                <T v="callout" color={on ? t.cyan : t.ink2} style={{ fontWeight: '600' }}>{ex.label}</T>
              </Pressable>
            );
          })}
        </View>
        {example === 'study' ? (
          <View style={[styles.studyNote, { backgroundColor: t.cyanSoft }]}>
            <BookOpen size={18} color={t.cyan} />
            <View style={{ flex: 1, gap: 2 }}>
              <T v="headline" style={{ fontSize: 14 }}>{L('附带学习台', 'Includes the study desk')}</T>
              <T v="callout" color={t.ink2} style={{ fontSize: 13, lineHeight: 18 }}>{L('创建后即附带学习台：添加课程后，每节课都有学习页、学习路线、闪卡和小测。可在手机上复习做题，在电脑上并排查看课件和学习页。',
                'Add courses; every session gets study notes, a study path, flashcards and a quiz. Review on your phone; view slides and notes side by side on a computer.')}</T>
            </View>
          </View>
        ) : null}

        <SectionLabel>{L('名称', 'Name')}</SectionLabel>
        <TextInput value={name} onChangeText={(v) => { setName(v); setErr(''); }} placeholder={L('例如：睡眠', 'e.g. Sleep')} placeholderTextColor={t.ink3}
          accessibilityLabel={L('Agent 名称', 'Agent name')}style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink }]} />
        {err.text && err.at === 'name' ? <T v="callout" color={t.bad} style={{ marginTop: 6 }}>{err.text}</T> : null}

        <SectionLabel>{L('职责', 'What it does')}</SectionLabel>
        <TextInput value={purpose} onChangeText={setPurpose} multiline
          placeholder={L('用一两句话描述职责。例如：记录每晚的入睡和起床时间，分析规律，提醒我按时休息。', 'Its job in a sentence or two. E.g. Log when I fall asleep and wake up, spot patterns, remind me not to stay up late.')}
          placeholderTextColor={t.ink3} accessibilityLabel={L('Agent 职责', 'Agent purpose')}
          style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink, minHeight: 96, textAlignVertical: 'top' }]} />
        <Pressable onPress={talk} disabled={!!busy} accessibilityRole="button" accessibilityState={{ busy: busy === 'talk' }}
          style={({ pressed }) => [styles.talk, { backgroundColor: t.goldSoft, opacity: pressed || busy === 'talk' ? 0.7 : 1 }]}>
          <View style={[styles.talkIcon, { backgroundColor: t.surface }]}><MessageCircle size={18} color={t.gold} /></View>
          <View style={{ flex: 1, gap: 2 }}>
            <T v="headline" style={{ fontSize: 15, fontWeight: '700' }}>{busy === 'talk' ? L('正在创建项目…', 'Creating a project…') : L('尚未确定？先讨论方案', 'Not sure yet? Talk it through')}</T>
            <T v="callout" color={t.ink2} style={{ fontSize: 13, lineHeight: 19 }}>{L(
              `创建一个项目，与 ${agentName()} 一起明确它的职责、记录内容和看板布局。方案确定后，经你确认即可创建。`,
              `Opens a project where you and ${agentName()} work out what it looks after, what it tracks and what goes on its dashboard. It then drafts a plan, and the Agent is created once you approve.`,
            )}</T>
          </View>
          <ChevronRight size={16} color={t.gold} />
        </Pressable>
        {err.text && err.at === 'talk' ? <T v="callout" color={t.bad} style={{ marginTop: 6 }}>{err.text}</T> : null}

        <IconColorPicker icon={icon} color={color} onIcon={setIcon} onColor={setColor} />

        <SectionLabel>{L('默认模型', 'Default model')}</SectionLabel>
        <ModelField value={modelId} onChange={setModelId} />
        <T v="callout" color={t.ink3} style={{ marginTop: 6, paddingHorizontal: space.xs }}>{L(
          '默认与主对话相同。记录类 Agent 使用低成本模型即可；需要规划和判断的建议使用高成本模型。进入对话后可随时切换。',
          'Same as the main chat by default. A low-cost model is enough for logging Agents; use a higher-cost one for planning and judgment. You can switch at any time in the chat.',
        )}</T>

        <View style={{ marginTop: space.xl }}><Btn label={busy === 'create' ? L('正在创建…', 'Creating…') : L('创建', 'Create')} onPress={create} /></View>
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
  example: { height: 34, paddingHorizontal: 14, borderRadius: 17, borderWidth: 1.5, alignItems: 'center', justifyContent: 'center' },
  studyNote: { flexDirection: 'row', alignItems: 'flex-start', gap: space.md, borderRadius: 14, padding: space.md, marginTop: space.md },
});
