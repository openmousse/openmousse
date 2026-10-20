// 对讲（第 8 批，13.6，2026-10-19）里跟字打交道的几样。纯函数，不引别的文件（node scripts/test-talk.mjs 直接跑）：
// - cut：回复一边流过来一边念。从已经念到的地方（原文的位置）切到最后一个句子结束的地方；没关上的代码块、还在往下长的表格先不念；
//   回复完了（final）把剩下的都念完。
// - Reading：这一轮念到哪了。回复还在流时又说了一句（插话、回答它的反问 ask_user：内核把这句并进正在跑的这一轮，回复接着往下长），
//   发的时候已经流出来的不再念，只念后面接着来的；那一轮先回完了、这句排成了下一轮，下一轮从头念。
// - speakable：回复是 Markdown，念之前去掉记号：代码块、表格各换成一句「见对话」，链接只念字，网址不念，
//   合并回复开头的「> 「原话」」引用行不念。
// - pieces：一段太长就按句子拆开，一句一个朗读（安卓的朗读一次最多四千来字）。
// - misheard：没人说话、只有一点杂音时，转写常编出几句固定的话（「Thank you for watching」「字幕由 Amara.org 社区提供」……），
//   这种不当成主人说的话发出去。英文和别的语种那几句改写自 Hermes 的 tools/voice_mode_transcript.py（WHISPER_HALLUCINATIONS、
//   _HALLUCINATION_REPEAT_RE、is_whisper_hallucination），NousResearch/hermes-agent commit f4ac0bea1ea50bba62886fb0b7e308e4edee8fd3，
//   MIT，Copyright (c) 2025 Nous Research；许可全文见仓库根目录的 THIRD_PARTY_NOTICES。中文那两句是这边补的。

/** 念到代码块、表格、网址时说的那一句（按 app 的语言传进来）。 */
export interface Notes { code: string; table: string; link: string }

const HARD = '。！？!?；;…';
const CLOSERS = '”’」』）)]"\'*_`';
const SOFT = '，,、：:';
/** 一直没有句子结束、攒到这么长就在逗号处先念一段 */
const SOFT_MAX = 120;
/** 一个朗读最长多少字 */
const PIECE_MAX = 300;

const isRow = (line: string) => /^\s*\|/.test(line);

/**
 * s 里最后一个句子结束的位置（句号、问号、换行……之后；英文句点后面得是空白，行首的「1.」是序号不算）。没有是 -1。
 * growing = 后面还有字要来：句号正好在最后时先不算，等下一个字（可能是跟着的引号、括号）。
 */
function lastBoundary(s: string, growing = false): number {
  let best = -1;
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (c === '\n') { best = i + 1; continue; }
    const dot = c === '.';
    if (!dot && !HARD.includes(c)) continue;
    let j = i + 1;
    while (j < s.length && (HARD.includes(s[j]) || s[j] === '.')) j++;  // ……、?!、... 连着的算一个
    while (j < s.length && CLOSERS.includes(s[j])) j++;
    if (growing && j >= s.length) break;
    if (dot && j === i + 1) {
      if (j >= s.length || !/\s/.test(s[j])) continue;  // 3.14、e.g.x、还没来下一个字
      const lineStart = s.lastIndexOf('\n', i) + 1;
      if (/^\s*\d+$/.test(s.slice(lineStart, i))) continue;  // 列表序号「1.」
    }
    best = j;
    i = j - 1;
  }
  return best;
}

/** 没有句子结束、又攒得太长：最后一个逗号（或空格）之后。没有是 -1。 */
function lastSoft(s: string): number {
  for (let i = s.length - 1; i > 0; i--) {
    if (SOFT.includes(s[i]) || (s[i] === ' ' && i > SOFT_MAX / 2)) return i + 1;
  }
  return -1;
}

/** 切到 end 的这一段以表格行结尾（表格可能还在往下长）：退回到这张表前面，等表格完了一起换成一句。 */
function backOffTable(zone: string, end: number): number {
  const lines = zone.slice(0, end).split('\n');
  if (lines[lines.length - 1] === '') lines.pop();
  let i = lines.length - 1;
  if (i < 0 || !isRow(lines[i])) return end;
  while (i > 0 && isRow(lines[i - 1])) i--;
  return lines.slice(0, i).reduce((n, l) => n + l.length + 1, 0);
}

