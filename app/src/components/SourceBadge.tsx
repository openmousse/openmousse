// 事情是谁提的 / 消息是谁发的：主对话是助手自己（头像），Agent 用它的图标，独立空间用对话图标。
import React from 'react';
import { View } from 'react-native';
import { agentName } from '../brand';
import { useStore } from '../store';
import { useTheme } from '../theme';
import { GroupBadge } from './GroupIcon';
import { LayoutGrid, MessagesSquare } from './icons';
import { LensAvatar } from './LensAvatar';

export function SourceBadge({ source, size }: { source?: string; size: number }) {
  const t = useTheme();
  const { groups, sideChats, avatar } = useStore();
  if (!source || source === 'main') return <LensAvatar size={size} config={avatar} />;
  const g = groups.find((x) => x.id === source);
  if (g) return <GroupBadge icon={g.icon} size={size} />;
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
