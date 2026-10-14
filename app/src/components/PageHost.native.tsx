// 页面的盒子 · 手机（1.0.6 起有 react-native-webview；网页版是 PageHost.tsx，Metro 按平台挑文件）。和网页版的 iframe 对齐：
// - 文档按 about:blank 加载（source.html + baseUrl about:blank）；无痕、不开文件访问、不共享 cookie、不开 DOM 存储，页面存不下东西。
// - 不许离开：只放行 about:blank / about:srcdoc（同一份文档里跳锚点也算），别的地址一律拦下并停掉页面（网页外壳 frame-src 'none' 的等价物）。
//   originWhitelist 不能只写 about:*：不在白名单里的地址 react-native-webview 会自己用 Linking.openURL 打开，等于让页面带着拼进地址的数据
//   跳到系统浏览器。这里放行全部、交给 onShouldStartLoadWithRequest 判断。安卓不开多窗口（window.open 的新窗口没人管，会交给系统浏览器）。
// - 第二次加载新文档（刷新、跳到别处）停下，和网页版数 load 一样；网页进程没了、一秒内消息太多也停：安卓拦跳转要等 JS 线程回话，
//   250 ms 等不到就放行，不能让页面把 JS 线程塞满。
// - 桥：页面经 window.ReactNativeWebView.postMessage 发 JSON 字符串（服务器的 bridge_js 认得这个盒子），
//   结果用 injectJavaScript 派一个 message 事件回去，形状和网页版一样。摄像头、麦克风、弹窗由桥在页面脚本之前拿掉。
import React, { useMemo, useRef } from 'react';
import { StyleSheet } from 'react-native';
import { WebView, type WebViewMessageEvent, type WebViewNavigation } from 'react-native-webview';
import { bridgeMsg, bridgeResult, type PageHostProps, type PageStop } from './pageBridge';

const HERE = /^about:(blank|srcdoc)(#|$)/;
const ANY = ['*'];           // originWhitelist：全部交给 onShouldStart 判断（见上）
const MAX_PER_SEC = 40;      // 桥一秒内最多收这么多条（正常页面远到不了）
const MAX_MSG = 32 * 1024;   // 一条消息的上限：要数据、放一句话、报错都很短

/** 派回页面的脚本：JSON 本身就是合法的 JS 字面量；U+2028 / U+2029 转义掉，老引擎里它们不能出现在字符串里。 */
const deliver = (msg: object) =>
  `window.dispatchEvent(new MessageEvent('message',{data:${JSON.stringify(msg).replace(/\u2028/g, '\\u2028').replace(/\u2029/g, '\\u2029')}}));true;`;

export function PageHost({ html, title, onBridge, onStop }: PageHostProps) {
  const ref = useRef<WebView>(null);
  const loads = useRef(0);
  const done = useRef(false);
  const rate = useRef({ at: 0, n: 0 });
  // 同一个对象：换了对象原生那边可能重新加载，会被当成第二次加载。baseUrl 写明 about:blank（安卓不写时是空串，两边不一致）
  const source = useMemo(() => ({ html, baseUrl: 'about:blank' }), [html]);

  const stop = (why: PageStop) => {
    if (done.current) return;
    done.current = true;
    onStop(why);
  };

  const onShouldStart = (req: { url: string; isTopFrame?: boolean }) => {
    if (HERE.test(req.url)) return true;
    if (req.isTopFrame !== false) stop('left');  // 安卓不报 isTopFrame，一律当作整页
    return false;
  };

  const onLoadStart = (e: { nativeEvent: WebViewNavigation }) => {
    const { url, navigationType } = e.nativeEvent;
    // 同一份文档里跳锚点、前进后退也会报开始加载（iOS 在放行时报，安卓每改一次历史记录报一次）：不算新文档
    if (loads.current > 0 && navigationType !== 'reload' && (url.includes('#') || navigationType === 'backforward')) return;
    loads.current += 1;
    if (loads.current > 1) stop('left');
  };

  const onMessage = (e: WebViewMessageEvent) => {
    if (done.current) return;
    const now = Date.now();
    const r = rate.current;
    if (now - r.at > 1000) { r.at = now; r.n = 0; }
    if (++r.n > MAX_PER_SEC) { stop('flood'); return; }
    const raw = e.nativeEvent.data;
    if (typeof raw !== 'string' || raw.length > MAX_MSG) return;
    let d: unknown;
    try { d = JSON.parse(raw); } catch { return; }
    const m = bridgeMsg(d);
    if (!m) return;
    onBridge(m, (res) => {
      if (!done.current) ref.current?.injectJavaScript(deliver(bridgeResult(m.id, res)));
    });
  };

  // dataDetectorTypes 一定传数组：原生组件的定义是数组，安卓（新架构）拿到字符串解析属性时直接崩（10/14 模拟器上崩过）
  return (
    <WebView ref={ref} source={source} accessibilityLabel={title} style={styles.web} containerStyle={styles.web}
      originWhitelist={ANY} onShouldStartLoadWithRequest={onShouldStart} onLoadStart={onLoadStart} onMessage={onMessage}
      onContentProcessDidTerminate={() => stop('crashed')} onRenderProcessGone={() => stop('crashed')}
      javaScriptEnabled incognito domStorageEnabled={false} sharedCookiesEnabled={false} thirdPartyCookiesEnabled={false}
      allowFileAccess={false} allowFileAccessFromFileURLs={false} allowUniversalAccessFromFileURLs={false}
      setSupportMultipleWindows={false} javaScriptCanOpenWindowsAutomatically={false} geolocationEnabled={false} saveFormDataDisabled
      mediaCapturePermissionGrantType="deny" allowsLinkPreview={false} dataDetectorTypes={['none']} allowsBackForwardNavigationGestures={false} />
  );
}

const styles = StyleSheet.create({
  web: { flex: 1, backgroundColor: 'transparent' },
});
