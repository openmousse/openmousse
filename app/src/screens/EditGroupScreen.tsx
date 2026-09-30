// 编辑 Agent：名字、图标和颜色、职责、默认模型；看它用到哪些数据；最底下删除。
// 从 Agent 页顶上点名字（旁边的小笔）进来，原生 modal。保存 = PATCH /api/groups/{id}，只发改了的。
import React, { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, TextInput, View } from 'react-native';
import { useNavigation, useRoute } from '@react-navigation/native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { httpStatus } from '../api/base';
import { GroupBadge, iconKey } from '../components/GroupIcon';
import { IconColorPicker } from '../components/IconColorPicker';
import { ModelField } from '../components/ModelPicker';
import { SheetProvider, useSheet } from '../components/Sheet';
import { Btn, Screen, SectionLabel, T } from '../components/ui';
import type { AgentColor, Group, GroupIcon, GroupPatch } from '../data/types';
import { L } from '../i18n';
import { useStore } from '../store';
import { radius, space, type, useTheme } from '../theme';

const COLORS: AgentColor[] = ['cyan', 'gold', 'green', 'purple', 'pink', 'orange'];
const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

// 原生 modal 盖在根部 SheetProvider 之上：换图标、选模型、确认删除的弹层都挂在页内这一个上。
export function EditGroupScreen() {
  return <SheetProvider><EditGroupForm /></SheetProvider>;
}

/** 这个 Agent 的看板用到哪些数据源（app 已经知道的：看板类型 + 服务器接了哪些源）。没有看板就是空，那一行不显示。 */
function useDataChips(g: Group): { label: string; on: boolean }[] {
  const { live } = useStore();
  const src = live?.sources ?? {};
  const has = (k: 'workouts' | 'meals' | 'health') => src[k] !== false;
  const health = L('Apple 健康', 'Apple Health');
  switch (g.dashboard) {
    case 'fitness': return [{ label: live?.week?.source || L('训练记录', 'Workout log'), on: has('workouts') }, { label: health, on: has('health') }];
    case 'diet': return [{ label: live?.diet?.source || L('饮食记录', 'Meal log'), on: has('meals') }, { label: health, on: has('health') }];
    case 'health': return [{ label: health, on: has('health') }];
    case 'apply':
    case 'masters': return [{ label: L('申请记录', 'Application tracker'), on: true }];
    case 'study': return [{ label: L('学习台', 'Study desk'), on: true }];
    default: return [];
  }
}

/** 弹层里挑图标和颜色：自己记着选中的（弹层内容不跟着页面重画），每点一下同时告诉页面，后面的大图标跟着变。 */
function PickerSheet({ icon, color, onIcon, onColor, close }: { icon: GroupIcon | null; color: AgentColor; onIcon: (i: GroupIcon) => void; onColor: (c: AgentColor) => void; close: () => void }) {
  const [i, setI] = useState(icon);
  const [c, setC] = useState(color);
  return (
    <View>
      <View style={{ alignItems: 'center' }}><GroupBadge icon={i} color={c} size={64} /></View>
      <IconColorPicker icon={i} color={c} onIcon={(v) => { setI(v); onIcon(v); }} onColor={(v) => { setC(v); onColor(v); }} />
      <View style={{ marginTop: space.xl }}><Btn label={L('好了', 'Done')} onPress={close} /></View>
    </View>
  );
}

function DeleteSheet({ g, close, onDeleted }: { g: Group; close: () => void; onDeleted: () => void }) {
  const t = useTheme();
  const { removeGroup, claw } = useStore();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const remove = () => {
    if (busy) return;
    setBusy(true);
    setErr('');
    removeGroup(g.id).then(() => { close(); onDeleted(); }).catch((e) => { setErr(errText(e)); setBusy(false); });
  };
  return (
    <View style={{ gap: space.md }}>
      <T v="callout" color={t.ink2}>{!claw.caps.agentWorkspaces ? L(`它从 Agents 里拿掉，${claw.name} 那边什么都不动。这里的对话记录、日志和卡片留着当历史。`,
        `It's removed from Agents; nothing changes on ${claw.name}'s side. Its chat history, journal and cards here stay as history.`) : L('它在服务器上的 OpenClaw agent 会去掉，工作区和记忆归档到 archive/（不删）。这里的对话记录、日志和卡片留着当历史。', 'Its OpenClaw agent on the server is removed, and its workspace and memory are moved to archive/ (not deleted). Its chats, journal and cards here are kept as history.')}</T>
      {err ? <T v="callout" color={t.bad}>{L(`删不了：${err}`, `Couldn't delete it: ${err}`)}</T> : null}
      <View style={{ flexDirection: 'row', gap: space.sm }}>
        <Btn flex kind="quiet" label={L('留着', 'Keep')} onPress={close} />
        <Btn flex kind="danger" label={busy ? L('正在删…', 'Deleting…') : L('删除', 'Delete')} onPress={remove} />
      </View>
    </View>
  );
}

