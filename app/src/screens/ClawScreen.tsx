// 设置 → 我的 claw → 正在用的那台（2026-09-29）：它是什么、怎么连的、用什么模型、有哪些聊天渠道、哪些设备能连它；
// 给另一台设备出配对码、收回某台设备、改地址和令牌、从这台设备上断开。
import React, { useCallback, useState } from 'react';
import { Alert, Platform, ScrollView, Share, View } from 'react-native';
import { useFocusEffect, useNavigation } from '@react-navigation/native';
import * as Clipboard from 'expo-clipboard';
import Svg, { Path, Rect } from 'react-native-svg';
import { KeyRound, Monitor, Plus, Server, ShieldCheck, Smartphone } from '../components/icons';
import { Chip, Dot, Group, GroupLabel, GroupNote, Row, SettingsHeader, Tile } from '../components/settings';
import { useSheet } from '../components/Sheet';
import { Btn, PullRefresh, Screen, T, showError } from '../components/ui';
import { forgetAccountClaw } from '../api/account';
import { getBase } from '../api/base';
import { activeClawId, forgetClaw, listClaws } from '../api/claws';
import { devicesApi, type Device, type PairCode } from '../api/devices';
import { home as friendsHome } from '../api/friends';
import { L } from '../i18n';
import { useStore } from '../store';
import { radius, space, useTheme } from '../theme';

function QrCode({ qr, label }: { qr: { size: number; path: string }; label: string }) {
  // 永远白底黑码：相机扫深色底的反色码常常扫不出来
  return (
    <View style={{ alignSelf: 'center', padding: 8, backgroundColor: '#FFFFFF', borderRadius: radius.md }} accessible accessibilityRole="image" accessibilityLabel={label}>
      <Svg width={220} height={220} viewBox={`0 0 ${qr.size} ${qr.size}`}>
        <Rect width={qr.size} height={qr.size} fill="#FFFFFF" />
        <Path d={qr.path} fill="#000000" />
      </Svg>
    </View>
  );
}

