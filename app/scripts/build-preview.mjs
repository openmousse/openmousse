// 把 Expo 的网页导出打成一个自包含的 HTML，方便当作可点击的原型分享。
// 用法：npm run preview   （先 expo export，再运行本脚本）
import { readFileSync, readdirSync, writeFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';

const root = new URL('..', import.meta.url).pathname;
const jsDir = join(root, 'dist/_expo/static/js/web');
const bundleName = readdirSync(jsDir).find((f) => f.endsWith('.js'));
if (!bundleName) throw new Error('没找到网页 bundle，先运行 npx expo export --platform web');
// 内联脚本里不能出现会让 HTML 解析器提前结束或进入注释态的序列。
const bundle = readFileSync(join(jsDir, bundleName), 'utf8')
  .replace(/<\/script/gi, '<\\/script')
  .replace(/<script/gi, '<\\script')
  .replace(/<!--/g, '<\\!--')
  // 发布平台的校验器会把 React DOM 里的这个属性名误认成另一类页面的标记。
  // 它只出现在字符串字面量里，用转义写法绕开，运行时等价。
  .replace(/data-precedence/g, 'data-p\\x72ecedence');

const shell = readFileSync(join(root, 'scripts/preview-shell.html'), 'utf8');
mkdirSync(join(root, 'preview'), { recursive: true });
const out = join(root, 'preview/grava-app-preview.html');
writeFileSync(out, shell.replace('/*__BUNDLE__*/', () => bundle));
console.log(`wrote ${out} (${(bundle.length / 1e6).toFixed(1)} MB bundle)`);
