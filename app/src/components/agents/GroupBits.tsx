// 群聊（主线第三步，设计稿第 44 张）用到的小零件：成员的头像（赛博 = 半身像 + 细金环；功能 Agent = 底色 + 图标；主持的助手 = 环与点的镜头）、
// 群的组合头像、名字后面那一行身份（「Victor · 赛博 · 早期投资人」）、主持人的小结卡（金色细边，下面三个动作）、「仅你可见」的卡那一块。
import React from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import type { ButlerAvatar as ButlerAvatarSpec } from '../../api/butler';
import type { CyberAvatarSpec } from '../../api/cybers';
import type { FriendGroup } from '../../api/friendgroups';
import type { GroupMember, GroupMsg, SummaryActions } from '../../api/groups';
import type { AgentColor } from '../../data/types';
import { L } from '../../i18n';
import { useStore } from '../../store';
import { radius, space, useTheme } from '../../theme';
import { Check } from '../icons';
import { LensAvatar } from '../LensAvatar';
import { AgentAvatar } from '../MeetingBits';
import { T } from '../ui';
import { ButlerAvatar, ButlerFace, CyberAvatar, PersonFace } from './CyberAvatar';

/** 头像要的几样：成员（赛博 / 功能 Agent），也可以是管家（butler：环与点或捏的）、好友（friend：首字母，没有环）。 */
export type FaceOf = { kind: string; avatar?: unknown; icon?: string | null; color?: AgentColor | null; name?: string };

/** 一位成员（或主持人）的头像。who = main：助手的镜头。 */
export function MemberFace({ m, size = 30, main }: { m?: FaceOf | null; size?: number; main?: boolean }) {
  const { avatar } = useStore();
  if (main) return <LensAvatar size={size} config={avatar} />;
  if (m?.kind === 'butler') return <ButlerAvatar avatar={m.avatar as ButlerAvatarSpec | null} size={size} />;
  if (m?.kind === 'friend') return <PersonFace name={m.name ?? ''} size={size} />;
  if (m?.kind === 'cyber') return <CyberAvatar avatar={m.avatar as CyberAvatarSpec | null} size={size} />;
  return <AgentAvatar icon={m?.icon} color={m?.color} size={size} />;
}

/** 群的组合头像：一个圆里最多四位（2 × 2），赛博画半身像（不带环，太小）、功能 Agent 画图标、好友画首字母。 */
export function GroupFaces({ members, size = 36 }: { members: (FaceOf & { id: string })[]; size?: number }) {
  const t = useTheme();
  const mini = Math.round(size * 0.42);
  const pad = Math.round(size * 0.1);
  const step = size - pad * 2 - mini;
  if (members.length === 1) {  // 只有一位：放在正中，大一些
    const m = members[0];
    const one = Math.round(size * 0.62);
    return (
      <View style={{ width: size, height: size, borderRadius: size / 2, backgroundColor: t.surface2, alignItems: 'center', justifyContent: 'center' }}
        accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
        {m.kind === 'cyber' ? <CyberAvatar avatar={m.avatar as CyberAvatarSpec | null} size={one} ring={false} />
          : m.kind === 'friend' ? <PersonFace name={m.name ?? ''} size={one} />
            : m.kind === 'butler' ? <ButlerFace size={one} /> : <AgentAvatar icon={m.icon} color={m.color} size={one} />}
      </View>
    );
  }
  return (
    <View style={{ width: size, height: size, borderRadius: size / 2, backgroundColor: t.surface2 }} accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
      {members.slice(0, 4).map((m, i) => (
        <View key={m.id} style={{ position: 'absolute', left: pad + (i % 2) * step, top: pad + Math.floor(i / 2) * step }}>
          {m.kind === 'cyber' ? <CyberAvatar avatar={m.avatar as CyberAvatarSpec | null} size={mini} ring={false} />
            : m.kind === 'friend' ? <PersonFace name={m.name ?? ''} size={mini} />
              : m.kind === 'butler' ? <ButlerFace size={mini} /> : <AgentAvatar icon={m.icon} color={m.color} size={mini} />}
        </View>
      ))}
    </View>
  );
}

/** 好友群页头那一行：人在前（你第一个），再是好友的管家，你的管家最后（「你、Mia、Mia 的管家、你的管家」）。 */
export function friendGroupLine(g: FriendGroup): string {
  const people = [...g.members].sort((a, b) => Number(b.me) - Number(a.me)).map((m) => (m.me ? L('你', 'You') : m.name));
  const theirs = g.members.filter((m) => m.butler && !m.me).map((m) => m.butlerName ?? L(`${m.name} 的管家`, `${m.name}'s butler`));
  const mine = g.members.some((m) => m.me && m.butler) ? [L('你的管家', 'your butler')] : [];
  return [...people, ...theirs, ...mine].join(L('、', ', '));
}

/** 好友群的头像：好友的首字母叠在一个圆里（管家不进头像）。 */
export function FriendGroupFace({ g, size = 36 }: { g: FriendGroup; size?: number }) {
  return <GroupFaces members={g.members.filter((m) => !m.me).map((m) => ({ id: m.kid, kind: 'friend', name: m.name }))} size={size} />;
}

