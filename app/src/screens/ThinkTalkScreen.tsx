// 聊聊：一个思考主题的对话（线程 id = 主题 id，不进主对话）。顶上是它读的那几条碎片；输入框上面切「说给它 / 只记下」。
// 想完了：它把碎片和聊过的整理成笔记草稿，你改完存进库（笔记 / 写作），要做的进日程，要记的进世界树。
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Linking, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { useNavigation, useRoute } from '@react-navigation/native';
import { CalendarPlus, Check, ChevronDown, ChevronLeft, ChevronUp, Layers, Lightbulb, NotebookText, Plus, TreeDeciduous, X } from '../components/icons';
import { ChatView } from '../components/ChatView';
import { useSheet } from '../components/Sheet';
import { Btn, Screen, SectionLabel, T, showError } from '../components/ui';
import * as thinkApi from '../api/think';
import * as sched from '../api/schedule';
import type { Draft, Fragment, Topic } from '../api/think';
import { L } from '../i18n';
import { useStore, useThreadOnScreen } from '../store';
import { radius, space, type, useTheme } from '../theme';
import { FragmentSheet } from '../think/FragmentSheet';
import { GrowInput, KeywordChip, TypeTile } from '../think/parts';
import { useThink } from '../think/ThinkStore';

function Header({ title, sub, onBack, right, icon }: { title: string; sub: string; onBack: () => void; right?: React.ReactNode; icon: React.ReactNode }) {
  const t = useTheme();
  return (
    <View style={[styles.head, { borderBottomColor: t.line }]}>
      <Pressable onPress={onBack} hitSlop={10} accessibilityRole="button" accessibilityLabel={L('返回', 'Back')} style={styles.back}>
        <ChevronLeft size={26} color={t.gold} />
      </Pressable>
      {icon}
      <View style={{ flex: 1, minWidth: 0 }}>
        <T v="headline" numberOfLines={1}>{title}</T>
        <T v="caption" color={t.ink3} numberOfLines={1}>{sub}</T>
      </View>
      {right}
    </View>
  );
}

const srcLabel = (f: Fragment | undefined) => (!f ? '' : f.note ? L('只记下的', 'private note') : f.kind === 'long' ? L('长文', 'long piece') : f.kind === 'keywords' ? L('关键词', 'keywords') : f.kind === 'voice' ? L('语音', 'voice') : f.time);

