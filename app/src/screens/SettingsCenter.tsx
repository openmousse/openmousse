// 设置中心的几页（2026-10-19，第 9 批；设计指挥部 ① 画布第 17 张，第三版，主人认了）：
// 「密钥 → 模型」（接上的服务商，一家一张卡）、「密钥 → 搜索」「密钥 → 向量」（和第一次打开的 ③ ④ 同一套控件，多显示现在用的哪家、测一下）、
// 「密钥 → 用量」（最近 30 天，稿子没单画：照现有设置页的最简版）、「连接 → 手」（每个 Agent 能动手做的事，这一批只读）。
// 首页在 MeScreen.tsx。都走 /api/setup/…（只认主人的设备，别的令牌 403：出错照 showError），老服务器（没有 setup）首页不放这几行。
import React, { useCallback, useState } from 'react';
import { ScrollView, StyleSheet, View } from 'react-native';
import { useFocusEffect, useNavigation } from '@react-navigation/native';
import { agentName } from '../brand';
import { Spinner } from '../components/ChatCards';
import { Plus } from '../components/icons';
import { RoundButton, SettingsHeader, Tile } from '../components/settings';
import { useSheet } from '../components/Sheet';
import {
  GoldLink, GroupHead, KitCard, KitRow, KitSep, LoadState, OkLine, PrimaryButton, QuietButton, detailOf, failLine, monoFont,
} from '../components/setupKit';
import { PullRefresh, Screen, Segmented, T, showError } from '../components/ui';
import { StatRow } from '../components/viz';
import {
  setupApi, type CloudRow, type EmbeddingChoice, type EngineSecurity, type HandsAgent, type LocalRow, type LoginRow, type MemoryView,
  type Overview, type ProbeError, type ProviderRow, type ProvidersList, type Purposes, type SearchView, type TestResult, type Usage, type UsageAcc,
} from '../api/setup';
import { L } from '../i18n';
import { useStore } from '../store';
import { space, useTheme } from '../theme';
import {
  MemoryChooser, SearchChooser, dayOf, embedName, memoryInitial, monoFor, saveMemory, searchNeedOf, shortName, type EmbedPick,
} from './SetupScreens';

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e ?? ''));

/** 设置里的一页：设置页的页头、下拉刷新、两边留白。 */
function Page({ title, onBack, right, onRefresh, children }: { title: string; onBack: () => void; right?: React.ReactNode; onRefresh?: () => unknown; children: React.ReactNode }) {
  return (
    <Screen>
      <SettingsHeader title={title} onBack={onBack} right={right} />
      <ScrollView contentContainerStyle={styles.page} keyboardShouldPersistTaps="handled" refreshControl={onRefresh ? <PullRefresh onRefresh={onRefresh} /> : undefined}>
        {children}
      </ScrollView>
    </Screen>
  );
}

/** 这一样归 claw 管（不是自带内核）：一张灰的卡说一句。 */
function External({ what }: { what: string }) {
  const t = useTheme();
  const { claw } = useStore();
  return (
    <KitCard style={styles.pad}>
      <T v="callout" color={t.ink2}>{L(`${what}由 ${claw.name} 管，在它自己的设置里改。`, `${claw.name} manages ${what.toLowerCase()}; change it in its own settings.`)}</T>
    </KitCard>
  );
}

/** 一句没通过的话 + 对方原话（测一下的结果，卡里一行）。 */
function FailLines({ error }: { error: ProbeError | null | undefined }) {
  const t = useTheme();
  const detail = detailOf(error?.message);
  return (
    <View style={{ gap: 3 }}>
      <T v="caption" color={t.bad} style={{ fontWeight: '600' }}>{L(`没通过：${failLine(error?.kind)}`, `Didn't pass: ${failLine(error?.kind)}`)}</T>
      {detail ? <T v="caption" color={t.ink3} selectable style={[styles.note, monoFont]}>{L(`对方原话：${detail}`, `Their reply: ${detail}`)}</T> : null}
    </View>
  );
}

// —— 密钥 → 模型：接上的服务商 ——————————————————————————————————————————————————————————

type Shown = CloudRow | LocalRow | LoginRow | Extract<ProviderRow, { kind: 'custom' }> | Extract<ProviderRow, { kind: 'openclaw' }>;

/** 列在「模型」页上的：连上了的，或者配过的（存过密钥、改过地址）。没碰过的在「添加服务商」里。 */
function shownProvider(r: ProviderRow): boolean {
  switch (r.kind) {
    case 'cloud': return r.connected || r.key.present || r.url.replace(/\/$/, '') !== r.defaultUrl.replace(/\/$/, '');
    case 'local': return r.detected || r.configured;
    case 'login': return r.role === 'chat' && (r.ready || !!r.connected || !!r.needsReconnect);
    default: return true;
  }
}

function sourceWord(source: string | null): string {
  switch (source) {
    case 'app': return L('在 app 里存的', 'Saved in the app');
    case 'env': return L('服务器的环境变量', "Server's environment");
    case 'env_file': return L('.env 文件', '.env file');
    case 'config': return 'server.json';
    default: return '';
  }
}

