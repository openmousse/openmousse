// Zen 的「想法」一边（2026-10-13 重排，服务器 supports('think.home')；老服务器还是 ThinkLegacy 的三栏）。
// 从上到下：冥想结束的提示 → 在想（讨论中的主题，横排卡片）→ 记下的（想法和收藏放在一起，按天分组，一句话就是一行）→ 已想完一行。
// 每条都有不经模型的出口：左滑 完成 / 删除；长按进多选，底部一排 归入主题 / 讨论 / 放进播客 / 写成笔记 / 完成 / 删除。
// 主线第三步（服务器 supports('think.discuss')，赛博或群聊开着）：只选了一条时「讨论」是「拿去讨论」：和一位赛博、新建群聊、放进播客、交给功能 Agent、
// 发给好友，或者照旧和助手讨论（IdeaDiscussSheet）；拿去讨论过的想法行尾带「讨论中」，点它去那里。
import React, { useEffect, useRef, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, TextInput, View } from 'react-native';
import Reanimated from 'react-native-reanimated';
import { useNavigation } from '@react-navigation/native';
import { Archive, BookOpen, Check, ChevronRight, CircleCheck, Lightbulb, MessageCircle, Mic, Moon, Trash2 } from '../components/icons';
import { ConfirmDelete, MenuItem, SwipeRow, rowTime, type SwipeAction } from '../components/ChatParts';
import { dismissMode, useBottomInset } from '../components/keyboard';
import { useSheet } from '../components/Sheet';
import { Btn, PullRefresh, T, showError } from '../components/ui';
import * as thinkApi from '../api/think';
import type { DayItem, Fragment, TopicCard } from '../api/think';
import { useCybersOn } from '../api/cybers';
import { useGroupsOn, type IdeaDiscussion } from '../api/groups';
import { supports } from '../api/version';
import { IdeaDiscussSheet, openDiscussion } from '../components/agents/IdeaDiscuss';
import { agentName } from '../brand';
import { L } from '../i18n';
import { useStore } from '../store';
import { radius, space, type, useTheme } from '../theme';
import { CaptureBar } from './CaptureBar';
import { FragmentSheet } from './FragmentSheet';
import { PutInPodcast } from './Materials';
import { openEpisode } from './Podcast';
import { TopicActionsSheet } from './TopicSheets';
import { itemId, useThink } from './ThinkStore';
import { openZenSummary } from './Zen';
import { ShelfCard, TypeTile, ZenHead, dayLabel, fileLine } from './parts';

