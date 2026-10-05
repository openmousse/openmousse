// 收件箱的一张卡：Agent（或者主对话）要你点头的一件事。「今天」页上有，对话里也跟在提它的那条消息下面。
// 同意 → 卡片收成一行回执，Agent 去做，做完回执变成结果；不要 → 记下来，同样的事不再提。
// 想改：「今天」页上点「有要改的？去对话里说」，带着这件事的引用回到那个对话，直接说；对话里就直接打字。
// 执行命令（OpenClaw 的审批）沿用「拒绝 / 这一次同意」：同意只放行这一次，不会变成长期授权。
// 处理过的（回执、「已处理」列表的一行）点开是详情：做了什么、为什么、结果、时间；没做完可以「跟进」，带着这件事回到那个对话接着说。
import React, { useCallback, useState } from 'react';
import { ActivityIndicator, Platform, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import type { InboxAction, InboxItem, InboxKind, InboxProposalInfo, InboxStatus } from '../data/types';
import { L } from '../i18n';
import { navigationRef, openThread } from '../navigation';
import { useStore } from '../store';
import { radius, space, type, useTheme } from '../theme';
import { Check, ChevronRight, CircleAlert, Flag, LoaderCircle, MessageCircle, Pencil, Pin, Target, X } from './icons';
import { Markdown } from './Markdown';
import { useSheet } from './Sheet';
import { SourceBadge, useSourceName } from './SourceBadge';
import { Pill, T, showError } from './ui';
import { AlertPreview, BoardPreview } from './blocks/BoardPreview';

// —— 文字（都是函数：L() 要在用的时候按当前语言取） ——

export const kindLabel = (k: InboxKind | string): string => ({
  exec: L('执行命令', 'Run command'), task: L('后台任务', 'Task'), write: L('写入', 'Write'), send: L('发送', 'Send'), spend: L('支出', 'Spend'),
  schedule: L('定时任务', 'Schedule'), push: L('推送', 'Notification'), skill: L('新 skill', 'New skill'), agent: L('新 Agent', 'New Agent'),
  block: L('看板功能块', 'Board block'), project: L('项目', 'Project'), code: L('代码改动', 'Code change'), calendar: L('日程', 'Calendar'),
  social: L('好友', 'Friends'), egress: L('代办', 'Errand'), app: L('连接器', 'Connector'), other: L('其他', 'Other'),
} as Record<string, string>)[k] ?? L('其他', 'Other');

/** 动到外面的（写、发、花钱、日程、派活）用警示色；提案类用金色（助手自己想做的）；执行命令中性。 */
const kindTone = (k: InboxKind): 'warn' | 'gold' | 'neutral' =>
  (['write', 'send', 'spend', 'calendar', 'task', 'social', 'egress', 'app'].includes(k) ? 'warn' : k === 'exec' ? 'neutral' : 'gold');

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
  if (item.kind === 'social' && item.social?.ask === 'review') {
    // Doorman 扣下的一句（server/cardagent.py 的 release）：点了就已经送到对方那里
    const peer = item.social.peer;
    if (item.status === 'rejected') return { head: L(`未发送 · ${title}`, `Not sent · ${title}`), sub: L(`已告知 ${peer}「无法回答」`, `Told ${peer} it can't be answered`) };
    if (item.status === 'done' || item.status === 'approved') {
      return { head: L(`${item.note ? '已发送修改版' : '已原样发送'} · ${title}`, `${item.note ? 'Sent your words' : 'Sent as is'} · ${title}`), sub: item.result || '' };
    }
    if (item.status === 'failed') return { head: L(`未送达 · ${title}`, `Not delivered · ${title}`), sub: item.result || '' };
  }
  if (item.kind === 'social') {
    // 名片 agent 的卡：点了就已经告诉对方了（server/cardagent.py 的 decide）
    if (item.status === 'revising') return { head: L(`另约时间 · ${title}`, `Another time · ${title}`), sub: L('已告知对方，等待对方重新提议', 'Told them; waiting for another suggestion') };
    if (item.status === 'rejected') return { head: L(`不参加 · ${title}`, `Not going · ${title}`), sub: item.result || L('已告知对方，未说明原因', 'Told them, no reason given') };
  }
  switch (item.status) {
    case 'approved':
      return exec
        ? { head: L(`已允许一次 · ${title}`, `Allowed once · ${title}`), sub: L(zh(name, '已继续执行'), `${name} is carrying on`) }
        : item.followedAt
          ? { head: L(`已跟进 · ${title}`, `Followed up · ${title}`), sub: L(zh(name, '正在继续处理，完成后此处显示结果'), `${name} is back on it. The result will show up here.`) }
          : { head: L(`已同意 · ${title}`, `Approved · ${title}`), sub: L(zh(name, '正在处理，完成后此处显示结果'), `${name} is on it. The result will show up here.`) };
    case 'done':
      return { head: L(`已完成 · ${title}`, `Done · ${title}`), sub: item.result || L(zh(name, '已完成'), `${name} finished it`) };
    case 'failed':
      return { head: L(`失败 · ${title}`, `Failed · ${title}`), sub: item.result || L(zh(name, '未能完成'), `${name} couldn't complete it`) };
    case 'rejected':
      return exec
        ? { head: L(`已拒绝 · ${title}`, `Denied · ${title}`), sub: L(zh(name, '将换一种方式，或直接询问你'), `${name} will try another way or ask you`) }
        : { head: L(`已拒绝 · ${title}`, `Declined · ${title}`), sub: L('已记录，不会再提出同类事项', "Noted. It won't come up again.") };
    case 'revising':
      return { head: L(`待修改 · ${title}`, `Changes requested · ${title}`), sub: L(zh(name, '修改后会重新提交'), `${name} will resubmit it once it's changed`) };
    case 'withdrawn':
      return { head: L(`已撤回 · ${title}`, `Withdrawn · ${title}`), sub: L(zh(name, '已不再需要'), `${name} no longer needs it`) };
    case 'expired':
      return { head: L(`已过期 · ${title}`, `Expired · ${title}`), sub: L('长时间未处理，已失效', 'It waited too long and lapsed') };
    default:
      return { head: title, sub: L('等待你确认', 'Waiting for your approval') };
  }
}

