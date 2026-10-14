// 本机的一小块文字，同步读写（草稿、「我自己来」这类只跟这台设备有关的）。
// iOS 用 React Native 自带的 Settings（NSUserDefaults），网页用 localStorage，
// 安卓用 app 文档目录里的一个文件（expo-file-system：expo 自带，两边的安装包里都有，不是新原生模块）。
// 读不到当没有、存不上就跳过（隐私模式的浏览器等），调用的地方只在内存里记着。
import { Platform, Settings } from 'react-native';

/** 只在安卓上加载 expo-file-system：iOS 和网页用不到，也不让已经装好的 iOS 包启动时多碰一个原生模块。 */
function fileOf(key: string) {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { File, Paths } = require('expo-file-system') as typeof import('expo-file-system');
  return new File(Paths.document, `${key}.txt`);
}

export function readLocal(key: string): string | null {
  try {
    if (Platform.OS === 'ios') { const v: unknown = Settings.get(key); return typeof v === 'string' ? v : null; }
    if (Platform.OS === 'web') return globalThis.localStorage?.getItem(key) ?? null;
    if (Platform.OS === 'android') { const f = fileOf(key); return f.exists ? f.textSync() : null; }
  } catch {
    // 当没有
  }
  return null;
}

export function writeLocal(key: string, value: string): void {
  try {
    if (Platform.OS === 'ios') Settings.set({ [key]: value });
    else if (Platform.OS === 'web') globalThis.localStorage?.setItem(key, value);
    else if (Platform.OS === 'android') fileOf(key).write(value);
  } catch {
    // 只在内存里
  }
}
