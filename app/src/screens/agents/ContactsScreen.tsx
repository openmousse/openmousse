// 通讯录（主线第三步，设计稿第 40 张）：对话页 Agent 栏最前面那一格进来。顶上是管家（第一步就是现在的名片 Agent），
// 下面三段：赛博（最近聊过的在前，名字后面写标签和模型）、功能 Agent（一句职责）、好友（每位下面挂「X 的管家」）。
// 搜索和筛选在本机按服务器给的 keywords 筛，不用每个字都问服务器。数据：GET /api/contacts（服务器 contacts.py）。
// 点一位赛博进它的资料页（证件：发消息、改法、装载都在那里），点功能 Agent 进它的资料页，点好友进聊天，点好友的管家看两边 Agent 的往来。
import React, { useCallback, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, TextInput, View } from 'react-native';
import { useFocusEffect, useNavigation } from '@react-navigation/native';
import { ButlerAvatar, ButlerFace, CyberAvatar, PersonFace } from '../../components/agents/CyberAvatar';
import { AgentFace, MenuItem } from '../../components/ChatParts';
import { ChevronRight, CirclePlus, Plus, Search, UserRoundPlus, X } from '../../components/icons';
import { useSheet } from '../../components/Sheet';
import { Card, NavHeader, PullRefresh, Screen, SectionLabel, T } from '../../components/ui';
import { cybersApi, type ContactFilter, type Contacts } from '../../api/cybers';
import { L } from '../../i18n';
import { radius, space, type, useTheme } from '../../theme';

function Row({ face, title, sub, onPress, indent }: { face: React.ReactNode; title: string; sub?: string; onPress: () => void; indent?: boolean }) {
  const t = useTheme();
  return (
    <Pressable onPress={onPress} accessibilityRole="button" style={({ pressed }) => [styles.row, indent && { paddingLeft: 44 + space.md }, { opacity: pressed ? 0.6 : 1 }]}>
      {face}
      <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
        <T v="headline" numberOfLines={1} style={{ fontSize: indent ? 14 : 16 }}>{title}</T>
        {sub ? <T v="callout" color={t.ink2} numberOfLines={1} style={{ fontSize: 13 }}>{sub}</T> : null}
      </View>
      <ChevronRight size={16} color={t.ink3} />
    </Pressable>
  );
}

