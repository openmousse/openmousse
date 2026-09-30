'use strict';
// 学习台：加一门课的五步向导（2026-09-30）。study.html 里的 h / L / api / S / renderMD 这些直接用。
// 左边五步，中间每一步的内容，右边一直开着学习 Agent 的对话（和 app 里「对话」同一个线程）：说哪里不对它就改，改过的标黄、能撤销。
// 数据都在服务器（/api/study/courses/*，courses.py），做到哪存到哪，关掉下次接着。

const ICON = {
  book: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 7v14"/><path d="M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z"/></svg>',
  image: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="18" height="18" x="3" y="3" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/></svg>',
  list: '<svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/><path d="M3 6h.01"/><path d="M3 12h.01"/><path d="M3 18h.01"/></svg>',
  folder: '<svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/></svg>',
  globe: '<svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><path d="M2 12h20"/></svg>',
  plus: '<svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M8 12h8"/><path d="M12 8v8"/></svg>',
  upload: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M17 8l-5-5-5 5"/><path d="M12 3v12"/></svg>',
};
const icon = name => h('span', { class: 'svgi', html: ICON[name] });
const W = { open: false, name: null, c: null, step: 1, draft: null, folders: [], agent: null, busy: false, chatSeq: 0, lastUpload: null, gen: null, poll: 0, pending: [] };
const cpath = (name, rest) => '/api/study/courses/' + encodeURIComponent(name) + (rest || '');
const EXAMS = () => [['closed', L('闭卷笔试', 'Closed-book exam')], ['open', L('开卷考试', 'Open-book exam')], ['essay', L('书面作业', 'Written coursework')],
  ['group', L('小组作业', 'Group work')], ['present', L('展示', 'Presentation')], ['unknown', L('还不知道', 'Not sure yet')]];
const LEARNS = () => [['zh', L('中文讲，术语留英文', 'Chinese, keep terms in English')], ['intuition', L('先讲直觉再上公式', 'Intuition before formulas')],
  ['examples', L('多举例子和案例', 'Lots of examples and cases')], ['video', L('要讲解视频', 'Explainer videos')], ['practice', L('多做题', 'More practice')]];
const STEPS = () => [[L('说说这门课', 'About the course'), L('课名、考试、怎么学', 'Name, exams, how you learn')],
  [L('你手上有什么', 'What you have'), L('大纲、课件或课程网站', 'Syllabus, files or course site')],
  [L('核对每一节', 'Check the sessions'), L('主题、日期、阅读、截止', 'Topics, dates, readings, due dates')],
  [L('放材料，查齐', 'Add materials'), L('缺什么先提醒', "Flags what's missing")],
  [L('生成', 'Generate'), L('学习页、路线、闪卡、小测', 'Notes, path, cards, quiz')]];
const PLAT = () => ({
  canvas: { label: 'Canvas', syl: L('Canvas 课程左边的 Syllabus，或者第一个模块里叫 Course outline 的文件。', 'The Syllabus link on the left of the Canvas course, or a Course outline file in the first module.'),
    files: L('Canvas 的 Modules 里一节一节下；Files 页可以多选，打包成 zip 下载。', 'Download from Modules session by session; on the Files page you can select several and download a zip.'),
    tip: L('Canvas 的话，大纲在课程左边的 Syllabus；课件去 Modules 下，或者在 Files 里多选打包。想以后自动同步，就用第三项「连上 Canvas」。找不到就截个图问我。',
      'On Canvas the syllabus is under Syllabus on the left; files are in Modules, or select several in Files and download a zip. To sync automatically later, use "Connect Canvas". Stuck? Send me a screenshot.') },
  moodle: { label: 'Moodle', syl: L('Moodle 课程主页最上面那一块，常叫 Course outline 或 Module guide。', 'The top block of the Moodle course page, often called Course outline or Module guide.'),
    files: L('每周那一块里点开下载；老师开了「下载课程内容」的话，课程菜单里能一次打包。', "Open each week's block and download; if the lecturer enabled \"Download course content\", the course menu packs everything at once."),
    tip: L('Moodle 还不能直接连，先传大纲和课件。每周那一块里找不到的话，截个图问我。', "Moodle can't be connected yet, so upload the syllabus and files. Can't find them in the weekly blocks? Send me a screenshot.") },
  blackboard: { label: 'Blackboard', syl: L('Course Content 里第一个文件夹，常叫 Syllabus 或 Module handbook。', 'The first folder in Course Content, often called Syllabus or Module handbook.'),
    files: L('Course Content 里一个文件夹一个文件夹下。', 'Download folder by folder in Course Content.'),
    tip: L('Blackboard 还不能直接连，先传大纲和课件。Course Content 里找不到的话，截个图问我。', "Blackboard can't be connected yet, so upload the syllabus and files. Can't find them in Course Content? Send me a screenshot.") },
  other: { label: L('别的 / 不知道', 'Other / not sure'), syl: L('课程网站首页或第一个模块，常叫 Syllabus、Course outline、Module handbook。', 'The course home page or first module, often called Syllabus, Course outline or Module handbook.'),
    files: L('课程网站的 Files 或 Modules，一节一节下；能整门打包就下 zip。', 'Files or Modules on the course site, session by session; download a zip if the whole course can be packed.'),
    tip: L('不知道用的是什么也没关系：截一张课程网站首页的图发我，我告诉你大纲和课件在哪。', "No problem if you're not sure: send me a screenshot of the course home page and I'll point out where the syllabus and files are.") },
});
const EXTRAS = () => [
  ['textbook', L('教材', 'Textbook'), L('阅读清单里的电子书链接、图书馆，或者你自己的 PDF / EPUB。', 'The e-book link on the reading list, the library, or your own PDF / EPUB.'), L('整本传上来就行，写学习页时按章节取用。', 'Upload the whole book; notes pick the chapters they need.'), 'file'],
  ['past', L('往年试卷', 'Past papers'), L('课程网站的 Past papers 或 Exams 模块，或者问学长学姐。', 'The Past papers or Exams module, or ask students from last year.'), L('有答案更好。按题型整理，考前出模拟题。', 'Answers help. Sorted by question type for mock exams.'), 'file'],
  ['captions', L('录播字幕', 'Lecture captions'), L('录播平台（Panopto、Echo360、Zoom 回放）能下载字幕的话，下 .vtt 或 .srt。', 'If the recording platform (Panopto, Echo360, Zoom) lets you download captions, get the .vtt or .srt.'), L('字幕就够，不用视频。学校不让下载就算了。', "Captions are enough, no video. Skip it if the school doesn't allow downloads."), 'file'],
  ['online', L('网课链接', 'Online lectures'), L('YouTube、Coursera、B 站的链接直接贴。', 'Paste YouTube, Coursera or Bilibili links.'), L('记下链接，挂在那一节。', 'The link is kept with that session.'), 'link'],
  ['notes', L('自己的笔记', 'Your own notes'), L('Notion、Obsidian、Word 都行，手写的拍照也行。', 'Notion, Obsidian, Word; photos of handwritten notes work too.'), L('跟课件对照，标出你漏掉的。', 'Compared with the slides to show what you missed.'), 'file'],
];

// —— 打开 / 关掉 ——
async function openWizard(name, step) {
  W.open = true;
  W.name = name || null;
  W.c = null;
  W.gen = null;
  W.lastUpload = null;
  document.body.classList.add('wz');
  store.set('study.wizard', JSON.stringify({ name: W.name, step: step || null }));
  try {
    const list = await api('/api/study/courses');
    W.folders = list.folders || [];
    W.agent = list.agent || null;
    if (W.name) W.c = (await api(cpath(W.name))).course;
  } catch (e) { if (e.message !== 'auth') alert(e.message); }
  W.step = step || (W.c ? (W.c.setup.confirmed && W.c.setup.step < 4 ? 4 : W.c.setup.step) : 1);
  W.draft = draftOf(W.c);
  paintWizard();
  loadAgentChat();
}
function draftOf(c) {
  return { name: c ? c.title : '', term: c ? c.term : '', exam: new Set(c ? c.exam : []), learn: new Set(c ? c.learn : ['zh', 'intuition'].filter(() => LANG === 'zh')),
    notes: c ? c.notes : '', adopt: '', platform: c ? c.platform : '', have: { ...(c ? c.have : {}) }, extras: c ? [...c.extras] : [] };
}
function closeWizard() {
  W.open = false;
  clearTimeout(W.poll);
  document.body.classList.remove('wz');
  store.set('study.wizard', null);
  const name = W.name;
  loadTree().then(() => {
    renderNav();
    const names = S.tree.courses.map(c => c.name);
    if (name && names.includes(name)) selectCourse(name); else if (S.course) selectCourse(S.course, !!S.unit);
  }).catch(() => {});
}
async function reloadCourse() {
  if (!W.name) return;
  try { W.c = (await api(cpath(W.name))).course; } catch { /* 下次再说 */ }
}
function goStep(n) {
  W.step = n;
  store.set('study.wizard', JSON.stringify({ name: W.name, step: n }));
  if (W.name && W.c && n > (W.c.setup.step || 1) && !(n >= 4 && !W.c.setup.confirmed)) api(cpath(W.name), jsonReq('PATCH', { step: n })).catch(() => {});
  paintWizard();
  paintTip();
}
const jsonReq = (method, body) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });

