// 扫一扫（1.0.6，expo-camera）：扫配对二维码和好友邀请二维码，认出来交给 navigation.tsx 的 openScanned 去跳（配对只填好连接页，不自动连）。
// 进来的地方：「对话」页「+」两边各一个、连接页配对码旁边的按钮（还没连服务器也能用：新手机先扫另一台设备「添加设备」的码）。
// expo-camera 只在原生端、真要开相机时才加载：网页包里不碰它，网页版只说明一句；缺这个原生模块的旧包显示「需要更新 app」，不会崩。
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AccessibilityInfo, ActivityIndicator, AppState, Linking, Platform, Pressable, StatusBar, StyleSheet, Text, useWindowDimensions, View } from 'react-native';
import { useNavigation, useRoute } from '@react-navigation/native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import * as Haptics from 'expo-haptics';
import type { BarcodeScanningResult, BarcodeSettings } from 'expo-camera';
import { Camera, ScanLine, X } from '../components/icons';
import { Btn } from '../components/ui';
import { L } from '../i18n';
import { openScanned } from '../navigation';
import { space, type } from '../theme';

type CameraModule = typeof import('expo-camera');
/** 从哪儿进来的（只决定提示语；两种码在哪儿扫到都认）：pair = 连接页，friend = 好友那边的「+」。 */
type ScanKind = 'pair' | 'friend';

let cameraMod: CameraModule | null | undefined;
/** 第一次用到时才加载 expo-camera。网页版、没有这个原生模块的旧包 → null。 */
function cameraModule(): CameraModule | null {
  if (cameraMod === undefined) {
    cameraMod = null;
    if (Platform.OS !== 'web') {
      try {
        // eslint-disable-next-line @typescript-eslint/no-require-imports
        cameraMod = require('expo-camera') as CameraModule;
      } catch { /* 旧包：没有 ExpoCamera */ }
    }
  }
  return cameraMod;
}

const QR_ONLY: BarcodeSettings = { barcodeTypes: ['qr'] };
// 整页深色（相机取景），不跟主题走
const INK = '#FFFFFF';
const INK2 = 'rgba(255,255,255,0.78)';
const DIM = 'rgba(0,0,0,0.55)';

export function ScanScreen() {
  const nav = useNavigation<any>();
  const route = useRoute<any>();
  const insets = useSafeAreaInsets();
  const kind = route.params?.kind as ScanKind | undefined;
  const cam = cameraModule();
  return (
    <View style={styles.root}>
      <StatusBar barStyle="light-content" />
      {Platform.OS === 'web' ? (
        <Notice icon={<ScanLine size={30} color={INK} />} title={L('网页版不支持扫码', "Scanning isn't available on the web")}
          body={L('请在手机 app 中使用扫一扫。在网页上，可以直接粘贴配对链接或好友邀请链接。', 'Use Scan in the phone app. On the web, paste the pairing link or invite link instead.')} />
      ) : cam ? (
        <Scanner cam={cam} kind={kind} />
      ) : (
        <Notice icon={<ScanLine size={30} color={INK} />} title={L('扫一扫需要更新 app', 'Scan needs a newer version of the app')}
          body={L('请更新 app 后再试。也可以直接粘贴配对链接或好友邀请链接。', 'Update the app and try again, or paste the pairing link or invite link instead.')} />
      )}
      <View style={[styles.top, { paddingTop: insets.top }]} pointerEvents="box-none">
        <Pressable onPress={() => nav.goBack()} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('关闭', 'Close')} style={styles.close}>
          <X size={24} color={INK} />
        </Pressable>
        <Text style={[type.headline, { color: INK }]} accessibilityRole="header">{L('扫一扫', 'Scan')}</Text>
        <View style={styles.close} />
      </View>
    </View>
  );
}

