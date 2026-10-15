// 工程看板（2026-10-15，画布「待办合进今天」第 11 张；server/engboard.py）：Claude Code 的交接本 todos.md 单向同步过来，只读。
// 工程的事不进「今天」的待处理、没有截止日：手机上只看做了哪些、没做哪些、进度到哪；拍板、动手、改状态都在电脑上的 Claude Code 里。
// 顶上三个数（这周做完 / 没做 / 等你），两个视图：进度（主线分格 + 各条线做完 / 总数和状态）、看板（没做 / 等你 / 这周做完三列，点一张看原文）。
// 从「今天」报告里的工程看板卡点进来；网页版 ?screen=EngBoard。
import { useNavigation } from '@react-navigation/native';
import React, { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import * as todayApi from '../api/today';
import type { EngBoard, EngCard, EngColumn, EngItem, EngLine, EngLineDetail, EngStatus } from '../api/today';
import { ChevronLeft } from '../components/icons';
import { useSheet } from '../components/Sheet';
import { ENG_SLOT } from '../components/today/Cats';
import { monthDay } from '../components/today/parts';
import { Btn, Disclosure, PullRefresh, Screen, T } from '../components/ui';
import { KanbanBoard, ProgressBar, Segments, seriesColor, toneColors, type KanbanBoardColumn, type KanbanCard } from '../components/viz';
import { L } from '../i18n';
import { radius, space, useTheme } from '../theme';

type View_ = 'progress' | 'board';
const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));
const STATUS_TONE: Record<EngStatus, 'cyan' | 'warn' | 'neutral' | 'good'> = { doing: 'cyan', wait: 'warn', queue: 'neutral', done: 'good' };

let cached: EngBoard | null = null;

export function EngBoardScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const sheet = useSheet();
  const [view, setView] = useState<View_>('progress');
  const [data, setData] = useState<EngBoard | null>(cached);
  const [error, setError] = useState('');
  const load = useCallback(() => todayApi.loadEng().then((d) => { cached = d; setData(d); setError(''); }, (e) => setError(errText(e))), []);
  useEffect(() => { load(); }, [load]);
  const openCard = (card: KanbanCard) => {
    const c = data?.columns.flatMap((x) => x.cards).find((x) => x.id === card.key);
    if (c) sheet.open({ title: c.title, content: () => <CardText c={c} /> });
  };
  const synced = data?.syncedLabel ?? '';
  return (
    <Screen>
      <View style={styles.header}>
        <Pressable onPress={() => nav.goBack()} hitSlop={12} accessibilityRole="button" accessibilityLabel={L('返回', 'Back')} style={styles.back}>
          <ChevronLeft size={26} color={t.gold} />
        </Pressable>
        <View style={{ flex: 1, gap: 1 }}>
          <T v="headline" style={{ fontSize: 20, fontWeight: '700' }}>{L('工程看板', 'Engineering')}</T>
          <T v="caption" color={t.ink3} style={{ fontWeight: '400' }} numberOfLines={1}>
            {[L('从 todos.md 同步 · 只读，处理在电脑上', 'Synced from todos.md · read only; work happens on the computer'), synced ? L(`更新于 ${synced}`, `updated ${synced}`) : ''].filter(Boolean).join(' · ')}
          </T>
        </View>
      </View>
      <View style={{ paddingHorizontal: space.lg }}>
        <View style={[styles.seg, { backgroundColor: t.surface }]} accessibilityRole="tablist" accessibilityLabel={L('视图', 'View')}>
          {([['progress', L('进度', 'Progress')], ['board', L('看板', 'Board')]] as [View_, string][]).map(([k, label]) => {
            const on = view === k;
            return (
              <Pressable key={k} onPress={() => setView(k)} accessibilityRole="tab" accessibilityState={{ selected: on }}
                style={[styles.segItem, on && { backgroundColor: t.mode === 'dark' ? t.line : t.surface2 }]}>
                <Text style={[styles.segText, { color: on ? t.ink : t.ink2 }]}>{label}</Text>
              </Pressable>
            );
          })}
        </View>
        {data ? <Stats d={data} /> : null}
      </View>
      {!data ? (
        <View style={{ padding: space.lg, gap: space.md }}>
          {error ? (
            <>
              <T v="callout" color={t.bad}>{L(`无法加载：${error}`, `Couldn't load: ${error}`)}</T>
              <Btn label={L('重试', 'Retry')} kind="quiet" onPress={load} />
            </>
          ) : <ActivityIndicator color={t.ink3} />}
        </View>
      ) : view === 'progress' ? (
        <ScrollView contentContainerStyle={{ padding: space.lg, paddingTop: 0, paddingBottom: space.xxl }} refreshControl={<PullRefresh onRefresh={load} />}>
          <Progress d={data} />
          {error || data.error ? <T v="caption" color={t.bad} style={styles.note}>{L(`未能刷新：${error || data.error}`, `Couldn't refresh: ${error || data.error}`)}</T> : null}
          <T v="caption" color={t.ink3} style={[styles.note, { textAlign: 'center' }]}>
            {L('手机上只看，处理都在电脑上的 Claude Code 里。', 'This is read only. Decisions and work happen in Claude Code on the computer.')}
          </T>
        </ScrollView>
      ) : (
        <View style={{ flex: 1, paddingHorizontal: space.lg, paddingTop: 14, paddingBottom: space.lg }}>
          <KanbanBoard columns={data.columns.map(kitCol)} onPressCard={openCard} />
        </View>
      )}
    </Screen>
  );
}