// —— 画 ——
function paintWizard() {
  if (!W.open) return;
  const nav = $('#wzNav'), main = $('#wzBody'), foot = $('#wzFoot');
  const steps = STEPS();
  nav.innerHTML = '';
  nav.append(h('div', { class: 'wzt' }, L('加一门课', 'Add a course')), h('div', { class: 'wzs' }, W.c ? W.c.title : (W.draft.name || L('还没起名', 'Untitled'))));
  steps.forEach(([t, sub], i) => {
    const n = i + 1, cur = W.step === n;
    const done = (W.c && (W.c.setup.step > n || (n === 3 && W.c.setup.confirmed))) || n < W.step;
    const can = n === 1 || !!W.c;
    nav.append(h('button', { class: 'wzstep' + (cur ? ' on' : '') + (done ? ' done' : ''), disabled: !can, 'aria-current': cur ? 'step' : null, onclick: () => can && goStep(n) },
      h('span', { class: 'n' }, done && !cur ? '✓' : String(n)), h('span', {}, h('b', {}, t), h('small', {}, sub))));
  });
  nav.append(h('div', { class: 'grow' }), h('div', { class: 'wznote' }, L('做到哪存到哪，关掉下次接着。手机上也能加。', 'Progress is saved as you go. You can also add courses on your phone.')));
  main.innerHTML = '';
  foot.innerHTML = '';
  const body = [null, step1, step2, step3, step4, step5][W.step]();
  main.append(body);
  $('#wzCancel').textContent = W.c && W.c.setup.confirmed ? L('完成', 'Done') : L('取消', 'Cancel');
}
function footer(note, { prev = true, next = null, nextLabel = L('下一步', 'Next'), disabled = false } = {}) {
  const foot = $('#wzFoot');
  foot.innerHTML = '';
  if (prev && W.step > 1) foot.append(h('button', { class: 'pill ghost', onclick: () => goStep(W.step - 1) }, L('上一步', 'Back')));
  foot.append(h('div', { class: 'grow note' }, note || ''));
  if (next) foot.append(h('button', { class: 'pill', disabled, onclick: disabled ? null : next }, nextLabel));
}
const chip = (label, on, onclick) => h('button', { class: 'wzchip' + (on ? ' on' : ''), 'aria-pressed': on ? 'true' : 'false', onclick }, label);
const whereWhat = (where, what) => h('div', { class: 'ww' }, where ? h('div', {}, h('b', {}, L('去哪拿', 'Where')), h('span', {}, where)) : null, h('div', {}, h('b', {}, L('拿什么', 'What')), h('span', {}, what)));
const dayLabel = d => { if (!d) return ''; const x = new Date(d + 'T12:00:00'); return LANG === 'zh' ? `${x.getMonth() + 1}/${x.getDate()} 周${'日一二三四五六'[x.getDay()]}` : x.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'numeric' }); };
const dueLabel = due => (due ? dayLabel(due.slice(0, 10)) + (due.length > 10 && due.slice(11, 16) !== '23:59' ? ' ' + due.slice(11, 16) : '') : L('日期待定', 'Date TBC'));
const readingLabel = r => (r.kind === 'textbook' && r.chapter ? L(`教材第 ${r.chapter} 章`, `Textbook ch. ${r.chapter}`) : r.title);

// 第 1 步：说说这门课
function step1() {
  const d = W.draft;
  const nameIn = h('input', { id: 'wz-name', value: d.name || '', placeholder: L('比如：Behavioural Economics 行为经济学', 'e.g. Behavioural Economics') });
  nameIn.addEventListener('input', () => { d.name = nameIn.value; paintFoot1(); });
  const termIn = h('input', { id: 'wz-term', value: d.term || '', placeholder: L('比如：2026 秋季', 'e.g. Autumn 2026') });
  termIn.addEventListener('input', () => { d.term = termIn.value; });
  const notes = h('textarea', { id: 'wz-more', rows: 3, placeholder: L('比如：微观学过，前景理论没学过；每节控制在两小时内。', "e.g. I know micro but not prospect theory; keep each session under two hours.") });
  notes.value = d.notes || '';
  notes.addEventListener('input', () => { d.notes = notes.value; });
  const group = (set, items) => h('div', { class: 'chips' }, items.map(([k, label]) => chip(label, set.has(k), e => { set.has(k) ? set.delete(k) : set.add(k); e.currentTarget.classList.toggle('on'); e.currentTarget.setAttribute('aria-pressed', set.has(k)); })));
  const adopt = !W.c && W.folders.length ? h('div', { class: 'field' }, h('label', { for: 'wz-adopt' }, L('课件目录里已经有这些文件夹（比如课程网站同步下来的）', 'These folders are already in the materials directory (e.g. synced from a course site)')),
    (() => {
      const sel = h('select', { id: 'wz-adopt' }, h('option', { value: '' }, L('不用，建一门新的', 'No, start a new course')), W.folders.map(f => h('option', { value: f, selected: d.adopt === f }, f)));
      sel.addEventListener('change', () => { d.adopt = sel.value; if (sel.value && !d.name) { d.name = sel.value; nameIn.value = sel.value; } paintFoot1(); });
      return sel;
    })()) : null;
  const paintFoot1 = () => footer(L('档案以后在这门课的设置里随时能改。', 'You can change this any time in the course settings.'), { prev: false, next: save1, disabled: !(d.name || '').trim() });
  setTimeout(paintFoot1, 0);
  return h('div', { class: 'wzstep1' },
    h('h1', {}, L('说说这门课', 'About the course')), h('p', { class: 'lead' }, L('存成这门课的档案。以后每次写学习页、出闪卡和小测都会带上，随时能改。', 'Saved as the course profile. Every study page, flashcard and quiz uses it, and you can change it any time.')),
    h('div', { class: 'two' }, h('div', { class: 'field' }, h('label', { for: 'wz-name' }, L('课名', 'Course name')), nameIn), h('div', { class: 'field' }, h('label', { for: 'wz-term' }, L('学校和学期（可选）', 'School and term (optional)')), termIn)),
    adopt,
    h('div', { class: 'field' }, h('div', { class: 'lab' }, L('考试怎么考（可多选）', 'How it is assessed (choose any)')), group(d.exam, EXAMS())),
    h('div', { class: 'field' }, h('div', { class: 'lab' }, L('你喜欢怎么学（可多选）', 'How you like to learn (choose any)')), group(d.learn, LEARNS())),
    h('div', { class: 'field' }, h('label', { for: 'wz-more' }, L('还想说的（可选）', 'Anything else (optional)')), notes));
}
async function save1() {
  const d = W.draft;
  try {
    if (!W.c) {
      const r = await api('/api/study/courses', jsonReq('POST', { name: d.adopt || d.name.trim(), title: d.name.trim(), term: d.term || '', exam: [...d.exam], learn: [...d.learn], notes: d.notes || '', adopt: !!d.adopt }));
      W.name = r.name;
      W.c = r.course;
    } else {
      W.c = (await api(cpath(W.name), jsonReq('PATCH', { title: d.name.trim(), term: d.term || '', exam: [...d.exam], learn: [...d.learn], notes: d.notes || '' }))).course;
    }
  } catch (e) { if (e.message !== 'auth') alert(e.message); return; }
  goStep(2);
}

