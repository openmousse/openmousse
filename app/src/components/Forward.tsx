// 合并转发的几块界面：聊天记录卡（发给朋友的、转给 Agent 的）、点开看全部、多选时底下那一条、转发篮的小按钮、长按菜单里的几项。
import React, { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, View } from 'react-native';
import * as fw from '../api/forward';
import type { ForwardItem } from '../api/forward';
import { Check, Forward, Layers, ListChecks, MessagesSquare, PackagePlus, X } from './icons';
import { Markdown } from './Markdown';
import { T } from './ui';
import { L } from '../i18n';
import { navigationRef } from '../navigation';
import { radius, space, useTheme } from '../theme';

// —— 转发篮里有几条：几个页面共用一个数，谁改了都通知一遍 ——

let basketCount = 0;
const listeners = new Set<(n: number) => void>();
export function setBasketCount(n: number) {
  basketCount = n;
  listeners.forEach((f) => f(n));
}
export const refreshBasket = () => fw.basket().then((b) => setBasketCount(b.count)).catch(() => {});
export function useBasketCount(): number {
  const [n, setN] = useState(basketCount);
  useEffect(() => {
    listeners.add(setN);
    refreshBasket();
    return () => { listeners.delete(setN); };
  }, []);
  return n;
}

/** 转发篮有东西时浮在输入栏上面的一个小按钮 */
export function BasketPill({ onPress }: { onPress: () => void }) {
  const t = useTheme();
  const n = useBasketCount();
  if (!n) return null;
  return (
    <Pressable onPress={onPress} accessibilityRole="button" accessibilityLabel={L(`转发篮，${n} 条`, `Forward basket, ${n}`)}
      style={({ pressed }) => [styles.basket, { backgroundColor: t.ink, opacity: pressed ? 0.8 : 1 }]}>
      <Layers size={15} color={t.bg} />
      <T v="callout" color={t.bg} style={{ fontWeight: '700' }}>{L(`转发篮 · ${n}`, `Basket · ${n}`)}</T>
    </Pressable>
  );
}

// —— 聊天记录卡 ——

export interface BundleLine { name: string; text: string }

const BLOCK = '▇▇▇';

/** 挡住的地方（服务器给的是 ▇▇▇）画成一小段浅灰条，和分享页一样 */
function Masked({ text, color }: { text: string; color: string }) {
  return <>{text.split(BLOCK).flatMap((part, i) => (i ? [<T key={i} v="callout" style={{ backgroundColor: color, color }}>{'\u2003\u2003'}</T>, part] : [part]))}</>;
}