/** 一家的第二行：云端 · 国际站 / 本机 · 127.0.0.1:11434 / 已登录的工具 · 花你自己的套餐。 */
function lineOf(r: Shown): string {
  if (r.kind === 'cloud') {
    const region = r.regions.find((g) => g.id === r.region)?.label;
    return `${L('云端', 'Cloud')} · ${region ?? (r.region === 'custom' ? L('自己改的地址', 'custom address') : L('按用量付费', 'pay as you go'))}`;
  }
  if (r.kind === 'local') return `${L('本机', 'Local')} · ${r.url.replace(/^https?:\/\//, '').replace(/\/v1$/, '')}`;
  if (r.kind === 'login') return L('已登录的工具 · 花你自己的套餐', 'Signed-in tool · uses your own plan');
  if (r.kind === 'custom') return `${L('自己填的地址', 'Your own address')} · ${r.url.replace(/^https?:\/\//, '')}`;
  return L('OpenClaw 里配的 · 在那里改', 'Set in OpenClaw · change it there');
}

/** 右上角的状态：已连通 / 在跑 / 已登录 / 没连通。 */
function stateOf(r: Shown, test?: TestResult): { text: string; tone: 'good' | 'warn' } {
  if (r.kind === 'login' && r.id === 'kimi') {
    if (test && !test.ok) return { text: L('没连通', 'Not connected'), tone: 'warn' };
    if (test?.ok || r.ready) return { text: L('已保存', 'Saved'), tone: 'good' };
    return { text: L('没贴密钥', 'No key'), tone: 'warn' };
  }
  const ok = test ? test.ok : r.kind === 'local' ? r.detected : r.kind === 'login' ? r.ready : r.connected;
  if (!ok) return { text: L('没连通', 'Not connected'), tone: 'warn' };
  if (r.kind === 'local') return { text: L('在跑', 'Running'), tone: 'good' };
  if (r.kind === 'login') return { text: r.id === 'chatgpt' ? L('已连接', 'Connected') : L('已登录', 'Signed in'), tone: 'good' };
  return { text: L('已连通', 'Connected'), tone: 'good' };
}

/** 还指着这一家的用途（删之前先提醒换一个）。 */
function usesOf(pid: string, pv: Purposes | null): string[] {
  if (!pv) return [];
  const out: string[] = [];
  const on = (v: unknown) => typeof v === 'string' && v.startsWith(`${pid}/`);
  for (const p of pv.purposes) {
    const parts = (p as { parts?: Record<string, { value?: unknown }> }).parts;
    if (p.id === 'embeddings') {
      const cur = p.current as { id?: string } | null;
      if (cur?.id && pv.choices.embeddings.find((c) => c.id === cur.id)?.provider === pid) out.push(p.label);
    } else if (parts) {
      if (Object.values(parts).some((x) => on(x?.value))) out.push(p.label);
    } else if (on(p.current)) out.push(p.label);
  }
  return out;
}

