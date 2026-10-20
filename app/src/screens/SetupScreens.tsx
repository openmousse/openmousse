// 第一次打开的三步 + 设好了（2026-10-19，第 9 批；设计指挥部 ① 画布第 18 张，第三版，主人认了）。
// 顺序：登录 → 连接服务器（配对在最前面）→ ① 选一个模型（点一家 → ② 填密钥、测试）→ ③ 上网搜索 → ④ 记忆放在哪 → ⑤ 设好了 →
// 好友怎么称呼你（MyNameScreen，服务器已有名字就自己跳过）→ 对话页。原来的「连接常用服务」（StarterScreen）挪进了设置的「连接 → 授权登录」。
// 只在 claw 是 builtin、/api/health 的 no_model（= /api/setup 的 needsSetup）为 true 时走；OpenClaw、Hermes、别的 claw 这几层归它们自己管，跳过。
// 已经在用的安装：no_model 为 true 才进来（SetupGate），配好了的看不到。老服务器（features 里没有 setup）照旧走 MyName → Starter。
// 每一步都能「以后再说」：记在这台设备上（welcome.ts），设置首页照样看得到哪一层没配。
// 设置里的「添加服务商」「换密钥」用同一个选择器和同一张填密钥的页：from = settings，不画进度环、没有「以后再说」，做完回到 returnTo。
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AccessibilityInfo, Linking, Pressable, StyleSheet, View } from 'react-native';
import { useFocusEffect, useNavigation, useRoute } from '@react-navigation/native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { agentName, setAgentName } from '../brand';
import { Spinner } from '../components/ChatCards';
import { LensAvatar } from '../components/LensAvatar';
import { Check, Plus, Search } from '../components/icons';
import { KeyboardScroll } from '../components/keyboard';
import { SettingsHeader, Tile } from '../components/settings';
import { useSheet } from '../components/Sheet';
import {
  BackButton, BrandRing, Field, GoldLink, GroupHead, KeyField, KitCard, KitRow, KitSep, LaterButton, LoadState, LocalTile, OkLine, PrimaryButton,
  ProviderTile, SetupHeading, StepRing, TestFail, TileRow, detailOf, failLine, monoFont,
} from '../components/setupKit';
import { Screen, Segmented, T, showError } from '../components/ui';
import { defaultBase, getBase, persistAgentName } from '../api/base';
import { probe } from '../api/live';
import {
  isPlanRef, monoOf, namingApi, planApi, setupApi, type AutoChoice, type CloudRow, type CustomRow, type EmbeddingChoice, type LocalRow, type LoginRow,
  type MemoryView, type ModelChoice, type ProbeError, type ProviderRow, type ProvidersList, type Purposes, type SearchRow, type SearchView, type TestResult,
} from '../api/setup';
import { setServerInfo, supports } from '../api/version';
import type { AvatarConfig } from '../data/types';
import { L } from '../i18n';
import { hasRoute, navigationRef } from '../navigation';
import { useStore } from '../store';
import { space, useTheme } from '../theme';
import { setupSkipped, skipSetup } from '../welcome';

// —— 走到哪、往哪去 ——————————————————————————————————————————————————————————————————

/** 这台服务器在本机记号里的名字（「以后再说」按它分开）。网页版和服务器同源，地址留空：用页面的地址。 */
export const serverKey = () => getBase() || defaultBase() || 'here';

/** 这次打开 app 已经进过三步（第一次连上时直接进的，或者 SetupGate 带进来的）：SetupGate 不再自己跳进来。 */
let entered = false;

type Route = { name: string; params?: object };

/** 连上服务器以后去哪（ConnectScreen）：自带内核、还没有能用的模型 → 三步；新的服务器 → 给助手起名字 → 好友怎么称呼你；老服务器、别的 claw 照旧（MyName → Starter）。 */
export async function routeAfterConnect(firstTime: boolean): Promise<Route> {
  const p = await probe();
  if (p.status !== 'ok') return { name: firstTime ? 'MyName' : 'Tabs' };
  setServerInfo(p.server);  // supports() 马上能用（store 的 refreshLive 稍后也会设）
  if (!p.server.features.includes('setup')) return { name: firstTime ? 'MyName' : 'Tabs' };
  // 别的 claw（OpenClaw、Hermes……）：features 不分 claw，新服务器也报 setup。照旧走：第一次 → 好友怎么称呼你 → 接上常用的，加一个 claw → 对话页
  if (p.claw.kind !== 'builtin') return { name: firstTime ? 'MyName' : 'Tabs' };
  const fresh = firstTime || p.firstRun;
  if (p.claw.kind === 'builtin' && p.noModel && !setupSkipped(serverKey(), 'models')) return { name: 'Setup', params: { fresh } };
  return fresh ? firstName(p.claw.kind === 'builtin', p.firstRun) : { name: 'Tabs' };
}

/** 第一次连上、「设好了」之后：全新的服务器（/api/health 的 first_run）先给助手起名字（服务器有 naming、自带内核才有这一屏），再去「好友怎么称呼你」（它自己会跳过）；新设备连已经在用的服务器不起名。 */
const firstName = (builtin: boolean, newServer: boolean): Route =>
  (builtin && newServer && supports('naming') && hasRoute('SetupName') ? { name: 'SetupName' } : { name: 'MyName', params: { next: 'Tabs' } });

/** 「设好了」之后：第一次连上的去起名字（只全新的服务器）、好友怎么称呼你，已经在用的回对话页（三步只在自带内核上有）。 */
const afterDone = (fresh: boolean, newServer: boolean): Route => (fresh ? firstName(true, newServer) : { name: 'Tabs' });

/**
 * 已经在用的安装：连上以后 /api/health 说 no_model（自带内核、选的模型用不了、备用链上也没有能用的），这次打开还没进过三步、
 * 这台设备上也没点过「以后再说」，人在对话页上 → 带进三步。别的时候什么都不做（不打断正在看的页）。
 */
export function SetupGate() {
  const { connected, noModel, claw, server } = useStore();
  useEffect(() => {
    if (!connected || !noModel || claw.kind !== 'builtin' || entered || !server.features.includes('setup') || setupSkipped(serverKey(), 'models')) return undefined;
    let tries = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const go = () => {
      if (entered) return;
      if (!navigationRef.isReady()) { if (tries++ < 10) timer = setTimeout(go, 400); return; }
      const root = navigationRef.getRootState();
      if (root?.routes[root.index ?? 0]?.name !== 'Tabs') return;
      entered = true;
      navigationRef.navigate('Setup', { fresh: false });
    };
    timer = setTimeout(go, 300);
    return () => { if (timer) clearTimeout(timer); };
  }, [connected, noModel, claw.kind, server]);
  return null;
}

type Mode = { fresh: boolean; settings: boolean; returnTo: string | null; params: Record<string, unknown> };

function useMode(): Mode {
  const route = useRoute<any>();
  const p = (route.params ?? {}) as Record<string, unknown>;
  return { fresh: !!p.fresh, settings: p.from === 'settings', returnTo: typeof p.returnTo === 'string' ? p.returnTo : null, params: p };
}

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e ?? ''));
const announce = (s: string) => { AccessibilityInfo.announceForAccessibility?.(s); };
const bare = (ref: string) => ref.split('/').slice(1).join('/') || ref;

/** 一页的骨架：左上角的进度环（点进一家以后前面有返回）；设置里用设置页的页头。底下是按钮。 */
function Frame({ step, onBack, settingsTitle, children, footer }: {
  step: number; onBack?: () => void; settingsTitle?: string | null; children: React.ReactNode; footer?: React.ReactNode;
}) {
  const insets = useSafeAreaInsets();
  return (
    <Screen>
      {settingsTitle ? <SettingsHeader title={settingsTitle} onBack={onBack} /> : (
        <View style={styles.top}>
          {onBack ? <BackButton onPress={onBack} /> : null}
          <StepRing step={step} />
        </View>
      )}
      <KeyboardScroll layout keyboardShouldPersistTaps="handled" contentContainerStyle={[styles.page, { paddingBottom: space.lg + insets.bottom }]}>
        {children}
        <View style={{ flex: 1, minHeight: space.xl }} />
        {footer ? <View style={{ gap: 2 }}>{footer}</View> : null}
      </KeyboardScroll>
    </Screen>
  );
}

// —— 名字、字母块 ——————————————————————————————————————————————————————————————————————

/** 稿子上的短名字（目录里的全名放不进小格）。 */
const SHORT: Record<string, () => string> = {
  openai: () => 'OpenAI', anthropic: () => 'Anthropic', google: () => 'Gemini', deepseek: () => 'DeepSeek', openrouter: () => 'OpenRouter',
  moonshot: () => 'Kimi', dashscope: () => L('通义千问', 'Qwen'), mistral: () => 'Mistral', ollama: () => 'Ollama', lmstudio: () => 'LM Studio',
  mlx: () => 'MLX', chatgpt: () => L('ChatGPT 套餐', 'ChatGPT plan'),
};
const MONO: Record<string, () => string> = {
  openrouter: () => 'OR', lmstudio: () => 'LM', dashscope: () => L('通', 'Q'), moonshot: () => 'K', 'claude-code': () => 'C', chatgpt: () => 'G',
};
export const shortName = (r: { id: string; name: string }) => SHORT[r.id]?.() ?? r.name;
export const monoFor = (r: { id: string; name: string }) => MONO[r.id]?.() ?? monoOf(shortName(r));

/** 第一屏的八家（稿子上的两排）和本机的三家。 */
const FEATURED = ['openai', 'anthropic', 'google', 'deepseek', 'openrouter', 'moonshot', 'dashscope', 'mistral'];
const LOCALS = ['ollama', 'lmstudio', 'mlx'];

