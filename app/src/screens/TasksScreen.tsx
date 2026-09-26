import React, { useCallback, useEffect, useState } from 'react';
import { agentName } from '../brand';
import { Alert, Pressable, ScrollView, StyleSheet, View } from 'react-native';
import { useNavigation, useRoute } from '@react-navigation/native';
import { dataApi } from '../api/data';
import { Check, CircleAlert, CircleDot, CircleX, Clock, Eye, Inbox, LoaderCircle, Lightbulb, ListChecks, Pencil, Terminal } from '../components/icons';
import { Spinner } from '../components/ChatCards';
import { useSheet } from '../components/Sheet';
import { ReviseSheetContent, fmtTokens, modelName, originName } from '../components/TaskCard';
import { Btn, Card, ListRow, NavHeader, Pill, PullRefresh, Screen, SectionLabel, T } from '../components/ui';
import type { Task, TaskQuota, TaskRun, TaskStep } from '../data/types';
import { L } from '../i18n';
import { useStore } from '../store';
import { radius, space, useTheme } from '../theme';

const statusTone = (s: Task['status']) => (s === '进行中' ? 'cyan' : s === '完成' ? 'good' : 'bad');
// 状态值是服务器给的中文枚举，程序拿它比较，不翻译；显示时换成当前语言。
const statusLabel = (s: Task['status']) =>
  ({ 进行中: L('进行中', 'Running'), 完成: L('完成', 'Done'), 失败: L('失败', 'Failed'), 已取消: L('已取消', 'Cancelled') })[s] ?? s;

/** 逻辑日从 04:00 开始（和服务器一样）：凌晨 1 点派的算前一天。 */
function dayStartMs(): number {
  const d = new Date();
  if (d.getHours() < 4) d.setDate(d.getDate() - 1);
  d.setHours(4, 0, 0, 0);
  return d.getTime();
}

/** 左边的状态圆点：在做 = 青色转圈，做完 = 绿勾，到点停了 = 钟，没做成 = 红色感叹号，停掉 = 灰叉。 */
function StatusDot({ task }: { task: Task }) {
  const t = useTheme();
  const [bg, fg, Icon] = task.status === '进行中' ? [t.cyanSoft, t.cyan, null]
    : task.status === '完成' ? [t.goodSoft, t.good, Check]
      : task.status === '已取消' ? [t.surface2, t.ink2, CircleX]
        : task.timedOut ? [t.warnSoft, t.warn, Clock] : [t.badSoft, t.bad, CircleAlert];
  return (
    <View style={{ width: 30, height: 30, borderRadius: 15, backgroundColor: bg, alignItems: 'center', justifyContent: 'center' }}>
      {Icon ? <Icon size={16} color={fg} /> : <Spinner size={16} color={fg} />}
    </View>
  );
}

function TaskRowItem({ task, last }: { task: Task; last: boolean }) {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { groups, sideChats } = useStore();
  const who = `${originName(task.origin, groups, sideChats)} → ${modelName(task.modelId)}`;
  const mins = task.minutes != null ? L(`${task.minutes} 分钟`, `${task.minutes} min`) : '';
  const tokens = task.tokens ? `${fmtTokens(task.tokens)} tokens` : '';
  const n = task.toolUseCount;
  const detail = task.status === '进行中' ? task.step || (n ? L(`第 ${n} 步`, `step ${n}`) : L('在想', 'thinking'))
    : task.status === '已取消' ? L('已停掉', 'stopped')
      : task.timedOut ? L('到时间上限停了，没做完', 'hit the time limit, unfinished')
        : task.status === '失败' ? L(`没做成${task.error ? `：${task.error}` : ''}`, `failed${task.error ? `: ${task.error}` : ''}`)
          : [mins, tokens].filter(Boolean).join(' · ');
  const right = task.status === '进行中' ? mins : task.finishedAt || task.createdAt;
  return <ListRow icon={<StatusDot task={task} />} title={task.title} sub={[who, detail].filter(Boolean).join(' · ')} last={last} onPress={() => nav.navigate('Task', { id: task.id })}
    right={right ? <T v="caption" color={t.ink3} style={{ fontVariant: ['tabular-nums'] }}>{right}</T> : undefined} />;
}