/** 三个数：这周做完（青）/ 没做 / 等你（琥珀），和「今天」报告卡一样。 */
function Stats({ d }: { d: EngBoard }) {
  const t = useTheme();
  const items: [number, string, string][] = [[d.weekDone, L('这周做完', 'Done this week'), t.cyan], [d.todo, L('没做', 'Not started'), t.ink], [d.wait, L('等你', 'Waiting on you'), t.warn]];
  return (
    <View style={styles.stats}>
      {items.map(([n, label, color]) => (
        <View key={label} style={[styles.stat, { backgroundColor: t.surface }]} accessible accessibilityLabel={`${label} ${n}`}>
          <Text style={[styles.statNum, { color }]}>{n}</Text>
          <T v="caption" color={t.ink2} style={{ fontWeight: '400' }} numberOfLines={1}>{label}</T>
        </View>
      ))}
    </View>
  );
}

function Progress({ d }: { d: EngBoard }) {
  const t = useTheme();
  const [older, setOlder] = useState(false);
  const cyan = seriesColor(t, ENG_SLOT);
  return (
    <View>
      {d.mainCells.length ? (
        <>
          <T v="callout" color={t.ink3} style={styles.label}>{L('主线', 'Main line')}</T>
          <View style={[styles.card, { backgroundColor: t.surface, padding: 14, gap: 10 }]}>
            {d.mainCells.map((m) => (
              <View key={m.name} style={styles.mainRow}>
                <T v="callout" style={styles.mainName} numberOfLines={1}>{m.name}</T>
                <View style={{ flex: 1 }}>
                  <Segments done={m.done} total={m.total} cells={m.cells} color={cyan} label={L(`${m.name}：完成 ${m.done} / ${m.total}`, `${m.name}: ${m.done} of ${m.total} done`)} />
                </View>
                <T v="caption" color={t.ink2} style={styles.count}>{`${m.done}/${m.total}`}</T>
              </View>
            ))}
            {d.mainNote ? <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{d.mainNote}</T> : null}
          </View>
        </>
      ) : null}
      <T v="callout" color={t.ink3} style={styles.label}>{L('各条线', 'Lines of work')}</T>
      <View style={[styles.card, { backgroundColor: t.surface }]}>
        {d.lines.map((l, i) => <LineRow key={l.id} l={l} sep={i < d.lines.length - 1 || d.older.length > 0} />)}
        {!d.lines.length ? <T v="callout" color={t.ink3} style={{ padding: 14 }}>{L('暂无进行中的线', 'No lines in progress')}</T> : null}
        {d.older.length ? (
          <>
            <Pressable onPress={() => setOlder((v) => !v)} accessibilityRole="button" accessibilityState={{ expanded: older }}
              style={({ pressed }) => [styles.fold, { opacity: pressed ? 0.6 : 1 }, older && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }]}>
              <T v="callout" color={t.ink2} style={{ flex: 1 }}>{L(`更早的线 · ${d.older.length}`, `Earlier lines · ${d.older.length}`)}</T>
              <Disclosure open={older} />
            </Pressable>
            {older ? d.older.map((l, i) => <LineRow key={l.id} l={l} sep={i < d.older.length - 1} />) : null}
          </>
        ) : null}
      </View>
    </View>
  );
}

