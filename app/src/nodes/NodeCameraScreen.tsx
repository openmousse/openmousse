// 拍照确认（第 8 批，route NodeCamera，全屏）：Agent 经 nodes 工具要这台手机拍一张时弹出来，看得到取景，主人点了「拍照」才拍。
// 关掉、点「不拍」、切到后台、服务器那边不等了：都不拍（src/nodes/phone.ts 回给服务器 CAMERA_DECLINED / 不在前台）。
// 照片缩到 1920×1080 左右再发（pictureSize），只发给主人自己的服务器。expo-camera 只在原生端用到时才加载（同 ScanScreen）。
import React, { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { ActivityIndicator, Linking, Platform, Pressable, StatusBar, StyleSheet, Text, View } from 'react-native';
import { useNavigation, useRoute } from '@react-navigation/native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { agentName } from '../brand';
import { Camera, X } from '../components/icons';
import { Btn } from '../components/ui';
import { L } from '../i18n';
import { space, type } from '../theme';
import { cameraModule, declined, onPhoneNode, photoRequest } from './phone';

type CameraModule = typeof import('expo-camera');
const INK = '#FFFFFF';
const INK2 = 'rgba(255,255,255,0.78)';
// 想要的照片大小（iOS 的预设名、安卓的宽 x 高）：够看清，base64 以后不到 1 MB 左右
const SIZES = ['1920x1080', 'High', '1280x720', 'Medium'];

export function NodeCameraScreen() {
  const nav = useNavigation<any>();
  const route = useRoute<any>();
  const insets = useSafeAreaInsets();
  const id = String(route.params?.id ?? '');
  const req = useSyncExternalStore(onPhoneNode, () => photoRequest(id));
  const cam = cameraModule();
  const live = useRef(true);

  // 这件活定下来了（拍了、没拍、服务器不等了）：页面自己关上
  useEffect(() => {
    if (!req && live.current) {
      live.current = false;
      if (nav.canGoBack()) nav.goBack();
    }
  }, [req, nav]);
  // 离开这一页还没拍：算没拍
  useEffect(() => () => { photoRequest(id)?.reject(declined()); }, [id]);

  const cancel = () => { photoRequest(id)?.reject(declined()); };
  return (
    <View style={styles.root}>
      <StatusBar barStyle="light-content" />
      {Platform.OS !== 'web' && cam && req ? <Viewfinder cam={cam} id={id} facing={req.facing} /> : (
        <View style={styles.center}>
          <Camera size={30} color={INK} />
          <Text style={[type.headline, styles.centerText]}>{cam ? L('这张照片已经不用拍了', 'This photo is no longer needed') : L('拍照需要更新 app', 'Taking photos needs a newer version of the app')}</Text>
        </View>
      )}
      <View style={[styles.top, { paddingTop: insets.top }]} pointerEvents="box-none">
        <Pressable onPress={cancel} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('不拍', "Don't take it")} style={styles.close}>
          <X size={24} color={INK} />
        </Pressable>
        <Text style={[type.headline, { color: INK, flex: 1, textAlign: 'center' }]} numberOfLines={1} accessibilityRole="header">
          {L(`${agentName()} 想拍一张照片`, `${agentName()} wants to take a photo`)}
        </Text>
        <View style={styles.close} />
      </View>
    </View>
  );
}

function Viewfinder({ cam, id, facing }: { cam: CameraModule; id: string; facing: 'back' | 'front' }) {
  const insets = useSafeAreaInsets();
  const [perm, ask] = cam.useCameraPermissions({ request: true });
  const ref = useRef<InstanceType<CameraModule['CameraView']> | null>(null);
  const [size, setSize] = useState<string | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const onReady = () => {
    ref.current?.getAvailablePictureSizesAsync().then((all) => {
      const pick = SIZES.find((s) => all.includes(s));
      if (pick) setSize(pick);
    }).catch(() => {});
  };

  const take = async () => {
    const req = photoRequest(id);
    if (busy || !req || !ref.current) return;
    setBusy(true);
    setError('');
    try {
      const pic = await ref.current.takePictureAsync({ quality: 0.7, base64: true, exif: false, shutterSound: true });
      if (!pic?.base64) throw new Error(L('相机没给出照片', "The camera didn't return a photo"));
      photoRequest(id)?.resolve({ format: 'jpeg', base64: pic.base64, width: pic.width, height: pic.height });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  if (!perm) return <View style={styles.center}><ActivityIndicator color={INK} /></View>;
  if (!perm.granted) {
    return (
      <View style={styles.center}>
        <Camera size={30} color={INK} />
        <Text style={[type.headline, styles.centerText]}>{L('需要相机权限', 'Camera access needed')}</Text>
        <Text style={[type.callout, styles.centerSub]}>{perm.canAskAgain ? L('允许后才能拍这张照片。', 'Allow it to take this photo.') : L('请在系统设置中开启相机权限。', 'Turn on camera access in Settings.')}</Text>
        <Btn label={perm.canAskAgain ? L('允许访问', 'Allow access') : L('打开设置', 'Open Settings')}
          onPress={() => { (perm.canAskAgain ? ask() : Linking.openSettings()).catch(() => {}); }} />
      </View>
    );
  }
  const CameraView = cam.CameraView;
  return (
    <View style={StyleSheet.absoluteFill}>
      <CameraView ref={ref} style={StyleSheet.absoluteFill} facing={facing} pictureSize={size} onCameraReady={onReady}
        onMountError={(e) => setError(e.message || L('无法启动相机', "Couldn't start the camera"))} />
      <View style={[styles.bottom, { paddingBottom: insets.bottom + space.lg }]}>
        <Text style={[type.callout, styles.note]}>{error || L('照片只发给你自己的服务器。点「拍照」才会拍。', 'The photo goes only to your own server. Nothing is taken until you tap Take photo.')}</Text>
        <Pressable onPress={() => { take().catch(() => {}); }} disabled={busy} accessibilityRole="button" accessibilityLabel={L('拍照', 'Take photo')}
          style={({ pressed }) => [styles.shutter, { opacity: pressed || busy ? 0.6 : 1 }]}>
          {busy ? <ActivityIndicator color="#000" /> : <View style={styles.shutterInner} />}
        </Pressable>
        <Text style={[type.caption, { color: INK2 }]}>{L('拍照', 'Take photo')}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#000' },
  top: { position: 'absolute', top: 0, left: 0, right: 0, flexDirection: 'row', alignItems: 'center', paddingHorizontal: space.md, paddingBottom: space.sm, backgroundColor: 'rgba(0,0,0,0.35)' },
  close: { width: 44, height: 44, alignItems: 'center', justifyContent: 'center' },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: space.md, paddingHorizontal: space.xl },
  centerText: { color: INK, textAlign: 'center' },
  centerSub: { color: INK2, textAlign: 'center' },
  bottom: { position: 'absolute', left: 0, right: 0, bottom: 0, alignItems: 'center', gap: space.sm, paddingTop: space.md, backgroundColor: 'rgba(0,0,0,0.35)' },
  note: { color: INK2, textAlign: 'center', paddingHorizontal: space.xl },
  shutter: { width: 74, height: 74, borderRadius: 37, borderWidth: 4, borderColor: INK, alignItems: 'center', justifyContent: 'center' },
  shutterInner: { width: 58, height: 58, borderRadius: 29, backgroundColor: INK },
});
