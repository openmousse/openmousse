// 新建群聊（主线第三步第二段，设计稿第 44 张）：路由 NewRoom（modal，页内自己挂 SheetProvider；params members? = 先勾上的，比如赛博资料页的「拉进群聊」）。
// 成员分三块：你这边（你，和主持的助手：开着 = 它主持、写小结；关了 = 不设主持）/ 赛博 / 功能 Agent。议题可选：填了，建好就先讨论一轮（每位各说一次）。
// 管健康、饮食、睡眠的功能 Agent 不和赛博同群（赛博用别家的模型，读得到群里的每一句）：勾了赛博，它们灰掉并写明为什么。服务器 groupchat.py 也照样拦。
// 好友（第三段，开着好友群才有这一块）：勾上好友 = 建好友群（friendgroup.py）：你的赛博和功能 Agent 变灰，你这边换成管家出面；
// 对方版本旧的灰掉，写明为什么只能一对一。
import React, { useEffect, useState } from 'react';
import { Pressable, StyleSheet, Switch, TextInput, View } from 'react-native';
import { useNavigation, useRoute } from '@react-navigation/native';
import { agentName } from '../../brand';
import { friendGroupsApi, useFriendGroupsOn, type FGCandidate } from '../../api/friendgroups';
import { groupsApi, type Candidates } from '../../api/groups';
import { useButlerAvatar } from '../../api/butler';
import { ButlerAvatar, PersonFace } from '../../components/agents/CyberAvatar';
import { MemberFace } from '../../components/agents/GroupBits';
import { Check } from '../../components/icons';
import { KeyboardScroll } from '../../components/keyboard';
import { SheetProvider } from '../../components/Sheet';
import { Screen, T, showError } from '../../components/ui';
import { L } from '../../i18n';
import { useStore } from '../../store';
import { radius, space, type, useTheme } from '../../theme';

export function NewRoomScreen() {
  return <SheetProvider><NewRoomForm /></SheetProvider>;
}

