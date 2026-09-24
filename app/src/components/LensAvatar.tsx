import React from 'react';
import { agentName } from '../brand';
import Svg, { Circle, ClipPath, Defs, G, Path } from 'react-native-svg';
import { useTheme } from '../theme';
import type { AvatarConfig } from '../data/types';

// Grava 的形象是引力透镜：暗的力场、被弯曲的金色光环、汇入中心的青色数据流。
// 三种样式共用同一套几何，只改光环与数据流的画法。
export function LensAvatar({ size = 40, config }: { size?: number; config: AvatarConfig }) {
  const t = useTheme();
  const { style, ring, stream } = config;
  const cid = `lens-${style}-${size}`;
  return (
    <Svg width={size} height={size} viewBox="0 0 100 100" accessibilityLabel={`${agentName()}`}>
      <Defs>
        <ClipPath id={cid}><Circle cx="50" cy="50" r="46" /></ClipPath>
      </Defs>
      <Circle cx="50" cy="50" r="50" fill={t.lensField} />
      <G clipPath={`url(#${cid})`}>
        {style === 'lens' && (<>
          <Circle cx="50" cy="50" r="46" fill={ring} />
          <Circle cx="62" cy="53" r="38" fill={t.lensField} />
        </>)}
        {style === 'eclipse' && (<>
          <Circle cx="50" cy="50" r="40" fill="none" stroke={ring} strokeWidth="9" />
          <Circle cx="50" cy="50" r="27" fill={t.lensField} />
        </>)}
        {style === 'orbit' && (<>
          <Circle cx="50" cy="50" r="42" fill="none" stroke={ring} strokeWidth="4" />
          <Circle cx="50" cy="50" r="31" fill="none" stroke={ring} strokeWidth="2" strokeDasharray="3 6" />
        </>)}
        <Circle cx="55" cy="52" r="11" fill="none" stroke={stream} strokeWidth="3.2" />
        <Path d="M46 45 A 30 30 0 0 1 92 38" fill="none" stroke={stream} strokeWidth="3.2" strokeLinecap="round" />
        <Path d="M64 46 A 26 26 0 0 1 82 84" fill="none" stroke={stream} strokeWidth="3.2" strokeLinecap="round" />
        <Path d="M46 58 A 24 24 0 0 0 40 76" fill="none" stroke={stream} strokeWidth="3.2" strokeLinecap="round" />
      </G>
    </Svg>
  );
}