/** 「已处理」列表每一行的第二行：「来源 · 状态：结果」。 */
export function handledText(item: InboxItem, name: string): string {
  const sep = L('：', ': ');
  const exec = item.kind === 'exec';
  const extra = (s: string) => (s ? `${sep}${s}` : '');
  switch (item.status) {
    case 'done': return `${name} · ${L('完成', 'Done')}${extra(item.result)}`;
    case 'failed': return `${name} · ${L('失败', 'Failed')}${extra(item.result)}`;
    case 'approved': return exec ? `${name} · ${L('已允许一次', 'Allowed once')}${extra(item.result)}`
      : item.followedAt ? `${name} · ${L('已跟进，处理中', 'Followed up, in progress')}${extra(item.followNote ?? '')}`
        : `${name} · ${L('进行中', 'In progress')}${extra(item.result)}`;
    case 'revising': return `${name} · ${L('已要求修改', 'You asked for changes')}${extra(item.note)}${L('。修改后会重新提交', '. It will be resubmitted once changed')}`;
    case 'rejected': return `${name} · ${exec ? L('已拒绝', 'Denied') : L('已拒绝，不会再提出', "Declined, won't come up again")}${extra(item.result)}`;
    case 'withdrawn': return `${name} · ${L('已撤回', 'Withdrawn')}${extra(item.result)}`;
    case 'expired': return `${name} · ${L('已过期', 'Expired')}${extra(item.result)}`;
    default: return `${name} · ${L('等待你确认', 'Waiting for your approval')}`;
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
  const [rewrite, setRewrite] = useState<string | null>(null);  // Doorman 扣下的那句 / 扣下的代办请求：「改一下」时你写的话
  const exec = item.kind === 'exec';
  const review = item.kind === 'social' && item.social?.ask === 'review';
  const act = (action: InboxAction, note?: string) => {
    if (busy) return;
    setBusy(action);
    // 成功后 store 把这张卡换成回执（「今天」和对话里同时）
    decide(item.id, action, note).catch((e) => showError(L('操作失败', "Couldn't complete the action"), e)).finally(() => setBusy(null));
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
          <T v="caption" color={t.ink3} style={{ fontWeight: '600' }}>{L('变更内容', 'Changes')}</T>
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
          <T v="callout" color={t.gold} style={{ fontWeight: '600' }}>{L('需要修改？在对话中说明', 'Need changes? Explain in chat')}</T>
          <ChevronRight size={16} color={t.gold} />
        </Pressable>
      ) : null}
      <View style={styles.actions}>
        {exec ? (
          <>
            <CardBtn kind="quiet" label={L('拒绝', 'Deny')} busy={busy === 'reject'} disabled={!!busy} onPress={() => act('reject')} />
            <CardBtn kind="primary" label={L('允许一次', 'Allow once')} busy={busy === 'approve'} disabled={!!busy} onPress={() => act('approve')} />
          </>
        ) : review && rewrite != null ? (
          // Doorman 扣下的那句，「改一下」：写你要发的话，发出去替它那句（算你说的）
          <View style={{ flex: 1, gap: space.sm }}>
            <TextInput value={rewrite} onChangeText={setRewrite} multiline maxLength={400} autoFocus
              placeholder={L(`输入要发送给 ${item.social?.peer ?? ''} 的内容`, `What ${item.social?.peer ?? 'they'} should see`)} placeholderTextColor={t.ink3}
              accessibilityLabel={L('待发送内容', 'What to send')}
              style={[type.body, styles.rewrite, { color: t.ink, borderColor: t.line, backgroundColor: t.bg }]} />
            <View style={styles.actions}>
              <CardBtn kind="quiet" label={L('取消', 'Cancel')} disabled={!!busy} onPress={() => setRewrite(null)} />
              <CardBtn kind="primary" label={L('发送', 'Send')} icon={Check} busy={busy === 'revise'} disabled={!!busy || !rewrite.trim()}
                onPress={() => act('revise', rewrite.trim())} />
            </View>
          </View>
        ) : review ? (
          // Doorman 扣下的那句：不发（告诉对方答不了）/ 改一下 / 照发
          <>
            <CardBtn kind="quiet" label={L('不发送', "Don't send")} busy={busy === 'reject'} disabled={!!busy} onPress={() => act('reject')} />
            <CardBtn kind="quiet" label={L('改写', 'Rewrite')} disabled={!!busy} onPress={() => setRewrite(item.social?.original ?? '')} />
            <CardBtn kind="primary" label={item.approveLabel || L('原样发送', 'Send as is')} icon={Check} busy={busy === 'approve'} disabled={!!busy} onPress={() => act('approve')} />
          </>
        ) : item.kind === 'egress' && rewrite != null ? (
          // Doorman 出口扣下的代办请求，「改一下」：写上怎么改，代办那边收到 403 和你的话，照着重新来
          <View style={{ flex: 1, gap: space.sm }}>
            <TextInput value={rewrite} onChangeText={setRewrite} multiline maxLength={400} autoFocus
              placeholder={L('说明代办需要如何修改（例如：主题改为 Hi）', 'Tell the errand what to change (e.g. make the subject "Hi")')} placeholderTextColor={t.ink3}
              accessibilityLabel={L('修改说明', 'What to change')}
              style={[type.body, styles.rewrite, { color: t.ink, borderColor: t.line, backgroundColor: t.bg }]} />
            <View style={styles.actions}>
              <CardBtn kind="quiet" label={L('取消', 'Cancel')} disabled={!!busy} onPress={() => setRewrite(null)} />
              <CardBtn kind="primary" label={L('退回修改', 'Send back')} icon={Check} busy={busy === 'revise'} disabled={!!busy || !rewrite.trim()}
                onPress={() => act('revise', rewrite.trim())} />
            </View>
          </View>
        ) : item.kind === 'egress' ? (
          // 代办要提交 / 发送 / 用你的凭证：不要 / 改一下 / 放行这一次（只放这一个请求）
          <>
            <CardBtn kind="quiet" label={L('拒绝', 'Deny')} busy={busy === 'reject'} disabled={!!busy} onPress={() => act('reject')} />
            <CardBtn kind="quiet" label={L('修改', 'Revise')} disabled={!!busy} onPress={() => setRewrite('')} />
            <CardBtn kind="primary" label={item.approveLabel || L('放行一次', 'Let it through')} icon={Check} busy={busy === 'approve'} disabled={!!busy} onPress={() => act('approve')} />
          </>
        ) : item.kind === 'social' && item.social?.counter ? (
          // 名片 agent 替你约的：不去 / 换个时间（按你空着的晚上提一个）/ 同意（设计稿 SocAgents）
          <>
            <CardBtn kind="quiet" label={L('不参加', 'Decline')} busy={busy === 'reject'} disabled={!!busy} onPress={() => act('reject')} />
            <CardBtn kind="quiet" label={L('另约时间', 'Another time')} busy={busy === 'revise'} disabled={!!busy} onPress={() => act('revise')} />
            <CardBtn kind="primary" label={item.approveLabel || L('同意', 'Approve')} icon={Check} busy={busy === 'approve'} disabled={!!busy} onPress={() => act('approve')} />
          </>
        ) : (
          <>
            <CardBtn kind="quiet" label={L('拒绝', 'Decline')} busy={busy === 'reject'} disabled={!!busy} onPress={() => act('reject')} />
            <CardBtn kind="primary" label={item.approveLabel || L('同意', 'Approve')} icon={Check} busy={busy === 'approve'} disabled={!!busy} onPress={() => act('approve')} />
          </>
        )}
      </View>
    </View>
  );
}

