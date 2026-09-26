// 记忆的一条原文 → 列表里显示的一句话标题、日期、是不是你定的。纯文字处理，不依赖界面（方便单独测）。
import { agentName } from '../brand';

const PAREN = /（[^（）]*）|\([^()]*\)/g;
/** 去掉括号里的补充和代码记号，压掉多余空格。 */
export const clean = (s: string) => s.replace(/`/g, '').replace(PAREN, '').replace(PAREN, '').replace(/\s+/g, ' ').trim();
const len = (s: string) => [...s].length;
/** 截到 n 个字加「…」；截断处在一个英文词 / 文件名中间时，退到它前面的空格（别留下半个词）。 */
const cut = (s: string, n: number) => {
  const cs = [...s];
  if (cs.length <= n) return s;
  let head = cs.slice(0, n - 1).join('');
  if (/[\w.]$/.test(head) && /^[\w.]/.test(cs[n - 1] ?? '')) {
    const sp = head.lastIndexOf(' ');
    if (sp >= head.length * 0.6) head = head.slice(0, sp);
  }
  return `${head.trim()}…`;
};
/** 只有日期的开头，比如「2026-09-24：」「9/24：」。 */
const DATE_ONLY = /^(?:\d{4}[-/.年])?\d{1,2}[-/.月]\d{1,2}日?$/;

/** 第一个在括号外面的「：」，或者后面跟空格的 ":"（「08:00」「agent:main」里的不算）。没有就是 -1。 */
function colonAt(s: string): number {
  let depth = 0;
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (c === '（' || c === '(') depth++;
    else if (c === '）' || c === ')') depth = Math.max(0, depth - 1);
    else if (depth === 0 && (c === '：' || (c === ':' && (i + 1 >= s.length || /\s/.test(s[i + 1]))))) return i;
  }
  return -1;
}

/**
 * 一条记忆的标题：「：」前面那段（去掉括号和代码记号）不超过 32 个字就用它；
 * 否则取第一句，太长取第一个分句，还长就截到 28 个字左右。开头只是日期的（「2026-09-24：…」）跳过日期。
 */
export function memoryTitle(text: string): string {
  let first = (text.split('\n')[0] ?? '').trim();
  let i = colonAt(first);
  if (i > 0 && DATE_ONLY.test(first.slice(0, i).trim())) { first = first.slice(i + 1).trim(); i = colonAt(first); }
  if (i > 0) {
    const raw = first.slice(0, i);
    const head = clean(raw);
    // 冒号前面已经过了一句（有句号、分号），就不是标题
    if (head && len(head) <= 32 && !/[。；;！？!?]/.test(raw)) return head;
  }
  // 整条都在括号里（「（待积累）」这种占位）：去掉括号原样用
  const body = clean(first) || first.replace(/[（）()`]/g, '').trim() || first;
  const sentence = (body.split(/[。；;！？!?]|\.\s/)[0] ?? body).trim() || body;
  if (len(sentence) <= 28) return sentence;
  const clause = (sentence.split(/[，,]/)[0] ?? '').trim();
  if (len(clause) >= 4 && len(clause) <= 28) return clause;
  return cut(sentence, 28);
}

const md = (m: number, d: number) => `${m}/${d}`;
/** 原文里第一个日期，写成 9/26。认 2026-09-26、2026/9/26、2026年9月26日、09-26、9/26、9月26日。 */
export function memoryDate(text: string): string | null {
  const hits: { at: number; v: string }[] = [];
  const scan = (re: RegExp, pick: (m: RegExpExecArray) => [number, number] | null) => {
    re.lastIndex = 0;
    for (let m = re.exec(text); m; m = re.exec(text)) {
      const p = pick(m);
      if (p && p[0] >= 1 && p[0] <= 12 && p[1] >= 1 && p[1] <= 31) hits.push({ at: m.index, v: md(p[0], p[1]) });
    }
  };
  // 年份开头的：不认点号分隔（「2026.9.5」多半是版本号）
  scan(/(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?/g, (m) => [Number(m[2]), Number(m[3])]);
  // 月-日要两位数（09-25），免得把「3-4 组」当成日期；前后不能连着数字
  scan(/(^|[^\d/.-])(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])(?![\d-])/g, (m) => [Number(m[2]), Number(m[3])]);
  // 9/25；后面跟着「组」「通过」这类的是比例（「4/4 组」「6/6 通过」），不是日期
  scan(/(^|[^\d/.])(1[0-2]|[1-9])\/(3[01]|[12]\d|[1-9])(?![\d/])(?!\s*(?:组|个|项|次|条|通过|完成|sets?\b|done\b|passed\b))/g, (m) => [Number(m[2]), Number(m[3])]);
  scan(/(1[0-2]|[1-9])月(3[01]|[12]\d|[1-9])日/g, (m) => [Number(m[1]), Number(m[2])]);
  if (!hits.length) return null;
  // 同一处可能被两条规则都认到（2026-09-26 里的 09-26）：取最靠前的
  hits.sort((a, b) => a.at - b.at);
  return hits[0].v;
}

/** 是不是你定的 / 你要求的：「Leo 2026-09-26 定」「Leo 明确要求」「（Leo 2026-09-26）」这类；助手自己的名字不算。 */
export function memoryWho(text: string): 'decided' | 'asked' | null {
  // 助手自己、英文里句首的代词不算「你」
  const not = new Set([agentName(), 'The', 'This', 'That', 'It', 'We', 'They', 'He', 'She', 'Agent']);
  const by = (re: RegExp) => {
    re.lastIndex = 0;
    for (let m = re.exec(text); m; m = re.exec(text)) if (!not.has(m[1])) return true;
    return false;
  };
  const date = String.raw`(?:\s*\d{4}-\d{1,2}-\d{1,2}|\s*\d{1,2}\/\d{1,2})?\s*`;
  if (by(new RegExp(String.raw`([A-Z][a-z]+|用户|你)${date}(?:亲自|明确)?(?:定的|定下|决定|拍板)`, 'g'))
    || by(new RegExp(String.raw`([A-Z][a-z]+|用户|你)${date}(?:亲自|明确)?定(?![a-zA-Z一-鿿])`, 'g'))
    || by(/[（(]\s*([A-Z][a-z]+)\s*,?\s*\d{4}-\d{1,2}-\d{1,2}\s*[）)]/g)
    || by(/\b([A-Z][a-z]+|[Uu]ser|[Yy]ou)\s+(?:decided|chose)\b/g)) return 'decided';
  if (by(new RegExp(String.raw`([A-Z][a-z]+|用户|你)${date}(?:明确)?要求`, 'g')) || by(/\b([A-Z][a-z]+|[Uu]ser|[Yy]ou)\s+(?:asked|requested|insisted)\b/g)) return 'asked';
  return null;
}
