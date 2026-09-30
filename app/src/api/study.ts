// 学习台（server/study.py + courses.py + studyapp.py，2026-09-30）：学习 Agent 看板上的学习台卡、原生学习屏、手机上加一门课。
import { Platform } from 'react-native';
import { fetch as expoFetch } from 'expo/fetch';
import type { PendingFile } from '../data/types';
import { authHeaders, fileUrl, getBase, request } from './base';
import { formFile, readSse, xhrUpload } from './client';

const q = (o: Record<string, string | number | null | undefined>) =>
  Object.entries(o).filter(([, v]) => v != null && v !== '').map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join('&');
const cpath = (course: string, rest = '') => `/api/study/courses/${encodeURIComponent(course)}${rest}`;

// —— 学习台卡 ——

export interface StudyHomeCourse { name: string; title: string; code: string; profile: boolean; done: number; taught: number; total: number }
export interface StudyHome {
  agent: string | null;
  configured: boolean;
  courses: StudyHomeCourse[];
  /** 没复习的：几条、最早那条在哪门课哪一节、从哪来（播客的费曼、自测第几题…） */
  review: { count: number; course: string; code: string; page: string | null; n: number | null; source: string; kind: string } | null;
  /** 接着学：最近打过勾、还没走完的那一节 */
  next: { course: string; courseTitle: string; code: string; page: string; n: number | null; title: string; step: number | null; steps: number; stepTitle: string | null; minutes: number | null } | null;
  deadlines: { due: string; course: string | null; code: string | null; title: string; url: string | null; courseId: string | null; sessionId: string | null; kind: string | null }[];
}
export const home = () => request<StudyHome & { ok: boolean }>('/api/study/home');

// —— 一门课每一节 ——

export type SessionStatus = 'done' | 'doing' | 'todo' | 'ready' | 'missing' | 'later' | 'empty' | 'info';
export interface OutlineSession { id: string | null; n: number | null; title: string; date: string | null; page: string | null; status: SessionStatus; progress: { done: number; total: number } | null; missing: number }
export interface Outline { name: string; title: string; code: string; profile: boolean; sessions: OutlineSession[]; done: number; taught: number; total: number }
export const outline = (course: string) => request<Outline & { ok: boolean }>(`/api/study/outline?${q({ course })}`);

// —— 一节 ——

export interface StudyFile { path: string; name: string; size: number; kind: string }
export interface StudyReading { id?: string; title: string; kind?: string; required: boolean; file: string | null; status: string; url?: string | null; instructions?: string | null; size?: number }
export interface StudyRec { id: string; title: string; viewer_url: string | null; has_captions: boolean; duration: number | null }
export interface StudyVideo { path: string; name: string; session: number | null; size: number }
export interface RouteRef { type: 'section' | 'file' | 'video' | 'recording' | 'cards' | 'quiz'; id?: string; label?: string; path?: string; page?: number; t?: number; url?: string | null }
export interface RouteStep { title: string; minutes: number; do: string; refs: RouteRef[] }
export interface Route { kind: 'path'; title: string; generated: string; phase?: 'pre' | 'post'; summary?: string; total_minutes?: number; items: RouteStep[] }
export interface ReviewItem { id: string; page: string | null; text: string; kind: string; source: string; from?: { title?: string } | null; created_at: string; done_at: string | null }
export interface StudyUnit {
  course: string; page: string; title: string; session: number | null; meta: Record<string, unknown>;
  files: StudyFile[]; readings: StudyReading[]; recordings: StudyRec[]; videos: StudyVideo[]; missing: { title: string }[];
  route: Route | null; done: number[]; generated: { path: boolean; cards: boolean; quiz: boolean }; review: ReviewItem[]; thread: string;
}
export const unit = (course: string, page: string) => request<StudyUnit & { ok: boolean }>(`/api/study/unit?${q({ course, page })}`);
export const pageMarkdown = (course: string, page: string) => request<{ markdown: string }>(`/api/study/page?${q({ course, path: page })}`).then((j) => j.markdown);
export const setStep = (course: string, page: string, step: number, done: boolean) =>
  request<{ done: number[]; total: number }>('/api/study/progress', { method: 'POST', body: { course, page, step, done } });

export interface Card { q: string; a: string; tag?: string; ref?: string }
export interface Question { q: string; options: string[]; answer: number; explain?: string; ref?: string }
export interface Generated<T> { status: 'none' | 'running' | 'done' | 'error'; error?: string | null; data: { items: T[]; generated?: string } | null }
export const generated = <T>(course: string, page: string, kind: 'cards' | 'quiz') =>
  request<Generated<T> & { ok: boolean }>(`/api/study/generated?${q({ course, page, kind })}`);
