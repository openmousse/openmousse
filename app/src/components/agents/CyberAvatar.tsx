// 赛博的头像（主线第三步，2026-10-21）：半身像（自己捏的部件）、机器类的线条图标、上传的图，外圈一道细金环。
// 部件的路径、颜色和叠放顺序照设计稿的附录（48×48 的 viewBox）；这些颜色是形象的内容色，放在这里自己的表里，不进 theme。
// 不画五官。96 / 64 / 44 / 28 / 20 几个大小都要认得出：20 像素时只剩轮廓和颜色（服务器随机时底色已经避开衣服的同色系）。
// 一眼分清：人 = 照片或首字母、没有环；功能 Agent = 底色 + 图标（GroupBadge）；赛博 = 半身像 + 细金环；管家 = 环与点（ButlerFace）。
import React from 'react';
import { Image, Text, View } from 'react-native';
import Svg, { Circle, Ellipse, G, Path, Rect } from 'react-native-svg';
import { fileUrl } from '../../api/base';
import type { AvatarParts, CyberAvatarSpec, Machine } from '../../api/cybers';
import { useTheme } from '../../theme';

// —— 内容色（附录）——————————————————————————————————————————————————————————————
/** 发色：[颜色, 胡茬用的浅版] */
export const HAIR_COLORS: Record<AvatarParts['hairColor'], readonly [string, string]> = {
  black: ['#15181D', '#7D7A76'], dark: ['#3B2618', '#8F7F70'], chestnut: ['#6B4226', '#A08670'],
  flax: ['#B89A6A', '#CDBA98'], grey: ['#D2CEC6', '#DCD6CD'], wine: ['#6E2433', '#A8707A'],
};
/** 衣服：[颜色, 深色（高领）] */
export const CLOTHES: Record<AvatarParts['clothes'], readonly [string, string]> = {
  cyan: ['#1E5664', '#163F4A'], camel: ['#6E5528', '#4F3D1C'], purple: ['#4D3E78', '#382C5A'],
  green: ['#285F42', '#1C4630'], rose: ['#73365A', '#552541'], rust: ['#78472A', '#59341E'],
};
export const SKINS: Record<AvatarParts['skin'], string> = { stone: '#E6DED3', warm: '#EFD7BF', wheat: '#D7AE88', tan: '#A97750', deep: '#6E4A33' };
export const BGS: Record<AvatarParts['bg'], string> = { cyan: '#10303A', gold: '#30271A', purple: '#29213F', green: '#12301F', pink: '#3A1A2A', orange: '#3A2416' };
export const ACC_COLORS: Record<AvatarParts['accColor'], string> = { black: '#15181D', gold: '#D9AE62', silver: '#C9CED6', cyan: '#5CCFE6' };
const COLLAR_TIP = '#E9EEF2';

