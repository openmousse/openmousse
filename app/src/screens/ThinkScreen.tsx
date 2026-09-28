// 「思考」tab：想法（AI 不看，等你叫）和收藏（别人的东西，先存着）。顶上一个搜索框（想法、收藏、聊过的、库里的笔记一起搜）和按天翻的日历。
import React, { useCallback, useRef, useState } from 'react';
import { Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import { useFocusEffect, useNavigation, useRoute } from '@react-navigation/native';
import { CalendarDays, Camera, ClipboardPaste, FileText, ImageIcon, Moon, Plus, Search, Timer } from '../components/icons';
import { pickDocuments, pickMedia } from '../components/chatInput';
import { useBottomInset } from '../components/keyboard';
import { useSheet } from '../components/Sheet';
import { Btn, LargeHeader, PullRefresh, Screen, SectionLabel, T, showError } from '../components/ui';
import * as thinkApi from '../api/think';
import type { Fragment } from '../api/think';
import type { PendingFile } from '../data/types';
import { L } from '../i18n';
import { useStore } from '../store';
import { radius, space, type, useTheme } from '../theme';
import { CaptureBar } from '../think/CaptureBar';
import { FloatBtn, FloatClose, Floater, FragmentCard, GrowInput, SaveRow, TopicRow, dayLabel } from '../think/parts';
import { FragmentSheet } from '../think/FragmentSheet';
import { TopicActionsSheet } from '../think/TopicSheets';
import { useThink } from '../think/ThinkStore';
import { ZenStartSheet, openZenSummary } from '../think/Zen';

type Tab = 'ideas' | 'saves';

export function ThinkScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const route = useRoute<any>();
  const sheet = useSheet();
  const { focus, savesNew, refresh, refreshSaves, refreshKeywords, loadFocus } = useThink();
  // 别处要求切到某一栏（比如收藏里点了「放进思考」）：带 tab + at
  const [sel, setSel] = useState<{ tab: Tab; at: number }>({ tab: route.params?.tab === 'saves' ? 'saves' : 'ideas', at: 0 });
  const wantedAt = (route.params?.at as number | undefined) ?? 0;
  const tab: Tab = wantedAt > sel.at && (route.params?.tab === 'saves' || route.params?.tab === 'ideas') ? route.params.tab : sel.tab;
  const setTab = (v: Tab) => setSel({ tab: v, at: Math.max(sel.at, wantedAt) });

  useFocusEffect(useCallback(() => { refresh(); refreshSaves(); refreshKeywords(); loadFocus(); }, [refresh, refreshSaves, refreshKeywords, loadFocus]));

  const zen = () => {
    if (focus) { nav.navigate('ThinkWrite', { zen: true }); return; }
    sheet.open({ title: L('进入冥想时间', 'Focus time'), content: (close) => <ZenStartSheet close={close} /> });
  };
  const right = tab === 'ideas' ? (
    <Pressable onPress={zen} accessibilityRole="button" accessibilityLabel={focus ? L(`冥想中，到 ${focus.until}`, `In focus until ${focus.until}`) : L('进入冥想时间', 'Start focus time')}
      style={({ pressed }) => [styles.zen, { backgroundColor: t.lensField, opacity: pressed ? 0.8 : 1 }]}>
      {focus ? <Timer size={15} color={t.goldFill} /> : <Moon size={15} color={t.goldFill} />}
      <Text style={[type.callout, { color: '#E8EAED', fontWeight: '600' }]}>{focus ? L(`到 ${focus.until}`, `Until ${focus.until}`) : L('冥想', 'Focus')}</Text>
    </Pressable>
  ) : <SaveButtons />;

  return (
    <Screen>
      <LargeHeader title={L('思考', 'Think')} sub={tab === 'ideas' ? L('想到什么先扔进来，它不看', "Drop thoughts here. It won't read them.") : L('别人的好东西，先存着', "Keep other people's good stuff")} right={right} />
      <View style={{ paddingHorizontal: space.lg, gap: 10, paddingBottom: space.sm }}>
        <View style={[styles.seg, { backgroundColor: t.surface2 }]} accessibilityRole="tablist">
          {([['ideas', L('想法', 'Thoughts'), 0], ['saves', L('收藏', 'Saved'), savesNew]] as const).map(([k, label, n]) => {
            const on = tab === k;
            return (
              <Pressable key={k} onPress={() => setTab(k)} accessibilityRole="tab" accessibilityState={{ selected: on }}
                style={[styles.segItem, on && { backgroundColor: t.surface }]}>
                <Text style={[type.headline, { fontSize: 15, color: on ? t.ink : t.ink2 }]}>{label}</Text>
                {n ? <View style={[styles.n, { backgroundColor: t.cyan }]}><Text style={{ color: t.surface, fontSize: 11, fontWeight: '700' }}>{n > 99 ? '99+' : n}</Text></View> : null}
              </Pressable>
            );
          })}
        </View>
        <View style={{ flexDirection: 'row', gap: 8 }}>
          <Pressable onPress={() => nav.navigate('ThinkSearch')} accessibilityRole="search" style={({ pressed }) => [styles.search, { backgroundColor: t.surface2, opacity: pressed ? 0.7 : 1 }]}>
            <Search size={17} color={t.ink2} />
            <T v="body" color={t.ink2} style={{ fontSize: 15 }}>{L('搜想法、收藏和聊过的', 'Search thoughts, saved and talks')}</T>
          </Pressable>
          <Pressable onPress={() => nav.navigate('ThinkHistory')} accessibilityRole="button" accessibilityLabel={L('按日期翻', 'Browse by date')}
            style={({ pressed }) => [styles.cal, { backgroundColor: t.surface2, opacity: pressed ? 0.7 : 1 }]}>
            <CalendarDays size={19} color={t.ink2} />
          </Pressable>
        </View>
      </View>
      {tab === 'ideas' ? <Ideas /> : <Saves />}
    </Screen>
  );
}

