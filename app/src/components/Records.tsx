// 日志、求职与申请看板、训练建议卡：Grava 用 workspace 的脚本写进 grava.db，这里只读。
import React from 'react';
import { agentName } from '../brand';
import { Alert, Linking, Pressable, StyleSheet, View } from 'react-native';
import type { Application, FeedItem, JournalEntry, TrainingPlan } from '../data/types';
import { useStore } from '../store';
import { Markdown } from './Markdown';
import { space, useTheme } from '../theme';
import { Dumbbell, Sparkles, Trash2 } from './icons';
import { Btn, Card, Pill, SectionLabel, T } from './ui';

const KIND_LABEL: Record<JournalEntry['kind'], string> = { feeling: '感受', thought: '想法', decision: '决定', note: '记录' };
const KIND_TONE: Record<JournalEntry['kind'], 'cyan' | 'gold' | 'good' | 'neutral'> = { feeling: 'cyan', thought: 'neutral', decision: 'gold', note: 'neutral' };

/** 日志列表。showGroup 时每条带 Group 名（我 → 日志用）。 */
export function JournalList({ entries, showGroup, empty }: { entries: JournalEntry[]; showGroup?: boolean; empty: string }) {
  const t = useTheme();
  const { groups, deleteJournal } = useStore();
  const gname = (id: string | null) => groups.find((g) => g.id === id)?.name ?? null;
  const remove = (e: JournalEntry) => Alert.alert('删掉这条日志？', `只删正文，${agentName()} 也不会再引用它。`, [
    { text: '取消', style: 'cancel' },
    { text: '删除', style: 'destructive', onPress: () => deleteJournal(e.id).catch((err) => Alert.alert('没做成', String(err))) },
  ]);
  if (!entries.length) return <Card><T v="callout" color={t.ink2}>{empty}</T></Card>;
  return (
    <View style={{ gap: space.sm }}>
      {entries.map((e, i) => {
        const head = i === 0 || entries[i - 1].date !== e.date ? e.date : null;
        return (
          <View key={e.id} style={{ gap: space.sm }}>
            {head ? <T v="caption" color={t.ink3} style={{ marginTop: space.xs }}>{head}</T> : null}
            <Card style={{ gap: 6 }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                <Pill label={KIND_LABEL[e.kind] ?? e.kind} tone={KIND_TONE[e.kind] ?? 'neutral'} />
                {showGroup && gname(e.groupId) ? <Pill label={gname(e.groupId)!} tone="neutral" /> : null}
                <T v="caption" color={t.ink3} style={{ flex: 1 }}>{e.time}{e.context ? ` · ${e.context}` : ''}</T>
                <Pressable onPress={() => remove(e)} hitSlop={10} accessibilityRole="button" accessibilityLabel="删除这条日志"><Trash2 size={15} color={t.ink3} /></Pressable>
              </View>
              <T v="body">{e.text}</T>
              {e.tags.length ? <T v="caption" color={t.ink3}>{e.tags.map((x) => `#${x}`).join(' ')}</T> : null}
            </Card>
          </View>
        );
      })}
    </View>
  );
}

const STATUS_LABEL: Record<Application['status'], string> = { planned: '计划中', in_progress: '准备中', submitted: '已提交', interview: '面试', offer: 'Offer', rejected: '拒了', closed: '关闭' };
const STATUS_TONE: Record<Application['status'], 'neutral' | 'cyan' | 'gold' | 'good' | 'bad'> = { planned: 'neutral', in_progress: 'cyan', submitted: 'gold', interview: 'gold', offer: 'good', rejected: 'bad', closed: 'neutral' };
const KIND_NAME: Record<Application['kind'], string> = { job: '实习 / 工作', masters: '硕士', fellowship: 'Fellowship', ra: 'RA', other: '其它' };

/** 求职 / 申请学校看板（同一张 applications 表按 kind 分）：按 ddl 排，进度条 + 下一步。 */
export function ApplicationsBoard({ apps, school }: { apps: Application[]; school?: boolean }) {
  const t = useTheme();
  const soon = apps.filter((a) => a.daysLeft != null && a.daysLeft <= 14);
  const noDl = apps.filter((a) => a.deadline == null);
  return (
    <View>
      <Card style={{ flexDirection: 'row', gap: space.md }}>
        <View style={{ flex: 1, gap: 2 }}><T v="title" style={{ fontVariant: ['tabular-nums'] }}>{apps.length}</T><T v="caption" color={t.ink3}>进行中</T></View>
        <View style={{ flex: 1, gap: 2 }}><T v="title" color={soon.length ? t.warn : t.ink} style={{ fontVariant: ['tabular-nums'] }}>{soon.length}</T><T v="caption" color={t.ink3}>两周内到期</T></View>
        <View style={{ flex: 1, gap: 2 }}><T v="title" style={{ fontVariant: ['tabular-nums'] }}>{noDl.length}</T><T v="caption" color={t.ink3}>没填 ddl</T></View>
      </Card>
      <SectionLabel>按 ddl 排</SectionLabel>
      {apps.length ? (
        <View style={{ gap: space.md }}>
          {apps.map((a) => (
            <Card key={a.id} style={{ gap: space.sm }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                <Pill label={STATUS_LABEL[a.status]} tone={STATUS_TONE[a.status]} />
                <T v="caption" color={t.ink3} style={{ flex: 1 }}>{KIND_NAME[a.kind]}</T>
                <T v="caption" color={a.daysLeft != null && a.daysLeft <= 14 ? t.warn : t.ink3} style={{ fontVariant: ['tabular-nums'] }}>
                  {a.deadline ? `ddl ${a.deadline.slice(5)}${a.daysLeft != null ? `（${a.daysLeft >= 0 ? `还剩 ${a.daysLeft} 天` : `过了 ${-a.daysLeft} 天`}）` : ''}` : 'ddl 待补'}
                </T>
              </View>
              <T v="headline">{a.org}</T>
              <T v="callout" color={t.ink2}>{a.role}</T>
              <View style={{ height: 6, borderRadius: 3, backgroundColor: t.track, overflow: 'hidden' }} accessibilityLabel={`材料完成 ${a.progress}%`}>
                <View style={{ width: `${Math.max(0, Math.min(100, a.progress))}%`, height: 6, backgroundColor: t.chartA }} />
              </View>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
                <T v="caption" color={t.ink3} style={{ fontVariant: ['tabular-nums'] }}>{a.progress}%</T>
                <T v="callout" color={t.ink2} style={{ flex: 1 }} numberOfLines={2}>{a.nextStep ? `下一步：${a.nextStep}` : '下一步还没定'}</T>
              </View>
              {a.materials.length ? <T v="caption" color={t.ink3}>材料：{a.materials.join('、')}</T> : null}
              {a.link ? <Pressable onPress={() => Linking.openURL(a.link!).catch(() => {})} accessibilityRole="link"><T v="caption" color={t.cyan} numberOfLines={1}>{a.link}</T></Pressable> : null}
            </Card>
          ))}
        </View>
      ) : <Card><T v="callout" color={t.ink2}>{school ? `还没有学校记录。在对话里告诉 ${agentName()} 学校、项目和 ddl，它会记在这里。` : `还没有申请记录。在对话里告诉 ${agentName()} 公司、岗位和 ddl，它会记在这里。`}</T></Card>}
      <T v="caption" color={t.ink3} style={{ marginTop: space.md, paddingHorizontal: space.xs }}>{school
        ? `在对话里说「交了 X」「推荐信到了」「ddl 是 X」，${agentName()} 会更新这张表。`
        : `在对话里说「我提交了」「收到面试」「ddl 是 X」，${agentName()} 会更新这张表。发 JD 过来它能按岗位改 CV 和 cover letter。`}</T>
    </View>
  );
}

const DECISION_TONE = (d: string): 'good' | 'warn' | 'bad' | 'neutral' => (d.includes('休息') ? 'bad' : d.includes('减量') || d.includes('轻') ? 'warn' : d.includes('练') ? 'good' : 'neutral');

/** 训练建议卡（kind = training_plan）。今天页和健身看板共用。 */
export function TrainingPlanCard({ plan }: { plan: TrainingPlan }) {
  const t = useTheme();
  return (
    <View style={{ gap: space.sm }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm, flexWrap: 'wrap' }}>
        <Pill label={plan.decision} tone={DECISION_TONE(plan.decision)} />
        {plan.session ? <T v="headline">{plan.session}</T> : null}
        {plan.time ? <T v="caption" color={t.ink3}>{plan.time}{plan.duration_min ? ` · ${plan.duration_min} 分钟` : ''}</T> : null}
      </View>
      {plan.intensity ? <T v="callout" color={t.ink2}>{plan.intensity}</T> : null}
      {plan.why ? <T v="caption" color={t.ink3}>{plan.why}</T> : null}
      {plan.focus?.length ? (
        <View style={{ gap: 4, marginTop: 2 }}>
          {plan.focus.map((f, i) => <View key={i} style={styles.row}><T v="callout" color={t.ink3}>{i + 1}.</T><T v="callout" style={{ flex: 1 }}>{f}</T></View>)}
        </View>
      ) : null}
      {plan.cautions?.length ? (
        <View style={{ gap: 4, marginTop: 2 }}>
          {plan.cautions.map((c, i) => <View key={i} style={styles.row}><T v="callout" color={t.warn}>!</T><T v="callout" color={t.ink2} style={{ flex: 1 }}>{c}</T></View>)}
        </View>
      ) : null}
    </View>
  );
}

const localDate = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
export const isTrainingPlan = (f: FeedItem): f is FeedItem & { data: TrainingPlan } => f.kind === 'training_plan' && !!f.data && 'decision' in f.data;

/** 健身看板顶部：今天的训练建议 + 一键让 Grava 出。 */
export function TrainingPlanSection({ groupId, onAsk }: { groupId: string; onAsk: () => void }) {
  const t = useTheme();
  const { feed, send, typing } = useStore();
  const today = localDate();
  const plan = feed.find((f) => f.groupId === groupId && isTrainingPlan(f) && (f.createdAt ?? '').slice(0, 10) === today);
  const busy = !!typing[groupId];
  const ask = () => { send(groupId, '出今天的训练建议'); onAsk(); };
  return (
    <View style={{ marginBottom: space.lg }}>
      <SectionLabel right={plan ? <T v="caption" color={t.ink3}>{plan.time}</T> : undefined}>今天怎么练</SectionLabel>
      {plan && isTrainingPlan(plan) ? (
        <Card style={{ gap: space.sm }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
            <Dumbbell size={16} color={t.ink2} />
            <T v="headline" style={{ flex: 1 }}>{plan.title}</T>
          </View>
          <TrainingPlanCard plan={plan.data} />
          <Btn label={busy ? `${agentName()} 正在出…` : '重新出一份'} kind="quiet" onPress={ask} icon={<Sparkles size={14} color={t.ink} />} />
        </Card>
      ) : (
        <Card style={{ gap: space.sm }}>
          <T v="callout" color={t.ink2}>{`今天还没有训练建议。${agentName()} 会按昨晚睡眠、恢复分、PPL 轮到哪个、今天的课和你最近的感受来配。`}</T>
          <Btn label={busy ? `${agentName()} 正在出…` : `让 ${agentName()} 出今天的建议`} kind="primary" onPress={ask} icon={<Sparkles size={14} color={t.onGold} />} />
        </Card>
      )}
    </View>
  );
}

const styles = StyleSheet.create({ row: { flexDirection: 'row', gap: 6, alignItems: 'flex-start' } });

/** 睡眠报告卡（kind = sleep_report，健康 Agent 每天早上出）。健康看板顶部。 */
export function SleepReportSection({ groupId, onAsk }: { groupId: string; onAsk: () => void }) {
  const t = useTheme();
  const { feed, send, typing } = useStore();
  const today = localDate();
  const card = feed.find((f) => f.groupId === groupId && f.kind === 'sleep_report' && (f.createdAt ?? '').slice(0, 10) === today);
  const busy = !!typing[groupId];
  const ask = () => { send(groupId, '出昨晚的睡眠报告'); onAsk(); };
  return (
    <View style={{ marginBottom: space.lg }}>
      <SectionLabel right={card ? <T v="caption" color={t.ink3}>{card.time}</T> : undefined}>昨晚睡得怎么样</SectionLabel>
      {card ? (
        <Card style={{ gap: space.sm }}>
          <T v="headline">{card.title}</T>
          <Markdown text={card.body} color={t.ink2} compact />
          <Btn label={busy ? `${agentName()} 正在出…` : '重新出一份'} kind="quiet" onPress={ask} icon={<Sparkles size={14} color={t.ink} />} />
        </Card>
      ) : (
        <Card style={{ gap: space.sm }}>
          <T v="callout" color={t.ink2}>今天还没有睡眠报告。健康 Agent 会按昨晚的分期、HRV、静息心率对你自己的基线来解读。</T>
          <Btn label={busy ? `${agentName()} 正在出…` : `让 ${agentName()} 解读昨晚`} kind="primary" onPress={ask} icon={<Sparkles size={14} color={t.onGold} />} />
        </Card>
      )}
    </View>
  );
}