// 第 2 步：你手上有什么
function step2() {
  const d = W.draft, P = PLAT(), plat = d.platform || 'canvas', p = P[plat];
  const wrap = h('div', { class: 'wzstep2' },
    h('h1', {}, L('你手上有什么？', 'What do you have?')), h('p', { class: 'lead' }, L('有几样选几样，都没有也行。选了就告诉你去哪拿、拿什么；找不到就问右边。', "Pick what you have, or nothing at all. Each tells you where to find it and what to get; stuck? Ask on the right.")));
  wrap.append(h('div', { class: 'platrow' }, h('span', { class: 'lab' }, L('你们的课程网站', 'Your course site')),
    Object.entries(P).map(([k, v]) => chip(v.label, plat === k, () => setPlatform(k)))));
  wrap.append(h('div', { class: 'banner' }, h('b', {}, L('最少', 'Minimum')), h('span', {}, L('大纲，或者第一节的课件，就能开始。', 'A syllabus, or the first session\'s slides, is enough to start.')),
    h('b', {}, L('越全越好', 'The more the better')), h('span', {}, L('阅读、练习和答案、往年试卷、录播字幕。', 'Readings, exercises and answers, past papers, lecture captions.'))));
  const card = (key, icon, title, sub, content) => {
    const on = !!d.have[key];
    const el = h('div', { class: 'hcard' + (on ? ' on' : '') },
      h('button', { class: 'hhead', 'aria-pressed': on ? 'true' : 'false', onclick: () => toggleHave(key) }, h('span', { class: 'hi ' + key }, icon), h('span', { class: 'ht' }, h('b', {}, title), h('small', {}, sub)), h('span', { class: 'check' }, on ? '✓' : '')),
      on ? content() : null);
    return el;
  };
  const syl = W.c && W.c.syllabus;
  const sylJob = W.c && W.c.syllabus_job;
  wrap.append(h('div', { class: 'hgrid' },
    card('syllabus', icon('list'), L('课程大纲 syllabus', 'Syllabus'), L('最省事：一份就能建好每一节和截止', 'Easiest: one file sets up every session and deadline'), () => h('div', { class: 'hbody' },
      whereWhat(p.syl, L('列了每节主题、阅读、作业和截止的那份。PDF、Word、网页链接都行。', 'The one listing each session\'s topic, readings, assignments and deadlines. PDF, Word or a web link.')),
      syl && (syl.file || syl.url) ? h('div', { class: 'filebox' }, h('span', { class: 'ext' }, (syl.file || 'URL').split('.').pop().slice(0, 4).toUpperCase()),
        h('span', { class: 'fb' }, h('b', {}, (syl.file || syl.url || '').split('/').pop()),
          sylJob && sylJob.status === 'running' ? h('small', {}, h('span', { class: 'spin' }), L('在读…（一两分钟）', 'Reading… (a minute or two)'))
            : sylJob && sylJob.status === 'error' ? h('small', { class: 'bad' }, L('没读成：', "Couldn't read it: ") + (sylJob.error || ''))
              : syl.summary ? h('small', { class: 'good' }, L('读完了：', 'Read: ') + syl.summary) : null),
        pickButton(L('换一份', 'Replace'), files => uploadSyllabus(files[0]))) : dropZone(L('把大纲拖到这里，或者', 'Drop the syllabus here, or'), files => uploadSyllabus(files[0]), false),
      linkRow(L('或者贴大纲的网页链接', 'Or paste a link to the syllabus'), url => syllabusFrom({ url })))),
    card('files', icon('folder'), L('课件文件', 'Course files'), L('slides、讲义、阅读、练习和答案', 'Slides, handouts, readings, exercises, answers'), () => h('div', { class: 'hbody' },
      whereWhat(p.files, L('文件名带 Week 3、Session 3、Lecture 3 的自动归到那一节，认不出的会问你。', 'Files named Week 3, Session 3 or Lecture 3 are filed to that session; anything unclear is asked about.')),
      dropZone(L('把文件或 zip 拖到这里，或者', 'Drop files or a zip here, or'), files => uploadFiles(files), true),
      W.lastUpload ? h('div', { class: 'note' }, uploadSummary(W.lastUpload)) : null)),
    card('site', icon('globe'), plat === 'canvas' ? L('连上 Canvas', 'Connect Canvas') : L('连上课程网站', 'Connect the course site'),
      plat === 'canvas' ? L('以后新课件和截止自动同步', 'New files and deadlines sync by themselves') : L(`${p.label} 还不能直接连`, `${p.label} can't be connected yet`), () => h('div', { class: 'hbody' },
        plat === 'canvas' ? canvasBox() : h('div', { class: 'ww' }, L(`还不能直接连 ${plat === 'other' ? '这个网站' : p.label}。先用大纲和课件，以后新课件出来拖进来就行。`, `${plat === 'other' ? 'This site' : p.label} can't be connected yet. Use the syllabus and files; drop new files in when they come out.`)))),
    card('none', icon('plus'), L('还没有', 'Nothing yet'), L('先建一门空课', 'Start with an empty course'), () => h('div', { class: 'hbody' },
      h('div', { class: 'ww' }, L('开课后随时往里加。老师发了大纲再传上来，它会补齐每一节和截止。', 'Add things once the course starts. Upload the syllabus when it comes out and it fills in the sessions and deadlines.'))))));
  // 还有别的
  const picked = new Set(d.extras.map(x => x.key));
  const custom = h('input', { placeholder: L('自己写一样，比如：老师的 GitHub', "Add your own, e.g. the lecturer's GitHub"), 'aria-label': L('自己写一样', 'Add your own') });
  const addCustom = () => { const t = custom.value.trim(); if (!t) return; d.extras.push({ key: 'custom', label: t }); custom.value = ''; saveHave(); paintWizard(); };
  custom.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.isComposing && e.keyCode !== 229) addCustom(); });
  wrap.append(h('div', { class: 'extras' }, h('div', { class: 'lab' }, L('还有别的？', 'Anything else?')),
    h('div', { class: 'chips' }, EXTRAS().map(([k, label]) => chip(label, picked.has(k), () => { if (picked.has(k)) d.extras = d.extras.filter(x => x.key !== k); else d.extras.push({ key: k, label }); saveHave(); paintWizard(); })),
      custom, h('button', { class: 'wzchip add', onclick: addCustom }, L('加上', 'Add'))),
    d.extras.length ? h('div', { class: 'xlist' }, d.extras.map(x => {
      const ex = EXTRAS().find(e => e[0] === x.key);
      return h('div', { class: 'xrow' }, h('b', {}, x.label), h('div', { class: 'xb' },
        ex ? whereWhat(ex[2], ex[3]) : whereWhat('', L('右边问一句：在哪、多大、要不要按节分。', 'Ask on the right: where it is, how big, whether to split by session.'))),
        ex && ex[4] === 'link' ? h('button', { class: 'wzchip add', onclick: () => { const u = prompt(L('贴链接', 'Paste the link')); if (u) askAgent(L(`网课链接：${u}`, `Online lecture link: ${u}`)); } }, L('贴链接', 'Paste link'))
          : pickButton(L('传文件', 'Upload'), files => uploadFiles(files, x.key === 'captions' ? null : 'course')));
    })) : null));
  const count = ['syllabus', 'files', 'site', 'none'].filter(k => d.have[k]).length;
  setTimeout(() => footer(count ? L(`选了 ${count} 样`, `${count} chosen`) + (d.extras.length ? L(`，另外 ${d.extras.length} 样别的。`, `, plus ${d.extras.length} more.`) : L('。', '.'))
    : L('至少选一样；什么都没有就选「还没有」。', 'Pick at least one; choose "Nothing yet" if you have nothing.'), { next: () => goStep(3), disabled: !count }), 0);
  return wrap;
}
function setPlatform(k) { W.draft.platform = k; saveHave(); paintWizard(); paintTip(); }
function toggleHave(k) { W.draft.have[k] = !W.draft.have[k]; if (k === 'none' && W.draft.have.none) { /* 还没有：别的照留着 */ } saveHave(); paintWizard(); }
function saveHave() {
  if (!W.name) return;
  api(cpath(W.name), jsonReq('PATCH', { platform: W.draft.platform || '', have: W.draft.have, extras: W.draft.extras })).then(r => { W.c = r.course; }).catch(() => {});
}
function pickButton(label, onFiles, multiple) {
  const inp = h('input', { type: 'file', multiple: !!multiple, style: 'display:none' });
  inp.addEventListener('change', () => { if (inp.files.length) onFiles([...inp.files]); inp.value = ''; });
  return h('span', {}, inp, h('button', { class: 'wzchip add', onclick: () => inp.click() }, label));
}
function dropZone(text, onFiles, multiple) {
  const z = h('div', { class: 'drop' }, icon('upload'), h('span', {}, text + ' '), pickButton(L('选文件', 'choose files'), onFiles, multiple));
  z.addEventListener('dragover', e => { e.preventDefault(); z.classList.add('over'); });
  z.addEventListener('dragleave', () => z.classList.remove('over'));
  z.addEventListener('drop', e => { e.preventDefault(); z.classList.remove('over'); const f = [...(e.dataTransfer?.files || [])]; if (f.length) onFiles(multiple ? f : [f[0]]); });
  return z;
}
function linkRow(ph, onUrl) {
  const inp = h('input', { placeholder: 'https://…', 'aria-label': ph });
  const go = () => { const u = inp.value.trim(); if (/^https?:\/\//i.test(u)) onUrl(u); };
  inp.addEventListener('keydown', e => { if (e.key === 'Enter') go(); });
  return h('div', { class: 'linkrow' }, h('span', { class: 'note' }, ph), inp, h('button', { class: 'wzchip add', onclick: go }, L('读', 'Read')));
}
async function uploadSyllabus(file) {
  if (!file || !W.name) return;
  const fd = new FormData();
  fd.append('file', file);
  try {
    const r = await fetch(cpath(W.name, '/syllabus'), { method: 'POST', headers: hdrs(), body: fd });
    if (r.status === 401) { showAuth(); return; }
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || j.error || 'HTTP ' + r.status);
  } catch (e) { alert(e.message); return; }
  W.draft.have.syllabus = true;
  await reloadCourse();
  paintWizard();
  pollSyllabus();
}
async function syllabusFrom(body) {
  try { await api(cpath(W.name, '/syllabus/from'), jsonReq('POST', body)); } catch (e) { if (e.message !== 'auth') alert(e.message); return; }
  W.draft.have.syllabus = true;
  await reloadCourse();
  paintWizard();
  pollSyllabus();
}
function pollSyllabus() {
  clearTimeout(W.poll);
  W.poll = setTimeout(async () => {
    if (!W.open) return;
    let j;
    try { j = await api(cpath(W.name, '/syllabus')); } catch { return; }
    if (j.job && j.job.status === 'running') { pollSyllabus(); return; }
    await reloadCourse();
    if (W.step === 2 || W.step === 3) paintWizard();
  }, 3000);
}
async function uploadFiles(files, session) {
  if (!files.length || !W.name) return;
  const fd = new FormData();
  for (const f of files) fd.append('files', f);
  if (session) fd.append('session', session);
  $('#wzBody').classList.add('busy');
  try {
    const r = await fetch(cpath(W.name, '/files'), { method: 'POST', headers: hdrs(), body: fd });
    if (r.status === 401) { showAuth(); return; }
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || j.error || 'HTTP ' + r.status);
    W.lastUpload = j;
    W.c = j.course;
    W.draft.have.files = true;
  } catch (e) { alert(e.message); } finally { $('#wzBody').classList.remove('busy'); }
  paintWizard();
}
function uploadSummary(j) {
  const f = j.files || [], filed = f.filter(x => x.status === 'filed' || x.status === 'info').length, ask = f.filter(x => x.status === 'ask').length;
  return L(`传了 ${f.length} 个：${filed} 个归好了` + (ask ? `，${ask} 个在第 4 步等你定` : ''), `${f.length} uploaded: ${filed} filed` + (ask ? `, ${ask} waiting for you in step 4` : ''));
}

