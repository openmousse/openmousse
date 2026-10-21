// 和赛博一对一（主线第三步，设计稿第 43 张）：复用对话页的组件（ChatView 只引用、不改），页头换成半身像 + 名字 +「装载：GitHub 雷达、笔记库」
// （没装载写「赛博 · 模型」）；ⓘ 进它的资料页。装载工具的步骤、改数据的卡、「先给样子」的改法卡都是服务器那边的事件和收件箱卡，对话里本来就会画。
// 路由 CyberChat（params id，同 Chat 的 at / quote / focus）。线程就是这位赛博的 id（服务器 cybers.py：/api/chat/send 到这里跑赛博回合）。
import React, { useCallback, useEffect, useState } from 'react';
import { useFocusEffect, useNavigation, useRoute } from '@react-navigation/native';
import { CyberAvatar } from '../../components/agents/CyberAvatar';
import { ChatHeader, StateFace } from '../../components/ChatParts';
import { ChatView } from '../../components/ChatView';
import { Screen } from '../../components/ui';
import { cybersApi, type Cyber, type LoadAgent } from '../../api/cybers';
import type { ChatQuote } from '../../navigation';
import { L } from '../../i18n';
import { useStore, useThreadOnScreen } from '../../store';
import { useChatList } from '../ChatScreen';

export function CyberChatScreen() {
  const nav = useNavigation<any>();
  const { id, at = 0, quote, focus } = (useRoute<any>().params ?? {}) as { id: string; at?: number; quote?: ChatQuote; focus?: string };
  const { refreshThread } = useStore();
  const list = useChatList();
  const [cy, setCy] = useState<Cyber | null>(null);
  const [loaded, setLoaded] = useState<LoadAgent[]>([]);
  useThreadOnScreen(id);
  // 赛博的对话不在 store 一开始读的那几个线程里：进来时读一次（之后来了新消息，store 照未读重读）
  useEffect(() => { refreshThread(id).catch(() => {}); }, [id, refreshThread]);
  useFocusEffect(useCallback(() => {
    cybersApi.get(id).then(setCy).catch(() => {});
    cybersApi.loadout(id).then((l) => setLoaded(l.loaded)).catch(() => {});
  }, [id]));
  const state = list.rows.find((r) => r.id === id)?.state ?? null;
  const row = list.rows.find((r) => r.id === id);
  const name = cy?.name ?? row?.title ?? L('赛博', 'Cyber');
  const usable = loaded.filter((a) => !a.health);
  const sub = usable.length ? L(`装载：${usable.map((a) => a.name).join('、')}`, `Loaded: ${usable.map((a) => a.name).join(', ')}`)
    : cy ? L(`赛博 · ${cy.modelName}`, `Cyber · ${cy.modelName}`) : L('赛博', 'Cyber');
  // 对话里回复旁边也画这位赛博，不画主对话的头像（Leo 10/21）
  const look = cy?.avatar ?? row?.avatar;
  const face = useCallback((size: number) => <CyberAvatar avatar={look} size={size} />, [look]);

  return (
    <Screen>
      <ChatHeader title={name} sub={sub} face={<StateFace size={36} state={state}><CyberAvatar avatar={cy?.avatar ?? row?.avatar} size={36} /></StateFace>}
        onBack={() => nav.goBack()} onInfo={() => nav.navigate('CyberProfile', { id })} infoLabel={L('资料、记忆与装载', 'Profile, memory and loadout')} />
      <ChatView key={id} threadId={id} face={face} quote={quote} quoteAt={quote ? at : 0} focus={focus} focusAt={focus ? at : 0}
        placeholder={L(`给 ${name} 发消息`, `Message ${name}`)}
        empty={cy?.identity ? L(`${name}：${cy.identity}`, `${name}: ${cy.identity}`) : L('说点什么，它陪你想。', "Say something; it'll think it through with you.")} />
    </Screen>
  );
}
