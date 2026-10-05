// 一个 Agent 的积木看板：读数据、按 after 排好每块放在哪（内置看板的某一节后面、另一块后面，或者最后），顶上的撤回条。
// 内置看板（健身、饮食……）的每一节也是看板上的一块（Sections.tsx 按 board.sections 排、能挪能藏），每节后面是 <Slot at="diet.next" />；
// 没有内置看板的 Agent 整页是 <AllBlocks />。
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, View } from 'react-native';
import { boardsApi, type Block, type Board, type BoardAlert, type BoardSection, type Pack } from '../../api/boards';
import { L } from '../../i18n';
import { radius, space, useTheme } from '../../theme';
import { ArrowUp, Bell, BellOff, ChevronRight, Clock, Eye, EyeOff, MessageCircle, PackagePlus, Sparkles, Trash2 } from '../icons';
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

/** 内置小节按显示顺序（服务器给的 sections；老服务器没有就按 anchors 的默认顺序、都不藏）。 */
export function sectionsOf(board: Board | null): BoardSection[] {
  const ids = (board?.anchors ?? []).filter((a) => a !== 'top');
  const got = (board?.sections ?? []).filter((s) => ids.includes(s.id));
  return [...got, ...ids.filter((id) => !got.some((s) => s.id === id)).map((id) => ({ id, title: '', hidden: false }))];
}

/** 按显示顺序分好的几段（最前面 / 每个内置小节后面 / 最后）。没有内置看板的只有一段。hidden：藏起来的小节（跨段挪的时候跳过它们）。 */
function slotsOf(board: Board): { keys: string[]; lists: Map<string, Block[]>; hidden: Set<string> } {
  const layout = layoutOf(board.blocks, board.anchors);
  if (board.dashboard === 'none' || !board.anchors.some((a) => a !== 'top')) {
    return { keys: [''], lists: new Map([['', [...(layout.get('top') ?? []), ...[...layout.entries()].filter(([k]) => k !== 'top').flatMap(([, v]) => v)]]]), hidden: new Set() };
  }
  const secs = sectionsOf(board);
  const keys = ['top', ...secs.map((x) => x.id), ''];
  return { keys, lists: new Map(keys.map((k) => [k, [...(layout.get(k) ?? [])]])), hidden: new Set(secs.filter((x) => x.hidden).map((x) => x.id)) };
}

/** 段 → 配置：每块的 after 就写它所在的段（「接在另一块后面」这种链条摊平），藏起来的原样留在最后。 */
function blocksFrom(board: Board, keys: string[], lists: Map<string, Block[]>): Block[] {
  const shown = keys.flatMap((k) => (lists.get(k) ?? []).map((b) => ({ ...b, after: k || undefined })));
  return [...shown, ...board.blocks.filter((b) => b.hidden)];
}

/** 挪一格：段里换位置；已经在段头 / 段尾就跨过一个内置小节，挪到上一段末尾 / 下一段开头。 */
export function moved(board: Board, id: string, dir: -1 | 1): Block[] | null {
  const { keys, lists, hidden } = slotsOf(board);
  const si = keys.findIndex((k) => (lists.get(k) ?? []).some((b) => b.id === id));
  if (si < 0) return null;
  const list = lists.get(keys[si]) as Block[];
  const i = list.findIndex((b) => b.id === id);
  const j = i + dir;
  if (j >= 0 && j < list.length) {
    [list[i], list[j]] = [list[j], list[i]];
  } else {
    let ti = si + dir;
    while (ti >= 0 && ti < keys.length && hidden.has(keys[ti])) ti += dir;  // 藏起来的小节看不见：跳过它那一段
    if (ti < 0 || ti >= keys.length) return null;
    const [b] = list.splice(i, 1);
    const target = lists.get(keys[ti]) as Block[];
    if (dir < 0) target.push(b); else target.unshift(b);
  }
  return blocksFrom(board, keys, lists);
}