// Canvas 个人令牌
function canvasBox() {
  const c = W.c || {};
  const cv = c.canvas || null;
  const base = h('input', { placeholder: 'https://canvas.example.edu', value: (cv && cv.base) || '', 'aria-label': L('Canvas 网址', 'Canvas address') });
  const tok = h('input', { type: 'password', placeholder: L('粘贴令牌', 'Paste the token'), 'aria-label': L('Canvas 令牌', 'Canvas token') });
  const out = h('div', { class: 'note' });
  const list = h('div', { class: 'cvlist' });
  const connect = async () => {
    out.textContent = L('在连…', 'Connecting…');
    try {
      const r = await api('/api/study/canvas/connect', jsonReq('POST', { base: base.value.trim(), token: tok.value.trim() }));
      tok.value = '';
      out.textContent = L(`连上了：${r.user || ''}。选这门课对应的是哪一门：`, `Connected as ${r.user || ''}. Which one is this course?`);
      list.innerHTML = '';
      for (const x of r.courses || []) list.append(h('button', { class: 'wzchip', onclick: () => linkCanvas(x.id, out) }, x.name + (x.term ? ' · ' + x.term : '')));
    } catch (e) { out.textContent = e.message; }
  };
  return h('div', {}, whereWhat(L('Canvas 左边 Account → Settings → Approved Integrations → New Access Token，用途写 OpenMousse。', 'In Canvas: Account → Settings → Approved Integrations → New Access Token; purpose: OpenMousse.'),
    L('复制那串令牌贴进来。只读，只拿课件、作业和截止。学校关了这个按钮，就用大纲和课件。', "Copy the token and paste it here. Read-only: files, assignments and due dates. If your school has turned this off, use the syllabus and files.")),
  cv && cv.course_id ? h('div', { class: 'note good' }, L(`已经连上 Canvas 课程 ${cv.name || cv.course_id}`, `Linked to Canvas course ${cv.name || cv.course_id}`) + (cv.synced_at ? L(`，上次同步 ${cv.synced_at.slice(5, 16).replace('T', ' ')}`, `, last sync ${cv.synced_at.slice(5, 16).replace('T', ' ')}`) : ''),
    h('button', { class: 'wzchip add', style: 'margin-left:8px', onclick: () => linkCanvas(cv.course_id, out) }, L('再同步一次', 'Sync again'))) : null,
  h('div', { class: 'linkrow' }, base), h('div', { class: 'linkrow' }, tok, h('button', { class: 'wzchip add', onclick: connect }, L('连接', 'Connect'))), out, list);
}
async function linkCanvas(id, out) {
  out.textContent = L('在同步课件和作业…（大的课要几分钟）', 'Syncing files and assignments… (a few minutes for big courses)');
  try {
    const r = await api(cpath(W.name, '/canvas'), jsonReq('POST', { course_id: id }));
    out.textContent = r.note || L('开始同步了', 'Sync started');
  } catch (e) { out.textContent = e.message; return; }
  const tick = async () => {
    await reloadCourse();
    const job = W.c && W.c.canvas_job;
    if (job && job.status === 'running') { out.textContent = job.stage || L('在同步…', 'Syncing…'); setTimeout(tick, 4000); return; }
    paintWizard();
  };
  setTimeout(tick, 3000);
}

