// 对话里的两种卡片（服务器 cards.py）：
// - 转交卡：主对话把问题转给了某个 Agent。问的时候就出现（「正在问 饮食记录…」，带着转过去的原话），答完收成一行「转给了 …」，
//   挂在发起转交的那条回复上面；点一下到那个 Agent 的对话里那个问题。Agent 那边那一条（「主对话转来」）点一下回来。
// - 任务卡：这条回复派出去的后台任务。进行中显示在做哪一步；做完显示结果开头几行，「改一下」把意见直接发给做它的那个子会话，
//   卡片变成第 2 轮；「看全文」「看过程」到任务详情。
// - 日程卡（服务器 schedule.py）：Agent 在这次回复里改了日程或「要记得的」（挪时间、课不去、打勾、改邮件条目），一行写改了什么，能撤销。
// - 项目小卡（服务器 projects.py）：Agent 在这次回复里改了项目卡（加了下一步、记了已定的、更新进度、开了项目、写了结论），能撤销；不在那个项目里就点一下过去。
import React, { useEffect, useState } from 'react';
import { ActivityIndicator, Alert, Animated, Easing, Platform, Pressable, StyleSheet, Text, View } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import * as studyApi from '../api/study';
import type { CourseChangeCard, HandoffCard, ProjectChangeCard, ScheduleChangeCard, TaskCardInfo } from '../data/types';
import { L } from '../i18n';
import { openTarget, openThread } from '../navigation';
import { useStore } from '../store';
import { radius, space, type, useTheme } from '../theme';
import { BookOpen, CalendarDays, Check, ChevronRight, CircleAlert, Clock, CornerDownLeft, FileText, FolderKanban, ListChecks, LoaderCircle, Pencil, Send, Square, X } from './icons';
import { Markdown } from './Markdown';
import { SourceBadge } from './SourceBadge';
import { modelOf } from './ModelPicker';
import { fmtTokens, modelName } from './TaskCard';
import { T, showError } from './ui';

const native = Platform.OS !== 'web';

/** 任务卡上的模型名：比 short 长一点，带版本（Fable 5.1、GPT-6 Astra、Kimi K3）；目录里没有的用 modelName。 */
export const modelLabel = (id: string | null | undefined) => modelOf(id ?? undefined)?.name.replace(/^Claude /, '') ?? modelName(id);

/** 每秒走一下的「现在」（毫秒）：只在 on 的时候走，给「问了 12 秒」「做了 6 分钟」这种活的数字用。 */
export function useNow(on: boolean): number {
  const [now, setNow] = useState(0);
  useEffect(() => {
    if (!on) return undefined;
    const tick = () => setNow(Date.now());
    const first = setTimeout(tick, 0);
    const h = setInterval(tick, 1000);
    return () => { clearTimeout(first); clearInterval(h); };
  }, [on]);
  return now;
}

/** 转圈的小图标（LoaderCircle 本身不会转）。 */
export function Spinner({ size = 14, color }: { size?: number; color: string }) {
  const [spin] = useState(() => new Animated.Value(0));
  useEffect(() => {
    const loop = Animated.loop(Animated.timing(spin, { toValue: 1, duration: 1100, easing: Easing.linear, useNativeDriver: native }));
    loop.start();
    return () => loop.stop();
  }, [spin]);
  const rotate = spin.interpolate({ inputRange: [0, 1], outputRange: ['0deg', '360deg'] });
  return <Animated.View style={{ transform: [{ rotate }] }}><LoaderCircle size={size} color={color} /></Animated.View>;
}