/** 挪一节：和上面 / 下面最近的一个没藏的小节换位置（挂在它后面的积木跟着走）。 */
export function movedSection(board: Board, id: string, dir: -1 | 1): BoardSection[] | null {
  const secs = sectionsOf(board);
  const i = secs.findIndex((x) => x.id === id);
  if (i < 0) return null;
  let j = i + dir;
  while (j >= 0 && j < secs.length && secs[j].hidden) j += dir;
  if (j < 0 || j >= secs.length) return null;
  const out = secs.filter((x) => x.id !== id);
  const at = out.findIndex((x) => x.id === secs[j].id);
  out.splice(dir < 0 ? at : at + 1, 0, secs[i]);
  return out;
}

export const sectionTitle = (board: Board | null, id: string) => sectionsOf(board).find((x) => x.id === id)?.title || id;

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
      boardsApi.put(agent, blocks, note).then(() => reload()).catch((e) => showError(L('修改失败', "Couldn't update the board"), e));
    };
    const name = block.title || block.actions?.map((a) => a.label).join(' / ') || block.id;
    sheet.open({
      title: name,
      content: (close) => <BlockMenu board={board} block={block} close={close} onSave={(b, note) => { close(); save(b, note); }}
        onAsk={() => { setChatDraft(agent, L(`看板上「${name}」这一块：`, `About the "${name}" block on the board: `)); close(); onChat(); }} />,
    });
  }, [agent, board, readOnly, reload, sheet, onChat]);
  const openSectionMenu = useCallback((id: string) => {
    if (!board || readOnly) return;
    const name = sectionTitle(board, id);
    const save = (secs: BoardSection[] | null, note: string) => {
      if (!secs) return;
      boardsApi.put(agent, board.blocks, note, secs).then(() => reload()).catch((e) => showError(L('修改失败', "Couldn't update the board"), e));
    };
    sheet.open({
      title: name,
      content: (close) => <SectionMenu board={board} id={id} name={name} close={close} onSave={(x, note) => { close(); save(x, note); }} />,
    });
  }, [agent, board, readOnly, reload, sheet]);
  const value = useMemo(() => ({ agent, board, error: error ?? null, reload, layout, fresh, readOnly, onChat, openMenu, openSectionMenu }),
    [agent, board, error, reload, layout, fresh, readOnly, onChat, openMenu, openSectionMenu]);
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
      <Row icon={<ArrowUp size={20} color={t.ink2} />} label={L('上移', 'Move up')} disabled={!up} onPress={() => onSave(up, L(`把「${name}」往上挪了`, `Moved "${name}" up`))} />
      <Row icon={<View style={{ transform: [{ rotate: '180deg' }] }}><ArrowUp size={20} color={t.ink2} /></View>} label={L('下移', 'Move down')} disabled={!down}
        onPress={() => onSave(down, L(`把「${name}」往下挪了`, `Moved "${name}" down`))} />
      <Row icon={<EyeOff size={20} color={t.ink2} />} label={L('隐藏', 'Hide')} sub={L('可在看板底部「已隐藏」中恢复', 'Restore it from "Hidden" at the bottom of the board')}
        onPress={() => onSave(board.blocks.map((b) => (b.id === block.id ? { ...b, hidden: true } : b)), L(`藏起了「${name}」`, `Hid "${name}"`))} />
      <Row icon={<MessageCircle size={20} color={t.gold} />} label={L('请 Agent 修改', 'Ask the Agent to change this')} sub={L('例如「只显示 2 天内到期的」', 'e.g. "only show what expires within 2 days"')} onPress={onAsk} />
      <Row icon={<Trash2 size={20} color={t.bad} />} color={t.bad} label={sure ? L('确认删除此区块', 'Confirm delete') : L('删除此区块', 'Delete this block')}
        sub={L('仅删除区块，数据保留；可在看板改动记录中恢复', 'Only the block is removed; the data stays, and board history can restore it')}
        onPress={() => (sure ? onSave(board.blocks.filter((b) => b.id !== block.id), L(`删了「${name}」`, `Deleted "${name}"`)) : setSure(true))} />
      <Pressable onPress={close} accessibilityRole="button" style={({ pressed }) => [styles.menuRow, { justifyContent: 'center', opacity: pressed ? 0.6 : 1 }]}>
        <T v="headline" color={t.ink2}>{L('取消', 'Cancel')}</T>
      </Pressable>
    </View>
  );
}

