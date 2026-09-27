// 找以前的：搜索（想法、收藏、聊过的、库里的笔记一起，按字面、不调模型）、关键词页（带这个词的全部，跨时间）、按日期翻。
import React, { useCallback, useEffect, useState } from 'react';
import { Linking, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { useFocusEffect, useNavigation, useRoute } from '@react-navigation/native';
import { CalendarDays, ChevronLeft, ChevronRight, Clock, Hash, Lightbulb, NotebookText, Search, X } from '../components/icons';
import { useBottomInset } from '../components/keyboard';
import { useSheet } from '../components/Sheet';
import { PullRefresh, Screen, SectionLabel, T, showError } from '../components/ui';
import * as thinkApi from '../api/think';
import type { DayItem, Fragment, KeywordPage, SearchResult } from '../api/think';
import { loadDraft, saveDraft } from '../drafts';
import { L } from '../i18n';
import { radius, space, type, useTheme } from '../theme';
import { CaptureBar } from '../think/CaptureBar';
import { FragmentSheet } from '../think/FragmentSheet';
import { FloatBtn, FloatClose, Floater, FragmentCard, Highlight, SaveRow, TopicRow, TypeTile, dayLabel } from '../think/parts';
import { useThink } from '../think/ThinkStore';

const RECENT = 'think:recent-searches';
const readRecent = (): string[] => { try { const v = JSON.parse(loadDraft(RECENT) || '[]'); return Array.isArray(v) ? v.filter((x) => typeof x === 'string') : []; } catch { return []; } };
const pushRecent = (q: string) => saveDraft(RECENT, JSON.stringify([q, ...readRecent().filter((x) => x !== q)].slice(0, 8)));

function BackBar({ title, sub, onBack, icon }: { title: string; sub?: string; onBack: () => void; icon?: React.ReactNode }) {
  const t = useTheme();
  return (
    <View style={[styles.head, { borderBottomColor: t.line }]}>
      <Pressable onPress={onBack} hitSlop={10} accessibilityRole="button" accessibilityLabel={L('返回', 'Back')} style={styles.back}><ChevronLeft size={26} color={t.gold} /></Pressable>
      {icon}
      <View style={{ flex: 1, minWidth: 0 }}>
        <T v="headline" numberOfLines={1}>{title}</T>
        {sub ? <T v="caption" color={t.ink3} numberOfLines={1}>{sub}</T> : null}
      </View>
    </View>
  );
}

export function ThinkSearchScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const route = useRoute<any>();
  const sheet = useSheet();
  const { keywords, stream, refreshKeywords } = useThink();
  const [q, setQ] = useState<string>(route.params?.q ?? '');
  const [scope, setScope] = useState('all');
  const [res, setRes] = useState<SearchResult | null>(null);
  const [recent, setRecent] = useState(readRecent);
  useEffect(() => { refreshKeywords(); }, [refreshKeywords]);
  useEffect(() => {
    const s = q.trim();
    if (!s) return undefined;
    let live = true;
    const h = setTimeout(() => {
      thinkApi.search(s, scope).then((r) => { if (live) setRes(r); }).catch((e) => showError(L('没搜成', "Search failed"), e));
    }, 250);
    return () => { live = false; clearTimeout(h); };
  }, [q, scope]);
  const shown = q.trim() && res?.q === q.trim() ? res : null;
  const remember = () => { if (q.trim()) { pushRecent(q.trim()); setRecent(readRecent()); } };
  const openIdea = (f: Fragment) => { remember(); sheet.open({ title: f.title || L('一条想法', 'A thought'), content: (close) => <FragmentSheet id={f.id} initial={f} close={close} /> }); };
  const vault = stream?.obsidianVault;
  const counts = shown?.counts;
  const scopes: [string, string, number | undefined][] = [['all', L('全部', 'All'), shown?.total], ['idea', L('想法', 'Thoughts'), counts?.ideas], ['save', L('收藏', 'Saved'), counts?.saves],
    ['topic', L('主题', 'Topics'), counts?.topics], ['note', L('笔记', 'Notes'), counts?.notes]];
  return (
    <Screen>
      <View style={[styles.searchRow]}>
        <View style={[styles.field, { backgroundColor: t.surface, borderColor: t.goldFill }]}>
          <Search size={17} color={t.ink2} />
          <TextInput value={q} onChangeText={setQ} autoFocus placeholder={L('搜想法、收藏和聊过的', 'Search thoughts, saved and talks')} placeholderTextColor={t.ink3}
            onSubmitEditing={remember} returnKeyType="search" accessibilityLabel={L('搜索', 'Search')} style={[type.body, { flex: 1, color: t.ink, paddingVertical: 8 }]} />
          {q ? <Pressable onPress={() => setQ('')} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('清空', 'Clear')} style={[styles.clear, { backgroundColor: t.ink3 }]}><X size={12} color={t.surface} strokeWidth={3} /></Pressable> : null}
        </View>
        <Pressable onPress={() => nav.goBack()} hitSlop={8} accessibilityRole="button"><T v="headline" color={t.gold}>{L('取消', 'Cancel')}</T></Pressable>
      </View>
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingTop: space.sm, gap: space.md, paddingBottom: space.xxl }} keyboardShouldPersistTaps="handled" keyboardDismissMode="on-drag">
        {!q.trim() ? (
          <>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <T v="label" color={t.ink3} style={{ textTransform: 'uppercase' }}>{L('关键词 · 用得多的在前', 'Keywords · most used first')}</T>
              <T v="caption" color={t.ink3}>{L(`共 ${keywords.length} 个`, `${keywords.length} total`)}</T>
            </View>
            <View style={styles.wrap}>
              {keywords.slice(0, 16).map((k) => (
                <Pressable key={k.k} onPress={() => nav.navigate('ThinkKeyword', { k: k.k })} accessibilityRole="button" style={[styles.kc, { backgroundColor: t.surface, borderColor: t.line }]}>
                  <Text style={[type.callout, { color: t.tints.cyan.fg, fontWeight: '600' }]}>#{k.k}</Text>
                  <Text style={[type.caption, { color: t.ink3 }]}>{k.n}</Text>
                </Pressable>
              ))}
              {!keywords.length ? <T v="callout" color={t.ink3}>{L('还没有关键词。输入栏的 # 键、或者句子里写 #护城河，就有了。', 'No keywords yet. Use the # key in the input bar, or write #word in a sentence.')}</T> : null}
            </View>
            {recent.length ? (
              <View>
                <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingTop: space.sm }}>
                  <T v="label" color={t.ink3} style={{ textTransform: 'uppercase' }}>{L('最近搜过', 'Recent')}</T>
                  <Pressable onPress={() => { saveDraft(RECENT, ''); setRecent([]); }} hitSlop={8} accessibilityRole="button"><T v="caption" color={t.gold} style={{ fontWeight: '600' }}>{L('清空', 'Clear')}</T></Pressable>
                </View>
                {recent.map((r, i) => (
                  <Pressable key={r} onPress={() => setQ(r)} accessibilityRole="button" style={[styles.rr, { borderBottomColor: i < recent.length - 1 ? t.line : 'transparent' }]}>
                    <Clock size={16} color={t.ink3} /><T v="body" style={{ flex: 1 }}>{r}</T><ChevronRight size={15} color={t.ink3} />
                  </Pressable>
                ))}
              </View>
            ) : null}
            <Pressable onPress={() => nav.navigate('ThinkHistory')} accessibilityRole="button" style={({ pressed }) => [styles.cal, { backgroundColor: t.surface, opacity: pressed ? 0.8 : 1 }]}>
              <View style={[styles.tile, { backgroundColor: t.goldSoft }]}><CalendarDays size={19} color={t.gold} /></View>
              <View style={{ flex: 1 }}>
                <T v="headline" style={{ fontSize: 15 }}>{L('按日期翻', 'Browse by date')}</T>
                <T v="caption" color={t.ink3}>{L('想法和收藏都在', 'Thoughts and saved items together')}</T>
              </View>
              <ChevronRight size={16} color={t.ink3} />
            </Pressable>
            <T v="caption" color={t.ink3} style={{ textAlign: 'center', lineHeight: 18 }}>{L('在服务器上直接搜，不调模型。语音转的字、文件和长文里的字都算，已想完的也在。', "Searched on your server, no model involved. Transcripts, file text and long pieces count, finished thoughts too.")}</T>
          </>
        ) : (
          <>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8 }} keyboardShouldPersistTaps="handled">
              {scopes.map(([k, label, n]) => (
                <Pressable key={k} onPress={() => setScope(k)} accessibilityRole="button" accessibilityState={{ selected: scope === k }}
                  style={[styles.chip, { backgroundColor: scope === k ? t.ink : t.surface, borderColor: scope === k ? t.ink : t.line }]}>
                  <Text style={[type.callout, { fontWeight: '600', color: scope === k ? t.bg : t.ink2 }]}>{label}{n != null && (scope === 'all' || scope === k) ? ` ${n}` : ''}</Text>
                </Pressable>
              ))}
            </ScrollView>
            {!shown ? <T v="callout" color={t.ink3}>{L('在找…', 'Searching…')}</T> : null}
            {shown?.keywords.map((k) => (
              <Pressable key={k.k} onPress={() => { remember(); nav.navigate('ThinkKeyword', { k: k.k }); }} accessibilityRole="button" style={[styles.kwRow, { backgroundColor: t.tints.cyan.soft }]}>
                <View style={[styles.tile, { backgroundColor: t.surface }]}><Hash size={18} color={t.tints.cyan.fg} /></View>
                <View style={{ flex: 1 }}>
                  <T v="headline" color={t.tints.cyan.fg}>#{k.k}</T>
                  <T v="caption" color={t.ink2}>{L(`关键词 · ${k.ideas} 条想法、${k.saves} 条收藏都在一页`, `Keyword · ${k.ideas} thoughts, ${k.saves} saved on one page`)}</T>
                </View>
                <ChevronRight size={16} color={t.tints.cyan.fg} />
              </Pressable>
            ))}
            {shown?.ideas.length ? (
              <Group label={L(`想法 · ${shown.ideas.length}`, `Thoughts · ${shown.ideas.length}`)}>
                {shown.ideas.slice(0, 30).map((f) => (
                  <Pressable key={f.id} onPress={() => openIdea(f)} accessibilityRole="button" style={[styles.hit, { borderTopColor: t.line }]}>
                    <TypeTile kind={f.kind} size={24} />
                    <View style={{ flex: 1, gap: 3 }}>
                      <Highlight parts={f.parts} v="callout" color={t.ink} />
                      <T v="caption" color={t.ink3}>{`${dayLabel(f.day).split(' · ')[0]} ${f.time}`}{f.status === 'done' ? L(' · 已想完', ' · finished') : ''}{f.kind === 'voice' ? L(' · 语音里的一句', ' · from a voice note') : ''}</T>
                    </View>
                  </Pressable>
                ))}
              </Group>
            ) : null}
            {shown?.saves.length ? (
              <Group label={L(`收藏 · ${shown.saves.length}`, `Saved · ${shown.saves.length}`)} plain>
                {shown.saves.slice(0, 30).map((s) => <SaveRow key={s.id} s={s} parts={s.parts} onPress={() => { remember(); nav.navigate('Save', { id: s.id }); }} />)}
              </Group>
            ) : null}
            {shown?.topics.length ? (
              <Group label={L(`主题 · ${shown.topics.length}`, `Topics · ${shown.topics.length}`)}>
                {shown.topics.map((tp) => (
                  <Pressable key={tp.id} onPress={() => { remember(); nav.navigate('ThinkTalk', { id: tp.id }); }} accessibilityRole="button" style={[styles.hit, { borderTopColor: t.line }]}>
                    <View style={[styles.tile24, { backgroundColor: t.tints.gold.soft }]}><Lightbulb size={13} color={t.tints.gold.fg} /></View>
                    <View style={{ flex: 1, gap: 3 }}>
                      <T v="headline" style={{ fontSize: 15 }}>{tp.title}</T>
                      <Highlight parts={tp.parts} v="caption" color={t.ink2} />
                    </View>
                  </Pressable>
                ))}
              </Group>
            ) : null}
            {shown?.notes.length ? (
              <Group label={L(`库里的笔记 · ${shown.notes.length}`, `Notes in the vault · ${shown.notes.length}`)}>
                {shown.notes.map((n) => (
                  <Pressable key={n.path} disabled={!vault} onPress={() => vault && Linking.openURL(`obsidian://open?vault=${encodeURIComponent(vault)}&file=${encodeURIComponent(n.path.replace(/\.md$/, ''))}`).catch(() => {})}
                    accessibilityRole="button" style={[styles.hit, { borderTopColor: t.line }]}>
                    <View style={[styles.tile24, { backgroundColor: t.goodSoft }]}><NotebookText size={13} color={t.good} /></View>
                    <View style={{ flex: 1, gap: 3 }}>
                      <T v="headline" style={{ fontSize: 15 }}>{n.title}</T>
                      <Highlight parts={n.parts} v="caption" color={t.ink2} />
                      <T v="caption" color={t.ink3}>{n.folder}</T>
                    </View>
                  </Pressable>
                ))}
              </Group>
            ) : null}
            {shown && !shown.total && !shown.keywords.length ? <T v="callout" color={t.ink3}>{L('字面上没找到。去「聊聊」里问它「我以前想过类似的吗」，它会按意思翻库。', 'Nothing matches literally. Ask in a talk: "have I thought about something like this before?" It searches by meaning.')}</T> : null}
            {shown?.total ? <T v="caption" color={t.ink3} style={{ textAlign: 'center' }}>{L('字面搜不到的，去「聊聊」里问它「我以前想过类似的吗」', 'For things worded differently, ask in a talk: "have I thought about something like this?"')}</T> : null}
          </>
        )}
      </ScrollView>
    </Screen>
  );
}

