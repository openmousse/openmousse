// 设备和节点（第 8 批，设置 → 硬件 → 设备和节点，route Nodes / Node）：配对的电脑和手机，各自能做什么、开到哪一档。
// 列表 → 一个节点：报上来的命令（批了没有、关 / 每次先问 / 直接给）、等批准的那几样（这里也能直接批）、放行的命令、给出来的文件夹、移除。
// 「添加一台电脑」：出一个节点配对码（POST /api/nodes/pair），给出在那台电脑上要跑的命令。服务器没开设备节点（engine.nodes）时说怎么开。
// 没有设计稿：照硬件页、claw 页的 Group / Row 做的最简版，等设计补稿。网页版也能用（只是管理，不当节点）。
import React, { useCallback, useState } from 'react';
import { ActivityIndicator, Alert, Platform, ScrollView, StyleSheet, View } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import { useFocusEffect, useNavigation, useRoute } from '@react-navigation/native';
import { getBase, httpStatus } from '../api/base';
import { nodesApi, type NodeCommand, type NodeInfo, type NodeLevel, type NodePair } from '../api/nodes';
import { needsUpdate, supports } from '../api/version';
import { Monitor, Plus, ShieldCheck, Smartphone, Terminal } from '../components/icons';
import { Dot, Group, GroupLabel, GroupNote, Row, SettingsHeader } from '../components/settings';
import { useSheet } from '../components/Sheet';
import { Btn, Screen, T, showError } from '../components/ui';
import { L } from '../i18n';
import { space, useTheme } from '../theme';
import { levelLabel } from './PhoneNodeScreen';

const LEVELS: NodeLevel[] = ['off', 'ask', 'allow'];