/** 「正在问」后面一闪一闪的三个点（回复正在出字时，对话里文字下面也用它）。 */
export function Dots({ color }: { color: string }) {
  const [v] = useState(() => new Animated.Value(0));
  useEffect(() => {
    const loop = Animated.loop(Animated.timing(v, { toValue: 3, duration: 1200, easing: Easing.linear, useNativeDriver: native }));
    loop.start();
    return () => loop.stop();
  }, [v]);
  return (
    <View style={{ flexDirection: 'row', gap: 3, alignItems: 'center' }} accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
      {[0, 1, 2].map((i) => (
        <Animated.View key={i} style={{ width: 5, height: 5, borderRadius: 3, backgroundColor: color,
          opacity: v.interpolate({ inputRange: [0, 1, 2, 3].map((x) => x), outputRange: [0, 1, 2, 3].map((x) => ((x + 3 - i) % 3 === 0 ? 1 : 0.25)) }) }} />
      ))}
    </View>
  );
}

const secs = (n: number) => (n < 60 ? L(`${n} 秒`, `${n}s`) : L(`${Math.floor(n / 60)} 分 ${n % 60} 秒`, `${Math.floor(n / 60)}m ${n % 60}s`));
/** 中文里名字是英文（比如 Grava）时，和汉字之间空一格。 */
const zh = (name: string, rest: string) => (/[A-Za-z0-9]$/.test(name) ? `${name} ${rest}` : `${name}${rest}`);

// —— 转交卡 ——————————————————————————————————————————————————————

/** 主对话这边：转给了谁、问了什么、多久答完。点一下到那个 Agent 的对话，定位到那个问题。 */
export function HandoffChip({ card }: { card: HandoffCard }) {
  const t = useTheme();
  const { groups } = useStore();
  const running = card.status === 'running';
  const now = useNow(running);
  const started = Date.parse(card.createdAt);
  const elapsed = running && now && !Number.isNaN(started) ? Math.max(0, Math.round((now - started) / 1000)) : null;
  const name = groups.find((g) => g.id === card.to)?.name ?? card.toName;
  const head = {
    running: L(`正在问 ${name}`, `Asking ${name}`),
    done: L(`转给了 ${name}`, `Asked ${name}`),
    error: L(`问 ${name} 没问成`, `Couldn't reach ${name}`),
    busy: L(zh(name, '正忙，没转过去'), `${name} was busy`),
    lost: L(`没等到 ${name} 的回答`, `No answer from ${name} here`),
  }[card.status];
  const meta = running ? (elapsed != null ? secs(elapsed) : '')
    : card.status === 'done' && card.seconds != null ? L(`${secs(card.seconds)}答完`, `answered in ${secs(card.seconds)}`)
      : card.status === 'lost' ? L('回答在它那边', 'see its chat') : '';
  const canOpen = card.status !== 'busy';
  const open = () => openThread(card.to, groups.some((g) => g.id === card.to), undefined, card.relayId ?? undefined);
  return (
    <Pressable onPress={canOpen ? open : undefined} disabled={!canOpen} accessibilityRole={canOpen ? 'button' : undefined}
      accessibilityLabel={`${head}${L('：', ': ')}${card.question}`} accessibilityHint={canOpen ? L(`打开 ${name} 的对话`, `Opens ${name}'s chat`) : undefined}
      style={({ pressed }) => [styles.chip, { backgroundColor: running ? t.cyanSoft : t.surface, borderColor: running ? t.cyan : t.line, opacity: pressed ? 0.7 : 1 }]}>
      <SourceBadge source={card.to} size={30} />
      <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
          <T v="headline" numberOfLines={1} style={{ fontSize: 14, fontWeight: '700', flexShrink: 1 }} color={card.status === 'error' || card.status === 'busy' ? t.bad : t.ink}>{head}</T>
          {running ? <Dots color={t.cyan} /> : null}
          {meta ? <T v="caption" color={t.ink3} style={running ? { marginLeft: 'auto', fontVariant: ['tabular-nums'] } : undefined}>{meta}</T> : null}
        </View>
        <T v="callout" numberOfLines={1} color={t.ink2} style={{ fontSize: 13, lineHeight: 18 }}>{card.question}</T>
      </View>
      {canOpen && !running ? <ChevronRight size={16} color={t.ink3} /> : null}
    </Pressable>
  );
}

