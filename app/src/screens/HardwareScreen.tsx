// 硬件（1.0.6，设置 → 应用 → 硬件）：手机经蓝牙连 Flipper Zero。
// 蓝牙状态和权限 → 搜索附近的 Flipper → 连接 / 忽略 → 设备卡（名字、固件、电量、蜂鸣）→ 红外：遥控器 → 按钮 → 点一下发送一次。
// 网页版和 1.0.6 以前的安装包没有蓝牙模块，只显示一行说明。连接和动作都在 src/hardware/flipper.ts，这页只画。
// 第 8 批：最底下一组「设备和节点」（这台手机、配对的电脑和手机，见 src/nodes/）。
import React, { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { ActivityIndicator, Alert, AppState, Linking, Platform, Pressable, ScrollView, StyleSheet, View } from 'react-native';
import { useFocusEffect, useNavigation } from '@react-navigation/native';
import { BatteryMedium, Bell, Bluetooth, BluetoothOff, CircleAlert, CircleCheck, Cpu, RefreshCw, Tv } from '../components/icons';
import { Dot, Group, GroupLabel, GroupNote, Row, SettingsHeader, Tile } from '../components/settings';
import { Disclosure, Screen, T } from '../components/ui';
import { bleAvailable, bleEnable, blePermission, bleStart, bleState, onBleState, scanFlippers, type BtState, type Found, type Permission } from '../hardware/ble';
import {
  connect, disconnect, explain, flipperState, forget, irButtons, irRemotes, irSend, onFlipper, ping, remoteName, status, type FlipperStatus,
} from '../hardware/flipper';
import { L } from '../i18n';
import { NodeEntries } from '../nodes/NodeEntries';
import { radius, space, useTheme } from '../theme';

export function HardwareScreen() {
  const nav = useNavigation<any>();
  const [available] = useState(bleAvailable);
  return (
    <Screen>
      <SettingsHeader title={L('硬件', 'Hardware')} onBack={nav.canGoBack() ? () => nav.goBack() : undefined} />
      {available ? <Body /> : <Unavailable />}
    </Screen>
  );
}

/** 网页版 / 没有蓝牙模块的旧安装包。 */
function Unavailable() {
  const t = useTheme();
  return (
    <ScrollView contentContainerStyle={styles.scroll}>
      <Group>
        <Row first icon={<BluetoothOff size={22} color={t.ink3} />} title={L('需要手机 app 1.0.6 或更高版本', 'Needs the phone app 1.0.6 or later')} />
      </Group>
      <GroupNote>{Platform.OS === 'web'
        ? L('网页版无法使用蓝牙。在手机 app 中打开此页，可通过蓝牙连接 Flipper Zero、查看状态并发送已保存的红外按钮。',
          "The web version can't use Bluetooth. Open this page in the phone app to connect a Flipper Zero over Bluetooth, see its status and send saved infrared buttons.")
        : L('当前安装的版本不含蓝牙模块。更新到 1.0.6 或更高版本后，可在此通过蓝牙连接 Flipper Zero。',
          'This installed version has no Bluetooth module. After updating to 1.0.6 or later you can connect a Flipper Zero over Bluetooth here.')}</GroupNote>
      <NodeEntries />
    </ScrollView>
  );
}

function Body() {
  const t = useTheme();
  const fl = useSyncExternalStore(onFlipper, flipperState);
  const [bt, setBt] = useState<BtState | null>(null);
  const [perm, setPerm] = useState<Permission | null>(Platform.OS === 'android' ? null : 'granted');
  const [btError, setBtError] = useState('');

  const check = useCallback(() => {
    bleStart().then(() => bleState()).then((s) => { setBt(s); setBtError(''); }).catch((e: unknown) => setBtError(explain(e).message));
    blePermission(false).then(setPerm).catch(() => {});
  }, []);
  // 进页面时、从系统设置回来时，重新看蓝牙开没开、权限给没给
  useFocusEffect(useCallback(() => {
    check();
    const off = onBleState(setBt);
    const sub = AppState.addEventListener('change', (s) => { if (s === 'active') check(); });
    return () => { off(); sub.remove(); };
  }, [check]));
  const ready = bt === 'on' && perm === 'granted';

  // 记住的那台：进页面、蓝牙就绪后自动连一次（失败的原因在设备卡下面）
  const autoTried = useRef(false);
  useEffect(() => {
    if (!ready || autoTried.current || !fl.device || fl.phase !== 'idle') return;
    autoTried.current = true;
    connect().catch(() => {});
  }, [ready, fl.device, fl.phase]);

  // —— 搜索 ——
  const [scan, setScan] = useState<{ on: boolean; done: boolean; found: Found[] }>({ on: false, done: false, found: [] });
  const stopScan = useRef<(() => void) | null>(null);
  const endScan = useCallback(() => {
    stopScan.current?.();
    stopScan.current = null;
  }, []);
  useFocusEffect(useCallback(() => endScan, [endScan]));  // 离开页面就停止搜索

  const search = async () => {
    if (Platform.OS === 'android') {
      const p = await blePermission(true).catch((): Permission => 'denied');
      setPerm(p);
      if (p !== 'granted') return;
    }
    endScan();
    setScan({ on: true, done: false, found: [] });
    try {
      stopScan.current = await scanFlippers(
        (d) => setScan((s) => ({ ...s, found: s.found.some((x) => x.id === d.id) ? s.found.map((x) => (x.id === d.id ? d : x)) : [...s.found, d] })),
        () => setScan((s) => ({ ...s, on: false, done: true })),
      );
    } catch (e) {
      setScan({ on: false, done: true, found: [] });
      setBtError(explain(e).message);
    }
  };

  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const pick = (d: Found) => {
    endScan();
    setScan((s) => ({ ...s, on: false }));
    setNote(null);
    connect({ id: d.id, name: d.name }).catch(() => {});
  };

  // —— 设备卡 ——
  const readyId = fl.phase === 'ready' ? fl.device?.id ?? null : null;
  const [info, setInfo] = useState<{ id: string; s: FlipperStatus } | null>(null);
  useEffect(() => {
    if (!readyId) return;
    let live = true;
    status().then((s) => { if (live) setInfo({ id: readyId, s }); }).catch((e: unknown) => { if (live) setNote({ ok: false, text: explain(e).message }); });
    return () => { live = false; };
  }, [readyId]);
  const shown = info && info.id === readyId ? info.s : null;

  const [beeping, setBeeping] = useState(false);
  const beep = () => {
    if (beeping) return;
    setBeeping(true);
    setNote(null);
    ping().then(() => setNote({ ok: true, text: L('已发送提示', 'Alert sent') }))
      .catch((e: unknown) => setNote({ ok: false, text: explain(e).message }))
      .finally(() => setBeeping(false));
  };

  const forgetIt = () => {
    Alert.alert(L('忽略此 Flipper？', 'Forget this Flipper?'),
      L('将断开连接并从此 app 中移除。手机蓝牙设置中的配对记录需在系统设置中删除。',
        "This disconnects it and removes it from this app. The pairing in the phone's Bluetooth settings stays until you remove it there."), [
        { text: L('取消', 'Cancel'), style: 'cancel' },
        { text: L('忽略', 'Forget'), style: 'destructive', onPress: () => { setNote(null); forget().catch(() => {}); } },
      ]);
  };

  const busy = fl.phase === 'connecting' || fl.phase === 'securing';
  const line = note ?? (fl.phase === 'error' && fl.error ? { ok: false, text: fl.error } : null);
  const phaseLine = fl.phase === 'ready' ? L('已连接', 'Connected')
    : fl.phase === 'connecting' ? L('正在连接…', 'Connecting…')
      : fl.phase === 'securing' ? L('正在建立加密连接…', 'Securing the connection…')
        : L('未连接', 'Not connected');
  const battery = shown?.battery != null
    ? `${shown.battery}%${shown.charging ? L('，充电中', ' · charging') : shown.chargeState === 'charged' ? L('，已充满', ' · charged') : ''}` : '';
  const firmware = shown?.firmware ? `${shown.firmware}${shown.fork && shown.fork !== 'Official' ? ` · ${shown.fork}` : ''}` : '';

  return (
    <ScrollView contentContainerStyle={styles.scroll}>
      <BluetoothStatus bt={bt} perm={perm} error={btError} onPerm={setPerm} />

      {fl.device ? (
        <>
          <GroupLabel>{L('我的 Flipper', 'My Flipper')}</GroupLabel>
          <Group>
            <Row first icon={<Tile size={36} bg={t.cyanSoft}><Bluetooth size={20} color={t.cyan} /></Tile>} title={fl.device.name} sub={phaseLine}
              right={busy ? <ActivityIndicator color={t.ink3} /> : <Dot tone={fl.phase === 'ready' ? 'good' : fl.phase === 'error' ? 'warn' : 'off'} />} />
            {firmware ? <Row icon={<Cpu size={22} color={t.cyan} />} title={L('固件', 'Firmware')} value={firmware} /> : null}
            {battery ? <Row icon={<BatteryMedium size={22} color={t.cyan} />} title={L('电量', 'Battery')} value={battery} /> : null}
            {fl.phase === 'ready' ? (
              <Row icon={<Bell size={22} color={t.cyan} />} title={L('蜂鸣', 'Beep')} sub={L('振动、闪光并发出提示音', 'Vibrates, flashes and beeps')}
                chevron={false} onPress={beep} right={beeping ? <ActivityIndicator color={t.ink3} /> : undefined} />
            ) : null}
            {busy ? <Row title={L('取消', 'Cancel')} accent onPress={() => { disconnect().catch(() => {}); }} />
              : fl.phase === 'ready' ? <Row title={L('断开连接', 'Disconnect')} accent onPress={() => { setNote(null); disconnect().catch(() => {}); }} />
                : <Row title={L('连接', 'Connect')} accent disabled={!ready} onPress={() => { setNote(null); connect().catch(() => {}); }} />}
            <Row title={L('忽略此设备', 'Forget this device')} danger onPress={forgetIt} />
          </Group>
          {fl.phase === 'securing' ? (
            <GroupNote>{L('如果 Flipper 屏幕上显示 6 位配对码，请在手机弹出的窗口中输入。', 'If the Flipper shows a 6-digit code, enter it in the prompt on the phone.')}</GroupNote>
          ) : null}
          {line ? <Result ok={line.ok} text={line.text} /> : null}
        </>
      ) : null}

      {ready && fl.phase !== 'ready' && !busy ? (
        <>
          <GroupLabel>{L('附近的 Flipper', 'Nearby')}</GroupLabel>
          <Group>
            <Row first icon={<Bluetooth size={22} color={t.cyan} />} title={scan.on ? L('正在搜索…', 'Searching…') : L('搜索', 'Search')} accent
              onPress={scan.on ? undefined : () => { search().catch(() => {}); }} right={scan.on ? <ActivityIndicator color={t.ink3} /> : undefined} />
            {scan.found.map((d) => (
              <Row key={d.id} icon={<Tile size={36}><Bluetooth size={18} color={t.ink2} /></Tile>} title={d.name} sub={signal(d.rssi)} onPress={() => pick(d)}
                label={L(`连接 ${d.name}`, `Connect ${d.name}`)} />
            ))}
            {scan.done && !scan.on && !scan.found.length ? <Row title={L('未找到 Flipper', 'No Flipper found')} /> : null}
          </Group>
          <GroupNote>{L('在 Flipper 的「设置 → 蓝牙」中打开蓝牙。首次连接时，Flipper 屏幕上会显示 6 位配对码，在手机弹出的窗口中输入。Flipper 一次只能连接一台手机，请先断开官方 Flipper app。',
            "Turn on Bluetooth in the Flipper's Settings → Bluetooth. On first connect the Flipper shows a 6-digit code; enter it in the prompt on the phone. A Flipper connects to one phone at a time, so disconnect the official Flipper app first.")}</GroupNote>
        </>
      ) : null}

      {fl.phase === 'ready' && fl.device ? <Infrared key={fl.device.id} /> : null}
      <NodeEntries />
    </ScrollView>
  );
}

function signal(rssi: number): string {
  if (rssi >= -60) return L('信号强', 'Strong signal');
  if (rssi >= -80) return L('信号中等', 'Fair signal');
  return L('信号弱', 'Weak signal');
}

/** 蓝牙没就绪时的一行：关着、没权限、不支持、还在检查。就绪了不画。 */
function BluetoothStatus({ bt, perm, error, onPerm }: { bt: BtState | null; perm: Permission | null; error: string; onPerm: (p: Permission) => void }) {
  const t = useTheme();
  let title = '';
  let sub = '';
  let action: { label: string; run: () => void } | null = null;
  if (error) {
    title = L('无法使用蓝牙', "Bluetooth can't be used");
    sub = error;
  } else if (perm === 'denied') {
    title = L('需要蓝牙权限', 'Bluetooth permission needed');
    sub = L('用于搜索和连接附近的 Flipper', 'Used to find and connect to a nearby Flipper');
    action = { label: L('允许', 'Allow'), run: () => { blePermission(true).then(onPerm).catch(() => {}); } };
  } else if (perm === 'blocked' || bt === 'unauthorized') {
    title = L('需要蓝牙权限', 'Bluetooth permission needed');
    sub = L('请在系统设置中允许此 app 使用蓝牙', 'Allow Bluetooth for this app in Settings');
    action = { label: L('设置', 'Settings'), run: () => { Linking.openSettings().catch(() => {}); } };
  } else if (bt === 'off' || bt === 'turning_off') {
    title = L('蓝牙已关闭', 'Bluetooth is off');
    if (Platform.OS === 'android') action = { label: L('打开', 'Turn on'), run: () => { bleEnable().catch(() => {}); } };
    else sub = L('请在控制中心或设置中打开蓝牙', 'Turn it on in Control Center or Settings');
  } else if (bt === 'unsupported') {
    title = L('此手机不支持低功耗蓝牙', "This phone doesn't support Bluetooth LE");
  } else if (bt !== 'on' || perm === null) {
    title = L('正在检查蓝牙…', 'Checking Bluetooth…');
  } else {
    return null;
  }
  return (
    <Group style={{ marginTop: space.sm }}>
      <Row first icon={<BluetoothOff size={22} color={t.warn} />} title={title} sub={sub || undefined}
        right={action ? <SmallButton label={action.label} onPress={action.run} /> : undefined} />
    </Group>
  );
}

function SmallButton({ label, onPress }: { label: string; onPress: () => void }) {
  const t = useTheme();
  return (
    <Pressable onPress={onPress} accessibilityRole="button" hitSlop={6}
      style={({ pressed }) => [styles.small, { backgroundColor: t.cyanSoft, opacity: pressed ? 0.6 : 1 }]}>
      <T v="callout" color={t.cyan} style={{ fontWeight: '600' }}>{label}</T>
    </Pressable>
  );
}

/** 结果的一行：图标 + 文字（成功 / 失败）。 */
function Result({ ok, text }: { ok: boolean; text: string }) {
  const t = useTheme();
  const color = ok ? t.good : t.bad;
  return (
    <View style={styles.result} accessibilityLiveRegion="polite">
      {ok ? <CircleCheck size={15} color={color} /> : <CircleAlert size={15} color={color} />}
      <T v="caption" color={color} style={{ flex: 1, lineHeight: 18, fontWeight: '400' }}>{text}</T>
    </View>
  );
}

interface Remote { file: string; name: string; size: number }

/** 红外：/ext/infrared 里的遥控器，点开是按钮，点按钮发一次。 */
function Infrared() {
  const t = useTheme();
  const [remotes, setRemotes] = useState<Remote[] | null>(null);
  const [error, setError] = useState('');
  const [nonce, setNonce] = useState(0);
  const [open, setOpen] = useState<string | null>(null);
  const [buttons, setButtons] = useState<Record<string, string[] | { error: string }>>({});
  const [sending, setSending] = useState('');
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    let live = true;
    irRemotes().then((r) => { if (live) { setRemotes(r); setError(''); } }).catch((e: unknown) => { if (live) setError(explain(e).message); });
    return () => { live = false; };
  }, [nonce]);

  const reload = () => {
    setRemotes(null);
    setError('');
    setButtons({});
    setOpen(null);
    setNonce((n) => n + 1);
  };

  const toggle = (file: string) => {
    const next = open === file ? null : file;
    setOpen(next);
    if (next && !buttons[file]) {
      irButtons({ file })
        .then((r) => setButtons((b) => ({ ...b, [file]: r.remotes[0]?.buttons ?? [] })))
        .catch((e: unknown) => setButtons((b) => ({ ...b, [file]: { error: explain(e).message } })));
    }
  };

  const send = (file: string, name: string) => {
    if (sending) return;
    setSending(`${file}#${name}`);
    setResult(null);
    irSend({ file, button: name })
      .then(() => setResult({ ok: true, text: L(`已发送「${name}」（${remoteName(file)}）`, `Sent "${name}" (${remoteName(file)})`) }))
      .catch((e: unknown) => setResult({ ok: false, text: explain(e).message }))
      .finally(() => setSending(''));
  };

  return (
    <>
      <GroupLabel right={(
        <Pressable onPress={reload} accessibilityRole="button" accessibilityLabel={L('刷新', 'Refresh')} hitSlop={10}>
          <RefreshCw size={16} color={t.ink3} />
        </Pressable>
      )}>{L('红外遥控器', 'Infrared remotes')}</GroupLabel>
      <Group>
        {error ? <Row first title={L('无法加载', "Couldn't load")} sub={error} />
          : remotes === null ? <Row first title={L('正在加载…', 'Loading…')} right={<ActivityIndicator color={t.ink3} />} />
            : !remotes.length ? <Row first title={L('没有遥控器', 'No remotes')} sub={L('/ext/infrared 中没有 .ir 文件', 'No .ir files in /ext/infrared')} />
              : remotes.map((r, i) => {
                const b = buttons[r.file];
                const isOpen = open === r.file;
                return (
                  <View key={r.file}>
                    <Row first={i === 0} icon={<Tv size={22} color={t.cyan} />} title={r.name} chevron={false} onPress={() => toggle(r.file)}
                      sub={Array.isArray(b) ? L(`${b.length} 个按钮`, `${b.length} ${b.length === 1 ? 'button' : 'buttons'}`) : undefined}
                      right={<Disclosure open={isOpen} />} />
                    {isOpen ? (
                      <View style={styles.keys}>
                        {b === undefined ? <ActivityIndicator color={t.ink3} />
                          : !Array.isArray(b) ? <T v="callout" color={t.bad}>{b.error}</T>
                            : !b.length ? <T v="callout" color={t.ink2}>{L('此遥控器没有按钮', 'This remote has no buttons')}</T>
                              : b.map((name, j) => (
                                <IrKey key={`${j}:${name}`} label={name} busy={sending === `${r.file}#${name}`} disabled={!!sending} onPress={() => send(r.file, name)} />
                              ))}
                      </View>
                    ) : null}
                  </View>
                );
              })}
      </Group>
      {result ? <Result ok={result.ok} text={result.text} />
        : <GroupNote>{L('点击按钮，Flipper 发送一次该红外信号。', 'Tap a button and the Flipper sends that infrared signal once.')}</GroupNote>}
    </>
  );
}