function Group({ label, children, plain }: { label: string; children: React.ReactNode; plain?: boolean }) {
  const t = useTheme();
  return (
    <View style={{ gap: 6 }}>
      <T v="label" color={t.ink3} style={{ textTransform: 'uppercase', paddingHorizontal: 4 }}>{label}</T>
      {plain ? <View style={{ gap: 8 }}>{children}</View> : <View style={[styles.group, { backgroundColor: t.surface }]}>{children}</View>}
    </View>
  );
}

// —— 关键词页 ————————————————————————————————————————————————————————

export function ThinkKeywordScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const route = useRoute<any>();
  const sheet = useSheet();
  const k = route.params?.k as string;
  const { openTopic } = useThink();
  const [page, setPage] = useState<KeywordPage | null>(null);
  const [sel, setSel] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const root = React.useRef<View>(null);
  const bottom = useBottomInset(root);
  const load = useCallback(() => thinkApi.keyword(k).then(setPage).catch((e) => showError(L('读不到', "Couldn't load"), e)), [k]);
  useFocusEffect(useCallback(() => { load(); }, [load]));
  const ideas = (page?.items ?? []).filter((x): x is Extract<DayItem, { type: 'idea' }> => x.type === 'idea').map((x) => x.idea);
  const months: [string, DayItem[]][] = [];
  for (const it of page?.items ?? []) {
    const m = it.at.slice(0, 7);
    const last = months[months.length - 1];
    if (last && last[0] === m) last[1].push(it); else months.push([m, [it]]);
  }
  const toggle = (id: string) => setSel((c) => (c.includes(id) ? c.filter((x) => x !== id) : [...c, id]));
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
  const since = page?.since ? `${Number(page.since.slice(5, 7))}/${Number(page.since.slice(8, 10))}` : '';
  const monthLabel = (m: string) => L(`${Number(m.slice(5, 7))} 月`, new Date(`${m}-15T12:00:00`).toLocaleString('en', { month: 'long' }));
  return (
    <Screen>
      <BackBar title={`#${page?.k ?? k}`} onBack={() => nav.goBack()}
        sub={page ? L(`${page.ideas} 条想法 · ${page.saves} 条收藏${since ? ` · 从 ${since} 起` : ''}`, `${page.ideas} thoughts · ${page.saves} saved${since ? ` · since ${since}` : ''}`) : ''}
        icon={<View style={[styles.round, { backgroundColor: t.tints.cyan.soft }]}><Hash size={18} color={t.tints.cyan.fg} /></View>} />
      <View ref={root} onLayout={bottom.onLayout} style={{ flex: 1, paddingBottom: bottom.inset }}>
        <View style={{ flex: 1 }}>
          <ScrollView contentContainerStyle={{ padding: space.lg, gap: 10, paddingBottom: 90 }} refreshControl={<PullRefresh onRefresh={load} />}>
            {page?.co.length ? (
              <>
                <T v="label" color={t.ink3} style={{ textTransform: 'uppercase' }}>{L('常一起出现', 'Often together')}</T>
                <View style={styles.wrap}>
                  {page.co.map((c) => (
                    <Pressable key={c.k} onPress={() => nav.push('ThinkKeyword', { k: c.k })} accessibilityRole="button" style={[styles.kc, { backgroundColor: t.surface, borderColor: t.line, height: 30 }]}>
                      <Text style={[type.callout, { color: t.tints.cyan.fg, fontWeight: '600' }]}>#{c.k}</Text><Text style={[type.caption, { color: t.ink3 }]}>{c.n}</Text>
                    </Pressable>
                  ))}
                </View>
              </>
            ) : null}
            {page?.topics.map((tp) => <TopicRow key={tp.id} tp={tp} onPress={() => nav.navigate('ThinkTalk', { id: tp.id })} />)}
            {months.map(([m, items]) => (
              <React.Fragment key={m}>
                <SectionLabel>{monthLabel(m)}</SectionLabel>
                {items.map((it) => (it.type === 'idea'
                  ? <FragmentCard key={it.idea.id} f={it.idea} withDay selected={sel.includes(it.idea.id)} onToggle={() => toggle(it.idea.id)}
                      onPress={() => sheet.open({ title: it.idea.title || L('一条想法', 'A thought'), content: (close) => <FragmentSheet id={it.idea.id} initial={it.idea} close={close} /> })}
                      onKeyword={(x) => (x === page?.k ? undefined : nav.push('ThinkKeyword', { k: x }))} />
                  : <SaveRow key={it.save.id} s={it.save} onPress={() => nav.navigate('Save', { id: it.save.id })} />))}
              </React.Fragment>
            ))}
            {page && !page.items.length ? <T v="callout" color={t.ink3}>{L('还没有带这个关键词的。', 'Nothing with this keyword yet.')}</T> : null}
            {page?.items.length ? <T v="caption" color={t.ink3} style={{ textAlign: 'center' }}>{L('跨多久都在这一页 · Obsidian 里按这个标签也找得到', 'Everything with it, however old · also a tag in Obsidian')}</T> : null}
          </ScrollView>
          {ideas.length ? (
            <Floater bottom={10}>
              {sel.length ? (
                <>
                  <Text style={[type.headline, { flex: 1, color: '#FFFFFF', fontSize: 15 }]}>{busy ? L('正在开…', 'Opening…') : L(`已选 ${sel.length} 条`, `${sel.length} picked`)}</Text>
                  <FloatBtn label={L('聊聊', 'Talk')} onPress={() => go('talk')} />
                  <FloatBtn label={L('想完了', 'Done')} primary onPress={() => go('done')} />
                  <FloatClose onPress={() => setSel([])} />
                </>
              ) : (
                <>
                  <Text style={[type.headline, { flex: 1, color: '#FFFFFF', fontSize: 15 }]} numberOfLines={1}>{L(`这 ${ideas.length} 条想法都带 #${page?.k ?? k}`, `${ideas.length} thoughts with #${page?.k ?? k}`)}</Text>
                  <FloatBtn label={L('全选', 'Select all')} primary onPress={() => setSel(ideas.map((f) => f.id))} />
                </>
              )}
            </Floater>
          ) : null}
        </View>
        <CaptureBar keyword={page?.k ?? k} placeholder={L(`记一条 #${page?.k ?? k}`, `Note it #${page?.k ?? k}`)} onSaved={load} />
      </View>
    </Screen>
  );
}