/** Agent 那边：「主对话转来」的那个问题。点一下回到转交它的那条回复。 */
export function HandoffFrom({ question, time, card, highlight }: { question: string; time: string; card?: HandoffCard; highlight?: boolean }) {
  const t = useTheme();
  const { groups, sideChats } = useStore();
  const from = card?.from ?? 'main';
  const fromName = from === 'main' ? L('主对话', 'Main chat') : groups.find((g) => g.id === from)?.name ?? sideChats.find((c) => c.id === from)?.title ?? card?.fromName ?? from;
  const back = () => openThread(from, groups.some((g) => g.id === from), undefined, card?.messageId != null ? `db${card.messageId}` : undefined);
  return (
    <Pressable onPress={back} accessibilityRole="button" accessibilityLabel={L(`${fromName}转来：${question}`, `From ${fromName}: ${question}`)} accessibilityHint={L(`回到${fromName}`, `Back to ${fromName}`)}
      style={({ pressed }) => [styles.from, { backgroundColor: t.surface, borderColor: highlight ? t.goldFill : t.goldSoft, opacity: pressed ? 0.7 : 1 }, highlight && { borderWidth: 2 }]}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
        <CornerDownLeft size={14} color={t.gold} />
        <T v="caption" color={t.gold} style={{ fontSize: 13, fontWeight: '700' }}>{L(`${fromName}转来`, `From ${fromName}`)}</T>
        <T v="caption" color={t.ink3}>{time}</T>
        <View style={{ flex: 1 }} />
        <T v="caption" color={t.gold} style={{ fontSize: 13, fontWeight: '600' }}>{L(`回${fromName}`, 'Back')}</T>
        <ChevronRight size={14} color={t.gold} />
      </View>
      <T v="callout" style={{ fontSize: 15, lineHeight: 21 }}>{question}</T>
    </Pressable>
  );
}

// —— 任务卡 ——————————————————————————————————————————————————————

const PREVIEW_LINES = 5;  // 表格是表头 + 分隔线 + 3 行
/** 结果开头几行（Markdown 按行截，表格也一样）；剩下几行。 */
function head(text: string): { shown: string; more: number } {
  const lines = text.replace(/\r\n/g, '\n').split('\n');
  let cut = 0;
  let kept = 0;
  while (cut < lines.length && kept < PREVIEW_LINES) { if (lines[cut].trim()) kept += 1; cut += 1; }
  const more = lines.slice(cut).filter((x) => x.trim()).length;
  return { shown: lines.slice(0, cut).join('\n').trim(), more };
}

function ModelChip({ id }: { id: string | null }) {
  const t = useTheme();
  if (!id) return null;
  return (
    <View style={[styles.model, { backgroundColor: t.bg }]}>
      <View style={{ width: 7, height: 7, borderRadius: 4, backgroundColor: t.cyan }} />
      <T v="caption" style={{ fontWeight: '600', color: t.ink }}>{modelLabel(id)}</T>
    </View>
  );
}

function SmallBtn({ label, icon: Icon, onPress, primary, busy }: { label: string; icon?: typeof Check; onPress: () => void; primary?: boolean; busy?: boolean }) {
  const t = useTheme();
  const fg = primary ? t.onGold : t.ink;
  return (
    <Pressable onPress={onPress} disabled={busy} accessibilityRole="button" accessibilityLabel={label}
      style={({ pressed }) => [styles.btn, { backgroundColor: primary ? t.goldFill : t.surface2, opacity: pressed ? 0.75 : 1 }, primary && { flexGrow: 1, flexShrink: 1 }]}>
      {busy ? <ActivityIndicator size="small" color={fg} /> : Icon ? <Icon size={16} color={fg} /> : null}
      <Text numberOfLines={1} style={[type.headline, { fontSize: 15, color: fg }]}>{label}</Text>
    </Pressable>
  );
}

