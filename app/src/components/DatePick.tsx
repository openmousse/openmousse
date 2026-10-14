// 日期框里的日期本身 · 网页版：输入框，照原样写 YYYY-MM-DD（手机上是 DatePick.native.tsx 的系统选择器）。
import React from 'react';
import { StyleSheet, TextInput } from 'react-native';
import { radius, space, type, useTheme } from '../theme';
import type { DatePickProps } from './DateField';

export function DatePick({ text, onText, label, placeholder }: DatePickProps) {
  const t = useTheme();
  return (
    <TextInput value={text} onChangeText={onText} accessibilityLabel={label} placeholder={placeholder ?? 'YYYY-MM-DD'} placeholderTextColor={t.ink3}
      autoCapitalize="none" autoCorrect={false} style={[type.body, styles.input, { backgroundColor: t.surface, color: t.ink }]} />
  );
}

const styles = StyleSheet.create({
  input: { borderRadius: radius.md, paddingHorizontal: space.lg, paddingVertical: 12 },
});
