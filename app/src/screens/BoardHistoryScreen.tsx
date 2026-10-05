// 看板改动记录：每一版是谁改的、改了什么，能回到任何一版（数据不动）；下面是这个 Agent 在记的表，能看全表、找回 30 天内删掉的行。
import React, { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, View } from 'react-native';
import { useNavigation, useRoute } from '@react-navigation/native';
import { boardsApi, type BoardVersion, type Collection, type TableRow } from '../api/boards';
import { ChevronRight } from '../components/icons';
import { useSheet } from '../components/Sheet';
import { Card, NavHeader, Pill, PullRefresh, Screen, SectionLabel, Segmented, showError, T } from '../components/ui';
import { L, lang } from '../i18n';
import { useStore } from '../store';
import { radius, space, useTheme } from '../theme';

const MONTHS_EN = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const when = (iso: string) => {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  return lang() === 'zh' ? `${d.getMonth() + 1}/${d.getDate()} ${hm}` : `${d.getDate()} ${MONTHS_EN[d.getMonth()]} ${hm}`;
};
const byLabel = (by: string) => (by === 'proposal' ? L('你批准的提案', 'A proposal you approved') : by === 'user' ? L('你的修改', 'Your change') : L('在对话中请 Agent 修改', 'Requested in chat'));

export function BoardHistoryScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { id } = useRoute<any>().params as { id: string };
  const { groups } = useStore();
  const g = groups.find((x) => x.id === id);
  const sheet = useSheet();
  const [versions, setVersions] = useState<BoardVersion[] | null>(null);
  const [colls, setColls] = useState<Collection[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const load = useCallback(() => Promise.all([boardsApi.history(id), boardsApi.collections(id)]).then(([h, c]) => {
    setVersions(h.versions.filter((v) => v.status === 'live' || v.status === 'old'));
    setColls(c.collections.filter((x) => x.status === 'active'));
    setErr(null);
  }, (e) => setErr(e instanceof Error ? e.message : String(e))), [id]);
  useEffect(() => { load(); }, [load]);
  const revert = (v: number) => {
    if (busy != null) return;
    setBusy(v);
    boardsApi.revert(id, v).then(load).catch((e) => showError(L('恢复失败', "Couldn't restore"), e)).finally(() => setBusy(null));
  };
  const openTable = (c: Collection) => sheet.open({ title: c.title, content: () => <TableView agent={id} coll={c} /> });
  return (
    <Screen>
      <NavHeader title={L('看板改动记录', 'Board history')} sub={g?.name} onBack={() => nav.goBack()} />
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingTop: 0, paddingBottom: space.xxl }} refreshControl={<PullRefresh onRefresh={load} />}>
        {err ? <Card style={{ marginTop: space.md }}><T v="callout" color={t.bad}>{L(`无法加载：${err}`, `Couldn't load: ${err}`)}</T></Card> : null}
        {!versions && !err ? <View style={{ padding: space.xl }}><ActivityIndicator color={t.ink3} /></View> : null}
        {versions ? (
          <>
            <SectionLabel>{L('改动', 'Changes')}</SectionLabel>
            <Card style={{ paddingVertical: 2, gap: 0 }}>
              {versions.length ? versions.map((v, i) => {
                const live = v.status === 'live';
                return (
                  <View key={v.version} style={[styles.ver, i > 0 && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }]}>
                    <View style={[styles.dot, { backgroundColor: live ? t.cyan : t.track }]} />
                    <View style={{ flex: 1, gap: 3 }}>
                      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                        <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{when(v.createdAt)}</T>
                        {live ? <Pill label={L('当前', 'Current')} tone="cyan" /> : null}
                      </View>
                      <T v="body" style={{ fontSize: 15, fontWeight: '600' }}>{v.note || (v.blocks.length ? v.blocks.map((b) => b.title || b.id).join(L('、', ', ')) : L('无区块', 'No blocks'))}</T>
                      <T v="caption" color={t.ink3} style={{ fontWeight: '400', fontSize: 13 }}>{byLabel(v.by)}{v.hiddenSections?.length ? L(` · 已隐藏：${v.hiddenSections.join('、')}`, ` · hidden: ${v.hiddenSections.join(', ')}`) : ''}</T>
                      {!live ? (
                        <Pressable onPress={() => revert(v.version)} disabled={busy != null} hitSlop={6} accessibilityRole="button" style={{ alignSelf: 'flex-start', paddingTop: 4 }}>
                          <T v="callout" color={t.gold} style={{ fontWeight: '700' }}>{busy === v.version ? L('正在恢复…', 'Restoring…') : L('恢复此版本', 'Restore this version')}</T>
                        </Pressable>
                      ) : null}
                    </View>
                  </View>
                );
              }) : <T v="callout" color={t.ink2} style={{ paddingVertical: space.md }}>{L('暂无改动。', 'No changes yet.')}</T>}
              {versions.length ? (
                <View style={[styles.ver, { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }]}>
                  <View style={[styles.dot, { backgroundColor: t.track }]} />
                  <View style={{ flex: 1, gap: 3 }}>
                    <T v="body" style={{ fontSize: 15, fontWeight: '600' }}>{g?.dashboard && g.dashboard !== 'none' ? L('原始看板（无区块）', 'The original dashboard (no blocks)') : L('空看板', 'Empty board')}</T>
                    <Pressable onPress={() => revert(0)} disabled={busy != null} hitSlop={6} accessibilityRole="button" style={{ alignSelf: 'flex-start', paddingTop: 4 }}>
                      <T v="callout" color={t.gold} style={{ fontWeight: '700' }}>{busy === 0 ? L('正在恢复…', 'Restoring…') : L('恢复此版本', 'Restore this version')}</T>
                    </Pressable>
                  </View>
                </View>
              ) : null}
            </Card>
            <T v="caption" color={t.ink3} style={{ fontWeight: '400', marginTop: space.sm, paddingHorizontal: space.xs }}>{L('恢复版本仅改变看板布局，已记录的数据不会删除。', 'Restoring a version only changes the board layout; recorded data is never deleted.')}</T>
            {colls.length ? (
              <>
                <SectionLabel>{L('已记录的数据', 'Recorded data')}</SectionLabel>
                <Card style={{ paddingVertical: 2, gap: 0 }}>
                  {colls.map((c, i) => (
                    <Pressable key={c.name} onPress={() => openTable(c)} accessibilityRole="button"
                      style={({ pressed }) => [styles.row, i > 0 && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }, { opacity: pressed ? 0.6 : 1 }]}>
                      <View style={{ flex: 1, gap: 2 }}>
                        <T v="body" style={{ fontSize: 15, fontWeight: '600' }}>{c.title}</T>
                        <T v="caption" color={t.ink3} style={{ fontWeight: '400', fontSize: 13 }}>{L(`${c.count ?? 0} 行 · ${c.fields.length} 个字段`, `${c.count ?? 0} rows · ${c.fields.length} fields`)}</T>
                      </View>
                      <ChevronRight size={16} color={t.ink3} />
                    </Pressable>
                  ))}
                </Card>
              </>
            ) : null}
          </>
        ) : null}
      </ScrollView>
    </Screen>
  );
}