/** 名字后面的身份：「赛博 · 早期投资人」/「功能 Agent」。 */
export function roleLine(m?: Pick<GroupMember, 'kind' | 'tag'> | null): string {
  if (!m) return '';
  if (m.kind === 'cyber') return m.tag ? L(`赛博 · ${m.tag}`, `Cyber · ${m.tag}`) : L('赛博', 'Cyber');
  return L('功能 Agent', 'Agent');
}

/** 页头副标题：「你、Victor、苏格拉底 · Mousse 主持」。 */
export function groupSubtitle(members: GroupMember[], host: { name: string } | null): string {
  const names = [L('你', 'You'), ...members.filter((m) => !m.gone).map((m) => m.name)].join(L('、', ', '));
  return host ? L(`${names} · ${host.name} 主持`, `${names} · hosted by ${host.name}`) : L(`${names} · 不设主持`, `${names} · no host`);
}

/** 主持人的小结卡（设计稿：金色细边「Mousse · 主持 · 第 1 轮小结」，卡下三个按钮「写回想法 / 发起会议 / 做成播客」）。 */
export function SummaryCard({ m, busy, onWriteback, onMeeting, onPodcast, onMemory, sourceKind }: {
  m: GroupMsg; busy: string | null; sourceKind?: string | null;
  onWriteback: () => void; onMeeting: () => void; onPodcast: () => void; onMemory: () => void;
}) {
  const t = useTheme();
  const s = m.summary;
  const acts: SummaryActions = m.actions ?? {};
  if (!s) return null;
  const head = L(`${m.host?.name ?? m.name} · 主持 · 第 ${m.round ?? 1} 轮小结`, `${m.host?.name ?? m.name} · host · summary of round ${m.round ?? 1}`);
  const wrote = acts.writeback && !acts.writeback.error;
  const btn = (key: string, label: string, done: boolean, onPress: () => void) => (
    <Pressable key={key} onPress={onPress} disabled={!!busy} accessibilityRole="button" accessibilityLabel={label}
      style={({ pressed }) => [styles.btn, { backgroundColor: done ? t.surface2 : t.goldSoft, opacity: busy && busy !== key ? 0.5 : pressed ? 0.8 : 1 }]}>
      {done ? <Check size={13} color={t.good} /> : null}
      <T v="caption" color={done ? t.ink2 : t.gold} style={{ fontSize: 13, fontWeight: '700' }}>{busy === key ? L('正在处理…', 'Working…') : label}</T>
    </Pressable>
  );
  return (
    <View style={{ flex: 1, minWidth: 0, gap: 3 }}>
      <T v="caption" color={t.gold} style={{ fontWeight: '600' }}>{head}</T>
      <View style={[styles.summary, { backgroundColor: t.surface, borderColor: t.goldFill }]}>
        {s.line ? <T v="callout" style={{ fontSize: 14 }}>{s.line}</T> : null}
        {s.decided.length ? (
          <View style={{ gap: 3 }}>
            <T v="caption" color={t.ink3}>{L('定了', 'Decided')}</T>
            {s.decided.map((d, i) => <T key={i} v="callout" style={{ fontSize: 14 }}>{`· ${d.text}${d.agreed ? L('（你同意了）', ' (you agreed)') : ''}`}</T>)}
          </View>
        ) : null}
        {s.disputed.length ? (
          <View style={{ gap: 3 }}>
            <T v="caption" color={t.ink3}>{L('还在争', 'Still disputed')}</T>
            {s.disputed.map((d, i) => (
              <View key={i} style={{ gap: 1 }}>
                <T v="callout" style={{ fontSize: 14 }}>{`· ${d.point}`}</T>
                {d.sides.map((x, j) => <T key={j} v="caption" color={t.ink2} style={{ paddingLeft: 10 }}>{`${x.name}${L('：', ': ')}${x.says}`}</T>)}
              </View>
            ))}
          </View>
        ) : null}
        {s.notes.length ? (
          <View style={{ gap: 3 }}>
            <T v="caption" color={t.ink3}>{L('各自记下的', 'What each kept')}</T>
            {s.notes.map((n, i) => <T key={i} v="caption" color={t.ink2}>{`${n.name}${L('：', ': ')}${n.text}`}</T>)}
          </View>
        ) : null}
        <Pressable onPress={onMemory} accessibilityRole="button" hitSlop={6}>
          <T v="caption" color={t.ink3}>{L('已写进群记忆 · 查看 ›', "In the group's memory · View ›")}</T>
        </Pressable>
      </View>
      <View style={styles.actions}>
        {btn('writeback', wrote ? L('已写回', 'Written back') : sourceKind === 'podcast' ? L('写回这一期', 'Write back') : L('写回想法', 'Write back'), !!wrote, onWriteback)}
        {btn('meeting', acts.meeting ? L('查看会议', 'Open meeting') : L('发起会议', 'Start a meeting'), !!acts.meeting, onMeeting)}
        {btn('podcast', acts.podcast ? L('查看播客', 'Open episode') : L('做成播客', 'Make a podcast'), !!acts.podcast, onPodcast)}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  summary: { borderWidth: 1, borderRadius: 16, borderTopLeftRadius: 4, padding: space.md, gap: 10 },
  actions: { flexDirection: 'row', flexWrap: 'wrap', gap: space.sm, marginTop: 4 },
  btn: { flexDirection: 'row', alignItems: 'center', gap: 4, height: 32, paddingHorizontal: 14, borderRadius: radius.lg },
});
