// 回复正文能选一段字（2026-10-20，设计第 36–39 张的方案 A）：只在 iOS、并且原生模块在（nativeReady()）时用，别的平台照旧是 Markdown。
// 正文画成只读、多行的 TextInput（editable=false、scrollEnabled=false）：长按就是系统选字（蓝色把手，拷贝 / 查询 / 翻译 / 共享），不弹键盘。
// Markdown 用嵌套的 <Text> 排进去（粗体、斜体、删除线、行内代码、标题、列表、引用、代码块；TextInput 收 Text 子元素当带样式的字，
// 不能同时给 value）。表格、图片、分隔线排不进输入框，照旧用 Markdown 组件画在两段中间（这几块不能选，选区也不能跨过它们）。
// 系统菜单里加三项（mousse-native 的 editMenu 钩子，1.0.5 起在包里，按 testID 认）：引用这段 / 收藏这段 / 转发这段。钩子回来的事件
// 只说点了哪一项（{key, id, handled}），不说选了哪段：选区用 onSelectionChange 自己记，点的时候按选区切出那段字。
// 链接：裸网址交给 dataDetectorTypes（系统认出来就能点）；[文字](网址) 在只读输入框里点不了，文字照旧是链接色，这一段下面再列一排链接（点了开）。
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Linking, Pressable, StyleSheet, Text, TextInput, View, type TextStyle } from 'react-native';
import { editMenu } from '../api/native';
import { L } from '../i18n';
import { radius, type, useTheme, type Theme } from '../theme';
import { ArrowUpRight } from './icons';
import { Markdown, md, mono } from './Markdown';
import { T } from './ui';

/** 系统菜单里加的三项：引用这段（replyTo 这一条，输入框里先填好这段）/ 收藏这段 / 转发这段（这一条，这段放进附言）。 */
export type PassageAction = 'quote' | 'save' | 'forward';

/** 一小段字的样子：b 粗体、i 斜体、del 删除线、code 行内代码、pre 代码块、h 标题级别、bar 引用的竖条、link 链接、gap 段落之间那一小行。 */
interface Mark { b?: true; i?: true; del?: true; code?: true; pre?: true; h?: number; bar?: true; link?: true; gap?: true }
interface Span { text: string; m: Mark }
export interface LinkRef { text: string; url: string }
/** 一块：text = 一个能选字的输入框（加上这段里点不了的链接）；md = 排不进输入框的（表格、图片、分隔线），照旧画。 */
export type Piece = { kind: 'text'; spans: Span[]; links: LinkRef[] } | { kind: 'md'; src: string };

const BAR = '▎ ';         // 引用：每行开头一条竖线（照 Markdown 组件引用左边那条线）
const INDENT = '    ';    // 列表每深一层
const SAFE = /^(https?:|mailto:)/i;

interface InlineToken { type: string; content: string; markup?: string; attrGet(name: string): string | null }
interface BlockToken extends InlineToken { tag: string; hidden: boolean; map: [number, number] | null; children: InlineToken[] | null }