// —— 部件的路径（附录）————————————————————————————————————————————————————————————
/** 发型：[后半（有的没有）, 前半] */
export const HAIRS: Record<AvatarParts['hair'], readonly [string | null, string]> = {
  bun: ['M19.8 9.6 a4.2 4.2 0 1 0 8.4 0 a4.2 4.2 0 1 0 -8.4 0 Z',
    'M16.2 20 C15.6 13.6 19 10.8 24 10.8 C29 10.8 32.4 13.6 31.8 20 C30.6 16.8 28 15.2 24 15.2 C20 15.2 17.4 16.8 16.2 20 Z'],
  short: [null, 'M16.2 19.8 C15.3 13.6 18.6 10 24.2 10 C29.6 10 32.9 13.2 32.2 18.6 C31.6 16.2 30.2 14.6 28 14.3 C25.2 14 21.6 15.4 19 15 C17.6 15.9 16.7 17.6 16.2 19.8 Z'],
  buzz: [null, 'M16.4 18.5 C16 13 19.4 10.6 24 10.6 C28.6 10.6 32 13 31.6 18.5 C29.6 15.6 27 14.6 24 14.6 C21 14.6 18.4 15.6 16.4 18.5 Z'],
  bob: [null, 'M15.4 25 C13.8 15.5 17.6 10 24 10 C30.4 10 34.2 15.5 32.6 25 L31 25 C31.4 20.6 31 17.4 29.4 15.8 C26.6 17.2 21.8 17.6 17.4 16.6 C16.8 19 16.8 22 17 25 Z'],
  long: ['M15 31 C13.4 19 16.2 9.6 24 9.6 C31.8 9.6 34.6 19 33 31 Z',
    'M15.8 19 C15.6 12.6 19 9.8 24 9.8 C29 9.8 32.4 12.6 32.2 19 C29.8 17.4 27 16.8 24 16.8 C21 16.8 18.2 17.4 15.8 19 Z'],
  pony: ['M29.5 13.5 C34.5 15 36 21 34.2 28 C33.4 24.5 32 20.5 30 18 Z',
    'M16.2 19.6 C15.6 13.2 19.2 10.2 24.2 10.2 C28.6 10.2 31.8 12.6 32 16.4 C29.6 15 26.4 14.6 23.4 15.4 C20.8 16 18 17.4 16.2 19.6 Z'],
  curly: [null, 'M15.6 19 C14 17 14.6 13.6 17 12.6 C17.4 10.2 20.4 8.8 22.6 9.8 C24.4 8.2 27.6 8.6 28.8 10.6 C31.4 10.6 33.4 13.2 32.4 15.8 C33.6 17.4 33 19.6 32.2 20 C31 17 28.6 15.6 24 15.6 C19.4 15.6 17 17 15.6 19 Z'],
  bald: [null, 'M16 21.5 C15.6 18.6 16 16.8 17 15.6 C17.4 17.6 17.6 19.6 17.5 21.5 Z M32 21.5 C32.4 18.6 32 16.8 31 15.6 C30.6 17.6 30.4 19.6 30.5 21.5 Z'],
};
export const BEARDS: Record<Exclude<AvatarParts['beard'], 'none'>, string> = {
  stubble: 'M17 22.5 C17.6 28 20.4 31 24 31 C27.6 31 30.4 28 31 22.5 C29.6 25 27.2 26.4 24 26.4 C20.8 26.4 18.4 25 17 22.5 Z',
  mustache: 'M20.4 24.8 C21.8 23.6 23.2 23.7 24 24.4 C24.8 23.7 26.2 23.6 27.6 24.8 C26.2 25.7 24.9 25.6 24 25.2 C23.1 25.6 21.8 25.7 20.4 24.8 Z',
  full: 'M16.6 21.6 C16.8 28.6 19.8 32.4 24 32.4 C28.2 32.4 31.2 28.6 31.4 21.6 C30 24.6 27.6 25.8 24 25.8 C20.4 25.8 18 24.6 16.6 21.6 Z',
};
const BODY = 'M5 48 C6 39 13 34 24 34 C35 34 42 39 43 48 Z';
const V_NECK = 'M20.4 34.1 L24 39 L27.6 34.1 Z';
const SHIRT_TIPS = 'M19.4 34.3 L24 39.6 L21.6 33.9 Z M28.6 34.3 L24 39.6 L26.4 33.9 Z';
const TURTLE = 'M19.6 30.4 H28.4 A1.2 1.2 0 0 1 29.6 31.6 V35.4 H18.4 V31.6 A1.2 1.2 0 0 1 19.6 30.4 Z';
const GLASSES = 'M18.2 20.6 a2.6 2.6 0 1 0 5.2 0 a2.6 2.6 0 1 0 -5.2 0 Z M24.6 20.6 a2.6 2.6 0 1 0 5.2 0 a2.6 2.6 0 1 0 -5.2 0 Z M23.4 20.4 L24.6 20.4';
const PHONES_BAND = 'M14.6 21 C14.6 12.6 18.8 8.4 24 8.4 C29.2 8.4 33.4 12.6 33.4 21';
const PHONES_CUPS = 'M12.6 19.6 a1.8 1.8 0 0 1 1.8 -1.8 h0.6 a1.8 1.8 0 0 1 1.8 1.8 v4.4 a1.8 1.8 0 0 1 -1.8 1.8 h-0.6 a1.8 1.8 0 0 1 -1.8 -1.8 Z M31.2 19.6 a1.8 1.8 0 0 1 1.8 -1.8 h0.6 a1.8 1.8 0 0 1 1.8 1.8 v4.4 a1.8 1.8 0 0 1 -1.8 1.8 h-0.6 a1.8 1.8 0 0 1 -1.8 -1.8 Z';
const EARRINGS = 'M15.8 25.4 a1.2 1.2 0 1 0 2.4 0 a1.2 1.2 0 1 0 -2.4 0 Z M29.8 25.4 a1.2 1.2 0 1 0 2.4 0 a1.2 1.2 0 1 0 -2.4 0 Z';
const HAT = 'M15.4 17.2 C15.2 10.8 19.2 7.4 24 7.4 C28.8 7.4 32.8 10.8 32.6 17.2 Z M14.6 15.8 H33.4 V18.6 H14.6 Z';
/** 机器类的线条图标（24×24，描边 t.gold）：出题机 = 计时器，反方 = 两道相对的弧，复盘机 = 回转箭头 */
export const MACHINE_PATHS: Record<Machine, string[]> = {
  timer: ['M10 2h4', 'M12 14l3-3', 'M12 6a8 8 0 1 0 0 16a8 8 0 1 0 0-16z'],
  sides: ['M8 4a8 8 0 0 0 0 16', 'M16 4a8 8 0 0 1 0 16', 'M12 9v6'],
  rewind: ['M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8', 'M3 3v5h5'],
};

