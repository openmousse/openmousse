// 回复进行中的那一块：还没出字时是「在想 / 在做」——绕着环转的点、现在在哪一步、用了几秒、做过的几步、最近一段思考摘要；
// 开始出字以后，做过的步骤收成一行放在文字上面，文字下面三个点一闪一闪，直到回完。
// 步骤和思考来自服务器的 progress 事件（Gateway 的 WebSocket 对话通道、自己的内核，api/progress.ts）；没有的时候只有动画和计时。
// 每一步怎么说：自己的内核给了 label / doing（server/engine/steps.py：「查看日程」「正在查看日程」）就用它的，后面只接 note（文件名、
// 域名这类）；交给你点头的那一步后面写「· 等你确认」（琥珀）。没给的（OpenClaw 的对话通道、老服务器）照下面这张表配。
import React, { useEffect, useState } from 'react';
import { Animated, Easing, Platform, StyleSheet, View } from 'react-native';
import { useModelSwitch, type ReplyProgress, type ReplyStep } from '../api/progress';
import { L } from '../i18n';
import { useTheme } from '../theme';
import { Dots, Spinner, modelLabel, useNow } from './ChatCards';
import { Check, CircleAlert, Clock } from './icons';
import { T } from './ui';

const native = Platform.OS !== 'web';

/** OpenClaw 的工具名 → [中文名, 英文名, 中文进行时, 英文进行时]（OpenClaw 的对话通道还在用；自己的内核给 label）。没列的按「调用工具」。 */
const TOOLS: Record<string, [string, string, string, string]> = {
  exec: ['运行命令', 'Command', '正在运行命令', 'Running a command'],
  process: ['运行命令', 'Command', '正在运行命令', 'Running a command'],
  read: ['读取文件', 'Read', '正在读取文件', 'Reading a file'],
  write: ['写入文件', 'Write', '正在写入文件', 'Writing a file'],
  edit: ['修改文件', 'Edit', '正在修改文件', 'Editing a file'],
  apply_patch: ['修改文件', 'Edit', '正在修改文件', 'Editing a file'],
  web_search: ['搜索网页', 'Web search', '正在搜索网页', 'Searching the web'],
  web_fetch: ['打开网页', 'Open page', '正在打开网页', 'Opening a page'],
  browser: ['使用浏览器', 'Browser', '正在使用浏览器', 'Using the browser'],
  memory_search: ['检索记忆', 'Memory', '正在检索记忆', 'Searching memory'],
  memory_get: ['读取记忆', 'Memory', '正在读取记忆', 'Reading memory'],
  sessions_spawn: ['派出后台任务', 'Background task', '正在派出后台任务', 'Starting a background task'],
  sessions_send: ['联系其他会话', 'Session', '正在联系其他会话', 'Messaging another session'],
  message: ['发送消息', 'Message', '正在发送消息', 'Sending a message'],
  cron: ['设置定时任务', 'Schedule', '正在设置定时任务', 'Setting up a schedule'],
  image: ['查看图片', 'Image', '正在查看图片', 'Looking at an image'],
  'llm-task': ['调用模型', 'Model call', '正在调用模型', 'Calling a model'],
};
const tool = (name: string) => TOOLS[name] ?? TOOLS[name.replace(/-/g, '_')] ?? ['调用工具', 'Tool', '正在调用工具', 'Using a tool'];
const stepName = (s: ReplyStep) => { if (s.label) return s.label; const x = tool(s.tool); return L(x[0], x[1]); };
/** 名字后面的灰字：服务器说了人话的只接 note；没说的照旧是标题，表里没有的工具写工具名。 */
const stepDetail = (s: ReplyStep) => (s.label ? s.note ?? '' : s.detail || (TOOLS[s.tool] ? '' : s.tool));
/** 交给你点头了：自己的内核标 wait（老服务器把「等你点头」写在 detail 里，照旧显示）。 */
const waits = (s: ReplyStep) => !!s.label && !!s.wait;