export function ThinkTalkScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const route = useRoute<any>();
  const sheet = useSheet();
  const id = route.params?.id as string;
  const { refreshThread } = useStore();
  const { capture, stream } = useThink();
  const [topic, setTopic] = useState<Topic | null>(null);
  const [open, setOpen] = useState(true);
  const [mode, setMode] = useState<'it' | 'note'>('it');
  useThreadOnScreen(id);
  const load = useCallback(() => thinkApi.getTopic(id).then(setTopic).catch((e) => showError(L('读不到这个主题', "Couldn't load this topic"), e)), [id]);
  useEffect(() => { load(); refreshThread(id).catch(() => {}); }, [id, load, refreshThread]);

  const talking = topic?.fragments.filter((f) => !f.note) ?? [];
  const note = (text: string) => capture({ text, topic: id }).then(() => { refreshThread(id).catch(() => {}); load(); });
  const finish = async () => {
    try { await thinkApi.done(id); nav.navigate('ThinkDone', { id }); } catch (e) { showError(L('没开始整理', "Couldn't start"), e); }
  };
  const rename = () => sheet.open({ title: L('主题叫什么', 'Topic name'), content: (close) => <RenameSheet topic={topic} close={close} onDone={load} /> });
  const pullMore = () => sheet.open({ title: L('再拉几条进来', 'Add more thoughts'), content: (close) => <PullSheet id={id} have={topic?.fragments.map((f) => f.id) ?? []} options={stream?.fragments ?? []} close={close} onDone={load} /> });
  const reopen = () => thinkApi.patchTopic(id, { status: 'open' }).then(setTopic).catch((e) => showError(L('没打开', "Couldn't reopen"), e));

  const toggle = (
    <View style={[styles.modeRow]}>
      <View style={[styles.seg, { backgroundColor: t.surface2 }]}>
        {(['it', 'note'] as const).map((m) => (
          <Pressable key={m} onPress={() => setMode(m)} accessibilityRole="button" accessibilityState={{ selected: mode === m }}
            style={[styles.segItem, mode === m && { backgroundColor: t.surface }]}>
            <Text style={[type.callout, { fontWeight: '600', color: mode === m ? t.ink : t.ink2 }]}>{m === 'it' ? L('说给它', 'Tell it') : L('只记下', 'Just note')}</Text>
          </Pressable>
        ))}
      </View>
      {mode === 'note' ? <T v="caption" color={t.ink3} style={{ flex: 1 }}>{L('记进这个主题，它看不到；想完了时一起用', "Kept in this topic, hidden from it; used when you finish")}</T> : null}
    </View>
  );

  return (
    <Screen>
      <Header title={topic?.title ?? L('思考', 'Thinking')} sub={L(`主题 · ${talking.length} 条碎片 · 不进主对话`, `Topic · ${talking.length} thoughts · separate from the main chat`)}
        onBack={() => nav.goBack()} icon={<Pressable onPress={rename} accessibilityRole="button" accessibilityLabel={L('改主题名字', 'Rename the topic')} style={[styles.icon, { backgroundColor: t.tints.gold.soft }]}><Lightbulb size={18} color={t.tints.gold.fg} /></Pressable>}
        right={<Pressable onPress={finish} accessibilityRole="button" style={({ pressed }) => [styles.doneBtn, { backgroundColor: t.goldFill, opacity: pressed ? 0.8 : 1 }]}>
          <Check size={14} color={t.onGold} strokeWidth={3} /><Text style={[type.callout, { color: t.onGold, fontWeight: '700' }]}>{L('想完了', 'Done')}</Text>
        </Pressable>} />
      {topic?.status === 'done' ? (
        <View style={[styles.doneBar, { backgroundColor: t.goodSoft }]}>
          <NotebookText size={16} color={t.good} />
          <T v="callout" color={t.good} style={{ flex: 1 }} numberOfLines={2}>{L(`想完了，存在 ${topic.notePath ?? '库里'}`, `Finished; saved to ${topic.notePath ?? 'the vault'}`)}</T>
          <Pressable onPress={reopen} hitSlop={8} accessibilityRole="button"><T v="callout" color={t.gold} style={{ fontWeight: '600' }}>{L('接着想', 'Keep going')}</T></Pressable>
        </View>
      ) : null}
      <View style={{ paddingHorizontal: space.md, paddingTop: space.sm }}>
        <View style={[styles.panel, { backgroundColor: t.surface, borderColor: t.line }]}>
          <Pressable onPress={() => setOpen((v) => !v)} accessibilityRole="button" accessibilityState={{ expanded: open }} style={styles.panelHead}>
            <View style={[styles.icon32, { backgroundColor: t.tints.gold.soft }]}><Layers size={16} color={t.tints.gold.fg} /></View>
            <View style={{ flex: 1 }}>
              <T v="headline" style={{ fontSize: 15 }}>{L(`${talking.length} 条碎片`, `${talking.length} thoughts`)}</T>
              <T v="caption" color={t.ink3}>{L('你叫它的时候，它从这里读起', 'It reads these when you call it in')}</T>
            </View>
            {open ? <ChevronUp size={18} color={t.ink3} /> : <ChevronDown size={18} color={t.ink3} />}
          </Pressable>
          {open ? (
            <ScrollView style={{ maxHeight: 220 }} contentContainerStyle={{ paddingHorizontal: space.md, paddingBottom: 6 }}>
              {talking.map((f) => (
                <Pressable key={f.id} onPress={() => sheet.open({ title: f.title || L('一条想法', 'A thought'), content: (close) => <FragmentSheet id={f.id} initial={f} close={close} /> })}
                  accessibilityRole="button" style={[styles.fragRow, { borderTopColor: t.line }]}>
                  <TypeTile kind={f.kind} />
                  <T v="callout" style={{ flex: 1 }} numberOfLines={2}>{f.kind === 'keywords' ? f.keywords.map((k) => `#${k}`).join(' ') : f.title ? `《${f.title}》${f.chars ? L(` · ${f.chars} 字`, ` · ${f.chars} chars`) : ''}` : f.text || f.files[0]?.name}</T>
                  <T v="caption" color={t.ink3}>{f.day === talking[0]?.day ? f.time : f.day.slice(5)}</T>
                </Pressable>
              ))}
              <Pressable onPress={pullMore} accessibilityRole="button" style={[styles.fragRow, { borderTopColor: t.line }]}>
                <Plus size={15} color={t.gold} /><T v="callout" color={t.gold} style={{ fontWeight: '600' }}>{L('再拉几条进来', 'Add more thoughts')}</T>
              </Pressable>
            </ScrollView>
          ) : null}
        </View>
      </View>
      <ChatView key={id} threadId={id} hideHistoryLink composerTop={toggle} intercept={mode === 'note' ? note : undefined}
        placeholder={mode === 'it' ? L('跟它说…', 'Tell it…') : L('记下来，它不会看', "Jot it down. It won't see it.")}
        empty={L('它在读这几条碎片，马上先问你几个问题。', "It's reading these thoughts and will ask you a few questions first.")} />
    </Screen>
  );
}

