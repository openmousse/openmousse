import React from 'react';
import { View } from 'react-native';
import { BookOpen, Briefcase, Dumbbell, HeartPulse, Moon, Plane, Utensils, Wallet } from './icons';
import type { GroupIcon as G } from '../data/types';
import { L } from '../i18n';
import { useTheme } from '../theme';

// label 是界面文字，写成 getter：每次读的时候按当前语言取（模块顶层不能直接调 L）。
export const GROUP_ICONS: { key: G; C: typeof Dumbbell; label: string }[] = [
  { key: 'dumbbell', C: Dumbbell, get label() { return L('训练', 'Workout'); } },
  { key: 'utensils', C: Utensils, get label() { return L('饮食', 'Meals'); } },
  { key: 'book', C: BookOpen, get label() { return L('学习', 'Study'); } },
  { key: 'wallet', C: Wallet, get label() { return L('财务', 'Finance'); } },
  { key: 'moon', C: Moon, get label() { return L('睡眠', 'Sleep'); } },
  { key: 'briefcase', C: Briefcase, get label() { return L('求职', 'Job search'); } },
  { key: 'heart', C: HeartPulse, get label() { return L('健康', 'Health'); } },
  { key: 'plane', C: Plane, get label() { return L('出行', 'Travel'); } },
];

export function GroupBadge({ icon, size = 44, active }: { icon: G; size?: number; active?: boolean }) {
  const t = useTheme();
  const C = GROUP_ICONS.find((x) => x.key === icon)?.C ?? Dumbbell;
  return (
    <View style={{ width: size, height: size, borderRadius: size * 0.3, backgroundColor: active ? t.goldSoft : t.cyanSoft, alignItems: 'center', justifyContent: 'center' }}>
      <C size={size * 0.5} color={active ? t.gold : t.cyan} />
    </View>
  );
}