/** 现在在做什么（标题那一行）。 */
function phaseLabel(p: ReplyProgress | null): string {
  const running = p?.steps.filter((s) => s.status === 'running') ?? [];
  if (running.length) {
    const s = running[running.length - 1];
    if (s.doing) return s.doing;
    const x = tool(s.tool);
    return L(x[2], x[3]);
  }
  switch (p?.phase) {
    case 'preparing_workspace': return L('正在准备', 'Getting ready');
    case 'preparing_context': return L('正在读取上下文', 'Loading context');
    default: return L('正在思考', 'Thinking');
  }
}

const elapsed = (n: number) => (n < 60 ? L(`${n} 秒`, `${n}s`) : L(`${Math.floor(n / 60)} 分 ${n % 60} 秒`, `${Math.floor(n / 60)}m ${n % 60}s`));

/** 环与点：一个点绕着细环转。 */
function Orbit({ size = 16, color, ring }: { size?: number; color: string; ring: string }) {
  const [spin] = useState(() => new Animated.Value(0));
  useEffect(() => {
    const loop = Animated.loop(Animated.timing(spin, { toValue: 1, duration: 1400, easing: Easing.linear, useNativeDriver: native }));
    loop.start();
    return () => loop.stop();
  }, [spin]);
  const rotate = spin.interpolate({ inputRange: [0, 1], outputRange: ['0deg', '360deg'] });
  const dot = Math.max(4, Math.round(size / 3.2));
  return (
    <View style={{ width: size, height: size }} accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
      <View style={{ position: 'absolute', top: dot / 2, left: dot / 2, right: dot / 2, bottom: dot / 2, borderRadius: size, borderWidth: 1.5, borderColor: ring, opacity: 0.45 }} />
      <Animated.View style={{ position: 'absolute', width: size, height: size, transform: [{ rotate }] }}>
        <View style={{ position: 'absolute', top: 0, left: (size - dot) / 2, width: dot, height: dot, borderRadius: dot, backgroundColor: color }} />
      </Animated.View>
    </View>
  );
}

/** 换了内容就淡入一次（思考摘要换了一段时）。 */
function FadeIn({ children }: { children: React.ReactNode }) {
  const [v] = useState(() => new Animated.Value(0));
  useEffect(() => {
    const a = Animated.timing(v, { toValue: 1, duration: 420, easing: Easing.out(Easing.quad), useNativeDriver: native });
    a.start();
    return () => a.stop();
  }, [v]);
  return <Animated.View style={{ opacity: v, transform: [{ translateY: v.interpolate({ inputRange: [0, 1], outputRange: [4, 0] }) }] }}>{children}</Animated.View>;
}

function StepRow({ s }: { s: ReplyStep }) {
  const t = useTheme();
  const detail = stepDetail(s);
  const wait = waits(s);
  return (
    <View style={styles.step}>
      <View style={styles.stepIcon}>
        {s.status === 'running' ? <Spinner size={13} color={t.gold} /> : s.status === 'failed' ? <CircleAlert size={13} color={t.bad} />
          : wait ? <Clock size={13} color={t.warn} /> : <Check size={13} color={t.good} />}
      </View>
      <T v="caption" numberOfLines={1} style={{ flex: 1, fontSize: 13 }} color={s.status === 'running' ? t.ink : t.ink2}>
        {stepName(s)}{detail ? <T v="caption" color={t.ink3} style={{ fontSize: 13 }}>{`  ${detail}`}</T> : null}
        {wait ? <T v="caption" color={t.warn} style={{ fontSize: 13 }}>{L(' · 等你确认', ' · awaiting your approval')}</T> : null}
      </T>
    </View>
  );
}

