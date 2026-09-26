// 七种积木：数字 stat / 进度 progress / 趋势 chart / 列表 list / 清单 checklist / 文字 text / 按钮 action。
// 数据和文字都是服务器按配置算好、按语言格式化好的，这里只管画和你自己点的改动。不认识的种类显示「要新版 app」。
import React, { useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';
import Svg, { Circle, Polyline } from 'react-native-svg';
import { boardsApi, type Block, type BoardAction, type BoardRow, type ChartPoint, type Tone } from '../../api/boards';
import { L } from '../../i18n';
import { useStore } from '../../store';
import { radius, space, type, useTheme, type Theme } from '../../theme';
import { Bar, Ring, weekdayShort } from '../charts';
import { Camera, Check, ChevronRight, FileText, ImageIcon, Plus, Sparkles } from '../icons';
import { Markdown } from '../Markdown';
import { useSheet } from '../Sheet';
import { Card, Disclosure, Pill, showError, T } from '../ui';
import { pickDocuments, pickMedia } from '../chatInput';
import { useBoard } from './ctx';
import { openForm, openRow } from './RowSheet';

const WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六'];
const MONTHS_EN = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export const toneColors = (t: Theme, tone?: Tone): [string, string] => ({
  good: [t.goodSoft, t.good], warn: [t.warnSoft, t.warn], bad: [t.badSoft, t.bad], cyan: [t.cyanSoft, t.cyan], gold: [t.goldSoft, t.gold],
  neutral: [t.surface2, t.ink2],
} as Record<Tone, [string, string]>)[tone ?? 'neutral'] ?? [t.surface2, t.ink2];

/** 小标题一行：标题（刚加的带「新」）+ 右边一行小字。按钮块没有标题就不画。 */
function BlockHeader({ block }: { block: Block }) {
  const t = useTheme();
  const ctx = useBoard();
  const fresh = !ctx?.readOnly && ctx?.fresh.has(block.id);
  if (!block.title && !fresh) return <View style={{ height: ctx?.readOnly ? space.sm : space.md }} />;
  return (
    <View style={[styles.header, ctx?.readOnly && { marginTop: space.md }]}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, flexShrink: 1 }}>
        {block.title ? <T v="label" color={t.ink3} numberOfLines={1} style={{ textTransform: 'uppercase', flexShrink: 1 }}>{block.title}</T> : null}
        {fresh ? <View style={[styles.newPill, { backgroundColor: t.cyan }]}><Text style={[type.caption, { color: t.surface, fontSize: 10, fontWeight: '700' }]}>{L('新', 'New')}</Text></View> : null}
      </View>
      {block.caption ? <T v="caption" color={t.ink3} numberOfLines={1} style={{ fontWeight: '400', flexShrink: 1, textAlign: 'right' }}>{block.caption}</T> : null}
    </View>
  );
}

export function BlockView({ block }: { block: Block }) {
  const t = useTheme();
  const d = block.data ?? {};
  let body: React.ReactNode;
  if (d.error) body = <Card><T v="callout" color={t.bad}>{L(`这一块出错了：${d.error}`, `This block has a problem: ${d.error}`)}</T></Card>;
  else if (block.type === 'stat') body = <StatBody block={block} />;
  else if (block.type === 'progress') body = <ProgressBody block={block} />;
  else if (block.type === 'chart') body = <ChartBody block={block} />;
  else if (block.type === 'list') body = <ListBody block={block} />;
  else if (block.type === 'checklist') body = <ChecklistBody block={block} />;
  else if (block.type === 'text') body = <Card><Markdown text={d.text || block.data.text || ''} compact /></Card>;
  else if (block.type === 'action') body = <ActionBody block={block} />;
  else body = <Card><T v="callout" color={t.ink2}>{L('这一块要新版 app 才能显示。', 'This block needs a newer version of the app.')}</T></Card>;
  return (
    <View>
      <BlockHeader block={block} />
      {body}
    </View>
  );
}

// —— 数字 ——