function IrKey({ label, busy, disabled, onPress }: { label: string; busy: boolean; disabled: boolean; onPress: () => void }) {
  const t = useTheme();
  return (
    <Pressable onPress={disabled ? undefined : onPress} accessibilityRole="button" accessibilityLabel={L(`发送 ${label}`, `Send ${label}`)}
      accessibilityState={{ disabled, busy }}
      style={({ pressed }) => [styles.key, { backgroundColor: t.surface2, opacity: pressed ? 0.6 : disabled && !busy ? 0.5 : 1 }]}>
      {busy ? <ActivityIndicator size="small" color={t.ink2} /> : null}
      <T v="callout" numberOfLines={1}>{label}</T>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  scroll: { paddingTop: 4, paddingBottom: space.xxl },
  small: { height: 32, borderRadius: 16, paddingHorizontal: 14, alignItems: 'center', justifyContent: 'center' },
  result: { flexDirection: 'row', alignItems: 'flex-start', gap: 6, marginTop: 8, marginHorizontal: space.lg + 16 },
  keys: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, paddingLeft: 60, paddingRight: 16, paddingBottom: 14 },
  key: { flexDirection: 'row', alignItems: 'center', gap: 6, minHeight: 36, maxWidth: '100%', borderRadius: radius.pill, paddingHorizontal: 14, paddingVertical: 7 },
});
