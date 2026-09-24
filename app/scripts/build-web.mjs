// 把 Expo 的网页导出加工成可以"添加到主屏幕"的全屏 web app。
// 用法：npm run web:build
import { copyFileSync, existsSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const root = new URL('..', import.meta.url).pathname;
const localCfg = existsSync(join(root, 'app.local.json')) ? JSON.parse(readFileSync(join(root, 'app.local.json'), 'utf8')) : {};
const iconsDir = (localCfg.icons || 'assets').replace(/^\.?\//, '');
const file = join(root, 'dist/index.html');
let html = readFileSync(file, 'utf8');

const head = `
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, user-scalable=no" />
    <meta name="apple-mobile-web-app-capable" content="yes" />
    <meta name="mobile-web-app-capable" content="yes" />
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
    <meta name="apple-mobile-web-app-title" content="Grava" />
    <meta name="theme-color" content="#F1F3F4" media="(prefers-color-scheme: light)" />
    <meta name="theme-color" content="#0B0D10" media="(prefers-color-scheme: dark)" />
    <link rel="apple-touch-icon" href="/apple-touch-icon.png" />
    <link rel="manifest" href="/manifest.json" />
    <style>
      html, body { background: #F1F3F4; overscroll-behavior: none; -webkit-tap-highlight-color: transparent; }
      @media (prefers-color-scheme: dark) { html, body { background: #0B0D10; } }
      input, textarea { font-size: 16px; } /* 小于 16px 时 iOS 会在聚焦输入框时自动放大页面 */
    </style>`;

html = html.replace(/<meta name="viewport"[^>]*>/, '').replace('</head>', `${head}\n  </head>`).replace('<html lang="en">', '<html lang="zh-CN">');
writeFileSync(file, html);
copyFileSync(join(root, iconsDir, 'apple-touch-icon.png'), join(root, 'dist/apple-touch-icon.png'));
writeFileSync(join(root, 'dist/manifest.json'), JSON.stringify({
  name: 'Grava', short_name: 'Grava', start_url: '/', display: 'standalone',
  background_color: '#0B0D10', theme_color: '#0B0D10',
  icons: [{ src: '/apple-touch-icon.png', sizes: '180x180', type: 'image/png' }],
}, null, 2));
console.log('dist/ is ready to serve');
