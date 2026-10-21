// 一天环的纯计算：时刻 → 角度，时段 → 弧。0 点在顶上，顺时针，1440 分钟 = 360°。
// 今天 04:00 以前（rolled = false），[0, 240) 在内圈，其余在外圈；跨过 04:00 或午夜就切开。
// 04:00 以后、以及不是今天，rolled = true：全部在外圈，凌晨那一段已经落到环上。04:00 这个点本身在外圈。
// 不引 React。几何和「今天」页的绘制分开，node scripts/test-day-ring.mjs 直接跑这份。

export const DAY_MINUTES = 1440;
export const ROLL_MINUTES = 240;
/** 内圈半径 / 外圈半径，照 R2：120 / 140。 */
export const INNER_RATIO = 120 / 140;

/** 手机上这只环的画布。数字和绘制用同一套，避免内圈比例漂掉。 */
export const RING_VB = 280;
export const RING_C = 140;
export const RING_R = 104;
export const RING_STROKE = 8;
export const RING_INNER_R = RING_R * INNER_RATIO;
export const RING_INNER_STROKE = 6.4;
export const RING_DOT_R = 8;

export type Lane = 'inner' | 'outer';

export interface RingArc {
  lane: Lane;
  /** 从 12 点顺时针的角度。 */
  start: number;
  /** 顺时针扫过的角度。整天是 360，绘制时再收成 SVG 画得动的弧。 */
  sweep: number;
}

const HHMM = /^(\d{1,2}):(\d{2})$/;

/** 点的位置折进 [0, 1440)。1440 和 0 是同一个点。 */
function posMinute(m: number): number {
  return ((m % DAY_MINUTES) + DAY_MINUTES) % DAY_MINUTES;
}

/**
 * 时段端点折进一天。正好落在 1440 的整倍、而且不是 0，留在 1440，
 * 这样 0→24:00 不会被折成空的。点的角度仍把 1440 当成 0。
 */
export function wrapMinute(m: number): number {
  if (!Number.isFinite(m)) return NaN;
  const mod = posMinute(m);
  if (mod === 0 && m !== 0) return DAY_MINUTES;
  return mod;
}

/** "3:05" → 185，"24:00" → 1440，"24:01" 和别的钟点都是 null。 */
export function parseHHMM(s: string | null | undefined): number | null {
  const m = HHMM.exec(s || '');
  if (!m) return null;
  const h = Number(m[1]);
  const min = Number(m[2]);
  if (min > 59 || h > 24 || (h === 24 && min !== 0)) return null;
  return h * 60 + min;
}

/** 顺时针角度。0 在顶上，6:00 = 90°，1440 回到 0。 */
export function minuteToAngle(m: number): number {
  if (!Number.isFinite(m)) return NaN;
  return (posMinute(m) / DAY_MINUTES) * 360;
}

export function polar(cx: number, cy: number, r: number, deg: number): { x: number; y: number } {
  const rad = (deg * Math.PI) / 180;
  return { x: cx + r * Math.sin(rad), y: cy - r * Math.cos(rad) };
}

function fmt(n: number): string {
  const r = Math.round(n * 1000) / 1000;
  if (r === 0) return '0';
  return String(r);
}

/** SVG 弧。一整圈收成 359.8°，否则 A 命令画不出闭合的圆。 */
export function arcPath(cx: number, cy: number, r: number, startDeg: number, sweepDeg: number): string {
  if (!Number.isFinite(cx) || !Number.isFinite(cy) || !Number.isFinite(r) || r <= 0) return '';
  if (!Number.isFinite(startDeg) || !Number.isFinite(sweepDeg) || sweepDeg <= 0) return '';
  const sweep = sweepDeg >= 359.9 ? 359.8 : sweepDeg;
  const a0 = polar(cx, cy, r, startDeg);
  const a1 = polar(cx, cy, r, startDeg + sweep);
  const large = sweep > 180 ? 1 : 0;
  return `M${fmt(a0.x)} ${fmt(a0.y)}A${fmt(r)} ${fmt(r)} 0 ${large} 1 ${fmt(a1.x)} ${fmt(a1.y)}`;
}

function piecesOf(from: number, to: number, rolled: boolean): RingArc[] {
  if (!(to > from)) return [];
  const start = (from / DAY_MINUTES) * 360;
  if (rolled || from >= ROLL_MINUTES) return [{ lane: 'outer', start, sweep: (to - from) / 4 }];
  if (to <= ROLL_MINUTES) return [{ lane: 'inner', start, sweep: (to - from) / 4 }];
  return [
    { lane: 'inner', start, sweep: (ROLL_MINUTES - from) / 4 },
    { lane: 'outer', start: (ROLL_MINUTES / DAY_MINUTES) * 360, sweep: (to - ROLL_MINUTES) / 4 },
  ];
}

