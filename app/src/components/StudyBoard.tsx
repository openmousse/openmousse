// 学习 Agent 的看板（dashboard = study，2026-09-30）：顶上「学习台」卡（复习、接着学、下一个截止、开始学、电脑上打开），
// 下面每门课的进度和「加一门课」。截止表、学习记录这些是 Agent 自己的积木，挂在后面（packs/study）。
// 数据：GET /api/study/home（server/studyapp.py）。点进去是原生学习屏（StudyScreens.tsx）。
import React, { useCallback, useState } from 'react';
import { Linking, Platform, Pressable, StyleSheet, View } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import { useFocusEffect, useNavigation } from '@react-navigation/native';
import * as study from '../api/study';
import { getBase } from '../api/base';
import { BookOpen, Clock, Monitor, Play, Plus, RotateCw } from './icons';
import { SectionedBoard } from './blocks/Sections';
import { Btn, Card, SectionLabel, T, showError } from './ui';
import { L, lang } from '../i18n';
import { space, useTheme } from '../theme';

/** 学习台的数据：看板和学习屏都用，回到这一页重读。 */
export function useStudyHome() {
  const [data, setData] = useState<study.StudyHome | null>(null);
  const [error, setError] = useState('');
  const load = useCallback(() => study.home().then((d) => { setData(d); setError(''); }, (e: unknown) => setError(e instanceof Error ? e.message : String(e))), []);
  useFocusEffect(useCallback(() => { load(); }, [load]));
  return { data, error, reload: load };
}

/** 「周五 09:00」「10/16 周五」这种：截止离现在一周内写星期，远的写日期。 */
export function dueText(due: string): string {
  const d = new Date(`${due.slice(0, 10)}T${due.length > 10 ? due.slice(11, 16) : '12:00'}:00`);
  if (Number.isNaN(d.getTime())) return due;
  const days = Math.round((d.getTime() - Date.now()) / 86400000);
  const wk = lang() === 'zh' ? `周${'日一二三四五六'[d.getDay()]}` : ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][d.getDay()];
  const time = due.length > 10 && due.slice(11, 16) !== '23:59' ? ` ${due.slice(11, 16)}` : '';
  return days >= 0 && days < 7 ? `${wk}${time}` : L(`${d.getMonth() + 1}/${d.getDate()} ${wk}${time}`, `${wk} ${d.getDate()}/${d.getMonth() + 1}${time}`);
}
export function daysLeft(due: string): string {
  const d = new Date(`${due.slice(0, 10)}T${due.length > 10 ? due.slice(11, 16) : '23:59'}:00`);
  const ms = d.getTime() - Date.now();
  if (ms < 0) return L('已逾期', 'overdue');
  const h = Math.floor(ms / 3600000);
  if (h < 24) return L(`剩余 ${h} 小时`, `${h}h left`);
  const n = Math.ceil(h / 24);
  return L(`剩余 ${n} 天`, `${n} day${n > 1 ? 's' : ''} left`);
}

/** 电脑上打开学习台：一次性链接（10 分钟、只能用一次），拷到剪贴板；网页版直接开一个新标签。 */
export async function copyDeskLink(params?: { course?: string; page?: string }): Promise<'copied' | 'opened'> {
  const qs = params?.course ? `?course=${encodeURIComponent(params.course)}${params.page ? `&page=${encodeURIComponent(params.page)}` : ''}` : '';
  if (Platform.OS === 'web' && typeof window !== 'undefined') {
    window.open(`${window.location.origin}/study${qs}`, '_blank');
    return 'opened';
  }
  const r = await study.loginLink(getBase());
  await Clipboard.setStringAsync(qs ? r.url.replace('/study#', `/study${qs}#`) : r.url);
  return 'copied';
}