/** 长按内置看板的一节：挪上挪下、藏起来。内容是 app 画的，不能删、不能让它改。 */
function SectionMenu({ board, id, name, close, onSave }: { board: Board; id: string; name: string; close: () => void; onSave: (secs: BoardSection[] | null, note: string) => void }) {
  const t = useTheme();
  const up = movedSection(board, id, -1);
  const down = movedSection(board, id, 1);
  return (
    <View style={{ gap: space.sm }}>
      <MenuRow icon={<ArrowUp size={20} color={t.ink2} />} label={L('上移', 'Move up')} disabled={!up} onPress={() => onSave(up, L(`把「${name}」往上挪了`, `Moved "${name}" up`))} />
      <MenuRow icon={<View style={{ transform: [{ rotate: '180deg' }] }}><ArrowUp size={20} color={t.ink2} /></View>} label={L('下移', 'Move down')} disabled={!down}
        onPress={() => onSave(down, L(`把「${name}」往下挪了`, `Moved "${name}" down`))} />
      <MenuRow icon={<EyeOff size={20} color={t.ink2} />} label={L('隐藏', 'Hide')} sub={L('可在看板底部「已隐藏」中恢复', 'Restore it from "Hidden" at the bottom of the board')}
        onPress={() => onSave(sectionsOf(board).map((x) => (x.id === id ? { ...x, hidden: true } : x)), L(`藏起了「${name}」`, `Hid "${name}"`))} />
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

/** 看板最底下：它的提醒（能暂停、删）、藏起来的块和小节（能放回来）、功能包、改动记录。 */
export function BoardFooter({ onHistory }: { onHistory: () => void }) {
  const t = useTheme();
  const ctx = useBoard();
  const sheet = useSheet();
  const agent = ctx?.agent ?? '';
  const [alerts, setAlerts] = useState<BoardAlert[]>([]);
  const [packs, setPacks] = useState<Pack[] | null>(null);
  const version = ctx?.board?.version;
  // 看板换了一版（装了包、同意了提案）就重读提醒和功能包；老服务器没有这两个接口：当作没有
  const loadExtras = useCallback(() => {
    if (!agent) return;
    boardsApi.alerts(agent).then((r) => setAlerts(r.alerts), () => setAlerts([]));
    boardsApi.packs().then((r) => setPacks(r.packs), () => setPacks(null));
  }, [agent]);
  useEffect(() => { loadExtras(); }, [loadExtras, version]);
  if (!ctx?.board || ctx.readOnly) return null;
  const { board, reload } = ctx;
  const hidden = board.blocks.filter((b) => b.hidden);
  const hiddenSecs = sectionsOf(board).filter((x) => x.hidden);
  const nHidden = hidden.length + hiddenSecs.length;
  // 功能包：装在这里的，或者适合这种看板的（没有内置看板的 Agent 什么包都能装）
  const fit = (packs ?? []).filter((p) => p.installedOn.includes(agent) || board.dashboard === 'none' || p.for.includes(board.dashboard) || p.for.includes(agent));
  const hasAny = board.blocks.length > 0 || board.version > 0 || alerts.length > 0 || fit.length > 0;
  if (!hasAny) return null;
  const fail = (e: unknown) => showError(L('修改失败', "Couldn't update the board"), e);
  const restore = (id: string, close: () => void) => {
    close();
    boardsApi.put(agent, board.blocks.map((b) => (b.id === id ? { ...b, hidden: false } : b)), L('放回了一块', 'Brought a block back')).then(() => reload()).catch(fail);
  };
  const restoreSection = (id: string, close: () => void) => {
    close();
    boardsApi.put(agent, board.blocks, L(`放回了「${sectionTitle(board, id)}」`, `Brought "${sectionTitle(board, id)}" back`),
      sectionsOf(board).map((x) => (x.id === id ? { ...x, hidden: false } : x))).then(() => reload()).catch(fail);
  };
  const setAlert = (a: BoardAlert, status: 'live' | 'paused' | 'deleted', close: () => void) => {
    close();
    boardsApi.setAlert(a.id, status).then(loadExtras).catch(fail);
  };
  const openAlert = (a: BoardAlert) => sheet.open({
    title: a.title,
    content: (close) => (
      <View style={{ gap: space.sm }}>
        <View style={[styles.note, { backgroundColor: t.surface }]}>
          <T v="callout" color={t.ink2}>{`${a.when} · ${a.levelText}`}</T>
          <T v="callout" color={a.preview ? t.ink : t.ink3}>{a.preview ? L(`按当前数据将推送：${a.preview}`, `With current data: ${a.preview}`) : L('当前数据无匹配内容，到时不会推送。', 'No data matches at present, so nothing will be sent.')}</T>
          {a.lastSent ? <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L(`上次推送：${a.lastSent.slice(5, 16).replace('T', ' ')}`, `Last sent ${a.lastSent.slice(5, 16).replace('T', ' ')}`)}</T> : null}
        </View>
        {a.status === 'paused'
          ? <MenuRow icon={<Bell size={20} color={t.gold} />} label={L('恢复', 'Resume')} onPress={() => setAlert(a, 'live', close)} />
          : <MenuRow icon={<BellOff size={20} color={t.ink2} />} label={L('暂停', 'Pause')} sub={L('停止推送，保留规则，可随时恢复', 'Stops sending; the rule stays and can be resumed')} onPress={() => setAlert(a, 'paused', close)} />}
        <MenuRow icon={<Trash2 size={20} color={t.bad} />} color={t.bad} label={L('删除此提醒', 'Delete this reminder')} sub={L('如需重新开启，需由 Agent 再次提议并经你确认', 'To turn it back on, the Agent must propose it again for your approval')}
          onPress={() => setAlert(a, 'deleted', close)} />
        <Pressable onPress={close} accessibilityRole="button" style={({ pressed }) => [styles.menuRow, { justifyContent: 'center', opacity: pressed ? 0.6 : 1 }]}>
          <T v="headline" color={t.ink2}>{L('取消', 'Cancel')}</T>
        </Pressable>
      </View>
    ),
  });
  const openPacks = () => sheet.open({
    title: L('功能包', 'Feature packs'),
    content: (close) => <PackList packs={fit} agent={agent} close={close} onDone={() => { loadExtras(); reload(); }} />,
  });
  return (
    <View style={{ marginTop: space.xl, gap: space.sm }}>
      {alerts.length ? (
        <View style={{ gap: space.sm }}>
          {alerts.map((a) => (
            <Pressable key={a.id} accessibilityRole="button" onPress={() => openAlert(a)} style={({ pressed }) => [styles.footRow, styles.tall, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
              {a.status === 'paused' ? <BellOff size={18} color={t.ink3} /> : <Bell size={18} color={t.gold} />}
              <View style={{ flex: 1 }}>
                <T v="callout" numberOfLines={1}>{a.title}</T>
                <T v="caption" color={t.ink3} numberOfLines={1} style={{ fontWeight: '400' }}>{a.status === 'paused' ? L(`暂停中 · ${a.when}`, `Paused · ${a.when}`) : a.when}</T>
              </View>
              <ChevronRight size={16} color={t.ink3} />
            </Pressable>
          ))}
        </View>
      ) : null}
      {nHidden ? (
        <Pressable accessibilityRole="button" onPress={() => sheet.open({
          title: L('已隐藏', 'Hidden'),
          content: (close) => (
            <View style={{ gap: space.sm }}>
              {hiddenSecs.map((x) => (
                <Pressable key={x.id} onPress={() => restoreSection(x.id, close)} accessibilityRole="button" style={({ pressed }) => [styles.menuRow, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
                  <View style={{ flex: 1 }}><T v="headline">{x.title || x.id}</T></View>
                  <T v="callout" color={t.gold} style={{ fontWeight: '700' }}>{L('恢复显示', 'Show again')}</T>
                </Pressable>
              ))}
              {hidden.map((b) => (
                <Pressable key={b.id} onPress={() => restore(b.id, close)} accessibilityRole="button" style={({ pressed }) => [styles.menuRow, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
                  <View style={{ flex: 1 }}><T v="headline">{b.title || b.id}</T></View>
                  <T v="callout" color={t.gold} style={{ fontWeight: '700' }}>{L('恢复显示', 'Show again')}</T>
                </Pressable>
              ))}
            </View>
          ),
        })} style={({ pressed }) => [styles.footRow, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
          <Eye size={18} color={t.ink2} />
          <T v="callout" style={{ flex: 1 }}>{L(`已隐藏 ${nHidden} 个区块`, `${nHidden} hidden block${nHidden === 1 ? '' : 's'}`)}</T>
          <ChevronRight size={16} color={t.ink3} />
        </Pressable>
      ) : null}
      {fit.length ? (
        <Pressable accessibilityRole="button" onPress={openPacks} style={({ pressed }) => [styles.footRow, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
          <PackagePlus size={18} color={t.ink2} />
          <T v="callout" style={{ flex: 1 }} numberOfLines={1}>{L('功能包', 'Feature packs')}</T>
          <T v="caption" color={t.ink3} style={{ fontWeight: '400' }} numberOfLines={1}>{fit.filter((p) => p.installedOn.includes(agent)).map((p) => p.title).join(L('、', ', '))}</T>
          <ChevronRight size={16} color={t.ink3} />
        </Pressable>
      ) : null}
      <Pressable accessibilityRole="button" onPress={onHistory} style={({ pressed }) => [styles.footRow, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
        <Clock size={18} color={t.ink2} />
        <T v="callout" style={{ flex: 1 }}>{L('看板改动记录', 'Board history')}</T>
        <ChevronRight size={16} color={t.ink3} />
      </Pressable>
      <T v="caption" color={t.ink3} style={{ fontWeight: '400', textAlign: 'center', marginTop: 2 }}>{L('长按任意区块：移动、隐藏、修改或删除', 'Long-press a block to move, hide, change or delete it')}</T>
    </View>
  );
}

/** 功能包列表：装在这里的标「已装」；没装的点「装上」直接装（表和积木；包里的提醒另外出卡等你点头）。 */
function PackList({ packs, agent, close, onDone }: { packs: Pack[]; agent: string; close: () => void; onDone: () => void }) {
  const t = useTheme();
  const [busy, setBusy] = useState<string | null>(null);
  const [done, setDone] = useState<{ name: string; lines: string[] } | null>(null);
  const install = (p: Pack) => {
    if (busy) return;
    setBusy(p.name);
    boardsApi.installPack(p.name, agent).then((r) => {
      const lines = [...r.changes];
      setDone({ name: p.title, lines });
      onDone();
    }).catch((e) => showError(L('安装失败', "Couldn't install"), e)).finally(() => setBusy(null));
  };
  if (done) {
    return (
      <View style={{ gap: space.md }}>
        <T v="headline">{L(`已安装「${done.name}」`, `Installed "${done.name}"`)}</T>
        {done.lines.map((x, i) => <T key={`${i}-${x}`} v="callout" color={t.ink2}>{`· ${x}`}</T>)}
        <Pressable onPress={close} accessibilityRole="button" style={({ pressed }) => [styles.menuRow, { justifyContent: 'center', backgroundColor: t.surface, opacity: pressed ? 0.6 : 1 }]}>
          <T v="headline" color={t.gold}>{L('好', 'OK')}</T>
        </Pressable>
      </View>
    );
  }
  return (
    <View style={{ gap: space.sm }}>
      {packs.map((p) => {
        const on = p.installedOn.includes(agent);
        return (
          <View key={p.name} style={[styles.note, { backgroundColor: t.surface }]}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
              <T v="headline" style={{ flex: 1 }}>{p.title}</T>
              {on ? <T v="caption" color={t.ink3}>{L('已安装', 'Installed')}</T> : (
                <Pressable onPress={() => install(p)} disabled={!!busy} accessibilityRole="button" hitSlop={8} style={({ pressed }) => ({ opacity: busy ? 0.5 : pressed ? 0.6 : 1 })}>
                  {busy === p.name ? <ActivityIndicator size="small" color={t.gold} /> : <T v="callout" color={t.gold} style={{ fontWeight: '700' }}>{L('安装', 'Install')}</T>}
                </Pressable>
              )}
            </View>
            {p.summary ? <T v="callout" color={t.ink2}>{p.summary}</T> : null}
            <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{[
              p.blocks.map((b) => b.title).filter(Boolean).join(L('、', ', ')),
              p.alerts.length ? L(`${p.alerts.length} 个提醒（安装后另行确认）`, `${p.alerts.length} reminder${p.alerts.length === 1 ? '' : 's'} (asked separately)`) : '',
            ].filter(Boolean).join(' · ')}</T>
          </View>
        );
      })}
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
    fn().then(() => { after?.(); return reload(); }).catch((e) => showError(L('操作失败', "Couldn't complete the action"), e)).finally(() => setBusy(false));
  };
  if (undone) {
    return (
      <View style={[styles.strip, { backgroundColor: t.surface2 }]}>
        <T v="callout" color={t.ink2} style={{ flex: 1 }}>{L('已撤回，看板已恢复到添加前的状态。已记录的数据仍保留。', 'Undone: the board is back to how it was. The data you recorded is still there.')}</T>
        <StripBtn label={L('恢复', 'Redo')} color={t.gold} disabled={busy} onPress={() => run(() => boardsApi.revert(agent, undone.version), () => setUndone(null))} />
      </View>
    );
  }
  if (!strip) return null;
  const titles = board?.blocks.filter((b) => strip.added.includes(b.id)).map((b) => b.title || b.actions?.map((a) => a.label).join(' / ') || b.id) ?? [];
  const head = strip.note || (titles.length ? L(`新增 ${titles.length} 个区块`, `Added ${titles.length} block${titles.length === 1 ? '' : 's'}`) : L('看板已更新', 'The board was updated'));
  return (
    <View style={[styles.strip, { backgroundColor: t.goldSoft, flexDirection: 'column', alignItems: 'stretch', gap: 6 }]}>
      <View style={{ flexDirection: 'row', gap: 10, alignItems: 'flex-start' }}>
        <View style={{ marginTop: 2 }}><Sparkles size={18} color={t.gold} /></View>
        <View style={{ flex: 1, gap: 2 }}>
          <T v="headline" style={{ fontSize: 15 }}>{head}</T>
          {titles.length ? <T v="callout" color={t.ink2} style={{ fontSize: 13 }}>{L('即标有「新」的区块。', 'The ones marked New.')}</T> : null}
        </View>
      </View>
      <View style={{ flexDirection: 'row', justifyContent: 'flex-end', gap: 14 }}>
        <StripBtn label={L('撤回', 'Undo')} color={t.gold} disabled={busy} onPress={() => run(() => boardsApi.revert(agent, strip.undoTo), () => setUndone({ version: strip.version }))} />
        <StripBtn label={L('关闭', 'Dismiss')} color={t.ink2} disabled={busy} onPress={() => run(() => boardsApi.ack(agent))} />
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
  note: { borderRadius: radius.md, paddingHorizontal: space.lg, paddingVertical: 12, gap: 4 },
  tall: { height: undefined, minHeight: 56, paddingVertical: 8 },
});