/** 卡片上一行的预览：去掉 Markdown 的符号、合成一行 */
export const plainLine = (s: string) => s.replace(/^\s{0,3}(#{1,6}\s+|>\s?|[-*+]\s+|\d+[.)]\s+)/gm, '').replace(/(\*\*|__|`)/g, '').replace(/\s+/g, ' ').trim();

/** 一份聊天记录：标题（要点）、几条、来自哪里、前三条的开头；点开看全部。 */
export function BundleCard({ title, n, convs, lines, mine, wide, note, onOpen, gone }: {
  title: string; n: number; convs?: string[]; lines: BundleLine[]; mine?: boolean; /** 占满一行（转发页里），不当气泡 */ wide?: boolean;
  note?: string; onOpen?: () => void; gone?: string;
}) {
  const t = useTheme();
  const from = convs?.length ? convs.join(L('、', ', ')) : '';
  return (
    <Pressable onPress={gone ? undefined : onOpen} disabled={!onOpen || !!gone} accessibilityRole="button"
      style={({ pressed }) => [styles.card, wide ? { maxWidth: '100%', alignSelf: 'stretch' } : { alignSelf: mine ? 'flex-end' : 'flex-start' },
        { backgroundColor: t.surface, borderColor: t.line, opacity: pressed ? 0.8 : 1 }]}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
        <MessagesSquare size={14} color={t.cyan} />
        <T v="caption" color={t.cyan} style={{ fontWeight: '600', flex: 1 }} numberOfLines={1}>
          {L(`聊天记录 · ${n} 条`, `${n} message${n === 1 ? '' : 's'}`)}{from ? L(` · 来自${from}`, ` · from ${from}`) : ''}
        </T>
      </View>
      {gone ? <T v="callout" color={t.ink3}>{gone}</T> : (
        <>
          <T v="headline" style={{ fontSize: 16 }} numberOfLines={2}>{title || L('（无标题）', '(untitled)')}</T>
          <View style={[styles.lines, { borderLeftColor: t.line }]}>
            {lines.slice(0, 3).map((x, i) => (
              <T key={i} v="callout" color={t.ink2} numberOfLines={1}>
                <T v="callout" color={t.ink} style={{ fontWeight: '600' }}>{x.name}</T>{L('：', ': ')}<Masked text={plainLine(x.text)} color={`${t.ink3}59`} />
              </T>
            ))}
            {n > 3 ? <T v="caption" color={t.ink3}>{L(`还有 ${n - 3} 条`, `${n - 3} more`)}</T> : null}
          </View>
          {note ? <T v="callout">{note}</T> : null}
          {onOpen ? <T v="callout" color={t.gold} style={{ fontWeight: '600' }}>{L('查看全部', 'See all')}</T> : null}
        </>
      )}
    </Pressable>
  );
}

/** 点开一份聊天记录：一条条按对话分组（弹层里用）。onBack：回到原来那段对话（只给自己的转发）。 */
export function Transcript({ items, onBack }: { items: { name: string; text: string; when?: string; convName?: string; who?: string }[]; onBack?: (i: number) => void }) {
  const t = useTheme();
  return (
    <View style={{ gap: space.md, paddingBottom: space.md }}>
      {items.map((x, i) => {
        const head = x.convName && x.convName !== items[i - 1]?.convName ? x.convName : null;  // 换了一段对话：上面写是哪段
        return (
          <View key={i} style={{ gap: 4 }}>
            {head ? (
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm, marginTop: i ? space.sm : 0 }}>
                <T v="caption" color={t.ink3} style={{ fontWeight: '700', flex: 1 }}>{head}</T>
                {onBack ? <Pressable onPress={() => onBack(i)} hitSlop={8} accessibilityRole="button"><T v="caption" color={t.gold} style={{ fontWeight: '600' }}>{L('打开对话', 'Open chat')}</T></Pressable> : null}
              </View>
            ) : null}
            <View style={{ flexDirection: 'row', gap: 6, alignItems: 'baseline' }}>
              <T v="callout" style={{ fontWeight: '700' }} color={x.who === 'friend' || x.who === 'friend_agent' ? t.cyan : t.ink}>{x.name}</T>
              {x.when ? <T v="caption" color={t.ink3}>{x.when}</T> : null}
            </View>
            {x.text.includes(BLOCK) ? <T v="body" selectable><Masked text={x.text} color={`${t.ink3}59`} /></T> : <Markdown text={x.text} />}
          </View>
        );
      })}
    </View>
  );
}

/** 打开一条的原处：Agent 的对话（主对话、项目在「对话」页，Group 在它自己的页）或和朋友的聊天。弹层里也能用（走 navigationRef）。 */
export function openBack(back: ForwardItem['back'], groupIds: string[]) {
  if (!navigationRef.isReady()) return;
  if (back.kind === 'friend') navigationRef.navigate('FriendChat', { id: back.friend });
  else if (groupIds.includes(back.thread)) navigationRef.navigate('Group', { id: back.thread });
  else navigationRef.navigate('Tabs', { screen: '对话', params: { thread: back.thread, at: Date.now(), focus: back.id } });
}

/** 转给 Agent 的那条消息：先拿全文再画（服务器在消息上只存了前三条的开头） */
export function ForwardTranscript({ id, onBack }: { id: string; onBack?: (it: ForwardItem) => void }) {
  const t = useTheme();
  const [items, setItems] = useState<ForwardItem[] | null>(null);
  const [err, setErr] = useState('');
  useEffect(() => { fw.getForward(id).then((f) => setItems(f.items)).catch((e) => setErr(e instanceof Error ? e.message : String(e))); }, [id]);
  if (err) return <T v="callout" color={t.bad}>{err}</T>;
  if (!items) return <ActivityIndicator color={t.gold} />;
  return <Transcript items={items} onBack={onBack ? (i) => onBack(items[i]) : undefined} />;
}

// —— 多选 ——

