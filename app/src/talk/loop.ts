// 对讲的一圈（第 8 批，13.6，2026-10-19）：听（录音，ears.ts 按音量判开口、说完）→ 转写（POST /api/chat/transcribe，和语音输入同一个）
// → 当主人自己的一句话发进当前对话（store 的 send：对话页里照常看得到，排队、断线重接都照旧）→ 回复一边流一边按句念（text.ts 切，
// voice.ts 念）→ 念完接着听。
// 念的时候麦也开着（ears.ts 的 Barge）：主人开口、或者点一下，就停下朗读、接着听；回复照样在对话里写完。
// 念完了回复还没完（它在等主人回答 ask_user 的反问、在跑工具）：停一会儿没有新的字就接着听（open）。主人说的插进这一轮（反问就拿它当回答），
// 没开口时回复又来了字就接着念。回复还在流时说的那一句，只念后面接着来的字（text.ts 的 Reading）。
// 只在前台用：切到后台就暂停（录音、朗读都停），回来点一下接着说。网页版不做。
// 不放进 React：状态在这里，对讲页用 useSyncExternalStore 画；流式的字、回复结束由对讲页喂进来（partial / replying）。
import { Platform } from 'react-native';
import { requireOptionalNativeModule } from 'expo';
import { AudioModule, setAudioModeAsync, type AudioRecorder } from 'expo-audio';
import { httpStatus } from '../api/base';
import { transcribeReady } from '../api/talk';
import { needsUpdate, supports } from '../api/version';
import type { Message, PendingFile } from '../data/types';
import { L } from '../lang';
import { Barge, Ears } from './ears';
import { langOf, misheard, pieces, Reading, speakable } from './text';
import { pickVoice, type Voice } from './voice';

export type Phase = 'starting' | 'listening' | 'thinking' | 'speaking' | 'paused' | 'blocked';
/** 用不了的原因：web 网页版；app 安装包没有朗读模块；server 服务器太旧；transcribe 服务器没配转写；mic 没给麦克风；error 连不上 */
export type BlockKind = 'web' | 'app' | 'server' | 'transcribe' | 'mic' | 'error';

export interface TalkState {
  phase: Phase;
  /** 刚才听到主人说的 */
  heard: string;
  /** 这一轮的回复（流着的或者完整的，原文） */
  reply: string;
  /** 一行说明：没听到声音、没听清、转写失败…… */
  note: string;
  block: { kind: BlockKind; text: string } | null;
  /** 麦克风的音量 0…1（听的时候画一圈） */
  level: number;
}

export interface TalkDeps {
  recorder: AudioRecorder;
  transcribe(file: PendingFile): Promise<string>;
  send(thread: string, text: string): void;
  stopReply(thread: string): Promise<void>;
}

const TICK = 100;
/** 连着几次没听清就停下来，不一直耗转写 */
const MISHEARD_MAX = 3;
/** 点「说完了」时录到的不到这么长：当没说，接着听 */
const MIN_MS = 500;
/** 回复这么久没再长：正好停在最后的那一句也念（ask_user 的问题、调工具前说的那句，后面一时没有字）；念完了回复还没完，就接着听 */
const STALL_MS = 800;

type KeepAwake = { activate?: (tag: string) => Promise<unknown>; deactivate?: (tag: string) => Promise<unknown> };
let keepMod: KeepAwake | null | undefined;
/** 对讲时屏幕不锁（ExpoKeepAwake 是 expo 自带的；拿不到就算了）。 */
function keepAwake(on: boolean) {
  try {
    if (keepMod === undefined) keepMod = requireOptionalNativeModule<KeepAwake>('ExpoKeepAwake');
    const p = on ? keepMod?.activate?.('mousse-talk') : keepMod?.deactivate?.('mousse-talk');
    p?.catch(() => {});
  } catch { /* 没有就算了 */ }
}

const msg = (e: unknown) => (e instanceof Error ? e.message : String(e));
const notes = () => ({ code: L('代码见对话。', 'The code is in the chat.'), table: L('表格见对话。', 'The table is in the chat.'), link: L('链接', 'a link') });