export function DeskLinkRow({ params, compact }: { params?: { course?: string; page?: string }; compact?: boolean }) {
  const t = useTheme();
  const [state, setState] = useState<'' | 'busy' | 'copied' | 'opened'>('');
  const press = () => {
    setState('busy');
    copyDeskLink(params).then(setState, (e) => { setState(''); showError(L('获取链接失败', "Couldn't get a link"), e); });
  };
  return (
    <View style={styles.deskRow}>
      <Monitor size={16} color={t.ink2} />
      <T v="callout" color={t.ink2} style={{ flex: 1, fontSize: 13 }}>
        {state === 'copied' ? L('已复制链接：请在 10 分钟内用电脑浏览器打开，仅可使用一次。', 'Link copied: open it in a browser on your computer within 10 minutes; it works once.')
          : compact ? L('在电脑上打开', 'Open on a computer') : L('在电脑上打开：并排查看课件和学习页', 'Open on a computer: slides and notes side by side')}
      </T>
      <Pressable onPress={press} disabled={state === 'busy'} accessibilityRole="button" style={({ pressed }) => [styles.smallBtn, { borderColor: t.line, backgroundColor: t.surface, opacity: pressed || state === 'busy' ? 0.6 : 1 }]}>
        <T v="callout" style={{ fontSize: 13 }}>{Platform.OS === 'web' ? L('打开', 'Open') : state === 'copied' ? L('再次复制', 'Copy again') : L('复制链接', 'Copy link')}</T>
      </Pressable>
    </View>
  );
}

/** 学习台卡、学习屏「今天」里的一行。 */
export function StudyLine({ icon, title, sub, right, onPress, last }: {
  icon: 'review' | 'next' | 'due'; title: string; sub?: string; right?: React.ReactNode; onPress?: () => void; last?: boolean;
}) {
  const t = useTheme();
  const [bg, fg] = icon === 'review' ? [t.goldSoft, t.gold] : icon === 'next' ? [t.cyanSoft, t.cyan] : [t.warnSoft, t.warn];
  const Icon = icon === 'review' ? RotateCw : icon === 'next' ? Play : Clock;
  const body = (
    <View style={[styles.line, !last && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }]}>
      <View style={[styles.lineIcon, { backgroundColor: bg }]}><Icon size={15} color={fg} /></View>
      <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
        <T v="headline" numberOfLines={1} style={{ fontSize: 15 }}>{title}</T>
        {sub ? <T v="callout" color={t.ink2} numberOfLines={1} style={{ fontSize: 13 }}>{sub}</T> : null}
      </View>
      {right}
    </View>
  );
  return onPress ? <Pressable onPress={onPress} accessibilityRole="button" style={({ pressed }) => ({ opacity: pressed ? 0.6 : 1 })}>{body}</Pressable> : body;
}

export function reviewLine(h: study.StudyHome) {
  const r = h.review;
  if (!r) return null;
  const where = [r.code, r.n != null ? `S${r.n}` : ''].filter(Boolean).join(' ');
  return { title: L(`复习 ${r.count} 条`, `${r.count} to review`), sub: [where, r.source].filter(Boolean).join(' · ') };
}
export function nextLine(h: study.StudyHome) {
  const n = h.next;
  if (!n) return null;
  return {
    title: L(`继续学习 ${n.code} ${n.n != null ? `S${n.n} ` : ''}${n.title}`, `Continue ${n.code} ${n.n != null ? `S${n.n} ` : ''}${n.title}`),
    sub: n.step ? [L(`第 ${n.step} / ${n.steps} 步`, `Step ${n.step} of ${n.steps}`), n.stepTitle, n.minutes ? L(`${n.minutes} 分钟`, `${n.minutes} min`) : ''].filter(Boolean).join(' · ') : L('尚未开始', 'Not started'),
  };
}

