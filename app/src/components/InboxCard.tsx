// 收件箱的一张卡：Agent（或者主对话）要你点头的一件事。「今天」页上有，对话里也跟在提它的那条消息下面。
// 同意 → 卡片收成一行回执，Agent 去做，做完回执变成结果；不要 → 记下来，同样的事不再提。
// 想改：「今天」页上点「有要改的？去对话里说」，带着这件事的引用回到那个对话，直接说；对话里就直接打字。
// 执行命令（OpenClaw 的审批）沿用「拒绝 / 这一次同意」：同意只放行这一次，不会变成长期授权。
import React, { useState } from 'react';
import { ActivityIndicator, Platform, Pressable, StyleSheet, Text, View } from 'react-native';
import type { InboxAction, InboxItem, InboxKind, InboxProposalInfo, InboxStatus } from '../data/types';
import { L } from '../i18n';
import { openThread } from '../navigation';
import { useStore } from '../store';
import { radius, space, type, useTheme } from '../theme';
import { Check, ChevronRight, CircleAlert, Flag, LoaderCircle, Pencil, Pin, Target, X } from './icons';
import { Markdown } from './Markdown';
import { SourceBadge, useSourceName } from './SourceBadge';
import { Pill, T, showError } from './ui';
import { AlertPreview, BoardPreview } from './blocks/BoardPreview';

// —— 文字（都是函数：L() 要在用的时候按当前语言取） ——

export const kindLabel = (k: InboxKind | string): string => ({
  exec: L('执行命令', 'Run command'), task: L('派活', 'Task'), write: L('写入', 'Write'), send: L('发送', 'Send'), spend: L('花钱', 'Spend'),
  schedule: L('定时任务', 'Schedule'), push: L('推送', 'Notification'), skill: L('新 skill', 'New skill'), agent: L('新 Agent', 'New agent'),
  block: L('看板功能块', 'Board block'), project: L('项目', 'Project'), code: L('代码改动', 'Code change'), calendar: L('日程', 'Calendar'), other: L('其他', 'Other'),
} as Record<string, string>)[k] ?? L('其他', 'Other');

/** 动到外面的（写、发、花钱、日程、派活）用警示色；提案类用金色（助手自己想做的）；执行命令中性。 */
const kindTone = (k: InboxKind): 'warn' | 'gold' | 'neutral' =>
  (['write', 'send', 'spend', 'calendar', 'task'].includes(k) ? 'warn' : k === 'exec' ? 'neutral' : 'gold');

