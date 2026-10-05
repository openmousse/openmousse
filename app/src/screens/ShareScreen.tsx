// 分享（server/share.py，社交第一层）：发之前先挡住私事，再发链接（微信、WhatsApp）或一张干净版卡片（小红书）。
// 进来的方式：对话里长按一条 →「分享」（from: message）、Zen「想完了」存好以后（from: note）、「我 → 分享出去的」点一条（id）。
// 卡片预览就是服务器画好的那张图，和别人收到的一样；挡住的地方一处一行，能放出来、挡回去。
import React, { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, Alert, Image, Linking, Platform, Pressable, ScrollView, Share as NativeShare, StyleSheet, Switch, Text, TextInput, View } from 'react-native';
import { useNavigation, useRoute } from '@react-navigation/native';
import * as Clipboard from 'expo-clipboard';
import { getBase } from '../api/base';
import * as shareApi from '../api/share';
import * as fr from '../api/friends';
import type { Friend } from '../api/friends';
import type { Share, ShareCard, ShareFrom, ShareMask, ShareStyle } from '../api/share';
import { Check, Copy, Eye, EyeOff, Link2, ShareIcon, ShieldCheck, TriangleAlert, Users } from '../components/icons';
import { Markdown } from '../components/Markdown';
import { Btn, Card, Disclosure, NavHeader, Screen, SectionLabel, Segmented, T, showError } from '../components/ui';
import { L } from '../i18n';
import { radius, space, type, useTheme } from '../theme';

const BLOCK = '▇▇▇';

/** 标题里挡住的地方（服务器给的是 ▇▇▇）画成一小段浅灰条，和链接页一样。 */
function masked(text: string, fill: string): React.ReactNode[] {
  return text.split(BLOCK).flatMap((part, i) => (i ? [<Text key={i} style={{ backgroundColor: fill, color: fill }}>{'\u2003\u2003'}</Text>, part] : [part]));
}

/** 链接页在自己设备上的地址（服务器地址 + 路径；网页版同源）。 */
function localUrl(path: string): string {
  const base = getBase() || (Platform.OS === 'web' && typeof window !== 'undefined' ? window.location.origin : '');
  return `${base}${path}`;
}

/** 网页版：把图存下来（data URI → 下载）。 */
function downloadWeb(dataUri: string, name: string) {
  const doc = (globalThis as { document?: { createElement: (t: string) => { href: string; download: string; click: () => void; remove: () => void }; body: { appendChild: (n: unknown) => void } } }).document;
  if (!doc) return;
  const a = doc.createElement('a');
  a.href = dataUri;
  a.download = name;
  doc.body.appendChild(a);
  a.click();
  a.remove();
}

function MaskRow({ m, last, onToggle }: { m: ShareMask; last: boolean; onToggle: () => void }) {
  const t = useTheme();
  return (
    <View style={[styles.maskRow, !last && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }]}>
      <View style={[styles.maskIcon, { backgroundColor: m.released ? t.goldSoft : t.surface2 }]}>
        {m.released ? <Eye size={15} color={t.ink2} /> : <EyeOff size={15} color={t.ink2} />}
      </View>
      <View style={{ flex: 1, gap: 2 }}>
        <T v="headline" style={{ fontSize: 15 }}>{m.label}</T>
        <Text numberOfLines={2} style={[type.callout, { color: t.ink3 }]}>
          {m.before ? `…${m.before}` : ''}
          <Text style={{ color: t.ink, fontWeight: '700', backgroundColor: m.released ? t.goldSoft : t.surface2 }}>{` ${m.text} `}</Text>
          {m.after ? `${m.after}…` : ''}
        </Text>
      </View>
      <Pressable onPress={onToggle} accessibilityRole="button" accessibilityLabel={`${m.released ? L('隐藏', 'Hide') : L('显示', 'Show')} ${m.label}`}
        style={({ pressed }) => [styles.mini, { backgroundColor: t.surface2, opacity: pressed ? 0.7 : 1 }]}>
        <T v="callout" style={{ fontWeight: '600' }}>{m.released ? L('隐藏', 'Hide') : L('显示', 'Show')}</T>
      </Pressable>
    </View>
  );
}

