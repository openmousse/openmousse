// 设备节点的入口（第 8 批）：硬件页里的一组两行（这台手机、设备和节点），和连上服务器以后让这台手机在前台等活的 PhoneNodeRunner。
// 只有自带内核的服务器有设备节点（server.json 的 engine.nodes）；服务器报 nodes 不分底座，所以还要看 claw.kind（同第 9 批 MeScreen 的做法）。
import React, { useEffect } from 'react';
import { Platform } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { supports } from '../api/version';
import { Monitor, Smartphone } from '../components/icons';
import { Group, GroupLabel, Row } from '../components/settings';
import { L } from '../i18n';
import { useStore } from '../store';
import { useTheme } from '../theme';
import { setCameraOpener, startPhoneNode } from './phone';

/** 这台服务器有设备节点：连着、报了 nodes、底座是自带内核。 */
function useNodesHere(): boolean {
  const { connected, claw } = useStore();
  return connected && claw.kind === 'builtin' && supports('nodes');
}

/** 硬件页的「设备和节点」一组：这台手机（只在手机 app 里）、设备和节点（配对的电脑和手机）。别的服务器不显示。 */
export function NodeEntries() {
  const nav = useNavigation<any>();
  const t = useTheme();
  const here = useNodesHere();
  const phone = Platform.OS !== 'web';
  if (!here) return null;
  return (
    <>
      <GroupLabel>{L('设备和节点', 'Devices and nodes')}</GroupLabel>
      <Group>
        {phone ? (
          <Row first icon={<Smartphone size={22} color={t.cyan} />} title={L('这台手机', 'This phone')}
            sub={L('定位、拍照、通知', 'Location, photos, notifications')} onPress={() => nav.navigate('PhoneNode')} />
        ) : null}
        <Row first={!phone} icon={<Monitor size={22} color={t.cyan} />} title={L('设备和节点', 'Devices and nodes')}
          sub={L('配对的电脑和手机', 'Paired computers and phones')} onPress={() => nav.navigate('Nodes')} />
      </Group>
    </>
  );
}

/**
 * 连上服务器以后（store.connected），让这台手机照「这台手机」里打开的几样在前台等活（src/nodes/phone.ts）。
 * openCamera：怎么打开拍照确认页（navigation.tsx 给，免得这里反过来引 navigation 成环）。不画任何东西。
 */
export function PhoneNodeRunner({ openCamera }: { openCamera: (id: string) => boolean }) {
  const here = useNodesHere();
  useEffect(() => {
    if (Platform.OS === 'web' || !here) return undefined;
    setCameraOpener(openCamera);
    const stop = startPhoneNode();
    return () => { stop(); setCameraOpener(null); };
  }, [here, openCamera]);
  return null;
}
