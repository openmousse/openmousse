// 页面的盒子 · 网页版（手机上是 PageHost.native.tsx，Metro 按平台挑文件，网页包里不会有 react-native-webview）。
// sandbox="allow-scripts"（不透明源）的 iframe，内容由服务器包好（CSP、桥、主题）。只认这个 iframe 发来的消息；
// 网页外壳有 frame-src 'none'，iframe 想跳到别处会加载失败，第二次 load 就停下（onStop('left')）。
import React, { useEffect, useRef } from 'react';
import { bridgeMsg, bridgeResult, type PageHostProps } from './pageBridge';

export function PageHost({ html, title, onBridge, onStop }: PageHostProps) {
  const ref = useRef<HTMLIFrameElement | null>(null);
  const loads = useRef(0);
  useEffect(() => {
    const onMsg = (e: MessageEvent) => {
      const f = ref.current;
      if (!f || e.source !== f.contentWindow || loads.current > 1) return;  // 只认这个盒子；它跳走以后不再理
      const m = bridgeMsg(e.data);
      if (!m) return;
      onBridge(m, (r) => {
        if (loads.current <= 1) f.contentWindow?.postMessage(bridgeResult(m.id, r), '*');
      });
    };
    window.addEventListener('message', onMsg);
    return () => window.removeEventListener('message', onMsg);
  }, [onBridge]);
  return React.createElement('iframe', {
    ref, title, srcDoc: html, sandbox: 'allow-scripts', referrerPolicy: 'no-referrer',
    onLoad: () => { loads.current += 1; if (loads.current > 1) onStop('left'); },
    style: { border: 0, width: '100%', height: '100%', display: 'block', background: 'transparent' },
  });
}