function StatBody({ block }: { block: Block }) {
  const t = useTheme();
  const items = block.data.items ?? [];
  const cols = items.length === 4 ? 2 : Math.max(1, items.length);
  const big = cols === 1 ? 32 : cols === 2 ? 26 : 22;
  return (
    <Card style={{ flexDirection: 'row', flexWrap: 'wrap', rowGap: 14 }}>
      {items.map((it, i) => (
        <View key={`${i}-${it.label}`} style={{ width: `${100 / cols}%`, gap: 2, paddingRight: space.sm }} accessible accessibilityLabel={`${it.label} ${it.text}${it.deltaText ? `，${it.deltaText}` : ''}`}>
          <T v="largeTitle" numberOfLines={1} adjustsFontSizeToFit style={{ fontSize: big, fontWeight: '800', letterSpacing: -0.3, fontVariant: ['tabular-nums'] }}>{it.text}</T>
          <T v="caption" color={t.ink2} style={{ fontWeight: '400' }}>{it.label}</T>
          {it.deltaText ? <T v="caption" color={toneColors(t, it.tone)[1]} style={{ fontWeight: '600' }}>{it.deltaText}</T>
            : it.sub ? <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{it.sub}</T> : null}
        </View>
      ))}
    </Card>
  );
}

// —— 进度 ——

function ProgressBody({ block }: { block: Block }) {
  const t = useTheme();
  const d = block.data;
  const ratio = d.ratio ?? 0;
  const pct = Math.round(ratio * 100);
  const left = d.leftText ? (d.over ? L(`超了 ${d.leftText}`, `${d.leftText} over`) : L(`还剩 ${d.leftText}`, `${d.leftText} left`)) : '';
  if (block.style === 'bar') {
    return (
      <Card style={{ gap: 10 }}>
        <View style={{ flexDirection: 'row', alignItems: 'baseline', gap: 6 }}>
          <T v="headline" style={{ fontSize: 20, fontWeight: '800', fontVariant: ['tabular-nums'] }}>{d.text}</T>
          <T v="callout" color={t.ink2}>/ {d.targetText}</T>
          <View style={{ flex: 1 }} />
          {left ? <T v="caption" color={d.over ? t.warn : t.ink3} style={{ fontWeight: d.over ? '600' : '400' }}>{left}</T> : null}
        </View>
        <Bar value={d.value ?? 0} target={d.target ?? 0} height={10} />
      </Card>
    );
  }
  return (
    <Card style={{ flexDirection: 'row', alignItems: 'center', gap: 14 }}>
      <Ring size={64} stroke={7} value={d.value ?? 0} target={d.target ?? 0} color={d.over ? t.warn : t.chartA}>
        <T v="headline" style={{ fontSize: 15, fontWeight: '800', fontVariant: ['tabular-nums'] }}>{`${pct}%`}</T>
      </Ring>
      <View style={{ flex: 1, gap: 4 }}>
        <T v="headline" style={{ fontSize: 20, fontWeight: '800', fontVariant: ['tabular-nums'] }}>{d.text}<T v="callout" color={t.ink2} style={{ fontSize: 15 }}>{` / ${d.targetText}`}</T></T>
        {left ? <T v="callout" color={d.over ? t.warn : t.ink2} style={{ fontSize: 13 }}>{left}</T> : null}
      </View>
    </Card>
  );
}

// —— 趋势 ——

function pointLabel(p: ChartPoint, by: string, n: number): string {
  const d = new Date(`${p.key}T12:00:00`);
  if (p.current) return by === 'week' ? L('这周', 'This wk') : by === 'month' ? L('本月', 'This mo') : L('今天', 'Today');
  if (by === 'month') return L(`${d.getMonth() + 1}月`, MONTHS_EN[d.getMonth()]);
  if (by === 'day' && n <= 14) return weekdayShort(WEEKDAYS[d.getDay()]);
  return L(`${d.getMonth() + 1}/${d.getDate()}`, `${d.getDate()} ${MONTHS_EN[d.getMonth()]}`);
}

