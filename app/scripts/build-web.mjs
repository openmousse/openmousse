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
// 它也让页面底色和浏览器顶栏（theme-color）跟存的外观走（localStorage mousse.appearance，规则同 src/theme.tsx 的 parseAppearance：
// 没选过或认不得是深色），不然浅色系统的浏览器每次先刷一下浅灰、顶栏一直是浅的。选了「跟随系统」才交给上面的 media 规则。
// 不在 <html> 上写 data-theme：src/theme.tsx 的 useHostTheme 把它当成外壳指定的深浅，「跟随系统」就不跟了。
// 页面（Agent 写的网页，src/screens/PageScreen.tsx）在 sandbox iframe（srcdoc）里跑：frame-src 'none' 让它没法把自己导航到别的网址
// （带着读到的数据跳走）。srcdoc 不经网络、不受它管；app 自己不嵌任何别的 iframe。
const head = `
    <meta http-equiv="Content-Security-Policy" content="frame-src 'none'" />
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
        var a = '';
        try { a = localStorage.getItem('mousse.appearance') || ''; } catch (e) {}
        if (a !== 'system') {
          var bg = a === 'light' ? '#F1F3F4' : '#0B0D10';
          var st = document.createElement('style');
          st.textContent = 'html, body { background: ' + bg + '; }';
          document.head.appendChild(st);
          var tc = document.querySelectorAll('meta[name="theme-color"]');
          for (var i = 0; i < tc.length; i++) tc[i].setAttribute('content', bg);
        }
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
