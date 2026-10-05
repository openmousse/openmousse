// 原生学习屏（2026-09-30）：学习台首页（今天 + 按课列节）→ 一节课（路线 / 自测 / 闪卡 / 小测 / 课件 / 问）。
// 手机上复习、刷卡、做题；课件和学习页并排看、写长答案用电脑上的 /study。数据都在服务器（api/study.ts），打勾、自测答案、复习两边共用。
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, Linking, Pressable, ScrollView, StyleSheet, TextInput, View } from 'react-native';
import { useFocusEffect, useNavigation, useRoute } from '@react-navigation/native';
import Reanimated from 'react-native-reanimated';
import * as study from '../api/study';
import { Check, ChevronRight, FileText, Film, Mic, Monitor, NotebookText, Plus, Send } from '../components/icons';
import { useBottomInset } from '../components/keyboard';
import { Markdown } from '../components/Markdown';
import { useSheet } from '../components/Sheet';
import { DeskLinkRow, StudyLine, copyDeskLink, daysLeft, dueText, nextLine, reviewLine, useStudyHome } from '../components/StudyBoard';
import { Btn, Card, NavHeader, PullRefresh, Screen, SectionLabel, Segmented, T, showError } from '../components/ui';
import type { Attachment } from '../data/types';
import { L } from '../i18n';
import { radius, space, type, useTheme } from '../theme';

const STATUS = () => ({
  done: [L('已学完', 'Done'), 'good'], doing: [L('学习中', 'In progress'), 'cyan'], todo: [L('未开始', 'Not started'), 'neutral'],
  ready: [L('材料齐全', 'Ready'), 'cyan'], missing: [L('材料不全', 'Missing'), 'warn'], later: [L('尚未上课', 'Not yet'), 'outline'],
  empty: [L('暂无材料', 'Empty'), 'outline'], info: [L('课前准备', 'Prep'), 'good'],
} as Record<study.SessionStatus, [string, 'good' | 'cyan' | 'neutral' | 'warn' | 'outline']>);

function StatusChip({ s }: { s: study.SessionStatus }) {
  const t = useTheme();
  const [label, tone] = STATUS()[s] ?? [s, 'neutral'];
  const [bg, fg, bd] = tone === 'good' ? [t.goodSoft, t.good, t.goodSoft] : tone === 'cyan' ? [t.cyanSoft, t.cyan, t.cyanSoft] : tone === 'warn' ? [t.warnSoft, t.warn, t.warnSoft]
    : tone === 'outline' ? [t.surface, t.ink3, t.line] : [t.surface2, t.ink2, t.surface2];
  return <View style={[styles.chip, { backgroundColor: bg, borderColor: bd }]}><T v="caption" color={fg} style={{ fontWeight: '600' }}>{label}</T></View>;
}

// —— 学习台首页 ——

export function StudyHomeScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const params = (useRoute<any>().params ?? {}) as { course?: string };
  const { data: h, error, reload } = useStudyHome();
  const [course, setCourse] = useState<string | undefined>(params.course);
  const [seen, setSeen] = useState(params.course);
  if (params.course !== seen) { setSeen(params.course); setCourse(params.course); }
  const cur = course && h?.courses.some((c) => c.name === course) ? course : h?.courses[0]?.name;
  const [outline, setOutline] = useState<study.Outline | null>(null);
  const [oErr, setOErr] = useState('');
  const loadOutline = useCallback(() => {
    if (!cur) return Promise.resolve();
    return study.outline(cur).then((o) => { setOutline(o); setOErr(''); }, (e: unknown) => setOErr(e instanceof Error ? e.message : String(e)));
  }, [cur]);
  useFocusEffect(useCallback(() => { loadOutline(); }, [loadOutline]));
  const sheet = useSheet();
  const rv = h ? reviewLine(h) : null;
  const nx = h ? nextLine(h) : null;
  const d = h?.deadlines[0];
  const openSession = (s: study.OutlineSession) => {
    if (s.page) { nav.navigate('StudySession', { course: cur, page: s.page }); return; }
    sheet.open({ title: `S${s.n ?? ''} ${s.title}`, content: (close) => <SessionSheet course={cur as string} s={s} close={close} onGenerated={loadOutline} /> });
  };
  const rows = (outline && outline.name === cur ? outline.sessions : []);
  return (
    <Screen>
      <NavHeader title={L('学习台', 'Study desk')} onBack={() => nav.goBack()} right={(
        <Pressable onPress={() => nav.navigate('AddCourse', {})} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('添加课程', 'Add a course')} style={styles.headBtn}>
          <Plus size={22} color={t.gold} />
        </Pressable>
      )} />
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingTop: 0, paddingBottom: space.xxl }} refreshControl={<PullRefresh onRefresh={() => Promise.all([reload(), loadOutline()])} />}>
        {!h ? <Card style={{ marginTop: space.md }}><T v="callout" color={error ? t.bad : t.ink2}>{error || L('正在加载…', 'Loading…')}</T></Card> : null}
        {h && (rv || nx || d) ? (
          <>
            <SectionLabel>{L('今天', 'Today')}</SectionLabel>
            <Card style={{ paddingVertical: 0 }}>
              {rv && h.review ? <StudyLine icon="review" title={rv.title} sub={rv.sub} last={!nx && !d}
                onPress={h.review.page ? () => nav.navigate('StudySession', { course: h.review?.course, page: h.review?.page, tab: 'path' }) : undefined} /> : null}
              {nx && h.next ? <StudyLine icon="next" title={nx.title} sub={nx.sub} last={!d} onPress={() => nav.navigate('StudySession', { course: h.next?.course, page: h.next?.page, tab: 'path' })} /> : null}
              {d ? <StudyLine icon="due" title={`${[d.code, d.title].filter(Boolean).join(' ')} · ${dueText(d.due)}`} sub={daysLeft(d.due)} last /> : null}
            </Card>
          </>
        ) : null}
        {h && h.courses.length ? (
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: space.sm, paddingTop: space.xl, paddingBottom: space.md }}>
            {h.courses.map((c) => {
              const on = c.name === cur;
              return (
                <Pressable key={c.name} onPress={() => setCourse(c.name)} accessibilityRole="button" accessibilityState={{ selected: on }}
                  style={[styles.courseChip, { backgroundColor: on ? t.ink : t.surface, borderColor: on ? t.ink : t.line }]}>
                  <T v="callout" color={on ? t.bg : t.ink2} style={{ fontWeight: '600' }}>{c.title}</T>
                </Pressable>
              );
            })}
          </ScrollView>
        ) : null}
        {h && !h.courses.length ? (
          <Card style={{ marginTop: space.md, gap: space.md }}>
            <T v="headline">{L('学习台暂无课程', 'The study desk is empty')}</T>
            <T v="callout" color={t.ink2}>{L('添加第一门课程：可使用大纲、课件或课程网站中的任意一项。', 'Add your first course with whatever you have: a syllabus, files or a course site.')}</T>
            <Btn label={L('添加课程', 'Add a course')} onPress={() => nav.navigate('AddCourse', {})} />
          </Card>
        ) : null}
        {oErr ? <Card><T v="callout" color={t.bad}>{oErr}</T></Card> : null}
        {rows.length ? (
          <Card style={{ paddingVertical: 0 }}>
            {rows.map((s, i) => (
              <Pressable key={`${s.id ?? s.page ?? s.title}-${i}`} onPress={() => openSession(s)} accessibilityRole="button"
                style={({ pressed }) => [styles.sRow, i > 0 && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }, { opacity: pressed ? 0.6 : 1 }]}>
                <View style={[styles.code, { backgroundColor: t.goldSoft }]}><T v="caption" color={t.gold} style={{ fontWeight: '700' }}>{s.n != null ? `S${s.n}` : L('总览', 'Info')}</T></View>
                <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
                  <T v="headline" numberOfLines={2} style={{ fontSize: 15, fontWeight: s.page ? '600' : '500' }}>{s.title}</T>
                  <T v="caption" color={t.ink3} numberOfLines={1} style={{ fontSize: 12 }}>{sessionSub(s)}</T>
                </View>
                <StatusChip s={s.status} />
              </Pressable>
            ))}
          </Card>
        ) : null}
        <T v="callout" color={t.ink3} style={{ marginTop: space.md, paddingHorizontal: space.lg, fontSize: 13, lineHeight: 19 }}>{L(
          '手机端用于复习、闪卡和小测。如需并排查看课件与学习页或撰写长答案，请使用电脑端学习台。', 'Review, flashcards and quizzes on the phone. For slides and notes side by side, or long answers, use the study desk on a computer.')}</T>
        <View style={{ marginTop: space.md, paddingHorizontal: space.lg }}><DeskLinkRow params={cur ? { course: cur } : undefined} /></View>
      </ScrollView>
    </Screen>
  );
}