export class Talk {
  private st: TalkState = { phase: 'starting', heard: '', reply: '', note: '', block: null, level: 0 };
  private subs = new Set<() => void>();
  private thread: string;
  private deps: TalkDeps;
  private voice: Voice | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;
  private ears: Ears | null = null;
  private recording = false;
  /** 每停一次（打断、暂停、退出、重新开始听）加一：之前发出去的异步回来一看对不上就不管了 */
  private gen = 0;
  private closed = false;
  private missed = 0;
  /** 对话里正在流的字（页面喂进来的；没在回复是 undefined） */
  private partialNow: string | undefined;
  // —— 这一轮回复 ——
  /** 发了，等回复、念回复 */
  private awaiting = false;
  /** 发出去以后见过「在回复」 */
  private sawTyping = false;
  /** 念到哪了：回复还在流时说的那句（插话、回答反问），只念后面接着来的 */
  private reading = new Reading();
  /** 交给朗读、还没念完的段数 */
  private queued = 0;
  private replyDone = false;
  /** 这一段朗读真出声了（voice 的 start 回过）：Barge 从这时起才量 */
  private voiceOn = false;
  /** 念完了、回复还没完：开着麦接着听。主人开口以后回复新来的字先不念，他说的发出去以后只念那以后的 */
  private open = false;
  /** 回复停了 STALL_MS 没再长 */
  private stall: ReturnType<typeof setTimeout> | null = null;

  constructor(thread: string, deps: TalkDeps) {
    this.thread = thread;
    this.deps = deps;
  }

  subscribe = (f: () => void) => {
    this.subs.add(f);
    return () => { this.subs.delete(f); };
  };

  snapshot = () => this.st;

  /** 页面每次渲染拿到的最新依赖（store 的 send / transcribe / stop、录音器） */
  use(deps: TalkDeps) {
    this.deps = deps;
  }

  private set(p: Partial<TalkState>) {
    this.st = { ...this.st, ...p };
    for (const f of this.subs) f();
  }

  /** 进页面：先看能不能用（网页版、安装包、服务器、转写、麦克风），能用就开始听。「重试」也走这里。 */
  async start() {
    this.closed = false;
    const g = ++this.gen;
    this.set({ phase: 'starting', block: null, note: '' });
    if (Platform.OS === 'web') return this.blocked('web', L('网页版不支持对讲，请在手机 app 里使用。', 'Talk mode is not available on the web. Use the phone app.'));
    this.voice = pickVoice();
    if (!this.voice) return this.blocked('app', L('对讲需要新版 app。', 'Talk mode needs a newer version of the app.'));
    if (!supports('talk')) return this.blocked('server', needsUpdate('这台服务器不支持对讲。', "This server doesn't support talk mode."));
    try {
      const r = await transcribeReady();
      if (g !== this.gen) return;
      if (!r.ready) return this.blocked('transcribe', r.why || L('服务器没有配置语音转写。', 'The server has no speech transcription set up.'));
    } catch (e) {
      if (g !== this.gen) return;
      return this.blocked('error', L(`无法连接服务器：${msg(e)}`, `Can't reach the server: ${msg(e)}`));
    }
    const perm = await AudioModule.requestRecordingPermissionsAsync().catch(() => null);
    if (g !== this.gen) return;
    if (!perm?.granted) return this.blocked('mic', L('需要麦克风权限。在系统设置里允许后重试。', 'Microphone access is needed. Allow it in Settings, then retry.'));
    keepAwake(true);
    await this.listen(false);
  }

  /** 下面那个大按钮：听的时候 = 说完了，想的时候 = 停止，念的时候 = 打断，停下来时 = 说话。 */
  tap() {
    switch (this.st.phase) {
      case 'listening': void this.finish(this.gen, true); break;
      case 'thinking': this.stopThinking(); break;
      case 'speaking': this.interrupt(false); break;
      case 'paused': this.missed = 0; keepAwake(true); void this.listen(false); break;
      default: break;
    }
  }