function seen(iso: string | null): string {
  if (!iso) return L('从未连接', 'Never connected');
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleString(L('zh-CN', 'en'), { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function confirm(title: string, body: string, action: string, run: () => void) {
  if (Platform.OS === 'web') {
    if (typeof window !== 'undefined' && window.confirm(`${title}\n${body}`)) run();
    return;
  }
  Alert.alert(title, body, [{ text: L('取消', 'Cancel'), style: 'cancel' }, { text: action, style: 'destructive', onPress: run }]);
}

/** 服务器没开、太旧：一行说明。 */
function Off({ old }: { old: boolean }) {
  const t = useTheme();
  return (
    <>
      <Group><Row first icon={<Monitor size={22} color={t.ink3} />} title={old ? L('需要更新服务器', 'Server update needed') : L('服务器未开启设备节点', 'Device nodes are off on the server')} /></Group>
      <GroupNote>{old ? needsUpdate('这台服务器还不支持设备节点。', "This server doesn't support device nodes yet.")
        : L('claw 选自己的内核（builtin），并在 server.json 的 engine 中加入 "nodes": {}。配对的电脑报上来的命令需要在收件箱中批准。',
          'Use the built-in engine and add "nodes": {} under engine in server.json. Commands a paired computer reports need approval in the inbox.')}</GroupNote>
    </>
  );
}

export function NodesScreen() {
  const nav = useNavigation<any>();
  const t = useTheme();
  const sheet = useSheet();
  const [nodes, setNodes] = useState<NodeInfo[] | null>(null);
  const [off, setOff] = useState(false);
  const [error, setError] = useState('');
  const server = supports('nodes');

  const load = useCallback(() => {
    if (!server) return;
    nodesApi.list().then((r) => { setNodes(r.nodes); setOff(false); setError(''); })
      .catch((e: unknown) => { if (httpStatus(e) === 404) setOff(true); else setError(e instanceof Error ? e.message : String(e)); });
  }, [server]);
  useFocusEffect(load);

  const addComputer = () => {
    const base = getBase() || (Platform.OS === 'web' && typeof window !== 'undefined' ? window.location.origin : '');
    nodesApi.pair(base, L('电脑', 'computer')).then((p) => {
      sheet.open({ title: L('添加一台电脑', 'Add a computer'), content: (close) => <PairSheet p={p} close={close} /> });
    }).catch((e: unknown) => showError(L('无法生成配对码', "Couldn't make a pairing code"), e));
  };

  return (
    <Screen>
      <SettingsHeader title={L('设备和节点', 'Devices and nodes')} onBack={nav.canGoBack() ? () => nav.goBack() : undefined} />
      <ScrollView contentContainerStyle={styles.scroll}>
        {!server || off ? <Off old={!server} /> : (
          <>
            <Group>
              {error ? <Row first title={L('无法加载', "Couldn't load")} sub={error} />
                : nodes === null ? <Row first title={L('正在加载…', 'Loading…')} right={<ActivityIndicator color={t.ink3} />} />
                  : !nodes.length ? <Row first title={L('还没有节点', 'No nodes yet')} sub={L('添加一台电脑，或在「这台手机」中打开一项', 'Add a computer, or turn something on under This phone')} />
                    : nodes.map((n, i) => {
                      const waiting = n.pending?.commands.length ?? 0;
                      const sub = [n.platform, n.online ? L('在线', 'Online') : L(`离线 · ${seen(n.seenAt)}`, `Offline · ${seen(n.seenAt)}`),
                        waiting ? L(`${waiting} 项待批准`, `${waiting} awaiting approval`) : ''].filter(Boolean).join(' · ');
                      return (
                        <Row key={n.id} first={i === 0} icon={n.kind === 'phone' ? <Smartphone size={22} color={t.cyan} /> : <Monitor size={22} color={t.cyan} />}
                          title={n.label} sub={sub} right={<Dot tone={n.online ? 'good' : waiting ? 'warn' : 'off'} />} onPress={() => nav.navigate('Node', { id: n.id })} />
                      );
                    })}
              <Row icon={<Plus size={22} color={t.cyan} />} title={L('添加一台电脑', 'Add a computer')} accent onPress={addComputer} />
            </Group>
            <GroupNote>{L('节点替 Agent 做服务器做不了的事：在电脑上跑命令、截屏、读写给出来的文件夹，在手机上定位、拍照。拍照、截屏、定位、跑命令、写文件和操作电脑批准以后仍默认关闭，需要逐项打开。',
              "Nodes do what the server can't for Agents: run commands, take screenshots and read or write shared folders on a computer; location and photos on a phone. Photos, screenshots, location, commands, file writes and computer control stay off after approval until you turn each one on.")}</GroupNote>
          </>
        )}
      </ScrollView>
    </Screen>
  );
}

function PairSheet({ p, close }: { p: NodePair; close: () => void }) {
  const t = useTheme();
  const [copied, setCopied] = useState(false);
  const [minutes] = useState(() => Math.max(1, Math.round((Date.parse(p.expires) - Date.now()) / 60_000)));
  return (
    <View style={{ gap: space.md, paddingBottom: space.sm }}>
      <T v="body" color={t.ink2}>{L('在要当节点的电脑上运行下面的命令（node_host.py 在仓库的 server/ 目录中；装了 OpenMousse 的电脑也可以用 mousse node pair）。',
        'Run this on the computer that should act as a node (node_host.py is in the repo\'s server/ folder; with OpenMousse installed, mousse node pair works too).')}</T>
      <View style={[styles.code, { backgroundColor: t.surface2 }]}><T v="callout" selectable>{p.command}</T></View>
      <T v="callout" color={t.ink3}>{L(`${minutes} 分钟内有效，只能用一次。连上以后，它报上来的命令在收件箱中批准。`, `Valid for ${minutes} min, once. Once it connects, approve the commands it reports in the inbox.`)}</T>
      <Btn label={copied ? L('已复制', 'Copied') : L('复制命令', 'Copy command')} onPress={() => { Clipboard.setStringAsync(p.command).then(() => setCopied(true)).catch(() => {}); }} />
      <Btn kind="quiet" label={L('完成', 'Done')} onPress={close} />
    </View>
  );
}

export function NodeScreen() {
  const nav = useNavigation<any>();
  const route = useRoute<any>();
  const t = useTheme();
  const sheet = useSheet();
  const id = String(route.params?.id ?? '');
  const [node, setNode] = useState<NodeInfo | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    nodesApi.get(id).then((r) => { setNode(r.node); setError(''); }).catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, [id]);
  useFocusEffect(load);

  const approve = () => {
    if (busy) return;
    setBusy(true);
    nodesApi.approve(id).then((r) => setNode(r.node)).catch((e: unknown) => showError(L('批准失败', "Couldn't approve"), e)).finally(() => setBusy(false));
  };
  const remove = () => {
    if (!node) return;
    confirm(L(`移除「${node.label}」？`, `Remove ${node.label}?`),
      node.kind === 'host' ? L('它的批准和各项设置都会删除，节点令牌失效，需重新配对。', 'Its approvals and settings are deleted and its node token stops working; it will need to pair again.')
        : L('它的批准和各项设置都会删除。这台手机的连接不受影响。', "Its approvals and settings are deleted. The phone's own connection stays."),
      L('移除', 'Remove'), () => { nodesApi.remove(id).then(() => nav.goBack()).catch((e: unknown) => showError(L('移除失败', "Couldn't remove it"), e)); });
  };
  const pick = (c: NodeCommand) => {
    sheet.open({ title: c.label, content: (close) => <LevelSheet node={id} c={c} kind={node?.kind ?? 'host'} close={close} onDone={setNode} /> });
  };

  const usable = node?.commands.filter((c) => c.approved) ?? [];
  const waiting = node?.pending?.commands ?? node?.commands.filter((c) => c.declared && !c.approved).map((c) => c.name) ?? [];
  return (
    <Screen>
      <SettingsHeader title={node?.label ?? L('节点', 'Node')} onBack={nav.canGoBack() ? () => nav.goBack() : undefined} />
      <ScrollView contentContainerStyle={styles.scroll}>
        {error ? <Group><Row first title={L('无法加载', "Couldn't load")} sub={error} /></Group>
          : !node ? <Group><Row first title={L('正在加载…', 'Loading…')} right={<ActivityIndicator color={t.ink3} />} /></Group> : (
            <>
              <Group>
                <Row first icon={node.kind === 'phone' ? <Smartphone size={22} color={t.cyan} /> : <Monitor size={22} color={t.cyan} />}
                  title={node.online ? L('在线', 'Online') : L('离线', 'Offline')} sub={node.online ? undefined : L(`上次连接：${seen(node.seenAt)}`, `Last seen: ${seen(node.seenAt)}`)}
                  right={<Dot tone={node.online ? 'good' : 'off'} />} />
                <Row title={L('平台', 'Platform')} value={[node.platform, node.version].filter(Boolean).join(' · ') || '—'} />
                {Object.entries(node.permissions).map(([k, v]) => <Row key={k} title={k} value={v ? L('已授权', 'Granted') : L('未授权', 'Not granted')} tone={v ? 'good' : 'warn'} />)}
              </Group>

              {waiting.length ? (
                <>
                  <GroupLabel>{L('待批准', 'Awaiting approval')}</GroupLabel>
                  <Group>
                    {waiting.map((name, i) => <Row key={name} first={i === 0} title={node.commands.find((c) => c.name === name)?.label ?? name} sub={name} />)}
                    <Row icon={<ShieldCheck size={22} color={t.cyan} />} title={busy ? L('正在批准…', 'Approving…') : L('批准', 'Approve')} accent onPress={approve} />
                  </Group>
                  <GroupNote>{L('也可以在收件箱中批准。拍照、截屏、定位、跑命令、写文件和操作电脑批准以后仍默认关闭。', 'You can also approve in the inbox. Photos, screenshots, location, commands, file writes and computer control stay off after approval.')}</GroupNote>
                </>
              ) : null}

              {usable.length ? (
                <>
                  <GroupLabel>{L('能做的', 'What it can do')}</GroupLabel>
                  <Group>
                    {usable.map((c, i) => (
                      <Row key={c.name} first={i === 0} title={c.label} sub={c.private ? L('隐私：默认关', 'Private: off by default') : c.declared ? undefined : L('现在没报', 'Not reported now')}
                        value={levelLabel(c.level)} tone={c.level === 'off' ? 'muted' : undefined} onPress={() => pick(c)} />
                    ))}
                  </Group>
                </>
              ) : null}

              {node.exec.length ? (
                <>
                  <GroupLabel>{L('直接运行的命令', 'Commands that run without asking')}</GroupLabel>
                  <Group>{node.exec.map((x, i) => <Row key={x} first={i === 0} icon={<Terminal size={20} color={t.ink2} />} title={x} />)}</Group>
                  <GroupNote>{L('在 server.json 的 engine.nodes 中修改。节点自己的配置里也要放行。', 'Edit them under engine.nodes in server.json. The node\'s own config must allow them too.')}</GroupNote>
                </>
              ) : null}

              {node.folders.length ? (
                <>
                  <GroupLabel>{L('给出来的文件夹', 'Shared folders')}</GroupLabel>
                  <Group>{node.folders.map((f, i) => <Row key={f.name} first={i === 0} title={f.name} value={f.write ? L('可读写', 'Read and write') : L('只读', 'Read only')} />)}</Group>
                </>
              ) : null}

              <Group style={{ marginTop: space.xl }}>
                <Row first title={L('移除此设备', 'Remove this device')} danger onPress={remove} />
              </Group>
            </>
          )}
      </ScrollView>
    </Screen>
  );
}

function LevelSheet({ node, c, kind, close, onDone }: { node: string; c: NodeCommand; kind: 'host' | 'phone'; close: () => void; onDone: (n: NodeInfo) => void }) {
  const t = useTheme();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const allowed = LEVELS.filter((lv) => LEVELS.indexOf(lv) <= LEVELS.indexOf(c.maxLevel));
  const explain = c.name === 'system.run'
    ? L('「直接给」：这个节点放行名单里的命令直接运行，其他命令每次先问；有风险的命令每次都问。', '"Allowed": commands on this node\'s allowlist run without asking, others ask first; risky ones always ask.')
    : c.name === 'computer.act' ? L('每个动作都无法撤回，只能「每次先问」。', 'Every action is irreversible, so it can only ask each time.')
      : c.name === 'camera.snap' && kind === 'phone' ? L('手机每次都会弹出取景画面，点了才拍。', 'The phone always shows the camera view and takes the photo only when you tap.')
        : c.private ? L('隐私、无法撤回的操作，默认关闭。', 'Private or irreversible, so it is off by default.') : '';
  const set = (lv: NodeLevel) => {
    if (busy) return;
    setBusy(true);
    setError('');
    nodesApi.allow(node, c.name, lv).then((r) => { onDone(r.node); close(); })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e))).finally(() => setBusy(false));
  };
  return (
    <View style={{ gap: space.md, paddingBottom: space.sm }}>
      <T v="callout" color={t.ink3}>{c.name}</T>
      {explain ? <T v="body" color={t.ink2}>{explain}</T> : null}
      {error ? <T v="callout" color={t.bad}>{error}</T> : null}
      {busy ? <ActivityIndicator color={t.ink3} /> : (
        <View style={{ gap: space.sm }}>
          {allowed.map((lv) => (
            <Btn key={lv} kind={lv === c.level ? 'primary' : 'quiet'} label={lv === c.level ? L(`${levelLabel(lv)}（当前）`, `${levelLabel(lv)} (current)`) : levelLabel(lv)}
              onPress={() => set(lv)} />
          ))}
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  scroll: { paddingTop: 4, paddingBottom: space.xxl },
  code: { borderRadius: 12, padding: space.md },
});
