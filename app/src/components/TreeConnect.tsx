// 「我 → 世界树」最上面的「接到你的 AI」（server/memtree.py 的 /api/tree/connect，2026-09-28）：每个 AI 平台一个地址，树按地址知道是谁写的。
// 一行一个平台（最近写过没有）；点开是地址（复制）、怎么接（一步一句）、要贴进它自定义指令的那句（复制）、删掉。
// 「加一个」：还没加的常见平台（DeepSeek、通义千问、Kimi……）或者自己写英文名。没开公网时平台们连不上，最上面一行提示，点开是开公网的命令。
// 都还没写过的（新装的）默认展开，写过的收成一行，省得把叶子挤下去。
import * as Clipboard from 'expo-clipboard';
import React, { useState } from 'react';
import { Platform, Pressable, StyleSheet, TextInput, View } from 'react-native';
import { addPlatform, removePlatform } from '../api/tree';
import type { TreeConnect, TreePlatform } from '../data/types';
import { L } from '../i18n';
import { radius, space, useTheme } from '../theme';
import { Check, Copy, Globe, Plus, Trash2 } from './icons';
import { useSheet } from './Sheet';
import { Btn, Card, Disclosure, SectionLabel, T, showError } from './ui';

const mono = Platform.select({ ios: 'Menlo', android: 'monospace', default: 'ui-monospace, SFMono-Regular, Menlo, monospace' });

/** 最近写过的日子：今天 / 昨天 / 9/26。 */
function wroteLabel(iso: string | null): string {
  if (!iso) return L('还没写过', 'Not used yet');
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return L('写过', 'Used');
  const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(new Date()) - day(d)) / 86400000);
  if (diff <= 0) return L('今天写过', 'Wrote today');
  if (diff === 1) return L('昨天写过', 'Wrote yesterday');
  return L(`${d.getMonth() + 1}/${d.getDate()} 写过`, `Wrote ${d.toLocaleDateString('en', { month: 'short', day: 'numeric' })}`);
}

/** 一段能复制的字：等宽框里原样显示（能长按选），右下角「复制」，点完变「复制好了」。 */
function CopyBox({ text, label, secret }: { text: string; label: string; secret?: boolean }) {
  const t = useTheme();
  const [copied, setCopied] = useState(false);
  return (
    <View style={[styles.box, { backgroundColor: t.surface }]}>
      <T v="callout" selectable style={[styles.mono, secret ? { color: t.ink2 } : null]}>{text}</T>
      <Pressable onPress={() => { Clipboard.setStringAsync(text).then(() => setCopied(true)).catch((e) => showError(L('没复制上', "Couldn't copy"), e)); }}
        accessibilityRole="button" accessibilityLabel={label} hitSlop={6}
        style={({ pressed }) => [styles.copy, { backgroundColor: t.surface2, opacity: pressed ? 0.6 : 1 }]}>
        {copied ? <Check size={14} color={t.ink} /> : <Copy size={14} color={t.ink} />}
        <T v="caption" style={{ fontWeight: '600' }}>{copied ? L('复制好了', 'Copied') : label}</T>
      </Pressable>
    </View>
  );
}

