// 一个 Agent 的积木看板：读数据、按 after 排好每块放在哪（内置看板的某一节后面、另一块后面，或者最后），顶上的撤回条。
// 内置看板（健身、饮食……）在小节之间放 <Slot at="diet.next" />；没有内置看板的 Agent 整页是 <AllBlocks />。
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import { boardsApi, type Block, type Board } from '../../api/boards';
import { L } from '../../i18n';
import { radius, space, useTheme } from '../../theme';
import { ArrowUp, ChevronRight, Clock, Eye, EyeOff, MessageCircle, Sparkles, Trash2 } from '../icons';
import { setChatDraft } from '../chatInput';
import { useSheet } from '../Sheet';
import { showError, T } from '../ui';
import { BlockView } from './Blocks';
import { Ctx, useBoard } from './ctx';

export { useBoard };

/** 位置 → 块。after 指向不存在的位置（比如内置看板换了）或者绕成圈的，都放到最后，不会丢。 */
export function layoutOf(blocks: Block[], anchors: string[]): Map<string, Block[]> {
  const shown = blocks.filter((b) => !b.hidden);
  const ids = new Set(shown.map((b) => b.id));
  const kids = new Map<string, Block[]>();
  for (const b of shown) {
    const key = b.after && (anchors.includes(b.after) || ids.has(b.after)) && b.after !== b.id ? b.after : '';
    kids.set(key, [...(kids.get(key) ?? []), b]);
  }
  const seen = new Set<string>();
  const chain = (key: string): Block[] => (kids.get(key) ?? []).flatMap((b) => {
    if (seen.has(b.id)) return [];
    seen.add(b.id);
    return [b, ...chain(b.id)];
  });
  const out = new Map<string, Block[]>();
  for (const a of ['top', ...anchors.filter((x) => x !== 'top'), '']) out.set(a, chain(a));
  const lost = shown.filter((b) => !seen.has(b.id));
  if (lost.length) out.set('', [...(out.get('') ?? []), ...lost]);
  return out;
}

/** 读一个 Agent 的看板。active：看板 tab 在屏幕上时才读，切过来时重读一次。 */
export function useAgentBoard(agent: string, active: boolean) {
  const [board, setBoard] = useState<Board | null>(null);
  const [error, setError] = useState<string | null>(null);
  const gen = useRef(0);
  // 只有最后一次读的结果算数（切 Agent、连着下拉时，先发的请求后回来不会盖掉新的）
  const fetchBoard = useCallback((): Promise<void> => {
    const my = ++gen.current;
    return boardsApi.get(agent).then(
      (b) => { if (my === gen.current) { setBoard(b); setError(null); } },
      (e) => {
        // 老服务器没有这个接口：当作没有积木，不显示错误
        const msg = e instanceof Error ? e.message : String(e);
        if (my === gen.current) setError(/HTTP 404|Not Found/i.test(msg) ? null : msg);
      });
  }, [agent]);
  useEffect(() => {
    if (!active) return;
    fetchBoard();
  }, [active, fetchBoard]);
  return { board, error, reload: fetchBoard };
}

/** 按显示顺序分好的几段（最前面 / 每个内置小节后面 / 最后）。没有内置看板的只有一段。 */
function slotsOf(board: Board): { keys: string[]; lists: Map<string, Block[]> } {
  const layout = layoutOf(board.blocks, board.anchors);
  if (board.dashboard === 'none' || !board.anchors.some((a) => a !== 'top')) {
    return { keys: [''], lists: new Map([['', [...(layout.get('top') ?? []), ...[...layout.entries()].filter(([k]) => k !== 'top').flatMap(([, v]) => v)]]]) };
  }
  const keys = ['top', ...board.anchors.filter((a) => a !== 'top'), ''];
  return { keys, lists: new Map(keys.map((k) => [k, [...(layout.get(k) ?? [])]])) };
}

/** 段 → 配置：每块的 after 就写它所在的段（「接在另一块后面」这种链条摊平），藏起来的原样留在最后。 */
function blocksFrom(board: Board, keys: string[], lists: Map<string, Block[]>): Block[] {
  const shown = keys.flatMap((k) => (lists.get(k) ?? []).map((b) => ({ ...b, after: k || undefined })));
  return [...shown, ...board.blocks.filter((b) => b.hidden)];
}

