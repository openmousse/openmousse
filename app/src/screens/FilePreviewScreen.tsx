// 附件预览（2026-09-30）：对话里点开附件就在 app 里看，不再跳浏览器。
// 同一条消息里的几个附件左右滑（网页版用底下的箭头 / 键盘左右键）。图片全屏、双指放大；PDF / EPUB / SVG 是服务器渲染的页图
// （手机包里没有 WebView）；Word / PPT / Excel / CSV / Markdown 是服务器转好的排版和表格；文字、代码原样；音频能放、带转写；
// 视频和看不了的给「用浏览器打开」。服务器那边见 server/preview.py。
// 参数：items（附件）+ index（先看哪个）；或者 file = 附件 id（逗号分开几个，网页版调试 ?screen=FilePreview&file=<id> 也走这条）。
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, FlatList, Image, Linking, Platform, Pressable, ScrollView, StyleSheet, Text, View, type LayoutChangeEvent, type NativeScrollEvent, type NativeSyntheticEvent } from 'react-native';
import { useNavigation, useRoute } from '@react-navigation/native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import * as Clipboard from 'expo-clipboard';
import { setAudioModeAsync, useAudioPlayer, useAudioPlayerStatus } from 'expo-audio';
import { ArrowUpRight, Check, ChevronLeft, ChevronRight, Copy, FileAudio, FileText, Film, ImageIcon, Paperclip, Pause, Play, X } from '../components/icons';
import { Markdown } from '../components/Markdown';
import { Btn, T } from '../components/ui';
import { fileUrl } from '../api/base';
import { filePreview, isRemote, pageUrl, pageWidth, withParam, type FilePreview, type PreviewTable } from '../api/files';
import type { Attachment } from '../data/types';
import { L } from '../i18n';
import { radius, space, type, useTheme } from '../theme';

const KIND_ICON = { doc: FileText, audio: FileAudio, video: Film, file: Paperclip, image: ImageIcon } as const;
const mono = Platform.select({ ios: 'Menlo', android: 'monospace', default: 'ui-monospace, SFMono-Regular, Menlo, monospace' });
const human = (n: number) => (n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);
/** 上传时浏览器把空格写成了 %20 的名字，显示时还原。 */
const prettyName = (s: string) => { if (!/%[0-9A-Fa-f]{2}/.test(s)) return s; try { return decodeURIComponent(s); } catch { return s; } };
const kindLabel = (k: Attachment['kind']) => ({ image: L('图片', 'Image'), doc: L('文档', 'Document'), audio: L('音频', 'Audio'), video: L('视频', 'Video'), file: L('文件', 'File') }[k] ?? L('文件', 'File'));
/** 「用浏览器打开」：inline=1 让 PDF、图片、音视频在浏览器里直接显示（别的类型服务器照旧给下载）。 */
const openInBrowser = (a: Attachment) => { Linking.openURL(withParam(a.url, 'inline=1')).catch(() => {}); };
type Loaded = FilePreview | { error: string };
/**
 * iOS 的 <Image> 按它在屏幕上的大小解码，放大就发虚。要能放大看清的图：按 k 倍大小排、再缩回原位（transform），
 * 解码就是 k 倍的像素（放大到 k 倍都清楚）；原图没那么大时按原图解，不会更占内存。
 */
const sharpBox = (w: number, h: number, k: number) => (k > 1
  ? { position: 'absolute' as const, left: (-w * (k - 1)) / 2, top: (-h * (k - 1)) / 2, width: w * k, height: h * k, transform: [{ scale: 1 / k }] }
  : { width: w, height: h });