/** 相机 + 取景框。权限：进来就问（点「扫一扫」就是要用相机）；拒过且不能再问 → 去系统设置，回到 app 时重新看一次。 */
function Scanner({ cam, kind }: { cam: CameraModule; kind?: ScanKind }) {
  const [perm, ask, recheck] = cam.useCameraPermissions({ request: true });
  const [mountErr, setMountErr] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const { width } = useWindowDimensions();
  const box = Math.min(Math.round(width * 0.7), 280);
  // 一个码在镜头里时每秒会报很多次：认出来、正在跳走（done）之后的都不管；
  // 刚提示过认不出的那个码（last）还在镜头里时只让提示多留一会儿，换了别的码才重新认
  const done = useRef(false);
  const last = useRef<{ data: string; note: string } | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  useEffect(() => {
    const sub = AppState.addEventListener('change', (s) => { if (s === 'active') recheck().catch(() => {}); });
    return () => sub.remove();
  }, [recheck]);

  const showNote = useCallback((text: string) => {
    setNote(text);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setNote(''), 2500);
  }, []);
  const onScan = useCallback((r: BarcodeScanningResult) => {
    const data = r.data || '';
    if (done.current) return;
    if (last.current?.data === data) { showNote(last.current.note); return; }
    const got = openScanned(data);
    if (got === 'pair' || got === 'friend') {
      done.current = true;
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
      return;
    }
    const text = got === 'offline'
      ? L('这是好友邀请码。请先连接服务器，再添加好友。', 'This is a friend invite. Connect to your server first, then add the friend.')
      : L('无法识别：不是配对二维码或好友邀请二维码。', "Not recognized: this isn't a pairing QR code or a friend invite.");
    last.current = { data, note: text };
    showNote(text);
    AccessibilityInfo.announceForAccessibility(text);
  }, [showNote]);

  if (!perm) return <View style={styles.center}><ActivityIndicator color={INK} /></View>;
  if (!perm.granted) {
    return perm.canAskAgain ? (
      <Notice icon={<Camera size={30} color={INK} />} title={L('需要相机权限', 'Camera access needed')}
        body={L('扫一扫使用相机读取二维码，画面不会保存或上传。', 'Scan uses the camera to read QR codes. Nothing is saved or uploaded.')}
        action={{ label: L('允许访问', 'Allow access'), onPress: () => { ask().catch(() => {}); } }} />
    ) : (
      <Notice icon={<Camera size={30} color={INK} />} title={L('无相机权限', 'No camera access')}
        body={L('请在系统设置中开启相机权限。', 'Turn on camera access in Settings.')}
        action={{ label: L('打开设置', 'Open Settings'), onPress: () => { Linking.openSettings().catch(() => {}); } }} />
    );
  }
  if (mountErr !== null) {
    return <Notice icon={<Camera size={30} color={INK} />} title={L('无法启动相机', "Couldn't start the camera")} body={mountErr || undefined} />;
  }
  const CameraView = cam.CameraView;
  const hint = kind === 'pair' ? L('对准 claw 的配对二维码', "Point the camera at your claw's pairing QR code")
    : kind === 'friend' ? L('对准好友的邀请二维码', "Point the camera at a friend's invite QR code")
      : L('对准配对二维码或好友邀请二维码', "Point the camera at a pairing QR code or a friend's invite");
  return (
    <View style={StyleSheet.absoluteFill}>
      <CameraView style={StyleSheet.absoluteFill} facing="back" barcodeScannerSettings={QR_ONLY} onBarcodeScanned={onScan}
        onMountError={(e) => setMountErr(e.message || '')} />
      {/* 取景框：四周压暗，中间留一个方框和四个角 */}
      <View style={StyleSheet.absoluteFill} pointerEvents="none">
        <View style={{ flex: 1, backgroundColor: DIM }} />
        <View style={{ flexDirection: 'row', height: box }}>
          <View style={{ flex: 1, backgroundColor: DIM }} />
          <View style={{ width: box, height: box }}>
            <View style={[styles.corner, { top: 0, left: 0, borderTopWidth: 3, borderLeftWidth: 3, borderTopLeftRadius: 14 }]} />
            <View style={[styles.corner, { top: 0, right: 0, borderTopWidth: 3, borderRightWidth: 3, borderTopRightRadius: 14 }]} />
            <View style={[styles.corner, { bottom: 0, left: 0, borderBottomWidth: 3, borderLeftWidth: 3, borderBottomLeftRadius: 14 }]} />
            <View style={[styles.corner, { bottom: 0, right: 0, borderBottomWidth: 3, borderRightWidth: 3, borderBottomRightRadius: 14 }]} />
          </View>
          <View style={{ flex: 1, backgroundColor: DIM }} />
        </View>
        <View style={styles.below}>
          <Text style={[type.callout, { color: INK2, textAlign: 'center' }]}>{hint}</Text>
          {note ? <View style={styles.note}><Text style={[type.callout, { color: INK, textAlign: 'center' }]}>{note}</Text></View> : null}
        </View>
      </View>
    </View>
  );
}

function Notice({ icon, title, body, action }: { icon: React.ReactNode; title: string; body?: string; action?: { label: string; onPress: () => void } }) {
  return (
    <View style={styles.center}>
      <View style={styles.noticeIcon}>{icon}</View>
      <Text style={[type.title, { color: INK, textAlign: 'center' }]}>{title}</Text>
      {body ? <Text style={[type.callout, { color: INK2, textAlign: 'center' }]}>{body}</Text> : null}
      {action ? <View style={{ alignSelf: 'stretch', marginTop: space.md }}><Btn label={action.label} onPress={action.onPress} /></View> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#000000' },
  top: { position: 'absolute', top: 0, left: 0, right: 0, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: space.md },
  close: { width: 44, height: 44, borderRadius: 22, alignItems: 'center', justifyContent: 'center', backgroundColor: 'rgba(0,0,0,0.35)', marginVertical: 4 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: space.xxl, gap: space.sm, maxWidth: 420, width: '100%', alignSelf: 'center' },
  noticeIcon: { width: 64, height: 64, borderRadius: 32, alignItems: 'center', justifyContent: 'center', backgroundColor: 'rgba(255,255,255,0.12)', marginBottom: space.sm },
  corner: { position: 'absolute', width: 30, height: 30, borderColor: INK },
  below: { flex: 1.3, backgroundColor: DIM, alignItems: 'center', paddingTop: space.xl, paddingHorizontal: space.xl, gap: space.md },
  note: { borderRadius: 12, paddingHorizontal: space.lg, paddingVertical: space.sm, backgroundColor: 'rgba(20,23,28,0.92)', maxWidth: 360 },
});