function sessionSub(s: study.OutlineSession): string {
  const date = s.date ? (() => { const x = new Date(`${s.date}T12:00:00`); return L(`${x.getMonth() + 1}/${x.getDate()}`, `${x.getDate()}/${x.getMonth() + 1}`); })() : '';
  if (s.progress && s.progress.total) {
    if (s.progress.done >= s.progress.total) return L('路线已完成', 'Path done');
    return s.progress.done ? L(`第 ${s.progress.done + 1} / ${s.progress.total} 步`, `Step ${s.progress.done + 1} of ${s.progress.total}`) : L(`路线共 ${s.progress.total} 步`, `${s.progress.total}-step path`);
  }
  if (s.status === 'missing') return L(`缺 ${s.missing || 1} 项材料`, `${s.missing || 1} item(s) missing`) + (date ? ` · ${date}` : '');
  if (s.status === 'ready') return L('材料齐全，可生成', 'Materials in: ready to generate');
  if (s.status === 'later') return date ? L(`${date} 上课`, `Class on ${date}`) : L('尚未上课', 'Not taught yet');
  if (s.page) return L('已有学习页', 'Has notes');
  return date || '—';
}

/** 还没有学习页的一节：材料齐没齐；齐了能直接生成，没齐去电脑上放材料（或者在对话里发给学习 Agent）。 */
function SessionSheet({ course, s, close, onGenerated }: { course: string; s: study.OutlineSession; close: () => void; onGenerated: () => void }) {
  const t = useTheme();
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState('');
  const gen = () => {
    if (!s.id) return;
    setBusy(true);
    study.generateSessions(course, [s.id], { cards: true, quiz: true }).then((r) => {
      setDone(r.ok === false ? L('材料尚未齐全。', 'Materials are still missing.') : L('已加入队列：完成后会在学习 Agent 的对话中通知（每节约需几分钟）。', "Queued: the study Agent's chat gets a line when it's done (a few minutes)."));
      onGenerated();
    }, (e) => showError(L('未能开始生成', "Couldn't start"), e)).finally(() => setBusy(false));
  };
  const word = s.status === 'ready' ? L('材料齐全，可生成学习页和学习路线。', 'Materials are in: ready to generate the notes and study path.')
    : s.status === 'missing' ? L('材料不全：请在电脑端学习台补充或跳过，也可将文件发送给学习 Agent。', 'Something is missing: add it or skip it on the study desk, or send the file to the study Agent.')
      : s.status === 'later' ? L('尚未上课。课件通常在课前发布，添加后即可生成。', 'Not taught yet. Slides usually arrive before class; add them and generate.')
        : L('这一节暂无材料。', 'No materials for this session yet.');
  return (
    <View style={{ gap: space.md, paddingBottom: space.lg }}>
      <T v="body" color={t.ink2}>{done || word}</T>
      {s.status === 'ready' && !done ? <Btn label={busy ? L('排队中…', 'Queuing…') : L('生成这一节', 'Generate this session')} onPress={gen} /> : null}
      <DeskLinkRow params={{ course }} />
      <Btn kind="quiet" label={L('完成', 'OK')} onPress={close} />
    </View>
  );
}

// —— 一节课 ——

type Tab = 'path' | 'self' | 'cards' | 'quiz' | 'files' | 'ask';

export function StudySessionScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const route = useRoute<any>();
  const { course, page, tab: tab0 } = route.params as { course: string; page: string; tab?: Tab };
  const [tab, setTab] = useState<Tab>(tab0 ?? 'path');
  const [u, setU] = useState<study.StudyUnit | null>(null);
  const [err, setErr] = useState('');
  const [askSeed, setAskSeed] = useState<{ text: string; quote?: string; step?: number | null; at: number } | null>(null);
  const load = useCallback(() => study.unit(course, page).then((x) => { setU(x); setErr(''); }, (e: unknown) => setErr(e instanceof Error ? e.message : String(e))), [course, page]);
  useFocusEffect(useCallback(() => { load(); }, [load]));
  const title = u ? (u.session != null ? `S${u.session} ${String(u.meta.title ?? u.title)}` : u.title) : L('这一节', 'Session');
  const ask = (text: string, extra?: { quote?: string; step?: number | null }) => { setAskSeed({ text, ...extra, at: Date.now() }); setTab('ask'); };
  return (
    <Screen>
      <NavHeader title={title} sub={course} onBack={() => nav.goBack()} right={(
        <Pressable onPress={() => { copyDeskLink({ course, page }).catch((e) => showError(L('获取链接失败', "Couldn't get a link"), e)); }} hitSlop={8} accessibilityRole="button"
          accessibilityLabel={L('在电脑上打开这一节（复制 10 分钟内有效的链接）', 'Open this session on a computer (copies a 10-minute link)')} style={styles.headBtn}>
          <Monitor size={20} color={t.gold} />
        </Pressable>
      )} />
      <View style={{ paddingHorizontal: space.lg, paddingVertical: space.sm }}>
        <Segmented value={tab} onChange={setTab} options={[{ value: 'path', label: L('路线', 'Path') }, { value: 'self', label: L('自测', 'Self-test') }, { value: 'cards', label: L('闪卡', 'Cards') },
          { value: 'quiz', label: L('小测', 'Quiz') }, { value: 'files', label: L('课件', 'Files') }, { value: 'ask', label: L('提问', 'Ask') }]} />
      </View>
      {err ? <Card style={{ margin: space.lg }}><T v="callout" color={t.bad}>{err}</T></Card> : !u ? <ActivityIndicator style={{ marginTop: space.xl }} color={t.ink3} />
        : tab === 'path' ? <PathTab u={u} reload={load} onAsk={ask} onTab={setTab} />
          : tab === 'self' ? <SelfTab u={u} onAsk={ask} />
            : tab === 'cards' ? <CardsTab u={u} />
              : tab === 'quiz' ? <QuizTab u={u} />
                : tab === 'files' ? <FilesTab u={u} />
                  : <AskTab u={u} seed={askSeed} />}
    </Screen>
  );
}

