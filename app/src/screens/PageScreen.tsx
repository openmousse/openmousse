// 页面页（2026-10-13，画布「Artifacts 原理与页面层」第 6–8 张）：Agent 写的网页放进盒子里显示。参数 {id}；网页版 ?screen=Page&id=pg-…
// - 页面要读数据、你还没决定过：先出授权 sheet（读哪些表和字段、只读、内容更新后要重新确认），决定了才显示；「不允许」也显示，只是读不到数据。
// - 盒子：网页版是 sandbox="allow-scripts" 的 iframe（srcdoc，不透明源）。页面经 postMessage 要数据（data）、往对话输入框放一句话（prompt）、
//   报错（error）；这里只认这个 iframe 发来的、限频，再用自己的令牌调服务器。iframe 自己跳到别处（第二次 load）就断开桥。
// - 手机原生包没有 WebView：给说明（页面在网页版里看，下一个安装包加入）。
// - 底下：在对话中讨论 / 钉到看板（Agent 的页面；钉上以后出一条带撤销的提示）。右上角「…」：读取权限、重新加载、删除页面。
import { useNavigation, useRoute } from '@react-navigation/native';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Platform, Pressable, StyleSheet, View } from 'react-native';
import { boardsApi } from '../api/boards';
import { canPages, pageErrorCode, pageErrorMessage, pagesApi, type PageInfo } from '../api/pages';
import { needsUpdate } from '../api/version';
import { agentName } from '../brand';
import { Check, ChevronLeft, Ellipsis, PanelsTopLeft, RefreshCw, Table, Trash2 } from '../components/icons';
import { pageTime, PageTag } from '../components/PageCards';
import { useSheet } from '../components/Sheet';
import { Card, Screen, showError, T } from '../components/ui';
import { L } from '../i18n';
import { openThread } from '../navigation';
import { useStore } from '../store';
import { space, useTheme } from '../theme';

interface Doc { html: string; digest: string; version: number; theme: 'dark' | 'light' }
interface BridgeMsg { mousse: 1; type: string; id?: number; payload?: { name?: unknown; text?: unknown; message?: unknown } }
type BridgeReply = { ok: true; data: unknown } | { ok: false; error: { code: string; message: string } };

const MAX_DATA_PER_MIN = 30;
const MAX_PROMPTS = 5;

