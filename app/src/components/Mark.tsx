// 静态标志：金环 + 1 点半的青点。页头、关于、登录用这一份；桌面图标和启动图要出包，不在这里改。
import React from 'react';
import Svg, { Circle } from 'react-native-svg';
import { markMetrics } from './markGeom';
import { useTheme } from '../theme';

export function Mark({ size = 88, decorative = false }: { size?: number; /** 旁边已经有名字时，不让读屏再念一遍 */ decorative?: boolean }) {
  const t = useTheme();
  const m = markMetrics(size);
  const light = t.mode === 'light';
  return (
    <Svg width={size} height={size} accessibilityRole="image" accessibilityLabel={decorative ? undefined : 'OpenMousse'}
      accessibilityElementsHidden={decorative} importantForAccessibility={decorative ? 'no-hide-descendants' : 'yes'}>
      <Circle cx={size / 2} cy={size / 2} r={m.ringR} fill="none" stroke={t.goldFill} strokeWidth={m.stroke} />
      <Circle cx={m.dotX} cy={m.dotY} r={m.dotR} fill={t.dot} stroke={light ? t.dotEdge : 'none'} strokeWidth={light ? m.dotStroke : 0} />
    </Svg>
  );
}