/** 一段时间画成一段或两段弧。长度为 0、或端点不是数，返回空。end ≤ start 视为过午夜。 */
export function spanToArcs(startMin: number, endMin: number, rolled: boolean): RingArc[] {
  if (!Number.isFinite(startMin) || !Number.isFinite(endMin)) return [];
  let start = wrapMinute(startMin);
  const end = wrapMinute(endMin);
  if (start === end) return [];
  if (start === DAY_MINUTES) start = 0;
  if (end > start) return piecesOf(start, end, rolled);
  return [...piecesOf(start, DAY_MINUTES, rolled), ...piecesOf(0, end, rolled)];
}

/**
 * 挨着的两段金弧之间留一道缝。每边让出 min(1.2°, 扫角的 18%)；
 * 剩下不到 0.8° 就不让了，免得短的时段被挤没。
 */
export function insetArc(start: number, sweep: number): { start: number; sweep: number } {
  if (!Number.isFinite(start) || !Number.isFinite(sweep) || sweep <= 0) return { start, sweep };
  const gap = Math.min(1.2, sweep * 0.18);
  const remain = sweep - gap * 2;
  if (remain < 0.8) return { start, sweep };
  return { start: start + gap, sweep: remain };
}

/** 青点在哪。null 表示这一天不放点（不是今天）。04:00 这个点在外圈。 */
export function dotAt(nowMin: number | null, rolled: boolean): { lane: Lane; angle: number } | null {
  if (nowMin == null || !Number.isFinite(nowMin)) return null;
  const m = posMinute(nowMin);
  return { lane: !rolled && m < ROLL_MINUTES ? 'inner' : 'outer', angle: (m / DAY_MINUTES) * 360 };
}

/** 不是今天、或还不知道现在，都当成已经过了 04:00：只有外圈。 */
export function dayHasRolled(nowMin: number | null, isToday: boolean): boolean {
  if (!isToday || nowMin == null || !Number.isFinite(nowMin)) return true;
  return posMinute(nowMin) >= ROLL_MINUTES;
}

/** 这台设备的钟（本地时间）几点几分。 */
export function clockMinute(nowMs: number): number {
  if (!Number.isFinite(nowMs)) return 0;
  const d = new Date(nowMs);
  const m = d.getHours() * 60 + d.getMinutes();
  if (!Number.isFinite(m)) return 0;
  return Math.min(DAY_MINUTES - 1, Math.max(0, m));
}

/** 两个钟点差几分钟，折进 (-720, 720]：跨午夜的取近的那边。 */
function halfDay(m: number): number {
  const x = posMinute(m);
  return x > DAY_MINUTES / 2 ? x - DAY_MINUTES : x;
}

/**
 * 服务器和这台设备的钟差几分钟，取到一刻钟的整倍（各地的时区都是一刻钟的整倍）。
 * 在一个时区就是 0：服务器给的那一分钟往下取整、网络慢几秒，都不会被当成时差。
 */
export function zoneOffset(anchorMin: number, anchorAt: number): number {
  if (!Number.isFinite(anchorMin) || !Number.isFinite(anchorAt) || anchorAt <= 0) return 0;
  return 15 * Math.round(halfDay(anchorMin - clockMinute(anchorAt)) / 15) || 0;  // -0 也写成 0
}

/**
 * 服务器那边此刻几点几分：这台设备的钟加上时差（zoneOffset）。跟状态栏同时翻分钟，夏令时由设备的钟管。
 * 读到锚以后过了服务器的午夜（还没重读到第二天）就停在 23:59，不跨进下一天。
 */
export function liveMinute(anchorMin: number, anchorAt: number, now: number): number {
  if (!Number.isFinite(anchorMin)) return 0;
  if (!Number.isFinite(anchorAt) || anchorAt <= 0 || !Number.isFinite(now)) return anchorMin;
  const m = posMinute(clockMinute(now) + zoneOffset(anchorMin, anchorAt));
  // 放到离「锚 + 过去的时间」最近的那一圈上，才看得出过没过午夜（设备的钟快几秒、先翻过 0 点也算）
  const est = anchorMin + Math.max(0, now - anchorAt) / 60000;
  const lap = m + DAY_MINUTES * Math.round((est - m) / DAY_MINUTES);
  return Math.min(DAY_MINUTES - 1, Math.max(0, lap));
}

/**
 * 「今天」页上的此刻，环和日程列表的现在线都用它。有新读到的锚按 liveMinute；
 * 没有（还没读到、或画的是缓存，anchorAt = 0）用这台设备的钟，连设备的时刻也还没有就用服务器给的那一分钟。
 */
export function nowMinute(anchorMin: number | null, anchorAt: number, now: number): number {
  if (anchorMin != null && anchorAt > 0) return liveMinute(anchorMin, anchorAt, now);
  if (now > 0) return clockMinute(now);
  return anchorMin ?? 0;
}

export function formatHHMM(min: number): string {
  if (!Number.isFinite(min)) return '--:--';
  const m = posMinute(min);
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
}

/** 下一次走一格还要等的毫秒：对准墙上的整分（状态栏翻分钟的那一刻）。 */
export function msUntilNextMinute(now: number): number {
  if (!Number.isFinite(now)) return 60000;
  const into = ((now % 60000) + 60000) % 60000;
  return into === 0 ? 60000 : 60000 - into;
}