const ZOOM_SHARP = Platform.OS === 'ios' ? 2 : 1;
/** 直接用 <Image> 显示的图。SVG 手机上的 <Image> 画不了，交给服务器渲染成页图（view: pages）。 */
const plainImage = (a: Attachment) => a.kind === 'image' && a.mime !== 'image/svg+xml' && !/\.svg$/i.test(a.name);
const NONE: Attachment[] = [];
const errText = (e: unknown) => {
  const msg = e instanceof Error ? e.message : String(e);
  return msg === 'Not Found' ? L('这台服务器还不支持在 app 里预览，更新服务器以后就能看。', "This server can't preview files in the app yet. Update the server.") : msg;
};

export function FilePreviewScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const route = useRoute<any>();
  const insets = useSafeAreaInsets();
  const ids = typeof route.params?.file === 'string' ? (route.params.file as string) : '';
  const [byId, setById] = useState<Attachment[] | null>(null);
  const items = useMemo(() => (route.params?.items as Attachment[] | undefined) ?? byId ?? NONE, [route.params?.items, byId]);
  const [index, setIndex] = useState<number>(Math.max(0, Number(route.params?.index) || 0));
  const [box, setBox] = useState<{ w: number; h: number } | null>(null);
  const [info, setInfo] = useState<Record<string, Loaded>>({});
  const [copied, setCopied] = useState(false);
  const list = useRef<FlatList<Attachment>>(null);
  const asked = useRef(new Set<string>());   // 同一个文件只取一次
  const a = items[Math.min(index, items.length - 1)];

  // 只给了 id：预览接口回的 file 就是附件本身
  useEffect(() => {
    if (!ids) return undefined;
    let live = true;
    const list0 = ids.split(',').filter(Boolean);
    list0.forEach((id) => asked.current.add(id));
    Promise.all(list0.map((id) => filePreview(id).then((p) => {
      if (live) setInfo((m) => ({ ...m, [id]: p }));
      return p.file ? { ...p.file, url: fileUrl(p.file.url) } : null;
    }, () => null))).then((xs) => { if (live) setById(xs.filter((x): x is Attachment => !!x)); });
    return () => { live = false; };
  }, [ids]);
  // 当前这个和左右两边的先取好，滑过去就有
  useEffect(() => {
    for (const x of [items[index - 1], items[index], items[index + 1]]) {
      if (!x || plainImage(x) || !isRemote(x.url) || asked.current.has(x.id)) continue;
      asked.current.add(x.id);
      filePreview(x.id).then((p) => setInfo((m) => ({ ...m, [x.id]: p })), (e: unknown) => setInfo((m) => ({ ...m, [x.id]: { error: errText(e) } })));
    }
  }, [index, items]);

  const go = useCallback((i: number) => {
    if (i < 0 || i >= items.length) return;
    setIndex(i);
    list.current?.scrollToIndex({ index: i, animated: true });
  }, [items.length]);
  // 网页版：Esc 关，左右键换
  useEffect(() => {
    if (Platform.OS !== 'web' || typeof window === 'undefined') return undefined;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') nav.goBack();
      else if (e.key === 'ArrowLeft') go(index - 1);
      else if (e.key === 'ArrowRight') go(index + 1);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [go, index, nav]);
  // 转屏 / 网页窗口变宽：保持在当前这一个
  useEffect(() => { if (box && items.length > 1) list.current?.scrollToIndex({ index, animated: false }); }, [box?.w]);  // eslint-disable-line react-hooks/exhaustive-deps

  if (!a) {
    return (
      <View style={[styles.center, { flex: 1, backgroundColor: t.bg }]}>
        {ids && !byId ? <ActivityIndicator color={t.ink3} /> : <T v="callout" color={t.ink3}>{L('找不到这个文件。', "Couldn't find this file.")}</T>}
      </View>
    );
  }
  const cur = info[a.id];
  const p = cur && !('error' in cur) ? cur : null;
  const copyText = p?.view === 'text' ? p.text ?? '' : p?.view === 'doc' ? (p.blocks ?? []).map((b) => ('md' in b ? b.md : b.table.rows.map((r) => r.join('\t')).join('\n'))).join('\n\n') : '';
  const sub = [p?.label || kindLabel(a.kind), a.size ? human(a.size) : '', items.length > 1 ? `${index + 1} / ${items.length}` : ''].filter(Boolean).join(' · ');
  const copy = () => { Clipboard.setStringAsync(copyText).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); }).catch(() => {}); };
  const onLayout = (e: LayoutChangeEvent) => { const { width, height } = e.nativeEvent.layout; if (!box || box.w !== width || box.h !== height) setBox({ w: width, h: height }); };
  const onScroll = (e: NativeSyntheticEvent<NativeScrollEvent>) => {
    if (!box) return;
    const i = Math.round(e.nativeEvent.contentOffset.x / box.w);
    if (i !== index && i >= 0 && i < items.length) setIndex(i);
  };

  return (
    <View style={{ flex: 1, backgroundColor: t.bg, paddingTop: insets.top }}>
      <View style={[styles.head, { borderBottomColor: t.line }]}>
        <View style={styles.side}>
          <Pressable onPress={() => nav.goBack()} hitSlop={10} accessibilityRole="button" accessibilityLabel={L('关闭', 'Close')} style={styles.icon}><X size={22} color={t.ink2} /></Pressable>
        </View>
        <View style={{ flex: 1, alignItems: 'center', minWidth: 0 }}>
          <T v="headline" numberOfLines={1} ellipsizeMode="middle" style={{ textAlign: 'center' }}>{prettyName(a.name)}</T>
          <T v="caption" color={t.ink3} numberOfLines={1} style={{ fontWeight: '400', marginTop: 1 }}>{sub}</T>
        </View>
        <View style={[styles.side, { justifyContent: 'flex-end' }]}>
          {copyText ? (
            <Pressable onPress={copy} hitSlop={8} accessibilityRole="button" accessibilityLabel={copied ? L('复制好了', 'Copied') : L('复制文字', 'Copy text')} style={styles.icon}>
              {copied ? <Check size={20} color={t.good} /> : <Copy size={19} color={t.ink2} />}
            </Pressable>
          ) : null}
          {isRemote(a.url) ? (
            <Pressable onPress={() => openInBrowser(a)} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('用浏览器打开', 'Open in the browser')} style={styles.icon}>
              <ArrowUpRight size={21} color={t.ink2} />
            </Pressable>
          ) : null}
        </View>
      </View>
      <View style={{ flex: 1 }} onLayout={onLayout}>
        {box ? (items.length === 1 ? (
          <Page a={a} info={cur} w={box.w} h={box.h} active bottom={insets.bottom} />
        ) : (
          <FlatList ref={list} data={items} horizontal pagingEnabled showsHorizontalScrollIndicator={false} keyExtractor={(x) => x.id}
            initialScrollIndex={index} getItemLayout={(_, i) => ({ length: box.w, offset: box.w * i, index: i })}
            onScroll={onScroll} scrollEventThrottle={32} windowSize={3} initialNumToRender={1} maxToRenderPerBatch={2}
            renderItem={({ item, index: i }) => <Page a={item} info={info[item.id]} w={box.w} h={box.h} active={i === index} bottom={insets.bottom} />} />
        )) : null}
        {items.length > 1 ? (
          <View pointerEvents="box-none" style={[styles.pager, { bottom: insets.bottom + 14 }]}>
            <View style={[styles.pill, { backgroundColor: t.surface, borderColor: t.line }]}>
              <Pressable onPress={() => go(index - 1)} disabled={index === 0} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('上一个', 'Previous')} style={{ opacity: index === 0 ? 0.3 : 1 }}>
                <ChevronLeft size={20} color={t.ink} />
              </Pressable>
              <T v="caption" color={t.ink2} style={{ minWidth: 44, textAlign: 'center' }}>{index + 1} / {items.length}</T>
              <Pressable onPress={() => go(index + 1)} disabled={index === items.length - 1} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('下一个', 'Next')} style={{ opacity: index === items.length - 1 ? 0.3 : 1 }}>
                <ChevronRight size={20} color={t.ink} />
              </Pressable>
            </View>
          </View>
        ) : null}
      </View>
    </View>
  );
}

