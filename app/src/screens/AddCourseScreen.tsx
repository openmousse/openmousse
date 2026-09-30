// 手机上加一门课（2026-09-30）：和电脑上 /study 的五步一样（说说这门课 → 你手上有什么 → 核对每一节 → 放材料 → 生成），
// 底下一直开着学习 Agent 的对话（和「对话」tab 同一个线程）：找不到东西问它、发截图，核对时说哪里不对它就改（标黄、能撤销）。
// 拖一堆文件还是电脑上方便：每一步都有「电脑上打开」。数据在服务器（api/study.ts → server/courses.py）。
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, Platform, Pressable, ScrollView, StyleSheet, TextInput, View } from 'react-native';
import { useFocusEffect, useNavigation, useRoute } from '@react-navigation/native';
import * as study from '../api/study';
import { CourseChip } from '../components/ChatCards';
import { pickDocuments, pickMedia } from '../components/chatInput';
import { Check, ImageIcon, Send } from '../components/icons';
import { Markdown } from '../components/Markdown';
import { useSheet } from '../components/Sheet';
import { DeskLinkRow } from '../components/StudyBoard';
import { Btn, Card, NavHeader, Screen, SectionLabel, T, showError } from '../components/ui';
import type { CourseChangeCard, PendingFile } from '../data/types';
import { L } from '../i18n';
import { useStore, useThreadOnScreen } from '../store';
import { radius, space, type, useTheme } from '../theme';

const EXAMS = () => [['closed', L('闭卷笔试', 'Closed-book exam')], ['open', L('开卷考试', 'Open-book exam')], ['essay', L('书面作业', 'Written coursework')],
  ['group', L('小组作业', 'Group work')], ['present', L('展示', 'Presentation')], ['unknown', L('还不知道', 'Not sure yet')]] as const;
const LEARNS = () => [['zh', L('中文讲，术语留英文', 'Chinese, terms in English')], ['intuition', L('先讲直觉再上公式', 'Intuition first')], ['examples', L('多举例子和案例', 'Lots of examples')],
  ['video', L('要讲解视频', 'Explainer videos')], ['practice', L('多做题', 'More practice')]] as const;
const PLAT = () => ({
  canvas: { label: 'Canvas', syl: L('Canvas 课程左边的 Syllabus，或者第一个模块里叫 Course outline 的文件。', 'The Syllabus link on the left of the Canvas course, or a Course outline file in the first module.'),
    files: L('Canvas 的 Modules 里一节一节下；Files 页可以多选，打包成 zip 下载。', 'Download from Modules, or select several in Files and download a zip.'),
    tip: L('Canvas 的话，大纲在课程左边的 Syllabus；想以后自动同步，就用第三项「连上 Canvas」。找不到就截个图问我。', 'On Canvas the syllabus is under Syllabus on the left; to sync automatically, use "Connect Canvas". Stuck? Send me a screenshot.') },
  moodle: { label: 'Moodle', syl: L('Moodle 课程主页最上面那一块，常叫 Course outline 或 Module guide。', 'The top block of the Moodle course page, often Course outline or Module guide.'),
    files: L('每周那一块里点开下载；老师开了「下载课程内容」的话，课程菜单里能一次打包。', "Each week's block; if the lecturer enabled it, the course menu can download everything at once."),
    tip: L('Moodle 还不能直接连，先传大纲和课件。每周那一块里找不到的话，截个图问我。', "Moodle can't be connected yet; upload the syllabus and files. Can't find them? Send me a screenshot.") },
  blackboard: { label: 'Blackboard', syl: L('Course Content 里第一个文件夹，常叫 Syllabus 或 Module handbook。', 'The first folder in Course Content, often Syllabus or Module handbook.'),
    files: L('Course Content 里一个文件夹一个文件夹下。', 'Folder by folder in Course Content.'),
    tip: L('Blackboard 还不能直接连，先传大纲和课件。找不到的话，截个图问我。', "Blackboard can't be connected yet; upload the syllabus and files. Stuck? Send me a screenshot.") },
  other: { label: L('别的 / 不知道', 'Other / not sure'), syl: L('课程网站首页或第一个模块，常叫 Syllabus、Course outline、Module handbook。', 'The course home page or first module: Syllabus, Course outline or Module handbook.'),
    files: L('课程网站的 Files 或 Modules，一节一节下；能整门打包就下 zip。', 'Files or Modules on the course site; a zip if you can pack the whole course.'),
    tip: L('不知道用的是什么也没关系：截一张课程网站首页的图发我，我告诉你大纲和课件在哪。', "No problem: send me a screenshot of the course home page and I'll show you where things are.") },
}) as Record<string, { label: string; syl: string; files: string; tip: string }>;

