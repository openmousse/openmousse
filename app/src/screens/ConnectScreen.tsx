import React, { useState } from 'react';
import { ScrollView, StyleSheet, TextInput, View } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { Btn, Card, NavHeader, Pill, Screen, SectionLabel, T } from '../components/ui';
import { defaultBase, getBase, getToken, saveServerConfig, testServer } from '../api/base';
import { L } from '../i18n';
import { useStore } from '../store';
import { radius, space, type, useTheme } from '../theme';

/** 连接页：填自己服务器的地址和接入令牌。第一次打开 app、或在「我 → 服务器」里都能进来。 */
export function ConnectScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { connected, needsServer, refreshLive } = useStore();
  const [base, setBase] = useState(getBase() || defaultBase());
  const [token, setToken] = useState(getToken());
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null);

  const connect = async () => {
    if (!base.trim()) { setMsg({ text: L('先填服务器地址', 'Enter the server address first.'), ok: false }); return; }
    setBusy(true); setMsg(null);
    const r = await testServer(base, token);
    if (!r.ok) { setMsg({ text: r.message, ok: false }); setBusy(false); return; }
    await saveServerConfig(base, token);
    setMsg({ text: L(`连上了：${r.appName}`, `Connected to ${r.appName}`), ok: true });
    refreshLive();
    setBusy(false);
    nav.reset({ index: 0, routes: [{ name: 'Tabs' }] });
  };

  return (
    <Screen>
      <NavHeader title={L('服务器', 'Server')} onBack={() => (needsServer ? undefined : nav.goBack())} right={connected ? <Pill label={L('已连接', 'Connected')} tone="good" /> : undefined} />
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl }} keyboardShouldPersistTaps="handled">
        <T v="callout" color={t.ink2} style={{ marginBottom: space.md }}>
          {L('这个 app 是一个壳，所有对话、记忆和数据都在你自己的服务器上。填服务器的地址和接入令牌就能用。',
            'This app connects to your own server, where all your chats, memory and data live. Enter the server address and access token to get started.')}
        </T>
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
          <T v="callout" color={t.ink2}>{L('在服务器上进 server 目录，运行：', 'On your server, go to the server/ folder and run:')}</T>
          <T v="callout" style={{ fontFamily: 'monospace', marginVertical: 6 }}>{L('python3 tokens.py add 手机', 'python3 tokens.py add phone')}</T>
          <T v="callout" color={t.ink2}>{L('把打印出来的令牌填到上面。令牌存在这台设备的钥匙串里，不经过任何第三方。',
            "Paste the token it prints into the field above. It's stored in this device's keychain and never passes through a third party.")}</T>
        </Card>
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({ input: { borderRadius: radius.md, paddingHorizontal: space.lg, paddingVertical: 13 } });