/** 本机那一格的状态：已找到 / 没在跑 / 仅 Mac（这一家只有 Mac 上有、服务器又不是 Mac）。 */
export function localStatus(r: LocalRow, hostMac: boolean | undefined): { found: boolean; text: string; macOnly: boolean } {
  if (r.detected) return { found: true, text: L('已找到', 'Found'), macOnly: false };
  if (r.mac && hostMac === false) return { found: false, text: L('仅 Mac', 'Mac only'), macOnly: true };
  return { found: false, text: L('没在跑', 'Not running'), macOnly: false };
}

function padRow(items: React.ReactNode[], n: number): React.ReactNode[] {
  return [...items, ...Array.from({ length: Math.max(0, n - items.length) }, (_, i) => <View key={`pad${i}`} />)];
}

// —— ① 选一个模型（第一次打开；设置里的「添加服务商」也是它） ——————————————————————————————————————

export function SetupModelsScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const sheet = useSheet();
  const { fresh, settings, returnTo } = useMode();
  const [data, setData] = useState<ProvidersList | null>(null);
  const [chat, setChat] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { entered = true; }, []);
  const load = useCallback(() => {
    Promise.all([setupApi.providers(), setupApi.purposes(false).catch(() => null)]).then(([p, pv]) => {
      setData(p);
      const cur = pv?.purposes.find((x) => x.id === 'chat')?.current;
      setChat(typeof cur === 'string' ? cur : null);
      setErr(null);
    }).catch((e) => setErr(errText(e)));
  }, []);
  useFocusEffect(useCallback(() => { load(); }, [load]));  // 从填密钥那页回来：连没连、选中哪一家跟着变

  const rows = data?.providers ?? [];
  const cloud = rows.filter((r): r is CloudRow => r.kind === 'cloud');
  const featured = FEATURED.map((id) => cloud.find((r) => r.id === id)).filter((r): r is CloudRow => !!r);
  const local = LOCALS.map((id) => rows.find((r) => r.id === id && r.kind === 'local')).filter((r): r is LocalRow => !!r);
  const logins = rows.filter((r): r is LoginRow => r.kind === 'login' && r.role === 'chat');
  const customs = rows.filter((r): r is CustomRow => r.kind === 'custom');
  const using = chat?.split('/')[0] ?? null;
  const open = (id: string, kind: ProviderRow['kind']) => nav.navigate('SetupProvider', { id, kind, fresh, ...(settings ? { from: 'settings', returnTo } : {}) });
  const later = () => { skipSetup(serverKey(), 'models'); nav.navigate('SetupSearch', { fresh }); };
  const all = () => sheet.open({
    title: L(`全部 ${cloud.length} 家`, `All ${cloud.length}`), tall: true,
    content: (close) => (
      <KitCard>
        {cloud.map((r, i) => (
          <KitRow key={r.id} first={i === 0} icon={<Tile size={28} mono={monoFor(r)} />} title={r.name} sub={r.note}
            value={r.connected ? L('已连', 'Connected') : undefined} tone={r.connected ? 'good' : undefined}
            onPress={() => { close(); open(r.id, 'cloud'); }} />
        ))}
      </KitCard>
    ),
  });
  const tile = (r: CloudRow) => (
    <ProviderTile key={r.id} name={shortName(r)} mono={monoFor(r)} selected={using === r.id} onPress={() => open(r.id, 'cloud')}
      label={[shortName(r), r.connected ? L('已连', 'connected') : ''].filter(Boolean).join(L('，', ', '))} />
  );

  return (
    <Frame step={1} settingsTitle={settings ? L('添加服务商', 'Add a provider') : null} onBack={settings ? () => nav.goBack() : undefined}
      footer={settings ? null : <LaterButton onPress={later} />}>
      {settings ? null : (
        <SetupHeading title={L('选一个模型', 'Choose a model')}
          sub={L(`${agentName()} 用它理解和回复。用你已有的一种，以后可以再加。`, `${agentName()} uses it to understand and reply. Use one you already have; you can add more later.`)} />
      )}
      {!data ? <LoadState error={err} onRetry={load} /> : (
        <>
          <GroupHead title={L('云端服务商', 'Cloud providers')} note={L('按用量付费，记在你自己的账上', 'Pay as you go, billed to your own account')} />
          <View style={{ gap: space.sm }}>
            <TileRow>{padRow(featured.slice(0, 4).map(tile), 4)}</TileRow>
            {featured.length > 4 ? <TileRow>{padRow(featured.slice(4, 8).map(tile), 4)}</TileRow> : null}
          </View>
          <View style={styles.allLink}><GoldLink label={L(`全部 ${cloud.length} 家`, `All ${cloud.length}`)} onPress={all} /></View>

          {local.length ? (
            <>
              <GroupHead title={L('本机跑的', 'On this computer')} note={L('在这台电脑上跑：不花钱、不出门，慢一些', 'Runs on this computer: free and private, but slower')} />
              <TileRow>
                {padRow(local.map((r) => {
                  const s = localStatus(r, data.host?.mac);
                  return <LocalTile key={r.id} name={shortName(r)} found={s.found} status={s.text} selected={using === r.id} onPress={() => open(r.id, 'local')} />;
                }), 3)}
              </TileRow>
            </>
          ) : null}

          {logins.length ? (
            <>
              <GroupHead title={L('用已登录的工具', "Tools you're signed in to")} note={L('花你自己的套餐，只给你自己的回合用', 'Uses your own plan, only for your own turns')} />
              <KitCard>
                {logins.map((r, i) => (
                  <KitRow key={r.id} first={i === 0} icon={<Tile size={28} mono={monoFor(r)} />} title={shortName(r)} onPress={() => open(r.id, 'login')}
                    value={r.ready ? (r.id === 'chatgpt' ? L('已连接', 'Connected') : L('已登录', 'Signed in')) : r.needsReconnect ? L('要重连', 'Reconnect') : r.id === 'chatgpt' ? L('没连接', 'Not connected') : L('没登录', 'Not signed in')}
                    tone={r.ready ? 'good' : r.needsReconnect ? 'warn' : 'muted'} />
                ))}
              </KitCard>
            </>
          ) : null}

          <GroupHead title={L('自己填地址', 'Your own address')} note={L('兼容 OpenAI 接口的地址', 'An OpenAI-compatible address')} />
          <KitCard>
            {customs.map((r, i) => (
              <KitRow key={r.id} first={i === 0} icon={<Tile size={28} mono={monoOf(r.name)} />} title={r.name} sub={r.url} mono={false}
                value={r.connected ? L('已连', 'Connected') : L('没连通', 'Not connected')} tone={r.connected ? 'good' : 'warn'} onPress={() => open(r.id, 'custom')} />
            ))}
            <KitRow first={!customs.length} icon={<Plus size={20} color={t.gold} />} title={L('填一个地址', 'Add an address')} onPress={() => open('', 'custom')} />
          </KitCard>
        </>
      )}
    </Frame>
  );
}

// —— ② 填密钥、测试（云端）；本机的、已登录的工具、自己填的地址也是这一页 ——————————————————————————————————

type Phase = 'form' | 'testing' | 'ok' | 'fail';
/** 主对话用哪个：auto = 这一次是自动选上的（follow：名片 Agent 和 Doorman 也跟它；plan：套餐，它们要另选）。 */
type ChatPick = { ref: string; choices: ModelChoice[]; recommended: string | null; auto: 'follow' | 'plan' | null };
type Verify = { state: 'busy' | 'ok' | 'fail'; error?: ProbeError | null } | null;

const EMBED = /embed|bge|e5-|gte-|rerank/i;

function firstOf(choices: ModelChoice[], rec: string | null, provider: string): string | null {
  if (rec && rec.startsWith(`${provider}/`) && choices.some((c) => c.ref === rec)) return rec;
  return (choices.find((c) => !EMBED.test(c.model)) ?? choices[0])?.ref ?? null;
}

/** 自定义地址的 id：名字转成小写字母、数字和 -；转不出来（中文名）用 custom，和已有的撞了加 -2、-3。 */
function newCustomId(name: string, taken: Set<string>): string {
  const base = name.toLowerCase().replace(/[^a-z0-9_.-]+/g, '-').replace(/^[^a-z0-9]+|-+$/g, '').slice(0, 24) || 'custom';
  let id = base;
  for (let n = 2; taken.has(id); n += 1) id = `${base}-${n}`;
  return id;
}

/** 哪个地区：现在存的那个；地址是自己改过的（custom）或者没有地区 → 第一个。 */
const regionOf = (r: CloudRow) => (r.regions.some((g) => g.id === r.region) ? r.region : r.regions[0]?.id ?? null);
/** 没有默认地址、也不分地区的云端（Azure）：地址是用户自己的资源，要在这一页填（服务器不填就回 400）。 */
const ownAddress = (r: CloudRow) => !r.defaultUrl && !r.regions.length;

function sourceText(source: string | null): string {
  switch (source) {
    case 'app': return L('在 app 里存的', 'saved in the app');
    case 'env': return L('服务器的环境变量', "the server's environment");
    case 'env_file': return L('.env 文件', 'the .env file');
    case 'config': return 'server.json';
    default: return '';
  }
}

/** 2026-10-19T08:00:00+00:00 → 10/19（本地日期）。 */
export function dayOf(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : `${d.getMonth() + 1}/${d.getDate()}`;
}