/** 派出去的一个后台任务。onRevise：点「改一下」/「接着做」（对话把输入框换成「改：标题」，发出去直接交给做它的子会话）。 */
export function TaskCardView({ card, onRevise }: { card: TaskCardInfo; onRevise: (card: TaskCardInfo) => void }) {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { cancelTask } = useStore();
  const [stopping, setStopping] = useState(false);
  const revising = card.round > 1 && card.roundStatus === 'running';
  const running = card.status === '进行中' || revising;
  const now = useNow(running);
  const started = card.createdAt ? Date.parse(card.createdAt) : NaN;
  const minutes = running && !revising && now && !Number.isNaN(started) ? Math.max(card.minutes, Math.floor((now - started) / 60000)) : card.minutes;
  const openDetail = () => nav.navigate('Task', { id: card.id });

  if (card.status === '已取消' && !revising) {
    return (
      <View style={[styles.rcpt, { backgroundColor: t.surface, borderColor: t.line }]} accessible accessibilityLabel={L(`已停掉：${card.title}`, `Stopped: ${card.title}`)}>
        <View style={[styles.circle, { backgroundColor: t.surface2 }]}><X size={15} color={t.ink2} /></View>
        <View style={{ flex: 1, gap: 2 }}>
          <T v="headline" numberOfLines={1} style={{ fontSize: 15 }}>{L(`已停掉 · ${card.title}`, `Stopped · ${card.title}`)}</T>
          <T v="callout" color={t.ink2} style={{ fontSize: 13, lineHeight: 18 }}>{L(`做了 ${card.minutes} 分钟`, `Ran for ${card.minutes} min`)}</T>
        </View>
      </View>
    );
  }

  const failedRound = card.round > 1 && card.roundStatus === 'failed';
  const failed = !running && (card.status === '失败' || failedRound);
  const timedOut = failed && card.timedOut && !failedRound;
  const [statusText, statusColor, StatusIcon] = running
    ? [card.round > 1 ? L(`第 ${card.round} 轮 · 进行中`, `Round ${card.round} · running`) : L('进行中', 'Running'), t.cyan, null]
    : timedOut ? [L('到点停了', 'Hit its time limit'), t.warn, Clock]
      : failed ? [failedRound ? L(`第 ${card.round} 轮没做成`, `Round ${card.round} failed`) : L('没做成', 'Failed'), t.bad, CircleAlert]
        : [card.round > 1 ? L(`第 ${card.round} 轮 · 完成`, `Round ${card.round} · done`) : L('完成', 'Done'), t.good, Check];
  // 右上角的用时和用量是第一轮的：改过之后标题那一行留给「第 N 轮」
  const metaBits = card.round > 1 ? [] : [
    running ? L(`${minutes} 分钟`, `${minutes} min`) : null,
    !running && card.minutes ? L(`${card.minutes} 分钟`, `${card.minutes} min`) : null,
    !running && card.tokens ? `${fmtTokens(card.tokens)} tokens` : null,
  ].filter(Boolean);
  const result = card.round > 1 && card.roundResult ? card.roundResult : card.result;
  const preview = !running && result ? head(result) : null;
  const seq = card.seq ? L(`今天第 ${card.seq}${card.dailyLimit ? `/${card.dailyLimit}` : ''} 个`, `#${card.seq}${card.dailyLimit ? ` of ${card.dailyLimit}` : ''} today`) : '';
  const stop = () => {
    const go = () => { setStopping(true); cancelTask(card.id).catch((e) => showError(L('没停掉', "Couldn't stop it"), e)).finally(() => setStopping(false)); };
    if (!native) { go(); return; }
    Alert.alert(L('停掉这个任务？', 'Stop this task?'), L('做到一半的不会保留结果。', "Anything half-done won't be kept."), [
      { text: L('接着做', 'Keep going'), style: 'cancel' },
      { text: L('停掉', 'Stop'), style: 'destructive', onPress: go },
    ]);
  };

  return (
    <View style={[styles.card, { backgroundColor: t.surface, borderColor: running ? t.cyanSoft : t.line }]}>
      <View style={styles.row}>
        <View style={[styles.pill, { backgroundColor: t.cyanSoft }]}>
          <Send size={12} color={t.cyan} />
          <T v="caption" color={t.cyan} style={{ fontWeight: '600' }}>{L('后台任务', 'Background task')}</T>
        </View>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4, flexShrink: 1 }}>
          {running ? <Spinner color={statusColor} /> : StatusIcon ? <StatusIcon size={14} color={statusColor} /> : null}
          <T v="caption" color={statusColor} numberOfLines={1} style={{ fontSize: 13, fontWeight: '600' }}>{statusText}</T>
        </View>
        <View style={{ flex: 1 }} />
        {metaBits.length ? <T v="caption" color={t.ink3} style={{ fontVariant: ['tabular-nums'] }}>{metaBits.join(' · ')}</T> : null}
      </View>
      <T v="headline" style={{ fontSize: 16, fontWeight: '700', lineHeight: 22 }}>{card.title}</T>

      {card.round > 1 && card.note ? (
        <View style={[styles.note, { backgroundColor: t.goldSoft }]}>
          <Pencil size={13} color={t.gold} style={{ marginTop: 3 }} />
          <T v="callout" style={{ flex: 1 }}>{L(`你的意见：${card.note}`, `Your notes: ${card.note}`)}</T>
        </View>
      ) : null}

      {running && card.round === 1 && card.deliverable.length ? (
        <View style={[styles.box, { backgroundColor: t.bg, borderColor: t.line }]}>
          <T v="caption" color={t.ink3} style={{ fontWeight: '600' }}>{L('要交', 'Deliverable')}</T>
          {card.deliverable.map((d, i) => (
            <View key={`${i}-${d}`} style={styles.li}>
              <View style={[styles.dot, { backgroundColor: t.ink3 }]} />
              <T v="callout" style={{ flex: 1 }}>{d}</T>
            </View>
          ))}
        </View>
      ) : null}

      {running ? (
        <Pressable onPress={openDetail} accessibilityRole="button" accessibilityHint={L('看每一步', 'Shows every step')} style={({ pressed }) => [styles.row, { opacity: pressed ? 0.7 : 1 }]}>
          <View style={[styles.circle, { backgroundColor: t.cyanSoft }]}><FileText size={14} color={t.cyan} /></View>
          <View style={{ flex: 1, minWidth: 0, gap: 1 }}>
            <T v="callout" numberOfLines={1} style={{ fontWeight: '600' }}>
              {revising ? L('在按你的意见改', 'Working on your notes') : card.step || L('在想', 'Thinking')}
            </T>
            <T v="caption" color={t.ink3}>
              {revising ? L('它记得上一轮做了什么，只改你说的', 'It remembers the last round and only changes what you asked')
                : card.tools ? L(`第 ${card.tools} 步${card.startedAt ? ` · ${card.startedAt} 开始` : ''}`, `Step ${card.tools}${card.startedAt ? ` · started ${card.startedAt}` : ''}`)
                  : card.startedAt ? L(`${card.startedAt} 开始`, `Started ${card.startedAt}`) : ''}
            </T>
          </View>
          <T v="caption" color={t.gold} style={{ fontSize: 13, fontWeight: '600' }}>{L('看过程', 'Steps')}</T>
          <ChevronRight size={14} color={t.gold} />
        </Pressable>
      ) : null}

      {failed ? (
        <T v="callout" color={timedOut ? t.ink2 : t.bad}>
          {timedOut ? L(`做了 ${card.minutes} 分钟，到${card.limitMinutes ? ` ${card.limitMinutes} 分钟的` : ''}上限停了，没做完。`, `Stopped after ${card.minutes} min at the${card.limitMinutes ? ` ${card.limitMinutes}-minute` : ''} limit, unfinished.`)
            : card.error || L('子会话出错了。', 'The sub-session hit an error.')}
        </T>
      ) : null}

      {preview && preview.shown ? (
        <View style={[styles.result, { backgroundColor: t.bg, borderColor: t.line }]}>
          <Markdown text={preview.shown} compact small />
          {preview.more ? <T v="caption" color={t.ink3}>{L(`还有 ${preview.more} 行`, `${preview.more} more line${preview.more === 1 ? '' : 's'}`)}</T> : null}
        </View>
      ) : null}

      {running ? (
        <>
          <View style={[styles.hr, { backgroundColor: t.line }]} />
          <View style={styles.row}>
            <ModelChip id={card.modelId} />
            {seq ? <T v="caption" color={t.ink3} numberOfLines={1} style={{ flexShrink: 1 }}>{seq}</T> : null}
            <View style={{ flex: 1 }} />
            {!revising ? <SmallBtn label={L('停掉', 'Stop')} icon={Square} busy={stopping} onPress={stop} /> : null}
          </View>
        </>
      ) : (
        <>
          <View style={styles.row}>
            <SmallBtn label={timedOut ? L('接着做', 'Keep going') : L('改一下', 'Revise')} icon={Pencil} onPress={() => onRevise(card)} />
            <SmallBtn label={failed && !preview ? L('看过程', 'See steps') : L('看全文', 'Read it all')} icon={FileText} primary onPress={openDetail} />
          </View>
          <View style={styles.row}>
            <ModelChip id={card.modelId} />
            <T v="caption" color={t.ink3} numberOfLines={1} style={{ flexShrink: 1 }}>
              {[card.finishedAt && card.round === 1 ? L(`${card.finishedAt} 做完`, `finished ${card.finishedAt}`) : '', seq].filter(Boolean).join(' · ')}
            </T>
          </View>
        </>
      )}
    </View>
  );
}