function ProviderCard({ r, test, busy, onTest, onKey, onDelete }: {
  r: Shown; test?: TestResult; busy: boolean; onTest: () => void; onKey?: () => void; onDelete?: () => void;
}) {
  const t = useTheme();
  const st = stateOf(r, test);
  const key = r.kind === 'cloud' ? r.key : r.kind === 'custom' ? r.key : r.kind === 'login' && r.id === 'kimi' ? r.key ?? null : null;
  const fixed = !!key?.present && (key.source === 'env' || key.source === 'env_file' || key.source === 'config');
  const listed = r.kind === 'cloud' || r.kind === 'custom' || r.kind === 'openclaw' ? r.models : null;
  return (
    <KitCard style={{ marginBottom: space.md }}>
      <View style={styles.cardHead}>
        <Tile size={32} mono={monoFor(r)} />
        <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
          <T v="headline" numberOfLines={1}>{shortName(r)}</T>
          <T v="caption" color={t.ink3} numberOfLines={1} style={{ fontWeight: '400' }}>{lineOf(r)}</T>
        </View>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 5 }}>
          <View style={{ width: 7, height: 7, borderRadius: 4, backgroundColor: st.tone === 'good' ? t.good : t.warn }} />
          <T v="caption" color={st.tone === 'good' ? t.good : t.warn} style={{ fontWeight: '600' }}>{st.text}</T>
        </View>
      </View>
      {r.kind === 'cloud' || r.kind === 'custom' ? (
        <>
          <KitRow title={L('密钥', 'Key')} value={key?.present ? key.hint : r.kind === 'custom' && r.keyless ? L('不用密钥', 'No key needed') : L('没有', 'None')}
            sub={key?.present ? [sourceWord(key.source), dayOf(key.savedAt)].filter(Boolean).join(' · ') : null}
            tone={key?.present || (r.kind === 'custom' && r.keyless) ? undefined : 'warn'} />
          <KitRow title={L('上次测试', 'Last test')} value={listed?.count ? L(`能用 ${listed.count} 个型号`, `${plural(listed.count, 'model')} work${listed.count === 1 ? 's' : ''}`) : L('还没测过', 'Not tested yet')}
            sub={listed?.count ? dayOf(listed.at) || null : null} tone={listed?.count ? undefined : 'muted'} />
        </>
      ) : null}
      {r.kind === 'local' ? (
        <KitRow title={L('型号', 'Models')} sub={r.models.length ? r.models.join(L('、', ', ')) : L('没探到装着的型号', 'No models found')} />
      ) : null}
      {r.kind === 'login' && r.id === 'kimi' ? (
        <KitRow title={L('密钥', 'Key')} value={r.key?.present ? (r.key.hint || L('已保存', 'Saved')) : L('没有', 'None')}
          sub={r.key?.present ? [sourceWord(r.key.source), dayOf(r.key.savedAt)].filter(Boolean).join(' · ') || null : null}
          tone={r.key?.present ? undefined : 'warn'} />
      ) : null}
      {r.kind === 'login' && r.id !== 'kimi' ? (
        <KitRow title={r.id === 'chatgpt' ? L('套餐', 'Plan') : L('登录', 'Sign-in')} value={r.plan || r.billing || (r.ready ? L('能用', 'Ready') : L('要重新登录', 'Sign in again'))} />
      ) : null}
      {test ? (
        <>
          <KitSep />
          <View style={styles.pad}>
            {test.ok ? <OkLine text={L(`通了 · ${test.count} 个型号`, `Works · ${plural(test.count, 'model')}`)} /> : <FailLines error={test.error} />}
          </View>
        </>
      ) : null}
      {r.kind !== 'openclaw' ? (
        <>
          <KitSep />
          <View style={styles.buttons}>
            <View style={{ flex: 1 }}><QuietButton label={L('测试', 'Test')} onPress={onTest} busy={busy} /></View>
            {onKey ? <View style={{ flex: 1 }}><QuietButton label={r.kind === 'login' ? (r.id === 'kimi' ? L('设置', 'Settings') : L('连接', 'Connect')) : L('换密钥', 'New key')} onPress={onKey} /></View> : null}
            {onDelete && !fixed ? <View style={{ flex: 1 }}><QuietButton label={L('删除', 'Delete')} onPress={onDelete} /></View> : null}
          </View>
          {fixed ? <T v="caption" color={t.ink3} style={[styles.note, { paddingHorizontal: space.lg, paddingBottom: space.md }]}>{L('要去服务器上改，这里删不了', "Change it on the server; it can't be deleted here")}</T> : null}
        </>
      ) : null}
    </KitCard>
  );
}

