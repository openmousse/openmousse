import React, { useState } from 'react';
import { agentName } from '../brand';
import { Alert, Pressable, RefreshControl, ScrollView, StyleSheet, TextInput, View } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { CheckCheck, CircleX, Clock, Eraser, MessageCircle, Pencil, Trash2, X } from '../components/icons';
import { LensAvatar } from '../components/LensAvatar';
import { MemoryList } from '../components/MemoryList';
import { modelOf, useBilling } from '../components/ModelPicker';
import { useSheet } from '../components/Sheet';
import { Btn, Card, ListRow, NavHeader, Pill, Screen, SectionLabel, T } from '../components/ui';
import { MODELS, billingLabel, costLabel } from '../data/models';
import type { ActivityEntry, AvatarConfig, ProfileItem } from '../data/types';
import { L } from '../i18n';
import { useStore, type DataKey } from '../store';
import { radius, space, type, useTheme } from '../theme';
import { JournalList } from '../components/Records';

function Page({ title, children, refresh }: { title: string; children: React.ReactNode; refresh?: DataKey[] }) {
  const nav = useNavigation<any>();
  const { reload, loading } = useStore();
  return (
    <Screen>
      <NavHeader title={title} onBack={() => nav.goBack()} />
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingBottom: space.xxl }} keyboardShouldPersistTaps="handled"
        refreshControl={refresh ? <RefreshControl refreshing={refresh.some((k) => loading[k])} onRefresh={() => reload(...refresh)} /> : undefined}>
        {children}
      </ScrollView>
    </Screen>
  );
}

/** 某块数据的三种非正常状态：没连上、读失败、正在读。正常时返回 null。 */
function Status({ k, empty }: { k: DataKey; empty?: boolean }) {
  const t = useTheme();
  const { connected, booting, loading, dataErrors } = useStore();
  if (booting) return <Card><T v="callout" color={t.ink2}>{L('正在连服务器…', 'Connecting to the server…')}</T></Card>;
  if (!connected) return <Card><T v="callout" color={t.ink2}>{L('没连上服务器。检查「我 → 服务器」后下拉刷新。', 'Not connected to the server. Check Me → Server, then pull down to refresh.')}</T></Card>;
  if (dataErrors[k]) return <Card><T v="callout" color={t.bad}>{L(`读取失败：${dataErrors[k]}`, `Couldn't load: ${dataErrors[k]}`)}</T></Card>;
  if (empty && loading[k]) return <Card><T v="callout" color={t.ink2}>{L('正在读…', 'Loading…')}</T></Card>;
  return null;
}

function EditProfileSheet({ item, close }: { item: ProfileItem; close: () => void }) {
  const t = useTheme();
  const { editProfile } = useStore();
  const [text, setText] = useState(item.text.replace(/\n/g, ' '));
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const run = (value: string | null) => {
    setBusy(true);
    editProfile(item.id, value).then(close).catch((e) => Alert.alert(L('没改成', "Couldn't update"), e instanceof Error ? e.message : String(e))).finally(() => setBusy(false));
  };
  const today = new Date().toLocaleDateString('en-CA');
  return (
    <View style={{ gap: space.md }}>
      <TextInput value={text} onChangeText={setText} multiline autoFocus accessibilityLabel={L('档案内容', 'Profile item')}
        style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink, minHeight: 96, textAlignVertical: 'top' }]} />
      <T v="caption" color={t.ink3}>{L(
        `保存后这一条会标成你今天确认的（[L] ${today}），旧的一版另存在服务器上，${agentName()} 检索不到。`,
        `Once saved, this item is marked as confirmed by you today ([L] ${today}). The old version is kept separately on the server, where ${agentName()} can't search it.`,
      )}</T>
      <Btn label={busy ? L('保存中…', 'Saving…') : L('保存', 'Save')} onPress={() => { if (!busy && text.trim()) run(text.trim()); }} />
      {confirm
        ? <Btn label={L('确认删掉这一条', 'Confirm delete')} kind="danger" icon={<Trash2 size={16} color={t.bad} />} onPress={() => !busy && run(null)} />
        : <Btn label={L('删掉这一条', 'Delete this item')} kind="danger" icon={<Trash2 size={16} color={t.bad} />} onPress={() => setConfirm(true)} />}
    </View>
  );
}

