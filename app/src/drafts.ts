// 没发出去的草稿按线程存在本机：切走、退出 app、手机重启以后都还在，发出去或清空了才删。
// iOS 用 React Native 自带的 Settings（NSUserDefaults，同步读写，不用新原生模块），网页用 localStorage，其它平台只在内存里。
// 读在模块加载时一次读进来，打开对话时同步就能拿到；写攒 0.5 秒，切到后台时立刻写。
import { AppState, Platform, Settings } from 'react-native';

const KEY = 'mousse.drafts';
const MAX_THREADS = 30;       // 最多记这么多个线程的草稿，多了丢最旧的
const MAX_CHARS = 20_000;     // 单个草稿最多存这么长
const MAX_AGE_MS = 14 * 86_400_000;  // 两周没动过的草稿不要了

type Saved = Record<string, { text: string; at: number }>;

function readAll(): Saved {
  try {
    const raw = Platform.OS === 'ios' ? Settings.get(KEY) : Platform.OS === 'web' ? globalThis.localStorage?.getItem(KEY) : null;
    const parsed: unknown = typeof raw === 'string' ? JSON.parse(raw) : null;
    if (!parsed || typeof parsed !== 'object') return {};
    const now = Date.now();
    return Object.fromEntries(Object.entries(parsed as Saved).filter(([, d]) => typeof d?.text === 'string' && now - d.at < MAX_AGE_MS));
  } catch {
    return {};
  }
}

const saved: Saved = readAll();
let timer: ReturnType<typeof setTimeout> | null = null;

function writeAll() {
  timer = null;
  const keep = Object.entries(saved).sort((a, b) => b[1].at - a[1].at).slice(0, MAX_THREADS);
  const value = JSON.stringify(Object.fromEntries(keep));
  try {
    if (Platform.OS === 'ios') Settings.set({ [KEY]: value });
    else if (Platform.OS === 'web') globalThis.localStorage?.setItem(KEY, value);
  } catch {
    // 存不上就只在内存里（隐私模式的浏览器等）
  }
}

/** 这个线程上次没发出去的草稿（没有是空字符串）。 */
export function loadDraft(thread: string): string {
  return saved[thread]?.text ?? '';
}

/** 记下草稿；空的（发出去了、删光了）就删掉。 */
export function saveDraft(thread: string, text: string) {
  const before = saved[thread]?.text ?? '';
  if (text === before) return;
  if (text.trim()) saved[thread] = { text: text.slice(0, MAX_CHARS), at: Date.now() };
  else delete saved[thread];
  if (timer) clearTimeout(timer);
  timer = setTimeout(writeAll, 500);
}

AppState.addEventListener('change', (next) => {
  if (next !== 'active' && timer) {
    clearTimeout(timer);
    writeAll();
  }
});
