// Grava 的回复是 Markdown（粗体、列表、代码、链接、表格），按主题样式渲染，不再露出 ** 和反引号。
// 用原版 react-native-markdown-display（纯 JS）。@ronradtke 那个 fork 依赖 @react-native-vector-icons（原生字体模块），热更新到没有它的包上会启动即崩（2026-09-23 的事故）。
import React, { useMemo } from 'react';
import { Linking, Platform } from 'react-native';
import MarkdownDisplay, { MarkdownIt } from 'react-native-markdown-display';
import { radius, type, useTheme } from '../theme';

const md = MarkdownIt({ typographer: false, linkify: true, breaks: true });
const mono = Platform.select({ ios: 'Menlo', android: 'monospace', default: 'ui-monospace, SFMono-Regular, Menlo, monospace' });

/** compact：段落、列表之间更紧（卡片里的说明）。small：字号降到 callout、表格格子更窄（卡片里的结果预览）。 */
export function Markdown({ text, color, compact, small }: { text: string; color?: string; compact?: boolean; small?: boolean }) {
  const t = useTheme();
  const ink = color ?? t.ink;
  const styles = useMemo(() => ({
    body: { ...(small ? type.callout : type.body), color: ink },
    paragraph: { marginTop: 0, marginBottom: compact ? 4 : 8 },
    text: { color: ink },
    strong: { fontWeight: '700' as const },
    em: { fontStyle: 'italic' as const },
    s: { textDecorationLine: 'line-through' as const },
    heading1: { ...type.title, color: ink, marginTop: 6, marginBottom: 6 },
    heading2: { ...type.title, color: ink, marginTop: 6, marginBottom: 4 },
    heading3: { ...type.headline, color: ink, marginTop: 6, marginBottom: 4 },
    heading4: { ...type.headline, color: ink, marginTop: 4, marginBottom: 2 },
    bullet_list: { marginBottom: compact ? 4 : 8 },
    ordered_list: { marginBottom: compact ? 4 : 8 },
    list_item: { marginBottom: 2, flexDirection: 'row' as const },
    bullet_list_icon: { color: ink, marginLeft: 2, marginRight: 8, ...(small ? type.callout : type.body) },
    ordered_list_icon: { color: ink, marginLeft: 2, marginRight: 8, ...(small ? type.callout : type.body) },
    code_inline: { fontFamily: mono, fontSize: 14, backgroundColor: t.surface2, color: ink, borderRadius: 4, paddingHorizontal: 4, borderWidth: 0 },
    code_block: { fontFamily: mono, fontSize: 13, backgroundColor: t.surface2, color: ink, borderRadius: radius.md, padding: 10, borderWidth: 0, marginBottom: 8 },
    fence: { fontFamily: mono, fontSize: 13, backgroundColor: t.surface2, color: ink, borderRadius: radius.md, padding: 10, borderWidth: 0, marginBottom: 8 },
    blockquote: { backgroundColor: 'transparent', borderLeftWidth: 3, borderLeftColor: t.line, paddingLeft: 10, paddingVertical: 0, marginLeft: 0, marginBottom: 8 },
    link: { color: t.cyan, textDecorationLine: 'underline' as const },
    hr: { backgroundColor: t.line, height: 1, marginVertical: 8 },
    table: { borderWidth: 1, borderColor: t.line, borderRadius: radius.sm, marginBottom: 8 },
    thead: { backgroundColor: t.surface2 },
    th: { padding: small ? 4 : 6, fontWeight: '700' as const },
    tr: { borderBottomWidth: 1, borderColor: t.line, flexDirection: 'row' as const },
    td: { padding: small ? 4 : 6 },
    image: { borderRadius: radius.md },
  }), [t, ink, compact, small]);
  return (
    <MarkdownDisplay style={styles as any} markdownit={md} onLinkPress={(url: string) => { Linking.openURL(url).catch(() => {}); return false; }}>
      {text}
    </MarkdownDisplay>
  );
}