export function AddCourseScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const params = (useRoute<any>().params ?? {}) as { course?: string };
  const [name, setName] = useState<string | null>(params.course ?? null);
  const [c, setC] = useState<study.Course | null>(null);
  const [step, setStep] = useState(1);
  const [agent, setAgent] = useState<string | null>(null);
  const [err, setErr] = useState('');
  useEffect(() => { study.courses().then((r) => setAgent(r.agent), () => {}); }, []);
  const reload = useCallback(async () => {
    if (!name) return;
    try { setC(await study.course(name)); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  }, [name]);
  const [first, setFirst] = useState(true);
  useEffect(() => {
    if (!name) return;
    study.course(name).then((x) => { setC(x); if (first) { setFirst(false); setStep(x.setup.confirmed ? Math.max(4, x.setup.step) : Math.max(2, x.setup.step)); } }, (e) => setErr(String(e)));
  }, [name]);  // eslint-disable-line react-hooks/exhaustive-deps
  useFocusEffect(useCallback(() => { reload(); }, [reload]));
  const go = (n: number) => { setStep(n); if (name && n <= 5) study.patchCourse(name, { step: n }).then(setC, () => {}); };
  return (
    <Screen>
      <NavHeader title={L('加一门课', 'Add a course')} sub={c ? c.title : undefined} onBack={() => nav.goBack()} right={<T v="callout" color={t.ink3}>{`${step} / 5`}</T>} />
      <View style={styles.dots}>{[1, 2, 3, 4, 5].map((n) => <View key={n} style={[styles.dot, { backgroundColor: n <= step ? t.goldFill : t.track }]} />)}</View>
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl, gap: space.md }} keyboardShouldPersistTaps="handled" automaticallyAdjustKeyboardInsets keyboardDismissMode="interactive">
        {err ? <T v="callout" color={t.bad}>{err}</T> : null}
        {step === 1 ? <Step1 c={c} onDone={(n, x) => { setName(n); setC(x); setStep(2); }} />
          : !c ? <ActivityIndicator color={t.ink3} />
            : step === 2 ? <Step2 c={c} setC={setC} reload={reload} onNext={() => go(3)} />
              : step === 3 ? <Step3 c={c} setC={setC} onNext={() => { if (c.setup.confirmed) go(4); else study.confirm(c.name).then((x) => { setC(x); setStep(4); }, (e) => showError(L('没建好', "Couldn't confirm"), e)); }} />
                : step === 4 ? <Step4 c={c} setC={setC} reload={reload} onNext={() => go(5)} />
                  : <Step5 c={c} reload={reload} onDone={() => nav.navigate('StudyHome', { course: c.name })} />}
        {c && step > 1 ? (
          <View style={{ flexDirection: 'row', gap: space.sm }}>
            <Btn flex kind="quiet" label={L('上一步', 'Back')} onPress={() => setStep(Math.max(1, step - 1))} />
          </View>
        ) : null}
        {c && agent ? <AgentMini agent={agent} course={c.name} step={step} onReply={reload} /> : null}
        {c && !agent ? <T v="callout" color={t.ink3}>{L('还没有学习 Agent：新建一个 Agent，从例子里选「学习」，就能在这里问它。', 'No study Agent yet: create an Agent from the "Study" example to ask it here.')}</T> : null}
        {c ? <DeskLinkRow params={{ course: c.name }} /> : null}
      </ScrollView>
    </Screen>
  );
}

function Chip({ label, on, onPress }: { label: string; on: boolean; onPress: () => void }) {
  const t = useTheme();
  return (
    <Pressable onPress={onPress} accessibilityRole="button" accessibilityState={{ selected: on }}
      style={[styles.chip, { borderColor: on ? t.cyan : t.line, backgroundColor: on ? t.cyanSoft : t.surface }]}>
      <T v="callout" color={on ? t.cyan : t.ink2} style={{ fontWeight: '600', fontSize: 13 }}>{label}</T>
    </Pressable>
  );
}

function WhereWhat({ where, what }: { where?: string; what: string }) {
  const t = useTheme();
  return (
    <View style={[styles.ww, { backgroundColor: t.bg }]}>
      {where ? <View style={{ flexDirection: 'row', gap: 10 }}><T v="caption" color={t.cyan} style={{ width: 44, fontWeight: '700' }}>{L('去哪拿', 'Where')}</T><T v="callout" style={{ flex: 1, fontSize: 13 }}>{where}</T></View> : null}
      <View style={{ flexDirection: 'row', gap: 10 }}><T v="caption" color={t.cyan} style={{ width: 44, fontWeight: '700' }}>{L('拿什么', 'What')}</T><T v="callout" style={{ flex: 1, fontSize: 13 }}>{what}</T></View>
    </View>
  );
}

// 第 1 步
function Step1({ c, onDone }: { c: study.Course | null; onDone: (name: string, c: study.Course) => void }) {
  const t = useTheme();
  const [name, setName] = useState(c?.title ?? '');
  const [term, setTerm] = useState(c?.term ?? '');
  const [exam, setExam] = useState<string[]>(c?.exam ?? []);
  const [learn, setLearn] = useState<string[]>(c?.learn ?? ['zh', 'intuition']);
  const [notes, setNotes] = useState(c?.notes ?? '');
  const [busy, setBusy] = useState(false);
  const flip = (xs: string[], k: string) => (xs.includes(k) ? xs.filter((x) => x !== k) : [...xs, k]);
  const next = async () => {
    if (!name.trim() || busy) return;
    setBusy(true);
    try {
      if (c) onDone(c.name, await study.patchCourse(c.name, { title: name.trim(), term, exam, learn, notes }));
      else { const r = await study.createCourse({ name: name.trim(), title: name.trim(), term, exam, learn, notes }); onDone(r.name, r.course); }
    } catch (e) { showError(L('没建成', "Couldn't create it"), e); } finally { setBusy(false); }
  };
  return (
    <View style={{ gap: space.md }}>
      <T v="title" style={{ fontWeight: '700' }}>{L('说说这门课', 'About the course')}</T>
      <T v="callout" color={t.ink2}>{L('存成这门课的档案。以后每次写学习页、出闪卡和小测都会带上，随时能改。', 'Saved as the course profile; every study page, flashcard and quiz uses it.')}</T>
      <TextInput value={name} onChangeText={setName} placeholder={L('课名，比如：行为经济学', 'Course name, e.g. Behavioural Economics')} placeholderTextColor={t.ink3} accessibilityLabel={L('课名', 'Course name')}
        style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink }]} />
      <TextInput value={term} onChangeText={setTerm} placeholder={L('学校和学期（可选）', 'School and term (optional)')} placeholderTextColor={t.ink3}
        style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink }]} />
      <SectionLabel>{L('考试怎么考', 'How it is assessed')}</SectionLabel>
      <View style={styles.wrap}>{EXAMS().map(([k, label]) => <Chip key={k} label={label} on={exam.includes(k)} onPress={() => setExam(flip(exam, k))} />)}</View>
      <SectionLabel>{L('你喜欢怎么学', 'How you like to learn')}</SectionLabel>
      <View style={styles.wrap}>{LEARNS().map(([k, label]) => <Chip key={k} label={label} on={learn.includes(k)} onPress={() => setLearn(flip(learn, k))} />)}</View>
      <TextInput value={notes} onChangeText={setNotes} multiline placeholder={L('还想说的（可选）：比如微观学过，前景理论没学过', "Anything else (optional): e.g. I know micro but not prospect theory")} placeholderTextColor={t.ink3}
        style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink, minHeight: 80, textAlignVertical: 'top' }]} />
      <Btn label={busy ? L('在建…', 'Creating…') : L('下一步：你手上有什么', 'Next: what you have')} onPress={next} />
    </View>
  );
}

