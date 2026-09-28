// 朋友那几页共用的小零件（FriendsScreens、FriendAgentsScreen 都用；单独放一个文件，两页互相引用时不绕圈）。
import React from 'react';
import { View } from 'react-native';
import { L } from '../i18n';
import { useStore } from '../store';
import { useTheme } from '../theme';
import { LensAvatar } from './LensAvatar';

/** 名片 agent 的小透镜：自己的用自己的形象，朋友的是粉紫色的环。 */
export function AgentLens({ mine, size = 28 }: { mine: boolean; size?: number }) {
  const t = useTheme();
  const { avatar } = useStore();
  if (mine) return <LensAvatar size={size} config={avatar} />;
  return (
    <View style={{ width: size, height: size, borderRadius: size / 2, backgroundColor: t.lensField, alignItems: 'center', justifyContent: 'center' }}>
      <View style={{ width: size / 2, height: size / 2, borderRadius: size / 4, borderWidth: 2, borderColor: '#F291BC', borderTopColor: '#B9A4F4' }} />
    </View>
  );
}

function ymd(d: Date) { return `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`; }

/** 列表和气泡上的时间：今天 = 钟点，昨天，今年 = 月/日，更早带年。 */
export function timeLabel(ts: string | null | undefined): string {
  if (!ts) return '';
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return '';
  const now = new Date();
  const yest = new Date(now.getTime() - 86400000);
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  if (ymd(d) === ymd(now)) return hm;
  if (ymd(d) === ymd(yest)) return L(`昨天 ${hm}`, `Yesterday ${hm}`);
  if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}/${d.getDate()}`;
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`;
}
