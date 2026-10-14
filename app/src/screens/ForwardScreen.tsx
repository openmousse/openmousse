// 合并转发（server/forward.py）：几条消息合成一份。进来的方式：长按一条「转发」、多选 / 「选这一段」以后点「转发」、点转发篮。
// 三个去处：发给朋友（一份「聊天记录」分享：先挡私事，能追问、能收回；里面有朋友说的话就不能追问）、
// 转给自己的 Agent（带一个意图和一句话；朋友说的话只交要点）、更多（放进播客、存进收藏）。
import React, { useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Switch, TextInput, View } from 'react-native';
import { useNavigation, useRoute } from '@react-navigation/native';
import * as fw from '../api/forward';
import type { ForwardIntent, ForwardItem, ForwardPreview } from '../api/forward';
import * as fr from '../api/friends';
import type { Friend } from '../api/friends';
import * as shareApi from '../api/share';
import type { Share, ShareMask } from '../api/share';
import type { MatKind } from '../api/podcast';
import { agentName } from '../brand';
import { BundleCard, Transcript, plainLine, setBasketCount } from '../components/Forward';
import { Bookmark, Check, Mic, ShieldCheck, Users, X } from '../components/icons';
import { useSheet } from '../components/Sheet';
import { Btn, Card, NavHeader, Screen, SectionLabel, Segmented, T, showError } from '../components/ui';
import { L } from '../i18n';
import { useStore } from '../store';
import { radius, space, type, useTheme } from '../theme';
import { PutInPodcast } from '../think/Materials';
import { openEpisode } from '../think/Podcast';
import { MaskRow } from './ShareScreen';
import { KeyboardScroll } from '../components/keyboard';

type Dest = 'friends' | 'agent' | 'more';
const EMPTY: ForwardPreview = { items: [], gist: '', convs: [], external: [], hasExternal: false };
const INTENTS: { value: ForwardIntent; label: string; note: string }[] = [
  { value: 'remember', label: L('记录', 'Remember'), note: L('存入该 Agent 负责的记录或记忆，完成后告诉你记录了什么', 'It keeps what matters in its records or memory and tells you what it kept') },
  { value: 'handle', label: L('处理', 'Handle'), note: L('按你的要求完成这件事；涉及付款或对外发送的操作仍需你批准', 'It does what you want done; payments or anything sent out still need your approval') },
  { value: 'look', label: L('仅阅读', 'Read only'), note: L('阅读后给出看法或建议，不做任何修改', 'It reads them and gives its view or suggestions without changing anything') },
];

/** 放进播客用的素材 id（podmaterials 的 ref）：对话是 <线程>:db<id>，朋友聊天是 <好友>:<行 id> */
const podTarget = (it: ForwardItem): { kind: MatKind; target: string } =>
  it.back.kind === 'chat' ? { kind: 'chat', target: `${it.back.thread}:${it.back.id}` } : { kind: 'friend', target: `${it.back.friend}:${it.back.id}` };

