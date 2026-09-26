// 事情是谁提的 / 消息是谁发的：主对话是助手自己（头像），Agent 用它的图标和颜色，独立空间用对话图标。
import React from 'react';
import { View } from 'react-native';
import { agentName } from '../brand';
import { useStore } from '../store';
import { agentTint, useTheme } from '../theme';
import { GroupBadge } from './GroupIcon';
import { LayoutGrid, MessagesSquare } from './icons';
import { LensAvatar } from './LensAvatar';
import { Pill } from './ui';

export function SourceBadge({ source, size }: { source?: string; size: number }) {
  const t = useTheme();
  const { groups, sideChats, avatar } = useStore();
  if (!source || source === 'main') return <LensAvatar size={size} config={avatar} />;
  const g = groups.find((x) => x.id === source);
  if (g) return <GroupBadge icon={g.icon} color={g.color} size={size} />;
  const Icon = sideChats.some((c) => c.id === source) ? MessagesSquare : LayoutGrid;
  return (
    <View style={{ width: size, height: size, borderRadius: size * 0.3, backgroundColor: t.cyanSoft, alignItems: 'center', justifyContent: 'center' }}>
      <Icon size={size * 0.5} color={t.cyan} />
    </View>
  );
}

/** 线程 / 来源 id → 显示的名字：主对话是助手的名字，其余是 Agent 名或独立空间的标题。 */
export function useSourceName() {
  const { groups, sideChats } = useStore();
  return (source?: string | null) => (!source || source === 'main'
    ? agentName()
    : groups.find((g) => g.id === source)?.name ?? sideChats.find((c) => c.id === source)?.title ?? source);
}

/** 来源的颜色：主对话 = 金（助手自己），Agent = 它自己的颜色，独立空间和认不出的 = 青。 */
export function useSourceTint() {
  const t = useTheme();
  const { groups } = useStore();
  return (source?: string | null): { soft: string; fg: string } => {
    if (!source || source === 'main') return { soft: t.goldSoft, fg: t.gold };
    const g = groups.find((x) => x.id === source);
    return g ? agentTint(t, g.color) : { soft: t.cyanSoft, fg: t.cyan };
  };
}

/** 带颜色的来源名小标签（「今天」的建议卡和日志、历史搜索、我 → 日志）。label 不给就用来源的名字。 */
export function SourcePill({ source, label }: { source?: string | null; label?: string }) {
  const name = useSourceName();
  const tint = useSourceTint()(source);
  return <Pill label={label ?? name(source)} colors={[tint.soft, tint.fg]} />;
}
