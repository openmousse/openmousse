// OpenMousse 账号（2026-09-29，只用邮箱验证码）：登录页（填邮箱 → 输 6 位验证码）和账号页（名字、账号里存了什么、退出、删账号）。
// 只有配了账号服务的壳才有（api/account.ts 的 accountsEnabled）；OpenMousse 第一次打开先到登录页，登录完再去连 claw。
import React, { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Alert, Pressable, ScrollView, StyleSheet, TextInput, View } from 'react-native';
import Constants from 'expo-constants';
import { useNavigation, useRoute } from '@react-navigation/native';
import Svg, { Circle } from 'react-native-svg';
import { ChevronLeft, Mail } from '../components/icons';
import { Group, GroupLabel, GroupNote, Row, RoundButton, SettingsHeader } from '../components/settings';
import { useSheet } from '../components/Sheet';
import { Btn, Screen, T, showError } from '../components/ui';
import {
  AccountError, deleteAccount, listAccountClaws, sendCode, setAccountName, signOut, syncClaw, verifyCode, type AccountClaw,
} from '../api/account';
import { getBase } from '../api/base';
import { L } from '../i18n';
import { useStore } from '../store';
import { radius, space, type, useTheme } from '../theme';
import { useAccount } from './MeScreen';

/** 这次打开 app 里，账号服务连不上时点了「先用着」：不再拦着。下次打开再请登录。 */
let skipped = false;
export const loginSkipped = () => skipped;

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const RESEND_S = 45;

function Halo({ size = 88 }: { size?: number }) {
  const t = useTheme();
  return (
    <Svg width={size} height={size} viewBox="0 0 96 96" accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
      <Circle cx={48} cy={48} r={40} stroke={t.cyan} strokeWidth={6} opacity={0.25} fill="none" />
      <Circle cx={48} cy={48} r={28} stroke={t.cyan} strokeWidth={6} fill="none" />
      <Circle cx={48} cy={48} r={10} fill={t.cyan} />
    </Svg>
  );
}