  /** 切到后台：停下，回来点一下接着说。 */
  background() {
    if (this.st.phase === 'listening' || this.st.phase === 'thinking' || this.st.phase === 'speaking') {
      this.pause(L('切到后台时对讲会暂停。', 'Talk mode pauses in the background.'));
    }
  }

  /** 离开对讲页。 */
  close() {
    this.closed = true;
    this.gen++;
    this.stopTicks();
    this.clearStall();
    this.voice?.stop();
    this.queued = 0;
    this.awaiting = false;
    this.open = false;
    keepAwake(false);
    void this.stopRecording().then(() => setAudioModeAsync({ allowsRecording: false, playsInSilentMode: true, interruptionMode: 'mixWithOthers' })).catch(() => {});
  }

  /** 对话里正在流的字（usePartial）。 */
  partial(text: string | undefined) {
    this.partialNow = text;
    if (!this.awaiting || this.closed) return;
    if (text === undefined) this.reading.end();  // 有一轮回完了（可能是这句插进去之前那一轮：下一轮从头念）
    else this.take(text, false);
  }

  /** 这个对话在不在回复、最后一条消息（回复结束时就是这一轮的回复）。 */
  replying(typing: boolean, last: Message | undefined) {
    if (!this.awaiting || this.closed) return;
    if (typing) { this.sawTyping = true; return; }
    if (!this.sawTyping) return;
    if (!last || last.role !== 'grava') { this.endWith(L('没有收到回复。', 'No reply was received.')); return; }
    if (last.error) { this.endWith(last.error); return; }
    this.take(last.body.text, true);
  }

  // —— 听 ——

