// 播客的录前讨论群（主线第三步第二段，服务器 ../../../server/podprep.py）用到的两样：
// - PrepShelfCard：Zen 播客一边「制作中」的一张卡。和 ShelfCard 一样大，多两样：参与者的头像叠放（主持人在前）、约好的录制时间（金色那一段）；
//   状态那一行是群里最新的一句（没人说话时写下一步「提纲已就绪 · 4 条」）；四格是 讨论 → 提纲 → 录制 → 整理。
// - PrepPanel：录前讨论群顶上钉着的这一期：提纲（收起时一行，展开逐条）、排提纲 / 放进素材 / 约时间 / 开始录制，下面是「放进」的卡（只有你看得到）。
// - BookSheet：约录制时间（日子 + 几点）：你定的直接进日程（key podcast:<期>:rec）；有好友的群里管家说一声。
// - openPrepGroup：录前讨论群有两种：群聊 gc-…（GroupChat），拉了好友的好友群 fg-…（FriendGroup，第三段）。
import React, { useState } from 'react';
import { Pressable, StyleSheet, TextInput, View } from 'react-native';
import type { PrepEpisode, PrepOffer } from '../../api/groups';
import { L } from '../../i18n';
import { radius, space, type, useTheme } from '../../theme';
import { Pips } from '../../think/parts';
import { DateField, plusDays } from '../DateField';
import { CalendarDays, ChevronDown, ChevronUp, Mic, Paperclip, TextAlignStart } from '../icons';
import { Btn, T } from '../ui';
import { MemberFace, type FaceOf } from './GroupBits';

/** 打开这一期的录前讨论群：好友群（fg-）进好友群页，别的进群聊页。replace：换掉现在这一页（「先讨论」页建好群以后）。 */
export function openPrepGroup(nav: { navigate: (name: string, params?: object) => void; replace?: (name: string, params?: object) => void },
  gid: string, replace = false) {
  const to = gid.startsWith('fg-') ? 'FriendGroup' : 'GroupChat';
  if (replace && nav.replace) nav.replace(to, { id: gid });
  else nav.navigate(to, { id: gid });
}

/** podcast.brief 在录前讨论群开着时多给的几样（podprep.row_fields）。 */
export interface PrepRow {
  steps?: { current: number; labels: string[]; done: boolean };
  prepGroup?: string | null;
  participants?: (FaceOf & { id: string; host: boolean })[];
  booked?: { at: string; label: string } | null;
  latest?: string | null;
}

/** 头像叠放：每个往左压 30%，主持人在最前面。 */
function Faces({ people, size = 22 }: { people: (FaceOf & { id: string })[]; size?: number }) {
  const t = useTheme();
  return (
    <View style={{ flexDirection: 'row' }} accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
      {people.slice(0, 4).map((p, i) => (
        <View key={p.id} style={{ marginLeft: i ? -Math.round(size * 0.3) : 0, borderRadius: size / 2, borderWidth: 1.5, borderColor: t.surface }}>
          <MemberFace m={p} size={size} />
        </View>
      ))}
    </View>
  );
}

export function PrepShelfCard({ title, date, row, waiting, onPress, onLongPress }: {
  title: string; date: string; row: PrepRow; waiting: boolean; onPress: () => void; onLongPress?: () => void;
}) {
  const t = useTheme();
  const steps = row.steps ?? { current: 1, labels: [], done: false };
  const people = row.participants ?? [];
  const state = row.latest ?? '';
  return (
    <Pressable onPress={onPress} onLongPress={onLongPress} delayLongPress={400} accessibilityRole="button"
      accessibilityLabel={`${title}，${state}${row.booked ? `，${row.booked.label}` : ''}${waiting ? L('，等你处理', ', waiting for you') : ''}，${L(`进度 ${steps.current}/4（${steps.labels.join('、')}）`, `step ${steps.current} of 4 (${steps.labels.join(', ')})`)}`}
      style={({ pressed }) => [styles.shelf, { backgroundColor: t.surface, opacity: pressed ? 0.8 : 1 }]}>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
        <View>
          {people.length ? <Faces people={people} /> : <View style={[styles.tile, { backgroundColor: t.goldSoft }]}><Mic size={15} color={t.gold} /></View>}
          {waiting ? <View style={[styles.wait, { backgroundColor: t.warn, borderColor: t.surface }]} /> : null}
        </View>
        <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{date}</T>
      </View>
      <T v="callout" numberOfLines={2} style={{ fontWeight: '600', minHeight: 38 }}>{title}</T>
      {row.booked ? <T v="caption" color={t.gold} numberOfLines={1} style={{ fontWeight: '600', fontSize: 11.5 }}>{row.booked.label}</T> : null}
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
        <Pips n={steps.current} />
        <T v="caption" color={waiting ? t.warn : t.ink3} numberOfLines={1} style={{ flex: 1, fontWeight: '400', fontSize: 11.5 }}>{state}</T>
      </View>
    </Pressable>
  );
}