export function SettingsProvidersScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const sheet = useSheet();
  const [data, setData] = useState<ProvidersList | null>(null);
  const [pv, setPv] = useState<Purposes | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tests, setTests] = useState<Record<string, TestResult>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const load = useCallback(() => Promise.all([setupApi.providers(), setupApi.purposes(false).catch(() => null)])
    .then(([p, v]) => { setData(p); setPv(v); setErr(null); }).catch((e) => setErr(errText(e))), []);
  useFocusEffect(useCallback(() => { load(); }, [load]));
  const rows = (data?.providers ?? []).filter(shownProvider) as Shown[];
  const add = () => nav.navigate('Setup', { from: 'settings', returnTo: 'SettingsProviders' });
  const test = async (r: Shown) => {
    setBusy(r.id);
    try {
      const res = await setupApi.test(r.id, {});
      setTests((x) => ({ ...x, [r.id]: res }));
      load();
    } catch (e) { showError(L('测试失败', 'Test failed'), e); } finally { setBusy(null); }
  };
  const remove = (r: Shown) => {
    const uses = usesOf(r.id, pv);
    sheet.open({
      title: L(`删除 ${shortName(r)}？`, `Delete ${shortName(r)}?`),
      content: (close) => (
        <View style={{ gap: space.md }}>
          <T v="callout" color={t.ink2}>{r.kind === 'custom' ? L('删掉这个地址和在 app 里存的密钥。', 'Removes this address and the key saved in the app.')
            : r.kind === 'local' ? L('删掉在 app 里存的地址（本机的服务不动）。', 'Removes the address saved in the app (the local service itself is untouched).')
              : L('删掉在 app 里存的密钥（服务器上别处的不动）。', 'Removes the key saved in the app (nothing else on the server).')}</T>
          {uses.length ? (
            <KitCard style={styles.pad}>
              <T v="callout" color={t.warn}>{L(`${uses.join('、')}还在用它：先在「按用途选模型」里换一个，不然那几样会用不了。`,
                `${uses.join(', ')} still ${uses.length === 1 ? 'uses' : 'use'} it: switch under Models by purpose first, or ${uses.length === 1 ? 'it' : 'they'} will stop working.`)}</T>
            </KitCard>
          ) : null}
          {uses.length ? <PrimaryButton label={L('去换一个', 'Switch first')} onPress={() => { close(); nav.navigate('Models'); }} /> : null}
          <QuietButton label={L('删除', 'Delete')} onPress={async () => {
            close();
            try { await setupApi.remove(r.id); setTests((x) => { const n = { ...x }; delete n[r.id]; return n; }); load(); } catch (e) { showError(L('删除失败', "Couldn't delete"), e); }
          }} />
        </View>
      ),
    });
  };
  const keyOf = (r: Shown) => (r.kind === 'cloud' || r.kind === 'custom' || r.kind === 'login'
    ? () => nav.navigate('SetupProvider', { id: r.id, kind: r.kind, from: 'settings', returnTo: 'SettingsProviders' }) : undefined);
  const deletable = (r: Shown) => (r.kind === 'cloud' && r.key.present) || r.kind === 'custom' || (r.kind === 'local' && r.configured)
    || (r.kind === 'login' && r.id === 'kimi' && !!r.key?.present);
  return (
    <Page title={L('模型', 'Models')} onBack={() => nav.goBack()} onRefresh={load}
      right={<RoundButton onPress={add} label={L('添加', 'Add')}><Plus size={20} color={t.ink} /></RoundButton>}>
      <T v="caption" color={t.ink3} style={[styles.note, { marginBottom: space.lg, paddingHorizontal: space.xs }]}>
        {L('接上的服务商。密钥原文 app 拿不到，只显示有没有、从哪来、末四位、哪天存的。', "Connected providers. The app never gets a key itself: only whether there is one, where it's from, its last four characters and when it was saved.")}
      </T>
      {!data ? <LoadState error={err} onRetry={load} /> : (
        <>
          {rows.length ? rows.map((r) => (
            <ProviderCard key={`${r.kind}:${r.id}`} r={r} test={tests[r.id]} busy={busy === r.id} onTest={() => test(r)} onKey={keyOf(r)}
              onDelete={deletable(r) ? () => remove(r) : undefined} />
          )) : (
            <KitCard style={styles.pad}><T v="callout" color={t.ink2}>{L('还没有接上任何服务商。', 'No provider is connected yet.')}</T></KitCard>
          )}
          <KitCard style={{ marginTop: space.sm }}>
            <KitRow first icon={<Plus size={20} color={t.gold} />} title={L('添加服务商', 'Add a provider')} onPress={add} />
          </KitCard>
        </>
      )}
    </Page>
  );
}

// —— 密钥 → 搜索 ——————————————————————————————————————————————————————————————————————

export function SettingsSearchScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { claw } = useStore();
  const [view, setView] = useState<SearchView | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [test, setTest] = useState<{ busy?: boolean; ok?: boolean; results?: number; error?: ProbeError | null } | null>(null);
  const load = useCallback(() => setupApi.search().then((v) => { setView(v); setErr(null); }).catch((e) => setErr(errText(e))), []);
  useFocusEffect(useCallback(() => { load(); }, [load]));
  const cur = view?.providers.find((r) => r.id === view.provider) ?? null;
  const pick = async (id: string) => {
    const row = view?.providers.find((r) => r.id === id);
    if (!row) return;
    if (searchNeedOf(row)) { nav.navigate('SetupSearchKey', { id, from: 'settings', returnTo: 'SettingsSearch' }); return; }
    try { setView(await setupApi.setSearch({ enabled: true, provider: id })); setTest(null); } catch (e) { showError(L('保存失败', "Couldn't save"), e); }
  };
  const off = async () => { try { setView(await setupApi.setSearch({ enabled: false })); setTest(null); } catch (e) { showError(L('保存失败', "Couldn't save"), e); } };
  const run = async () => {
    if (!cur) return;
    setTest({ busy: true });
    try { const r = await setupApi.testSearch(cur.id); setTest({ ok: r.ok, results: r.results, error: r.error }); } catch (e) { setTest(null); showError(L('测试失败', 'Test failed'), e); }
  };
  return (
    <Page title={L('搜索', 'Search')} onBack={() => nav.goBack()} onRefresh={load}>
      {claw.kind !== 'builtin' ? <External what={L('上网搜索', 'Web search')} /> : !view ? <LoadState error={err} onRetry={load} /> : (
        <>
          <KitCard>
            <KitRow first title={L('现在用', 'In use')} value={cur ? cur.name : view.off ? L('关着', 'Off') : L('没设', 'Not set')} tone={cur ? undefined : 'muted'} />
            {test ? (
              <>
                <KitSep />
                <View style={styles.pad}>
                  {test.busy ? <View style={styles.inline}><Spinner size={13} color={t.ink3} /><T v="caption" color={t.ink3}>{L('正在测试…', 'Testing…')}</T></View>
                    : test.ok ? <OkLine text={L(`搜得到 · ${test.results ?? 0} 条结果`, `Works · ${test.results ?? 0} results`)} /> : <FailLines error={test.error} />}
                </View>
              </>
            ) : null}
            {cur ? (
              <>
                <KitSep />
                <View style={styles.buttons}>
                  <View style={{ flex: 1 }}><QuietButton label={L('测试', 'Test')} onPress={run} busy={!!test?.busy} /></View>
                  {!cur.keyless || cur.selfHosted ? <View style={{ flex: 1 }}><QuietButton label={cur.selfHosted ? L('换地址', 'New address') : L('换密钥', 'New key')}
                    onPress={() => nav.navigate('SetupSearchKey', { id: cur.id, from: 'settings', returnTo: 'SettingsSearch' })} /></View> : null}
                  <View style={{ flex: 1 }}><QuietButton label={L('关掉', 'Turn off')} onPress={off} /></View>
                </View>
              </>
            ) : null}
          </KitCard>
          <SearchChooser view={view} selected={view.provider} onSelect={pick} />
        </>
      )}
    </Page>
  );
}

