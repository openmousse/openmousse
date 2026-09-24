import React from 'react';
import { View } from 'react-native';
import { BookOpen, Briefcase, Dumbbell, HeartPulse, Moon, Plane, Utensils, Wallet } from './icons';
import type { GroupIcon as G } from '../data/types';
import { useTheme } from '../theme';

export const GROUP_ICONS: { key: G; C: typeof Dumbbell; label: string }[] = [
  { key: 'dumbbell', C: Dumbbell, label: '训练' }, { key: 'utensils', C: Utensils, label: '饮食' },
  { key: 'book', C: BookOpen, label: '学习' }, { key: 'wallet', C: Wallet, label: '财务' },
  { key: 'moon', C: Moon, label: '睡眠' }, { key: 'briefcase', C: Briefcase, label: '求职' },
  { key: 'heart', C: HeartPulse, label: '健康' }, { key: 'plane', C: Plane, label: '出行' },
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