/** 一张表：最近的 200 行；「删掉的」里 30 天内删的能找回。 */
function TableView({ agent, coll }: { agent: string; coll: Collection }) {
  const t = useTheme();
  const [mode, setMode] = useState<'rows' | 'deleted'>('rows');
  const [rows, setRows] = useState<TableRow[] | null>(null);
  const [total, setTotal] = useState(0);
  const load = useCallback((m: 'rows' | 'deleted') => boardsApi.rows(agent, coll.name, m === 'deleted').then((r) => { setRows(r.rows); setTotal(r.total); }, () => setRows([])), [agent, coll.name]);
  useEffect(() => { load(mode); }, [load, mode]);
  const shown = coll.fields.slice(0, 3);
  const restore = (rid: string) => boardsApi.restoreRow(rid).then(() => load(mode)).catch((e) => showError(L('恢复失败', "Couldn't restore"), e));
  return (
    <View style={{ gap: space.md }}>
      <Segmented value={mode} onChange={(m) => { setRows(null); setMode(m); }} options={[{ value: 'rows', label: L('当前', 'Current') }, { value: 'deleted', label: L('已删除', 'Deleted') }]} />
      {!rows ? <ActivityIndicator color={t.ink3} /> : !rows.length ? (
        <T v="callout" color={t.ink2}>{mode === 'deleted' ? L('近 30 天无删除记录。', 'Nothing deleted in the last 30 days.') : L('表格为空。', 'The table is empty.')}</T>
      ) : (
        <View style={{ backgroundColor: t.surface, borderRadius: radius.lg, paddingHorizontal: space.lg }}>
          {rows.map((r, i) => (
            <View key={r.id} style={[styles.row, i > 0 && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }]}>
              <View style={{ flex: 1, gap: 2 }}>
                <T v="body" numberOfLines={1} style={{ fontSize: 15, fontWeight: '600' }}>{r.display[shown[0]?.key] ?? '—'}</T>
                <T v="caption" color={t.ink3} numberOfLines={1} style={{ fontWeight: '400', fontSize: 13 }}>{shown.slice(1).map((f) => r.display[f.key]).filter((x) => x && x !== '—').join(' · ')}</T>
              </View>
              {mode === 'deleted' ? (
                <Pressable onPress={() => restore(r.id)} hitSlop={8} accessibilityRole="button"><T v="callout" color={t.gold} style={{ fontWeight: '700' }}>{L('恢复', 'Restore')}</T></Pressable>
              ) : null}
            </View>
          ))}
        </View>
      )}
      {rows && total > rows.length ? <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L(`仅显示最近 ${rows.length} 行，共 ${total} 行`, `Showing the latest ${rows.length} of ${total}`)}</T> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  ver: { flexDirection: 'row', gap: space.md, paddingVertical: 14 },
  dot: { width: 10, height: 10, borderRadius: 5, marginTop: 5 },
  row: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: 12 },
});