function RenameSheet({ topic, close, onDone }: { topic: Topic | null; close: () => void; onDone: () => void }) {
  const t = useTheme();
  const [v, setV] = useState(topic?.title ?? '');
  if (!topic) return null;
  return (
    <View style={{ gap: space.md }}>
      <TextInput value={v} onChangeText={setV} autoFocus accessibilityLabel={L('主题名字', 'Topic name')} style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink }]} />
      <Btn label={L('改', 'Rename')} onPress={() => { if (v.trim()) thinkApi.patchTopic(topic.id, { title: v.trim() }).then(() => { onDone(); close(); }).catch((e) => showError(L('没改成', "Couldn't rename"), e)); }} />
    </View>
  );
}

function PullSheet({ id, have, options, close, onDone }: { id: string; have: string[]; options: Fragment[]; close: () => void; onDone: () => void }) {
  const t = useTheme();
  const [pick, setPick] = useState<string[]>([]);
  const list = options.filter((f) => !have.includes(f.id));
  if (!list.length) return <T v="callout" color={t.ink3}>{L('想法页上没有别的了。', 'No other thoughts on the page.')}</T>;
  return (
    <View style={{ gap: space.sm }}>
      {list.slice(0, 30).map((f) => {
        const on = pick.includes(f.id);
        return (
          <Pressable key={f.id} onPress={() => setPick((c) => (on ? c.filter((x) => x !== f.id) : [...c, f.id]))} accessibilityRole="checkbox" accessibilityState={{ checked: on }}
            style={[styles.pullRow, { backgroundColor: on ? t.goldSoft : t.surface }]}>
            <TypeTile kind={f.kind} />
            <T v="callout" style={{ flex: 1 }} numberOfLines={2}>{f.kind === 'keywords' ? f.keywords.map((k) => `#${k}`).join(' ') : f.title || f.text || f.files[0]?.name}</T>
            <View style={[styles.circle, { borderColor: on ? t.goldFill : t.line, backgroundColor: on ? t.goldFill : t.surface }]}>{on ? <Check size={12} color={t.onGold} strokeWidth={3} /> : null}</View>
          </Pressable>
        );
      })}
      <Btn label={pick.length ? L(`加进来（${pick.length}）`, `Add (${pick.length})`) : L('选几条', 'Pick some')} onPress={() => {
        if (!pick.length) return;
        thinkApi.patchTopic(id, { add: pick }).then(() => { onDone(); close(); }).catch((e) => showError(L('没加上', "Couldn't add"), e));
      }} />
    </View>
  );
}

// —— 想完了 ————————————————————————————————————————————————————————————

interface Form { title: string; oneLine: string; points: { text: string; from: string[] }[]; open: string[]; next: { text: string; date: string; added?: string }[]; keywords: string[]; suggest: string[]; tree: string; branch: string }
const formOf = (d: Draft): Form => ({ ...d, points: d.points.map((p) => ({ ...p })), open: [...d.open], next: d.next.map((n) => ({ ...n })), keywords: [...d.keywords], suggest: [...d.suggest] });