export function LoginScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const route = useRoute<any>();
  const fromSettings = route.params?.from === 'settings';
  const { needsServer, connected, appName, claw } = useStore();
  const [step, setStep] = useState<'email' | 'code'>('email');
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [offline, setOffline] = useState(false);
  const [wait, setWait] = useState(0);
  const codeRef = useRef<TextInput>(null);

  useEffect(() => {
    if (wait <= 0) return undefined;
    const h = setTimeout(() => setWait((w) => w - 1), 1000);
    return () => clearTimeout(h);
  }, [wait]);

  const goOn = () => {
    if (fromSettings && nav.canGoBack()) { nav.goBack(); return; }
    nav.reset({ index: 0, routes: [{ name: needsServer ? 'Connect' : 'Tabs' }] });
  };

  const fail = (e: unknown) => {
    const net = e instanceof AccountError && e.network;
    setOffline(!!net);
    setMsg(e instanceof Error ? e.message : String(e));
  };

  const send = async () => {
    const v = email.trim();
    if (!EMAIL.test(v)) { setMsg(L('邮箱格式不对', "That email address doesn't look right")); return; }
    setBusy(true); setMsg(null); setOffline(false);
    try {
      await sendCode(v);
      setStep('code'); setCode(''); setWait(RESEND_S);
      setTimeout(() => codeRef.current?.focus(), 300);
    } catch (e) { fail(e); } finally { setBusy(false); }
  };

  const verify = async (c = code) => {
    const v = c.replace(/\D/g, '');
    if (v.length < 6) { setMsg(L('验证码是 6 位数字', 'The code is 6 digits')); return; }
    setBusy(true); setMsg(null); setOffline(false);
    try {
      await verifyCode(email, v);
      // 已经连着一台 claw（老用户第一次登录）：记进账号
      if (connected && getBase()) syncClaw({ base: getBase(), name: appName, clawKind: claw.kind, clawName: claw.name }).catch(() => {});
      goOn();
    } catch (e) { fail(e); setCode(''); } finally { setBusy(false); }
  };

  const onCode = (v: string) => {
    const d = v.replace(/\D/g, '').slice(0, 8);
    setCode(d); setMsg(null);
    if (d.length === 6 && !busy) verify(d);  // 从邮件里自动填进来的：直接提交
  };

  return (
    <Screen>
      <ScrollView contentContainerStyle={{ flexGrow: 1 }} keyboardShouldPersistTaps="handled" automaticallyAdjustKeyboardInsets>
        {step === 'email' ? (
          <View style={{ flex: 1, paddingHorizontal: space.xl }}>
            {fromSettings ? <View style={{ paddingTop: 6 }}><RoundButton onPress={() => nav.goBack()} label={L('返回', 'Back')}><ChevronLeft size={22} color={t.ink} /></RoundButton></View> : null}
            <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', gap: 12 }}>
              <Halo />
              <T v="largeTitle" style={{ marginTop: 8 }}>{Constants.expoConfig?.name ?? 'OpenMousse'}</T>
              <T v="body" color={t.ink2} style={{ textAlign: 'center' }}>{L('你的 agent，住在你自己的机器上。', 'Your agent, living on your own machine.')}</T>
            </View>
            <View style={{ gap: 12, paddingBottom: space.lg }}>
              <T v="callout" color={t.ink2} style={{ paddingLeft: 4 }}>{L('邮箱', 'Email')}</T>
              <TextInput value={email} onChangeText={(v) => { setEmail(v); setMsg(null); }} placeholder="you@example.com" placeholderTextColor={t.ink3}
                autoCapitalize="none" autoCorrect={false} keyboardType="email-address" textContentType="emailAddress" autoComplete="email"
                returnKeyType="send" onSubmitEditing={send} accessibilityLabel={L('邮箱', 'Email')}
                style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink, borderColor: t.line }]} />
              {msg ? <T v="callout" color={t.bad}>{msg}</T> : null}
              <Pressable onPress={busy ? undefined : send} accessibilityRole="button" style={({ pressed }) => [styles.primary, { backgroundColor: t.cyan, opacity: pressed || busy ? 0.7 : 1 }]}>
                {busy ? <ActivityIndicator color="#FFFFFF" /> : <T v="headline" color="#FFFFFF" style={{ fontSize: 17 }}>{L('发验证码', 'Send code')}</T>}
              </Pressable>
              {offline ? <Btn kind="quiet" label={L('先用着，下次再登录', 'Continue for now, sign in later')} onPress={() => { skipped = true; goOn(); }} /> : null}
            </View>
            <T v="caption" color={t.ink3} style={{ textAlign: 'center', lineHeight: 18, fontWeight: '400', paddingBottom: space.xl }}>
              {L('不用密码：我们往你的邮箱发一个 6 位数。账号只用来认出你、记住你连了哪几台 claw，对话和记忆不上传。',
                'No password: we email you a 6-digit code. Your account only recognises you and remembers which claws you use; chats and memory never leave your claw.')}
            </T>
          </View>
        ) : (
          <View style={{ flex: 1, paddingHorizontal: space.xl }}>
            <View style={{ paddingTop: 6 }}>
              <RoundButton onPress={() => { setStep('email'); setMsg(null); }} label={L('换个邮箱', 'Use another email')}><ChevronLeft size={22} color={t.ink} /></RoundButton>
            </View>
            <View style={{ gap: 8, marginTop: space.xl }}>
              <T v="largeTitle" style={{ fontSize: 28 }}>{L('看一眼邮箱', 'Check your email')}</T>
              <T v="body" color={t.ink2}>{L(`6 位验证码发到了 ${email.trim()}。`, `We sent a 6-digit code to ${email.trim()}.`)}</T>
            </View>
            <TextInput ref={codeRef} value={code} onChangeText={onCode} placeholder="······" placeholderTextColor={t.ink3}
              keyboardType="number-pad" textContentType="oneTimeCode" autoComplete="one-time-code" maxLength={8}
              accessibilityLabel={L('验证码', 'Code')} returnKeyType="done" onSubmitEditing={() => verify()}
              style={[styles.code, { backgroundColor: t.surface, color: t.ink, borderColor: code ? t.cyan : t.line }]} />
            <View style={[styles.hint, { backgroundColor: t.surface }]}>
              <Mail size={20} color={t.cyan} />
              <T v="callout" color={t.ink2} style={{ flex: 1 }}>{L('iPhone 会在键盘上方给出邮件里的验证码，点一下就填好。', 'Your iPhone shows the code from Mail above the keyboard; one tap fills it in.')}</T>
            </View>
            {msg ? <T v="callout" color={t.bad} style={{ marginTop: space.md }}>{msg}</T> : null}
            <View style={{ flex: 1 }} />
            <View style={{ gap: 6, paddingBottom: space.xl }}>
              <Pressable onPress={busy ? undefined : () => verify()} accessibilityRole="button" style={({ pressed }) => [styles.primary, { backgroundColor: t.cyan, opacity: pressed || busy ? 0.7 : 1 }]}>
                {busy ? <ActivityIndicator color="#FFFFFF" /> : <T v="headline" color="#FFFFFF" style={{ fontSize: 17 }}>{L('继续', 'Continue')}</T>}
              </Pressable>
              <Pressable onPress={wait > 0 || busy ? undefined : send} accessibilityRole="button" style={{ height: 44, alignItems: 'center', justifyContent: 'center' }}>
                <T v="callout" color={wait > 0 ? t.ink3 : t.cyan}>{wait > 0 ? L(`没收到？${wait} 秒后可以重发`, `Didn't get it? Resend in ${wait}s`) : L('重发验证码', 'Resend the code')}</T>
              </Pressable>
              {offline ? <Btn kind="quiet" label={L('先用着，下次再登录', 'Continue for now, sign in later')} onPress={() => { skipped = true; goOn(); }} /> : null}
            </View>
          </View>
        )}
      </ScrollView>
    </Screen>
  );
}