export function ShareScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const route = useRoute<any>();
  const from: ShareFrom | undefined = route.params?.from;
  const openId: string | undefined = route.params?.id;
  const [share, setShare] = useState<Share | null>(null);
  const [err, setErr] = useState('');
  const [style, setStyle] = useState<ShareStyle>('link');
  const [card, setCard] = useState<{ key: string; data: ShareCard } | null>(null);
  const [cardErr, setCardErr] = useState<{ key: string; text: string } | null>(null);
  const [quote, setQuote] = useState<{ id: string; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const [showAll, setShowAll] = useState(false);
  // 发给朋友（社交第二层）：有朋友才出这一块
  const [friends, setFriends] = useState<Friend[]>([]);
  const [picked, setPicked] = useState<string[]>([]);
  const [canAsk, setCanAsk] = useState(true);
  const [withLink, setWithLink] = useState(false);
  useEffect(() => { fr.home().then((h) => setFriends(h.friends.filter((f) => f.status === 'active'))).catch(() => setFriends([])); }, []);

  const load = useCallback(() => {
    const go = openId ? shareApi.getShare(openId) : from ? shareApi.createShare(from) : Promise.reject(new Error(L('未指定分享内容', 'Nothing to share')));
    go.then((s) => { setShare(s); setErr(''); }).catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, [openId, from]);
  useEffect(() => { load(); }, [load]);

  // 卡片图跟着挡没挡、那一句、标题变：换了就重新要一张（旧的不显示）
  const cardKey = share && share.status !== 'revoked' ? `${share.id}|${style}|${share.blocked}|${share.quote}|${share.title}|${(share.masks || []).map((m) => (m.released ? 1 : 0)).join('')}` : '';
  useEffect(() => {
    if (!share || !cardKey || card?.key === cardKey) return;
    let live = true;
    shareApi.shareCard(share.id, style)
      .then((data) => { if (live) setCard({ key: cardKey, data }); })
      .catch((e) => { if (live) setCardErr({ key: cardKey, text: e instanceof Error ? e.message : String(e) }); });
    return () => { live = false; };
  }, [share, style, cardKey, card?.key]);

  const update = async (patch: Parameters<typeof shareApi.patchShare>[1]) => {
    if (!share) return;
    try { setShare(await shareApi.patchShare(share.id, patch)); } catch (e) { showError(L('修改失败', "Couldn't update"), e); }
  };
  const toggle = (m: ShareMask) => update(m.released ? { hide: [m.id] } : { release: [m.id] });
  const quoteText = quote && share && quote.id === share.id ? quote.text : share?.quote ?? '';
  const saveQuote = async () => {
    if (!share || !quote || quote.id !== share.id) return;
    const v = quote.text.trim();
    if (v && v !== share.quote) await update({ quote: v });
    setQuote(null);
  };

  const publish = async (): Promise<Share | null> => {
    if (!share) return null;
    if (share.status === 'live') return share;
    const s = await shareApi.publishShare(share.id);
    setShare(s);
    return s;
  };
  const shareLink = async () => {
    if (!share || busy) return;
    setBusy(true);
    setNote('');
    try {
      const s = await publish();
      if (!s?.url) return;
      try {
        await NativeShare.share(Platform.OS === 'ios' ? { url: s.url, message: s.title } : { message: `${s.title}\n${s.url}`, title: s.title });
      } catch {
        await Clipboard.setStringAsync(s.url);  // 网页版的浏览器不支持系统分享：复制链接
        setNote(L('链接已复制，粘贴给对方即可', 'Link copied. Paste it to them.'));
      }
    } catch (e) { showError(L('分享失败', "Couldn't share"), e); } finally { setBusy(false); }
  };
  const copyLink = async () => {
    if (!share || busy) return;
    setBusy(true);
    try {
      const s = await publish();
      if (s?.url) { await Clipboard.setStringAsync(s.url); setNote(L('链接已复制', 'Link copied')); }
    } catch (e) { showError(L('复制失败', "Couldn't copy"), e); } finally { setBusy(false); }
  };
  const shareImage = async () => {
    if (!share || busy) return;
    const data = card?.key === cardKey ? card.data : null;
    if (!data) return;
    setBusy(true);
    setNote('');
    try {
      if (Platform.OS === 'web') { downloadWeb(data.dataUri, `${share.title || 'card'}.png`); setNote(L('图片已保存', 'Image saved')); }
      else await NativeShare.share({ url: data.dataUri });
    } catch (e) { showError(L('分享失败', "Couldn't share"), e); } finally { setBusy(false); }
  };
  const sendToFriends = async () => {
    if (!share || busy || !picked.length) return;
    setBusy(true);
    setNote('');
    try {
      const r = await fr.sendShare(share.id, { friends: picked, ask: canAsk, link: withLink && share.canLink });
      setShare(r.share);
      const names = friends.filter((f) => picked.includes(f.id)).map((f) => f.name);
      setPicked([]);
      setNote(L(`已发送给 ${names.join('、')}`, `Sent to ${names.join(', ')}`));
    } catch (e) { showError(L('发送失败', "Couldn't send"), e); } finally { setBusy(false); }
  };
  const revoke = () => {
    if (!share) return;
    const go = async () => {
      try { await shareApi.revokeShare(share.id); setShare(await shareApi.getShare(share.id)); } catch (e) { showError(L('收回失败', "Couldn't withdraw"), e); }
    };
    if (Platform.OS === 'web') { go(); return; }
    Alert.alert(L('收回此分享？', 'Withdraw this share?'), L('链接将立即失效，对方会看到分享已收回；发送给好友的副本也将一并收回。已保存的图片无法收回。', "The link stops working right away and shows “withdrawn”; copies sent to friends are withdrawn too. Images people already saved can't be taken back."),
      [{ text: L('取消', 'Cancel'), style: 'cancel' }, { text: L('收回', 'Withdraw'), style: 'destructive', onPress: go }]);
  };

  const masks = share?.masks || [];
  const q = share?.source?.withQuestion;
  const shown = card?.key === cardKey ? card.data : null;
  const cardError = !shown && cardErr?.key === cardKey ? cardErr.text : '';
  const fullText = (share?.segments || []).map((s) => (s.m && !s.released ? BLOCK : s.t)).join('');
  const kindLine = share ? [share.kind === 'note' ? L('笔记', 'Note') : share.kind === 'message' ? L('对话消息', 'From a chat') : L('文本', 'Text'),
    share.status === 'live' ? L(`已发布 · 浏览 ${share.views} 次`, `Shared · ${share.views} view${share.views === 1 ? '' : 's'}`) : share.status === 'friends' ? L('仅发送给好友', 'Sent to friends only')
      : share.status === 'revoked' ? L('已收回', 'Withdrawn') : ''].filter(Boolean).join(' · ') : '';
  const sentTo = share?.sentTo ?? [];
  const notYet = friends.filter((f) => !sentTo.some((x) => x.id === f.id));  // 发过的不再列出来（在下面「已经发给」里）
  const friendsBlock = share && share.status !== 'revoked' && (notYet.length || sentTo.length) ? (
    <>
      <SectionLabel>{L('发送给好友', 'Send to friends')}</SectionLabel>
      <Card style={{ gap: space.md }}>
        {notYet.length ? <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm }}>
          {notYet.map((f) => {
            const on = picked.includes(f.id);
            return (
              <Pressable key={f.id} onPress={() => setPicked(on ? picked.filter((x) => x !== f.id) : [...picked, f.id])} accessibilityRole="button" accessibilityState={{ selected: on }}
                style={[styles.pill, { borderColor: on ? t.ink : t.line, backgroundColor: on ? t.ink : t.surface }]}>
                <T v="callout" color={on ? t.bg : t.ink} style={{ fontWeight: '600' }}>{f.name}</T>
              </Pressable>
            );
          })}
        </View> : null}
        {notYet.length ? <><View style={styles.switchRow}>
          <View style={{ flex: 1, gap: 2 }}>
            <T v="body">{L('允许好友追问', 'Friends can ask about it')}</T>
            <T v="caption" color={t.ink3} style={{ fontSize: 13 }}>{L('你的名片 Agent 按对方所在档位回答（「同学」档仅可查看）', 'Your card agent answers by their tier (Classmates can only read)')}</T>
          </View>
          <Switch value={canAsk} onValueChange={setCanAsk} accessibilityLabel={L('允许好友追问', 'Friends can ask about it')} trackColor={{ true: t.cyan, false: t.track }} thumbColor="#FFFFFF" />
        </View>
        {share.canLink && share.status !== 'live' ? (
          <View style={{ gap: space.xs }}>
            <T v="caption" color={t.ink3}>{L('可见范围', 'Who can see it')}</T>
            <Segmented<'only' | 'link'> value={withLink ? 'link' : 'only'} onChange={(v) => setWithLink(v === 'link')}
              options={[{ value: 'only', label: L('仅接收者', 'Only them') }, { value: 'link', label: L('知道链接的人', 'Anyone with the link') }]} />
          </View>
        ) : null}
        <Btn label={busy ? L('正在发送…', 'Sending…') : picked.length ? (picked.length === 1 ? L(`发送给 ${friends.find((f) => f.id === picked[0])?.name}`, `Send to ${friends.find((f) => f.id === picked[0])?.name}`) : L(`发送给 ${picked.length} 位好友`, `Send to ${picked.length} friends`)) : L('请选择接收者', 'Select recipients')}
          icon={<Users size={17} color={t.onGold} />} onPress={sendToFriends} /></> : null}
        {sentTo.length ? <T v="caption" color={t.ink3}>{L(`已发送给：${sentTo.map((x) => x.name).join('、')}`, `Already sent to: ${sentTo.map((x) => x.name).join(', ')}`)}</T> : null}
      </Card>
    </>
  ) : null;

  return (
    <Screen>
      <NavHeader title={L('分享', 'Share')} sub={kindLine || undefined} onBack={() => nav.goBack()} />
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl, gap: space.md }} automaticallyAdjustKeyboardInsets keyboardShouldPersistTaps="handled">
        {err ? <Card><T v="callout" color={t.bad}>{L(`无法打开：${err}`, `Couldn't open it: ${err}`)}</T></Card> : null}
        {!share && !err ? <ActivityIndicator color={t.gold} style={{ marginTop: space.xl }} /> : null}

        {share ? (
          <Card style={{ gap: 4 }}>
            <T v="headline" style={{ fontSize: 17 }} numberOfLines={3}>{share.title ? masked(share.title, `${t.ink3}59`) : L('（无标题）', '(untitled)')}</T>
            <T v="caption" color={t.ink3}>{`${share.day} ${share.time}`}</T>
          </Card>
        ) : null}

        {share?.status === 'revoked' ? (
          <Card style={{ gap: space.sm }}>
            <T v="headline">{L('此分享已收回', 'This share was withdrawn')}</T>
            <T v="callout" color={t.ink2}>{L('链接现显示为已收回，服务器上的快照也已删除。如需再次分享，请回到原内容重新分享（将生成新链接）。', 'The link now says it was withdrawn and the snapshot on the server is gone. Share the original again for a new link.')}</T>
          </Card>
        ) : null}

        {share && share.status !== 'revoked' ? (
          <>
            <Card style={{ paddingVertical: space.sm, gap: 2 }}>
              <View style={styles.shieldHead}>
                <View style={[styles.shield, { backgroundColor: t.goodSoft }]}><ShieldCheck size={17} color={t.good} /></View>
                <View style={{ flex: 1, gap: 2 }}>
                  <T v="headline">{share.maskCount === 0 ? L('未发现需要隐藏的隐私信息', 'Nothing private spotted')
                    : share.blocked > 0 ? L(`发送前已隐藏 ${share.blocked} 处`, `Hidden before sharing: ${share.blocked}`) : L(`${share.maskCount} 处已全部显示`, `You showed all ${share.maskCount}`)}</T>
                  <T v="caption" color={t.ink3} style={{ fontSize: 13, lineHeight: 18 }}>{L('住址、家人姓名、邮箱和电话、身体数据。隐藏的原文不会离开你的服务器。', 'Addresses, family names, contact details, body numbers. Hidden text never leaves your server.')}</T>
                </View>
              </View>
              {masks.map((m, i) => <MaskRow key={m.id} m={m} last={i === masks.length - 1} onToggle={() => toggle(m)} />)}
            </Card>

            {share.kind === 'message' && share.source.hasQuestion ? (
              <Card style={styles.switchRow}>
                <View style={{ flex: 1, gap: 2 }}>
                  <T v="body">{L('包含我的提问', 'Include my question')}</T>
                  <T v="caption" color={t.ink3} style={{ fontSize: 13 }}>{L('显示在回复上方，便于对方了解上下文', 'Shown above the reply so they know the context')}</T>
                </View>
                <Switch value={!!q} onValueChange={(v) => update({ withQuestion: v })} accessibilityLabel={L('包含我的提问', 'Include my question')}
                  trackColor={{ true: t.cyan, false: t.track }} thumbColor="#FFFFFF" />
              </Card>
            ) : null}

            <SectionLabel>{L('卡片样式', 'Card')}</SectionLabel>
            <Segmented<ShareStyle> value={style} onChange={(v) => { setStyle(v); setNote(''); }}
              options={[{ value: 'link', label: L('含链接', 'With link') }, { value: 'clean', label: L('简洁版', 'Clean') }]} />
            <View style={[styles.preview, { backgroundColor: t.surface2, aspectRatio: style === 'clean' ? 1080 / 1440 : 1200 / 630 }]}>
              {shown ? <Image source={{ uri: shown.dataUri }} style={StyleSheet.absoluteFill} resizeMode="contain" accessibilityLabel={L('卡片预览', 'Card preview')} />
                : cardError ? <T v="callout" color={t.bad} style={{ padding: space.md }}>{cardError}</T>
                  : <ActivityIndicator color={t.gold} />}
            </View>
            <T v="caption" color={t.ink3} style={{ fontSize: 13 }}>
              {style === 'link' ? L('适用于微信、WhatsApp：对方点开链接即可阅读全文，无需安装 app。对话中显示的即为此预览图。', 'For WhatsApp or WeChat: they tap the link to read it all, no app needed. This is the preview they see.')
                : L('适用于小红书：图片中不含网址、二维码和 app 名称。', 'For Xiaohongshu and the like: no link, QR code or app name in the image.')}
            </T>

            <SectionLabel>{L('卡片文案', 'The line on the card')}</SectionLabel>
            <Card style={{ gap: space.sm }}>
              <TextInput value={quoteText} onChangeText={(v) => setQuote({ id: share.id, text: v })} onBlur={saveQuote} onSubmitEditing={saveQuote}
                multiline blurOnSubmit returnKeyType="done" maxLength={140} placeholder={L('输入卡片上显示的一句话', 'A line for the card')} placeholderTextColor={t.ink3}
                style={[type.body, styles.input, { color: t.ink, borderColor: t.line }]} accessibilityLabel={L('卡片文案', 'The line on the card')} />
              {share.quoteCustom ? (
                <Pressable onPress={() => update({ quote: '' })} accessibilityRole="button" style={{ alignSelf: 'flex-start' }}>
                  <T v="callout" color={t.cyan} style={{ fontWeight: '600' }}>{L('恢复默认文案', 'Use the default line')}</T>
                </Pressable>
              ) : null}
            </Card>

            <Pressable onPress={() => setShowAll(!showAll)} accessibilityRole="button" style={styles.fold}>
              <T v="callout" color={t.ink2} style={{ fontWeight: '600', flex: 1 }}>{L('查看将发送的全文', 'See the full text they get')}</T>
              <Disclosure open={showAll} />
            </Pressable>
            {showAll ? <Card><Markdown text={fullText} /></Card> : null}

            {friendsBlock}

            {style === 'link' && !share.canLink ? (
              <View style={[styles.warn, { backgroundColor: t.warnSoft }]}>
                <TriangleAlert size={17} color={t.warn} />
                <T v="callout" color={t.ink} style={{ flex: 1 }}>{L('服务器尚未配置公网链接地址，他人无法打开分享链接。简洁版卡片不受影响，可直接发送。', "The server doesn't have a public address for links yet, so others couldn't open one. The clean card works now.")}</T>
              </View>
            ) : null}

            {style === 'link' ? (share.canLink ? (
              <View style={{ gap: space.sm }}>
                <Btn label={busy ? L('正在分享…', 'Sharing…') : L('分享链接', 'Share link')} icon={<ShareIcon size={18} color={t.onGold} />} onPress={shareLink} />
                <Btn label={L('复制链接', 'Copy link')} kind="quiet" icon={<Copy size={18} color={t.ink} />} onPress={copyLink} />
              </View>
            ) : <Btn label={L('改用简洁版卡片', 'Send the clean card instead')} kind="quiet" onPress={() => setStyle('clean')} />) : (
              <Btn label={L('分享图片', 'Share image')} icon={<ShareIcon size={18} color={t.onGold} />} onPress={shareImage} />
            )}
            {note ? <View style={styles.noteRow}><Check size={16} color={t.good} /><T v="callout" color={t.good}>{note}</T></View> : null}

            {share.status === 'friends' ? (
              <Card style={{ gap: space.sm, marginTop: space.sm }}>
                <T v="callout" color={t.ink2}>{L('此分享仅发送给好友，链接未开放。', 'Sent to friends only; the link is closed.')}</T>
                <Btn label={L('收回（好友处一并收回）', 'Withdraw (from friends too)')} kind="danger" onPress={revoke} />
              </Card>
            ) : null}
            {share.status === 'live' ? (
              <Card style={{ gap: space.sm, marginTop: space.sm }}>
                <View style={styles.noteRow}>
                  <Link2 size={16} color={t.cyan} />
                  <T v="callout" color={t.ink2} style={{ flex: 1 }} numberOfLines={1}>{share.url || L('链接仅可在你自己的设备上打开', 'The link only opens on your own devices')}</T>
                </View>
                {share.path ? <Btn label={L('在浏览器中打开', 'Open in browser')} kind="quiet" onPress={() => Linking.openURL(share.url || localUrl(share.path!)).catch((e) => showError(L('无法打开', "Couldn't open it"), e))} /> : null}
                <Btn label={L('收回链接', 'Withdraw link')} kind="danger" onPress={revoke} />
              </Card>
            ) : null}
          </>
        ) : null}
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  shieldHead: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: space.sm },
  shield: { width: 32, height: 32, borderRadius: 10, alignItems: 'center', justifyContent: 'center' },
  maskRow: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: 10 },
  maskIcon: { width: 28, height: 28, borderRadius: 14, alignItems: 'center', justifyContent: 'center' },
  mini: { height: 32, paddingHorizontal: 12, borderRadius: 16, alignItems: 'center', justifyContent: 'center' },
  switchRow: { flexDirection: 'row', alignItems: 'center', gap: space.md },
  preview: { width: '100%', borderRadius: radius.md, overflow: 'hidden', alignItems: 'center', justifyContent: 'center' },
  input: { minHeight: 64, borderWidth: StyleSheet.hairlineWidth, borderRadius: radius.sm, paddingHorizontal: space.md, paddingVertical: space.sm, textAlignVertical: 'top' },
  fold: { flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingVertical: space.xs, paddingHorizontal: space.xs },
  warn: { flexDirection: 'row', gap: space.sm, alignItems: 'flex-start', padding: space.md, borderRadius: radius.md },
  noteRow: { flexDirection: 'row', alignItems: 'center', gap: space.sm },
  pill: { height: 36, paddingHorizontal: 14, borderRadius: 18, borderWidth: 1, alignItems: 'center', justifyContent: 'center' },
});