function EditGroupForm() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { id } = useRoute<any>().params as { id: string };
  const { groups, loading, booting } = useStore();
  const found = groups.find((x) => x.id === id);
  // 删掉之后列表里就没有它了：关页面前的那一下还用最后见到的样子，不闪成空页
  const [last, setLast] = useState(found);
  if (found && found !== last) setLast(found);
  const g = found ?? last;
  if (!g) {
    return (
      <Screen>
        <Header onCancel={() => nav.goBack()} />
        <T v="callout" color={t.ink2} style={{ padding: space.lg }}>{booting || loading.groups ? L('正在读…', 'Loading…') : L('找不到这个 Agent，可能已经删了。', "Can't find this agent. It may have been deleted.")}</T>
      </Screen>
    );
  }
  // 表单在拿到这个 Agent 之后才挂上：输入框的初始值是它当时的样子
  return <EditGroupBody key={g.id} g={g} />;
}

function EditGroupBody({ g }: { g: Group }) {
  const t = useTheme();
  const nav = useNavigation<any>();
  const insets = useSafeAreaInsets();
  const sheet = useSheet();
  const { threadModel, updateGroup, connected } = useStore();
  const [orig] = useState(() => ({
    model: threadModel[g.id] ?? g.modelId ?? threadModel.main,
    color: (COLORS.includes(g.color as AgentColor) ? g.color : 'cyan') as AgentColor,
    icon: iconKey(g.icon),
  }));
  const [name, setName] = useState(g.name);
  const [purpose, setPurpose] = useState(g.purpose ?? '');
  const [icon, setIcon] = useState<GroupIcon | null>(orig.icon);
  const [color, setColor] = useState<AgentColor>(orig.color);
  const [modelId, setModelId] = useState(orig.model);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const chips = useDataChips(g);

  const patch: GroupPatch = {};
  if (name.trim() && name.trim() !== g.name) patch.name = name.trim();
  if (purpose.trim() !== (g.purpose ?? '').trim()) patch.purpose = purpose.trim();
  if (icon && icon !== orig.icon) patch.icon = icon;
  if (color !== orig.color) patch.color = color;
  if (modelId !== orig.model) patch.modelId = modelId;
  const dirty = Object.keys(patch).length > 0;

  const save = () => {
    if (busy) return;
    if (!name.trim()) { setErr(L('名字不能空着', "The name can't be empty")); return; }
    if (!dirty) { nav.goBack(); return; }
    if (!connected) { setErr(L('没连上服务器，存不了', "Not connected to the server, can't save")); return; }
    setBusy(true);
    setErr('');
    updateGroup(g.id, patch).then(() => nav.goBack()).catch((e) => {
      // 405：服务器还是老版本，没有 PATCH /api/groups/{id}
      setErr(httpStatus(e) === 405 ? L('服务器还不支持改 Agent，先更新服务器。', "This server can't edit agents yet. Update the server first.") : errText(e));
      setBusy(false);
    });
  };
  const openPicker = () => sheet.open({
    title: L('换图标和颜色', 'Icon and color'),
    content: (close) => <PickerSheet icon={icon} color={color} onIcon={setIcon} onColor={setColor} close={close} />,
  });
  const confirmDelete = () => sheet.open({
    title: L(`删除「${g.name}」？`, `Delete "${g.name}"?`),
    // 删完回到 Agents 列表（这一页和它的 Agent 页一起退掉）
    content: (close) => <DeleteSheet g={g} close={close} onDeleted={() => nav.navigate('Tabs', { screen: 'Agents' }, { pop: true })} />,
  });

  return (
    <Screen>
      <Header onCancel={() => nav.goBack()} onSave={save} canSave={dirty && !busy && !!name.trim()} busy={busy} />
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingTop: 22, paddingBottom: space.xxl + insets.bottom }} keyboardShouldPersistTaps="handled" automaticallyAdjustKeyboardInsets keyboardDismissMode="interactive">
        <View style={{ alignItems: 'center', gap: 10 }}>
          <Pressable onPress={openPicker} accessibilityRole="button" accessibilityLabel={L('换图标和颜色', 'Change icon and color')}>
            <GroupBadge icon={icon ?? g.icon} color={color} size={76} />
          </Pressable>
          <Pressable onPress={openPicker} hitSlop={8} accessibilityRole="button">
            <T v="callout" color={t.gold} style={{ fontWeight: '600' }}>{L('换图标和颜色', 'Change icon and color')}</T>
          </Pressable>
        </View>

        <SectionLabel>{L('名字', 'Name')}</SectionLabel>
        <TextInput value={name} onChangeText={(v) => { setName(v); setErr(''); }} placeholderTextColor={t.ink3} placeholder={L('比如：睡眠', 'e.g. Sleep')}
          accessibilityLabel={L('Agent 名字', 'Agent name')} style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink }]} />

        <SectionLabel>{L('它负责什么', 'What it does')}</SectionLabel>
        <TextInput value={purpose} onChangeText={setPurpose} multiline placeholderTextColor={t.ink3}
          placeholder={L('一两句话说清职责。', 'Its job in a sentence or two.')} accessibilityLabel={L('Agent 职责', 'Agent purpose')}
          style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink, minHeight: 96, textAlignVertical: 'top' }]} />
        <T v="callout" color={t.ink2} style={styles.help}>{L('改了这里，它自己的说明也会跟着改，下一条消息开始按新的来。', 'Change this and its own instructions change too, starting from the next message.')}</T>

        <SectionLabel>{L('默认模型', 'Default model')}</SectionLabel>
        <ModelField value={modelId} onChange={setModelId} />

        {chips.length ? (
          <>
            <SectionLabel>{L('用到的数据', 'Data it uses')}</SectionLabel>
            <View style={styles.chips}>
              {chips.map((c) => (
                <View key={c.label} style={[styles.chip, { backgroundColor: c.on ? t.goodSoft : t.surface2 }]}>
                  <T v="caption" color={c.on ? t.good : t.ink2} style={{ fontSize: 13, fontWeight: '600' }}>{c.on ? c.label : L(`${c.label}（没接）`, `${c.label} (not connected)`)}</T>
                </View>
              ))}
            </View>
          </>
        ) : null}

        {err ? <T v="callout" color={t.bad} style={{ marginTop: space.lg, paddingHorizontal: space.xs }}>{err}</T> : null}

        <View style={[styles.danger, { backgroundColor: t.surface }]}>
          <Pressable onPress={confirmDelete} hitSlop={8} accessibilityRole="button" style={({ pressed }) => ({ alignSelf: 'flex-start', opacity: pressed ? 0.6 : 1 })}>
            <T v="headline" color={t.bad}>{L('删除这个 Agent', 'Delete this agent')}</T>
          </Pressable>
          <T v="callout" color={t.ink2} style={{ fontSize: 13, lineHeight: 19 }}>{L('它的工作区和记忆移到 archive/，不会删；对话记录、日志和卡片都留着。', 'Its workspace and memory move to archive/ and are not deleted; its chats, journal and cards all stay.')}</T>
        </View>
      </ScrollView>
    </Screen>
  );
}