// —— 日程卡 ——————————————————————————————————————————————————————

/** Agent 改了日程 / 要记得的：一行「训练 · Push A　17:30 → 18:00」，右边「撤销」（撤销过的是「恢复」）。 */
export function ScheduleChip({ card }: { card: ScheduleChangeCard }) {
  const t = useTheme();
  const { undoScheduleCard } = useStore();
  const [busy, setBusy] = useState(false);
  const undone = card.status === 'undone';
  const remember = card.area === 'remember';
  const Icon = remember ? ListChecks : CalendarDays;
  const press = () => {
    setBusy(true);
    undoScheduleCard(card).catch((e) => showError(undone ? L('没恢复成', "Couldn't redo") : L('没撤销成', "Couldn't undo"), e)).finally(() => setBusy(false));
  };
  const what = remember ? L('要记得的', 'To remember') : L('日程', 'Schedule');
  return (
    <View accessible={false} style={[styles.chip, { backgroundColor: t.surface, borderColor: t.line, opacity: undone ? 0.65 : 1 }]}>
      <View style={[styles.circle, { width: 30, height: 30, borderRadius: 9, backgroundColor: remember ? t.goldSoft : t.cyanSoft }]}>
        <Icon size={16} color={remember ? t.gold : t.cyan} />
      </View>
      <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
        <T v="headline" numberOfLines={1} style={{ fontSize: 14, fontWeight: '700' }}>{card.title}</T>
        <T v="callout" numberOfLines={2} color={t.ink2} style={{ fontSize: 13, lineHeight: 18 }}>
          {undone ? L(`${what} · 已撤销`, `${what} · undone`) : `${what} · ${card.summary}`}
        </T>
      </View>
      <Pressable onPress={press} disabled={busy} accessibilityRole="button" hitSlop={6}
        accessibilityLabel={undone ? L(`恢复：${card.title}`, `Redo: ${card.title}`) : L(`撤销：${card.title}`, `Undo: ${card.title}`)}
        style={({ pressed }) => [styles.undo, { backgroundColor: t.surface2, opacity: pressed || busy ? 0.6 : 1 }]}>
        <T v="callout" style={{ fontSize: 13, fontWeight: '600' }}>{undone ? L('恢复', 'Redo') : L('撤销', 'Undo')}</T>
      </Pressable>
    </View>
  );
}