// —— 密钥 → 向量（记忆和向量） ————————————————————————————————————————————————————————————

export function SettingsMemoryScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { claw } = useStore();
  const builtin = claw.kind === 'builtin';
  const [view, setView] = useState<MemoryView | null>(null);
  const [choices, setChoices] = useState<EmbeddingChoice[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [pick, setPick] = useState<ReturnType<typeof memoryInitial> | null>(null);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ text: string; ok: boolean; probe?: ProbeError | null } | null>(null);
  const [test, setTest] = useState<{ busy?: boolean; ok?: boolean; dims?: number | null; error?: ProbeError | null } | null>(null);
  const load = useCallback(() => Promise.all([setupApi.memory(), builtin ? setupApi.purposes(true).catch(() => null) : Promise.resolve(null)]).then(([m, pv]) => {
    setView(m);
    setChoices(pv?.choices.embeddings ?? []);
    setPick(memoryInitial(m));
    setErr(null);
  }).catch((e) => setErr(errText(e))), [builtin]);
  useFocusEffect(useCallback(() => { load(); }, [load]));
  const base = view ? memoryInitial(view) : null;
  const changed = !!pick && !!base && (pick.storage !== base.storage || pick.path.trim() !== base.path || pick.embed !== base.embed || (pick.embed === 'provider' && pick.provider !== base.provider && !!pick.provider));
  const set = (patch: Partial<ReturnType<typeof memoryInitial>>) => { setMsg(null); setPick((p) => (p ? { ...p, ...patch } : p)); };
  const save = async () => {
    if (!view || !pick) return;
    setSaving(true);
    setMsg(null);
    try {
      const r = await saveMemory(view, choices, pick, builtin);
      if (r.error) setMsg({ text: r.error, ok: false, probe: r.probe });
      else { setMsg({ text: L('已保存', 'Saved'), ok: true }); setTest(null); await load(); }
    } catch (e) { showError(L('保存失败', "Couldn't save"), e); } finally { setSaving(false); }
  };
  const cur = view?.current ?? null;
  const run = async () => {
    if (!cur || cur.id === 'custom') return;
    setTest({ busy: true });
    try { const r = await setupApi.testEmbeddings({ id: cur.id, model: cur.model }); setTest({ ok: r.ok, dims: r.dims, error: r.error }); } catch (e) { setTest(null); showError(L('测试失败', 'Test failed'), e); }
  };
  const st = view?.embeddings as { vectors?: number; pending?: number; lastError?: string | null } | undefined;
  const tree = view?.tree;
  const where = !tree ? '' : tree.selected === 'script' ? L('你自己的世界树脚本', 'Your own memory tree script') : !tree.initialized ? L('还没建', 'Not set up yet')
    : tree.storage === 'markdown' ? (tree.path ?? L('一个 Markdown 文件夹', 'A Markdown folder')) : L('这台电脑', 'This computer');
  return (
    <Page title={L('记忆和向量', 'Memory and embeddings')} onBack={() => nav.goBack()} onRefresh={load}>
      {!view || !pick ? <LoadState error={err} onRetry={load} /> : (
        <>
          <KitCard>
            <KitRow first title={L('世界树', 'Memory tree')} value={where} sub={tree?.memories != null ? L(`${tree.memories} 条记忆`, `${tree.memories} memories`) : null} />
            {builtin ? (
              <KitRow title={L('按意思搜', 'Search by meaning')} value={cur ? embedName(cur, choices) : L('没开', 'Off')} tone={cur ? undefined : 'muted'}
                sub={cur ? [st?.vectors != null ? L(`${st.vectors} 个向量`, `${st.vectors} vectors`) : null, st?.pending ? L(`${st.pending} 条待补`, `${st.pending} waiting`) : null,
                  st?.lastError ? L(`上次出错：${st.lastError}`, `Last error: ${st.lastError}`) : null].filter(Boolean).join(' · ') || null : L('只按关键词找', 'Keywords only')} />
            ) : null}
            {test ? (
              <>
                <KitSep />
                <View style={styles.pad}>
                  {test.busy ? <View style={styles.inline}><Spinner size={13} color={t.ink3} /><T v="caption" color={t.ink3}>{L('正在测试…', 'Testing…')}</T></View>
                    : test.ok ? <OkLine text={L(`通了 · ${test.dims ?? '?'} 维`, `Works · ${test.dims ?? '?'} dimensions`)} /> : <FailLines error={test.error} />}
                </View>
              </>
            ) : null}
            {builtin && cur && cur.id !== 'custom' ? (
              <>
                <KitSep />
                <View style={styles.buttons}><View style={{ flex: 1 }}><QuietButton label={L('测试', 'Test')} onPress={run} busy={!!test?.busy} /></View></View>
              </>
            ) : null}
          </KitCard>
          {builtin ? (
            <MemoryChooser view={view} choices={choices} storage={pick.storage} path={pick.path} embed={pick.embed} provider={pick.provider}
              onStorage={(s) => set({ storage: s })} onPath={(p) => set({ path: p })} onEmbed={(e: EmbedPick) => set({ embed: e })} onProvider={(id) => set({ provider: id })} />
          ) : (
            <>
              <MemoryChooser view={view} choices={[]} storage={pick.storage} path={pick.path} embed="off" provider={null} embeddings={false}
                onStorage={(s) => set({ storage: s })} onPath={(p) => set({ path: p })} onEmbed={() => {}} onProvider={() => {}} />
              <View style={{ marginTop: space.lg }}><External what={L('按意思搜', 'Search by meaning')} /></View>
            </>
          )}
          {msg ? <T v="callout" color={msg.ok ? t.good : t.bad} style={{ marginTop: space.lg, paddingHorizontal: space.xs }}>{msg.text}</T> : null}
          {msg?.probe && detailOf(msg.probe.message) ? <T v="caption" color={t.ink3} selectable style={[styles.note, monoFont, { paddingHorizontal: space.xs }]}>{L(`对方原话：${detailOf(msg.probe.message)}`, `Their reply: ${detailOf(msg.probe.message)}`)}</T> : null}
          <View style={{ marginTop: space.xl }}><PrimaryButton label={saving ? L('正在保存…', 'Saving…') : L('保存', 'Save')} busy={saving} disabled={!changed} onPress={save} /></View>
        </>
      )}
    </Page>
  );
}