// 第 2 步
function Step2({ c, setC, reload, onNext }: { c: study.Course; setC: (c: study.Course) => void; reload: () => Promise<void>; onNext: () => void }) {
  const t = useTheme();
  const [plat, setPlat] = useState(c.platform || 'canvas');
  const [have, setHave] = useState<Record<string, boolean>>(c.have ?? {});
  const [busy, setBusy] = useState('');
  const [link, setLink] = useState('');
  const [note, setNote] = useState('');
  const P = PLAT()[plat] ?? PLAT().other;
  const save = (patch: Record<string, unknown>) => study.patchCourse(c.name, patch).then(setC, () => {});
  const toggle = (k: string) => { const h = { ...have, [k]: !have[k] }; setHave(h); save({ have: h }); };
  const running = c.syllabus_job?.status === 'running';
  useEffect(() => {
    if (!running) return undefined;
    const id = setTimeout(() => { reload(); }, 3000);
    return () => clearTimeout(id);
  }, [running, c, reload]);
  const pickSyllabus = async () => {
    const files = await pickDocuments().catch(() => [] as PendingFile[]);
    if (!files.length) return;
    setBusy('syl');
    try { await study.uploadSyllabus(c.name, files[0]); await reload(); } catch (e) { showError(L('没传上', "Couldn't upload"), e); } finally { setBusy(''); }
  };
  const readLink = async () => {
    if (!/^https?:\/\//i.test(link.trim())) return;
    setBusy('syl');
    try { await study.syllabusFrom(c.name, { url: link.trim() }); setLink(''); await reload(); } catch (e) { showError(L('打不开这个链接', "Couldn't open the link"), e); } finally { setBusy(''); }
  };
  const pickFiles = async () => {
    const files = await pickDocuments().catch(() => [] as PendingFile[]);
    if (!files.length) return;
    setBusy('files');
    try {
      const r = await study.uploadFiles(c.name, files);
      setC(r.course);
      const filed = r.files.filter((f) => f.status === 'filed' || f.status === 'info').length;
      setNote(L(`传了 ${r.files.length} 个：${filed} 个归好了`, `${r.files.length} uploaded: ${filed} filed`) + (r.files.length > filed ? L('，别的在第 4 步等你定', '; the rest wait for you in step 4') : ''));
    } catch (e) { showError(L('没传上', "Couldn't upload"), e); } finally { setBusy(''); }
  };
  const card = (k: string, title: string, sub: string, body: React.ReactNode) => (
    <Card style={{ padding: 0, borderWidth: 1.5, borderColor: have[k] ? t.cyan : t.surface }}>
      <Pressable onPress={() => toggle(k)} accessibilityRole="button" accessibilityState={{ selected: !!have[k] }} style={styles.hhead}>
        <View style={{ flex: 1, gap: 2 }}><T v="headline" style={{ fontSize: 15 }}>{title}</T><T v="caption" color={t.ink3} style={{ fontSize: 12.5 }}>{sub}</T></View>
        <View style={[styles.check, { borderColor: have[k] ? t.cyan : t.ink3, backgroundColor: have[k] ? t.cyan : t.surface }]}>{have[k] ? <Check size={12} color="#fff" /> : null}</View>
      </Pressable>
      {have[k] ? <View style={{ paddingHorizontal: space.md, paddingBottom: space.md, gap: space.sm }}>{body}</View> : null}
    </Card>
  );
  const count = ['syllabus', 'files', 'site', 'none'].filter((k) => have[k]).length;
  return (
    <View style={{ gap: space.md }}>
      <T v="title" style={{ fontWeight: '700' }}>{L('你手上有什么？', 'What do you have?')}</T>
      <T v="callout" color={t.ink2}>{L('有几样选几样，都没有也行。选了就告诉你去哪拿、拿什么。', "Pick what you have, or nothing. Each tells you where to find it and what to get.")}</T>
      <SectionLabel>{L('你们的课程网站', 'Your course site')}</SectionLabel>
      <View style={styles.wrap}>{Object.entries(PLAT()).map(([k, v]) => <Chip key={k} label={v.label} on={plat === k} onPress={() => { setPlat(k); save({ platform: k }); }} />)}</View>
      <Card style={{ gap: 4, paddingVertical: space.md }}>
        <T v="callout" style={{ fontSize: 13 }}><T v="callout" color={t.cyan} style={{ fontWeight: '700', fontSize: 13 }}>{L('最少　', 'Minimum  ')}</T>{L('大纲，或者第一节的课件，就能开始。', "A syllabus, or the first session's slides.")}</T>
        <T v="callout" style={{ fontSize: 13 }}><T v="callout" color={t.cyan} style={{ fontWeight: '700', fontSize: 13 }}>{L('越全越好　', 'Better  ')}</T>{L('阅读、练习和答案、往年试卷、录播字幕。', 'Readings, exercises and answers, past papers, captions.')}</T>
      </Card>
      {card('syllabus', L('课程大纲', 'Syllabus'), L('最省事：一份就能建好每一节和截止', 'Easiest: one file sets up every session and deadline'), (
        <>
          <WhereWhat where={P.syl} what={L('列了每节主题、阅读、作业和截止的那份。PDF、Word、网页链接都行。', 'The one listing topics, readings, assignments and deadlines. PDF, Word or a link.')} />
          {c.syllabus?.file || c.syllabus?.url ? (
            <T v="callout" color={running ? t.ink2 : c.syllabus_job?.status === 'error' ? t.bad : t.good} style={{ fontSize: 13 }}>
              {running ? L('在读大纲…（一两分钟）', 'Reading the syllabus… (a minute or two)') : c.syllabus_job?.status === 'error' ? L(`没读成：${c.syllabus_job.error ?? ''}`, `Couldn't read it: ${c.syllabus_job.error ?? ''}`)
                : c.syllabus?.summary ? L(`读完了：${c.syllabus.summary}`, `Read: ${c.syllabus.summary}`) : L('传好了', 'Uploaded')}
            </T>
          ) : null}
          <View style={{ flexDirection: 'row', gap: space.sm }}>
            <Btn flex kind="quiet" label={busy === 'syl' ? L('在传…', 'Uploading…') : c.syllabus?.file ? L('换一份', 'Replace') : L('选文件', 'Choose a file')} onPress={pickSyllabus} />
          </View>
          <View style={{ flexDirection: 'row', gap: space.sm, alignItems: 'center' }}>
            <TextInput value={link} onChangeText={setLink} placeholder={L('或者贴大纲的链接 https://…', 'Or paste a link https://…')} placeholderTextColor={t.ink3} autoCapitalize="none"
              style={[type.callout, styles.input, { flex: 1, backgroundColor: t.bg, color: t.ink, paddingVertical: 8 }]} />
            <Pressable onPress={readLink} accessibilityRole="button" style={[styles.small, { backgroundColor: t.cyanSoft }]}><T v="callout" color={t.cyan} style={{ fontWeight: '600', fontSize: 13 }}>{L('读', 'Read')}</T></Pressable>
          </View>
        </>
      ))}
      {card('files', L('课件文件', 'Course files'), L('slides、讲义、阅读、练习和答案', 'Slides, handouts, readings, exercises'), (
        <>
          <WhereWhat where={P.files} what={L('文件名带 Week 3、Session 3、Lecture 3 的自动归到那一节，认不出的会问你。', 'Files named Week 3, Session 3 or Lecture 3 go to that session; unclear ones are asked about.')} />
          <Btn kind="quiet" label={busy === 'files' ? L('在传…', 'Uploading…') : L('从「文件」里选', 'Choose from Files')} onPress={pickFiles} />
          {note ? <T v="callout" color={t.ink2} style={{ fontSize: 13 }}>{note}</T> : null}
        </>
      ))}
      {card('site', plat === 'canvas' ? L('连上 Canvas', 'Connect Canvas') : L('连上课程网站', 'Connect the course site'),
        plat === 'canvas' ? L('以后新课件和截止自动同步', 'Files and deadlines sync') : L(`${P.label} 还不能直接连`, `${P.label} can't be connected yet`),
        plat === 'canvas' ? <CanvasConnect c={c} reload={reload} /> : <T v="callout" color={t.ink2} style={{ fontSize: 13 }}>{L(`还不能直接连 ${plat === 'other' ? '这个网站' : P.label}。先用大纲和课件，以后新课件出来传上来就行。`, `${plat === 'other' ? 'This site' : P.label} can't be connected yet. Use the syllabus and files.`)}</T>)}
      {card('none', L('还没有', 'Nothing yet'), L('先建一门空课', 'Start empty'), <T v="callout" color={t.ink2} style={{ fontSize: 13 }}>{L('开课后随时往里加。老师发了大纲再传上来，它会补齐每一节和截止。', 'Add things once the course starts; the syllabus fills in the sessions later.')}</T>)}
      <Btn label={count ? L('下一步：核对每一节', 'Next: check the sessions') : L('至少选一样', 'Pick at least one')} onPress={count ? onNext : () => {}} />
    </View>
  );
}

function CanvasConnect({ c, reload }: { c: study.Course; reload: () => Promise<void> }) {
  const t = useTheme();
  const [base, setBase] = useState('');
  const [token, setToken] = useState('');
  const [msg, setMsg] = useState('');
  const [list, setList] = useState<{ id: number; name: string; term?: string | null }[]>([]);
  const connect = async () => {
    setMsg(L('在连…', 'Connecting…'));
    try {
      const r = await study.canvasConnect(base, token);
      setToken('');
      setList(r.courses);
      setMsg(L(`连上了：${r.user ?? ''}。这门课对应哪一门？`, `Connected as ${r.user ?? ''}. Which one is this course?`));
    } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
  };
  const pick = async (id: number) => {
    setMsg(L('在同步课件和作业…', 'Syncing files and assignments…'));
    try { await study.canvasLink(c.name, id); setList([]); setTimeout(() => { reload(); }, 4000); }
    catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
  };
  return (
    <View style={{ gap: space.sm }}>
      <WhereWhat where={L('Canvas 左边 Account → Settings → Approved Integrations → New Access Token，用途写 OpenMousse。', 'In Canvas: Account → Settings → Approved Integrations → New Access Token.')}
        what={L('复制那串令牌贴进来。只读，只拿课件、作业和截止。学校关了这个按钮，就用大纲和课件。', 'Paste the token. Read-only: files, assignments and due dates. If your school turned it off, use the syllabus and files.')} />
      <TextInput value={base} onChangeText={setBase} placeholder="https://canvas.example.edu" placeholderTextColor={t.ink3} autoCapitalize="none" style={[type.callout, styles.input, { backgroundColor: t.bg, color: t.ink, paddingVertical: 8 }]} />
      <View style={{ flexDirection: 'row', gap: space.sm, alignItems: 'center' }}>
        <TextInput value={token} onChangeText={setToken} placeholder={L('粘贴令牌', 'Paste the token')} placeholderTextColor={t.ink3} secureTextEntry autoCapitalize="none"
          style={[type.callout, styles.input, { flex: 1, backgroundColor: t.bg, color: t.ink, paddingVertical: 8 }]} />
        <Pressable onPress={connect} accessibilityRole="button" style={[styles.small, { backgroundColor: t.cyanSoft }]}><T v="callout" color={t.cyan} style={{ fontWeight: '600', fontSize: 13 }}>{L('连接', 'Connect')}</T></Pressable>
      </View>
      {c.canvas?.course_id ? <T v="callout" color={t.good} style={{ fontSize: 13 }}>{c.canvas_job?.status === 'running' ? (c.canvas_job.stage ?? L('在同步…', 'Syncing…')) : L(`已经连上 Canvas：${c.canvas.name ?? c.canvas.course_id}`, `Linked to Canvas: ${c.canvas.name ?? c.canvas.course_id}`)}</T> : null}
      {msg ? <T v="callout" color={t.ink2} style={{ fontSize: 13 }}>{msg}</T> : null}
      <View style={styles.wrap}>{list.map((x) => <Chip key={x.id} label={x.name + (x.term ? ` · ${x.term}` : '')} on={false} onPress={() => pick(x.id)} />)}</View>
    </View>
  );
}

// 第 3 步
const dayLabel = (d: string | null, time?: string) => {
  if (!d) return L('没日期', 'No date');
  const x = new Date(`${d}T12:00:00`);
  return (L(`${x.getMonth() + 1}/${x.getDate()} 周${'日一二三四五六'[x.getDay()]}`, x.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'numeric' }))) + (time ? ` ${time}` : '');
};
const readingLabel = (r: study.CourseReading) => (r.kind === 'textbook' && r.chapter ? L(`教材第 ${r.chapter} 章`, `Textbook ch. ${r.chapter}`) : r.title);