function Ideas() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const sheet = useSheet();
  const { connected, booting } = useStore();
  const { stream, streamError, refresh, openTopic, unseenFocus } = useThink();
  const [sel, setSel] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const root = useRef<View>(null);
  const bottom = useBottomInset(root);
  const frags = stream?.fragments ?? [];
  const days: [string, Fragment[]][] = [];
  for (const f of frags) {
    const last = days[days.length - 1];
    if (last && last[0] === f.day) last[1].push(f); else days.push([f.day, [f]]);
  }
  const toggle = (id: string) => setSel((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));
  const open = (f: Fragment) => sheet.open({ title: f.title || L('一条想法', 'A thought'), content: (close) => <FragmentSheet id={f.id} initial={f} close={close} /> });
  const go = async (then: 'talk' | 'done') => {
    if (busy || !sel.length) return;
    setBusy(true);
    try {
      const tp = await openTopic(sel);
      if (then === 'talk') await thinkApi.talk(tp.id); else await thinkApi.done(tp.id);
      setSel([]);
      nav.navigate(then === 'talk' ? 'ThinkTalk' : 'ThinkDone', { id: tp.id });
    } catch (e) { showError(L('没开成', "Couldn't start"), e); } finally { setBusy(false); }
  };
  return (
    <View ref={root} onLayout={bottom.onLayout} style={{ flex: 1, paddingBottom: bottom.inset }}>
      <View style={{ flex: 1 }}>
        <ScrollView contentContainerStyle={{ paddingHorizontal: space.lg, paddingBottom: sel.length ? 90 : space.xl, gap: 10 }} keyboardShouldPersistTaps="handled" keyboardDismissMode={Platform.OS === 'web' ? 'none' : 'on-drag'}
          refreshControl={<PullRefresh onRefresh={refresh} />}>
          {unseenFocus ? (
            <Pressable onPress={() => openZenSummary(nav, unseenFocus.id)} accessibilityRole="button" style={({ pressed }) => [styles.notice, { backgroundColor: t.lensField, opacity: pressed ? 0.85 : 1 }]}>
              <Moon size={16} color={t.goldFill} />
              <T v="callout" color="#E8EAED" style={{ flex: 1 }}>{unseenFocus.held ? L(`冥想结束了，压住的 ${unseenFocus.held} 条在小结里`, `Focus time ended; ${unseenFocus.held} held in the summary`) : L('冥想结束了', 'Focus time ended')}</T>
              <T v="callout" color={t.goldFill} style={{ fontWeight: '600' }}>{L('看小结', 'Summary')}</T>
            </Pressable>
          ) : null}
          {stream?.topics.length ? (
            <>
              <SectionLabel>{L('在想的', 'Thinking about')}</SectionLabel>
              {stream.topics.map((tp) => <TopicRow key={tp.id} tp={tp} onPress={() => nav.navigate('ThinkTalk', { id: tp.id })}
                onLongPress={() => sheet.open({ title: tp.title, content: (close) => <TopicActionsSheet key={tp.id} topic={tp} close={close} onDone={refresh} /> })} />)}
            </>
          ) : null}
          {days.map(([day, list]) => (
            <React.Fragment key={day}>
              <SectionLabel>{dayLabel(day)}</SectionLabel>
              {list.map((f) => (
                <FragmentCard key={f.id} f={f} selected={sel.includes(f.id)} onToggle={() => toggle(f.id)} onPress={() => open(f)}
                  onKeyword={(k) => nav.navigate('ThinkKeyword', { k })} />
              ))}
            </React.Fragment>
          ))}
          {!frags.length ? (
            <View style={{ alignItems: 'center', gap: space.sm, paddingVertical: space.xxl, paddingHorizontal: space.lg }}>
              <T v="callout" color={t.ink3} style={{ textAlign: 'center' }}>
                {booting ? L('正在连服务器…', 'Connecting to the server…') : !connected ? L('没连上服务器。', 'Not connected to the server.') : streamError
                  ? streamError : !stream ? L('正在读…', 'Loading…') : L('想到什么先扔进来：一句话、几个关键词、一段语音、一张照片都行。它不看，等你勾几条叫它「聊聊」或者「想完了」。', "Drop anything in: a sentence, a few keywords, a voice note, a photo. It won't read them until you pick some and ask it to talk or help you finish.")}
              </T>
            </View>
          ) : (
            <T v="caption" color={t.ink3} style={{ textAlign: 'center', paddingTop: space.sm }}>
              {stream?.vault ? L(`每条都是库里「${stream.folder}」的一篇笔记，Obsidian 里也能看`, `Each one is a note in the vault's "${stream.folder}", also in Obsidian`) : L('长按不用：点右上角的圈选几条', 'Tap the circle on the right to pick some')}
            </T>
          )}
        </ScrollView>
        {sel.length ? (
          <Floater bottom={10}>
            <Text style={[type.headline, { flex: 1, color: '#FFFFFF', fontSize: 15 }]}>{busy ? L('正在开…', 'Opening…') : L(`已选 ${sel.length} 条`, `${sel.length} picked`)}</Text>
            <FloatBtn label={L('聊聊', 'Talk')} onPress={() => go('talk')} />
            <FloatBtn label={L('想完了', 'Done')} primary onPress={() => go('done')} />
            <FloatClose onPress={() => setSel([])} />
          </Floater>
        ) : null}
      </View>
      <CaptureBar />
    </View>
  );
}