/** 一条线：名字、做完 / 总数的条、几比几、状态（在做 / 等你 / 排队 / 做完）。点开看这条线里的每一件。 */
function LineRow({ l, sep }: { l: EngLine; sep: boolean }) {
  const t = useTheme();
  const sheet = useSheet();
  const [bg, fg] = toneColors(t, STATUS_TONE[l.status] ?? 'neutral');
  return (
    <Pressable onPress={() => sheet.open({ title: l.name, content: () => <LineDetail id={l.id} /> })} accessibilityRole="button"
      accessibilityLabel={L(`${l.name}：完成 ${l.done} / ${l.total}，${l.statusLabel}`, `${l.name}: ${l.done} of ${l.total} done, ${l.statusLabel}`)}
      style={({ pressed }) => [styles.lineRow, sep && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }, { opacity: pressed ? 0.6 : 1 }]}>
      <T v="callout" style={styles.lineName} numberOfLines={1}>{l.name}</T>
      <ProgressBar value={l.done} target={Math.max(1, l.total)} height={6} color={seriesColor(t, ENG_SLOT)} style={{ flex: 1 }} />
      <T v="caption" color={t.ink2} style={styles.count}>{`${l.done}/${l.total}`}</T>
      <View style={[styles.chip, { backgroundColor: bg }]}><Text style={[styles.chipText, { color: fg }]} numberOfLines={1}>{l.statusLabel}</Text></View>
    </Pressable>
  );
}

/** 一条线里的事：没做 / 等你 / 做完，每件一行（点开看原文）。 */
function LineDetail({ id }: { id: string }) {
  const t = useTheme();
  const [d, setD] = useState<EngLineDetail | null>(null);
  const [err, setErr] = useState('');
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => {
    let on = true;
    todayApi.loadEngLine(id).then((x) => { if (on) setD(x); }, (e) => { if (on) setErr(errText(e)); });
    return () => { on = false; };
  }, [id]);
  if (err) return <T v="callout" color={t.bad}>{L(`无法加载：${err}`, `Couldn't load: ${err}`)}</T>;
  if (!d) return <ActivityIndicator color={t.ink3} style={{ marginVertical: space.lg }} />;
  const group = (title: string, items: EngItem[], color: string) => (items.length ? (
    <View key={title} style={{ gap: 6 }}>
      <T v="caption" color={t.ink3}>{`${title} · ${items.length}`}</T>
      <View style={[styles.card, { backgroundColor: t.surface }]}>
        {items.map((x, i) => (
          <Pressable key={x.id} onPress={() => setOpen((v) => (v === x.id ? null : x.id))} accessibilityRole="button" accessibilityState={{ expanded: open === x.id }}
            style={[styles.itemRow, i < items.length - 1 && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }]}>
            <View style={[styles.itemDot, { backgroundColor: color }]} />
            <View style={{ flex: 1, gap: 3 }}>
              <T v="callout" color={x.state === 'done' ? t.ink2 : t.ink}>{x.title}</T>
              {x.doneAt ? <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L(`${monthDay(x.doneAt)} 做完`, `done ${monthDay(x.doneAt)}`)}</T> : null}
              {open === x.id ? <T v="caption" color={t.ink2} style={{ fontWeight: '400', lineHeight: 18 }}>{x.text}</T> : null}
            </View>
          </Pressable>
        ))}
      </View>
    </View>
  ) : null);
  return (
    <View style={{ gap: space.md }}>
      <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{d.heading}</T>
      {group(L('等你', 'Waiting on you'), d.wait, t.warn)}
      {group(L('没做', 'Not started'), d.todo, t.ink3)}
      {group(L('做完', 'Done'), d.done, seriesColor(t, ENG_SLOT))}
      <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L('要改状态或接着做，在电脑上的 Claude Code 里说。', 'To change it or pick it up, ask Claude Code on the computer.')}</T>
    </View>
  );
}