function DeskCard({ h }: { h: study.StudyHome }) {
  const t = useTheme();
  const nav = useNavigation<any>();
  const rv = reviewLine(h);
  const nx = nextLine(h);
  const d = h.deadlines[0];
  return (
    <Card style={{ marginTop: space.sm, gap: space.md }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.md }}>
        <View style={[styles.bigIcon, { backgroundColor: t.cyanSoft }]}><BookOpen size={22} color={t.cyan} /></View>
        <View style={{ flex: 1, gap: 2 }}>
          <T v="headline" style={{ fontSize: 17 }}>{L('学习台', 'Study desk')}</T>
          <T v="callout" color={t.ink2} numberOfLines={1} style={{ fontSize: 13 }}>{h.courses.map((c) => c.title).join(' · ')}</T>
        </View>
      </View>
      {rv || nx || d ? (
        <View style={{ borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }}>
          {rv && h.review ? <StudyLine icon="review" title={rv.title} sub={rv.sub} last={!nx && !d}
            onPress={() => (h.review?.page ? nav.navigate('StudySession', { course: h.review.course, page: h.review.page, tab: 'path' }) : nav.navigate('StudyHome', { course: h.review?.course }))} /> : null}
          {nx && h.next ? <StudyLine icon="next" title={nx.title} sub={nx.sub} last={!d} onPress={() => nav.navigate('StudySession', { course: h.next?.course, page: h.next?.page, tab: 'path' })} /> : null}
          {d ? <StudyLine icon="due" title={[d.code, d.title].filter(Boolean).join(' ')} sub={L(`${dueText(d.due)} 截止`, `Due ${dueText(d.due)}`)} last
            right={<View style={[styles.pill, { backgroundColor: t.warnSoft }]}><T v="caption" color={t.warn} style={{ fontSize: 13, fontWeight: '600' }}>{daysLeft(d.due)}</T></View>}
            onPress={d.courseId ? () => nav.navigate('StudyHome', { course: d.courseId }) : undefined} /> : null}
        </View>
      ) : null}
      <Btn label={L('开始学习', 'Start studying')} onPress={() => nav.navigate('StudyHome', {})} />
      <DeskLinkRow />
    </Card>
  );
}

