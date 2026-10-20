// 主对话顶上的「从这里开始」（新实例第一次打开时）：点了「我自己来」，这台设备上就不再显示。
// 和草稿一样存在本机（deviceStore.ts，同步读写）。模块加载时读一次，打开主对话时同步就知道，不会先闪一下卡片。
import { readLocal, writeLocal } from './deviceStore';

const KEY = 'mousse.welcomeHidden';

let hidden = readLocal(KEY) === '1';

/** 这台设备上点过「我自己来」。 */
export const welcomeHidden = () => hidden;

/** 「我自己来」：记在本机，以后不再显示。 */
export function hideWelcome() {
  hidden = true;
  writeLocal(KEY, '1');  // 存不上就只在这次打开里不显示（隐私模式的浏览器等）
}

// 第一次打开的三步（2026-10-19）：哪一步点了「以后再说」也记在本机，按服务器分开（一台设备能连好几台 claw）。
// 跳过了「选模型」的，这台设备上不再自己跳进三步；设置首页照样看得到哪一层没配。
const SETUP_KEY = 'mousse.setupSkipped';
export type SetupStep = 'models' | 'search' | 'memory' | 'name';

function readSkipped(): Record<string, SetupStep[]> {
  try {
    const v: unknown = JSON.parse(readLocal(SETUP_KEY) || '{}');
    return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, SetupStep[]>) : {};
  } catch {
    return {};
  }
}

let skipped = readSkipped();

/** 这台设备上，这台服务器的这一步点过「以后再说」。 */
export const setupSkipped = (server: string, step: SetupStep) => (skipped[server] ?? []).includes(step);

export function skipSetup(server: string, step: SetupStep) {
  if (setupSkipped(server, step)) return;
  skipped = { ...skipped, [server]: [...(skipped[server] ?? []), step] };
  writeLocal(SETUP_KEY, JSON.stringify(skipped));
}