const FILTERS = ['all', 'new', 'link', 'file', 'image'] as const;

function Saves() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { saves, savesNew, savesError, refreshSaves } = useThink();
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>('all');
  const pick = (f: (typeof FILTERS)[number]) => { setFilter(f); refreshSaves(f); };
  const label = { all: L('全部', 'All'), new: L(`没看 ${savesNew}`, `Unread ${savesNew}`), link: L('链接', 'Links'), file: L('文件', 'Files'), image: L('图片', 'Images') };
  return (
    <ScrollView contentContainerStyle={{ paddingHorizontal: space.lg, paddingBottom: space.xxl, gap: 10 }} refreshControl={<PullRefresh onRefresh={() => refreshSaves()} />}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8, paddingVertical: 2 }}>
        {FILTERS.map((f) => (
          <Pressable key={f} onPress={() => pick(f)} accessibilityRole="button" accessibilityState={{ selected: filter === f }}
            style={[styles.chip, { backgroundColor: filter === f ? t.ink : t.surface, borderColor: filter === f ? t.ink : t.line }]}>
            <Text style={[type.callout, { fontWeight: '600', color: filter === f ? t.bg : t.ink2 }]}>{label[f]}</Text>
          </Pressable>
        ))}
      </ScrollView>
      {saves.map((s) => <SaveRow key={s.id} s={s} onPress={() => nav.navigate('Save', { id: s.id })} />)}
      {!saves.length ? (
        <T v="callout" color={t.ink3} style={{ textAlign: 'center', paddingVertical: space.xxl, paddingHorizontal: space.lg }}>
          {savesError ?? (filter === 'all' ? L('右上角「粘贴」一个链接，或者「+」从相册、文件里加。对话里长按一条消息也能收藏。', 'Paste a link (top right), or add from photos and files with +. You can also long-press a chat message to save it.') : L('这一栏还没有。', 'Nothing here yet.'))}
        </T>
      ) : (
        <T v="caption" color={t.ink3} style={{ textAlign: 'center', paddingTop: space.sm }}>{L('存的时候不调模型。点开一条，你决定怎么处理。', "Saving doesn't call a model. Open one to decide what to do with it.")}</T>
      )}
    </ScrollView>
  );
}