const kitCard = (c: EngCard): KanbanCard => ({ key: c.id, title: c.title, tags: c.tags });
const kitCol = (c: EngColumn): KanbanBoardColumn => ({ key: c.id, title: c.label, slot: c.color ?? -1, cards: c.cards.map(kitCard), total: c.count, drop: false, more: c.more });

/** 点开一张卡：交接本里的原文（只读）。 */
function CardText({ c }: { c: EngCard }) {
  const t = useTheme();
  return (
    <View style={{ gap: space.md }}>
      <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>
        {[c.data.line, c.data.doneAt ? L(`${monthDay(c.data.doneAt)} 做完`, `done ${monthDay(c.data.doneAt)}`) : ''].filter(Boolean).join(' · ')}
      </T>
      <View style={[styles.box, { backgroundColor: t.surface }]}><T v="callout">{c.data.text ?? c.title}</T></View>
      <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L('要改状态或接着做，在电脑上的 Claude Code 里说。', 'To change it or pick it up, ask Claude Code on the computer.')}</T>
    </View>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingRight: space.md, paddingLeft: 4, paddingTop: 6, paddingBottom: 6 },
  back: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center' },
  seg: { flexDirection: 'row', borderRadius: radius.md, padding: 3, gap: 3, marginTop: 6 },
  segItem: { flex: 1, height: 32, borderRadius: 9, alignItems: 'center', justifyContent: 'center' },
  segText: { fontSize: 14, fontWeight: '600' },
  stats: { flexDirection: 'row', gap: 8, paddingTop: 12, paddingBottom: 4 },
  stat: { flex: 1, borderRadius: 14, paddingHorizontal: 12, paddingVertical: 10, gap: 2 },
  statNum: { fontSize: 22, fontWeight: '700', fontVariant: ['tabular-nums'] },
  label: { fontSize: 13, fontWeight: '700', paddingHorizontal: space.xs, marginTop: space.lg, marginBottom: space.sm },
  card: { borderRadius: radius.lg, overflow: 'hidden' },
  mainRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  mainName: { width: 92, fontSize: 13, fontWeight: '600' },
  count: { width: 38, textAlign: 'right', fontVariant: ['tabular-nums'], fontWeight: '400' },
  lineRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 14, paddingVertical: 10, minHeight: 40 },
  lineName: { width: 118, fontSize: 13, fontWeight: '600' },
  itemRow: { flexDirection: 'row', gap: 10, paddingHorizontal: 12, paddingVertical: 10, alignItems: 'flex-start' },
  itemDot: { width: 8, height: 8, borderRadius: 4, marginTop: 6 },
  chip: { borderRadius: 6, paddingHorizontal: 6, paddingVertical: 2, minWidth: 38, alignItems: 'center' },
  chipText: { fontSize: 11, fontWeight: '600' },
  fold: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 14, height: 42 },
  note: { marginTop: space.lg, paddingHorizontal: space.xs, fontWeight: '400' },
  box: { borderRadius: radius.md, padding: space.md },
});