const firstLine = (s: string) => s.split('\n').map((x) => x.trim()).find(Boolean) ?? '';
const host = (url: string) => url.replace(/^https?:\/\//, '').replace(/^www\./, '').split('/')[0];
const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`;
const from = (source: string) => (source === 'obsidian' ? L('来自 Obsidian', 'from Obsidian') : source && source !== 'app' ? L(`来自 ${source}`, `from ${source}`) : '');

/** 一行怎么写：一句话的想法就是一行字；文件、长文、链接、语音、照片、收藏才带图标和第二行。 */
export function describe(x: DayItem): { text: string; sub: string; rich: boolean; kind: string; unread: boolean } {
  if (x.type === 'save') {
    const s = x.save;
    const what = s.kind === 'file' ? fileLine(s.name ?? '', s.size) : s.kind === 'image' ? L('图片', 'Image') : s.kind === 'chat' ? L('对话', 'Chat')
      : s.kind === 'link' ? L('链接', 'Link') : L('文字', 'Text');
    const src = s.source ? (s.kind === 'link' ? s.source : L(`来自${s.source}`, `from ${s.source}`)) : '';
    return { text: s.title || s.name || L('（无标题）', '(No title)'), sub: [what, src].filter(Boolean).join(' · '), rich: true, kind: s.kind, unread: !s.seen };
  }
  const f: Fragment = x.idea;
  const plain = (f.kind === 'text' || f.kind === 'keywords') && !f.files.length && !f.title && !f.url;
  if (plain) {
    const text = f.kind === 'keywords' ? f.keywords.map((k) => `#${k}`).join(' ') : firstLine(f.text);
    return { text, sub: '', rich: false, kind: f.kind, unread: false };
  }
  const photos = f.files.filter((y) => y.kind === 'photo').length;
  const doc = f.files.find((y) => y.kind !== 'photo' && y.kind !== 'voice');
  let text = f.title || firstLine(f.text);
  let what = '';
  switch (f.kind) {
    case 'voice': text = f.text ? L(`「${firstLine(f.text)}」`, `“${firstLine(f.text)}”`) : L('语音', 'Voice note'); what = [L('语音', 'Voice'), f.duration ? mmss(f.duration) : ''].filter(Boolean).join(' · '); break;
    case 'long': what = L(`长文 · ${f.chars} 字`, `Long piece · ${f.chars} chars`); break;
    case 'photo': text = text || L('照片', 'Photo'); what = L(`照片 · ${photos} 张`, `${photos} photo${photos === 1 ? '' : 's'}`); break;
    case 'link': text = f.linkTitle || text || host(f.url); what = [L('链接', 'Link'), host(f.url)].join(' · '); break;
    case 'save': what = L('收藏', 'Saved'); break;
    default: what = doc ? fileLine(doc.name, doc.size) : photos ? L(`照片 · ${photos} 张`, `${photos} photo${photos === 1 ? '' : 's'}`) : '';
  }
  if (!text && doc) text = doc.name;
  return { text: text || L('（空）', '(Empty)'), sub: [what, from(f.source)].filter(Boolean).join(' · '), rich: true, kind: f.kind === 'text' && doc ? 'file' : f.kind, unread: false };
}

export function Row({ x, first, selecting, on, onPress, onLong }: { x: DayItem; first: boolean; selecting: boolean; on: boolean; onPress: () => void; onLong?: () => void }) {
  const t = useTheme();
  const r = describe(x);
  // 拿去讨论过（服务器 think.frag_json 的 discussion）：行尾「讨论中」，点它去那里（一样东西只有一个家）
  const disc = x.type === 'idea' ? (x.idea as Fragment & { discussion?: Pick<IdeaDiscussion, 'kind' | 'id'> | null }).discussion : null;
  return (
    <Pressable onPress={onPress} onLongPress={selecting || !onLong ? undefined : onLong} delayLongPress={400}
      accessibilityRole={selecting ? 'checkbox' : 'button'} accessibilityState={selecting ? { checked: on } : undefined}
      accessibilityLabel={[r.text, r.sub, r.unread ? L('未读', 'unread') : ''].filter(Boolean).join(L('，', ', '))}
      accessibilityHint={selecting || !onLong ? undefined : L('左滑：完成或删除；长按：多选', 'Swipe left to finish or delete; long-press to select')}
      style={({ pressed }) => [styles.row, { backgroundColor: on ? t.goldSoft : t.surface, borderTopColor: first ? 'transparent' : t.line, paddingVertical: r.rich ? 10 : 12, opacity: pressed ? 0.85 : 1 }]}>
      {selecting ? (
        <View style={[styles.circle, on ? { backgroundColor: t.goldFill, borderColor: t.goldFill } : { borderColor: t.ink3 }]}>
          {on ? <Check size={13} color={t.onGold} strokeWidth={3} /> : null}
        </View>
      ) : null}
      {r.rich ? <TypeTile kind={r.kind} size={34} /> : null}
      <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
        <T v="body" numberOfLines={1} style={{ fontSize: 15, fontWeight: r.rich ? '600' : '400' }}>{r.text}</T>
        {r.sub ? <T v="caption" color={t.ink3} numberOfLines={1} style={{ fontSize: 12.5, fontWeight: '400' }}>{r.sub}</T> : null}
      </View>
      {disc && !selecting ? (
        <Pressable onPress={() => openDiscussion(disc)} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('讨论中，打开', 'Being discussed; open it')}
          style={[styles.tag, { backgroundColor: t.goldSoft }]}>
          <T v="caption" color={t.gold} style={{ fontSize: 11.5, fontWeight: '700' }}>{L('讨论中', 'Discussing')}</T>
        </Pressable>
      ) : null}
      {r.unread ? <View style={[styles.dot, { backgroundColor: t.cyan }]} /> : null}
    </Pressable>
  );
}