// —— 密钥 → 用量（最近 30 天） ——————————————————————————————————————————————————————————

/** 美元：0 → $0，不到一分钱的多写几位。 */
export function money(v: number): string {
  if (!v) return '$0';
  return v < 0.01 ? `$${v.toFixed(4)}` : `$${v.toFixed(2)}`;
}

const PURPOSE_NAMES: Record<string, () => string> = {
  chat: () => L('主对话', 'Main chat'), background: () => L('后台和定时', 'Background and scheduled'), outsider: () => L('外人回合（名片 Agent、Doorman）', "Other people's turns (card agent, Doorman)"),
  api: () => L('对外接口', 'API'), cyber: () => L('赛博', 'Cyber'), claw: () => L('OpenClaw 的回合', 'OpenClaw turns'), other: () => L('别的', 'Other'),
};

/** 一行的钱：算了钱的写金额；只有套餐的写「套餐 N 轮」；价目里没有的写「没算钱」。 */
function costText(a: UsageAcc): string {
  const parts: string[] = [];
  if (a.cost || (!a.planTurns && !a.unpricedTurns)) parts.push(money(a.cost));
  if (a.planTurns) parts.push(L(`套餐 ${a.planTurns} 轮`, plural(a.planTurns, 'plan turn')));
  if (a.unpricedTurns) parts.push(L('没算钱', 'not priced'));
  return parts.join(' · ');
}

const tokens = (a: UsageAcc) => a.in + a.out + a.cacheRead + a.cacheWrite;
/** 英文的单复数（中文不用）：1 turn、2 turns。 */
export const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const num = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(n));

