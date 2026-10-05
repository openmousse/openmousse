import React from 'react';
import { View } from 'react-native';
import {
  BookOpen, Briefcase, Camera, Car, Code, Coffee, Dumbbell, Gamepad2, Globe, GraduationCap, HeartPulse, House, Leaf, Lightbulb, LayoutGrid,
  Moon, Music, Palette, PawPrint, Pill, Plane, ShoppingCart, Trophy, Utensils, Wallet,
} from './icons';
import type { AgentColor, GroupIcon as G } from '../data/types';
import { L } from '../i18n';
import { agentTint, useTheme } from '../theme';

// label 是界面文字（读屏用），写成 getter：每次读的时候按当前语言取（模块顶层不能直接调 L）。
// 顺序就是选图标时的顺序（6 列 4 行）。
export const GROUP_ICONS: { key: G; C: typeof Dumbbell; label: string }[] = [
  { key: 'moon', C: Moon, get label() { return L('睡眠', 'Sleep'); } },
  { key: 'dumbbell', C: Dumbbell, get label() { return L('训练', 'Workout'); } },
  { key: 'utensils', C: Utensils, get label() { return L('饮食', 'Meals'); } },
  { key: 'book', C: BookOpen, get label() { return L('学习', 'Study'); } },
  { key: 'wallet', C: Wallet, get label() { return L('财务', 'Finance'); } },
  { key: 'briefcase', C: Briefcase, get label() { return L('求职', 'Job search'); } },
  { key: 'heart', C: HeartPulse, get label() { return L('健康', 'Health'); } },
  { key: 'plane', C: Plane, get label() { return L('出行', 'Travel'); } },
  { key: 'coffee', C: Coffee, get label() { return L('咖啡', 'Coffee'); } },
  { key: 'music', C: Music, get label() { return L('音乐', 'Music'); } },
  { key: 'camera', C: Camera, get label() { return L('摄影', 'Photography'); } },
  { key: 'code', C: Code, get label() { return L('代码', 'Code'); } },
  { key: 'cart', C: ShoppingCart, get label() { return L('购物', 'Shopping'); } },
  { key: 'home', C: House, get label() { return L('家居', 'Home'); } },
  { key: 'car', C: Car, get label() { return L('汽车', 'Car'); } },
  { key: 'paw', C: PawPrint, get label() { return L('宠物', 'Pets'); } },
  { key: 'leaf', C: Leaf, get label() { return L('植物', 'Plants'); } },
  { key: 'gamepad', C: Gamepad2, get label() { return L('游戏', 'Games'); } },
  { key: 'palette', C: Palette, get label() { return L('绘画', 'Art'); } },
  { key: 'globe', C: Globe, get label() { return L('语言', 'Languages'); } },
  { key: 'graduation', C: GraduationCap, get label() { return L('申请', 'Applications'); } },
  { key: 'lightbulb', C: Lightbulb, get label() { return L('灵感', 'Ideas'); } },
  { key: 'trophy', C: Trophy, get label() { return L('竞赛', 'Competitions'); } },
  { key: 'pill', C: Pill, get label() { return L('用药', 'Medication'); } },
];

/** 别的写法（lucide 的原名、老数据）→ 服务器认的 key。 */
const ALIAS: Record<string, G> = {
  'book-open': 'book', 'heart-pulse': 'heart', 'shopping-cart': 'cart', house: 'home', 'paw-print': 'paw',
  'gamepad-2': 'gamepad', 'graduation-cap': 'graduation', 'utensils-crossed': 'utensils',
};
/** 服务器给的图标 key 换成认识的；不认识就是 null（显示默认图标，选图标时哪个都不选中）。 */
export const iconKey = (icon?: string | null): G | null => {
  if (!icon) return null;
  if (GROUP_ICONS.some((x) => x.key === icon)) return icon as G;
  return ALIAS[icon] ?? null;
};

/** Agent 的方块图标：浅底 + 前景都跟它的颜色走（没有颜色 = 青）。 */
export function GroupBadge({ icon, color, size = 44 }: { icon?: G | string | null; color?: AgentColor | null; size?: number }) {
  const t = useTheme();
  const tint = agentTint(t, color);
  const key = iconKey(icon);
  const C = GROUP_ICONS.find((x) => x.key === key)?.C ?? LayoutGrid;
  return (
    <View style={{ width: size, height: size, borderRadius: size * 0.3, backgroundColor: tint.soft, alignItems: 'center', justifyContent: 'center' }}>
      <C size={size * 0.5} color={tint.fg} />
    </View>
  );
}