/** 收藏页右上角：粘贴、从相册 / 文件加。 */
function SaveButtons() {
  const t = useTheme();
  const sheet = useSheet();
  const { addSaveFiles } = useThink();
  const paste = async () => {
    const clip = await Clipboard.getStringAsync().catch(() => '');
    sheet.open({ title: L('收藏一条', 'Save something'), content: (close) => <PasteSheet initial={clip} close={close} /> });
  };
  const add = () => {
    const up = (files: PendingFile[], source: string) => {
      if (!files.length) return;
      addSaveFiles(files, { source }).catch((e) => showError(L('没存上', "Couldn't save"), e));
    };
    if (Platform.OS === 'web') { pickDocuments().then((f) => up(f, L('文件', 'Files'))).catch((e) => showError(L('没选上', "Couldn't pick"), e)); return; }
    sheet.open({
      title: L('收藏照片或文件', 'Save photos or files'),
      content: (close) => (
        <View style={{ gap: space.sm }}>
          {[
            { Icon: Camera, label: L('拍照', 'Take a photo'), go: () => pickMedia(true), source: L('拍照', 'Camera') },
            { Icon: ImageIcon, label: L('相册', 'Photo library'), go: () => pickMedia(false), source: L('相册', 'Photos') },
            { Icon: FileText, label: L('文件', 'Files'), go: () => pickDocuments(), source: L('文件', 'Files') },
          ].map(({ Icon, label, go, source }) => (
            <Pressable key={label} onPress={() => { close(); go().then((f) => up(f, source)).catch((e) => showError(L('没选上', "Couldn't pick"), e)); }} accessibilityRole="button"
              style={({ pressed }) => [styles.action, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
              <Icon size={20} color={t.ink} /><T v="headline">{label}</T>
            </Pressable>
          ))}
          <T v="caption" color={t.ink3}>{L('原件存在服务器上，不进 Obsidian 库；PDF、Word、表格的文字会抽出来，能搜。', "Originals stay on the server, not in the Obsidian vault. Text in PDFs, Word files and spreadsheets is extracted so you can search it.")}</T>
        </View>
      ),
    });
  };
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
      <Pressable onPress={paste} accessibilityRole="button" style={({ pressed }) => [styles.pill, { backgroundColor: t.surface, opacity: pressed ? 0.7 : 1 }]}>
        <ClipboardPaste size={16} color={t.ink} /><Text style={[type.callout, { color: t.ink, fontWeight: '600' }]}>{L('粘贴', 'Paste')}</Text>
      </Pressable>
      <Pressable onPress={add} accessibilityRole="button" accessibilityLabel={L('从相册或文件加', 'Add from photos or files')}
        style={({ pressed }) => [styles.plus, { backgroundColor: t.goldFill, opacity: pressed ? 0.8 : 1 }]}>
        <Plus size={20} color={t.onGold} />
      </Pressable>
    </View>
  );
}

function PasteSheet({ initial, close }: { initial: string; close: () => void }) {
  const t = useTheme();
  const { addSave } = useThink();
  const [text, setText] = useState(initial);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const save = async () => {
    if (!text.trim() || busy) return;
    setBusy(true);
    try { await addSave({ text: text.trim(), note: note.trim() || undefined }); close(); } catch (e) { showError(L('没存上', "Couldn't save"), e); } finally { setBusy(false); }
  };
  return (
    <View style={{ gap: space.md }}>
      <GrowInput value={text} onChangeText={setText} multiline placeholder={L('链接，或者一段文字', 'A link, or some text')} placeholderTextColor={t.ink3} autoFocus={!initial}
        accessibilityLabel={L('要收藏的', 'What to save')} style={[type.body, styles.input, { minHeight: 80, backgroundColor: t.surface, color: t.ink }]} />
      <TextInput value={note} onChangeText={setNote} placeholder={L('加一句或 #关键词（可以不写）', 'A note or #keywords (optional)')} placeholderTextColor={t.ink3}
        accessibilityLabel={L('备注', 'Note')} style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink }]} />
      <T v="caption" color={t.ink3}>{L('有链接就按链接存，服务器在后台把正文抓一份（公众号文章被删了也还在）。存的时候不调模型。', "If there's a link it's saved as a link and the server keeps a copy of the text in the background (so it survives deletion). No model is called.")}</T>
      <Btn label={busy ? L('正在存…', 'Saving…') : L('存', 'Save')} onPress={save} />
    </View>
  );
}