/** 多选时代替输入栏的那一条：已选几条、加入转发篮、转发、取消 */
export function SelectBar({ n, busy, onCancel, onBasket, onForward }: { n: number; busy?: boolean; onCancel: () => void; onBasket: () => void; onForward: () => void }) {
  const t = useTheme();
  const off = !n || busy;
  return (
    <View style={[styles.selectBar, { borderTopColor: t.line, backgroundColor: t.bg }]}>
      <Pressable onPress={onCancel} hitSlop={10} accessibilityRole="button" accessibilityLabel={L('取消多选', 'Cancel selection')} style={{ padding: 4 }}>
        <X size={20} color={t.ink2} />
      </Pressable>
      <T v="callout" color={t.ink2} style={{ flex: 1 }}>{busy ? L('正在圈选…', 'Selecting…') : L(`已选 ${n} 条`, `${n} selected`)}</T>
      <Pressable onPress={onBasket} disabled={off} accessibilityRole="button"
        style={({ pressed }) => [styles.barBtn, { backgroundColor: t.surface2, opacity: off ? 0.4 : pressed ? 0.7 : 1 }]}>
        <PackagePlus size={16} color={t.ink} />
        <T v="callout" style={{ fontWeight: '600' }}>{L('加入转发篮', 'Add to basket')}</T>
      </Pressable>
      <Pressable onPress={onForward} disabled={off} accessibilityRole="button"
        style={({ pressed }) => [styles.barBtn, { backgroundColor: t.goldFill, opacity: off ? 0.4 : pressed ? 0.7 : 1 }]}>
        <Forward size={16} color={t.onGold} />
        <T v="callout" color={t.onGold} style={{ fontWeight: '700' }}>{L('转发', 'Forward')}</T>
      </Pressable>
    </View>
  );
}

/** 多选时消息左边的勾 */
export function SelectMark({ on }: { on: boolean }) {
  const t = useTheme();
  return (
    <View style={[styles.mark, { borderColor: on ? t.goldFill : t.ink3, backgroundColor: on ? t.goldFill : 'transparent' }]}>
      {on ? <Check size={13} color={t.onGold} strokeWidth={3} /> : null}
    </View>
  );
}

/** 一个对话里的多选：选了哪些 ref；「选这一段」交给服务器圈，圈完带回一句要点 */
export function useSelect() {
  const [refs, setRefs] = useState<string[] | null>(null);
  const [gist, setGist] = useState('');
  const [busy, setBusy] = useState(false);
  const start = useCallback((ref?: string) => { setRefs(ref ? [ref] : []); setGist(''); }, []);
  const stop = useCallback(() => { setRefs(null); setGist(''); }, []);
  const toggle = useCallback((ref: string) => { setGist(''); setRefs((cur) => (cur ? (cur.includes(ref) ? cur.filter((x) => x !== ref) : [...cur, ref]) : cur)); }, []);
  const around = useCallback(async (ref: string) => {
    setRefs([ref]);
    setBusy(true);
    try {
      const s = await fw.segment(ref);
      setRefs(s.refs.length ? s.refs : [ref]);
      setGist(s.gist);
    } catch {
      setRefs([ref]);
    } finally { setBusy(false); }
  }, []);
  return { on: refs !== null, refs: refs ?? [], gist, busy, start, stop, toggle, around, has: (ref: string) => !!refs?.includes(ref) };
}

/** 长按菜单里转发那几项（ChatView、朋友聊天共用）：每项是 [图标, 名字, 说明, 点了做什么] */
export function forwardActions(go: { forward: () => void; around: () => void; pick: () => void; basket: () => void }) {
  return [
    { icon: Forward, label: L('转发', 'Forward'), note: L('发送给好友，或转给你的 Agent', 'Send it to a friend or one of your Agents'), onPress: go.forward },
    { icon: Layers, label: L('选这一段', 'Select this conversation'), note: L('由 Agent 选中讨论同一件事的消息，再合并转发', 'Your Agent selects the messages on the same topic for you to forward together'), onPress: go.around },
    { icon: ListChecks, label: L('多选', 'Select messages'), note: L('选择多条消息合并转发', 'Pick several messages to forward together'), onPress: go.pick },
    { icon: PackagePlus, label: L('加入转发篮', 'Add to basket'), note: L('与其他对话中的消息汇集后一并转发', 'Collect it with messages from other chats and forward them together'), onPress: go.basket },
  ];
}

const styles = StyleSheet.create({
  basket: { flexDirection: 'row', alignItems: 'center', gap: 6, alignSelf: 'center', height: 30, paddingHorizontal: 14, borderRadius: 15, marginTop: space.sm },
  card: { maxWidth: '88%', minWidth: 220, borderWidth: StyleSheet.hairlineWidth, borderRadius: 18, padding: space.md, gap: 8 },
  lines: { borderLeftWidth: 2, paddingLeft: space.sm, gap: 2 },
  selectBar: { flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingHorizontal: space.md, paddingVertical: space.sm, borderTopWidth: StyleSheet.hairlineWidth },
  barBtn: { flexDirection: 'row', alignItems: 'center', gap: 5, height: 36, paddingHorizontal: 12, borderRadius: radius.md },
  mark: { width: 22, height: 22, borderRadius: 11, borderWidth: 1.5, alignItems: 'center', justifyContent: 'center' },
});