export function IdentityScreen() {
  const t = useTheme();
  const sheet = useSheet();
  const { profile, live } = useStore();
  const sections = [...new Set(profile.map((p) => p.section))];
  return (
    <Page title={L('基础档案', 'Profile')} refresh={['profile']}>
      <T v="callout" color={t.ink2} style={{ marginBottom: space.md }}>
        {L(
          `${agentName()} 的 L0 档案（服务器上的 shared/profile/USER.md），所有 agent 共用。点一条就能改，改完直接写回文件。`,
          `${agentName()}'s L0 profile (shared/profile/USER.md on the server), shared by all agents. Tap an item to edit it; changes are written straight back to the file.`,
        )}
      </T>
      {live?.body.length ? (
        <>
          <SectionLabel right={<Pill label={L('只读', 'Read-only')} tone="good" />}>{L('身体数据', 'Body data')}</SectionLabel>
          <Card style={{ paddingVertical: space.xs }}>
            {live.body.map((m, i) => (
              <ListRow key={m.type} title={m.label} sub={L(`记录于 ${m.date}`, `Recorded ${m.date}`)} last={i === live.body.length - 1}
                right={<T v="headline" style={{ fontVariant: ['tabular-nums'] }}>{m.value} {m.unit}</T>} />
            ))}
          </Card>
        </>
      ) : null}
      <Status k="profile" empty={!profile.length} />
      {sections.map((sec) => {
        const rows = profile.filter((p) => p.section === sec);
        return (
          <View key={sec}>
            <SectionLabel>{sec}</SectionLabel>
            <Card style={{ paddingVertical: space.xs }}>
              {rows.map((p, i) => (
                <Pressable key={p.id} onPress={() => sheet.open({ title: L(`改「${sec}」里的一条`, `Edit an item in "${sec}"`), content: (close) => <EditProfileSheet item={p} close={close} /> })}
                  accessibilityRole="button" accessibilityHint={L('点一下修改', 'Tap to edit')}
                  style={({ pressed }) => [styles.prow, i < rows.length - 1 && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }, { opacity: pressed ? 0.6 : 1 }]}>
                  <T v="body">{p.text}</T>
                  {p.sources.length || p.date ? <T v="caption" color={t.ink3}>{[p.sources.join(L('、', ', ')), p.date].filter(Boolean).join(' · ')}</T> : null}
                </Pressable>
              ))}
            </Card>
          </View>
        );
      })}
    </Page>
  );
}

export function JournalScreen() {
  const t = useTheme();
  const { journal } = useStore();
  return (
    <Page title={L('日志', 'Journal')} refresh={['journal']}>
      <T v="callout" color={t.ink2} style={{ marginBottom: space.md }}>
        {L(
          `你在对话里说的训练感受、对事情的想法、做的决定，${agentName()} 记在这里（服务器的数据库，不进模型上下文，它需要时用工具查）。带 Agent 的也会出现在那个 Agent 的「记忆」页。`,
          `How a workout felt, what you think about things, decisions you made: when you say it in chat, ${agentName()} logs it here (in the server's database, not the model's context; it looks things up with a tool when needed). Entries tied to an agent also show on that agent's Memory page.`,
        )}
      </T>
      <JournalList entries={journal} showGroup empty={L(
        `还没有记录。跟 ${agentName()} 说「记一下」或者直接分享感受，就会出现在这里。`,
        `Nothing yet. Tell ${agentName()} "note this" or just share how you feel, and it shows up here.`,
      )} />
    </Page>
  );
}

export function MemoryScreen() {
  const t = useTheme();
  const { groups, memoriesUpdated } = useStore();
  return (
    <Page title={L('记忆', 'Memory')} refresh={['memories']}>
      <T v="callout" color={t.ink2}>
        {L(
          `${agentName()} 的长期记忆（L1，MEMORY.md${memoriesUpdated ? `，${memoriesUpdated}更新` : ''}）。每天的工作记忆由夜里的 Dreaming 整理后晋升到这里。点垃圾桶让它忘记。`,
          `${agentName()}'s long-term memory (L1, MEMORY.md${memoriesUpdated ? `, updated ${memoriesUpdated}` : ''}). Each day's working memory is sorted overnight by Dreaming and promoted here. Tap the trash can to make it forget.`,
        )}
      </T>
      <MemoryList scope="main" />
      {groups.map((g) => (
        <View key={g.id}>
          <SectionLabel>{g.name}</SectionLabel>
          <MemoryList scope={g.id} />
        </View>
      ))}
    </Page>
  );
}