/** 挪一格：段里换位置；已经在段头 / 段尾就跨过一个内置小节，挪到上一段末尾 / 下一段开头。 */
export function moved(board: Board, id: string, dir: -1 | 1): Block[] | null {
  const { keys, lists } = slotsOf(board);
  const si = keys.findIndex((k) => (lists.get(k) ?? []).some((b) => b.id === id));
  if (si < 0) return null;
  const list = lists.get(keys[si]) as Block[];
  const i = list.findIndex((b) => b.id === id);
  const j = i + dir;
  if (j >= 0 && j < list.length) {
    [list[i], list[j]] = [list[j], list[i]];
  } else {
    const ti = si + dir;
    if (ti < 0 || ti >= keys.length) return null;
    const [b] = list.splice(i, 1);
    const target = lists.get(keys[ti]) as Block[];
    if (dir < 0) target.push(b); else target.unshift(b);
  }
  return blocksFrom(board, keys, lists);
}

export function BoardProvider({ agent, board, error, reload, onChat, readOnly = false, children }: {
  agent: string; board: Board | null; error?: string | null; reload: () => Promise<void>; onChat: () => void; readOnly?: boolean; children: React.ReactNode;
}) {
  const sheet = useSheet();
  const layout = useMemo(() => layoutOf(board?.blocks ?? [], board?.anchors ?? []), [board]);
  const fresh = useMemo(() => new Set(board?.strip?.added ?? []), [board]);
  const openMenu = useCallback((block: Block) => {
    if (!board || readOnly) return;
    const save = (blocks: Block[] | null, note: string) => {
      if (!blocks) return;
      boardsApi.put(agent, blocks, note).then(() => reload()).catch((e) => showError(L('没改成', "Couldn't change the board"), e));
    };
    const name = block.title || block.actions?.map((a) => a.label).join(' / ') || block.id;
    sheet.open({
      title: name,
      content: (close) => <BlockMenu board={board} block={block} close={close} onSave={(b, note) => { close(); save(b, note); }}
        onAsk={() => { setChatDraft(agent, L(`看板上「${name}」这一块：`, `About the "${name}" block on the board: `)); close(); onChat(); }} />,
    });
  }, [agent, board, readOnly, reload, sheet, onChat]);
  const value = useMemo(() => ({ agent, board, error: error ?? null, reload, layout, fresh, readOnly, onChat, openMenu }), [agent, board, error, reload, layout, fresh, readOnly, onChat, openMenu]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

function BlockMenu({ board, block, close, onSave, onAsk }: { board: Board; block: Block; close: () => void; onSave: (blocks: Block[] | null, note: string) => void; onAsk: () => void }) {
  const t = useTheme();
  const [sure, setSure] = useState(false);
  const up = moved(board, block.id, -1);
  const down = moved(board, block.id, 1);
  const name = block.title || block.id;
  const Row = MenuRow;
  return (
    <View style={{ gap: space.sm }}>
      <Row icon={<ArrowUp size={20} color={t.ink2} />} label={L('挪到上面', 'Move up')} disabled={!up} onPress={() => onSave(up, L(`把「${name}」往上挪了`, `Moved "${name}" up`))} />
      <Row icon={<View style={{ transform: [{ rotate: '180deg' }] }}><ArrowUp size={20} color={t.ink2} /></View>} label={L('挪到下面', 'Move down')} disabled={!down}
        onPress={() => onSave(down, L(`把「${name}」往下挪了`, `Moved "${name}" down`))} />
      <Row icon={<EyeOff size={20} color={t.ink2} />} label={L('先藏起来', 'Hide for now')} sub={L('看板最底下「藏起来的」里能放回来', 'Bring it back from "Hidden" at the bottom of the board')}
        onPress={() => onSave(board.blocks.map((b) => (b.id === block.id ? { ...b, hidden: true } : b)), L(`藏起了「${name}」`, `Hid "${name}"`))} />
      <Row icon={<MessageCircle size={20} color={t.gold} />} label={L('让它改这一块', 'Ask it to change this')} sub={L('比如「只看 2 天内到期的」', 'e.g. "only show what expires within 2 days"')} onPress={onAsk} />
      <Row icon={<Trash2 size={20} color={t.bad} />} color={t.bad} label={sure ? L('确定删掉这一块', 'Delete this block') : L('删掉这一块', 'Delete this block')}
        sub={L('只删这一块，数据还在；改动记录里能回到删之前', 'Only the block goes; the data stays, and the board history can bring it back')}
        onPress={() => (sure ? onSave(board.blocks.filter((b) => b.id !== block.id), L(`删了「${name}」`, `Deleted "${name}"`)) : setSure(true))} />
      <Pressable onPress={close} accessibilityRole="button" style={({ pressed }) => [styles.menuRow, { justifyContent: 'center', opacity: pressed ? 0.6 : 1 }]}>
        <T v="headline" color={t.ink2}>{L('取消', 'Cancel')}</T>
      </Pressable>
    </View>
  );
}

function MenuRow({ icon, label, sub, color, onPress, disabled }: { icon: React.ReactNode; label: string; sub?: string; color?: string; onPress: () => void; disabled?: boolean }) {
  const t = useTheme();
  return (
    <Pressable onPress={onPress} disabled={disabled} accessibilityRole="button" style={({ pressed }) => [styles.menuRow, { backgroundColor: t.surface, opacity: disabled ? 0.4 : pressed ? 0.7 : 1 }]}>
      {icon}
      <View style={{ flex: 1, gap: 2 }}>
        <T v="headline" color={color}>{label}</T>
        {sub ? <T v="caption" color={t.ink3} style={{ fontWeight: '400', fontSize: 13 }}>{sub}</T> : null}
      </View>
    </Pressable>
  );
}

/** 看板最底下：藏起来的块（能放回来）、改动记录。 */
export function BoardFooter({ onHistory }: { onHistory: () => void }) {
  const t = useTheme();
  const ctx = useBoard();
  const sheet = useSheet();
  if (!ctx?.board || ctx.readOnly) return null;
  const { board, agent, reload } = ctx;
  const hidden = board.blocks.filter((b) => b.hidden);
  const hasAny = board.blocks.length > 0 || board.version > 0;
  if (!hasAny) return null;
  const restore = (id: string, close: () => void) => {
    close();
    boardsApi.put(agent, board.blocks.map((b) => (b.id === id ? { ...b, hidden: false } : b)), L('放回了一块', 'Brought a block back'))
      .then(() => reload()).catch((e) => showError(L('没改成', "Couldn't change the board"), e));
  };
  return (
    <View style={{ marginTop: space.xl, gap: space.sm }}>
      {hidden.length ? (
        <Pressable accessibilityRole="button" onPress={() => sheet.open({
          title: L('藏起来的', 'Hidden'),
          content: (close) => (
            <View style={{ gap: space.sm }}>
              {hidden.map((b) => (
                <Pressable key={b.id} onPress={() => restore(b.id, close)} accessibilityRole="button" style={({ pressed }) => [styles.menuRow, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
                  <View style={{ flex: 1 }}><T v="headline">{b.title || b.id}</T></View>
                  <T v="callout" color={t.gold} style={{ fontWeight: '700' }}>{L('放回来', 'Show again')}</T>
                </Pressable>
              ))}
            </View>
          ),
        })} style={({ pressed }) => [styles.footRow, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
          <Eye size={18} color={t.ink2} />
          <T v="callout" style={{ flex: 1 }}>{L(`藏起来的 ${hidden.length} 块`, `${hidden.length} hidden block${hidden.length === 1 ? '' : 's'}`)}</T>
          <ChevronRight size={16} color={t.ink3} />
        </Pressable>
      ) : null}
      <Pressable accessibilityRole="button" onPress={onHistory} style={({ pressed }) => [styles.footRow, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
        <Clock size={18} color={t.ink2} />
        <T v="callout" style={{ flex: 1 }}>{L('看板改动记录', 'Board history')}</T>
        <ChevronRight size={16} color={t.ink3} />
      </Pressable>
      <T v="caption" color={t.ink3} style={{ fontWeight: '400', textAlign: 'center', marginTop: 2 }}>{L('长按任意一块：挪位置、藏起来、让它改、删掉', 'Long-press a block to move, hide, change or delete it')}</T>
    </View>
  );
}

/** 内置看板某一节后面挂的积木。 */
export function Slot({ at }: { at: string }) {
  const ctx = useBoard();
  const list = ctx?.layout.get(at) ?? [];
  if (!list.length) return null;
  return <>{list.map((b) => <BlockView key={b.id} block={b} />)}</>;
}

/** 没有内置看板的 Agent：整页积木（最前面的 + 其余按顺序）。 */
export function AllBlocks() {
  const ctx = useBoard();
  if (!ctx) return null;
  const list = [...(ctx.layout.get('top') ?? []), ...[...ctx.layout.entries()].filter(([k]) => k !== 'top').flatMap(([, v]) => v)];
  return <>{list.map((b) => <BlockView key={b.id} block={b} />)}</>;
}

export const hasBlocks = (board: Board | null) => !!board?.blocks.some((b) => !b.hidden);

/** 看板顶上一条：Agent 刚改了看板（你让它加的，或者你同意的提案），能撤回；撤回之后能恢复。 */
export function UndoStrip() {
  const t = useTheme();
  const ctx = useBoard();
  const [undone, setUndone] = useState<{ version: number } | null>(null);
  const [busy, setBusy] = useState(false);
  if (!ctx || ctx.readOnly) return null;
  const { board, agent, reload } = ctx;
  const strip = board?.strip;
  const run = (fn: () => Promise<unknown>, after?: () => void) => {
    if (busy) return;
    setBusy(true);
    fn().then(() => { after?.(); return reload(); }).catch((e) => showError(L('没做成', "Didn't go through"), e)).finally(() => setBusy(false));
  };
  if (undone) {
    return (
      <View style={[styles.strip, { backgroundColor: t.surface2 }]}>
        <T v="callout" color={t.ink2} style={{ flex: 1 }}>{L('撤回了，看板回到加之前的样子。记下的数据都还在。', 'Undone: the board is back to how it was. The data you recorded is still there.')}</T>
        <StripBtn label={L('恢复', 'Redo')} color={t.gold} disabled={busy} onPress={() => run(() => boardsApi.revert(agent, undone.version), () => setUndone(null))} />
      </View>
    );
  }
  if (!strip) return null;
  const titles = board?.blocks.filter((b) => strip.added.includes(b.id)).map((b) => b.title || b.actions?.map((a) => a.label).join(' / ') || b.id) ?? [];
  const head = strip.note || (titles.length ? L(`加了 ${titles.length} 块`, `Added ${titles.length} block${titles.length === 1 ? '' : 's'}`) : L('看板改了', 'The board changed'));
  return (
    <View style={[styles.strip, { backgroundColor: t.goldSoft, flexDirection: 'column', alignItems: 'stretch', gap: 6 }]}>
      <View style={{ flexDirection: 'row', gap: 10, alignItems: 'flex-start' }}>
        <View style={{ marginTop: 2 }}><Sparkles size={18} color={t.gold} /></View>
        <View style={{ flex: 1, gap: 2 }}>
          <T v="headline" style={{ fontSize: 15 }}>{head}</T>
          {titles.length ? <T v="callout" color={t.ink2} style={{ fontSize: 13 }}>{L('标「新」的就是。', 'The ones marked New.')}</T> : null}
        </View>
      </View>
      <View style={{ flexDirection: 'row', justifyContent: 'flex-end', gap: 14 }}>
        <StripBtn label={L('撤回', 'Undo')} color={t.gold} disabled={busy} onPress={() => run(() => boardsApi.revert(agent, strip.undoTo), () => setUndone({ version: strip.version }))} />
        <StripBtn label={L('知道了', 'Got it')} color={t.ink2} disabled={busy} onPress={() => run(() => boardsApi.ack(agent))} />
      </View>
    </View>
  );
}

function StripBtn({ label, color, onPress, disabled }: { label: string; color: string; onPress: () => void; disabled?: boolean }) {
  return (
    <Pressable onPress={onPress} disabled={disabled} hitSlop={8} accessibilityRole="button" style={({ pressed }) => ({ paddingVertical: 4, paddingHorizontal: 2, opacity: disabled ? 0.5 : pressed ? 0.6 : 1 })}>
      <T v="callout" color={color} style={{ fontWeight: '700' }}>{label}</T>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  menuRow: { flexDirection: 'row', alignItems: 'center', gap: space.md, borderRadius: radius.md, paddingHorizontal: space.lg, paddingVertical: 12, minHeight: 52 },
  footRow: { flexDirection: 'row', alignItems: 'center', gap: space.md, borderRadius: radius.md, paddingHorizontal: space.lg, height: 48 },
  strip: { borderRadius: radius.md + 2, paddingVertical: 12, paddingHorizontal: 14, flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: space.md },
});