function PlatformSheet({ p, info, close, onChanged }: { p: TreePlatform; info: TreeConnect; close: () => void; onChanged: () => void }) {
  const t = useTheme();
  const sheet = useSheet();
  const url = p.url ?? L('<地址>', '<address>');
  return (
    <View style={{ gap: space.md }}>
      {p.url ? <CopyBox text={p.url} label={L('复制地址', 'Copy address')} /> : (
        <T v="callout" color={t.warn}>{L('还没开公网，这个平台现在连不上你的世界树。先按上面「还没开公网」那一行开好，地址就会出现在这里。',
          "The memory tree isn't on the internet yet, so this app can't reach it. Open it up first (the row at the top), and the address appears here.")}</T>
      )}
      {p.auth === 'header' && p.token ? <CopyBox text={p.token} label={L('复制令牌', 'Copy token')} secret /> : null}
      <View style={{ gap: space.sm }}>
        <T v="label" color={t.ink3}>{L('怎么接', 'HOW TO CONNECT')}</T>
        {p.steps.map((s, i) => (
          <View key={i} style={styles.step}>
            <T v="callout" color={t.gold} style={styles.stepNum}>{i + 1}</T>
            <T v="callout" selectable style={{ flex: 1 }}>{s.split('{url}').join(url)}</T>
          </View>
        ))}
      </View>
      <View style={{ gap: space.sm }}>
        <T v="label" color={t.ink3}>{L('贴进它的自定义指令', 'FOR ITS CUSTOM INSTRUCTIONS')}</T>
        <CopyBox text={info.instruction} label={L('复制这句', 'Copy')} />
      </View>
      <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L(
        '地址里带着这个平台的钥匙，别发给别人。它写下的每一条都记在它名下，在下面「按来源」里能看到。',
        "The address carries this app's key, so keep it to yourself. Everything it saves is filed under its name; see By source below.",
      )}</T>
      <Btn kind="danger" label={L('删掉这个平台', 'Remove this app')} icon={<Trash2 size={16} color={t.bad} />}
        onPress={() => sheet.open({ title: L(`删掉 ${p.name}？`, `Remove ${p.name}?`), content: (c) => <RemoveSheet p={p} close={() => { c(); close(); }} onChanged={onChanged} restartable={info.restartable} /> })} />
    </View>
  );
}

function RemoveSheet({ p, close, onChanged, restartable }: { p: TreePlatform; close: () => void; onChanged: () => void; restartable: boolean }) {
  const t = useTheme();
  const [busy, setBusy] = useState(false);
  return (
    <View style={{ gap: space.md }}>
      <T v="callout" color={t.ink2}>{L(
        `${p.name} 的地址马上作废，它读不到、也写不进世界树了。它以前写下的叶子都还在。${restartable ? '世界树会重启几秒，别的平台不受影响。' : ''}`,
        `${p.name}'s address stops working right away; it can no longer read or write your memory tree. What it saved before stays.${restartable ? ' The memory tree restarts for a few seconds; other apps are fine.' : ''}`,
      )}</T>
      <View style={{ flexDirection: 'row', gap: space.sm }}>
        <Btn flex kind="quiet" label={L('留着', 'Keep')} onPress={close} />
        <Btn flex kind="danger" label={busy ? L('正在删…', 'Removing…') : L('删掉', 'Remove')} onPress={() => {
          if (busy) return;
          setBusy(true);
          removePlatform(p.id).then((r) => {
            onChanged();
            close();
            if (r.changed && r.restarted === false) needRestart();
          }).catch((e) => { showError(L('没删掉', "Couldn't remove it"), e); setBusy(false); });
        }} />
      </View>
    </View>
  );
}

/** 服务器重启不了世界树（没有 systemd 服务）：告诉你自己重启。 */
function needRestart() {
  showError(L('还差一步', 'One more step'), new Error(L(
    '重启一下世界树服务才生效：在服务器上运行 systemctl --user restart mousse-tree',
    'Restart the memory tree service for this to take effect: on the server run systemctl --user restart mousse-tree',
  )));
}

