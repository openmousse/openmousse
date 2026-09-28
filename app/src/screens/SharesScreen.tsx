// 我 → 分享出去的：发出去的链接（看过几次）和收回过的；点一条回到分享页，能复制、收回。只存成图片的不在这里（图发出去就收不回了）。
import React, { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, ScrollView, View } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import * as shareApi from '../api/share';
import type { Share } from '../api/share';
import { Link2, TriangleAlert, Undo2 } from '../components/icons';
import { Card, ListRow, NavHeader, PullRefresh, Screen, T } from '../components/ui';
import { L } from '../i18n';
import { radius, space, useTheme } from '../theme';

export function SharesScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const [shares, setShares] = useState<Share[] | null>(null);
  const [canLink, setCanLink] = useState(true);
  const [err, setErr] = useState('');
  const load = useCallback(() => shareApi.listShares()
    .then((j) => { setShares(j.shares); setCanLink(j.canLink); setErr(''); })
    .catch((e) => setErr(e instanceof Error ? e.message : String(e))), []);
  useEffect(() => { load(); }, [load]);
  // 从分享页回来（刚发出去 / 收回）时重读
  useEffect(() => nav.addListener('focus', () => { load(); }), [nav, load]);

  const live = (shares || []).filter((s) => s.status === 'live' || s.status === 'friends');
  const gone = (shares || []).filter((s) => s.status === 'revoked');
  return (
    <Screen>
      <NavHeader title={L('分享出去的', 'Shared')} onBack={() => nav.goBack()} />
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl, gap: space.md }} refreshControl={<PullRefresh onRefresh={load} />}>
        {!canLink ? (
          <View style={{ flexDirection: 'row', gap: space.sm, padding: space.md, borderRadius: radius.md, backgroundColor: t.warnSoft }}>
            <TriangleAlert size={17} color={t.warn} />
            <T v="callout" style={{ flex: 1 }}>{L('服务器还没开对外的链接地址：链接只有你自己的设备打得开。干净版卡片不受影响。', "The server has no public address for links yet, so only your own devices can open them. Clean cards work regardless.")}</T>
          </View>
        ) : null}
        {err ? <Card><T v="callout" color={t.bad}>{L(`读不到：${err}`, `Couldn't load: ${err}`)}</T></Card> : null}
        {!shares && !err ? <ActivityIndicator color={t.gold} style={{ marginTop: space.xl }} /> : null}
        {shares && !shares.length ? (
          <Card style={{ gap: space.xs }}>
            <T v="headline">{L('还没分享过', 'Nothing shared yet')}</T>
            <T v="callout" color={t.ink2}>{L('对话里长按一条回复 →「分享」，或者在 Zen 想完了存好以后点「分享」。发之前会先把私事挡住。', 'Long-press a reply in a chat → Share, or tap Share after saving a Done-thinking note in Zen. Private bits are hidden before anything goes out.')}</T>
          </Card>
        ) : null}
        {live.length ? (
          <Card style={{ paddingVertical: space.xs }}>
            {live.map((s, i) => (
              <ListRow key={s.id} icon={<Link2 size={20} color={t.cyan} />} title={s.title || L('（没有标题）', '(untitled)')}
                sub={s.status === 'friends'
                  ? L(`${s.day} · 只发给了 ${(s.sentTo ?? []).map((x) => x.name).join('、') || '朋友'}`, `${s.day} · sent to ${(s.sentTo ?? []).map((x) => x.name).join(', ') || 'friends'} only`)
                  : L(`${s.day} · 看过 ${s.views} 次${s.blocked ? ` · 挡着 ${s.blocked} 处` : ''}${s.sentTo?.length ? ` · 发给了 ${s.sentTo.map((x) => x.name).join('、')}` : ''}`,
                    `${s.day} · ${s.views} view${s.views === 1 ? '' : 's'}${s.blocked ? ` · ${s.blocked} hidden` : ''}${s.sentTo?.length ? ` · sent to ${s.sentTo.map((x) => x.name).join(', ')}` : ''}`)}
                onPress={() => nav.navigate('Share', { id: s.id })} last={i === live.length - 1} />
            ))}
          </Card>
        ) : null}
        {gone.length ? (
          <>
            <T v="caption" color={t.ink3} style={{ paddingHorizontal: space.xs, marginTop: space.sm }}>{L('收回了的', 'Withdrawn')}</T>
            <Card style={{ paddingVertical: space.xs }}>
              {gone.map((s, i) => (
                <ListRow key={s.id} icon={<Undo2 size={20} color={t.ink3} />} title={s.title || L('（没有标题）', '(untitled)')}
                  sub={L(`${s.day} 发的 · 看过 ${s.views} 次`, `Shared ${s.day} · ${s.views} view${s.views === 1 ? '' : 's'}`)} last={i === gone.length - 1} />
              ))}
            </Card>
          </>
        ) : null}
      </ScrollView>
    </Screen>
  );
}