const kindIcon: Record<ActivityEntry['kind'], typeof Clock> = {
  reply: MessageCircle, cron: Clock, failed: CircleX, approved: CheckCheck, denied: X, forgot: Eraser, edit: Pencil, deleted: Trash2, toggled: Clock,
};

export function ActivityScreen() {
  const t = useTheme();
  const { activity } = useStore();
  const tint = (k: ActivityEntry['kind']) => (k === 'failed' || k === 'denied' || k === 'deleted' ? t.bad : k === 'approved' ? t.good : k === 'toggled' || k === 'edit' ? t.gold : t.ink2);
  return (
    <Page title={L('活动记录', 'Activity')} refresh={['activity']}>
      <T v="callout" color={t.ink2} style={{ marginBottom: space.md }}>
        {L(
          `${agentName()} 每一次回复（在哪、用了什么工具）、定时任务的结果，以及你在 app 里做的有后果的操作。Gateway 的审计只记元数据，不记内容。`,
          `Every reply from ${agentName()} (where, and which tools it used), scheduled job results, and consequential actions you took in the app. The Gateway audit log keeps metadata only, not content.`,
        )}
      </T>
      <Status k="activity" empty={!activity.length} />
      {activity.length ? (
        <Card style={{ paddingVertical: space.xs }}>
          {activity.map((a, i) => {
            const Icon = kindIcon[a.kind] ?? Clock;
            return (
              <View key={a.id} style={[styles.act, i < activity.length - 1 && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }]}>
                <Icon size={18} color={tint(a.kind)} style={{ marginTop: 2 }} />
                <View style={{ flex: 1, gap: 3 }}>
                  <T v="body">{a.text}</T>
                  <T v="caption" color={t.ink3}>{a.actor} · {a.time}</T>
                </View>
              </View>
            );
          })}
        </Card>
      ) : null}
    </Page>
  );
}

export function SecurityScreen() {
  const t = useTheme();
  const { security } = useStore();
  return (
    <Page title={L('安全', 'Security')} refresh={['security']}>
      <T v="callout" color={t.ink2} style={{ marginBottom: space.md }}>{L(
        '下面是服务器上的实测状态。安全措施没建好之前，发邮件以上级别的代办能力不会开放。',
        'Below is the live status from the server. Until the safeguards below are built, acting on your behalf (sending email or anything bigger) stays off.',
      )}</T>
      <Status k="security" empty={!security} />
      {security ? (
        <>
          <SectionLabel>{L('现在的状态', 'Current status')}</SectionLabel>
          <Card style={{ paddingVertical: space.xs }}>
            {security.facts.map((r, i) => <ListRow key={r.title} title={r.title} sub={r.sub} right={<Pill label={r.state} tone={r.tone} />} last={i === security.facts.length - 1} />)}
          </Card>
          <SectionLabel>{L('还没做的安全措施', 'Security still to build')}</SectionLabel>
          <Card style={{ paddingVertical: space.xs }}>
            {security.plan.map((r, i) => <ListRow key={r.title} title={r.title} sub={r.sub} right={<Pill label={L('计划', 'Planned')} />} last={i === security.plan.length - 1} />)}
          </Card>
          <SectionLabel>{L('代办能力的审批规则（计划，还没生效）', 'Approval rules for acting on your behalf (planned, not in effect yet)')}</SectionLabel>
          <Card style={{ paddingVertical: space.xs }}>
            {security.rules.map(([k, v], i) => <ListRow key={k} title={k} right={<T v="callout" color={t.ink2}>{v}</T>} last={i === security.rules.length - 1} />)}
          </Card>
        </>
      ) : null}
    </Page>
  );
}

// L() 要在渲染时调用，所以写成函数
function providerNote(provider: string): string | undefined {
  const notes: Record<string, string> = { anthropic: L('Claude Max 订阅', 'Claude Max subscription'), openai: L('ChatGPT 订阅', 'ChatGPT subscription') };
  return notes[provider];
}
function providerLabel(provider: string): string | undefined {
  const labels: Record<string, string> = { anthropic: 'Claude', openai: 'OpenAI', google: 'Google', moonshot: 'Moonshot', dashscope: L('阿里 DashScope', 'Alibaba DashScope'), deepseek: 'DeepSeek', zai: L('智谱', 'Z.ai'), xai: 'xAI' };
  return labels[provider];
}