function AddSheet({ info, close, onChanged }: { info: TreeConnect; close: () => void; onChanged: (id?: string) => void }) {
  const t = useTheme();
  const [name, setName] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const add = (n: string) => {
    const v = n.trim();
    if (!v || busy) return;
    setBusy(v);
    addPlatform(v).then((r) => {
      close();
      onChanged(r.id);
      if (r.changed && r.restarted === false) needRestart();
    }).catch((e) => { showError(L('没加上', "Couldn't add it"), e); setBusy(null); });
  };
  return (
    <View style={{ gap: space.md }}>
      <T v="callout" color={t.ink2}>{L(
        '支持 MCP 的 AI 都能接：给它一个自己的地址，它写下的就记在它名下。',
        'Any AI that speaks MCP can connect. It gets its own address, and what it saves is filed under its name.',
      )}</T>
      {info.presets.length ? (
        <View style={styles.chips}>
          {info.presets.map((p) => (
            <Pressable key={p.id} onPress={() => add(p.id)} disabled={!!busy} accessibilityRole="button"
              style={({ pressed }) => [styles.chip, { backgroundColor: t.surface2, opacity: pressed || (busy && busy !== p.id) ? 0.6 : 1 }]}>
              <Plus size={14} color={t.ink} />
              <T v="callout" style={{ fontWeight: '600' }}>{busy === p.id ? L('正在加…', 'Adding…') : p.name}</T>
            </Pressable>
          ))}
        </View>
      ) : null}
      <View style={{ flexDirection: 'row', gap: space.sm, alignItems: 'center' }}>
        <TextInput value={name} onChangeText={setName} placeholder={L('别的平台：英文名，比如 perplexity', 'Another app: its name in English, e.g. perplexity')}
          placeholderTextColor={t.ink3} autoCapitalize="none" autoCorrect={false} returnKeyType="done" onSubmitEditing={() => add(name)}
          accessibilityLabel={L('平台名', 'App name')} style={[styles.input, { backgroundColor: t.surface, color: t.ink, flex: 1 }]} />
        <Btn label={busy && busy === name.trim() ? L('正在加…', 'Adding…') : L('加上', 'Add')} onPress={() => add(name)} />
      </View>
      {info.restartable ? <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L('加的时候世界树会重启几秒，别的平台不受影响。', 'The memory tree restarts for a few seconds while it adds one; other apps are fine.')}</T> : null}
    </View>
  );
}

function FunnelSheet({ info }: { info: TreeConnect }) {
  const t = useTheme();
  return (
    <View style={{ gap: space.md }}>
      <T v="callout" color={t.ink2}>{L(
        'Claude.ai、ChatGPT 这些平台是从它们自己的云来连你的，所以世界树要有一个公网 HTTPS 地址。最省事的是 Tailscale Funnel（免费），在服务器上跑：',
        'Claude.ai, ChatGPT and the like connect from their own clouds, so the memory tree needs a public HTTPS address. The easiest is Tailscale Funnel (free). On the server, run:',
      )}</T>
      {(info.funnel ?? []).map((c) => <CopyBox key={c} text={c} label={L('复制', 'Copy')} />)}
      <T v="callout" color={t.ink2}>{L(
        '然后再跑一遍安装命令，问到「要不要让 AI 平台连世界树」时回答要：它会把地址加进白名单、重启世界树，这里的地址就出来了。',
        'Then run the install command again and answer yes when it asks about letting AI apps connect: it allowlists the address and restarts the memory tree, and the addresses show up here.',
      )}</T>
      <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L('只开 /t 和 /m 两条路径；世界树的管理页不要放到公网。', 'Open only the /t and /m paths; never put the admin page on the internet.')}</T>
    </View>
  );
}