/** 一个附件占一整屏。 */
function Page({ a, info, w, h, active, bottom }: { a: Attachment; info?: Loaded; w: number; h: number; active: boolean; bottom: number }) {
  const t = useTheme();
  if (plainImage(a)) return <ImagePage a={a} w={w} h={h} />;
  if (!isRemote(a.url)) return <NonePage a={a} note={L('还在上传，传完再点开看。', 'Still uploading. Open it once it has finished.')} w={w} h={h} />;
  if (!info) {
    return <View style={[styles.center, { width: w, height: h }]}><ActivityIndicator color={t.ink3} /></View>;
  }
  if ('error' in info) return <NonePage a={a} note={info.error} w={w} h={h} />;
  switch (info.view) {
    case 'pages': return <PagesPage a={a} p={info} w={w} h={h} bottom={bottom} />;
    case 'doc': return <DocPage p={info} w={w} h={h} bottom={bottom} />;
    case 'text': return <TextPage p={info} w={w} h={h} bottom={bottom} />;
    case 'audio': return <AudioPage a={a} p={info} w={w} h={h} active={active} />;
    case 'video': return <VideoPage a={a} w={w} h={h} />;
    case 'image': return <ImagePage a={a} w={w} h={h} />;
    default: return <NonePage a={a} note={info.note ?? ''} label={info.label} w={w} h={h} />;
  }
}