/** 从测通的型号里换主对话用的（型号多的时候能筛）。 */
function ModelList({ choices, current, rec, onPick }: { choices: ModelChoice[]; current: string; rec: string | null; onPick: (ref: string) => void }) {
  const t = useTheme();
  const [q, setQ] = useState('');
  const shown = choices.filter((c) => !q.trim() || c.ref.toLowerCase().includes(q.trim().toLowerCase())).slice(0, 120);
  return (
    <View style={{ gap: space.md }}>
      {choices.length > 12 ? <Field value={q} onChangeText={setQ} placeholder={L('筛选型号', 'Filter models')} accessibilityLabel={L('筛选型号', 'Filter models')} /> : null}
      <KitCard>
        {shown.map((c, i) => (
          <KitRow key={c.ref} first={i === 0} title={c.model} mono value={c.ref === rec ? L('推荐', 'Recommended') : undefined}
            right={c.ref === current ? <Check size={18} color={t.gold} /> : undefined} chevron={false} onPress={() => onPick(c.ref)} />
        ))}
      </KitCard>
      {!shown.length ? <T v="callout" color={t.ink3}>{L('没有对得上的型号', 'No matching models')}</T> : null}
    </View>
  );
}

export function SetupProviderScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const sheet = useSheet();
  const { fresh, settings, returnTo, params } = useMode();
  const kind = (String(params.kind || 'cloud')) as ProviderRow['kind'];
  const [pid, setPid] = useState(String(params.id || ''));
  const [list, setList] = useState<ProvidersList | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [key, setKey] = useState('');
  const [region, setRegion] = useState<string | null>(null);
  const [regionTouched, setRegionTouched] = useState(false);  // 只有用户自己换了地区才存（地址是自己改过的，不能拿默认地区冲掉）
  const [model, setModel] = useState('');
  const [name, setName] = useState('');
  const [url, setUrl] = useState('');
  const [api, setApi] = useState<'openai' | 'anthropic'>('openai');
  const [phase, setPhase] = useState<Phase>('form');
  const [test, setTest] = useState<TestResult | null>(null);
  const [chat, setChat] = useState<ChatPick | null>(null);
  const [verify, setVerify] = useState<Verify>(null);
  const [plan, setPlan] = useState<string | null>(null);  // ChatGPT 授权进行中：授权网址
  const [pasted, setPasted] = useState('');
  const poll = useRef(0);
  useEffect(() => () => { poll.current += 1; }, []);  // 走了就不再等授权

  useEffect(() => {
    let live = true;
    setupApi.providers({ probe: kind === 'local', check: kind === 'login' }).then((p) => {
      if (!live) return;
      setList(p);
      setErr(null);
      const r = p.providers.find((x) => x.id === String(params.id || '') && x.kind === kind);
      if (r?.kind === 'cloud') {
        setRegion(regionOf(r));
        if (ownAddress(r)) setUrl(r.url);
        if (!r.canList) setModel(r.suggest.cheap || r.suggest.chat || '');
      } else if (r?.kind === 'custom') {
        setName(r.name);
        setUrl(r.url);
        setApi(r.api === 'anthropic' ? 'anthropic' : 'openai');
      }
    }).catch((e) => { if (live) setErr(errText(e)); });
    return () => { live = false; };
  }, [kind, params.id, tick]);

  const row = list?.providers.find((x) => x.id === pid && x.kind === kind) ?? null;
  const replaceRow = (r: ProviderRow) => setList((l) => (l ? { ...l, providers: l.providers.some((x) => x.id === r.id) ? l.providers.map((x) => (x.id === r.id ? r : x)) : [...l.providers, r] } : l));

  /** 主对话用哪个：已经选了就照旧（是别家的也照实显示）；还没选 → 这一家推荐的（只连了这一家时服务器给的 auto），顺手存上。 */
  const pickChat = async (provider: string, saved: AutoChoice | null): Promise<ChatPick | null> => {
    const pv: Purposes = await setupApi.purposes(kind === 'local');  // 本机的：现探一遍，探到的型号才选得上
    const choices = pv.choices.models.filter((c) => c.provider === provider);
    const p = pv.purposes.find((x) => x.id === 'chat');
    const rec = typeof p?.recommended?.value === 'string' ? p.recommended.value : null;
    const current = typeof p?.current === 'string' ? p.current : null;
    if (current) return { ref: current, choices, recommended: rec, auto: saved ? (saved.outsider ? 'follow' : 'plan') : null };
    const a = pv.auto && pv.auto.provider === provider ? pv.auto : null;
    const want = a?.chat ?? firstOf(choices, rec, provider);
    if (!want) return null;
    // 和服务器「存的时候自动选」一样：不是套餐的，名片 Agent 和 Doorman 也跟着主对话
    await setupApi.setPurposes({ chat: want, ...(a?.outsider ? { outsider: 'default' } : {}) });
    return { ref: want, choices, recommended: rec ?? want, auto: a ? (a.outsider ? 'follow' : 'plan') : isPlanRef(want) ? 'plan' : null };
  };

  /** 用一次真实回复验证模型（OpenClaw 的 onboard 也这么做）：拿主对话的型号发一句最小的请求。本机的、套餐的不在这里测。 */
  const verifyModel = async (provider: string, ref: string) => {
    if (!ref.startsWith(`${provider}/`) || isPlanRef(ref) || kind === 'local' || kind === 'login') { setVerify(null); return; }
    setVerify({ state: 'busy' });
    try {
      const r = await setupApi.test(provider, { model: bare(ref) });
      setVerify(r.ok ? { state: 'ok' } : { state: 'fail', error: r.error });
    } catch (e) {
      setVerify({ state: 'fail', error: { kind: 'bad', message: errText(e) } });
    }
  };

  const finish = async (provider: string, saved: AutoChoice | null, res: TestResult) => {
    const pick = await pickChat(provider, saved);
    setChat(pick);
    setPhase('ok');
    announce(L(`已连通，${res.count} 个型号能用`, `Connected, ${res.count} models available`));
    if (pick) verifyModel(provider, pick.ref).catch(() => {});
  };

  const failed = (res: TestResult) => {
    setPhase('fail');
    announce(L(`没通过：${failLine(res.error?.kind)}`, `Didn't pass: ${failLine(res.error?.kind)}`));
  };

  const run = async () => {
    if (phase === 'testing') return;
    setPhase('testing');
    setTest(null);
    setVerify(null);
    try {
      if (kind === 'cloud' && row?.kind === 'cloud') {
        const k = key.trim();
        const moved = regionTouched && !!region && region !== row.region && row.regions.length > 0;
        const u = ownAddress(row) ? url.trim().replace(/\/+$/, '') : '';  // 自己的资源地址：跟密钥一起测，通了一起存
        const urlMoved = !!u && u !== row.url;
        const res = await setupApi.test(row.id, { ...(k ? { key: k } : {}), ...(moved && region ? { region } : {}), ...(u ? { url: u } : {}), ...(!row.canList && model.trim() ? { model: model.trim() } : {}) });
        setTest(res);
        if (!res.ok) { failed(res); return; }
        let saved: AutoChoice | null = null;
        if (k || moved || urlMoved) {  // 通了再存：新密钥进 secrets.env，换了地区、地址写 server.json（先备份）
          const s = await setupApi.save(row.id, { ...(k ? { key: k } : {}), ...(moved && region ? { region } : {}), ...(urlMoved ? { url: u } : {}) });
          saved = s.auto;
          replaceRow(s.provider);
          setKey('');
          setRegionTouched(false);
        }
        await finish(row.id, saved, res);
      } else if (kind === 'custom') {
        const u = url.trim();
        const id = pid || newCustomId(name, new Set((list?.providers ?? []).map((x) => x.id)));
        const k = key.trim();
        const res = await setupApi.test(id, { url: u, api, ...(k ? { key: k } : {}) });
        setTest(res);
        if (!res.ok) { failed(res); return; }
        const s = await setupApi.save(id, { name: name.trim() || id, url: u, api, ...(k ? { key: k } : {}) });
        setPid(id);
        replaceRow(s.provider);
        setKey('');
        await finish(id, s.auto, res);
      } else if (row) {  // 本机的（探一遍、列它装着的模型）、已登录的工具（现查一遍）
        const res = await setupApi.test(row.id, {});
        setTest(res);
        if (!res.ok) { failed(res); return; }
        await finish(row.id, null, res);
      } else {
        setPhase('form');
      }
    } catch (e) {
      setPhase('form');
      showError(L('测试失败', 'Test failed'), e);
    }
  };

  const choose = async (ref: string) => {
    if (!chat) return;
    try {
      await setupApi.setPurposes({ chat: ref });
      setChat({ ...chat, ref, auto: null });
      verifyModel(pid, ref).catch(() => {});
    } catch (e) {
      showError(L('保存失败', "Couldn't save"), e);
    }
  };
  const pickModel = () => {
    if (!chat?.choices.length) return;
    sheet.open({
      title: L('主对话用', 'Main chat uses'), tall: true,
      content: (close) => <ModelList choices={chat.choices} current={chat.ref} rec={chat.recommended} onPick={(ref) => { close(); choose(ref); }} />,
    });
  };

  // ChatGPT 套餐：在浏览器里授权（服务器本机的浏览器会自己落到回调上；手机上把地址栏整串粘回来），这里每 2 秒看一眼
  const afterPlan = async () => {
    poll.current += 1;
    setPlan(null);
    setPasted('');
    try {
      const p = await setupApi.providers({ probe: false });
      setList(p);
      const r = p.providers.find((x) => x.id === 'chatgpt');
      if (r && r.kind === 'login' && r.ready) await run();
    } catch (e) { showError(L('无法加载', "Couldn't load"), e); }
  };
  const startPlan = async () => {
    try {
      const l = await planApi.start();
      setPlan(l.url);
      Linking.openURL(l.url).catch(() => {});
      const me = ++poll.current;
      for (;;) {
        await new Promise((ok) => setTimeout(ok, 2000));
        if (poll.current !== me) return;
        const s = await planApi.state().catch(() => null);
        if (!s || s.status === 'pending') continue;
        if (poll.current !== me) return;
        if (s.status === 'done') { await afterPlan(); return; }
        poll.current += 1;
        setPlan(null);
        showError(L('没有连上', 'Not connected'), s.error || s.status);
        return;
      }
    } catch (e) {
      setPlan(null);
      showError(L('无法开始授权', "Couldn't start signing in"), e);
    }
  };
  const submitPlan = async () => {
    try {
      await planApi.callback(pasted.trim());
      await afterPlan();
    } catch (e) { showError(L('没有连上', 'Not connected'), e); }
  };
  const cancelPlan = () => { poll.current += 1; setPlan(null); setPasted(''); planApi.cancel().catch(() => {}); };

  const back = () => nav.goBack();
  const next = () => {
    if (settings) { if (returnTo) nav.navigate(returnTo); else nav.goBack(); return; }
    nav.navigate('SetupSearch', { fresh });
  };

  // —— 画 ——
  const title = settings ? (kind === 'custom' && !pid ? L('填一个地址', 'Add an address') : row ? shortName(row) : '') : null;
  if (!list) {
    return <Frame step={1} onBack={back} settingsTitle={title}><LoadState error={err} onRetry={() => setTick((x) => x + 1)} /></Frame>;
  }
  if (!row && kind !== 'custom') {
    return <Frame step={1} onBack={back} settingsTitle={title}><LoadState error={L('没有这一家', 'No such provider')} /></Frame>;
  }

  const cloud = row?.kind === 'cloud' ? row : null;
  const local = row?.kind === 'local' ? row : null;
  const login = row?.kind === 'login' ? row : null;
  const keyInfo = cloud?.key ?? (row?.kind === 'custom' ? row.key : null);
  const keyUrl = cloud ? cloud.regions.find((g) => g.id === region)?.keyUrl || cloud.keyUrl : null;
  const lstat = local ? localStatus(local, list.host?.mac) : null;
  const needsKey = !!cloud && !key.trim() && !cloud.key.present;
  const needsModel = !!cloud && !cloud.canList && !model.trim();
  const ownUrl = !!cloud && ownAddress(cloud);
  const planWaiting = !!plan;
  const canTest = kind === 'custom' ? !!url.trim() : cloud ? !needsKey && !needsModel && !(ownUrl && !url.trim()) : !!row && !(lstat?.macOnly);
  const kindLine = cloud ? `${L('云端', 'Cloud')} · ${cloud.cost || L('按用量付费', 'Pay as you go')}`
    : local ? `${L('本机', 'Local')} · ${local.url.replace(/^https?:\/\//, '').replace(/\/v1$/, '')}`
      : login ? L('已登录的工具 · 花你自己的套餐', 'Signed-in tool · uses your own plan')
        : L('兼容 OpenAI 接口的地址', 'An OpenAI-compatible address');
  const shown = row ? shortName(row) : (name.trim() || L('自己填的地址', 'Your own address'));
  const models = (n: number) => `${n} model${n === 1 ? '' : 's'}`;
  const okText = test ? (local ? L(`在跑 · ${test.count} 个型号`, `Running · ${models(test.count)}`)
    : login ? L(`能用 · ${test.count} 个型号`, `Ready · ${models(test.count)}`)
      : L(`已连通 · ${test.count} 个型号能用`, `Connected · ${models(test.count)} available`)) : '';
  const pickedName = chat ? bare(chat.ref) : '';
  const note = !chat ? null : chat.auto === 'follow'
    ? L(`只接了这一家，主对话先用推荐的 ${pickedName}，名片 Agent 和 Doorman 也跟它；以后在「按用途选模型」里换。`,
      `Only this provider is connected, so the main chat uses the recommended ${pickedName} for now and the card agent and Doorman follow it. Change it later under Models by purpose.`)
    : chat.auto === 'plan'
      ? L(`只接了这一家，主对话先用推荐的 ${pickedName}，名片 Agent 和 Doorman 不能用套餐，需要另选。`,
        `Only this provider is connected, so the main chat uses the recommended ${pickedName} for now. The card agent and Doorman can't use a plan and need their own model.`)
      : L('以后在「按用途选模型」里换。', 'Change it later under Models by purpose.');

  let primary: React.ReactNode = null;
  if (phase === 'ok') primary = <PrimaryButton label={settings ? L('完成', 'Done') : L('下一步', 'Next')} onPress={next} />;
  else if (login && login.id === 'chatgpt' && !login.ready) {
    primary = planWaiting ? null : <PrimaryButton label="Continue with ChatGPT" onPress={startPlan} />;
  } else if (phase !== 'fail') {
    primary = <PrimaryButton label={phase === 'testing' ? L('正在测试…', 'Testing…') : L('测试', 'Test')} busy={phase === 'testing'} disabled={!canTest} onPress={run} />;
  }

  return (
    <Frame step={1} onBack={back} settingsTitle={title} footer={primary}>
      <View style={styles.provHead}>
        {row ? <Tile size={44} mono={monoFor(row)} /> : <Tile size={44}><Plus size={22} color={t.gold} /></Tile>}
        <View style={{ flex: 1, minWidth: 0, gap: 3 }}>
          <T v="title" numberOfLines={1} accessibilityRole="header">{shown}</T>
          <T v="caption" color={t.ink3} style={{ fontWeight: '400' }} numberOfLines={2}>{kindLine}</T>
        </View>
      </View>

      {cloud ? (
        <>
          {cloud.regions.length ? (
            <>
              <GroupHead title={L('地区', 'Region')} note={cloud.note} />
              <Segmented options={cloud.regions.map((g) => ({ value: g.id, label: g.label }))} value={region ?? cloud.regions[0].id}
                onChange={(v) => { setRegion(v); setRegionTouched(true); if (phase !== 'testing') setPhase('form'); }} />
            </>
          ) : null}
          {ownUrl ? (
            <>
              <GroupHead title={L('地址', 'Address')} note={cloud.note} />
              <Field mono value={url} onChangeText={(v) => { setUrl(v); if (phase !== 'testing') setPhase('form'); }} placeholder="https://example.com/v1" keyboardType="url" accessibilityLabel={L('地址', 'Address')} />
            </>
          ) : null}
          <GroupHead title={L('API 密钥', 'API key')} note={cloud.regions.length || ownUrl ? null : cloud.note} />
          <View style={{ gap: space.sm }}>
            {keyInfo?.present ? (
              <T v="caption" color={t.ink2} style={{ fontWeight: '400' }}>
                {[L(`已存 ${keyInfo.hint ?? ''}`, `Saved ${keyInfo.hint ?? ''}`), sourceText(keyInfo.source), dayOf(keyInfo.savedAt)].filter(Boolean).join(' · ')}
              </T>
            ) : null}
            <KeyField value={key} onChange={(v) => { setKey(v); if (phase === 'fail' || phase === 'ok') setPhase('form'); }} label={L('API 密钥', 'API key')}
              placeholder={keyInfo?.present ? L('换一把新的（不填就用存下的）', 'A new one (leave empty to keep the saved key)') : L('粘贴你的 API 密钥', 'Paste your API key')} />
            {keyUrl ? <GoldLink label={L(`去 ${shortName(cloud)} 拿密钥`, `Get a key from ${shortName(cloud)}`)} onPress={() => { Linking.openURL(keyUrl).catch((e) => showError(L('无法打开', "Couldn't open it"), e)); }} /> : null}
            <T v="caption" color={t.ink3} style={styles.note}>{L('密钥只存在你的服务器上。存好以后，这里只显示后四位。', 'The key is stored only on your server. Once saved, only its last four characters show here.')}</T>
          </View>
          {!cloud.canList ? (
            <>
              <GroupHead title={L('型号名', 'Model name')} note={L('这一家列不了型号：写一个带着去测', "This provider can't list its models: enter one to test with")} />
              <Field mono value={model} onChangeText={(v) => { setModel(v); if (phase !== 'testing') setPhase('form'); }} placeholder={cloud.suggest.cheap || 'model-id'} accessibilityLabel={L('型号名', 'Model name')} />
            </>
          ) : null}
        </>
      ) : null}

      {kind === 'custom' ? (
        <>
          <GroupHead title={L('名字', 'Name')} />
          <Field value={name} onChangeText={setName} placeholder={L('公司代理、自己的服务……', 'Company proxy, your own server…')} accessibilityLabel={L('名字', 'Name')} maxLength={60} />
          <GroupHead title={L('地址', 'Address')} note={list.custom.hint} />
          <Field mono value={url} onChangeText={(v) => { setUrl(v); if (phase !== 'testing') setPhase('form'); }} placeholder="https://example.com/v1" keyboardType="url" accessibilityLabel={L('地址', 'Address')} />
          <GroupHead title={L('接口类型', 'API type')} />
          <Segmented options={[{ value: 'openai' as const, label: 'OpenAI' }, { value: 'anthropic' as const, label: 'Anthropic' }]} value={api} onChange={setApi} />
          <GroupHead title={L('API 密钥', 'API key')} note={L('没有可以不填', 'Optional')} />
          {keyInfo?.present ? <T v="caption" color={t.ink2} style={{ fontWeight: '400', marginBottom: space.sm }}>{[L(`已存 ${keyInfo.hint ?? ''}`, `Saved ${keyInfo.hint ?? ''}`), sourceText(keyInfo.source), dayOf(keyInfo.savedAt)].filter(Boolean).join(' · ')}</T> : null}
          <KeyField value={key} onChange={setKey} label={L('API 密钥', 'API key')} placeholder={L('可以不填', 'Optional')} />
          <T v="caption" color={t.ink3} style={[styles.note, { marginTop: space.sm }]}>{L('密钥只存在你的服务器上。存好以后，这里只显示后四位。', 'The key is stored only on your server. Once saved, only its last four characters show here.')}</T>
        </>
      ) : null}

      {local && phase !== 'ok' ? (
        <KitCard style={styles.infoCard}>
          {local.detected ? (
            <OkLine text={L(`已找到 · ${local.models.length} 个型号`, `Found · ${models(local.models.length)}`)} />
          ) : lstat?.macOnly ? (
            <T v="callout" color={t.ink2}>{L('这一家只在 Mac 上跑，这台服务器不是 Mac。', "This one runs only on a Mac, and this server isn't one.")}</T>
          ) : (
            <>
              <T v="callout" color={t.ink2}>{L('没在跑：在服务器上起它，再点「测试」。', 'Not running: start it on the server, then tap Test.')}</T>
              {local.start ? <T v="callout" selectable style={styles.monoLine}>{local.start}</T> : null}
              {local.install ? <GoldLink label={L(`装 ${shortName(local)}`, `Install ${shortName(local)}`)} onPress={() => { Linking.openURL(local.install!).catch(() => {}); }} /> : null}
            </>
          )}
        </KitCard>
      ) : null}

      {login && phase !== 'ok' ? (
        <KitCard style={styles.infoCard}>
          {login.ready ? (
            <OkLine text={login.id === 'chatgpt' ? L(`已连接${login.plan ? ` · ${login.plan}` : ''}`, `Connected${login.plan ? ` · ${login.plan}` : ''}`)
              : L(`已登录${login.plan ? ` · ${login.plan}` : ''}`, `Signed in${login.plan ? ` · ${login.plan}` : ''}`)} />
          ) : (
            <>
              <T v="callout" color={t.ink2}>{login.how || ''}</T>
              {login.docs ? <GoldLink label={login.id === 'chatgpt' ? L('看条款', 'Read the terms') : L('看说明', 'Read the guide')}
                onPress={() => { Linking.openURL(login.docs!).catch(() => {}); }} /> : null}
            </>
          )}
          {planWaiting ? (
            <View style={{ gap: space.sm, marginTop: space.sm }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
                <Spinner size={14} color={t.ink2} />
                <T v="callout" style={{ flex: 1 }}>{L('在浏览器里授权一次。', 'Approve once in the browser.')}</T>
              </View>
              <T v="caption" color={t.ink3} style={styles.note}>{L('在手机上授权完，那一页会打不开：把地址栏里整串复制回来，粘在这里。', "On a phone the last page won't open: copy the whole address from the address bar and paste it here.")}</T>
              <Field mono value={pasted} onChangeText={setPasted} placeholder="http://127.0.0.1:1455/auth/callback?code=…" accessibilityLabel={L('授权后的地址', 'The address after approving')} />
              <View style={{ flexDirection: 'row', gap: space.sm }}>
                <View style={{ flex: 1 }}><PrimaryButton label={L('提交', 'Submit')} disabled={!pasted.trim()} onPress={submitPlan} /></View>
              </View>
              <LaterButton label={L('取消', 'Cancel')} onPress={cancelPlan} />
              {plan ? <GoldLink label={L('再打开授权页', 'Open the sign-in page again')} onPress={() => { Linking.openURL(plan).catch(() => {}); }} /> : null}
            </View>
          ) : null}
        </KitCard>
      ) : null}

      {phase === 'fail' ? (
        <View style={{ marginTop: space.lg }}>
          <TestFail error={test?.error ?? null} busy={false} onRetry={run}
            onChangeKey={cloud || kind === 'custom' ? () => { setKey(''); setPhase('form'); } : undefined} />
        </View>
      ) : null}

      {phase === 'ok' ? (
        <KitCard style={{ marginTop: space.lg }}>
          <View style={styles.cardPad}><OkLine text={okText} /></View>
          {chat ? (
            <KitRow title={L('主对话用', 'Main chat uses')} value={`${pickedName}${chat.ref === chat.recommended ? L(' · 推荐', ' · recommended') : ''}`}
              onPress={chat.choices.length > 1 ? pickModel : undefined} />
          ) : null}
          {verify ? <KitSep /> : null}
          {verify ? (
            <View style={styles.cardPad}>
              {verify.state === 'busy' ? (
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
                  <Spinner size={13} color={t.ink3} />
                  <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{L('正在用一次真实回复验证…', 'Checking with one real reply…')}</T>
                </View>
              ) : verify.state === 'ok' ? (
                <T v="caption" color={t.good} style={{ fontWeight: '500' }}>{L(`${pickedName} · 验证通过`, `${pickedName} · Verified`)}</T>
              ) : (
                <View style={{ gap: 4 }}>
                  <T v="caption" color={t.warn} style={{ fontWeight: '600' }}>{L(`${pickedName} · 验证没通过：${failLine(verify.error?.kind)}`, `${pickedName} · Didn't pass: ${failLine(verify.error?.kind)}`)}</T>
                  {detailOf(verify.error?.message) ? <T v="caption" color={t.ink3} selectable style={[styles.note, styles.monoLine]}>{L(`对方原话：${detailOf(verify.error?.message)}`, `Their reply: ${detailOf(verify.error?.message)}`)}</T> : null}
                </View>
              )}
            </View>
          ) : null}
          {note ? <KitSep /> : null}
          {note ? <View style={styles.cardPad}><T v="caption" color={t.ink3} style={styles.note}>{note}</T></View> : null}
        </KitCard>
      ) : null}
    </Frame>
  );
}