// —— 按日期翻 ————————————————————————————————————————————————————————

const pad = (n: number) => String(n).padStart(2, '0');
const monthOf = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}`;

export function ThinkHistoryScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const sheet = useSheet();
  const today = new Date().toLocaleDateString('en-CA');
  const [month, setMonth] = useState(today.slice(0, 7));
  const [counts, setCounts] = useState<Record<string, { ideas: number; saves: number }>>({});
  const [day, setDay] = useState(today);
  const [items, setItems] = useState<DayItem[] | null>(null);
  useEffect(() => {
    let live = true;
    thinkApi.days(month).then((r) => { if (live) setCounts(Object.fromEntries(r.days.map((d) => [d.day, { ideas: d.ideas, saves: d.saves }]))); }).catch(() => {});
    return () => { live = false; };
  }, [month]);
  useEffect(() => {
    let live = true;
    thinkApi.day(day).then((r) => { if (live) setItems(r); }).catch(() => { if (live) setItems([]); });
    return () => { live = false; };
  }, [day]);
  const [y, m] = month.split('-').map(Number);
  const first = new Date(y, m - 1, 1);
  const lead = (first.getDay() + 6) % 7;  // 一周从周一开始
  const total = new Date(y, m, 0).getDate();
  const cells: (number | null)[] = [...Array(lead).fill(null), ...Array.from({ length: total }, (_, i) => i + 1)];
  while (cells.length % 7) cells.push(null);
  const shift = (n: number) => setMonth(monthOf(new Date(y, m - 1 + n, 1)));
  const recorded = Object.keys(counts).length;
  const dotColor = (n: number, on: boolean) => (on ? t.goldFill : n >= 4 ? t.gold : n >= 2 ? t.goldFill : t.goldSoft);
  const ideaN = (items ?? []).filter((x) => x.type === 'idea').length;
  const wd = [L('一', 'M'), L('二', 'T'), L('三', 'W'), L('四', 'T'), L('五', 'F'), L('六', 'S'), L('日', 'S')];
  return (
    <Screen>
      <BackBar title={L('历史', 'History')} sub={L('想法和收藏，按天看', 'Thoughts and saved items by day')} onBack={() => nav.goBack()}
        icon={<View style={[styles.round, { backgroundColor: t.goldSoft }]}><CalendarDays size={18} color={t.gold} /></View>} />
      <ScrollView contentContainerStyle={{ padding: space.lg, gap: space.md, paddingBottom: space.xxl }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
          <Pressable onPress={() => shift(-1)} accessibilityRole="button" accessibilityLabel={L('上个月', 'Previous month')} style={[styles.nav, { backgroundColor: t.surface }]}><ChevronLeft size={18} color={t.ink} /></Pressable>
          <T v="title" style={{ flex: 1, textAlign: 'center', fontWeight: '800' }}>{L(`${y} 年 ${m} 月`, first.toLocaleString('en', { month: 'long', year: 'numeric' }))}</T>
          <Pressable onPress={() => shift(1)} accessibilityRole="button" accessibilityLabel={L('下个月', 'Next month')} style={[styles.nav, { backgroundColor: t.surface }]}><ChevronRight size={18} color={t.ink} /></Pressable>
        </View>
        <View style={[styles.calCard, { backgroundColor: t.surface }]}>
          <View style={styles.week}>{wd.map((w, i) => <Text key={i} style={[type.caption, styles.wd, { color: t.ink3 }]}>{w}</Text>)}</View>
          {Array.from({ length: cells.length / 7 }, (_, r) => (
            <View key={r} style={styles.week}>
              {cells.slice(r * 7, r * 7 + 7).map((d, i) => {
                if (!d) return <View key={i} style={styles.cell} />;
                const key = `${month}-${pad(d)}`;
                const c = counts[key];
                const n = c ? c.ideas + c.saves : 0;
                const on = key === day;
                return (
                  <Pressable key={i} onPress={() => setDay(key)} accessibilityRole="button" accessibilityState={{ selected: on }}
                    accessibilityLabel={L(`${m} 月 ${d} 日${n ? `，记了 ${n} 条` : '，没记'}`, `${first.toLocaleString('en', { month: 'short' })} ${d}${n ? `, ${n} items` : ', nothing'}`)}
                    style={[styles.cell, on ? { backgroundColor: t.lensField } : null, key === today && !on ? { borderWidth: 2, borderColor: t.goldFill } : null]}>
                    <Text style={[type.headline, { fontSize: 15, color: on ? '#FFFFFF' : n ? t.ink : t.ink3, fontWeight: n ? '600' : '400' }]}>{d}</Text>
                    <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: n ? dotColor(n, on) : 'transparent' }} />
                  </Pressable>
                );
              })}
            </View>
          ))}
        </View>
        <T v="caption" color={t.ink3} style={{ textAlign: 'center' }}>{L(`这个月记了 ${recorded} 天 · 点越深记得越多 · 金圈是今天`, `${recorded} days with entries · darker dot = more · gold ring = today`)}</T>
        <View style={{ flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between', paddingTop: 4 }}>
          <T v="title" style={{ fontSize: 17, fontWeight: '800' }}>{dayLabel(day)}</T>
          {items?.length ? <T v="caption" color={t.ink3}>{L(`${ideaN} 条想法 · ${items.length - ideaN} 条收藏`, `${ideaN} thoughts · ${items.length - ideaN} saved`)}</T> : null}
        </View>
        {items && !items.length ? <T v="callout" color={t.ink3}>{L('这天没记东西。', 'Nothing that day.')}</T> : null}
        {(items ?? []).map((it) => (it.type === 'idea'
          ? <FragmentCard key={it.idea.id} f={it.idea} onPress={() => sheet.open({ title: it.idea.title || L('一条想法', 'A thought'), content: (close) => <FragmentSheet id={it.idea.id} initial={it.idea} close={close} /> })}
              onKeyword={(k) => nav.navigate('ThinkKeyword', { k })} />
          : <SaveRow key={it.save.id} s={it.save} onPress={() => nav.navigate('Save', { id: it.save.id })} />))}
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  head: { flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingHorizontal: space.sm, paddingVertical: space.sm, borderBottomWidth: StyleSheet.hairlineWidth },
  back: { width: 36, height: 36, alignItems: 'center', justifyContent: 'center' },
  round: { width: 34, height: 34, borderRadius: 17, alignItems: 'center', justifyContent: 'center' },
  searchRow: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: space.lg, paddingVertical: space.sm },
  field: { flex: 1, flexDirection: 'row', alignItems: 'center', gap: 8, borderRadius: radius.md, borderWidth: 1.5, paddingLeft: 12, paddingRight: 8, minHeight: 42 },
  clear: { width: 20, height: 20, borderRadius: 10, alignItems: 'center', justifyContent: 'center' },
  wrap: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  kc: { flexDirection: 'row', alignItems: 'center', gap: 6, height: 36, paddingHorizontal: 12, borderRadius: 18, borderWidth: StyleSheet.hairlineWidth },
  rr: { flexDirection: 'row', alignItems: 'center', gap: 10, height: 46, borderBottomWidth: StyleSheet.hairlineWidth, paddingHorizontal: 4 },
  cal: { flexDirection: 'row', alignItems: 'center', gap: space.md, borderRadius: radius.md + 2, padding: space.md },
  tile: { width: 40, height: 40, borderRadius: 11, alignItems: 'center', justifyContent: 'center' },
  tile24: { width: 24, height: 24, borderRadius: 7, alignItems: 'center', justifyContent: 'center', marginTop: 1 },
  chip: { height: 32, paddingHorizontal: 12, borderRadius: 16, borderWidth: StyleSheet.hairlineWidth, alignItems: 'center', justifyContent: 'center' },
  kwRow: { flexDirection: 'row', alignItems: 'center', gap: space.md, borderRadius: radius.md + 2, padding: space.md },
  group: { borderRadius: radius.lg - 2, paddingHorizontal: space.md },
  hit: { flexDirection: 'row', alignItems: 'flex-start', gap: 10, paddingVertical: 11, borderTopWidth: StyleSheet.hairlineWidth },
  nav: { width: 36, height: 36, borderRadius: 18, alignItems: 'center', justifyContent: 'center' },
  calCard: { borderRadius: radius.lg - 2, padding: 8, gap: 2 },
  week: { flexDirection: 'row', gap: 4 },
  wd: { flex: 1, textAlign: 'center', paddingVertical: 4, fontWeight: '700' },
  cell: { flex: 1, height: 48, borderRadius: 12, alignItems: 'center', justifyContent: 'center', gap: 4 },
});