/** 地址看着是哪种连法：100.x / *.ts.net = Tailscale 私网，https 域名 = 公网，其余 = 局域网或本机。 */
function networkOf(base: string): string {
  const host = base.replace(/^https?:\/\//, '').split(/[/:]/)[0];
  if (/^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\./.test(host) || host.endsWith('.ts.net')) return L('Tailscale 私网', 'Tailscale (private)');
  if (base.startsWith('https://')) return L('公网（HTTPS）', 'Internet (HTTPS)');
  return L('局域网 / 本机', 'Local network');
}

const shortModel = (m: string) => m.split('/').pop() || m;

export function ClawScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const sheet = useSheet();
  const { appName, claw, connected, groups, models, connectors, reload, refreshLive } = useStore();
  const [devices, setDevices] = useState<Device[] | null>(null);
  const [finger, setFinger] = useState<string | null>(null);
  const base = getBase() || (Platform.OS === 'web' && typeof window !== 'undefined' ? window.location.origin : '');

  const load = useCallback(() => {
    if (!connected) return;
    devicesApi.list().then((r) => setDevices(r.devices)).catch(() => setDevices(null));
    friendsHome().then((h) => setFinger(h.me?.fingerprint || null)).catch(() => setFinger(null));
    reload('connectors', 'models').catch(() => {});
  }, [connected, reload]);
  useFocusEffect(useCallback(() => { load(); }, [load]));
  const shown = connected ? devices : null;  // 断开了就不显示旧的设备列表

  const channels = connectors?.kind === 'ok' ? (connectors.data.groups.find((g) => g.id === 'channels')?.items ?? []) : [];

  const addDevice = async () => {
    try {
      const p: PairCode = await devicesApi.pairNew(base, L('另一台设备', 'another device'));
      sheet.open({
        title: L('添加一台设备', 'Add a device'),
        content: () => (
          <View style={{ gap: space.md }}>
            <T v="callout" color={t.ink2}>{L('在另一台 iPhone 上用相机扫这个码，或者把链接发过去点开。10 分钟内有效，只能用一次。',
              'Scan this with the camera on the other iPhone, or send it the link. Valid for 10 minutes, once.')}</T>
            <QrCode qr={p.qr} label={L('配对二维码', 'Pairing QR code')} />
            <T v="title" selectable style={{ textAlign: 'center', letterSpacing: 3 }}>{p.code}</T>
            <View style={{ flexDirection: 'row', gap: space.sm }}>
              <Btn flex kind="quiet" label={L('拷贝链接', 'Copy link')} onPress={() => { Clipboard.setStringAsync(p.link).catch(() => {}); }} />
              {Platform.OS === 'web' ? null : <Btn flex kind="quiet" label={L('发给…', 'Send…')} onPress={() => { Share.share({ message: p.link }).catch(() => {}); }} />}
            </View>
          </View>
        ),
      });
    } catch (e) { showError(L('出不了配对码', "Couldn't make a pairing code"), e); }
  };

  const removeDevice = (d: Device) => {
    Alert.alert(L(`收回「${d.label}」？`, `Remove ${d.label}?`), L('它的令牌作废，下次要重新配对才能连。', "Its token stops working; it'll need to pair again."), [
      { text: L('取消', 'Cancel'), style: 'cancel' },
      { text: L('收回', 'Remove'), style: 'destructive', onPress: () => { devicesApi.remove(d.name).then(load).catch((e) => showError(L('没收回', "Couldn't remove it"), e)); } },
    ]);
  };

  const disconnect = () => {
    Alert.alert(L(`从这台设备上断开「${appName}」？`, `Disconnect ${appName} from this device?`),
      L('只删这台设备上的令牌，claw 上的对话、记忆、连接器都不动。以后要连，重新配对就行。', 'Only this device forgets its token; chats, memory and connectors on the claw stay. You can pair again any time.'), [
        { text: L('取消', 'Cancel'), style: 'cancel' },
        {
          text: L('断开', 'Disconnect'), style: 'destructive', onPress: async () => {
            try {
              const list = await listClaws(appName);
              const id = activeClawId(list);
              const was = getBase();
              const next = id ? await forgetClaw(id) : null;
              forgetAccountClaw(was).catch(() => {});
              refreshLive();
              nav.reset({ index: 0, routes: [{ name: next ? 'Tabs' : 'Connect' }] });
            } catch (e) { showError(L('没断开', "Couldn't disconnect"), e); }
          },
        },
      ]);
  };

  const agents = groups.length + 1;
  return (
    <Screen>
      <SettingsHeader title={appName} onBack={() => nav.goBack()} />
      <ScrollView contentContainerStyle={{ paddingBottom: space.xxl, paddingTop: 4 }} refreshControl={<PullRefresh onRefresh={load} />}>
        <Group style={{ padding: 18, gap: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 14 }}>
            <Tile size={56} bg={t.cyanSoft}><Server size={28} color={t.cyan} /></Tile>
            <View style={{ flex: 1, minWidth: 0, gap: 4 }}>
              <T v="title" numberOfLines={1}>{appName}</T>
              <T v="callout" color={t.ink2} numberOfLines={1}>{`${claw.name}${L(' · ', ' · ')}${L(`${agents} 个 agent`, `${agents} agents`)}`}</T>
            </View>
            <Chip label={connected ? L('在线', 'Online') : L('连不上', 'Offline')} tone={connected ? 'good' : 'warn'} />
          </View>
          <T v="callout" color={t.ink2}>{L('对话、记忆、连接器的令牌都存在这台机器上。', 'Chats, memory and connector tokens are kept on this machine.')}</T>
        </Group>

        <GroupLabel>{L('怎么连的', 'How it connects')}</GroupLabel>
        <Group>
          <Row first title={L('地址', 'Address')} value={base.replace(/^https?:\/\//, '')} />
          <Row title={L('网络', 'Network')} value={networkOf(base)} />
          {finger ? <Row title={L('身份指纹', 'Fingerprint')} value={finger} /> : null}
          <Row title={L('地址和令牌', 'Address and token')} onPress={() => nav.navigate('Connect')} />
        </Group>

        {models ? (
          <>
            <GroupLabel>{L('模型', 'Models')}</GroupLabel>
            <Group>
              <Row first title={L('默认', 'Default')} value={shortModel(models.primary)} onPress={() => nav.navigate('Models')} />
              {models.fallbacks[0] ? <Row title={L('备用', 'Fallback')} value={shortModel(models.fallbacks[0])} onPress={() => nav.navigate('Models')} /> : null}
            </Group>
          </>
        ) : null}

        {channels.length ? (
          <>
            <GroupLabel>{L('聊天渠道和通知', 'Channels & notifications')}</GroupLabel>
            <Group>
              {channels.map((c, i) => (
                <Row key={c.id} first={i === 0} title={c.name} sub={c.line} right={<Dot tone={c.status === 'ok' ? 'good' : c.status === 'warn' ? 'warn' : 'off'} />} />
              ))}
            </Group>
          </>
        ) : null}

        {shown ? (
          <>
            <GroupLabel>{L('能连它的设备', 'Devices that can connect')}</GroupLabel>
            <Group>
              {shown.map((d, i) => (
                <Row key={d.name} first={i === 0}
                  icon={d.current ? <Smartphone size={22} color={t.cyan} /> : d.paired ? <Smartphone size={22} color={t.ink3} /> : /web/i.test(d.name) ? <Monitor size={22} color={t.ink3} /> : <KeyRound size={22} color={t.ink3} />}
                  title={d.label} value={d.current ? L('这台', 'This one') : undefined} tone="accent"
                  onPress={d.current ? undefined : () => removeDevice(d)} chevron={false}
                  right={d.current ? undefined : <T v="callout" color={t.bad}>{L('收回', 'Remove')}</T>} />
              ))}
              <Row icon={<Plus size={22} color={t.cyan} />} title={L('添加一台设备', 'Add a device')} accent onPress={addDevice} />
            </Group>
            <GroupNote>{L('新设备扫码就连上，令牌不经过任何聊天。', "A new device connects by scanning a code; no token goes through a chat.")}</GroupNote>
          </>
        ) : null}

        <Group style={{ marginTop: 26 }}>
          <Row first icon={<ShieldCheck size={22} color={t.cyan} />} title={L('安全检查', 'Security check')} onPress={() => nav.navigate('Security')} />
          <Row title={L('从这台设备上断开', 'Disconnect from this device')} danger onPress={disconnect} />
        </Group>
        <GroupNote>{L('断开只删这台设备上的令牌，claw 上的东西都不动。', 'Disconnecting only removes the token on this device; nothing on the claw changes.')}</GroupNote>
      </ScrollView>
    </Screen>
  );
}
