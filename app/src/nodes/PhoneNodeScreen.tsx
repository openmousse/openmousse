// 这台手机（第 8 批，设置 → 硬件 → 这台手机，route PhoneNode）：把这台手机当成 Agent 的一个节点。
// 三样各一行（定位、拍照、通知），默认都关；点进去先说清楚能做什么，再确认打开（不放一排开关）。打开就是批准，不交收件箱卡。
// 只在 app 打开时可用；切到后台时，Agent 那边收到「手机 app 不在前台」。连着、等活都在 src/nodes/phone.ts，这页只画。
// 没有设计稿：照硬件页的 Group / Row 做的最简版，等设计补稿。
import React, { useCallback, useState, useSyncExternalStore } from 'react';
import { ActivityIndicator, Linking, Platform, ScrollView, StyleSheet, View } from 'react-native';
import { useFocusEffect, useNavigation } from '@react-navigation/native';
import { httpStatus } from '../api/base';
import { needsUpdate, supports } from '../api/version';
import { nodesApi, type NodeLevel } from '../api/nodes';
import { agentName } from '../brand';
import { Bell, Camera, MapPin, Smartphone } from '../components/icons';
import { Dot, Group, GroupLabel, GroupNote, Row, SettingsHeader } from '../components/settings';
import { useSheet } from '../components/Sheet';
import { Btn, Screen, T } from '../components/ui';
import { L } from '../i18n';
import { space, useTheme } from '../theme';
import {
  cameraModule, locationLog, locationModule, onPhoneNode, phoneCaps, phoneNodeState, setPhoneCaps, setPhoneLevel, type Caps, type PhoneNodeState,
} from './phone';

type Kind = keyof Caps;

export function PhoneNodeScreen() {
  const nav = useNavigation<any>();
  const t = useTheme();
  return (
    <Screen>
      <SettingsHeader title={L('这台手机', 'This phone')} onBack={nav.canGoBack() ? () => nav.goBack() : undefined} />
      {Platform.OS === 'web' ? (
        <ScrollView contentContainerStyle={styles.scroll}>
          <Group><Row first icon={<Smartphone size={22} color={t.ink3} />} title={L('仅限手机 app', 'Phone app only')} /></Group>
          <GroupNote>{L('在手机 app 中打开此页，可让 Agent 使用这台手机的定位、相机和通知。', "Open this page in the phone app to let Agents use the phone's location, camera and notifications.")}</GroupNote>
        </ScrollView>
      ) : <Body />}
    </Screen>
  );
}

export const levelLabel = (lv: NodeLevel | undefined) => (lv === 'allow' ? L('直接给', 'Allowed') : lv === 'ask' ? L('每次先问', 'Ask each time') : L('关', 'Off'));

function statusLine(st: PhoneNodeState): { tone: 'good' | 'warn' | 'off'; title: string; sub?: string } {
  switch (st.phase) {
    case 'online': return { tone: 'good', title: L('已连接', 'Connected'), sub: L('app 打开时可用', 'Available while the app is open') };
    case 'connecting': return { tone: 'off', title: L('正在连接…', 'Connecting…') };
    case 'background': return { tone: 'off', title: L('已暂停', 'Paused'), sub: L('app 在后台', 'The app is in the background') };
    case 'serverOff': return { tone: 'warn', title: L('服务器未开启设备节点', 'Device nodes are off on the server'), sub: L('在 server.json 的 engine 中加入 "nodes": {}', 'Add "nodes": {} under engine in server.json') };
    case 'error': return { tone: 'warn', title: L('无法连接', "Can't connect"), sub: st.error || undefined };
    default: return { tone: 'off', title: L('未开启', 'Off'), sub: L('下面三项都关着', 'All three below are off') };
  }
}

