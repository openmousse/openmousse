// 语言设置：跟随系统 / 中文 / English。选择存在本机（和服务器地址同一处），启动时先读出来再渲染，避免先闪一下另一种语言。
// 切换时给子树换 key 整棵重挂：界面文字重新算，store 也重新拉数据（服务器按新的 Accept-Language 回文字）。
import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { Platform } from 'react-native';
import { loadLangPref, saveLangPref } from './api/base';
import { lang, setLang, systemLang, type Lang } from './lang';

export { L, lang, type Lang } from './lang';
export type LangPref = Lang | 'system';

const Ctx = createContext<{ lang: Lang; pref: LangPref; setPref: (p: LangPref) => void }>({ lang: lang(), pref: 'system', setPref: () => {} });

export const useLang = () => useContext(Ctx);

export function LangProvider({ children }: { children: React.ReactNode }) {
  const [pref, setPrefState] = useState<LangPref>('system');
  const [value, setValue] = useState<Lang>(lang());
  const [ready, setReady] = useState(false);

  useEffect(() => {
    loadLangPref().then((saved) => {
      const p: LangPref = saved === 'zh' || saved === 'en' ? saved : 'system';
      const l = p === 'system' ? systemLang() : p;
      setLang(l);
      setPrefState(p);
      setValue(l);
      setReady(true);
    });
  }, []);

  const setPref = useCallback((p: LangPref) => {
    const l = p === 'system' ? systemLang() : p;
    setLang(l);
    // 网页版：<html lang> 跟着换（读屏的语音、浏览器要不要提示翻译都看它；首次加载由 build-web.mjs 的内联脚本设）
    if (Platform.OS === 'web' && typeof document !== 'undefined') document.documentElement.lang = l === 'zh' ? 'zh-CN' : 'en';
    setPrefState(p);
    setValue(l);
    saveLangPref(p === 'system' ? '' : p);
  }, []);

  if (!ready) return null;
  return (
    <Ctx.Provider value={{ lang: value, pref, setPref }}>
      <React.Fragment key={value}>{children}</React.Fragment>
    </Ctx.Provider>
  );
}
