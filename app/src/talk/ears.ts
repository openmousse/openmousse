// 对讲的耳朵（第 8 批，13.6，2026-10-19）。纯逻辑，不引别的文件（node scripts/test-talk.mjs 直接跑）。
// 录音时大约每 100 毫秒拿一次音量（dBFS，-160…0；iOS 是平均功率，安卓是峰值，所以门槛都按「比底噪高多少」算，不写死）。
// - Ears：听主人说话。先等开口（连着 300 毫秒比底噪高出 15 dB），开口以后停顿 1.5 秒算说完；15 秒都没开口就不等了；一句最长 90 秒。
//   底噪跟着走：更安静就马上降下来，不说话时慢慢往上跟（一开口就录到的那一下会在字和字的空隙里降回去）。
// - Barge：念回复的时候也开着麦，主人开口就打断。喇叭的声音也会进麦（iOS 这里没有回声消除），所以门槛跟着最近 3 秒念的音量走：
//   取第 90 百分位，再高 15 dB、连着 300 毫秒才算；开头 400 毫秒只量不判，量出来的底不再往下掉。手机上喇叭离麦近得多，
//   上限放到 -3 dB：喇叭开得很响时只能点一下打断，不会自己把自己打断。滚动底噪这套做法改写自 Hermes 的 tools/voice_mode.py
//   （listen_for_speech：只拿没过门槛的音量算底、第 90 百分位、开头校准后锁住的最低底、连续几块才算），NousResearch/hermes-agent
//   commit f4ac0bea1ea50bba62886fb0b7e308e4edee8fd3，MIT，Copyright (c) 2025 Nous Research；许可全文见仓库根目录的 THIRD_PARTY_NOTICES。

export interface EarsOptions {
  /** 比底噪高多少 dB 算在说话 */
  margin: number;
  /** 门槛最低是多少 dB（太安静的屋子里不把呼吸声当说话） */
  minDb: number;
  /** 连着多久算开口 */
  onsetMs: number;
  /** 开口以后安静多久算说完 */
  silenceMs: number;
  /** 一直没开口，等多久不等了 */
  waitMs: number;
  /** 一句最长 */
  maxMs: number;
}

export const EARS: EarsOptions = { margin: 15, minDb: -48, onsetMs: 300, silenceMs: 1500, waitMs: 15000, maxMs: 90000 };

/** waiting：还没开口；speaking：在说；done：说完了（或说到了最长）；nothing：一直没开口。 */
export type Heard = 'waiting' | 'speaking' | 'done' | 'nothing';

const level = (db: number | undefined) => (db === undefined || !Number.isFinite(db) ? -90 : Math.max(-90, Math.min(0, db)));

export class Ears {
  private floor: number | null = null;
  private loud = 0;
  private quiet = 0;
  private waited = 0;
  private talked = 0;
  private heard: boolean;
  private o: EarsOptions;

  /** started：已经在说了（念的时候被打断，接着录的这一句） */
  constructor(started = false, o: Partial<EarsOptions> = {}) {
    this.heard = started;
    this.o = { ...EARS, ...o };
  }

  /** 门槛（给界面画音量用） */
  threshold(): number {
    return Math.max((this.floor ?? -60) + this.o.margin, this.o.minDb);
  }

  /** 来一次音量和离上一次过了多少毫秒。 */
  feed(db: number | undefined, ms: number): Heard {
    const x = level(db);
    if (this.floor === null || x < this.floor) this.floor = x;
    const loud = x >= this.threshold();
    if (!loud) this.floor += (x - this.floor) * 0.05;
    if (loud) {
      this.loud += ms;
      this.quiet = 0;
      if (this.loud >= this.o.onsetMs) this.heard = true;
    } else {
      this.loud = 0;
      if (this.heard) this.quiet += ms;
    }
    if (this.heard) {
      this.talked += ms;
      return this.quiet >= this.o.silenceMs || this.talked >= this.o.maxMs ? 'done' : 'speaking';
    }
    this.waited += ms;
    return this.waited >= this.o.waitMs ? 'nothing' : 'waiting';
  }

  /** 听到过开口没有 */
  started(): boolean {
    return this.heard;
  }
}

function p90(xs: number[]): number {
  const s = [...xs].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.floor(s.length * 0.9))];
}

export class Barge {
  private win: number[] = [];
  private calib = 0;
  private minFloor: number | null = null;
  private loud = 0;
  private cap: number;
  private margin: number;

  constructor(cap = -3, margin = 15) {
    this.cap = cap;
    this.margin = margin;
  }

  /** 来一次音量；true = 主人开口了，停下朗读。 */
  feed(db: number | undefined, ms: number): boolean {
    const x = level(db);
    if (this.calib < 400) {
      this.calib += ms;
      this.win.push(x);
      return false;
    }
    const recent = p90(this.win);
    if (this.minFloor === null) this.minFloor = Math.max(recent, -45);
    const trigger = Math.min(Math.max(Math.max(recent, this.minFloor) + this.margin, -38), this.cap);
    if (x < trigger) {
      this.win.push(x);
      if (this.win.length > 30) this.win.shift();  // 约 3 秒
    }
    this.loud = x >= trigger ? this.loud + ms : 0;
    return this.loud >= 300;
  }
}