export const DEFAULT_PARTS: AvatarParts = {
  hair: 'short', hairColor: 'black', beard: 'none', collar: 'round', clothes: 'cyan', skin: 'warm', bg: 'gold', accColor: 'black',
  acc: { glasses: false, phones: false, earring: false, hat: false },
};

/** 服务器给的形象（可能缺字段、可能是老数据）→ 能画的部件；上传的、机器类的原样。 */
export function partsOf(a: CyberAvatarSpec | null | undefined): AvatarParts {
  if (!a || 'upload' in a || 'machine' in a) return DEFAULT_PARTS;
  return { ...DEFAULT_PARTS, ...a, acc: { ...DEFAULT_PARTS.acc, ...(a.acc ?? {}) } };
}

/** 半身像本身（48×48 画布，按 size 缩放）。 */
export function CyberFigure({ parts, size }: { parts: AvatarParts; size: number }) {
  const [hairFill, stubble] = HAIR_COLORS[parts.hairColor] ?? HAIR_COLORS.black;
  const [cloth, clothDark] = CLOTHES[parts.clothes] ?? CLOTHES.cyan;
  const skin = SKINS[parts.skin] ?? SKINS.warm;
  const acc = ACC_COLORS[parts.accColor] ?? ACC_COLORS.black;
  const [back, front] = HAIRS[parts.hair] ?? HAIRS.short;
  return (
    <Svg width={size} height={size} viewBox="0 0 48 48">
      <Rect width={48} height={48} fill={BGS[parts.bg] ?? BGS.gold} />
      {back ? <Path d={back} fill={hairFill} /> : null}
      <Path d={BODY} fill={cloth} />
      <Rect x={20.6} y={25} width={6.8} height={10} rx={3} fill={skin} />
      {parts.collar === 'shirt' ? <G><Path d={V_NECK} fill={skin} /><Path d={SHIRT_TIPS} fill={COLLAR_TIP} /></G>
        : parts.collar === 'v' ? <Path d={V_NECK} fill={skin} />
          : parts.collar === 'turtle' ? <Path d={TURTLE} fill={clothDark} /> : null}
      <Ellipse cx={24} cy={20} rx={7.6} ry={8.8} fill={skin} />
      {parts.beard !== 'none' && BEARDS[parts.beard] ? <Path d={BEARDS[parts.beard]} fill={parts.beard === 'stubble' ? stubble : hairFill} /> : null}
      <Path d={front} fill={hairFill} />
      {parts.acc.hat ? <Path d={HAT} fill={acc} /> : null}
      {parts.acc.glasses ? <Path d={GLASSES} fill="none" stroke={acc} strokeWidth={1.2} /> : null}
      {parts.acc.phones ? <G><Path d={PHONES_BAND} fill="none" stroke={acc} strokeWidth={2} strokeLinecap="round" /><Path d={PHONES_CUPS} fill={acc} /></G> : null}
      {parts.acc.earring ? <Path d={EARRINGS} fill={acc} /> : null}
    </Svg>
  );
}

/** 机器类：底色上一个金色的线条图标。 */
export function MachineFigure({ machine, bg, size }: { machine: Machine; bg?: AvatarParts['bg']; size: number }) {
  const t = useTheme();
  const paths = MACHINE_PATHS[machine] ?? MACHINE_PATHS.timer;
  const icon = Math.round(size * 0.54);
  return (
    <View style={{ width: size, height: size, backgroundColor: BGS[bg ?? 'cyan'] ?? BGS.cyan, alignItems: 'center', justifyContent: 'center' }}>
      <Svg width={icon} height={icon} viewBox="0 0 24 24">
        {paths.map((d) => <Path key={d} d={d} fill="none" stroke={t.gold} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />)}
      </Svg>
    </View>
  );
}