/** 图片：先铺缩略图（对话里已经取过），大图到了盖上去；iOS 上双指放大。 */
function ImagePage({ a, w, h }: { a: Attachment; w: number; h: number }) {
  const t = useTheme();
  const remote = isRemote(a.url);
  const [state, setState] = useState<'loading' | 'ok' | 'error'>('loading');
  return (
    <ScrollView style={{ width: w, height: h }} contentContainerStyle={{ width: w, height: h }} maximumZoomScale={5} minimumZoomScale={1} bouncesZoom centerContent
      showsHorizontalScrollIndicator={false} showsVerticalScrollIndicator={false}>
      {remote && state !== 'ok' ? <Image source={{ uri: withParam(a.url, 'thumb=1') }} style={StyleSheet.absoluteFill} resizeMode="contain" /> : null}
      <Image source={{ uri: remote ? withParam(a.url, 'preview=1') : a.url }} style={sharpBox(w, h, ZOOM_SHARP)} resizeMode="contain" accessibilityLabel={prettyName(a.name)}
        onLoad={() => setState('ok')} onError={() => setState('error')} />
      {state === 'loading' ? <View pointerEvents="none" style={[StyleSheet.absoluteFill, styles.center]}><ActivityIndicator color={t.ink3} /></View> : null}
      {state === 'error' ? (
        <View style={[StyleSheet.absoluteFill, styles.center, { padding: space.xl }]}>
          <T v="callout" color={t.ink2} style={{ textAlign: 'center' }}>{L('大图没加载出来。', "Couldn't load the full image.")}</T>
        </View>
      ) : null}
    </ScrollView>
  );
}