/** 处理过的：一行回执（状态圆点 + 「已同意 · 标题」+ 说明 / 结果），点开看详情、跟进。开好了的项目右边是「去看看」。 */
function InboxReceipt({ item, chat }: { item: InboxItem; chat: boolean }) {
  const t = useTheme();
  const nameOf = useSourceName();
  const openDetail = useInboxDetail();
  const name = item.sourceName || nameOf(item.source);
  const r = receiptText(item, name);
  const project = item.kind === 'project' && item.project?.action === 'open' && item.status === 'done' ? item.project.project : undefined;
  return (
    <Pressable onPress={() => openDetail(item)} accessible={!project} accessibilityRole="button" accessibilityLabel={`${r.head}${L('，', ', ')}${r.sub}`} accessibilityHint={L('查看详情', 'Shows the details')}
      style={({ pressed }) => [styles.rcpt, chat && styles.chatRcpt, { backgroundColor: t.surface, borderColor: t.line, opacity: pressed ? 0.7 : 1 }]}>
      <StatusCircle status={item.status} exec={item.kind === 'exec'} />
      <View style={{ flex: 1, gap: 2 }}>
        <T v="headline" numberOfLines={1} style={{ fontSize: 15 }}>{r.head}</T>
        {r.sub ? <T v="callout" color={t.ink2} numberOfLines={chat ? 3 : 2} style={{ fontSize: 13, lineHeight: 18 }}>{r.sub}</T> : null}
      </View>
      {project ? (
        <Pressable onPress={() => openThread(project.id, false)} hitSlop={8} accessibilityRole="button" accessibilityLabel={L(`查看：${project.title}`, `Open ${project.title}`)}
          style={({ pressed }) => [{ flexDirection: 'row', alignItems: 'center', gap: 2, opacity: pressed ? 0.6 : 1 }]}>
          <T v="callout" color={t.gold} style={{ fontWeight: '600' }}>{L('查看', 'Open')}</T>
          <ChevronRight size={15} color={t.gold} />
        </Pressable>
      ) : (
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 2 }}>
          <T v="caption" color={t.ink3}>{relTime(item.followedAt || item.decidedAt)}</T>
          <ChevronRight size={14} color={t.ink3} />
        </View>
      )}
    </Pressable>
  );
}