// —— ③ 上网搜索 ————————————————————————————————————————————————————————————————————————

const KEYLESS = ['duckduckgo', 'searxng'];
const KEYED = ['brave', 'tavily', 'exa', 'perplexity'];

/** 小格下面那一行：接口给的 free（非官方的再加一句）。 */
const searchCaption = (r: SearchRow) => [r.free, r.unofficial ? L('非官方接口', 'unofficial') : ''].filter(Boolean).join(' · ');
/** 选中的这一家还要先填什么：密钥 / 地址 / 不用。 */
const searchNeeds = (r: SearchRow): 'key' | 'url' | null => (r.connected ? null : r.selfHosted ? 'url' : r.keyless ? null : 'key');

/** 上网搜索的选择器（第一次打开的 ③、设置里的搜索页同一套）。 */
export function SearchChooser({ view, selected, onSelect }: { view: SearchView; selected: string | null; onSelect: (id: string) => void }) {
  const t = useTheme();
  const pick = (ids: string[]) => ids.map((id) => view.providers.find((r) => r.id === id)).filter((r): r is SearchRow => !!r);
  const free = pick(KEYLESS);
  const keyed = pick(KEYED);
  const sel = view.providers.find((r) => r.id === selected) ?? null;
  const need = sel ? searchNeeds(sel) : null;
  return (
    <View>
      <GroupHead title={L('不用密钥', 'No key needed')} />
      <TileRow>
        {padRow(free.map((r) => <ProviderTile key={r.id} name={r.name} mono={monoOf(r.name)} caption={searchCaption(r) || null} selected={selected === r.id} onPress={() => onSelect(r.id)} />), 2)}
      </TileRow>
      <GroupHead title={L('用你的密钥', 'With your key')} />
      <TileRow>
        {padRow(keyed.map((r) => <ProviderTile key={r.id} name={r.name.replace(/ Search$/, '')} mono={monoOf(r.name)} selected={selected === r.id} onPress={() => onSelect(r.id)}
          label={[r.name, r.connected ? L('已连', 'connected') : ''].filter(Boolean).join(L('，', ', '))} />), 4)}
      </TileRow>
      {sel ? (
        <View style={styles.searchLine}>
          <Search size={16} color={t.ink2} />
          <View style={{ flex: 1, gap: 3 }}>
            <T v="callout">{L(`上网搜用 ${sel.name}`, `Web search uses ${sel.name}`)}{need === 'key' ? L(' · 下一步先填密钥', ' · enter its key next') : need === 'url' ? L(' · 下一步先填地址', ' · enter its address next') : ''}</T>
            {[sel.keyless ? null : sel.free, sel.note].filter(Boolean).map((x) => <T key={x!} v="caption" color={t.ink3} style={styles.note}>{x}</T>)}
          </View>
        </View>
      ) : null}
    </View>
  );
}