function CoursesCard({ h }: { h: study.StudyHome }) {
  const t = useTheme();
  const nav = useNavigation<any>();
  return (
    <View>
      <SectionLabel>{L('课程', 'Courses')}</SectionLabel>
      <Card style={{ paddingVertical: 0 }}>
        {h.courses.map((c) => (
          <Pressable key={c.name} onPress={() => nav.navigate('StudyHome', { course: c.name })} accessibilityRole="button"
            style={({ pressed }) => [styles.courseRow, { borderBottomColor: t.line, opacity: pressed ? 0.6 : 1 }]}>
            <View style={{ flex: 1, gap: 8 }}>
              <View style={{ flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between', gap: 8 }}>
                <T v="headline" numberOfLines={1} style={{ fontSize: 15, flexShrink: 1 }}>{c.title}</T>
                <T v="callout" color={t.ink2} style={{ fontSize: 13 }}>{c.profile
                  ? L(`已学完 ${c.done} · 已上课 ${c.taught}`, `${c.done} done · ${c.taught} taught`)
                  : L(`已学完 ${c.done} · 学习页 ${c.taught}`, `${c.done} done · ${c.taught} pages`)}</T>
              </View>
              <View style={[styles.bar, { backgroundColor: t.track }]}>
                <View style={[styles.bar, { width: `${c.total ? Math.round((100 * c.done) / c.total) : 0}%`, backgroundColor: t.cyan }]} />
              </View>
            </View>
          </Pressable>
        ))}
        <Pressable onPress={() => nav.navigate('AddCourse', {})} accessibilityRole="button" style={({ pressed }) => [styles.addRow, { opacity: pressed ? 0.6 : 1 }]}>
          <Plus size={18} color={t.cyan} />
          <T v="headline" color={t.cyan} style={{ fontSize: 15 }}>{L('添加课程', 'Add a course')}</T>
        </Pressable>
      </Card>
    </View>
  );
}

/** 学习 Agent 刚建好、一门课都没有：加第一门课，也可以在对话里说。 */
export function StudyEmpty({ onChat }: { onChat: () => void }) {
  const t = useTheme();
  const nav = useNavigation<any>();
  return (
    <View style={{ gap: space.md, marginTop: space.sm }}>
      <Card style={{ alignItems: 'center', gap: 14, paddingVertical: 30 }}>
        <View style={[styles.hero, { backgroundColor: t.cyanSoft }]}><BookOpen size={36} color={t.cyan} /></View>
        <T v="title" style={{ fontWeight: '700' }}>{L('学习台暂无课程', 'The study desk is empty')}</T>
        <T v="body" color={t.ink2} style={{ textAlign: 'center', fontSize: 15, lineHeight: 22 }}>{L('添加第一门课程。选择你已有的材料（大纲、课件或课程网站），即可查看获取位置和所需内容。',
          'Add your first course. Choose what you have (a syllabus, files or a course site) to see where to find them and what to get.')}</T>
        <View style={{ alignSelf: 'stretch', marginTop: 6 }}><Btn label={L('添加课程', 'Add a course')} onPress={() => nav.navigate('AddCourse', {})} /></View>
      </Card>
      <Card style={{ gap: 10 }}>
        <T v="callout" color={t.ink2} style={{ fontWeight: '600' }}>{L('也可以在对话中添加', 'Or add it in the chat')}</T>
        <Pressable onPress={onChat} accessibilityRole="button" style={[styles.bubble, { backgroundColor: t.goldSoft }]}>
          <T v="body" style={{ fontSize: 15 }}>{L('这学期有一门行为经济学，大纲在附件里', 'I have a behavioural economics course this term; the syllabus is attached')}</T>
        </Pressable>
        <T v="callout" color={t.ink3} style={{ fontSize: 13 }}>{L('学习 Agent 会按相同步骤引导添加，缺少材料时会向你确认。', "The study Agent follows the same steps and asks for anything missing.")}</T>
      </Card>
      <View style={{ paddingHorizontal: space.lg }}><DeskLinkRow compact /></View>
    </View>
  );
}

/** 学习 Agent 的看板（GroupScreen 里 dashboard = study）。 */
export function StudyBoard({ onChat }: { onChat: () => void }) {
  const t = useTheme();
  const { data, error } = useStudyHome();
  if (!data) {
    return <Card style={{ marginTop: space.md }}><T v="callout" color={error ? t.bad : t.ink2}>{error ? L(`无法加载学习台：${error}`, `Couldn't load the study desk: ${error}`) : L('正在加载…', 'Loading…')}</T></Card>;
  }
  if (!data.courses.length) return <SectionedBoard els={{ 'study.desk': <StudyEmpty onChat={onChat} />, 'study.courses': null }} />;
  return <SectionedBoard els={{ 'study.desk': <DeskCard h={data} />, 'study.courses': <CoursesCard h={data} /> }} />;
}

/** 「今天」页：有没复习的，一行直接去那一节（没有就什么都不画）。 */
export function StudyTodayRow() {
  const nav = useNavigation<any>();
  const { data: h } = useStudyHome();
  const rv = h ? reviewLine(h) : null;
  if (!h || !rv || !h.review) return null;
  return (
    <View>
      <SectionLabel>{L('学习', 'Study')}</SectionLabel>
      <Card style={{ paddingVertical: 0 }}>
        <StudyLine icon="review" title={rv.title} sub={rv.sub} last
          onPress={() => (h.review?.page ? nav.navigate('StudySession', { course: h.review.course, page: h.review.page, tab: 'path' }) : nav.navigate('StudyHome', { course: h.review?.course }))} />
      </Card>
    </View>
  );
}

/** 打开课件：PDF 这类在 app 里用对话附件的预览页看，别的（视频、网页）用浏览器。 */
export function openStudyLink(url: string) {
  Linking.openURL(url).catch((e) => showError(L('无法打开', "Couldn't open it"), e));
}

const styles = StyleSheet.create({
  bigIcon: { width: 40, height: 40, borderRadius: 12, alignItems: 'center', justifyContent: 'center' },
  hero: { width: 72, height: 72, borderRadius: 22, alignItems: 'center', justifyContent: 'center' },
  line: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: space.md },
  lineIcon: { width: 30, height: 30, borderRadius: 9, alignItems: 'center', justifyContent: 'center' },
  pill: { height: 26, paddingHorizontal: 10, borderRadius: 13, alignItems: 'center', justifyContent: 'center' },
  deskRow: { flexDirection: 'row', alignItems: 'center', gap: space.sm },
  smallBtn: { height: 30, paddingHorizontal: 12, borderRadius: 15, borderWidth: StyleSheet.hairlineWidth, alignItems: 'center', justifyContent: 'center' },
  courseRow: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: 14, borderBottomWidth: StyleSheet.hairlineWidth },
  bar: { height: 6, borderRadius: 3 },
  addRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 14 },
  bubble: { alignSelf: 'flex-end', maxWidth: '88%', borderRadius: 16, borderBottomRightRadius: 4, paddingHorizontal: 13, paddingVertical: 10 },
});