export function ThinkDoneScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const route = useRoute<any>();
  const sheet = useSheet();
  const id = route.params?.id as string;
  const { refresh, refreshKeywords, stream } = useThink();
  const [topic, setTopic] = useState<Topic | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [formFor, setFormFor] = useState<string | null>(null);
  const [folder, setFolder] = useState<'notes' | 'writing'>('notes');
  const [tree, setTree] = useState<'ask' | 'yes' | 'no'>('ask');
  const [saved, setSaved] = useState<{ path: string; tree: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const scroller = useRef<ScrollView>(null);
  const notes = topic?.fragments.filter((f) => f.note).length ?? 0;
  const frags = (topic?.fragments.length ?? 0) - notes;
  const used = notes ? L(`${frags} 条碎片和 ${notes} 句「只记下」`, `${frags} thoughts and ${notes} private notes`) : L(`${frags} 条碎片`, `${frags} thoughts`);

  const load = useCallback(() => thinkApi.getTopic(id).then(setTopic).catch((e) => showError(L('读不到这个主题', "Couldn't load this topic"), e)), [id]);
  useEffect(() => { load(); }, [load]);
  const running = topic?.draftStatus === 'running';
  useEffect(() => {
    if (!running) return undefined;
    const h = setInterval(load, 2000);
    return () => clearInterval(h);
  }, [running, load]);
  // 草稿到了：拿它填表（同一份草稿只填一次，你改过的不被冲掉）
  const draftKey = topic?.draft ? `${topic.id}:${topic.draftStatus}:${topic.draft.title}:${topic.draft.points.length}` : null;
  if (topic?.draft && draftKey !== formFor) {
    setFormFor(draftKey);
    setForm(formOf(topic.draft));
  }
  const byId = new Map((topic?.fragments ?? []).map((f) => [f.id, f]));
  const set = (patch: Partial<Form>) => setForm((f) => (f ? { ...f, ...patch } : f));
  const retry = () => thinkApi.done(id, true).then(load).catch((e) => showError(L('没开始整理', "Couldn't start"), e));

  const addSchedule = (i: number) => {
    const n = form?.next[i];
    if (!n) return;
    sheet.open({ title: L('加进日程', 'Add to schedule'), content: (close) => <ScheduleSheet text={n.text} date={n.date} close={close} onDone={(when) => set({ next: form!.next.map((x, k) => (k === i ? { ...x, added: when } : x)) })} /> });
  };
  const save = async () => {
    if (!form || busy) return;
    setBusy(true);
    try {
      const r = await thinkApi.saveTopic(id, {
        title: form.title, oneLine: form.oneLine, points: form.points.map((p) => p.text).filter((x) => x.trim()), open: form.open.filter((x) => x.trim()),
        next: form.next.map((n) => n.text).filter((x) => x.trim()), keywords: form.keywords, folder, tree: tree === 'yes' ? form.tree : null, branch: tree === 'yes' ? form.branch : null,
      });
      setSaved({ path: r.path, tree: tree === 'yes' });
      refresh();
      refreshKeywords();
      scroller.current?.scrollTo({ y: 0, animated: true });
    } catch (e) { showError(L('没存进去', "Couldn't save"), e); } finally { setBusy(false); }
  };
  const obsidian = stream?.obsidianVault;

  return (
    <Screen>
      <Header title={L('想完了', 'Done thinking')} sub={saved ? L('存好了', 'Saved') : L('草稿 · 用你的原话，点哪改哪', 'Draft · your own words, edit anything')} onBack={() => nav.goBack()}
        icon={<View style={[styles.icon, { backgroundColor: t.goodSoft }]}><Check size={18} color={t.good} strokeWidth={2.5} /></View>} />
      <ScrollView ref={scroller} contentContainerStyle={{ padding: space.md, gap: space.md, paddingBottom: space.xxl }} automaticallyAdjustKeyboardInsets keyboardShouldPersistTaps="handled">
        {saved ? (
          <View style={[styles.ok, { backgroundColor: t.goodSoft }]}>
            <T v="headline" color={t.good}>{L('存好了', 'Saved')}</T>
            <T v="callout" color={t.good}>{L(`库 › ${saved.path}，Obsidian 里也能看。${saved.tree ? '世界树记了一条。' : ''}用到的碎片挪进了「已想完」。`, `Vault › ${saved.path}, also in Obsidian.${saved.tree ? ' One memory-tree leaf added.' : ''} The thoughts moved to Done.`)}</T>
            <View style={{ flexDirection: 'row', gap: space.sm, marginTop: 4 }}>
              {obsidian ? <Btn label={L('在 Obsidian 里打开', 'Open in Obsidian')} kind="quiet" onPress={() => Linking.openURL(`obsidian://open?vault=${encodeURIComponent(obsidian)}&file=${encodeURIComponent(saved.path.replace(/\.md$/, ''))}`).catch(() => {})} /> : null}
              <Btn label={L('回到思考', 'Back to Think')} onPress={() => nav.navigate('Tabs', { screen: '思考' })} />
            </View>
          </View>
        ) : null}
        {!topic || running ? (
          <View style={[styles.wait, { backgroundColor: t.surface }]}>
            <ActivityIndicator color={t.gold} />
            <T v="callout" color={t.ink2} style={{ flex: 1 }}>{L(`正在整理…它在读 ${used} 和你们聊过的，一般半分钟左右。`, `Drafting… reading ${used} and your talk, usually about half a minute.`)}</T>
          </View>
        ) : topic.draftStatus === 'error' ? (
          <View style={[styles.wait, { backgroundColor: t.warnSoft, flexDirection: 'column', alignItems: 'stretch' }]}>
            <T v="callout" color={t.warn}>{L(`没整理出来：${topic.draftError ?? ''}`, `Couldn't draft it: ${topic.draftError ?? ''}`)}</T>
            <Btn label={L('再试一次', 'Try again')} onPress={retry} />
          </View>
        ) : null}
        {form && !running ? (
          <>
            <View style={[styles.card, { backgroundColor: t.surface, borderColor: t.line }]}>
              <TextInput value={form.title} onChangeText={(v) => set({ title: v })} accessibilityLabel={L('标题', 'Title')} style={[styles.titleIn, { color: t.ink, borderBottomColor: t.line }]} />
              <Block label={L('一句话', 'In one line')}>
                <GrowInput value={form.oneLine} onChangeText={(v) => set({ oneLine: v })} multiline accessibilityLabel={L('一句话', 'In one line')} style={[type.body, { color: t.ink, lineHeight: 23 }]} />
              </Block>
              <Block label={L('要点', 'Points')}>
                {form.points.map((p, i) => (
                  <View key={i} style={styles.li}>
                    <View style={[styles.dot, { backgroundColor: t.ink3 }]} />
                    <GrowInput value={p.text} onChangeText={(v) => set({ points: form.points.map((x, k) => (k === i ? { ...x, text: v } : x)) })} multiline accessibilityLabel={L(`要点 ${i + 1}`, `Point ${i + 1}`)}
                      style={[type.body, { flex: 1, color: t.ink, fontSize: 15 }]} />
                    {p.from.map((fid) => (byId.get(fid) ? <View key={fid} style={[styles.src, { backgroundColor: t.bg }]}><Text style={[type.caption, { color: t.ink3, fontSize: 11 }]}>{srcLabel(byId.get(fid))}</Text></View> : null))}
                  </View>
                ))}
              </Block>
              {form.open.length ? (
                <Block label={L('还没想清的', 'Still open')}>
                  {form.open.map((o, i) => (
                    <View key={i} style={styles.li}>
                      <View style={[styles.dot, { backgroundColor: t.ink3 }]} />
                      <GrowInput value={o} onChangeText={(v) => set({ open: form.open.map((x, k) => (k === i ? v : x)) })} multiline style={[type.body, { flex: 1, color: t.ink, fontSize: 15 }]} />
                    </View>
                  ))}
                </Block>
              ) : null}
              {form.next.length ? (
                <Block label={L('下一步', 'Next')}>
                  {form.next.map((n, i) => (
                    <View key={i} style={[styles.li, { alignItems: 'center' }]}>
                      <View style={[styles.dot, { backgroundColor: t.ink3, marginTop: 0 }]} />
                      <GrowInput value={n.text} onChangeText={(v) => set({ next: form.next.map((x, k) => (k === i ? { ...x, text: v } : x)) })} multiline style={[type.body, { flex: 1, color: t.ink, fontSize: 15 }]} />
                      <Pressable onPress={() => addSchedule(i)} disabled={!!n.added} accessibilityRole="button" style={[styles.mini, { backgroundColor: n.added ? t.goodSoft : t.bg }]}>
                        {n.added ? <Check size={13} color={t.good} strokeWidth={2.5} /> : <CalendarPlus size={14} color={t.ink} />}
                        <Text style={[type.caption, { color: n.added ? t.good : t.ink, fontWeight: '600', fontSize: 13 }]}>{n.added ?? L('加进日程', 'Schedule')}</Text>
                      </Pressable>
                    </View>
                  ))}
                </Block>
              ) : null}
              <Block label={L('关键词', 'Keywords')}>
                <View style={styles.chips}>
                  {form.keywords.map((k) => (
                    <Pressable key={k} onPress={() => set({ keywords: form.keywords.filter((x) => x !== k) })} accessibilityRole="button" accessibilityLabel={L(`去掉 ${k}`, `Remove ${k}`)}>
                      <KeywordChip k={k} big />
                    </Pressable>
                  ))}
                  {form.suggest.map((k) => (
                    <Pressable key={k} onPress={() => set({ keywords: [...form.keywords, k], suggest: form.suggest.filter((x) => x !== k) })} accessibilityRole="button"
                      style={[styles.sug, { borderColor: t.cyan }]}>
                      <Plus size={12} color={t.tints.cyan.fg} /><Text style={[type.callout, { color: t.tints.cyan.fg, fontWeight: '600' }]}>#{k}</Text>
                    </Pressable>
                  ))}
                </View>
                <T v="caption" color={t.ink3}>{form.suggest.length ? L('碎片上带过来的都在；虚线的是它建议的，点了才加。点已有的去掉。', "The thoughts' own keywords are all here; dashed ones are its suggestions, added only if you tap. Tap one to remove it.") : L('点一个去掉。', 'Tap one to remove it.')}</T>
              </Block>
            </View>

            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
              <SectionLabel>{L('存到库里', 'Save to the vault')}</SectionLabel>
              <View style={[styles.seg, { backgroundColor: t.surface2 }]}>
                {(['notes', 'writing'] as const).map((f) => (
                  <Pressable key={f} onPress={() => setFolder(f)} accessibilityRole="button" accessibilityState={{ selected: folder === f }} style={[styles.segItem, folder === f && { backgroundColor: t.surface }]}>
                    <Text style={[type.callout, { fontWeight: '600', color: folder === f ? t.ink : t.ink2 }]}>{f === 'notes' ? L('笔记', 'Notes') : L('写作', 'Writing')}</Text>
                  </Pressable>
                ))}
              </View>
            </View>
            <T v="caption" color={t.ink3} style={{ marginTop: -8 }}>{L(`库 › ${folder === 'notes' ? '笔记' : '写作'} › ${form.title || '…'}.md`, `Vault › ${folder === 'notes' ? 'Notes' : 'Writing'} › ${form.title || '…'}.md`)}</T>

            {form.tree ? (
              <View style={[styles.card, { backgroundColor: t.surface, borderColor: t.line, gap: 8 }]}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                  <View style={[styles.icon28, { backgroundColor: t.goodSoft }]}><TreeDeciduous size={15} color={t.good} /></View>
                  <T v="headline" style={{ flex: 1, fontSize: 15 }}>{L('要不要记进世界树？', 'Add to your memory tree?')}</T>
                </View>
                <GrowInput value={form.tree} onChangeText={(v) => set({ tree: v })} multiline accessibilityLabel={L('记进世界树的那一句', 'The memory to add')} style={[type.body, { color: t.ink, fontSize: 15 }]} />
                <T v="caption" color={t.ink3}>{L(`挂在「${form.branch || '主干'}」 · 你所有的 AI 以后都知道你这么想`, `On "${form.branch || 'the trunk'}" · every AI you use will know you think this`)}</T>
                {tree === 'ask' ? (
                  <View style={{ flexDirection: 'row', gap: space.sm }}>
                    <Btn label={L('不记', 'No')} kind="quiet" flex onPress={() => setTree('no')} />
                    <Btn label={L('记一条', 'Add it')} flex onPress={() => setTree('yes')} />
                  </View>
                ) : (
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                    {tree === 'yes' ? <Check size={15} color={t.good} strokeWidth={2.5} /> : <X size={15} color={t.ink3} />}
                    <T v="callout" color={tree === 'yes' ? t.good : t.ink2} style={{ flex: 1, fontWeight: '600' }}>{tree === 'yes' ? L('存进库时一起记', 'Added when you save') : L('不记，只存笔记', 'Just the note')}</T>
                    <Pressable onPress={() => setTree('ask')} hitSlop={8} accessibilityRole="button"><T v="callout" color={t.gold} style={{ fontWeight: '600' }}>{L('改主意', 'Change')}</T></Pressable>
                  </View>
                )}
              </View>
            ) : null}
            <T v="caption" color={t.ink3}>{L(`用到的 ${used}，存完挪进「收件箱/已想完」，原文都在。`, `The ${used} move to Inbox/Done once saved; nothing is lost.`)}</T>
            {!saved ? <Btn label={busy ? L('正在存…', 'Saving…') : L('存进库', 'Save to the vault')} icon={<NotebookText size={16} color={t.onGold} />} onPress={save} /> : null}
            <Btn label={L('还没想完，接着聊', 'Not done yet, keep talking')} kind="quiet" onPress={() => nav.navigate('ThinkTalk', { id })} />
            <Pressable onPress={retry} accessibilityRole="button" style={{ alignSelf: 'center', padding: 6 }}><T v="caption" color={t.ink3}>{L('不满意？让它重新整理一遍', 'Not right? Draft it again')}</T></Pressable>
          </>
        ) : null}
      </ScrollView>
    </Screen>
  );
}