export function SetupSearchScreen() {
  const nav = useNavigation<any>();
  const { fresh } = useMode();
  const [view, setView] = useState<SearchView | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => {
    setupApi.search().then((v) => { setView(v); setErr(null); setSel((s) => s ?? v.provider ?? null); }).catch((e) => setErr(errText(e)));
  }, []);
  useFocusEffect(useCallback(() => { load(); }, [load]));
  const row = view?.providers.find((r) => r.id === sel) ?? null;
  const later = () => { skipSetup(serverKey(), 'search'); nav.navigate('SetupMemory', { fresh }); };
  const next = async () => {
    if (!row) return;
    if (searchNeeds(row)) { nav.navigate('SetupSearchKey', { id: row.id, fresh }); return; }
    setBusy(true);
    try {
      await setupApi.setSearch({ enabled: true, provider: row.id });
      nav.navigate('SetupMemory', { fresh });
    } catch (e) {
      showError(L('保存失败', "Couldn't save"), e);
    } finally { setBusy(false); }
  };
  return (
    <Frame step={2} footer={<><PrimaryButton label={L('下一步', 'Next')} onPress={next} busy={busy} disabled={!row} /><LaterButton onPress={later} /></>}>
      <SetupHeading title={L('上网搜索', 'Web search')} sub={L('不配的话，回答只靠模型自己知道的；打开网址照样能用。', 'Without it, answers rely on what the model already knows; opening links still works.')} />
      {view ? <SearchChooser view={view} selected={sel} onSelect={setSel} /> : <LoadState error={err} onRetry={load} />}
    </Frame>
  );
}