/** PDF 这类：一页一张服务器渲染的图，只挂屏幕附近的几页；iOS 上双指放大，放大到 1.6 倍以上时屏幕上那几页叠一张 2000px 的清楚的。 */
function PagesPage({ a, p, w, h, bottom }: { a: Attachment; p: FilePreview; w: number; h: number; bottom: number }) {
  const t = useTheme();
  const sizes = useMemo(() => p.pages ?? [], [p.pages]);
  const pad = 12;
  const gap = 10;
  const col = Math.min(w - pad * 2, 880);
  const px = pageWidth(col);
  const heights = useMemo(() => sizes.map(([pw, ph]) => Math.max(40, Math.round((col * ph) / (pw || 1)))), [sizes, col]);
  const tops = useMemo(() => { const out: number[] = []; let y = pad; for (const hh of heights) { out.push(y); y += hh + gap; } return out; }, [heights]);
  const [win, setWin] = useState<[number, number]>([0, Math.min(2, sizes.length - 1)]);
  const [vis, setVis] = useState<[number, number]>([0, 0]);
  const [zoomed, setZoomed] = useState(false);
  const [cur, setCur] = useState(1);
  const onScroll = (e: NativeSyntheticEvent<NativeScrollEvent>) => {
    const { contentOffset, layoutMeasurement, zoomScale } = e.nativeEvent;
    const z = zoomScale || 1;
    const top = contentOffset.y / z;
    const bot = (contentOffset.y + layoutMeasurement.height) / z;
    let first = tops.findIndex((y, i) => y + heights[i] >= top);
    if (first < 0) first = tops.length - 1;
    let last = first;
    while (last + 1 < tops.length && tops[last + 1] <= bot) last += 1;
    const lo = Math.max(0, first - 2);
    const hi = Math.min(tops.length - 1, last + 2);
    setWin((o) => (o[0] === lo && o[1] === hi ? o : [lo, hi]));
    setVis((o) => (o[0] === first && o[1] === last ? o : [first, last]));
    setZoomed(z >= 1.6);
    const mid = (top + bot) / 2;
    const at = tops.findIndex((y, i) => mid >= y && mid < y + heights[i] + gap);
    setCur(at < 0 ? first + 1 : at + 1);
  };
  return (
    <View style={{ width: w, height: h, backgroundColor: t.surface2 }}>
      <ScrollView maximumZoomScale={4} minimumZoomScale={1} bouncesZoom onScroll={onScroll} scrollEventThrottle={48}
        contentContainerStyle={{ paddingTop: pad, paddingBottom: pad + bottom + 56, alignItems: 'center' }}>
        {sizes.map((_, i) => (
          <View key={i} style={[styles.sheet, { width: col, height: heights[i], marginBottom: gap }]}>
            {i >= win[0] && i <= win[1] ? <View style={[StyleSheet.absoluteFill, styles.center]}><ActivityIndicator color="#9AA1A9" /></View> : null}
            {i >= win[0] && i <= win[1] ? (
              <Image source={{ uri: pageUrl(a.id, i + 1, px) }} style={{ width: col, height: heights[i] }} resizeMode="contain"
                accessibilityLabel={L(`第 ${i + 1} 页`, `Page ${i + 1}`)} />
            ) : null}
            {zoomed && ZOOM_SHARP > 1 && i >= vis[0] && i <= vis[1] ? (
              <Image source={{ uri: pageUrl(a.id, i + 1, 2000) }} style={sharpBox(col, heights[i], 2)} resizeMode="contain" />
            ) : null}
          </View>
        ))}
        {(p.pageCount ?? 0) > sizes.length ? (
          <T v="caption" color={t.ink3} style={{ paddingVertical: space.md }}>{L(`只列了前 ${sizes.length} 页，全部 ${p.pageCount} 页请用浏览器打开。`, `Showing the first ${sizes.length} of ${p.pageCount} pages. Open it in the browser for the rest.`)}</T>
        ) : null}
      </ScrollView>
      {sizes.length > 1 ? (
        <View pointerEvents="none" style={[styles.pageNo, { backgroundColor: t.surface, borderColor: t.line }]}>
          <T v="caption" color={t.ink2}>{cur} / {p.pageCount ?? sizes.length}</T>
        </View>
      ) : null}
    </View>
  );
}

/** 读起来舒服的栏宽：手机上是整屏，网页宽屏上居中不超过 760。 */
const readingPad = (w: number) => Math.max(space.lg, (w - 760) / 2);