/** 顶上的额度：今天派了几个 / 上限，单个最长几分钟，超了先问你。 */
function QuotaCard({ q }: { q: TaskQuota }) {
  const t = useTheme();
  if (q.today == null) return null;
  const full = q.today >= q.limit;
  return (
    <Card style={{ gap: 10, marginTop: space.lg }}>
      <View style={{ flexDirection: 'row', alignItems: 'baseline', gap: 8 }}>
        <T v="label" color={t.ink3}>{L('今天', 'TODAY')}</T>
        <T v="title" style={{ fontSize: 28, fontWeight: '800', fontVariant: ['tabular-nums'] }}>{q.today}</T>
        <T v="headline" color={t.ink3}>{L(`/ ${q.limit} 个`, `/ ${q.limit}`)}</T>
        <View style={{ flex: 1 }} />
        {q.tokens ? <T v="callout" color={t.ink2}>{fmtTokens(q.tokens)} tokens</T> : null}
      </View>
      <View style={{ height: 8, borderRadius: 4, backgroundColor: t.track, overflow: 'hidden' }}
        accessible accessibilityRole="progressbar" accessibilityLabel={L(`今天派了 ${q.today} 个，上限 ${q.limit} 个`, `${q.today} of ${q.limit} started today`)}>
        <View style={{ width: `${Math.min(100, (q.today / Math.max(1, q.limit)) * 100)}%`, height: 8, borderRadius: 4, backgroundColor: full ? t.warn : t.cyan }} />
      </View>
      <View style={{ gap: 4 }}>
        <View style={styles.qline}><Clock size={14} color={t.ink2} /><T v="callout" color={t.ink2} style={{ flex: 1 }}>{L(`单个最长 ${q.maxMinutes} 分钟，到点自动停`, `Each task stops after ${q.maxMinutes} minutes`)}</T></View>
        <View style={styles.qline}><Inbox size={14} color={t.ink2} /><T v="callout" color={t.ink2} style={{ flex: 1 }}>{L('超出的先进「等你点头」，你点了才派', 'Anything over the limit waits in "Needs your OK" until you say yes')}</T></View>
      </View>
    </Card>
  );
}

export function TasksScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { tasks, taskQuota, reload, loading, dataErrors, connected } = useStore();
  const start = dayStartMs();
  const running = tasks.filter((x) => x.status === '进行中');
  const today = tasks.filter((x) => x.status !== '进行中' && (x.createdMs ?? 0) >= start);
  const earlier = tasks.filter((x) => x.status !== '进行中' && (x.createdMs ?? 0) < start);
  const sections: [string, Task[]][] = [[L('进行中', 'Running'), running], [L('今天做完', 'Finished today'), today], [L('更早', 'Earlier'), earlier]];
  return (
    <Screen>
      <NavHeader title={L('任务', 'Tasks')} onBack={() => nav.goBack()} />
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl }}
        refreshControl={<PullRefresh onRefresh={() => reload('tasks')} />}>
        <T v="callout" color={t.ink2}>{L(
          `${agentName()} 派出去的后台任务。每个任务是 OpenClaw 的一个子会话：派给谁、做到哪、怎么做的，都在这里；对话里也有一张卡跟着它。在对话里让 ${agentName()}「派给 Fable 做」就会出现一个。`,
          `Background tasks ${agentName()} has sent out. Each is an OpenClaw sub-session: who got it, how far along it is and how it was done. A card in the chat follows it too. Ask ${agentName()} to "send this to Fable" and one shows up.`,
        )}</T>
        {taskQuota ? <QuotaCard q={taskQuota} /> : null}
        {dataErrors.tasks ? <Card style={{ marginTop: space.lg }}><T v="callout" color={t.bad}>{L(`读不到任务：${dataErrors.tasks}`, `Couldn't load tasks: ${dataErrors.tasks}`)}</T></Card> : null}
        {sections.filter(([, list]) => list.length).map(([title, list]) => (
          <View key={title}>
            <SectionLabel>{title}</SectionLabel>
            <Card style={{ paddingVertical: space.xs }}>
              {list.map((task, i) => <TaskRowItem key={task.id} task={task} last={i === list.length - 1} />)}
            </Card>
          </View>
        ))}
        {!tasks.length && !loading.tasks && !dataErrors.tasks ? (
          <Card style={{ marginTop: space.lg }}><T v="callout" color={t.ink2}>{connected ? L('还没有派出去的任务。', 'No tasks sent out yet.') : L('没连上服务器。', 'Not connected to the server.')}</T></Card>
        ) : null}
      </ScrollView>
    </Screen>
  );
}