const styles = StyleSheet.create({
  zen: { flexDirection: 'row', alignItems: 'center', gap: 6, height: 40, paddingHorizontal: 14, borderRadius: 20 },
  seg: { flexDirection: 'row', borderRadius: 11, padding: 3, gap: 2 },
  segItem: { flex: 1, flexDirection: 'row', gap: 6, alignItems: 'center', justifyContent: 'center', height: 36, borderRadius: 9 },
  n: { minWidth: 18, height: 18, borderRadius: 9, paddingHorizontal: 5, alignItems: 'center', justifyContent: 'center' },
  search: { flex: 1, height: 42, borderRadius: radius.md, flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 12 },
  cal: { width: 42, height: 42, borderRadius: radius.md, alignItems: 'center', justifyContent: 'center' },
  notice: { flexDirection: 'row', alignItems: 'center', gap: 10, borderRadius: radius.md, paddingHorizontal: 14, paddingVertical: 12, marginTop: 4 },
  chip: { height: 32, paddingHorizontal: 13, borderRadius: 16, borderWidth: StyleSheet.hairlineWidth, alignItems: 'center', justifyContent: 'center' },
  pill: { flexDirection: 'row', alignItems: 'center', gap: 5, height: 40, paddingHorizontal: 12, borderRadius: 20 },
  plus: { width: 40, height: 40, borderRadius: 20, alignItems: 'center', justifyContent: 'center' },
  action: { flexDirection: 'row', alignItems: 'center', gap: space.md, borderRadius: radius.md, padding: space.lg },
  input: { borderRadius: radius.md, paddingHorizontal: space.lg, paddingVertical: 12 },
});