/** Word / PPT / Excel / Markdown：一块一块排（长文不会一次卡住）。 */
function DocPage({ p, w, h, bottom }: { p: FilePreview; w: number; h: number; bottom: number }) {
  const t = useTheme();
  const blocks = p.blocks ?? [];
  return (
    <FlatList style={{ width: w, height: h }} data={blocks} keyExtractor={(_, i) => String(i)} initialNumToRender={3} windowSize={7}
      contentContainerStyle={{ paddingHorizontal: readingPad(w), paddingTop: space.lg, paddingBottom: bottom + space.xxl + 40 }}
      ItemSeparatorComponent={Gap}
      ListEmptyComponent={<T v="callout" color={t.ink3}>{L('里面没有能显示的内容。', 'Nothing in it to show.')}</T>}
      ListFooterComponent={<Footnote p={p} />}
      renderItem={({ item }) => ('table' in item ? <TableBlock table={item.table} /> : <Markdown text={item.md} />)} />
  );
}

function Gap() { return <View style={{ height: space.sm }} />; }

function Footnote({ p }: { p: FilePreview }) {
  const t = useTheme();
  const text = [p.note, p.truncated ? L('太长了，这里只显示前面一部分；全文请用浏览器打开。', 'Too long to show in full here. Open it in the browser for the rest.') : ''].filter(Boolean).join(' ');
  return text ? <T v="caption" color={t.ink3} style={{ marginTop: space.md, lineHeight: 17 }}>{text}</T> : null;
}

/** 估一格文字多宽（14 号字：西文、数字约 8.6，中文约 14）。 */
const visualLen = (s: string) => { let n = 0; for (const ch of s) n += /[\u2E80-\uFFEF]/.test(ch) ? 1.65 : 1; return n; };

/** 表格：左右滑；每列按内容估宽度（56–240）。 */
function TableBlock({ table }: { table: PreviewTable }) {
  const t = useTheme();
  const rows = table.rows;
  const ncol = rows[0]?.length ?? 0;
  const widths = useMemo(() => Array.from({ length: ncol }, (_, c) => {
    let m = 2;
    for (const r of rows.slice(0, 80)) m = Math.max(m, Math.min(30, visualLen(r[c] ?? '')));
    return Math.max(56, Math.min(240, Math.round(m * 8.6 + 22)));
  }), [rows, ncol]);
  const more = (table.rowsTotal ?? 0) > rows.length || (table.colsTotal ?? 0) > ncol;
  const line = StyleSheet.hairlineWidth;
  return (
    <View style={{ gap: 6 }}>
      <ScrollView horizontal showsHorizontalScrollIndicator={Platform.OS === 'web'} bounces={false}>
        <View style={{ borderWidth: line, borderColor: t.line, borderRadius: radius.sm, overflow: 'hidden' }}>
          {rows.map((r, i) => (
            <View key={i} style={{ flexDirection: 'row', backgroundColor: i === 0 && table.head ? t.surface2 : t.surface }}>
              {r.map((c, j) => (
                <View key={j} style={{ width: widths[j], paddingHorizontal: 8, paddingVertical: 6, borderColor: t.line, borderRightWidth: j < ncol - 1 ? line : 0, borderBottomWidth: i < rows.length - 1 ? line : 0 }}>
                  <Text selectable numberOfLines={6} style={[type.callout, { color: t.ink, fontWeight: i === 0 && table.head ? '600' : '400' }]}>{c}</Text>
                </View>
              ))}
            </View>
          ))}
        </View>
      </ScrollView>
      {more ? (
        <T v="caption" color={t.ink3}>{L(`共 ${table.rowsTotal ?? rows.length} 行 × ${table.colsTotal ?? ncol} 列，这里显示前 ${rows.length} 行 × ${ncol} 列。`, `${table.rowsTotal ?? rows.length} rows × ${table.colsTotal ?? ncol} columns; showing the first ${rows.length} × ${ncol}.`)}</T>
      ) : null}
    </View>
  );
}