// 第 3 步：核对每一节
function step3() {
  const c = W.c;
  const wrap = h('div', { class: 'wzstep3' }, h('h1', {}, L('核对每一节', 'Check the sessions')));
  if (!c) return wrap;
  const job = c.syllabus_job;
  const live = c.sessions || [], ddl = c.deadlines || [], marks = c.marks || { sessions: {}, deadlines: {} };
  const edited = marks.change && Object.keys(marks.sessions || {}).length + Object.keys(marks.deadlines || {}).length;
  wrap.append(h('p', { class: 'lead' }, job && job.status === 'running' ? h('span', {}, h('span', { class: 'spin' }), L('在读大纲，读完就填进来…', 'Reading the syllabus; the table fills in when it\'s done…'))
    : !live.length ? L('还没有节：加一节，或者在右边跟学习 Agent 说（「这门课周一周三上，一共 8 节」）。', 'No sessions yet: add one, or tell the study Agent on the right ("Mondays and Wednesdays, 8 sessions").')
      : edited && marks.change && marks.change.actor !== 'user' ? L(`按你说的改了，改过的地方标黄了。`, 'Changed as you asked; the changes are highlighted.')
        : L(`从大纲里读到 ${live.length} 节、${ddl.length} 个作业和考试。哪里不对直接改，或者跟右边说；看不清的地方它会问你。`,
          `${live.length} sessions and ${ddl.length} assignments and exams from the syllabus. Fix anything in place or tell the Agent on the right; it asks about unclear points.`)));
  if (job && job.status === 'running') { pollSyllabus(); }
  const qBySession = {};
  for (const q of c.questions || []) (qBySession[q.session || ''] ||= []).push(q);
  const table = h('div', { class: 'stable' }, h('div', { class: 'srow head' }, ...[L('节', 'No.'), L('日期', 'Date'), L('主题', 'Topic'), L('要读的', 'Readings'), L('作业和考试', 'Due')].map(t => h('span', {}, t))));
  for (const s of live) {
    const mk = (marks.sessions || {})[s.id] || [];
    const isNew = mk[0] === 'new';
    const hl = f => (isNew || mk.includes(f) ? ' hl' : '');
    const dues = ddl.filter(x => x.session === s.id);
    const qs2 = qBySession[s.id] || [];
    const row = h('div', { class: 'srow' + (qs2.length ? ' ask' : '') + (isNew ? ' new' : '') },
      h('span', { class: 'sn' }, 'S' + s.n, isNew ? h('i', { class: 'tag new' }, L('新', 'New')) : mk.length && !(mk.length === 1 && mk[0] === 'n') ? h('i', { class: 'tag' }, L('改了', 'Edited')) : null),
      editCell(dueCell(s), hl('date') || hl('time'), () => editSession(s, 'date')),
      editCell(s.topic, hl('topic'), () => editSession(s, 'topic'), true),
      h('span', { class: 'reads' + hl('readings') }, qs2.filter(q => q.field === 'readings').length ? questionBox(qs2.filter(q => q.field === 'readings')) : null,
        s.readings.length ? s.readings.map(r => h('span', { class: 'rd' + (r.required ? '' : ' optional') }, readingLabel(r) + (r.required ? '' : L('（选读）', ' (optional)')))) : h('span', { class: 'dim' }, '—'),
        h('button', { class: 'mini', onclick: () => editSession(s, 'readings') }, L('改', 'Edit'))),
      h('span', { class: 'dues' }, dues.length ? dues.map(x => h('span', { class: 'due' + (((marks.deadlines || {})[x.id] || []).length ? ' hl' : '') }, `${x.title} · ${dueLabel(x.due)}`)) : h('span', { class: 'dim' }, '—')));
    table.append(row);
    for (const q of qs2.filter(q => q.field !== 'readings')) table.append(h('div', { class: 'srow qrow' }, h('span', {}), questionBox([q])));
  }
  wrap.append(table);
  const loose = ddl.filter(x => !x.session);
  const other = (qBySession[''] || []);
  if (loose.length || other.length) {
    wrap.append(h('div', { class: 'lab', style: 'margin-top:14px' }, L('整门课的作业和考试', 'Course-wide assignments and exams')),
      h('div', { class: 'dlist' }, loose.map(x => h('div', { class: 'drow' + (((marks.deadlines || {})[x.id] || []).length ? ' hl' : '') }, h('b', {}, x.title), h('span', {}, dueLabel(x.due) + (x.weight ? ' · ' + x.weight : '')),
        h('button', { class: 'mini', onclick: () => editDeadline(x) }, L('改', 'Edit')))), other.length ? questionBox(other) : null));
  }
  wrap.append(h('div', { class: 'addrow' }, h('button', { class: 'wzchip add', onclick: addSession }, '+ ' + L('加一节', 'Add a session')),
    h('button', { class: 'wzchip add', onclick: () => editDeadline(null) }, '+ ' + L('加作业 / 考试', 'Add a deadline')),
    h('span', { class: 'note' }, L('点格子能直接改，也可以跟右边说。作业和考试会进「今天」和学习 Agent 的截止表。', 'Click a cell to edit, or tell the Agent on the right. Assignments and exams go into Today and the study Agent\'s deadline table.'))));
  const pending = (c.questions || []).length;
  setTimeout(() => footer(pending ? L(`还有 ${pending} 处要你定。`, `${pending} point${pending > 1 ? 's' : ''} still need you.`) : c.setup.confirmed ? L('核对过了，改了也会跟着更新。', 'Already confirmed; changes update everything.') : L('核对完才建，不会动你别的课。', "Nothing is created until you confirm; your other courses aren't touched."),
    { next: confirm3, nextLabel: c.setup.confirmed ? L('下一步', 'Next') : L('核对完了', 'Looks right'), disabled: !live.length }), 0);
  return wrap;
}
const dueCell = s => (s.date ? dayLabel(s.date) + (s.time ? ' ' + s.time : '') : L('没日期', 'No date'));
function editCell(text, cls, onclick, strong) { return h('button', { class: 'cell' + (cls || '') + (strong ? ' strong' : ''), onclick, title: L('点一下改', 'Click to edit') }, text || '—'); }
function questionBox(qs2) {
  return h('div', { class: 'qbox' }, qs2.map(q => h('div', { class: 'q' }, h('div', { class: 'qt' }, q.text),
    h('div', { class: 'chips' }, (q.options || []).map(o => h('button', { class: 'wzchip q', onclick: () => answer(q, o) }, o)),
      h('button', { class: 'wzchip q ghost', onclick: () => { const a = prompt(q.text); if (a != null) answer(q, a); } }, L('自己写', 'Other…'))))));
}
async function answer(q, a) {
  try { W.c = (await api(cpath(W.name, '/questions/' + encodeURIComponent(q.id)), jsonReq('POST', { answer: a }))).course; } catch (e) { if (e.message !== 'auth') alert(e.message); return; }
  paintWizard();
}
async function editSession(s, field) {
  let body = null;
  if (field === 'date') {
    const v = prompt(L('日期（YYYY-MM-DD，可以加时间 HH:MM）', 'Date (YYYY-MM-DD, optionally HH:MM)'), (s.date || '') + (s.time ? ' ' + s.time : ''));
    if (v == null) return;
    const m = /^(\d{4}-\d{2}-\d{2})?\s*(\d{1,2}:\d{2})?$/.exec(v.trim());
    if (!m) { alert(L('写成 2026-10-05 10:00', 'Write it as 2026-10-05 10:00')); return; }
    body = { date: m[1] || '', time: m[2] || '' };
  } else if (field === 'topic') {
    const v = prompt(L('主题', 'Topic'), s.topic);
    if (v == null || !v.trim()) return;
    body = { topic: v.trim() };
  } else {
    const v = prompt(L('要读的，一行一篇（前面加 ? 是选读）', 'Readings, one per line (prefix ? for optional)'), s.readings.map(r => (r.required ? '' : '?') + r.title).join('\n'));
    if (v == null) return;
    body = { readings: v.split('\n').map(x => x.trim()).filter(Boolean).map(x => (x.startsWith('?') ? { title: x.slice(1).trim(), required: false } : { title: x, required: true })) };
  }
  try { W.c = (await api(cpath(W.name, '/sessions/' + encodeURIComponent(s.id)), jsonReq('PATCH', body))).course; } catch (e) { if (e.message !== 'auth') alert(e.message); return; }
  paintWizard();
}
async function addSession() {
  const topic = prompt(L('这一节的主题', 'Topic of the session'));
  if (!topic || !topic.trim()) return;
  const date = prompt(L('哪天（YYYY-MM-DD，可以不填）', 'Date (YYYY-MM-DD, optional)'), '') || '';
  try { W.c = (await api(cpath(W.name, '/sessions'), jsonReq('POST', { topic: topic.trim(), date: date.trim() || null }))).course; } catch (e) { if (e.message !== 'auth') alert(e.message); return; }
  paintWizard();
}
async function editDeadline(x) {
  const title = prompt(L('什么作业 / 考试', 'What is due'), x ? x.title : '');
  if (!title || !title.trim()) return;
  const due = prompt(L('截止（YYYY-MM-DD HH:MM，不知道就空着）', 'Due (YYYY-MM-DD HH:MM, blank if unknown)'), x ? x.due || '' : '');
  if (due == null) return;
  const body = { title: title.trim(), due: due.trim() || null, kind: x ? x.kind : (/exam|考试/i.test(title) ? 'exam' : 'assignment') };
  try {
    W.c = (await api(x ? cpath(W.name, '/deadlines/' + encodeURIComponent(x.id)) : cpath(W.name, '/deadlines'), jsonReq(x ? 'PATCH' : 'POST', body))).course;
  } catch (e) { if (e.message !== 'auth') alert(e.message); return; }
  paintWizard();
}
async function confirm3() {
  if (!W.c.setup.confirmed) {
    try { W.c = (await api(cpath(W.name, '/confirm'), { method: 'POST' })).course; } catch (e) { if (e.message !== 'auth') alert(e.message); return; }
  }
  goStep(4);
}

