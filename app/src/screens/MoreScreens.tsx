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
import { MODELS } from '../data/models';
import type { ActivityEntry, AvatarConfig, ProfileItem } from '../data/types';
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
  if (booting) return <Card><T v="callout" color={t.ink2}>正在连服务器…</T></Card>;
  if (!connected) return <Card><T v="callout" color={t.ink2}>没连上服务器。检查「我 → 服务器」后下拉刷新。</T></Card>;
  if (dataErrors[k]) return <Card><T v="callout" color={t.bad}>读取失败：{dataErrors[k]}</T></Card>;
  if (empty && loading[k]) return <Card><T v="callout" color={t.ink2}>正在读…</T></Card>;
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
    editProfile(item.id, value).then(close).catch((e) => Alert.alert('没改成', e instanceof Error ? e.message : String(e))).finally(() => setBusy(false));
  };
  return (
    <View style={{ gap: space.md }}>
      <TextInput value={text} onChangeText={setText} multiline autoFocus accessibilityLabel="档案内容"
        style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink, minHeight: 96, textAlignVertical: 'top' }]} />
      <T v="caption" color={t.ink3}>保存后这一条会标成你今天确认的（[L] {new Date().toLocaleDateString('en-CA')}），旧的一版另存在服务器上，{agentName()} 检索不到。</T>
      <Btn label={busy ? '保存中…' : '保存'} onPress={() => { if (!busy && text.trim()) run(text.trim()); }} />
      {confirm
        ? <Btn label="确认删掉这一条" kind="danger" icon={<Trash2 size={16} color={t.bad} />} onPress={() => !busy && run(null)} />
        : <Btn label="删掉这一条" kind="danger" icon={<Trash2 size={16} color={t.bad} />} onPress={() => setConfirm(true)} />}
    </View>
  );
}

