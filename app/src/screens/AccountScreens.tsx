// OpenMousse 账号（2026-09-29，只用邮箱验证码）：登录页（填邮箱 → 输 6 位验证码）和账号页（名字、账号里存了什么、退出、删账号）。
// 只有配了账号服务的壳才有（api/account.ts 的 accountsEnabled）；OpenMousse 第一次打开先到登录页，登录完再去连 claw。
import React, { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Alert, Pressable, ScrollView, StyleSheet, TextInput, View } from 'react-native';
import Constants from 'expo-constants';
import { useNavigation, useRoute } from '@react-navigation/native';
import { ChevronLeft, Mail } from '../components/icons';
import { Mark } from '../components/Mark';
import { Group, GroupLabel, GroupNote, Row, RoundButton, SettingsHeader } from '../components/settings';
import { useSheet } from '../components/Sheet';
import { Btn, Screen, T, showError } from '../components/ui';
import {
  AccountError, deleteAccount, forgetCode, initialsOf, listAccountClaws, pendingCode, RESEND_S, sendCode, setAccountName, signOut, syncClaw, verifyCode,
  type AccountClaw,
} from '../api/account';
import { getBase } from '../api/base';
import { L } from '../i18n';
import { useStore } from '../store';
import { radius, space, type, useTheme } from '../theme';
import { useAccount } from './MeScreen';
import { KeyboardScroll } from '../components/keyboard';