export function ForwardScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const route = useRoute<any>();
  const sheet = useSheet();
  const { groups } = useStore();
  const fromBasket = !!route.params?.basket;
  const [refs, setRefs] = useState<string[]>(route.params?.refs ?? []);
  const [pv, setPv] = useState<ForwardPreview | null>(null);
  const [gist, setGist] = useState<string>(route.params?.gist ?? '');
  const [err, setErr] = useState('');
  const [dest, setDest] = useState<Dest>('friends');
  const [done, setDone] = useState('');
  const [busy, setBusy] = useState(false);
  // 朋友
  const [friends, setFriends] = useState<Friend[]>([]);
  const [picked, setPicked] = useState<string[]>([]);
  const [canAsk, setCanAsk] = useState(true);
  const [share, setShare] = useState<Share | null>(null);
  // Agent
  const [agent, setAgent] = useState('main');
  const [intent, setIntent] = useState<ForwardIntent>('remember');
  const [note, setNote] = useState('');

  const startRefs: string[] | undefined = route.params?.refs;
  const startGist: string | undefined = route.params?.gist;
  // 先拿要转的几条（转发篮的从服务器取），再要预览和一句要点（多选 /「选这一段」带过来的要点就用它）
  useEffect(() => {
    let live = true;
    const got = fromBasket ? fw.basket().then((b) => { setBasketCount(b.count); return b.items.map((i) => i.ref); }) : Promise.resolve(startRefs ?? []);
    got.then((r) => {
      if (!live) return null;
      setRefs(r);
      return r.length ? fw.preview(r, startGist) : EMPTY;
    }).then((p) => {
      if (!live || !p) return;
      setPv(p);
      setGist((g) => g || p.gist);
      setErr('');
    }).catch((e) => { if (live) setErr(e instanceof Error ? e.message : String(e)); });
    return () => { live = false; };
  }, [fromBasket, startRefs, startGist]);
  useEffect(() => { fr.home().then((h) => setFriends(h.friends.filter((f) => f.status === 'active'))).catch(() => setFriends([])); }, []);

  const items = pv?.items ?? [];
  const external = pv?.external ?? [];
  const hasExternal = !!pv?.hasExternal;
  // 发给朋友：先建一份草稿分享，看挡了哪些；要点改了发之前再改标题
  useEffect(() => {
    if (dest !== 'friends' || share || !items.length || done) return;
    fw.toShare(items.map((i) => i.ref), gist).then(setShare).catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dest, items.length]);
  const toggleMask = async (m: ShareMask) => {
    if (!share) return;
    try { setShare(await shareApi.patchShare(share.id, m.released ? { hide: [m.id] } : { release: [m.id] })); } catch (e) { showError(L('修改失败', "Couldn't update"), e); }
  };
  const removeItem = async (ref: string) => {
    const left = refs.filter((r) => r !== ref);
    setRefs(left);
    setShare(null);
    setPv((p) => (p ? { ...p, items: p.items.filter((i) => i.ref !== ref) } : p));
    if (fromBasket) fw.dropFromBasket([ref]).then(setBasketCount).catch(() => {});
    if (left.length) fw.preview(left, gist || undefined).then(setPv).catch(() => {});
  };
  const finish = async (line: string) => {
    setDone(line);
    if (fromBasket) { await fw.dropFromBasket().catch(() => 0); setBasketCount(0); }
  };

  const sendFriends = async () => {
    if (!share || busy || !picked.length) return;
    setBusy(true);
    try {
      if (gist.trim() && gist.trim() !== share.title) await shareApi.patchShare(share.id, { title: gist.trim() });
      await fr.sendShare(share.id, { friends: picked, ask: canAsk && !hasExternal, link: false });
      const names = friends.filter((f) => picked.includes(f.id)).map((f) => f.name);
      await finish(L(`已发送给 ${names.join('、')}`, `Sent to ${names.join(', ')}`));
    } catch (e) { showError(L('发送失败', "Couldn't send"), e); } finally { setBusy(false); }
  };
  const sendAgent = async () => {
    if (busy || !items.length) return;
    setBusy(true);
    try {
      const r = await fw.toAgent({ refs: items.map((i) => i.ref), gist: gist.trim(), agent, intent, note: note.trim() || undefined });
      await finish('');
      if (r.thread === 'main') nav.replace('Chat', { id: 'main', at: Date.now(), focus: r.messageId });
      else nav.replace('Group', { id: r.thread });
    } catch (e) { showError(L('转发失败', "Couldn't forward"), e); } finally { setBusy(false); }
  };
  const save = async () => {
    if (busy || !items.length) return;
    setBusy(true);
    try {
      await fw.toSave(items.map((i) => i.ref), gist.trim());
      await finish(L('已收藏，可在「Zen → 收藏」中查看', 'Saved. Find it in Zen → Saved'));
    } catch (e) { showError(L('收藏失败', "Couldn't save"), e); } finally { setBusy(false); }
  };
  const toPodcast = () => {
    if (!items.length) return;
    const [first, ...rest] = items.map(podTarget);
    sheet.open({ title: L('选择一期', 'Choose an episode'), content: (close) => (
      <PutInPodcast kind={first.kind} target={first.target} more={rest} close={() => { close(); finish(L('已添加到播客', 'Added to the podcast')); }}
        onOpen={(e) => openEpisode(nav, e)} />
    ) });
  };
  const openAll = () => sheet.open({ title: gist || L('聊天记录', 'Messages'), content: () => (
    <Transcript items={dest === 'friends' && share?.items?.length ? share.items.map((i, k) => ({ ...i, when: items[k]?.when, convName: items[k]?.convName })) : items} />
  ) });

  const agents = useMemo(() => [{ id: 'main', name: agentName() }, ...groups.map((g) => ({ id: g.id, name: g.name }))], [groups]);
  const masks = share?.masks ?? [];
  const sub = items.length ? L(`${items.length} 条 · 来自${(pv?.convs ?? []).join('、')}`, `${items.length} message${items.length === 1 ? '' : 's'} · from ${(pv?.convs ?? []).join(', ')}`) : undefined;

  return (
    <Screen>
      <NavHeader title={fromBasket ? L('转发篮', 'Forward basket') : L('转发', 'Forward')} sub={sub} onBack={() => nav.goBack()} />
      <KeyboardScroll contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl, gap: space.md }} keyboardShouldPersistTaps="handled">
        {err ? <Card><T v="callout" color={t.bad}>{L(`无法打开：${err}`, `Couldn't open it: ${err}`)}</T></Card> : null}
        {!pv && !err ? <ActivityIndicator color={t.gold} style={{ marginTop: space.xl }} /> : null}
        {pv && !items.length ? (
          <Card><T v="callout" color={t.ink2}>{fromBasket ? L('转发篮为空。长按任意消息 →「加入转发篮」，可汇集多个对话中的消息一并转发。', 'The basket is empty. Long-press any message → Add to basket to collect messages from several chats and forward them together.')
            : L('消息不存在或无法转发。', "Those messages are gone or can't be forwarded.")}</T></Card>
        ) : null}

        {items.length ? (
          <>
            <SectionLabel>{L('要点', 'Title')}</SectionLabel>
            <TextInput value={gist} onChangeText={setGist} maxLength={60} placeholder={L('一句话概括内容', 'One line on what this is')} placeholderTextColor={t.ink3}
              style={[type.body, styles.field, { color: t.ink, borderColor: t.line, backgroundColor: t.surface }]} accessibilityLabel={L('要点', 'Title')} />
            {/* 发给好友时预览就是对方看到的样子（挡好的、站在对方的角度写的说话人） */}
            <BundleCard wide title={gist} n={items.length} convs={pv?.convs} lines={dest === 'friends' && share?.items?.length ? share.items : items} onOpen={openAll} />

            {fromBasket ? (
              <Card style={{ gap: 0, paddingVertical: space.xs }}>
                {items.map((it, i) => (
                  <View key={it.ref} style={[styles.row, i < items.length - 1 && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }]}>
                    <View style={{ flex: 1, minWidth: 0 }}>
                      <T v="caption" color={t.ink3} numberOfLines={1}>{`${it.convName} · ${it.when}`}</T>
                      <T v="callout" numberOfLines={1}><T v="callout" style={{ fontWeight: '600' }}>{it.name}</T>{L('：', ': ')}{plainLine(it.text)}</T>
                    </View>
                    <Pressable onPress={() => removeItem(it.ref)} hitSlop={10} accessibilityRole="button" accessibilityLabel={L('从转发篮拿掉', 'Remove from basket')}>
                      <X size={16} color={t.ink3} />
                    </Pressable>
                  </View>
                ))}
              </Card>
            ) : null}

            {done ? (
              <Card style={{ gap: space.md }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                  <Check size={18} color={t.good} strokeWidth={2.5} />
                  <T v="headline" color={t.good} style={{ flex: 1 }}>{done}</T>
                </View>
                <View style={{ flexDirection: 'row', gap: space.sm }}>
                  {share && dest === 'friends' ? <Btn label={L('收回', 'Withdraw')} kind="quiet" flex onPress={() => { shareApi.revokeShare(share.id).then(() => setDone(L('已收回，对方处的内容已清除', 'Withdrawn; it was removed on their side'))).catch((e) => showError(L('收回失败', "Couldn't withdraw"), e)); }} /> : null}
                  <Btn label={L('完成', 'Done')} flex onPress={() => nav.goBack()} />
                </View>
              </Card>
            ) : (
              <>
                <SectionLabel>{L('转发到', 'Send to')}</SectionLabel>
                <Segmented<Dest> value={dest} onChange={setDest}
                  options={[{ value: 'friends', label: L('好友', 'Friends') }, { value: 'agent', label: L('我的 Agent', 'My Agents') }, { value: 'more', label: L('更多', 'More') }]} />

                {dest === 'friends' ? (
                  <Card style={{ gap: space.md }}>
                    {!friends.length ? <T v="callout" color={t.ink2}>{L('暂无好友。可在「对话 → 好友」中添加。', 'No friends yet. Add one under Chats → Friends.')}</T> : (
                      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm }}>
                        {friends.map((f) => {
                          const on = picked.includes(f.id);
                          return (
                            <Pressable key={f.id} onPress={() => setPicked(on ? picked.filter((x) => x !== f.id) : [...picked, f.id])} accessibilityRole="button" accessibilityState={{ selected: on }}
                              style={[styles.pill, { borderColor: on ? t.ink : t.line, backgroundColor: on ? t.ink : t.surface }]}>
                              <T v="callout" color={on ? t.bg : t.ink} style={{ fontWeight: '600' }}>{f.name}</T>
                            </Pressable>
                          );
                        })}
                      </View>
                    )}
                    {hasExternal ? (
                      <T v="caption" color={t.ink3} style={{ fontSize: 13 }}>
                        {external.length ? L(`包含 ${external.join('、')} 的发言：对方可看到每条的发言人；此转发不可追问，你的名片 Agent 不代他人回答。`, `Includes messages from ${external.join(', ')}: they'll see who said what. It can't take questions; your card agent doesn't answer for others.`)
                          : L('包含你的名片 Agent 对好友的代答，转发时会注明发言人。', 'Includes your card agent\'s answers to a friend; it shows who said what.')}
                      </T>
                    ) : (
                      <View style={styles.switchRow}>
                        <View style={{ flex: 1, gap: 2 }}>
                          <T v="body">{L('允许好友追问', 'Friends can ask about it')}</T>
                          <T v="caption" color={t.ink3} style={{ fontSize: 13 }}>{L('你的名片 Agent 仅依据这段内容、按对方所在档位回答', 'Your card agent answers from these messages only, by their tier')}</T>
                        </View>
                        <Switch value={canAsk} onValueChange={setCanAsk} accessibilityLabel={L('允许好友追问', 'Friends can ask about it')} trackColor={{ true: t.cyan, false: t.track }} thumbColor="#FFFFFF" />
                      </View>
                    )}
                    {share ? (
                      <View style={{ gap: 2 }}>
                        <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
                          <ShieldCheck size={16} color={t.good} />
                          <T v="callout" style={{ flex: 1, fontWeight: '600' }}>{share.maskCount === 0 ? L('未发现需要隐藏的隐私信息', 'Nothing private spotted')
                            : share.blocked > 0 ? L(`发送前已隐藏 ${share.blocked} 处`, `Hidden before sending: ${share.blocked}`) : L(`${share.maskCount} 处已全部显示`, `You showed all ${share.maskCount}`)}</T>
                        </View>
                        {masks.map((m, i) => <MaskRow key={m.id} m={m} last={i === masks.length - 1} onToggle={() => toggleMask(m)} />)}
                      </View>
                    ) : friends.length ? <ActivityIndicator color={t.gold} /> : null}
                    {friends.length ? (
                      <Btn label={busy ? L('正在发送…', 'Sending…') : picked.length ? (picked.length === 1 ? L(`发送给 ${friends.find((f) => f.id === picked[0])?.name}`, `Send to ${friends.find((f) => f.id === picked[0])?.name}`) : L(`发送给 ${picked.length} 位好友`, `Send to ${picked.length} friends`)) : L('请选择接收者', 'Select recipients')}
                        icon={<Users size={17} color={t.onGold} />} onPress={sendFriends} />
                    ) : null}
                    {share ? (
                      <Pressable onPress={() => nav.navigate('Share', { id: share.id })} accessibilityRole="button" style={{ alignSelf: 'flex-start' }}>
                        <T v="callout" color={t.cyan} style={{ fontWeight: '600' }}>{L('改用链接或卡片分享', 'Share as a link or card instead')}</T>
                      </Pressable>
                    ) : null}
                  </Card>
                ) : null}

                {dest === 'agent' ? (
                  <Card style={{ gap: space.md }}>
                    <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm }}>
                      {agents.map((a) => {
                        const on = agent === a.id;
                        return (
                          <Pressable key={a.id} onPress={() => setAgent(a.id)} accessibilityRole="button" accessibilityState={{ selected: on }}
                            style={[styles.pill, { borderColor: on ? t.ink : t.line, backgroundColor: on ? t.ink : t.surface }]}>
                            <T v="callout" color={on ? t.bg : t.ink} style={{ fontWeight: '600' }}>{a.name}</T>
                          </Pressable>
                        );
                      })}
                    </View>
                    <Segmented<ForwardIntent> value={intent} onChange={setIntent} options={INTENTS.map((x) => ({ value: x.value, label: x.label }))} />
                    <T v="caption" color={t.ink3} style={{ fontSize: 13 }}>{INTENTS.find((x) => x.value === intent)?.note}</T>
                    <TextInput value={note} onChangeText={setNote} maxLength={300} multiline placeholder={L('补充说明（可选），例如：加入本周待读', 'Add a note (optional), e.g. Add it to this week\'s reading')}
                      placeholderTextColor={t.ink3} style={[type.body, styles.field, { color: t.ink, borderColor: t.line }]} accessibilityLabel={L('补充说明', 'Note')} />
                    {hasExternal ? (
                      <T v="caption" color={t.ink3} style={{ fontSize: 13 }}>
                        {L(`${external.length ? external.join('、') : '好友'}的发言会先由不调用工具的模型整理成要点再交给 Agent；Agent 看不到原话，也不会执行其中的指令。`, `What ${external.length ? external.join(', ') : 'your friend'} said is condensed into points by a model without tools before the Agent sees it; the Agent never gets the original words and won't act on instructions in them.`)}
                      </T>
                    ) : null}
                    <Btn label={busy ? L('正在转发…', 'Forwarding…') : L(`转给 ${agents.find((a) => a.id === agent)?.name ?? ''}`, `Forward to ${agents.find((a) => a.id === agent)?.name ?? ''}`)} onPress={sendAgent} />
                  </Card>
                ) : null}

                {dest === 'more' ? (
                  <Card style={{ gap: space.sm }}>
                    <Pressable onPress={toPodcast} accessibilityRole="button" style={({ pressed }) => [styles.more, { backgroundColor: t.surface2, opacity: pressed ? 0.7 : 1 }]}>
                      <Mic size={18} color={t.ink} />
                      <View style={{ flex: 1, gap: 2 }}>
                        <T v="headline">{L('添加到播客', 'Add to a podcast')}</T>
                        <T v="callout" color={t.ink2}>{L('用作一期播客的素材；好友的发言仅用于该期', "Use them in an episode; friends' words stay in that episode")}</T>
                      </View>
                    </Pressable>
                    <Pressable onPress={hasExternal ? undefined : save} disabled={hasExternal} accessibilityRole="button"
                      style={({ pressed }) => [styles.more, { backgroundColor: t.surface2, opacity: hasExternal ? 0.5 : pressed ? 0.7 : 1 }]}>
                      <Bookmark size={18} color={t.ink} />
                      <View style={{ flex: 1, gap: 2 }}>
                        <T v="headline">{L('收藏', 'Save')}</T>
                        <T v="callout" color={t.ink2}>{hasExternal ? L('包含好友的发言，无法收藏（收藏可交给 Agent 处理）', "Includes a friend's messages, so it can't be saved (saved items can be handed to an Agent)")
                          : L('保存为一条收藏，之后可搜索或交给 Agent', 'Keep them as one saved item to search or hand to an Agent later')}</T>
                      </View>
                    </Pressable>
                  </Card>
                ) : null}
              </>
            )}
          </>
        ) : null}
      </KeyboardScroll>
    </Screen>
  );
}

const styles = StyleSheet.create({
  field: { borderWidth: StyleSheet.hairlineWidth, borderRadius: radius.md, paddingHorizontal: space.md, paddingVertical: 10, minHeight: 44 },
  pill: { borderWidth: 1, borderRadius: 18, paddingHorizontal: 14, height: 36, justifyContent: 'center' },
  switchRow: { flexDirection: 'row', alignItems: 'center', gap: space.md },
  row: { flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingVertical: space.sm },
  more: { flexDirection: 'row', alignItems: 'center', gap: space.md, borderRadius: radius.md, padding: space.md },
});