// —— 详情：处理过的一件事点开 ——

/** 时间点：今天 20:01 / 昨天 20:01 / 9月27日 20:01。 */
function stamp(iso: string | null | undefined): string {
  const ms = iso ? Date.parse(iso) : NaN;
  if (Number.isNaN(ms)) return '';
  const d = new Date(ms);
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const day = (x: Date) => x.toLocaleDateString('en-CA');
  const yest = new Date(); yest.setDate(yest.getDate() - 1);
  if (day(d) === day(new Date())) return L(`今天 ${hm}`, `Today ${hm}`);
  if (day(d) === day(yest)) return L(`昨天 ${hm}`, `Yesterday ${hm}`);
  return `${d.toLocaleDateString(L('zh-CN', 'en'), { month: 'short', day: 'numeric' })} ${hm}`;
}

/** 中文里名字是英文（比如 Grava）时，前面空一格。 */
const lead = (name: string) => (/^[A-Za-z0-9]/.test(name) ? ` ${name}` : name);

/** 详情顶上那一句：现在是什么状态。 */
function statusWord(item: InboxItem): string {
  const exec = item.kind === 'exec';
  switch (item.status) {
    case 'approved':
      return exec ? L('已允许一次', 'Allowed once') : item.followedAt ? L('已跟进，处理中', 'Followed up, back in progress') : L('已同意，处理中', 'Approved, in progress');
    case 'done': return L('已完成', 'Done');
    case 'failed': return L('失败', 'Failed');
    case 'rejected': return exec ? L('已拒绝', 'Denied') : L('已拒绝', 'Declined');
    case 'revising': return L('已要求修改，等待重新提交', 'Changes requested, waiting for resubmission');
    case 'withdrawn': return L('已撤回', 'Withdrawn');
    case 'expired': return L('已过期', 'Expired');
    default: return L('等待你确认', 'Waiting for your approval');
  }
}