export function PageScreen() {
  const t = useTheme();
  const nav = useNavigation<any>();
  const sheet = useSheet();
  const { groups } = useStore();
  const { id } = useRoute<any>().params as { id: string };
  const [info, setInfo] = useState<PageInfo | null>(null);
  const [doc, setDoc] = useState<Doc | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pageErrors, setPageErrors] = useState<string[]>([]);
  const [left, setLeft] = useState(false);
  const [toast, setToast] = useState<{ text: string; undo?: () => void } | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const asked = useRef<string | null>(null);
  const live = useRef<{ info: PageInfo | null; doc: Doc | null }>({ info: null, doc: null });
  const budget = useRef({ windowStart: 0, data: 0, prompts: 0, lastPrompt: 0 });
  const web = Platform.OS === 'web';
  const theme: 'dark' | 'light' = t.mode === 'light' ? 'light' : 'dark';

  const gen = useRef(0);
  // 只有最后一次读的结果算数（主题切换、页面刚更新又重读时，先发的请求后回来不会盖掉新的）
  const load = useCallback((): Promise<void> => {
    const my = ++gen.current;
    return Promise.all([pagesApi.get(id), web ? pagesApi.doc(id, theme) : Promise.resolve(null)]).then(
      ([p, d]) => {
        if (my !== gen.current) return;
        setInfo(p);
        setDoc(d ? { html: d.html, digest: d.digest, version: d.version, theme } : null);
        setLeft(false);
        setPageErrors([]);
        setError(null);
      },
      (e) => { if (my === gen.current) setError(pageErrorMessage(e)); });
  }, [id, web, theme]);

  useEffect(() => { if (canPages()) void load(); }, [load]);
  useEffect(() => { live.current = { info, doc }; }, [info, doc]);
  useEffect(() => () => { if (toastTimer.current) clearTimeout(toastTimer.current); }, []);

  const owner = info ? (info.agent === 'main' ? agentName() : groups.find((g) => g.id === info.agent)?.name ?? info.agent) : '';

  const showToast = useCallback((text: string, undo?: () => void) => {
    if (toastTimer.current) clearTimeout(toastTimer.current);
    setToast({ text, undo });
    toastTimer.current = setTimeout(() => setToast(null), 6000);
  }, []);

  const decide = useCallback(async (p: PageInfo, decision: 'allow' | 'deny') => {
    try {
      await pagesApi.grant(p.id, p.digest, decision);
      setInfo((cur) => (cur && cur.digest === p.digest ? { ...cur, grant: decision } : cur));
    } catch (e) {
      if (pageErrorCode(e) === 'stale') void load();
      else showError(L('保存失败', "Couldn't save"), e);
    }
  }, [load]);

  const askConsent = useCallback((p: PageInfo) => {
    sheet.open({
      title: L(`「${p.title}」请求读取数据`, `"${p.title}" asks to read data`),
      sub: L(`由 ${owner || p.agent} 生成 · 第 ${p.version} 版`, `Made by ${owner || p.agent} · version ${p.version}`),
      content: () => <ConsentBody page={p} />,
      footer: (close) => (
        <View style={styles.twoButtons}>
          <Pressable onPress={() => { close(); void decide(p, 'deny'); }} accessibilityRole="button"
            style={({ pressed }) => [styles.button, { backgroundColor: t.surface2, opacity: pressed ? 0.7 : 1 }]}>
            <T v="headline" style={{ fontSize: 15 }}>{L('不允许', "Don't allow")}</T>
          </Pressable>
          <Pressable onPress={() => { close(); void decide(p, 'allow'); }} accessibilityRole="button"
            style={({ pressed }) => [styles.button, { backgroundColor: t.goldFill, opacity: pressed ? 0.8 : 1 }]}>
            <T v="headline" color={t.onGold} style={{ fontSize: 15, fontWeight: '700' }}>{L('允许', 'Allow')}</T>
          </Pressable>
        </View>
      ),
    });
  }, [sheet, owner, decide, t]);

  // 这一版要读数据、还没决定过：打开时问一次（同一个 digest 只问一次；之后从「…」里改）
  useEffect(() => {
    if (!web || !info || !info.needs.length || info.grant != null || asked.current === info.digest) return;
    asked.current = info.digest;
    askConsent(info);
  }, [web, info, askConsent]);

  const onBridge = useCallback((m: BridgeMsg, reply: (r: BridgeReply) => void) => {
    const { info: p, doc: d } = live.current;
    const fail = (code: string, message: string) => reply({ ok: false, error: { code, message } });
    if (!p || !d) return fail('not_ready', L('页面还没准备好', 'The page is not ready'));
    const now = Date.now();
    const b = budget.current;
    if (now - b.windowStart > 60_000) { b.windowStart = now; b.data = 0; }
    if (m.type === 'data') {
      if (++b.data > MAX_DATA_PER_MIN) return fail('rate_limited', L('读取太频繁，请稍后再试', 'Too many reads; try again shortly'));
      if (p.grant !== 'allow') return fail('not_granted', L('你没有允许这个页面读取数据', "You haven't allowed this page to read data"));
      const name = String(m.payload?.name ?? '');
      pagesApi.data(p.id, d.digest, name).then(
        (r) => reply({ ok: true, data: { name: r.name, collection: r.collection, title: r.title, fields: r.fields, rows: r.rows, total: r.total, truncated: r.truncated } }),
        (e) => {
          const code = pageErrorCode(e) ?? 'error';
          if (code === 'stale') void load();
          fail(code, pageErrorMessage(e));
        });
      return;
    }
    if (m.type === 'prompt') {
      const text = String(m.payload?.text ?? '').trim().slice(0, 2000);
      if (!text) return fail('empty', L('没有内容', 'Nothing to send'));
      if (now - b.lastPrompt < 3000 || b.prompts >= MAX_PROMPTS) return fail('rate_limited', L('操作太频繁', 'Too many requests'));
      b.lastPrompt = now;
      b.prompts += 1;
      openThread(p.agent === 'main' ? 'main' : p.agent, p.agent !== 'main', { title: '', fill: text });
      return reply({ ok: true, data: null });
    }
    if (m.type === 'error') {
      const msg = String(m.payload?.message ?? '').slice(0, 200);
      setPageErrors((xs) => (xs.length >= 3 || !msg ? xs : [...xs, msg]));
      return;
    }
    fail('unknown', L('不支持的请求', 'Unsupported request'));
  }, [load]);

  const pin = useCallback(async () => {
    if (!info) return;
    try {
      const r = await pagesApi.pin(info.id);
      if (r.already) return showToast(L(`已在「${owner}」的看板上`, `Already on ${owner}'s board`));
      const undoTo = r.undoTo;
      showToast(L(`已钉到「${owner}」的看板`, `Pinned to ${owner}'s board`), undoTo ? () => {
        boardsApi.revert(info.agent, undoTo).then(() => showToast(L('已撤销', 'Undone')), (e) => showError(L('撤销失败', "Couldn't undo"), e));
      } : undefined);
    } catch (e) {
      showError(L('钉到看板失败', "Couldn't pin to the board"), e);
    }
  }, [info, owner, showToast]);

  const discuss = useCallback(() => {
    if (!info) return;
    openThread(info.agent === 'main' ? 'main' : info.agent, info.agent !== 'main');
  }, [info]);

  const openMenu = useCallback(() => {
    if (!info) return;
    sheet.open({
      title: info.title,
      content: (close) => (
        <View style={{ gap: 2 }}>
          {info.needs.length ? <MenuRow icon={<Check size={18} color={t.ink} />} label={L('读取权限', 'Data access')}
            sub={info.grant === 'allow' ? L('已允许', 'Allowed') : info.grant === 'deny' ? L('未允许', 'Not allowed') : L('未决定', 'Not decided')}
            onPress={() => { close(); setTimeout(() => askConsent(info), 300); }} /> : null}
          <MenuRow icon={<RefreshCw size={18} color={t.ink} />} label={L('重新加载', 'Reload')} onPress={() => { close(); void load(); }} />
          <MenuRow icon={<Trash2 size={18} color={t.bad} />} label={L('删除页面', 'Delete page')} danger
            sub={L('对话里的卡片和看板上的积木会显示「已删除」', 'Its chat cards and board block will show it as deleted')}
            onPress={() => {
              close();
              pagesApi.remove(info.id).then(() => nav.goBack(), (e) => showError(L('删除失败', "Couldn't delete"), e));
            }} />
        </View>
      ),
    });
  }, [info, sheet, t, askConsent, load, nav]);

  if (!canPages()) {
    return (
      <Screen>
        <Header title={L('页面', 'Page')} sub="" onBack={() => nav.goBack()} />
        <View style={{ padding: space.lg }}><T v="callout" color={t.ink2}>{needsUpdate('这台服务器还不支持页面。', "This server doesn't support pages yet.")}</T></View>
      </Screen>
    );
  }

  const waiting = !!info && info.needs.length > 0 && info.grant == null;
  const ready = !!info && !!doc && doc.digest === info.digest && !waiting;
  const reads = info?.needs.map((n) => n.title).join(L('、', ', ')) ?? '';
  const status = !info ? '' : !info.needs.length ? L('不读取数据', 'Reads no data')
    : info.grant === 'allow' ? L(`实时数据 · 读取：${reads}`, `Live data · reads ${reads}`)
      : info.grant === 'deny' ? L(`未允许读取：${reads}`, `Not allowed to read ${reads}`) : L('等待确认读取权限', 'Waiting for your OK to read data');

  return (
    <Screen>
      <Header title={info?.title ?? L('页面', 'Page')} onBack={() => nav.goBack()} onMenu={info ? openMenu : undefined}
        sub={info ? [L('页面', 'Page'), owner, L(`第 ${info.version} 版`, `version ${info.version}`)].filter(Boolean).join(' · ') : ''} />
      {info ? (
        <View style={styles.status}>
          <View style={[styles.dot, { backgroundColor: info.grant === 'allow' ? t.viz.series[0] : t.ink3 }]} />
          <T v="caption" color={t.ink3} style={{ flex: 1, fontWeight: '400' }} numberOfLines={1}>
            {[status, pageTime(info.updatedAt) ? L(`${pageTime(info.updatedAt)} 更新`, `updated ${pageTime(info.updatedAt)}`) : ''].filter(Boolean).join(' · ')}
          </T>
          {info.grant === 'deny' ? (
            <Pressable onPress={() => askConsent(info)} hitSlop={8} accessibilityRole="button">
              <T v="caption" color={t.gold} style={{ fontWeight: '600' }}>{L('授权', 'Allow')}</T>
            </Pressable>
          ) : null}
        </View>
      ) : null}
      <View style={[styles.frame, { borderColor: t.tints.purple.fg }]}>
        {error ? (
          <View style={styles.center}><T v="callout" color={t.bad}>{L(`无法加载：${error}`, `Couldn't load: ${error}`)}</T></View>
        ) : !info ? (
          <View style={styles.center}><T v="callout" color={t.ink2}>{L('正在加载…', 'Loading…')}</T></View>
        ) : info.deleted ? (
          <View style={styles.center}><T v="callout" color={t.ink2}>{L('这个页面已删除。', 'This page has been deleted.')}</T></View>
        ) : !web ? (
          <NativeNote page={info} />
        ) : waiting ? (
          <View style={styles.center}><T v="callout" color={t.ink2}>{L('等待确认读取权限…', 'Waiting for your OK to read data…')}</T></View>
        ) : ready && doc ? (
          left ? (
            <View style={styles.center}><T v="callout" color={t.bad}>{L('页面试图打开别的地址，已停止运行。', 'The page tried to navigate away and was stopped.')}</T></View>
          ) : (
            <PageFrame key={`${doc.digest}:${doc.theme}`} html={doc.html} title={info.title} onBridge={onBridge} onLeft={() => setLeft(true)} />
          )
        ) : (
          <View style={styles.center}><T v="callout" color={t.ink2}>{L('正在加载…', 'Loading…')}</T></View>
        )}
      </View>
      {pageErrors.length ? (
        <View style={[styles.errors, { backgroundColor: t.badSoft }]}>
          <T v="caption" color={t.bad} style={{ fontWeight: '600' }} numberOfLines={2}>{L(`页面脚本出错：${pageErrors[0]}`, `Page script error: ${pageErrors[0]}`)}</T>
        </View>
      ) : null}
      {info && !info.deleted ? (
        <View style={styles.bottom}>
          <Pressable onPress={discuss} accessibilityRole="button" style={({ pressed }) => [styles.button, { backgroundColor: t.surface2, opacity: pressed ? 0.7 : 1 }]}>
            <T v="headline" style={{ fontSize: 14 }}>{L('在对话中讨论', 'Discuss in chat')}</T>
          </Pressable>
          {info.agent !== 'main' ? (
            <Pressable onPress={() => void pin()} accessibilityRole="button" style={({ pressed }) => [styles.button, { backgroundColor: t.goldFill, opacity: pressed ? 0.8 : 1 }]}>
              <T v="headline" color={t.onGold} style={{ fontSize: 14, fontWeight: '700' }}>{L('钉到看板', 'Pin to board')}</T>
            </Pressable>
          ) : null}
        </View>
      ) : null}
      {toast ? (
        <View style={[styles.toast, { backgroundColor: t.surface2 }]} accessibilityLiveRegion="polite">
          <T v="callout" style={{ fontSize: 14 }}>{toast.text}</T>
          {toast.undo ? (
            <Pressable onPress={() => { const u = toast.undo; setToast(null); u?.(); }} hitSlop={8} accessibilityRole="button">
              <T v="callout" color={t.gold} style={{ fontSize: 14, fontWeight: '700' }}>{L('撤销', 'Undo')}</T>
            </Pressable>
          ) : null}
        </View>
      ) : null}
    </Screen>
  );
}