const initials = (s: string) => {
  const parts = s.trim().split(/[\s@._-]+/).filter(Boolean);
  return (parts.length > 1 ? parts[0][0] + parts[1][0] : s.slice(0, 2)).toUpperCase();
};

export function AccountScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const sheet = useSheet();
  const { user } = useAccount();
  const [claws, setClaws] = useState<AccountClaw[] | null>(null);

  useEffect(() => { listAccountClaws().then(setClaws).catch(() => setClaws(null)); }, []);

  if (!user) {
    return (
      <Screen>
        <SettingsHeader title={L('账号', 'Account')} onBack={() => nav.goBack()} />
        <Group><Row first title={L('登录账号', 'Sign in')} onPress={() => nav.navigate('Login', { from: 'settings' })} /></Group>
      </Screen>
    );
  }

  const editName = () => {
    let draft = user.name || '';
    sheet.open({
      title: L('名字', 'Name'),
      content: (close) => (
        <View style={{ gap: space.md }}>
          <TextInput defaultValue={draft} onChangeText={(v) => { draft = v; }} autoFocus placeholder={L('怎么称呼你', 'What should we call you')} placeholderTextColor={t.ink3}
            style={[type.body, styles.input, { backgroundColor: t.surface2, color: t.ink, borderColor: t.line }]} accessibilityLabel={L('名字', 'Name')} />
          <Btn label={L('存下', 'Save')} onPress={() => { setAccountName(draft).then(close).catch((e) => showError(L('没存上', "Couldn't save"), e)); }} />
        </View>
      ),
    });
  };

  const out = () => {
    Alert.alert(L('退出登录？', 'Sign out?'), L('只退出账号，这台设备连着的 claw 照常能用。', 'Only the account signs out; the claws on this device keep working.'), [
      { text: L('取消', 'Cancel'), style: 'cancel' },
      { text: L('退出', 'Sign out'), style: 'destructive', onPress: () => { signOut().then(() => nav.reset({ index: 0, routes: [{ name: 'Login' }] })).catch((e) => showError(L('没退出成', "Couldn't sign out"), e)); } },
    ]);
  };

  const remove = () => {
    Alert.alert(L('删除账号？', 'Delete your account?'),
      L('账号和它记着的 claw 列表都会删掉，删了找不回来。你的 claw 上的对话、记忆、连接器一样都不动。', "Your account and the list of claws it remembers are deleted for good. Nothing on your claws changes: chats, memory and connectors stay."), [
        { text: L('取消', 'Cancel'), style: 'cancel' },
        {
          text: L('删除', 'Delete'), style: 'destructive', onPress: () => {
            deleteAccount().then(() => nav.reset({ index: 0, routes: [{ name: 'Login' }] })).catch((e) => showError(L('没删成', "Couldn't delete it"), e));
          },
        },
      ]);
  };

  const here = getBase();
  return (
    <Screen>
      <SettingsHeader title={L('账号', 'Account')} onBack={() => nav.goBack()} />
      <ScrollView contentContainerStyle={{ paddingBottom: space.xxl }}>
        <View style={{ alignItems: 'center', gap: 6, paddingVertical: space.lg }}>
          <View style={{ width: 76, height: 76, borderRadius: 38, backgroundColor: t.surface2, alignItems: 'center', justifyContent: 'center' }}>
            <T v="title" style={{ fontSize: 24 }}>{initials(user.name || user.email)}</T>
          </View>
          <T v="title" style={{ marginTop: 6 }}>{user.name || user.email.split('@')[0]}</T>
          <T v="callout" color={t.ink2}>{user.email}</T>
        </View>
        <Group>
          <Row first title={L('名字', 'Name')} value={user.name || L('没填', 'Not set')} tone={user.name ? undefined : 'muted'} onPress={editName} />
          <Row title={L('邮箱', 'Email')} value={user.email} />
          <Row title={L('登录方式', 'Sign-in')} value={L('邮箱验证码', 'Email code')} />
        </Group>

        <GroupLabel>{L('账号里存了什么', "What's in your account")}</GroupLabel>
        <Group style={{ padding: 18, gap: 8 }}>
          <T v="body">{L('邮箱、名字，和你连过的 claw（名字、地址、种类，不含令牌）。', 'Your email, name and the claws you connect (name, address and kind; never tokens).')}</T>
          <T v="callout" color={t.ink2}>{L('对话、记忆、连接器的令牌都不在账号里，只在你自己的 claw 上。', 'Chats, memory and connector tokens are never in your account; they stay on your own claw.')}</T>
        </Group>
        {claws && claws.length ? (
          <>
            <GroupLabel>{L('你连过的 claw', 'Claws you connected')}</GroupLabel>
            <Group>
              {claws.map((c, i) => (
                <Row key={c.base} first={i === 0} title={c.name} sub={[c.claw_name, c.base.replace(/^https?:\/\//, '')].filter(Boolean).join(' · ')}
                  value={c.base === here ? L('这台设备在用', 'On this device') : undefined} tone="accent" />
              ))}
            </Group>
            <GroupNote>{L('在别的设备上连过的也在这里；那台要在这台设备上用，重新配对一次。', 'Claws you connected elsewhere show up too; to use one on this device, pair it once more.')}</GroupNote>
          </>
        ) : null}

        <Group style={{ marginTop: 26 }}>
          <Row first title={L('退出登录', 'Sign out')} danger onPress={out} />
          <Row title={L('删除账号', 'Delete account')} sub={L('claw 上的东西一样都不动', 'Nothing on your claws changes')} danger onPress={remove} />
        </Group>
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  input: { height: 52, borderRadius: radius.md, borderWidth: StyleSheet.hairlineWidth, paddingHorizontal: space.lg },
  primary: { height: 52, borderRadius: radius.md, alignItems: 'center', justifyContent: 'center' },
  code: { marginTop: space.xl, height: 64, borderRadius: radius.md, borderWidth: 1.5, paddingHorizontal: space.lg, fontSize: 30, fontWeight: '600', letterSpacing: 12, textAlign: 'center' },
  hint: { flexDirection: 'row', alignItems: 'center', gap: 12, marginTop: space.lg, padding: 14, borderRadius: radius.md },
});
