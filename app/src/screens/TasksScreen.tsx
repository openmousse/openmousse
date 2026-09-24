import React, { useCallback, useEffect, useState } from 'react';
import { agentName } from '../brand';
import { Alert, Pressable, RefreshControl, ScrollView, StyleSheet, View } from 'react-native';
import { useNavigation, useRoute } from '@react-navigation/native';
import { dataApi } from '../api/data';
import { Check, CircleDot, CircleX, Eye, LoaderCircle, Lightbulb, ListChecks, Pencil, Terminal } from '../components/icons';
import { useSheet } from '../components/Sheet';
import { ReviseSheetContent, fmtTokens, modelName, originName } from '../components/TaskCard';
import { Btn, Card, ListRow, NavHeader, Pill, Screen, SectionLabel, T } from '../components/ui';
import type { Task, TaskRun, TaskStep } from '../data/types';
import { useStore } from '../store';
import { radius, space, useTheme } from '../theme';

const statusTone = (s: Task['status']) => (s === '进行中' ? 'cyan' : s === '完成' ? 'good' : 'bad');

function TaskRowItem({ task, last }: { task: Task; last: boolean }) {
  const nav = useNavigation<any>();
  const { groups, sideChats } = useStore();
  const tools = task.toolUseCount ? ` · ${task.toolUseCount} 次工具` : '';
  const sub = `${originName(task.origin, groups, sideChats)} → ${modelName(task.modelId)}${tools} · ${fmtTokens(task.tokens)} tokens · ${task.createdAt}`;
  return <ListRow title={task.title} sub={sub} last={last} onPress={() => nav.navigate('Task', { id: task.id })}
    right={<Pill label={task.status} tone={statusTone(task.status)} />} />;
}

export function TasksScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { tasks, reload, loading, dataErrors, connected } = useStore();
  const by = (s: Task['status'][]) => tasks.filter((x) => s.includes(x.status));
  const sections: [string, Task[]][] = [['进行中', by(['进行中'])], ['做完的', by(['完成'])], ['失败或取消', by(['失败', '已取消'])]];
  return (
    <Screen>
      <NavHeader title="任务" onBack={() => nav.goBack()} />
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl }}
        refreshControl={<RefreshControl refreshing={!!loading.tasks} onRefresh={() => reload('tasks')} />}>
        <T v="callout" color={t.ink2}>{`${agentName()} 派出去的活。每个任务是 OpenClaw 的一个子会话：派给谁、做到哪、怎么做的，都在这里。在对话里让 ${agentName()}「派给 Fable 做」就会出现一个。`}</T>
        {dataErrors.tasks ? <Card style={{ marginTop: space.lg }}><T v="callout" color={t.bad}>读不到任务：{dataErrors.tasks}</T></Card> : null}
        {sections.filter(([, list]) => list.length).map(([title, list]) => (
          <View key={title}>
            <SectionLabel>{title}</SectionLabel>
            <Card style={{ paddingVertical: space.xs }}>
              {list.map((task, i) => <TaskRowItem key={task.id} task={task} last={i === list.length - 1} />)}
            </Card>
          </View>
        ))}
        {!tasks.length && !loading.tasks && !dataErrors.tasks ? (
          <Card style={{ marginTop: space.lg }}><T v="callout" color={t.ink2}>{connected ? '还没有派出去的任务。' : '没连上服务器。'}</T></Card>
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
        <T v="headline" style={{ flex: 1 }}>第 {run.version} 轮</T>
        <T v="caption" color={t.ink3}>{run.startedAt}{run.finishedAt ? ` – ${run.finishedAt}` : ''} · {fmtTokens(run.tokens)} tokens</T>
      </View>
      {run.note ? (
        <View style={[styles.note, { backgroundColor: t.goldSoft }]}>
          <Pencil size={13} color={t.gold} />
          <T v="callout" color={t.ink} style={{ flex: 1 }}>你的意见：{run.note}</T>
        </View>
      ) : null}
      <Pressable onPress={() => setShowSteps((v) => !v)} accessibilityRole="button" style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
        <Eye size={13} color={t.ink3} />
        <T v="caption" color={t.ink3}>{showSteps ? '收起过程' : `看过程（${run.steps.length} 步）`}</T>
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
          {run.status === 'running' ? <T v="caption" color={t.cyan}>{modelName(modelId)} 还在做…</T> : null}
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
  const [loading, setLoading] = useState(false);
  const fetchTask = useCallback(() => dataApi.task(id).then((x) => { setTask(x); setErr(''); }).catch((e) => setErr(e instanceof Error ? e.message : String(e))), [id]);
  const load = useCallback(() => { setLoading(true); fetchTask().finally(() => setLoading(false)); }, [fetchTask]);
  useEffect(() => { fetchTask(); }, [fetchTask]);
  if (!task) {
    return (
      <Screen>
        <NavHeader title="任务" onBack={() => nav.goBack()} />
        <View style={{ padding: space.lg }}><T v="callout" color={err ? t.bad : t.ink2}>{err || '正在读…'}</T></View>
      </Screen>
    );
  }
  const rows: [string, string][] = [
    ['派发者', originName(task.origin, groups, sideChats)],
    ['交给', task.modelId ?? '—'],
    ['子会话', task.sessionKey],
    ['时间', `${task.createdAt}${task.finishedAt ? `，${task.finishedAt} 结束` : ''}`],
    ['用量', `${fmtTokens(task.tokens)} tokens${task.costUsd != null ? ` · 约 $${task.costUsd.toFixed(3)}（按 API 价估算）` : ''}`],
  ];
  const cancel = () => cancelTask(task.id).then(load).catch((e) => Alert.alert('没取消成', e instanceof Error ? e.message : String(e)));
  return (
    <Screen>
      <NavHeader title={task.title} onBack={() => nav.goBack()} right={<Pill label={task.status} tone={statusTone(task.status)} />} />
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl }} refreshControl={<RefreshControl refreshing={loading} onRefresh={load} />}>
        {task.brief ? (
          <>
            <SectionLabel>任务</SectionLabel>
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
        {task.error ? <Card style={{ marginTop: space.md }}><T v="callout" color={t.bad}>失败原因：{task.error}</T></Card> : null}

        <SectionLabel>过程</SectionLabel>
        {task.runs?.length ? (
          <View style={{ gap: space.md }}>{task.runs.map((r) => <RunCard key={r.version} run={r} modelId={task.modelId} />)}</View>
        ) : (
          <Card><T v="callout" color={t.ink2}>{task.summary || '子会话还没有记录。'}</T></Card>
        )}

        <View style={{ flexDirection: 'row', gap: space.sm, marginTop: space.xl }}>
          {task.status === '进行中' ? <Btn label="取消" kind="danger" flex icon={<CircleX size={15} color={t.bad} />} onPress={cancel} /> : null}
          {task.status === '完成' ? (
            <Btn label="改一下" flex icon={<Pencil size={15} color={t.onGold} />}
              onPress={() => sheet.open({ title: '哪里要改', content: (close) => <ReviseSheetContent task={task} close={() => { close(); load(); }} /> })} />
          ) : null}
        </View>
        <T v="caption" color={t.ink3} style={{ marginTop: space.md, paddingHorizontal: space.xs }}>
          「改一下」发给同一个子会话，它接着上一轮改；「取消」会停掉子会话。下拉刷新看最新进度。
        </T>
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  note: { flexDirection: 'row', alignItems: 'flex-start', gap: 6, borderRadius: radius.sm, padding: space.sm },
  result: { borderRadius: radius.md, padding: space.md },
  meta: { flexDirection: 'row', gap: space.md, paddingVertical: 10 },
});
