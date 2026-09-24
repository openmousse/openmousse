// 双语：界面文字一律写成 L('中文', 'English')，中英并排，改一处看得见另一处。
// 语言默认跟随系统（系统语言是中文就用中文，其他一律英文），「我 → 语言」可以固定一种（见 i18n.tsx）。
// L() 在调用时取当前语言：别在模块顶层调用（只会算一次），要常量就写成函数。切换语言时整棵树重挂，所有 L() 重新算。
// 服务器返回的枚举值（任务状态、餐次之类）是给程序比较的，不翻译；显示时在 app 里用 L() 换成对应语言。
import { Platform } from 'react-native';

export type Lang = 'zh' | 'en';

export function systemLang(): Lang {
  try {
    const loc = (Platform.OS === 'web' && typeof navigator !== 'undefined' ? navigator.language : '') || Intl.DateTimeFormat().resolvedOptions().locale || '';
    return /^zh/i.test(loc) ? 'zh' : 'en';
  } catch {
    return 'en';
  }
}

let current: Lang = systemLang();

export const lang = (): Lang => current;
export const setLang = (l: Lang) => { current = l; };

export function L(zh: string, en: string): string {
  return current === 'zh' ? zh : en;
}

/** 请求头 Accept-Language：服务器按它返回中文或英文的提示文字。 */
export const acceptLanguage = (): string => (current === 'zh' ? 'zh-CN' : 'en');