// 第 4 步：放材料，查齐
function step4() {
  const c = W.c;
  const wrap = h('div', { class: 'wzstep4' }, h('h1', {}, L('放材料，查齐', 'Add materials')),
    h('p', { class: 'lead' }, L('每一节要的东西齐了才生成。缺了先告诉你缺什么、去哪找。', "A session is generated once its materials are in. If something's missing you'll see what and where to find it.")));
  if (!c) return wrap;
  const cnt = c.counts || {};
  const later = (cnt.later || 0) + (cnt.empty || 0), miss = (cnt.missing || 0) + (cnt.noslides || 0);
  wrap.append(h('div', { class: 'chips' }, h('span', { class: 'stat good' }, L(`${cnt.ready || 0} 节齐了`, `${cnt.ready || 0} ready`)),
    miss ? h('span', { class: 'stat warn' }, L(`${miss} 节缺材料`, `${miss} missing materials`)) : null, later ? h('span', { class: 'stat' }, L(`${later} 节还没上课`, `${later} not taught yet`)) : null));
  const zone = dropZone(L('把课件拖到这里（文件或 zip），按文件名自动归到每一节', 'Drop files or a zip here; they are filed to each session by name'), files => uploadFiles(files), true);
  const box = h('div', { class: 'dropbox' }, zone);
  if (W.lastUpload) {
    box.append(h('div', { class: 'ulist' }, (W.lastUpload.files || []).map(f => h('div', { class: 'urow' }, h('span', { class: 'un' }, f.name),
      f.status === 'filed' ? h('span', { class: 'good' }, L(`归到 S${f.n}`, `→ S${f.n}`)) : f.status === 'info' ? h('span', { class: 'good' }, L('整门课的资料', 'Course info'))
        : h('span', { class: 'warn' }, f.reason)))));
  }
  wrap.append(box);
  if ((c.incoming || []).length) {
    wrap.append(h('div', { class: 'lab' }, L('等你定归哪一节', 'Waiting for you to file')), h('div', { class: 'ilist' }, c.incoming.map(f => {
      const hint = (W.lastUpload?.files || []).find(x => x.file === f);
      const sel = h('select', { 'aria-label': L('归到哪一节', 'File to session') }, h('option', { value: '' }, L('归到…', 'File to…')),
        (hint?.options?.length ? hint.options.map(id => c.sessions.find(s => s.id === id)).filter(Boolean) : []).map(s => h('option', { value: s.id }, `S${s.n} ${s.topic}`)),
        hint?.options?.length ? h('option', { disabled: true }, '──') : null,
        c.sessions.map(s => h('option', { value: s.id }, `S${s.n} ${s.topic}`)), h('option', { value: 'course' }, L('整门课的资料', 'Course info')));
      sel.addEventListener('change', () => sel.value && assign(f, sel.value));
      return h('div', { class: 'irow' }, h('span', { class: 'un' }, f.split('/').pop()), hint?.reason ? h('small', { class: 'dim' }, hint.reason) : null, sel,
        h('button', { class: 'mini', onclick: () => dropIncoming(f) }, L('删掉', 'Delete')));
    })));
  }
  const list = h('div', { class: 'clist' });
  let laterRun = [];
  const flush = () => {
    if (!laterRun.length) return;
    const a = laterRun[0], b = laterRun[laterRun.length - 1];
    list.append(h('div', { class: 'crow' }, h('b', {}, a === b ? `S${a.n} ${a.topic}` : `S${a.n}–S${b.n}`),
      h('span', { class: 'dim' }, L('还没上课，材料到了再放。老师一般课前传课件。', 'Not taught yet; add materials when they come. Slides usually arrive before class.')), statusChip('later')));
    laterRun = [];
  };
  for (const s of c.sessions) {
    const ch = s.check;
    if (ch.status === 'later' && !ch.slides.length && !ch.have) { laterRun.push(s); continue; }
    flush();
    const row = h('div', { class: 'crow' }, h('b', {}, `S${s.n} ${s.topic}`),
      h('span', {}, L(`课件 ${ch.slides.length ? '✓' : '—'} · 阅读 ${ch.have}/${ch.total} · 录播 ${ch.captions.length ? '✓' : '—'}`, `Files ${ch.slides.length ? '✓' : '—'} · readings ${ch.have}/${ch.total} · captions ${ch.captions.length ? '✓' : '—'}`)),
      statusChip(ch.status, ch));
    list.append(row);
    if (ch.status === 'missing' || ch.status === 'noslides' || ch.skipped) {
      const detail = h('div', { class: 'cmiss' });
      for (const r of ch.readings.filter(r => r.required && r.state !== 'have')) {
        const how = h('div', { class: 'how', hidden: true }, L('课程网站或图书馆的阅读清单（Reading List）一般直接有链接；论文可以在 Google Scholar 搜标题找 PDF。也可以让右边去找。实在找不到先跳过，学习页会写明这篇没读到。',
          'The course site or library reading list usually links it; for papers, search the title on Google Scholar for a PDF. You can also ask the Agent on the right. If you really can\'t find it, skip it and the notes will say it wasn\'t read.'));
        detail.append(h('div', { class: 'mr' }, h('span', {}, (r.state === 'skipped' ? L('跳过了：', 'Skipped: ') : L('缺：', 'Missing: ')) + readingLabel(r)),
          h('div', { class: 'chips' }, h('button', { class: 'wzchip', onclick: () => { how.hidden = !how.hidden; } }, L('怎么找', 'How to find it')),
            pickButton(L('传文件', 'Upload'), files => uploadFiles(files, s.id)),
            h('button', { class: 'wzchip', onclick: () => skipReading(s, r, r.state !== 'skipped') }, r.state === 'skipped' ? L('撤销跳过', 'Undo skip') : L('先跳过', 'Skip for now'))), how));
      }
      if (ch.status === 'noslides') detail.append(h('div', { class: 'mr' }, h('span', {}, L('还没有这一节的课件', 'No slides for this session yet')), pickButton(L('传文件', 'Upload'), files => uploadFiles(files, s.id), true)));
      if (detail.childNodes.length) list.append(detail);
    }
  }
  flush();
  wrap.append(list, h('div', { class: 'note' }, L('录播：有字幕文件（.vtt、.srt）就传上来，学习页会带时间点；学校不让下载就不用管。', "Recordings: upload caption files (.vtt, .srt) if you have them and notes get timestamps; skip it if the school doesn't allow downloads.")));
  const skipped = c.sessions.filter(s => s.check.skipped).length;
  setTimeout(() => footer(skipped ? L(`${skipped} 节跳过了阅读，学习页会写明没读到。`, `${skipped} session(s) skip a reading; the notes will say so.`) : L('缺的可以补，也可以明着跳过。', 'Fill the gaps, or skip them openly.'), { next: () => goStep(5) }), 0);
  return wrap;
}
function statusChip(st, ch) {
  const m = { ready: [L('齐了', 'Ready'), 'good'], missing: [ch && ch.missing.length ? L(`缺 ${ch.missing.length} 篇`, `${ch.missing.length} missing`) : L('缺阅读', 'Readings missing'), 'warn'],
    noslides: [L('缺课件', 'No slides'), 'warn'], later: [L('还没上', 'Not yet'), ''], empty: [L('还没有材料', 'Nothing yet'), ''] }[st] || [st, ''];
  if (st === 'ready' && ch && ch.skipped) m[0] = L(`跳过 ${ch.skipped} 篇`, `${ch.skipped} skipped`);
  return h('span', { class: 'schip ' + m[1] }, m[0]);
}
async function assign(file, session) {
  try { W.c = (await api(cpath(W.name, '/files/assign'), jsonReq('POST', { file, session }))).course; } catch (e) { if (e.message !== 'auth') alert(e.message); return; }
  paintWizard();
}
async function dropIncoming(file) {
  if (!confirm(L('删掉这个文件？', 'Delete this file?'))) return;
  try { await api(cpath(W.name, '/files?' + qs({ file })), { method: 'DELETE' }); } catch (e) { if (e.message !== 'auth') alert(e.message); return; }
  await reloadCourse();
  paintWizard();
}
async function skipReading(s, r, skip) {
  try { W.c = (await api(cpath(W.name, `/sessions/${encodeURIComponent(s.id)}/readings/${encodeURIComponent(r.id)}`), jsonReq('PATCH', { skip }))).course; } catch (e) { if (e.message !== 'auth') alert(e.message); return; }
  paintWizard();
}