const pad = (n: number) => String(n).padStart(2, '0');
/** 刚刚 / 5 分钟前 / 14:05 / 昨天 14:05 / 9月24日 */
export function relTime(iso: string | null | undefined): string {
  const ms = iso ? Date.parse(iso) : NaN;
  if (Number.isNaN(ms)) return '';
  const diff = Date.now() - ms;
  if (diff < 60_000) return L('刚刚', 'just now');
  if (diff < 3_600_000) { const m = Math.floor(diff / 60_000); return L(`${m} 分钟前`, `${m} min ago`); }
  const d = new Date(ms);
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const day = (x: Date) => x.toLocaleDateString('en-CA');
  const yest = new Date(); yest.setDate(yest.getDate() - 1);
  if (day(d) === day(new Date())) return hm;
  if (day(d) === day(yest)) return L(`昨天 ${hm}`, `Yesterday ${hm}`);
  return d.toLocaleDateString(L('zh-CN', 'en'), { month: 'short', day: 'numeric' });
}
/** 只要钟点（「已处理」列表右边）。 */
export function clock(iso: string | null | undefined): string {
  const ms = iso ? Date.parse(iso) : NaN;
  if (Number.isNaN(ms)) return '';
  const d = new Date(ms);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** 中文里名字是英文（比如 Grava）时，和汉字之间空一格。 */
const zh = (name: string, rest: string) => (/[A-Za-z0-9]$/.test(name) ? `${name} ${rest}` : `${name}${rest}`);

/** 回执两行：「已同意 · 标题」+ 说明。 */
export function receiptText(item: InboxItem, name: string): { head: string; sub: string } {
  const exec = item.kind === 'exec';
  const title = item.title;
  switch (item.status) {
    case 'approved':
      return exec
        ? { head: L(`这一次同意 · ${title}`, `Allowed once · ${title}`), sub: L(zh(name, '接着往下做了'), `${name} is carrying on`) }
        : { head: L(`已同意 · ${title}`, `Approved · ${title}`), sub: L(zh(name, '在做，做完这里会变成结果'), `${name} is on it. The result will show up here.`) };
    case 'done':
      return { head: L(`做完了 · ${title}`, `Done · ${title}`), sub: item.result || L(zh(name, '做完了'), `${name} finished it`) };
    case 'failed':
      return { head: L(`没做成 · ${title}`, `Didn't work · ${title}`), sub: item.result || L(zh(name, '没做成'), `${name} couldn't do it`) };
    case 'rejected':
      return exec
        ? { head: L(`已拒绝 · ${title}`, `Denied · ${title}`), sub: L(zh(name, '会换个办法，或者直接问你'), `${name} will try another way or ask you`) }
        : { head: L(`没要 · ${title}`, `Declined · ${title}`), sub: L('记下了，同样的事不会再提', "Noted. It won't come up again.") };
    case 'revising':
      return { head: L(`改一下 · ${title}`, `Changes asked · ${title}`), sub: L(zh(name, '改好会再交回来'), `${name} will bring it back once it's changed`) };
    case 'withdrawn':
      return { head: L(`撤回了 · ${title}`, `Withdrawn · ${title}`), sub: L(zh(name, '不需要了'), `${name} no longer needs it`) };
    case 'expired':
      return { head: L(`过期了 · ${title}`, `Expired · ${title}`), sub: L('等太久没处理，作废了', 'It waited too long and lapsed') };
    default:
      return { head: title, sub: L('等你点头', 'Waiting for your OK') };
  }
}

/** 「已处理」列表每一行的第二行：「来源 · 状态：结果」。 */
export function handledText(item: InboxItem, name: string): string {
  const sep = L('：', ': ');
  const exec = item.kind === 'exec';
  const extra = (s: string) => (s ? `${sep}${s}` : '');
  switch (item.status) {
    case 'done': return `${name} · ${L('完成', 'Done')}${extra(item.result)}`;
    case 'failed': return `${name} · ${L('没做成', 'Failed')}${extra(item.result)}`;
    case 'approved': return exec ? `${name} · ${L('这一次同意', 'Allowed once')}${extra(item.result)}` : `${name} · ${L('进行中', 'In progress')}${extra(item.result)}`;
    case 'revising': return `${name} · ${L('你让它改', 'You asked for changes')}${extra(item.note)}${L('。改好会再交回来', '. It will come back once changed')}`;
    case 'rejected': return `${name} · ${exec ? L('已拒绝', 'Denied') : L('没要，不会再提', "Declined, won't come up again")}${extra(item.result)}`;
    case 'withdrawn': return `${name} · ${L('撤回了', 'Withdrawn')}${extra(item.result)}`;
    case 'expired': return `${name} · ${L('过期了', 'Expired')}${extra(item.result)}`;
    default: return `${name} · ${L('等你点头', 'Waiting for your OK')}`;
  }
}

/** 状态圆点：做完 = 绿勾；在做 = 青色转圈；改一下 = 金色笔；没做成 = 红色感叹号；没要 / 撤回 / 过期 = 灰叉。执行命令同意了就算做了。 */
export function StatusCircle({ status, exec, size = 28 }: { status: InboxStatus; exec?: boolean; size?: number }) {
  const t = useTheme();
  const [bg, fg, Icon] =
    status === 'done' || (status === 'approved' && exec) ? [t.goodSoft, t.good, Check]
      : status === 'approved' ? [t.cyanSoft, t.cyan, LoaderCircle]
        : status === 'revising' || status === 'pending' ? [t.goldSoft, t.gold, Pencil]
          : status === 'failed' ? [t.badSoft, t.bad, CircleAlert]
            : [t.surface2, t.ink2, X];
  return (
    <View style={{ width: size, height: size, borderRadius: size / 2, backgroundColor: bg, alignItems: 'center', justifyContent: 'center' }}>
      <Icon size={Math.round(size * 0.55)} color={fg} />
    </View>
  );
}

// —— 按钮 ——

function CardBtn({ label, kind, icon: Icon, busy, disabled, onPress }: {
  label: string; kind: 'primary' | 'quiet'; icon?: typeof Check; busy?: boolean; disabled?: boolean; onPress: () => void;
}) {
  const t = useTheme();
  const primary = kind === 'primary';
  const fg = primary ? t.onGold : t.ink;
  return (
    <Pressable onPress={onPress} disabled={disabled} accessibilityRole="button" accessibilityLabel={label} accessibilityState={{ disabled: !!disabled, busy: !!busy }}
      style={({ pressed }) => [styles.btn, { backgroundColor: primary ? t.goldFill : t.surface2, opacity: disabled && !busy ? 0.5 : pressed ? 0.75 : 1 }, primary && { flexGrow: 1, flexShrink: 1, minWidth: 0 }]}>
      {busy ? <ActivityIndicator size="small" color={fg} /> : Icon ? <Icon size={18} color={fg} /> : null}
      <Text numberOfLines={1} style={[type.headline, { fontSize: 15, color: fg, flexShrink: 1 }]}>{label}</Text>
    </Pressable>
  );
}

// —— 卡片 ——

/**
 * 等你点头的一张卡；处理过的（status 不是 pending）显示成一行回执。
 * variant = chat：对话里用的紧凑版，不要来源那一行（对话本身就说明是谁），也不要「去对话里说」（已经在对话里了，直接打字）。
 */
export function InboxCard({ item, variant = 'today' }: { item: InboxItem; variant?: 'today' | 'chat' }) {
  return item.status === 'pending' ? <PendingCard item={item} chat={variant === 'chat'} /> : <InboxReceipt item={item} chat={variant === 'chat'} />;
}

function PendingCard({ item, chat }: { item: InboxItem; chat: boolean }) {
  const t = useTheme();
  const { decide, groups } = useStore();
  const nameOf = useSourceName();
  const name = item.sourceName || nameOf(item.source);
  const [busy, setBusy] = useState<InboxAction | null>(null);
  const [more, setMore] = useState(false);
  const [alertShown, setAlertShown] = useState(false);  // 提醒卡画出了通知预览：detail 是同一句的文字版，不再显示
  const exec = item.kind === 'exec';
  const act = (action: InboxAction) => {
    if (busy) return;
    setBusy(action);
    // 成功后 store 把这张卡换成回执（「今天」和对话里同时）
    decide(item.id, action).catch((e) => showError(L('没做成', "Didn't go through"), e)).finally(() => setBusy(null));
  };
  // 有要改的：回到提这件事的对话，输入框上面带着「回复：标题」，直接说
  const talk = () => openThread(item.thread, groups.some((g) => g.id === item.thread), { inboxId: item.id, title: item.title });
  const fields = item.fields ?? [];
  // 字段名一列对齐：按最长的那个估宽度（汉字约 13pt，其余约 8pt）
  const keyW = Math.min(96, Math.max(28, ...fields.map((f) => [...f.k].reduce((w, ch) => w + (/[　-鿿]/.test(ch) ? 13 : 8), 0))));
  return (
    <View style={[styles.card, chat && styles.chatCard, { backgroundColor: t.surface, borderColor: t.line, opacity: busy ? 0.75 : 1 }]}>
      <View style={styles.head}>
        {chat ? null : <SourceBadge source={item.source} size={28} />}
        {chat ? null : <T v="caption" color={t.ink2} numberOfLines={1} style={{ fontSize: 13, fontWeight: '600', flexShrink: 1 }}>{name}</T>}
        <Pill label={kindLabel(item.kind)} tone={kindTone(item.kind)} />
        <View style={{ flex: 1 }} />
        <T v="caption" color={t.ink3}>{relTime(item.createdAt) || item.whenText || ''}</T>
      </View>
      <T v="headline" style={chat ? styles.chatTitle : styles.title}>{item.title}</T>
      {item.why ? <T v="callout" color={t.ink2}>{item.why}</T> : null}
      {item.skill || item.agent ? <ProposalPreview info={(item.skill ?? item.agent) as InboxProposalInfo} kind={item.kind} /> : null}
      {item.kind === 'block' ? <BoardPreview inboxId={item.id} />
        : item.kind === 'agent' ? <BoardPreview inboxId={item.id} plan />
          : item.kind === 'push' ? <AlertPreview inboxId={item.id} source={name} onShown={setAlertShown} /> : null}
      {item.kind === 'project' && item.project?.action === 'open' ? <ProjectPreview info={item.project} /> : null}
      {item.detail && !(item.kind === 'project' && item.project?.action === 'open') && !(item.kind === 'push' && alertShown) ? (
        <>
          {more ? <Markdown text={item.detail} color={t.ink2} compact /> : null}
          <Pressable onPress={() => setMore((v) => !v)} hitSlop={8} accessibilityRole="button" accessibilityState={{ expanded: more }} style={{ alignSelf: 'flex-start' }}>
            <T v="caption" color={t.gold} style={{ fontWeight: '600', fontSize: 13 }}>{more ? L('收起', 'Show less') : L('展开', 'Show more')}</T>
          </Pressable>
        </>
      ) : null}
      {item.changes.length ? (
        <View style={[styles.box, { backgroundColor: t.bg, borderColor: t.line }]}>
          <T v="caption" color={t.ink3} style={{ fontWeight: '600' }}>{L('会改什么', 'What changes')}</T>
          {item.changes.map((c, i) => (
            <View key={`${i}-${c}`} style={styles.li}>
              <View style={[styles.dot, { backgroundColor: t.ink3 }]} />
              <T v="callout" style={{ flex: 1 }}>{c}</T>
            </View>
          ))}
        </View>
      ) : null}
      {fields.length ? (
        <View style={[styles.box, { backgroundColor: t.bg, borderColor: t.line, gap: 4 }]}>
          {fields.map((f, i) => (
            <View key={`${i}-${f.k}`} style={{ flexDirection: 'row', gap: 10 }}>
              <Text style={[styles.mono, { color: t.ink3, width: keyW }]}>{f.k}</Text>
              <Text selectable style={[styles.mono, { color: t.ink, flex: 1 }]}>{f.v}</Text>
            </View>
          ))}
        </View>
      ) : null}
      {!exec && !chat && item.thread ? (
        <Pressable onPress={talk} disabled={!!busy} hitSlop={6} accessibilityRole="button" style={({ pressed }) => [styles.link, { opacity: pressed ? 0.6 : 1 }]}>
          <T v="callout" color={t.gold} style={{ fontWeight: '600' }}>{L('有要改的？去对话里说', 'Want changes? Say it in chat')}</T>
          <ChevronRight size={16} color={t.gold} />
        </Pressable>
      ) : null}
      <View style={styles.actions}>
        {exec ? (
          <>
            <CardBtn kind="quiet" label={L('拒绝', 'Deny')} busy={busy === 'reject'} disabled={!!busy} onPress={() => act('reject')} />
            <CardBtn kind="primary" label={L('这一次同意', 'Allow once')} busy={busy === 'approve'} disabled={!!busy} onPress={() => act('approve')} />
          </>
        ) : (
          <>
            <CardBtn kind="quiet" label={L('不要', 'No')} busy={busy === 'reject'} disabled={!!busy} onPress={() => act('reject')} />
            <CardBtn kind="primary" label={item.approveLabel || L('同意', 'Approve')} icon={Check} busy={busy === 'approve'} disabled={!!busy} onPress={() => act('approve')} />
          </>
        )}
      </View>
    </View>
  );
}

/** 处理过的：一行回执（状态圆点 + 「已同意 · 标题」+ 说明 / 结果）。开好了的项目右边是「去看看」。 */
function InboxReceipt({ item, chat }: { item: InboxItem; chat: boolean }) {
  const t = useTheme();
  const nameOf = useSourceName();
  const name = item.sourceName || nameOf(item.source);
  const r = receiptText(item, name);
  const project = item.kind === 'project' && item.project?.action === 'open' && item.status === 'done' ? item.project.project : undefined;
  return (
    <View style={[styles.rcpt, chat && styles.chatRcpt, { backgroundColor: t.surface, borderColor: t.line }]} accessible={!project} accessibilityLabel={`${r.head}${L('，', ', ')}${r.sub}`}>
      <StatusCircle status={item.status} exec={item.kind === 'exec'} />
      <View style={{ flex: 1, gap: 2 }}>
        <T v="headline" numberOfLines={1} style={{ fontSize: 15 }}>{r.head}</T>
        {r.sub ? <T v="callout" color={t.ink2} numberOfLines={chat ? 3 : 2} style={{ fontSize: 13, lineHeight: 18 }}>{r.sub}</T> : null}
      </View>
      {project ? (
        <Pressable onPress={() => openThread(project.id, false)} hitSlop={8} accessibilityRole="button" accessibilityLabel={L(`去看看：${project.title}`, `Open ${project.title}`)}
          style={({ pressed }) => [{ flexDirection: 'row', alignItems: 'center', gap: 2, opacity: pressed ? 0.6 : 1 }]}>
          <T v="callout" color={t.gold} style={{ fontWeight: '600' }}>{L('去看看', 'Open')}</T>
          <ChevronRight size={15} color={t.gold} />
        </Pressable>
      ) : <T v="caption" color={t.ink3}>{relTime(item.decidedAt)}</T>}
    </View>
  );
}

/** 日结提案：它是从哪几次对话里看出来的（原话，默认三条），skill 的全文点开才看。 */
function ProposalPreview({ info, kind }: { info: InboxProposalInfo; kind: InboxKind }) {
  const t = useTheme();
  const [allQuotes, setAllQuotes] = useState(false);
  const [full, setFull] = useState(false);
  const quotes = allQuotes ? info.evidence : info.evidence.slice(0, 3);
  const more = info.evidence.length - 3;
  return (
    <>
      {quotes.length ? (
        <View style={[styles.box, { backgroundColor: t.bg, borderColor: t.line }]}>
          <T v="caption" color={t.ink3} style={{ fontWeight: '600' }}>{L('这几次你是这么说的', 'What you said')}</T>
          {quotes.map((e, i) => (
            <View key={`${i}-${e.quote}`} style={styles.quote}>
              <T v="callout" style={{ lineHeight: 20 }}>{L(`「${e.quote}」`, `"${e.quote}"`)}</T>
              {e.date || e.thread ? <T v="caption" color={t.ink3}>{[e.date, e.thread].filter(Boolean).join(' · ')}</T> : null}
            </View>
          ))}
          {more > 0 ? (
            <Pressable onPress={() => setAllQuotes((v) => !v)} hitSlop={8} accessibilityRole="button" accessibilityState={{ expanded: allQuotes }} style={{ alignSelf: 'flex-start' }}>
              <T v="caption" color={t.gold} style={{ fontWeight: '600', fontSize: 13 }}>{allQuotes ? L('收起', 'Show less') : L(`还有 ${more} 次`, `${more} more`)}</T>
            </Pressable>
          ) : null}
        </View>
      ) : null}
      {kind === 'skill' && info.markdown ? (
        <>
          {full ? (
            <View style={[styles.box, { backgroundColor: t.bg, borderColor: t.line }]}>
              <Markdown text={info.markdown} color={t.ink2} compact />
            </View>
          ) : null}
          <Pressable onPress={() => setFull((v) => !v)} hitSlop={8} accessibilityRole="button" accessibilityState={{ expanded: full }} style={{ alignSelf: 'flex-start' }}>
            <T v="caption" color={t.gold} style={{ fontWeight: '600', fontSize: 13 }}>{full ? L('收起', 'Show less') : L('看这套做法的全文', 'Read the full steps')}</T>
          </Pressable>
        </>
      ) : null}
    </>
  );
}

/** 开项目的提案：一张小项目卡——目标、截止、已定的、下一步。 */
function ProjectPreview({ info }: { info: NonNullable<InboxItem['project']> }) {
  const t = useTheme();
  const rows: { key: string; icon: React.ReactNode; bg: string; text: string }[] = [];
  if (info.goal) rows.push({ key: 'goal', icon: <Target size={14} color={t.ink2} />, bg: t.surface2, text: info.goal });
  if (info.deadlines?.length) rows.push({ key: 'due', icon: <Flag size={14} color={t.warn} />, bg: t.warnSoft, text: info.deadlines.join('\n') });
  if (info.decisions?.length) rows.push({ key: 'dec', icon: <Pin size={14} color={t.cyan} />, bg: t.cyanSoft, text: info.decisions.join('\n') });
  if (info.steps?.length) rows.push({ key: 'step', icon: <Check size={14} color={t.ink2} />, bg: t.surface2, text: info.steps.join('\n') });
  if (!rows.length) return null;
  return (
    <View style={[styles.box, { backgroundColor: t.bg, borderColor: t.line, paddingVertical: 4, gap: 0 }]}>
      {rows.map((r, i) => (
        <View key={r.key} style={[styles.prev, i > 0 && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }]}>
          <View style={[styles.prevIcon, { backgroundColor: r.bg }]}>{r.icon}</View>
          <T v="callout" style={{ flex: 1, lineHeight: 20 }}>{r.text}</T>
        </View>
      ))}
    </View>
  );
}