type SelAct = 'topic' | 'talk' | 'podcast' | 'note' | 'done' | 'delete';

/** 多选时代替输入栏的一排：归入主题 / 讨论 / 放进播客 / 写成笔记 / 完成 / 删除。 */
function SelectBar({ n, busy, onAct, discuss }: { n: number; busy: boolean; onAct: (a: SelAct) => void; discuss?: boolean }) {
  const t = useTheme();
  const acts: [SelAct, string, typeof Mic, string][] = [
    ['topic', L('归入主题', 'To topic'), Lightbulb, t.gold],
    ['talk', discuss && n === 1 ? L('拿去讨论', 'Discuss…') : L('讨论', 'Discuss'), MessageCircle, t.ink],
    ['podcast', L('放进播客', 'To podcast'), Mic, t.ink],
    ['note', L('写成笔记', 'Write up'), BookOpen, t.ink],
    ['done', L('完成', 'Done'), CircleCheck, t.ink],
    ['delete', L('删除', 'Delete'), Trash2, t.bad],
  ];
  return (
    <View style={[styles.selBar, { backgroundColor: t.surface, borderTopColor: t.line }]}>
      <View style={{ alignItems: 'center' }}>
        <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L('归入主题、放进播客、完成：不调用模型', 'To topic, To podcast, Done: no model involved')}</T>
        <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L(`讨论、写成笔记：交给 ${agentName()}`, `Discuss, Write up: handled by ${agentName()}`)}</T>
      </View>
      <View style={styles.selGrid}>
        {acts.map(([k, label, Icon, color]) => (
          <Pressable key={k} onPress={() => onAct(k)} disabled={!n || busy} accessibilityRole="button" accessibilityLabel={label}
            style={({ pressed }) => [styles.selBtn, { opacity: !n || busy ? 0.4 : pressed ? 0.6 : 1 }]}>
            <Icon size={22} color={color} />
            <T v="caption" color={color} numberOfLines={1} style={{ fontSize: 11.5, fontWeight: '600' }}>{label}</T>
          </Pressable>
        ))}
      </View>
    </View>
  );
}

/** 归入主题：挑一个在想的主题，或者新建一个（只归类，不调用模型）。 */
function TopicPick({ topics, close, onPick }: { topics: TopicCard[]; close: () => void; onPick: (tid: string | null, name: string) => Promise<void> }) {
  const t = useTheme();
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const go = (tid: string | null) => {
    if (busy) return;
    setBusy(true);
    onPick(tid, name.trim()).then(close).catch((e) => { setBusy(false); showError(L('归入失败', "Couldn't add"), e); });
  };
  return (
    <View style={{ gap: space.sm }}>
      {topics.map((tp) => (
        <MenuItem key={tp.id} icon={Lightbulb} title={tp.title} desc={L(`${tp.count} 条 · ${tp.state}`, `${tp.count} · ${tp.state}`)} onPress={() => go(tp.id)} />
      ))}
      <TextInput value={name} onChangeText={setName} placeholder={L('新主题名称（可留空）', 'New topic name (optional)')} placeholderTextColor={t.ink3}
        accessibilityLabel={L('新主题名称', 'New topic name')} style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink }]} />
      <Btn label={busy ? L('正在归入…', 'Adding…') : L('新建主题', 'New topic')} onPress={() => go(null)} />
      <T v="caption" color={t.ink3}>{L('只归类，不调用模型。之后可在「在想」中打开讨论或写成笔记。', 'Only groups them; no model is called. Open it from Thinking to discuss or write it up.')}</T>
    </View>
  );
}