export function SettingsUsageScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const [u, setU] = useState<Usage | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => setupApi.usage(30).then((x) => { setU(x); setErr(null); }).catch((e) => setErr(errText(e))), []);
  useFocusEffect(useCallback(() => { load(); }, [load]));
  const emb = u?.embeddings.total;
  const total = (u?.total.cost ?? 0) + (emb?.cost ?? 0);
  return (
    <Page title={L('用量', 'Usage')} onBack={() => nav.goBack()} onRefresh={load}>
      {!u ? <LoadState error={err} onRetry={load} /> : (
        <>
          <KitCard style={styles.pad}>
            <StatRow items={[
              { label: L('最近 30 天，一共', 'Last 30 days, in all'), value: money(total) },
              { label: L('套餐，不计钱', 'On your plan, not billed'), value: L(`${u.total.planTurns} 轮`, plural(u.total.planTurns, 'turn')) },
            ]} />
            <T v="caption" color={t.ink3} style={[styles.note, { marginTop: space.md }]}>
              {[L(`${u.since} 到 ${u.until}，${u.total.turns} 轮`, `${u.since} to ${u.until}, ${plural(u.total.turns, 'turn')}`),
                u.total.unpricedTurns ? L(`价目里没有的 ${u.total.unpricedTurns} 轮没算钱`, `${plural(u.total.unpricedTurns, 'turn')} not priced`) : null,
                emb?.requests ? L(`向量 ${emb.requests} 个请求 ${money(emb.cost)}`, `embeddings: ${plural(emb.requests, 'request')}, ${money(emb.cost)}`) : null].filter(Boolean).join(L('；', '; '))}
            </T>
          </KitCard>
          {!u.total.turns && !emb?.requests ? (
            <KitCard style={[styles.pad, { marginTop: space.md }]}><T v="callout" color={t.ink2}>{L('最近 30 天还没有用量。', 'No usage in the last 30 days.')}</T></KitCard>
          ) : null}
          {u.byPurpose.length ? (
            <>
              <GroupHead title={L('按用途', 'By purpose')} />
              <KitCard>
                {u.byPurpose.map((p, i) => (
                  <KitRow key={p.purpose} first={i === 0} title={PURPOSE_NAMES[p.purpose]?.() ?? p.purpose} value={costText(p)}
                    sub={L(`${p.turns} 轮 · ${num(tokens(p))} token`, `${plural(p.turns, 'turn')} · ${num(tokens(p))} tokens`)} />
                ))}
              </KitCard>
            </>
          ) : null}
          {u.byModel.length ? (
            <>
              <GroupHead title={L('按模型', 'By model')} />
              <KitCard>
                {u.byModel.map((m, i) => (
                  <KitRow key={m.model} first={i === 0} title={m.model} mono value={m.billing === 'plan' ? L('套餐，不计钱', 'Plan, not billed') : costText(m)}
                    sub={L(`${m.turns} 轮 · ${num(tokens(m))} token`, `${plural(m.turns, 'turn')} · ${num(tokens(m))} tokens`)} />
                ))}
              </KitCard>
            </>
          ) : null}
          {u.embeddings.byModel.length ? (
            <>
              <GroupHead title={L('向量（按意思搜）', 'Embeddings (search by meaning)')} />
              <KitCard>
                {u.embeddings.byModel.map((m, i) => (
                  <KitRow key={m.model} first={i === 0} title={m.model} mono value={m.billing === 'unpriced' ? L('没算钱', 'Not priced') : money(m.cost)}
                    sub={L(`${m.requests} 个请求 · ${num(m.tokens)} token`, `${plural(m.requests, 'request')} · ${num(m.tokens)} tokens`)} />
                ))}
              </KitCard>
            </>
          ) : null}
        </>
      )}
    </Page>
  );
}

// —— 连接 → 手（只读） ——————————————————————————————————————————————————————————————————