/** 这次打开 app 里，账号服务连不上时点了「先用着」：不再拦着。下次打开再请登录。 */
let skipped = false;
export const loginSkipped = () => skipped;

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function LoginScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const route = useRoute<any>();
  const fromSettings = route.params?.from === 'settings';
  const { needsServer, connected, appName, claw } = useStore();
  // 码发出去还没用上（10 分钟内）：app 重载、被系统收掉以后回来，直接接着输验证码
  const [pending] = useState(pendingCode);
  const [step, setStep] = useState<'email' | 'code'>(pending ? 'code' : 'email');
  const [email, setEmail] = useState(pending?.email ?? '');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [offline, setOffline] = useState(false);
  const [wait, setWait] = useState(pending?.wait ?? 0);
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

  const toCode = (seconds: number, why: string | null) => {
    setStep('code'); setCode(''); setWait(seconds); setNote(why);
    setTimeout(() => codeRef.current?.focus(), 300);
  };

  const send = async () => {
    const v = email.trim();
    if (!EMAIL.test(v)) { setMsg(L('邮箱格式无效', 'Invalid email address')); return; }
    setBusy(true); setMsg(null); setNote(null); setOffline(false);
    try {
      await sendCode(v);
      toCode(RESEND_S, null);
    } catch (e) {
      // 这个邮箱一分钟内刚发过码（发完码 app 重载了、退回来又点一次）：不报错，去输邮件里那个码
      if (e instanceof AccountError && e.retryAfter) toCode(e.retryAfter, L('验证码刚刚已发送，请使用邮件中最新的一封。', 'A code was sent a moment ago. Use the latest one in your email.'));
      else fail(e);
    } finally { setBusy(false); }
  };

  /** 邮箱里已经有码（比如限流发不出新的）：不发，直接去输。 */
  const haveCode = () => {
    if (!EMAIL.test(email.trim())) { setMsg(L('请先填写邮箱', 'Enter your email first')); return; }
    setMsg(null); setOffline(false);
    toCode(0, null);
  };

  const verify = async (c = code) => {
    const v = c.replace(/\D/g, '');
    if (v.length < 6) { setMsg(L('验证码为 6 位数字', 'The code is 6 digits')); return; }
    setBusy(true); setMsg(null); setNote(null); setOffline(false);
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
      <KeyboardScroll layout contentContainerStyle={{ flexGrow: 1 }} keyboardShouldPersistTaps="handled">
        {step === 'email' ? (
          <View style={{ flex: 1, paddingHorizontal: space.xl }}>
            {fromSettings ? <View style={{ paddingTop: 6 }}><RoundButton onPress={() => nav.goBack()} label={L('返回', 'Back')}><ChevronLeft size={22} color={t.ink} /></RoundButton></View> : null}
            <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', gap: 12 }}>
              <Mark size={108} decorative />
              <T v="largeTitle" style={{ marginTop: 8 }}>{Constants.expoConfig?.name ?? 'OpenMousse'}</T>
              <T v="body" color={t.ink2} style={{ textAlign: 'center' }}>{L('你的 Agent，运行在你自己的机器上。', 'Your Agent, running on your own machine.')}</T>
            </View>
            <View style={{ gap: 12, paddingBottom: space.lg }}>
              <T v="callout" color={t.ink2} style={{ paddingLeft: 4 }}>{L('邮箱', 'Email')}</T>
              <TextInput value={email} onChangeText={(v) => { setEmail(v); setMsg(null); }} placeholder="you@example.com" placeholderTextColor={t.ink3}
                autoCapitalize="none" autoCorrect={false} keyboardType="email-address" textContentType="emailAddress" autoComplete="email"
                returnKeyType="send" onSubmitEditing={send} accessibilityLabel={L('邮箱', 'Email')}
                style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink, borderColor: t.line }]} />
              {msg ? <T v="callout" color={t.bad}>{msg}</T> : null}
              <Pressable onPress={busy ? undefined : send} accessibilityRole="button" style={({ pressed }) => [styles.primary, { backgroundColor: t.cyan, opacity: pressed || busy ? 0.7 : 1 }]}>
                {busy ? <ActivityIndicator color={t.onCyan} /> : <T v="headline" color={t.onCyan} style={{ fontSize: 17 }}>{L('发送验证码', 'Send code')}</T>}
              </Pressable>
              {offline ? <Btn kind="quiet" label={L('暂时跳过，下次登录', 'Continue for now, sign in later')} onPress={() => { skipped = true; goOn(); }} /> : null}
              <Pressable onPress={busy ? undefined : haveCode} accessibilityRole="button" style={{ height: 36, alignItems: 'center', justifyContent: 'center' }}>
                <T v="callout" color={t.cyan}>{L('已有验证码', 'I have a code')}</T>
              </Pressable>
            </View>
            <T v="caption" color={t.ink3} style={{ textAlign: 'center', lineHeight: 18, fontWeight: '400', paddingBottom: space.xl }}>
              {L('无需密码：我们会向你的邮箱发送 6 位验证码。账号仅用于识别你的身份并记录你连接的 claw，对话和记忆不会上传。',
                'No password needed: we email you a 6-digit code. Your account only identifies you and records which claws you use; chats and memory never leave your claw.')}
            </T>
          </View>
        ) : (
          <View style={{ flex: 1, paddingHorizontal: space.xl }}>
            <View style={{ paddingTop: 6 }}>
              <RoundButton onPress={() => { setStep('email'); setMsg(null); setNote(null); forgetCode(); }} label={L('更换邮箱', 'Use another email')}><ChevronLeft size={22} color={t.ink} /></RoundButton>
            </View>
            <View style={{ gap: 8, marginTop: space.xl }}>
              <T v="largeTitle" style={{ fontSize: 28 }}>{L('查看邮箱', 'Check your email')}</T>
              <T v="body" color={t.ink2}>{L(`6 位验证码已发送至 ${email.trim()}。`, `We sent a 6-digit code to ${email.trim()}.`)}</T>
            </View>
            <TextInput ref={codeRef} value={code} onChangeText={onCode} placeholder="······" placeholderTextColor={t.ink3}
              keyboardType="number-pad" textContentType="oneTimeCode" autoComplete="one-time-code" maxLength={8}
              accessibilityLabel={L('验证码', 'Code')} returnKeyType="done" onSubmitEditing={() => verify()}
              style={[styles.code, { backgroundColor: t.surface, color: t.ink, borderColor: code ? t.cyan : t.line }]} />
            <View style={[styles.hint, { backgroundColor: t.surface }]}>
              <Mail size={20} color={t.cyan} />
              <T v="callout" color={t.ink2} style={{ flex: 1 }}>{L('iPhone 会在键盘上方显示邮件中的验证码，轻点即可填入。', 'Your iPhone shows the code from Mail above the keyboard; tap it to fill it in.')}</T>
            </View>
            {note && !msg ? <T v="callout" color={t.ink2} style={{ marginTop: space.md }}>{note}</T> : null}
            {msg ? <T v="callout" color={t.bad} style={{ marginTop: space.md }}>{msg}</T> : null}
            <View style={{ flex: 1 }} />
            <View style={{ gap: 6, paddingBottom: space.xl }}>
              <Pressable onPress={busy ? undefined : () => verify()} accessibilityRole="button" style={({ pressed }) => [styles.primary, { backgroundColor: t.cyan, opacity: pressed || busy ? 0.7 : 1 }]}>
                {busy ? <ActivityIndicator color={t.onCyan} /> : <T v="headline" color={t.onCyan} style={{ fontSize: 17 }}>{L('继续', 'Continue')}</T>}
              </Pressable>
              <Pressable onPress={wait > 0 || busy ? undefined : send} accessibilityRole="button" style={{ height: 44, alignItems: 'center', justifyContent: 'center' }}>
                <T v="callout" color={wait > 0 ? t.ink3 : t.cyan}>{wait > 0 ? L(`未收到？${wait} 秒后可重新发送`, `Didn't get it? Resend in ${wait}s`) : L('重新发送验证码', 'Resend code')}</T>
              </Pressable>
              {offline ? <Btn kind="quiet" label={L('暂时跳过，下次登录', 'Continue for now, sign in later')} onPress={() => { skipped = true; goOn(); }} /> : null}
            </View>
          </View>
        )}
      </KeyboardScroll>
    </Screen>
  );
}


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
          <TextInput defaultValue={draft} onChangeText={(v) => { draft = v; }} autoFocus placeholder={L('你的称呼', 'Your name')} placeholderTextColor={t.ink3}
            style={[type.body, styles.input, { backgroundColor: t.surface2, color: t.ink, borderColor: t.line }]} accessibilityLabel={L('名字', 'Name')} />
          <Btn label={L('保存', 'Save')} onPress={() => { setAccountName(draft).then(close).catch((e) => showError(L('保存失败', "Couldn't save"), e)); }} />
        </View>
      ),
    });
  };

  const out = () => {
    Alert.alert(L('退出登录？', 'Sign out?'), L('仅退出账号，此设备已连接的 claw 仍可正常使用。', 'This only signs out of your account; the claws on this device keep working.'), [
      { text: L('取消', 'Cancel'), style: 'cancel' },
      { text: L('退出', 'Sign out'), style: 'destructive', onPress: () => { signOut().then(() => nav.reset({ index: 0, routes: [{ name: 'Login' }] })).catch((e) => showError(L('退出失败', "Couldn't sign out"), e)); } },
    ]);
  };

  const remove = () => {
    Alert.alert(L('删除账号？', 'Delete your account?'),
      L('账号及其记录的 claw 列表将被永久删除，无法恢复。你的 claw 上的对话、记忆和连接器不受影响。', "Your account and its list of claws will be permanently deleted. Nothing on your claws changes: chats, memory and connectors stay."), [
        { text: L('取消', 'Cancel'), style: 'cancel' },
        {
          text: L('删除', 'Delete'), style: 'destructive', onPress: () => {
            deleteAccount().then(() => nav.reset({ index: 0, routes: [{ name: 'Login' }] })).catch((e) => showError(L('删除失败', "Couldn't delete it"), e));
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
            <T v="title" style={{ fontSize: 24 }}>{initialsOf(user)}</T>
          </View>
          <T v="title" style={{ marginTop: 6 }}>{user.name || user.email.split('@')[0]}</T>
          <T v="callout" color={t.ink2}>{user.email}</T>
        </View>
        <Group>
          <Row first title={L('名字', 'Name')} value={user.name || L('未设置', 'Not set')} tone={user.name ? undefined : 'muted'} onPress={editName} />
          <Row title={L('邮箱', 'Email')} value={user.email} />
          <Row title={L('登录方式', 'Sign-in')} value={L('邮箱验证码', 'Email code')} />
        </Group>

        <GroupLabel>{L('账号存储的内容', 'What your account stores')}</GroupLabel>
        <Group style={{ padding: 18, gap: 8 }}>
          <T v="body">{L('邮箱、名字，以及你连接过的 claw（名称、地址、类型，不含令牌）。', 'Your email, name and the claws you connect (name, address and kind; never tokens).')}</T>
          <T v="callout" color={t.ink2}>{L('对话、记忆和连接器令牌均不存储在账号中，仅保存在你自己的 claw 上。', 'Chats, memory and connector tokens are never in your account; they stay on your own claw.')}</T>
        </Group>
        {claws && claws.length ? (
          <>
            <GroupLabel>{L('已连接的 claw', 'Connected claws')}</GroupLabel>
            <Group>
              {claws.map((c, i) => (
                <Row key={c.base} first={i === 0} title={c.name} sub={[c.claw_name, c.base.replace(/^https?:\/\//, '')].filter(Boolean).join(' · ')}
                  value={c.base === here ? L('本设备使用中', 'On this device') : undefined} tone="accent" />
              ))}
            </Group>
            <GroupNote>{L('在其他设备上连接过的 claw 也会列在这里；如需在本设备上使用，请重新配对。', 'Claws you connected on other devices appear here too; to use one on this device, pair it again.')}</GroupNote>
          </>
        ) : null}

        <Group style={{ marginTop: 26 }}>
          <Row first title={L('退出登录', 'Sign out')} danger onPress={out} />
          <Row title={L('删除账号', 'Delete account')} sub={L('claw 上的数据不受影响', 'Nothing on your claws changes')} danger onPress={remove} />
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