export function ContactsScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const sheet = useSheet();
  const [data, setData] = useState<Contacts | null>(null);
  const [err, setErr] = useState('');
  const [q, setQ] = useState('');
  const [filter, setFilter] = useState<ContactFilter>('all');
  const load = useCallback(() => cybersApi.contacts().then((d) => { setData(d); setErr(''); })
    .catch((e) => setErr(e instanceof Error ? e.message : String(e))), []);
  useFocusEffect(useCallback(() => { load(); }, [load]));

  const words = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const hit = (keywords: string) => words.every((w) => keywords.includes(w));
  const sections = (data?.sections ?? []).filter((s) => filter === 'all' || s.kind === filter);
  const butler = data?.butler && filter === 'all' && hit(data.butler.keywords) ? data.butler : null;

  const newMenu = () => sheet.open({ title: L('新建', 'New'), content: (close) => (
    <View>
      <MenuItem icon={UserRoundPlus} title={L('新建赛博', 'New Cyber')} desc={L('一句话开始，陪你想的', 'Start from one sentence; it thinks with you')} chevron
        onPress={() => { close(); nav.navigate('NewCyber'); }} />
      <MenuItem icon={CirclePlus} title={L('新建功能 Agent', 'New Agent')} desc={L('一句话开始，做事的', 'Start from one sentence; it gets things done')} chevron
        onPress={() => { close(); nav.navigate('NewGroup'); }} />
    </View>
  ) });

  const empty = data && !butler && sections.every((s) => !s.items.some((x) => hit(x.keywords)));
  return (
    <Screen>
      <NavHeader title={L('通讯录', 'Contacts')} onBack={() => nav.goBack()}
        right={<Pressable onPress={newMenu} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('新建', 'New')} style={[styles.newBtn, { backgroundColor: t.goldFill }]}>
          <Plus size={18} color={t.onGold} />
        </Pressable>} />
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl }} keyboardShouldPersistTaps="handled"
        refreshControl={<PullRefresh onRefresh={load} />}>
        <View style={[styles.search, { backgroundColor: t.surface2 }]}>
          <Search size={17} color={t.ink3} />
          <TextInput value={q} onChangeText={setQ} placeholder={L('搜索赛博、Agent 和好友', 'Search Cybers, Agents and friends')} placeholderTextColor={t.ink3}
            autoCorrect={false} returnKeyType="search" accessibilityLabel={L('搜索', 'Search')}
            style={[type.body, { flex: 1, color: t.ink, paddingVertical: 0, outlineWidth: 0 }]} />
          {q ? <Pressable onPress={() => setQ('')} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('清空', 'Clear')}><X size={16} color={t.ink3} /></Pressable> : null}
        </View>
        <View style={styles.chips}>
          {(data?.filters ?? []).map((f) => {
            const on = f.id === filter;
            return (
              <Pressable key={f.id} onPress={() => setFilter(f.id)} accessibilityRole="tab" accessibilityState={{ selected: on }}
                style={[styles.chip, { backgroundColor: on ? t.goldSoft : t.surface, borderColor: on ? t.goldFill : t.line }]}>
                <T v="caption" color={on ? t.gold : t.ink2} style={{ fontSize: 13 }}>{f.label}</T>
              </Pressable>
            );
          })}
        </View>
        {err ? <Card style={{ marginTop: space.md }}><T v="callout" color={t.bad}>{L(`无法加载：${err}`, `Couldn't load: ${err}`)}</T></Card> : null}
        {!data && !err ? <T v="callout" color={t.ink3} style={{ marginTop: space.lg }}>{L('正在加载…', 'Loading…')}</T> : null}

        {butler ? (
          <Card style={{ marginTop: space.md, paddingVertical: space.sm }}>
            <Row face={<ButlerAvatar avatar={butler.avatar} size={44} />} title={butler.name} sub={butler.line} onPress={() => nav.navigate(butler.open)} />
          </Card>
        ) : null}

        {sections.map((s) => {
          const items = (s.items as { keywords: string }[]).filter((x) => hit(x.keywords));
          if (!items.length) return null;
          return (
            <View key={s.kind}>
              <SectionLabel>{`${s.title} · ${items.length}`}</SectionLabel>
              <Card style={{ paddingVertical: space.sm }}>
                {s.kind === 'cyber' ? s.items.filter((c) => hit(c.keywords)).map((c) => (
                  <Row key={c.id} face={<CyberAvatar avatar={c.avatar} size={44} />} title={c.name}
                    sub={[c.tag, L(`赛博 · ${c.modelName}`, `Cyber · ${c.modelName}`)].filter(Boolean).join(' · ')}
                    onPress={() => nav.navigate('CyberProfile', { id: c.id })} />
                )) : s.kind === 'agent' ? s.items.filter((a) => hit(a.keywords)).map((a) => (
                  <Row key={a.id} face={<AgentFace icon={a.icon} color={a.color} size={44} />} title={a.name} sub={a.purpose || L('功能 Agent', 'Agent')}
                    onPress={() => nav.navigate('Info', { kind: 'agent', id: a.id })} />
                )) : s.items.filter((f) => hit(f.keywords)).map((f) => (
                  <View key={f.id}>
                    <Row face={<PersonFace name={f.name} />} title={f.name} sub={f.line || undefined} onPress={() => nav.navigate('FriendChat', { id: f.id })} />
                    {f.butler ? (
                      <Row indent face={<ButlerFace size={28} tone="grey" />} title={f.butler.name} sub={f.butler.line}
                        onPress={() => nav.navigate('FriendAgents', { id: f.id })} />
                    ) : null}
                  </View>
                ))}
              </Card>
            </View>
          );
        })}
        {empty ? <T v="callout" color={t.ink3} style={{ marginTop: space.lg }}>{words.length ? L('没有找到。', 'Nothing found.') : L('还没有联系人。', 'No contacts yet.')}</T> : null}
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  newBtn: { width: 32, height: 32, borderRadius: 16, alignItems: 'center', justifyContent: 'center', marginRight: space.xs },
  search: { flexDirection: 'row', alignItems: 'center', gap: space.sm, borderRadius: radius.md, paddingHorizontal: space.md, height: 40 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: space.sm, marginTop: space.md },
  chip: { height: 32, paddingHorizontal: 14, borderRadius: 16, borderWidth: 1, alignItems: 'center', justifyContent: 'center' },
  row: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: space.sm },
});