/**
 * 回复的原文 full 里从 from 起现在能念的一段：text 是这段原文（还没去记号），to 是念到原文的哪里。
 * final = 回复完了，剩下的都给。还不能念（句子没说完、代码块没关上）时 text 是 ''、to 不动。
 */
export function cut(full: string, from: number, final: boolean): { text: string; to: number } {
  const rest = full.slice(from);
  if (final) return { text: rest, to: full.length };
  let limit = rest.length;
  const fences: number[] = [];
  for (let i = rest.indexOf('```'); i >= 0; i = rest.indexOf('```', i + 3)) fences.push(i);
  if (fences.length % 2 === 1) limit = fences[fences.length - 1];  // 代码块还没关上：只看它前面
  const zone = rest.slice(0, limit);
  let end = lastBoundary(zone, limit === rest.length);
  if (end <= 0 && zone.length > SOFT_MAX) end = lastSoft(zone);
  // 关上了的代码块整块一起：切的地方落在块里（块里的换行）就退到块前面
  for (let k = 0; k + 1 < fences.length; k += 2) {
    if (end > fences[k] && end < fences[k + 1] + 3) end = fences[k];
  }
  if (end > 0) end = backOffTable(zone, end);
  if (end <= 0) return { text: '', to: from };
  return { text: zone.slice(0, end), to: from + end };
}

/** 两段字从头开始一样的有多长（回复整段换掉时，从这里接着念）。 */
export function common(a: string, b: string): number {
  const n = Math.min(a.length, b.length);
  let i = 0;
  while (i < n && a.charCodeAt(i) === b.charCodeAt(i)) i++;
  return i;
}

let pictographs: RegExp | null | undefined;
/** 表情符号（念出来是「红心」这种）：引擎不认 \p{…} 就不去 */
function dropPictographs(s: string): string {
  if (pictographs === undefined) {
    try { pictographs = new RegExp('[\\p{Extended_Pictographic}\\u{FE0F}\\u{200D}]', 'gu'); } catch { pictographs = null; }
  }
  return pictographs ? s.replace(pictographs, '') : s;
}

