// 冥想时间：开始前的弹层（多久、这段时间里有什么、会发生什么），和看小结。
// 弹层（Sheet）画在导航容器外面，里面不能用 useNavigation，跳转一律走 navigationRef。
import React, { useEffect, useState } from 'react';
import { Linking, Platform, Pressable, StyleSheet, Switch, Text, View } from 'react-native';
import { BellOff, CircleCheck, TextIcon, TriangleAlert } from '../components/icons';
import { Btn, T, showError } from '../components/ui';
import * as thinkApi from '../api/think';
import type { HeadsUp } from '../api/think';
import { agentName } from '../brand';
import { loadDraft, saveDraft } from '../drafts';
import { L } from '../i18n';
import { radius, space, type, useTheme } from '../theme';
import { navigationRef } from '../navigation';
import { useThink } from './ThinkStore';

const DURATIONS = [25, 45, 90, 0] as const;  // 0 = 不限（最长 3 小时）
const SHORTCUT_PREF = 'think:focus-shortcut';
/** 连 iPhone 专注模式：在「快捷指令」里建两个这个名字的快捷指令（里面放「设定专注模式」），开始 / 结束时 app 打开它们。 */
export const SHORTCUT_START = '冥想开始';
export const SHORTCUT_END = '冥想结束';
export const runShortcut = (name: string) => Linking.openURL(`shortcuts://run-shortcut?name=${encodeURIComponent(name)}`).catch(() => {});
export const shortcutOn = () => Platform.OS === 'ios' && loadDraft(SHORTCUT_PREF) === '1';

export function ZenStartSheet({ close }: { close: () => void }) {
  const t = useTheme();
  const { startFocus } = useThink();
  const [dur, setDur] = useState<number>(45);
  const [pv, setPv] = useState<{ now: string; until: string; minutes: number; items: HeadsUp[] } | null>(null);
  const [focusMode, setFocusMode] = useState(() => loadDraft(SHORTCUT_PREF) === '1');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let live = true;
    thinkApi.focusPreview(dur).then((p) => { if (live) setPv(p); }).catch(() => { if (live) setPv(null); });
    return () => { live = false; };
  }, [dur]);
  const items = pv?.items ?? [];
  const warn = items.length > 0;
  const tip = !pv ? L('在看这段时间里有没有日程…', 'Checking your schedule…')
    : warn ? L(`到 ${pv.until} 之间有：`, `Before ${pv.until}:`) + items.map((x) => `${x.time} ${x.title}`).join(L('；', '; '))
      : L(`到 ${pv.until} 为止没有截止和日程，放心写。`, `Nothing scheduled until ${pv.until}. Write away.`);
  const start = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await startFocus(dur);
      close();
      if (focusMode && Platform.OS === 'ios') runShortcut(SHORTCUT_START);
      navigationRef.navigate('ThinkWrite', { zen: true });  // 弹层画在导航容器外面：用全局的导航
    } catch (e) { showError(L('没开始', "Couldn't start"), e); } finally { setBusy(false); }
  };
  return (
    <View style={{ gap: space.md }}>
      <T v="callout" color={t.ink2}>{L(`整个 ${agentName()} 安静下来，只留写字板。`, 'Everything goes quiet; only the writing pad stays.')}</T>
      <View style={{ flexDirection: 'row', gap: 8 }}>
        {DURATIONS.map((d) => {
          const on = dur === d;
          return (
            <Pressable key={d} onPress={() => setDur(d)} accessibilityRole="button" accessibilityState={{ selected: on }}
              style={[styles.dur, { backgroundColor: on ? t.lensField : t.surface, borderColor: on ? t.lensField : t.line }]}>
              <Text style={[type.headline, { fontSize: 15, color: on ? '#FFFFFF' : t.ink }]}>{d ? L(`${d} 分钟`, `${d} min`) : L('不限', 'Open')}</Text>
            </Pressable>
          );
        })}
      </View>
      <View style={[styles.tip, { backgroundColor: warn ? t.warnSoft : t.goodSoft }]}>
        {warn ? <TriangleAlert size={17} color={t.warn} /> : <CircleCheck size={17} color={t.good} />}
        <T v="callout" color={warn ? t.warn : t.good} style={{ flex: 1, fontWeight: '500' }}>{tip}{dur === 0 ? L('（不限时最长 3 小时，到点自动结束。）', ' (Open-ended stops after 3 hours.)') : ''}</T>
      </View>
      <View style={{ gap: 10, paddingHorizontal: 2 }}>
        <View style={styles.li}><BellOff size={18} color={t.ink2} /><T v="callout" style={{ flex: 1 }}>{L('推送全压住，结束时一次给你', 'Notifications are held and handed over at the end')}</T></View>
        <View style={styles.li}><TextIcon size={18} color={t.ink2} /><T v="callout" style={{ flex: 1 }}>{L('没有 tab、卡片和角标，只剩字', 'No tabs, cards or badges, just words')}</T></View>
      </View>
      {Platform.OS === 'ios' ? (
        <View style={[styles.toggle, { backgroundColor: t.surface }]}>
          <View style={{ flex: 1, gap: 2 }}>
            <T v="headline" style={{ fontSize: 15 }}>{L('连 iPhone 专注模式', 'Also turn on an iPhone Focus')}</T>
            <T v="caption" color={t.ink3}>{L(`别的 App 也安静。要先在「快捷指令」里建「${SHORTCUT_START}」「${SHORTCUT_END}」两个。`, `Quiets other apps too. Create two Shortcuts named "${SHORTCUT_START}" and "${SHORTCUT_END}" first.`)}</T>
          </View>
          <Switch value={focusMode} onValueChange={(v) => { setFocusMode(v); saveDraft(SHORTCUT_PREF, v ? '1' : ''); }} accessibilityLabel={L('连 iPhone 专注模式', 'Also turn on an iPhone Focus')} />
        </View>
      ) : null}
      <Btn label={busy ? L('正在开始…', 'Starting…') : L(`开始 · ${dur ? `${dur} 分钟` : '不限时'}`, `Start · ${dur ? `${dur} min` : 'open-ended'}`)} onPress={start} />
    </View>
  );
}

/** 到点自己结束的那一次：读小结、标成看过，去小结页。 */
export async function openZenSummary(nav: any, id: number) {
  try {
    const summary = await thinkApi.focusSummary(id);
    thinkApi.focusSeen(id).catch(() => {});
    nav.navigate('ZenEnd', { summary });
  } catch (e) { showError(L('读不到小结', "Couldn't load the summary"), e); }
}

const styles = StyleSheet.create({
  dur: { flex: 1, height: 44, borderRadius: radius.md, borderWidth: StyleSheet.hairlineWidth, alignItems: 'center', justifyContent: 'center' },
  tip: { flexDirection: 'row', alignItems: 'flex-start', gap: 8, borderRadius: radius.md, paddingHorizontal: 12, paddingVertical: 11 },
  li: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  toggle: { flexDirection: 'row', alignItems: 'center', gap: space.md, borderRadius: radius.md, paddingHorizontal: space.md, paddingVertical: 10 },
});