function ChartBody({ block }: { block: Block }) {
  const t = useTheme();
  const pts = block.data.points ?? [];
  const by = block.data.by ?? 'day';
  const vals = pts.map((p) => p.value).filter((v): v is number => v != null);
  const H = 64;
  const gap = pts.length > 20 ? 2 : 5;
  const every = pts.length > 10 ? Math.ceil(pts.length / 7) : 1;  // 标签太挤就隔几个标一个
  const [w, setW] = useState(0);
  // 柱子从 0 画；折线（体重这类）按数据自己的高低画，上下留一点，没数据的那几段跳过不连
  const lo = block.chart === 'line' ? Math.min(...vals) : 0;
  const hi = Math.max(...vals, 0);
  const span = block.chart === 'line' ? (hi - lo || Math.abs(hi) || 1) : (hi || 1);
  const yOf = (v: number) => (block.chart === 'line' ? 6 + (1 - (v - lo) / span) * (H - 12) : H - (v / span) * H);
  const xOf = (i: number) => (pts.length > 1 ? (i / (pts.length - 1)) * (w - 12) + 6 : w / 2);
  const line = pts.map((p, i) => (p.value != null ? `${xOf(i)},${yOf(p.value)}` : null)).filter(Boolean).join(' ');
  return (
    <Card style={{ gap: 10 }}>
      {block.chart === 'line' ? (
        <View style={{ height: H }} onLayout={(e) => setW(e.nativeEvent.layout.width)}>
          {w > 0 && vals.length ? (
            <Svg width={w} height={H}>
              {vals.length > 1 ? <Polyline points={line} fill="none" stroke={t.chartA} strokeWidth={2.5} strokeLinejoin="round" strokeLinecap="round" /> : null}
              {pts.map((p, i) => (p.value != null ? <Circle key={p.key} cx={xOf(i)} cy={yOf(p.value)} r={p.current ? 4.5 : 3} fill={p.current ? t.cyan : t.chartA} /> : null))}
            </Svg>
          ) : null}
        </View>
      ) : (
        <View style={{ flexDirection: 'row', alignItems: 'flex-end', height: H, gap }}>
          {pts.map((p) => {
            const h = p.value ? Math.max(3, Math.round((p.value / span) * H)) : 2;
            return (
              <View key={p.key} style={{ flex: 1, height: h, borderRadius: pts.length > 20 ? 2 : 4, backgroundColor: p.value ? (p.current ? t.cyan : t.chartA) : t.track }}
                accessible accessibilityLabel={`${pointLabel(p, by, pts.length)} ${p.text || '0'}`} />
            );
          })}
        </View>
      )}
      <View style={{ flexDirection: 'row', gap }}>
        {pts.map((p, i) => (
          <Text key={p.key} numberOfLines={1} style={[type.caption, { flex: 1, fontSize: 10, textAlign: 'center', color: p.current ? t.ink : t.ink3, fontWeight: p.current ? '700' : '500' }]}>
            {i % every === 0 || p.current ? pointLabel(p, by, pts.length) : ''}
          </Text>
        ))}
      </View>
      {block.data.summary ? <T v="callout" color={t.ink2}>{block.data.summary}</T> : null}
    </Card>
  );
}

// —— 列表 ——

function RowLine({ row, first, onPress, disabled }: { row: BoardRow; first: boolean; onPress?: () => void; disabled?: boolean }) {
  const t = useTheme();
  const [bg, fg] = toneColors(t, row.badge?.tone);
  const body = (
    <View style={[styles.row, !first && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }]}>
      <View style={{ flex: 1, gap: 2 }}>
        <T v="body" numberOfLines={2} style={{ fontSize: 15, fontWeight: '600' }}>{row.title}</T>
        {row.sub ? <T v="callout" color={t.ink2} numberOfLines={1} style={{ fontSize: 13 }}>{row.sub}</T> : null}
      </View>
      {row.badge ? <Pill label={row.badge.text} colors={[bg, fg]} /> : null}
      {row.right && !row.badge ? <T v="callout" color={t.ink2} numberOfLines={1} style={{ maxWidth: '45%', fontVariant: ['tabular-nums'] }}>{row.right}</T> : null}
      {row.right && row.badge ? <T v="caption" color={t.ink3} numberOfLines={1} style={{ fontWeight: '400' }}>{row.right}</T> : null}
    </View>
  );
  return onPress ? (
    <Pressable onPress={onPress} disabled={disabled} accessibilityRole="button" style={({ pressed }) => ({ opacity: pressed ? 0.6 : 1 })}>{body}</Pressable>
  ) : body;
}