export function ModelsScreen() {
  const t = useTheme();
  const { models } = useStore();
  const billing = useBilling();
  const name = (id: string | null | undefined) => (id ? modelOf(id)?.name ?? id : '—');
  const allowed = models ? MODELS.filter((m) => models.allowed.includes(m.id)) : [];
  // 只看能选的模型用到的那几家
  const used = new Set(allowed.map((m) => m.id.split('/')[0]));
  const subs = (models?.providers ?? []).filter((p) => p.subscription && used.has(p.provider));
  const keyed = (models?.providers ?? []).filter((p) => !p.subscription && used.has(p.provider)).map((p) => providerLabel(p.provider) ?? p.name);
  const expired = subs.filter((p) => p.status !== 'ok');
  return (
    <Page title={L('模型与计费', 'Models & billing')} refresh={['models']}>
      <Status k="models" empty={!models} />
      {models ? (
        <>
          <SectionLabel>{L('日常对话', 'Everyday chat')}</SectionLabel>
          <Card>
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 6 }}>
              {[models.primary, ...models.fallbacks].map((id, i) => (
                <React.Fragment key={id}>
                  {i ? <T v="callout" color={t.ink3}>→</T> : null}
                  <Pill label={modelOf(id)?.short ?? id} tone={i === 0 ? 'gold' : 'neutral'} />
                </React.Fragment>
              ))}
            </View>
            <T v="callout" color={t.ink2} style={{ marginTop: space.md }}>{L(
              `默认 ${name(models.primary)}。前一个登录失效或额度用尽时自动换下一个，回复上标的是实际回答的模型。`,
              `Default: ${name(models.primary)}. If one's login lapses or its quota runs out, the next takes over; each reply shows the model that actually answered.`,
            )}</T>
          </Card>
          <SectionLabel>{L('派出去的任务', 'Tasks sent out')}</SectionLabel>
          <Card><T v="callout">{L(`默认 ${name(models.subagent)}，派活时可以换。`, `Default: ${name(models.subagent)}. You can switch when sending a task.`)}</T></Card>

          <SectionLabel>{L('登录与计费', 'Sign-in & billing')}</SectionLabel>
          <Card style={{ paddingVertical: space.xs }}>
            {subs.map((p, i) => {
              const ok = p.status === 'ok';
              const note = providerNote(p.provider) ?? L('订阅', 'Subscription');
              // expires 是 OpenClaw 的短标签：30m / 5h / 30d（天数至少 2）
              const sub = ok
                ? L(`${note}，登录有效${p.expires ? `，${p.expires.replace('d', ' 天')}后到期` : ''}。`, `${note}, signed in${p.expires ? `, expires in ${p.expires.replace('d', ' days')}` : ''}.`)
                : L(`${note}的登录过期了。请求还能用，但会退到 API key 按量计费。`, `${note} login expired. Requests still work but fall back to pay-as-you-go API key billing.`);
              return <ListRow key={p.provider} title={providerLabel(p.provider) ?? p.name} sub={sub} right={<Pill label={ok ? L('正常', 'OK') : L('已过期', 'Expired')} tone={ok ? 'good' : 'warn'} />} last={i === subs.length - 1 && !keyed.length} />;
            })}
            {keyed.length ? <ListRow title={L('API key 按量计费', 'Pay-as-you-go API key')} sub={keyed.join(L('、', ', '))} last /> : null}
          </Card>
          {expired.map((p) => (
            <Card key={p.provider} style={{ marginTop: space.sm, gap: 6 }}>
              <T v="callout" color={t.warn}>{L(`重新登录 ${providerLabel(p.provider) ?? p.name}：在服务器上运行`, `To sign in to ${providerLabel(p.provider) ?? p.name} again, run this on the server:`)}</T>
              <T v="callout" selectable style={{ fontFamily: 'Menlo' }}>openclaw models auth login --provider {p.provider} --device-code</T>
            </Card>
          ))}

          <SectionLabel>{L(`能选的模型（${allowed.length}）`, `Available models (${allowed.length})`)}</SectionLabel>
          <Card style={{ paddingVertical: space.xs }}>
            {allowed.map((m, i) => (
              <ListRow key={m.id} title={m.name} sub={m.note} last={i === allowed.length - 1}
                right={<View style={{ flexDirection: 'row', gap: 6 }}><Pill label={billingLabel(billing(m))} tone={billing(m) === '订阅' ? 'gold' : 'cyan'} />{m.cost === '贵' ? <Pill label={costLabel(m.cost)} tone="warn" /> : null}</View>} />
            ))}
          </Card>
          <T v="caption" color={t.ink3} style={{ marginTop: space.sm, paddingHorizontal: space.xs }}>{L(
            `只列 Gateway 允许列表里、并且 app 认识的模型。允许列表一共 ${models.allowed.length} 个，在 openclaw.json 里改。`,
            `Only models on the Gateway allowlist that the app knows about. The allowlist has ${models.allowed.length} in total; edit it in openclaw.json.`,
          )}</T>
        </>
      ) : null}
    </Page>
  );
}

