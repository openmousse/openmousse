// 一个 Agent 的积木看板：读数据、按 after 排好每块放在哪（内置看板的某一节后面、另一块后面，或者最后），顶上的撤回条。
// 内置看板（健身、饮食……）在小节之间放 <Slot at="diet.next" />；没有内置看板的 Agent 整页是 <AllBlocks />。
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import { boardsApi, type Block, type Board } from '../../api/boards';
import { L } from '../../i18n';
import { radius, space, useTheme } from '../../theme';
import { Sparkles } from '../icons';
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

export function BoardProvider({ agent, board, error, reload, onChat, readOnly = false, children }: {
  agent: string; board: Board | null; error?: string | null; reload: () => Promise<void>; onChat: () => void; readOnly?: boolean; children: React.ReactNode;
}) {
  const layout = useMemo(() => layoutOf(board?.blocks ?? [], board?.anchors ?? []), [board]);
  const fresh = useMemo(() => new Set(board?.strip?.added ?? []), [board]);
  const value = useMemo(() => ({ agent, board, error: error ?? null, reload, layout, fresh, readOnly, onChat }), [agent, board, error, reload, layout, fresh, readOnly, onChat]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
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
  strip: { borderRadius: radius.md + 2, paddingVertical: 12, paddingHorizontal: 14, flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: space.md },
});