export function ConnectSection({ info, onChanged }: { info: TreeConnect; onChanged: () => void }) {
  const t = useTheme();
  const sheet = useSheet();
  const used = info.platforms.filter((p) => p.lastWrote).length;
  const [open, setOpen] = useState(() => used === 0);
  const openPlatform = (p: TreePlatform) => sheet.open({ title: p.name, content: (c) => <PlatformSheet p={p} info={info} close={c} onChanged={onChanged} /> });
  const summary = used
    ? L(`${info.platforms.length} 个平台，${used} 个写过`, `${info.platforms.length} apps, ${used} have written`)
    : L(`${info.platforms.length} 个平台，还都没写过`, `${info.platforms.length} apps, none used yet`);
  const sep = { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line };
  return (
    <>
      <SectionLabel right={(
        <Pressable onPress={() => sheet.open({ title: L('加一个平台', 'Add an app'), content: (c) => <AddSheet info={info} close={c} onChanged={onChanged} /> })}
          hitSlop={8} accessibilityRole="button" accessibilityLabel={L('加一个平台', 'Add an app')} style={styles.add}>
          <Plus size={15} color={t.gold} />
          <T v="callout" color={t.gold} style={{ fontWeight: '600' }}>{L('加一个', 'Add')}</T>
        </Pressable>
      )}>{L('接到你的 AI', 'Connect your AI')}</SectionLabel>
      <Card style={{ paddingVertical: space.xs }}>
        <Pressable onPress={() => setOpen((v) => !v)} accessibilityRole="button" accessibilityState={{ expanded: open }}
          style={({ pressed }) => [styles.row, { opacity: pressed ? 0.6 : 1 }]}>
          <Globe size={18} color={t.cyan} />
          <View style={{ flex: 1 }}>
            <T v="body">{summary}</T>
            <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L('Claude、ChatGPT、DeepSeek、通义千问、Kimi……支持 MCP 的都能接', 'Claude, ChatGPT, DeepSeek, Qwen, Kimi… anything that speaks MCP')}</T>
          </View>
          <Disclosure open={open} />
        </Pressable>
        {open ? (
          <>
            {!info.public ? (
              <Pressable onPress={() => sheet.open({ title: L('开公网', 'Put it on the internet'), content: () => <FunnelSheet info={info} /> })} accessibilityRole="button"
                style={({ pressed }) => [styles.row, sep, { opacity: pressed ? 0.6 : 1 }]}>
                <View style={{ flex: 1 }}>
                  <T v="body" color={t.warn}>{L('还没开公网', 'Not on the internet yet')}</T>
                  <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L('开好之前这些平台连不上，点这里看怎么开', "Until then these apps can't reach it. Tap to see how")}</T>
                </View>
                <Disclosure open={false} />
              </Pressable>
            ) : null}
            {info.platforms.map((p) => (
              <Pressable key={p.id} onPress={() => openPlatform(p)} accessibilityRole="button" accessibilityHint={L('看地址和怎么接', 'Shows the address and how to connect')}
                style={({ pressed }) => [styles.row, sep, { opacity: pressed ? 0.6 : 1 }]}>
                <T v="body" style={{ flex: 1 }}>{p.name}</T>
                <T v="caption" color={p.lastWrote ? t.good : t.ink3} style={{ fontWeight: '500' }}>{wroteLabel(p.lastWrote)}</T>
                <Disclosure open={false} />
              </Pressable>
            ))}
          </>
        ) : null}
      </Card>
      {open ? <T v="caption" color={t.ink3} style={styles.note}>{L(
        '每个平台一个地址。接上以后，在它的自定义指令里贴那一句，它就会先读树再回答，你说起自己的新事它会写回来。',
        'Each app gets its own address. Once connected, paste the instruction into its custom instructions: it reads the tree before answering and writes back what you tell it about yourself.',
      )}</T> : null}
    </>
  );
}

const styles = StyleSheet.create({
  box: { borderRadius: radius.md, padding: space.md, gap: space.sm },
  mono: { fontFamily: mono, fontSize: 13, lineHeight: 19 },
  copy: { alignSelf: 'flex-end', flexDirection: 'row', alignItems: 'center', gap: 5, borderRadius: radius.pill, paddingHorizontal: 12, paddingVertical: 6 },
  step: { flexDirection: 'row', gap: space.sm, alignItems: 'flex-start' },
  stepNum: { width: 16, fontWeight: '700', fontVariant: ['tabular-nums'] },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: space.sm },
  chip: { flexDirection: 'row', alignItems: 'center', gap: 4, borderRadius: radius.pill, paddingHorizontal: 12, paddingVertical: 8 },
  input: { borderRadius: radius.md, paddingHorizontal: space.md, paddingVertical: 11, fontSize: 15 },
  add: { flexDirection: 'row', alignItems: 'center', gap: 3 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 12, minHeight: 46 },
  note: { marginTop: space.sm, paddingHorizontal: space.xs, lineHeight: 17, fontWeight: '400' },
});