/** 取消 / 编辑 Agent / 保存。没改东西时「保存」是灰的。 */
function Header({ onCancel, onSave, canSave, busy }: { onCancel: () => void; onSave?: () => void; canSave?: boolean; busy?: boolean }) {
  const t = useTheme();
  return (
    <View style={[styles.header, { borderBottomColor: t.line }]}>
      <Pressable onPress={onCancel} hitSlop={10} accessibilityRole="button" style={styles.side}>
        <T v="headline" color={t.ink2} style={{ fontWeight: '400' }}>{L('取消', 'Cancel')}</T>
      </Pressable>
      <T v="headline" numberOfLines={1} style={{ flex: 1, textAlign: 'center' }}>{L('编辑 Agent', 'Edit agent')}</T>
      <View style={[styles.side, { alignItems: 'flex-end' }]}>
        {onSave ? (
          <Pressable onPress={onSave} disabled={!canSave} hitSlop={10} accessibilityRole="button" accessibilityState={{ disabled: !canSave, busy: !!busy }}>
            <T v="headline" color={canSave ? t.gold : t.ink3} style={{ fontWeight: '700' }}>{busy ? L('保存中…', 'Saving…') : L('保存', 'Save')}</T>
          </Pressable>
        ) : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: 'row', alignItems: 'center', height: 52, paddingHorizontal: space.lg, borderBottomWidth: StyleSheet.hairlineWidth },
  side: { minWidth: 72, justifyContent: 'center' },
  input: { borderRadius: radius.md, paddingHorizontal: space.lg, paddingVertical: 13 },
  help: { fontSize: 13, lineHeight: 19, marginTop: space.sm, paddingHorizontal: space.xs },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: space.sm, paddingHorizontal: 2 },
  chip: { height: 28, borderRadius: 14, paddingHorizontal: 11, justifyContent: 'center' },
  danger: { borderRadius: 16, paddingVertical: 14, paddingHorizontal: space.lg, gap: 6, marginTop: space.xl },
});