function NewRoomForm() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { reload } = useStore();
  const params = (useRoute<any>().params ?? {}) as { members?: string[]; topic?: string };
  const [c, setC] = useState<Candidates | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<string[]>(params.members ?? []);
  const [topic, setTopic] = useState(params.topic ?? '');
  const [hosted, setHosted] = useState(true);
  const [starting, setStarting] = useState(false);
  const fgOn = useFriendGroupsOn();
  const butlerAvatar = useButlerAvatar();
  const [fc, setFc] = useState<{ friends: FGCandidate[]; note: string } | null>(null);
  const [friends, setFriends] = useState<string[]>([]);
  useEffect(() => {
    groupsApi.candidates().then(setC).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);
  useEffect(() => {
    if (!fgOn) return;
    let live = true;
    friendGroupsApi.candidates().then((x) => { if (live) setFc(x); }).catch(() => {});
    return () => { live = false; };
  }, [fgOn]);
  const withFriends = friends.length > 0;
  const toggleFriend = (fid: string) => setFriends((cur) => (cur.includes(fid) ? cur.filter((x) => x !== fid) : [...cur, fid]));
  const cyberIds = new Set((c?.cybers ?? []).map((x) => x.id));
  const withCyber = picked.some((id) => cyberIds.has(id));
  const healthIds = new Set((c?.agents ?? []).filter((a) => a.health).map((a) => a.id));
  const withHealth = picked.some((id) => healthIds.has(id));
  const toggle = (id: string) => setPicked((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));
  const max = c?.max ?? 8;
  const FRIENDS_MAX = 8;  // 连你在内最多 9 人（friendgroup.MEMBERS_MAX）
  const can = withFriends ? friends.length <= FRIENDS_MAX && !starting : !!c && picked.length >= (c?.min ?? 1) && picked.length <= max && !starting;
  const start = async () => {
    if (!can) return;
    setStarting(true);
    try {
      if (withFriends) {  // 好友群：群名用议题，没填就用大家的名字
        const names = [L('你', 'You'), ...(fc?.friends ?? []).filter((f) => friends.includes(f.id)).map((f) => f.name)];
        const g = await friendGroupsApi.create((topic.trim() || names.join(L('、', ', '))).slice(0, 40), friends);
        nav.replace('FriendGroup', { id: g.id });
        return;
      }
      const g = await groupsApi.create({ members: picked, topic: topic.trim() || undefined, host: hosted ? 'main' : '' });
      reload('chats').catch(() => {});
      nav.replace('GroupChat', { id: g.id });
    } catch (e) {
      showError(L('无法新建群聊', "Couldn't start the group chat"), e);
      setStarting(false);
    }
  };
  const row = (id: string, face: React.ReactNode, name: string, sub: string, i: number, n: number, off?: string) => {
    const on = picked.includes(id) && !withFriends;
    const blocked = withFriends || (!!off && !on);
    return (
      <Pressable key={id} onPress={() => toggle(id)} disabled={blocked || (!on && picked.length >= max)} accessibilityRole="checkbox"
        accessibilityState={{ checked: on, disabled: blocked }} accessibilityLabel={name} accessibilityHint={off}
        style={({ pressed }) => [styles.pickRow, { opacity: blocked ? 0.45 : pressed ? 0.7 : 1 }]}>
        {face}
        <View style={[styles.pickBody, i < n - 1 && { borderBottomColor: t.surface2, borderBottomWidth: StyleSheet.hairlineWidth }]}>
          <View style={{ flex: 1, minWidth: 0, gap: 1 }}>
            <T v="body" style={{ fontWeight: '600' }} numberOfLines={1}>{name}</T>
            <T v="caption" color={t.ink3} numberOfLines={1} style={{ fontWeight: '400' }}>{withFriends ? L('有好友在：换成管家出面', 'Friends are in: your butler speaks instead') : blocked ? off : sub}</T>
          </View>
          {on ? <View style={[styles.check, { backgroundColor: t.cyan }]}><Check size={15} color={t.bg} strokeWidth={3} /></View>
            : <View style={[styles.uncheck, { borderColor: t.ink3 }]} />}
        </View>
      </Pressable>
    );
  };
  const cy = c?.cybers ?? [];
  const ag = c?.agents ?? [];
  return (
    <Screen>
      <View style={styles.modalHead}>
        <Pressable onPress={() => nav.goBack()} hitSlop={8} accessibilityRole="button" style={{ width: 64 }}>
          <T v="body" color={t.ink2}>{L('取消', 'Cancel')}</T>
        </Pressable>
        <T v="headline" style={{ fontSize: 17, fontWeight: '700' }}>{L('新建群聊', 'New group chat')}</T>
        <Pressable onPress={start} disabled={!can} hitSlop={8} accessibilityRole="button" accessibilityState={{ disabled: !can }} style={{ width: 64, alignItems: 'flex-end' }}>
          <T v="body" color={can ? t.gold : t.ink3} style={{ fontWeight: '700' }}>{starting ? L('正在新建', 'Starting') : L('开始', 'Start')}</T>
        </Pressable>
      </View>
      <KeyboardScroll contentContainerStyle={{ paddingHorizontal: space.lg, paddingBottom: space.xxl }} keyboardShouldPersistTaps="handled">
        <T v="caption" color={t.ink3} style={styles.formLabel}>{L('议题 · 可选', 'Topic · optional')}</T>
        <TextInput value={topic} onChangeText={setTopic} multiline maxLength={400} placeholder={L('例如：同步库要不要开源', 'e.g. Should the sync library be open source?')}
          placeholderTextColor={t.ink3} accessibilityLabel={L('议题', 'Topic')} style={[type.body, styles.topicInput, { backgroundColor: t.surface, color: t.ink }]} />
        <T v="caption" color={t.ink3} style={styles.hint}>{withFriends ? L('有好友在：议题就是群名；不填就用大家的名字。', 'With friends here, the topic is the group name; leave it empty to use everyone’s names.')
          : L('填了议题，成员先各说一轮；不填就等你开口。', 'With a topic, each member speaks once first; without one, they wait for you.')}</T>

        <T v="caption" color={t.ink3} style={[styles.formLabel, { marginTop: space.lg }]}>{L('你这边', 'Your side')}</T>
        <View style={[styles.group, { backgroundColor: t.surface }]}>
          <View style={styles.pickRow}>
            <View style={[styles.you, { backgroundColor: t.surface2 }]}><T v="caption" style={{ fontWeight: '700' }}>{L('你', 'You')}</T></View>
            <View style={[styles.pickBody, { borderBottomColor: t.surface2, borderBottomWidth: StyleSheet.hairlineWidth }]}>
              <T v="body" style={{ flex: 1, fontWeight: '600' }}>{L('你', 'You')}</T>
            </View>
          </View>
          {withFriends ? (
            <View style={styles.pickRow}>
              <ButlerAvatar avatar={butlerAvatar} size={34} />
              <View style={styles.pickBody}>
                <View style={{ flex: 1, minWidth: 0, gap: 1 }}>
                  <T v="body" style={{ fontWeight: '600' }} numberOfLines={1}>{L('你的管家 · 主持', 'Your butler · hosts')}</T>
                  <T v="caption" color={t.ink3} numberOfLines={2} style={{ fontWeight: '400' }}>{L('替你出面；需要时去问你的赛博', 'Speaks for you; asks your Cybers when it needs to')}</T>
                </View>
              </View>
            </View>
          ) : <View style={styles.pickRow}>
            <MemberFace main size={34} />
            <View style={styles.pickBody}>
              <View style={{ flex: 1, minWidth: 0, gap: 1 }}>
                <T v="body" style={{ fontWeight: '600' }} numberOfLines={1}>{L(`${c?.host.name ?? agentName()} · 主持`, `${c?.host.name ?? agentName()} · host`)}</T>
                <T v="caption" color={t.ink3} numberOfLines={2} style={{ fontWeight: '400' }}>
                  {hosted ? L('每轮写小结，记进群记忆', 'Writes a summary each round, kept in the group’s memory') : L('不设主持：没有小结', 'No host: no summaries')}
                </T>
              </View>
              <Switch value={hosted} onValueChange={setHosted} accessibilityLabel={L('主持', 'Host')} trackColor={{ true: t.cyan, false: t.surface2 }} />
            </View>
          </View>}
        </View>

        <T v="caption" color={t.ink3} style={[styles.formLabel, { marginTop: space.lg }]}>{L(`赛博${cy.length ? ` · ${cy.length}` : ''}`, `Cybers${cy.length ? ` · ${cy.length}` : ''}`)}</T>
        <View style={[styles.group, { backgroundColor: t.surface }]}>
          {cy.length ? cy.map((x, i) => row(x.id, <MemberFace m={x} size={34} />, x.name, x.tag || x.about || L('赛博', 'Cyber'), i, cy.length,
            withHealth ? L('群里有健康类 Agent：赛博不进这个群', 'A health Agent is in this group: Cybers stay out') : undefined))
            : <T v="callout" color={t.ink3} style={{ padding: space.lg }}>{c ? L('还没有赛博。', 'No Cybers yet.') : error ? L(`无法加载：${error}`, `Couldn't load: ${error}`) : L('正在加载…', 'Loading…')}</T>}
        </View>

        <T v="caption" color={t.ink3} style={[styles.formLabel, { marginTop: space.lg }]}>{L(`功能 Agent${ag.length ? ` · ${ag.length}` : ''}`, `Agents${ag.length ? ` · ${ag.length}` : ''}`)}</T>
        <View style={[styles.group, { backgroundColor: t.surface }]}>
          {ag.length ? ag.map((x, i) => row(x.id, <MemberFace m={x} size={34} />, x.name, x.about || L('功能 Agent', 'Agent'), i, ag.length,
            x.health && withCyber ? L('管健康、饮食或睡眠：不和赛博同群', 'Looks after health, diet or sleep: not in a group with Cybers') : undefined))
            : <T v="callout" color={t.ink3} style={{ padding: space.lg }}>{c ? L('还没有功能 Agent。', 'No Agents yet.') : L('正在加载…', 'Loading…')}</T>}
        </View>

        {fgOn && fc ? (
          <>
            <T v="caption" color={t.ink3} style={[styles.formLabel, { marginTop: space.lg }]}>{L(`好友${fc.friends.length ? ` · ${fc.friends.length}` : ''}`, `Friends${fc.friends.length ? ` · ${fc.friends.length}` : ''}`)}</T>
            <View style={[styles.group, { backgroundColor: t.surface }]}>
              {fc.friends.length ? fc.friends.map((f, i) => {
                const on = friends.includes(f.id);
                const off = !f.can;
                return (
                  <Pressable key={f.id} onPress={() => toggleFriend(f.id)} disabled={off || (!on && friends.length >= FRIENDS_MAX)} accessibilityRole="checkbox"
                    accessibilityState={{ checked: on, disabled: off }} accessibilityLabel={f.name} accessibilityHint={f.why ?? undefined}
                    style={({ pressed }) => [styles.pickRow, { opacity: off ? 0.45 : pressed ? 0.7 : 1 }]}>
                    <PersonFace name={f.name} size={34} />
                    <View style={[styles.pickBody, i < fc.friends.length - 1 && { borderBottomColor: t.surface2, borderBottomWidth: StyleSheet.hairlineWidth }]}>
                      <View style={{ flex: 1, minWidth: 0, gap: 1 }}>
                        <T v="body" style={{ fontWeight: '600' }} numberOfLines={1}>{f.name}</T>
                        <T v="caption" color={t.ink3} numberOfLines={2} style={{ fontWeight: '400' }}>{f.why ?? L('好友 · 管家由对方自己决定加不加入', "Friend · they decide whether their butler joins")}</T>
                      </View>
                      {on ? <View style={[styles.check, { backgroundColor: t.cyan }]}><Check size={15} color={t.bg} strokeWidth={3} /></View>
                        : <View style={[styles.uncheck, { borderColor: t.ink3 }]} />}
                    </View>
                  </Pressable>
                );
              }) : <T v="callout" color={t.ink3} style={{ padding: space.lg }}>{L('还没有好友。', 'No friends yet.')}</T>}
            </View>
          </>
        ) : null}

        <T v="caption" color={t.ink3} style={[styles.hint, { paddingTop: space.lg }]}>
          {withFriends ? fc?.note ?? '' : L(`只有你和你的 Agent：${c?.host.name ?? agentName()} 主持，小结写回议题的来处。拉进好友，就换成管家出面。`,
            `Only you and your Agents: ${c?.host.name ?? agentName()} hosts, and summaries go back where the topic came from. Bring in a friend and the butler takes over.`)}
        </T>
        {picked.length && !withFriends ? (
          <T v="caption" color={t.ink3} style={[styles.hint, { paddingTop: 4 }]}>
            {L(`已选 ${picked.length} 位（最多 ${max} 位）。@所有 Agent 一轮 = 每位说一次${hosted ? ' + 1 次小结' : ''}。`, `${picked.length} picked (up to ${max}). One round with @All Agents = one reply each${hosted ? ' + 1 summary' : ''}.`)}
          </T>
        ) : null}
      </KeyboardScroll>
    </Screen>
  );
}

const styles = StyleSheet.create({
  modalHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: space.lg, paddingTop: space.lg, paddingBottom: space.md },
  formLabel: { fontSize: 13, fontWeight: '600', paddingLeft: 4, marginBottom: space.sm },
  topicInput: { minHeight: 64, borderRadius: 14, paddingHorizontal: 14, paddingTop: 12, paddingBottom: 12, fontSize: 15, lineHeight: 21, textAlignVertical: 'top' },
  hint: { fontSize: 13, lineHeight: 19, fontWeight: '400', paddingHorizontal: space.xs, paddingTop: space.sm },
  group: { borderRadius: radius.lg - 2, overflow: 'hidden' },
  pickRow: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingLeft: space.md },
  pickBody: { flex: 1, minWidth: 0, minHeight: 58, flexDirection: 'row', alignItems: 'center', gap: 10, paddingRight: 14, paddingVertical: 8 },
  check: { width: 24, height: 24, borderRadius: 12, alignItems: 'center', justifyContent: 'center' },
  uncheck: { width: 22, height: 22, borderRadius: 11, borderWidth: 1.5 },
  you: { width: 34, height: 34, borderRadius: 17, alignItems: 'center', justifyContent: 'center' },
});