// 第 5 步：生成
function step5() {
  const c = W.c;
  const wrap = h('div', { class: 'wzstep5' }, h('h1', {}, L('生成', 'Generate')), h('p', { class: 'lead' }, L('先生成材料齐了的节，其他的等材料到了再生成。', 'Sessions with complete materials first; the rest when their materials arrive.')));
  if (!c) return wrap;
  const jobs = Object.entries(c.sessions.reduce((a, s) => (s.job ? { ...a, [s.id]: s.job } : a), {}));
  const live = jobs.some(([, j]) => j.status === 'queued' || j.status === 'running');
  if (!W.gen) W.gen = { pick: new Set(c.sessions.filter(s => s.check.status === 'ready' && !s.has_page).map(s => s.id)), cards: true, quiz: true, video: false };
  const g = W.gen;
  if (live || W.showJobs) {
    wrap.append(h('div', { class: 'glist' }, c.sessions.filter(s => s.job).map(s => jobRow(s))));
    wrap.append(h('div', { class: 'note' }, c.gen_note || ''));
    if (live) {
      W.genLive = true;
      clearTimeout(W.poll);
      W.poll = setTimeout(async () => { if (!W.open || W.step !== 5) return; await reloadCourse(); paintWizard(); }, 4000);
    } else if (W.genLive) { W.genLive = false; loadAgentChat(); }  // 写完了：右边对话里那行「写好了」接上
    setTimeout(() => {
      footer('', { prev: !live, next: live ? null : () => { W.showJobs = false; paintWizard(); }, nextLabel: L('再生成几节', 'Generate more') });
      $('#wzFoot').append(h('button', { class: 'pill', onclick: () => { closeWizard(); } }, L('去学习台看', 'Open the desk')));
    }, 0);
    return wrap;
  }
  const box = h('div', { class: 'gbox' }, h('div', { class: 'gh' }, L('生成哪几节', 'Which sessions')));
  let laterFrom = null;
  for (const s of c.sessions) {
    const st = s.check.status;
    if (st === 'later' || st === 'empty') { laterFrom = laterFrom || s; continue; }
    const ok = st === 'ready';
    const on = ok && g.pick.has(s.id);
    box.append(h('button', { class: 'grow2' + (ok ? '' : ' off'), 'aria-pressed': on ? 'true' : 'false', disabled: !ok, onclick: () => { g.pick.has(s.id) ? g.pick.delete(s.id) : g.pick.add(s.id); paintWizard(); } },
      h('span', { class: 'cb' + (on ? ' on' : '') }, on ? '✓' : ''), h('b', {}, `S${s.n} ${s.topic}`),
      h('span', { class: ok ? 'good' : 'warn' }, s.has_page ? L('已经有学习页（再选会重写）', 'Has notes (picking rewrites them)') : ok ? (s.check.skipped ? L(`跳过了 ${s.check.skipped} 篇，学习页会写明`, `${s.check.skipped} skipped; the notes will say so`) : L('齐了', 'Ready'))
        : L(`缺 ${s.check.missing.length || 1} 样：回上一步补上或先跳过`, `${s.check.missing.length || 1} missing: add it in the last step or skip it`))));
  }
  if (laterFrom) box.append(h('div', { class: 'glater' }, L(`S${laterFrom.n} 以后：材料到了再生成`, `S${laterFrom.n} onwards: when their materials arrive`)));
  wrap.append(box);
  const opt = (on, label, sub, onclick, disabled) => h('button', { class: 'grow2', 'aria-pressed': on ? 'true' : 'false', disabled, onclick },
    h('span', { class: 'cb' + (on ? ' on' : '') + (disabled ? ' fixed' : '') }, on ? '✓' : ''), h('span', {}, h('b', {}, label), sub ? h('small', {}, sub) : null));
  wrap.append(h('div', { class: 'gbox' }, h('div', { class: 'gh' }, L('每一节生成什么', 'For each session')),
    opt(true, L('学习页和学习路线', 'Study notes and path'), L('每一节先有这个；路线 5–8 步，能打勾', 'Always first; a 5–8 step path you can tick off'), null, true),
    opt(g.cards, L('闪卡和小测', 'Flashcards and quiz'), '', () => { g.cards = g.quiz = !g.cards; paintWizard(); }),
    S.tree?.video ? opt(g.video, L('讲解视频', 'Explainer video'), L('每一节挑一个概念做成短视频', 'One short video on a key concept'), () => { g.video = !g.video; paintWizard(); }) : null));
  const remind = !!c.remind_ready;
  wrap.append(h('button', { class: 'remind', 'aria-pressed': remind ? 'true' : 'false', onclick: async () => { try { W.c = (await api(cpath(W.name), jsonReq('PATCH', { remind_ready: !remind }))).course; } catch { return; } paintWizard(); } },
    h('span', {}, h('b', {}, L('新的一节材料齐了，提醒我生成', 'Remind me when a new session is ready')), h('small', {}, S.tree?.notify_ready ? L('推送到手机，点一下就开始', 'A push to your phone; tap to start') : L('推送要在服务器上打开（server.json 的 study.notify_ready）', 'Needs pushes turned on on the server (study.notify_ready in server.json)'))),
    h('span', { class: 'sw' + (remind ? ' on' : '') }, h('i', {}))));
  const n = [...g.pick].filter(id => c.sessions.find(s => s.id === id && s.check.status === 'ready')).length;
  setTimeout(() => footer(L('用你的 claw 的模型写，写好一节在学习 Agent 的对话里说一声。', "Written by your claw's model; the study Agent's chat gets a line when each is done."),
    { next: wzStartGen, nextLabel: n ? L(`开始生成 ${n} 节`, `Generate ${n} session${n > 1 ? 's' : ''}`) : L('先选一节', 'Pick a session'), disabled: !n }), 0);
  return wrap;
}
function jobRow(s) {
  const j = s.job, st = j.steps || {};
  const lab = { page: L('学习页', 'Notes'), path: L('学习路线', 'Path'), cards: L('闪卡', 'Cards'), quiz: L('小测', 'Quiz'), video: L('视频', 'Video') };
  const word = { queued: L('排队中', 'queued'), running: L('在写', 'writing'), done: L('写好了', 'done'), error: L('没成', 'failed'), skipped: L('跳过', 'skipped') };
  return h('div', { class: 'grow3' }, h('b', {}, `S${s.n} ${s.topic}`),
    j.status === 'interrupted' ? h('span', { class: 'warn' }, L('服务器重启过，没写完：再点一次生成', 'Interrupted by a server restart: generate again')) : null,
    h('div', { class: 'steps' }, Object.entries(st).map(([k, v]) => h('span', { class: v === 'done' ? 'good' : v === 'error' ? 'bad' : v === 'running' ? 'run' : 'dim' },
      v === 'running' ? h('span', { class: 'spin' }) : null, `${lab[k] || k} · ${word[v] || v}` + (k === 'path' && v === 'done' && s.progress ? L(` · ${s.progress.total} 步`, ` · ${s.progress.total} steps`) : '')))),
    j.error ? h('small', { class: 'bad' }, j.error) : null);
}
async function wzStartGen() {
  const g = W.gen;
  const ids = [...g.pick];
  const rewrite = ids.some(id => W.c.sessions.find(s => s.id === id)?.has_page);
  try {
    const r = await api(cpath(W.name, '/generate'), jsonReq('POST', { sessions: ids, cards: g.cards, quiz: g.quiz, video: g.video, rewrite }));
    if (r.ok === false) { alert(L('这几节材料还没齐：', 'These sessions still miss materials: ') + (r.blocked || []).map(b => 'S' + b.n).join(', ')); return; }
  } catch (e) { if (e.message !== 'auth') alert(e.message); return; }
  W.showJobs = true;
  await reloadCourse();
  paintWizard();
}