/** 「+」里的「讨论一个话题」：写一句想聊什么，开一个主题，它先提问。 */
export function NewTopicSheet({ close, onOpen }: { close: () => void; onOpen: (tid: string) => void }) {
  const t = useTheme();
  const { refreshHome } = useThink();
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const go = async () => {
    const v = text.trim();
    if (!v || busy) return;
    setBusy(true);
    try {
      const f = await thinkApi.addFragment({ kind: 'text', text: v });
      const tp = await thinkApi.createTopic([f.id], v.slice(0, 40));
      await thinkApi.talk(tp.id);
      refreshHome();
      close();
      onOpen(tp.id);
    } catch (e) { showError(L('无法开始', "Couldn't start"), e); setBusy(false); }
  };
  return (
    <View style={{ gap: space.md }}>
      <TextInput value={text} onChangeText={setText} autoFocus placeholder={L('想讨论什么？一句话即可', 'What do you want to think through? One line is enough')}
        placeholderTextColor={t.ink3} returnKeyType="go" onSubmitEditing={go} accessibilityLabel={L('话题', 'Topic')}
        style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink }]} />
      <Btn label={busy ? L('正在开始…', 'Starting…') : L('开始讨论', 'Start')} onPress={go} />
      <T v="caption" color={t.ink3}>{L(`${agentName()} 先提问，帮你把想法理清；结束后可写成笔记。这句话也会记在主题里。`, `${agentName()} asks first to help you think it through; afterwards you can write it up. This line is kept in the topic.`)}</T>
    </View>
  );
}

