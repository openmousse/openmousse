// 把 Expo 的网页导出加工成可以"添加到主屏幕"的全屏 web app。
// 用法：npm run web:build
import { copyFileSync, existsSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const root = new URL('..', import.meta.url).pathname;
const readJson = (name) => (existsSync(join(root, name)) ? JSON.parse(readFileSync(join(root, name), 'utf8')) : {});
// 身份文件的取法和 app.config.js 一致：MOUSSE_LOCAL 可以换别的文件，none = 纯模板。
const localName = process.env.MOUSSE_LOCAL || 'app.local.json';
const localCfg = localName !== 'none' ? readJson(localName) : {};
const iconsDir = (localCfg.icons || 'assets').replace(/^\.?\//, '');
// 主屏幕上显示的名字：身份文件里的 name，没有就用模板 app.json 的。
const appName = localCfg.name || readJson('app.json').expo?.name || 'OpenMousse';
const attr = (s) => s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
const file = join(root, 'dist/index.html');
let html = readFileSync(file, 'utf8');

// 末尾的小脚本让 <html lang> 跟界面语言走（读屏的发音、浏览器的翻译提示看它）：
// 「我 → 语言」存的选择（localStorage mousse.lang）优先，否则看系统语言，和 src/lang.ts 同一规则。
const head = `
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, user-scalable=no" />
    <meta name="apple-mobile-web-app-capable" content="yes" />
    <meta name="mobile-web-app-capable" content="yes" />
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
    <meta name="apple-mobile-web-app-title" content="${attr(appName)}" />
    <meta name="theme-color" content="#F1F3F4" media="(prefers-color-scheme: light)" />
    <meta name="theme-color" content="#0B0D10" media="(prefers-color-scheme: dark)" />
    <link rel="apple-touch-icon" href="/apple-touch-icon.png" />
    <link rel="manifest" href="/manifest.json" />
    <style>
      html, body { background: #F1F3F4; overscroll-behavior: none; -webkit-tap-highlight-color: transparent; }
      @media (prefers-color-scheme: dark) { html, body { background: #0B0D10; } }
      input, textarea { font-size: 16px; } /* 小于 16px 时 iOS 会在聚焦输入框时自动放大页面 */
    </style>
    <script>
      (function () {
        var p = '';
        try { p = localStorage.getItem('mousse.lang') || ''; } catch (e) {}
        var zh = p === 'zh' || (p !== 'en' && /^zh/i.test(navigator.language || ''));
        document.documentElement.lang = zh ? 'zh-CN' : 'en';
      })();
    </script>`;

html = html.replace(/<meta name="viewport"[^>]*>/, '').replace('</head>', `${head}\n  </head>`);
writeFileSync(file, html);
copyFileSync(join(root, iconsDir, 'apple-touch-icon.png'), join(root, 'dist/apple-touch-icon.png'));
writeFileSync(join(root, 'dist/manifest.json'), JSON.stringify({
  name: appName, short_name: appName, start_url: '/', display: 'standalone',
  background_color: '#0B0D10', theme_color: '#0B0D10',
  icons: [{ src: '/apple-touch-icon.png', sizes: '180x180', type: 'image/png' }],
}, null, 2));
console.log('dist/ is ready to serve');