/** 录前讨论群顶上的这一期。busy：正在排提纲 / 放进素材 / 处理哪张卡。onBook：约录制时间（给了才有那个按钮）。 */
export function PrepPanel({ episode, offers, busy, onOutline, onOffer, onRecord, onDecide, onBook }: {
  episode: PrepEpisode; offers: PrepOffer[]; busy: string | null;
  onOutline: () => void; onOffer: () => void; onRecord: () => void; onDecide: (offer: PrepOffer, action: 'put' | 'skip') => void; onBook?: () => void;
}) {
  const t = useTheme();
  const [open, setOpen] = useState(false);
  const items = episode.outline;
  const waiting = offers.filter((o) => o.status === 'offered');
  const put = offers.filter((o) => o.status === 'put' || o.status === 'used').length;
  const btn = (key: string, label: string, Icon: typeof Mic, onPress: () => void, gold?: boolean) => (
    <Pressable key={key} onPress={onPress} disabled={!!busy} accessibilityRole="button" accessibilityLabel={label}
      style={({ pressed }) => [styles.btn, { backgroundColor: gold ? t.goldFill : t.surface2, opacity: busy && busy !== key ? 0.5 : pressed ? 0.8 : 1 }]}>
      <Icon size={13} color={gold ? t.onGold : t.ink} />
      <T v="caption" color={gold ? t.onGold : t.ink} style={{ fontSize: 13, fontWeight: '700' }}>{busy === key ? L('正在处理…', 'Working…') : label}</T>
    </Pressable>
  );
  return (
    <View style={[styles.panel, { backgroundColor: t.surface, borderBottomColor: t.line }]}>
      <Pressable onPress={() => setOpen((v) => !v)} accessibilityRole="button" accessibilityState={{ expanded: open }}
        style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
        <TextAlignStart size={14} color={t.gold} />
        <T v="caption" color={t.ink2} style={{ fontWeight: '700' }}>{items.length ? L(`提纲 · ${items.length} 条`, `Outline · ${items.length}`) : L('还没有提纲', 'No outline yet')}</T>
        <T v="caption" color={t.ink3} numberOfLines={1} style={{ flex: 1 }}>{[episode.modeLabel, episode.hostLine, episode.booked?.label].filter(Boolean).join(' · ')}</T>
        {items.length ? (open ? <ChevronUp size={15} color={t.ink3} /> : <ChevronDown size={15} color={t.ink3} />) : null}
      </Pressable>
      {open && items.length ? (
        <View style={{ gap: 4, paddingTop: 6 }}>
          {items.map((o, i) => <T key={i} v="callout" color={o.done ? t.ink3 : t.ink} style={{ fontSize: 14, textDecorationLine: o.done ? 'line-through' : 'none' }}>{`${i + 1}. ${o.text}`}</T>)}
        </View>
      ) : !open && items.length ? <T v="callout" numberOfLines={1} style={{ fontSize: 14, paddingTop: 4 }}>{`1. ${items[0].text}`}</T> : null}
      <View style={styles.actions}>
        {btn('outline', items.length ? L('改提纲', 'Revise outline') : L('排提纲', 'Draft outline'), TextAlignStart, onOutline)}
        {btn('offer', put ? L(`放进素材 · ${put}`, `Add material · ${put}`) : L('放进素材', 'Add material'), Paperclip, onOffer)}
        {onBook ? btn('book', episode.booked ? L('改时间', 'Change time') : L('约时间', 'Book a time'), CalendarDays, onBook) : null}
        {btn('record', L('开始录制', 'Start recording'), Mic, onRecord, true)}
      </View>
      {waiting.map((o) => (
        <View key={o.id} style={[styles.offer, { borderColor: t.line }]}>
          <T v="caption" color={t.ink3}>{L('仅你可见', 'Only you can see this')}</T>
          <T v="callout" style={{ fontWeight: '600', fontSize: 14 }}>{o.title}</T>
          {o.preview ? <T v="caption" color={t.ink2} numberOfLines={3}>{o.preview}</T> : null}
          <T v="caption" color={o.health ? t.warn : t.ink3}>{o.line}</T>
          <View style={{ flexDirection: 'row', gap: space.sm }}>
            <Pressable onPress={() => onDecide(o, 'put')} disabled={!!busy} accessibilityRole="button" style={({ pressed }) => [styles.btn, { backgroundColor: t.goldFill, opacity: busy ? 0.5 : pressed ? 0.8 : 1 }]}>
              <T v="caption" color={t.onGold} style={{ fontSize: 13, fontWeight: '700' }}>{busy === o.id ? L('正在处理…', 'Working…') : L('放进', 'Put in')}</T>
            </Pressable>
            <Pressable onPress={() => onDecide(o, 'skip')} disabled={!!busy} accessibilityRole="button" style={({ pressed }) => [styles.btn, { backgroundColor: t.surface2, opacity: busy ? 0.5 : pressed ? 0.8 : 1 }]}>
              <T v="caption" style={{ fontSize: 13, fontWeight: '600' }}>{L('不用', 'Skip')}</T>
            </Pressable>
          </View>
        </View>
      ))}
    </View>
  );
}