function Block({ label, children }: { label: string; children: React.ReactNode }) {
  const t = useTheme();
  return (
    <View style={[styles.block, { borderTopColor: t.line }]}>
      <T v="label" color={t.ink3} style={{ textTransform: 'uppercase' }}>{label}</T>
      {children}
    </View>
  );
}

function ScheduleSheet({ text, date, close, onDone }: { text: string; date: string; close: () => void; onDone: (when: string) => void }) {
  const t = useTheme();
  const tomorrow = () => { const d = new Date(); d.setDate(d.getDate() + 1); return d.toLocaleDateString('en-CA'); };
  const [title, setTitle] = useState(text);
  const [day, setDay] = useState(date || tomorrow());
  const [time, setTime] = useState('');
  const [busy, setBusy] = useState(false);
  const add = async () => {
    if (busy) return;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || (time && !/^\d{1,2}:\d{2}$/.test(time))) { showError(L('日子写成 2026-10-03，时间写成 14:00', 'Use 2026-10-03 for the date and 14:00 for the time'), ''); return; }
    setBusy(true);
    try {
      await sched.addItem({ title: title.trim() || text, date: day, ...(time ? { start: time.padStart(5, '0') } : {}) });
      onDone(`${day.slice(5).replace('-', '/')}${time ? ` ${time}` : ''}`);
      close();
    } catch (e) { showError(L('没加上', "Couldn't add"), e); } finally { setBusy(false); }
  };
  return (
    <View style={{ gap: space.md }}>
      <TextInput value={title} onChangeText={setTitle} accessibilityLabel={L('做什么', 'What')} style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink }]} />
      <View style={{ flexDirection: 'row', gap: space.sm }}>
        <TextInput value={day} onChangeText={setDay} placeholder="2026-10-03" placeholderTextColor={t.ink3} accessibilityLabel={L('哪天', 'Date')} style={[type.body, styles.input, { flex: 1, backgroundColor: t.surface, color: t.ink }]} />
        <TextInput value={time} onChangeText={setTime} placeholder={L('几点（可以不填）', 'Time (optional)')} placeholderTextColor={t.ink3} accessibilityLabel={L('几点', 'Time')} style={[type.body, styles.input, { flex: 1, backgroundColor: t.surface, color: t.ink }]} />
      </View>
      <Btn label={busy ? L('正在加…', 'Adding…') : L('加进日程', 'Add to schedule')} onPress={add} />
      <T v="caption" color={t.ink3}>{L('进的是「今天」页那条时间线（日程层），不改课表。', "Goes into the Today timeline (your schedule layer); the class calendar isn't touched.")}</T>
    </View>
  );
}