function Reviews({ u, reload }: { u: study.StudyUnit; reload: () => void }) {
  const t = useTheme();
  const [gone, setGone] = useState<Set<string>>(new Set());
  const items = u.review.filter((r) => !gone.has(r.id));
  if (!items.length) return null;
  const done = (r: study.ReviewItem) => {
    setGone((s) => new Set(s).add(r.id));
    study.reviewDone(u.course, r.id).then(() => reload(), (e) => { setGone((s) => { const n = new Set(s); n.delete(r.id); return n; }); showError(L('标记失败', "Couldn't mark it"), e); });
  };
  const from = items.find((r) => r.from?.title)?.from?.title || items[0].source;
  return (
    <Card style={{ backgroundColor: t.goldSoft, gap: space.sm }}>
      <T v="headline" color={t.gold} style={{ fontSize: 14 }}>{L(`复习 · ${items.length} 条`, `To review · ${items.length}`) + (from ? L(` · ${from}`, ` · ${from}`) : '')}</T>
      {items.map((r) => (
        <View key={r.id} style={{ flexDirection: 'row', alignItems: 'flex-start', gap: space.sm }}>
          <T v="body" style={{ flex: 1, fontSize: 15 }}>{r.text}</T>
          <Pressable onPress={() => done(r)} accessibilityRole="button" style={[styles.miniBtn, { borderColor: t.line, backgroundColor: t.surface }]}>
            <T v="caption" style={{ fontSize: 13 }}>{L('已复习', 'Reviewed')}</T>
          </Pressable>
        </View>
      ))}
    </Card>
  );
}

function refLabel(r: study.RouteRef, u: study.StudyUnit): string {
  if (r.type === 'section') return r.label || L('学习页', 'Notes');
  if (r.type === 'file') {
    const rd = u.readings.find((x) => x.file === r.path);
    return (rd ? rd.title : (r.path ?? '').split('/').pop()?.replace(/\.(pdf|pptx?|docx?)$/i, '') ?? '') + (r.page ? ` · p.${r.page}` : '');
  }
  if (r.type === 'video') return u.videos.find((v) => v.path === r.path)?.name || L('讲解视频', 'Video');
  if (r.type === 'recording') return (r.label || L('录播', 'Lecture')) + (r.t != null ? ` · ${Math.floor(r.t / 60)}:${String(Math.floor(r.t % 60)).padStart(2, '0')}` : '');
  return r.type === 'cards' ? L('闪卡', 'Flashcards') : L('小测', 'Quiz');
}

function PathTab({ u, reload, onAsk, onTab }: { u: study.StudyUnit; reload: () => void; onAsk: (text: string, extra?: { step?: number | null }) => void; onTab: (t: Tab) => void }) {
  const t = useTheme();
  const nav = useNavigation<any>();
  const [done, setDone] = useState<number[]>(u.done);
  const [seenDone, setSeenDone] = useState(u.done);
  if (u.done !== seenDone) { setSeenDone(u.done); setDone(u.done); }
  const [open, setOpen] = useState<number | null>(null);
  const items = u.route?.items ?? [];
  const cur = items.findIndex((_, i) => !done.includes(i));
  const shown = open ?? cur;
  const toggle = (i: number) => {
    const want = !done.includes(i);
    setDone((d) => (want ? [...d, i] : d.filter((x) => x !== i)));
    study.setStep(u.course, u.page, i, want).then((r) => setDone(r.done), (e) => { reload(); showError(L('保存失败', "Couldn't save"), e); });
    setOpen(null);
  };
  const openRef = (r: study.RouteRef) => {
    if (r.type === 'cards' || r.type === 'quiz') { onTab(r.type); return; }
    if (r.type === 'section') { nav.navigate('StudyPage', { course: u.course, page: u.page }); return; }
    if (r.type === 'file' && r.path) { openFile(nav, u.course, r.path); return; }
    if (r.type === 'recording' && r.url) { Linking.openURL(r.url + (r.t != null ? `${r.url.includes('?') ? '&' : '?'}start=${Math.floor(r.t)}` : '')).catch(() => {}); return; }
    if (r.type === 'video' && r.path) Linking.openURL(study.fileLink(u.course, r.path, 'pages')).catch(() => {});
  };
  const mins = items.reduce((a, s, i) => a + (done.includes(i) ? 0 : s.minutes || 0), 0);
  return (
    <ScrollView contentContainerStyle={{ padding: space.lg, paddingTop: space.xs, paddingBottom: space.xxl, gap: space.md }} refreshControl={<PullRefresh onRefresh={reload} />}>
      <Reviews u={u} reload={reload} />
      {!u.route ? (
        <Card style={{ gap: space.md }}>
          <T v="callout" color={t.ink2}>{L('这一节尚无学习路线。在电脑端学习台打开这一节即可生成。', "No study path yet. Open this session on the study desk to generate one.")}</T>
          <DeskLinkRow params={{ course: u.course, page: u.page }} />
        </Card>
      ) : (
        <>
          <View style={{ gap: 8 }}>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <T v="headline" style={{ fontSize: 15 }}>{L(`学习路线 · ${done.length} / ${items.length} 步`, `Study path · ${done.length} / ${items.length}`)}</T>
              <T v="caption" color={t.ink3}>{mins ? L(`还需约 ${mins} 分钟`, `~${mins} min left`) : L('已完成', 'All done')}</T>
            </View>
            <View style={[styles.bar, { backgroundColor: t.track }]}><View style={[styles.bar, { width: `${items.length ? (100 * done.length) / items.length : 0}%`, backgroundColor: t.cyan }]} /></View>
          </View>
          <Card style={{ paddingVertical: 0, paddingHorizontal: 0, overflow: 'hidden' }}>
            {items.map((s, i) => {
              const on = done.includes(i);
              const expanded = shown === i;
              return (
                <View key={i} style={[i > 0 && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }, expanded && { backgroundColor: t.bg }]}>
                  <Pressable onPress={() => setOpen(expanded ? -1 : i)} style={styles.stepRow} accessibilityRole="button">
                    <Pressable onPress={() => toggle(i)} hitSlop={8} accessibilityRole="checkbox" accessibilityState={{ checked: on }} accessibilityLabel={(on ? L('取消勾选：', 'Untick: ') : L('勾选：', 'Tick: ')) + s.title}
                      style={[styles.check, { borderColor: on ? t.cyan : t.ink3, backgroundColor: on ? t.cyan : t.surface }]}>
                      {on ? <Check size={13} color="#fff" /> : null}
                    </Pressable>
                    <T v="body" numberOfLines={2} color={on ? t.ink3 : t.ink} style={[{ flex: 1, fontSize: 15, fontWeight: i === cur ? '600' : '400' }, on && { textDecorationLine: 'line-through' }]}>{`${i + 1}. ${s.title}`}</T>
                    {s.minutes ? <T v="caption" color={t.ink3}>{L(`${s.minutes} 分钟`, `${s.minutes} min`)}</T> : null}
                  </Pressable>
                  {expanded ? (
                    <View style={{ paddingHorizontal: space.lg, paddingBottom: space.md, paddingLeft: 48, gap: space.sm }}>
                      {s.do ? <Markdown text={s.do} small /> : null}
                      {s.refs.length ? (
                        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
                          {s.refs.map((r, k) => (
                            <Pressable key={k} onPress={() => openRef(r)} accessibilityRole="button" style={[styles.ref, { backgroundColor: t.goldSoft }]}>
                              <T v="caption" color={t.gold} numberOfLines={1} style={{ fontWeight: '600' }}>{refLabel(r, u)}</T>
                            </Pressable>
                          ))}
                        </View>
                      ) : null}
                      <View style={{ flexDirection: 'row', gap: space.sm }}>
                        <Pressable onPress={() => toggle(i)} accessibilityRole="button" style={[styles.pillBtn, { backgroundColor: t.cyanSoft }]}>
                          <T v="callout" color={t.cyan} style={{ fontWeight: '600', fontSize: 13 }}>{on ? L('标为未完成', 'Not done') : L('标为完成', 'Done')}</T>
                        </Pressable>
                        <Pressable onPress={() => onAsk(L(`第 ${i + 1} 步「${s.title}」：`, `Step ${i + 1} "${s.title}": `), { step: i })} accessibilityRole="button" style={[styles.pillBtn, { backgroundColor: t.surface2 }]}>
                          <T v="callout" style={{ fontSize: 13 }}>{L('就此步提问', 'Ask about this step')}</T>
                        </Pressable>
                      </View>
                    </View>
                  ) : null}
                </View>
              );
            })}
          </Card>
          {u.route.summary ? <T v="callout" color={t.ink3} style={{ paddingHorizontal: space.xs }}>{u.route.summary}</T> : null}
        </>
      )}
    </ScrollView>
  );
}

