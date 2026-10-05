// 思考空间几页共用的小件：一条想法的卡、关键词、高亮的搜索结果、主题一行、收藏一行、底上浮着的操作条。
import React from 'react';
import { Image, Platform, Pressable, StyleSheet, Text, TextInput, View, type TextInputProps } from 'react-native';
import { setAudioModeAsync, useAudioPlayer, useAudioPlayerStatus } from 'expo-audio';
import {
  FileAudio as AudioLines, Bookmark, Check, FileText, Hash, ImageIcon, Lightbulb, Link2, MessageCircle, Pencil, Play, Square, TextIcon, X,
} from '../components/icons';
import type { Fragment, FragmentKind, Parts, SaveItem, TopicBrief } from '../api/think';
import { L } from '../i18n';
import { radius, space, type, useTheme, type Theme } from '../theme';
import { T } from '../components/ui';

export const AudioLinesIcon = AudioLines;

/**
 * 多行输入框，随字长高、删字变矮。iOS 的多行输入自己会长；网页上是 textarea（默认两行高、不跟着长），
 * 字一变就先把高度设成 auto 再按 scrollHeight 设回去（style 里的 minHeight / maxHeight 照样管用）。
 */
export const GrowInput = React.forwardRef<TextInput, TextInputProps>(function GrowInput(props, ref) {
  const node = React.useRef<TextInput | null>(null);
  const web = Platform.OS === 'web';
  React.useLayoutEffect(() => {
    const el = node.current as unknown as HTMLTextAreaElement | null;
    if (!web || !el?.style) return;
    el.style.height = 'auto';
    el.style.height = `${el.scrollHeight}px`;
  }, [web, props.value]);
  const setRef = React.useCallback((v: TextInput | null) => {
    node.current = v;
    if (typeof ref === 'function') ref(v);
    else if (ref) ref.current = v;
  }, [ref]);
  return <TextInput {...props} ref={setRef} multiline numberOfLines={web ? 1 : props.numberOfLines} />;
});

/** 一种碎片的图标和颜色。颜色取 Agent 的六种（深浅两套都校验过对比度）。 */
export function kindLook(t: Theme, kind: FragmentKind | string) {
  const neutral = { soft: t.surface2, fg: t.ink2 };
  switch (kind) {
    case 'keywords': return { Icon: Hash, ...t.tints.cyan, label: L('关键词', 'Keywords') };
    case 'voice': return { Icon: AudioLines, ...t.tints.purple, label: L('语音', 'Voice') };
    case 'photo': return { Icon: ImageIcon, ...t.tints.orange, label: L('照片', 'Photo') };
    case 'file': return { Icon: FileText, ...neutral, label: L('文件', 'File') };
    case 'link': return { Icon: Link2, ...t.tints.green, label: L('链接', 'Link') };
    case 'save': return { Icon: Bookmark, ...t.tints.pink, label: L('收藏', 'Saved') };
    case 'long': return { Icon: Pencil, ...t.tints.gold, label: L('长文', 'Long piece') };
    case 'image': return { Icon: ImageIcon, ...t.tints.orange, label: L('图片', 'Image') };
    case 'chat': return { Icon: MessageCircle, ...t.tints.gold, label: L('对话', 'Chat') };
    default: return { Icon: TextIcon, ...neutral, label: '' };
  }
}

export function TypeTile({ kind, size = 22 }: { kind: FragmentKind | string; size?: number }) {
  const t = useTheme();
  const k = kindLook(t, kind);
  return (
    <View style={{ width: size, height: size, borderRadius: size * 0.32, backgroundColor: k.soft, alignItems: 'center', justifyContent: 'center' }}>
      <k.Icon size={Math.round(size * 0.58)} color={k.fg} />
    </View>
  );
}