/** 搜索要先填的：密钥（Brave、Tavily、Exa、Perplexity）或者地址（SearXNG）。测通了才存，存完设成默认的一家。 */
export function SetupSearchKeyScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { fresh, settings, returnTo, params } = useMode();
  const id = String(params.id || '');
  const [view, setView] = useState<SearchView | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [key, setKey] = useState('');
  const [url, setUrl] = useState('');
  const [phase, setPhase] = useState<Phase>('form');
  const [error, setError] = useState<ProbeError | null>(null);
  const [hits, setHits] = useState(0);
  useEffect(() => {
    let live = true;
    setupApi.search().then((v) => {
      if (!live) return;
      setView(v);
      setErr(null);
      setUrl(v.providers.find((r) => r.id === id)?.url ?? '');
    }).catch((e) => { if (live) setErr(errText(e)); });
    return () => { live = false; };
  }, [id, tick]);
  const row = view?.providers.find((r) => r.id === id) ?? null;
  const back = () => nav.goBack();
  const title = settings && row ? row.name : null;
  if (!view || !row) {
    return <Frame step={2} onBack={back} settingsTitle={title}><LoadState error={view && !row ? L('没有这一家', 'No such provider') : err} onRetry={() => setTick((x) => x + 1)} /></Frame>;
  }
  const self = row.selfHosted;
  const run = async () => {
    setPhase('testing');
    setError(null);
    try {
      const body = self ? { url: url.trim() } : key.trim() ? { key: key.trim() } : {};
      const r = await setupApi.testSearch(row.id, body);
      if (!r.ok) { setError(r.error); setPhase('fail'); announce(L(`没通过：${failLine(r.error?.kind)}`, `Didn't pass: ${failLine(r.error?.kind)}`)); return; }
      if (self || key.trim()) await setupApi.saveSearch(row.id, body);
      await setupApi.setSearch({ enabled: true, provider: row.id });
      setKey('');
      setHits(r.results);
      setPhase('ok');
      announce(L(`搜得到，${r.results} 条结果`, `Works, ${r.results} results`));
    } catch (e) {
      setPhase('form');
      showError(L('测试失败', 'Test failed'), e);
    }
  };
  const next = () => {
    if (settings) { if (returnTo) nav.navigate(returnTo); else nav.goBack(); return; }
    nav.navigate('SetupMemory', { fresh });
  };
  const can = self ? !!url.trim() : !!key.trim() || !!row.key?.present;
  const primary = phase === 'ok' ? <PrimaryButton label={settings ? L('完成', 'Done') : L('下一步', 'Next')} onPress={next} />
    : phase === 'fail' ? null : <PrimaryButton label={phase === 'testing' ? L('正在测试…', 'Testing…') : L('测试', 'Test')} busy={phase === 'testing'} disabled={!can} onPress={run} />;
  return (
    <Frame step={2} onBack={back} settingsTitle={title} footer={primary}>
      <View style={styles.provHead}>
        <Tile size={44} mono={monoOf(row.name)} />
        <View style={{ flex: 1, minWidth: 0, gap: 3 }}>
          <T v="title" numberOfLines={1} accessibilityRole="header">{row.name}</T>
          {row.free ? <T v="caption" color={t.ink3} style={{ fontWeight: '400' }} numberOfLines={2}>{row.free}</T> : null}
        </View>
      </View>
      {self ? (
        <>
          <GroupHead title={L('地址', 'Address')} note={row.note} />
          <Field mono value={url} onChangeText={(v) => { setUrl(v); if (phase !== 'testing') setPhase('form'); }} placeholder="https://searx.example.org" keyboardType="url" accessibilityLabel={L('地址', 'Address')} />
        </>
      ) : (
        <>
          <GroupHead title={L('API 密钥', 'API key')} note={row.note} />
          <View style={{ gap: space.sm }}>
            {row.key?.present ? <T v="caption" color={t.ink2} style={{ fontWeight: '400' }}>{[L(`已存 ${row.key.hint ?? ''}`, `Saved ${row.key.hint ?? ''}`), sourceText(row.key.source), dayOf(row.key.savedAt)].filter(Boolean).join(' · ')}</T> : null}
            <KeyField value={key} onChange={(v) => { setKey(v); if (phase === 'fail' || phase === 'ok') setPhase('form'); }} label={L('API 密钥', 'API key')}
              placeholder={row.key?.present ? L('换一把新的（不填就用存下的）', 'A new one (leave empty to keep the saved key)') : L('粘贴你的 API 密钥', 'Paste your API key')} />
            {row.keyUrl ? <GoldLink label={L(`去 ${row.name} 拿密钥`, `Get a key from ${row.name}`)} onPress={() => { Linking.openURL(row.keyUrl!).catch((e) => showError(L('无法打开', "Couldn't open it"), e)); }} /> : null}
            <T v="caption" color={t.ink3} style={styles.note}>{L('密钥只存在你的服务器上。存好以后，这里只显示后四位。', 'The key is stored only on your server. Once saved, only its last four characters show here.')}</T>
          </View>
        </>
      )}
      {phase === 'fail' ? (
        <View style={{ marginTop: space.lg }}>
          <TestFail error={error} onRetry={run} changeLabel={self ? L('换地址', 'New address') : undefined}
            onChangeKey={() => { if (!self) setKey(''); setPhase('form'); }} />
        </View>
      ) : null}
      {phase === 'ok' ? (
        <KitCard style={[styles.cardPad, { marginTop: space.lg, gap: space.sm }]}>
          <OkLine text={L(`搜得到 · ${hits} 条结果`, `Works · ${hits} results`)} />
          <T v="caption" color={t.ink3} style={styles.note}>{L(`上网搜用 ${row.name}。`, `Web search uses ${row.name}.`)}</T>
        </KitCard>
      ) : null}
    </Frame>
  );
}

// —— ④ 记忆放在哪 ——————————————————————————————————————————————————————————————————————

/** 按意思搜的三种：不开、Ollama（本机）、用接上的服务商。 */
export type EmbedPick = 'off' | 'ollama' | 'provider';

/** 本机 Ollama 用哪个向量模型：装着的里面名字带 embed 的，没有就目录里的默认（nomic-embed-text）。 */
function ollamaModel(c: EmbeddingChoice): string | null {
  return c.models?.find((m) => /embed/i.test(m)) ?? c.model ?? c.models?.[0] ?? null;
}

/** 记忆的两张卡（第一次打开的 ④、设置里的「记忆和向量」页同一套）：放在哪、按意思搜。 */
export function MemoryChooser({ view, choices, storage, path, embed, provider, onStorage, onPath, onEmbed, onProvider, embeddings = true }: {
  view: MemoryView; choices: EmbeddingChoice[]; storage: 'sqlite' | 'markdown'; path: string; embed: EmbedPick; provider: string | null;
  onStorage: (s: 'sqlite' | 'markdown') => void; onPath: (p: string) => void; onEmbed: (e: EmbedPick) => void; onProvider: (id: string) => void;
  /** false：按意思搜那张卡不画（claw 不是自带内核：它有自己的记忆检索） */
  embeddings?: boolean;
}) {
  const t = useTheme();
  const sheet = useSheet();
  const tree = view.tree;
  const ollama = choices.find((c) => c.id === 'ollama') ?? null;
  const remote = choices.filter((c) => !c.local);
  const chosen = remote.find((c) => c.id === provider) ?? remote[0] ?? null;
  const tick = (on: boolean) => (on ? <Check size={20} color={t.gold} /> : undefined);
  const pickProvider = () => sheet.open({
    title: L('用哪一家算', 'Which provider'),
    content: (close) => (
      <KitCard>
        {remote.map((c, i) => <KitRow key={c.id} first={i === 0} title={c.name} sub={c.model} chevron={false} right={tick(c.id === chosen?.id)} onPress={() => { close(); onProvider(c.id); onEmbed('provider'); }} />)}
      </KitCard>
    ),
  });
  return (
    <View>
      <GroupHead title={L('放在哪', 'Where')} />
      <KitCard>
        {tree.canChoose ? (
          <>
            <KitRow first title={L('这台电脑（推荐）', 'This computer (recommended)')} sub={L('存成一个数据库文件，最省事', 'One database file; the simplest')}
              chevron={false} right={tick(storage === 'sqlite')} onPress={() => onStorage('sqlite')} />
            <KitRow title={L('一个 Markdown 文件夹', 'A Markdown folder')} sub={L('每条记忆是一篇笔记，Obsidian 库也行', 'Each memory is a note; an Obsidian vault works')}
              chevron={false} right={tick(storage === 'markdown')} onPress={() => onStorage('markdown')} />
            {storage === 'markdown' ? (
              <View style={[styles.cardPad, { paddingTop: 0 }]}>
                <Field mono value={path} onChangeText={onPath} placeholder="~/Notes/Memory" accessibilityLabel={L('文件夹的完整路径', 'Full path of the folder')} />
                <T v="caption" color={t.ink3} style={[styles.note, { marginTop: 6 }]}>{L('服务器上的完整路径（/… 或 ~/…）。', 'A full path on the server (/… or ~/…).')}</T>
              </View>
            ) : null}
          </>
        ) : (
          <KitRow first chevron={false} right={tick(tree.initialized || tree.selected === 'script')}
            title={tree.selected === 'script' ? L('你自己的世界树脚本', 'Your own memory tree script') : !tree.initialized ? L('还没建', 'Not set up yet')
              : tree.storage === 'markdown' ? L('一个 Markdown 文件夹', 'A Markdown folder') : L('这台电脑', 'This computer')}
            sub={[tree.storage === 'markdown' || tree.selected === 'script' ? tree.path : null, tree.why].filter(Boolean).join(' · ')} />
        )}
      </KitCard>
      {embeddings ? <GroupHead title={L('按意思搜', 'Search by meaning')} note={L('找得到意思相近的，不只是同一个词', 'Finds things with a similar meaning, not just the same words')} /> : null}
      {embeddings ? <KitCard>
        <KitRow first title={L('不开', 'Off')} sub={L('只按关键词找', 'Keywords only')} chevron={false} right={tick(embed === 'off')} onPress={() => onEmbed('off')} />
        <KitRow title={L('Ollama（本机）', 'Ollama (local)')} sub={ollama ? L('不花钱、不出门', 'Free and private') : L('不花钱、不出门 · Ollama 没在跑', 'Free and private · Ollama is not running')}
          chevron={false} disabled={!ollama} right={tick(embed === 'ollama')} onPress={() => onEmbed('ollama')} />
        <KitRow title={L('用接上的服务商', 'A connected provider')}
          sub={chosen ? L(`笔记和对话会发给这一家去算 · ${chosen.name}`, `Your notes and chats are sent to it to compute · ${chosen.name}`)
            : L('笔记和对话会发给这一家去算 · 还没接能算的一家', 'Your notes and chats are sent to it to compute · none connected yet')}
          chevron={embed === 'provider' && remote.length > 1} disabled={!chosen} right={tick(embed === 'provider')}
          onPress={() => (embed === 'provider' && remote.length > 1 ? pickProvider() : onEmbed('provider'))} />
      </KitCard> : null}
    </View>
  );
}