const TIMES = ['10:00', '14:00', '15:00', '19:00', '20:00'];
const HM = /^([01]\d|2[0-3]):[0-5]\d$/;

/** 约录制时间：日子（今天 / 明天 / +3 天 / +1 周）+ 几点；booked = 现在约的（YYYY-MM-DDTHH:MM），给了就多一个「不约了」。
 * onBook 收 "" = 不约了。friends：有好友的录前讨论（下面那一行说管家会在群里说一声）。 */
export function BookSheet({ booked, friends, close, onBook }: { booked: string | null; friends?: boolean; close: () => void; onBook: (at: string) => Promise<unknown> }) {
  const t = useTheme();
  const [day, setDay] = useState(booked ? booked.slice(0, 10) : plusDays(1));
  const [time, setTime] = useState(booked ? booked.slice(11, 16) : '15:00');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const ok = /^\d{4}-\d{2}-\d{2}$/.test(day) && HM.test(time);
  const go = (v: string) => {
    if (busy) return;
    setBusy(true);
    onBook(v).then(close).catch((e) => { setErr(e instanceof Error ? e.message : String(e)); setBusy(false); });
  };
  return (
    <View style={{ gap: space.md }}>
      <DateField value={day} onChange={setDay} label={L('日子', 'Day')} />
      <View style={styles.times}>
        {TIMES.map((x) => (
          <Pressable key={x} onPress={() => setTime(x)} accessibilityRole="button" accessibilityState={{ selected: x === time }}
            style={[styles.time, { backgroundColor: x === time ? t.goldSoft : t.surface, borderColor: x === time ? t.goldFill : t.line }]}>
            <T v="caption" color={x === time ? t.gold : t.ink2} style={{ fontSize: 13, fontWeight: '600' }}>{x}</T>
          </Pressable>
        ))}
        <TextInput value={time} onChangeText={setTime} placeholder="15:00" placeholderTextColor={t.ink3} maxLength={5} keyboardType="numbers-and-punctuation"
          accessibilityLabel={L('几点（HH:MM）', 'Time (HH:MM)')} style={[type.callout, styles.timeInput, { backgroundColor: t.surface, color: t.ink, borderColor: HM.test(time) ? t.line : t.bad }]} />
      </View>
      <T v="caption" color={t.ink3}>{friends ? L('你定的直接进日程，你的管家在群里说一声。好友在群里提的时间，会先出卡给你。', 'What you set goes straight into your calendar, and your butler tells the group. A time a friend proposes there comes to you as a card first.')
        : L('约好的时间进日程，和「制作中」那一段金色一致。', 'The time goes into your calendar and shows as the gold part of the in-progress row.')}</T>
      <Btn label={busy ? L('正在保存…', 'Saving…') : L('约好', 'Book it')} onPress={() => { if (ok) go(`${day}T${time}`); }} />
      {booked ? <Btn kind="quiet" label={L('不约了', 'Clear the time')} onPress={() => go('')} /> : null}
      {err ? <T v="callout" color={t.bad}>{err}</T> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  times: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, alignItems: 'center' },
  time: { height: 34, paddingHorizontal: 12, borderRadius: 17, borderWidth: StyleSheet.hairlineWidth, alignItems: 'center', justifyContent: 'center' },
  timeInput: { height: 34, width: 72, borderRadius: 17, borderWidth: StyleSheet.hairlineWidth, paddingHorizontal: 12, paddingVertical: 0, textAlign: 'center' },
  shelf: { width: 156, borderRadius: 14, padding: 12, gap: 8 },
  tile: { width: 30, height: 30, borderRadius: 9, alignItems: 'center', justifyContent: 'center' },
  wait: { position: 'absolute', right: -3, top: -3, width: 10, height: 10, borderRadius: 5, borderWidth: 2 },
  panel: { paddingHorizontal: space.lg, paddingVertical: 10, borderBottomWidth: StyleSheet.hairlineWidth, gap: 2 },
  actions: { flexDirection: 'row', flexWrap: 'wrap', gap: space.sm, paddingTop: 8 },
  btn: { flexDirection: 'row', alignItems: 'center', gap: 4, height: 30, paddingHorizontal: 12, borderRadius: radius.lg },
  offer: { borderWidth: StyleSheet.hairlineWidth, borderRadius: radius.md, padding: 10, gap: 4, marginTop: 8 },
});