function Step3({ c, setC, onNext }: { c: study.Course; setC: (c: study.Course) => void; onNext: () => void }) {
  const t = useTheme();
  const marks = c.marks?.sessions ?? {};
  const dmarks = c.marks?.deadlines ?? {};
  const answer = (qid: string, a: string) => study.answer(c.name, qid, a).then(setC, (e) => showError(L('没改成', "Couldn't change it"), e));
  const hl = { backgroundColor: t.mode === 'dark' ? '#4A3A12' : '#FFEFC2', borderRadius: 4, paddingHorizontal: 3 };
  const running = c.syllabus_job?.status === 'running';
  return (
    <View style={{ gap: space.md }}>
      <T v="title" style={{ fontWeight: '700' }}>{L('核对每一节', 'Check the sessions')}</T>
      <T v="callout" color={t.ink2}>{running ? L('在读大纲，读完就填进来…', 'Reading the syllabus; it fills in when done…')
        : c.sessions.length ? L(`读到 ${c.sessions.length} 节、${c.deadlines.length} 个作业和考试。哪里不对，在下面直接说。`, `${c.sessions.length} sessions and ${c.deadlines.length} deadlines. Say below if anything is wrong.`)
          : L('还没有节。在下面跟学习 Agent 说（「这门课周一周三上，一共 8 节」），或者回上一步传大纲。', 'No sessions yet. Tell the study Agent below, or upload a syllabus.')}</T>
      <Card style={{ paddingVertical: 0 }}>
        {c.sessions.map((s, i) => {
          const mk = marks[s.id] ?? [];
          const isNew = mk[0] === 'new';
          const qs = c.questions.filter((q) => q.session === s.id);
          const dues = c.deadlines.filter((d) => d.session === s.id);
          return (
            <View key={s.id} style={[styles.sessRow, i > 0 && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }, (isNew || qs.length) ? { backgroundColor: isNew ? (t.mode === 'dark' ? '#2A2412' : '#FFF6DD') : t.warnSoft, marginHorizontal: -space.lg, paddingHorizontal: space.lg } : null]}>
              <View style={{ flexDirection: 'row', gap: 6, alignItems: 'center', width: 44 }}>
                <T v="headline" color={t.gold} style={{ fontSize: 14 }}>{`S${s.n}`}</T>
              </View>
              <View style={{ flex: 1, gap: 3 }}>
                <View style={{ flexDirection: 'row', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                  <T v="headline" style={[{ fontSize: 15 }, isNew || mk.includes('topic') ? hl : null]}>{s.topic}</T>
                  {isNew ? <T v="caption" color={t.cyan} style={{ fontWeight: '700' }}>{L('新', 'New')}</T> : mk.length && !(mk.length === 1 && mk[0] === 'n') ? <T v="caption" color={t.warn} style={{ fontWeight: '700' }}>{L('改了', 'Edited')}</T> : null}
                </View>
                <T v="caption" color={t.ink2} style={mk.includes('date') || mk.includes('time') ? hl : null}>{dayLabel(s.date, s.time)}</T>
                {qs.filter((q) => q.field === 'readings').length ? qs.filter((q) => q.field === 'readings').map((q) => (
                  <View key={q.id} style={{ gap: 6 }}>
                    <T v="caption" color={t.warn} style={{ fontWeight: '700' }}>{q.text}</T>
                    <View style={styles.wrap}>{q.options.map((o) => <Chip key={o} label={o} on={false} onPress={() => answer(q.id, o)} />)}</View>
                  </View>
                )) : <T v="caption" color={t.ink2} style={mk.includes('readings') ? hl : null}>{L('读：', 'Read: ') + (s.readings.map(readingLabel).join('；') || '—')}</T>}
                {dues.map((d) => <T key={d.id} v="caption" style={[{ fontWeight: '600' }, dmarks[d.id]?.length ? hl : null]}>{L('截止：', 'Due: ')}{d.title} · {d.due ? dayLabel(d.due.slice(0, 10), d.due.slice(11, 16)) : L('日期待定', 'TBC')}</T>)}
              </View>
            </View>
          );
        })}
      </Card>
      {c.deadlines.filter((d) => !d.session).length ? (
        <Card style={{ gap: 6 }}>
          <T v="caption" color={t.ink3}>{L('整门课的作业和考试', 'Course-wide assignments and exams')}</T>
          {c.deadlines.filter((d) => !d.session).map((d) => <T key={d.id} v="callout" style={dmarks[d.id]?.length ? hl : null}>{`${d.title} · ${d.due ? dayLabel(d.due.slice(0, 10), d.due.slice(11, 16)) : L('日期待定', 'date TBC')}`}</T>)}
        </Card>
      ) : null}
      {c.questions.filter((q) => q.field !== 'readings').map((q) => (
        <Card key={q.id} style={{ gap: 6, backgroundColor: t.warnSoft }}>
          <T v="callout" color={t.warn} style={{ fontWeight: '700' }}>{q.text}</T>
          <View style={styles.wrap}>{q.options.map((o) => <Chip key={o} label={o} on={false} onPress={() => answer(q.id, o)} />)}{q.options.length ? null : <Chip label={L('先不管', 'Skip for now')} on={false} onPress={() => answer(q.id, '')} />}</View>
        </Card>
      ))}
      <Btn label={c.setup.confirmed ? L('下一步：放材料，查齐', 'Next: add materials') : L('核对完了', 'Looks right')} onPress={c.sessions.length ? onNext : () => {}} />
    </View>
  );
}

// 第 4 步
function Step4({ c, setC, reload, onNext }: { c: study.Course; setC: (c: study.Course) => void; reload: () => Promise<void>; onNext: () => void }) {
  const t = useTheme();
  const sheet = useSheet();
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const pick = async (session?: string) => {
    const files = await pickDocuments().catch(() => [] as PendingFile[]);
    if (!files.length) return;
    setBusy(true);
    try {
      const r = await study.uploadFiles(c.name, files, session);
      setC(r.course);
      const filed = r.files.filter((f) => f.status === 'filed' || f.status === 'info');
      setNote(filed.map((f) => (f.n ? `${f.name} → S${f.n}` : f.name)).join('\n') + (r.files.length > filed.length ? L(`\n${r.files.length - filed.length} 个等你定`, `\n${r.files.length - filed.length} to file`) : ''));
    } catch (e) { showError(L('没传上', "Couldn't upload"), e); } finally { setBusy(false); }
  };
  const assign = (file: string) => sheet.open({ title: L('归到哪一节', 'File to which session'), content: (close) => (
    <View style={{ gap: 2, paddingBottom: space.lg }}>
      {c.sessions.map((s) => (
        <Pressable key={s.id} onPress={() => { close(); study.assign(c.name, file, s.id).then(setC, (e) => showError(L('没挪成', "Couldn't move it"), e)); }} style={styles.pickRow} accessibilityRole="button">
          <T v="body">{`S${s.n} ${s.topic}`}</T>
        </Pressable>
      ))}
      <Pressable onPress={() => { close(); study.assign(c.name, file, 'course').then(setC, () => {}); }} style={styles.pickRow}><T v="body" color={t.ink2}>{L('整门课的资料', 'Course info')}</T></Pressable>
    </View>
  ) });
  const skip = (s: study.CourseSession, rid: string, on: boolean) => study.skipReading(c.name, s.id, rid, on).then(setC, (e) => showError(L('没改成', "Couldn't change it"), e));
  const cnt = c.counts ?? {};
  const status = (s: study.CourseSession) => ({ ready: [L('齐了', 'Ready'), t.good], missing: [L(`缺 ${s.check.missing.length} 篇`, `${s.check.missing.length} missing`), t.warn], noslides: [L('缺课件', 'No slides'), t.warn],
    later: [L('还没上', 'Not yet'), t.ink3], empty: [L('还没有', 'Empty'), t.ink3] } as Record<string, [string, string]>)[s.check.status];
  return (
    <View style={{ gap: space.md }}>
      <T v="title" style={{ fontWeight: '700' }}>{L('放材料，查齐', 'Add materials')}</T>
      <T v="callout" color={t.ink2}>{L(`${cnt.ready ?? 0} 节齐了 · ${(cnt.missing ?? 0) + (cnt.noslides ?? 0)} 节缺材料 · ${(cnt.later ?? 0) + (cnt.empty ?? 0)} 节还没上课`, `${cnt.ready ?? 0} ready · ${(cnt.missing ?? 0) + (cnt.noslides ?? 0)} missing · ${(cnt.later ?? 0) + (cnt.empty ?? 0)} not yet`)}</T>
      <Btn kind="quiet" label={busy ? L('在传…', 'Uploading…') : L('从「文件」里选课件（文件或 zip）', 'Choose files or a zip')} onPress={() => pick()} />
      {note ? <T v="callout" color={t.ink2} style={{ fontSize: 13 }}>{note}</T> : null}
      {c.incoming.length ? (
        <Card style={{ gap: 6 }}>
          <T v="caption" color={t.ink3}>{L('等你定归哪一节', 'Waiting to be filed')}</T>
          {c.incoming.map((f) => (
            <Pressable key={f} onPress={() => assign(f)} accessibilityRole="button" style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
              <T v="callout" style={{ flex: 1 }} numberOfLines={1}>{f.split('/').pop()}</T><T v="callout" color={t.cyan} style={{ fontWeight: '600' }}>{L('归到…', 'File to…')}</T>
            </Pressable>
          ))}
        </Card>
      ) : null}
      <Card style={{ paddingVertical: 0 }}>
        {c.sessions.map((s, i) => {
          const [label, color] = status(s) ?? ['', t.ink3];
          const miss = s.check.readings.filter((r) => r.required && r.state !== 'have');
          return (
            <View key={s.id} style={[{ paddingVertical: space.md, gap: 6 }, i > 0 && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }]}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
                <T v="headline" style={{ flex: 1, fontSize: 14 }} numberOfLines={1}>{`S${s.n} ${s.topic}`}</T>
                <T v="caption" color={color} style={{ fontWeight: '700' }}>{label}</T>
              </View>
              <T v="caption" color={t.ink3}>{L(`课件 ${s.check.slides.length ? '✓' : '—'} · 阅读 ${s.check.have}/${s.check.total} · 录播 ${s.check.captions.length ? '✓' : '—'}`, `Files ${s.check.slides.length ? '✓' : '—'} · readings ${s.check.have}/${s.check.total} · captions ${s.check.captions.length ? '✓' : '—'}`)}</T>
              {s.check.status !== 'later' ? miss.map((r) => (
                <View key={r.id} style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
                  <T v="caption" color={t.ink2} style={{ flex: 1 }} numberOfLines={2}>{(r.state === 'skipped' ? L('跳过了：', 'Skipped: ') : L('缺：', 'Missing: ')) + readingLabel(r)}</T>
                  <Pressable onPress={() => pick(s.id)} hitSlop={6}><T v="caption" color={t.cyan} style={{ fontWeight: '600' }}>{L('传文件', 'Upload')}</T></Pressable>
                  <Pressable onPress={() => skip(s, r.id, r.state !== 'skipped')} hitSlop={6}><T v="caption" color={t.ink2} style={{ fontWeight: '600' }}>{r.state === 'skipped' ? L('撤销跳过', 'Undo skip') : L('先跳过', 'Skip')}</T></Pressable>
                </View>
              )) : null}
            </View>
          );
        })}
      </Card>
      <T v="caption" color={t.ink3}>{L('一堆文件的话，电脑上拖进学习台更方便。', 'For lots of files, dragging them into the desk on a computer is easier.')}</T>
      <Btn label={L('下一步：生成', 'Next: generate')} onPress={() => { reload(); onNext(); }} />
    </View>
  );
}