/** 按意思搜现在用的那一家叫什么：目录里的名字（Ollama、OpenAI……），对不上的用服务器给的 backend/型号。 */
export function embedName(cur: { id: string; label: string }, choices: EmbeddingChoice[]): string {
  return choices.find((c) => c.id === cur.id)?.name ?? cur.label;
}

/** 记忆现在的样子 → 选择器的初始值。 */
export function memoryInitial(view: MemoryView): { storage: 'sqlite' | 'markdown'; path: string; embed: EmbedPick; provider: string | null } {
  const cur = view.current;
  return {
    storage: view.tree.storage === 'markdown' ? 'markdown' : 'sqlite', path: view.tree.storage === 'markdown' ? view.tree.path ?? '' : '',
    embed: !cur ? 'off' : cur.id === 'ollama' ? 'ollama' : 'provider', provider: cur && cur.id !== 'ollama' ? cur.id : null,
  };
}

/** 存记忆的选择：存法变了（或者世界树还没建）先建；按意思搜换了先算一个向量试试，通了才存。→ 一句出错的话（没错 = null）。 */
export async function saveMemory(view: MemoryView, choices: EmbeddingChoice[], pick: { storage: 'sqlite' | 'markdown'; path: string; embed: EmbedPick; provider: string | null }, builtin: boolean): Promise<{ error: string | null; probe?: ProbeError | null }> {
  const tree = view.tree;
  const moved = !tree.initialized || tree.storage !== pick.storage || (pick.storage === 'markdown' && (tree.path ?? '') !== pick.path.trim());
  if (tree.canChoose && moved) {
    if (pick.storage === 'markdown' && !pick.path.trim()) return { error: L('Markdown 要写文件夹的路径', 'A Markdown folder needs its path') };
    try {
      await setupApi.setMemory(pick.storage === 'markdown' ? { storage: 'markdown', path: pick.path.trim() } : { storage: 'sqlite' });
    } catch (e) {
      const data = (e as { data?: { migrate?: boolean } } | null)?.data;
      if (data?.migrate) return { error: L('已经在用，换要迁移（以后做）', 'Already in use; switching needs a migration (later)') };
      return { error: errText(e) };
    }
  }
  if (!builtin) return { error: null };
  // 按意思搜只在用户改了那一栏时才动：现在用的那家这会儿不在 choices 里（Ollama 没在跑、密钥没了、目录没读到）不算改，不悄悄关掉、不换到别家
  const init = memoryInitial(view);
  if (pick.embed === init.embed && (pick.embed !== 'provider' || pick.provider === init.provider)) return { error: null };
  const cur = view.current?.id ?? null;
  if (pick.embed === 'off') {
    if (cur) await setupApi.setMemory({ embeddings: 'off' });
    return { error: null };
  }
  const remote = choices.filter((c) => !c.local);
  // 没点开挑哪一家时，选择器上显示的是第一家（MemoryChooser 的 chosen），照它
  const target = pick.embed === 'ollama' ? choices.find((c) => c.id === 'ollama') : pick.provider ? remote.find((c) => c.id === pick.provider) : remote[0];
  if (!target) return { error: pick.embed === 'ollama' ? L('Ollama 没在跑', 'Ollama is not running') : L('这一家还没连上', 'This provider is not connected yet') };
  if (target.id === cur) return { error: null };
  const model = target.local ? ollamaModel(target) : target.model;
  const r = await setupApi.testEmbeddings({ id: target.id, model });
  if (!r.ok) return { error: L(`按意思搜没通过：${failLine(r.error?.kind)}`, `Search by meaning didn't pass: ${failLine(r.error?.kind)}`), probe: r.error };
  await setupApi.setMemory({ embeddings: { id: target.id, model } });
  return { error: null };
}

export function SetupMemoryScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { fresh } = useMode();
  const { claw } = useStore();
  const [view, setView] = useState<MemoryView | null>(null);
  const [choices, setChoices] = useState<EmbeddingChoice[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [pick, setPick] = useState<ReturnType<typeof memoryInitial> | null>(null);
  const [busy, setBusy] = useState(false);
  const [fail, setFail] = useState<{ error: string; probe?: ProbeError | null } | null>(null);
  const load = useCallback(() => {
    Promise.all([setupApi.memory(), setupApi.purposes(true).catch(() => null)]).then(([m, pv]) => {
      setView(m);
      setChoices(pv?.choices.embeddings ?? []);
      setPick((p) => p ?? memoryInitial(m));
      setErr(null);
    }).catch((e) => setErr(errText(e)));
  }, []);
  useEffect(() => { load(); }, [load]);
  const later = () => { skipSetup(serverKey(), 'memory'); nav.navigate('SetupDone', { fresh }); };
  const done = async () => {
    if (!view || !pick) return;
    setBusy(true);
    setFail(null);
    try {
      const r = await saveMemory(view, choices, pick, claw.kind === 'builtin');
      if (r.error) { setFail({ error: r.error, probe: r.probe }); return; }
      nav.navigate('SetupDone', { fresh });
    } catch (e) {
      showError(L('保存失败', "Couldn't save"), e);
    } finally { setBusy(false); }
  };
  const set = (patch: Partial<ReturnType<typeof memoryInitial>>) => { setFail(null); setPick((p) => (p ? { ...p, ...patch } : p)); };
  return (
    <Frame step={3} footer={<><PrimaryButton label={L('完成', 'Done')} onPress={done} busy={busy} disabled={!pick} /><LaterButton onPress={later} /></>}>
      <SetupHeading title={L('记忆放在哪', 'Where memory lives')}
        sub={L(`${agentName()} 记下的事都存进世界树，换个对话也找得到。`, `What ${agentName()} remembers goes into the memory tree, so any chat can find it.`)} />
      {view && pick ? (
        <MemoryChooser view={view} choices={choices} storage={pick.storage} path={pick.path} embed={pick.embed} provider={pick.provider}
          onStorage={(s) => set({ storage: s })} onPath={(p) => set({ path: p })} onEmbed={(e) => set({ embed: e })} onProvider={(id) => set({ provider: id })} />
      ) : <LoadState error={err} onRetry={load} />}
      {fail ? (
        <KitCard style={[styles.cardPad, { marginTop: space.lg, gap: 4 }]} accessibilityLiveRegion="polite">
          <T v="callout" color={t.bad} style={{ fontWeight: '600' }}>{fail.error}</T>
          {detailOf(fail.probe?.message) ? <T v="caption" color={t.ink3} selectable style={[styles.note, styles.monoLine]}>{L(`对方原话：${detailOf(fail.probe?.message)}`, `Their reply: ${detailOf(fail.probe?.message)}`)}</T> : null}
        </KitCard>
      ) : null}
    </Frame>
  );
}

// —— ⑤ 设好了 ——————————————————————————————————————————————————————————————————————————

type Summary = { model: { top: string; sub: string | null } | null; search: string | null; memory: { top: string; sub: string } };

/** 「设好了」那张卡的一行：左边是哪一样，右边两行。 */
function SummaryRow({ label, top, sub, first }: { label: string; top: string; sub?: string | null; first?: boolean }) {
  const t = useTheme();
  return (
    <View accessible accessibilityLabel={[label, top, sub].filter(Boolean).join(L('，', ', '))}>
      {first ? null : <KitSep />}
      <View style={styles.sumRow}>
        <T v="callout" color={t.ink2} style={{ width: 96 }}>{label}</T>
        <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
          <T v="body" numberOfLines={2} style={{ fontSize: 16 }}>{top}</T>
          {sub ? <T v="caption" color={t.ink3} numberOfLines={2} style={{ fontWeight: '400' }}>{sub}</T> : null}
        </View>
      </View>
    </View>
  );
}