function ListBody({ block }: { block: Block }) {
  const t = useTheme();
  const ctx = useBoard();
  const sheet = useSheet();
  const d = block.data;
  const [group, setGroup] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const all = d.rows ?? [];
  const rows = group == null || !block.group ? all : all.filter((r) => String(r.data[block.group as string] ?? '') === group);
  const canOpen = !ctx?.readOnly && (block.edit !== false || !!block.rowActions?.length);
  const tap = (r: BoardRow) => (canOpen && ctx ? () => openRow(sheet, ctx, block, r) : undefined);
  if (!all.length) return <Card><T v="callout" color={t.ink2}>{block.empty || L('还没有记录。', 'Nothing here yet.')}</T></Card>;
  if (block.style === 'chips') {
    return (
      <View style={styles.chips}>
        {rows.map((r) => (
          <Pressable key={r.id} onPress={tap(r)} disabled={!canOpen} style={[styles.chip, { backgroundColor: t.surface, borderColor: t.line }]}>
            <T v="caption" style={{ fontSize: 13, fontWeight: '600' }}>{r.sub ? `${r.title} · ${r.sub}` : r.title}</T>
          </Pressable>
        ))}
      </View>
    );
  }
  const limit = block.limit && !open ? block.limit : rows.length;
  const hiddenN = rows.length - Math.min(limit, rows.length);
  const more = (d.total ?? all.length) - all.length;  // 服务器只给了前 100 行
  return (
    <Card style={{ paddingVertical: block.group && d.groups?.length ? space.md : 2, gap: 0 }}>
      {block.group && d.groups && d.groups.length > 1 ? (
        <View style={[styles.chips, { paddingBottom: 6 }]}>
          <GroupChip label={L(`全部 ${all.length}`, `All ${all.length}`)} on={group == null} onPress={() => setGroup(null)} />
          {d.groups.map((g) => <GroupChip key={g.key} label={`${g.key || L('其他', 'Other')} ${g.count}`} on={group === g.key} onPress={() => setGroup(group === g.key ? null : g.key)} />)}
        </View>
      ) : null}
      {rows.slice(0, limit).map((r, i) => <RowLine key={r.id} row={r} first={i === 0 && !(block.group && d.groups && d.groups.length > 1)} onPress={tap(r)} />)}
      {hiddenN > 0 || (open && block.limit && rows.length > block.limit) ? (
        <Pressable onPress={() => setOpen((v) => !v)} accessibilityRole="button" accessibilityState={{ expanded: open }}
          style={({ pressed }) => [styles.row, { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line, opacity: pressed ? 0.6 : 1 }]}>
          <T v="callout" style={{ flex: 1, fontWeight: '600' }}>{open ? L('收起', 'Show less') : L(`还有 ${hiddenN} 条`, `${hiddenN} more`)}</T>
          <Disclosure open={open} />
        </Pressable>
      ) : null}
      {more > 0 && (open || !block.limit) ? <T v="caption" color={t.ink3} style={{ fontWeight: '400', paddingVertical: 8 }}>{L(`只显示了前 ${all.length} 条，一共 ${d.total} 条`, `Showing the first ${all.length} of ${d.total}`)}</T> : null}
    </Card>
  );
}

function GroupChip({ label, on, onPress }: { label: string; on: boolean; onPress: () => void }) {
  const t = useTheme();
  return (
    <Pressable onPress={onPress} accessibilityRole="button" accessibilityState={{ selected: on }}
      style={[styles.groupChip, { backgroundColor: on ? t.ink : t.bg }]}>
      <Text style={[type.caption, { fontSize: 13, fontWeight: '600', color: on ? t.surface : t.ink2 }]}>{label}</Text>
    </Pressable>
  );
}