// 第 5 步
function Step5({ c, reload, onDone }: { c: study.Course; reload: () => Promise<void>; onDone: () => void }) {
  const t = useTheme();
  const ready = c.sessions.filter((s) => s.check.status === 'ready' && !s.has_page);
  const [pick, setPick] = useState<Set<string>>(() => new Set(ready.map((s) => s.id)));
  const [cards, setCards] = useState(true);
  const jobs = c.sessions.filter((s) => s.job);
  const live = jobs.some((s) => s.job && (s.job.status === 'queued' || s.job.status === 'running'));
  useEffect(() => {
    if (!live) return undefined;
    const id = setTimeout(() => { reload(); }, 5000);
    return () => clearTimeout(id);
  }, [live, c, reload]);
  const start = () => {
    study.generateSessions(c.name, [...pick], { cards, quiz: cards }).then(() => reload(), (e) => showError(L('没开始', "Couldn't start"), e));
  };
  const word = (v: string) => ({ queued: L('排队中', 'queued'), running: L('在写', 'writing'), done: L('写好了', 'done'), error: L('没成', 'failed'), skipped: L('跳过', 'skipped') } as Record<string, string>)[v] ?? v;
  const lab = (k: string) => ({ page: L('学习页', 'Notes'), path: L('路线', 'Path'), cards: L('闪卡', 'Cards'), quiz: L('小测', 'Quiz'), video: L('视频', 'Video') } as Record<string, string>)[k] ?? k;
  return (
    <View style={{ gap: space.md }}>
      <T v="title" style={{ fontWeight: '700' }}>{L('生成', 'Generate')}</T>
      <T v="callout" color={t.ink2}>{L('先生成材料齐了的节，其他的等材料到了再生成。', 'Complete sessions first; the rest when their materials arrive.')}</T>
      {jobs.length ? (
        <Card style={{ gap: space.sm }}>
          {jobs.map((s) => (
            <View key={s.id} style={{ gap: 4 }}>
              <T v="headline" style={{ fontSize: 14 }}>{`S${s.n} ${s.topic}`}</T>
              <T v="caption" color={t.ink2}>{Object.entries(s.job?.steps ?? {}).map(([k, v]) => `${lab(k)} · ${word(v)}`).join('   ')}</T>
              {s.job?.error ? <T v="caption" color={t.bad}>{s.job.error}</T> : null}
            </View>
          ))}
          {live ? <ActivityIndicator color={t.ink3} style={{ alignSelf: 'flex-start' }} /> : null}
        </Card>
      ) : null}
      {ready.length ? (
        <Card style={{ paddingVertical: 0 }}>
          {ready.map((s, i) => {
            const on = pick.has(s.id);
            return (
              <Pressable key={s.id} onPress={() => setPick((p) => { const n = new Set(p); if (on) n.delete(s.id); else n.add(s.id); return n; })} accessibilityRole="checkbox" accessibilityState={{ checked: on }}
                style={[styles.genRow, i > 0 && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }]}>
                <View style={[styles.box, { borderColor: on ? t.cyan : t.ink3, backgroundColor: on ? t.cyan : t.surface }]}>{on ? <Check size={12} color="#fff" /> : null}</View>
                <T v="body" style={{ flex: 1, fontSize: 15 }}>{`S${s.n} ${s.topic}`}</T>
              </Pressable>
            );
          })}
        </Card>
      ) : !jobs.length ? <T v="callout" color={t.ink3}>{L('还没有材料齐了的节。', 'No session has all its materials yet.')}</T> : null}
      {ready.length ? (
        <Pressable onPress={() => setCards((x) => !x)} accessibilityRole="checkbox" accessibilityState={{ checked: cards }} style={[styles.genRow, { paddingHorizontal: 0 }]}>
          <View style={[styles.box, { borderColor: cards ? t.cyan : t.ink3, backgroundColor: cards ? t.cyan : t.surface }]}>{cards ? <Check size={12} color="#fff" /> : null}</View>
          <T v="body" style={{ flex: 1, fontSize: 15 }}>{L('也出闪卡和小测', 'Flashcards and quiz too')}</T>
        </Pressable>
      ) : null}
      {ready.length ? <Btn label={pick.size ? L(`开始生成 ${pick.size} 节`, `Generate ${pick.size}`) : L('先选一节', 'Pick a session')} onPress={pick.size ? start : () => {}} /> : null}
      <T v="caption" color={t.ink3}>{c.gen_note ?? ''}</T>
      <Btn kind="quiet" label={L('去学习台看', 'Open the study desk')} onPress={onDone} />
    </View>
  );
}