/** 还没出字：在想 / 在做。计时从这一轮开始算（服务器给的 since；没有进度时从这一块出现算起）。 */
export function ThinkingPanel({ progress }: { progress: ReplyProgress | null }) {
  const t = useTheme();
  const now = useNow(true);
  const [shown, setShown] = useState(0);
  if (now && !shown) setShown(now);
  const start = progress?.since ?? shown / 1000;
  const secs = now ? Math.max(0, Math.round(now / 1000 - start)) : 0;
  const steps = progress?.steps ?? [];
  const thought = progress?.thought.trim() ?? '';
  return (
    <View style={{ gap: 8 }} accessibilityLiveRegion="polite">
      <View style={[styles.head, { minHeight: 28 }]}>
        <Orbit size={18} color={t.gold} ring={t.ink3} />
        <T v="callout" style={{ fontWeight: '600' }}>{phaseLabel(progress)}</T>
        <T v="caption" color={t.ink3} style={{ fontVariant: ['tabular-nums'] }}>{secs ? elapsed(secs) : ''}</T>
      </View>
      {steps.length ? <View style={{ gap: 4 }}>{steps.map((s) => <StepRow key={s.id} s={s} />)}</View> : null}
      {thought ? (
        <FadeIn key={thought.slice(0, 40)}>
          <View style={[styles.thought, { borderLeftColor: t.line }]}>
            <T v="caption" color={t.ink3} numberOfLines={3} style={{ fontSize: 13, lineHeight: 18 }}>{thought}</T>
          </View>
        </FadeIn>
      ) : null}
    </View>
  );
}

/** 已经在出字：做过的步骤收成一行（在文字上面）。 */
export function StepsLine({ progress }: { progress: ReplyProgress | null }) {
  const t = useTheme();
  const steps = progress?.steps ?? [];
  if (!steps.length) return null;
  const names = [...new Set(steps.map(stepName))].join(L('、', ', '));
  return (
    <View style={styles.head}>
      <Check size={13} color={t.ink3} />
      <T v="caption" color={t.ink3} numberOfLines={1} style={{ flex: 1, fontSize: 13 }}>
        {L(`${steps.length} 个步骤 · ${names}`, `${steps.length} ${steps.length === 1 ? 'step' : 'steps'} · ${names}`)}
      </T>
    </View>
  );
}

/** 为什么换了模型（server/engine/fallback.py 的 reason_of；换下用户自己的 Claude Code 时是 engine/owner.py 的 quota / broken / auth）。
 *  认不出的不写原因。 */
const REASONS: Record<string, [string, string]> = {
  rate_limit: ['限流', 'rate limited'], overloaded: ['过载', 'overloaded'], server: ['服务出错', 'server error'], timeout: ['超时', 'timed out'],
  network: ['连不上', 'unreachable'], auth: ['密钥或登录无效', 'key or sign-in rejected'], billing: ['额度用完', 'out of credit'],
  model_not_found: ['型号不存在', 'model unavailable'], context_overflow: ['上下文太长', 'context too long'], cooldown: ['在冷却', 'cooling down'],
  quota: ['套餐额度用完', 'plan limit reached'], broken: ['无法运行', "couldn't run"],
};
export const switchReason = (reason?: string) => { const x = reason ? REASONS[reason] : undefined; return x ? L(x[0], x[1]) : ''; };

/** 回复进行中换了模型（model_switch）：这条回复下面一行小字，琥珀，和回完以后模型那行「已按回退链切换」一个颜色。 */
export function SwitchLine({ thread }: { thread: string }) {
  const t = useTheme();
  const sw = useModelSwitch(thread);
  if (!sw || sw.from === sw.to) return null;
  const from = modelLabel(sw.from);
  const to = modelLabel(sw.to);
  const why = switchReason(sw.reason);
  return (
    <T v="caption" color={t.warn} style={{ fontSize: 13 }} accessibilityLiveRegion="polite">
      {why ? L(`${from} ${why}，这一轮换成 ${to} 回答`, `${from} ${why}; this turn uses ${to}`) : L(`已换成 ${to}`, `Switched to ${to}`)}
    </T>
  );
}

/** 文字下面：还在写。 */
export function WritingDots() {
  const t = useTheme();
  return <View style={{ paddingTop: 2, paddingBottom: 4 }} accessibilityLabel={L('正在回复', 'Replying')}><Dots color={t.gold} /></View>;
}

const styles = StyleSheet.create({
  head: { flexDirection: 'row', alignItems: 'center', gap: 8, minHeight: 22 },
  step: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  stepIcon: { width: 16, alignItems: 'center' },
  thought: { borderLeftWidth: 2, paddingLeft: 10, paddingVertical: 1 },
});