/** 「跟进」上面那句：点了会怎样。 */
function followHint(item: InboxItem, name: string): string {
  switch (item.status) {
    case 'approved':
      return L(zh(name, '尚未报告结果。跟进：在对话中附上此事项，询问进展。'), `${name} hasn't reported back yet. Follow up to ask for progress, with this item attached.`);
    case 'done':
    case 'failed':
      return L(`${item.status === 'done' ? '仍有遗漏？' : '需要重试或换一种方式？'}跟进会将其恢复为「处理中」，并将你的说明连同此事项一并交给${lead(name)}。`,
        `${item.status === 'done' ? 'Something still missing?' : 'Try again or take another approach?'} Following up puts it back in progress and hands ${name} what you say along with this item.`);
    case 'revising':
      return L('跟进：在对话中附上此事项，继续说明修改要求。', 'Follow up to keep discussing the changes, with this item attached.');
    default:
      return L(`改变主意？跟进：在对话中附上此事项跟${zh(lead(name), '说')}明；如需执行，将重新提交并等待你确认。`,
        `Changed your mind? Follow up to tell ${name}; if it should happen after all, it will ask for your approval again.`);
  }
}

/** 打开一件事的详情（弹层）。「已处理」列表、「今天」和对话里的回执都用它。 */
export function useInboxDetail() {
  const sheet = useSheet();
  return useCallback((item: InboxItem) => sheet.open({ title: item.title, content: (close) => <InboxDetail item={item} close={close} /> }), [sheet]);
}

