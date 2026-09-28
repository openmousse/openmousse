// 播客的素材（服务端 ../server/podmaterials.py）：一期播客能放进主对话里说的、和朋友的聊天、文件、Zen 的想法 / 主题、收藏，
// 录前聊天、主持人追问、录完整理、费曼对照都参考。朋友说的只在这一期里用（存进库的笔记只写「参考了和 X 的聊天」）。
//   MaterialsRow   录前页 / 录音页（深色）/ 录完页的一行「素材 · N」，点开 MaterialsSheet
//   MaterialsSheet 弹层：列出素材（能拿掉）+ 加素材（每种一个挑选页，多选）
//   PutInPodcast   对话、朋友聊天里长按一条「放进播客」：新开一期或者放进最近的一期
// 弹层渲染在 NavigationContainer 外面：要跳页的地方由调用方传回调进来。
import React, { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';
import { Bookmark, Check, ChevronLeft, ChevronRight, FileText, Lightbulb, MessageCircle, Mic, Paperclip, Plus, Users, X } from '../components/icons';
import { useSheet } from '../components/Sheet';
import { timeLabel } from '../components/FriendBits';
import { pickDocuments } from '../components/chatInput';
import { Btn, T, showError } from '../components/ui';
import * as pod from '../api/podcast';
import type { MatKind, PodBrief, PodMaterial, PodPick } from '../api/podcast';
import { agentName } from '../brand';
import { L } from '../i18n';
import { radius, space, type, useTheme, type Theme } from '../theme';

function kindLook(t: Theme, k: MatKind | 'friends') {
  switch (k) {
    case 'chat': return { Icon: MessageCircle, ...t.tints.gold };
    case 'friend': case 'friends': return { Icon: Users, ...t.tints.pink };
    case 'file': return { Icon: FileText, ...t.tints.cyan };
    case 'save': return { Icon: Bookmark, ...t.tints.green };
    default: return { Icon: Lightbulb, ...t.tints.purple };
  }
}

/** 一条素材是谁的：你说的 / 它回的 / 朋友说的 / 文件、收藏。 */
function whoLabel(m: PodMaterial): string {
  if (m.kind === 'file') return m.note || L('文件', 'File');
  if (m.kind === 'save') return L('收藏', 'Saved');
  if (m.who === 'friend') return L(`${m.friend ?? '朋友'}说的 · 只在这一期里用`, `${m.friend ?? 'A friend'} said · this episode only`);
  if (m.who === 'assistant') return m.kind === 'friend' ? L('你的名片 agent 答的', 'Your card agent') : L(`${agentName()} 回的`, `${agentName()} replied`);
  return L('你说的', 'You said');
}

/** 「素材 · N」那一行。dark：录音页的深色。 */
export function MaterialsRow({ id, count, dark, onCount }: { id: string; count: number; dark?: boolean; onCount: (n: number) => void }) {
  const t = useTheme();
  const sheet = useSheet();
  const open = () => sheet.open({ title: L('这一期的素材', 'Materials for this episode'), content: () => <MaterialsSheet id={id} onCount={onCount} /> });
  const fg = dark ? '#ECEEF0' : t.ink;
  const sub = dark ? '#A6AEB7' : t.ink3;
  return (
    <Pressable onPress={open} accessibilityRole="button" accessibilityLabel={L(`素材 ${count} 条，点开加或拿掉`, `${count} materials, tap to add or remove`)}
      style={({ pressed }) => [styles.row, { backgroundColor: dark ? '#15181D' : t.surface, borderColor: dark ? '#2A3038' : t.line, opacity: pressed ? 0.7 : 1 }]}>
      <Paperclip size={16} color={dark ? '#DDB56A' : t.gold} />
      <Text style={[type.callout, { color: fg, fontWeight: '600' }]}>{L(`素材 · ${count}`, `Materials · ${count}`)}</Text>
      <Text style={[type.caption, { color: sub, flex: 1, fontWeight: '400' }]} numberOfLines={1}>
        {count ? L('录前、追问、整理都参考', 'Used in prep, questions and the note') : L('放进聊过的、朋友聊天、文件', 'Add chats, friend chats, files')}
      </Text>
      {count ? <ChevronRight size={16} color={sub} /> : <Plus size={16} color={sub} />}
    </Pressable>
  );
}

type View_ = { v: 'list' } | { v: 'chat' } | { v: 'friends' } | { v: 'friend'; id: string; name: string } | { v: 'zen' } | { v: 'save' };

export function MaterialsSheet({ id, onCount }: { id: string; onCount?: (n: number) => void }) {
  const t = useTheme();
  const [items, setItems] = useState<PodMaterial[] | null>(null);
  const [view, setView] = useState<View_>({ v: 'list' });
  const [busy, setBusy] = useState<string | null>(null);
  const got = useCallback((list: PodMaterial[]) => { setItems(list); onCount?.(list.length); }, [onCount]);
  useEffect(() => { pod.materials(id).then(got).catch((e) => showError(L('素材没读出来', "Couldn't load materials"), e)); }, [id, got]);

  const drop = async (m: PodMaterial) => {
    if (busy) return;
    setBusy(m.id);
    try { got(await pod.removeMaterial(id, m.id)); } catch (e) { showError(L('没拿掉', "Couldn't remove it"), e); } finally { setBusy(null); }
  };
  const files = async () => {
    if (busy) return;
    const picked = await pickDocuments().catch((e) => { showError(L('选不了文件', "Couldn't pick files"), e); return []; });
    if (!picked.length) return;
    setBusy('files');
    try {
      const r = await pod.uploadMaterials(id, picked.slice(0, 10));
      got(r.items);
      if (r.failed.length) showError(L('有的没放进来', 'Some files were not added'), r.failed.map((f) => `${f.name}：${f.error}`).join('\n'));
    } catch (e) { showError(L('没传上去', "Couldn't upload"), e); } finally { setBusy(null); }
  };
  const add = async (list: { kind: MatKind; ref: string }[]) => {
    const r = await pod.addMaterials(id, list);
    got(r.items);
    setView({ v: 'list' });
    if (r.failed.length) showError(L(`${r.failed.length} 条没放进来`, `${r.failed.length} not added`), r.failed.map((f) => f.error).join('\n'));
  };

  if (view.v !== 'list') {
    const back = () => setView(view.v === 'friend' ? { v: 'friends' } : { v: 'list' });
    return <Picker kind={view.v} friendId={view.v === 'friend' ? view.id : undefined} friendName={view.v === 'friend' ? view.name : undefined} onBack={back}
      onFriend={(f) => setView({ v: 'friend', id: f.ref, name: f.text })} onAdd={add} have={new Set((items ?? []).map((m) => `${m.kind}:${m.ref}`))} />;
  }
  const kinds: { k: View_['v'] | 'file'; label: string; Icon: typeof Paperclip; look: MatKind }[] = [
    { k: 'chat', label: L('主对话', 'Chats'), Icon: MessageCircle, look: 'chat' },
    { k: 'friends', label: L('朋友聊天', 'Friends'), Icon: Users, look: 'friend' },
    { k: 'file', label: L('文件', 'Files'), Icon: FileText, look: 'file' },
    { k: 'zen', label: L('Zen 想法', 'Zen'), Icon: Lightbulb, look: 'idea' },
    { k: 'save', label: L('收藏', 'Saved'), Icon: Bookmark, look: 'save' },
  ];
  return (
    <View style={{ gap: space.md }}>
      <T v="callout" color={t.ink2}>{L('录前聊天、主持人追问、录完整理、费曼对照都会参考。朋友说的只在这一期里用，存进库的笔记只写「参考了和谁的聊天」。',
        "Used when you talk it through, for the host's questions, the note and the Feynman check. What friends said stays in this episode; the saved note only says whose chat it drew on.")}</T>
      {items === null ? <ActivityIndicator color={t.gold} /> : items.length ? (
        <View style={[styles.box, { backgroundColor: t.surface, borderColor: t.line }]}>
          {items.map((m, i) => {
            const lk = kindLook(t, m.kind);
            return (
              <View key={m.id} style={[styles.item, i ? { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line } : null]}>
                <View style={[styles.tile, { backgroundColor: lk.soft }]}><lk.Icon size={15} color={lk.fg} /></View>
                <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
                  <T v="callout" numberOfLines={1} style={{ fontWeight: '600' }}>{m.title}</T>
                  {m.preview ? <T v="callout" color={t.ink2} numberOfLines={2}>{m.preview}</T> : null}
                  <T v="caption" color={m.who === 'friend' ? lk.fg : t.ink3} style={{ fontWeight: '400' }} numberOfLines={1}>{whoLabel(m)}</T>
                </View>
                <Pressable onPress={() => drop(m)} hitSlop={10} accessibilityRole="button" accessibilityLabel={L(`拿掉：${m.title}`, `Remove: ${m.title}`)} style={styles.x}>
                  {busy === m.id ? <ActivityIndicator size="small" color={t.ink3} /> : <X size={16} color={t.ink3} />}
                </Pressable>
              </View>
            );
          })}
        </View>
      ) : <T v="callout" color={t.ink3}>{L('还没有素材。', 'No materials yet.')}</T>}
      <T v="label" color={t.ink3} style={{ textTransform: 'uppercase' }}>{L('加素材', 'Add')}</T>
      <View style={styles.grid}>
        {kinds.map((x) => {
          const lk = kindLook(t, x.look);
          return (
            <Pressable key={x.k} onPress={() => (x.k === 'file' ? files() : setView({ v: x.k } as View_))} disabled={!!busy} accessibilityRole="button"
              style={({ pressed }) => [styles.kind, { backgroundColor: t.surface, borderColor: t.line, opacity: pressed || (busy === 'files' && x.k === 'file') ? 0.6 : 1 }]}>
              <View style={[styles.tile, { backgroundColor: lk.soft }]}>{busy === 'files' && x.k === 'file' ? <ActivityIndicator size="small" color={lk.fg} /> : <x.Icon size={15} color={lk.fg} />}</View>
              <T v="callout" style={{ fontWeight: '600' }}>{x.label}</T>
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}

/** 挑素材：主对话里你说的（最近两周）、朋友列表 → 一个朋友的聊天、Zen 的主题和想法、收藏。多选，点「放进这一期」。 */
function Picker({ kind, friendId, friendName, onBack, onFriend, onAdd, have }: {
  kind: Exclude<View_['v'], 'list'>; friendId?: string; friendName?: string; onBack: () => void; onFriend: (f: PodPick) => void;
  onAdd: (list: { kind: MatKind; ref: string }[]) => Promise<void>; have: Set<string>;
}) {
  const t = useTheme();
  const [rows, setRows] = useState<PodPick[] | null>(null);
  const [sel, setSel] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [shownFor, setShownFor] = useState(`${kind}:${friendId ?? ''}`);
  if (shownFor !== `${kind}:${friendId ?? ''}`) { setShownFor(`${kind}:${friendId ?? ''}`); setRows(null); setSel([]); }  // 换了一页：先清空
  useEffect(() => {
    const load = kind === 'zen' ? Promise.all([pod.pick('topic'), pod.pick('idea')]).then(([a, b]) => [...a, ...b])
      : kind === 'friends' ? pod.pick('friend') : kind === 'friend' ? pod.pick('friend', friendId) : pod.pick(kind);
    let live = true;  // 点得快换了页：旧的那次回来不算
    load.then((r) => { if (live) setRows(r); }).catch((e) => { if (live) { setRows([]); showError(L('没读出来', "Couldn't load"), e); } });
    return () => { live = false; };
  }, [kind, friendId]);
  const title = { chat: L('主对话里你说的', 'What you said in chats'), friends: L('和谁的聊天', 'Which friend'), friend: L(`和${friendName ?? ''}的聊天`, `Chat with ${friendName ?? ''}`),
    zen: L('Zen 的主题和想法', 'Zen topics and thoughts'), save: L('收藏', 'Saved') }[kind];
  const toggle = (k: string) => setSel((s) => (s.includes(k) ? s.filter((x) => x !== k) : [...s, k]));
  const go = async () => {
    if (!sel.length || busy) return;
    setBusy(true);
    try {
      await onAdd(sel.map((k) => { const [kind, ...rest] = k.split('|'); return { kind: kind as MatKind, ref: rest.join('|') }; }));
    } catch (e) { showError(L('没放进来', "Couldn't add"), e); } finally { setBusy(false); }
  };
  const sub = (r: PodPick) => {
    const when = timeLabel(r.at);
    if (r.kind === 'chat') return [r.where, when].filter(Boolean).join(' · ');
    if (r.kind === 'friend') return [r.who === 'me' ? L('你', 'You') : r.who === 'agent' ? L('你的名片 agent', 'Your card agent') : r.agent ? L(`${r.where}的名片 agent`, `${r.where}'s card agent`) : r.where, when].filter(Boolean).join(' · ');
    if (r.kind === 'topic') return L(`主题 · ${r.n ?? 0} 条想法`, `Topic · ${r.n ?? 0} thoughts`);
    return when;
  };
  return (
    <View style={{ gap: space.md }}>
      <Pressable onPress={onBack} accessibilityRole="button" style={{ flexDirection: 'row', alignItems: 'center', gap: 2, alignSelf: 'flex-start' }}>
        <ChevronLeft size={20} color={t.gold} /><T v="callout" color={t.gold} style={{ fontWeight: '600' }}>{kind === 'friend' ? L('朋友', 'Friends') : L('素材', 'Materials')}</T>
      </Pressable>
      <T v="headline">{title}</T>
      {kind === 'friend' ? <T v="caption" color={t.ink3} style={{ fontWeight: '400', marginTop: -8 }}>{L(`${friendName}说的只在这一期里用，不原话进库和世界树。`, `What ${friendName} said stays in this episode, never quoted into the vault or memory tree.`)}</T> : null}
      {rows === null ? <ActivityIndicator color={t.gold} /> : !rows.length ? (
        <T v="callout" color={t.ink3}>{kind === 'friends' ? L('还没有和朋友聊过。', 'No friend chats yet.') : L('这里还没有东西。', 'Nothing here yet.')}</T>
      ) : (
        <View style={[styles.box, { backgroundColor: t.surface, borderColor: t.line }]}>
          {rows.map((r, i) => {
            const k = `${r.kind}|${r.ref}`;
            const on = sel.includes(k);
            const already = have.has(`${r.kind}:${r.ref}`);
            const lk = kindLook(t, r.kind);
            const line = i ? { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line } : null;
            if (r.kind === 'friends') {
              return (
                <Pressable key={k} onPress={() => onFriend(r)} accessibilityRole="button" style={({ pressed }) => [styles.item, line, { opacity: pressed ? 0.6 : 1 }]}>
                  <View style={[styles.tile, { backgroundColor: lk.soft }]}><Users size={15} color={lk.fg} /></View>
                  <View style={{ flex: 1, gap: 2 }}>
                    <T v="callout" style={{ fontWeight: '600' }}>{r.text}</T>
                    <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L(`${r.n ?? 0} 条 · 最近 ${timeLabel(r.at)}`, `${r.n ?? 0} messages · last ${timeLabel(r.at)}`)}</T>
                  </View>
                  <ChevronRight size={16} color={t.ink3} />
                </Pressable>
              );
            }
            return (
              <Pressable key={k} onPress={() => !already && toggle(k)} disabled={already} accessibilityRole="checkbox" accessibilityState={{ checked: on || already, disabled: already }}
                style={({ pressed }) => [styles.item, line, { opacity: already ? 0.5 : pressed ? 0.7 : 1 }]}>
                <View style={[styles.check, { borderColor: on || already ? t.goldFill : t.ink3, backgroundColor: on || already ? t.goldFill : 'transparent' }]}>
                  {on || already ? <Check size={13} color={t.onGold} strokeWidth={3} /> : null}
                </View>
                <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
                  <T v="callout" numberOfLines={3}>{r.text || r.title}</T>
                  <T v="caption" color={t.ink3} style={{ fontWeight: '400' }} numberOfLines={1}>{already ? L('已经放进来了', 'Already added') : sub(r)}</T>
                </View>
              </Pressable>
            );
          })}
        </View>
      )}
      {kind !== 'friends' ? <Btn label={busy ? L('正在放…', 'Adding…') : sel.length ? L(`放进这一期 · ${sel.length}`, `Add to this episode · ${sel.length}`) : L('选几条', 'Pick some')} onPress={go} /> : null}
    </View>
  );
}

/** 长按一条「放进播客」：新开一期（标题取这一条的开头；朋友说的不拿来当标题），或者放进最近的一期。 */
export function PutInPodcast({ kind, target, close, onOpen }: { kind: MatKind; target: string; close: () => void; onOpen: (e: PodBrief) => void }) {
  const t = useTheme();
  const [eps, setEps] = useState<PodBrief[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [done, setDone] = useState<{ episode: PodBrief; count: number } | null>(null);
  useEffect(() => {
    pod.home().then((h) => setEps(h.episodes.filter((e) => e.status !== 'processing').slice(0, 6))).catch(() => setEps([]));
  }, []);
  const put = async (episode?: string) => {
    if (busy) return;
    setBusy(episode ?? 'new');
    try {
      const r = await pod.quick(kind, target, episode);
      setDone({ episode: r.episode, count: r.count });
    } catch (e) { showError(L('没放进去', "Couldn't add it"), e); } finally { setBusy(null); }
  };
  if (done) {
    return (
      <View style={{ gap: space.md }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <Check size={18} color={t.good} strokeWidth={2.5} />
          <T v="headline" color={t.good} style={{ flex: 1 }}>{L(`放进了「${done.episode.title}」`, `Added to “${done.episode.title}”`)}</T>
        </View>
        <T v="callout" color={t.ink2}>{L(`这一期现在有 ${done.count} 条素材。录前聊天、主持人追问、整理都会参考。`, `This episode now has ${done.count} materials, used in prep, questions and the note.`)}</T>
        <View style={{ flexDirection: 'row', gap: space.sm }}>
          <Btn label={L('好', 'OK')} kind="quiet" flex onPress={close} />
          <Btn label={L('去这一期', 'Open it')} flex onPress={() => { close(); onOpen(done.episode); }} />
        </View>
      </View>
    );
  }
  return (
    <View style={{ gap: space.sm }}>
      <Pressable onPress={() => put()} disabled={!!busy} accessibilityRole="button" style={({ pressed }) => [styles.ep, { backgroundColor: t.goldSoft, opacity: pressed ? 0.7 : 1 }]}>
        {busy === 'new' ? <ActivityIndicator size="small" color={t.gold} /> : <Plus size={18} color={t.gold} />}
        <T v="headline" color={t.gold} style={{ flex: 1 }}>{L('新开一期', 'New episode')}</T>
      </Pressable>
      {eps === null ? <ActivityIndicator color={t.gold} /> : eps.map((e) => (
        <Pressable key={e.id} onPress={() => put(e.id)} disabled={!!busy} accessibilityRole="button" style={({ pressed }) => [styles.ep, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
          {busy === e.id ? <ActivityIndicator size="small" color={t.ink2} /> : <Mic size={16} color={t.ink2} />}
          <View style={{ flex: 1, minWidth: 0 }}>
            <T v="callout" numberOfLines={1} style={{ fontWeight: '600' }}>{e.title}</T>
            <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{[timeLabel(e.createdAt), e.chip?.text].filter(Boolean).join(' · ')}</T>
          </View>
        </Pressable>
      ))}
      <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{kind === 'friend'
        ? L('朋友说的只在这一期里用：不原话进库和世界树，存进库的笔记只写「参考了和谁的聊天」。', "A friend's words stay in that episode: never quoted into the vault or memory tree.")
        : L('录前聊天、主持人追问、录完整理都会参考它。', "Used in prep, the host's questions and the note.")}</T>
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: 8, borderRadius: radius.md, borderWidth: StyleSheet.hairlineWidth, paddingHorizontal: 12, height: 42 },
  box: { borderRadius: 14, borderWidth: StyleSheet.hairlineWidth, overflow: 'hidden' },
  item: { flexDirection: 'row', alignItems: 'flex-start', gap: 10, padding: 12 },
  tile: { width: 30, height: 30, borderRadius: 9, alignItems: 'center', justifyContent: 'center' },
  x: { width: 28, height: 28, alignItems: 'center', justifyContent: 'center' },
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  kind: { flexDirection: 'row', alignItems: 'center', gap: 8, borderRadius: 12, borderWidth: StyleSheet.hairlineWidth, paddingHorizontal: 10, paddingVertical: 8, flexBasis: '47%', flexGrow: 1 },
  check: { width: 22, height: 22, borderRadius: 11, borderWidth: 1.5, alignItems: 'center', justifyContent: 'center', marginTop: 1 },
  ep: { flexDirection: 'row', alignItems: 'center', gap: 10, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 11 },
});