function Header({ title, sub, onBack, onMenu }: { title: string; sub: string; onBack: () => void; onMenu?: () => void }) {
  const t = useTheme();
  return (
    <View style={styles.header}>
      <Pressable onPress={onBack} hitSlop={12} accessibilityRole="button" accessibilityLabel={L('返回', 'Back')} style={styles.icon}>
        <ChevronLeft size={26} color={t.gold} />
      </Pressable>
      <View style={{ flex: 1, gap: 1 }}>
        <T v="headline" style={{ fontSize: 17, fontWeight: '700' }} numberOfLines={1}>{title}</T>
        {sub ? <T v="caption" color={t.ink3} style={{ fontWeight: '400' }} numberOfLines={1}>{sub}</T> : null}
      </View>
      {onMenu ? (
        <Pressable onPress={onMenu} hitSlop={8} accessibilityRole="button" accessibilityLabel={L('更多', 'More')} style={styles.icon}>
          <Ellipsis size={22} color={t.ink2} />
        </Pressable>
      ) : null}
    </View>
  );
}

function MenuRow({ icon, label, sub, danger, onPress }: { icon: React.ReactNode; label: string; sub?: string; danger?: boolean; onPress: () => void }) {
  const t = useTheme();
  return (
    <Pressable onPress={onPress} accessibilityRole="button" style={({ pressed }) => [styles.menuRow, { opacity: pressed ? 0.6 : 1 }]}>
      {icon}
      <View style={{ flex: 1, gap: 2 }}>
        <T v="body" color={danger ? t.bad : t.ink}>{label}</T>
        {sub ? <T v="caption" color={t.ink3} style={{ fontWeight: '400' }}>{sub}</T> : null}
      </View>
    </Pressable>
  );
}