/** 课件、阅读、页面在 app 里的预览页看（和对话附件同一个）；FilePreview 拿 study 的地址取预览和页图。 */
export function openFile(nav: { navigate: (s: string, p: object) => void }, course: string, path: string, where: 'materials' | 'pages' = 'materials') {
  const name = path.split('/').pop() ?? path;
  const ext = name.split('.').pop()?.toLowerCase() ?? '';
  const kind: Attachment['kind'] = ['png', 'jpg', 'jpeg', 'gif', 'webp'].includes(ext) ? 'image' : ['mp4', 'mov', 'm4v', 'webm'].includes(ext) ? 'video' : 'doc';
  const a: Attachment = { id: `study:${course}:${where}:${path}`, name, mime: null, size: 0, kind, url: study.fileLink(course, path, where), study: { course, path, where } };
  nav.navigate('FilePreview', { items: [a], index: 0 });
}

// —— 自测：学习页「自测题」那一节，一题一张卡（和电脑上的「自测」标签同一份答案）——

const SELF_H = /^(?:\d+[.、．]\s*)?(?:自测题|自测|self[- ]?test|check yourself)/i;
export interface SelfItem { label: string; tag: string; q: string; a: string }
export function selfTest(md: string): SelfItem[] {
  const lines = String(md || '').split('\n');
  const start = lines.findIndex((l) => /^##\s/.test(l) && SELF_H.test(l.replace(/^##\s+/, '').trim()));
  if (start < 0) return [];
  let end = lines.length;
  for (let i = start + 1; i < lines.length; i++) if (/^##\s/.test(lines[i])) { end = i; break; }
  const body = lines.slice(start + 1, end).join('\n');
  const re = /<details>\s*<summary>[\s\S]*?<\/summary>([\s\S]*?)<\/details>/gi;
  const MARK = /^\*\*\s*(Q?\s*\d+)\s*(?:[（(]([^）)\n]*)[）)])?\s*[.、．:：]?\s*\*\*[ \t]*/gim;
  const items: SelfItem[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(body))) {
    let q = body.slice(last, m.index).trim();
    const a = m[1].trim();
    last = re.lastIndex;
    let label = '';
    let tag = '';
    const marks = [...q.matchAll(MARK)];
    if (marks.length) {
      const k = marks[marks.length - 1];
      label = k[1].replace(/\s+/g, '').replace(/^q/, 'Q');
      tag = (k[2] || '').trim();
      q = q.slice((k.index ?? 0) + k[0].length).trim();
    }
    if (!q && items.length) { items[items.length - 1].a += `\n\n${a}`; continue; }
    items.push({ label: label || String(items.length + 1), tag, q, a });
  }
  return items;
}

function SelfTab({ u, onAsk }: { u: study.StudyUnit; onAsk: (text: string, extra?: { quote?: string }) => void }) {
  const t = useTheme();
  const [items, setItems] = useState<SelfItem[] | null>(null);
  const [st, setSt] = useState<study.SelfState>({ v: {} });
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    let live = true;
    Promise.all([study.pageMarkdown(u.course, u.page), study.selfGet(u.course, u.page).catch(() => null)]).then(([md, s]) => {
      if (!live) return;
      setItems(selfTest(md));
      if (s && s.v) setSt(s);
    }, () => { if (live) setItems([]); });
    return () => { live = false; };
  }, [u.course, u.page]);
  const save = (next: study.SelfState) => {
    setSt(next);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => { study.selfPut(u.course, u.page, { ...next, n: items?.length ?? next.n }).catch(() => {}); }, 600);
  };
  const upd = (i: number, patch: study.SelfRec) => save({ ...st, v: { ...st.v, [i]: { ...(st.v[i] ?? {}), ...patch } } });
  if (!items) return <ActivityIndicator style={{ marginTop: space.xl }} color={t.ink3} />;
  if (!items.length) return <Card style={{ margin: space.lg }}><T v="callout" color={t.ink2}>{L('这一节的学习页暂无自测题。可在「提问」中请求出题。', 'These notes have no self-test. Ask for a few questions in Ask.')}</T></Card>;
  const seen = Object.values(st.v).filter((x) => x?.open).length;
  return (
    <ScrollView contentContainerStyle={{ padding: space.lg, paddingTop: space.xs, paddingBottom: space.xxl, gap: space.md }} keyboardShouldPersistTaps="handled" automaticallyAdjustKeyboardInsets>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
        <T v="headline" style={{ fontSize: 15 }}>{L(`自测 · ${seen} / ${items.length}`, `Self-test · ${seen} / ${items.length}`)}</T>
        <T v="caption" color={t.ink3}>{L('可先作答，也可直接查看答案', 'Answer first, or view the answer')}</T>
      </View>
      {items.map((it, i) => <SelfCard key={i} it={it} i={i} rec={st.v[i] ?? {}} upd={(p) => upd(i, p)} u={u} onAsk={onAsk} />)}
    </ScrollView>
  );
}