export function SettingsHandsScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { claw } = useStore();
  const [agents, setAgents] = useState<HandsAgent[] | null>(null);
  const [engine, setEngine] = useState<EngineSecurity | null>(null);
  const [search, setSearch] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [sel, setSel] = useState('main');
  const builtin = claw.kind === 'builtin';
  const load = useCallback(() => {
    if (!builtin) return Promise.resolve();
    return Promise.all([setupApi.overview(), setupApi.hands().catch(() => null), setupApi.search().catch(() => null)]).then(([o, e, s]: [Overview, EngineSecurity | null, SearchView | null]) => {
      setAgents(o.layers.hands.agents ?? []);
      setEngine(e);
      setSearch(s?.provider ? s.providers.find((x) => x.id === s.provider)?.name ?? s.provider : null);
      setErr(null);
    }).catch((e) => setErr(errText(e)));
  }, [builtin]);
  useFocusEffect(useCallback(() => { load(); }, [load]));
  const a = agents?.find((x) => x.id === sel) ?? agents?.[0] ?? null;
  const policy = a ? engine?.hostExec.find((p) => p.agent === a.id) ?? null : null;
  const box = a ? engine?.sandbox.find((s) => s.agent === a.id) ?? null : null;
  const onOff = (on: boolean) => (on ? L('开', 'On') : L('关', 'Off'));
  const missLine = (p: typeof policy) => (!p ? L('默认全关 · 名单外的先问你', 'Off by default · anything else asks you first')
    : p.security === 'full' ? L('不看放行名单：每条都直接跑，危险的才问', 'No allowlist: every command runs; only risky ones ask')
      : p.ask === 'always' ? L('每一条都先问你', 'Every command asks you first') : p.ask === 'off' ? L('名单外的直接不跑', 'Anything else is refused')
        : L('名单外的先问你', 'Anything else asks you first'));
  return (
    <Page title={L('手', 'Hands')} onBack={() => nav.goBack()} onRefresh={load}>
      {!builtin ? <External what={L('能动手做的事', 'What Agents can do')} /> : !agents ? <LoadState error={err} onRetry={load} /> : !a ? (
        <KitCard style={styles.pad}><T v="callout" color={t.ink2}>{L('还没有 Agent。', 'No Agents yet.')}</T></KitCard>
      ) : (
        <>
          {agents.length > 1 ? (
            agents.length <= 3 ? <Segmented options={agents.map((x) => ({ value: x.id, label: x.name }))} value={a.id} onChange={setSel} /> : (
              <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: space.sm }}>
                {agents.map((x) => (
                  <View key={x.id}><QuietPill label={x.name} on={x.id === a.id} onPress={() => setSel(x.id)} /></View>
                ))}
              </ScrollView>
            )
          ) : null}
          <T v="caption" color={t.ink3} style={[styles.note, { marginTop: space.md, marginBottom: space.md, paddingHorizontal: space.xs }]}>
            {L(`${agentName()} 能动手做的事。改动先给你看，确认后才生效。`, `What ${agentName()} can do with its hands. Changes are shown to you first and only take effect once you confirm.`)}
          </T>
          <KitCard>
            <KitRow first title={L('文件', 'Files')} sub={L('自己的工作区 · 写之前留旧版本', 'Its own workspace · keeps the old version before writing')} value={onOff(a.files)} tone={a.files ? undefined : 'muted'} />
            <KitRow title={L('网页', 'Web')} value={onOff(a.webFetch || a.webSearch)} tone={a.webFetch || a.webSearch ? undefined : 'muted'}
              sub={[a.webSearch ? L(`搜索用 ${search ?? '—'}`, `Search uses ${search ?? '—'}`) : L('不能搜索', 'No search'), a.webFetch ? L('能打开网址', 'Can open links') : L('不能打开网址', "Can't open links")].join(' · ')} />
            <KitRow title={L('沙箱和浏览器', 'Sandbox and browser')} value={onOff(a.sandbox)} tone={a.sandbox ? undefined : 'muted'}
              sub={a.sandbox ? [L('命令在沙箱里跑', 'Commands run in the sandbox'), a.browser ? L('带浏览器', 'with a browser') : null,
                box?.access === 'ro' ? L('工作区只读', 'workspace read-only') : box?.access === 'rw' ? L('能写工作区', 'workspace writable') : null].filter(Boolean).join(' · ')
                : L('没有沙箱（服务器上没装，或者这个 Agent 不给）', 'No sandbox (not installed on the server, or not for this Agent)')} />
            <KitRow title={L('本机命令', 'Commands on the server')} sub={missLine(policy)} value={policy ? L(`放行 ${policy.allow.length} 条`, `${policy.allow.length} allowed`) : L('关', 'Off')}
              tone={policy?.security === 'full' ? 'warn' : policy ? undefined : 'muted'} />
          </KitCard>
          {policy && policy.allow.length ? (
            <>
              <GroupHead title={L('放行名单', 'Allowlist')} note={L('这几条直接跑，其余的出一张卡', 'These run right away; anything else gets a card')} />
              <KitCard>
                {policy.allow.map((cmd, i) => <KitRow key={`${i}:${cmd}`} first={i === 0} title={cmd} mono />)}
              </KitCard>
            </>
          ) : null}
          <T v="caption" color={t.ink3} style={[styles.note, { marginTop: space.lg, paddingHorizontal: space.xs }]}>
            {L('名单外的命令出一张卡：「这一次」或「以后都放行」。危险的命令每次都问。', 'A command off the list gets a card: "Just this once" or "Always allow". Risky commands ask every time.')}
          </T>
          {engine === null ? (
            <View style={{ marginTop: space.md }}><GoldLink label={L('看安全页', 'See Security')} onPress={() => nav.navigate('Security')} /></View>
          ) : null}
        </>
      )}
    </Page>
  );
}

/** Agent 多的时候顶上的一排小胶囊（Segmented 放不下）。 */
function QuietPill({ label, on, onPress }: { label: string; on: boolean; onPress: () => void }) {
  const t = useTheme();
  return (
    <T v="callout" onPress={onPress} accessibilityRole="tab" accessibilityState={{ selected: on }}
      style={{ paddingHorizontal: space.md, paddingVertical: 7, borderRadius: 999, overflow: 'hidden', backgroundColor: on ? t.surface : t.surface2, color: on ? t.ink : t.ink2, fontWeight: '600' }}>
      {label}
    </T>
  );
}

const styles = StyleSheet.create({
  page: { paddingHorizontal: space.lg, paddingTop: space.sm, paddingBottom: space.xxl },
  pad: { paddingHorizontal: space.lg, paddingVertical: space.md },
  note: { fontWeight: '400', lineHeight: 17 },
  cardHead: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingHorizontal: space.lg, paddingTop: space.md, paddingBottom: space.sm },
  buttons: { flexDirection: 'row', gap: space.sm, paddingHorizontal: space.lg, paddingVertical: space.md },
  inline: { flexDirection: 'row', alignItems: 'center', gap: space.sm },
});
