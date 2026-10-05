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
  if (!iso) return L('尚未写入', 'Not used yet');
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return L('已写入', 'Used');
  const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(new Date()) - day(d)) / 86400000);
  if (diff <= 0) return L('今天写入', 'Wrote today');
  if (diff === 1) return L('昨天写入', 'Wrote yesterday');
  return L(`${d.getMonth() + 1}/${d.getDate()} 写入`, `Wrote ${d.toLocaleDateString('en', { month: 'short', day: 'numeric' })}`);
}

/** 一段能复制的字：等宽框里原样显示（能长按选），右下角「复制」，点完变「复制好了」。 */
function CopyBox({ text, label, secret }: { text: string; label: string; secret?: boolean }) {
  const t = useTheme();
  const [copied, setCopied] = useState(false);
  return (
    <View style={[styles.box, { backgroundColor: t.surface }]}>
      <T v="callout" selectable style={[styles.mono, secret ? { color: t.ink2 } : null]}>{text}</T>
      <Pressable onPress={() => { Clipboard.setStringAsync(text).then(() => setCopied(true)).catch((e) => showError(L('复制失败', "Couldn't copy"), e)); }}
        accessibilityRole="button" accessibilityLabel={label} hitSlop={6}
        style={({ pressed }) => [styles.copy, { backgroundColor: t.surface2, opacity: pressed ? 0.6 : 1 }]}>
        {copied ? <Check size={14} color={t.ink} /> : <Copy size={14} color={t.ink} />}
        <T v="caption" style={{ fontWeight: '600' }}>{copied ? L('已复制', 'Copied') : label}</T>
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
        <T v="callout" color={t.warn}>{L('尚未开放公网访问，此平台目前无法连接你的世界树。请先按上方「尚未开放公网」一行的说明开启，地址将显示在这里。',
          "The memory tree isn't publicly reachable yet, so this app can't connect to it. Enable public access first (the row at the top); the address will appear here.")}</T>
      )}
      {p.auth === 'header' && p.token ? <CopyBox text={p.token} label={L('复制令牌', 'Copy token')} secret /> : null}
      <View style={{ gap: space.sm }}>
        <T v="label" color={t.ink3}>{L('连接步骤', 'HOW TO CONNECT')}</T>
        {p.steps.map((s, i) => (
          <View key={i} style={styles.step}>
            <T v="callout" color={t.gold} style={styles.stepNum}>{i + 1}</T>
            <T v="callout" selectable style={{ flex: 1 }}>{s.split('{url}').join(url)}</T>
          </View>
        ))}
      </View>
      <View style={{ gap: space.sm }}>
        <T v="label" color={t.ink3}>{L('粘贴到该平台的自定义指令', 'FOR ITS CUSTOM INSTRUCTIONS')}</T>
        <CopyBox text={info.instruction} label={L('复制', 'Copy')} />
      </View>
      <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L(
        '地址包含此平台的密钥，请勿分享给他人。该平台写入的每一条都记在其名下，可在下方「按来源」中查看。',
        "The address contains this app's key. Do not share it. Everything it saves is filed under its name; see By source below.",
      )}</T>
      <Btn kind="danger" label={L('移除此平台', 'Remove this app')} icon={<Trash2 size={16} color={t.bad} />}
        onPress={() => sheet.open({ title: L(`移除 ${p.name}？`, `Remove ${p.name}?`), content: (c) => <RemoveSheet p={p} close={() => { c(); close(); }} onChanged={onChanged} restartable={info.restartable} /> })} />
    </View>
  );
}

function RemoveSheet({ p, close, onChanged, restartable }: { p: TreePlatform; close: () => void; onChanged: () => void; restartable: boolean }) {
  const t = useTheme();
  const [busy, setBusy] = useState(false);
  return (
    <View style={{ gap: space.md }}>
      <T v="callout" color={t.ink2}>{L(
        `${p.name} 的地址将立即失效，此后无法读取或写入世界树；此前写入的叶子仍会保留。${restartable ? '世界树会重启几秒，别的平台不受影响。' : ''}`,
        `${p.name}'s address stops working right away; it can no longer read or write your memory tree. What it saved before stays.${restartable ? ' The memory tree restarts for a few seconds; other apps are fine.' : ''}`,
      )}</T>
      <View style={{ flexDirection: 'row', gap: space.sm }}>
        <Btn flex kind="quiet" label={L('保留', 'Keep')} onPress={close} />
        <Btn flex kind="danger" label={busy ? L('正在移除…', 'Removing…') : L('移除', 'Remove')} onPress={() => {
          if (busy) return;
          setBusy(true);
          removePlatform(p.id).then((r) => {
            onChanged();
            close();
            if (r.changed && r.restarted === false) needRestart();
          }).catch((e) => { showError(L('移除失败', "Couldn't remove it"), e); setBusy(false); });
        }} />
      </View>
    </View>
  );
}

