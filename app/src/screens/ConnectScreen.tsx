import React, { useState } from 'react';
import { Linking, Platform, Pressable, ScrollView, StyleSheet, TextInput, View } from 'react-native';
import { useNavigation, useRoute } from '@react-navigation/native';
import * as Device from 'expo-device';
import { Btn, Card, NavHeader, Pill, Screen, SectionLabel, T, showError } from '../components/ui';
import { defaultBase, getBase, getToken, pairWithCode, parsePairing, saveServerConfig, testServer } from '../api/base';
import { L } from '../i18n';
import { useStore } from '../store';
import { radius, space, type, useTheme } from '../theme';

/** 网站上的新手指南：从装服务到在手机上说第一句话。 */
const GUIDE_URL = 'https://openmousse.ai/start.html';

/** 连接页：填自己服务器的地址和接入令牌。第一次打开 app、或在「我 → 服务器」里都能进来。 */
export function ConnectScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { connected, needsServer, refreshLive } = useStore();
  const [base, setBase] = useState(getBase() || defaultBase());
  const [token, setToken] = useState(getToken());
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null);
  // 配对码：服务器上 tokens.py pair 出的（claw 替你装好后会发来一条链接）。点链接进来时地址和码已经填好，看一眼地址再点「连接」
  const route = useRoute<any>();
  const [pairText, setPairText] = useState('');
  const pairAt = (route.params?.at as number | undefined) ?? 0;
  const [seenAt, setSeenAt] = useState(0);
  if (pairAt !== seenAt && route.params?.pairServer && route.params?.pairCode) {  // 新点进来的链接：渲染时就换上（React 推荐的「随参数调整状态」写法）
    setSeenAt(pairAt);
    setBase(route.params.pairServer);
    setPairText(route.params.pairCode);
    setMsg(null);
  }

  const finish = (appName: string) => {
    setMsg({ text: L(`连上了：${appName}`, `Connected to ${appName}`), ok: true });
    refreshLive();
    setBusy(false);
    nav.reset({ index: 0, routes: [{ name: 'Tabs' }] });
  };

  const pair = async () => {
    const p = parsePairing(pairText);
    const server = (p.server || base).trim();
    if (!p.code) { setMsg({ text: L('配对码是 8 位字母数字，或者整条 openmousse://pair 链接', 'A pairing code is 8 letters and digits, or the whole openmousse://pair link'), ok: false }); return; }
    if (!server) { setMsg({ text: L('先填服务器地址', 'Enter the server address first.'), ok: false }); return; }
    setBusy(true); setMsg(null);
    const device = Platform.OS === 'web' ? 'web' : (Device.deviceName || Device.modelName || Platform.OS);
    const r = await pairWithCode(server, p.code, device);
    if (!r.ok) { setMsg({ text: r.message, ok: false }); setBusy(false); return; }
    setBase(server);
    finish(r.appName);
  };

  const connect = async () => {
    if (!base.trim()) { setMsg({ text: L('先填服务器地址', 'Enter the server address first.'), ok: false }); return; }
    setBusy(true); setMsg(null);
    const r = await testServer(base, token);
    if (!r.ok) { setMsg({ text: r.message, ok: false }); setBusy(false); return; }
    await saveServerConfig(base, token);
    finish(r.appName);
  };

  return (
    <Screen>
      <NavHeader title={L('服务器', 'Server')} onBack={() => (needsServer ? undefined : nav.goBack())} right={connected ? <Pill label={L('已连接', 'Connected')} tone="good" /> : undefined} />
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl }} keyboardShouldPersistTaps="handled" automaticallyAdjustKeyboardInsets keyboardDismissMode="interactive">
        <T v="callout" color={t.ink2} style={{ marginBottom: space.md }}>
          {L('这个 app 是一个壳，所有对话、记忆和数据都在你自己的服务器上。点你的 claw 发来的配对链接，或者填服务器地址和接入令牌，就能用。',
            'This app connects to your own server, where all your chats, memory and data live. Tap the pairing link your claw sent you, or enter the server address and access token, to get started.')}
        </T>
        {/* 还没连上时才有用：没有服务器的人从这里去看怎么装 */}
        {connected ? null : (
          <Pressable onPress={() => { Linking.openURL(GUIDE_URL).catch((e) => showError(L('打不开', "Couldn't open it"), e)); }} accessibilityRole="link" hitSlop={6} style={{ alignSelf: 'flex-start' }}>
            <T v="callout" color={t.gold} style={{ fontWeight: '600' }}>{L('还没有服务器？看新手指南 →', 'No server yet? See the getting-started guide →')}</T>
          </Pressable>
        )}
        <SectionLabel>{L('配对码', 'Pairing code')}</SectionLabel>
        <T v="callout" color={t.ink2} style={{ marginBottom: 6 }}>
          {L('你的 claw 替你装好以后会发来一条配对链接，点它就进到这里；也可以把链接或 8 位码粘贴在这里。',
            'After your claw sets things up it sends you a pairing link; tapping it brings you here. You can also paste the link or the 8-character code.')}
        </T>
        <TextInput value={pairText} onChangeText={(v) => { setPairText(v); setMsg(null); const p = parsePairing(v); if (p.server) setBase(p.server); }}
          placeholder={L('openmousse://pair?… 或 8 位配对码', 'openmousse://pair?… or the 8-character code')}
          placeholderTextColor={t.ink3} autoCapitalize="characters" autoCorrect={false} accessibilityLabel={L('配对码', 'Pairing code')}
          style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink }]} />
        {pairText.trim() ? (
          <View style={{ marginTop: space.md }}>
            <T v="callout" color={t.ink2} style={{ marginBottom: 6 }}>{L(`会连到：${(parsePairing(pairText).server || base || '（先填下面的服务器地址）').trim()}`, `Will connect to: ${(parsePairing(pairText).server || base || '(enter the server address below)').trim()}`)}</T>
            <Btn label={busy ? L('连接中…', 'Connecting…') : L('用配对码连接', 'Connect with the code')} onPress={() => !busy && pair()} />
          </View>
        ) : null}

        <SectionLabel>{L('服务器地址', 'Server address')}</SectionLabel>
        <TextInput value={base} onChangeText={(v) => { setBase(v); setMsg(null); }} placeholder={L('https://你的域名 或 http://100.x.x.x:8080', 'https://your-domain.com or http://100.x.x.x:8080')}
          placeholderTextColor={t.ink3} autoCapitalize="none" autoCorrect={false} keyboardType="url" accessibilityLabel={L('服务器地址', 'Server address')}
          style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink }]} />
        <SectionLabel>{L('接入令牌', 'Access token')}</SectionLabel>
        <TextInput value={token} onChangeText={(v) => { setToken(v); setMsg(null); }} placeholder={L('在服务器上生成，可留空（Tailscale 白名单设备）', 'Generated on your server (optional for allowlisted Tailscale devices)')}
          placeholderTextColor={t.ink3} autoCapitalize="none" autoCorrect={false} secureTextEntry accessibilityLabel={L('接入令牌', 'Access token')}
          style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink }]} />
        {msg ? <T v="callout" color={msg.ok ? t.good : t.bad} style={{ marginTop: 8 }}>{msg.text}</T> : null}
        <View style={{ marginTop: space.xl }}><Btn label={busy ? L('连接中…', 'Connecting…') : L('连接', 'Connect')} onPress={() => !busy && connect()} /></View>

        <SectionLabel>{L('怎么拿令牌', 'How to get a token')}</SectionLabel>
        <Card>
          <T v="callout" color={t.ink2}>{L('更省事的是配对码：在服务器上运行 tokens.py pair（或者让你的 claw 跑），手机扫码或点链接就行。要手填令牌的话，安装命令最后打印的「手机令牌」就是它。丢了的话，在服务器上运行：',
            'Easier: a pairing code. Run tokens.py pair on your server (or ask your claw to), then scan the code or tap the link on the phone. To type a token instead, it\'s the phone token the install command printed at the end. Lost it? On your server run:')}</T>
          <T v="callout" selectable style={{ fontFamily: 'monospace', marginVertical: 6 }}>~/.openmousse/venv/bin/python ~/.openmousse/repo/server/tokens.py add phone2</T>
          <T v="callout" color={t.ink2}>{L('把打印出来的新令牌填到上面。令牌存在这台设备的钥匙串里，不经过任何第三方。',
            "Paste the new token it prints into the field above. It's stored in this device's keychain and never passes through a third party.")}</T>
        </Card>
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({ input: { borderRadius: radius.md, paddingHorizontal: space.lg, paddingVertical: 13 } });