/** 环的粗细：1.5（设计稿），20 像素时也看得出；和头像之间留 1 的空。 */
const RING = 1.5;
const GAP = 1;

/**
 * 一位赛博的头像：size 是整个圆的直径（含金环）。ring = false 只画里面（形象编辑器的部件格）。
 * 上传的图裁成圆形，外圈照样是金环；图只存在你自己的服务器上（/api/files/<id>）。
 */
export function CyberAvatar({ avatar, size = 44, ring = true, label }: { avatar: CyberAvatarSpec | null | undefined; size?: number; ring?: boolean; label?: string }) {
  const t = useTheme();
  const inner = ring ? size - 2 * (RING + GAP) : size;
  const body = avatar && 'upload' in avatar
    ? <Image source={{ uri: fileUrl(`/api/files/${avatar.upload}`) }} style={{ width: inner, height: inner }} accessibilityIgnoresInvertColors />
    : avatar && 'machine' in avatar ? <MachineFigure machine={avatar.machine} bg={avatar.bg} size={inner} />
      : <CyberFigure parts={partsOf(avatar)} size={inner} />;
  const clip = <View style={{ width: inner, height: inner, borderRadius: inner / 2, overflow: 'hidden', backgroundColor: t.surface2 }}>{body}</View>;
  if (!ring) return <View accessible={!!label} accessibilityLabel={label}>{clip}</View>;
  return (
    <View accessible={!!label} accessibilityLabel={label}
      style={{ width: size, height: size, borderRadius: size / 2, borderWidth: RING, borderColor: t.goldFill, alignItems: 'center', justifyContent: 'center' }}>
      {clip}
    </View>
  );
}

/** 人：首字母，没有环（一眼和赛博、功能 Agent、管家分开）。通讯录、好友群用。 */
export function PersonFace({ name, size = 44 }: { name: string; size?: number }) {
  const t = useTheme();
  const ch = (name || '?').trim().slice(0, 1).toUpperCase();
  return (
    <View style={{ width: size, height: size, borderRadius: size / 2, backgroundColor: t.surface2, alignItems: 'center', justifyContent: 'center' }}>
      <Text style={{ color: t.ink2, fontSize: Math.round(size * 0.4), fontWeight: '600' }} allowFontScaling={false}>{ch}</Text>
    </View>
  );
}

/** 你的管家：默认环与点（avatar 是 {orbit: true} 或没给）；捏过的、上传的照样外圈金环（和赛博一样）。 */
export function ButlerAvatar({ avatar, size = 44, label }: { avatar: { orbit: true } | CyberAvatarSpec | null | undefined; size?: number; label?: string }) {
  if (!avatar || 'orbit' in avatar) return <ButlerFace size={size} label={label} />;
  return <CyberAvatar avatar={avatar} size={size} label={label} />;
}

/**
 * 管家的头像（环与点）：自己的管家 = 金环 + 环与点（圈 t.gold、点 t.cyan、底 t.goldSoft）；好友的管家 = 灰环 + 灰色的环与点（tone grey）。
 * 环与点在 Thinking.tsx 里有一个没导出的 Orbit（那个文件不归这一包）：这里另画一个静止的。
 */
export function ButlerFace({ size = 44, tone = 'gold', label }: { size?: number; tone?: 'gold' | 'grey'; label?: string }) {
  const t = useTheme();
  const ringColor = tone === 'gold' ? t.goldFill : t.ink3;
  const orbit = tone === 'gold' ? t.gold : t.ink3;
  const dot = tone === 'gold' ? t.cyan : t.ink2;
  const inner = size - 2 * (RING + GAP);
  return (
    <View accessible={!!label} accessibilityLabel={label}
      style={{ width: size, height: size, borderRadius: size / 2, borderWidth: RING, borderColor: ringColor, alignItems: 'center', justifyContent: 'center' }}>
      <View style={{ width: inner, height: inner, borderRadius: inner / 2, backgroundColor: tone === 'gold' ? t.goldSoft : t.surface2, alignItems: 'center', justifyContent: 'center' }}>
        <Svg width={inner * 0.7} height={inner * 0.7} viewBox="0 0 24 24">
          <Circle cx={12} cy={12} r={8} fill="none" stroke={orbit} strokeWidth={2} />
          <Circle cx={18.2} cy={7} r={2.6} fill={dot} />
        </Svg>
      </View>
    </View>
  );
}