/** 按行切成三千字左右一段：很长的文字、代码也能滑得动。 */
function splitText(s: string, size = 3000): string[] {
  const out: string[] = [];
  let i = 0;
  while (i < s.length) {
    let j = Math.min(s.length, i + size);
    if (j < s.length) { const nl = s.lastIndexOf('\n', j); if (nl > i) j = nl + 1; }
    out.push(s.slice(i, j).replace(/\n$/, ''));
    i = j;
  }
  return out.length ? out : [''];
}

function TextPage({ p, w, h, bottom }: { p: FilePreview; w: number; h: number; bottom: number }) {
  const t = useTheme();
  const chunks = useMemo(() => splitText(p.text ?? ''), [p.text]);
  const style = p.mono ? { fontFamily: mono, fontSize: 13, lineHeight: 19 } : type.body;
  return (
    <FlatList style={{ width: w, height: h }} data={chunks} keyExtractor={(_, i) => String(i)} initialNumToRender={2} windowSize={7}
      contentContainerStyle={{ paddingHorizontal: p.mono ? space.md : readingPad(w), paddingTop: space.md, paddingBottom: bottom + space.xxl + 40 }}
      ListFooterComponent={<Footnote p={p} />}
      renderItem={({ item }) => <Text selectable style={[style, { color: t.ink }]}>{item}</Text>} />
  );
}

const clock = (s: number) => { const x = Math.max(0, Math.round(s || 0)); return `${Math.floor(x / 60)}:${String(x % 60).padStart(2, '0')}`; };

/** 音频：点了才去取；滑走就停；下面是上传时的转写。 */
function AudioPage({ a, p, w, h, active }: { a: Attachment; p: FilePreview; w: number; h: number; active: boolean }) {
  const t = useTheme();
  const player = useAudioPlayer(null, { updateInterval: 250 });
  const st = useAudioPlayerStatus(player);
  const [loaded, setLoaded] = useState(false);
  const [track, setTrack] = useState(0);
  useEffect(() => { if (!active && st.playing) player.pause(); }, [active]);  // eslint-disable-line react-hooks/exhaustive-deps
  const toggle = async () => {
    if (st.playing) { player.pause(); return; }
    await setAudioModeAsync({ playsInSilentMode: true, allowsRecording: false }).catch(() => {});
    if (!loaded) { player.replace(a.url); setLoaded(true); } else if (st.duration && st.currentTime >= st.duration - 0.2) await player.seekTo(0);
    player.play();
  };
  const frac = st.duration ? Math.min(1, st.currentTime / st.duration) : 0;
  return (
    <ScrollView style={{ width: w, height: h }} contentContainerStyle={{ paddingHorizontal: readingPad(w), paddingVertical: space.xl, gap: space.lg }}>
      <View style={[styles.player, { backgroundColor: t.surface }]}>
        <Pressable onPress={toggle} accessibilityRole="button" accessibilityLabel={st.playing ? L('暂停', 'Pause') : L('播放', 'Play')}
          style={[styles.play, { backgroundColor: t.cyanSoft }]}>
          {st.playing ? <Pause size={22} color={t.cyan} fill={t.cyan} /> : <Play size={22} color={t.cyan} fill={t.cyan} />}
        </Pressable>
        <View style={{ flex: 1, gap: 8 }}>
          <Pressable onLayout={(e) => setTrack(e.nativeEvent.layout.width)} disabled={!st.duration} accessibilityRole="adjustable" accessibilityLabel={L('进度', 'Progress')}
            onPress={(e) => { if (st.duration && track) player.seekTo((e.nativeEvent.locationX / track) * st.duration); }}
            style={{ height: 24, justifyContent: 'center' }}>
            <View style={{ height: 4, borderRadius: 2, backgroundColor: t.track }}>
              <View style={{ width: `${frac * 100}%`, height: 4, borderRadius: 2, backgroundColor: t.cyan }} />
            </View>
          </Pressable>
          <T v="caption" color={t.ink3}>{loaded && st.duration ? `${clock(st.currentTime)} / ${clock(st.duration)}` : L('点一下开始放', 'Tap to play')}</T>
        </View>
      </View>
      {p.transcript ? (
        <View style={{ gap: 6 }}>
          <T v="label" color={t.ink3} style={{ textTransform: 'uppercase' }}>{L('转写', 'Transcript')}</T>
          <Text selectable style={[type.body, { color: t.ink }]}>{p.transcript}</Text>
        </View>
      ) : null}
    </ScrollView>
  );
}