function SelfCard({ it, i, rec, upd, u, onAsk }: { it: SelfItem; i: number; rec: study.SelfRec; upd: (p: study.SelfRec) => void; u: study.StudyUnit; onAsk: (text: string, extra?: { quote?: string }) => void }) {
  const t = useTheme();
  const [a, setA] = useState(rec.a ?? '');
  const [seenA, setSeenA] = useState(rec.a);
  if (rec.a !== seenA) { setSeenA(rec.a); setA(rec.a ?? ''); }
  const mine = a.trim();
  const n = /^\d+$/.test(it.label) ? it.label : it.label.replace(/^Q/i, '');
  const border = rec.g === 'ok' ? t.good : rec.g === 'close' ? t.warn : rec.g === 'miss' ? t.bad : 'transparent';
  const addReview = () => {
    upd({ rv: 1 });
    study.reviewAdd(u.course, u.page, [{ text: it.q.replace(/\s+/g, ' ').slice(0, 400), kind: 'missed', source: L(`自测第 ${n} 题`, `Self-test Q${n}`) }])
      .catch((e) => { upd({ rv: 0 }); showError(L('添加失败', "Couldn't add it"), e); });
  };
  return (
    <Card style={{ gap: space.sm, borderWidth: 1.5, borderColor: border }}>
      <View style={{ flexDirection: 'row', gap: 8 }}>
        <T v="headline" color={t.gold} style={{ fontSize: 15 }}>{/^\d+$/.test(it.label) ? `${it.label}.` : it.label}</T>
        <View style={{ flex: 1 }}>{it.tag ? <T v="caption" color={t.ink3}>{it.tag}</T> : null}<Markdown text={it.q} /></View>
      </View>
      <TextInput value={a} onChangeText={setA} onEndEditing={() => upd({ a })} onBlur={() => upd({ a })} multiline placeholder={L('你的答案（可选）', 'Your answer (optional)')}
        placeholderTextColor={t.ink3} accessibilityLabel={L(`第 ${i + 1} 题你的答案`, `Your answer to question ${i + 1}`)}
        style={[type.body, styles.input, { backgroundColor: t.bg, color: t.ink, minHeight: 64 }]} />
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm }}>
        <Pressable onPress={() => upd({ a, open: !rec.open })} accessibilityRole="button" style={[styles.pillBtn, { backgroundColor: t.goldFill }]}>
          <T v="callout" color={t.onGold} style={{ fontWeight: '600', fontSize: 13 }}>{rec.open ? L('隐藏答案', 'Hide answer') : mine ? L('查看答案并对照', 'Reveal and compare') : L('查看答案', 'Show the answer')}</T>
        </Pressable>
        {mine ? (
          <Pressable onPress={() => onAsk(L(`我的答案：${mine}\n\n对照参考答案帮我看看：对在哪、漏了什么、哪里说错了。简短点。`, `My answer: ${mine}\n\nCompare it with the reference answer: what's right, missing or wrong. Keep it short.`),
            { quote: L(`自测第 ${n} 题\n题目：${it.q}\n\n参考答案：${it.a}`, `Self-test Q${n}\nQuestion: ${it.q}\n\nReference answer: ${it.a}`) })} accessibilityRole="button" style={[styles.pillBtn, { backgroundColor: t.surface2 }]}>
            <T v="callout" style={{ fontSize: 13 }}>{L('批改答案', 'Check my answer')}</T>
          </Pressable>
        ) : null}
      </View>
      {rec.open ? (
        <View style={{ gap: space.sm }}>
          {mine ? <View style={[styles.cmp, { backgroundColor: t.goldSoft }]}><T v="caption" color={t.ink3}>{L('你的答案', 'Your answer')}</T><T v="callout">{mine}</T></View> : null}
          <View style={[styles.cmp, { backgroundColor: t.surface2 }]}><T v="caption" color={t.ink3}>{L('参考答案', 'Reference answer')}</T><Markdown text={it.a} small /></View>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 6 }}>
            <T v="caption" color={t.ink3}>{L('自评：', 'Mark it:')}</T>
            {([['ok', L('正确', 'Right'), t.goodSoft, t.good], ['close', L('接近', 'Close'), t.warnSoft, t.warn], ['miss', L('未答出', 'Missed'), t.badSoft, t.bad]] as const).map(([g, label, bg, fg]) => (
              <Pressable key={g} onPress={() => upd({ g: rec.g === g ? undefined : g })} accessibilityRole="button" accessibilityState={{ selected: rec.g === g }}
                style={[styles.grade, { borderColor: rec.g === g ? fg : t.line, backgroundColor: rec.g === g ? bg : t.surface }]}>
                <T v="caption" color={rec.g === g ? fg : t.ink2} style={{ fontWeight: '600', fontSize: 13 }}>{label}</T>
              </Pressable>
            ))}
            {rec.g === 'miss' || rec.g === 'close' ? (
              <Pressable onPress={rec.rv ? undefined : addReview} disabled={!!rec.rv} accessibilityRole="button" style={[styles.grade, { borderColor: t.line, backgroundColor: t.surface }]}>
                <T v="caption" color={rec.rv ? t.good : t.ink2} style={{ fontWeight: '600', fontSize: 13 }}>{rec.rv ? L('已加入复习 ✓', 'Added to review ✓') : L('加入复习', 'Add to review')}</T>
              </Pressable>
            ) : null}
          </View>
        </View>
      ) : null}
    </Card>
  );
}

// —— 闪卡 ——

function useGenerated<T>(u: study.StudyUnit, kind: 'cards' | 'quiz') {
  const [g, setG] = useState<study.Generated<T> | null>(null);
  const load = useCallback(() => study.generated<T>(u.course, u.page, kind).then(setG, () => setG({ status: 'error', data: null })), [u.course, u.page, kind]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (g?.status !== 'running') return undefined;
    const id = setTimeout(load, 5000);
    return () => clearTimeout(id);
  }, [g, load]);
  const start = () => {
    setG((x) => (x ? { ...x, status: 'running' } : { status: 'running', data: null }));
    study.generate(u.course, u.page, kind).then((r) => {
      if (r.ok === false && r.status === 'needs_materials') { showError(L('材料尚未齐全', 'Materials are missing'), (r.missing ?? []).map((m) => m.title).join('、')); load(); return; }
      load();
    }, (e) => { showError(L('未能开始生成', "Couldn't start"), e); load(); });
  };
  return { g, start };
}