// —— 清单 ——

function ChecklistBody({ block }: { block: Block }) {
  const t = useTheme();
  const ctx = useBoard();
  const sheet = useSheet();
  const rows = block.data.rows ?? [];
  const [local, setLocal] = useState<Record<string, boolean>>({});  // 点了还没回来的
  const [showDone, setShowDone] = useState(false);
  const isOn = (r: BoardRow) => local[r.id] ?? !!r.checked;
  const toggle = (r: BoardRow) => {
    if (!ctx || ctx.readOnly || !block.check) return;
    const next = !isOn(r);
    setLocal((m) => ({ ...m, [r.id]: next }));
    boardsApi.patchRow(r.id, { [block.check]: next })
      .then(() => ctx.reload())
      .catch((e) => { setLocal((m) => { const n = { ...m }; delete n[r.id]; return n; }); showError(L('没记上', "Couldn't save"), e); })
      .finally(() => setLocal((m) => { const n = { ...m }; delete n[r.id]; return n; }));
  };
  if (!rows.length) return <Card><T v="callout" color={t.ink2}>{block.empty || L('清单是空的。', 'The list is empty.')}</T></Card>;
  const todo = rows.filter((r) => !isOn(r));
  const done = rows.filter(isOn);
  const line = (r: BoardRow, i: number) => {
    const on = isOn(r);
    return (
      <View key={r.id} style={[styles.row, i > 0 && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }]}>
        <Pressable onPress={() => toggle(r)} hitSlop={8} accessibilityRole="checkbox" accessibilityState={{ checked: on }} accessibilityLabel={r.title}
          style={[styles.check, { borderColor: on ? t.cyan : t.ink3, backgroundColor: on ? t.cyan : 'transparent' }]}>
          {on ? <Check size={15} color={t.surface} strokeWidth={3} /> : null}
        </Pressable>
        <Pressable style={{ flex: 1, gap: 2 }} onPress={ctx && !ctx.readOnly && block.edit !== false ? () => openRow(sheet, ctx, block, r) : undefined}>
          <T v="body" style={{ fontSize: 15, fontWeight: on ? '400' : '600', color: on ? t.ink3 : t.ink, textDecorationLine: on ? 'line-through' : 'none' }}>{r.title}</T>
          {r.sub ? <T v="callout" color={t.ink3} numberOfLines={1} style={{ fontSize: 13 }}>{r.sub}</T> : null}
        </Pressable>
      </View>
    );
  };
  return (
    <Card style={{ paddingVertical: 2, gap: 0 }}>
      {todo.map(line)}
      {done.length ? (
        <>
          <Pressable onPress={() => setShowDone((v) => !v)} accessibilityRole="button" accessibilityState={{ expanded: showDone }}
            style={({ pressed }) => [styles.row, todo.length > 0 && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }, { opacity: pressed ? 0.6 : 1 }]}>
            <T v="callout" color={t.ink2} style={{ flex: 1 }}>{L(`已完成 ${done.length} 项`, `${done.length} done`)}</T>
            <Disclosure open={showDone} />
          </Pressable>
          {showDone ? done.map((r, i) => line(r, i + 1)) : null}
        </>
      ) : null}
    </Card>
  );
}

// —— 按钮 ——