/** 视频：网页版直接放；手机上这个版本的 app 没有播放器，交给浏览器。 */
function VideoPage({ a, w, h }: { a: Attachment; w: number; h: number }) {
  if (Platform.OS === 'web' && isRemote(a.url)) {
    return (
      <View style={[styles.center, { width: w, height: h, backgroundColor: '#000' }]}>
        {React.createElement('video', { src: withParam(a.url, 'inline=1'), controls: true, playsInline: true, style: { maxWidth: '100%', maxHeight: '100%' } })}
      </View>
    );
  }
  return <NonePage a={a} note={L('这个版本的 app 还不能直接放视频，用浏览器打开就能看。', "This version of the app can't play videos yet. Open it in the browser to watch.")} w={w} h={h} />;
}

/** 看不了的：文件信息 +「用浏览器打开」。 */
function NonePage({ a, note, label, w, h }: { a: Attachment; note: string; label?: string; w: number; h: number }) {
  const t = useTheme();
  const Icon = KIND_ICON[a.kind] ?? Paperclip;
  return (
    <View style={[styles.center, { width: w, height: h, padding: space.xl, gap: space.md }]}>
      <View style={[styles.bigIcon, { backgroundColor: t.surface }]}><Icon size={34} color={t.cyan} /></View>
      <T v="headline" numberOfLines={2} style={{ textAlign: 'center' }}>{prettyName(a.name)}</T>
      <T v="caption" color={t.ink3}>{[label || kindLabel(a.kind), a.size ? human(a.size) : ''].filter(Boolean).join(' · ')}</T>
      {note ? <T v="callout" color={t.ink2} style={{ textAlign: 'center', maxWidth: 420 }}>{note}</T> : null}
      {isRemote(a.url) ? <View style={{ marginTop: space.sm, minWidth: 200 }}><Btn label={L('用浏览器打开', 'Open in the browser')} kind="quiet" icon={<ArrowUpRight size={16} color={t.gold} />} onPress={() => openInBrowser(a)} /></View> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  head: { flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingHorizontal: space.sm, height: 56, borderBottomWidth: StyleSheet.hairlineWidth },
  side: { width: 80, flexDirection: 'row', alignItems: 'center' },
  icon: { width: 38, height: 38, alignItems: 'center', justifyContent: 'center' },
  center: { alignItems: 'center', justifyContent: 'center' },
  pager: { position: 'absolute', left: 0, right: 0, alignItems: 'center' },
  pill: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 10, paddingVertical: 6, borderRadius: radius.pill, borderWidth: StyleSheet.hairlineWidth },
  sheet: { backgroundColor: '#FFFFFF', borderRadius: 2, overflow: 'hidden', shadowColor: '#000', shadowOpacity: 0.12, shadowRadius: 4, shadowOffset: { width: 0, height: 1 }, elevation: 2 },
  pageNo: { position: 'absolute', top: 12, right: 12, paddingHorizontal: 10, paddingVertical: 4, borderRadius: radius.pill, borderWidth: StyleSheet.hairlineWidth },
  player: { flexDirection: 'row', alignItems: 'center', gap: space.md, borderRadius: radius.lg, padding: space.lg },
  play: { width: 52, height: 52, borderRadius: 26, alignItems: 'center', justifyContent: 'center' },
  bigIcon: { width: 72, height: 72, borderRadius: 20, alignItems: 'center', justifyContent: 'center' },
});