function GenEmpty({ running, kind, onStart }: { running: boolean; kind: 'cards' | 'quiz'; onStart: () => void }) {
  const t = useTheme();
  return (
    <Card style={{ margin: space.lg, gap: space.md }}>
      <T v="callout" color={t.ink2}>{running ? (kind === 'cards' ? L('正在生成闪卡，约需一两分钟。', 'Making flashcards; a minute or two.') : L('正在生成小测，约需一两分钟。', 'Making a quiz; a minute or two.'))
        : kind === 'cards' ? L('这一节尚无闪卡：将根据学习页和课件生成 12–20 张。', 'No flashcards yet: 12–20 from the notes and materials.') : L('这一节尚无小测：将根据学习页和课件生成 8 道单选题。', 'No quiz yet: 8 multiple-choice questions from the notes and materials.')}</T>
      {running ? <ActivityIndicator color={t.ink3} /> : <Btn label={kind === 'cards' ? L('生成闪卡', 'Make flashcards') : L('生成小测', 'Make a quiz')} onPress={onStart} />}
    </Card>
  );
}

function CardsTab({ u }: { u: study.StudyUnit }) {
  const t = useTheme();
  const { g, start } = useGenerated<study.Card>(u, 'cards');
  const [i, setI] = useState(0);
  const [flip, setFlip] = useState(false);
  const [again, setAgain] = useState(0);
  const cards = g?.data?.items ?? [];
  if (!g) return <ActivityIndicator style={{ marginTop: space.xl }} color={t.ink3} />;
  if (!cards.length) return <GenEmpty running={g.status === 'running'} kind="cards" onStart={start} />;
  const c = cards[i % cards.length];
  const next = (missed: boolean) => {
    if (missed) {
      setAgain((n) => n + 1);
      study.reviewAdd(u.course, u.page, [{ text: c.q.replace(/\s+/g, ' ').slice(0, 400), kind: 'card', source: L('闪卡', 'Flashcards') }]).catch(() => {});
    }
    setFlip(false);
    setI((x) => (x + 1) % cards.length);
  };
  return (
    <ScrollView contentContainerStyle={{ padding: space.lg, paddingTop: space.xs, gap: space.md }}>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
        <T v="headline" style={{ fontSize: 15 }}>{L(`闪卡 · ${(i % cards.length) + 1} / ${cards.length}`, `Card ${(i % cards.length) + 1} of ${cards.length}`)}</T>
        {again ? <T v="caption" color={t.warn}>{L(`已加入复习 ${again} 张`, `${again} added to review`)}</T> : null}
      </View>
      <Pressable onPress={() => setFlip((f) => !f)} accessibilityRole="button" accessibilityLabel={L('翻面', 'Flip')}
        style={[styles.flash, { backgroundColor: t.surface, borderColor: flip ? t.cyan : t.goldFill }]}>
        <T v="caption" color={flip ? t.cyan : t.gold} style={{ fontWeight: '700' }}>{flip ? L('答案', 'Answer') : L('问题', 'Question')}</T>
        <View style={{ alignSelf: 'stretch' }}><Markdown text={flip ? c.a : c.q} /></View>
        {flip && c.ref ? <T v="caption" color={t.ink3}>{c.ref}</T> : null}
        <T v="caption" color={t.ink3}>{flip ? L('轻点翻回正面', 'Tap to flip back') : L('轻点查看答案', 'Tap to see the answer')}</T>
      </Pressable>
      <View style={{ flexDirection: 'row', gap: space.sm }}>
        <Btn flex kind="quiet" label={L('未记住', "Didn't know")} onPress={() => next(true)} />
        <Btn flex label={L('已记住', 'Knew it')} onPress={() => next(false)} />
      </View>
      <T v="caption" color={t.ink3} style={{ textAlign: 'center' }}>{L('未记住的卡片会加入这一节的复习。卡片根据这一节的学习页和课件生成。', "Cards you didn't know go into this session's review.")}</T>
    </ScrollView>
  );
}

// —— 小测 ——

function QuizTab({ u }: { u: study.StudyUnit }) {
  const t = useTheme();
  const { g, start } = useGenerated<study.Question>(u, 'quiz');
  const [i, setI] = useState(0);
  const [picked, setPicked] = useState<number | null>(null);
  const [right, setRight] = useState(0);
  const qs = g?.data?.items ?? [];
  if (!g) return <ActivityIndicator style={{ marginTop: space.xl }} color={t.ink3} />;
  if (!qs.length) return <GenEmpty running={g.status === 'running'} kind="quiz" onStart={start} />;
  if (i >= qs.length) {
    return (
      <Card style={{ margin: space.lg, gap: space.md }}>
        <T v="title">{L(`答对 ${right} / ${qs.length}`, `${right} / ${qs.length} correct`)}</T>
        <T v="callout" color={t.ink2}>{L('答错的题目已加入这一节的复习。', "Wrong answers were added to this session's review.")}</T>
        <Btn label={L('重做', 'Again')} onPress={() => { setI(0); setPicked(null); setRight(0); }} />
      </Card>
    );
  }
  const q = qs[i];
  const pick = (k: number) => {
    if (picked != null) return;
    setPicked(k);
    if (k === q.answer) setRight((n) => n + 1);
    else study.reviewAdd(u.course, u.page, [{ text: q.q.replace(/\s+/g, ' ').slice(0, 400), kind: 'quiz', source: L(`小测第 ${i + 1} 题`, `Quiz Q${i + 1}`) }]).catch(() => {});
  };
  return (
    <ScrollView contentContainerStyle={{ padding: space.lg, paddingTop: space.xs, gap: space.md, paddingBottom: space.xxl }}>
      <T v="caption" color={t.ink3}>{L(`小测 · 第 ${i + 1} / ${qs.length} 题`, `Quiz · ${i + 1} of ${qs.length}`)}</T>
      <Markdown text={q.q} />
      {q.options.map((o, k) => {
        const ok = picked != null && k === q.answer;
        const bad = picked === k && k !== q.answer;
        return (
          <Pressable key={k} onPress={() => pick(k)} accessibilityRole="button"
            style={[styles.opt, { backgroundColor: ok ? t.goodSoft : bad ? t.badSoft : t.surface, borderColor: ok ? t.good : bad ? t.bad : t.line }]}>
            <T v="headline" color={ok ? t.good : bad ? t.bad : t.ink2} style={{ fontSize: 14, width: 18 }}>{'ABCDEFG'[k]}</T>
            <View style={{ flex: 1, marginBottom: -8 }}><Markdown text={String(o)} small /></View>
            {ok ? <T v="caption" color={t.good} style={{ fontWeight: '700' }}>{L('正确', 'Right')}</T> : bad ? <T v="caption" color={t.bad} style={{ fontWeight: '700' }}>{L('你的选择', 'Yours')}</T> : null}
          </Pressable>
        );
      })}
      {picked != null ? (
        <Card style={{ gap: space.sm, backgroundColor: t.surface2 }}>
          <T v="headline" color={picked === q.answer ? t.good : t.bad} style={{ fontSize: 15 }}>{picked === q.answer ? L('回答正确', 'Correct') : L('回答错误，已加入复习', 'Wrong: added to review')}</T>
          {q.explain ? <Markdown text={q.explain} small /> : null}
          {q.ref ? <T v="caption" color={t.ink3}>{q.ref}</T> : null}
          <Btn label={i + 1 < qs.length ? L('下一题', 'Next') : L('查看结果', 'See the score')} onPress={() => { setI((x) => x + 1); setPicked(null); }} />
        </Card>
      ) : null}
    </ScrollView>
  );
}