/** Markdown → 几块。和 Markdown 组件用同一个 markdown-it（linkify、breaks 一样）。 */
export function pieces(src: string): Piece[] {
  const tokens = md.parse(src, {}) as unknown as BlockToken[];
  const lines = src.split('\n');
  const out: Piece[] = [];
  let spans: Span[] = [];
  let links: LinkRef[] = [];
  let brk = 0;          // 下一块字前面要什么：0 什么都不要，1 换一行，2 空一小行（段落之间）
  let quote = 0;        // 在几层引用里
  let heading = 0;
  let headed = false;   // 刚写了列表项的头（• / 1.）：接着的段落不再换行
  const lists: { ordered: boolean; n: number }[] = [];
  const push = (text: string, m: Mark = {}) => { if (text) spans.push({ text, m }); };
  const flush = () => { if (spans.length) out.push({ kind: 'text', spans, links }); spans = []; links = []; brk = 0; };
  const raw = (map: [number, number] | null, fallback: string) => { flush(); out.push({ kind: 'md', src: map ? lines.slice(map[0], map[1]).join('\n') : fallback }); };
  const start = () => {  // 一块字开头：照前一块要的换行，引用里的先画竖条
    if (spans.length) {
      if (brk >= 1) push('\n');
      if (brk >= 2) push('\n', { gap: true });
    }
    brk = 0;
    if (quote) push(BAR.repeat(quote), { bar: true });
  };
  const newline = () => {  // 段落里的换行：接着画引用的竖条、列表的缩进
    push('\n');
    if (quote) push(BAR.repeat(quote), { bar: true });
    if (lists.length) push(INDENT.repeat(lists.length));
  };
  const inline = (children: InlineToken[]) => {
    let b = 0, i = 0, del = 0;
    let href: string | null = null;
    let auto = false;
    let said = '';
    const mark = (extra?: Mark): Mark => ({ ...(b ? { b: true } : {}), ...(i ? { i: true } : {}), ...(del ? { del: true } : {}), ...(heading ? { h: heading } : {}), ...(href ? { link: true } : {}), ...extra });
    for (const c of children) {
      switch (c.type) {
        case 'softbreak': case 'hardbreak': newline(); break;
        case 'strong_open': b++; break;
        case 'strong_close': b = Math.max(0, b - 1); break;
        case 'em_open': i++; break;
        case 'em_close': i = Math.max(0, i - 1); break;
        case 's_open': del++; break;
        case 's_close': del = Math.max(0, del - 1); break;
        case 'link_open': href = c.attrGet('href'); auto = c.markup === 'linkify'; said = ''; break;
        case 'link_close':
          // 文字就是网址（裸网址、linkify 认出来的）：系统的数据检测会把它变成能点的；别的列到这一段下面
          if (href && SAFE.test(href) && !auto && said.trim() && said.trim() !== href && !links.some((x) => x.url === href)) links.push({ text: said.trim(), url: href });
          href = null;
          break;
        case 'code_inline': push(c.content, mark({ code: true })); if (href) said += c.content; break;
        case 'image': push(c.content ? `[${c.content}]` : '', mark()); break;
        default: push(c.content, mark()); if (href) said += c.content;
      }
    }
  };
  for (let k = 0; k < tokens.length; k++) {
    const tk = tokens[k];
    switch (tk.type) {
      case 'heading_open': start(); heading = Number(tk.tag.slice(1)) || 3; break;
      case 'heading_close': heading = 0; brk = 2; break;
      case 'paragraph_open': {
        // 只有一张图的段落：排不进输入框，照旧画
        const kids = tokens[k + 1]?.children ?? [];
        if (kids.some((c) => c.type === 'image')) {
          raw(tk.map, tokens[k + 1]?.content ?? '');
          while (k < tokens.length && tokens[k].type !== 'paragraph_close') k++;
          break;
        }
        if (headed) headed = false;
        else { start(); if (lists.length) push(INDENT.repeat(lists.length)); }
        break;
      }
      case 'paragraph_close': brk = Math.max(brk, tk.hidden ? 1 : 2); break;
      case 'inline': inline(tk.children ?? []); break;
      case 'bullet_list_open': lists.push({ ordered: false, n: 0 }); break;
      case 'ordered_list_open': lists.push({ ordered: true, n: (Number(tk.attrGet('start')) || 1) - 1 }); break;
      case 'bullet_list_close': case 'ordered_list_close': lists.pop(); brk = lists.length ? 1 : 2; break;
      case 'list_item_open': {
        brk = Math.max(brk, 1);
        start();
        const top = lists[lists.length - 1];
        if (top) top.n += 1;
        push(INDENT.repeat(Math.max(0, lists.length - 1)) + (top?.ordered ? `${top.n}. ` : '• '));
        headed = true;
        break;
      }
      case 'list_item_close': headed = false; brk = Math.max(brk, 1); break;
      case 'blockquote_open': quote++; break;
      case 'blockquote_close': quote = Math.max(0, quote - 1); brk = 2; break;
      case 'fence': case 'code_block': start(); push(tk.content.replace(/\n$/, ''), { pre: true }); brk = 2; break;
      case 'table_open':
        raw(tk.map, '');
        while (k < tokens.length && tokens[k].type !== 'table_close') k++;
        break;
      case 'hr': raw(tk.map, '---'); break;
      default:
        if (tk.content && !tk.type.endsWith('_close')) { start(); push(tk.content); brk = 2; }
    }
  }
  flush();
  return out;
}

/** 选中的那段：去掉引用的竖条，前后空白。 */
export const cleanPassage = (s: string) => s.split('\n').map((x) => x.replace(/^(▎ )+/, '')).join('\n').trim();

function spanStyle(m: Mark, t: Theme): TextStyle {
  const big = m.h === 1 || m.h === 2;
  return {
    ...(m.b || m.h ? { fontWeight: big ? '600' : m.b ? '700' : '600' } : {}),
    ...(m.i ? { fontStyle: 'italic' } : {}),
    ...(m.del ? { textDecorationLine: 'line-through' } : m.link ? { textDecorationLine: 'underline' } : {}),
    ...(m.link ? { color: t.cyan } : m.bar ? { color: t.line } : {}),
    ...(m.code || m.pre ? { fontFamily: mono, backgroundColor: t.surface2, fontSize: m.pre ? 13 : 14 } : {}),
    ...(m.pre ? { lineHeight: 19 } : {}),
    ...(big ? { fontSize: 20, lineHeight: 27 } : {}),
    ...(m.gap ? { fontSize: 7, lineHeight: 8 } : {}),
  };
}

