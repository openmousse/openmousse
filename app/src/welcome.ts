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
