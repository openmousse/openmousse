// 标志的几何，照设计系统 SVG（viewBox 120）：
// 环半径 R = 38，线宽 0.3R = 11.4，点半径 0.26R = 9.88，圆心压在环的中线上，在 1 点半（从 12 点顺时针 45°）。
// 浅色青点另描 1.6。活的场合（一天的环）不走这里，点按时刻走。

export const MARK_BOX = 120;
export const MARK_R = 38;
export const MARK_STROKE = 11.4;
export const MARK_DOT_R = 9.88;
export const MARK_DOT_DEG = 45;
export const MARK_DOT_STROKE = 1.6;

export interface MarkMetrics {
  ringR: number;
  stroke: number;
  dotR: number;
  dotStroke: number;
  dotX: number;
  dotY: number;
}

/** size 是画布边长。120 时点心在 (86.87, 33.13)，和 openmousse-mark.svg 一致。 */
export function markMetrics(size: number): MarkMetrics {
  const s = size / MARK_BOX;
  const ringR = MARK_R * s;
  const rad = (MARK_DOT_DEG * Math.PI) / 180;
  return {
    ringR,
    stroke: MARK_STROKE * s,
    dotR: MARK_DOT_R * s,
    dotStroke: MARK_DOT_STROKE * s,
    dotX: size / 2 + ringR * Math.sin(rad),
    dotY: size / 2 - ringR * Math.cos(rad),
  };
}