/** 服务器重启不了世界树（没有 systemd 服务）：告诉你自己重启。 */
function needRestart() {
  showError(L('还需一步', 'One more step'), new Error(L(
    '重启世界树服务后生效：在服务器上运行 systemctl --user restart mousse-tree',
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
    }).catch((e) => { showError(L('添加失败', "Couldn't add it"), e); setBusy(null); });
  };
  return (
    <View style={{ gap: space.md }}>
      <T v="callout" color={t.ink2}>{L(
        '支持 MCP 的 AI 均可连接：每个平台使用独立地址，写入的内容记在其名下。',
        'Any AI that supports MCP can connect. Each app gets its own address, and what it saves is filed under its name.',
      )}</T>
      {info.presets.length ? (
        <View style={styles.chips}>
          {info.presets.map((p) => (
            <Pressable key={p.id} onPress={() => add(p.id)} disabled={!!busy} accessibilityRole="button"
              style={({ pressed }) => [styles.chip, { backgroundColor: t.surface2, opacity: pressed || (busy && busy !== p.id) ? 0.6 : 1 }]}>
              <Plus size={14} color={t.ink} />
              <T v="callout" style={{ fontWeight: '600' }}>{busy === p.id ? L('正在添加…', 'Adding…') : p.name}</T>
            </Pressable>
          ))}
        </View>
      ) : null}
      <View style={{ flexDirection: 'row', gap: space.sm, alignItems: 'center' }}>
        <TextInput value={name} onChangeText={setName} placeholder={L('其他平台：输入英文名，如 perplexity', 'Another app: its name in English, e.g. perplexity')}
          placeholderTextColor={t.ink3} autoCapitalize="none" autoCorrect={false} returnKeyType="done" onSubmitEditing={() => add(name)}
          accessibilityLabel={L('平台名称', 'App name')} style={[styles.input, { backgroundColor: t.surface, color: t.ink, flex: 1 }]} />
        <Btn label={busy && busy === name.trim() ? L('正在添加…', 'Adding…') : L('添加', 'Add')} onPress={() => add(name)} />
      </View>
      {info.restartable ? <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L('添加时世界树将重启数秒，其他平台不受影响。', 'The memory tree restarts for a few seconds while adding; other apps are not affected.')}</T> : null}
    </View>
  );
}

function FunnelSheet({ info }: { info: TreeConnect }) {
  const t = useTheme();
  return (
    <View style={{ gap: space.md }}>
      <T v="callout" color={t.ink2}>{L(
        'Claude.ai、ChatGPT 等平台从各自的云端发起连接，因此世界树需要一个公网 HTTPS 地址。推荐使用 Tailscale Funnel（免费），在服务器上运行：',
        'Claude.ai, ChatGPT and similar apps connect from their own clouds, so the memory tree needs a public HTTPS address. The simplest option is Tailscale Funnel (free). On the server, run:',
      )}</T>
      {(info.funnel ?? []).map((c) => <CopyBox key={c} text={c} label={L('复制', 'Copy')} />)}
      <T v="callout" color={t.ink2}>{L(
        '然后重新运行安装命令，在询问「要不要让 AI 平台连世界树」时回答「要」：安装程序会将地址加入白名单并重启世界树，地址随后显示在这里。',
        'Then run the install command again and answer yes when it asks about letting AI apps connect. It adds the address to the allowlist and restarts the memory tree; the addresses then appear here.',
      )}</T>
      <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L('仅开放 /t 和 /m 两条路径；请勿将世界树管理页暴露到公网。', 'Open only the /t and /m paths. Never expose the admin page to the internet.')}</T>
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
    ? L(`${info.platforms.length} 个平台，${used} 个已写入`, `${info.platforms.length} apps, ${used} have written`)
    : L(`${info.platforms.length} 个平台，均未写入`, `${info.platforms.length} apps, none used yet`);
  const sep = { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line };
  return (
    <>
      <SectionLabel right={(
        <Pressable onPress={() => sheet.open({ title: L('添加平台', 'Add an app'), content: (c) => <AddSheet info={info} close={c} onChanged={onChanged} /> })}
          hitSlop={8} accessibilityRole="button" accessibilityLabel={L('添加平台', 'Add an app')} style={styles.add}>
          <Plus size={15} color={t.gold} />
          <T v="callout" color={t.gold} style={{ fontWeight: '600' }}>{L('添加', 'Add')}</T>
        </Pressable>
      )}>{L('连接你的 AI', 'Connect your AI')}</SectionLabel>
      <Card style={{ paddingVertical: space.xs }}>
        <Pressable onPress={() => setOpen((v) => !v)} accessibilityRole="button" accessibilityState={{ expanded: open }}
          style={({ pressed }) => [styles.row, { opacity: pressed ? 0.6 : 1 }]}>
          <Globe size={18} color={t.cyan} />
          <View style={{ flex: 1 }}>
            <T v="body">{summary}</T>
            <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L('Claude、ChatGPT、DeepSeek、通义千问、Kimi 等支持 MCP 的 AI 均可连接', 'Claude, ChatGPT, DeepSeek, Qwen, Kimi and any other AI that supports MCP')}</T>
          </View>
          <Disclosure open={open} />
        </Pressable>
        {open ? (
          <>
            {!info.public ? (
              <Pressable onPress={() => sheet.open({ title: L('开放公网访问', 'Enable public access'), content: () => <FunnelSheet info={info} /> })} accessibilityRole="button"
                style={({ pressed }) => [styles.row, sep, { opacity: pressed ? 0.6 : 1 }]}>
                <View style={{ flex: 1 }}>
                  <T v="body" color={t.warn}>{L('尚未开放公网', 'Not publicly reachable yet')}</T>
                  <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L('开启前这些平台无法连接，点击查看开启方法', "Until then these apps can't connect. Tap for instructions")}</T>
                </View>
                <Disclosure open={false} />
              </Pressable>
            ) : null}
            {info.platforms.map((p) => (
              <Pressable key={p.id} onPress={() => openPlatform(p)} accessibilityRole="button" accessibilityHint={L('查看地址和连接步骤', 'Shows the address and how to connect')}
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
        '每个平台使用独立地址。连接后，将上述指令粘贴到该平台的自定义指令中：它会先读取世界树再回答，并写回你提到的新信息。',
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