// —— 右边：学习 Agent 的对话 ——
const TIPS = () => ({
  1: L('左边可以一项项填，也可以直接跟我说，比如「这学期的行为经济学，期末闭卷，微观学过，前景理论没学过」，我帮你填好。', 'Fill in the left, or just tell me, e.g. "Behavioural economics this term, closed-book final, I know micro but not prospect theory", and I\'ll fill it in.'),
  2: null,
  3: L('表里有不对的直接跟我说，我改好标黄；改错了能撤销。', "Tell me what's wrong in the table; I'll fix it and highlight the change. Anything I get wrong can be undone."),
  4: L('缺的阅读我可以去找公开的版本，找不到你再决定跳不跳过。', "I can look for public copies of missing readings; if I can't find one, you decide whether to skip it."),
  5: L('生成好一节我就在这里说一声。学习页想换写法，比如更短、多举例、先讲直觉，直接跟我说，以后每一节都照着写。', "I'll post a line here when each session is ready. Want the notes written differently (shorter, more examples, intuition first)? Tell me and every session follows it."),
});
function paintTip() {
  const box = $('#wzTip');
  if (!box) return;
  box.innerHTML = '';
  if (W.step === 2) {
    const P = PLAT(), plat = W.draft?.platform;
    box.append(h('div', { class: 'tip' }, plat ? P[plat].tip : L('你们学校的课程网站用哪个？我照你们的界面带你找。', "Which course site does your school use? I'll guide you through its screens.")),
      h('div', { class: 'chips' }, Object.entries(P).map(([k, v]) => h('button', { class: 'wzchip q', onclick: () => setPlatform(k) }, v.label))),
      h('div', { class: 'note' }, icon('image'), ' ' + L('找不到就截个图发我，我告诉你点哪里。', "Can't find it? Send me a screenshot and I'll show you where to click.")));
  } else if (TIPS()[W.step]) box.append(h('div', { class: 'tip' }, TIPS()[W.step]));
}
async function loadAgentChat() {
  const box = $('#wzMsgs');
  const my = ++W.chatSeq;
  box.innerHTML = '';
  paintTip();
  $('#wzAgentName').textContent = L('学习 Agent', 'Study Agent');
  if (!W.agent) {
    box.append(h('div', { class: 'note' }, L('还没有学习 Agent：在 app 的 Agents 里新建一个，从例子里选「学习」。建好以后这里就能跟它说。', 'No study Agent yet: in the app, create an Agent and pick the "Study" example. Then you can talk to it here.')));
    $('#wzInput').disabled = true;
    return;
  }
  $('#wzInput').disabled = false;
  try {
    const [hist, cards] = await Promise.all([api('/api/chat/history?' + qs({ thread: W.agent, limit: 40 })), api('/api/chat/cards?' + qs({ thread: W.agent })).catch(() => ({ cards: [] }))]);
    if (my !== W.chatSeq) return;
    const byMsg = {};
    for (const c of (cards.cards || []).filter(c => c.kind === 'course')) (byMsg['db' + c.messageId] ||= []).push(c);
    for (const m of (hist.messages || []).slice(-20)) {
      box.append(wzMsg(m.role, m.text, m.time));
      for (const c of byMsg[m.id] || []) box.append(courseCard(c));
    }
    if (hist.inFlight) wzReattach(my);
    box.scrollTop = box.scrollHeight;
  } catch (e) { if (e.message !== 'auth') box.append(h('div', { class: 'err' }, e.message)); }
}
function wzMsg(role, text, time) {
  if (role === 'user') return h('div', { class: 'msg user' }, h('div', {}, h('div', { class: 'bub' }, text), time ? h('div', { class: 'time' }, time) : null));
  if (role === 'auto') return h('div', { class: 'msg auto' }, text);
  return h('div', { class: 'msg bot' }, h('div', { class: 'who' }, L('学习 Agent', 'Study Agent')), mdEl(text || '', 'bub'), time ? h('div', { class: 'time' }, time) : null);
}
function courseCard(c) {
  const el = h('div', { class: 'ccard' + (c.status === 'undone' ? ' undone' : '') });
  const paint = () => {
    el.innerHTML = '';
    el.className = 'ccard' + (c.status === 'undone' ? ' undone' : '');
    el.append(h('div', { class: 'cct' }, c.status === 'undone' ? L('撤销了', 'Undone') + ' · ' + c.title : c.title),
      (c.lines || []).length ? h('div', { class: 'ccl' }, c.lines.map(l => h('div', {}, '· ' + l))) : null,
      h('div', { class: 'chips' }, h('button', { class: 'wzchip', onclick: async () => {
        try { const r = await api(cpath(c.course, `/undo/${c.changeId}?redo=${c.status === 'undone' ? 1 : 0}`), { method: 'POST' }); Object.assign(c, r.card); } catch (e) { if (e.message !== 'auth') alert(e.message); return; }
        paint();
        if (W.name === c.course) { await reloadCourse(); paintWizard(); }
      } }, c.status === 'undone' ? L('重做', 'Redo') : L('撤销', 'Undo'))));
  };
  paint();
  return el;
}
function wzStream(my) {
  const box = $('#wzMsgs');
  const bot = h('div', { class: 'msg bot' }, h('div', { class: 'who' }, L('学习 Agent', 'Study Agent')), h('div', { class: 'md bub' }, h('span', { class: 'spin' })));
  box.append(bot);
  box.scrollTop = box.scrollHeight;
  let text = '', raf = 0;
  const paint = () => { raf = 0; if (my !== W.chatSeq) return; bot.querySelector('.bub').innerHTML = renderMD(text); box.scrollTop = box.scrollHeight; };
  return async (ev, d) => {
    if (ev === 'delta') { text += d.text || ''; if (!raf) raf = requestAnimationFrame(paint); }
    if (ev === 'card' && d.kind === 'course') { bot.after(courseCard(d)); await reloadCourse(); paintWizard(); }
    if (ev === 'done') { text = d.text || text; paint(); if (d.status === 'error') bot.classList.add('err'); await reloadCourse(); paintWizard(); }
  };
}
async function wzReattach(my) {
  W.busy = true;
  try { const r = await fetch('/api/chat/stream?' + qs({ thread: W.agent }), { headers: hdrs() }); if (r.status === 200) await readSSE(r, wzStream(my)); } catch { /* 刷新能看到 */ }
  W.busy = false;
}
async function askAgent(text) {
  text = (text || '').trim();
  if ((!text && !W.pending.length) || W.busy || !W.agent) return;
  const my = W.chatSeq;
  const box = $('#wzMsgs');
  const files = W.pending;
  W.pending = [];
  paintPending();
  box.append(wzMsg('user', text || L('（截图）', '(screenshot)'), ''));
  $('#wzInput').value = '';
  W.busy = true;
  try {
    let ids = [];
    if (files.length) {
      const fd = new FormData();
      fd.append('thread', W.agent);
      for (const f of files) fd.append('files', f);
      const up = await fetch('/api/chat/upload', { method: 'POST', headers: hdrs(), body: fd });
      const uj = await up.json();
      if (!up.ok) throw new Error(uj.detail || uj.error || 'HTTP ' + up.status);
      ids = (uj.files || []).map(f => f.id);
    }
    const r = await fetch('/api/chat/send', { method: 'POST', headers: hdrs({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ thread: W.agent, text, attachments: ids, study: (W.name || '') + '|' + W.step }) });
    if (r.status === 401) { showAuth(); throw new Error('auth'); }
    if (!r.ok) { let j = null; try { j = await r.json(); } catch { /* */ } throw new Error((j && (j.detail || j.error)) || 'HTTP ' + r.status); }
    await readSSE(r, wzStream(my));
  } catch (e) { if (e.message !== 'auth') box.append(h('div', { class: 'msg bot err' }, h('div', { class: 'bub' }, L('没发出去：', "Couldn't send: ") + e.message))); }
  W.busy = false;
}
function paintPending() {
  const p = $('#wzPending');
  p.innerHTML = '';
  for (const f of W.pending) p.append(h('span', { class: 'wzchip' }, icon('image'), ' ' + short(f.name, 18), h('button', { class: 'x', onclick: () => { W.pending = W.pending.filter(x => x !== f); paintPending(); } }, '✕')));
}
function wireWizard() {
  $('#wzCancel').onclick = closeWizard;
  const inp = $('#wzInput');
  inp.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); askAgent(inp.value); } });
  inp.addEventListener('paste', e => {
    const imgs = [...(e.clipboardData?.files || [])].filter(f => f.type.startsWith('image/'));
    if (imgs.length) { e.preventDefault(); W.pending.push(...imgs); paintPending(); }
  });
  $('#wzSend').onclick = () => askAgent(inp.value);
  const pick = $('#wzPick');
  pick.addEventListener('change', () => { W.pending.push(...pick.files); pick.value = ''; paintPending(); });
  $('#wzImg').onclick = () => pick.click();
}