export function IdentityScreen() {
  const t = useTheme();
  const sheet = useSheet();
  const { profile, live } = useStore();
  const sections = [...new Set(profile.map((p) => p.section))];
  return (
    <Page title="基础档案" refresh={['profile']}>
      <T v="callout" color={t.ink2} style={{ marginBottom: space.md }}>
        {`${agentName()} 的 L0 档案（服务器上的 shared/profile/USER.md），所有 agent 共用。点一条就能改，改完直接写回文件。`}
      </T>
      {live?.body.length ? (
        <>
          <SectionLabel right={<Pill label="只读" tone="good" />}>身体数据</SectionLabel>
          <Card style={{ paddingVertical: space.xs }}>
            {live.body.map((m, i) => (
              <ListRow key={m.type} title={m.label} sub={`记录于 ${m.date}`} last={i === live.body.length - 1}
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
                <Pressable key={p.id} onPress={() => sheet.open({ title: `改「${sec}」里的一条`, content: (close) => <EditProfileSheet item={p} close={close} /> })}
                  accessibilityRole="button" accessibilityHint="点一下修改"
                  style={({ pressed }) => [styles.prow, i < rows.length - 1 && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }, { opacity: pressed ? 0.6 : 1 }]}>
                  <T v="body">{p.text}</T>
                  {p.sources.length || p.date ? <T v="caption" color={t.ink3}>{[p.sources.join('、'), p.date].filter(Boolean).join(' · ')}</T> : null}
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
    <Page title="日志" refresh={['journal']}>
      <T v="callout" color={t.ink2} style={{ marginBottom: space.md }}>
        {`你在对话里说的训练感受、对事情的想法、做的决定，${agentName()} 记在这里（服务器的数据库，不进模型上下文，它需要时用工具查）。带 Agent 的也会出现在那个 Agent 的「记忆」页。`}
      </T>
      <JournalList entries={journal} showGroup empty={`还没有记录。跟 ${agentName()} 说「记一下」或者直接分享感受，就会出现在这里。`} />
    </Page>
  );
}

export function MemoryScreen() {
  const t = useTheme();
  const { groups, memoriesUpdated } = useStore();
  return (
    <Page title="记忆" refresh={['memories']}>
      <T v="callout" color={t.ink2}>
        {agentName()} 的长期记忆（L1，MEMORY.md{memoriesUpdated ? `，${memoriesUpdated}更新` : ''}）。每天的工作记忆由夜里的 Dreaming 整理后晋升到这里。点垃圾桶让它忘记。
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
    <Page title="活动记录" refresh={['activity']}>
      <T v="callout" color={t.ink2} style={{ marginBottom: space.md }}>
        {`${agentName()} 每一次回复（在哪、用了什么工具）、定时任务的结果，以及你在 app 里做的有后果的操作。Gateway 的审计只记元数据，不记内容。`}
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
    <Page title="安全" refresh={['security']}>
      <T v="callout" color={t.ink2} style={{ marginBottom: space.md }}>下面是服务器上的实测状态。安全底座（第 9 步）没建好之前，发邮件以上级别的代办能力不会开放。</T>
      <Status k="security" empty={!security} />
      {security ? (
        <>
          <SectionLabel>现在的状态</SectionLabel>
          <Card style={{ paddingVertical: space.xs }}>
            {security.facts.map((r, i) => <ListRow key={r.title} title={r.title} sub={r.sub} right={<Pill label={r.state} tone={r.tone} />} last={i === security.facts.length - 1} />)}
          </Card>
          <SectionLabel>第 9 步要建的</SectionLabel>
          <Card style={{ paddingVertical: space.xs }}>
            {security.plan.map((r, i) => <ListRow key={r.title} title={r.title} sub={r.sub} right={<Pill label="计划" />} last={i === security.plan.length - 1} />)}
          </Card>
          <SectionLabel>代办能力的审批规则（计划，第 9、10 步上线后生效）</SectionLabel>
          <Card style={{ paddingVertical: space.xs }}>
            {security.rules.map(([k, v], i) => <ListRow key={k} title={k} right={<T v="callout" color={t.ink2}>{v}</T>} last={i === security.rules.length - 1} />)}
          </Card>
        </>
      ) : null}
    </Page>
  );
}

const PROVIDER_NOTE: Record<string, string> = { anthropic: 'Claude Max 订阅', openai: 'ChatGPT 订阅' };
const PROVIDER_LABEL: Record<string, string> = { anthropic: 'Claude', openai: 'OpenAI', google: 'Google', moonshot: 'Moonshot', dashscope: '阿里 DashScope', deepseek: 'DeepSeek', zai: '智谱', xai: 'xAI' };

export function ModelsScreen() {
  const t = useTheme();
  const { models } = useStore();
  const billing = useBilling();
  const name = (id: string | null | undefined) => (id ? modelOf(id)?.name ?? id : '—');
  const allowed = models ? MODELS.filter((m) => models.allowed.includes(m.id)) : [];
  // 只看能选的模型用到的那几家
  const used = new Set(allowed.map((m) => m.id.split('/')[0]));
  const subs = (models?.providers ?? []).filter((p) => p.subscription && used.has(p.provider));
  const keyed = (models?.providers ?? []).filter((p) => !p.subscription && used.has(p.provider)).map((p) => PROVIDER_LABEL[p.provider] ?? p.name);
  const expired = subs.filter((p) => p.status !== 'ok');
  return (
    <Page title="模型与计费" refresh={['models']}>
      <Status k="models" empty={!models} />
      {models ? (
        <>
          <SectionLabel>日常对话</SectionLabel>
          <Card>
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 6 }}>
              {[models.primary, ...models.fallbacks].map((id, i) => (
                <React.Fragment key={id}>
                  {i ? <T v="callout" color={t.ink3}>→</T> : null}
                  <Pill label={modelOf(id)?.short ?? id} tone={i === 0 ? 'gold' : 'neutral'} />
                </React.Fragment>
              ))}
            </View>
            <T v="callout" color={t.ink2} style={{ marginTop: space.md }}>默认 {name(models.primary)}。前一个登录失效或额度用尽时自动换下一个，回复上标的是实际回答的模型。</T>
          </Card>
          <SectionLabel>派出去的任务</SectionLabel>
          <Card><T v="callout">默认 {name(models.subagent)}，派活时可以换。</T></Card>

          <SectionLabel>登录与计费</SectionLabel>
          <Card style={{ paddingVertical: space.xs }}>
            {subs.map((p, i) => {
              const ok = p.status === 'ok';
              const sub = ok
                ? `${PROVIDER_NOTE[p.provider] ?? '订阅'}，登录有效${p.expires ? `，${p.expires.replace('d', ' 天')}后到期` : ''}。`
                : `${PROVIDER_NOTE[p.provider] ?? '订阅'}的登录过期了。请求还能用，但会退到 API key 按量计费。`;
              return <ListRow key={p.provider} title={PROVIDER_LABEL[p.provider] ?? p.name} sub={sub} right={<Pill label={ok ? '正常' : '已过期'} tone={ok ? 'good' : 'warn'} />} last={i === subs.length - 1 && !keyed.length} />;
            })}
            {keyed.length ? <ListRow title="API key 按量计费" sub={keyed.join('、')} last /> : null}
          </Card>
          {expired.map((p) => (
            <Card key={p.provider} style={{ marginTop: space.sm, gap: 6 }}>
              <T v="callout" color={t.warn}>重新登录 {PROVIDER_LABEL[p.provider] ?? p.name}：在服务器上运行</T>
              <T v="callout" selectable style={{ fontFamily: 'Menlo' }}>openclaw models auth login --provider {p.provider} --device-code</T>
            </Card>
          ))}

          <SectionLabel>{`能选的模型（${allowed.length}）`}</SectionLabel>
          <Card style={{ paddingVertical: space.xs }}>
            {allowed.map((m, i) => (
              <ListRow key={m.id} title={m.name} sub={m.note} last={i === allowed.length - 1}
                right={<View style={{ flexDirection: 'row', gap: 6 }}><Pill label={billing(m)} tone={billing(m) === '订阅' ? 'gold' : 'cyan'} />{m.cost === '贵' ? <Pill label="贵" tone="warn" /> : null}</View>} />
            ))}
          </Card>
          <T v="caption" color={t.ink3} style={{ marginTop: space.sm, paddingHorizontal: space.xs }}>只列 Gateway 允许列表里、并且 app 认识的模型。允许列表一共 {models.allowed.length} 个，在 openclaw.json 里改。</T>
        </>
      ) : null}
    </Page>
  );
}

const RINGS = ['#D9AE62', '#E8E3D6', '#E58F6B', '#9FB4FF'];
const STREAMS = ['#5CCFE6', '#7BE0B0', '#F2D58A', '#C7A6FF'];
const STYLES: { key: AvatarConfig['style']; label: string }[] = [{ key: 'lens', label: '透镜' }, { key: 'eclipse', label: '日食' }, { key: 'orbit', label: '轨道' }];

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
    <Page title="形象">
      <View style={{ alignItems: 'center', paddingVertical: space.xl }}><LensAvatar size={148} config={avatar} /></View>
      <SectionLabel>样式</SectionLabel>
      <View style={{ flexDirection: 'row', gap: space.md }}>
        {STYLES.map((s) => (
          <Pressable key={s.key} onPress={() => setAvatar({ style: s.key })} accessibilityRole="radio" accessibilityState={{ selected: avatar.style === s.key }}
            style={[styles.styleOpt, { backgroundColor: t.surface, borderColor: avatar.style === s.key ? t.goldFill : 'transparent' }]}>
            <LensAvatar size={56} config={{ ...avatar, style: s.key }} />
            <T v="caption" color={avatar.style === s.key ? t.ink : t.ink2}>{s.label}</T>
          </Pressable>
        ))}
      </View>
      <SectionLabel>光环</SectionLabel>
      <Swatches colors={RINGS} value={avatar.ring} onPick={(c) => setAvatar({ ring: c })} label="光环颜色" />
      <SectionLabel>数据流</SectionLabel>
      <Swatches colors={STREAMS} value={avatar.stream} onPick={(c) => setAvatar({ stream: c })} label="数据流颜色" />
      <T v="caption" color={t.ink3} style={{ marginTop: space.lg }}>存在服务器上，换设备也一样。</T>
    </Page>
  );
}

const styles = StyleSheet.create({
  act: { flexDirection: 'row', gap: space.md, paddingVertical: 12 },
  prow: { gap: 3, paddingVertical: 11 },
  input: { borderRadius: radius.md, paddingHorizontal: space.md, paddingVertical: 10 },
  styleOpt: { flex: 1, alignItems: 'center', gap: 8, borderRadius: radius.lg, paddingVertical: space.lg, borderWidth: 1.5 },
});