const styles = StyleSheet.create({
  head: { flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingHorizontal: space.sm, paddingVertical: space.sm, borderBottomWidth: StyleSheet.hairlineWidth },
  back: { width: 36, height: 36, alignItems: 'center', justifyContent: 'center' },
  icon: { width: 34, height: 34, borderRadius: 17, alignItems: 'center', justifyContent: 'center' },
  icon32: { width: 32, height: 32, borderRadius: 10, alignItems: 'center', justifyContent: 'center' },
  icon28: { width: 28, height: 28, borderRadius: 9, alignItems: 'center', justifyContent: 'center' },
  doneBtn: { flexDirection: 'row', alignItems: 'center', gap: 4, height: 34, paddingHorizontal: 12, borderRadius: 17 },
  doneBar: { flexDirection: 'row', alignItems: 'center', gap: 8, marginHorizontal: space.md, marginTop: space.sm, borderRadius: radius.md, paddingHorizontal: 12, paddingVertical: 10 },
  panel: { borderRadius: radius.lg - 2, borderWidth: StyleSheet.hairlineWidth },
  panelHead: { flexDirection: 'row', alignItems: 'center', gap: 10, padding: space.md },
  fragRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 9, borderTopWidth: StyleSheet.hairlineWidth },
  modeRow: { flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingHorizontal: space.md, paddingTop: space.sm },
  seg: { flexDirection: 'row', borderRadius: 11, padding: 3, gap: 2 },
  segItem: { height: 32, paddingHorizontal: 14, borderRadius: 9, alignItems: 'center', justifyContent: 'center' },
  input: { borderRadius: radius.md, paddingHorizontal: space.lg, paddingVertical: 12 },
  pullRow: { flexDirection: 'row', alignItems: 'center', gap: 10, borderRadius: radius.md, padding: space.md },
  circle: { width: 22, height: 22, borderRadius: 11, borderWidth: 2, alignItems: 'center', justifyContent: 'center' },
  ok: { borderRadius: radius.md + 2, padding: space.md, gap: 4 },
  wait: { flexDirection: 'row', alignItems: 'center', gap: space.md, borderRadius: radius.md + 2, padding: space.lg },
  card: { borderRadius: radius.lg - 2, borderWidth: StyleSheet.hairlineWidth, paddingHorizontal: space.md, paddingTop: space.sm },
  titleIn: { fontSize: 19, fontWeight: '800', paddingVertical: 8, borderBottomWidth: 1, borderStyle: 'dashed' },
  block: { gap: 6, paddingVertical: 12, borderTopWidth: StyleSheet.hairlineWidth },
  li: { flexDirection: 'row', alignItems: 'flex-start', gap: 8 },
  dot: { width: 5, height: 5, borderRadius: 3, marginTop: 10 },
  src: { borderRadius: 9, paddingHorizontal: 6, paddingVertical: 2, marginTop: 4 },
  mini: { flexDirection: 'row', alignItems: 'center', gap: 4, height: 30, paddingHorizontal: 10, borderRadius: 15 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, alignItems: 'center' },
  sug: { flexDirection: 'row', alignItems: 'center', gap: 3, height: 28, paddingHorizontal: 10, borderRadius: 14, borderWidth: 1, borderStyle: 'dashed' },
});