export const generate = (course: string, page: string, kind: 'cards' | 'quiz' | 'path', force = false) =>
  request<{ ok: boolean; status?: string; missing?: { title: string }[] }>('/api/study/generate', { method: 'POST', body: { course, page, kind, force } });

export const reviewList = (course: string, page?: string) => request<{ items: ReviewItem[] }>(`/api/study/review?${q({ course, page })}`).then((j) => j.items);
export const reviewDone = (course: string, id: string, done = true) => request<{ item: ReviewItem }>('/api/study/review/done', { method: 'POST', body: { course, id, done } });
export const reviewAdd = (course: string, page: string | null, items: { text: string; kind: string; source: string }[]) =>
  request<{ added: number }>('/api/study/review/add', { method: 'POST', body: { course, page, items } });

/** 自测：写的答案、看没看、自己判的（服务器存，和电脑上的学习台共用） */
export interface SelfRec { a?: string; open?: boolean; g?: 'ok' | 'close' | 'miss'; rv?: number }
export interface SelfState { n?: number; v: Record<string, SelfRec> }
export const selfGet = (course: string, page: string) => request<{ state: SelfState | null }>(`/api/study/self?${q({ course, page })}`).then((j) => j.state);
export const selfPut = (course: string, page: string, state: SelfState) => request('/api/study/self', { method: 'PUT', body: { course, page, state } });

/** 课件在 app 里看：和对话附件同一个预览页（FilePreview），地址指到学习台的文件。 */
export const fileLink = (course: string, path: string, where: 'materials' | 'pages' = 'materials') => fileUrl(`/api/study/file?${q({ course, path, where })}`);

// —— 问这一节（SSE，和电脑上的「问这一节」同一条路：每天第一句带上这一节的材料）——

export async function history(course: string, page: string) {
  return request<{ messages: { id: string; role: string; text: string; time: string }[]; thread: string; inFlight?: unknown }>(`/api/study/history?${q({ course, page })}`);
}
export async function ask(course: string, page: string, text: string, onDelta: (partial: string) => void, opts?: { step?: number | null; quote?: string | null }): Promise<string> {
  const r = await expoFetch(`${getBase()}/api/study/ask`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream', ...authHeaders() },
    body: JSON.stringify({ course, page, text, step: opts?.step ?? null, quote: opts?.quote ?? null }),
  });
  if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.detail ?? j.error ?? `HTTP ${r.status}`); }
  let text2 = '';
  let fail = '';
  await readSse(r.body as unknown as ReadableStream<Uint8Array>, (ev, d) => {
    if (ev === 'delta') { text2 += d.text || ''; onDelta(text2); }
    if (ev === 'text') { text2 = d.text || ''; onDelta(text2); }
    if (ev === 'done') { text2 = d.text || text2; if (d.status === 'error') fail = d.error || 'error'; onDelta(text2); }
  });
  if (fail) throw new Error(fail);
  return text2;
}

// —— 电脑上打开 ——

export const loginLink = (server: string) => request<{ url: string; expires: string }>('/api/study/login-link', { method: 'POST', body: { server } });

// —— 加一门课（手机上的几步；电脑上是 /study 的五步向导）——