const mono = Platform.select({ ios: 'Menlo', android: 'monospace', default: 'ui-monospace, SFMono-Regular, Menlo, monospace' });

const styles = StyleSheet.create({
  card: { borderRadius: radius.lg, padding: space.lg, gap: 10 },
  // 对话里：紧一点，加一道细边和背景里的消息分开
  chatCard: { padding: 14, gap: 8, borderWidth: StyleSheet.hairlineWidth },
  chatRcpt: { borderWidth: StyleSheet.hairlineWidth },
  head: { flexDirection: 'row', alignItems: 'center', gap: space.sm },
  title: { fontSize: 17, fontWeight: '700', lineHeight: 23 },
  chatTitle: { fontSize: 16, fontWeight: '700', lineHeight: 22 },
  link: { flexDirection: 'row', alignItems: 'center', gap: 2, alignSelf: 'flex-start', paddingVertical: 2 },
  box: { borderWidth: 1, borderRadius: radius.md, paddingVertical: 10, paddingHorizontal: space.md, gap: 6 },
  li: { flexDirection: 'row', gap: space.sm },
  dot: { width: 5, height: 5, borderRadius: 3, marginTop: 8 },
  mono: { fontFamily: mono, fontSize: 13, lineHeight: 19 },
  actions: { flexDirection: 'row', gap: space.sm, paddingTop: 2 },
  btn: { flexDirection: 'row', gap: 6, alignItems: 'center', justifyContent: 'center', borderRadius: radius.md, paddingHorizontal: space.lg, height: 44 },
  rcpt: { flexDirection: 'row', alignItems: 'center', gap: space.md, borderRadius: 14, paddingVertical: space.md, paddingHorizontal: 14 },
  prev: { flexDirection: 'row', alignItems: 'flex-start', gap: 10, paddingVertical: 8 },
  quote: { gap: 1, paddingVertical: 2 },
  prevIcon: { width: 24, height: 24, borderRadius: 8, alignItems: 'center', justifyContent: 'center' },
});