/** 一个关键词。current：正在看的那个关键词（关键词页里填满）。 */
export function KeywordChip({ k, onPress, big, current }: { k: string; onPress?: () => void; big?: boolean; current?: boolean }) {
  const t = useTheme();
  const c = t.tints.cyan;
  const body = (
    <View style={[styles.kw, big ? styles.kwBig : null, { backgroundColor: current ? c.swatch : c.soft }]}>
      <Text style={[big ? type.headline : type.caption, { color: current ? t.surface : c.fg, fontSize: big ? 15 : 12 }]}>#{k}</Text>
    </View>
  );
  return onPress ? <Pressable onPress={onPress} hitSlop={4} accessibilityRole="button" accessibilityLabel={L(`关键词 ${k}`, `Keyword ${k}`)}>{body}</Pressable> : body;
}

/** 搜索结果：命中的那几个字高亮。 */
export function Highlight({ parts, v = 'callout', color, numberOfLines }: { parts: Parts; v?: keyof typeof type; color?: string; numberOfLines?: number }) {
  const t = useTheme();
  return (
    <T v={v} color={color} numberOfLines={numberOfLines}>
      {parts.map(([s, hit], i) => (hit
        ? <Text key={i} style={{ backgroundColor: t.goldSoft, color: t.ink, fontWeight: '600' }}>{s}</Text>
        : <Text key={i}>{s}</Text>))}
    </T>
  );
}

const pad = (n: number) => String(n).padStart(2, '0');
/** 「今天 · 9/27」「昨天 · 9/26」「9/25 周四」 */
export function dayLabel(day: string) {
  const d = new Date(`${day}T12:00:00`);
  const today = new Date();
  const key = (x: Date) => `${x.getFullYear()}-${pad(x.getMonth() + 1)}-${pad(x.getDate())}`;
  const y = new Date(today); y.setDate(today.getDate() - 1);
  const md = `${d.getMonth() + 1}/${d.getDate()}`;
  if (key(d) === key(today)) return L(`今天 · ${md}`, `Today · ${md}`);
  if (key(d) === key(y)) return L(`昨天 · ${md}`, `Yesterday · ${md}`);
  const wd = L('日一二三四五六'[d.getDay()], ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][d.getDay()]);
  return L(`${md} 周${wd}`, `${wd} ${md}`);
}

/** 不按天分组的地方（关键词页、点开的一条）：「今天 14:40」「9/24 20:41」。 */
export function whenLabel(day: string, time: string) {
  const label = dayLabel(day);
  if (label.includes(' · ')) return `${label.split(' · ')[0]} ${time}`;
  const d = new Date(`${day}T12:00:00`);
  return `${d.getMonth() + 1}/${d.getDate()} ${time}`;
}

/** 字数：汉字按字，英文按词。 */
export function wordCount(text: string) {
  const cjk = (text.match(/[㐀-鿿豈-﫿]/g) ?? []).length;
  const words = (text.replace(/[㐀-鿿豈-﫿]/g, ' ').match(/[A-Za-z0-9']+/g) ?? []).length;
  return cjk + words;
}

const human = (n: number) => (n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);
const mmss = (s: number) => `${Math.floor(s / 60)}:${pad(Math.round(s % 60))}`;

/** 语音：点一下才建播放器（一页上很多条语音也不会一起去下载）。 */
function VoicePlay({ url, duration }: { url: string; duration: number | null }) {
  const t = useTheme();
  const player = useAudioPlayer(null);
  const st = useAudioPlayerStatus(player);
  const [loaded, setLoaded] = React.useState(false);
  const toggle = async () => {
    if (st.playing) { player.pause(); return; }
    await setAudioModeAsync({ playsInSilentMode: true, allowsRecording: false }).catch(() => {});
    if (!loaded) { player.replace(url); setLoaded(true); }
    else if (st.duration && st.currentTime >= st.duration - 0.2) await player.seekTo(0);
    player.play();
  };
  const c = t.tints.purple;
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
      <Pressable onPress={toggle} hitSlop={6} accessibilityRole="button" accessibilityLabel={st.playing ? L('暂停', 'Pause') : L('播放原始录音', 'Play the recording')}
        style={{ width: 34, height: 34, borderRadius: 17, backgroundColor: c.soft, alignItems: 'center', justifyContent: 'center' }}>
        {st.playing ? <Square size={12} color={c.fg} fill={c.fg} /> : <Play size={14} color={c.fg} fill={c.fg} />}
      </Pressable>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 3, height: 24 }}>
        {[8, 14, 20, 12, 18, 24, 16, 10, 22, 14, 8, 18, 12, 20, 10, 6].map((h, i) => (
          <View key={i} style={{ width: 3, height: h, borderRadius: 2, backgroundColor: c.fg, opacity: st.playing && st.duration && i / 16 <= st.currentTime / st.duration ? 0.9 : 0.35 }} />
        ))}
      </View>
      {duration ? <T v="caption" color={t.ink3}>{mmss(st.playing ? st.currentTime : duration)}</T> : null}
    </View>
  );
}