// —— 底下的学习 Agent 对话（和「对话」tab 同一个线程）——

const TIPS = () => ({
  2: L('上面选好你们的课程网站，「去哪拿」跟着变。找不到就截个图发我，我告诉你点哪里。', "Pick your course site above and \"Where\" follows it. Can't find something? Send me a screenshot."),
  1: L('可以一项项填，也可以直接跟我说，比如「这学期的行为经济学，期末闭卷，微观学过」。', 'Fill it in, or just tell me, e.g. "Behavioural economics, closed-book final, I know micro".'),
  3: L('有不对的直接说，我改好标黄；改错了能撤销。', "Say what's wrong; I'll fix it and highlight the change. It can be undone."),
  4: L('缺的阅读我可以去找公开的版本，找不到你再决定跳不跳过。', "I can look for public copies of missing readings."),
  5: L('生成好一节我就在这里说一声。想换学习页的写法，直接跟我说。', "I'll post here when each session is done. Want a different writing style? Just say."),
}) as Record<number, string>;

function AgentMini({ agent, course, step, onReply }: { agent: string; course: string; step: number; onReply: () => void }) {
  const t = useTheme();
  const { threads, streaming, typing, send, refreshThread, cardsByThread, liveCards, groups } = useStore();
  useThreadOnScreen(agent);
  const [text, setText] = useState('');
  const [files, setFiles] = useState<PendingFile[]>([]);
  // 只显示这次打开以后的来回：学习 Agent 的对话里还有别的事，整段搬进加课页太长。完整的在它的「对话」里。
  const [loaded, setLoaded] = useState(false);
  useEffect(() => { refreshThread(agent).catch(() => {}).finally(() => setLoaded(true)); }, [agent, refreshThread]);
  const all = threads[agent];
  const [base, setBase] = useState<string | null | undefined>(undefined);
  if (loaded && base === undefined) setBase(all?.length ? all[all.length - 1].id : null);
  const busy = !!typing[agent];
  const [wasBusy, setWasBusy] = useState(busy);
  if (busy !== wasBusy) { setWasBusy(busy); if (!busy) onReply(); }
  const msgs = useMemo(() => {
    if (!all || base === undefined) return [];
    const i = base ? all.findIndex((m) => m.id === base) : -1;
    return all.slice(i + 1).slice(-6);
  }, [all, base]);
  const cards = useMemo(() => {
    const all = [...(cardsByThread[agent]?.cards ?? []), ...Object.values(liveCards[agent] ?? {})];
    return all.filter((c): c is CourseChangeCard => c.kind === 'course');
  }, [cardsByThread, liveCards, agent]);
  const name = groups.find((g) => g.id === agent)?.name ?? L('学习 Agent', 'Study Agent');
  const tip = TIPS()[step];
  const go = () => {
    const s = text.trim();
    if (!s && !files.length) return;
    send(agent, s || L('（截图）', '(screenshot)'), files.length ? files : undefined, { study: `${course}|${step}` });
    setText('');
    setFiles([]);
  };
  const attach = async () => {
    const got = Platform.OS === 'web' ? await pickDocuments().catch(() => []) : await pickMedia(false).catch(() => []);
    if (got.length) setFiles((f) => [...f, ...got]);
  };
  return (
    <Card style={{ gap: space.sm }}>
      <T v="headline" style={{ fontSize: 14 }}>{name}</T>
      {tip ? <View style={[styles.tip, { backgroundColor: t.bg }]}><T v="callout" style={{ fontSize: 14 }}>{tip}</T></View> : null}
      {msgs.map((m) => {
        const txt = m.body.type === 'text' ? m.body.text : '';
        const mine = cards.filter((c) => `db${c.messageId}` === m.id);
        if (m.role === 'user') return <View key={m.id} style={[styles.userBub, { backgroundColor: t.goldSoft }]}><T v="callout">{txt}</T></View>;
        if (m.role === 'auto') return <T key={m.id} v="caption" color={t.ink3} style={{ textAlign: 'center' }}>{txt}</T>;
        return <View key={m.id} style={{ gap: 6 }}><Markdown text={txt} small />{mine.map((c) => <CourseChip key={c.id} card={c} />)}</View>;
      })}
      {busy ? <View style={{ gap: 6 }}>{streaming[agent] ? <Markdown text={streaming[agent]} small /> : <ActivityIndicator color={t.ink3} style={{ alignSelf: 'flex-start' }} />}
        {cards.filter((c) => c.messageId == null).map((c) => <CourseChip key={c.id} card={c} />)}</View> : null}
      {files.length ? <T v="caption" color={t.ink2}>{L(`${files.length} 张图 / 文件，发的时候一起带上`, `${files.length} attachment(s) will be sent`)}</T> : null}
      <View style={{ flexDirection: 'row', gap: space.sm, alignItems: 'flex-end' }}>
        <Pressable onPress={attach} accessibilityRole="button" accessibilityLabel={L('发截图或文件', 'Send a screenshot or file')} style={[styles.icon, { borderColor: t.line }]}><ImageIcon size={18} color={t.ink2} /></Pressable>
        <TextInput value={text} onChangeText={setText} multiline placeholder={step === 3 ? L('有不对的直接说…', "Say what's wrong…") : L('找不到？问它，或者发截图', 'Stuck? Ask, or send a screenshot')} placeholderTextColor={t.ink3}
          style={[type.callout, styles.input, { flex: 1, backgroundColor: t.bg, color: t.ink, paddingVertical: 9, maxHeight: 100 }]} />
        <Pressable onPress={go} accessibilityRole="button" accessibilityLabel={L('发送', 'Send')} style={[styles.icon, { backgroundColor: t.goldFill, borderColor: t.goldFill }]}><Send size={17} color={t.onGold} /></Pressable>
      </View>
      <T v="caption" color={t.ink3}>{L('这段对话就是学习 Agent 的「对话」，课建好以后在那里说也一样能改。', "This is the study Agent's chat; after setup you can keep changing the course there.")}</T>
    </Card>
  );
}