// —— 课件 ——

function FilesTab({ u }: { u: study.StudyUnit }) {
  const t = useTheme();
  const nav = useNavigation<any>();
  const row = (key: string, icon: React.ReactNode, title: string, sub: string, onPress: () => void, first = false) => (
    <Pressable key={key} onPress={onPress} accessibilityRole="button" style={({ pressed }) => [styles.fileRow, !first && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }, { opacity: pressed ? 0.6 : 1 }]}>
      {icon}
      <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
        <T v="body" numberOfLines={2} style={{ fontSize: 15 }}>{title}</T>
        <T v="caption" color={t.ink3}>{sub}</T>
      </View>
      <ChevronRight size={16} color={t.ink3} />
    </Pressable>
  );
  const badge = (label: string) => <View style={[styles.ext, { backgroundColor: t.badSoft }]}><T v="caption" color={t.bad} style={{ fontSize: 9, fontWeight: '800' }}>{label}</T></View>;
  const ext = (p: string) => (p.split('.').pop() ?? '').slice(0, 4).toUpperCase();
  const readings = u.readings.filter((r) => r.file && !u.files.some((f) => f.path === r.file));
  return (
    <ScrollView contentContainerStyle={{ padding: space.lg, paddingTop: space.xs, gap: space.md, paddingBottom: space.xxl }}>
      <Card style={{ paddingVertical: 0 }}>
        {row('page', <View style={[styles.ext, { backgroundColor: t.goldSoft }]}><NotebookText size={16} color={t.gold} /></View>, L('学习页', 'Study notes'), String(u.meta.title ?? u.title), () => nav.navigate('StudyPage', { course: u.course, page: u.page }), true)}
        {u.files.map((f) => row(f.path, badge(ext(f.name)), f.name.replace(/\.[^.]+$/, ''), L('课件', 'Material'), () => openFile(nav, u.course, f.path)))}
        {readings.map((r) => row(`r-${r.file}`, badge(ext(r.file as string)), r.title, r.required ? L('必读', 'Required reading') : L('选读', 'Optional reading'), () => openFile(nav, u.course, r.file as string)))}
        {u.videos.map((v) => row(`v-${v.path}`, <View style={[styles.ext, { backgroundColor: t.cyanSoft }]}><Film size={16} color={t.cyan} /></View>, v.name, L('讲解视频（在浏览器中打开）', 'Explainer video (opens in the browser)'),
          () => { Linking.openURL(study.fileLink(u.course, v.path, 'pages')).catch(() => {}); }))}
        {u.recordings.filter((r) => r.viewer_url).map((r) => row(`rec-${r.id}`, <View style={[styles.ext, { backgroundColor: t.surface2 }]}><Mic size={16} color={t.ink2} /></View>, r.title,
          L('录播（在浏览器中打开）', 'Lecture recording (opens in the browser)'), () => { Linking.openURL(r.viewer_url as string).catch(() => {}); }))}
      </Card>
      {u.missing.length ? <T v="callout" color={t.warn}>{L(`缺少：${u.missing.map((m) => m.title).join('；')}`, `Missing: ${u.missing.map((m) => m.title).join('; ')}`)}</T> : null}
      <T v="caption" color={t.ink3}>{L('PDF 在 app 内预览，与对话附件相同；录播和视频在浏览器中打开。', 'PDFs open in the same preview as chat attachments; recordings and videos open in the browser.')}</T>
    </ScrollView>
  );
}

// —— 问这一节 ——

interface AskMsg { role: 'user' | 'bot'; text: string; err?: boolean }

function AskTab({ u, seed }: { u: study.StudyUnit; seed: { text: string; quote?: string; step?: number | null; at: number } | null }) {
  const t = useTheme();
  const [msgs, setMsgs] = useState<AskMsg[] | null>(null);
  const [text, setText] = useState('');
  const [quote, setQuote] = useState<string | null>(null);
  const [step, setStepIdx] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const scroll = useRef<ScrollView>(null);
  const [seedAt, setSeedAt] = useState(0);
  if (seed && seed.at !== seedAt) { setSeedAt(seed.at); setText(seed.text); setQuote(seed.quote ?? null); setStepIdx(seed.step ?? null); }
  useEffect(() => {
    let live = true;
    study.history(u.course, u.page).then((h) => { if (live) setMsgs(h.messages.map((m) => ({ role: m.role === 'user' ? 'user' : 'bot', text: m.text }))); }, () => { if (live) setMsgs([]); });
    return () => { live = false; };
  }, [u.course, u.page]);
  const send = (s?: string) => {
    const q = (s ?? text).trim();
    if (!q || busy) return;
    setBusy(true);
    setText('');
    const shown = quote ? `${L('〔关于', '[Re: ')}${quote.split('\n')[0]}${L('〕', '] ')}${q}` : q;
    setMsgs((m) => [...(m ?? []), { role: 'user', text: shown }, { role: 'bot', text: '' }]);
    const paint = (partial: string) => setMsgs((m) => (m ? [...m.slice(0, -1), { role: 'bot', text: partial }] : m));
    study.ask(u.course, u.page, shown, paint, { quote, step }).catch((e: unknown) => setMsgs((m) => (m ? [...m.slice(0, -1), { role: 'bot', text: e instanceof Error ? e.message : String(e), err: true }] : m)))
      .finally(() => { setBusy(false); setQuote(null); setStepIdx(null); });
  };
  const hints = useMemo(() => [L('用三句话讲清这一节在说什么', 'Explain this session in three sentences'), L('考试最可能怎么考这一块？', 'How is this most likely to be examined?'), L('出一道题考我，先别给答案', 'Quiz me with one question, no answer yet')], []);
  const root = useRef<View>(null);
  const bottom = useBottomInset(root);
  return (
    <Reanimated.View ref={root} onLayout={bottom.onLayout} style={[{ flex: 1 }, bottom.style]}>
      <ScrollView ref={scroll} onContentSizeChange={() => scroll.current?.scrollToEnd({ animated: true })} contentContainerStyle={{ padding: space.lg, paddingTop: space.xs, gap: space.md }} keyboardShouldPersistTaps="handled">
        <T v="caption" color={t.ink3}>{L('仅依据这一节的学习页、课件和阅读材料回答，并注明出处。', "Answers come from this session's notes, materials and readings, with sources.")}</T>
        {msgs === null ? <ActivityIndicator color={t.ink3} /> : null}
        {msgs?.map((m, i) => m.role === 'user' ? (
          <View key={i} style={[styles.userBub, { backgroundColor: t.goldSoft }]}><T v="body" style={{ fontSize: 15 }}>{m.text}</T></View>
        ) : (
          <View key={i} style={{ alignSelf: 'stretch' }}>{m.text ? <Markdown text={m.text} color={m.err ? t.bad : undefined} /> : <ActivityIndicator color={t.ink3} style={{ alignSelf: 'flex-start' }} />}</View>
        ))}
        {msgs && !msgs.length ? (
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
            {hints.map((h) => (
              <Pressable key={h} onPress={() => send(h)} accessibilityRole="button" style={[styles.hint, { borderColor: t.line, backgroundColor: t.surface }]}>
                <T v="caption" color={t.ink2} style={{ fontSize: 13 }}>{h}</T>
              </Pressable>
            ))}
          </View>
        ) : null}
      </ScrollView>
      {quote ? (
        <View style={[styles.quoteBar, { backgroundColor: t.goldSoft }]}>
          <T v="caption" numberOfLines={1} style={{ flex: 1 }}>{L('引用：', 'Quoting: ')}{quote.split('\n')[0]}</T>
          <Pressable onPress={() => setQuote(null)} hitSlop={8}><T v="caption" color={t.ink3}>✕</T></Pressable>
        </View>
      ) : null}
      <View style={[styles.askBar, { borderTopColor: t.line, backgroundColor: t.bg }]}>
        <TextInput value={text} onChangeText={setText} placeholder={L('就这一节提问…', 'Ask about this session…')} placeholderTextColor={t.ink3} multiline
          style={[type.body, styles.askInput, { backgroundColor: t.surface, color: t.ink }]} accessibilityLabel={L('就这一节提问', 'Ask about this session')} />
        <Pressable onPress={() => send()} disabled={busy || !text.trim()} accessibilityRole="button" accessibilityLabel={L('发送', 'Send')}
          style={[styles.sendBtn, { backgroundColor: t.goldFill, opacity: busy || !text.trim() ? 0.5 : 1 }]}>
          {busy ? <ActivityIndicator color={t.onGold} size="small" /> : <Send size={18} color={t.onGold} />}
        </Pressable>
      </View>
    </Reanimated.View>
  );
}