let seq = 0;

/** 一个能选字的输入框。onPick 有才往系统菜单里加三项（历史记录页没有）。 */
function TextPiece({ piece, onPick }: { piece: Extract<Piece, { kind: 'text' }>; onPick?: (what: PassageAction, text: string) => void }) {
  const t = useTheme();
  // 每个输入框一个 key（往原生登记菜单用）：同一条回复可能同时开在对话页和历史页上，key 不能只按消息 id
  const [key] = useState(() => `reply-sel-${++seq}`);
  const plain = useMemo(() => piece.spans.map((s) => s.text).join(''), [piece]);
  const children = useMemo(() => piece.spans.map((s, i) => <Text key={i} style={spanStyle(s.m, t)}>{s.text}</Text>), [piece, t]);
  const sel = useRef({ start: 0, end: 0 });
  const pickRef = useRef(onPick);
  useEffect(() => { pickRef.current = onPick; });
  const canPick = !!onPick;
  useEffect(() => {
    if (!canPick) return undefined;
    const items = [
      { id: 'quote', title: L('引用这段', 'Quote this'), icon: 'text.quote' },
      { id: 'save', title: L('收藏这段', 'Save this'), icon: 'bookmark' },
      { id: 'forward', title: L('转发这段', 'Forward this'), icon: 'arrowshape.turn.up.right' },
    ];
    const off = editMenu(key, items, (e) => {
      const { start, end } = sel.current;
      const text = cleanPassage(plain.slice(Math.min(start, end), Math.max(start, end))) || cleanPassage(plain);
      if (text && (e.id === 'quote' || e.id === 'save' || e.id === 'forward')) pickRef.current?.(e.id, text);
    });
    return off ?? undefined;
  }, [key, canPick, plain]);
  return (
    <TextInput testID={key} editable={false} multiline scrollEnabled={false} dataDetectorTypes="link" caretHidden
      onSelectionChange={(e) => { sel.current = e.nativeEvent.selection; }}
      style={[styles.input, { color: t.ink }]}>
      {children}
    </TextInput>
  );
}

/** 这一段里点不了的链接（[文字](网址)）：一排小块，点了在浏览器里开。 */
function LinkRow({ links }: { links: LinkRef[] }) {
  const t = useTheme();
  return (
    <View style={styles.links}>
      {links.map((x) => (
        <Pressable key={x.url} onPress={() => { Linking.openURL(x.url).catch(() => {}); }} accessibilityRole="link" accessibilityLabel={L(`打开链接：${x.text}`, `Open link: ${x.text}`)} hitSlop={4}
          style={({ pressed }) => [styles.link, { backgroundColor: t.surface2, opacity: pressed ? 0.6 : 1 }]}>
          <ArrowUpRight size={13} color={t.cyan} />
          <T v="caption" color={t.cyan} numberOfLines={1} style={{ flexShrink: 1, fontSize: 13, fontWeight: '500' }}>{x.text}</T>
        </Pressable>
      ))}
    </View>
  );
}

/** 能选字的 Markdown（回复正文一段）。memo：文字没变就不重新解析。 */
export const SelectableMarkdown = React.memo(function SelectableMarkdown({ text, onPick }: { text: string; onPick?: (what: PassageAction, text: string) => void }) {
  const list = useMemo(() => pieces(text), [text]);
  return (
    <View style={styles.wrap}>
      {list.map((p, k) => (p.kind === 'md' ? <Markdown key={k} text={p.src} /> : (
        <View key={k}>
          <TextPiece piece={p} onPick={onPick} />
          {p.links.length ? <LinkRow links={p.links} /> : null}
        </View>
      )))}
    </View>
  );
});

const styles = StyleSheet.create({
  wrap: { gap: 6 },
  // 和 Markdown 组件的正文一样大；去掉输入框自带的内边距，左右和上下都贴齐
  input: { ...type.body, padding: 0, paddingTop: 0, paddingBottom: 0, margin: 0, textAlignVertical: 'top' },
  links: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: 6 },
  link: { flexDirection: 'row', alignItems: 'center', gap: 3, borderRadius: radius.sm, paddingHorizontal: 8, paddingVertical: 4, maxWidth: 240 },
});