const stepIcon: Record<TaskStep['kind'], typeof Check> = { start: CircleDot, tool: Terminal, think: Lightbulb, check: ListChecks, done: Check };

function RunCard({ run, modelId }: { run: TaskRun; modelId: string | null }) {
  const t = useTheme();
  const [showSteps, setShowSteps] = useState(run.status === 'running');
  return (
    <Card style={{ gap: space.sm }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
        {run.status === 'running' ? <LoaderCircle size={16} color={t.cyan} /> : run.status === 'failed' ? <CircleX size={16} color={t.bad} /> : <Check size={16} color={t.good} />}
        <T v="headline" style={{ flex: 1 }}>{L(`第 ${run.version} 轮`, `Round ${run.version}`)}</T>
        <T v="caption" color={t.ink3}>{run.startedAt}{run.finishedAt ? ` – ${run.finishedAt}` : ''} · {fmtTokens(run.tokens)} tokens</T>
      </View>
      {run.note ? (
        <View style={[styles.note, { backgroundColor: t.goldSoft }]}>
          <Pencil size={13} color={t.gold} />
          <T v="callout" color={t.ink} style={{ flex: 1 }}>{L(`你的意见：${run.note}`, `Your feedback: ${run.note}`)}</T>
        </View>
      ) : null}
      <Pressable onPress={() => setShowSteps((v) => !v)} accessibilityRole="button" style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
        <Eye size={13} color={t.ink3} />
        <T v="caption" color={t.ink3}>{showSteps ? L('收起过程', 'Hide steps') : L(`看过程（${run.steps.length} 步）`, `Show steps (${run.steps.length})`)}</T>
      </Pressable>
      {showSteps ? (
        <View style={{ gap: 8, paddingLeft: 2 }}>
          {run.steps.map((s, i) => {
            const Icon = stepIcon[s.kind];
            return (
              <View key={`${s.time}-${i}`} style={{ flexDirection: 'row', gap: space.sm, alignItems: 'flex-start' }}>
                <Icon size={14} color={s.kind === 'done' ? t.good : s.kind === 'tool' ? t.cyan : s.kind === 'check' ? t.bad : t.ink3} style={{ marginTop: 3 }} />
                <T v="callout" color={t.ink2} style={{ flex: 1 }}>{s.text}</T>
                <T v="caption" color={t.ink3}>{s.time}</T>
              </View>
            );
          })}
          {run.status === 'running' ? <T v="caption" color={t.cyan}>{L(`${modelName(modelId)} 还在做…`, `${modelName(modelId)} is still working…`)}</T> : null}
        </View>
      ) : null}
      {run.result ? (
        <View style={[styles.result, { backgroundColor: t.bg }]}>
          <T v="body">{run.result.summary}</T>
        </View>
      ) : null}
    </Card>
  );
}

export function TaskScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const sheet = useSheet();
  const { id } = useRoute<any>().params as { id: string };
  const { tasks, groups, sideChats, cancelTask } = useStore();
  const [task, setTask] = useState<Task | undefined>(() => tasks.find((x) => x.id === id));
  const [err, setErr] = useState('');
  const fetchTask = useCallback(() => dataApi.task(id).then((x) => { setTask(x); setErr(''); }).catch((e) => setErr(e instanceof Error ? e.message : String(e))), [id]);
  useEffect(() => { fetchTask(); }, [fetchTask]);
  if (!task) {
    return (
      <Screen>
        <NavHeader title={L('任务', 'Task')} onBack={() => nav.goBack()} />
        <View style={{ padding: space.lg }}><T v="callout" color={err ? t.bad : t.ink2}>{err || L('正在读…', 'Loading…')}</T></View>
      </Screen>
    );
  }
  // 左栏只有 64pt 宽：英文标签挑短词
  const rows: [string, string][] = [
    [L('派发者', 'From'), originName(task.origin, groups, sideChats)],
    [L('交给', 'Model'), task.modelId ?? '—'],
    [L('子会话', 'Session'), task.sessionKey],
    [L('时间', 'Time'), L(`${task.createdAt}${task.finishedAt ? `，${task.finishedAt} 结束` : ''}`, `${task.createdAt}${task.finishedAt ? `, ended ${task.finishedAt}` : ''}`)],
    [L('用量', 'Usage'), `${fmtTokens(task.tokens)} tokens${task.costUsd != null ? L(` · 约 $${task.costUsd.toFixed(3)}（按 API 价估算）`, ` · about $${task.costUsd.toFixed(3)} (estimated at API prices)`) : ''}`],
  ];
  const cancel = () => cancelTask(task.id).then(fetchTask).catch((e) => Alert.alert(L('没取消成', "Couldn't cancel"), e instanceof Error ? e.message : String(e)));
  return (
    <Screen>
      <NavHeader title={task.title} onBack={() => nav.goBack()} right={<Pill label={statusLabel(task.status)} tone={statusTone(task.status)} />} />
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl }} refreshControl={<PullRefresh onRefresh={fetchTask} />}>
        {task.brief ? (
          <>
            <SectionLabel>{L('任务', 'Task')}</SectionLabel>
            <Card><T v="callout">{task.brief}</T></Card>
          </>
        ) : null}
        <Card style={{ paddingVertical: space.xs, marginTop: space.md }}>
          {rows.map(([k, v], i) => (
            <View key={k} style={[styles.meta, i < rows.length - 1 && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }]}>
              <T v="callout" color={t.ink3} style={{ width: 64 }}>{k}</T>
              <T v="callout" style={{ flex: 1 }} numberOfLines={3}>{v}</T>
            </View>
          ))}
        </Card>
        {task.error ? <Card style={{ marginTop: space.md }}><T v="callout" color={t.bad}>{L(`失败原因：${task.error}`, `Why it failed: ${task.error}`)}</T></Card> : null}

        <SectionLabel>{L('过程', 'Steps')}</SectionLabel>
        {task.runs?.length ? (
          <View style={{ gap: space.md }}>{task.runs.map((r) => <RunCard key={r.version} run={r} modelId={task.modelId} />)}</View>
        ) : (
          <Card><T v="callout" color={t.ink2}>{task.summary || L('子会话还没有记录。', 'Nothing recorded in the sub-session yet.')}</T></Card>
        )}

        <View style={{ flexDirection: 'row', gap: space.sm, marginTop: space.xl }}>
          {task.status === '进行中' ? <Btn label={L('取消', 'Cancel')} kind="danger" flex icon={<CircleX size={15} color={t.bad} />} onPress={cancel} /> : null}
          {task.status === '完成' ? (
            <Btn label={L('改一下', 'Revise')} flex icon={<Pencil size={15} color={t.onGold} />}
              onPress={() => sheet.open({ title: L('哪里要改', 'What to change'), content: (close) => <ReviseSheetContent task={task} close={() => { close(); fetchTask(); }} /> })} />
          ) : null}
        </View>
        <T v="caption" color={t.ink3} style={{ marginTop: space.md, paddingHorizontal: space.xs }}>
          {L(
            '「改一下」发给同一个子会话，它接着上一轮改；「取消」会停掉子会话。下拉刷新看最新进度。',
            '"Revise" goes to the same sub-session, which picks up from the last round; "Cancel" stops the sub-session. Pull down to refresh for the latest progress.',
          )}
        </T>
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  qline: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  note: { flexDirection: 'row', alignItems: 'flex-start', gap: 6, borderRadius: radius.sm, padding: space.sm },
  result: { borderRadius: radius.md, padding: space.md },
  meta: { flexDirection: 'row', gap: space.md, paddingVertical: 10 },
});