const styles = StyleSheet.create({
  dots: { flexDirection: 'row', gap: 6, paddingHorizontal: space.lg, paddingTop: space.sm },
  dot: { flex: 1, height: 4, borderRadius: 2 },
  input: { borderRadius: radius.md, paddingHorizontal: space.md, paddingVertical: 12 },
  wrap: { flexDirection: 'row', flexWrap: 'wrap', gap: space.sm },
  chip: { minHeight: 34, paddingHorizontal: 12, paddingVertical: 6, borderRadius: 17, borderWidth: 1.5, alignItems: 'center', justifyContent: 'center' },
  ww: { borderRadius: radius.md, padding: space.md, gap: 6 },
  hhead: { flexDirection: 'row', alignItems: 'center', gap: space.md, padding: space.md },
  check: { width: 22, height: 22, borderRadius: 11, borderWidth: 2, alignItems: 'center', justifyContent: 'center' },
  small: { height: 34, paddingHorizontal: 14, borderRadius: 10, alignItems: 'center', justifyContent: 'center' },
  sessRow: { flexDirection: 'row', gap: space.sm, paddingVertical: space.md },
  pickRow: { paddingVertical: 12 },
  genRow: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: space.md },
  box: { width: 22, height: 22, borderRadius: 6, borderWidth: 2, alignItems: 'center', justifyContent: 'center' },
  tip: { borderRadius: 14, borderBottomLeftRadius: 4, padding: space.md },
  userBub: { alignSelf: 'flex-end', maxWidth: '88%', borderRadius: 14, borderBottomRightRadius: 4, paddingHorizontal: 12, paddingVertical: 8 },
  icon: { width: 38, height: 38, borderRadius: 10, borderWidth: StyleSheet.hairlineWidth, alignItems: 'center', justifyContent: 'center' },
});
