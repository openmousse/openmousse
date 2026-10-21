// 好友一边「有人在的群」那几行（主线第三步第三段，设计稿第 40 张）：「周末读书会 · 群聊 · Mia 的管家：周六下午图书馆三楼？」。
// 好友群开着才画（useFriendGroupsOn）；FriendsHome 里只插这一个组件。点开是好友群页（FriendGroup）。
import React, { useCallback, useState } from 'react';
import { View } from 'react-native';
import { useFocusEffect, useNavigation } from '@react-navigation/native';
import { friendGroupsApi, useFriendGroupsOn, type FriendGroup } from '../../api/friendgroups';
import { L } from '../../i18n';
import { ChatRowView, rowTime } from '../ChatParts';
import { FriendGroupFace } from './GroupBits';

export function FriendGroupRows({ query = '' }: { query?: string }) {
  const nav = useNavigation<any>();
  const on = useFriendGroupsOn();
  const [groups, setGroups] = useState<FriendGroup[]>([]);
  useFocusEffect(useCallback(() => {
    if (!on) return;
    let live = true;
    friendGroupsApi.list().then((g) => { if (live) setGroups(g); }).catch(() => {});
    return () => { live = false; };
  }, [on]));
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const shown = groups.filter((g) => words.every((w) => `${g.title} ${g.members.map((m) => m.name).join(' ')}`.toLowerCase().includes(w)));
  if (!on || !shown.length) return null;
  return (
    <View>
      {shown.map((g, i) => (
        <ChatRowView key={g.id} face={<View style={{ padding: 4 }}><FriendGroupFace g={g} size={46} /></View>} title={g.title} tag={L('群聊', 'Group')}
          time={rowTime(g.last?.at ?? g.updatedAt)} unread={g.unread ?? 0} last={i === shown.length - 1}
          line={g.last ? `${g.last.name ? `${g.last.name}${L('：', ': ')}` : ''}${g.last.text}` : L('还没有人说话', 'Nobody has said anything yet')}
          onPress={() => nav.navigate('FriendGroup', { id: g.id })} />
      ))}
    </View>
  );
}