// —— 课程改动卡（学习 Agent 改了课，server/courses.py）————————————————————————————————

/** 学习 Agent 在回复里改了课：几行改了什么（S5 读第 6 章；加了第 8 节……），右边「撤销」；有动到的那一节就能「打开这一节」。 */
export function CourseChip({ card }: { card: CourseChangeCard }) {
  const t = useTheme();
  const [status, setStatus] = useState(card.status);
  const [seen, setSeen] = useState(card.status);
  if (card.status !== seen) { setSeen(card.status); setStatus(card.status); }
  const [busy, setBusy] = useState(false);
  const undone = status === 'undone';
  const press = () => {
    setBusy(true);
    studyApi.undoChange(card.course, card.changeId, undone).then((r) => setStatus(r.card.status), (e) => showError(undone ? L('没恢复成', "Couldn't redo") : L('没撤销成', "Couldn't undo"), e)).finally(() => setBusy(false));
  };
  const open = card.session ? () => openTarget({ type: 'study', course: card.course, page: card.session?.page ?? null, session: card.session?.session ?? null }) : undefined;
  return (
    <View accessible={false} style={[styles.chip, { backgroundColor: t.surface, borderColor: t.line, opacity: undone ? 0.65 : 1, alignItems: 'flex-start' }]}>
      <View style={[styles.circle, { width: 30, height: 30, borderRadius: 9, backgroundColor: t.cyanSoft }]}>
        <BookOpen size={16} color={t.cyan} />
      </View>
      <View style={{ flex: 1, minWidth: 0, gap: 3 }}>
        <T v="headline" numberOfLines={1} style={{ fontSize: 14, fontWeight: '700' }}>{undone ? L(`撤销了 · ${card.courseTitle}`, `Undone · ${card.courseTitle}`) : L(`改了「${card.courseTitle}」的课程结构`, `Changed ${card.courseTitle}`)}</T>
        {(card.lines.length ? card.lines : [card.summary]).slice(0, 5).map((l, i) => (
          <T key={i} v="callout" color={t.ink2} numberOfLines={2} style={{ fontSize: 13, lineHeight: 18, textDecorationLine: undone ? 'line-through' : 'none' }}>{`· ${l}`}</T>
        ))}
        <T v="caption" color={t.ink3}>{undone ? L('今天页的日程和学习台也改回去了。', 'Today and the study desk are back as they were.') : L('今天页的日程、学习台和截止表跟着改了。', 'Today, the study desk and the deadline table follow.')}</T>
        {open ? (
          <Pressable onPress={open} accessibilityRole="button" hitSlop={6}>
            <T v="callout" color={t.gold} style={{ fontSize: 13, fontWeight: '600' }}>{L('打开这一节', 'Open this session')}</T>
          </Pressable>
        ) : null}
      </View>
      <Pressable onPress={press} disabled={busy} accessibilityRole="button" hitSlop={6}
        accessibilityLabel={undone ? L(`重做：${card.title}`, `Redo: ${card.title}`) : L(`撤销：${card.title}`, `Undo: ${card.title}`)}
        style={({ pressed }) => [styles.undo, { backgroundColor: t.surface2, opacity: pressed || busy ? 0.6 : 1 }]}>
        <T v="callout" style={{ fontSize: 13, fontWeight: '600' }}>{undone ? L('重做', 'Redo') : L('撤销', 'Undo')}</T>
      </Pressable>
    </View>
  );
}

