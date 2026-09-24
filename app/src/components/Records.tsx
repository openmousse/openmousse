// 日志、求职与申请看板、训练建议卡：Grava 用 workspace 的脚本写进 grava.db，这里只读。
import React from 'react';
import { agentName } from '../brand';
import { Alert, Linking, Pressable, StyleSheet, View } from 'react-native';
import type { Application, FeedItem, JournalEntry, TrainingPlan } from '../data/types';
import { L } from '../i18n';
import { useStore } from '../store';
import { Markdown } from './Markdown';
import { space, useTheme } from '../theme';
import { Dumbbell, Sparkles, Trash2 } from './icons';
import { Btn, Card, Pill, SectionLabel, T } from './ui';

const KIND_LABEL = (): Record<JournalEntry['kind'], string> => ({ feeling: L('感受', 'Feeling'), thought: L('想法', 'Thought'), decision: L('决定', 'Decision'), note: L('记录', 'Note') });
const KIND_TONE: Record<JournalEntry['kind'], 'cyan' | 'gold' | 'good' | 'neutral'> = { feeling: 'cyan', thought: 'neutral', decision: 'gold', note: 'neutral' };

/** 日志列表。showGroup 时每条带 Group 名（我 → 日志用）。 */
export function JournalList({ entries, showGroup, empty }: { entries: JournalEntry[]; showGroup?: boolean; empty: string }) {
  const t = useTheme();
  const { groups, deleteJournal } = useStore();
  const gname = (id: string | null) => groups.find((g) => g.id === id)?.name ?? null;
  const remove = (e: JournalEntry) => Alert.alert(L('删掉这条日志？', 'Delete this journal entry?'), L(`只删正文，${agentName()} 也不会再引用它。`, `This erases the text, and ${agentName()} won't refer to it again.`), [
    { text: L('取消', 'Cancel'), style: 'cancel' },
    { text: L('删除', 'Delete'), style: 'destructive', onPress: () => deleteJournal(e.id).catch((err) => Alert.alert(L('没做成', "Didn't go through"), String(err))) },
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
                <Pill label={KIND_LABEL()[e.kind] ?? e.kind} tone={KIND_TONE[e.kind] ?? 'neutral'} />
                {showGroup && gname(e.groupId) ? <Pill label={gname(e.groupId)!} tone="neutral" /> : null}
                <T v="caption" color={t.ink3} style={{ flex: 1 }}>{e.time}{e.context ? ` · ${e.context}` : ''}</T>
                <Pressable onPress={() => remove(e)} hitSlop={10} accessibilityRole="button" accessibilityLabel={L('删除这条日志', 'Delete this journal entry')}><Trash2 size={15} color={t.ink3} /></Pressable>
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

const STATUS_LABEL = (): Record<Application['status'], string> => ({ planned: L('计划中', 'Planned'), in_progress: L('准备中', 'Preparing'), submitted: L('已提交', 'Submitted'), interview: L('面试', 'Interview'), offer: 'Offer', rejected: L('拒了', 'Rejected'), closed: L('关闭', 'Closed') });
const STATUS_TONE: Record<Application['status'], 'neutral' | 'cyan' | 'gold' | 'good' | 'bad'> = { planned: 'neutral', in_progress: 'cyan', submitted: 'gold', interview: 'gold', offer: 'good', rejected: 'bad', closed: 'neutral' };
const KIND_NAME = (): Record<Application['kind'], string> => ({ job: L('实习 / 工作', 'Internship / job'), masters: L('硕士', "Master's"), fellowship: 'Fellowship', ra: 'RA', other: L('其它', 'Other') });
const nDays = (n: number) => (n === 1 ? '1 day' : `${n} days`);

/** 求职 / 申请学校看板（同一张 applications 表按 kind 分）：按 ddl 排，进度条 + 下一步。 */
export function ApplicationsBoard({ apps, school }: { apps: Application[]; school?: boolean }) {
  const t = useTheme();
  const soon = apps.filter((a) => a.daysLeft != null && a.daysLeft <= 14);
  const noDl = apps.filter((a) => a.deadline == null);
  return (
    <View>
      <Card style={{ flexDirection: 'row', gap: space.md }}>
        <View style={{ flex: 1, gap: 2 }}><T v="title" style={{ fontVariant: ['tabular-nums'] }}>{apps.length}</T><T v="caption" color={t.ink3}>{L('进行中', 'Active')}</T></View>
        <View style={{ flex: 1, gap: 2 }}><T v="title" color={soon.length ? t.warn : t.ink} style={{ fontVariant: ['tabular-nums'] }}>{soon.length}</T><T v="caption" color={t.ink3}>{L('两周内到期', 'Due in 2 weeks')}</T></View>
        <View style={{ flex: 1, gap: 2 }}><T v="title" style={{ fontVariant: ['tabular-nums'] }}>{noDl.length}</T><T v="caption" color={t.ink3}>{L('没填 ddl', 'No deadline')}</T></View>
      </Card>
      <SectionLabel>{L('按 ddl 排', 'By deadline')}</SectionLabel>
      {apps.length ? (
        <View style={{ gap: space.md }}>
          {apps.map((a) => (
            <Card key={a.id} style={{ gap: space.sm }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                <Pill label={STATUS_LABEL()[a.status]} tone={STATUS_TONE[a.status]} />
                <T v="caption" color={t.ink3} style={{ flex: 1 }}>{KIND_NAME()[a.kind]}</T>
                <T v="caption" color={a.daysLeft != null && a.daysLeft <= 14 ? t.warn : t.ink3} style={{ fontVariant: ['tabular-nums'] }}>
                  {a.deadline
                    ? L(`ddl ${a.deadline.slice(5)}${a.daysLeft != null ? `（${a.daysLeft >= 0 ? `还剩 ${a.daysLeft} 天` : `过了 ${-a.daysLeft} 天`}）` : ''}`,
                      `Due ${a.deadline.slice(5)}${a.daysLeft != null ? ` (${a.daysLeft >= 0 ? `${nDays(a.daysLeft)} left` : `${nDays(-a.daysLeft)} ago`})` : ''}`)
                    : L('ddl 待补', 'Deadline TBD')}
                </T>
              </View>
              <T v="headline">{a.org}</T>
              <T v="callout" color={t.ink2}>{a.role}</T>
              <View style={{ height: 6, borderRadius: 3, backgroundColor: t.track, overflow: 'hidden' }} accessibilityLabel={L(`材料完成 ${a.progress}%`, `Materials ${a.progress}% done`)}>
                <View style={{ width: `${Math.max(0, Math.min(100, a.progress))}%`, height: 6, backgroundColor: t.chartA }} />
              </View>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
                <T v="caption" color={t.ink3} style={{ fontVariant: ['tabular-nums'] }}>{a.progress}%</T>
                <T v="callout" color={t.ink2} style={{ flex: 1 }} numberOfLines={2}>{a.nextStep ? L(`下一步：${a.nextStep}`, `Next: ${a.nextStep}`) : L('下一步还没定', 'No next step yet')}</T>
              </View>
              {a.materials.length ? <T v="caption" color={t.ink3}>{L(`材料：${a.materials.join('、')}`, `Materials: ${a.materials.join(', ')}`)}</T> : null}
              {a.link ? <Pressable onPress={() => Linking.openURL(a.link!).catch(() => {})} accessibilityRole="link"><T v="caption" color={t.cyan} numberOfLines={1}>{a.link}</T></Pressable> : null}
            </Card>
          ))}
        </View>
      ) : <Card><T v="callout" color={t.ink2}>{school
        ? L(`还没有学校记录。在对话里告诉 ${agentName()} 学校、项目和 ddl，它会记在这里。`, `No schools yet. Tell ${agentName()} the school, program and deadline in chat and it'll track them here.`)
        : L(`还没有申请记录。在对话里告诉 ${agentName()} 公司、岗位和 ddl，它会记在这里。`, `No applications yet. Tell ${agentName()} the company, role and deadline in chat and it'll track them here.`)}</T></Card>}
      <T v="caption" color={t.ink3} style={{ marginTop: space.md, paddingHorizontal: space.xs }}>{school
        ? L(`在对话里说「交了 X」「推荐信到了」「ddl 是 X」，${agentName()} 会更新这张表。`, `Say "submitted X", "reference letter is in" or "deadline is X" in chat and ${agentName()} updates this table.`)
        : L(`在对话里说「我提交了」「收到面试」「ddl 是 X」，${agentName()} 会更新这张表。发 JD 过来它能按岗位改 CV 和 cover letter。`, `Say "I submitted", "got an interview" or "deadline is X" in chat and ${agentName()} updates this table. Send a job description and it can tailor your CV and cover letter.`)}</T>
    </View>
  );
}

// decision 是 agent 按用户的语言写的：中文关键词照旧，英文另配一组（rest / deload、light / train）。
const DECISION_TONE = (d: string): 'good' | 'warn' | 'bad' | 'neutral' => (d.includes('休息') || /\brest\b/i.test(d) ? 'bad'
  : d.includes('减量') || d.includes('轻') || /deload|\blight|\beasy|\breduce/i.test(d) ? 'warn'
  : d.includes('练') || /\btrain|\bworkout|\blift|\bgo\b/i.test(d) ? 'good' : 'neutral');

/** 训练建议卡（kind = training_plan）。今天页和健身看板共用。 */
export function TrainingPlanCard({ plan }: { plan: TrainingPlan }) {
  const t = useTheme();
  return (
    <View style={{ gap: space.sm }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm, flexWrap: 'wrap' }}>
        <Pill label={plan.decision} tone={DECISION_TONE(plan.decision)} />
        {plan.session ? <T v="headline">{plan.session}</T> : null}
        {plan.time ? <T v="caption" color={t.ink3}>{plan.time}{plan.duration_min ? L(` · ${plan.duration_min} 分钟`, ` · ${plan.duration_min} min`) : ''}</T> : null}
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
  const ask = () => { send(groupId, L('出今天的训练建议', "Plan today's workout")); onAsk(); };
  return (
    <View style={{ marginBottom: space.lg }}>
      <SectionLabel right={plan ? <T v="caption" color={t.ink3}>{plan.time}</T> : undefined}>{L('今天怎么练', "Today's workout")}</SectionLabel>
      {plan && isTrainingPlan(plan) ? (
        <Card style={{ gap: space.sm }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
            <Dumbbell size={16} color={t.ink2} />
            <T v="headline" style={{ flex: 1 }}>{plan.title}</T>
          </View>
          <TrainingPlanCard plan={plan.data} />
          <Btn label={busy ? L(`${agentName()} 正在出…`, `${agentName()} is on it…`) : L('重新出一份', 'Make a new one')} kind="quiet" onPress={ask} icon={<Sparkles size={14} color={t.ink} />} />
        </Card>
      ) : (
        <Card style={{ gap: space.sm }}>
          <T v="callout" color={t.ink2}>{L(`今天还没有训练建议。${agentName()} 会按昨晚睡眠、恢复分、PPL 轮到哪个、今天的课和你最近的感受来配。`, `No workout plan for today yet. ${agentName()} plans it around last night's sleep, your recovery score, where you are in your PPL split, today's classes and how you've been feeling.`)}</T>
          <Btn label={busy ? L(`${agentName()} 正在出…`, `${agentName()} is on it…`) : L(`让 ${agentName()} 出今天的建议`, `Ask ${agentName()} for today's plan`)} kind="primary" onPress={ask} icon={<Sparkles size={14} color={t.onGold} />} />
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
  const ask = () => { send(groupId, L('出昨晚的睡眠报告', "Report on last night's sleep")); onAsk(); };
  return (
    <View style={{ marginBottom: space.lg }}>
      <SectionLabel right={card ? <T v="caption" color={t.ink3}>{card.time}</T> : undefined}>{L('昨晚睡得怎么样', 'How you slept last night')}</SectionLabel>
      {card ? (
        <Card style={{ gap: space.sm }}>
          <T v="headline">{card.title}</T>
          <Markdown text={card.body} color={t.ink2} compact />
          <Btn label={busy ? L(`${agentName()} 正在出…`, `${agentName()} is on it…`) : L('重新出一份', 'Make a new one')} kind="quiet" onPress={ask} icon={<Sparkles size={14} color={t.ink} />} />
        </Card>
      ) : (
        <Card style={{ gap: space.sm }}>
          <T v="callout" color={t.ink2}>{L('今天还没有睡眠报告。健康 Agent 会按昨晚的分期、HRV、静息心率对你自己的基线来解读。', "No sleep report yet today. The health agent reads last night's sleep stages, HRV and resting heart rate against your own baseline.")}</T>
          <Btn label={busy ? L(`${agentName()} 正在出…`, `${agentName()} is on it…`) : L(`让 ${agentName()} 解读昨晚`, `Ask ${agentName()} about last night`)} kind="primary" onPress={ask} icon={<Sparkles size={14} color={t.onGold} />} />
        </Card>
      )}
    </View>
  );
}