export function ZenIdeas({ sel, setSel }: { sel: string[] | null; setSel: (v: string[] | null) => void }) {
  const t = useTheme();
  const nav = useNavigation<any>();
  const sheet = useSheet();
  const { connected, booting } = useStore();
  const { home, homeError, refreshHome, unseenFocus, finish, removeFragment, removeSave } = useThink();
  const [busy, setBusy] = useState(false);
  const [undo, setUndo] = useState<{ ids: string[]; text: string } | null>(null);
  const cybersOn = useCybersOn();
  const groupsOn = useGroupsOn();
  const canDiscuss = supports('think.discuss') && (cybersOn === true || groupsOn === true);  // 赛博、群聊都关着：服务器那边是 404，照旧「和助手讨论」
  const root = useRef<View>(null);
  const bottom = useBottomInset(root);
  useEffect(() => {
    if (!undo) return undefined;
    const h = setTimeout(() => setUndo(null), 6000);
    return () => clearTimeout(h);
  }, [undo]);

  const items = home?.items ?? [];
  const thinking = home?.thinking ?? [];
  const selecting = sel !== null;
  const days: [string, DayItem[]][] = [];
  for (const x of items) {
    const day = x.type === 'idea' ? x.idea.day : x.save.day;
    const last = days[days.length - 1];
    if (last && last[0] === day) last[1].push(x); else days.push([day, [x]]);
  }
  const label = (x: DayItem) => describe(x).text;

  const open = (x: DayItem) => {
    if (x.type === 'save') { nav.navigate('Save', { id: x.save.id }); return; }
    const f = x.idea;
    sheet.open({ title: f.title || L('想法', 'Thought'), content: (close) => <FragmentSheet id={f.id} initial={f} close={close} /> });
  };
  const toggle = (id: string) => setSel(sel?.includes(id) ? sel.filter((x) => x !== id) : [...(sel ?? []), id]);
  const done = async (list: DayItem[]) => {
    const ids = list.map(itemId);
    try {
      await finish(ids);
      setUndo({ ids, text: ids.length === 1 ? L(`已完成「${label(list[0])}」`, `Finished “${label(list[0])}”`) : L(`已完成 ${ids.length} 条`, `Finished ${ids.length}`) });
    } catch (e) { showError(L('操作失败', "Couldn't finish"), e); }
  };
  const remove = (list: DayItem[], after?: () => void) => sheet.open({
    title: list.length === 1 ? L('删除这一条？', 'Delete this?') : L(`删除 ${list.length} 条？`, `Delete ${list.length}?`),
    content: (close) => (
      <ConfirmDelete close={close} label={L('删除', 'Delete')}
        body={L('想法移入库的回收站（Obsidian 的 .trash），收藏从列表中移除。', "Thoughts go to the vault's trash (Obsidian's .trash); saved items are removed from the list.")}
        run={async () => {
          for (const x of list) await (x.type === 'idea' ? removeFragment(x.idea.id) : removeSave(x.save.id));
          refreshHome();
          after?.();
        }} />
    ),
  });
  const swipe = (x: DayItem): SwipeAction[] => [
    { key: 'done', label: L('完成', 'Done'), icon: <Check size={18} color={t.onGold} strokeWidth={2.6} />, tone: 'gold', onPress: () => { done([x]); } },
    { key: 'delete', label: L('删除', 'Delete'), icon: <Trash2 size={18} color="#FFFFFF" />, tone: 'danger', onPress: () => remove([x]) },
  ];

  // 多选的几样：讨论 / 写成笔记 / 归入主题要的是想法，选中的收藏先「放进思考」变成一条想法（不调模型）
  const picked = items.filter((x) => sel?.includes(itemId(x)));
  const ideaIds = async () => {
    const out: string[] = [];
    for (const x of picked) out.push(x.type === 'idea' ? x.idea.id : (await thinkApi.saveToIdea(x.save.id)).id);
    return out;
  };
  const act = async (a: SelAct) => {
    if (!picked.length || busy) return;
    if (a === 'done') { setSel(null); done(picked); return; }
    if (a === 'delete') { remove(picked, () => setSel(null)); return; }
    if (a === 'podcast') {
      const refs = picked.map((x) => (x.type === 'idea' ? { kind: 'idea' as const, target: x.idea.id } : { kind: 'save' as const, target: x.save.id }));
      sheet.open({ title: L('放进播客', 'Add to a podcast'), content: (close) => (
        <PutInPodcast kind={refs[0].kind} target={refs[0].target} more={refs.slice(1)} close={() => { close(); setSel(null); }} onOpen={(e) => openEpisode(nav, e)} />
      ) });
      return;
    }
    if (a === 'topic') {
      sheet.open({ title: L('归入主题', 'Add to a topic'), content: (close) => (
        <TopicPick topics={thinking} close={close} onPick={async (tid, name) => {
          const ids = await ideaIds();
          if (tid) await thinkApi.patchTopic(tid, { add: ids }); else await thinkApi.createTopic(ids, name || undefined);
          setSel(null);
          refreshHome();
        }} />
      ) });
      return;
    }
    const toTopic = async (ids: string[]) => {
      const tp = await thinkApi.createTopic(ids);
      if (a === 'talk') await thinkApi.talk(tp.id); else await thinkApi.done(tp.id);
      setSel(null);
      refreshHome();
      nav.navigate(a === 'talk' ? 'ThinkTalk' : 'ThinkDone', { id: tp.id });
    };
    setBusy(true);
    try {
      const ids = await ideaIds();
      if (a === 'talk' && ids.length === 1 && canDiscuss) {  // 只选了一条：拿去讨论（去哪由你挑；原来的「和助手讨论」是最后一行）
        sheet.open({ title: L('拿去讨论', 'Take it to discuss'), content: (close) => (
          <IdeaDiscussSheet idea={ids[0]} close={close} onDone={() => { setSel(null); refreshHome(); }}
            onTalk={() => { toTopic(ids).catch((e) => showError(L('无法开始', "Couldn't start"), e)); }} />
        ) });
        return;
      }
      await toTopic(ids);
    } catch (e) { showError(L('无法开始', "Couldn't start"), e); } finally { setBusy(false); }
  };
  const openTopicCard = (tp: TopicCard) => nav.navigate(tp.draftStatus === 'ready' ? 'ThinkDone' : 'ThinkTalk', { id: tp.id });

  const empty = !home ? (booting ? L('正在连接服务器…', 'Connecting to the server…') : !connected ? L('未连接服务器。', 'Not connected to the server.') : homeError ?? L('正在加载…', 'Loading…')) : null;
  return (
    <Reanimated.View ref={root} onLayout={bottom.onLayout} style={[{ flex: 1 }, bottom.style]}>
      <View style={{ flex: 1 }}>
        <ScrollView contentContainerStyle={{ paddingHorizontal: space.lg, paddingBottom: undo ? 80 : space.xl }} keyboardShouldPersistTaps="handled" keyboardDismissMode={dismissMode}
          refreshControl={<PullRefresh onRefresh={refreshHome} />}>
          {unseenFocus && !selecting ? (
            <Pressable onPress={() => openZenSummary(nav, unseenFocus.id)} accessibilityRole="button" style={({ pressed }) => [styles.notice, { backgroundColor: t.lensField, opacity: pressed ? 0.85 : 1 }]}>
              <Moon size={16} color={t.goldFill} />
              <T v="callout" color="#E8EAED" style={{ flex: 1 }}>{unseenFocus.held ? L(`冥想已结束，暂缓的 ${unseenFocus.held} 条通知见小结`, `Focus time ended; ${unseenFocus.held} held in the summary`) : L('冥想已结束', 'Focus time ended')}</T>
              <T v="callout" color={t.goldFill} style={{ fontWeight: '600' }}>{L('查看小结', 'Summary')}</T>
            </Pressable>
          ) : null}
          {thinking.length && !selecting ? (
            <>
              <ZenHead title={L(`在想 · ${thinking.length}`, `Thinking · ${thinking.length}`)} right={L('按最近一次推进排列', 'Latest progress first')} />
              <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginHorizontal: -space.lg }} contentContainerStyle={{ paddingHorizontal: space.lg, gap: 10 }}>
                {thinking.map((tp) => (
                  <ShelfCard key={tp.id} title={tp.title} date={rowTime(tp.lastAt ?? tp.updatedAt)} Icon={Lightbulb} tint={t.tints.gold} stage={tp.stage} state={tp.state} waiting={tp.waiting}
                    steps={L('记下、讨论、草稿、存入', 'noted, discussed, drafted, saved')} onPress={() => openTopicCard(tp)}
                    onLongPress={() => sheet.open({ title: tp.title, content: (close) => <TopicActionsSheet key={tp.id} topic={tp} close={close} onDone={refreshHome} /> })} />
                ))}
              </ScrollView>
            </>
          ) : null}
          {home ? <ZenHead title={L(`记下的 · ${home.count}`, `Noted · ${home.count}`)} right={selecting ? undefined : L('新的在前', 'Newest first')} /> : null}
          {days.map(([day, list]) => (
            <View key={day}>
              <T v="caption" color={t.ink3} style={styles.day}>{dayLabel(day)}</T>
              <View style={[styles.card, { backgroundColor: t.surface }]}>
                {list.map((x, i) => {
                  const id = itemId(x);
                  const row = <Row x={x} first={i === 0} selecting={selecting} on={!!sel?.includes(id)} onPress={() => (selecting ? toggle(id) : open(x))} onLong={() => setSel([id])} />;
                  return selecting ? <View key={id}>{row}</View> : <SwipeRow key={id} actions={swipe(x)}>{row}</SwipeRow>;
                })}
              </View>
            </View>
          ))}
          {empty ? <T v="callout" color={t.ink3} style={styles.empty}>{empty}</T> : !items.length ? (
            <T v="callout" color={t.ink3} style={styles.empty}>
              {L('随手记录想法：一句话、几个关键词、一段语音或一张照片均可。这些内容不会发送给 Agent。', 'Capture anything: a sentence, a few keywords, a voice note, a photo. Nothing is sent to the Agent.')}
            </T>
          ) : null}
          {home?.finished && !selecting ? (
            <Pressable onPress={() => nav.navigate('ThinkFinished')} accessibilityRole="button" style={({ pressed }) => [styles.finished, { opacity: pressed ? 0.6 : 1 }]}>
              <Archive size={18} color={t.ink3} />
              <T v="callout" color={t.ink2} style={{ flex: 1 }}>{L(`已想完 · ${home.finished}`, `Done thinking · ${home.finished}`)}</T>
              <ChevronRight size={16} color={t.ink3} />
            </Pressable>
          ) : null}
          {home?.vault && items.length && !selecting ? (
            <T v="caption" color={t.ink3} style={{ textAlign: 'center', paddingTop: space.sm, fontWeight: '400' }}>
              {L(`每条想法都是库中「${home.folder}」下的一篇笔记，也可在 Obsidian 中查看`, `Each thought is a note in the vault's "${home.folder}", also in Obsidian`)}
            </T>
          ) : null}
        </ScrollView>
        {undo ? (
          <View style={[styles.undo, { backgroundColor: t.surface2 }]}>
            <Check size={17} color={t.good} strokeWidth={2.6} />
            <T v="callout" numberOfLines={1} style={{ flex: 1 }}>{undo.text}</T>
            <Pressable onPress={() => { const ids = undo.ids; setUndo(null); finish(ids, true).catch((e) => showError(L('撤销失败', "Couldn't undo"), e)); }}
              hitSlop={8} accessibilityRole="button" style={{ paddingHorizontal: 10, paddingVertical: 6 }}>
              <T v="callout" color={t.gold} style={{ fontWeight: '600' }}>{L('撤销', 'Undo')}</T>
            </Pressable>
          </View>
        ) : null}
      </View>
      {selecting ? <SelectBar n={picked.length} busy={busy} onAct={act} discuss={canDiscuss} /> : <CaptureBar />}
    </Reanimated.View>
  );
}