// —— 学习页（整页读）——

/** 学习页里的 <details> 在手机上的 Markdown 里画不出来：换成引用块，答案直接显示。 */
function detailsToQuote(md: string): string {
  return md.replace(/<details>\s*<summary>([\s\S]*?)<\/summary>([\s\S]*?)<\/details>/gi, (_, s: string, body: string) =>
    `> **${s.trim()}**\n>\n${body.trim().split('\n').map((l) => `> ${l}`).join('\n')}\n`);
}

export function StudyPageScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { course, page } = useRoute<any>().params as { course: string; page: string };
  const [md, setMd] = useState<string | null>(null);
  const [err, setErr] = useState('');
  useEffect(() => {
    let live = true;
    study.pageMarkdown(course, page).then((x) => { if (live) setMd(detailsToQuote(x)); }, (e: unknown) => { if (live) setErr(e instanceof Error ? e.message : String(e)); });
    return () => { live = false; };
  }, [course, page]);
  return (
    <Screen>
      <NavHeader title={L('学习页', 'Study notes')} sub={page.replace(/\.md$/, '')} onBack={() => nav.goBack()} right={(
        <Pressable onPress={() => { copyDeskLink({ course, page }).catch(() => {}); }} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('在电脑上打开', 'Open on a computer')} style={styles.headBtn}>
          <FileText size={20} color={t.gold} />
        </Pressable>
      )} />
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl }}>
        {err ? <T v="callout" color={t.bad}>{err}</T> : md == null ? <ActivityIndicator color={t.ink3} /> : <Markdown text={md} />}
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  headBtn: { width: 44, height: 44, alignItems: 'center', justifyContent: 'center' },
  chip: { height: 24, paddingHorizontal: 9, borderRadius: 12, borderWidth: 1, alignItems: 'center', justifyContent: 'center' },
  courseChip: { height: 34, paddingHorizontal: 14, borderRadius: 17, borderWidth: 1, alignItems: 'center', justifyContent: 'center' },
  sRow: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: space.md },
  code: { minWidth: 36, height: 24, paddingHorizontal: 4, borderRadius: 6, alignItems: 'center', justifyContent: 'center' },
  bar: { height: 6, borderRadius: 3 },
  stepRow: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingHorizontal: space.lg, paddingVertical: space.md },
  check: { width: 22, height: 22, borderRadius: 11, borderWidth: 2, alignItems: 'center', justifyContent: 'center' },
  ref: { paddingHorizontal: 9, paddingVertical: 4, borderRadius: radius.pill, maxWidth: '100%' },
  pillBtn: { height: 32, paddingHorizontal: 12, borderRadius: 16, alignItems: 'center', justifyContent: 'center' },
  miniBtn: { height: 28, paddingHorizontal: 10, borderRadius: 14, borderWidth: StyleSheet.hairlineWidth, alignItems: 'center', justifyContent: 'center' },
  input: { borderRadius: radius.md, paddingHorizontal: space.md, paddingVertical: 10, textAlignVertical: 'top' },
  cmp: { borderRadius: radius.md, padding: space.md, gap: 4 },
  grade: { height: 30, paddingHorizontal: 11, borderRadius: 15, borderWidth: 1, alignItems: 'center', justifyContent: 'center' },
  flash: { minHeight: 220, borderRadius: radius.lg, borderWidth: 1.5, padding: space.xl, alignItems: 'center', justifyContent: 'center', gap: space.md },
  opt: { flexDirection: 'row', alignItems: 'center', gap: space.md, borderRadius: radius.md, borderWidth: 1.5, padding: space.md },
  fileRow: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: space.md },
  ext: { width: 34, height: 34, borderRadius: 9, alignItems: 'center', justifyContent: 'center' },
  userBub: { alignSelf: 'flex-end', maxWidth: '88%', borderRadius: 16, borderBottomRightRadius: 4, paddingHorizontal: 13, paddingVertical: 10 },
  hint: { paddingHorizontal: 11, paddingVertical: 7, borderRadius: 16, borderWidth: StyleSheet.hairlineWidth },
  quoteBar: { flexDirection: 'row', alignItems: 'center', gap: space.sm, marginHorizontal: space.lg, marginBottom: 6, paddingHorizontal: space.md, paddingVertical: 8, borderRadius: radius.md },
  askBar: { flexDirection: 'row', alignItems: 'flex-end', gap: space.sm, paddingHorizontal: space.lg, paddingVertical: space.sm, borderTopWidth: StyleSheet.hairlineWidth },
  askInput: { flex: 1, maxHeight: 120, borderRadius: radius.md, paddingHorizontal: space.md, paddingVertical: 9 },
  sendBtn: { width: 40, height: 40, borderRadius: 20, alignItems: 'center', justifyContent: 'center' },
});
