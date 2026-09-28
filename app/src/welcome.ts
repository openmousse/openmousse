// 主对话顶上的「从这里开始」（新实例第一次打开时）：点了「我自己来」，这台设备上就不再显示。
// 和草稿一样存在本机（见 drafts.ts）：iOS 用 React Native 自带的 Settings（NSUserDefaults，同步读写，不用新原生模块），
// 网页用 localStorage，其它平台只在内存里。模块加载时读一次，打开主对话时同步就知道，不会先闪一下卡片。
import { Platform, Settings } from 'react-native';

const KEY = 'mousse.welcomeHidden';

function read(): boolean {
  try {
    const raw = Platform.OS === 'ios' ? Settings.get(KEY) : Platform.OS === 'web' ? globalThis.localStorage?.getItem(KEY) : null;
    return raw === '1';
  } catch {
    return false;
  }
}

let hidden = read();

/** 这台设备上点过「我自己来」。 */
export const welcomeHidden = () => hidden;

/** 「我自己来」：记在本机，以后不再显示。 */
export function hideWelcome() {
  hidden = true;
  try {
    if (Platform.OS === 'ios') Settings.set({ [KEY]: '1' });
    else if (Platform.OS === 'web') globalThis.localStorage?.setItem(KEY, '1');
  } catch {
    // 存不上就只在这次打开里不显示（隐私模式的浏览器等）
  }
}