/** 多选时的页头：取消 · 已选 N 条 · 全选。 */
export function SelectHeader({ n, all, onCancel, onAll }: { n: number; all: boolean; onCancel: () => void; onAll: () => void }) {
  const t = useTheme();
  return (
    <View style={[styles.selHead, { borderBottomColor: t.line }]}>
      <Pressable onPress={onCancel} hitSlop={8} accessibilityRole="button"><T v="headline" color={t.gold}>{L('取消', 'Cancel')}</T></Pressable>
      <T v="headline">{L(`已选 ${n} 条`, `${n} selected`)}</T>
      <Pressable onPress={onAll} hitSlop={8} accessibilityRole="button"><T v="headline" color={t.gold}>{all ? L('全不选', 'None') : L('全选', 'All')}</T></Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 14, borderTopWidth: StyleSheet.hairlineWidth },
  circle: { width: 22, height: 22, borderRadius: 11, borderWidth: 1.5, alignItems: 'center', justifyContent: 'center' },
  dot: { width: 8, height: 8, borderRadius: 4 },
  tag: { borderRadius: 9, paddingHorizontal: 7, height: 20, justifyContent: 'center' },
  day: { fontWeight: '600', paddingHorizontal: 4, paddingTop: 10, paddingBottom: 6 },
  card: { borderRadius: 14, overflow: 'hidden' },
  empty: { textAlign: 'center', paddingVertical: space.xxl, paddingHorizontal: space.lg },
  finished: { flexDirection: 'row', alignItems: 'center', gap: 10, height: 48, marginTop: space.md, paddingHorizontal: 4 },
  notice: { flexDirection: 'row', alignItems: 'center', gap: 10, borderRadius: radius.md, paddingHorizontal: 14, paddingVertical: 12, marginTop: 8 },
  undo: { position: 'absolute', left: space.lg, right: space.lg, bottom: 10, height: 46, borderRadius: 14, flexDirection: 'row', alignItems: 'center', gap: 10, paddingLeft: 14, paddingRight: 4 },
  selBar: { borderTopWidth: StyleSheet.hairlineWidth, paddingTop: 8, paddingHorizontal: 8, paddingBottom: 6, gap: 6 },
  selGrid: { flexDirection: 'row' },
  selBtn: { flex: 1, height: 54, alignItems: 'center', justifyContent: 'center', gap: 4 },
  selHead: { height: 52, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: space.lg, borderBottomWidth: StyleSheet.hairlineWidth },
  input: { borderRadius: radius.md, paddingHorizontal: space.lg, paddingVertical: 12 },
});