function ActionBody({ block }: { block: Block }) {
  const t = useTheme();
  const ctx = useBoard();
  const sheet = useSheet();
  const { send, typing } = useStore();
  const [busy, setBusy] = useState<number | null>(null);
  const acts = block.actions ?? [];
  if (!ctx) return null;
  const agentBusy = !!typing[ctx.agent];
  const sendFiles = async (a: BoardAction, which: 'camera' | 'photos' | 'files') => {
    const files = which === 'files' ? await pickDocuments() : await pickMedia(which === 'camera');
    if (!files.length) return;
    send(ctx.agent, a.message || '', files);
    ctx.onChat();
  };
  const run = (a: BoardAction, i: number) => {
    if (ctx.readOnly) return;
    if (a.kind === 'ask') { send(ctx.agent, a.message || a.label); ctx.onChat(); return; }
    if (a.kind === 'form') {
      const fields = block.data.forms?.[a.collection ?? ''];
      if (fields && a.collection) openForm(sheet, ctx, a.collection, a.label, fields, a.defaults);
      return;
    }
    const accept: ('camera' | 'photos' | 'files')[] = a.accept?.length ? a.accept : ['camera', 'photos', 'files'];
    const go = (which: 'camera' | 'photos' | 'files') => {
      setBusy(i);
      sendFiles(a, which).catch((e) => showError(L('没传上去', "Couldn't upload"), e)).finally(() => setBusy(null));
    };
    if (accept.length === 1) { go(accept[0]); return; }
    sheet.open({
      title: a.label,
      content: (close) => (
        <View style={{ gap: space.sm }}>
          {accept.includes('camera') ? <PickRow icon={Camera} label={L('拍照', 'Take a photo')} onPress={() => { close(); go('camera'); }} /> : null}
          {accept.includes('photos') ? <PickRow icon={ImageIcon} label={L('从相册选', 'Choose from Photos')} onPress={() => { close(); go('photos'); }} /> : null}
          {accept.includes('files') ? <PickRow icon={FileText} label={L('选文件', 'Choose a file')} onPress={() => { close(); go('files'); }} /> : null}
        </View>
      ),
    });
  };
  const icon = (a: BoardAction, color: string) => (a.kind === 'upload' ? <Camera size={18} color={color} /> : a.kind === 'form' ? <Plus size={18} color={color} /> : <Sparkles size={16} color={color} />);
  return (
    <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm }}>
      {acts.map((a, i) => {
        const primary = !!a.primary;
        const fg = primary ? t.onGold : t.ink;
        const disabled = ctx.readOnly || busy != null || (a.kind !== 'form' && agentBusy);
        return (
          <Pressable key={`${i}-${a.label}`} onPress={() => run(a, i)} disabled={disabled} accessibilityRole="button" accessibilityLabel={a.label}
            style={({ pressed }) => [styles.btn, { backgroundColor: primary ? t.goldFill : t.surface2, flexGrow: primary ? 2 : 1, opacity: disabled && busy !== i ? 0.5 : pressed ? 0.75 : 1 }]}>
            {busy === i ? <ActivityIndicator size="small" color={fg} /> : icon(a, fg)}
            <Text numberOfLines={1} style={[type.headline, { fontSize: 15, color: fg, flexShrink: 1 }]}>{a.label}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

function PickRow({ icon: Icon, label, onPress }: { icon: typeof Camera; label: string; onPress: () => void }) {
  const t = useTheme();
  return (
    <Pressable onPress={onPress} accessibilityRole="button" style={({ pressed }) => [styles.pick, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
      <Icon size={20} color={t.ink} />
      <T v="headline" style={{ flex: 1 }}>{label}</T>
      <ChevronRight size={18} color={t.ink3} />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: space.sm, paddingHorizontal: space.xs, marginTop: space.xl, marginBottom: space.sm },
  newPill: { borderRadius: radius.pill, paddingHorizontal: 6, height: 16, alignItems: 'center', justifyContent: 'center' },
  row: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: 11 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: space.sm },
  chip: { minHeight: 28, borderRadius: 14, borderWidth: StyleSheet.hairlineWidth, paddingHorizontal: 11, paddingVertical: 4, justifyContent: 'center' },
  groupChip: { height: 30, borderRadius: 15, paddingHorizontal: 12, alignItems: 'center', justifyContent: 'center' },
  check: { width: 24, height: 24, borderRadius: 7, borderWidth: 2, alignItems: 'center', justifyContent: 'center' },
  btn: { flexDirection: 'row', gap: 6, alignItems: 'center', justifyContent: 'center', borderRadius: 12, paddingHorizontal: 16, height: 44, minWidth: 120 },
  pick: { flexDirection: 'row', alignItems: 'center', gap: space.md, borderRadius: radius.md, paddingHorizontal: space.lg, height: 52 },
});