/** 授权 sheet 的内容：要读的每张表（字段、只读），三条说明。 */
function ConsentBody({ page }: { page: PageInfo }) {
  const t = useTheme();
  return (
    <View style={{ gap: space.md }}>
      <View style={[styles.needs, { backgroundColor: t.surface }]}>
        {page.needs.map((n, i) => (
          <View key={n.name} style={[styles.need, i > 0 && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.line }]}>
            <View style={[styles.needIcon, { backgroundColor: t.cyanSoft }]}><Table size={16} color={t.cyan} /></View>
            <View style={{ flex: 1, gap: 2 }}>
              <T v="headline" style={{ fontSize: 15 }}>{n.missing ? L(`${n.title}（表不存在）`, `${n.title} (missing)`) : n.title}</T>
              {n.fields.length ? <T v="caption" color={t.ink2} style={{ fontWeight: '400' }} numberOfLines={2}>{n.fields.slice(0, 8).join(L('、', ', '))}</T> : null}
            </View>
            <View style={[styles.chip, { backgroundColor: t.cyanSoft }]}><T v="caption" color={t.cyan} style={{ fontSize: 11, fontWeight: '700' }}>{L('只读', 'Read-only')}</T></View>
          </View>
        ))}
      </View>
      <View style={{ gap: 6 }}>
        {[[t.good, '✓', L('只在打开这个页面时读取', 'Read only while this page is open')],
          [t.good, '✓', L('页面不能修改数据，也不能访问其他网站', "The page can't change data or reach other websites")],
          [t.gold, '!', L('页面内容更新后，需要重新确认', 'You confirm again whenever the page changes')]].map(([c, mark, text]) => (
          <View key={text} style={{ flexDirection: 'row', gap: 8 }}>
            <T v="caption" color={c} style={{ width: 12, fontWeight: '700' }}>{mark}</T>
            <T v="caption" color={t.ink2} style={{ flex: 1, fontWeight: '400', fontSize: 13 }}>{text}</T>
          </View>
        ))}
      </View>
    </View>
  );
}

