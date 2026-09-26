// 挑 Agent 的颜色和图标：6 个色点一行，24 个图标 6 列 4 行。新建页直接放在表单里，编辑页放在弹层里。
import React from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import type { AgentColor, GroupIcon } from '../data/types';
import { L } from '../i18n';
import { agentTint, space, useTheme } from '../theme';
import { GROUP_ICONS } from './GroupIcon';
import { SectionLabel } from './ui';

export const AGENT_COLORS: AgentColor[] = ['cyan', 'gold', 'green', 'purple', 'pink', 'orange'];
/** 颜色名（读屏用）。 */
export const colorLabel = (c: AgentColor): string => ({
  cyan: L('青', 'Teal'), gold: L('金', 'Gold'), green: L('绿', 'Green'), purple: L('紫', 'Purple'), pink: L('粉', 'Pink'), orange: L('橙', 'Orange'),
})[c];

const COLS = 6;

export function ColorSwatches({ value, onChange }: { value: AgentColor; onChange: (c: AgentColor) => void }) {
  const t = useTheme();
  return (
    <View style={styles.swatches} accessibilityRole="radiogroup" accessibilityLabel={L('颜色', 'Color')}>
      {AGENT_COLORS.map((c) => {
        const tint = agentTint(t, c);
        const on = c === value;
        // 选中：外面一圈同色的环，和色点之间隔一道底色（设计稿里的 box-shadow 做法）
        return (
          <Pressable key={c} onPress={() => onChange(c)} hitSlop={4} accessibilityRole="radio" accessibilityState={{ selected: on }} accessibilityLabel={colorLabel(c)}
            style={({ pressed }) => [styles.ring, { borderColor: on ? tint.fg : 'transparent', opacity: pressed ? 0.7 : 1 }]}>
            <View style={[styles.dot, { backgroundColor: tint.swatch }]} />
          </Pressable>
        );
      })}
    </View>
  );
}

export function IconGrid({ value, color, onChange }: { value: GroupIcon | null; color: AgentColor; onChange: (i: GroupIcon) => void }) {
  const t = useTheme();
  const tint = agentTint(t, color);
  const rows: (typeof GROUP_ICONS)[] = [];
  for (let i = 0; i < GROUP_ICONS.length; i += COLS) rows.push(GROUP_ICONS.slice(i, i + COLS));
  return (
    <View style={{ gap: space.sm }} accessibilityRole="radiogroup" accessibilityLabel={L('图标', 'Icon')}>
      {rows.map((row, r) => (
        <View key={r} style={{ flexDirection: 'row', gap: space.sm }}>
          {row.map(({ key, C, label }) => {
            const on = key === value;
            return (
              <Pressable key={key} onPress={() => onChange(key)} accessibilityRole="radio" accessibilityState={{ selected: on }} accessibilityLabel={label}
                style={({ pressed }) => [styles.tile, { backgroundColor: on ? tint.soft : t.surface, borderColor: on ? tint.fg : 'transparent', opacity: pressed ? 0.7 : 1 }]}>
                <C size={22} color={on ? tint.fg : t.ink2} />
              </Pressable>
            );
          })}
        </View>
      ))}
    </View>
  );
}

/** 颜色 + 图标两段，各带小标题。 */
export function IconColorPicker({ icon, color, onIcon, onColor }: { icon: GroupIcon | null; color: AgentColor; onIcon: (i: GroupIcon) => void; onColor: (c: AgentColor) => void }) {
  return (
    <View>
      <SectionLabel>{L('颜色', 'Color')}</SectionLabel>
      <ColorSwatches value={color} onChange={onColor} />
      <SectionLabel>{L('图标', 'Icon')}</SectionLabel>
      <IconGrid value={icon} color={color} onChange={onIcon} />
    </View>
  );
}

const styles = StyleSheet.create({
  // 6 × 46 + 5 × 10 = 326：375 宽的手机（内容 343）也放得下一行
  swatches: { flexDirection: 'row', gap: 10, paddingHorizontal: 2 },
  ring: { width: 46, height: 46, borderRadius: 23, borderWidth: 2, alignItems: 'center', justifyContent: 'center' },
  dot: { width: 36, height: 36, borderRadius: 18 },
  tile: { flex: 1, height: 48, borderRadius: 14, borderWidth: 2, alignItems: 'center', justifyContent: 'center' },
});