/** 详情：状态、结果、你跟进过的话，接着「看原对话」「跟进」，再往下是为什么、会改什么、细节、时间。弹层画在导航外面：跳转走 navigationRef。 */
function InboxDetail({ item, close }: { item: InboxItem; close: () => void }) {
  const t = useTheme();
  const { groups } = useStore();
  const nameOf = useSourceName();
  const name = item.sourceName || nameOf(item.source);
  const exec = item.kind === 'exec';
  const canFollow = !exec && !!item.thread && item.status !== 'pending';
  // 跟进：回到提这件事的对话，输入框上面是「跟进：标题」；发出去时服务器给模型带上这件事的前情，做完 / 没做成的改回在做
  const follow = () => { close(); openThread(item.thread, groups.some((g) => g.id === item.thread), { inboxId: item.id, title: item.title, follow: true }); };
  // 看原对话：那天的记录，滚到提它的那条回复
  const original = item.messageId != null && item.day && item.thread
    ? () => { close(); navigationRef.navigate('HistoryDay', { thread: item.thread, day: item.day, focus: `db${item.messageId}` }); }
    : undefined;
  const fields = item.fields ?? [];
  const keyW = Math.min(96, Math.max(28, ...fields.map((f) => [...f.k].reduce((w, ch) => w + (/[　-鿿]/.test(ch) ? 13 : 8), 0))));
  const times: [string, string][] = [[L('提出', 'Raised'), stamp(item.createdAt)]];
  if (item.decidedAt) times.push([item.status === 'rejected' ? L('你拒绝', 'You declined') : item.status === 'revising' ? L('你要求修改', 'You asked for changes') : L('你同意', 'You approved'), stamp(item.decidedAt)]);
  if (item.followedAt) times.push([L('你跟进', 'You followed up'), stamp(item.followedAt)]);
  const last = Math.max(Date.parse(item.decidedAt ?? '') || 0, Date.parse(item.followedAt ?? '') || 0, Date.parse(item.createdAt) || 0);
  if (item.updatedAt && (Date.parse(item.updatedAt) || 0) - last > 60_000) {
    times.push([item.status === 'done' ? L('完成', 'Finished') : item.status === 'failed' ? L('报告失败', 'Reported failed') : L('最后更新', 'Last update'), stamp(item.updatedAt)]);
  }
  return (
    <View style={{ gap: space.md, paddingBottom: space.sm }}>
      <View style={styles.dHead}>
        <StatusCircle status={item.status} exec={exec} size={36} />
        <View style={{ flex: 1, gap: 4 }}>
          <T v="headline" style={{ fontSize: 17 }}>{statusWord(item)}</T>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
            <SourceBadge source={item.source} size={20} />
            <T v="caption" color={t.ink2} numberOfLines={1} style={{ fontSize: 13, fontWeight: '600', flexShrink: 1 }}>{name}</T>
            <Pill label={kindLabel(item.kind)} tone={kindTone(item.kind)} />
          </View>
        </View>
      </View>
      {item.result ? (
        <View style={[styles.box, { backgroundColor: item.status === 'failed' ? t.badSoft : t.surface, borderColor: t.line }]}>
          <T v="caption" color={t.ink3} style={{ fontWeight: '600' }}>{item.status === 'failed' ? L('失败原因', 'Why it failed') : L('结果', 'Result')}</T>
          <T v="callout" selectable style={{ lineHeight: 21 }}>{item.result.length > 4000 ? `${item.result.slice(0, 4000)}\n…${L(`（其余 ${item.result.length - 4000} 字未显示）`, ` (${item.result.length - 4000} more characters)`)}` : item.result}</T>
        </View>
      ) : null}
      {item.followedAt && item.followNote ? (
        <View style={[styles.box, { backgroundColor: t.goldSoft, borderColor: t.line }]}>
          <T v="caption" color={t.ink3} style={{ fontWeight: '600' }}>{L(`跟进说明 · ${stamp(item.followedAt)}`, `Follow-up note · ${stamp(item.followedAt)}`)}</T>
          <T v="callout" selectable style={{ lineHeight: 21 }}>{item.followNote}</T>
        </View>
      ) : null}
      {item.note && (item.status === 'revising' || item.status === 'rejected') ? (
        <View style={[styles.box, { backgroundColor: t.surface, borderColor: t.line }]}>
          <T v="caption" color={t.ink3} style={{ fontWeight: '600' }}>{item.status === 'revising' ? L('修改要求', 'Your changes') : L('你的理由', 'Your reason')}</T>
          <T v="callout" selectable style={{ lineHeight: 21 }}>{item.note}</T>
        </View>
      ) : null}
      {/* 跟进放在结果下面、细节上面：细节可能很长，按钮不能沉到底 */}
      {canFollow ? <T v="callout" color={t.ink2} style={{ lineHeight: 20 }}>{followHint(item, name)}</T> : null}
      {canFollow || original ? (
        <View style={styles.actions}>
          {original ? <CardBtn kind="quiet" label={L('查看原对话', 'See the chat')} icon={MessageCircle} onPress={original} /> : null}
          {canFollow ? <CardBtn kind="primary" label={L('跟进', 'Follow up')} icon={ChevronRight} onPress={follow} /> : null}
        </View>
      ) : null}
      {item.why ? (
        <View style={{ gap: 4 }}>
          <T v="caption" color={t.ink3} style={{ fontWeight: '600' }}>{L('原因', 'Why')}</T>
          <T v="callout" color={t.ink2} selectable style={{ lineHeight: 21 }}>{item.why}</T>
        </View>
      ) : null}
      {item.changes.length ? (
        <View style={[styles.box, { backgroundColor: t.surface, borderColor: t.line }]}>
          <T v="caption" color={t.ink3} style={{ fontWeight: '600' }}>{L('变更内容', 'Changes')}</T>
          {item.changes.map((c, i) => (
            <View key={`${i}-${c}`} style={styles.li}>
              <View style={[styles.dot, { backgroundColor: t.ink3 }]} />
              <T v="callout" style={{ flex: 1 }}>{c}</T>
            </View>
          ))}
        </View>
      ) : null}
      {item.skill || item.agent ? <ProposalPreview info={(item.skill ?? item.agent) as InboxProposalInfo} kind={item.kind} /> : null}
      {item.kind === 'project' && item.project?.action === 'open' ? <ProjectPreview info={item.project} /> : null}
      {item.detail && !(item.kind === 'project' && item.project?.action === 'open') ? (
        <View style={[styles.box, { backgroundColor: t.surface, borderColor: t.line }]}>
          <T v="caption" color={t.ink3} style={{ fontWeight: '600' }}>{L('详情', 'Details')}</T>
          <Markdown text={item.detail} color={t.ink2} compact />
        </View>
      ) : null}
      {fields.length ? (
        <View style={[styles.box, { backgroundColor: t.surface, borderColor: t.line, gap: 4 }]}>
          {fields.map((f, i) => (
            <View key={`${i}-${f.k}`} style={{ flexDirection: 'row', gap: 10 }}>
              <Text style={[styles.mono, { color: t.ink3, width: keyW }]}>{f.k}</Text>
              <Text selectable style={[styles.mono, { color: t.ink, flex: 1 }]}>{f.v}</Text>
            </View>
          ))}
        </View>
      ) : null}
      <View style={{ gap: 3 }}>
        {times.map(([k, v]) => (
          <View key={k} style={{ flexDirection: 'row', gap: 8 }}>
            <T v="caption" color={t.ink3} style={{ fontSize: 13, minWidth: 64 }}>{k}</T>
            <T v="caption" color={t.ink2} style={{ fontSize: 13, fontVariant: ['tabular-nums'] }}>{v}</T>
          </View>
        ))}
      </View>
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
          <T v="caption" color={t.ink3} style={{ fontWeight: '600' }}>{L('你的原话', 'What you said')}</T>
          {quotes.map((e, i) => (
            <View key={`${i}-${e.quote}`} style={styles.quote}>
              <T v="callout" style={{ lineHeight: 20 }}>{L(`「${e.quote}」`, `"${e.quote}"`)}</T>
              {e.date || e.thread ? <T v="caption" color={t.ink3}>{[e.date, e.thread].filter(Boolean).join(' · ')}</T> : null}
            </View>
          ))}
          {more > 0 ? (
            <Pressable onPress={() => setAllQuotes((v) => !v)} hitSlop={8} accessibilityRole="button" accessibilityState={{ expanded: allQuotes }} style={{ alignSelf: 'flex-start' }}>
              <T v="caption" color={t.gold} style={{ fontWeight: '600', fontSize: 13 }}>{allQuotes ? L('收起', 'Show less') : L(`另有 ${more} 条`, `${more} more`)}</T>
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
            <T v="caption" color={t.gold} style={{ fontWeight: '600', fontSize: 13 }}>{full ? L('收起', 'Show less') : L('查看完整步骤', 'Read the full steps')}</T>
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
  dHead: { flexDirection: 'row', alignItems: 'center', gap: space.md },
  prev: { flexDirection: 'row', alignItems: 'flex-start', gap: 10, paddingVertical: 8 },
  quote: { gap: 1, paddingVertical: 2 },
  prevIcon: { width: 24, height: 24, borderRadius: 8, alignItems: 'center', justifyContent: 'center' },
  rewrite: { borderWidth: StyleSheet.hairlineWidth, borderRadius: radius.md, paddingHorizontal: space.md, paddingVertical: 10, minHeight: 72, textAlignVertical: 'top' },
});