/** Markdown → 念的字。一行没有标点结尾（标题、列表）就补一个句号（有汉字补「。」），念的时候停一下。 */
export function speakable(md: string, notes: Notes): string {
  const s = md.replace(/\r\n?/g, '\n').replace(/```[\s\S]*?(?:```|$)/g, `\n${notes.code}\n`);
  const out: string[] = [];
  let table = false;
  for (const raw of s.split('\n')) {
    const line = raw.trim();
    if (isRow(line)) {
      if (!table) out.push(notes.table);
      table = true;
      continue;
    }
    table = false;
    if (/^>\s*「[^」]*」\s*$/.test(line)) continue;  // 合并回复每段开头的「> 「原话」」
    let x = line
      .replace(/^#{1,6}\s+/, '')
      .replace(/^>\s?/, '')
      .replace(/^(?:[-*+•]|\d+[.)])\s+/, '')
      .replace(/^[-*_]{3,}$/, '')
      .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
      .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
      .replace(/<https?:\/\/[^>\s]+>/g, notes.link)
      .replace(/https?:\/\/\S+/g, notes.link)
      .replace(/`([^`]*)`/g, '$1')
      .replace(/(\*\*|__|~~)(.+?)\1/g, '$2')
      .replace(/(^|[^\w*])\*(?!\s)([^*\n]+?)\*(?!\w)/g, '$1$2')
      .replace(/(^|[^\w_])_(?!\s)([^_\n]+?)_(?!\w)/g, '$1$2')
      .replace(/\*{2,}|_{2,}|~~/g, '')
      .replace(/<\/?[a-zA-Z][^>]*>/g, '');
    x = dropPictographs(x).replace(/\s+/g, ' ').trim();
    if (!x) continue;
    if (!/[。！？!?.；;…：:，,”’」』）)\]"']$/.test(x)) x += langOf(x) === 'zh' ? '。' : '.';
    out.push(x);
  }
  return out.join('\n');
}

/** 念的字拆成一个个朗读：按行，一行太长再按句子拆。 */
export function pieces(text: string, max = PIECE_MAX): string[] {
  const out: string[] = [];
  for (let line of text.split('\n')) {
    line = line.trim();
    while (line.length > max) {
      let end = lastBoundary(line.slice(0, max));
      if (end <= 0) end = lastSoft(line.slice(0, max));
      if (end <= 0) end = max;
      out.push(line.slice(0, end).trim());
      line = line.slice(end).trim();
    }
    if (line) out.push(line);
  }
  return out;
}

/** 这段念中文还是英文：有汉字就中文。 */
export const langOf = (text: string): 'zh' | 'en' => (/[\u3400-\u9fff\uf900-\ufaff]/.test(text) ? 'zh' : 'en');

const MISHEARD = new Set([
  'thank you', 'thanks for watching', 'subscribe to my channel', 'like and subscribe',
  'please subscribe', 'thank you for watching', 'bye', 'you', 'the end',
  'продолжение следует', 'sous-titres', "sous-titres réalisés par la communauté d'amara.org",
  'sottotitoli creati dalla comunità amara.org', 'untertitel von stephanie geiges',
  'amara.org', 'www.mooji.org', 'ご視聴ありがとうございました',
]);
const REPEAT = /^(?:thank you|thanks|bye|you|ok|okay|the end|\.|\s|,|!)+$/i;

/** 转写回来的这句是不是杂音上编出来的（空的也算）。 */
export function misheard(text: string): boolean {
  const t = text.trim().toLowerCase();
  if (!t) return true;
  if (MISHEARD.has(t.replace(/[.!]+$/, '')) || REPEAT.test(t)) return true;
  return /amara\.org|明镜与点点/.test(t);  // 中文转写常编的两句：Amara 的字幕署名、某个栏目的求赞
}

/**
 * 这一轮念到哪了（loop.ts 用）。平常一问一答：从回复的头念起。
 * 回复还在流时又说了一句（插话、回答 ask_user 的反问）：内核把它并进正在跑的这一轮，回复在同一段字后面接着长。发的时候已经流出来的
 * 当念过（念过的，或者主人打断了不要听的），只念后面接着来的；原来是从头整条重念（审查时发现的）。那一轮先回完了、这句排成了下一轮
 * （插话没赶上）：后面来的字和发的时候流着的那段对不上，从头念。
 */
export class Reading {
  /** 念过（或者不念了）的那段原文：来的字以它开头 = 同一轮接着长 */
  private raw = '';
  /** 发这一句时已经流出来的字 */
  private base = '';
  /** 发了以后有一轮回完了（流着的字没了）：后面来的可能是下一轮 */
  private ended = false;

  /** 发出一句。streaming：这时对话里正在流的字（没在回复是 undefined）。 */
  start(streaming: string | undefined) {
    this.base = streaming ?? '';
    this.raw = this.base;
    this.ended = false;
  }

  /** 流着的字没了：有一轮回完了。 */
  end() {
    this.ended = true;
  }

  /** 给页面看的字：发这一句以后来的。 */
  shown(text: string): string {
    return text.startsWith(this.base) ? text.slice(this.base.length) : text;
  }

  /** 回复的原文来了（流着的，或者 final = 完整的）：回这次要念的那段原文。 */
  take(text: string, final: boolean): string {
    this.sync(text);
    const r = cut(text, this.raw.length, final);
    if (r.to > this.raw.length) this.raw = text.slice(0, r.to);
    return r.text;
  }

  /** 回复停了一会儿没再长：正好停在最后的那一句也念（ask_user 的问题后面一时不会有字）。代码块还没关上就接着等。 */
  flush(text: string): string {
    this.sync(text);
    if ((text.slice(this.raw.length).match(/```/g) ?? []).length % 2 === 1) return '';
    return this.take(text, true);
  }

  private sync(text: string) {
    if (!text.startsWith(this.raw)) {
      const same = common(text, this.raw);
      if (this.ended && same < this.base.trimEnd().length) {  // 跟发的时候流着的那段对不上：换成下一轮了，从头念
        this.base = '';
        this.raw = '';
      } else {
        this.raw = text.slice(0, same);  // 同一轮整段换掉了：从相同的开头接着念
      }
    }
    this.ended = false;
  }
}