const RINGS = ['#D9AE62', '#E8E3D6', '#E58F6B', '#9FB4FF'];
const STREAMS = ['#5CCFE6', '#7BE0B0', '#F2D58A', '#C7A6FF'];
const styleOptions = (): { key: AvatarConfig['style']; label: string }[] => [
  { key: 'lens', label: L('透镜', 'Lens') }, { key: 'eclipse', label: L('日食', 'Eclipse') }, { key: 'orbit', label: L('轨道', 'Orbit') },
];

function Swatches({ colors, value, onPick, label }: { colors: string[]; value: string; onPick: (c: string) => void; label: string }) {
  const t = useTheme();
  return (
    <View style={{ flexDirection: 'row', gap: space.md }} accessibilityRole="radiogroup" accessibilityLabel={label}>
      {colors.map((c) => (
        <Pressable key={c} onPress={() => onPick(c)} accessibilityRole="radio" accessibilityState={{ selected: c === value }} accessibilityLabel={c}
          style={{ width: 44, height: 44, borderRadius: 22, borderWidth: 2, borderColor: c === value ? t.ink : 'transparent', alignItems: 'center', justifyContent: 'center' }}>
          <View style={{ width: 32, height: 32, borderRadius: 16, backgroundColor: c, borderWidth: StyleSheet.hairlineWidth, borderColor: t.line }} />
        </Pressable>
      ))}
    </View>
  );
}

export function AvatarScreen() {
  const t = useTheme();
  const { avatar, setAvatar } = useStore();
  return (
    <Page title={L('形象', 'Look')}>
      <View style={{ alignItems: 'center', paddingVertical: space.xl }}><LensAvatar size={148} config={avatar} /></View>
      <SectionLabel>{L('样式', 'Style')}</SectionLabel>
      <View style={{ flexDirection: 'row', gap: space.md }}>
        {styleOptions().map((s) => (
          <Pressable key={s.key} onPress={() => setAvatar({ style: s.key })} accessibilityRole="radio" accessibilityState={{ selected: avatar.style === s.key }}
            style={[styles.styleOpt, { backgroundColor: t.surface, borderColor: avatar.style === s.key ? t.goldFill : 'transparent' }]}>
            <LensAvatar size={56} config={{ ...avatar, style: s.key }} />
            <T v="caption" color={avatar.style === s.key ? t.ink : t.ink2}>{s.label}</T>
          </Pressable>
        ))}
      </View>
      <SectionLabel>{L('光环', 'Halo')}</SectionLabel>
      <Swatches colors={RINGS} value={avatar.ring} onPick={(c) => setAvatar({ ring: c })} label={L('光环颜色', 'Halo color')} />
      <SectionLabel>{L('数据流', 'Data stream')}</SectionLabel>
      <Swatches colors={STREAMS} value={avatar.stream} onPick={(c) => setAvatar({ stream: c })} label={L('数据流颜色', 'Data stream color')} />
      <T v="caption" color={t.ink3} style={{ marginTop: space.lg }}>{L('存在服务器上，换设备也一样。', 'Saved on the server, so it looks the same on every device.')}</T>
    </Page>
  );
}

const styles = StyleSheet.create({
  act: { flexDirection: 'row', gap: space.md, paddingVertical: 12 },
  prow: { gap: 3, paddingVertical: 11 },
  input: { borderRadius: radius.md, paddingHorizontal: space.md, paddingVertical: 10 },
  styleOpt: { flex: 1, alignItems: 'center', gap: 8, borderRadius: radius.lg, paddingVertical: space.lg, borderWidth: 1.5 },
});