// —— 项目小卡 ————————————————————————————————————————————————————————

/** Agent 改了项目卡：一行「已定的 +1 · 预算那段」，右边「撤销」；开了项目的是「去看看」。在别的对话里（主对话改了某个项目）点整张进那个项目。 */
export function ProjectChip({ card, here }: { card: ProjectChangeCard; here: string }) {
  const t = useTheme();
  const { undoProjectCard } = useStore();
  const [busy, setBusy] = useState(false);
  const undone = card.status === 'undone';
  const away = card.project !== here;
  const go = () => openThread(card.project, false);
  const press = () => {
    if (card.action === 'create' || !card.undoable) { go(); return; }
    setBusy(true);
    undoProjectCard(card).catch((e) => showError(undone ? L('没恢复成', "Couldn't redo") : L('没撤销成', "Couldn't undo"), e)).finally(() => setBusy(false));
  };
  const what = away && card.projectTitle ? card.projectTitle : L('项目卡', 'Project card');
  const btn = card.action === 'create' || !card.undoable ? L('去看看', 'Open') : undone ? L('恢复', 'Redo') : L('撤销', 'Undo');
  return (
    <Pressable onPress={away ? go : undefined} disabled={!away} accessibilityRole={away ? 'button' : undefined} accessibilityLabel={away ? L(`打开项目：${card.projectTitle}`, `Open project: ${card.projectTitle}`) : undefined}
      style={({ pressed }) => [styles.chip, { backgroundColor: t.surface, borderColor: t.line, opacity: undone ? 0.65 : pressed ? 0.8 : 1 }]}>
      <View style={[styles.circle, { width: 30, height: 30, borderRadius: 9, backgroundColor: t.cyanSoft }]}>
        <FolderKanban size={16} color={t.cyan} />
      </View>
      <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
        <T v="headline" numberOfLines={1} style={{ fontSize: 14, fontWeight: '700' }}>{card.title}</T>
        <T v="callout" numberOfLines={2} color={t.ink2} style={{ fontSize: 13, lineHeight: 18 }}>
          {undone ? L(`${what} · 已撤销`, `${what} · undone`) : `${what} · ${card.summary}`}
        </T>
      </View>
      {card.action === 'create' && !away ? null : (
        <Pressable onPress={press} disabled={busy} accessibilityRole="button" hitSlop={6} accessibilityLabel={`${btn}：${card.title}`}
          style={({ pressed }) => [styles.undo, { backgroundColor: t.surface2, opacity: pressed || busy ? 0.6 : 1 }]}>
          <T v="callout" style={{ fontSize: 13, fontWeight: '600' }}>{btn}</T>
        </Pressable>
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  undo: { height: 30, paddingHorizontal: 10, borderRadius: 9, alignItems: 'center', justifyContent: 'center' },
  chip: { flexDirection: 'row', alignItems: 'center', gap: 10, borderRadius: 14, borderWidth: StyleSheet.hairlineWidth, paddingVertical: 9, paddingLeft: 9, paddingRight: 10 },
  from: { alignSelf: 'center', maxWidth: '92%', minWidth: '70%', gap: 4, borderRadius: 14, borderWidth: 1.5, paddingVertical: 10, paddingHorizontal: 14 },
  card: { borderRadius: 16, borderWidth: StyleSheet.hairlineWidth, padding: 14, gap: 10 },
  row: { flexDirection: 'row', alignItems: 'center', gap: space.sm },
  pill: { flexDirection: 'row', alignItems: 'center', gap: 4, height: 22, paddingHorizontal: 9, borderRadius: 11 },
  note: { flexDirection: 'row', gap: 6, alignItems: 'flex-start', borderRadius: 10, paddingVertical: 8, paddingHorizontal: 10 },
  box: { borderWidth: 1, borderRadius: radius.md, paddingVertical: 10, paddingHorizontal: space.md, gap: 6 },
  li: { flexDirection: 'row', gap: space.sm },
  dot: { width: 5, height: 5, borderRadius: 3, marginTop: 8 },
  circle: { width: 26, height: 26, borderRadius: 13, alignItems: 'center', justifyContent: 'center' },
  result: { borderWidth: 1, borderRadius: radius.md, paddingVertical: 8, paddingHorizontal: space.md, gap: 4 },
  hr: { height: StyleSheet.hairlineWidth },
  model: { flexDirection: 'row', alignItems: 'center', gap: 5, height: 24, paddingHorizontal: 9, borderRadius: 12 },
  btn: { flexDirection: 'row', gap: 6, alignItems: 'center', justifyContent: 'center', borderRadius: radius.md, paddingHorizontal: 14, height: 40 },
  rcpt: { flexDirection: 'row', alignItems: 'center', gap: space.md, borderRadius: 14, borderWidth: StyleSheet.hairlineWidth, paddingVertical: space.md, paddingHorizontal: 14 },
});