/** 一条想法。selected / onToggle：右上角的圈（勾几条去聊聊、想完了）；onPress：点开看全文、改、删。 */
export function FragmentCard({ f, selected, onToggle, onPress, onKeyword, full, withDay }: {
  f: Fragment; selected?: boolean; onToggle?: () => void; onPress?: () => void; onKeyword?: (k: string) => void; full?: boolean;
  /** 不在按天分组的列表里：时间前面带上哪天 */ withDay?: boolean;
}) {
  const t = useTheme();
  const look = kindLook(t, f.kind);
  const photos = f.files.filter((x) => x.kind === 'photo');
  const others = f.files.filter((x) => x.kind !== 'photo' && x.kind !== 'voice');
  const audio = f.files.find((x) => x.kind === 'voice');
  const lines = full ? undefined : f.kind === 'long' ? 3 : 6;
  const showKw = f.kind !== 'keywords' && f.keywords.length > 0;
  const head = [look.label, f.kind === 'long' && f.chars ? L(`${f.chars} 字`, `${f.chars} chars`) : '', f.duration && f.kind === 'voice' ? mmss(f.duration) : '', withDay || full ? whenLabel(f.day, f.time) : f.time].filter(Boolean).join(' · ');
  return (
    <Pressable onPress={onPress} disabled={!onPress} accessibilityRole={onPress ? 'button' : undefined} accessibilityHint={onPress ? L('打开以查看全文、编辑或删除', 'Open to read, edit or delete') : undefined}
      style={({ pressed }) => [styles.card, { backgroundColor: selected ? t.goldSoft : t.surface, borderColor: selected ? t.goldFill : t.line, borderWidth: selected ? 2 : StyleSheet.hairlineWidth, opacity: pressed ? 0.85 : 1, paddingRight: onToggle ? 46 : space.md }]}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
        <TypeTile kind={f.kind} />
        <T v="caption" color={t.ink3} style={{ fontWeight: '500' }}>{head}</T>
        {f.source === 'obsidian' ? <T v="caption" color={t.ink3}>{L(' · 来自 Obsidian', ' · from Obsidian')}</T> : null}
      </View>
      {f.kind === 'keywords' ? (
        <View style={styles.chips}>{f.keywords.map((k) => <KeywordChip key={k} k={k} big onPress={onKeyword ? () => onKeyword(k) : undefined} />)}</View>
      ) : null}
      {f.kind === 'voice' && audio ? <VoicePlay url={audio.url} duration={f.duration} /> : null}
      {f.title ? <T v="headline" numberOfLines={2}>{f.title}</T> : null}
      {f.kind !== 'keywords' && f.text ? (
        <T v={f.kind === 'voice' || f.kind === 'long' ? 'callout' : 'body'} color={f.kind === 'voice' || f.kind === 'long' ? t.ink2 : t.ink} numberOfLines={lines}>
          {f.kind === 'voice' ? L(`「${f.text}」`, `“${f.text}”`) : f.text}
        </T>
      ) : null}
      {f.url ? <T v="caption" color={t.ink3} numberOfLines={1}>{f.linkTitle ? `${f.linkTitle} · ` : ''}{f.url.replace(/^https?:\/\//, '')}</T> : null}
      {photos.length ? (
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
          {photos.slice(0, 4).map((p) => (
            <Image key={p.url} source={{ uri: `${p.url}${p.url.includes('?') ? '&' : '?'}thumb=1` }} accessibilityLabel={p.name}
              style={{ width: photos.length === 1 ? 160 : 84, height: photos.length === 1 ? 120 : 64, borderRadius: radius.sm, backgroundColor: t.surface2 }} />
          ))}
        </View>
      ) : null}
      {others.map((x) => (
        <View key={x.url} style={[styles.file, { backgroundColor: t.bg }]}>
          <FileText size={18} color={t.cyan} />
          <View style={{ flexShrink: 1 }}>
            <T v="callout" numberOfLines={1} style={{ fontWeight: '600' }}>{x.name}</T>
            <T v="caption" color={t.ink3}>{human(x.size)}</T>
          </View>
        </View>
      ))}
      {showKw ? <View style={styles.chips}>{f.keywords.map((k) => <KeywordChip key={k} k={k} onPress={onKeyword ? () => onKeyword(k) : undefined} />)}</View> : null}
      {onToggle ? (
        <Pressable onPress={onToggle} hitSlop={4} accessibilityRole="checkbox" accessibilityState={{ checked: !!selected }} accessibilityLabel={L('选择此条', 'Select this')} style={styles.check}>
          <View style={[styles.circle, { borderColor: selected ? t.goldFill : t.line, backgroundColor: selected ? t.goldFill : t.surface }]}>
            {selected ? <Check size={13} color={t.onGold} strokeWidth={3} /> : null}
          </View>
        </Pressable>
      ) : null}
    </Pressable>
  );
}

export function TopicRow({ tp, onPress, onLongPress }: { tp: TopicBrief; onPress: () => void; onLongPress?: () => void }) {
  const t = useTheme();
  const g = t.tints.gold;
  const sub = [L(`${tp.count} 条碎片`, `${tp.count} thoughts`), tp.talked ? (tp.lastLine || L('已讨论', 'Talked')) : L('尚未讨论', 'Not talked yet')].join(' · ');
  return (
    <Pressable onPress={onPress} onLongPress={onLongPress} accessibilityHint={onLongPress ? L('长按以重命名或删除主题', 'Long-press to rename or delete the topic') : undefined} accessibilityRole="button" style={({ pressed }) => [styles.row, { backgroundColor: t.surface, opacity: pressed ? 0.8 : 1 }]}>
      <View style={[styles.tile34, { backgroundColor: g.soft }]}><Lightbulb size={17} color={g.fg} /></View>
      <View style={{ flex: 1, gap: 1 }}>
        <T v="headline" numberOfLines={1}>{tp.title}</T>
        <T v="caption" color={t.ink3} numberOfLines={1}>{sub}</T>
      </View>
    </Pressable>
  );
}

const STATUS_TONE: Record<string, 'good' | 'warn' | 'neutral'> = { ok: 'good', fetching: 'neutral', blocked: 'warn', failed: 'warn', empty: 'warn' };
export const saveStatusLabel = (s: SaveItem) => ({
  ok: s.kind === 'link' ? L('正文已保存', 'Text saved') : s.kind === 'file' ? L('文字已提取', 'Text extracted') : '',
  fetching: L('正在保存正文…', 'Saving the text…'), blocked: L('网站拒绝访问', 'Blocked by the site'), failed: L('正文保存失败', 'Text not saved'),
  empty: L('无正文', 'No text'), none: '',
}[s.textStatus] ?? '');

export function SaveRow({ s, onPress, parts }: { s: SaveItem; onPress: () => void; parts?: Parts }) {
  const t = useTheme();
  const status = saveStatusLabel(s);
  const tone = STATUS_TONE[s.textStatus] ?? 'neutral';
  const [bg, fg] = tone === 'good' ? [t.goodSoft, t.good] : tone === 'warn' ? [t.warnSoft, t.warn] : [t.surface2, t.ink2];
  return (
    <Pressable onPress={onPress} accessibilityRole="button" style={({ pressed }) => [styles.row, { alignItems: 'flex-start', backgroundColor: t.surface, opacity: pressed ? 0.8 : 1 }]}>
      {s.kind === 'image' && s.thumbUrl
        ? <Image source={{ uri: s.thumbUrl }} style={{ width: 52, height: 52, borderRadius: radius.sm, backgroundColor: t.surface2 }} accessibilityLabel={s.title || s.name || ''} />
        : <TypeTile kind={s.kind} size={40} />}
      <View style={{ flex: 1, gap: 3 }}>
        <T v="headline" numberOfLines={2} style={{ fontSize: 15 }}>{s.title || s.name || L('（无标题）', '(No title)')}</T>
        {parts ? <Highlight parts={parts} v="caption" color={t.ink2} numberOfLines={2} /> : null}
        <T v="caption" color={t.ink2} numberOfLines={1}>{[s.source, s.time && s.day ? `${dayLabel(s.day).split(' · ')[0]} ${s.time}` : '', s.givenTo ? L(`已交给 ${s.givenTo}`, `Handed to ${s.givenTo}`) : ''].filter(Boolean).join(' · ')}</T>
        {status || s.keywords.length ? (
          <View style={[styles.chips, { marginTop: 2 }]}>
            {status ? <View style={{ backgroundColor: bg, borderRadius: radius.pill, paddingHorizontal: 7, paddingVertical: 2 }}><Text style={[type.caption, { color: fg, fontSize: 11 }]}>{status}</Text></View> : null}
            {s.keywords.slice(0, 3).map((k) => <KeywordChip key={k} k={k} />)}
          </View>
        ) : null}
      </View>
      {!s.seen ? <View style={{ width: 9, height: 9, borderRadius: 5, backgroundColor: t.cyan, marginTop: 6 }} accessibilityLabel={L('未读', 'Unread')} /> : null}
    </Pressable>
  );
}

/** 底上浮着的一条（勾了几条之后）：深色，按钮在右边。 */
export function Floater({ children, bottom }: { children: React.ReactNode; bottom: number }) {
  return <View style={[styles.floater, { bottom }]}>{children}</View>;
}

export function FloatBtn({ label, onPress, primary, icon }: { label: string; onPress: () => void; primary?: boolean; icon?: React.ReactNode }) {
  const t = useTheme();
  return (
    <Pressable onPress={onPress} accessibilityRole="button" style={({ pressed }) => [styles.floatBtn, { backgroundColor: primary ? t.goldFill : '#2B3038', opacity: pressed ? 0.8 : 1 }]}>
      {icon}
      <Text style={[type.headline, { fontSize: 15, color: primary ? t.onGold : '#FFFFFF' }]}>{label}</Text>
    </Pressable>
  );
}

export function FloatClose({ onPress }: { onPress: () => void }) {
  return (
    <Pressable onPress={onPress} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('取消选择', 'Clear selection')} style={{ width: 36, height: 36, alignItems: 'center', justifyContent: 'center' }}>
      <X size={18} color="#C9CED4" />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: { borderRadius: radius.md + 2, padding: space.md, gap: 7, position: 'relative' },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, alignItems: 'center' },
  kw: { borderRadius: radius.pill, paddingHorizontal: 8, paddingVertical: 3 },
  kwBig: { paddingHorizontal: 11, paddingVertical: 5 },
  file: { flexDirection: 'row', alignItems: 'center', gap: 10, borderRadius: radius.sm, paddingHorizontal: 10, paddingVertical: 9 },
  check: { position: 'absolute', top: 0, right: 0, width: 46, height: 46, alignItems: 'center', justifyContent: 'center' },
  circle: { width: 22, height: 22, borderRadius: 11, borderWidth: 2, alignItems: 'center', justifyContent: 'center' },
  row: { flexDirection: 'row', alignItems: 'center', gap: space.md, borderRadius: radius.md + 2, padding: space.md },
  tile34: { width: 34, height: 34, borderRadius: 10, alignItems: 'center', justifyContent: 'center' },
  floater: { position: 'absolute', left: space.md, right: space.md, backgroundColor: '#12151A', borderRadius: radius.lg - 2, paddingLeft: space.lg, paddingRight: 6, paddingVertical: 8, flexDirection: 'row', alignItems: 'center', gap: space.sm, shadowColor: '#000', shadowOpacity: 0.25, shadowRadius: 12, shadowOffset: { width: 0, height: 6 }, elevation: 8 },
  floatBtn: { flexDirection: 'row', alignItems: 'center', gap: 6, height: 40, borderRadius: radius.md, paddingHorizontal: 14 },
});