function Body() {
  const t = useTheme();
  const sheet = useSheet();
  const st = useSyncExternalStore(onPhoneNode, phoneNodeState);
  const caps = phoneCaps();
  const log = locationLog();
  const hasLoc = !!locationModule();
  const hasCam = !!cameraModule();
  const server = supports('nodes');
  const status = statusLine(st);
  // 服务器报 nodes 不看 server.json 开没开：进页先问一声，没开（404）就说清楚、三行灰掉（审查：原来先弹系统权限框再报错）
  const [off, setOff] = useState(false);
  useFocusEffect(useCallback(() => {
    if (!server) return;
    nodesApi.list().then(() => setOff(false)).catch((e: unknown) => { if (httpStatus(e) === 404) setOff(true); });
  }, [server]));

  const open = (kind: Kind) => {
    const title = kind === 'location' ? L('定位', 'Location') : kind === 'camera' ? L('拍照', 'Photos') : L('通知', 'Notifications');
    sheet.open({ title, content: (close) => <CapSheet kind={kind} level={st.levels[kind === 'location' ? 'location.get' : kind === 'camera' ? 'camera.snap' : 'system.notify']} close={close} /> });
  };

  return (
    <ScrollView contentContainerStyle={styles.scroll}>
      {!server ? (
        <>
          <Group><Row first icon={<Smartphone size={22} color={t.ink3} />} title={L('需要更新服务器', 'Server update needed')} /></Group>
          <GroupNote>{needsUpdate('这台服务器还不支持设备节点。', "This server doesn't support device nodes yet.")}</GroupNote>
        </>
      ) : off ? (
        <>
          <Group><Row first icon={<Smartphone size={22} color={t.ink3} />} title={L('服务器未开启设备节点', 'Device nodes are off on the server')} /></Group>
          <GroupNote>{L('在 server.json 的 engine 中加入 "nodes": {}。', 'Add "nodes": {} under engine in server.json.')}</GroupNote>
        </>
      ) : (
        <Group>
          <Row first icon={<Smartphone size={22} color={t.cyan} />} title={status.title} sub={status.sub} right={<Dot tone={status.tone} />} />
        </Group>
      )}

      <GroupLabel>{L(`${agentName()} 能用的`, `What ${agentName()} can use`)}</GroupLabel>
      <Group>
        <Row first icon={<MapPin size={22} color={t.cyan} />} title={L('定位', 'Location')}
          sub={hasLoc ? undefined : L('需要新版 app', 'Needs a newer version of the app')} disabled={!hasLoc || !server || off}
          value={caps.location && hasLoc ? levelLabel(st.levels['location.get'] ?? 'allow') : L('关', 'Off')} onPress={() => open('location')} />
        <Row icon={<Camera size={22} color={t.cyan} />} title={L('拍照', 'Photos')}
          sub={hasCam ? undefined : L('需要新版 app', 'Needs a newer version of the app')} disabled={!hasCam || !server || off}
          value={caps.camera && hasCam ? L('开（每次确认）', 'On (confirm each)') : L('关', 'Off')} onPress={() => open('camera')} />
        <Row icon={<Bell size={22} color={t.cyan} />} title={L('通知', 'Notifications')} disabled={!server || off}
          value={caps.notify ? L('开', 'On') : L('关', 'Off')} onPress={() => open('notify')} />
      </Group>
      <GroupNote>{L(`只在 app 打开时可用。切到后台时，${agentName()} 会得知手机不在前台。`,
        `Only while the app is open. When it's in the background, ${agentName()} is told the phone isn't available.`)}</GroupNote>

      {caps.location || log.length ? (
        <>
          <GroupLabel>{L('最近的定位记录', 'Recent location reads')}</GroupLabel>
          <Group>
            {log.length ? log.slice(0, 10).map((r, i) => (
              <Row key={r.at} first={i === 0} title={new Date(r.at).toLocaleString(L('zh-CN', 'en'), { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                value={r.accuracy != null ? L(`精度约 ${Math.round(r.accuracy)} 米`, `±${Math.round(r.accuracy)} m`) : undefined} />
            )) : <Row first title={L('还没有读取过', 'No reads yet')} />}
          </Group>
        </>
      ) : null}
    </ScrollView>
  );
}

/** 一样的说明 + 确认：先说能做什么，再打开；打开以后能改档（定位）或关掉。 */
function CapSheet({ kind, level, close }: { kind: Kind; level: NodeLevel | undefined; close: () => void }) {
  const t = useTheme();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const on = phoneCaps()[kind];
  const name = agentName();
  const text = kind === 'location'
    ? L(`${name} 在你让它查的时候读取这台手机的当前位置，例如附近的地点、到某处的距离。只在 app 打开时读取，不在后台定位。每次读取都记录在本页。`,
      `${name} reads this phone's current location when you ask for something that needs it, like nearby places or the distance to somewhere. Only while the app is open, never in the background. Every read is listed on this page.`)
    : kind === 'camera'
      ? L(`${name} 需要看一眼时，这台手机弹出取景画面，你点「拍照」才会拍。照片只发给你自己的服务器。只在 app 打开时可用。`,
        `When ${name} needs to see something, this phone shows the camera view and takes the photo only when you tap Take photo. Photos go only to your own server. Only while the app is open.`)
      : L(`${name} 可以给这台手机发通知，提醒你它查到或做完的事。通知经推送发送，app 在后台也能收到。`,
        `${name} can send this phone a notification about something it found or finished. Notifications arrive by push, even with the app in the background.`);

  const run = async (fn: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      await fn();
      close();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const enable = (lv?: NodeLevel) => run(async () => {
    // 先确认服务器收得下（开了设备节点），再要系统权限：没开的话不弹权限框
    await nodesApi.list().catch((e: unknown) => {
      throw httpStatus(e) === 404 ? new Error(L('服务器未开启设备节点：在 server.json 的 engine 中加入 "nodes": {}。', 'Device nodes are off on the server: add "nodes": {} under engine in server.json.')) : e;
    });
    if (kind === 'location') {
      const p = await locationModule()!.requestForegroundPermissionsAsync();
      if (p.status !== 'granted') {
        if (!p.canAskAgain) Linking.openSettings().catch(() => {});
        throw new Error(L('没有定位权限：在系统设置中允许「使用 App 期间」访问位置。', 'No location permission: allow location "While Using the App" in Settings.'));
      }
      await setPhoneCaps({ location: true }, { 'location.get': lv ?? 'allow' });
    } else if (kind === 'camera') {
      const p = await cameraModule()!.Camera.requestCameraPermissionsAsync();
      if (!p.granted) {
        if (!p.canAskAgain) Linking.openSettings().catch(() => {});
        throw new Error(L('没有相机权限：在系统设置中开启。', 'No camera permission: turn it on in Settings.'));
      }
      await setPhoneCaps({ camera: true }, { 'camera.snap': 'allow' });
    } else {
      await setPhoneCaps({ notify: true }, { 'system.notify': 'allow' });
    }
  });
  const disable = () => run(() => setPhoneCaps({ [kind]: false } as Partial<Caps>));

  return (
    <View style={{ gap: space.md, paddingBottom: space.sm }}>
      <T v="body" color={t.ink2}>{text}</T>
      {kind === 'location' ? (
        <T v="callout" color={t.ink3}>{L('「直接给」：读取时不再确认。「每次先问」：每次在收件箱中确认。', '"Allow": no confirmation for each read. "Ask each time": confirm each read in the inbox.')}</T>
      ) : null}
      {error ? <T v="callout" color={t.bad}>{error}</T> : null}
      {busy ? <ActivityIndicator color={t.ink3} /> : (
        <View style={{ gap: space.sm }}>
          {kind === 'location' ? (
            <>
              <Btn label={on && level === 'allow' ? L('直接给（当前）', 'Allow (current)') : L('直接给', 'Allow')}
                onPress={() => { (on ? run(() => setPhoneLevel('location.get', 'allow')) : enable('allow')).catch(() => {}); }} />
              <Btn kind="quiet" label={on && level === 'ask' ? L('每次先问（当前）', 'Ask each time (current)') : L('每次先问', 'Ask each time')}
                onPress={() => { (on ? run(() => setPhoneLevel('location.get', 'ask')) : enable('ask')).catch(() => {}); }} />
            </>
          ) : !on ? <Btn label={L('打开', 'Turn on')} onPress={() => { enable().catch(() => {}); }} /> : null}
          {on ? <Btn kind="danger" label={L('关闭', 'Turn off')} onPress={() => { disable().catch(() => {}); }} /> : null}
          <Btn kind="quiet" label={L('取消', 'Cancel')} onPress={close} />
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  scroll: { paddingTop: 4, paddingBottom: space.xxl },
});