export function SetupDoneScreen() {
  const nav = useNavigation<any>();
  const { fresh } = useMode();
  const { firstRun } = useStore();
  const [sum, setSum] = useState<Summary | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => {
    Promise.all([setupApi.overview(), setupApi.providers({ probe: false }), setupApi.search(), setupApi.memory(), setupApi.purposes(false).catch(() => null)]).then(([o, p, s, m, pv]) => {
      const ref = o.layers.models.chat ?? null;
      const pid = ref?.split('/')[0] ?? '';
      const row = p.providers.find((x) => x.id === pid);
      const region = row?.kind === 'cloud' ? row.regions.find((g) => g.id === row.region)?.label : null;
      const sname = s.provider ? s.providers.find((x) => x.id === s.provider)?.name ?? s.provider : null;
      const where = m.tree.storage === 'markdown' ? (m.tree.path ?? L('一个 Markdown 文件夹', 'A Markdown folder')) : L('这台电脑', 'This computer');
      setSum({
        model: ref ? { top: [row ? shortName(row) : pid, region].filter(Boolean).join(' · '), sub: bare(ref) } : null,
        search: sname,
        memory: { top: m.tree.initialized || m.tree.selected === 'script' ? where : L('还没建', 'Not set up yet'),
          sub: m.current ? L(`按意思搜用 ${embedName(m.current, pv?.choices.embeddings ?? [])}`, `Search by meaning uses ${embedName(m.current, pv?.choices.embeddings ?? [])}`) : L('只按关键词找', 'Keywords only') },
      });
      setErr(null);
    }).catch((e) => setErr(errText(e)));
  }, []);
  useEffect(() => { load(); }, [load]);
  const go = () => { const r = afterDone(fresh, firstRun); nav.reset({ index: 0, routes: [r] }); };
  const insets = useSafeAreaInsets();
  return (
    <Screen>
      <KeyboardScroll contentContainerStyle={[styles.page, { flexGrow: 1, paddingTop: space.xl, paddingBottom: space.lg + insets.bottom }]}>
        <View style={{ alignItems: 'flex-start' }}><BrandRing size={88} /></View>
        <SetupHeading title={L('设好了', "You're set")} sub={L('三样都能在「设置 → 密钥」里改。', 'All three can be changed under Settings → Keys.')} />
        <View style={{ marginTop: space.xl }}>
          {sum ? (
            <KitCard>
              <SummaryRow first label={L('模型', 'Model')} top={sum.model?.top ?? L('没设', 'Not set')} sub={sum.model?.sub} />
              <SummaryRow label={L('上网搜索', 'Web search')} top={sum.search ?? L('没设', 'Not set')} />
              <SummaryRow label={L('记忆', 'Memory')} top={sum.memory.top} sub={sum.memory.sub} />
            </KitCard>
          ) : <LoadState error={err} onRetry={load} />}
        </View>
        <View style={{ flex: 1, minHeight: space.xl }} />
        <PrimaryButton label={L('开始用', 'Start')} onPress={go} />
      </KeyboardScroll>
    </Screen>
  );
}

// —— 给助手起个名字（第一次打开：「设好了」之后、「好友怎么称呼你」之前；设置里也能再进来）——————————————————————————————

const LOOKS = (): { key: AvatarConfig['style']; label: string }[] => [
  { key: 'lens', label: L('透镜', 'Lens') }, { key: 'eclipse', label: L('日食', 'Eclipse') }, { key: 'orbit', label: L('轨道', 'Orbit') },
];

/**
 * 名字（默认现在的 app_name）、一句性格（可不填）、形象（透镜 / 日食 / 轨道，同「形象」页）。名字和性格走 /api/naming（server/naming.py），
 * 形象走 store 的 setAvatar（PUT /api/settings/avatar）。第一次打开从「设好了」来，params.next = MyName；从设置来（from = settings）存好就回去。
 * 最简版（照 MyNameScreen）：等设计稿。
 */
export function SetupNameScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const { settings } = useMode();
  const { avatar, setAvatar, refreshLive } = useStore();
  const [initial, setInitial] = useState<{ name: string; vibe: string } | null>(null);
  const [name, setName] = useState('');
  const [vibe, setVibe] = useState('');
  const [look, setLook] = useState<AvatarConfig['style'] | null>(null);
  const [busy, setBusy] = useState(false);
  const insets = useSafeAreaInsets();
  const next = useCallback(() => {
    if (settings) nav.goBack();
    else nav.reset({ index: 0, routes: [{ name: 'MyName', params: { next: 'Tabs' } }] });
  }, [nav, settings]);
  useEffect(() => {
    namingApi.get().then((n) => {
      if (!n.applies) { next(); return; }
      setInitial({ name: n.name, vibe: n.vibe }); setName(n.name); setVibe(n.vibe);
    }).catch(() => next());
  }, [next]);
  const style = look ?? avatar.style;
  const save = async () => {
    const n = name.trim(), v = vibe.trim();
    if (!initial || !n || busy) return;
    setBusy(true);
    try {
      if (n !== initial.name || v !== initial.vibe) {
        const r = await namingApi.save({ ...(n !== initial.name ? { name: n } : {}), ...(v !== initial.vibe ? { vibe: v } : {}) });
        setAgentName(r.name);
        persistAgentName(r.name).catch(() => {});
        refreshLive().catch(() => {});
      }
      if (style !== avatar.style) setAvatar({ style });
      next();
    } catch (e) {
      showError(L('保存失败', "Couldn't save"), e);
      setBusy(false);
    }
  };
  if (!initial) return <Screen><LoadState /></Screen>;
  return (
    <Screen>
      {settings ? <SettingsHeader title={L('名字和性格', 'Name and character')} onBack={() => nav.goBack()} /> : null}
      <KeyboardScroll layout contentContainerStyle={[styles.page, { flexGrow: 1, paddingTop: settings ? space.sm : space.xl, paddingBottom: space.lg + insets.bottom }]}
        keyboardShouldPersistTaps="handled">
        <View style={{ alignItems: 'flex-start' }}><LensAvatar size={88} config={{ ...avatar, style }} /></View>
        {settings ? (
          <T v="callout" color={t.ink2} style={{ marginTop: space.lg, lineHeight: 22 }}>{L('app 里、对话里都用这个名字。', "It's the name in the app and in the chat.")}</T>
        ) : (
          <SetupHeading title={L('给助手起个名字', 'Name your assistant')}
            sub={L('app 里、对话里都用这个名字。之后在「设置 → 名字和性格」里改。', "It's the name in the app and in the chat. Change it later under Settings → Name and character.")} />
        )}
        <GroupHead title={L('名字', 'Name')} />
        <Field value={name} onChangeText={setName} maxLength={40} autoCapitalize="words" returnKeyType="next" accessibilityLabel={L('助手的名字', "The assistant's name")}
          placeholder={L('名字', 'Name')} />
        <GroupHead title={L('一句性格', 'One line of character')} note={L('可不填。会写进它的 IDENTITY.md。', "Optional. It goes into its IDENTITY.md.")} />
        <Field value={vibe} onChangeText={setVibe} maxLength={120} returnKeyType="done" onSubmitEditing={save} accessibilityLabel={L('一句性格', 'One line of character')}
          placeholder={L('比如：说话直接，偶尔幽默', 'For example: plain-spoken, a little dry')} />
        <GroupHead title={L('形象', 'Look')} />
        <View style={styles.looks} accessibilityRole="radiogroup" accessibilityLabel={L('形象', 'Look')}>
          {LOOKS().map((o) => {
            const on = style === o.key;
            return (
              <Pressable key={o.key} onPress={() => setLook(o.key)} accessibilityRole="radio" accessibilityState={{ selected: on }} accessibilityLabel={o.label}
                style={[styles.look, { backgroundColor: t.surface, borderColor: on ? t.goldFill : 'transparent' }]}>
                <LensAvatar size={48} config={{ ...avatar, style: o.key }} />
                <T v="caption" color={on ? t.ink : t.ink2}>{o.label}</T>
              </Pressable>
            );
          })}
        </View>
        <View style={{ flex: 1, minHeight: space.xl }} />
        <PrimaryButton label={settings ? L('保存', 'Save') : L('继续', 'Continue')} onPress={save} busy={busy} disabled={!name.trim()} />
        {settings ? null : <LaterButton onPress={next} label={L('跳过', 'Skip')} />}
      </KeyboardScroll>
    </Screen>
  );
}

/** 设置首页、服务商页用：服务器有 setup 才走这一套（老服务器没有这些接口）。 */
export const setupSupported = () => supports('setup');

/** 选中的那一家还要先填什么（设置的搜索页用）。 */
export const searchNeedOf = searchNeeds;

const styles = StyleSheet.create({
  top: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingHorizontal: space.lg + space.xs, paddingTop: space.sm, minHeight: 56 },
  page: { flexGrow: 1, paddingHorizontal: space.lg + space.xs, paddingTop: space.xs },
  allLink: { alignItems: 'flex-end', marginTop: space.sm + 2 },
  provHead: { flexDirection: 'row', alignItems: 'center', gap: space.md, marginTop: space.lg },
  note: { fontWeight: '400', lineHeight: 17 },
  monoLine: monoFont,
  infoCard: { padding: space.lg, gap: space.sm, marginTop: space.lg },
  cardPad: { paddingHorizontal: space.lg, paddingVertical: space.md },
  searchLine: { flexDirection: 'row', alignItems: 'flex-start', gap: space.sm, marginTop: space.lg, paddingHorizontal: space.xs },
  sumRow: { flexDirection: 'row', alignItems: 'flex-start', gap: space.md, paddingHorizontal: space.lg, paddingVertical: 14 },
  looks: { flexDirection: 'row', gap: space.md },
  look: { flex: 1, alignItems: 'center', gap: 8, borderRadius: 18, paddingVertical: space.lg, borderWidth: 1.5 },
});