export interface CourseReading { id: string; title: string; required: boolean; file: string | null; skip: boolean; note: string; kind: string; chapter: string | number | null }
export interface CourseSession {
  id: string; n: number; date: string | null; time: string; week: number | null; topic: string; kind: string; readings: CourseReading[]; folder: string | null; page: string | null;
  has_page?: boolean; check: { status: 'ready' | 'missing' | 'noslides' | 'later' | 'empty'; slides: string[]; captions: string[]; have: number; total: number; missing: { id: string; title: string }[]; skipped: number;
    readings: (CourseReading & { state: 'have' | 'skipped' | 'missing' })[] };
  job: { status: string; steps?: Record<string, string>; error?: string | null } | null; progress: { done: number; total: number } | null;
}
export interface CourseDeadline { id: string; title: string; due: string | null; kind: string; session: string | null; done: boolean; weight: string; url?: string | null }
export interface Course {
  name: string; title: string; code: string; term: string; exam: string[]; learn: string[]; notes: string; style: string; platform: string;
  have: Record<string, boolean>; extras: { key: string; label: string }[]; syllabus: { file?: string | null; url?: string | null; read_at?: string | null; summary?: string | null } | null;
  canvas: { base?: string; course_id?: number; name?: string | null; synced_at?: string } | null; canvas_job?: { status: string; stage?: string; error?: string } | null;
  sessions: CourseSession[]; deadlines: CourseDeadline[]; questions: { id: string; session: string | null; field: string; text: string; options: string[] }[];
  setup: { step: number; confirmed: boolean }; remind_ready: boolean; marks: { sessions: Record<string, string[]>; deadlines: Record<string, string[]>; change: unknown };
  incoming: string[]; counts: Record<string, number>; syllabus_job: { status: string; error?: string; summary?: string } | null; study_agent: string | null; gen_note?: string;
}
export const courses = () => request<{ courses: { name: string; title: string; code: string; profile: boolean }[]; folders: string[]; agent: string | null }>('/api/study/courses');
export const course = (name: string) => request<{ course: Course }>(cpath(name)).then((j) => j.course);
export const createCourse = (b: { name: string; title?: string; term?: string; exam?: string[]; learn?: string[]; notes?: string; platform?: string }) =>
  request<{ name: string; course: Course }>('/api/study/courses', { method: 'POST', body: b });
export const patchCourse = (name: string, b: Record<string, unknown>) => request<{ course: Course }>(cpath(name), { method: 'PATCH', body: b }).then((j) => j.course);
export const answer = (name: string, qid: string, a: string) => request<{ course: Course }>(cpath(name, `/questions/${encodeURIComponent(qid)}`), { method: 'POST', body: { answer: a } }).then((j) => j.course);
export const confirm = (name: string) => request<{ course: Course }>(cpath(name, '/confirm'), { method: 'POST' }).then((j) => j.course);
export const skipReading = (name: string, sid: string, rid: string, skip: boolean) =>
  request<{ course: Course }>(cpath(name, `/sessions/${encodeURIComponent(sid)}/readings/${encodeURIComponent(rid)}`), { method: 'PATCH', body: { skip } }).then((j) => j.course);
export const assign = (name: string, file: string, session: string) => request<{ course: Course }>(cpath(name, '/files/assign'), { method: 'POST', body: { file, session } }).then((j) => j.course);
export const generateSessions = (name: string, sessions: string[], opts: { cards: boolean; quiz: boolean }) =>
  request<{ ok: boolean; queued?: string[]; blocked?: { n: number }[]; note?: string }>(cpath(name, '/generate'), { method: 'POST', body: { sessions, ...opts } });
export const undoChange = (name: string, change: number, redo: boolean) =>
  request<{ card: { status: 'done' | 'undone' } }>(cpath(name, `/undo/${change}?redo=${redo ? 1 : 0}`), { method: 'POST' });
export const syllabusFrom = (name: string, b: { url?: string; upload?: string; text?: string }) => request(cpath(name, '/syllabus/from'), { method: 'POST', body: b });
/** Canvas 个人令牌（server/canvasapi.py）：连上 → 在读的课；把这门课和 Canvas 上的一门对上 → 后台同步课件和作业。 */
export const canvasConnect = (base: string, token: string) =>
  request<{ user: string | null; courses: { id: number; name: string; code?: string | null; term?: string | null }[] }>('/api/study/canvas/connect', { method: 'POST', body: { base, token } });
export const canvasLink = (name: string, courseId: number) => request<{ note?: string }>(cpath(name, '/canvas'), { method: 'POST', body: { course_id: courseId } });

/** 传大纲 / 课件（手机：DocumentPicker 选的文件；网页：File）。 */
export async function uploadSyllabus(name: string, f: PendingFile) {
  const fd = new FormData();
  formFile(fd, 'file', f);
  return xhrUpload(`${getBase()}${cpath(name, '/syllabus')}`, fd);
}
export async function uploadFiles(name: string, files: PendingFile[], session?: string) {
  const fd = new FormData();
  for (const f of files) formFile(fd, 'files', f);
  if (session) fd.append('session', session);
  return xhrUpload(`${getBase()}${cpath(name, '/files')}`, fd) as Promise<{ files: { name: string; status: string; n: number | null; reason: string }[]; course: Course }>;
}

/** 学习台在电脑上的地址：网页版就是同一个服务器；手机上用连着的服务器地址。 */
export const deskUrl = () => `${getBase() || (Platform.OS === 'web' && typeof window !== 'undefined' ? window.location.origin : '')}/study`;