/** 手机原生包里没有 WebView：说明 + 页面摘要。 */
function NativeNote({ page }: { page: PageInfo }) {
  const t = useTheme();
  return (
    <View style={{ padding: space.md, gap: space.md }}>
      <Card style={{ gap: 8 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <PanelsTopLeft size={18} color={t.tints.purple.fg} />
          <T v="headline" style={{ fontSize: 15 }}>{L('这个版本的 app 还不能显示页面', "This version of the app can't show pages yet")}</T>
        </View>
        <T v="callout" color={t.ink2}>{L('请在网页版中打开。下一个安装包会加入页面显示。', 'Open it in the web version. The next app build adds page display.')}</T>
      </Card>
      {page.summary ? <T v="callout" color={t.ink2}>{page.summary}</T> : null}
      <PageTag />
    </View>
  );
}

/** 网页版的盒子：sandbox="allow-scripts"（不透明源）的 iframe，内容由服务器包好（CSP、桥、主题）。 */
function PageFrame({ html, title, onBridge, onLeft }: { html: string; title: string; onBridge: (m: BridgeMsg, reply: (r: BridgeReply) => void) => void; onLeft: () => void }) {
  const ref = useRef<HTMLIFrameElement | null>(null);
  const loads = useRef(0);
  useEffect(() => {
    const onMsg = (e: MessageEvent) => {
      const f = ref.current;
      if (!f || e.source !== f.contentWindow || loads.current > 1) return;  // 只认这个盒子；它跳走以后不再理
      const m = e.data as BridgeMsg | null;
      if (!m || m.mousse !== 1 || typeof m.type !== 'string') return;
      onBridge(m, (r) => {
        if (loads.current <= 1) f.contentWindow?.postMessage({ mousse: 1, type: 'result', id: m.id, ...r }, '*');
      });
    };
    window.addEventListener('message', onMsg);
    return () => window.removeEventListener('message', onMsg);
  }, [onBridge]);
  return React.createElement('iframe', {
    ref, title, srcDoc: html, sandbox: 'allow-scripts', referrerPolicy: 'no-referrer',
    onLoad: () => { loads.current += 1; if (loads.current > 1) onLeft(); },
    style: { border: 0, width: '100%', height: '100%', display: 'block', background: 'transparent' },
  });
}

const styles = StyleSheet.create({
  header: { flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingRight: space.sm, paddingLeft: 4, paddingTop: 6, paddingBottom: 4 },
  icon: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center' },
  status: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: space.md, paddingBottom: 10 },
  dot: { width: 7, height: 7, borderRadius: 4 },
  frame: { flex: 1, marginHorizontal: 12, borderWidth: 1, borderStyle: 'dashed', borderRadius: 18, overflow: 'hidden' },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: space.lg },
  errors: { marginHorizontal: 12, marginTop: 8, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 8 },
  bottom: { flexDirection: 'row', gap: 8, paddingHorizontal: 12, paddingTop: 10, paddingBottom: 26 },
  button: { flex: 1, height: 44, borderRadius: 14, alignItems: 'center', justifyContent: 'center' },
  twoButtons: { flexDirection: 'row', gap: 10 },
  toast: { position: 'absolute', alignSelf: 'center', bottom: 90, flexDirection: 'row', alignItems: 'center', gap: 14, paddingHorizontal: 16, paddingVertical: 10, borderRadius: 22 },
  menuRow: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 12 },
  needs: { borderRadius: 14, overflow: 'hidden' },
  need: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 12, paddingHorizontal: 14 },
  needIcon: { width: 30, height: 30, borderRadius: 9, alignItems: 'center', justifyContent: 'center' },
  chip: { borderRadius: 7, paddingHorizontal: 8, paddingVertical: 2 },
});