  private async listen(started: boolean) {
    const g = ++this.gen;
    this.stopTicks();
    await this.stopRecording();
    if (g !== this.gen) return;
    this.set({ phase: 'listening', level: 0, ...(started ? { note: '' } : {}) });
    try {
      await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true, interruptionMode: 'doNotMix', shouldRouteThroughEarpiece: false });
      await this.deps.recorder.prepareToRecordAsync();
      if (g !== this.gen) return;
      this.deps.recorder.record();
      this.recording = true;
    } catch (e) {
      if (g === this.gen) this.pause(L(`无法录音：${msg(e)}`, `Couldn't record: ${msg(e)}`));
      return;
    }
    // 接着听（open）时不限开口的时间：回复还在跑；回复完了 next() 换成平常的耳朵
    this.ears = new Ears(started, this.open ? { waitMs: Infinity } : {});
    this.ticks(g, (db) => {
      const r = this.ears?.feed(db, TICK);
      if (r === 'done') void this.finish(g, false);
      else if (r === 'nothing') this.pause(L('没有听到声音。点按「说话」再开始。', "Didn't hear anything. Tap Talk to start again."));
    });
    if (this.open && this.partialNow !== undefined) this.take(this.partialNow, false);  // 刚才那句没发出去（没听清）时来的字：接着念
  }

  /** 这一句说完了（force = 主人点了「说完了」）：停录、转写、发出去。 */
  private async finish(g: number, force: boolean) {
    if (g !== this.gen) return;
    this.stopTicks();
    let ms = 0;
    try { ms = this.deps.recorder.getStatus().durationMillis ?? 0; } catch { /* 拿不到就当够长 */ ms = MIN_MS; }
    if (force && ms < MIN_MS && !this.ears?.started()) { void this.listen(false); return; }
    const file = await this.stopRecording();
    if (g !== this.gen) return;
    if (!file) { void this.listen(false); return; }
    this.set({ phase: 'thinking', level: 0, note: '' });
    let text = '';
    try {
      text = (await this.deps.transcribe(file)).trim();
    } catch (e) {
      if (g !== this.gen) return;
      if (httpStatus(e) === 502) {
        // 可能是转写没配好（钥匙没了、engine.use.stt 选的那一家用不了）：再问一遍服务器，用它的说法（它知道走的是哪一家）
        const r = await transcribeReady().catch(() => null);
        if (g !== this.gen) return;
        if (r && !r.ready) {
          this.blocked('transcribe', r.why || L('服务器没有配置语音转写。', 'The server has no speech transcription set up.'));
          return;
        }
      }
      this.pause(L(`转写失败：${msg(e)}`, `Transcription failed: ${msg(e)}`));
      return;
    }
    if (g !== this.gen) return;
    if (misheard(text)) {
      this.missed += 1;
      if (this.missed >= MISHEARD_MAX) { this.pause(L('没听清。点按「说话」再开始。', "Didn't catch that. Tap Talk to start again.")); return; }
      this.set({ note: L('没听清，请再说一遍。', "Didn't catch that. Say it again.") });
      void this.listen(false);
      return;
    }
    this.missed = 0;
    this.ask(text);
  }

  /** 当主人自己的一句话发进当前对话。 */
  private ask(text: string) {
    this.clearStall();
    this.awaiting = true;
    this.open = false;
    this.sawTyping = false;
    this.reading.start(this.partialNow);  // 回复还在流：已经流出来的不再念
    this.queued = 0;
    this.replyDone = false;
    this.set({ phase: 'thinking', heard: text, reply: '', note: '' });
    this.deps.send(this.thread, text);
  }

  /** 想的时候点「停止」：还在转写就不发了；已经发了就停下这条回复（对话里留着已经写出来的）。 */
  private stopThinking() {
    this.gen++;
    this.clearStall();
    if (this.awaiting && !this.open) {  // open：在转写插的那一句，只是不发了，回复照旧
      this.awaiting = false;
      this.deps.stopReply(this.thread).catch(() => {});
    }
    void this.listen(false);
  }

  // —— 念 ——

  /** 回复的原文来了（流着的，或者 final = 完整的）：切出能念的一段交给朗读。 */
  private take(text: string, final: boolean) {
    const shown = this.reading.shown(text);
    if (shown !== this.st.reply) this.set({ reply: shown });
    if (this.holding()) {  // 接着听时主人开口了：先不念，他说的发出去以后只念那以后的
      if (final) { this.replyDone = true; this.awaiting = false; this.open = false; this.clearStall(); }
      return;
    }
    this.speak(this.reading.take(text, final));
    if (final) {
      this.replyDone = true;
      this.clearStall();
      this.next();
    } else {
      this.armStall();
    }
  }

  /** 一段原文去掉记号、拆开交给朗读；回有没有要念的。 */
  private speak(raw: string): boolean {
    const ps = raw ? pieces(speakable(raw, notes())) : [];
    for (const p of ps) this.say(p);
    return ps.length > 0;
  }

  private say(text: string) {
    const voice = this.voice;
    if (!voice) return;
    let fresh = false;
    if (this.open) {  // 在接着听、主人还没开口：不听了，念新来的（gen 加一，还没起来的那次听作废，念的时候的麦重新开）
      this.open = false;
      this.gen++;
      this.set({ note: '' });
      fresh = true;
    }
    const g = this.gen;
    this.queued += 1;
    if (this.st.phase !== 'speaking' || fresh) {
      this.set({ phase: 'speaking', level: 0 });
      this.voiceOn = false;
      void this.watchBarge(g);
    }
    voice.say(text, langOf(text), () => {
      if (g !== this.gen) return;
      this.queued = Math.max(0, this.queued - 1);
      this.next();
    }, () => {
      if (g === this.gen) this.voiceOn = true;
    });
  }

  /** 念完了手上的：回复也完了就接着听；没完就等它停一会儿（stalled）。 */
  private next() {
    if (!this.awaiting || this.queued > 0) return;
    if (!this.replyDone) { this.armStall(); return; }
    this.awaiting = false;
    this.clearStall();
    if (this.open && this.st.phase === 'listening') {  // 本来就在接着听：录音不断，换成平常的耳朵（从现在起 15 秒没开口就停）
      this.open = false;
      this.ears = new Ears(false);
      this.set({ note: '' });
      return;
    }
    this.open = false;
    void this.listen(false);
  }

  /** 回复停了一会儿没再长：正好停在最后的那一句念掉；念完了、回复还没完，就接着听（主人可以回答它的反问、插话）。 */
  private stalled() {
    this.stall = null;
    if (!this.awaiting || this.replyDone || this.closed || this.holding()) return;
    if (this.partialNow !== undefined && this.speak(this.reading.flush(this.partialNow))) return;
    if (this.queued > 0 || this.st.phase !== 'speaking') return;  // 还在念；或者还一个字都没念（「想」的时候照旧等）
    this.open = true;
    this.set({ note: L('还在回复，可以直接说。', 'Still replying. You can talk.') });
    void this.listen(false);
  }

  private armStall() {
    this.clearStall();
    const g = this.gen;
    this.stall = setTimeout(() => { if (g === this.gen) this.stalled(); }, STALL_MS);
  }

  private clearStall() {
    if (this.stall) clearTimeout(this.stall);
    this.stall = null;
  }

  /** 接着听的时候主人开口了（或者在转写他说的）：回复新来的字先不念。 */
  private holding(): boolean {
    return this.open && (this.st.phase === 'thinking' || (this.st.phase === 'listening' && !!this.ears?.started()));
  }

  /** 这一轮没回成：停下（主人正在接着说、在转写时不打断他，只写一行）。 */
  private endWith(note: string) {
    this.awaiting = false;
    if (this.holding()) { this.open = false; this.clearStall(); this.set({ note }); return; }
    this.pause(note);
  }

  /**
   * 念的时候开着麦：主人开口就打断。起不来（录音器忙、没权限）就只能点一下打断。
   * 喇叭真出声了（voiceOn）才喂给 Barge：朗读引擎冷启动可能比开麦晚好几百毫秒，先量到屋里的底噪，门槛就定低了，
   * 自己念的声音会把自己打断（审查时模拟出来的）。
   */
  private async watchBarge(g: number) {
    try {
      this.stopTicks();
      await this.stopRecording();
      await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true, interruptionMode: 'doNotMix', shouldRouteThroughEarpiece: false });
      await this.deps.recorder.prepareToRecordAsync();
      if (g !== this.gen || this.st.phase !== 'speaking') return;
      this.deps.recorder.record();
      this.recording = true;
    } catch {
      return;
    }
    const barge = new Barge();
    this.ticks(g, (db) => {
      if (this.st.phase === 'speaking' && this.voiceOn && barge.feed(db, TICK)) this.interrupt(true);
    });
  }

  /** 停下朗读、接着听（这一轮剩下的字不念了，对话里照样写完）。byVoice：主人开口打断的，已经在说了。 */
  private interrupt(byVoice: boolean) {
    this.gen++;
    this.clearStall();
    this.voice?.stop();
    this.queued = 0;
    this.awaiting = false;
    this.open = false;
    void this.listen(byVoice);
  }

  // —— 共用 ——

  private pause(note: string) {
    this.gen++;
    this.stopTicks();
    this.clearStall();
    this.voice?.stop();
    this.queued = 0;
    this.awaiting = false;
    this.open = false;
    keepAwake(false);
    void this.stopRecording();
    this.set({ phase: 'paused', note, level: 0 });
  }

  private blocked(kind: BlockKind, text: string) {
    this.pause('');
    this.set({ phase: 'blocked', block: { kind, text } });
  }

  private ticks(g: number, fn: (db: number | undefined) => void) {
    this.stopTicks();
    this.timer = setInterval(() => {
      if (g !== this.gen) { this.stopTicks(); return; }
      let db: number | undefined;
      try { db = this.deps.recorder.getStatus().metering; } catch { db = undefined; }
      const level = db === undefined ? 0 : Math.max(0, Math.min(1, (db + 60) / 60));
      if (Math.abs(level - this.st.level) > 0.04) this.set({ level });
      fn(db);
    }, TICK);
  }

  private stopTicks() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  /** 停下正在录的，回这一段的文件（听的时候用）；没在录是 null。 */
  private async stopRecording(): Promise<PendingFile | null> {
    if (!this.recording) return null;
    this.recording = false;
    try {
      await this.deps.recorder.stop();
      const uri = this.deps.recorder.uri;
      return uri ? { uri, name: 'talk.m4a', mime: 'audio/m4a', size: 0 } : null;
    } catch {
      return null;
    }
  }
}
